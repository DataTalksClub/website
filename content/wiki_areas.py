"""Which browse area of the wiki hub a wiki page belongs to.

The podwiki pages carry a kind (guide, comparison, roadmap, transition, how-to,
or a plain topic) but no subject area, so the hub's "Browse by area" band files
each page by an explicit, reviewed keyword rule over its title.  Rules are tried
in order and the first match wins; a page no rule claims lands in ``Tools``,
the catch-all area.  Career-transition pages always file under roles and
careers, whatever their title mentions.

TODO: move the area into podwiki frontmatter (an ``area:`` key per page) and
read it from the synced record, so authors assign it and this module goes away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WikiArea:
    slug: str
    title: str
    description: str
    pattern: re.Pattern[str]


def _rule(*alternatives: str) -> re.Pattern[str]:
    return re.compile("|".join(alternatives))


#: The areas in the order the hub shows them and the rules are tried.
AREAS: tuple[WikiArea, ...] = (
    WikiArea(
        "roles-and-careers",
        "Roles and careers",
        "Roles, interviews, job search and career switches.",
        _rule(
            r"\brole",
            r"career",
            r"\bjob",
            r"\bcv\b",
            r"interview",
            r"hiring",
            r"\bhire",
            r"salary",
            r"portfolio",
            r"recruit",
            r"freelanc",
            r"mentoring",
            r"academia",
            r"founder",
            r"solopreneur",
            r"entrepreneur",
            r"public learning",
            r"competitions",
            r"volunteer",
            r"\bto\b .*(engineer|science|ml|pm)",
        ),
    ),
    WikiArea(
        "ai-and-llms",
        "AI and LLMs",
        "LLM applications, RAG, agents, evaluation and AI tooling.",
        _rule(
            r"\bai\b",
            r"llm",
            r"\brag\b",
            r"retrieval",
            r"agent",
            r"prompt",
            r"embedding",
            r"vector",
            r"generative",
            r"context eng",
            r"\bnlp\b",
            r"multimodal",
            r"text-to-sql",
            r"red team",
        ),
    ),
    WikiArea(
        "ml-and-mlops",
        "ML and MLOps",
        "Models in production: MLOps, monitoring, platforms and system design.",
        _rule(
            r"\bml\b",
            r"mlops",
            r"machine learning",
            r"model",
            r"feature store",
            r"experiment tracking",
            r"deep learning",
            r"computer vision",
            r"reinforcement",
            r"recommendation",
            r"interpretab",
            r"notebook",
            r"^production$",
            r"scikit",
            r"metaflow",
            r"reproducib",
            r"annotation",
            r"evolutionary",
            r"synthetic",
            r"personalization",
        ),
    ),
    WikiArea(
        "data-engineering",
        "Data engineering",
        "Pipelines, warehouses, lakehouses, orchestration and data quality.",
        _rule(
            r"data engineer",
            r"pipeline",
            r"warehouse",
            r"lake",
            r"\betl\b",
            r"\belt\b",
            r"dbt",
            r"airflow",
            r"orchestration",
            r"streaming",
            r"batch",
            r"\bcdc\b",
            r"dataops",
            r"iceberg",
            r"duckdb",
            r"quality",
            r"observab",
            r"contract",
            r"mesh",
            r"modern data",
            r"platform",
            r"reverse etl",
            r"finops",
            r"gitops",
            r"entity resolution",
        ),
    ),
    WikiArea(
        "analytics-and-product",
        "Analytics and product",
        "Analysis, experiments, metrics and data products.",
        _rule(
            r"analy",
            r"a/b",
            r"a/a",
            r"experiment",
            r"metric",
            r"\bkpi",
            r"dashboard",
            r"business intelligence",
            r"product",
            r"tracking",
            r"\brfm\b",
            r"causal",
            r"power analysis",
            r"customer data",
            r"activation",
            r"growth",
            r"data science",
            r"data scientist",
        ),
    ),
    WikiArea(
        "leadership-and-teams",
        "Leadership and teams",
        "Leading data teams, strategy, governance and communication.",
        _rule(
            r"lead",
            r"team",
            r"manager",
            r"officer",
            r"strategy",
            r"governance",
            r"communication",
            r"community",
            r"teaching",
            r"writing",
            r"documentation",
            r"devrel",
            r"developer rel",
            r"open source",
            r"contribut",
            r"practices",
            r"translator",
            r"trust",
            r"privacy",
            r"security",
            r"responsible",
        ),
    ),
    WikiArea(
        "tools",
        "Tools",
        "Choosing and using the tools: Python, SQL, search, CI/CD and more.",
        re.compile(r"."),
    ),
)

CATCH_ALL = AREAS[-1]


def area_for(title: str, kind: str) -> WikiArea:
    """The area a page with this title and kind is filed under."""

    if kind == "transition":
        return AREAS[0]
    folded = title.casefold()
    return next((area for area in AREAS if area.pattern.search(folded)), CATCH_ALL)


__all__ = ["AREAS", "WikiArea", "area_for"]
