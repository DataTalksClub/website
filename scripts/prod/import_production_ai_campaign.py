#!/usr/bin/env python3
"""Create the Production AI registration campaign (Mikhail / Nebius).

One-time bootstrap, safe to re-run. Creates the course family, the 2026
cohort and the application-style registration campaign for
"Production AI: Agents + Generative Media" (limited seats,
medium-advanced level, approval after registration). Every write is keyed
on a natural slug, so a second run reports ``replayed`` and creates
nothing.

Operational fields (``current_course``, ``is_active``,
``requires_approval``, ``application_notice``, ``edition_label``) are
replayed on every run. Marketing copy (``marketing_markdown``,
``meta_description``) is written only on create or with
``--refresh-copy``: once Mikhail's final copy lands, editors curate it in
Studio/admin exactly like every other campaign, and a re-run must not
clobber it.

    uv run --frozen python scripts/prod/import_production_ai_campaign.py \\
        --database .tmp/local.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "one-time"
BOOTSTRAPS_EMPTY_DATABASE = True

FAMILY_SLUG = "production-ai-agents-generative-media"
COHORT_SLUG = "production-ai-agents-generative-media-2026"
CAMPAIGN_SLUG = "production-ai-agents-generative-media"
COHORT_YEAR = 2026
COURSE_TITLE = "Production AI: Agents + Generative Media"

COURSE_DESCRIPTION = (
    "A 4-week, project-based course on building production AI systems in "
    "which agents orchestrate GPU-powered image, video and audio models."
)
COURSE_PREREQUISITES = (
    "Python, Docker basics, HTTP APIs, command line. "
    "No diffusion theory required. "
    "Prior LLM or MLOps course experience helps, not required."
)
WEEKLY_COMMITMENT = "4 weeks, about 8-10 hours per week. Free."

APPLICATION_NOTICE = (
    "4 weeks, about 8-10 hours per week, free and open-source. "
    "Prerequisites: Python, Docker basics, HTTP APIs, command line. "
    "Only applicants whose background fits the medium-advanced level "
    "are approved."
)

MARKETING_MARKDOWN = """\
## Production AI: Agents + Generative Media

4 weeks · ~8–10 h/week · free · open-source · project-based.

> How do you build a production AI system when an agent can launch expensive,
> slow, failure-prone GPU workloads that produce large media artifacts?

Outcome: build, deploy, evaluate, optimize and monitor a production AI system
in which agents orchestrate GPU-powered image, video and audio models.

### Program

- **Week 1 — Make it work.** Generative media workloads: modalities and model
  families, first GPU generation from script to container to GPU job,
  containers for generative AI, GPU engineering (precision, quantization,
  offloading, batching, OOM debugging), jobs vs endpoints.
- **Week 2 — Make it intelligent.** Agents that use GPUs: the agent loop,
  tools and structured outputs, CPU agents with GPU tools, long-running tools.
- **Week 3 — Make it scale.**
- **Week 4 — Make it production-ready.**

### Who this is for

- ML / MLOps / data engineers adding image, video or audio generation to real systems.
- Backend engineers turning a prototype into a service with SLOs and a cost line.
- Founders and small teams deciding when self-hosting beats per-call media APIs.

### Prerequisites

Python, Docker basics, HTTP APIs, command line. No diffusion theory required.
Prior LLM or MLOps course experience helps, not required.
"""

META_DESCRIPTION = (
    "Production AI: Agents + Generative Media — a free 4-week project-based "
    "course on production GPU media systems. Limited seats, application only."
)


class ProductionAICampaignImportError(RuntimeError):
    """A safe refusal that carries a condition code, never a row value."""


def run(*, apply: bool = True, refresh_copy: bool = False) -> dict[str, Any]:
    from django.db import transaction

    from courses.models import Cohort, Course, RegistrationCampaign

    if not apply:
        return {
            "applied": False,
            "family_slug": FAMILY_SLUG,
            "cohort_slug": COHORT_SLUG,
            "campaign_slug": CAMPAIGN_SLUG,
            "family_exists": Course.objects.filter(slug=FAMILY_SLUG).exists(),
            "cohort_exists": Cohort.objects.filter(slug=COHORT_SLUG).exists(),
            "campaign_exists": RegistrationCampaign.objects.filter(slug=CAMPAIGN_SLUG).exists(),
        }

    with transaction.atomic():
        family, family_created = Course.objects.update_or_create(
            slug=FAMILY_SLUG,
            defaults={
                "title": COURSE_TITLE,
                "description": COURSE_DESCRIPTION,
                "prerequisites": COURSE_PREREQUISITES,
                "weekly_commitment": WEEKLY_COMMITMENT,
            },
        )
        cohort, cohort_created = Cohort.objects.update_or_create(
            slug=COHORT_SLUG,
            defaults={
                "course": family,
                "identifier": str(COHORT_YEAR),
                "year": COHORT_YEAR,
                "title": f"{COURSE_TITLE} {COHORT_YEAR}",
                "description": COURSE_DESCRIPTION,
                "visible": True,
            },
        )
        campaign, campaign_created = RegistrationCampaign.objects.update_or_create(
            slug=CAMPAIGN_SLUG,
            defaults={
                "title": COURSE_TITLE,
                "edition_label": f"{COHORT_YEAR} cohort",
                "current_course": cohort,
                "is_active": True,
                "requires_approval": True,
                "application_notice": APPLICATION_NOTICE,
            },
        )
        copy_written = False
        if campaign_created or refresh_copy:
            campaign.marketing_markdown = MARKETING_MARKDOWN
            campaign.meta_description = META_DESCRIPTION
            campaign.save(update_fields=["marketing_markdown", "meta_description"])
            copy_written = True

    return {
        "applied": True,
        "family_slug": family.slug,
        "family_created": family_created,
        "cohort_slug": cohort.slug,
        "cohort_created": cohort_created,
        "campaign_slug": campaign.slug,
        "campaign_created": campaign_created,
        "copy_written": copy_written,
        "register_path": f"/courses/register/{campaign.slug}/",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report which rows exist and write nothing.",
    )
    parser.add_argument(
        "--refresh-copy",
        action="store_true",
        help="Rewrite the campaign marketing copy from this script.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        configure_target(parser, args)
        report = run(apply=not args.dry_run, refresh_copy=args.refresh_copy)
    except ProductionAICampaignImportError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
