"""Database-owned primary navigation shared by Studio, the admin API, and the public shell."""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.db import DatabaseError, IntegrityError
from django.db.models import Prefetch
from django.urls import NoReverseMatch, reverse

from core.audit import AuditWriteContext, record_audit_event
from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from core.idempotency import (
    IdempotencyResult,
    JsonObject,
    JsonValue,
    execute_idempotent,
    hash_idempotency_key,
)
from core.models import (
    AuditEvent,
    RevisionConflict,
    SiteNavigationEntry,
    SiteNavigationMenu,
    SiteNavigationRevision,
)
from core.services import ServiceContext, validate_actor_ref

logger = logging.getLogger(__name__)

PRIMARY_MENU_KEY = "primary"
NAVIGATION_SOURCES = frozenset({"studio", "admin_api"})
NAVIGATION_READ_PERMISSION = "core.read_site_navigation"
NAVIGATION_WRITE_PERMISSION = "core.change_site_navigation"
NAVIGATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
NAVIGATION_LABEL_MAX = 80
NAVIGATION_MIN_ENTRIES = 1
NAVIGATION_MAX_ENTRIES = 12
NAVIGATION_FORM_SLOTS = 12

NAVIGATION_TARGETS: tuple[tuple[str, str], ...] = (
    ("home", "Home"),
    ("events", "Events"),
    ("course_list", "Courses"),
    ("articles", "Blog"),
    ("podcast", "Podcast"),
    ("wiki-home", "Wiki"),
    ("books", "Books"),
    ("docs-home", "Docs"),
    ("faq-home", "FAQ"),
    ("sponsors", "Sponsors"),
    ("slack", "Slack"),
)
NAVIGATION_TARGET_ALLOWLIST = frozenset(target for target, _label in NAVIGATION_TARGETS)
NAVIGATION_TARGET_CURRENT_KEYS = {
    "events": "events",
    "course_list": "courses",
    "articles": "blog",
    "podcast": "podcast",
    "wiki-home": "wiki",
    "books": "books",
    "docs-home": "docs",
    "faq-home": "faq",
    "sponsors": "sponsors",
    "slack": "slack",
}
DEFAULT_PRIMARY_NAVIGATION: tuple[tuple[str, str, str, int, bool], ...] = (
    ("events", "Events", "events", 1, True),
    ("courses", "Courses", "course_list", 2, True),
    ("blog", "Blog", "articles", 3, True),
    ("podcast", "Podcast", "podcast", 4, True),
    ("wiki", "Wiki", "wiki-home", 5, True),
    ("books", "Books", "books", 6, True),
    ("docs", "Docs", "docs-home", 7, True),
    ("faq", "FAQ", "faq-home", 8, True),
    ("sponsors", "Sponsors", "sponsors", 9, True),
    ("slack", "Slack", "slack", 10, True),
)
_WRITE_REDACTED = (
    "authorization",
    "body",
    "cookie",
    "csrfmiddlewaretoken",
    "entries",
    "label",
    "token",
)
_UNSAFE_TARGET_MARKERS = ("://", "//", "\\", "..", "?", "#", "@")


class InvalidSiteNavigation(ValueError):
    """A complete navigation command or stored row failed validation."""

    def __init__(self, message: str, *, fields: dict[str, str] | None = None) -> None:
        self.fields = fields or {}
        super().__init__(message)


class SiteNavigationRevisionConflict(RuntimeError):
    """The submitted whole-menu revision no longer matches the stored menu."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"navigation expected revision {expected}, found {actual}")


@dataclass(frozen=True, slots=True)
class NormalizedNavigationEntry:
    key: str
    label: str
    target: str
    position: int
    visible: bool

    def as_dict(self) -> JsonObject:
        return {
            "key": self.key,
            "label": self.label,
            "target": self.target,
            "position": self.position,
            "visible": self.visible,
        }

    def identity_dict(self) -> JsonObject:
        return {
            "key": self.key,
            "target": self.target,
            "position": self.position,
            "visible": self.visible,
        }


@dataclass(frozen=True, slots=True)
class SiteNavigationCommandResult:
    menu: JsonObject
    replayed: bool

    def as_dict(self) -> JsonObject:
        return {**self.menu, "replayed": self.replayed}


def default_navigation_entries() -> tuple[NormalizedNavigationEntry, ...]:
    return tuple(
        NormalizedNavigationEntry(
            key=key,
            label=label,
            target=target,
            position=position,
            visible=visible,
        )
        for key, label, target, position, visible in DEFAULT_PRIMARY_NAVIGATION
    )


def default_navigation_menu() -> JsonObject:
    return {
        "menu": PRIMARY_MENU_KEY,
        "source": "code_default",
        "revision": 0,
        "entries": [entry.as_dict() for entry in default_navigation_entries()],
    }


def resolve_navigation_target(target: str) -> str:
    try:
        return reverse(target)
    except NoReverseMatch:
        return ""


def _reject_control_and_markup(value: str, *, field: str) -> str:
    if any(
        character in "\r\n\t"
        or unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise InvalidSiteNavigation(
            f"{field} must be one safe line",
            fields={field: f"Enter one plain-text {field}."},
        )
    if "<" in value or ">" in value:
        raise InvalidSiteNavigation(
            f"{field} cannot contain markup",
            fields={field: f"Enter plain text without markup in {field}."},
        )
    return value


def _normalize_key(value: object, *, field: str) -> str:
    if not isinstance(value, str) or NAVIGATION_KEY_PATTERN.fullmatch(value) is None:
        raise InvalidSiteNavigation(
            "navigation key is invalid",
            fields={field: "Enter a lowercase key of letters, numbers, and underscores."},
        )
    return value


def _normalize_label(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidSiteNavigation(
            "navigation label is invalid",
            fields={field: "Enter a plain-text label."},
        )
    normalized = _reject_control_and_markup(value, field=field).strip()
    if not 1 <= len(normalized) <= NAVIGATION_LABEL_MAX:
        raise InvalidSiteNavigation(
            "navigation label length is invalid",
            fields={field: "Enter a label of 1 to 80 characters."},
        )
    return normalized


def _looks_like_url(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith(("/", "\\")) or any(
        marker in stripped for marker in _UNSAFE_TARGET_MARKERS
    ):
        return True
    parsed = urlsplit(stripped)
    return bool(parsed.scheme or parsed.netloc or parsed.query or parsed.fragment)


def _normalize_target(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidSiteNavigation(
            "navigation target is invalid",
            fields={field: "Choose a registered public route."},
        )
    if _looks_like_url(value) or value not in NAVIGATION_TARGET_ALLOWLIST:
        raise InvalidSiteNavigation(
            "navigation target is invalid",
            fields={field: "Choose a registered public route."},
        )
    return value


def _normalize_position(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSiteNavigation(
            "navigation position is invalid",
            fields={field: "Enter a unique position from 1 to 12."},
        )
    if not 1 <= value <= NAVIGATION_MAX_ENTRIES:
        raise InvalidSiteNavigation(
            "navigation position is invalid",
            fields={field: "Enter a unique position from 1 to 12."},
        )
    return value


def _normalize_visible(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidSiteNavigation(
            "navigation visibility is invalid",
            fields={field: "Visible must be true or false."},
        )
    return value


def _normalize_entries(value: object) -> tuple[NormalizedNavigationEntry, ...]:
    if not isinstance(value, list):
        raise InvalidSiteNavigation(
            "navigation entries are invalid",
            fields={"entries": "Submit between 1 and 12 navigation entries."},
        )
    if not NAVIGATION_MIN_ENTRIES <= len(value) <= NAVIGATION_MAX_ENTRIES:
        raise InvalidSiteNavigation(
            "navigation entries are invalid",
            fields={"entries": "Submit between 1 and 12 navigation entries."},
        )
    normalized: list[NormalizedNavigationEntry] = []
    seen_keys: set[str] = set()
    seen_positions: set[int] = set()
    allowed_fields = {"key", "label", "target", "position", "visible"}
    for index, item in enumerate(value):
        prefix = f"entries.{index}"
        if not isinstance(item, dict):
            raise InvalidSiteNavigation(
                "navigation entries are invalid",
                fields={prefix: "Each entry must be an object."},
            )
        extra = set(item) - allowed_fields
        missing = allowed_fields - set(item)
        if extra or missing:
            raise InvalidSiteNavigation(
                "navigation entries are invalid",
                fields={
                    prefix: (
                        "Each entry must contain only key, label, target, position, and visible."
                    )
                },
            )
        entry = NormalizedNavigationEntry(
            key=_normalize_key(item.get("key"), field=f"{prefix}.key"),
            label=_normalize_label(item.get("label"), field=f"{prefix}.label"),
            target=_normalize_target(item.get("target"), field=f"{prefix}.target"),
            position=_normalize_position(item.get("position"), field=f"{prefix}.position"),
            visible=_normalize_visible(item.get("visible"), field=f"{prefix}.visible"),
        )
        if entry.key in seen_keys:
            raise InvalidSiteNavigation(
                "navigation keys must be unique",
                fields={f"{prefix}.key": "Each entry key must be unique."},
            )
        if entry.position in seen_positions:
            raise InvalidSiteNavigation(
                "navigation positions must be unique",
                fields={f"{prefix}.position": "Each entry position must be unique."},
            )
        seen_keys.add(entry.key)
        seen_positions.add(entry.position)
        normalized.append(entry)
    return tuple(sorted(normalized, key=lambda item: (item.position, item.key)))


def _normalize_expected_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidSiteNavigation(
            "expected revision is invalid",
            fields={"expected_revision": "Enter the current menu revision."},
        )
    return value


def _require_source(source: object) -> str:
    if source not in NAVIGATION_SOURCES:
        raise InvalidSiteNavigation("navigation source is invalid")
    return str(source)


def _require_actor_ref(actor_ref: object, context: ServiceContext | None) -> str:
    if not isinstance(actor_ref, str) or not actor_ref:
        raise InvalidSiteNavigation("navigation actor is invalid")
    try:
        validate_actor_ref(actor_ref)
    except ValueError as error:
        raise InvalidSiteNavigation("navigation actor is invalid") from error
    if context is not None and context.actor_ref != actor_ref:
        raise InvalidSiteNavigation("navigation actor context is invalid")
    return actor_ref


def _idempotency_scope(actor_ref: str) -> str:
    scope = f"site.navigation.write:{actor_ref}"
    if len(scope) > 128:
        raise InvalidSiteNavigation("navigation actor scope is invalid")
    return scope


def _entry_dicts(entries: object) -> list[JsonObject]:
    items: list[JsonObject] = []
    if not isinstance(entries, list):
        return items
    for item in entries:
        if isinstance(item, NormalizedNavigationEntry):
            items.append(item.as_dict())
        elif isinstance(item, dict):
            key = item.get("key")
            label = item.get("label")
            target = item.get("target")
            position = item.get("position")
            visible = item.get("visible")
            if (
                isinstance(key, str)
                and isinstance(label, str)
                and isinstance(target, str)
                and isinstance(position, int)
                and not isinstance(position, bool)
                and isinstance(visible, bool)
            ):
                items.append(
                    {
                        "key": key,
                        "label": label,
                        "target": target,
                        "position": position,
                        "visible": visible,
                    }
                )
    return items


def serialize_navigation_menu(
    menu: SiteNavigationMenu,
    *,
    entries: tuple[NormalizedNavigationEntry, ...] | None = None,
) -> JsonObject:
    resolved = entries
    if resolved is None:
        resolved = tuple(
            NormalizedNavigationEntry(
                key=item.key,
                label=item.label,
                target=item.target,
                position=item.position,
                visible=item.visible,
            )
            for item in sorted(menu.entries.all(), key=lambda item: (item.position, item.key))
        )
    return {
        "menu": menu.key,
        "source": menu.source,
        "revision": menu.revision,
        "entries": [item.as_dict() for item in resolved],
    }


def _validate_stored_menu(payload: JsonObject) -> JsonObject:
    entries = _normalize_entries(payload.get("entries"))
    source = payload.get("source")
    if source not in NAVIGATION_SOURCES:
        raise InvalidSiteNavigation("stored navigation source is invalid")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise InvalidSiteNavigation("stored navigation revision is invalid")
    menu_key = payload.get("menu")
    if menu_key != PRIMARY_MENU_KEY:
        raise InvalidSiteNavigation("stored navigation menu is invalid")
    return {
        "menu": PRIMARY_MENU_KEY,
        "source": source,
        "revision": revision,
        "entries": [item.as_dict() for item in entries],
    }


def _load_menu(*, using: str) -> SiteNavigationMenu | None:
    return (
        SiteNavigationMenu.objects.using(using)
        .filter(key=PRIMARY_MENU_KEY)
        .prefetch_related(
            Prefetch(
                "entries",
                queryset=SiteNavigationEntry.objects.using(using).order_by("position", "key"),
            )
        )
        .first()
    )


def query_site_navigation(
    _query: object = None,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """Resolve the primary menu with one bounded database lookup."""

    del context
    menu = _load_menu(using=using)
    if menu is None:
        return default_navigation_menu()
    return _validate_stored_menu(serialize_navigation_menu(menu))


def public_primary_navigation(*, using: str = "default") -> tuple[JsonObject, ...]:
    """Return visible public masthead entries, or fail closed to the code-owned default."""

    try:
        resolved = query_site_navigation(using=using)
        entries = resolved.get("entries")
        if not isinstance(entries, list):
            raise InvalidSiteNavigation("navigation query result is invalid")
        public_entries = _public_entries(entries)
    except (DatabaseError, InvalidSiteNavigation) as error:
        logger.warning(
            "Public site navigation is unavailable (%s).",
            type(error).__name__,
        )
        public_entries = _public_entries(default_navigation_menu()["entries"])
    return public_entries


def _public_entries(entries: object) -> tuple[JsonObject, ...]:
    resolved: list[JsonObject] = []
    if not isinstance(entries, list):
        return ()
    for item in entries:
        if not isinstance(item, dict) or item.get("visible") is not True:
            continue
        target = item.get("target")
        label = item.get("label")
        key = item.get("key")
        if not isinstance(target, str) or not isinstance(label, str) or not isinstance(key, str):
            continue
        href = resolve_navigation_target(target)
        if not href:
            continue
        resolved.append(
            {
                "key": key,
                "label": label,
                "href": href,
                "current_key": NAVIGATION_TARGET_CURRENT_KEYS.get(target, ""),
            }
        )
    return tuple(resolved)


def _same_entries(
    current: tuple[NormalizedNavigationEntry, ...],
    proposed: tuple[NormalizedNavigationEntry, ...],
) -> bool:
    return tuple(item.as_dict() for item in current) == tuple(item.as_dict() for item in proposed)


def _assert_surviving_targets(
    current: tuple[NormalizedNavigationEntry, ...],
    proposed: tuple[NormalizedNavigationEntry, ...],
) -> None:
    current_by_key = {item.key: item for item in current}
    for index, item in enumerate(proposed):
        existing = current_by_key.get(item.key)
        if existing is None:
            continue
        if resolve_navigation_target(existing.target) != resolve_navigation_target(item.target):
            raise InvalidSiteNavigation(
                "navigation target cannot change for a surviving entry",
                fields={
                    f"entries.{index}.target": (
                        "Replace this entry instead of changing where its key points."
                    )
                },
            )


def _stored_entries(menu: SiteNavigationMenu) -> tuple[NormalizedNavigationEntry, ...]:
    return tuple(
        NormalizedNavigationEntry(
            key=item.key,
            label=item.label,
            target=item.target,
            position=item.position,
            visible=item.visible,
        )
        for item in sorted(menu.entries.all(), key=lambda item: (item.position, item.key))
    )


def _replace_entries(
    menu: SiteNavigationMenu,
    entries: tuple[NormalizedNavigationEntry, ...],
    *,
    using: str,
) -> None:
    SiteNavigationEntry.objects.using(using).filter(menu=menu).delete()
    for entry in entries:
        SiteNavigationEntry.objects.using(using).create(
            menu=menu,
            key=entry.key,
            label=entry.label,
            target=entry.target,
            position=entry.position,
            visible=entry.visible,
        )


def _apply_navigation(
    entries: tuple[NormalizedNavigationEntry, ...],
    *,
    expected_revision: int,
    source: str,
    context: AuditWriteContext,
    using: str,
) -> JsonObject:
    menu = _load_menu(using=using)
    actual_revision = menu.revision if menu is not None else 0
    if expected_revision != actual_revision:
        raise SiteNavigationRevisionConflict(
            expected=expected_revision,
            actual=actual_revision,
        )
    current = () if menu is None else _stored_entries(menu)
    comparable = default_navigation_entries() if menu is None else current
    _assert_surviving_targets(current, entries)
    if _same_entries(comparable, entries):
        payload = default_navigation_menu() if menu is None else serialize_navigation_menu(menu)
        return {
            "source": payload["source"],
            "revision": payload["revision"],
            "changed": False,
            "entries": [item.identity_dict() for item in entries],
        }

    before_revision = actual_revision
    if menu is None:
        try:
            menu = SiteNavigationMenu.objects.using(using).create(
                key=PRIMARY_MENU_KEY,
                source=source,
                revision=1,
            )
        except IntegrityError as error:
            current_menu = SiteNavigationMenu.objects.using(using).get(key=PRIMARY_MENU_KEY)
            raise SiteNavigationRevisionConflict(
                expected=0,
                actual=current_menu.revision,
            ) from error
    else:
        menu.source = source
        menu.revision += 1
        try:
            menu.save(using=using, update_fields=("source", "revision", "updated_at"))
        except RevisionConflict as error:
            raise SiteNavigationRevisionConflict(
                expected=error.expected,
                actual=error.actual,
            ) from error
    try:
        _replace_entries(menu, entries, using=using)
    except IntegrityError as error:
        raise InvalidSiteNavigation(
            "navigation entries conflict",
            fields={"entries": "Each entry key and position must be unique."},
        ) from error
    menu = _load_menu(using=using)
    if menu is None:
        raise InvalidSiteNavigation("navigation menu is unavailable")
    current_by_key = {item.key: item for item in current}
    changed_keys = sorted(set(current_by_key) ^ {item.key for item in entries})
    updated_keys = sorted(
        item.key
        for item in entries
        if item.key in current_by_key and item.as_dict() != current_by_key[item.key].as_dict()
    )
    audit_event = record_audit_event(
        action="core.site_navigation.updated",
        target_type="core.site_navigation",
        target_id=menu.id,
        target_label=PRIMARY_MENU_KEY,
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=context,
        changes={
            "revision": {"before": before_revision, "after": menu.revision},
        },
        metadata={
            "source": source,
            "changed_keys": changed_keys + updated_keys,
        },
        using=using,
    )
    SiteNavigationRevision.objects.using(using).create(
        menu=menu,
        menu_key=PRIMARY_MENU_KEY,
        source=menu.source,
        revision=menu.revision,
        entries=[item.identity_dict() for item in entries],
        changed_by_id=context.actor_id,
        changed_by_ref=context.actor_ref,
        audit_event=audit_event,
    )
    return {
        "source": menu.source,
        "revision": menu.revision,
        "changed": True,
        "entries": [item.identity_dict() for item in entries],
    }


def replace_site_navigation(
    *,
    entries: object,
    expected_revision: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SiteNavigationCommandResult:
    """Normalize and atomically replace the primary menu."""

    source_value = _require_source(source)
    actor = _require_actor_ref(actor_ref, context)
    normalized = _normalize_entries(entries)
    revision = _normalize_expected_revision(expected_revision)
    scope = _idempotency_scope(actor)
    service_context = context or ServiceContext.from_current(actor_ref=actor)
    audit_context = AuditWriteContext.from_service_context(
        service_context,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        idempotency_key_hash=hash_idempotency_key(scope, idempotency_key),
    )
    request: JsonObject = {
        "entries": [item.as_dict() for item in normalized],
        "expected_revision": revision,
    }
    result: IdempotencyResult = execute_idempotent(
        scope=scope,
        key=idempotency_key,
        request=request,
        command=lambda: _apply_navigation(
            normalized,
            expected_revision=revision,
            source=source_value,
            context=audit_context,
            using=using,
        ),
        using=using,
    )
    payload = result.value
    source_result = payload.get("source")
    revision_result = payload.get("revision")
    changed_result = payload.get("changed")
    stored_entries = payload.get("entries")
    if (
        source_result not in NAVIGATION_SOURCES | {"code_default"}
        or not isinstance(source_result, str)
        or isinstance(revision_result, bool)
        or not isinstance(revision_result, int)
        or revision_result < 0
        or not isinstance(changed_result, bool)
        or not isinstance(stored_entries, list)
        or len(stored_entries) != len(normalized)
    ):
        raise InvalidSiteNavigation("navigation replay result is invalid")
    reconstructed: list[JsonValue] = [item.as_dict() for item in normalized]
    return SiteNavigationCommandResult(
        menu={
            "menu": PRIMARY_MENU_KEY,
            "source": source_result,
            "revision": revision_result,
            "entries": reconstructed,
            "changed": changed_result,
        },
        replayed=result.replayed,
    )


def _navigation_factory() -> JsonObject:
    return default_navigation_menu()


def _navigation_field_policy(_actor: object, field: str) -> bool:
    return field == "entries"


SITE_NAVIGATION_READ = Capability(
    key="site.navigation.read",
    description="Read the public site primary navigation",
    service_kind=ServiceKind.QUERY,
    service=query_site_navigation,
    django_permission=NAVIGATION_READ_PERMISSION,
    studio=AdapterMetadata(
        route="studio:navigation",
        method="GET",
        operation_id="site.navigation.read.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/navigation",
        method="GET",
        operation_id="site.navigation.read",
        scopes=("site.navigation.read",),
        result_schema="SiteNavigation",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="core.site_navigation.read",
    redacted_fields=("authorization", "cookie", "label", "token"),
    test_factory=_navigation_factory,
)

SITE_NAVIGATION_WRITE = Capability(
    key="site.navigation.write",
    description="Replace the public site primary navigation",
    service_kind=ServiceKind.COMMAND,
    service=replace_site_navigation,
    django_permission=NAVIGATION_WRITE_PERMISSION,
    studio=AdapterMetadata(
        route="studio:navigation",
        method="POST",
        operation_id="site.navigation.write.html",
        writable_fields=("entries",),
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/navigation",
        method="PUT",
        operation_id="site.navigation.write",
        scopes=("site.navigation.write",),
        request_schema="SiteNavigationReplaceRequest",
        result_schema="SiteNavigationCommandResult",
        writable_fields=("entries",),
        rate_class="write",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.IF_MATCH,
    audit_action="core.site_navigation.updated",
    redacted_fields=_WRITE_REDACTED,
    test_factory=_navigation_factory,
    field_policy=_navigation_field_policy,
)

SITE_NAVIGATION_CAPABILITIES = (SITE_NAVIGATION_READ, SITE_NAVIGATION_WRITE)
