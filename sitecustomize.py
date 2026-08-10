"""Carry the ordinary-test network denial into Python subprocesses."""

from __future__ import annotations

import os

if os.environ.get("DTC_TEST_NETWORK_DENY") == "1":
    from test_support.network import install_process_network_guard

    install_process_network_guard()
