"""Normalize GitHub repository links supplied with learner submissions."""

from urllib.parse import urlsplit


def canonical_github_repository_url(url: str | None) -> str | None:
    """Return the browser URL for a GitHub repository, leaving other links alone.

    A repository root has exactly an owner and a repository name. Deeper paths,
    query strings, and fragments can identify specific content, so keep those
    links as submitted.
    """
    if not url:
        return url

    try:
        parsed = urlsplit(url)
        is_github_http = (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname in {"github.com", "www.github.com"}
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return url

    if not is_github_http:
        return url

    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return url

    owner, repository = parts
    repository = repository.removesuffix(".git")
    if not repository:
        return url

    return f"https://github.com/{owner}/{repository}"
