#!/usr/bin/env python3
"""Authenticated CONNECT-only egress for Magalu; TLS stays with the API client.

Run with --credentials /private/path/proxy.json (mode 0600), containing only
{"username": "...", "password": "..."}. This process never reads Magalu tokens.
It binds exclusively to IPv4 loopback; SSH supplies the private reverse tunnel.
"""

import argparse
import asyncio
import base64
import binascii
import hmac
import json
import os
import re
import stat
import sys

LISTEN_HOST = "127.0.0.1"
DEFAULT_PORT = 13129
MAX_HEADER_BYTES = 16384
ALLOWED_TARGETS = {"id.magalu.com:443", "api.magalu.com:443"}
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ProxyError(Exception):
    """A sanitized HTTP rejection; never includes request data."""

    def __init__(self, status):
        self.status = status


def load_credentials(path):
    """Read a private regular file without following a final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
        ):
            raise ValueError("Credentials require an owned regular file with mode 0600")
        raw = source.read(4097)
    if len(raw) > 4096:
        raise ValueError("Invalid proxy credentials file")
    try:
        config = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid proxy credentials file") from None
    if not isinstance(config, dict) or set(config) != {"username", "password"}:
        raise ValueError("Invalid proxy credentials file")
    username, password = config["username"], config["password"]
    for value, limit in ((username, 255), (password, 1024)):
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= limit
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise ValueError("Proxy credentials must be nonempty printable ASCII without spaces")
    if ":" in username:
        raise ValueError("Proxy username cannot contain a colon")
    return (username + ":" + password).encode("ascii")


def parse_connect(header, credentials):
    """Accept only a complete, unambiguous CONNECT header to the two hosts."""
    if len(header) > MAX_HEADER_BYTES:
        raise ProxyError(431)
    if not header.endswith(b"\r\n\r\n"):
        raise ProxyError(400)
    try:
        lines = header[:-4].decode("ascii").split("\r\n")
    except UnicodeError:
        raise ProxyError(400) from None
    request = lines[0].split(" ")
    if len(request) != 3 or request[2] not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ProxyError(400)
    method, target, _version = request
    if method != "CONNECT":
        raise ProxyError(405)
    if target not in ALLOWED_TARGETS:
        raise ProxyError(403)
    headers = {}
    for line in lines[1:]:
        if ":" not in line or line.startswith((" ", "\t")):
            raise ProxyError(400)
        name, value = line.split(":", 1)
        if not HEADER_NAME.fullmatch(name) or any(
            ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value
        ):
            raise ProxyError(400)
        name = name.lower()
        if name in headers:
            raise ProxyError(400)
        headers[name] = value.strip(" \t")
    if "content-length" in headers or "transfer-encoding" in headers:
        raise ProxyError(400)
    if "host" in headers and headers["host"] != target:
        raise ProxyError(400)
    authorization = headers.get("proxy-authorization", "").split(" ")
    if len(authorization) != 2 or authorization[0].lower() != "basic":
        raise ProxyError(407)
    try:
        supplied = base64.b64decode(authorization[1], validate=True)
    except (ValueError, binascii.Error):
        raise ProxyError(407) from None
    if not hmac.compare_digest(supplied, credentials):
        raise ProxyError(407)
    return target[:-4], 443


async def close_writer(writer):
    if writer is None:
        return
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 2)
    except (OSError, asyncio.TimeoutError):
        writer.transport.abort()
    except asyncio.CancelledError:
        writer.transport.abort()
        raise


async def relay(left_reader, left_writer, right_reader, right_writer, idle_timeout):
    """Bounded bidirectional relay, preserving a half-close for the response."""
    loop = asyncio.get_running_loop()
    activity = [loop.time()]

    async def pump(reader, writer):
        while True:
            remaining = idle_timeout - (loop.time() - activity[0])
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                chunk = await asyncio.wait_for(reader.read(65536), remaining)
            except asyncio.TimeoutError:
                if loop.time() - activity[0] >= idle_timeout:
                    raise
                continue
            if not chunk:
                if writer.can_write_eof():
                    writer.write_eof()
                    await asyncio.wait_for(writer.drain(), idle_timeout)
                return
            activity[0] = loop.time()
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), idle_timeout)
            activity[0] = loop.time()

    tasks = [
        asyncio.create_task(pump(left_reader, right_writer)),
        asyncio.create_task(pump(right_reader, left_writer)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ProxyServer:
    def __init__(
        self, credentials, header_timeout=10, connect_timeout=10,
        idle_timeout=120, max_connections=64, connector=None,
    ):
        self.credentials = credentials
        self.header_timeout = header_timeout
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.max_connections = max_connections
        self.connector = connector or asyncio.open_connection
        self.active_connections = 0

    async def reject(self, writer, status):
        reason = {
            400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed",
            407: "Proxy Authentication Required", 408: "Request Timeout",
            431: "Request Header Fields Too Large", 502: "Bad Gateway",
            503: "Service Unavailable", 504: "Gateway Timeout",
        }[status]
        challenge = 'Proxy-Authenticate: Basic realm="Magalu egress"\r\n' if status == 407 else ""
        writer.write((
            f"HTTP/1.1 {status} {reason}\r\n{challenge}"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode("ascii"))
        await asyncio.wait_for(writer.drain(), self.header_timeout)

    async def handle_client(self, reader, writer):
        upstream_writer = None
        admitted = False
        established = False
        try:
            if self.active_connections >= self.max_connections:
                raise ProxyError(503)
            self.active_connections += 1
            admitted = True
            try:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.header_timeout)
            except asyncio.LimitOverrunError:
                raise ProxyError(431) from None
            except asyncio.IncompleteReadError:
                raise ProxyError(400) from None
            except asyncio.TimeoutError:
                raise ProxyError(408) from None
            host, port = parse_connect(header, self.credentials)
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    self.connector(host, port), self.connect_timeout,
                )
            except asyncio.TimeoutError:
                raise ProxyError(504) from None
            except OSError:
                raise ProxyError(502) from None
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.wait_for(writer.drain(), self.header_timeout)
            established = True
            await relay(reader, writer, upstream_reader, upstream_writer, self.idle_timeout)
        except ProxyError as error:
            if not established:
                try:
                    await self.reject(writer, error.status)
                except (OSError, asyncio.TimeoutError):
                    pass
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            try:
                await close_writer(upstream_writer)
            finally:
                try:
                    await close_writer(writer)
                finally:
                    if admitted:
                        self.active_connections -= 1


async def serve(credentials, port):
    proxy = ProxyServer(credentials)
    server = await asyncio.start_server(
        proxy.handle_client, LISTEN_HOST, port, limit=MAX_HEADER_BYTES - 4,
    )
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        credentials = load_credentials(args.credentials)
    except (OSError, ValueError):
        sys.exit("Cannot load proxy credentials: use a valid owned JSON file with mode 0600")
    try:
        asyncio.run(serve(credentials, args.port))
    except KeyboardInterrupt:
        pass
    except OSError:
        sys.exit("Cannot start the loopback Magalu proxy")


if __name__ == "__main__":
    main()
