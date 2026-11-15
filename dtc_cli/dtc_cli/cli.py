"""CLI entry point for ``dtc``."""

from __future__ import annotations

import sys

import click

from dtc_cli import __version__
from dtc_cli.client import APIError
from dtc_cli.commands import commands


class DtcGroup(click.Group):
    """Custom group with a short header above the standard help."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_heading("dtc -- DataTalks.Club CLI")
        formatter.write_paragraph()
        formatter.write_text(
            "Command-line client for the DataTalks.Club management API "
            "(https://prod.datatalks.club). "
            "Run 'dtc <group> --help' to see commands and flags."
        )
        super().format_help(ctx, formatter)


@click.group(cls=DtcGroup)
@click.version_option(__version__, prog_name="dtc")
def cli() -> None:
    """DataTalks.Club website management CLI."""


for _cmd in commands:
    cli.add_command(_cmd)


def main() -> None:
    try:
        cli()
    except APIError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
