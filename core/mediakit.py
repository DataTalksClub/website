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
            "Mention in the course launch stream",
            "Encouragement for students to try your product",
            "Optional demo video (up to 10 min, open-source only)",
            "Shout-out from the official Telegram channel",
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
            "Logo placement on the course page",
            "Mention in the course launch stream",
            "Live-streamed workshop, part of the course curriculum",
            "Dedicated homework assignment (3 questions)",
            "Encouragement for students to use your tool in their projects",
            "Optional raffle or giveaway for participants",
            "Permanent availability on GitHub and YouTube",
            "Post-event analytics on views and engagement",
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
            "Logo and mention in the launch stream",
            "Full module (5–10 lessons) featuring your tool",
            "Homework assignment (6–7 questions) based on your product",
            "Office hours with course participants",
            "Evergreen visibility on GitHub and YouTube",
            "Post-launch analytics on module reach and engagement",
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
    {
        "author": "Adrian Brudaru",
        "role": "dltHub",
        "url": "https://www.linkedin.com/in/data-team",
        "quote": (
            "Collaborating with DataTalks.Club for the Data Engineering Zoomcamp on "
            "data ingestion went way better than we thought. We didn't just hit our "
            "target audience; we attracted a mix of beginners and experts from diverse "
            "backgrounds.\n\nThe workshop got over 10,000 views on YouTube. Plus, the "
            "participants really put the word out, doing a lot of the marketing legwork "
            "for us. Just last week, we were featured in two podcasts, and the number "
            "of people trying out dlt doubled. Our Slack community also saw a 25% "
            "increase in a month, largely thanks to the workshop.\n\nWe will continue "
            "working with DataTalks.Club. They're focused on providing value to the "
            "professionals and sponsors alike. This workshop wasn't just another "
            "content we did - it changed the game regarding dlt's exposure to our data "
            "audience. Besides addressing the professionals who took the course, DTC "
            "put dlt on the radars of other SaaS vendors who started integrating dlt "
            "into their products.\n\nThe real win for us wasn't just the numbers. The "
            "workshop enabled us to get our tool in the hands of various types of "
            "professionals, giving us invaluable feedback."
        ),
    },
    {
        "author": "Kevin Kho",
        "role": "ex-Prefect, now Figue",
        "url": "https://www.linkedin.com/in/kvnkho/",
        "quote": (
            "At Prefect, we sponsored multiple conferences such as PyCon and KubeCon. "
            "At startup price, you pay $10k-$15k USD to have a booth for 2-3 days. We "
            "got around 200-300 people stop by the booth, a lot of which are existing "
            "users. We maybe got 4-5 real new users.\n\nCompare this to MLOps Zoomcamp "
            "where we had around 300-400 homework submissions. There are also a lot of "
            "people who just watch the courses without doing the homework. Some of "
            "these people are in brand name institutions too. I've seen people from "
            "Micron, IBM, Accenture taking the course to name a few.\n\nBeyond MLOps "
            "Zoomcamp, the DataTalks.Club Slack channel is very big on democratizing "
            "high quality information to people trying to break into industry. The "
            "Slack is very welcoming and people really help each other. They also "
            "support open source technologies with the Open Source Spotlight and the "
            "Podcast. Sponsoring the initiative feels more like it goes to a good cause "
            "in comparison to conferences."
        ),
    },
    {
        "author": "Tim Liu",
        "role": "BentoML",
        "url": "https://www.linkedin.com/in/timliu9/",
        "quote": (
            "We sponsored Module 7 of Machine Learning Zoomcamp, which covers "
            "production-ready machine learning using BentoML. Over 165 students "
            "attempted the homework, and many used BentoML in their midterm and final "
            "projects. Some units in the module had over 1,000 views, and we also "
            "noticed a lot of positive social media posts about BentoML.\n\nCompared to "
            "other sponsorship opportunities we evaluated, ML Zoomcamp aligned more "
            "with BentoML's developer-focused approach. It's been great to provide "
            "value to the open-source community and support developers in serving their "
            "models at scale.\n\nOverall, we're satisfied with our decision to support ML "
            "Zoomcamp and DataTalks.Club and we're grateful for the opportunity to help "
            "so many students learn and use BentoML."
        ),
    },
    {
        "author": "Daniel Jeffries",
        "role": "Managing Director, AI Infrastructure Alliance",
        "url": "https://www.linkedin.com/in/danjeffries/",
        "quote": (
            "AI Infrastructure Alliance (AIIA) is a collection of MLOps businesses and "
            "communities of data scientists and data engineers that helps bring clarity "
            "to MLOps through education and research.\n\nDataTalks.Club stands as one "
            "of the strongest AIIA communities, with a wide range of seasoned and "
            "aspiring data enthusiasts.\n\nDataTalks.Club continually delivers "
            "excellent content and consistently drives highly engaged people to AIIA "
            "events and research."
        ),
    },
    {
        "author": "Henrik Skogström",
        "role": "Head of Growth, Valohai",
        "url": "https://www.linkedin.com/in/skogstrom/",
        "quote": (
            "The Valohai team was looking to connect with more data scientists and ML "
            "pioneers. That is when we came across DataTalks.Club community.\n\nThanks "
            "to Alexey, who was really easy to talk to and very fast to respond, our "
            "ebook has found the right audience through the newsletter and Alexey's "
            "personal recommendations on LinkedIn.\n\nThis was one of the most fruitful "
            "collaborations in terms of lead acquisition. We are looking forward to "
            "working with Alexey further on."
        ),
    },
    {
        "author": "Nathan Jefferson",
        "role": "ex-Topcoder, Founder of IMBALANCE",
        "url": "https://www.linkedin.com/in/nathanjefferson/",
        "quote": (
            "When we kicked off a project which required some talented Data Scientists "
            "to come and help one of our clients, NASA, the challenge was speed. We were "
            "in a rush and we needed quick access to an engaged community. We found "
            "DataTalks.Club, collaborated with Alexey to create content through 3 "
            "channels: the community Slack, email newsletter and his personal LI feed."
            "\n\nOutcome: the lowest candidate acquisition cost we've seen across all "
            "channels this year.\n\nThanks, Alexey. Can't wait to work with you again."
        ),
    },
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
