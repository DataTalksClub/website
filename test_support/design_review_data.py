"""Deterministic, synthetic rendered-review data for issue #237.

This module is deliberately test support rather than an installed application.  It
composes the shared adopted-course factory, then adds only current ORM relationships
which already have public views.  Callers must point Django at an isolated SQLite
database below ``.tmp``; no production or imported snapshot is an input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse

from courses.models import (
    AnswerTypes,
    Cohort,
    Course,
    CourseRegistration,
    CurriculumFlowItem,
    CurriculumFormat,
    Enrollment,
    Homework,
    HomeworkState,
    Module,
    Project,
    ProjectState,
    Question,
    QuestionTypes,
    RegistrationCampaign,
    Unit,
    UnitReadState,
)
from courses.services.local_course_seed import assert_local_database
from events.identity import load_identity_manifest
from events.models import (
    Event,
    EventQnaCohostInvite,
    EventQnaQuestion,
    EventQnaSession,
)
from events.qna.services import event_qna_path
from test_support.design_review_identity import FROZEN_AT, SEED
from test_support.factories import FactoryContext, create_current_scenario

from .reference_data import EVENT_IDENTITY_MANIFEST

QNA_REVIEW_EVENT_PUBLIC_ID = 364


def ensure_checked_event_identity_snapshot() -> Event:
    """Restore the complete checked identity map after transactional test flushes.

    The product importer also provisions one durable Q&A job per identity. Rendered
    review tests need only the normal public-route mapping, so test support inserts
    missing checked identity rows directly and leaves jobs, aliases, and projection
    files untouched.
    """

    manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    existing = {event.id: event for event in Event.objects.all()}
    existing_public_ids = {
        event.public_id: event.id for event in existing.values() if event.public_id is not None
    }
    missing: list[Event] = []
    for item in manifest.events:
        current = existing.get(item.id)
        if current is not None:
            if (
                current.public_id != item.public_id
                or current.source_repository != item.source.repository
                or current.source_revision != item.source.revision
                or current.source_key != item.source.source_key
            ):
                raise RuntimeError("checked public event identity mapping conflicts with test DB")
            continue
        if item.public_id in existing_public_ids:
            raise RuntimeError("checked public event ID conflicts with test DB")
        missing.append(
            Event(
                id=item.id,
                public_id=item.public_id,
                title=item.title,
                slug=item.slug,
                source_repository=item.source.repository,
                source_revision=item.source.revision,
                source_key=item.source.source_key,
                source_path=item.source_path,
                source_checksum=item.source_checksum,
                lifecycle=Event.Lifecycle.PUBLISHED,
            )
        )
    if missing:
        Event.objects.bulk_create(missing)
    return Event.objects.get(public_id=QNA_REVIEW_EVENT_PUBLIC_ID)


@dataclass(frozen=True, slots=True)
class ReviewPersona:
    key: str
    username: str
    purpose: str


@dataclass(frozen=True, slots=True)
class ReviewSurface:
    key: str
    path: str
    actor: str
    state: str
    representation: str
    expected_status: int = 200


@dataclass(frozen=True, slots=True)
class DesignReviewData:
    personas: tuple[ReviewPersona, ...]
    surfaces: tuple[ReviewSurface, ...]

    def manifest(self) -> dict[str, object]:
        return {
            "frozen_at": FROZEN_AT.isoformat(),
            "generator": "test_support.design_review_data:v1",
            "personas": [
                {"key": item.key, "username": item.username, "purpose": item.purpose}
                for item in self.personas
            ],
            "seed": SEED,
            "surfaces": [
                {
                    "actor": item.actor,
                    "key": item.key,
                    "path": item.path,
                    "representation": item.representation,
                    "state": item.state,
                    "expected_status": item.expected_status,
                }
                for item in self.surfaces
            ],
        }


def _route(name: str, cohort: Cohort, **extra: object) -> str:
    return reverse(
        name,
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_year": cohort.identifier,
            **extra,
        },
    )


def _user(context: FactoryContext, key: str, username: str):
    User = get_user_model()
    return User.objects.create_user(
        id=context.physical_int("design-review-user", key),
        username=username,
        email=context.synthetic_email("design-review-user", key),
    )


def _homework(
    context: FactoryContext,
    cohort: Cohort,
    *,
    key: str,
    title: str,
    days: int,
    state: str,
) -> Homework:
    return Homework.objects.create(
        id=context.physical_int("design-review-homework", key),
        course=cohort,
        slug=key,
        title=title,
        description=(
            "Apply the lesson to a small, reproducible dataset and explain the "
            "operational trade-offs in plain language."
        ),
        instructions_markdown=(
            "## Deliverables\n\nSubmit a reproducible result, a short decision log, "
            "and one paragraph describing what you would monitor in production."
        ),
        due_date=FROZEN_AT + timedelta(days=days),
        state=state,
    )


def _project(
    context: FactoryContext,
    cohort: Cohort,
    *,
    key: str,
    title: str,
    state: str,
    days: int,
) -> Project:
    return Project.objects.create(
        id=context.physical_int("design-review-project", key),
        course=cohort,
        slug=key,
        title=title,
        description=(
            "Design, document, and defend a production-ready system using bounded "
            "replay workloads and observable acceptance criteria."
        ),
        submission_due_date=FROZEN_AT + timedelta(days=days),
        peer_review_due_date=FROZEN_AT + timedelta(days=days + 7),
        number_of_peers_to_evaluate=2,
        state=state,
    )


def _seed_event_qna(context: FactoryContext) -> tuple[str, str]:
    """Create one deterministic, individual-event Q&A review surface."""

    # The title link in every Q&A shell points back to the canonical event detail.
    # Attach the synthetic interaction state to a checked projection identity so
    # that destination remains real without adding to or altering the event list.
    event = ensure_checked_event_identity_snapshot()
    session = EventQnaSession.objects.create(
        id=context.logical_uuid("design-review-event-qna", "qna-clinic"),
        event=event,
        state=EventQnaSession.State.OPEN,
        allow_names=True,
        require_names=False,
        state_changed_at=FROZEN_AT,
        q_total=3,
        revision=2,
    )
    questions = (
        (
            "retry-budget",
            "How do you choose a retry budget when upstream latency changes throughout the day?",
            "Mina Okafor",
            True,
        ),
        (
            "recovery-signals",
            "Which signals distinguish a slow recovery from a stalled recovery?",
            "Jon Bell",
            False,
        ),
        (
            "schema-migration",
            "Can the replay boundary be moved safely after a schema migration?",
            "",
            False,
        ),
    )
    for position, (key, text, author_name, pinned) in enumerate(questions):
        question = EventQnaQuestion.objects.create(
            question_id=context.physical_key("design-review-qna-question", key, length=26),
            session=session,
            text=text,
            author_name=author_name,
            participant_digest=context.physical_key(
                "design-review-qna-participant", key, length=64
            ),
            score=1,
            pinned=pinned,
        )
        EventQnaQuestion.objects.filter(pk=question.pk).update(
            created_at=FROZEN_AT + timedelta(minutes=position)
        )
    EventQnaCohostInvite.objects.create(
        invite_id=context.physical_key("design-review-qna-invite", "review-host", length=26),
        session=session,
        name="review-host",
        passcode_digest="!synthetic-review-passcode-disabled",
        created_by_ref="review:issue-237",
    )
    public_path = f"{event_qna_path(event)}/"
    return public_path, f"{event_qna_path(event)}/cohost/review-host/"


def _unit_markdown(module_number: int, unit_number: int, *, long: bool = False) -> str:
    title = "Choosing boundaries for reliable replay and recovery"
    if long:
        title += " when throughput, ownership, and delayed side effects all compete"
    return f"""## {title}

Reliable replay begins with a clear ownership boundary. This lesson explains how a
team can make failure visible, keep retries bounded, and verify that a recovery
procedure produces the same result twice.

### Working example

| Signal | Healthy | Investigate |
| --- | ---: | ---: |
| Replay lag | under 5 min | over 20 min |
| Duplicate rate | 0 | above 0 |

```python
def review_key(module={module_number}, unit={unit_number}):
    return f"module-{{module}}-unit-{{unit}}"
```

```mermaid
flowchart LR
    Input --> Validate --> Store --> Observe
```

> A useful recovery plan is specific enough that another learner can run it.

Continue with the [next lesson](../module-{module_number:02d}/unit-{unit_number + 1:02d}).
"""


def _seed_module_cohort(
    context: FactoryContext,
    *,
    family: Course,
    active_learner,
) -> tuple[Cohort, list[Module], list[Unit], list[Project]]:
    cohort = Cohort.objects.create(
        id=context.physical_int("design-review-cohort", "native-active"),
        uuid=context.logical_uuid("design-review-cohort", "native-active"),
        course=family,
        identifier="autumn-2026",
        year=2026,
        slug="streaming-systems-lab-autumn-2026",
        title=(
            "Streaming Systems Lab: Reliable Event Pipelines from First Byte to Auditable Recovery"
        ),
        description=(
            "A hands-on cohort for building observable streaming systems, testing "
            "failure modes, and documenting recovery decisions."
        ),
        outcome="Ship a replay-safe pipeline and an evidence-backed operations guide.",
        start_date=FROZEN_AT.date() - timedelta(days=26),
        end_date=FROZEN_AT.date() + timedelta(days=50),
        curriculum_format=CurriculumFormat.MODULES,
        github_repo_url="https://repository.example.invalid/streaming-systems-lab",
        first_homework_scored=True,
        project_passing_score=7,
        visible=True,
    )
    Enrollment.objects.create(
        id=context.physical_int("design-review-enrollment", "native-active"),
        student=active_learner,
        course=cohort,
        display_name="Avery Quartz — replay and recovery study group",
        total_score=18,
        position_on_leaderboard=4,
        display_public_profile=True,
    )

    modules: list[Module] = []
    units: list[Unit] = []
    flow_position = 10
    unit_counts = (1, 4, 7, 3, 2)
    for module_number, unit_count in enumerate(unit_counts, start=1):
        homework = _homework(
            context,
            cohort,
            key=f"module-{module_number:02d}-homework",
            title=f"Module {module_number} checkpoint: evidence and recovery notes",
            days=module_number * 8 - 20,
            state=(HomeworkState.SCORED.value if module_number <= 2 else HomeworkState.OPEN.value),
        )
        Question.objects.create(
            id=context.physical_int("design-review-question", f"module-{module_number}"),
            homework=homework,
            text=(
                "Which evidence most directly shows that replay is idempotent under "
                "a duplicated delivery?"
            ),
            question_type=QuestionTypes.MULTIPLE_CHOICE.value,
            possible_answers=(
                "A stable output checksum\nA larger worker pool\nA shorter README\nA new queue name"
            ),
            correct_answer="1",
        )
        module = Module.objects.create(
            id=context.physical_int("design-review-module", f"module-{module_number}"),
            cohort=cohort,
            position=module_number * 10,
            slug=f"module-{module_number:02d}",
            title=(
                f"Module {module_number}: "
                + (
                    "Failure-aware ingestion, replay boundaries, and operational evidence"
                    if module_number == 3
                    else "Reliable streaming foundations"
                )
            ),
            terminal_homework=homework,
        )
        modules.append(module)
        for unit_number in range(1, unit_count + 1):
            unit = Unit.objects.create(
                id=context.physical_int(
                    "design-review-unit", f"module-{module_number}-unit-{unit_number}"
                ),
                module=module,
                position=unit_number * 10,
                slug=f"unit-{unit_number:02d}",
                title=(
                    "Tracing a delayed, duplicated event across ownership boundaries "
                    "without losing the learner's original intent"
                    if module_number == 3 and unit_number == 4
                    else f"Lesson {unit_number}: observable delivery and bounded recovery"
                ),
                content_markdown=_unit_markdown(
                    module_number,
                    unit_number,
                    long=module_number == 3 and unit_number == 4,
                )
                if not (module_number == 1 and unit_number == 1)
                else "",
            )
            units.append(unit)
            if module_number < 3 or unit_number <= 2:
                UnitReadState.objects.create(user=active_learner, unit=unit, read_at=FROZEN_AT)
        CurriculumFlowItem.objects.create(
            cohort=cohort,
            position=flow_position,
            module=module,
        )
        flow_position += 10
        if module_number in {2, 4}:
            project = _project(
                context,
                cohort,
                key=f"native-project-{module_number // 2}",
                title=(
                    "Recovery drill: prove a replay-safe pipeline under delayed and "
                    "duplicated deliveries"
                ),
                state=(
                    ProjectState.PEER_REVIEWING.value
                    if module_number == 2
                    else ProjectState.COLLECTING_SUBMISSIONS.value
                ),
                days=module_number * 9,
            )
            CurriculumFlowItem.objects.create(
                cohort=cohort,
                position=flow_position,
                project=project,
            )
            flow_position += 10

    return cohort, modules, units, list(cohort.project_set.order_by("id"))


@transaction.atomic
def seed_design_review_data(*, execution_namespace: str = "local-review") -> DesignReviewData:
    """Create the complete issue #237 course review graph in an empty database.

    Everything below is invented -- synthetic courses, cohorts, users and
    submissions.  The guard is *inside* the function rather than only in the
    caller: this module ships in the release image, so the refusal has to travel
    with the code that writes the rows.
    """

    assert_local_database()
    context = FactoryContext(SEED, execution_namespace, FROZEN_AT)
    scenario = create_current_scenario(
        context,
        bundle="adopted_courses",
        state="minimal_valid",
    ).by_factory()
    qna_public_path, qna_cohost_path = _seed_event_qna(context)

    legacy = scenario["adopted_courses.course"].value
    learner = scenario["adopted_courses.enrollment"].value.student
    reviewer = scenario["adopted_courses.peer_review"].value.reviewer.student
    learner.username = "review-active-learner"
    learner.save(update_fields=("username",))
    reviewer.username = "review-peer-reviewer"
    reviewer.save(update_fields=("username",))

    legacy_family = legacy.course
    legacy_family.slug = "data-reliability-zoomcamp"
    legacy_family.title = "Data Reliability Zoomcamp"
    legacy_family.description = (
        "An adopted course-platform family preserving established assignment workflows."
    )
    legacy_family.outcome = "Build and operate dependable analytical data products."
    legacy_family.save()
    legacy.slug = "data-reliability-zoomcamp-2026"
    legacy.identifier = "2026"
    legacy.year = 2026
    legacy.title = "Data Reliability Zoomcamp 2026"
    legacy.description = (
        "A realistic synthetic active cohort using the adopted homework-then-projects "
        "presentation and compatibility routes."
    )
    legacy.start_date = FROZEN_AT.date() - timedelta(days=35)
    legacy.end_date = FROZEN_AT.date() + timedelta(days=35)
    legacy.curriculum_format = CurriculumFormat.LEGACY
    legacy.first_homework_scored = True
    legacy.project_passing_score = 5
    legacy.finished = False
    legacy.save()

    legacy_homework = scenario["adopted_courses.homework"].value
    legacy_homework.slug = "reliability-baseline"
    legacy_homework.title = "Reliability baseline and failure inventory"
    legacy_homework.description = "Map failure modes before choosing recovery mechanisms."
    legacy_homework.due_date = FROZEN_AT - timedelta(days=14)
    legacy_homework.state = HomeworkState.SCORED.value
    legacy_homework.save()
    legacy_project = scenario["adopted_courses.project"].value
    legacy_project.slug = "incident-recovery-capstone"
    legacy_project.title = "Incident recovery capstone with auditable replay evidence"
    legacy_project.state = ProjectState.PEER_REVIEWING.value
    legacy_project.save()
    scenario["adopted_courses.enrollment"].value.total_score = 34
    scenario["adopted_courses.enrollment"].value.position_on_leaderboard = 2
    scenario["adopted_courses.enrollment"].value.save()

    open_homework = _homework(
        context,
        legacy,
        key="service-level-objectives",
        title="Service-level objectives for freshness, completeness, and recovery",
        days=6,
        state=HomeworkState.OPEN.value,
    )
    Question.objects.create(
        id=context.physical_int("design-review-question", "legacy-open-long"),
        homework=open_homework,
        text=(
            "Explain how you would distinguish a late-but-correct batch from a missing "
            "batch without alerting twice for the same underlying incident."
        ),
        question_type=QuestionTypes.FREE_FORM_LONG.value,
        answer_type=AnswerTypes.ANY.value,
    )
    _homework(
        context,
        legacy,
        key="closed-backfill-drill",
        title="Closed backfill drill",
        days=-30,
        state=HomeworkState.CLOSED.value,
    )
    _project(
        context,
        legacy,
        key="lineage-observability-project",
        title="Lineage and observability project",
        state=ProjectState.COLLECTING_SUBMISSIONS.value,
        days=12,
    )

    observer = _user(context, "observer", "review-unenrolled-observer")
    graduate = _user(context, "graduate", "review-course-graduate")
    for index in range(1, 9):
        person = _user(context, f"leaderboard-{index}", f"review-learner-{index:02d}")
        Enrollment.objects.create(
            id=context.physical_int("design-review-enrollment", f"leaderboard-{index}"),
            student=person,
            course=legacy,
            display_name=(
                "Synthetic Learner with an intentionally long public leaderboard name"
                if index == 8
                else f"Synthetic Learner {index:02d}"
            ),
            display_on_leaderboard=index != 7,
            total_score=42 - index * 3,
            position_on_leaderboard=index + 2,
        )

    completed_legacy = Cohort.objects.create(
        id=context.physical_int("design-review-cohort", "legacy-completed"),
        uuid=context.logical_uuid("design-review-cohort", "legacy-completed"),
        course=legacy_family,
        identifier="2025",
        year=2025,
        slug="data-reliability-zoomcamp-2025",
        title="Data Reliability Zoomcamp 2025",
        description="Completed synthetic adopted-platform cohort retained in the archive.",
        start_date=FROZEN_AT.date() - timedelta(days=420),
        end_date=FROZEN_AT.date() - timedelta(days=330),
        curriculum_format=CurriculumFormat.LEGACY,
        first_homework_scored=True,
        finished=True,
    )
    completed_hw = _homework(
        context,
        completed_legacy,
        key="completed-scorecard",
        title="Completed reliability scorecard",
        days=-350,
        state=HomeworkState.SCORED.value,
    )
    completed_project = _project(
        context,
        completed_legacy,
        key="completed-capstone",
        title="Completed reliability capstone",
        state=ProjectState.COMPLETED.value,
        days=-340,
    )
    del completed_hw, completed_project
    Enrollment.objects.create(
        id=context.physical_int("design-review-enrollment", "graduate"),
        student=graduate,
        course=completed_legacy,
        display_name="Riley Synthetic — 2025 graduate",
        total_score=71,
        certificate_name="Riley Synthetic",
        certificate_url="https://certificates.example.invalid/review-course-graduate",
        position_on_leaderboard=1,
    )
    empty_legacy = Cohort.objects.create(
        id=context.physical_int("design-review-cohort", "legacy-empty"),
        uuid=context.logical_uuid("design-review-cohort", "legacy-empty"),
        course=legacy_family,
        identifier="archive-preview",
        year=2024,
        slug="data-reliability-zoomcamp-archive-preview",
        title="Data Reliability Zoomcamp — empty curriculum archive preview",
        description="A supported empty legacy curriculum state for layout review.",
        start_date=FROZEN_AT.date() - timedelta(days=700),
        end_date=FROZEN_AT.date() - timedelta(days=620),
        curriculum_format=CurriculumFormat.LEGACY,
        finished=True,
        visible=False,
    )

    native_family = Course.objects.create(
        id=context.logical_uuid("design-review-family", "native"),
        slug="streaming-systems-lab",
        title="Streaming Systems Lab",
        description="A DB-managed course family using the module curriculum presentation.",
        outcome="Ship an observable streaming pipeline and recovery guide.",
        visible=True,
    )
    native, modules, units, projects = _seed_module_cohort(
        context,
        family=native_family,
        active_learner=learner,
    )
    upcoming_native = Cohort.objects.create(
        id=context.physical_int("design-review-cohort", "native-upcoming"),
        uuid=context.logical_uuid("design-review-cohort", "native-upcoming"),
        course=native_family,
        identifier="spring-2027",
        year=2027,
        slug="streaming-systems-lab-spring-2027",
        title="Streaming Systems Lab — Spring 2027",
        description="Upcoming synthetic cohort with registration open and no curriculum yet.",
        start_date=FROZEN_AT.date() + timedelta(days=100),
        end_date=FROZEN_AT.date() + timedelta(days=180),
        registration_url="https://registration.example.invalid/streaming-systems-lab",
        curriculum_format=CurriculumFormat.MODULES,
        visible=True,
    )
    campaign = RegistrationCampaign.objects.create(
        id=context.physical_int("design-review-campaign", "native-upcoming"),
        slug="streaming-systems-lab-spring-2027",
        title="Streaming Systems Lab",
        edition_label="Spring 2027 cohort",
        current_course=upcoming_native,
        is_active=True,
        marketing_markdown=(
            "Build a reliable event pipeline in public, with weekly laboratories, "
            "bounded exercises, and a peer-reviewed recovery drill."
        ),
        meta_description="Free synthetic review cohort for streaming systems.",
    )
    CourseRegistration.objects.create(
        id=context.physical_int("design-review-registration", "already-registered"),
        campaign=campaign,
        course=upcoming_native,
        user=learner,
        email=learner.email,
        name="Avery Quartz",
        country="Exampleland",
        region="Synthetic region",
        role=CourseRegistration.Role.DATA_ENGINEER,
        comment="Practice recovery procedures and make system behavior explainable.",
    )

    closed_campaign = RegistrationCampaign.objects.create(
        id=context.physical_int("design-review-campaign", "closed"),
        slug="streaming-systems-lab-closed-preview",
        title="Streaming Systems Lab",
        edition_label="Closed preview",
        current_course=upcoming_native,
        is_active=False,
        marketing_markdown="Registration is intentionally closed in this review state.",
    )

    legacy_enrollment = scenario["adopted_courses.enrollment"].value
    wrapped = scenario["adopted_courses.wrapped_statistics"].value
    wrapped.year = 2026
    wrapped.total_participants = 12
    wrapped.total_enrollments = 17
    wrapped.course_stats = [
        {
            "title": "Data Reliability Zoomcamp 2026",
            "slug": legacy.slug,
            "enrollment_count": 9,
        },
        {
            "title": "Streaming Systems Lab — Autumn 2026",
            "slug": native.slug,
            "enrollment_count": 8,
        },
    ]
    wrapped.leaderboard = [
        {
            "display_name": "Avery Quartz",
            "rank": 1,
            "student_id": learner.id,
            "total_score": 71,
        },
        {
            "display_name": "Synthetic Peer Reviewer",
            "rank": 2,
            "student_id": reviewer.id,
            "total_score": 64,
        },
    ]
    wrapped.save()
    user_wrapped = scenario["adopted_courses.user_wrapped_statistics"].value
    user_wrapped.total_points = 34
    user_wrapped.courses = [
        {
            "title": "Data Reliability Zoomcamp 2026",
            "slug": legacy.slug,
            "score": 34,
            "enrollment_id": legacy_enrollment.id,
        },
    ]
    user_wrapped.rank = 2
    user_wrapped.display_name = "Avery Quartz"
    user_wrapped.total_hours = 19.5
    user_wrapped.homework_count = 2
    user_wrapped.project_count = 1
    user_wrapped.peer_reviews_given = 1
    user_wrapped.learning_in_public_count = 3
    user_wrapped.faq_contributions_count = 1
    user_wrapped.certificates_earned = 0
    user_wrapped.save()

    surfaces = (
        ReviewSurface(
            "courses-index-anonymous", "/courses", "anonymous", "mixed lifecycle", "both"
        ),
        ReviewSurface(
            "legacy-family",
            reverse("course_family", args=[legacy_family.slug]),
            "anonymous",
            "multiple cohorts",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-active-anonymous",
            legacy.canonical_url_path,
            "anonymous",
            "active and unenrolled",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-active-enrolled",
            legacy.canonical_url_path,
            "active-learner",
            "submitted, scored, open, closed",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-compatibility",
            f"/courses/{legacy.slug}/",
            "anonymous",
            "compatibility route redirects to its canonical family/cohort URL",
            "legacy edition-slug adapter",
            301,
        ),
        ReviewSurface(
            "legacy-completed",
            completed_legacy.canonical_url_path,
            "graduate",
            "completed with certificate",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-hidden-empty",
            empty_legacy.canonical_url_path,
            "anonymous",
            "hidden direct-link empty archive",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "native-family",
            reverse("course_family", args=[native_family.slug]),
            "anonymous",
            "multiple cohorts",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-active-anonymous",
            native.canonical_url_path,
            "anonymous",
            "dense active curriculum",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-active-enrolled",
            native.canonical_url_path,
            "active-learner",
            "read and unread progress",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-registration",
            reverse("registration_campaign", args=[campaign.slug]),
            "anonymous",
            "registration open",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-already-registered",
            reverse("registration_campaign", args=[campaign.slug]),
            "active-learner",
            "already registered",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-registration-closed",
            reverse("registration_campaign", args=[closed_campaign.slug]),
            "anonymous",
            "closed campaign safe denial",
            "DB-managed module curriculum",
            404,
        ),
        ReviewSurface(
            "native-module-dense",
            reverse("module", args=[native_family.slug, native.identifier, modules[2].slug]),
            "active-learner",
            "seven units, mixed read state",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-unit-rich",
            reverse(
                "unit",
                args=[
                    native_family.slug,
                    native.identifier,
                    modules[2].slug,
                    modules[2].units.order_by("position")[3].slug,
                ],
            ),
            "active-learner",
            "long title, table, code, Mermaid, prev-next",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "native-unit-empty",
            reverse(
                "unit", args=[native_family.slug, native.identifier, modules[0].slug, units[0].slug]
            ),
            "anonymous",
            "empty-content fallback",
            "DB-managed module curriculum",
        ),
        ReviewSurface(
            "legacy-dashboard",
            _route("dashboard", legacy),
            "active-learner",
            "busy progress",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-dashboard-unenrolled",
            _route("dashboard", empty_legacy),
            "observer",
            "empty hidden cohort with no enrollment, assignment, or progress data",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-homework-open",
            _route("homework", legacy, homework_slug=open_homework.slug),
            "active-learner",
            "open and unsubmitted",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-homework-scored",
            _route("homework", legacy, homework_slug=legacy_homework.slug),
            "active-learner",
            "submitted and scored",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-homework-closed",
            _route("homework", legacy, homework_slug="closed-backfill-drill"),
            "observer",
            "closed and unsubmitted",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-homework-stats",
            _route("homework_statistics", legacy, homework_slug=legacy_homework.slug),
            "anonymous",
            "scored statistics",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-project-peer-review",
            _route("project", legacy, project_slug=legacy_project.slug),
            "active-learner",
            "submitted and peer reviewing",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-project-collecting",
            _route("project", legacy, project_slug="lineage-observability-project"),
            "active-learner",
            "collecting submissions",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-peer-review",
            _route("projects_eval", legacy, project_slug=legacy_project.slug),
            "peer-reviewer",
            "assigned and completed review",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-leaderboard",
            _route("leaderboard", legacy),
            "active-learner",
            "populated with hidden and long names",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-score-breakdown",
            _route("leaderboard_score_breakdown", legacy, enrollment_id=legacy_enrollment.id),
            "active-learner",
            "score detail",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "legacy-complaint",
            _route("leaderboard_complaint", legacy, enrollment_id=legacy_enrollment.id),
            "peer-reviewer",
            "complaint form",
            "adopted legacy curriculum",
        ),
        ReviewSurface(
            "wrapped-aggregate",
            reverse("wrapped", args=[wrapped.year]),
            "anonymous",
            "visible aggregate",
            "adopted platform",
        ),
        ReviewSurface(
            "wrapped-no-data",
            reverse("wrapped", args=[2025]),
            "anonymous",
            "hidden or absent aggregate",
            "adopted platform",
        ),
        ReviewSurface(
            "wrapped-individual",
            reverse("user_wrapped", args=[wrapped.year, learner.id]),
            "anonymous",
            "shareable learner summary",
            "adopted platform",
        ),
        ReviewSurface(
            "wrapped-individual-empty",
            reverse("user_wrapped", args=[wrapped.year, observer.id]),
            "anonymous",
            "learner with no activity",
            "adopted platform",
        ),
        ReviewSurface(
            "event-qna-participant",
            qna_public_path,
            "anonymous",
            "open Q&A with named, anonymous, pinned, and populated questions",
            "website-native individual-event Q&A",
        ),
        ReviewSurface(
            "event-qna-cohost-gate",
            qna_cohost_path,
            "anonymous",
            "co-host passcode entry",
            "website-native individual-event Q&A",
        ),
    )
    return DesignReviewData(
        personas=(
            ReviewPersona(
                "active-learner",
                learner.username,
                "enrolled in legacy and module cohorts with mixed progress",
            ),
            ReviewPersona(
                "peer-reviewer", reviewer.username, "assigned peer review and complaint states"
            ),
            ReviewPersona(
                "graduate", graduate.username, "completed cohort, score, and certificate state"
            ),
            ReviewPersona(
                "observer", observer.username, "authenticated but unenrolled safe-denial state"
            ),
        ),
        surfaces=surfaces,
    )


__all__ = [
    "DesignReviewData",
    "FROZEN_AT",
    "ReviewPersona",
    "ReviewSurface",
    "SEED",
    "seed_design_review_data",
]
