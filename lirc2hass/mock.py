"""LIRCClient mock classes."""

import logging
import time
import random

from .lirc2hass import LircClient

_LOGGER = logging.getLogger(__name__)


class MockLircClient(LircClient):
    def connect(self):
        _LOGGER.debug("mocking LIRC socket connect")

    def disconnect(self):
        _LOGGER.debug("mocking LIRC socket disconnect")

    def get_event(self):
        time.sleep(random.randint(1, 5))
        return f"0 0 TEST_KEY{random.randint(1,9)} a b c"
