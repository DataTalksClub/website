from urllib.parse import urljoin, urlparse

import requests
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from core.security import UnsafeInputError, validate_url

FAQ_CONTRIBUTION_FIELD = "faq_contribution_url"
FAQ_URL_FORMAT_ERROR = "FAQ contribution must be a valid HTTPS GitHub issue or pull request URL."
FAQ_URL_REPOSITORY_ERROR = (
    "FAQ contribution must be a DataTalksClub/faq issue "
    "or pull request URL, for example "
    "https://github.com/DataTalksClub/faq/issues/281."
)
FAQ_URL_VALIDATOR = URLValidator(schemes=["https"])


def clean_faq_contribution_url(url: str | None) -> str:
    url = (url or "").strip()
    if not url:
        return ""

    _validate_faq_contribution_url_format(url)
    if not _is_faq_issue_or_pull_request(url):
        raise _faq_contribution_url_error(FAQ_URL_REPOSITORY_ERROR)

    return url


def _validate_faq_contribution_url_format(url: str) -> None:
    try:
        FAQ_URL_VALIDATOR(url)
        # The host/path allowlist below is the product rule; this shared guard
        # additionally rejects credentials, fragments, private literals, and
        # control-character tricks before the value is persisted.
        validate_url(url, reject_private=True, resolve_private=False)
    except ValidationError:
        raise _faq_contribution_url_error(FAQ_URL_FORMAT_ERROR) from None
    except UnsafeInputError:
        raise _faq_contribution_url_error(FAQ_URL_FORMAT_ERROR) from None


def _is_faq_issue_or_pull_request(url: str) -> bool:
    parsed = urlparse(url)
    path_parts = _url_path_parts(parsed.path)
    return _is_faq_github_host(parsed) and _is_faq_issue_or_pull_path(path_parts)


def _url_path_parts(path: str) -> list[str]:
    path_parts = []
    raw_parts = path.split("/")
    for part in raw_parts:
        if part:
            path_parts.append(part)
    return path_parts


def _is_faq_github_host(parsed_url) -> bool:
    return parsed_url.hostname == "github.com" and parsed_url.port in {None, 443}


def _is_faq_issue_or_pull_path(path_parts: list[str]) -> bool:
    return (
        len(path_parts) == 4
        and path_parts[0].lower() == "datatalksclub"
        and path_parts[1].lower() == "faq"
        and path_parts[2].lower() in {"issues", "pull"}
        and path_parts[3].isdigit()
    )


def _faq_contribution_url_error(message: str) -> ValidationError:
    return ValidationError({FAQ_CONTRIBUTION_FIELD: message})


def get_error_message(status_code, url):
    if status_code != 404:
        return (
            f"The submitted link {url} does not "
            + "return a 200 status code. Status code: "
            + f"{status_code}."
        )

    # 404 status code
    if "github" in url.lower():
        return (
            f"The submitted GitHub link {url} does not "
            + "exist. Make sure the repository is public."
        )

    return f"The submitted link {url} does not exist."


# Cap how long we wait on the remote server so a slow or hanging URL
# cannot tie up a worker process indefinitely.
URL_VALIDATION_TIMEOUT = 3
URL_VALIDATION_ERRORS = (
    requests.RequestException,
    UnicodeError,
    ValueError,
    TypeError,
    AttributeError,
)


SAFE_URL_ERROR = "The submitted URL is not a safe HTTPS destination."


def _validate_submission_url(url):
    parsed = urlparse(url)
    if parsed.scheme.casefold() == "git":
        # Git links are identifiers only.  They are never fetched by this
        # validator; credentials and private/metadata hosts remain rejected.
        return validate_url(
            url,
            allow_git=True,
            reject_private=True,
            # Git links are identifiers and are never fetched; avoid a DNS
            # side effect for this non-network path.
            resolve_private=False,
        )
    # The synchronous courses validator never performs DNS or HTTP work.  A
    # provider-owned async transport may call ``validate_outbound_url`` before
    # its bounded fetch; this request-path guard rejects known private/metadata
    # literals without allowing user input to trigger network I/O.
    return validate_url(url, reject_private=True, resolve_private=False)


def _should_retry_with_get(status_code):
    return status_code in [403, 405, 501]


def _validated_url_response(url, get_method):
    response = get_method(
        url,
        timeout=URL_VALIDATION_TIMEOUT,
        allow_redirects=False,
    )
    if _should_retry_with_get(response.status_code):
        # Preserve the legacy HEAD->GET fallback for explicitly injected
        # transports, while still preventing that transport from following a
        # redirect automatically.
        return requests.get(
            url,
            timeout=URL_VALIDATION_TIMEOUT,
            allow_redirects=False,
        )

    return response


def _reject_redirect(response, url):
    if not 300 <= response.status_code < 400:
        return
    location = (getattr(response, "headers", {}) or {}).get("Location")
    if location:
        # Validate a redirect target even though redirects are never followed.
        # This prevents a future transport change from turning this path into
        # an SSRF primitive and rejects DNS/private metadata destinations.
        _validate_submission_url(urljoin(url, location))
    raise ValidationError("The submitted URL returned a redirect.")


def _raise_url_status_error(status_code, url, code, params):
    error_message = get_error_message(status_code, url)
    raise ValidationError(error_message, code=code, params=params)


def validate_url_200(url, get_method=None, code=None, params=None):
    try:
        safe_url = _validate_submission_url(url)
        # Production callers omit ``get_method``: URL validation is bounded to
        # syntax/SSRF checks and performs no synchronous user-controlled HTTP.
        # The injected method is retained only for legacy tests and controlled
        # callers that own their transport.
        if get_method is None:
            return

        response = _validated_url_response(safe_url, get_method)
        _reject_redirect(response, safe_url)
        status_code = response.status_code

        if status_code == 200:
            return

        _raise_url_status_error(status_code, url, code, params)
    except ValidationError:
        raise
    except UnsafeInputError:
        raise ValidationError(SAFE_URL_ERROR, code=code, params=params) from None
    except URL_VALIDATION_ERRORS:
        # Not just requests.exceptions.RequestException: malformed-but-
        # valid-looking URLs can raise UnicodeError / LocationParseError,
        # which would otherwise escape as an uncaught 500.
        raise ValidationError(
            "An error occurred while trying to validate the URL.",
            code=code,
            params=params,
        ) from None


class Status200UrlValidator(URLValidator):
    def __call__(self, value):
        super().__call__(value)
        validate_url_200(value, code=self.code, params={"value": value})
