#!/usr/bin/env python3
"""Fail when application code, ordinary CI, or specs regain backend-specific behavior."""

from __future__ import annotations

import html
import os
import re
import stat
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SPECIFICATION_ROOT = ROOT / "_docs" / "specs"
FORMER_AWS_SPEC_NAME = "08-aws-" + "sand" + "box-terraform.md"
AUTHORITATIVE_SPECIFICATIONS = (
    "README.md",
    "01-platform-architecture.md",
    "02-url-link-seo-compatibility.md",
    "03-github-content-and-people.md",
    "04-courses-and-cohorts.md",
    "05-events-registration-email.md",
    "06-studio-and-admin-api.md",
    "07-security-privacy-operations.md",
    "08-aws-development-terraform.md",
    "09-migration-rollout-roadmap.md",
    "10-verification-strategy.md",
    "open-decisions.md",
)
COMPATIBILITY_SPECIFICATIONS = (FORMER_AWS_SPEC_NAME,)
EXPECTED_SPECIFICATIONS = AUTHORITATIVE_SPECIFICATIONS + COMPATIBILITY_SPECIFICATIONS
ORDINARY_JOBS = ("quality", "django", "playwright", "container")
FULL_DJANGO_COMMANDS = ("make test", "make test-django-full")
APPLICATION_ROOTS = (
    "accounts",
    "api",
    "studio_courses",
    "content",
    "content_sync",
    "core",
    "course_management",
    "courses",
    "data",
    "email_app",
    "events",
    "jobs",
    "management_api",
    "management_auth",
    "studio",
)
BACKEND_PATTERNS = (
    "connection.vendor",
    "has_select_for_update",
    "select_for_update",
    "django.contrib.postgres",
    "pg_advisory",
    "runsql",
    "create trigger",
    "create function",
    "pragma ",
)
BACKEND_TEST_PATTERNS = (
    "skipunlessdbfeature",
    "connection.vendor",
    "has_select_for_update",
)
# This adapter reads an external, immutable CMP SQLite snapshot. The exception is
# limited to SQLite safety/schema statements; its target persistence still uses
# portable Django models and services, and every other backend token remains banned.
BACKEND_PATTERN_EXCEPTIONS = {
    Path("courses/registration_count_importer.py"): frozenset({"pragma "}),
}
DATABASE_REFERENCE = re.compile(
    r"\b(?:postgres(?:ql)?|psycopg|database_url)\b",
    re.IGNORECASE,
)
POSTGRESQL_REFERENCE = re.compile(r"\bpostgres(?:ql)?\b", re.IGNORECASE)
MARKDOWN_BLOCK_START = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\|)")
CONFIG_ASSIGNMENT = re.compile(r"^[a-z0-9_.-]+\s*[:=]\s*\S", re.IGNORECASE)
SEARCH_BEHAVIOR = re.compile(
    r"(?:\b(?:search|ranking|indexing)\b|\bsearch[_-](?:engine|backend|projection)\b)",
    re.IGNORECASE,
)
CONSTRAINT_OR_TEST_BEHAVIOR = re.compile(
    r"\b(?:constraints?|concurrency|engine behavior|application[- ]tests?|test suites?)\b",
    re.IGNORECASE,
)
TRANSACTION_BEHAVIOR = re.compile(
    r"\b(?:transactions?|serializable|repeatable read|isolation (?:levels?|semantics?)|"
    r"application writes?|row locks?|"
    r"row locking)\b",
    re.IGNORECASE,
)
FOUNDATION_BEHAVIOR = re.compile(
    r"\b(?:foundation|django project|custom user model|local development|ordinary ci)\b",
    re.IGNORECASE,
)
APPLICATION_BEHAVIOR = re.compile(
    r"\b(?:application behavior|django|models?|migrations?|services?|fields?|extensions?|"
    r"indexes?|triggers?|advisory locks?|raw sql|vendor branches?)\b",
    re.IGNORECASE,
)
POSTGRESQL_FEATURE_BEHAVIOR = re.compile(
    r"\b(?:application behavior|fields?|extensions?|indexes?|triggers?|advisory locks?|raw sql|"
    r"vendor branches?)\b",
    re.IGNORECASE,
)

_DATABASE_TOKEN = r"(?:postgres(?:ql)?|psycopg|database_url)"
_ANAPHORIC_DATABASE_TOKEN = (
    r"(?:it|its|that\s+engine|this\s+engine|the\s+engine|"
    r"this\s+database|that\s+database|the\s+database|"
    r"this\s+backend|that\s+backend|the\s+backend)"
)
_DANGEROUS_BEHAVIOR = (
    r"(?:search|ranking|indexing|search[_-](?:engine|backend|projection)|constraints?|"
    r"concurrency|transactions?|serializable|repeatable read|isolation(?: levels?| semantics?)?|"
    r"application writes?|row locks?|row locking|application behavior|django|models?|"
    r"migrations?|services?|fields?|extensions?|indexes?|triggers?|advisory locks?|raw sql|"
    r"vendor branches?|foundation|custom user model|local development|ordinary ci)"
)
_PASSIVE_PROVIDER_ACTION = (
    r"(?:powered|backed|driven|supported|provided|supplied|enforced|implemented|guaranteed|"
    r"hosted|enabled)"
)
_DEPENDENCY_PREDICATE = (
    r"(?:requires?|depend(?:s|ed)?\s+(?:on|upon)|rel(?:y|ies|ied)\s+(?:on|upon)|uses?|needs?|"
    r"run(?:s|ning)?\s+on|"
    r"(?:is|are|was|were|remains?)\s+(?:dependent|reliant)\s+(?:on|upon)|"
    r"(?:is|are|was|were|remains?|has\s+been|have\s+been|had\s+been|is\s+being|"
    r"are\s+being|must\s+be|shall\s+be|will\s+be)\s+"
    rf"{_PASSIVE_PROVIDER_ACTION}\s+by)"
)
_PROVIDER_PREDICATE = (
    r"(?:suppl(?:y|ies|ied|ying)|provid(?:e|es|ed|ing)|enforc(?:e|es|ed|ing)|"
    r"power(?:s|ed|ing)?|back(?:s|ed|ing)?|driv(?:e|es|en|ing)|drove|"
    r"support(?:s|ed|ing)?|implement(?:s|ed|ing)?|guarantee(?:s|d|ing)?|"
    r"host(?:s|ed|ing)?|enabl(?:e|es|ed|ing))"
)
_HISTORY_MARKER = (
    r"(?:historically|previously|formerly|in\s+the\s+past|before\s+(?:the\s+)?migration|"
    r"until\s+(?:the\s+)?migration|prior\s+to(?:\s+(?:the\s+)?migration)?|"
    r"(?:many\s+|several\s+|\d+\s+)?years\s+ago|once)"
)
_CURRENT_MARKER = r"(?:now|still|currently|today|again|at\s+present)"
_HISTORICAL_ACTION = (
    rf"(?:used|ran|run|{_PROVIDER_PREDICATE}|implemented|maintained|exercised|depended|relied)"
)
_SQLITE_SUBJECT = (
    r"(?:(?:the\s+)?sqlite(?:\s+(?:database\s+backend|storage\s+engine|database\s+engine|"
    r"backend|engine))?)"
)
_SQLITE_RESPONSIBILITY_LINK = (
    rf"(?:(?:\s*,?\s+(?:rather\s+than|instead\s+of)\s+{_DATABASE_TOKEN}\s*,?\s*)|"
    rf"(?:\s*,?\s+and\s+not\s+{_DATABASE_TOKEN}\s*,?\s*)|"
    rf"(?:\s*(?:,|-)\s*not\s+{_DATABASE_TOKEN}\s*(?:,|-)\s*)|(?:\s+))"
)
_BOUNDED_CLAUSE = r"[^,.;!?]{0,240}"
_REPORT_QUALIFIER = (
    r"(?:former|obsolete|superseded|forbidden|invalid|rejected|non-normative|disallowed)"
)
_REPORT_NOUN = (
    r"(?:example|specification|wording|statement|language|proposal|requirement|design|text)"
)
MAX_DECODE_PASSES = 16

HYPOTHETICAL_DATABASE_MANDATES = (
    re.compile(
        rf"\b(?:if|assum(?:e|ing)(?:\s+that)?|suppose(?:\s+that)?|imagine)\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:had|should)\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bwere\s+(?:an?\s+)?{_DATABASE_TOKEN}\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwere\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b)"
        rf"(?={_BOUNDED_CLAUSE}\bto\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_DATABASE_TOKEN}\b\s+were\s+to\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bconsider\s+(?:an?\s+|the\s+|this\s+)?"
        r"(?:hypothetical\s+|example\s+)?(?:specification|scenario|example)\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bconsider\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b)"
        rf"(?={_BOUNDED_CLAUSE}\bas\s+(?:an?\s+)?counterexample\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:for\s+example|as\s+an\s+example)\b\s*,?\s*"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b(?:might|could|would|were\s+to)\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:an?\s+)?(?:hypothetical|example)\s+(?:specification|scenario|example)\b"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b(?:might|could|would|were\s+to)\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_REPORT_QUALIFIER}\s+{_REPORT_NOUN}"
        r"\b\s*:?\s*(?:(?:was|is|said|says|read|reads|stated|states|proposed|proposes|"
        r"required|requires)\s+(?:that\s+)?)?[\"'`]"
        rf"(?=[^\"'`]{{0,240}}\b{_DATABASE_TOKEN}\b)"
        rf"(?=[^\"'`]{{0,240}}\b{_DANGEROUS_BEHAVIOR}\b)[^\"'`]{{0,240}}[\"'`]",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_REPORT_QUALIFIER}\s+{_REPORT_NOUN}"
        r"\b\s*:?\s*(?:(?:was|is|said|says|read|reads|stated|states|proposed|proposes|"
        r"required|requires)\s+(?:that\s+)?)?[\"'`]?"
        rf"(?={_BOUNDED_CLAUSE}\b{_DATABASE_TOKEN}\b)"
        rf"(?={_BOUNDED_CLAUSE}\b{_DANGEROUS_BEHAVIOR}\b){_BOUNDED_CLAUSE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:rejects?|forbids?|invalidates?)\s+(?:the\s+)?{_REPORT_NOUN}\b\s*:?\s*"
        r"[\"'`]"
        rf"(?=[^\"'`]{{0,240}}\b{_DATABASE_TOKEN}\b)"
        rf"(?=[^\"'`]{{0,240}}\b{_DANGEROUS_BEHAVIOR}\b)[^\"'`]{{0,240}}[\"'`]",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:\b(?:the\s+)?{_REPORT_NOUN}\s+)?[\"'`]"
        rf"(?=[^\"'`]{{0,240}}\b{_DATABASE_TOKEN}\b)"
        rf"(?=[^\"'`]{{0,240}}\b{_DANGEROUS_BEHAVIOR}\b)[^\"'`]{{0,240}}[\"'`]\s+"
        rf"(?:is|was)\s+(?:an?\s+)?{_REPORT_QUALIFIER}(?:\s+{_REPORT_NOUN})?\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ReferenceRule:
    category: str
    patterns: tuple[re.Pattern[str], ...]


NEGATIVE_REFERENCE_RULE = ReferenceRule(
    category="explicit prohibition",
    patterns=(
        re.compile(
            rf"\b(?:no|without)\b[^.;!?]{{0,120}}?\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:must|should|shall|do|does|did|can|may|will)\s+not\b"
            rf"[^.;!?]{{0,120}}?\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:must\s+|should\s+)?(?:avoid|exclude|ignore|forbid|prohibit|reject|remove)\w*\b"
            rf"[^.;!?]{{0,120}}?\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bnever\s+use\b[^.;!?]{{0,120}}?\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,120}}?"
            r"\b(?:must|should)\s+never\s+be\s+used\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            r"\b(?:must|should|shall|may|can|will)\s+not\s+(?:be\s+)?"
            rf"(?:used|{_PROVIDER_PREDICATE}|run(?:s|ning)?\s+on)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            rf"\bno\s+longer\s+{_PROVIDER_PREDICATE}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            r"\b(?:do|does|did)\s+not\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:cannot|can't|don't|doesn't)\s+use\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            rf"\b(?:cannot|can't)\s+(?:be\s+)?(?:used|{_PROVIDER_PREDICATE})\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,80}}?"
            rf"\b(?:cannot|can't)\s+(?:run\s+on|use|be\s+{_PASSIVE_PROVIDER_ACTION}\s+by)\s+"
            rf"{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            r"\b(?:is|are)\s+(?:forbidden|prevented)\s+from\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:forbidden|prevented)\s+from\s+(?:using|depending\s+(?:on|upon)|"
            rf"being\s+{_PASSIVE_PROVIDER_ACTION}\s+by)\b"
            rf"[^.;!?]{{0,120}}?\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,80}}?\bis\s+not\s+to\s+be\s+"
            rf"{_PASSIVE_PROVIDER_ACTION}\s+by\s+{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            r"\b(?:is|are)\s+not\s+to\s+be\s+used\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:disallow|omit)\w*\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bneither\b[^.;!?]{{0,120}}?\b{_DATABASE_TOKEN}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            r"\b(?:isn't|aren't|isnt|arent|is\s+not|are\s+not)\s+"
            r"(?:used|required|maintained|exercised|provisioned|connected|allowed|permitted)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            r"\b(?:is|are)\s+"
            r"(?:forbidden|prohibited|disallowed|barred|excluded|ignored|removed)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:never|rather\s+than|instead\s+of)\b[^.;!?]{{0,80}}?"
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:,|-)\s*not\s+\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\band\s+not\s+{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:not\s+permitted|barred|disallowed)\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bpostgres(?:ql)?-free\b", re.IGNORECASE),
    ),
)

HISTORICAL_REFERENCE_RULE = ReferenceRule(
    category="historical/adoption record",
    patterns=(
        re.compile(
            rf"\b(?:historical|legacy|adoption|former|previously|earlier|preserved|copied|"
            rf"adopted|source-project)\b[^.;!?]{{0,100}}?"
            rf"\b(?:records?|describes?|documents?|mentions?|retains?|contained?|installed?|used)\b"
            rf"[^.;!?]{{0,100}}?\b{_DATABASE_TOKEN}(?:-specific|-only)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,100}}?"
            rf"\b(?:was|were|is|are)\s+(?:historical|legacy|formerly used|preserved|copied)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:legacy|historical|already-applied)\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}\b[^.;!?]{{0,120}}?"
            rf"\b(?:objects?|inventory|catalog|cleanup|leave/remove|follow-up)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:inventory|catalog|cleanup|leave/remove|follow-up)\b[^.;!?]{{0,120}}?"
            rf"\b(?:legacy|historical|already-applied)\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:historical|legacy|former|previously)\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}\b[^.;!?]{{0,120}}?"
            r"\b(?:inventoried|documented|recorded|catalogued)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:historical|legacy|former|previously)\b[^.;!?]{{0,120}}?"
            rf"\b{_DATABASE_TOKEN}\b[^.;!?]{{0,120}}?\bprovenance\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,120}}?"
            r"\b(?:(?:was|were|had\s+been)\s+(?:historically|previously|formerly)\s+used|"
            r"(?:was|were|had\s+been)\s+used\b[^.;!?]{0,80}?"
            r"(?:\bhistorically\b|\bpreviously\b|\bformerly\b|\bin\s+the\s+past\b))",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,120}}?"
            r"\b(?:powered|provided|supplied|enforced|backed|implemented|maintained|exercised)\b"
            r"[^.;!?]{0,80}?(?:\bhistorically\b|\bpreviously\b|\bformerly\b|"
            r"\bin\s+the\s+past\b)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_HISTORY_MARKER}\b[^.;!?]{{0,100}}?"
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,120}}?"
            rf"\b{_HISTORICAL_ACTION}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,100}}?"
            rf"\b{_HISTORY_MARKER}\b[^.;!?]{{0,80}}?\b{_HISTORICAL_ACTION}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,100}}?"
            rf"\b{_HISTORICAL_ACTION}\b[^.;!?]{{0,100}}?\b{_HISTORY_MARKER}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            rf"\bused\s+to\b[^.;!?]{{0,80}}?\b{_DANGEROUS_BEHAVIOR}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}(?:-specific|-only)?\b[^.;!?]{{0,80}}?"
            rf"\bhad\s+(?:been\s+)?{_HISTORICAL_ACTION}\b[^.;!?]{{0,100}}?"
            rf"\b{_DANGEROUS_BEHAVIOR}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_HISTORY_MARKER}\b"
            rf"(?=[^.;!?]{{0,220}}\b{_DATABASE_TOKEN}\b)"
            rf"(?=[^.;!?]{{0,220}}\b{_DANGEROUS_BEHAVIOR}\b)"
            rf"(?=[^.;!?]{{0,220}}\b{_HISTORICAL_ACTION}\b)[^.;!?]{{0,220}}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bhad\s+(?:been\s+)?{_HISTORICAL_ACTION}\b"
            rf"(?=[^.;!?]{{0,180}}\b{_DATABASE_TOKEN}\b)"
            rf"(?=[^.;!?]{{0,180}}\b{_DANGEROUS_BEHAVIOR}\b)[^.;!?]{{0,180}}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,100}}?"
            rf"\bhad\s+(?:been\s+)?{_HISTORICAL_ACTION}\b[^.;!?]{{0,100}}?"
            rf"\b{_DATABASE_TOKEN}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?=[^.;!?]{{0,220}}\b{_DATABASE_TOKEN}\b)"
            rf"(?=[^.;!?]{{0,220}}\b{_DANGEROUS_BEHAVIOR}\b)"
            rf"(?=[^.;!?]{{0,220}}\b{_HISTORICAL_ACTION}\b)"
            rf"(?=[^.;!?]{{0,220}}\b{_HISTORY_MARKER}\b)[^.;!?]{{0,220}}",
            re.IGNORECASE,
        ),
    ),
)

DEPLOYED_REFERENCE_RULE = ReferenceRule(
    category="deployed database boundary",
    patterns=(
        re.compile(r"\brds\s+postgresql\b", re.IGNORECASE),
        re.compile(r"\bpostgresql\s+(?:on|in)\s+rds\b", re.IGNORECASE),
        re.compile(
            rf"\b(?:deployed|deployment|production)\b[^.;!?]{{0,100}}?\b{_DATABASE_TOKEN}\b"
            r"[^.;!?]{0,100}?\b(?:storage|database|configuration|connectivity|settings)\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b{_DATABASE_TOKEN}\b[^.;!?]{{0,100}}?"
            r"\b(?:deployed|deployment|production)\b[^.;!?]{0,100}?"
            r"\b(?:storage|database|configuration|connectivity|settings)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:private\s+)?database\s+subnets?\s+accept\s+postgresql\s+only\s+from\s+"
            r"application\s+tasks?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:deployed|development|production)\s+settings\b[^.;!?]{0,120}?"
            r"\bpostgres(?:ql)?\b[^.;!?]{0,80}?\bdatabase_url\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bproduction\s+database\b[^.;!?]{{0,80}}?\b(?:uses|selects)\b"
            rf"[^.;!?]{{0,80}}?\b{_DATABASE_TOKEN}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bproduction\s+database_url\b[^.;!?]{{0,80}}?\bselects\b"
            rf"[^.;!?]{{0,80}}?\b{_DATABASE_TOKEN}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\bdeployed\s+runtime\s+database\s+engine\b[^.;!?]{{0,80}}?"
            rf"\b(?:is|uses|selects)\b[^.;!?]{{0,80}}?\b{_DATABASE_TOKEN}\b",
            re.IGNORECASE,
        ),
    ),
)

RUNTIME_DRIVER_REFERENCE_RULE = ReferenceRule(
    category="runtime driver",
    patterns=(
        re.compile(
            r"\b(?:runtime\s+)?psycopg\b[^.;!?]{0,80}?"
            r"\b(?:runtime|driver|dependency|connectivity|connection)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:runtime|driver|dependency|connectivity|connection)\b[^.;!?]{0,80}?"
            r"\bpsycopg\b",
            re.IGNORECASE,
        ),
    ),
)

HYPOTHETICAL_REFERENCE_RULE = ReferenceRule(
    category="hypothetical policy violation",
    patterns=HYPOTHETICAL_DATABASE_MANDATES,
)

ALLOWED_REFERENCE_RULES = (
    DEPLOYED_REFERENCE_RULE,
    RUNTIME_DRIVER_REFERENCE_RULE,
    HYPOTHETICAL_REFERENCE_RULE,
)
POSITIVE_DATABASE_REQUIREMENT = (
    re.compile(
        rf"\b(?:requir(?:e|es|ed|ing)|mandatory|necessary|needed)\b"
        rf"[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,120}}?"
        r"\b(?:is|are|remains?|becomes?)\s+(?:required|mandatory|necessary|needed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:must|shall)\s+(?:be\s+)?"
        r"(?:use[ds]?|power(?:s|ed)?|back(?:s|ed)?|provide[ds]?|suppl(?:y|ies|ied)|"
        r"enforce[ds]?|drive[sn]?|support(?:s|ed)?|validate[ds]?|run[ns]?|implement(?:s|ed)?|"
        r"maintain(?:s|ed)?|exercise[ds]?|provision(?:s|ed)?|connect(?:s|ed)?)\b"
        rf"[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,100}}?"
        r"\b(?:must|shall)\s+(?:be\s+)?"
        r"(?:use[ds]?|power(?:s|ed)?|back(?:s|ed)?|provide[ds]?|suppl(?:y|ies|ied)|"
        r"enforce[ds]?|drive[sn]?|support(?:s|ed)?|validate[ds]?|run[ns]?|implement(?:s|ed)?|"
        r"maintain(?:s|ed)?|exercise[ds]?|provision(?:s|ed)?|connect(?:s|ed)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,100}}?"
        rf"\b{_DEPENDENCY_PREDICATE}\b"
        rf"[^.;!?]{{0,80}}?\b(?:{_DATABASE_TOKEN}|{_ANAPHORIC_DATABASE_TOKEN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:requires?|depend(?:s|ed)?\s+(?:on|upon)|"
        r"rel(?:y|ies|ied)\s+(?:on|upon))\b"
        rf"[^.;!?]{{0,100}}?"
        rf"\b{_DATABASE_TOKEN}\b[^.;!?]{{0,140}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_DATABASE_TOKEN}|{_ANAPHORIC_DATABASE_TOKEN})\b[^.;!?]{{0,100}}?"
        rf"\b{_PROVIDER_PREDICATE}\b[^.;!?]{{0,140}}?"
        rf"\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PROVIDER_PREDICATE}\b[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
)

DISCOURSE_BOUNDARY = re.compile(
    r"(?:[.;!?]+\s*|\b(?:even\s+though|though|but|yet|while|whereas|although|however|"
    r"as\s+well\s+as)\b|"
    r"\bat\s+present\b|"
    rf",\s*(?={_CURRENT_MARKER}\b)|"
    rf"(?:,\s*)?\band\s+(?=(?:continues?\b|{_CURRENT_MARKER}|"
    rf"(?:it|the\s+engine|this\s+database)\s+{_CURRENT_MARKER}|"
    rf"{_DANGEROUS_BEHAVIOR}\b[^,.;!?]{{0,40}}?"
    rf"{_CURRENT_MARKER})\b))",
    re.IGNORECASE,
)
NEGATED_MANDATE = re.compile(
    r"\b(?:not|never|cannot|can't|don't|doesn't|isn't|aren't|isnt|arent|avoid\w*|exclude\w*|"
    r"disallow\w*|barred|forbidden|prevented|omit\w*|no\s+longer)\b",
    re.IGNORECASE,
)
HISTORICAL_PAST_USE = re.compile(
    rf"(?:\b{_HISTORY_MARKER}\b[^.;!?]{{0,160}}?\b{_HISTORICAL_ACTION}\b|"
    rf"\b{_HISTORICAL_ACTION}\b[^.;!?]{{0,160}}?\b{_HISTORY_MARKER}\b|"
    rf"\bused\s+to\b[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b|"
    rf"\bhad\s+(?:been\s+)?{_HISTORICAL_ACTION}\b[^.;!?]{{0,100}}?"
    rf"\b{_DANGEROUS_BEHAVIOR}\b|"
    rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,100}}?"
    rf"\bhad\s+(?:been\s+)?{_HISTORICAL_ACTION}\b)",
    re.IGNORECASE,
)
ALTERNATIVE_MANDATE_SUBJECT = (
    re.compile(
        rf"\b{_SQLITE_SUBJECT}\b{_SQLITE_RESPONSIBILITY_LINK}"
        rf"(?:is|are|remains?)\s+"
        rf"(?:required|mandatory|necessary|needed)\b[^.;!?]{{0,100}}?"
        rf"\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:sqlite|backend-portable|portable)\s+{_DANGEROUS_BEHAVIOR}\b"
        r"[^.;!?]{0,100}?\b(?:is|are|remains?)\s+"
        r"(?:required|mandatory|necessary|needed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_DANGEROUS_BEHAVIOR}\b[^.;!?]{{0,80}}?"
        rf"\b(?:{_DEPENDENCY_PREDICATE}|must\s+(?:use|provide|support))\b"
        r"[^.;!?]{0,40}?\b(?:sqlite|backend-portable|portable)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_SQLITE_SUBJECT}|backend-portable)\b{_SQLITE_RESPONSIBILITY_LINK}"
        r"(?:must|shall)\s+(?:be\s+)?"
        r"(?:use[ds]?|power(?:s|ed)?|provide[ds]?|support(?:s|ed)?|maintain(?:s|ed)?|"
        r"exercise[ds]?)\b"
        rf"[^.;!?]{{0,80}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:sqlite|backend-portable|portable)\s+{_DANGEROUS_BEHAVIOR}\b"
        r"[^.;!?]{0,80}?\b(?:must|shall)\s+(?:be\s+)?"
        r"(?:use[ds]?|power(?:s|ed)?|provide[ds]?|support(?:s|ed)?|maintain(?:s|ed)?|"
        r"exercise[ds]?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:rather\s+than|instead\s+of)\s+{_DATABASE_TOKEN}\s*,?\s+"
        rf"\b{_SQLITE_SUBJECT}\b\s+"
        r"(?:(?:is|are|remains?)\s+(?:required|mandatory|necessary|needed)|"
        r"(?:must|shall)\s+(?:be\s+)?"
        r"(?:use[ds]?|power(?:s|ed)?|provide[ds]?|support(?:s|ed)?|maintain(?:s|ed)?|"
        r"exercise[ds]?))\b"
        rf"[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_SQLITE_SUBJECT}|backend-portable|portable\s+(?:backend|layer|engine))\b"
        rf"{_SQLITE_RESPONSIBILITY_LINK}"
        rf"(?:{_CURRENT_MARKER}\s+)?(?:continues?\s+(?:to\s+)?)?"
        rf"{_PROVIDER_PREDICATE}\b[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_SQLITE_SUBJECT}|backend-portable|portable\s+(?:backend|layer|engine))\b"
        rf"{_SQLITE_RESPONSIBILITY_LINK}(?:is|are)\s+(?:{_CURRENT_MARKER}\s+)?"
        rf"(?:continues?\s+(?:to\s+)?)?{_PROVIDER_PREDICATE}\b"
        rf"[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bit\s+is\s+{_SQLITE_SUBJECT}\b[^.;!?]{{0,80}}?"
        rf"\b{_PROVIDER_PREDICATE}\b[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b"
        rf"[^.;!?]{{0,80}}?(?:\b(?:rather\s+than|instead\s+of|and\s+not)\b|"
        rf"(?:,|-)\s*not\b)[^.;!?]{{0,40}}?"
        rf"\b{_DATABASE_TOKEN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bit\s+is\s+{_SQLITE_SUBJECT}\b[^.;!?]{{0,80}}?"
        rf"(?:\b(?:rather\s+than|instead\s+of|and\s+not)\b|(?:,|-)\s*not\b)"
        rf"[^.;!?]{{0,40}}?\b{_DATABASE_TOKEN}\b[^.;!?]{{0,80}}?"
        rf"\b{_PROVIDER_PREDICATE}\b[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bit\s+is\s+{_SQLITE_SUBJECT}\b[^.;!?]{{0,80}}?"
        rf"\b{_PROVIDER_PREDICATE}\b[^.;!?]{{0,100}}?\b{_DANGEROUS_BEHAVIOR}\b",
        re.IGNORECASE,
    ),
)
SQLITE_REFERENT_SWITCH = (
    re.compile(
        rf"\b{_SQLITE_SUBJECT}\b\s*,?\s*(?:rather\s+than|instead\s+of|and\s+not|not)\s+"
        rf"{_DATABASE_TOKEN}\b\s*,?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:we|the\s+(?:application|project|site)|local\s+development|ordinary\s+ci)\s+"
        rf"(?:(?:have|has|had)\s+chosen|select(?:s|ed)?|choose|chooses|chose|use(?:s|d)?)\s+"
        rf"(?:the\s+)?sqlite\b"
        rf"(?:\s*(?:\b(?:rather\s+than|instead\s+of|and\s+not)\b|(?:,|-)\s*not\b)"
        rf"\s*{_DATABASE_TOKEN}\b)?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bthe\s+(?:database|backend|engine)\s+(?:is|uses|selects)\s+(?:the\s+)?sqlite\b"
        rf"(?:\s*(?:\b(?:rather\s+than|instead\s+of|and\s+not)\b|(?:,|-)\s*not\b)"
        rf"\s*{_DATABASE_TOKEN}\b)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsqlite\s+is\s+the\s+(?:database|backend|engine)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bsqlite\s+(?:is|was)\s+(?:now\s+)?selected\b", re.IGNORECASE),
    re.compile(rf"\bit\s+is\s+{_SQLITE_SUBJECT}\b", re.IGNORECASE),
)
LINK_TARGET = re.compile(
    r"(?:"
    r"\]\s*\(\s*<?(?P<markdown>[^\s)>]+)"
    r"|\]\s*:\s*<?(?P<reference>\S+)"
    r"|\bhref\s*=\s*(?:['\"](?P<href_quoted>.*?)[\"']|(?P<href_bare>[^\s>]+))"
    r"|<(?P<autolink>(?:https?://|\.\.?[/\\])[^<>\s]+)>"
    r"|(?P<url>https?://[^\s<>()'\"`]+)"
    r"|(?<![\w`])(?P<relative>(?:\.\.?[/\\])+[^\s<>()'\"`]+)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


class SpecificationNormalizationError(ValueError):
    """Raised when bounded decoding cannot reach a stable representation."""


def nested_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from nested_strings(key)
            yield from nested_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_strings(item)


def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return unicodedata.category(character) == "Cf" or any(
        start <= codepoint <= end
        for start, end in (
            (0x034F, 0x034F),
            (0x115F, 0x1160),
            (0x17B4, 0x17B5),
            (0x180B, 0x180F),
            (0x3164, 0x3164),
            (0xFE00, 0xFE0F),
            (0xFFA0, 0xFFA0),
            (0x1BCA0, 0x1BCAF),
            (0x1D173, 0x1D17A),
            (0xE0000, 0xE0FFF),
        )
    )


def _canonicalize_characters(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(
        "-" if unicodedata.category(character) == "Pd" else character
        for character in normalized
        if not _is_default_ignorable(character)
    )
    normalized = normalized.translate(
        {
            ord("’"): "'",
            ord("‘"): "'",
            ord("‛"): "'",
            ord("ʼ"): "'",
            ord("＇"): "'",
        }
    ).casefold()
    return "\n".join(re.sub(r"[^\S\n]+", " ", line) for line in normalized.split("\n"))


def normalize_specification_text(text: str) -> str:
    """Canonicalize visually equivalent prose while retaining line-oriented diagnostics."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for _ in range(MAX_DECODE_PASSES):
        candidate = _canonicalize_characters(html.unescape(normalized))
        if candidate == normalized:
            return re.sub(r"\bpostgre[ _-]+sql\b", "postgresql", normalized)
        normalized = candidate
    if _canonicalize_characters(html.unescape(normalized)) != normalized:
        raise SpecificationNormalizationError("HTML entity decoding did not stabilize")
    return re.sub(r"\bpostgre[ _-]+sql\b", "postgresql", normalized)


def markdown_statements(text: str) -> Iterator[tuple[int, str]]:
    """Yield prose blocks and each fenced/config line with its first normalized line."""

    start_line = 0
    parts: list[str] = []
    fence_marker: str | None = None

    def flush() -> tuple[int, str] | None:
        if not parts:
            return None
        statement = " ".join(parts)
        parts.clear()
        return start_line, statement

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if marker is not None:
            if statement := flush():
                yield statement
            fence_marker = None if fence_marker == marker else marker
            continue
        if fence_marker is not None:
            if stripped:
                yield line_number, stripped
            continue
        if not stripped:
            if statement := flush():
                yield statement
            continue
        if CONFIG_ASSIGNMENT.match(stripped):
            if statement := flush():
                yield statement
            yield line_number, stripped
            continue
        if parts and MARKDOWN_BLOCK_START.match(stripped):
            if statement := flush():
                yield statement
        if not parts:
            start_line = line_number
        parts.append(stripped)
    if statement := flush():
        yield statement


def _covered_references(statement: str, rule: ReferenceRule) -> set[tuple[int, int]]:
    references = tuple(DATABASE_REFERENCE.finditer(statement))
    covered: set[tuple[int, int]] = set()
    for pattern in rule.patterns:
        for rule_match in pattern.finditer(statement):
            covered.update(
                (reference.start(), reference.end())
                for reference in references
                if rule_match.start() <= reference.start() and reference.end() <= rule_match.end()
            )
    return covered


def _discourse_segments(statement: str) -> Iterator[str]:
    for segment in DISCOURSE_BOUNDARY.split(statement):
        if stripped := segment.strip(" ,:\n\t"):
            yield stripped


def _positive_mandates(segment: str) -> Iterator[re.Match[str]]:
    hypothetical_spans = tuple(
        match.span()
        for pattern in HYPOTHETICAL_DATABASE_MANDATES
        for match in pattern.finditer(segment)
    )
    for pattern in POSITIVE_DATABASE_REQUIREMENT:
        for match in pattern.finditer(segment):
            if any(
                start <= match.start() and match.end() <= end for start, end in hypothetical_spans
            ):
                continue
            prefix = segment[: match.start()]
            local_prefix = re.split(r"(?:[,;.!?]|\b(?:and|but|yet)\b)", prefix)[-1]
            if NEGATED_MANDATE.search(f"{local_prefix} {match.group()}"):
                continue
            historical_context = segment[max(0, match.start() - 100) : match.end() + 100]
            historical_action = re.search(
                rf"\b{_HISTORICAL_ACTION}\b",
                match.group(),
                re.IGNORECASE,
            )
            if historical_action and HISTORICAL_PAST_USE.search(historical_context):
                continue
            yield match


def _alternative_mandates(segment: str) -> tuple[re.Match[str], ...]:
    return tuple(
        match for pattern in ALTERNATIVE_MANDATE_SUBJECT for match in pattern.finditer(segment)
    )


def _sqlite_referent_switches(segment: str) -> tuple[re.Match[str], ...]:
    return tuple(match for pattern in SQLITE_REFERENT_SWITCH for match in pattern.finditer(segment))


def _mandate_category(mandate_text: str) -> str:
    if SEARCH_BEHAVIOR.search(mandate_text):
        return "requires PostgreSQL-specific search behavior"
    if CONSTRAINT_OR_TEST_BEHAVIOR.search(mandate_text):
        return "requires PostgreSQL-specific application constraints/concurrency/tests"
    if TRANSACTION_BEHAVIOR.search(mandate_text) or POSTGRESQL_FEATURE_BEHAVIOR.search(
        mandate_text
    ):
        return "requires PostgreSQL-specific application behavior"
    if FOUNDATION_BEHAVIOR.search(mandate_text):
        return "requires PostgreSQL in the application foundation"
    return "contains an unclassified PostgreSQL reference"


def _mandate_diagnostic(statement: str) -> str | None:
    mandate_matches: list[str] = []
    referent: str | None = None
    for segment in _discourse_segments(statement):
        postgres_references = tuple(POSTGRESQL_REFERENCE.finditer(segment))
        alternative_mandates = _alternative_mandates(segment)
        sqlite_switches = _sqlite_referent_switches(segment)
        portable_contexts = (*alternative_mandates, *sqlite_switches)
        for match in _positive_mandates(segment):
            matched_text = match.group()
            explicitly_portable = any(
                match.start() < context.end() and context.start() < match.end()
                for context in portable_contexts
            )
            explicit_postgresql = POSTGRESQL_REFERENCE.search(matched_text) is not None
            matched_references = tuple(DATABASE_REFERENCE.finditer(matched_text))
            contrasted_references = _covered_references(segment, NEGATIVE_REFERENCE_RULE)
            postgresql_is_only_contrast = bool(matched_references) and all(
                (
                    match.start() + reference.start(),
                    match.start() + reference.end(),
                )
                in contrasted_references
                for reference in matched_references
            )
            if explicitly_portable and (not explicit_postgresql or postgresql_is_only_contrast):
                continue
            if explicit_postgresql or postgres_references or referent == "postgresql":
                mandate_matches.append(matched_text)

        last_postgresql = postgres_references[-1].start() if postgres_references else -1
        last_alternative = max(
            (match.end() for match in portable_contexts),
            default=-1,
        )
        if last_postgresql >= 0 or last_alternative >= 0:
            referent = "portable" if last_alternative > last_postgresql else "postgresql"

    if not mandate_matches:
        return None
    return _mandate_category(" ".join(mandate_matches))


def _classify_specification_reference(statement: str) -> str | None:
    references = tuple(DATABASE_REFERENCE.finditer(statement))
    if not references:
        return None

    if diagnostic := _mandate_diagnostic(statement):
        return diagnostic

    covered = _covered_references(statement, NEGATIVE_REFERENCE_RULE)
    covered.update(_covered_references(statement, HISTORICAL_REFERENCE_RULE))
    unresolved = {(reference.start(), reference.end()) for reference in references} - covered
    if not unresolved:
        return None

    for rule in ALLOWED_REFERENCE_RULES:
        covered.update(_covered_references(statement, rule))
    unresolved = {(reference.start(), reference.end()) for reference in references} - covered
    if not unresolved:
        return None
    if SEARCH_BEHAVIOR.search(statement):
        return "requires PostgreSQL-specific search behavior"
    if CONSTRAINT_OR_TEST_BEHAVIOR.search(statement):
        return "requires PostgreSQL-specific application constraints/concurrency/tests"
    if TRANSACTION_BEHAVIOR.search(statement) or POSTGRESQL_FEATURE_BEHAVIOR.search(statement):
        return "requires PostgreSQL-specific application behavior"
    if FOUNDATION_BEHAVIOR.search(statement):
        return "requires PostgreSQL in the application foundation"
    if APPLICATION_BEHAVIOR.search(statement):
        return "requires PostgreSQL-specific application behavior"
    return "contains an unclassified PostgreSQL reference"


def _decode_link_text_fixed_point(text: str) -> str:
    decoded = text
    for _ in range(MAX_DECODE_PASSES):
        candidate = normalize_specification_text(unquote(decoded))
        if candidate == decoded:
            return decoded
        decoded = candidate
    if normalize_specification_text(unquote(decoded)) != decoded:
        raise SpecificationNormalizationError("link decoding did not stabilize")
    return decoded


def _is_stale_aws_target(target: str) -> bool:
    normalized = _decode_link_text_fixed_point(target).replace("\\", "/")
    return FORMER_AWS_SPEC_NAME.casefold() in normalized


def _stale_aws_link_lines(text: str) -> Iterator[int]:
    decoded = _decode_link_text_fixed_point(text)
    lines: set[int] = set()
    target_lines: set[int] = set()
    for match in LINK_TARGET.finditer(decoded):
        target_name, target = next(
            (name, value) for name, value in match.groupdict().items() if value is not None
        )
        if _is_stale_aws_target(target):
            lines.add(decoded.count("\n", 0, match.start()) + 1)
            target_lines.add(decoded.count("\n", 0, match.start(target_name)) + 1)

    for line_number, line in enumerate(decoded.splitlines(), start=1):
        if line_number in target_lines:
            continue
        plain = re.sub(r"^\s*(?:(?:[-*+]|>)\s+)+", "", line).strip().strip("<>")
        if not re.search(r"\s", plain) and _is_stale_aws_target(plain):
            lines.add(line_number)
    yield from sorted(lines)


def _display_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def check_specification_text(
    text: str,
    *,
    display_path: Path,
    classify_database: bool = True,
) -> list[str]:
    try:
        normalized = normalize_specification_text(text)
        stale_link_lines = tuple(_stale_aws_link_lines(normalized))
    except SpecificationNormalizationError:
        return [f"{display_path}:1: contains unresolved nested encoding"]
    errors = [
        f"{display_path}:{line_number}: links to the former AWS specification path"
        for line_number in stale_link_lines
    ]
    if not classify_database:
        return errors
    for line_number, statement in markdown_statements(normalized):
        if diagnostic := _classify_specification_reference(statement):
            errors.append(f"{display_path}:{line_number}: {diagnostic}")
    return errors


def check_specification(
    path: Path,
    *,
    display_root: Path = ROOT,
    classify_database: bool = True,
) -> list[str]:
    relative = _display_path(path, display_root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [f"{relative}: specification is not a readable UTF-8 regular file"]
    return check_specification_text(
        text,
        display_path=relative,
        classify_database=classify_database,
    )


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _symbolic_link_ancestor(path: Path) -> Path | None:
    absolute = _absolute_without_resolving(path)
    for candidate in (absolute, *absolute.parents):
        try:
            if stat.S_ISLNK(candidate.lstat().st_mode):
                return candidate
        except FileNotFoundError:
            continue
    return None


def _is_markdown_candidate(name: str) -> bool:
    normalized = normalize_specification_text(name)
    return Path(normalized).suffix in {".md", ".markdown"}


def _specification_inventory(
    specification_root: Path,
) -> tuple[list[str], dict[str, Path]]:
    try:
        root_mode = specification_root.lstat().st_mode
    except FileNotFoundError:
        return [f"normative specification directory is missing: {specification_root}"], {}
    except OSError:
        return [f"normative specification directory cannot be inspected: {specification_root}"], {}

    if stat.S_ISLNK(root_mode):
        return [f"normative specification root is a symbolic link: {specification_root}"], {}
    if not stat.S_ISDIR(root_mode):
        return [f"normative specification root is not a directory: {specification_root}"], {}
    if ancestor := _symbolic_link_ancestor(specification_root):
        return [f"normative specification root has a symbolic-link ancestor: {ancestor}"], {}

    try:
        resolved_root = specification_root.resolve(strict=True)
        entries = sorted(specification_root.iterdir(), key=lambda path: path.name)
    except OSError:
        return [f"normative specification directory cannot be inspected: {specification_root}"], {}

    errors: list[str] = []
    seen_names: set[str] = set()
    safe_paths: dict[str, Path] = {}
    expected_names = set(EXPECTED_SPECIFICATIONS)
    for entry in entries:
        seen_names.add(entry.name)
        try:
            mode = entry.lstat().st_mode
        except OSError:
            errors.append(f"specification entry cannot be inspected: {entry.name}")
            continue
        if stat.S_ISLNK(mode):
            errors.append(f"unexpected symbolic link in specification directory: {entry.name}")
            continue
        if stat.S_ISDIR(mode):
            errors.append(f"unexpected directory in specification directory: {entry.name}")
            continue
        if not stat.S_ISREG(mode):
            errors.append(
                f"unexpected special filesystem entry in specification directory: {entry.name}"
            )
            continue
        try:
            is_markdown_candidate = _is_markdown_candidate(entry.name)
        except SpecificationNormalizationError:
            errors.append(f"specification entry has unresolved nested encoding: {entry.name}")
            continue
        if not is_markdown_candidate:
            continue
        if entry.name not in expected_names:
            errors.append(f"unexpected specification candidate: {entry.name}")
            continue
        try:
            resolved_path = entry.resolve(strict=True)
            resolved_path.relative_to(resolved_root)
        except (OSError, ValueError):
            errors.append(f"authoritative specification resolves outside its root: {entry.name}")
            continue
        safe_paths[entry.name] = entry

    errors.extend(
        f"authoritative specification is missing: {name}"
        for name in EXPECTED_SPECIFICATIONS
        if name not in seen_names
    )
    return errors, safe_paths


def check_specifications(specification_root: Path = SPECIFICATION_ROOT) -> list[str]:
    errors, paths = _specification_inventory(specification_root)
    for name in AUTHORITATIVE_SPECIFICATIONS:
        if path := paths.get(name):
            errors.extend(
                check_specification(
                    path,
                    display_root=specification_root,
                    classify_database=True,
                )
            )
    for name in COMPATIBILITY_SPECIFICATIONS:
        if path := paths.get(name):
            errors.extend(
                check_specification(
                    path,
                    display_root=specification_root,
                    classify_database=False,
                )
            )
    return errors


def check_workflow() -> list[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs", {})
    errors: list[str] = []
    for name in ORDINARY_JOBS:
        job = jobs.get(name)
        if not isinstance(job, dict):
            errors.append(f"ordinary CI job is missing: {name}")
            continue
        if job.get("services"):
            errors.append(f"ordinary CI job declares a service container: {name}")
        text = "\n".join(nested_strings(job)).lower()
        for forbidden in ("postgres", "database_url", "dtc_use_sqlite"):
            if forbidden in text:
                errors.append(f"ordinary CI job {name} contains {forbidden}")

    django_job = jobs.get("django", {})
    django_environment = django_job.get("env", {}) if isinstance(django_job, dict) else {}
    if django_environment.get("DJANGO_SETTINGS_MODULE") != "website.settings.test":
        errors.append("ordinary Django CI does not select website.settings.test")
    if django_environment.get("DTC_SQLITE_PATH"):
        errors.append("ordinary Django CI bypasses the owned test-runtime SQLite path")
    django_steps = django_job.get("steps", []) if isinstance(django_job, dict) else []
    command_lines = [
        line.strip()
        for step in django_steps
        if isinstance(step, dict)
        for line in str(step.get("run", "")).splitlines()
    ]
    required_full_commands = ("make test-factories", "make test-migrations")
    full_django_indices = tuple(
        index for index, command in enumerate(command_lines) if command in FULL_DJANGO_COMMANDS
    )
    try:
        if len(full_django_indices) != 1:
            raise ValueError
        full_indices = tuple(command_lines.index(command) for command in required_full_commands) + (
            full_django_indices[0],
        )
    except (IndexError, ValueError):
        errors.append("ordinary Django CI does not run the owned full SQLite harness")
    else:
        if tuple(sorted(full_indices)) != full_indices:
            errors.append("ordinary Django CI runs the full SQLite harness out of order")
    return errors


def check_application() -> list[str]:
    errors: list[str] = []
    for root_name in APPLICATION_ROOTS:
        for path in (ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(ROOT)
            text = path.read_text(encoding="utf-8").lower()
            is_test = "tests" in relative.parts
            patterns = BACKEND_TEST_PATTERNS if is_test else BACKEND_PATTERNS
            exceptions = BACKEND_PATTERN_EXCEPTIONS.get(relative, frozenset())
            for pattern in patterns:
                if pattern in text and pattern not in exceptions:
                    errors.append(f"{relative}: backend-specific token {pattern!r}")
            if is_test and "postgresql" in path.name.lower():
                errors.append(f"{relative}: backend-specific test module name")
    return errors


def main() -> int:
    errors = [*check_workflow(), *check_application(), *check_specifications()]
    if errors:
        raise SystemExit("database portability check failed:\n- " + "\n- ".join(errors))
    print(
        "database portability check passed for ordinary CI, maintained Django code, "
        "and normative specifications"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
