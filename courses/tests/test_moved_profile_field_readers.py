"""No production module reads a moved course-platform field off the user.

Plan issue D3.1b (DataTalksClub/website#391) asks for regression coverage that
forces every reader of the ten moved fields through ``learner_profile``.  The
issue names three project-gallery view modules; they read none of these fields
today, and pinning three filenames would not stop the next module that starts
rendering a submitter's role or certificate name from reading the user column
instead.

So the pin is the rule rather than the file list: no tracked production module
and no template may read one of the ten moved fields off a user-shaped object.
Reading them off a ``LearnerProfile`` (or an ``Enrollment``/``CourseRegistration``
row that owns its own copy) is exactly what the move asks for, and stays allowed.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]

MOVED_FIELDS = frozenset(
    {
        "role",
        "certificate_name",
        "country",
        "region",
        "registration_role",
        "github_url",
        "linkedin_url",
        "personal_website_url",
        "about_me",
        "dark_mode",
    }
)

#: Names that hold a user-shaped object across this codebase.  A field read off
#: one of these is a read of the auth user model.
USER_NAMES = frozenset(
    {
        "user",
        "account",
        "actor",
        "auth_user",
        "learner",
        "member",
        "request_user",
        "source_user",
        "student",
        "survivor",
        "target_user",
        "viewer",
    }
)

#: Paths that legitimately still name the columns: the migrations that move
#: them, the model that received them, and the tests that pin the move.
EXEMPT_PREFIXES = (
    "accounts/migrations/",
    "accounts_ext/migrations/",
    "courses/migrations/",
    "courses/models/learner_profile.py",
)


#: Reviewed, phase-scoped exemptions.  Each entry names the module, why it is
#: not switched yet, and the issue that removes it.  An empty mapping is the
#: end state.
REVIEWED_UNSWITCHED: dict[str, str] = {}


def _tracked(*patterns: str) -> tuple[Path, ...]:
    listing = subprocess.run(
        ("git", "-C", str(ROOT), "ls-files", "-z", *patterns),
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return tuple(Path(entry) for entry in listing.split("\0") if entry)


def _is_production_module(relative: Path) -> bool:
    text = relative.as_posix()
    if text.startswith(EXEMPT_PREFIXES):
        return False
    return not any(
        part.startswith(("test_", "tests_")) or part in {"tests", "playwright_tests", "e2e"}
        for part in relative.parts
    )


def _user_expression(node: ast.expr) -> bool:
    """True when ``node`` evaluates to a user-shaped object."""

    if isinstance(node, ast.Name):
        return node.id in USER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in USER_NAMES
    if isinstance(node, ast.Call):
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
        return name in {"get_user_model"} or name in USER_NAMES
    return False


class MovedProfileFieldReaderTests(SimpleTestCase):
    def test_no_production_module_reads_a_moved_field_off_a_user(self):
        offenders = []
        for relative in _tracked("*.py"):
            if not _is_production_module(relative):
                continue
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr not in MOVED_FIELDS:
                    continue
                if not _user_expression(node.value):
                    continue
                if relative.as_posix() in REVIEWED_UNSWITCHED:
                    continue
                offenders.append(f"{relative.as_posix()}:{node.lineno} .{node.attr}")
        self.assertEqual(
            offenders,
            [],
            "these read a moved course-platform field off the user model; route them "
            "through courses.models.learner_profile instead: " + repr(sorted(offenders)),
        )

    def test_every_exemption_names_a_tracked_module(self):
        # An exemption that outlives its module silently stops meaning anything.
        tracked = {path.as_posix() for path in _tracked("*.py")}
        self.assertEqual(sorted(set(REVIEWED_UNSWITCHED) - tracked), [])

    def test_templates_read_moved_fields_only_off_an_allowed_holder(self):
        # Allowlist rather than a user-name denylist: the reader that this
        # rule first caught rendered the fields off a context variable called
        # ``public_profile`` that held the user, which no denylist would name.
        allowed_holders = frozenset(
            {
                "form",              # a bound form field, not a stored value
                "profile",
                "learner_profile",
                "public_profile",    # the LearnerProfile row, see the view
                "enrollment",        # owns its own certificate_name copy
                "registration",      # owns its own snapshot
                "submission",
            }
        )
        # Only the fields whose name is unambiguous in a template. ``country``,
        # ``region``, ``role`` and ``registration_role`` also name loop
        # variables, filter dictionaries and editorial rows that have nothing
        # to do with an account, so the Python rule above is their guard.
        person_fields = MOVED_FIELDS - {"country", "region", "role", "registration_role"}
        pattern = re.compile(
            r"([A-Za-z_][\w.]*)\.(" + "|".join(sorted(person_fields)) + r")\b"
        )
        comments = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}", re.S)
        offenders = []
        for relative in _tracked("*.html"):
            body = comments.sub("", (ROOT / relative).read_text(encoding="utf-8"))
            for holder, field in pattern.findall(body):
                if holder.rsplit(".", 1)[-1] in allowed_holders:
                    continue
                offenders.append(f"{relative.as_posix()} {holder}.{field}")
        self.assertEqual(
            sorted(set(offenders)),
            [],
            "these templates render a moved field off something that is not a "
            "LearnerProfile row or a model owning its own copy",
        )
