from __future__ import annotations

from courses.services.lesson_content import (
    canonical_github_source_url,
    canonical_module_path,
    canonical_unit_path,
    rewrite_relative_lesson_links,
)


def test_canonical_paths_keep_numeric_source_slugs() -> None:
    module_path = canonical_module_path(
        course_slug="llm-zoomcamp",
        cohort_identifier="2026",
        module_slug="01-agentic-rag",
    )
    assert module_path == "/courses/llm-zoomcamp/2026/modules/01-agentic-rag"
    assert (
        canonical_unit_path(
            course_slug="llm-zoomcamp",
            cohort_identifier="2026",
            module_slug="01-agentic-rag",
            unit_slug="01-intro",
        )
        == "/courses/llm-zoomcamp/2026/modules/01-agentic-rag/01-intro"
    )


def test_canonical_github_source_url_uses_commit_pinned_source_path() -> None:
    assert (
        canonical_github_source_url(
            repository_url="https://github.com/DataTalksClub/llm-zoomcamp.git",
            commit_sha="a" * 40,
            source_path="cohorts/2026/01-agentic-rag/lessons/01-intro.md",
        )
        == "https://github.com/DataTalksClub/llm-zoomcamp/blob/"
        + "a" * 40
        + "/cohorts/2026/01-agentic-rag/lessons/01-intro.md"
    )


def test_rewrites_known_relative_links_but_not_non_lesson_links() -> None:
    current_source_path = "cohorts/2026/01-agentic-rag/lessons/02-environment.md"
    public_urls = {
        "cohorts/2026/01-agentic-rag/lessons/01-intro.md": (
            "/courses/llm-zoomcamp/2026/modules/01-agentic-rag/01-intro"
        ),
        "cohorts/2026/02-vector-search/lessons/01-intro.md": (
            "/courses/llm-zoomcamp/2026/modules/02-vector-search/01-intro"
        ),
    }
    markdown = """[Previous](01-intro.md#setup "Previous lesson")

[Next](../../02-vector-search/lessons/01-intro.md?preview=1)

[Unknown](../README.md)

![Image](../images/course.png)

[External](https://example.com/lesson.md)

```markdown
[Inside a code block](01-intro.md)
```
"""

    rewritten = rewrite_relative_lesson_links(
        markdown,
        current_source_path=current_source_path,
        public_urls_by_source_path=public_urls,
    )

    assert (
        '[Previous](/courses/llm-zoomcamp/2026/modules/01-agentic-rag/01-intro#setup '
        '"Previous lesson")'
        in rewritten
    )
    assert (
        "[Next](/courses/llm-zoomcamp/2026/modules/02-vector-search/01-intro?preview=1)"
        in rewritten
    )
    assert "[Unknown](../README.md)" in rewritten
    assert "![Image](../images/course.png)" in rewritten
    assert "[External](https://example.com/lesson.md)" in rewritten
    assert "[Inside a code block](01-intro.md)" in rewritten
