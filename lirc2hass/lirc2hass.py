"""Bridge LIRC input events to Home Assistant via REST API."""

import signal
import socket
import logging
import traceback
import time
import random
import argparse
import requests
import json
import websockets
from websockets.sync.client import connect as ws_connect
from typing import Any

APP_NAME = "lirc2hass"
VERSION = "0.1.0"
_LOGGER = logging.getLogger(APP_NAME)
DEF_LOG_LEVEL = logging.WARN

DEF_LIRC_SOCK_PATH = "/var/run/lirc/lircd"
DEF_REST_API_PATH = "/api/events/"
DEF_WS_API_PATH = "/api/websocket"
DEF_MAX_RECONNECT_DELAY = 64
DEF_MIN_REPEAT_TIME_MS = 740
DEF_USE_WS = False
DEF_TIMEOUT = 2  # seconds
SOCK_BUFSIZE = 4096
CACHED_EVENT_EXPIRE_TIME = 5

BUTTON_NAME = "button_name"
EVENT_IR_COMMAND_RECEIVED = "ir_command_received"


class ExitApp(Exception):
    pass


class APIError(Exception):
    pass


class LircDisconnected(Exception):
    pass


class LircClient:
    """Class for lirc socket."""

    def __init__(
        self,
        args: dict[str, Any],
        hass_auth_token: str = None,
    ):
        self._hass_base_uri = args["hass_base_uri"]
        self._lirc_sock_path = args.get("lirc_sock_path", DEF_LIRC_SOCK_PATH)
        self._min_repeat_time_ms = args.get(
            "min_repeat_time_ms", DEF_MIN_REPEAT_TIME_MS
        )
        self._hass_auth_token = hass_auth_token

        self.api_connected = False
        self._lirc_sock = None
        self._last_event_timestamp = 0

    def connect(self) -> None:
        """Connect to LIRC socket."""
        if not self._lirc_sock:
            _LOGGER.debug("connecting to LIRC socket %s", self._lirc_sock_path)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self._lirc_sock_path)
            self._lirc_sock = sock
        else:
            _LOGGER.warning("LIRC socket %s already connected", self._lirc_sock_path)
        _LOGGER.info("connected to LIRC socket %s", self._lirc_sock_path)

    def disconnect(self) -> None:
        """Disconnect from LIRC socket."""
        if self._lirc_sock:
            self._lirc_sock.close()
            self._lirc_sock = None

    def get_event(self) -> bytes:
        """Get an LIRC event from the socket."""
        return self._lirc_sock.recv(SOCK_BUFSIZE)

    def send_upstream_event(self, _lirc_key: str) -> None:
        raise NotImplementedError

    def send_event(self, event: bytes) -> None:
        """Send an LIRC event upstream to Home Assistant."""
        event_timestamp = time.time()
        event_timedelta_ms = (event_timestamp - self._last_event_timestamp) * 1000

        event_info = str(event).split(" ", 4)
        repeat_flag = event_info[1] != "0"
        lirc_key = event_info[2]

        if repeat_flag and event_timedelta_ms < self._min_repeat_time_ms:
            ## Ignore repeated key
            _LOGGER.debug("ignoring repeated key: %s", lirc_key)
            return

        if self._last_event_timestamp == 0:
            event_timedelta_ms = 0  ## special case first event
        _LOGGER.info("firing event: %s (+%dms)", lirc_key, round(event_timedelta_ms))
        self._last_event_timestamp = event_timestamp
        self.send_upstream_event(lirc_key)

    def event_loop(self) -> None:
        """Get LIRC events and send upstream."""
        while True:
            try:
                ## Read a key event from the socket
                event = self.get_event()
                _LOGGER.debug("received LIRC event: '%s'", event)
            except OSError as e:
                ## Socket error, mark disconnected
                _LOGGER.error("could not read from LIRC: %s", e)
                raise LircDisconnected

            if event:
                self.send_event(event)  ## Send event to upstream
            else:
                _LOGGER.error(f"empty event received from LIRC, reconnecting")
                raise LircDisconnected

    def start_event_loop(self) -> None:
        """Start event loop."""
        self.api_connected = True
        self.event_loop()


class HaRestLircClient(LircClient):
    """LIRC client that sends upstream events to HA via REST API."""

    def __init__(
        self,
        args: dict[str, Any],
        hass_auth_token: str = None,
    ):
        super().__init__(args, hass_auth_token)
        self.rest_session = None
        self.rest_headers = {
            "Content-Type": "application/json",
        }
        if hass_auth_token:
            self.rest_headers |= {
                "Authorization": "Bearer " + hass_auth_token,
            }

    def send_upstream_event(self, lirc_key: str) -> None:
        """Send an LIRC event to Home Assistant via REST API."""
        try:
            data = '{"' + BUTTON_NAME + '":"' + lirc_key + '"}'
            result = self.rest_session.post(
                self._hass_base_uri + DEF_REST_API_PATH + EVENT_IR_COMMAND_RECEIVED,
                headers=self.rest_headers,
                data=data,
            )
            result.raise_for_status()
        except requests.exceptions.RequestException as e:
            _LOGGER.error("could not send key to Home Assistant REST API: %s", e)
            raise APIError

    def start_event_loop(self) -> None:
        """Set up REST requests session and start event loop."""
        with requests.Session() as req:
            self.rest_session = req
            super().start_event_loop()
        self.rest_session = None


class HaWsLircClient(LircClient):
    """LIRC client that sends upstream events to HA via Websockets API."""

    def __init__(
        self,
        args: dict[str, Any],
        hass_auth_token: str = None,
    ):
        super().__init__(args, hass_auth_token)
        self.ws = None
        self.ws_next_id = 1

    def ws_recv_msg(self, msg_type: str = None, timeout: float = None) -> dict:
        """Wait for the next Websocket message."""
        if not self.ws:
            _LOGGER.error("websocket not available")
            raise websockets.exceptions.ConnectionClosedError

        if msg_type is None:
            return json.loads(self.ws.recv(timeout=timeout))

        recv_msg = {}
        recv_msg_type = None
        while True:
            recv_msg = json.loads(self.ws.recv(timeout=timeout))
            recv_msg_type = recv_msg.get("type")
            if recv_msg_type == msg_type:
                break
            _LOGGER.debug(
                "discarding unexpected message type: %s (waiting for type: %s)",
                recv_msg_type,
                msg_type,
            )

        return recv_msg

    def ws_send_msg(
        self,
        msg_type: str,
        msg_data: dict,
        response_msg_type: str = None,
        timeout: float = None,
        include_id: bool = True,
    ) -> dict | None:
        """Send a Websocket message and wait for a response."""
        if not self.ws:
            _LOGGER.error("websocket not available")
            raise websockets.exceptions.ConnectionClosedError

        send_msg = {"type": msg_type}
        msg_id = None
        if include_id:
            msg_id = self.ws_next_id
            self.ws_next_id += 1
            send_msg |= {"id": msg_id}

        send_msg |= msg_data
        self.ws.send(json.dumps(send_msg))
        if not response_msg_type:
            if include_id:
                return {"id": msg_id}
            return None

        recv_msg_id = None
        while True:
            recv_msg = self.ws_recv_msg(msg_type=response_msg_type, timeout=timeout)
            recv_msg_id = recv_msg.get("id")
            if recv_msg_id == msg_id:
                break
            _LOGGER.debug(
                "discarding unexpected response message for id: %d", recv_msg_id
            )

        return recv_msg

    def send_upstream_event(self, lirc_key: str) -> None:
        """Send an LIRC event to Home Assistant via Websockets API."""
        try:
            ws_msg = {
                "event_type": EVENT_IR_COMMAND_RECEIVED,
                "event_data": {BUTTON_NAME: lirc_key},
            }
            self.ws_send_msg(
                "fire_event", ws_msg, response_msg_type="result", timeout=DEF_TIMEOUT
            )
        except websockets.exceptions.WebSocketException as e:
            _LOGGER.error("could not send key to Home Assistant Websockets API: %s", e)
            raise APIError

    def ws_do_auth(self) -> None:
        """Authenticate with Websocket server."""
        self.ws_recv_msg("auth_required", timeout=DEF_TIMEOUT)
        self.ws_send_msg(
            "auth", {"access_token": self._hass_auth_token}, include_id=False
        )
        ws_recv_msg = self.ws_recv_msg(timeout=DEF_TIMEOUT)
        _LOGGER.info("ws_recv_msg: %s", ws_recv_msg)
        if ws_recv_msg.get("type") != "auth_ok":
            ws_error_msg = ws_recv_msg.get("message") or str(ws_recv_msg)
            _LOGGER.error("could not authenticate with Websocket API: %s", ws_error_msg)
            raise APIError

    def start_event_loop(self) -> None:
        """Start event loop."""
        with ws_connect(self._hass_base_uri + DEF_WS_API_PATH) as ws:
            self.ws = ws
            self.ws_do_auth()
            super().start_event_loop()
        self.ws = None


def get_backoff_delay(retry_count: int, delay_max: float) -> float:
    """Calculate exponential backoff with random jitter delay."""
    delay = round(
        min(delay_max, (2**retry_count)) - (random.randint(0, 1000) / 1000),
        3,
    )
    return delay


def main_loop(args: dict[str, Any]) -> None:
    ## Create LIRC client
    hass_auth_token_file = args.get("hass_auth_token_file", None)
    if hass_auth_token_file:
        with open(hass_auth_token_file, "r") as file:
            hass_auth_token = file.read().replace("\n", "")
    else:
        hass_auth_token = args.get("hass_auth_token", None)
    max_reconnect_delay = args.get("max_reconnect_delay", DEF_MAX_RECONNECT_DELAY)
    if args.get("use_ws", DEF_USE_WS):
        lirc_client = HaWsLircClient(args, hass_auth_token)
    else:
        lirc_client = HaRestLircClient(args, hass_auth_token)
    lirc_connected = False
    lirc_retry = 0
    api_retry = 0

    while True:
        ## Connect to LIRC
        if not lirc_connected:
            try:
                lirc_client.connect()
                lirc_connected = True
                lirc_retry = 0
            except OSError as e:
                if lirc_retry == 0:
                    _LOGGER.error("could not connect to LIRC: %s, retrying", e)
                else:
                    _LOGGER.debug("LIRC connect retry #%d failed: %s", lirc_retry, e)
                lirc_delay = get_backoff_delay(lirc_retry, max_reconnect_delay)
                _LOGGER.debug("waiting %.3fs before retrying LIRC", lirc_delay)
                time.sleep(lirc_delay)
                lirc_retry += 1
                continue

        ## Start main event loop and handle errors/disconnects
        try:
            lirc_client.start_event_loop()
        except LircDisconnected:
            lirc_connected = False
            lirc_client.disconnect()
        except APIError:
            if lirc_client.api_connected:
                api_retry = 0
                lirc_client.api_connected = False
            api_delay = get_backoff_delay(api_retry, max_reconnect_delay)
            _LOGGER.debug("waiting %.3fs before retrying API connection", api_delay)
            time.sleep(api_delay)
            api_retry += 1
            continue
        except Exception as e:
            lirc_client.disconnect()
            raise e


def sigterm_handler(_signal, _frame) -> None:
    _LOGGER.warning("SIGTERM received, exiting")
    raise ExitApp


## https://stackoverflow.com/questions/14117415/in-python-using-argparse-allow-only-positive-integers
def check_positive(value: Any) -> int:
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("%s not an integer" % value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError("%s is an invalid positive int value" % value)
    return ivalue


def parse_args() -> dict[str, Any]:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)
    parser.add_argument(
        "hass_base_uri", help="Home Assistant base URI for REST/websocket API"
    )
    parser.add_argument(
        "-w",
        "--use-ws",
        action=argparse.BooleanOptionalAction,
        help="Use Home Assistant websocket API",
        default=DEF_USE_WS,
    )
    parser.add_argument(
        "-a",
        "--hass-auth-token",
        help="Home Assistant authorisation token",
    )
    parser.add_argument(
        "-A",
        "--hass-auth-token-file",
        help="Path to file containing Home Assistant authorisation token",
    )
    parser.add_argument(
        "-l",
        "--lirc-sock-path",
        help="LIRC socket location",
        default=DEF_LIRC_SOCK_PATH,
    )
    parser.add_argument(
        "-c",
        "--max-reconnect-delay",
        type=check_positive,
        help="Maximum client reconnection delay",
        default=DEF_MAX_RECONNECT_DELAY,
    )
    parser.add_argument(
        "-r",
        "--min-repeat-time-ms",
        type=check_positive,
        help="Minimum time between repeated keystrokes (ms)",
        default=DEF_MIN_REPEAT_TIME_MS,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="set logging verbosity, repeat to increase",
        action="count",
    )
    parser.add_argument(
        "-V",
        "--version",
        help="show application version",
        action="version",
        version="%(prog)s " + VERSION,
    )
    args = vars(parser.parse_args())
    return args


def main() -> None:
    log_level = DEF_LOG_LEVEL
    args = parse_args()
    log_level_count = args.get("verbose", 0)
    log_level_name = "(none)"
    if log_level_count >= 2:
        log_level = logging.DEBUG
        log_level_name = "debug"
    elif log_level_count >= 1:
        log_level = logging.INFO
        log_level_name = "info"

    ## Enable logging
    log_format = "%(asctime)s %(levelname)s: %(message)s"
    log_format_color = "%(log_color)s" + log_format
    date_format = "%Y-%m-%d %H:%M:%S"
    try:
        import colorlog

        colorlog.basicConfig(
            level=log_level, format=log_format_color, datefmt=date_format
        )
    except:
        logging.basicConfig(level=log_level, format=log_format, datefmt=date_format)
    _LOGGER.info("setting log level to %s", log_level_name)
    _LOGGER.debug("args: %s", args)

    ## Catch SIGTERM and start main loop
    signal.signal(signal.SIGTERM, sigterm_handler)
    try:
        main_loop(args)
    except KeyboardInterrupt:
        _LOGGER.warning("Keyboard interrupt, exiting")
        exit(255)
    except ExitApp:
        exit(0)
    except Exception as e:
        _LOGGER.error(f"Exception: {e}")
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
