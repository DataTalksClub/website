from __future__ import annotations

from django.apps import apps

from accounts.identity_relations import (
    ACCOUNT_EXTENSION_MODELS,
    ACCOUNT_RELATIONS,
    _extension_join_field,
)
from accounts.identity_values import canonical_json, sha256_text


def _relation_logical_rows(
    *,
    model_label: str,
    field_name: str,
    aliases: dict[int, int],
) -> list[list[str | int]]:
    model = apps.get_model(model_label)
    rows = list(
        model._base_manager.exclude(**{f"{field_name}__isnull": True})
        .order_by("pk")
        .values_list("pk", f"{field_name}_id")
    )
    logical: list[list[str | int]] = []
    for row_id, user_id in rows:
        logical.append([str(row_id), aliases.get(int(user_id), int(user_id))])
    return logical


def _relations_and_keys() -> list[tuple[str, str, str]]:
    # (key, model_label, field_name) for every relation this module tracks:
    # the named ACCOUNT_RELATIONS plus, D3.3, the account's own extension
    # rows -- the two tables the reviewed merge writes that ACCOUNT_RELATIONS
    # itself does not name (see ACCOUNT_EXTENSION_MODELS above).
    entries = [(spec.key, spec.model_label, spec.field_name) for spec in ACCOUNT_RELATIONS]
    for model_label, skip in ACCOUNT_EXTENSION_MODELS:
        field_name = _extension_join_field(model_label, skip)
        entries.append((f"{model_label}.{field_name}", model_label, field_name))
    return entries


def extension_relation_keys() -> frozenset[str]:
    """Evidence keys that belong to an ``ACCOUNT_EXTENSION_MODELS`` row rather
    than to a named ``ACCOUNT_RELATIONS`` spec.

    These rows are the account's own storage, not a dependent relation: a
    merge is allowed to *create* one where a user had none yet (the same
    backfill ``courses.LearnerProfile``/``accounts_ext.IdentityState`` do
    outside a merge), so a caller comparing before/after evidence for these
    keys should check that nothing already present was lost (a subset
    check), not that the row set is byte-identical the way it is for every
    other, purely-reparented relation.
    """
    keys = set()
    for model_label, skip in ACCOUNT_EXTENSION_MODELS:
        keys.add(f"{model_label}.{_extension_join_field(model_label, skip)}")
    return frozenset(keys)


def append_only_relation_keys() -> frozenset[str]:
    """Every evidence key where a merge is allowed to *add* a row.

    The extension tables (``extension_relation_keys``) and
    ``accounts_ext.AccountIdentityAlias.survivor`` (``"merge_ledger_append_only"``
    handling) both grow as a normal, expected side effect of the very apply
    whose evidence is being checked -- a backfilled extension row, or the one
    new alias row every successful merge creates. A caller checking before/
    after evidence for one of these keys should require a subset (nothing
    already there was lost), not byte-identical equality.
    """
    keys = set(extension_relation_keys())
    for spec in ACCOUNT_RELATIONS:
        if spec.handling == "merge_ledger_append_only":
            keys.add(spec.key)
    return frozenset(keys)


def _current_aliases(alias_overrides: dict[int, int] | None) -> dict[int, int]:
    from accounts_ext.models import AccountIdentityAlias

    aliases = dict(
        AccountIdentityAlias.objects.values_list(
            "source_user_id",
            "survivor_id",
        )
    )
    aliases.update(alias_overrides or {})
    return aliases


def relationship_evidence(
    *,
    alias_overrides: dict[int, int] | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    aliases = _current_aliases(alias_overrides)
    counts: dict[str, int] = {}
    checksums: dict[str, str] = {}
    for key, model_label, field_name in _relations_and_keys():
        logical_rows = _relation_logical_rows(
            model_label=model_label,
            field_name=field_name,
            aliases=aliases,
        )
        counts[key] = len(logical_rows)
        checksums[key] = sha256_text(canonical_json({"rows": logical_rows}))
    return counts, checksums


def relationship_row_identities(
    *,
    alias_overrides: dict[int, int] | None = None,
) -> dict[str, frozenset[tuple[str, int]]]:
    """The same rows ``relationship_evidence`` counts and checksums, as sets.

    A checksum proves two runs saw the identical row set, but cannot answer
    whether one is a *subset* of the other -- the question a caller has to
    ask for an ``extension_relation_keys()`` key, where a merge is allowed to
    add a row (the account gained a previously-missing extension row) but
    never allowed to lose one.
    """
    aliases = _current_aliases(alias_overrides)
    identities = {}
    for key, model_label, field_name in _relations_and_keys():
        rows = _relation_logical_rows(
            model_label=model_label,
            field_name=field_name,
            aliases=aliases,
        )
        identities[key] = frozenset((str(row_id), int(owner_id)) for row_id, owner_id in rows)
    return identities
