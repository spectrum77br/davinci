"""Standalone checks: python3 apps/api/tests/unit/test_magalu_mac_proxy.py.

Only local TCP/Unix sockets are used. No app configuration, database, or Magalu
request is loaded. Pytest users can run this file with --noconftest.
"""

import asyncio
import base64
import importlib.util
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = load_module("magalu_mac_proxy", ROOT / "scripts/magalu_mac_proxy.py")
bridge = load_module("magalu_bridge", ROOT / "infra/magalu-proxy/bridge.py")
CREDENTIALS = b"test-user:test-password"
AUTH = base64.b64encode(CREDENTIALS)


def request(target=b"api.magalu.com:443", auth=AUTH, extra=b""):
    return (
        b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n"
        + (b"Proxy-Authorization: Basic " + auth + b"\r\n" if auth is not None else b"")
        + extra + b"\r\n"
    )


@asynccontextmanager
async def listener(handler, unix_path=None):
    clients = set()

    async def tracked(reader, writer):
        task = asyncio.current_task()
        clients.add(task)
        try:
            await handler(reader, writer)
        finally:
            await proxy.close_writer(writer)
            clients.discard(task)

    if unix_path:
        server = await asyncio.start_unix_server(tracked, unix_path)
        address = unix_path
    else:
        server = await asyncio.start_server(
            tracked, "127.0.0.1", 0, limit=proxy.MAX_HEADER_BYTES - 4,
        )
        address = server.sockets[0].getsockname()
    try:
        yield address
    finally:
        server.close()
        await server.wait_closed()
        for task in list(clients):
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)


class ParserTests(unittest.TestCase):
    def assert_status(self, raw, status):
        with self.assertRaises(proxy.ProxyError) as error:
            proxy.parse_connect(raw, CREDENTIALS)
        self.assertEqual(error.exception.status, status)

    def test_only_two_literal_authorities_are_allowed(self):
        for host in (b"api.magalu.com", b"id.magalu.com"):
            self.assertEqual(
                proxy.parse_connect(request(host + b":443"), CREDENTIALS),
                (host.decode(), 443),
            )
        for target in (
            b"127.0.0.1:443", b"localhost:443", b"[::1]:443", b"169.254.169.254:443",
            b"api.magalu.com:80", b"api.magalu.com:0443", b"api.magalu.com",
            b"https://api.magalu.com:443", b"api.magalu.com:443/path",
            b"api.magalu.com:443?x=1", b"api.magalu.com:443#fragment",
            b"user@api.magalu.com:443", b"api.magalu.com.evil.test:443",
            b"api.magalu.com.:443", b"API.MAGALU.COM:443", b"api%2emagalu.com:443",
            b"api.magalu.com:443\x00", b"api.magalu.com:443\t",
        ):
            with self.subTest(target=target):
                self.assert_status(request(target), 403)

    def test_authentication_is_mandatory_and_strict(self):
        for auth in (None, b"", b"not!base64", base64.b64encode(b"test-user:wrong")):
            with self.subTest(auth=auth):
                self.assert_status(request(auth=auth), 407)
        self.assert_status(request().replace(b"Basic ", b"Bearer "), 407)

    def test_ambiguous_and_injected_headers_are_rejected(self):
        for raw in (
            request(extra=b"Proxy-Authorization: Basic " + AUTH + b"\r\n"),
            request(extra=b"Host: id.magalu.com:443\r\n"),
            request(extra=b" folded: bad\r\n"),
            request(extra=b"Invalid Name: bad\r\n"),
            request(extra=b"Invalid: bad\x00value\r\n"),
            request(extra=b"Invalid: bad\nvalue\r\n"),
            request(extra=b"Content-Length: 0\r\n"),
            request(extra=b"Transfer-Encoding: chunked\r\n"),
            request().replace(b"Host: api.magalu.com:443", b"Host: localhost:443"),
            request().replace(b" HTTP/1.1", b"  HTTP/1.1"),
            request().replace(b"HTTP/1.1", b"HTTP/2"),
            request()[:-2],
        ):
            with self.subTest(raw=raw):
                self.assert_status(raw, 400)
        self.assert_status(request().replace(b"CONNECT ", b"GET "), 405)
        self.assert_status(request(extra=b"X-Padding: " + b"x" * 16384 + b"\r\n"), 431)


class CredentialTests(unittest.TestCase):
    def test_credentials_require_private_owned_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text(json.dumps({"username": "test-user", "password": "test-password"}))
            path.chmod(0o600)
            self.assertEqual(proxy.load_credentials(path), CREDENTIALS)
            for mode in (0o644, 0o640, 0o400, 0o660):
                path.chmod(mode)
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    proxy.load_credentials(path)
            path.chmod(0o600)
            link = Path(directory) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                proxy.load_credentials(link)

    def test_invalid_credentials_fail_without_echoing_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            for body in (
                "not JSON test-password", "[]",
                json.dumps({"username": "x", "password": "test-password", "token": "ignored"}),
                json.dumps({"username": "x:y", "password": "test-password"}),
                json.dumps({"username": "x", "password": "test-password\n"}),
                json.dumps({"username": "x", "password": None}),
                json.dumps({"username": "", "password": "test-password"}),
                "x" * 4097,
            ):
                path.write_text(body)
                path.chmod(0o600)
                with self.subTest(body=body), self.assertRaises(ValueError) as error:
                    proxy.load_credentials(path)
                self.assertNotIn("test-password", str(error.exception))


class ProxySocketTests(unittest.IsolatedAsyncioTestCase):
    async def response(self, proxy_server, raw):
        async with listener(proxy_server.handle_client) as address:
            reader, writer = await asyncio.open_connection(*address)
            try:
                writer.write(raw)
                await writer.drain()
                return await asyncio.wait_for(reader.read(), 2)
            finally:
                await proxy.close_writer(writer)

    async def test_rejected_requests_never_open_an_upstream_connection(self):
        calls = []

        async def connector(host, port):
            calls.append((host, port))
            raise AssertionError("Rejected requests must not leave this machine")

        server = proxy.ProxyServer(CREDENTIALS, connector=connector)
        for raw, status in ((request(auth=None), 407), (request(b"localhost:443"), 403)):
            response = await self.response(server, raw)
            self.assertTrue(response.startswith(f"HTTP/1.1 {status} ".encode()))
            self.assertNotIn(CREDENTIALS, response)
        self.assertEqual(calls, [])
        self.assertEqual(server.active_connections, 0)

    async def test_connect_preserves_bytes_and_half_close_without_forwarding_proxy_headers(self):
        received = []
        calls = []
        opaque = b"\x16\x03\x01encrypted-TLS-bytes\x00\xff"

        async def upstream(reader, writer):
            received.append(await reader.read())
            writer.write(b"opaque-response:" + received[-1])
            await writer.drain()

        async with listener(upstream) as upstream_address:
            async def connector(host, port):
                calls.append((host, port))
                return await asyncio.open_connection(*upstream_address)

            server = proxy.ProxyServer(CREDENTIALS, connector=connector)
            async with listener(server.handle_client) as address:
                reader, writer = await asyncio.open_connection(*address)
                try:
                    writer.write(request() + opaque)
                    await writer.drain()
                    self.assertEqual(
                        await reader.readuntil(b"\r\n\r\n"),
                        b"HTTP/1.1 200 Connection Established\r\n\r\n",
                    )
                    writer.write_eof()
                    self.assertEqual(
                        await asyncio.wait_for(reader.read(), 2), b"opaque-response:" + opaque,
                    )
                finally:
                    await proxy.close_writer(writer)
        self.assertEqual(calls, [("api.magalu.com", 443)])
        self.assertEqual(received, [opaque])
        self.assertEqual(server.active_connections, 0)

    async def test_header_timeout_and_size_limit_close_connection(self):
        server = proxy.ProxyServer(CREDENTIALS, header_timeout=0.03)
        response = await self.response(server, b"CONNECT api.magalu.com:443 HTTP/1.1\r\n")
        self.assertTrue(response.startswith(b"HTTP/1.1 408 "))
        response = await self.response(server, b"X" * (proxy.MAX_HEADER_BYTES + 1))
        self.assertTrue(response.startswith(b"HTTP/1.1 431 "))
        self.assertEqual(server.active_connections, 0)

    async def test_upstream_failures_are_sanitized_and_close_connection(self):
        async def failing(host, port):
            raise OSError("sensitive diagnostic must not leave this process")

        async def slow(host, port):
            await asyncio.sleep(5)

        for connector, status in ((failing, 502), (slow, 504)):
            server = proxy.ProxyServer(CREDENTIALS, connector=connector, connect_timeout=0.03)
            response = await self.response(server, request())
            self.assertTrue(response.startswith(f"HTTP/1.1 {status} ".encode()))
            self.assertNotIn(b"sensitive", response)
            self.assertEqual(server.active_connections, 0)

    async def test_idle_tunnel_closes_both_ends(self):
        upstream_closed = asyncio.Event()

        async def upstream(reader, writer):
            self.assertEqual(await reader.read(), b"")
            upstream_closed.set()

        async with listener(upstream) as upstream_address:
            async def connector(host, port):
                return await asyncio.open_connection(*upstream_address)

            server = proxy.ProxyServer(CREDENTIALS, connector=connector, idle_timeout=0.03)
            response = await self.response(server, request())
            self.assertEqual(response, b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.wait_for(upstream_closed.wait(), 1)
        self.assertEqual(server.active_connections, 0)

    async def test_connection_limit_rejects_excess_clients(self):
        server = proxy.ProxyServer(CREDENTIALS, max_connections=1)
        async with listener(server.handle_client) as address:
            first_reader, first_writer = await asyncio.open_connection(*address)
            second_reader, second_writer = await asyncio.open_connection(*address)
            try:
                response = await asyncio.wait_for(second_reader.read(), 1)
                self.assertTrue(response.startswith(b"HTTP/1.1 503 "))
                self.assertEqual(server.active_connections, 1)
                first_writer.write_eof()
                self.assertTrue((await first_reader.read()).startswith(b"HTTP/1.1 400 "))
            finally:
                await proxy.close_writer(first_writer)
                await proxy.close_writer(second_writer)
        self.assertEqual(server.active_connections, 0)

    async def test_cancellation_during_cleanup_still_closes_client_and_releases_capacity(self):
        class RecordingWriter:
            def __init__(self, block_close=False):
                self.closed = False
                self.aborted = False
                self.transport = self
                self.block_close = block_close
                self.close_started = asyncio.Event()

            def close(self):
                self.closed = True

            async def wait_closed(self):
                self.close_started.set()
                if self.block_close:
                    await asyncio.Event().wait()

            def abort(self):
                self.aborted = True

            def can_write_eof(self):
                return False

            def write(self, data):
                pass

            async def drain(self):
                pass

        client_reader = asyncio.StreamReader()
        client_reader.feed_data(request())
        client_reader.feed_eof()
        client_writer = RecordingWriter()
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_eof()
        upstream_writer = RecordingWriter(block_close=True)

        async def connector(host, port):
            return upstream_reader, upstream_writer

        server = proxy.ProxyServer(CREDENTIALS, connector=connector)
        task = asyncio.create_task(server.handle_client(client_reader, client_writer))
        await asyncio.wait_for(upstream_writer.close_started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(upstream_writer.closed)
        self.assertTrue(upstream_writer.aborted)
        self.assertTrue(client_writer.closed)
        self.assertEqual(server.active_connections, 0)


class BridgeSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_uses_configured_unix_socket_and_recovers_after_it_returns(self):
        with tempfile.TemporaryDirectory(prefix="mg-", dir="/tmp") as directory:
            socket_path = str(Path(directory) / "magalu.sock")
            server = bridge.BridgeServer(socket_path)
            seen = []

            async def unix_peer(reader, writer):
                seen.append(await reader.read())
                writer.write(b"reply:" + seen[-1])
                await writer.drain()

            async with listener(server.handle_client) as address:
                reader, writer = await asyncio.open_connection(*address)
                self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
                await proxy.close_writer(writer)
                async with listener(unix_peer, socket_path):
                    reader, writer = await asyncio.open_connection(*address)
                    try:
                        writer.write(b"CONNECT-with-auth-remains-opaque\x00\xff")
                        await writer.drain()
                        writer.write_eof()
                        self.assertEqual(
                            await asyncio.wait_for(reader.read(), 1),
                            b"reply:CONNECT-with-auth-remains-opaque\x00\xff",
                        )
                    finally:
                        await proxy.close_writer(writer)
            await server.close()
            self.assertEqual(seen, [b"CONNECT-with-auth-remains-opaque\x00\xff"])

    async def test_bridge_idle_timeout_and_shutdown_close_both_ends(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown):
                with tempfile.TemporaryDirectory(prefix="mg-", dir="/tmp") as directory:
                    socket_path = str(Path(directory) / "magalu.sock")
                    connected = asyncio.Event()
                    closed = asyncio.Event()

                    async def unix_peer(reader, writer, connected=connected, closed=closed):
                        connected.set()
                        self.assertEqual(await reader.read(), b"")
                        closed.set()

                    server = bridge.BridgeServer(socket_path, idle_timeout=0.03)
                    async with listener(unix_peer, socket_path):
                        async with listener(server.handle_client) as address:
                            reader, writer = await asyncio.open_connection(*address)
                            try:
                                await asyncio.wait_for(connected.wait(), 1)
                                if shutdown:
                                    await server.close()
                                self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
                                await asyncio.wait_for(closed.wait(), 1)
                            finally:
                                await proxy.close_writer(writer)
                    await server.close()


if __name__ == "__main__":
    unittest.main()
