[![PyPI](https://img.shields.io/pypi/v/lirc2hass.svg)](https://pypi.python.org/pypi/lirc2hass/)

# lirc2hass

Listen for input events detected by [InputLIRC](https://packages.debian.org/buster/inputlirc) and generate events in [Home Assistant](https://www.home-assistant.io/). Primarily useful for triggering Home Assistant automations using Logitech Harmony remotes via an LIRC-compatible remote control.

The `lirc2hass` daemon generates Home Assistant events when input events are received from an IR receiver by InputLIRC. Home Assistant automations can be triggered by these events, which can then call any services available to Home Assistant, such as turning lights on and off, close blinds, etc.

It is the missing link that allows buttons on the Logitech Harmony remote to trigger any device that can be controlled by Home Assistant, where the Home Assistant server cannot be physically collocated with the Harmony Hub. (If it were, then the [LIRC integration](https://www.home-assistant.io/integrations/lirc/) can be used with an IR blaster connected directly to the HA server.)

## Installation

`lirc2hass` should run on most Linux installations (tested on Rasperry Pi OS) and requires the following:

- an IR receiver that is supported by inputlirc and connected locally to the system
- `inputlirc` package installed and configured on the underlying operating system to read and process input from the IR receiver

Install the latest release of this package via PyPi:
```yaml
pip install lirc2hass
```

Run the daemon using `lirc2hass hass_base_url -a `, where `hass_base_url` is the base URL for your instance of Home Assistant.

### `systemd` configuration

Configure `systemd` to start the daemon at boot by installing `lirc2hass.service` into `/etc/systemd/system` after editing to suit your installation.

If the daemon is run as a non-root user, ensure that user has the necessary privileges to read from the LIRC socket. On Debian-based systems, membership of group `input` is required to read the LIRC socket, located at `/var/run/lirc/lircd`.

### Logitech Harmony configuration

- Position IR receiver to reliably receive IR commands from Harmony (or mini-IR blaster)
- In the myHarmony app, add a `Windows MCE controller` device to your setup
- Include the device for each activity that will be sending commands using the `Windows MCE controller`
- Configure buttons on the Harmony remote to send commands using the device

### Home Assistant configuration

- (optional) Add new Home Assistant admin user. An admin user is required to fire events on Home Assistant via the API
- Create long-lived authorisation token for the admin user
- When starting `lirc2hass`, pass the token using `--hass-auth-token` or `--hass-auth-token-file` options

## Usage

`lirc2hass [-h] [-w | --use-ws | --no-use-ws] [-a HASS_AUTH_TOKEN] [-A HASS_AUTH_TOKEN_FILE] [-l LIRC_SOCK_PATH] [-c MAX_RECONNECT_DELAY] [-r MIN_REPEAT_TIME_MS] [-v] [-V] hass_base_uri`

| **Option** | **Type/Default** | **Description**
| -- | -- | --
| _hass_base_uri_ | | The URI for the Home Assistant REST API, or the Home Assistant websocket API if `--use-ws` is enabled. When using the websocket API, the URI should be in the form of `ws://host:port` or `wss://host:port` for an SSL connection
| <nobr>`-w` \| `--[no-]use-ws`</nobr> | false | Use Home Assistant websocket API instead of the REST API
| <nobr>`-a` \| `--hass-auth-token`</nobr> | *auth_token* | Enable sending of authorisation header, using *auth_token* as the token
| <nobr>`-A` \| `--hass-auth-token-file`</nobr> | *filename* | Read authorisation token from *filename*
| <nobr>`-l` \| `--lirc-sock-path`</nobr> | `/var/run/lirc/lircd` | Set path to LIRC socket
| <nobr>`-c` \| `--max-reconnect-delay`</nobr> | `64` | Set maximum reconnect delay for the LIRC socket. The daemon reconnects automatically on disconnection using an exponential backoff delay with this value as the maximum
| <nobr>`-r` \| `--min-repeat-time-ms`</nobr> | `740` | Ignore repeated keystrokes that are generated within the specified time (in ms)
| <nobr>`-v` \| `--verbose`</nobr> | | Set logging verbosity (repeat to increase)
| <nobr>`-V` \| `--version`</nobr> | | Show currently installed version
