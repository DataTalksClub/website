"""Hidden, share-by-link media kit page."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.http import require_safe

ARCHIVED_SPONSORS = (
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


NEWSLETTER_PACKAGES = (
    {
        "title": "Secondary Slot",
        "price": "€1,000",
        "description": (
            "Placed in the Events section after the main announcement, a high-visibility "
            "slot for events, webinars, and conferences."
        ),
        "image": "newsletter-secondary.png",
        "metric": "250–300 clicks",
        "features": (
            "Positioned in the middle of the newsletter",
            "Great for events, webinars, conferences & community initiatives",
        ),
        "url": "https://us19.campaign-archive.com/?u=0d7822ab98152f5afc118c176&id=482b851777",
    },
    {
        "title": "Primary Slot",
        "price": "€1,500",
        "description": (
            "At the very top of our weekly newsletter, opened every Monday by data "
            "engineers, ML engineers, and analytics leaders."
        ),
        "image": "newsletter-primary.png",
        "metric": "700–1,500 clicks",
        "features": (
            "Featured at the very top — our most visible placement",
            "Best for major launches, courses, e-books & guides",
        ),
        "url": "https://us19.campaign-archive.com/?u=0d7822ab98152f5afc118c176&id=7cbf64d3d0",
    },
    {
        "title": "Stand-Alone",
        "price": "€2,500",
        "description": (
            "A dedicated email to our entire audience featuring only your content — "
            "ideal for launches and lead-generation campaigns."
        ),
        "image": "newsletter-standalone.png",
        "metric": "700–2,000 clicks",
        "features": (
            "A dedicated send with no other sponsors and no other content",
            "Full control over the send date and time",
        ),
        "url": "https://us19.campaign-archive.com/?u=0d7822ab98152f5afc118c176&id=314cda7d4b",
    },
)

COURSE_PACKAGES = (
    {
        "title": "Mention",
        "price": "€5,000",
        "image": "course-mention.png",
        "description": (
            "Get your brand featured in one of our popular Zoomcamp courses, followed "
            "by thousands of learners worldwide."
        ),
        "metric": "5K–10K views",
        "features": (
            "Logo placement on the course page throughout the cohort",
            "Mention in the course launch stream and official Telegram channel",
            "Optional demo video (up to 10 min, open-source only)",
            "Post-launch analytics on course reach and video performance",
        ),
        "example_url": (
            "https://github.com/DataTalksClub/machine-learning-zoomcamp/blob/"
            "e787517e9c9555fc873d1be262193bfaecc0d9d2/08-deep-learning/"
            "01b-saturn-cloud.md"
        ),
        "example_label": "See it in the course",
    },
    {
        "title": "Workshop",
        "price": "€10,000",
        "extra": "+€5,000 if done by us",
        "image": "course-workshop.png",
        "description": (
            "A hands-on workshop inside a course module, teaching students to apply "
            "concepts using your product."
        ),
        "metric": "5K–10K views",
        "features": (
            "Live-streamed workshop, part of the course curriculum",
            "Dedicated homework assignment (3 questions)",
            "Optional raffle or giveaway for participants",
            "Permanent availability on GitHub and YouTube",
        ),
        "example_url": (
            "https://github.com/DataTalksClub/data-engineering-zoomcamp/blob/main/"
            "cohorts/2024/workshops/dlt.md"
        ),
        "example_label": "See the dlt workshop",
    },
    {
        "title": "Full Module",
        "price": "€20,000",
        "extra": "+€5,000 if done by us",
        "image": "course-module.png",
        "description": (
            "Our deepest integration: your tool is used to teach a full module topic "
            "that students use throughout."
        ),
        "metric": "10K–50K total views",
        "features": (
            "Full module (5–10 lessons) featuring your tool",
            "Homework and office hours with course participants",
            "Evergreen visibility on GitHub and YouTube",
            "For open-source tools only",
        ),
        "example_url": (
            "https://github.com/DataTalksClub/data-engineering-zoomcamp/tree/main/"
            "cohorts/2024/02-workflow-orchestration"
        ),
        "example_label": "See the module",
    },
)

TESTIMONIALS = (
    (
        "Adrian Brudaru",
        "dltHub",
        "Collaborating with DataTalks.Club for the Data Engineering Zoomcamp on data "
        "ingestion went way better than we thought. The workshop got over 10,000 views "
        "on YouTube, and the number of people trying out dlt doubled.",
    ),
    (
        "Tim Liu",
        "BentoML",
        "We sponsored Module 7 of Machine Learning Zoomcamp. Over 165 students attempted "
        "the homework, and many used BentoML in their midterm and final projects.",
    ),
    (
        "Henrik Skogström",
        "Head of Growth, Valohai",
        "This was one of the most fruitful collaborations in terms of lead acquisition. "
        "We are looking forward to working with Alexey further on.",
    ),
    (
        "Kevin Kho",
        "ex-Prefect, now Figue",
        "Compare this to MLOps Zoomcamp where we had around 300–400 homework submissions. "
        "Sponsoring the initiative feels more like it goes to a good cause in comparison "
        "to conferences.",
    ),
    (
        "Daniel Jeffries",
        "Managing Director, AI Infrastructure Alliance",
        "DataTalks.Club stands as one of the strongest AIIA communities, with a wide "
        "range of seasoned and aspiring data enthusiasts.",
    ),
    (
        "Nathan Jefferson",
        "ex-Topcoder, Founder of IMBALANCE",
        "The outcome was the lowest candidate acquisition cost we've seen across all "
        "channels this year.",
    ),
)


@require_safe
def media_kit(request: HttpRequest) -> HttpResponse:
    newsletter_packages = tuple(
        {**package, "image_url": static(f"core/mediakit/{package['image']}")}
        for package in NEWSLETTER_PACKAGES
    )
    course_packages = tuple(
        {**package, "image_url": static(f"core/mediakit/{package['image']}")}
        for package in COURSE_PACKAGES
    )
    response = render(
        request,
        "core/mediakit.html",
        {
            "canonical_url": "https://datatalks.club/mediakit/",
            "newsletter_packages": newsletter_packages,
            "course_packages": course_packages,
            "archived_sponsors": ARCHIVED_SPONSORS,
            "testimonials": TESTIMONIALS,
        },
    )
    # The page is intentionally shared by direct URL only, including in production.
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
