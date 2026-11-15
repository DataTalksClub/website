"""HTTP client for ``dtc``."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from dtc_cli.config import ConfigError, resolve_credentials


class APIError(RuntimeError):
    """HTTP or API error returned by the management server."""


class DtcClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
            follow_redirects=False,
        )

    def _handle(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    code = err.get("code", response.status_code)
                    message = err.get("message", response.text)
                    raise APIError(f"{code}: {message}")
                if isinstance(err, str):
                    raise APIError(f"{err}: {body.get('message', response.text)}")
            raise APIError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise APIError(f"Invalid JSON response from server: {exc}") from exc
        if not isinstance(data, dict):
            return {"data": data}
        return data

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._handle(self._http.get(path, params=params))

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
        return self._handle(self._http.post(path, json=json_body or {}, headers=headers))

    def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
        return self._handle(self._http.patch(path, json=json_body or {}, headers=headers))


def get_client(url: str | None = None, token: str | None = None) -> DtcClient:
    try:
        base, secret = resolve_credentials(url=url, token=token)
    except ConfigError as exc:
        raise APIError(str(exc)) from exc
    return DtcClient(base, secret)
