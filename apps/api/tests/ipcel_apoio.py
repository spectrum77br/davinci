# ruff: noqa: S105, S106  (senhas de teste, nada real)
"""Apoio dos testes do IP da loja no celular: proxy SOCKS5 "de mentira" (faz o
papel do proxy da empresa), servidor de eco e um cliente SOCKS5 mínimo."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import struct
from dataclasses import dataclass, field


@dataclass
class Visto:
    usuario: str
    senha: str
    destino: str
    porta: int


@dataclass
class ProxyFalso:
    """SOCKS5 com usuário/senha. Todo CONNECT aceito vai para o servidor de
    eco (o destino pedido só é anotado)."""

    usuario: str
    senha: str
    porta_eco: int
    rep: int = 0
    vistos: list[Visto] = field(default_factory=list)
    recusas: int = 0
    porta: int = 0
    _servidor: asyncio.base_events.Server | None = None
    _conexoes: set = field(default_factory=set)

    async def iniciar(self) -> ProxyFalso:
        self._servidor = await asyncio.start_server(self._tratar, "127.0.0.1", 0)
        self.porta = self._servidor.sockets[0].getsockname()[1]
        return self

    async def fechar(self) -> None:
        if self._servidor is not None:
            self._servidor.close()
            for t in list(self._conexoes):
                t.cancel()
            await asyncio.gather(*self._conexoes, return_exceptions=True)
            await self._servidor.wait_closed()

    async def _tratar(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        tarefa = asyncio.current_task()
        self._conexoes.add(tarefa)
        eco_w = None
        try:
            _ver, n = await r.readexactly(2)
            metodos = await r.readexactly(n)
            if 2 not in metodos:
                w.write(b"\x05\xff")
                return
            w.write(b"\x05\x02")
            await w.drain()
            _v, ulen = await r.readexactly(2)
            u = (await r.readexactly(ulen)).decode()
            (plen,) = await r.readexactly(1)
            s = (await r.readexactly(plen)).decode()
            if (u, s) != (self.usuario, self.senha):
                self.recusas += 1
                w.write(b"\x01\x01")
                await w.drain()
                return
            w.write(b"\x01\x00")
            await w.drain()
            _v, _cmd, _r, atyp = await r.readexactly(4)
            if atyp == 1:
                destino = str(ipaddress.IPv4Address(await r.readexactly(4)))
            elif atyp == 4:
                destino = str(ipaddress.IPv6Address(await r.readexactly(16)))
            else:
                (n,) = await r.readexactly(1)
                destino = (await r.readexactly(n)).decode()
            (porta,) = struct.unpack(">H", await r.readexactly(2))
            self.vistos.append(Visto(u, s, destino, porta))
            if self.rep != 0:
                w.write(b"\x05" + bytes([self.rep]) + b"\x00\x01" + b"\x00" * 6)
                await w.drain()
                return
            eco_r, eco_w = await asyncio.open_connection("127.0.0.1", self.porta_eco)
            w.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
            await w.drain()
            await _ligar(r, w, eco_r, eco_w)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            # abort (e não close): com os buffers cheios o close esperaria
            # para sempre e o teste travaria no fim.
            for x in (w, eco_w):
                if x is not None:
                    x.transport.abort()
            self._conexoes.discard(tarefa)


async def _ligar(r1, w1, r2, w2) -> None:
    async def copiar(a, b):
        try:
            while True:
                d = await a.read(65536)
                if not d:
                    with contextlib.suppress(Exception):
                        b.write_eof()
                    return
                b.write(d)
                await b.drain()
        except (ConnectionError, OSError):
            return

    await asyncio.gather(copiar(r1, w2), copiar(r2, w1))


class Eco:
    def __init__(self) -> None:
        self.porta = 0
        self._servidor = None
        self._conexoes: set = set()

    async def iniciar(self) -> Eco:
        self._servidor = await asyncio.start_server(self._tratar, "127.0.0.1", 0)
        self.porta = self._servidor.sockets[0].getsockname()[1]
        return self

    async def _tratar(self, r, w):
        tarefa = asyncio.current_task()
        self._conexoes.add(tarefa)
        try:
            while True:
                d = await r.read(65536)
                if not d:
                    break
                w.write(d)
                await w.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            w.transport.abort()
            self._conexoes.discard(tarefa)

    async def fechar(self) -> None:
        self._servidor.close()
        for t in list(self._conexoes):
            t.cancel()
        await asyncio.gather(*self._conexoes, return_exceptions=True)
        await self._servidor.wait_closed()


@dataclass
class Resultado:
    metodo: bytes = b""
    auth: bytes = b""
    rep: int | None = None
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None

    def fechar(self) -> None:
        if self.writer is not None:
            self.writer.close()


async def socks(
    porta: int,
    usuario: str | None,
    senha: str | None,
    destino: str = "loja.exemplo.com.br",
    dporta: int = 443,
    *,
    cmd: int = 1,
    metodos: bytes = b"\x02",
) -> Resultado:
    """Cliente SOCKS5 mínimo (o papel do Shadowrocket)."""
    res = Resultado()
    r, w = await asyncio.open_connection("127.0.0.1", porta)
    res.reader, res.writer = r, w
    try:
        w.write(b"\x05" + bytes([len(metodos)]) + metodos)
        await w.drain()
        res.metodo = await r.readexactly(2)
        if res.metodo != b"\x05\x02":
            return res
        u = (usuario or "").encode()
        s = (senha or "").encode()
        w.write(b"\x01" + bytes([len(u)]) + u + bytes([len(s)]) + s)
        await w.drain()
        res.auth = await r.readexactly(2)
        if res.auth != b"\x01\x00":
            return res
        try:
            ip = ipaddress.ip_address(destino)
            end = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        except ValueError:
            b = destino.encode()
            end = b"\x03" + bytes([len(b)]) + b
        w.write(b"\x05" + bytes([cmd]) + b"\x00" + end + struct.pack(">H", dporta))
        await w.drain()
        cab = await r.readexactly(4)
        res.rep = cab[1]
        await r.readexactly(6 if cab[3] == 1 else 18)
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    return res


async def eco_ok(res: Resultado, dados: bytes = b"ola iphone") -> bool:
    assert res.writer is not None and res.reader is not None
    res.writer.write(dados)
    await res.writer.drain()
    volta = await asyncio.wait_for(res.reader.readexactly(len(dados)), 5)
    return volta == dados


async def fechou(res: Resultado, prazo: float = 5.0) -> bool:
    """True se o intermediário derrubou a conexão dentro do prazo."""
    assert res.reader is not None
    try:
        while True:
            d = await asyncio.wait_for(res.reader.read(65536), prazo)
            if not d:
                return True
    except (ConnectionError, OSError):
        return True
    except TimeoutError:
        return False
