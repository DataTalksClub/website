"""Public acknowledgements for organizations that supported DataTalks.Club."""

from __future__ import annotations

from django.templatetags.static import static

FEATURED_SUPPORTERS = (
    {
        "name": "dltHub",
        "url": "https://dlthub.com/",
        "logo": "dlthub.png",
        "description": (
            "dltHub builds dlt, the open-source Python library for moving data from "
            "messy sources into well-structured datasets. Their collaboration brings "
            "production-minded data ingestion into Data Engineering Zoomcamp and "
            "LLM Zoomcamp."
        ),
    },
    {
        "name": "Astronomer",
        "url": "https://www.astronomer.io/",
        "logo": "astronomer.png",
        "description": (
            "Astronomer helps teams build and run reliable data pipelines with Apache "
            "Airflow. Their support helps us teach practical orchestration skills to "
            "the next generation of data engineers."
        ),
    },
    {
        "name": "Kestra",
        "url": "https://kestra.io/",
        "logo": "kestra.png",
        "description": (
            "Kestra is an open-source orchestration platform for business-critical "
            "workflows. Community members encounter it through hands-on projects in "
            "Data Engineering Zoomcamp and LLM Zoomcamp."
        ),
    },
    {
        "name": "Snowplow",
        "url": "https://snowplow.io/",
        "logo": "snowplow.png",
        "description": (
            "Snowplow gives teams control of high-quality behavioral data for analytics "
            "and AI. Their support helps us keep community events and practical learning "
            "available to everyone."
        ),
    },
)

PAST_SUPPORTERS = (
    "AI Infrastructure Alliance",
    "Aiven",
    "Anaconda",
    "Arize AI",
    "Astronomer",
    "Atlan",
    "BentoML",
    "Bruin",
    "dltHub",
    "Double Cloud",
    "dstack",
    "Exasol",
    "Iterative",
    "JetBrains",
    "Kestra",
    "Mage",
    "Nebius",
    "NVIDIA",
    "Prefect",
    "Qwak",
    "Saturn Cloud",
    "Scale AI",
    "Snorkel AI",
    "Snowflake",
    "Snowplow",
    "SODA",
    "Tecton",
    "Temporal.io",
    "Toloka",
    "Valohai",
    "Weights & Biases",
    "WhyLabs",
    "WikiMedia",
)


def featured_supporters() -> tuple[dict[str, str], ...]:
    return tuple(
        {
            **supporter,
            "logo_url": static(f"core/sponsors/{supporter['logo']}"),
        }
        for supporter in FEATURED_SUPPORTERS
    )
