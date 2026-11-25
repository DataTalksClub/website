"""Carry the ordinary-test network and machine-local-path denials into Python subprocesses."""

from __future__ import annotations

import os

if os.environ.get("DTC_TEST_NETWORK_DENY") == "1":
    from test_support.network import install_process_network_guard

    install_process_network_guard()

if os.environ.get("DTC_TEST_LOCAL_PATH_DENY") == "1":
    from test_support.local_paths import install_process_local_path_guard

    install_process_local_path_guard()
