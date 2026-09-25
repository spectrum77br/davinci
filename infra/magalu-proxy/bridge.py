#!/usr/bin/env python3
"""Relay TCP streams to the Unix socket maintained by the SSH tunnel."""

import argparse
import asyncio
import math
import signal

BUFFER_SIZE = 64 * 1024
CLOSE_TIMEOUT = 5.0


class BridgeServer:
    """A bounded, content-agnostic TCP-to-Unix stream relay."""

    def __init__(
        self,
        socket_path: str,
        idle_timeout: float = 120,
        connect_timeout: float = 10,
        max_connections: int = 64,
    ) -> None:
        if not socket_path:
            raise ValueError("socket_path must not be empty")
        for name, timeout in (
            ("idle_timeout", idle_timeout),
            ("connect_timeout", connect_timeout),
        ):
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if max_connections < 1:
            raise ValueError("max_connections must be positive")
        self.socket_path = socket_path
        self.idle_timeout = idle_timeout
        self.connect_timeout = connect_timeout
        self.max_connections = max_connections
        self._clients: set[asyncio.Task] = set()
        self._closing = False

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            writer.transport.abort()
        except asyncio.CancelledError:
            writer.transport.abort()
            raise

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Admission has no await, so it is atomic within the event loop. Reject
        # excess clients instead of accumulating an unbounded semaphore queue.
        if self._closing or len(self._clients) >= self.max_connections:
            await self._close_writer(writer)
            return
        client_task = asyncio.current_task()
        self._clients.add(client_task)
        upstream_writer = None
        workers = []
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path, limit=BUFFER_SIZE),
                timeout=self.connect_timeout,
            )
            loop = asyncio.get_running_loop()
            last_activity = [loop.time()]

            async def copy_stream(source, destination) -> None:
                while True:
                    chunk = await source.read(BUFFER_SIZE)
                    if not chunk:
                        # EOF affects only this direction. The opposite pump
                        # stays alive so the peer can finish its response.
                        if destination.can_write_eof():
                            destination.write_eof()
                            await destination.drain()
                        return
                    last_activity[0] = loop.time()
                    destination.write(chunk)
                    await destination.drain()

            async def watch_idle() -> None:
                while True:
                    remaining = self.idle_timeout - (
                        loop.time() - last_activity[0]
                    )
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    await asyncio.sleep(remaining)

            pumps = {
                asyncio.create_task(copy_stream(reader, upstream_writer)),
                asyncio.create_task(copy_stream(upstream_reader, writer)),
            }
            idle_guard = asyncio.create_task(watch_idle())
            workers = list(pumps) + [idle_guard]
            while pumps:
                completed, _ = await asyncio.wait(
                    pumps | {idle_guard}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in completed:
                    task.result()
                pumps.difference_update(completed)
        except (OSError, asyncio.TimeoutError):
            # Missing or stale tunnel sockets affect this connection only.
            # Every subsequent client makes a fresh Unix-socket connection.
            pass
        finally:
            try:
                for worker in workers:
                    worker.cancel()
                if workers:
                    await asyncio.gather(*workers, return_exceptions=True)
                writers = [writer]
                if upstream_writer is not None:
                    writers.append(upstream_writer)
                await asyncio.gather(
                    *(self._close_writer(stream) for stream in writers),
                    return_exceptions=True,
                )
            finally:
                self._clients.discard(client_task)

    async def close(self) -> None:
        """Stop accepting work and close all admitted client connections."""
        self._closing = True
        clients = list(self._clients)
        for client in clients:
            client.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)


async def run(socket_path: str, port: int) -> None:
    bridge = BridgeServer(socket_path)
    listener = await asyncio.start_server(
        bridge.handle_client, "0.0.0.0", port, limit=BUFFER_SIZE  # noqa: S104 - private container
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals = []
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
            installed_signals.append(signum)
        async with listener:
            await stop.wait()
    finally:
        listener.close()
        await listener.wait_closed()
        await bridge.close()
        for signum in installed_signals:
            loop.remove_signal_handler(signum)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="/tunnel/magalu.sock")
    parser.add_argument("--port", type=int, default=13129)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        asyncio.run(run(args.socket, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
