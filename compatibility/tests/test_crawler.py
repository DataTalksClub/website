from __future__ import annotations

import hashlib
import http.client
import json
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import TracebackType

import pytest

import compatibility.crawler as crawler_module
from compatibility.crawler import (
    CRAWLER_USER_AGENT,
    AllowlistRule,
    AuthorizedTarget,
    BoundedHttpTransport,
    CrawlBounds,
    CrawlCheckpoint,
    CrawlCheckpointError,
    CrawlPolicy,
    CrawlPolicyError,
    CrawlTransportError,
    HttpResponse,
    LocalTreeSource,
    crawl_http,
    inventory_local_tree,
    load_checkpoint,
    new_checkpoint,
    save_checkpoint,
)
from compatibility.models import ObservationOrigin, RedirectHop

PUBLIC_IP = "93.184.216.34"
FIXTURES = Path(__file__).parent / "fixtures" / "crawler"
REVISION = hashlib.sha1(b"fixture revision", usedforsecurity=False).hexdigest()


def resolver(_host: str, _port: int) -> tuple[str, ...]:
    return (PUBLIC_IP,)


def policy(**bound_overrides: object) -> CrawlPolicy:
    defaults: dict[str, object] = {
        "max_urls": 20,
        "max_responses": 30,
        "max_redirects": 3,
        "max_retries": 1,
        "max_url_length": 300,
        "max_response_bytes": 2_000,
        "max_total_bytes": 10_000,
        "request_timeout_seconds": 2.0,
        "max_run_seconds": 30.0,
    }
    defaults.update(bound_overrides)
    return CrawlPolicy(
        rules=(
            AllowlistRule(
                scheme="https",
                host="legacy.example.test",
                port=443,
                path_prefixes=("/",),
                allowed_query_keys=("q", "type"),
            ),
        ),
        bounds=CrawlBounds(**defaults),  # type: ignore[arg-type]
        robots_required=False,
    )


class FakeRemoteResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.status = status
        self._body = body
        self._headers = tuple(headers)

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def getheader(self, name: str) -> str | None:
        for key, value in self._headers:
            if key.lower() == name.lower():
                return value
        return None

    def read(self, amount: int) -> bytes:
        return self._body[:amount]


class FakeConnection:
    def __init__(self, response: FakeRemoteResponse, requests: list[dict[str, object]]) -> None:
        self.response = response
        self.requests = requests

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def request(
        self, method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> None:
        self.requests.append({"method": method, "url": url, "body": body, "headers": dict(headers)})

    def getresponse(self) -> FakeRemoteResponse:
        return self.response


class ResponseFactory:
    def __init__(self, responses: Iterable[FakeRemoteResponse | BaseException]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []
        self.targets: list[tuple[AuthorizedTarget, str, float]] = []

    def __call__(self, target: AuthorizedTarget, address: str, timeout: float) -> FakeConnection:
        self.targets.append((target, address, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeConnection(response, self.requests)


@pytest.mark.parametrize(
    "url,code",
    [
        ("http://legacy.example.test/", "url_not_allowlisted"),
        ("https://other.example.test/", "url_not_allowlisted"),
        ("https://legacy.example.test:444/", "url_not_allowlisted"),
        ("https://user:password@legacy.example.test/", "url_contains_credentials"),
        ("https://legacy.example.test/?token=secret", "query_key_not_allowlisted"),
        ("https://legacy.example.test/?q=person%40example.com", "query_contains_private_data"),
        ("https://legacy.example.test/%2e%2e/private", "url_contains_parent_segment"),
        (
            "https://legacy.example.test/safe/%2525252e%2525252e/admin",
            "url_contains_parent_segment",
        ),
        ("https://legacy.example.test/path%5cprivate", "url_contains_backslash"),
        ("https://legacy.example.test/path with space", "url_contains_raw_whitespace"),
    ],
)
def test_policy_rejects_every_unallowlisted_url_without_echoing_it(url: str, code: str) -> None:
    with pytest.raises(CrawlPolicyError) as raised:
        policy().authorize(url, resolver)

    assert str(raised.value) == code
    assert url not in str(raised.value)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "0.0.0.0",
    ],
)
def test_policy_rejects_non_public_dns_results(address: str) -> None:
    with pytest.raises(CrawlPolicyError, match="^dns_returned_non_public_address$"):
        policy().authorize("https://legacy.example.test/", lambda _host, _port: (address,))


def test_policy_rejects_mixed_public_and_private_dns_results() -> None:
    with pytest.raises(CrawlPolicyError, match="^dns_returned_non_public_address$"):
        policy().authorize(
            "https://legacy.example.test/",
            lambda _host, _port: (PUBLIC_IP, "127.0.0.1"),
        )


def test_policy_bounds_validated_dns_address_count() -> None:
    addresses = tuple(f"93.184.216.{index}" for index in range(1, 10))
    with pytest.raises(CrawlPolicyError, match="^dns_address_count_limit_exceeded$"):
        policy(max_addresses=8).authorize(
            "https://legacy.example.test/", lambda _host, _port: addresses
        )


def test_transport_pins_validated_address_and_static_headers() -> None:
    factory = ResponseFactory(
        [
            FakeRemoteResponse(
                200,
                b"ok",
                (
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Set-Cookie", "private=value"),
                ),
            )
        ]
    )
    response = BoundedHttpTransport(policy(), resolver=resolver, connection_factory=factory).fetch(
        "https://legacy.example.test/"
    )

    assert response.status == 200
    assert response.headers == (("content-type", "text/plain; charset=utf-8"),)
    assert factory.targets[0][1] == PUBLIC_IP
    assert factory.requests == [
        {
            "method": "GET",
            "url": "/",
            "body": None,
            "headers": {
                "Accept": (
                    "text/html,application/xhtml+xml,application/json,application/xml,"
                    "text/xml;q=0.9,*/*;q=0.1"
                ),
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Host": "legacy.example.test",
                "User-Agent": CRAWLER_USER_AGENT,
            },
        }
    ]


def test_redirect_target_is_reauthorized_before_second_connection() -> None:
    factory = ResponseFactory(
        [FakeRemoteResponse(302, headers=(("Location", "http://127.0.0.1/private"),))]
    )
    transport = BoundedHttpTransport(policy(), resolver=resolver, connection_factory=factory)

    with pytest.raises(CrawlPolicyError, match="^url_not_allowlisted$"):
        transport.fetch("https://legacy.example.test/")

    assert len(factory.targets) == 1


def test_redirect_chain_and_loop_are_bounded() -> None:
    chain_factory = ResponseFactory(
        [
            FakeRemoteResponse(301, headers=(("Location", "/next#Exact"),)),
            FakeRemoteResponse(200, b"done", (("Content-Type", "text/plain"),)),
        ]
    )
    response = BoundedHttpTransport(
        policy(), resolver=resolver, connection_factory=chain_factory
    ).fetch("https://legacy.example.test/")
    assert response.final_url == "https://legacy.example.test/next"
    assert response.redirect_chain == (
        RedirectHop(status=301, url="https://legacy.example.test/next#Exact"),
    )

    loop_factory = ResponseFactory(
        [
            FakeRemoteResponse(302, headers=(("Location", "/two"),)),
            FakeRemoteResponse(302, headers=(("Location", "/"),)),
        ]
    )
    with pytest.raises(CrawlTransportError, match="^redirect_loop$"):
        BoundedHttpTransport(policy(), resolver=resolver, connection_factory=loop_factory).fetch(
            "https://legacy.example.test/"
        )


def test_redirect_destination_must_pass_robots_and_sensitive_fragment_is_redacted() -> None:
    denied_factory = ResponseFactory([FakeRemoteResponse(302, headers=(("Location", "/private"),))])

    def verify_robots(url: str) -> None:
        if url.endswith("/private"):
            raise CrawlPolicyError("robots_policy_disallows_redirect")

    with pytest.raises(CrawlPolicyError, match="^robots_policy_disallows_redirect$"):
        BoundedHttpTransport(
            policy(),
            resolver=resolver,
            connection_factory=denied_factory,
            robots_verifier=verify_robots,
        ).fetch("https://legacy.example.test/")
    assert len(denied_factory.requests) == 1

    redacted_factory = ResponseFactory(
        [
            FakeRemoteResponse(
                301,
                headers=(("Location", "/next#person@example.com"),),
            ),
            FakeRemoteResponse(200, b"done"),
        ]
    )
    response = BoundedHttpTransport(
        policy(), resolver=resolver, connection_factory=redacted_factory
    ).fetch("https://legacy.example.test/")
    assert response.redirect_chain[0].url.startswith(
        "https://legacy.example.test/next#redacted-sha256-"
    )
    assert "person@example.com" not in response.redirect_chain[0].url


def test_redirect_allows_only_safe_local_next_query_value() -> None:
    selected_policy = CrawlPolicy(
        rules=(
            AllowlistRule(
                "https",
                "legacy.example.test",
                443,
                ("/",),
                ("next",),
            ),
        ),
        bounds=policy().bounds,
        robots_required=False,
    )
    factory = ResponseFactory(
        [
            FakeRemoteResponse(
                302,
                headers=(("Location", "/accounts/login/?next=/cadmin/"),),
            ),
            FakeRemoteResponse(200, b"login"),
        ]
    )
    response = BoundedHttpTransport(
        selected_policy,
        resolver=resolver,
        connection_factory=factory,
    ).fetch("https://legacy.example.test/cadmin/")

    assert response.final_url == "https://legacy.example.test/accounts/login/?next=/cadmin/"
    with pytest.raises(CrawlPolicyError, match="^query_next_value_not_safe$"):
        selected_policy.authorize(
            "https://legacy.example.test/accounts/login/?next=https://evil.example/",
            resolver,
        )


def test_redirect_sensitive_query_fails_before_second_request() -> None:
    factory = ResponseFactory(
        [
            FakeRemoteResponse(
                302,
                headers=(("Location", "/?q=person@example.com"),),
            )
        ]
    )
    with pytest.raises(CrawlPolicyError, match="^query_contains_private_data$"):
        BoundedHttpTransport(policy(), resolver=resolver, connection_factory=factory).fetch(
            "https://legacy.example.test/"
        )

    assert len(factory.requests) == 1


@pytest.mark.parametrize(
    "location,encoded_path",
    [("/next path", "/next%20path"), ("/café", "/caf%C3%A9")],
)
def test_redirect_normalizes_path_before_authorization_and_fetch(
    location: str, encoded_path: str
) -> None:
    factory = ResponseFactory(
        [
            FakeRemoteResponse(302, headers=(("Location", location),)),
            FakeRemoteResponse(200, b"done"),
        ]
    )
    response = BoundedHttpTransport(policy(), resolver=resolver, connection_factory=factory).fetch(
        "https://legacy.example.test/"
    )

    assert response.final_url == f"https://legacy.example.test{encoded_path}"
    assert response.redirect_chain[0].url == response.final_url
    assert factory.requests[1]["url"] == encoded_path


def test_retry_count_and_response_limit_are_deterministic() -> None:
    factory = ResponseFactory(
        [
            FakeRemoteResponse(503),
            FakeRemoteResponse(200, b"ok", (("Content-Type", "text/plain"),)),
        ]
    )
    response = BoundedHttpTransport(
        policy(max_retries=1), resolver=resolver, connection_factory=factory
    ).fetch("https://legacy.example.test/")
    assert response.response_count == 2
    assert len(factory.requests) == 2


def test_caller_budgets_cannot_exceed_policy_hard_limits() -> None:
    response_factory = ResponseFactory([FakeRemoteResponse(503)])
    with pytest.raises(CrawlTransportError, match="^response_count_limit_exceeded$"):
        BoundedHttpTransport(
            policy(max_responses=1, max_retries=1),
            resolver=resolver,
            connection_factory=response_factory,
        ).fetch("https://legacy.example.test/", max_responses=100)
    assert len(response_factory.requests) == 1

    byte_factory = ResponseFactory([FakeRemoteResponse(200, b"abc")])
    transport = BoundedHttpTransport(
        policy(max_response_bytes=10, max_total_bytes=2),
        resolver=resolver,
        connection_factory=byte_factory,
    )
    with pytest.raises(CrawlTransportError, match="^total_response_size_limit_exceeded$"):
        transport.fetch("https://legacy.example.test/", max_bytes=100)
    assert transport.last_transfer_bytes == 3

    with pytest.raises(CrawlTransportError, match="^invalid_byte_budget$"):
        transport.fetch("https://legacy.example.test/", max_bytes=True)
    with pytest.raises(CrawlTransportError, match="^invalid_response_budget$"):
        transport.fetch("https://legacy.example.test/", max_responses=0)


def test_transient_connection_failure_retries_then_succeeds() -> None:
    factory = ResponseFactory(
        [OSError("transient connection failure"), FakeRemoteResponse(200, b"ok")]
    )
    sleeps: list[float] = []
    transport = BoundedHttpTransport(
        policy(max_retries=1, retry_backoff_seconds=0.25),
        resolver=resolver,
        connection_factory=factory,
        sleeper=sleeps.append,
    )

    response = transport.fetch("https://legacy.example.test/")

    assert response.status == 200
    assert response.response_count == 2
    assert transport.last_response_count == 2
    assert sleeps == [0.25]


def test_each_validated_address_uses_the_remaining_absolute_deadline() -> None:
    class Clock:
        value = 10.0

        def monotonic(self) -> float:
            return self.value

    clock = Clock()
    timeouts: list[float] = []

    def failing_factory(_target: AuthorizedTarget, _address: str, timeout: float) -> FakeConnection:
        timeouts.append(timeout)
        clock.value += 0.6
        raise OSError("connection failed")

    transport = BoundedHttpTransport(
        policy(max_retries=0, request_timeout_seconds=5.0),
        resolver=lambda _host, _port: ("93.184.216.1", "93.184.216.2"),
        connection_factory=failing_factory,
        monotonic=clock.monotonic,
    )
    with pytest.raises(CrawlTransportError, match="^all_validated_addresses_failed$"):
        transport.fetch("https://legacy.example.test/", deadline=11.0)

    assert timeouts == pytest.approx([1.0, 0.4])


def test_incomplete_response_accounts_partial_bytes() -> None:
    class IncompleteResponse(FakeRemoteResponse):
        def read(self, _amount: int) -> bytes:
            raise http.client.IncompleteRead(b"partial")

    factory = ResponseFactory([IncompleteResponse(200)])
    transport = BoundedHttpTransport(
        policy(max_retries=0), resolver=resolver, connection_factory=factory
    )

    with pytest.raises(CrawlTransportError, match="^incomplete_response$"):
        transport.fetch("https://legacy.example.test/")
    assert transport.last_transfer_bytes == len(b"partial")


def test_response_size_is_bounded_using_header_and_stream_limit() -> None:
    declared = ResponseFactory([FakeRemoteResponse(200, b"ignored", (("Content-Length", "2001"),))])
    with pytest.raises(CrawlTransportError, match="^response_too_large$"):
        BoundedHttpTransport(policy(), resolver=resolver, connection_factory=declared).fetch(
            "https://legacy.example.test/"
        )

    streamed = ResponseFactory([FakeRemoteResponse(200, b"x" * 2001)])
    transport = BoundedHttpTransport(policy(), resolver=resolver, connection_factory=streamed)
    with pytest.raises(CrawlTransportError, match="^response_too_large$"):
        transport.fetch("https://legacy.example.test/")
    assert transport.last_transfer_bytes == 2_001


def test_oversized_failures_count_toward_the_aggregate_byte_limit() -> None:
    factory = ResponseFactory(
        [FakeRemoteResponse(200, b"x" * 5), FakeRemoteResponse(200, b"y" * 5)]
    )
    transport = BoundedHttpTransport(
        policy(max_response_bytes=4, max_total_bytes=6),
        resolver=resolver,
        connection_factory=factory,
    )
    transitions: list[tuple[int, str]] = []

    with pytest.raises(CrawlTransportError, match="^total_response_size_limit_exceeded$"):
        crawl_http(
            seeds=[
                "https://legacy.example.test/first",
                "https://legacy.example.test/second",
            ],
            policy=policy(max_response_bytes=4, max_total_bytes=6),
            transport=transport,
            observer=lambda capture, checkpoint: transitions.append(
                (checkpoint.total_bytes, capture.error_code)
            ),
        )

    assert transitions == [(5, "response_too_large")]
    assert transport.last_transfer_bytes == 2
    assert len(factory.requests) == 2


def test_exact_budget_exhaustion_is_terminal_and_transport_independent() -> None:
    selected_policy = policy(max_response_bytes=10, max_total_bytes=2)
    factory = ResponseFactory([FakeRemoteResponse(200, b"ok")])
    reused = BoundedHttpTransport(
        selected_policy,
        resolver=resolver,
        connection_factory=factory,
    )
    seeds = (
        "https://legacy.example.test/first",
        "https://legacy.example.test/second",
    )
    first = crawl_http(
        seeds=seeds,
        policy=selected_policy,
        max_new_captures=1,
        transport=reused,
    )
    assert first.checkpoint.total_bytes == 2

    for transport in (
        reused,
        BoundedHttpTransport(
            selected_policy,
            resolver=resolver,
            connection_factory=ResponseFactory([]),
        ),
    ):
        with pytest.raises(CrawlTransportError, match="^crawl_budget_exhausted$"):
            crawl_http(
                seeds=seeds,
                policy=selected_policy,
                checkpoint=first.checkpoint,
                completed_captures=first.captures,
                transport=transport,
            )
    assert len(factory.requests) == 1


def test_checkpoint_is_canonical_strict_and_atomic() -> None:
    state = new_checkpoint(
        policy(),
        ["https://legacy.example.test/docs/", "https://legacy.example.test/"],
    )
    path = Path.cwd() / ".tmp/tests" / f"crawl-{uuid.uuid4().hex}.json"
    save_checkpoint(path, state)

    assert load_checkpoint(path) == state
    assert path.read_text(encoding="utf-8") == state.dumps()
    assert not tuple(path.parent.glob(f".{path.name}.*.pending"))
    assert list(json.loads(state.dumps())) == [
        "completed_urls",
        "pending_urls",
        "policy_sha256",
        "response_count",
        "schema_version",
        "seeds_sha256",
        "total_bytes",
    ]

    invalid = json.loads(state.dumps())
    invalid["unexpected"] = True
    with pytest.raises(CrawlCheckpointError, match="^invalid_checkpoint_shape$"):
        CrawlCheckpoint.loads(json.dumps(invalid))
    path.unlink()


@pytest.mark.parametrize(
    ("field_name", "error_code"),
    [
        ("schema_version", "unsupported_checkpoint_schema"),
        ("response_count", "invalid_checkpoint_counter"),
        ("total_bytes", "invalid_checkpoint_counter"),
    ],
)
def test_direct_checkpoint_constructor_rejects_boolean_integers(
    field_name: str,
    error_code: str,
) -> None:
    values: dict[str, object] = {
        "policy_sha256": "a" * 64,
        "seeds_sha256": "b" * 64,
        "pending_urls": (),
        "completed_urls": (),
        field_name: True,
    }
    with pytest.raises(CrawlCheckpointError, match=f"^{error_code}$"):
        CrawlCheckpoint(**values)  # type: ignore[arg-type]


def test_checkpoint_rejects_policy_and_capture_drift() -> None:
    state = new_checkpoint(policy(), ["https://legacy.example.test/"])
    with pytest.raises(CrawlCheckpointError, match="^checkpoint_policy_mismatch$"):
        crawl_http(
            seeds=["https://legacy.example.test/"],
            policy=policy(max_urls=19),
            checkpoint=state,
        )
    drifted = CrawlCheckpoint(
        policy_sha256=state.policy_sha256,
        seeds_sha256=state.seeds_sha256,
        pending_urls=(),
        completed_urls=("https://legacy.example.test/",),
    )
    with pytest.raises(CrawlCheckpointError, match="^checkpoint_capture_set_mismatch$"):
        crawl_http(
            seeds=["https://legacy.example.test/"],
            policy=policy(),
            checkpoint=drifted,
        )


def test_checkpoint_rejects_crawler_semantics_version_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_policy = policy()
    state = new_checkpoint(selected_policy, ["https://legacy.example.test/"])

    monkeypatch.setattr(
        crawler_module,
        "CRAWLER_TOOL_VERSION",
        "dtc-legacy-manifest-crawler/future-extraction-semantics",
    )

    assert selected_policy.fingerprint != state.policy_sha256
    with pytest.raises(CrawlCheckpointError, match="^checkpoint_policy_mismatch$"):
        crawl_http(
            seeds=["https://legacy.example.test/"],
            policy=selected_policy,
            checkpoint=state,
        )


def test_checkpoint_io_rejects_outside_paths_and_symlinks() -> None:
    state = new_checkpoint(policy(), ["https://legacy.example.test/"])
    outside = Path.cwd() / f"checkpoint-{uuid.uuid4().hex}.json"
    with pytest.raises(CrawlCheckpointError, match="^checkpoint_must_be_below_project_tmp$"):
        save_checkpoint(outside, state)
    assert not outside.exists()

    directory = Path.cwd() / ".tmp/tests"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"target-{uuid.uuid4().hex}.json"
    link = directory / f"link-{uuid.uuid4().hex}.json"
    target.write_text(state.dumps(), encoding="utf-8")
    link.symlink_to(target)
    try:
        with pytest.raises(CrawlCheckpointError, match="^checkpoint_symlink_forbidden$"):
            save_checkpoint(link, state)
        with pytest.raises(CrawlCheckpointError, match="^checkpoint_read_failed$"):
            load_checkpoint(link)
    finally:
        link.unlink()
        target.unlink()


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"max_urls": True}, "crawl_integer_bound_must_be_integer"),
        ({"max_run_seconds": float("nan")}, "crawl_time_bound_must_be_finite_number"),
        ({"request_timeout_seconds": float("inf")}, "crawl_time_bound_must_be_finite_number"),
        ({"request_interval_seconds": -0.1}, "negative_delay_bound"),
    ],
)
def test_bounds_reject_boolean_nonfinite_and_negative_values(
    kwargs: dict[str, object], code: str
) -> None:
    with pytest.raises(CrawlPolicyError, match=f"^{code}$"):
        policy(**kwargs)


def test_transport_paces_requests_and_honors_bounded_retry_after() -> None:
    class Clock:
        value = 0.0

        def monotonic(self) -> float:
            return self.value

        def sleep(self, duration: float) -> None:
            sleeps.append(duration)
            self.value += duration

    clock = Clock()
    sleeps: list[float] = []
    factory = ResponseFactory(
        [
            FakeRemoteResponse(503, headers=(("Retry-After", "2"),)),
            FakeRemoteResponse(200, b"ok", (("Content-Type", "text/plain"),)),
            FakeRemoteResponse(200, b"ok", (("Content-Type", "text/plain"),)),
        ]
    )
    transport = BoundedHttpTransport(
        policy(
            request_interval_seconds=0.5,
            retry_backoff_seconds=0.25,
            max_retry_after_seconds=1.0,
        ),
        resolver=resolver,
        connection_factory=factory,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )
    transport.fetch("https://legacy.example.test/")
    transport.fetch("https://legacy.example.test/next")
    assert sleeps == [1.0, 0.5]


def test_crawl_records_safe_failure_row_and_continues() -> None:
    class FailingTransport:
        last_response_count = 1
        last_transfer_bytes = 0

        def fetch(self, *_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
            raise CrawlTransportError("redirect_loop")

    run = crawl_http(
        seeds=["https://legacy.example.test/"],
        policy=policy(),
        transport=FailingTransport(),  # type: ignore[arg-type]
    )
    assert run.complete is True
    assert run.captures[0].status == 0
    assert run.captures[0].error_code == "redirect_loop"
    assert run.checkpoint.response_count == 1


def test_invalid_utf8_is_a_per_url_failure_and_crawl_continues() -> None:
    bodies = {
        "https://legacy.example.test/bad": b"\xff",
        "https://legacy.example.test/good": b"valid text",
    }

    class MappingTransport:
        def fetch(self, url: str, **_kwargs: object) -> HttpResponse:
            body = bodies[url]
            return HttpResponse(
                requested_url=url,
                final_url=url,
                status=200,
                headers=(("content-type", "text/plain"),),
                body=body,
                redirect_chain=(),
                response_count=1,
                transfer_bytes=len(body),
            )

    run = crawl_http(
        seeds=tuple(bodies),
        policy=policy(),
        transport=MappingTransport(),  # type: ignore[arg-type]
    )

    assert run.complete is True
    assert [(capture.status, capture.error_code) for capture in run.captures] == [
        (0, "text_response_is_not_utf8"),
        (200, ""),
    ]
    assert run.checkpoint.response_count == 2
    assert run.checkpoint.total_bytes == sum(map(len, bodies.values()))


def test_production_crawl_requires_explicit_robots_verification() -> None:
    required = CrawlPolicy(rules=policy().rules, bounds=policy().bounds, robots_required=True)
    with pytest.raises(CrawlPolicyError, match="^production_robots_verification_required$"):
        crawl_http(seeds=["https://legacy.example.test/"], policy=required)


def test_interrupted_crawl_resumes_in_same_order_as_one_pass() -> None:
    bodies = {
        "https://legacy.example.test/": b'<a href="/b">B</a><a href="/a">A</a>',
        "https://legacy.example.test/a": b"<h1>A</h1>",
        "https://legacy.example.test/b": b"<h1>B</h1>",
    }

    class MappingTransport:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def fetch(
            self,
            url: str,
            *,
            deadline: float | None = None,
            max_responses: int | None = None,
            max_bytes: int | None = None,
        ):  # type: ignore[no-untyped-def]
            from compatibility.crawler import HttpResponse

            self.requests.append(url)
            return HttpResponse(
                requested_url=url,
                final_url=url,
                status=200,
                headers=(("content-type", "text/html"),),
                body=bodies[url],
                redirect_chain=(),
                response_count=1,
                transfer_bytes=len(bodies[url]),
            )

    first_transport = MappingTransport()
    first = crawl_http(
        seeds=["https://legacy.example.test/"],
        policy=policy(),
        max_new_captures=1,
        transport=first_transport,  # type: ignore[arg-type]
    )
    assert first.complete is False
    assert first_transport.requests == ["https://legacy.example.test/"]

    resumed_transport = MappingTransport()
    resumed = crawl_http(
        seeds=["https://legacy.example.test/"],
        policy=policy(),
        checkpoint=first.checkpoint,
        completed_captures=first.captures,
        transport=resumed_transport,  # type: ignore[arg-type]
    )
    one_pass_transport = MappingTransport()
    one_pass = crawl_http(
        seeds=["https://legacy.example.test/"],
        policy=policy(),
        transport=one_pass_transport,  # type: ignore[arg-type]
    )

    assert resumed.complete is True
    assert first_transport.requests + resumed_transport.requests == one_pass_transport.requests
    assert [capture.requested_url for capture in first.captures + resumed.captures] == [
        capture.requested_url for capture in one_pass.captures
    ]
    assert resumed.checkpoint == one_pass.checkpoint


def test_discovery_retains_but_never_fetches_redacted_query_evidence() -> None:
    class SingleResponseTransport:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def fetch(self, url: str, **_kwargs: object) -> HttpResponse:
            self.requested.append(url)
            if len(self.requested) > 1:
                raise AssertionError("synthetic redacted URL must not be fetched")
            body = b'<a href="/?q=person@example.com">private query</a>'
            return HttpResponse(
                requested_url=url,
                final_url=url,
                status=200,
                headers=(("content-type", "text/html"),),
                body=body,
                redirect_chain=(),
                response_count=1,
                transfer_bytes=len(body),
            )

    transport = SingleResponseTransport()
    run = crawl_http(
        seeds=["https://legacy.example.test/"],
        policy=policy(),
        transport=transport,  # type: ignore[arg-type]
    )

    assert transport.requested == ["https://legacy.example.test/"]
    assert run.complete is True
    assert "redacted-sha256-" in run.captures[0].metadata.references[0].url


@pytest.mark.parametrize("deadline", [float("inf"), float("nan"), True])
def test_caller_deadline_must_be_finite_number(deadline: float) -> None:
    with pytest.raises(CrawlTransportError, match="^invalid_crawl_deadline$"):
        crawl_http(
            seeds=["https://legacy.example.test/"],
            policy=policy(),
            deadline=deadline,
        )


def test_chunks_share_one_explicit_absolute_deadline() -> None:
    class Clock:
        value = 0.0

        def monotonic(self) -> float:
            return self.value

    clock = Clock()

    class AdvancingTransport:
        def fetch(self, url: str, **_kwargs: object) -> HttpResponse:
            clock.value += 0.6
            return HttpResponse(
                requested_url=url,
                final_url=url,
                status=200,
                headers=(),
                body=b"",
                redirect_chain=(),
                response_count=1,
                transfer_bytes=0,
            )

    seeds = tuple(f"https://legacy.example.test/{name}" for name in ("a", "b", "c"))
    first = crawl_http(
        seeds=seeds,
        policy=policy(),
        max_new_captures=1,
        transport=AdvancingTransport(),  # type: ignore[arg-type]
        monotonic=clock.monotonic,
        deadline=1.0,
    )
    with pytest.raises(CrawlTransportError, match="^crawl_deadline_exceeded$"):
        crawl_http(
            seeds=seeds,
            policy=policy(),
            checkpoint=first.checkpoint,
            completed_captures=first.captures,
            transport=AdvancingTransport(),  # type: ignore[arg-type]
            monotonic=clock.monotonic,
            deadline=1.0,
        )


def test_local_inventory_is_sorted_mapped_and_does_not_follow_symlinks() -> None:
    captures = inventory_local_tree(
        LocalTreeSource(
            root=FIXTURES,
            public_base_url="https://legacy.example.test/",
            repository="https://github.com/DataTalksClub/fixture.git",
            revision=REVISION,
        ),
        policy=policy(max_response_bytes=10_000, max_total_bytes=20_000),
    )
    assert [capture.requested_url for capture in captures] == [
        "https://legacy.example.test/assets/logo.svg",
        "https://legacy.example.test/docs/",
        "https://legacy.example.test/",
        "https://legacy.example.test/server.py",
    ]
    assert [capture.source_path for capture in captures] == [
        "assets/logo.svg",
        "docs/index.html",
        "index.html",
        "server.py",
    ]
    assert all(capture.origin is ObservationOrigin.SOURCE for capture in captures)

    unsafe = Path.cwd() / ".tmp/tests" / f"tree-{uuid.uuid4().hex}"
    unsafe.mkdir(parents=True)
    source_file = unsafe / "index.html"
    symlink = unsafe / "escape"
    source_file.write_text("safe", encoding="utf-8")
    symlink.symlink_to(FIXTURES / "index.html")
    try:
        with pytest.raises(CrawlPolicyError, match="^source_tree_contains_symlink$"):
            inventory_local_tree(
                LocalTreeSource(
                    root=unsafe,
                    public_base_url="https://legacy.example.test/",
                    repository="https://github.com/DataTalksClub/fixture.git",
                    revision=REVISION,
                ),
                policy=policy(),
            )
    finally:
        symlink.unlink()
        source_file.unlink()
        unsafe.rmdir()


def test_source_content_types_ignore_host_mime_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        crawler_module.mimetypes,
        "guess_type",
        lambda _path: ("application/x-host-specific", None),
    )

    assert crawler_module._source_content_type("index.html") == "text/html"
    assert crawler_module._source_content_type("sitemap.xml") == "application/xml"
    assert crawler_module._source_content_type("unknown.host-extension") == (
        "application/octet-stream"
    )


def test_local_inventory_percent_encodes_exact_public_path_identity() -> None:
    root = Path.cwd() / ".tmp/tests" / f"encoded-tree-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    relative_to_public = {
        "nested/path file.txt": "nested/path%20file.txt",
        "person(name).html": "person%28name%29.html",
        "data-&-ai.txt": "data-%26-ai.txt",
        "already%2fescaped.txt": "already%252fescaped.txt",
        "CaseSensitive.HTML": "CaseSensitive.HTML",
        "café.html": "caf%C3%A9.html",
    }
    try:
        for relative in relative_to_public:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")

        captures = inventory_local_tree(
            LocalTreeSource(
                root=root,
                public_base_url="https://legacy.example.test/",
                repository="https://github.com/DataTalksClub/fixture.git",
                revision=REVISION,
            ),
            policy=policy(),
        )

        assert {capture.source_path: capture.requested_url for capture in captures} == {
            relative: f"https://legacy.example.test/{public}"
            for relative, public in relative_to_public.items()
        }
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            else:
                path.rmdir()
        root.rmdir()
