"""CLI command groups."""

from dtc_cli.commands.auth import login_cmd, logout_cmd, whoami_cmd
from dtc_cli.commands.courses import courses
from dtc_cli.commands.events import events

commands = [login_cmd, logout_cmd, whoami_cmd, courses, events]
