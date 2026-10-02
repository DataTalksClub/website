from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps
from django.contrib.auth import get_user_model


@dataclass(frozen=True, slots=True)
class AccountRelationSpec:
    model_label: str
    field_name: str
    handling: str

    @property
    def key(self) -> str:
        return f"{self.model_label}.{self.field_name}"


_MANDATORY_RELATIONS = (
    AccountRelationSpec(
        "accounts.User_groups",
        "user",
        "source_authority_only",
    ),
    AccountRelationSpec(
        "accounts.User_user_permissions",
        "user",
        "source_authority_only",
    ),
    AccountRelationSpec("admin.LogEntry", "user", "provenance_alias"),
    AccountRelationSpec(
        "management_auth.APIPrincipal",
        "user",
        "disable_source_principal",
    ),
    AccountRelationSpec(
        "management_auth.APIPrincipal",
        "created_by",
        "provenance_alias",
    ),
    AccountRelationSpec(
        "management_auth.APICredential",
        "created_by",
        "provenance_alias",
    ),
    AccountRelationSpec("core.AuditEvent", "actor", "provenance_alias"),
    AccountRelationSpec("core.StaffSession", "user", "reparent"),
    AccountRelationSpec("core.Operation", "actor", "provenance_alias"),
    AccountRelationSpec("accounts.Token", "user", "compatibility_alias"),
    AccountRelationSpec("courses.CourseRegistration", "user", "reparent"),
    AccountRelationSpec("courses.Enrollment", "student", "reparent"),
    AccountRelationSpec("courses.Submission", "student", "reparent"),
    AccountRelationSpec("courses.ProjectSubmission", "student", "reparent"),
    AccountRelationSpec("courses.LeaderboardComplaint", "reporter", "reparent"),
    AccountRelationSpec("courses.LeaderboardComplaint", "resolved_by", "reparent"),
    AccountRelationSpec("courses.ProjectVote", "voter", "reparent"),
    AccountRelationSpec("courses.UserWrappedStatistics", "user", "reparent"),
    AccountRelationSpec("account.EmailAddress", "user", "verified_only"),
    AccountRelationSpec("socialaccount.SocialAccount", "user", "verified_only"),
    # D3.3: the rest of this tuple was found by deriving the guard from
    # ``User._meta.related_objects`` instead of trusting this list to be
    # exhaustive (see ``unclassified_account_relations`` below). Every one of
    # these predates D3.3; none of them is the two relations that issue was
    # filed about (those live in ACCOUNT_EXTENSION_MODELS, not here). Each is
    # classified on its own rather than exempted as a group.
    #
    # The shared ``community_base.events`` app has owned the "events" label
    # and taken live writes since D4.1 (see website/settings/base.py), so a
    # merge must move a person's registrations the same way it already moves
    # ``courses.Enrollment``/``courses.CourseRegistration`` rows.
    AccountRelationSpec("events.EventRegistration", "user", "reparent"),
    AccountRelationSpec("events.SeriesRegistration", "user", "reparent"),
    AccountRelationSpec("events.SeriesOccurrenceOptOut", "user", "reparent"),
    # community_base.mail's EmailDelivery has taken real writes since D1.2b
    # (course_management/package_mail.py sends five production purposes
    # through it). idempotency_key is globally unique, not scoped to the
    # recipient, so reparenting never collides with it.
    AccountRelationSpec("cb_mail.EmailDelivery", "recipient_user", "reparent"),
    # Package Studio notes are durable member-owned account data. The note
    # follows the surviving account; its author is audit provenance, matching
    # the site's other nullable actor relations.
    AccountRelationSpec("cb_studio.MemberNote", "member", "reparent"),
    AccountRelationSpec("cb_studio.MemberNote", "created_by", "provenance_alias"),
    # community_base.coursework/curriculum are installed but explicitly not
    # yet served (website/settings/base.py: "Nothing serves from these
    # tables until the D5.2 route flip"; scripts/prod/import_shared_course_platform.py
    # is their only writer, and it has not run). Each one mirrors an
    # already-reparented ``courses.*`` model field-for-field, so classifying
    # them "reparent" now is a no-op today and correct without a follow-up
    # once D5.1/D5.2 land -- the alternative, leaving them for that phase to
    # notice, is exactly the kind of drift this issue exists to close.
    AccountRelationSpec("cb_coursework.CourseRegistration", "user", "reparent"),
    AccountRelationSpec("cb_coursework.Submission", "student", "reparent"),
    AccountRelationSpec("cb_coursework.ProjectSubmission", "student", "reparent"),
    AccountRelationSpec("cb_coursework.LeaderboardComplaint", "reporter", "reparent"),
    AccountRelationSpec("cb_coursework.LeaderboardComplaint", "resolved_by", "reparent"),
    AccountRelationSpec("cb_coursework.ProjectVote", "voter", "reparent"),
    AccountRelationSpec("cb_coursework.UserWrappedStatistics", "user", "reparent"),
    AccountRelationSpec("cb_curriculum.Enrollment", "user", "reparent"),
    AccountRelationSpec("cb_curriculum.UnitProgress", "user", "reparent"),
    # courses.UnitReadState/SharedLessonReadState are live per-account read
    # markers (courses/services/unit_read_state.py, member_home.py). Each
    # carries a unique constraint on (user, unit)/(user, shared_lesson), the
    # same shape ``courses.Enrollment``'s already-accepted ``unique_together
    # = ["student", "course"]`` has under "reparent" today: a collision
    # aborts the whole merge with an IntegrityError inside the same
    # transaction rather than silently dropping a row, which is the existing
    # risk profile for every reparented relation, not a new one.
    AccountRelationSpec("courses.UnitReadState", "user", "reparent"),
    AccountRelationSpec("courses.SharedLessonReadState", "user", "reparent"),
    # Shared homework drafts are in-progress account data. Move a source
    # user's draft to the survivor; the (user, assignment_key) constraint
    # fails the merge closed if both accounts have a draft for one assignment.
    AccountRelationSpec("cb_homework_steps.HomeworkDraft", "user", "reparent"),
    # cb_api.APIKey.user: community_base.api is installed (website/settings
    # /base.py) but no urlconf, view, management command, or job in this
    # site ever includes its urls or imports the model -- website/urls.py
    # has no route for it. Nothing can create a row, so there is no row a
    # merge could lose. Revisit if this site ever wires the app in.
    AccountRelationSpec("cb_api.APIKey", "user", "no_handling_required"),
    # event_registrants.EventRegistrantIdentity.account is a nullable
    # OneToOne written only by scripts/prod/registrant_import.py and its
    # siblings, which resolve it by ``normalized_email`` against
    # ``accounts_user`` on every run (see the model's own docstring: "a
    # future import that matches this address onto a real account attaches
    # through the same account-first lookup on its next run"). A merge
    # reparenting it here would fight that reconciliation and, since the
    # field is a plain (non-unique-safe) OneToOne, could collide if the
    # survivor already has its own row; letting the next import re-resolve
    # both accounts to the one survivor is the correct convergence, not a
    # gap.
    AccountRelationSpec(
        "event_registrants.EventRegistrantIdentity",
        "account",
        "no_handling_required",
    ),
    # accounts_ext.AccountIdentityAlias.survivor is the merge's own ledger --
    # the row this relation lives on *is* the record of a previous merge, not
    # a fact about the survivor that a later merge could leave stale.
    # Reparenting it onto a new survivor when the current survivor is itself
    # later absorbed would rewrite the history it exists to preserve, in
    # place of the row a chained merge should add. Unlike the two
    # ``no_handling_required`` entries above, this table is *not* static
    # during an apply: every successful merge creates exactly one new row
    # here (the alias just recorded), so it needs the same append-only
    # evidence rule as the extension tables, not the plain-equality rule
    # ``no_handling_required`` gets -- see ``append_only_relation_keys``.
    AccountRelationSpec(
        "accounts_ext.AccountIdentityAlias",
        "survivor",
        "merge_ledger_append_only",
    ),
)

# The durable account is the user row plus its extension rows. The D3.1 field
# move took twelve fields off the user model; it did not take them out of the
# account, and the reviewed merge still decides ten of them
# (``scripts.prod.account_reconciliation.PROFILE_FIELDS``) while the identity
# window still turns on the other two. So the inventory enumerates all three
# models: a report that walked only the user model would quietly stop naming
# them, which is precisely what an operator reads this report to find out.
# The join column and each extension row's surrogate key are not account
# fields and are left out.
ACCOUNT_EXTENSION_MODELS: tuple[tuple[str, frozenset[str]], ...] = (
    ("accounts_ext.IdentityState", frozenset({"id", "user"})),
    ("courses.LearnerProfile", frozenset({"id", "user"})),
)


def _extension_join_field(model_label: str, skip: frozenset[str]) -> str:
    # The join column's name is not stored anywhere else; find it rather
    # than assume it is always "user" so a differently-named future
    # extension model does not silently miscount.
    User = get_user_model()
    model = apps.get_model(model_label)
    for name in skip:
        field = model._meta.get_field(name)
        if getattr(field, "related_model", None) is User:
            return name
    raise LookupError(f"{model_label} has no relation to the user model within {sorted(skip)!r}")


def _validate_relation(spec: AccountRelationSpec) -> None:
    model = apps.get_model(spec.model_label)
    field = model._meta.get_field(spec.field_name)
    if not (field.many_to_one or field.one_to_one) or field.related_model is not get_user_model():
        raise LookupError(f"{spec.key} must reference the account")


def installed_account_relations() -> tuple[AccountRelationSpec, ...]:
    """Activate only the reviewed optional history model; fail closed on drift."""
    for spec in _MANDATORY_RELATIONS:
        _validate_relation(spec)
    curriculum = apps.get_app_config("cb_curriculum")
    model = curriculum.models.get("courseenrollment")
    if model is None:
        return _MANDATORY_RELATIONS
    optional = AccountRelationSpec("cb_curriculum.CourseEnrollment", "user", "reparent")
    _validate_relation(optional)
    return _MANDATORY_RELATIONS + (optional,)


ACCOUNT_RELATIONS = installed_account_relations()
