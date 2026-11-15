"""Output formatting helpers for ``dtc``."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import click


def emit(data: Any, fmt: str = "json", *, columns: list[str] | None = None) -> None:
    if fmt == "table" and isinstance(data, list) and columns:
        if not data:
            click.echo("(no results)")
            return
        widths = {col: len(col) for col in columns}
        str_rows = []
        for row in data:
            str_row = {
                col: str(row.get(col, "") if row.get(col) is not None else "")
                for col in columns
            }
            for col in columns:
                widths[col] = max(widths[col], len(str_row[col]))
            str_rows.append(str_row)
        header = "  ".join(col.ljust(widths[col]) for col in columns)
        sep = "  ".join("-" * widths[col] for col in columns)
        click.echo(header)
        click.echo(sep)
        for str_row in str_rows:
            click.echo("  ".join(str_row[col].ljust(widths[col]) for col in columns))
        return
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


def format_csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames})
    return buf.getvalue()
