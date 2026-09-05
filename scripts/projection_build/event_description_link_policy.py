"""Shared, code-owned link decisions for reviewed event descriptions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import MappingProxyType
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# The route registry is read from the migration helper projection, which is
# where the built snapshot lives; runtime never reads it.
PROJECTION_ROOT = REPOSITORY_ROOT / "temporary" / "content" / "public_projection"

FRAGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:%~-]{0,199}$")

EXPECTED_LINK_DECISION_COUNTS = MappingProxyType(
    {
        "external_resource_kept": 148,
        "internal_rewritten": 154,
        "internal_target_missing": 1,
        "provider_action_removed": 49,
        "registration_action_removed": 185,
        "remote_image_removed": 1,
        "unreviewed_shortlink_removed": 2,
    }
)

REMOVED_REGISTRATION_HOSTS = frozenset(
    {
        "airtable.com",
        "app.slack.com",
        "app.sli.do",
        "forms.gle",
        "maven.com",
        "qna.dtcdev.click",
        "www.meetup.com",
    }
)
FORBIDDEN_JOIN_HOSTS = frozenset(
    {
        "discord.gg",
        "join.slack.com",
        "meet.google.com",
        "teams.microsoft.com",
        "zoom.us",
    }
)
REVIEWED_EXTERNAL_HOSTS = frozenset(
    {
        "alod83.medium.com",
        "app--data-impact-compass-49e1cfff.base44.app",
        "aurelytix.com",
        "blog.ivan.digital",
        "book.the-turing-way.org",
        "calmcode.io",
        "carpentries.org",
        "deeplearning.ai",
        "dev.to",
        "dlthub.com",
        "docs.google.com",
        "double.cloud",
        "flowexperts.ai",
        "github.com",
        "high-signal.delphina.ai",
        "hub.superlinked.com",
        "intervu.ai",
        "iscbsc.org",
        "koaning.io",
        "llmsforsocialscience.net",
        "maryamramezani.com",
        "massive.com",
        "medium.com",
        "mercedes-benz.io",
        "multiomics-analytics-group.github.io",
        "neubird.ai",
        "openlifesci.org",
        "orkes.io",
        "pythoninvest.com",
        "risingwave.com",
        "snowplow.io",
        "streamlit.io",
        "superlinked.com",
        "temporal.io",
        "twitter.com",
        "vanishinggradients.fireside.fm",
        "wandb.me",
        "www.astronomer.io",
        "www.biosustain.dtu.dk",
        "www.jetbrains.com",
        "www.kaggle.com",
        "www.kern.ai",
        "www.linkedin.com",
        "www.snowflake.com",
        "www.wikimedia.de",
        "www.youtube.com",
    }
)
INTERNAL_ALIASES = MappingProxyType(
    {
        "/articles.html": "/blog",
        "/blog/": "/blog",
        "/podcast.html": "/podcast",
        "/podcast/": "/podcast",
        "/books.html": "/books",
        "/books/": "/books",
        "/events.html": "/events",
        "/events/": "/events",
        "/courses/": "/courses",
        "/wiki/": "/wiki",
    }
)

# Only destinations produced by the accepted source review may reach stored HTML. Updating
# source content therefore requires a deliberate code review of this literal set as well as a
# new decision-inventory digest. Host-level approval alone is intentionally insufficient.
REVIEWED_RENDERED_LINKS = frozenset(
    {
        "http://Flowexperts.ai",
        "http://Mercedes-Benz.io",
        "http://NeuBird.ai",
        "http://Superlinked.com",
        "http://dev.to",
        "http://intervu.ai",
        "http://pythoninvest.com",
        "http://wandb.me/data_talks_club",
        "https://DeepLearning.ai",
        "https://Flowexperts.ai",
        "https://Massive.com",
        "https://PythonInvest.com",
        "https://alod83.medium.com/",
        "https://app--data-impact-compass-49e1cfff.base44.app/",
        "https://aurelytix.com/",
        "https://blog.ivan.digital/context-engineering-for-agentic-hybrid-applications-why-code-agents-fail-and-how-to-fix-them-076cab699262",
        "https://book.the-turing-way.org/",
        "https://calmcode.io",
        "https://carpentries.org/",
        "https://datatalks.club/",
        "https://datatalks.club/blog/data-engineering-zoomcamp.html",
        "https://datatalks.club/blog/guide-to-free-online-courses-at-datatalks-club.html#llm-zoomcamp",
        "https://datatalks.club/blog/machine-learning-zoomcamp.html",
        "https://dlthub.com/",
        "https://docs.google.com/document/d/15ENlteDAkPfGA69ibHYk0hzPh8SO3-v2l13FfmS2sd0/edit?tab=t.0#heading=h.9hovmrjzpeli",
        "https://double.cloud/",
        "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp",
        "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/01-overview",
        "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/02-end-to-end",
        "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/03-mcp",
        "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/04-ai-security-audit-devops",
        "https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/04-devops",
        "https://github.com/DataTalksClub/data-engineering-zoomcamp",
        "https://github.com/DataTalksClub/llm-zoomcamp",
        "https://github.com/DataTalksClub/llm-zoomcamp/tree/main",
        "https://github.com/DataTalksClub/llm-zoomcamp/tree/main#-capstone-project",
        "https://github.com/DataTalksClub/llm-zoomcamp/tree/main#-course-syllabus",
        "https://github.com/DataTalksClub/llm-zoomcamp/tree/main#-what-youll-learn",
        "https://github.com/DataTalksClub/machine-learning-zoomcamp",
        "https://github.com/DataTalksClub/machine-learning-zoomcamp/tree/master",
        "https://github.com/DataTalksClub/open-source-llm-zoomcamp",
        "https://github.com/Multiomics-Analytics-Group/MicW2Graph",
        "https://github.com/Multiomics-Analytics-Group/vuecore",
        "https://github.com/Multiomics-Analytics-Group/vuegen",
        "https://github.com/aaalexlit",
        "https://github.com/aaalexlit/faq-slack-bot",
        "https://github.com/alexeygrigorev/telegram-writing-assistant/blob/master/articles/faq-assistant-on-automator.md",
        "https://github.com/alod83",
        "https://github.com/ivan-digital/llm-alignment/blob/master/Qwen3_Adapters_SoftPrompt_LoRA_KL_SFT_Workshop.executed.ipynb",
        "https://high-signal.delphina.ai/",
        "https://hub.superlinked.com/",
        "https://iscbsc.org/",
        "https://koaning.io",
        "https://llmsforsocialscience.net/",
        "https://maryamramezani.com",
        "https://medium.com/@aaalex.lit",
        "https://multiomics-analytics-group.github.io/",
        "https://openlifesci.org/",
        "https://orkes.io/",
        "https://pythoninvest.com/course",
        "https://risingwave.com/",
        "https://snowplow.io/",
        "https://streamlit.io/creators",
        "https://temporal.io/?_gl=1*1wqns7o*_gcl_au*MjExMTkzNzU4My4xNzYzOTMxOTE3*_ga*MTkyMDE2NjY1NC4xNzYzOTMxOTE3*_ga_R90Q9SJD3D*czE3NjM5MzQxNDckbzIkZzEkdDE3NjM5MzQxNjAkajQ3JGwwJGgw",
        "https://twitter.com/aaalexlit",
        "https://twitter.com/alod83",
        "https://vanishinggradients.fireside.fm/",
        "https://www.astronomer.io/",
        "https://www.biosustain.dtu.dk/",
        "https://www.jetbrains.com/",
        "https://www.kaggle.com/competitions/ai-mathematical-olympiad-progress-prize-2/",
        "https://www.kern.ai/",
        "https://www.linkedin.com/in/agrigorev/",
        "https://www.linkedin.com/in/alex-l-45464424/",
        "https://www.linkedin.com/in/angelicaloduca/",
        "https://www.snowflake.com/en/",
        "https://www.snowflake.com/en/engineering-blog/ai-agent-evaluation-gpa-framework/",
        "https://www.wikimedia.de/",
        "https://www.youtube.com/watch?v=GH3lrOsU3AU&t=6s&pp=0gcJCccJAYcqIYzv",
        "https://www.youtube.com/watch?v=MeDUe75WQaQ&list=PLSWnIAnueyu8KBcwhE48ASp20nZMg2qtt",
    }
)


class EventDescriptionLinkPolicyError(ValueError):
    """A bounded, content-free link policy failure."""


def projection_routes_and_fragments() -> tuple[set[str], dict[str, set[str]]]:
    paths = {
        "/",
        "/blog",
        "/podcast",
        "/events",
        "/books",
        "/courses",
        "/wiki",
        "/docs/",
        "/faq/",
        "/slack",
    }
    fragments: dict[str, set[str]] = {}
    for collection in (
        "articles",
        "podcasts",
        "books",
        "people",
        "events",
        "wiki",
        "courses",
    ):
        path = PROJECTION_ROOT / f"{collection}.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EventDescriptionLinkPolicyError("public route registry is unavailable") from exc
        if not isinstance(records, list):
            raise EventDescriptionLinkPolicyError("public route registry is invalid")
        for record in records:
            if not isinstance(record, dict):
                raise EventDescriptionLinkPolicyError("public route registry is invalid")
            public_path = record.get("public_path")
            if not isinstance(public_path, str):
                raise EventDescriptionLinkPolicyError("public route registry is invalid")
            paths.add(public_path)
            blocks = record.get("blocks", [])
            if not isinstance(blocks, list):
                raise EventDescriptionLinkPolicyError("public route registry is invalid")
            ids = {
                block["id"]
                for block in blocks
                if isinstance(block, dict) and isinstance(block.get("id"), str)
            }
            if ids:
                fragments[public_path] = ids
    return paths, fragments


def _safe_parsed_url(value: str) -> tuple[SplitResult, str]:
    if (
        not value
        or len(value) > 2_048
        or any(character in value for character in ("\x00", "\r", "\n"))
        or value.startswith("//")
    ):
        raise EventDescriptionLinkPolicyError("description URL is unsafe")
    candidate = f"https://datatalks.club{value}" if value.startswith("/") else value
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise EventDescriptionLinkPolicyError("description URL is unsafe") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise EventDescriptionLinkPolicyError("description URL is unsafe")
    return parsed, candidate


def classify_source_url(
    value: str,
    *,
    public_paths: set[str],
    fragments: dict[str, set[str]],
) -> tuple[str, str]:
    parsed, candidate = _safe_parsed_url(value)
    hostname = (parsed.hostname or "").casefold()
    if hostname in {"luma.com", "lu.ma"}:
        return "provider_action_removed", ""
    if hostname == "images.lumacdn.com":
        return "remote_image_removed", ""
    if hostname in FORBIDDEN_JOIN_HOSTS or hostname in REMOVED_REGISTRATION_HOSTS:
        return "registration_action_removed", ""
    if hostname == "docs.google.com" and parsed.path.startswith("/forms/"):
        return "registration_action_removed", ""
    if hostname == "courses.datatalks.club":
        if parsed.path.startswith("/register/"):
            return "registration_action_removed", ""
        return "course_compatibility_missing", ""
    if hostname == "bit.ly":
        return "unreviewed_shortlink_removed", ""
    if hostname == "datatalks.club":
        path = parsed.path or "/"
        if path == "/slack.html":
            return "registration_action_removed", ""
        path = INTERNAL_ALIASES.get(path, path)
        if path not in public_paths:
            return "internal_target_missing", ""
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if query_pairs and all(key.casefold().startswith("utm_") for key, _ in query_pairs):
            query_pairs = []
        elif query_pairs:
            valid_wiki_query = path == "/wiki" and [key for key, _ in query_pairs] == ["q"]
            valid_podcast_query = (
                path == "/podcast"
                and len(query_pairs) == 1
                and query_pairs[0][0] == "page"
                and query_pairs[0][1].isdigit()
                and not query_pairs[0][1].startswith("0")
            )
            if not valid_wiki_query and not valid_podcast_query:
                return "internal_query_invalid", ""
        fragment = parsed.fragment
        if fragment and (
            FRAGMENT.fullmatch(fragment) is None or fragment not in fragments.get(path, set())
        ):
            return "internal_fragment_missing", ""
        final = urlunsplit(("https", "datatalks.club", path, urlencode(query_pairs), fragment))
        return "internal_rewritten", final
    if hostname not in REVIEWED_EXTERNAL_HOSTS:
        raise EventDescriptionLinkPolicyError("description URL has no reviewed decision")
    lowered_path = parsed.path.casefold()
    if any(
        marker in lowered_path
        for marker in ("/join", "/register", "/registration", "/rsvp", "/sign-up", "/signup")
    ):
        raise EventDescriptionLinkPolicyError(
            "description registration URL has no removal decision"
        )
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if query_pairs and all(key.casefold().startswith("utm_") for key, _ in query_pairs):
        candidate = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                parsed.fragment,
            )
        )
    return "external_resource_kept", candidate


def classify_rendered_url(
    value: str,
    *,
    public_paths: set[str],
    fragments: dict[str, set[str]],
) -> str:
    decision, final = classify_source_url(
        value,
        public_paths=public_paths,
        fragments=fragments,
    )
    if decision not in {"internal_rewritten", "external_resource_kept"} or final != value:
        raise EventDescriptionLinkPolicyError("description rendered link is forbidden")
    if value not in REVIEWED_RENDERED_LINKS:
        raise EventDescriptionLinkPolicyError("description rendered link is not reviewed")
    return decision
