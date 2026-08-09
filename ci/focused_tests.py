from __future__ import annotations

import argparse
import subprocess
import sys

from ci.selection import load_selection


def run(selection_path: str) -> None:
    selection = load_selection(selection_path)
    if selection["profile"] != "focused":
        raise ValueError("the focused runner requires profile=focused")
    subprocess.run(
        [sys.executable, "manage.py", "test", *selection["test_labels"]],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", required=True)
    args = parser.parse_args()
    run(args.selection)


if __name__ == "__main__":
    main()
