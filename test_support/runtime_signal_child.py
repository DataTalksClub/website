from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from test_support.runtime import TestRuntime


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    runtime = TestRuntime.acquire(repository)
    layout = runtime.worker("main")
    connection = sqlite3.connect(layout.database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE signal_probe (value TEXT NOT NULL)")
    connection.execute("INSERT INTO signal_probe VALUES ('synthetic')")
    connection.commit()
    print(json.dumps({"run_root": str(runtime.run_root)}), flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
