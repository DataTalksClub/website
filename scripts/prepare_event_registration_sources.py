#!/usr/bin/env python3
"""Prepare protected event exports for the aggregate-only migration adapters.

The output remains attendee-level protected data and must stay below the main
checkout's gitignored ``.local/migration-data`` directory.  This command never
prints event names, provider identifiers, archive members, or attendee fields.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


class PreparationError(ValueError):
    pass


def _main_checkout_root() -> Path:
    common_dir = subprocess.run(
        ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    ).stdout.strip()
    return Path(common_dir).resolve().parent


def _tree_checksum(root: Path) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def prepare_luma(
    source: Path, destination: Path, *, replace: bool = False
) -> dict[str, object]:
    csv_files = {path.stem: path for path in source.glob("*.csv")}
    checkpoints = {path.stem: path for path in (source / "_json").glob("*.json")}
    if not csv_files or set(csv_files) != set(checkpoints):
        raise PreparationError("luma_pair_mismatch")
    if destination.exists():
        if not replace:
            raise PreparationError("luma_destination_exists")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    statuses: Counter[str] = Counter()
    rows = 0
    try:
        for stem in sorted(csv_files):
            checkpoint = json.loads(checkpoints[stem].read_text(encoding="utf-8"))
            event = checkpoint.get("event") if isinstance(checkpoint, dict) else None
            event_id = event.get("id") if isinstance(event, dict) else None
            event_url = event.get("url") if isinstance(event, dict) else None
            if not isinstance(event_id, str) or not event_id:
                raise PreparationError("luma_checkpoint_invalid")
            if not isinstance(event_url, str) or not event_url.startswith("https://"):
                raise PreparationError("luma_checkpoint_invalid")
            with csv_files[stem].open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                if reader.fieldnames is None or any(
                    field not in reader.fieldnames
                    for field in ("event_id", "guest_id", "approval_status")
                ):
                    raise PreparationError("luma_csv_invalid")
                for row in reader:
                    rows += 1
                    status = row.get("approval_status", "").casefold()
                    if not status:
                        raise PreparationError("luma_csv_invalid")
                    statuses[status] += 1
            shutil.copyfile(csv_files[stem], destination / f"{stem}.csv")
            (destination / f"{stem}.json").write_text(
                json.dumps(
                    {"schema_version": 1, "event_id": event_id, "event_url": event_url},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
    except Exception:
        shutil.rmtree(destination)
        raise
    return {
        "event_total": len(csv_files),
        "row_total": rows,
        "status_totals": dict(sorted(statuses.items())),
        "tree_sha256": _tree_checksum(destination),
    }


def prepare_eventbrite(
    source: Path, destination: Path, *, replace: bool = False
) -> dict[str, object]:
    if destination.exists():
        if not replace:
            raise PreparationError("eventbrite_destination_exists")
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    csv_total = xlsx_total = 0
    rows = 0
    statuses: Counter[str] = Counter()
    try:
        with ZipFile(source) as incoming, ZipFile(
            destination, "w", compression=ZIP_DEFLATED, compresslevel=9
        ) as outgoing:
            for source_info in incoming.infolist():
                name = source_info.filename
                if name == "eventbrite/events.xlsx":
                    output_name = "events.xlsx"
                    xlsx_total += 1
                elif (
                    name.startswith("eventbrite/csv/")
                    and name.removeprefix("eventbrite/csv/").removesuffix(".csv").isdigit()
                    and name.endswith(".csv")
                ):
                    output_name = name.removeprefix("eventbrite/csv/")
                    csv_total += 1
                    text = incoming.read(source_info).decode("utf-8-sig")
                    reader = csv.DictReader(text.splitlines(), strict=True)
                    if reader.fieldnames is None or "Attendee Status" not in reader.fieldnames:
                        raise PreparationError("eventbrite_csv_invalid")
                    for row in reader:
                        rows += 1
                        status = row.get("Attendee Status", "").casefold()
                        if not status:
                            raise PreparationError("eventbrite_csv_invalid")
                        statuses[status] += 1
                else:
                    raise PreparationError("eventbrite_archive_invalid")
                payload = incoming.read(source_info)
                info = ZipInfo(output_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                outgoing.writestr(info, payload, compresslevel=9)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {
        "csv_total": csv_total,
        "xlsx_total": xlsx_total,
        "row_total": rows,
        "status_totals": dict(sorted(statuses.items())),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def main() -> None:
    default_root = _main_checkout_root() / ".local" / "migration-data" / "events"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--luma-source", type=Path)
    parser.add_argument("--eventbrite-source", type=Path)
    parser.add_argument("--destination", type=Path, default=default_root)
    parser.add_argument(
        "--replace", action="store_true", help="replace only the prepared adapter outputs"
    )
    args = parser.parse_args()
    if args.luma_source is None and args.eventbrite_source is None:
        parser.error("provide --luma-source and/or --eventbrite-source")

    report: dict[str, object] = {"schema_version": 1}
    if args.luma_source is not None:
        report["luma"] = prepare_luma(
            args.luma_source.resolve(),
            args.destination.resolve() / "luma-aggregate-v1",
            replace=args.replace,
        )
    if args.eventbrite_source is not None:
        report["eventbrite"] = prepare_eventbrite(
            args.eventbrite_source.resolve(),
            args.destination.resolve() / "eventbrite" / "aggregate-v1.zip",
            replace=args.replace,
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
