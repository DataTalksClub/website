"""Bounded regressions for the locked urllib3 security update."""

from __future__ import annotations

import socket
import ssl
import subprocess
import sys
import threading
import zlib
from pathlib import Path
from typing import cast
from unittest.mock import patch

import urllib3
from django.test import SimpleTestCase
from urllib3._base_connection import ProxyConfig
from urllib3.connection import HTTPSConnection, _WrappedAndVerifiedSocket
from urllib3.exceptions import ProtocolError
from urllib3.util.ssl_ import create_urllib3_context

READ_SIZE = 65_536
SERVER_TIMEOUT_SECONDS = 2
STREAM_TIMEOUT_SECONDS = 3


class RawResponseServer:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(SERVER_TIMEOUT_SECONDS)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.error: Exception | None = None

    @property
    def url(self) -> str:
        port = self.listener.getsockname()[1]
        return f"http://127.0.0.1:{port}/"

    def _serve(self) -> None:
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(SERVER_TIMEOUT_SECONDS)
                request = b""
                while not request.endswith(b"\r\n\r\n"):
                    received = connection.recv(READ_SIZE)
                    if not received:
                        raise ConnectionError("client closed before completing its request")
                    request += received
                connection.sendall(self.response)
        except Exception as error:
            self.error = error

    def __enter__(self) -> RawResponseServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.listener.close()
        self.thread.join(timeout=SERVER_TIMEOUT_SECONDS)
        if self.thread.is_alive():
            raise RuntimeError("synthetic HTTP server did not terminate")
        if self.error is not None:
            raise self.error


def response_bytes(headers: bytes, body: bytes) -> bytes:
    return b"HTTP/1.1 200 OK\r\n" + headers + b"Connection: close\r\n\r\n" + body


def chunked_body(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n0\r\n\r\n"


def proxy_policy_connection() -> tuple[HTTPSConnection, ssl.SSLContext]:
    target_context = create_urllib3_context(cert_reqs=ssl.CERT_NONE)
    proxy_context = create_urllib3_context(cert_reqs=ssl.CERT_REQUIRED)
    connection = HTTPSConnection(
        "destination.invalid",
        ssl_context=target_context,
        cert_reqs=ssl.CERT_NONE,
        assert_hostname="destination.invalid",
        assert_fingerprint="11" * 32,
    )
    connection.proxy_config = ProxyConfig(proxy_context, False, "proxy.invalid", "22" * 32)
    return connection, proxy_context


def deflate_fixture() -> tuple[bytes, bytes]:
    decoded = b"bounded response" * 20
    encoded = zlib.compress(decoded) + b"trailing-data"
    headers = b"Transfer-Encoding: chunked\r\nContent-Encoding: deflate\r\n"
    return decoded, response_bytes(headers, chunked_body(encoded))


def stream_deflate_fixture() -> bytes:
    _, wire = deflate_fixture()
    with RawResponseServer(wire) as server, urllib3.PoolManager() as pool:
        response = pool.request("GET", server.url, preload_content=False)
        return b"".join(response.stream(50, decode_content=True))


def bounded_stream_response() -> bytes:
    command = (sys.executable, str(Path(__file__).resolve()), "deflate-probe")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(timeout=STREAM_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        process.terminate()
        try:
            process.communicate(timeout=SERVER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise AssertionError(
            "streaming Deflate response exceeded its subprocess timeout"
        ) from error
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace")
        raise AssertionError(
            f"streaming subprocess failed with exit {process.returncode}: {detail}"
        )
    return stdout


class Urllib3DependencySecurityTests(SimpleTestCase):
    def test_proxy_tls_policy_is_separate_from_destination_policy(self) -> None:
        connection, proxy_context = proxy_policy_connection()
        captured: dict[str, object] = {}

        def capture(sock: socket.socket, **kwargs: object) -> _WrappedAndVerifiedSocket:
            captured.update(kwargs)
            return _WrappedAndVerifiedSocket(cast(ssl.SSLSocket, sock), True)

        with socket.socket() as sock:
            with patch(
                "urllib3.connection._ssl_wrap_socket_and_match_hostname", side_effect=capture
            ):
                wrapped = connection._connect_tls_proxy("proxy.invalid", sock)

        self.assertIs(wrapped, sock)
        self.assertIs(captured["ssl_context"], proxy_context)
        self.assertEqual(captured["cert_reqs"], ssl.CERT_REQUIRED)
        self.assertEqual(captured["assert_hostname"], "proxy.invalid")
        self.assertEqual(captured["assert_fingerprint"], "22" * 32)
        self.assertEqual(proxy_context.verify_mode, ssl.CERT_REQUIRED)

    def test_oversized_chunk_size_line_is_rejected_at_the_bound(self) -> None:
        body = b"f" * (READ_SIZE + 1_024)
        wire = response_bytes(b"Transfer-Encoding: chunked\r\n", body)

        with RawResponseServer(wire) as server, urllib3.PoolManager() as pool:
            response = pool.request("GET", server.url, preload_content=False)
            with self.assertRaisesRegex(ProtocolError, "exceeded maximum allowed length"):
                next(response.stream(READ_SIZE))

    def test_chunked_deflate_with_trailing_bytes_terminates(self) -> None:
        decoded, _ = deflate_fixture()

        self.assertEqual(bounded_stream_response(), decoded)

    def test_ordinary_http_response_remains_readable(self) -> None:
        body = b"ordinary synthetic response"
        headers = f"Content-Length: {len(body)}\r\n".encode("ascii")
        wire = response_bytes(headers, body)

        with RawResponseServer(wire) as server, urllib3.PoolManager() as pool:
            response = pool.request("GET", server.url)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, body)


if __name__ == "__main__":
    if sys.argv[1:] != ["deflate-probe"]:
        raise SystemExit("expected deflate-probe")
    sys.stdout.buffer.write(stream_deflate_fixture())
