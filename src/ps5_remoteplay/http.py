"""Minimal HTTP/1.1 over asyncio streams.

The Remote Play ctrl request hands its TCP connection over to the binary
frame protocol, so the response must be read without consuming anything past
the headers. A general-purpose client can't guarantee that.
"""

import asyncio
import logging
from dataclasses import dataclass

from .const import REMOTEPLAY_PORT, USER_AGENT
from .errors import ProtocolError, RemotePlayHttpError

_LOGGER = logging.getLogger(__name__)

_MAX_HEADER_BYTES = 64 * 1024


@dataclass
class Response:
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes


def build_request(
    method: str,
    host: str,
    port: int,
    path: str,
    headers: dict[str, str],
    body: bytes = b"",
) -> bytes:
    # Content-Length: 0 is required even on GETs; the console returns 403 without it
    lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}:{port}",
        f"User-Agent: {USER_AGENT}",
        *(f"{k}: {v}" for k, v in headers.items()),
        f"Content-Length: {len(body)}",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


async def read_response(reader: asyncio.StreamReader, *, read_body: bool = True) -> Response:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError as e:
        raise ProtocolError("Connection closed before HTTP response headers") from e
    except asyncio.LimitOverrunError as e:
        raise ProtocolError("HTTP response headers too large") from e

    if len(head) > _MAX_HEADER_BYTES:
        raise ProtocolError("HTTP response headers too large")

    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        raise ProtocolError(f"Invalid HTTP status line: {status_line!r}")

    headers: dict[str, str] = {}
    for line in header_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    body = b""
    if read_body:
        if "chunked" in headers.get("transfer-encoding", "").lower():
            raise ProtocolError("Chunked HTTP responses are not supported")
        if "content-length" in headers:
            body = await reader.readexactly(int(headers["content-length"]))
        else:
            body = await reader.read()

    return Response(int(parts[1]), parts[2] if len(parts) > 2 else "", headers, body)


def raise_for_status(response: Response) -> None:
    if response.status >= 300:
        raise RemotePlayHttpError(response.status, response.headers.get("rp-application-reason"))


async def request(
    host: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes = b"",
    *,
    port: int = REMOTEPLAY_PORT,
    timeout: float = 15.0,
) -> Response:
    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_connection(host, port)
        try:
            _LOGGER.debug("%s %s: request sent", method, path)
            writer.write(build_request(method, host, port, path, headers, body))
            await writer.drain()
            response = await read_response(reader)
        finally:
            writer.close()
    _LOGGER.debug(
        "%s %s: HTTP %d, headers %s, %d body bytes",
        method, path, response.status, sorted(response.headers), len(response.body),
    )
    raise_for_status(response)
    return response
