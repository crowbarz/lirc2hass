"""Bridge LIRC input events to Home Assistant via REST API."""

import signal
import socket
import logging
import traceback
import time
import random
import argparse
import requests

APP_NAME = "lirc2hass"
VERSION = "0.1.0"
_LOGGER = logging.getLogger(APP_NAME)
DEF_LOG_LEVEL = logging.WARN

DEF_LIRC_SOCK_PATH = "/var/run/lirc/lircd"
DEF_MAX_RECONNECT_DELAY = 64
DEF_MIN_REPEAT_TIME_MS = 740
SOCK_BUFSIZE = 4096
CACHED_EVENT_EXPIRE_TIME = 5

BUTTON_NAME = "button_name"
EVENT_IR_COMMAND_RECEIVED = "ir_command_received"


class ExitApp(Exception):
    pass


class RequestsError(Exception):
    pass


class LircDisconnected(Exception):
    pass


class LircClient:
    """Class for lirc socket."""

    _last_event_timestamp = 0

    def __init__(
        self,
        sock_path,
        hass_url,
        hass_auth_token=None,
        min_repeat_time_ms=DEF_MIN_REPEAT_TIME_MS,
    ):
        self._sock_path = sock_path
        self._sock = None
        self._hass_url = hass_url
        self._min_repeat_time_ms = min_repeat_time_ms
        self._rest_headers = {
            "Content-Type": "application/json",
        }
        if hass_auth_token:
            self._rest_headers.update(
                {
                    "Authorization": "Bearer " + hass_auth_token,
                }
            )

    def connect(self):
        """Connect to LIRC socket."""
        if not self._sock:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self._sock_path)
            self._sock = sock
        else:
            _LOGGER.warning("LIRC socket %s already connected", self._sock_path)

    def disconnect(self):
        """Disconnect from LIRC socket."""
        if self._sock:
            self._sock.close()
            self._sock = None

    def send_event(self, event):
        """Send an LIRC event to Home Assistant via the REST API."""
        event_timestamp = time.time()
        event_timedelta_ms = (event_timestamp - LircClient._last_event_timestamp) * 1000
        try:
            event_info = str(event).split(" ", 4)
            repeat_flag = event_info[1] != "0"
            lirc_key = event_info[2]

            if repeat_flag and event_timedelta_ms < self._min_repeat_time_ms:
                ## Ignore repeated key
                _LOGGER.debug("ignoring repeated key: %s", lirc_key)
                return

            LircClient._last_event_timestamp = event_timestamp

            rest_data = '{"' + BUTTON_NAME + '":"' + lirc_key + '"}'
            _LOGGER.info(
                "firing event: %s (+%dms)", lirc_key, round(event_timedelta_ms)
            )
            rest_res = requests.post(
                self._hass_url + "/api/events/" + EVENT_IR_COMMAND_RECEIVED,
                headers=self._rest_headers,
                data=rest_data,
            )
            rest_res.raise_for_status()
        except requests.exceptions.RequestException as e:
            _LOGGER.error("could not send key to Home Assistant API: %s", e)
            raise RequestsError

    def event_loop(self):
        """Main event loop."""
        while True:
            try:
                ## Read a key event from the socket
                event = self._sock.recv(SOCK_BUFSIZE)
                _LOGGER.debug("received LIRC event: '%s'", event)
            except OSError as e:
                ## Socket error, mark disconnected
                _LOGGER.error("could not read from LIRC: %s", e)
                raise LircDisconnected

            if event:
                self.send_event(event)  ## Send event to Home Assistant
            else:
                _LOGGER.error(f"empty event received from LIRC, reconnecting")
                raise LircDisconnected


def get_backoff_delay(retry_count, delay_max):
    """Calculate exponential backoff with random jitter delay."""
    delay = round(
        min(delay_max, (2 ** retry_count)) - (random.randint(0, 1000) / 1000),
        3,
    )
    return delay


def main_loop(args):
    ## Create LIRC client
    hass_url = args["hass_url"]
    hass_auth_token_file = args.get("hass_auth_token_file", None)
    if hass_auth_token_file:
        with open(hass_auth_token_file, "r") as file:
            hass_auth_token = file.read().replace("\n", "")
    else:
        hass_auth_token = args.get("hass_auth_token", None)
    max_reconnect_delay = args.get("max_reconnect_delay", DEF_MAX_RECONNECT_DELAY)
    min_repeat_time_ms = args.get("min_repeat_time_ms", DEF_MIN_REPEAT_TIME_MS)
    lirc_sock_path = args.get("lirc_sock_path", DEF_LIRC_SOCK_PATH)
    lirc_client = LircClient(
        lirc_sock_path, hass_url, hass_auth_token, min_repeat_time_ms
    )
    lirc_connected = False
    lirc_retry = 0

    while True:
        ## Connect to LIRC
        if not lirc_connected:
            try:
                _LOGGER.debug("connecting to LIRC socket %s", lirc_sock_path)
                lirc_client.connect()
                _LOGGER.info("connected to LIRC socket %s", lirc_sock_path)
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

        try:
            lirc_client.event_loop()
        except LircDisconnected:
            lirc_connected = False
            lirc_client.disconnect()
        except RequestsError:
            continue
        except Exception as e:
            lirc_client.disconnect()
            raise e


def sigterm_handler(signal, frame):
    _LOGGER.warning("SIGTERM received, exiting")
    raise ExitApp


## https://stackoverflow.com/questions/14117415/in-python-using-argparse-allow-only-positive-integers
def check_positive(value):
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("%s not an integer" % value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError("%s is an invalid positive int value" % value)
    return ivalue


parser = argparse.ArgumentParser(...)
parser.add_argument("foo", type=check_positive)


def parse_args():
    """ Parse command line arguments. """
    parser = argparse.ArgumentParser(argument_default=argparse.SUPPRESS)
    parser.add_argument("hass_url", help="Home Assistant base URL")
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


def main():
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
    try:
        ## Catch SIGTERM and enable logging
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s[%(threadName)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _LOGGER.setLevel(log_level)
        _LOGGER.info("setting log level to %s", log_level_name)
        _LOGGER.debug("args: %s", args)
        signal.signal(signal.SIGTERM, sigterm_handler)

        ## Start main loop
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