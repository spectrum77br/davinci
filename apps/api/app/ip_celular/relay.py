"""Intermediário SOCKS5 do "Usar este IP no celular" (16/11/2026).

O iPhone (Shadowrocket) conecta AQUI com uma credencial temporária; daqui a
conexão segue para o proxy SOCKS5 de verdade da empresa (o mesmo IP do perfil
do AdsPower), com a senha real decifrada só dentro deste processo.

    iPhone ──SOCKS5 (credencial temporária)──▶ ipcel_relay:21080
           ──SOCKS5 (senha real)──▶ proxy da empresa (companies.ip:porta)
           ──▶ site (Mercado Livre)

Regras:
- Só usuário/senha (RFC 1929) e só TCP CONNECT. UDP ASSOCIATE e BIND são
  recusados (código 07).
- O destino vai para o proxy da empresa como veio (nome de site = resolvido
  LÁ, no IP da loja). Destino interno é recusado (código 02): IP privado, de
  loopback, link-local, do próprio servidor, nomes sem ponto (api, redis7...),
  localhost e afins, IPv4 disfarçado — e, para nome, se o DNS daqui mostrar
  que ele aponta para dentro.
- O intermediário NUNCA conecta no destino pedido: só no proxy da empresa,
  que precisa ser IP público e não pode ser o servidor.
- Limites por credencial: conexões ao mesmo tempo, banda (bytes/s, somando as
  conexões) e um teto de dados por link. Limite geral de conexões.
- Senha errada: 10 erros em 15 minutos bloqueiam o IP de origem (credencial
  que existiu e venceu não conta — é o iPhone insistindo com a antiga).
- No fim do prazo ou no "Cortar agora" (canal no Redis + conferência a cada
  5 s): recusa as novas e DERRUBA as abertas.
- Falha fechada: Redis fora = ninguém autentica; fora por mais de 30 s =
  derruba tudo.
- Log sem segredo: nem a senha real, nem o usuário/senha temporários, nem o
  endereço do proxy da empresa. Identifica pelo link_id.

Roda com `python -m app.ip_celular.relay` (serviço `ipcel_relay` no docker
compose, mesma imagem da api, SEM o .env: só REDIS_URL e IPCEL_RELAY_KEY).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import signal
import socket
import struct
import time
from dataclasses import dataclass, field

from redis.asyncio import BlockingConnectionPool, Redis

from app.ip_celular.comum import (
    PORTA_PADRAO,
    PREFIXO_PADRAO,
    Chaves,
    Cofre,
    contexto_upstream,
    motivo_destino_bloqueado,
    motivo_ip_bloqueado,
    motivo_upstream_bloqueado,
    normalizar_nome,
    redes_extras,
    usuario_valido,
)

log = logging.getLogger("ipcel.relay")

BUFFER = 64 * 1024
FECHAR_S = 5.0

# Códigos de resposta do SOCKS5 (RFC 1928).
REP_OK = 0x00
REP_FALHA = 0x01
REP_PROIBIDO = 0x02
REP_REDE = 0x03
REP_HOST = 0x04
REP_RECUSADA = 0x05
REP_COMANDO = 0x07
REP_ENDERECO = 0x08


class Recusa(Exception):  # noqa: N818 - nome do domínio, como no resto do código
    """Encerra a negociação. `rep` = código a responder ao celular (None =
    só fecha)."""

    def __init__(self, motivo: str, rep: int | None = None) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.rep = rep


class Encerrar(Exception):  # noqa: N818
    """Fim de uma conexão já aberta (prazo, corte, teto)."""


@dataclass
class Credencial:
    usuario: str
    link_id: str
    empresa: str
    expira: float
    up_host: str
    up_porta: int
    up_usuario: str
    up_senha: str
    max_conexoes: int
    banda: int
    teto: int

    def __repr__(self) -> str:  # nunca imprime a senha, nem por engano
        return f"Credencial(link={self.link_id})"


class Balde:
    """Banda por credencial (balde de fichas dividido entre as conexões)."""

    def __init__(self, taxa: int) -> None:
        self.taxa = max(1, int(taxa))
        self.fichas = float(self.taxa)
        self.ultimo = time.monotonic()

    async def gastar(self, n: int) -> None:
        agora = time.monotonic()
        self.fichas = min(self.taxa, self.fichas + (agora - self.ultimo) * self.taxa)
        self.ultimo = agora
        self.fichas -= n
        if self.fichas < 0:
            await asyncio.sleep(-self.fichas / self.taxa)


@dataclass
class EstadoLink:
    link_id: str
    usuario: str
    expira: float
    max_conexoes: int
    teto: int
    balde: Balde
    conexoes: set[asyncio.Task] = field(default_factory=set)
    total: int = 0  # bytes deste link (os dois sentidos), para o teto
    sobe: int = 0  # pendente de somar no Redis
    desce: int = 0
    aceitas: int = 0
    recusadas: int = 0
    teto_estourado: bool = False
    total_lido: bool = False


class Relay:
    def __init__(
        self,
        redis: Redis,
        cofre: Cofre,
        *,
        prefixo: str = PREFIXO_PADRAO,
        bloquear: list[str] | tuple[str, ...] = (),
        max_total: int = 512,
        max_negociando_por_ip: int = 64,
        handshake_s: float = 15.0,
        conectar_s: float = 20.0,
        ocioso_s: float = 300.0,
        max_falhas_ip: int = 10,
        janela_falhas_s: float = 15 * 60,
        vigia_s: float = 5.0,
        redis_fora_max_s: float = 30.0,
        checar_dns: bool = True,
        dns_s: float = 2.0,
        permitir_upstream_local: bool = False,
        pipeline_upstream: bool = False,
    ) -> None:
        self.redis = redis
        self.cofre = cofre
        self.k = Chaves(prefixo)
        self.extras = redes_extras(bloquear)
        self.max_total = max_total
        self.max_negociando_por_ip = max_negociando_por_ip
        self.handshake_s = handshake_s
        self.conectar_s = conectar_s
        self.ocioso_s = ocioso_s
        self.max_falhas_ip = max_falhas_ip
        self.janela_falhas_s = janela_falhas_s
        self.vigia_s = vigia_s
        self.redis_fora_max_s = redis_fora_max_s
        self.checar_dns = checar_dns
        self.dns_s = dns_s
        # SÓ para teste local: aceita proxy da empresa em 127.0.0.1. Nunca no
        # docker compose.
        self.permitir_upstream_local = permitir_upstream_local
        self.pipeline_upstream = pipeline_upstream
        self._clientes: set[asyncio.Task] = set()
        self._negociando: dict[str, int] = {}
        self._falhas: dict[str, list[float]] = {}
        self._links: dict[str, EstadoLink] = {}
        self._fundo: list[asyncio.Task] = []
        self._soltas: set[asyncio.Task] = set()  # referência forte (o loop só guarda fraca)
        self._fechando = False
        self._ultimo_redis_ok = time.monotonic()

    # ------------------------------------------------------------ servidor

    async def iniciar(self, host: str, porta: int) -> asyncio.Server:
        servidor = await asyncio.start_server(self.tratar, host, porta, limit=BUFFER)
        self._fundo = [
            asyncio.create_task(self._vigiar(), name="ipcel-vigia"),
            asyncio.create_task(self._ouvir_cortes(), name="ipcel-cortes"),
        ]
        return servidor

    async def fechar(self) -> None:
        self._fechando = True
        for t in self._fundo:
            t.cancel()
        clientes = list(self._clientes)
        for c in clientes:
            c.cancel()
        await asyncio.gather(*self._fundo, *clientes, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self._descarregar_metricas()

    # ------------------------------------------------------------ conexão

    @staticmethod
    async def _fechar_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), FECHAR_S)
        except (TimeoutError, OSError):
            writer.transport.abort()
        except asyncio.CancelledError:
            writer.transport.abort()
            raise

    async def tratar(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Admissão sem await: atômica no loop (molde do magalu-proxy).
        if self._fechando or len(self._clientes) >= self.max_total:
            writer.transport.abort()
            return
        pares = writer.get_extra_info("peername") or ("?", 0)
        ip_cliente = str(pares[0])
        if self._negociando.get(ip_cliente, 0) >= self.max_negociando_por_ip:
            writer.transport.abort()
            return
        tarefa = asyncio.current_task()
        assert tarefa is not None
        self._clientes.add(tarefa)
        self._negociando[ip_cliente] = self._negociando.get(ip_cliente, 0) + 1
        negociando = True
        estado: EstadoLink | None = None
        up_writer: asyncio.StreamWriter | None = None
        inicio = time.monotonic()
        # Corte, prazo ou teto: derruba NA HORA (RST), sem esperar o celular
        # ler o que ainda estava no buffer — o fechamento educado esperava
        # até 5 s com dados ainda passando.
        abortar = False
        try:
            try:
                estado, cred, up_reader, up_writer = await self._negociar(
                    reader, writer, ip_cliente, tarefa
                )
            except Recusa as r:
                if r.rep is not None:
                    with contextlib.suppress(Exception):
                        writer.write(_resposta(r.rep))
                        await writer.drain()
                if r.motivo not in ("fechou",):
                    log.info("ipcel recusa motivo=%s origem=%s", r.motivo, ip_cliente)
                return
            except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
                return
            self._negociando[ip_cliente] -= 1
            negociando = False
            await self._encaminhar(reader, writer, up_reader, up_writer, estado, cred)
        except Encerrar as e:
            abortar = True
            log.info("ipcel conexao_encerrada link=%s motivo=%s", estado and estado.link_id, e)
        except asyncio.CancelledError:
            abortar = True
            raise
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            try:
                if negociando:
                    self._negociando[ip_cliente] -= 1
                if self._negociando.get(ip_cliente) == 0:
                    self._negociando.pop(ip_cliente, None)
                writers = [writer] + ([up_writer] if up_writer is not None else [])
                if abortar:
                    for w in writers:
                        w.transport.abort()
                else:
                    await asyncio.gather(
                        *(self._fechar_writer(w) for w in writers), return_exceptions=True
                    )
            finally:
                self._clientes.discard(tarefa)
                if estado is not None:
                    estado.conexoes.discard(tarefa)
                    log.info(
                        "ipcel conexao_fechada link=%s duracao_s=%.1f abertas=%d",
                        estado.link_id,
                        time.monotonic() - inicio,
                        len(estado.conexoes),
                    )

    async def _negociar(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        ip_cliente: str,
        tarefa: asyncio.Task,
    ):
        cred = await asyncio.wait_for(
            self._autenticacao(reader, writer, ip_cliente), self.handshake_s
        )
        estado = await self._estado(cred)
        atyp, endereco, host, porta = await asyncio.wait_for(self._pedido(reader), self.handshake_s)
        if time.time() >= cred.expira or estado.teto_estourado:
            raise Recusa("credencial_vencida", REP_FALHA)
        if len(estado.conexoes) >= estado.max_conexoes:
            estado.recusadas += 1
            raise Recusa("limite_de_conexoes", REP_FALHA)
        motivo = motivo_destino_bloqueado(host, porta, self.extras)
        if motivo is None and isinstance(host, str):
            motivo = await self._dns_aponta_para_dentro(host, porta)
        if motivo is None and _mesmo_ip(host, cred.up_host):
            motivo = "destino_e_o_proxy"
        if motivo is not None:
            estado.recusadas += 1
            log.warning(
                "ipcel destino_bloqueado link=%s motivo=%s destino=%r porta=%d",
                cred.link_id,
                motivo,
                str(host)[:120],
                porta,
            )
            raise Recusa("destino_bloqueado", REP_PROIBIDO)
        # Conta ANTES de abrir o proxy da empresa: o corte que chegar durante
        # a conexão de saída já acha esta tarefa para derrubar.
        estado.conexoes.add(tarefa)
        estado.aceitas += 1
        try:
            up_reader, up_writer = await self._conectar_upstream(cred, atyp, endereco, porta)
        except BaseException:
            estado.conexoes.discard(tarefa)
            raise
        writer.write(_resposta(REP_OK))
        await writer.drain()
        log.info(
            "ipcel conexao_aberta link=%s porta=%d abertas=%d",
            cred.link_id,
            porta,
            len(estado.conexoes),
        )
        return estado, cred, up_reader, up_writer

    async def _autenticacao(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ip_cliente: str
    ) -> Credencial:
        try:
            ver, n = await reader.readexactly(2)
        except asyncio.IncompleteReadError:
            raise Recusa("fechou") from None
        if ver != 5:
            raise Recusa("nao_e_socks5")
        metodos = await reader.readexactly(n)
        if 0x02 not in metodos:
            writer.write(b"\x05\xff")
            await writer.drain()
            raise Recusa("sem_usuario_senha")
        writer.write(b"\x05\x02")
        await writer.drain()
        ver, ulen = await reader.readexactly(2)
        if ver != 1:
            raise Recusa("auth_invalida")
        usuario_b = await reader.readexactly(ulen)
        (plen,) = await reader.readexactly(1)
        senha_b = await reader.readexactly(plen)
        try:
            cred = await self._conferir(ip_cliente, usuario_b, senha_b)
        except Recusa:
            await asyncio.sleep(0.2)
            writer.write(b"\x01\x01")
            with contextlib.suppress(Exception):
                await writer.drain()
            raise
        writer.write(b"\x01\x00")
        await writer.drain()
        return cred

    def _falhas_recentes(self, ip: str) -> int:
        limite = time.monotonic() - self.janela_falhas_s
        lista = [t for t in self._falhas.get(ip, []) if t > limite]
        if lista:
            self._falhas[ip] = lista
        else:
            self._falhas.pop(ip, None)
        return len(lista)

    def _anotar_falha(self, ip: str) -> None:
        self._falhas.setdefault(ip, []).append(time.monotonic())
        if len(self._falhas) > 10_000:  # memória limitada mesmo sob ataque
            for velho in list(self._falhas)[:1000]:
                self._falhas.pop(velho, None)

    async def _conferir(self, ip: str, usuario_b: bytes, senha_b: bytes) -> Credencial:
        if self._falhas_recentes(ip) >= self.max_falhas_ip:
            raise Recusa("ip_bloqueado_por_tentativas")
        try:
            usuario = usuario_b.decode("ascii")
            senha = senha_b.decode("ascii")
        except UnicodeDecodeError:
            self._anotar_falha(ip)
            raise Recusa("senha_errada") from None
        if not usuario_valido(usuario):
            self._anotar_falha(ip)
            raise Recusa("senha_errada")
        try:
            bruto = await self.redis.get(self.k.cred(usuario))
            self._ultimo_redis_ok = time.monotonic()
        except Exception:  # noqa: BLE001 - falha fechada
            log.error("ipcel redis_fora etapa=autenticar")
            raise Recusa("redis_fora") from None
        if not bruto:
            # Credencial que existiu (venceu/cortada) não conta como tentativa.
            try:
                existiu = await self.redis.exists(self.k.usado(usuario))
            except Exception:  # noqa: BLE001
                existiu = 0
            if not existiu:
                self._anotar_falha(ip)
            raise Recusa("credencial_vencida" if existiu else "senha_errada")
        try:
            rec = json.loads(bruto)
            certo = self.cofre.senha_confere(usuario, senha, rec.get("resumo", ""))
        except (ValueError, TypeError):
            certo = False
            rec = {}
        if not certo:
            self._anotar_falha(ip)
            raise Recusa("senha_errada")
        expira = float(rec.get("expira") or 0)
        if time.time() >= expira:
            raise Recusa("credencial_vencida")
        try:
            up = self.cofre.decifrar(rec["up"], contexto_upstream(usuario))
            cred = Credencial(
                usuario=usuario,
                link_id=str(rec["link"]),
                empresa=str(rec.get("empresa") or ""),
                expira=expira,
                up_host=str(up["host"]),
                up_porta=int(up["porta"]),
                up_usuario=str(up["usuario"]),
                up_senha=str(up["senha"]),
                max_conexoes=int(rec.get("max_con") or 48),
                banda=int(rec.get("banda") or 3_000_000),
                teto=int(rec.get("teto") or 2_000_000_000),
            )
        except (KeyError, ValueError, TypeError):
            # Cifrado com outra chave ou trocado no Redis: não usa.
            log.error("ipcel credencial_adulterada link=%s", rec.get("link"))
            raise Recusa("credencial_adulterada") from None
        if not self.permitir_upstream_local:
            motivo = motivo_upstream_bloqueado(cred.up_host, self.extras)
            if motivo is not None:
                log.error("ipcel upstream_recusado link=%s motivo=%s", cred.link_id, motivo)
                raise Recusa("upstream_recusado")
        if not 0 < cred.up_porta < 65536:
            raise Recusa("upstream_recusado")
        return cred

    async def _estado(self, cred: Credencial) -> EstadoLink:
        estado = self._links.get(cred.link_id)
        if estado is None:
            estado = EstadoLink(
                link_id=cred.link_id,
                usuario=cred.usuario,
                expira=cred.expira,
                max_conexoes=cred.max_conexoes,
                teto=cred.teto,
                balde=Balde(cred.banda),
            )
            self._links[cred.link_id] = estado
        if not estado.total_lido:
            # Depois de um reinício do intermediário o teto continua valendo.
            try:
                sobe, desce = await self.redis.hmget(self.k.met(cred.link_id), "sobe", "desce")
                estado.total += int(sobe or 0) + int(desce or 0)
                estado.total_lido = True
            except Exception:  # noqa: BLE001 - sem Redis a autenticação já teria falhado
                log.warning("ipcel redis_fora etapa=teto link=%s", cred.link_id)
        return estado

    async def _pedido(self, reader: asyncio.StreamReader):
        ver, cmd, _rsv, atyp = await reader.readexactly(4)
        if ver != 5:
            raise Recusa("pedido_invalido")
        if atyp == 0x01:
            bruto = await reader.readexactly(4)
            host: str | ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.IPv4Address(bruto)
            endereco = bruto
        elif atyp == 0x04:
            bruto = await reader.readexactly(16)
            host = ipaddress.IPv6Address(bruto)
            endereco = bruto
        elif atyp == 0x03:
            (n,) = await reader.readexactly(1)
            nome = normalizar_nome(await reader.readexactly(n))
            if not nome:
                await reader.readexactly(2)
                raise Recusa("nome_invalido", REP_PROIBIDO)
            host = nome
            endereco = bytes([len(nome)]) + nome.encode()
        else:
            raise Recusa("tipo_de_endereco", REP_ENDERECO)
        (porta,) = struct.unpack(">H", await reader.readexactly(2))
        if cmd != 0x01:
            # 0x02 BIND e 0x03 UDP ASSOCIATE: só TCP CONNECT passa.
            raise Recusa("comando_nao_suportado", REP_COMANDO)
        return atyp, endereco, host, porta

    async def _dns_aponta_para_dentro(self, nome: str, porta: int) -> str | None:
        """Resolve AQUI só para conferir (o nome segue para o proxy da empresa,
        que resolve de novo lá). Nome que não resolve daqui segue em frente."""
        if not self.checar_dns:
            return None
        try:
            enderecos = await asyncio.wait_for(self._resolver(nome, porta), self.dns_s)
        except (TimeoutError, OSError, UnicodeError):
            return None
        for texto in enderecos:
            try:
                ip = ipaddress.ip_address(texto.split("%", 1)[0])
            except ValueError:
                continue
            if motivo_ip_bloqueado(ip, self.extras) is not None:
                return "nome_aponta_para_dentro"
        return None

    async def _resolver(self, nome: str, porta: int) -> list[str]:
        """DNS daqui (os testes trocam)."""
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(nome, porta, type=socket.SOCK_STREAM)
        return [str(i[4][0]) for i in infos]

    async def _abrir_upstream(self, host: str, porta: int):
        """Abre o TCP para o proxy da empresa (os testes trocam o destino)."""
        return await asyncio.open_connection(host, porta, limit=BUFFER)

    async def _conectar_upstream(self, cred: Credencial, atyp: int, endereco: bytes, porta: int):
        try:
            up_reader, up_writer = await asyncio.wait_for(
                self._abrir_upstream(cred.up_host, cred.up_porta), self.conectar_s
            )
        except (TimeoutError, OSError):
            log.warning("ipcel upstream_inalcancavel link=%s", cred.link_id)
            raise Recusa("upstream_inalcancavel", REP_REDE) from None
        try:
            await asyncio.wait_for(
                self._socks_upstream(up_reader, up_writer, cred, atyp, endereco, porta),
                self.conectar_s,
            )
        except BaseException:
            up_writer.transport.abort()
            raise
        return up_reader, up_writer

    async def _socks_upstream(self, r, w, cred: Credencial, atyp: int, endereco: bytes, porta: int):
        u = cred.up_usuario.encode()
        s = cred.up_senha.encode()
        if len(u) > 255 or len(s) > 255:
            raise Recusa("upstream_credencial_longa", REP_FALHA)
        saudacao = b"\x05\x01\x02"
        auth = b"\x01" + bytes([len(u)]) + u + bytes([len(s)]) + s
        pedido = b"\x05\x01\x00" + bytes([atyp]) + endereco + struct.pack(">H", porta)
        try:
            w.write(saudacao + auth + pedido if self.pipeline_upstream else saudacao)
            await w.drain()
            if await r.readexactly(2) != b"\x05\x02":
                log.error("ipcel upstream_sem_usuario_senha link=%s", cred.link_id)
                raise Recusa("upstream_metodo", REP_FALHA)
            if not self.pipeline_upstream:
                w.write(auth)
                await w.drain()
            status = await r.readexactly(2)
            if status[1] != 0x00:
                # O proxy da empresa recusou a senha real (ou o IP do servidor).
                log.error("ipcel upstream_recusou_senha link=%s", cred.link_id)
                raise Recusa("upstream_recusou_senha", REP_FALHA)
            if not self.pipeline_upstream:
                w.write(pedido)
                await w.drain()
            ver, rep, _rsv, tipo = await r.readexactly(4)
            if tipo == 0x01:
                await r.readexactly(4)
            elif tipo == 0x04:
                await r.readexactly(16)
            elif tipo == 0x03:
                (n,) = await r.readexactly(1)
                await r.readexactly(n)
            await r.readexactly(2)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            log.warning("ipcel upstream_caiu_na_negociacao link=%s", cred.link_id)
            raise Recusa("upstream_caiu", REP_FALHA) from None
        if ver != 5:
            raise Recusa("upstream_resposta_invalida", REP_FALHA)
        if rep != REP_OK:
            raise Recusa(f"upstream_destino_{rep}", rep if 0 < rep < 9 else REP_FALHA)

    async def _encaminhar(self, reader, writer, up_reader, up_writer, estado: EstadoLink, cred):
        loop = asyncio.get_running_loop()
        ultimo = [loop.time()]

        async def copiar(origem, destino, sentido: str) -> None:
            while True:
                pedaco = await origem.read(BUFFER)
                if not pedaco:
                    if destino.can_write_eof():
                        destino.write_eof()
                        await destino.drain()
                    return
                n = len(pedaco)
                if sentido == "sobe":
                    estado.sobe += n
                else:
                    estado.desce += n
                estado.total += n
                if estado.total >= estado.teto:
                    self._estourou_teto(estado)
                    raise Encerrar("teto_de_dados")
                await estado.balde.gastar(n)
                ultimo[0] = loop.time()
                destino.write(pedaco)
                await destino.drain()

        async def ocioso() -> None:
            while True:
                resta = self.ocioso_s - (loop.time() - ultimo[0])
                if resta <= 0:
                    raise Encerrar("ocioso")
                await asyncio.sleep(resta)

        async def prazo() -> None:
            await asyncio.sleep(max(0.0, cred.expira - time.time()))
            raise Encerrar("venceu")

        bombas = {
            asyncio.create_task(copiar(reader, up_writer, "sobe")),
            asyncio.create_task(copiar(up_reader, writer, "desce")),
        }
        guardas = {asyncio.create_task(ocioso()), asyncio.create_task(prazo())}
        try:
            while bombas:
                feitas, _ = await asyncio.wait(
                    bombas | guardas, return_when=asyncio.FIRST_COMPLETED
                )
                for t in feitas:
                    t.result()
                bombas.difference_update(feitas)
        finally:
            for t in bombas | guardas:
                t.cancel()
            await asyncio.gather(*bombas, *guardas, return_exceptions=True)

    # ------------------------------------------------------------ cortes

    def derrubar(self, link_id: str, motivo: str) -> int:
        estado = self._links.get(link_id)
        if estado is None:
            return 0
        tarefas = list(estado.conexoes)
        for t in tarefas:
            t.cancel()
        if tarefas:
            log.info("ipcel derrubou link=%s motivo=%s conexoes=%d", link_id, motivo, len(tarefas))
        return len(tarefas)

    def _estourou_teto(self, estado: EstadoLink) -> None:
        if estado.teto_estourado:
            return
        estado.teto_estourado = True
        log.warning("ipcel teto_de_dados link=%s", estado.link_id)

        async def _apagar() -> None:
            with contextlib.suppress(Exception):
                pipe = self.redis.pipeline(transaction=False)
                pipe.hset(self.k.met(estado.link_id), "teto", 1)
                pipe.delete(self.k.cred(estado.usuario))
                await pipe.execute()
            self.derrubar(estado.link_id, "teto_de_dados")

        t = asyncio.get_running_loop().create_task(_apagar())
        self._soltas.add(t)
        t.add_done_callback(self._soltas.discard)

    async def _ouvir_cortes(self) -> None:
        while not self._fechando:
            pubsub = self.redis.pubsub()
            try:
                await pubsub.subscribe(self.k.canal_cortar)
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    dado = msg.get("data")
                    if isinstance(dado, bytes):
                        dado = dado.decode(errors="replace")
                    self.derrubar(str(dado), "cortado")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Redis caiu: tenta de novo (a vigia cobre)
                log.warning("ipcel canal_de_corte_caiu")
                await asyncio.sleep(2)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def _descarregar_metricas(self) -> None:
        pipe = self.redis.pipeline(transaction=False)
        enviados: list[tuple[EstadoLink, int, int, int, int]] = []
        for estado in self._links.values():
            sobe, desce, aceitas, recusadas = (
                estado.sobe,
                estado.desce,
                estado.aceitas,
                estado.recusadas,
            )
            chave = self.k.met(estado.link_id)
            if sobe:
                pipe.hincrby(chave, "sobe", sobe)
            if desce:
                pipe.hincrby(chave, "desce", desce)
            if aceitas:
                pipe.hincrby(chave, "conexoes", aceitas)
            if recusadas:
                pipe.hincrby(chave, "recusadas", recusadas)
            pipe.hset(chave, mapping={"abertas": len(estado.conexoes), "visto": int(time.time())})
            pipe.expireat(chave, int(estado.expira) + 3600)
            enviados.append((estado, sobe, desce, aceitas, recusadas))
        if not enviados:
            return
        await pipe.execute()
        for estado, sobe, desce, aceitas, recusadas in enviados:
            estado.sobe -= sobe
            estado.desce -= desce
            estado.aceitas -= aceitas
            estado.recusadas -= recusadas

    async def _vigiar(self) -> None:
        while True:
            await asyncio.sleep(self.vigia_s)
            try:
                await self.vigiar_uma_vez()
                self._ultimo_redis_ok = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                fora = time.monotonic() - self._ultimo_redis_ok
                log.error("ipcel redis_fora etapa=vigia segundos=%.0f", fora)
                if fora > self.redis_fora_max_s:
                    # Falha fechada: sem como saber se foi cortado, derruba.
                    for link_id in list(self._links):
                        self.derrubar(link_id, "redis_fora")

    async def vigiar_uma_vez(self) -> None:
        """Derruba o que venceu ou foi cortado (caso a mensagem do canal tenha
        se perdido) e manda os números para o Redis."""
        agora = time.time()
        for link_id, estado in list(self._links.items()):
            if estado.conexoes:
                if agora >= estado.expira:
                    self.derrubar(link_id, "venceu")
                elif not await self.redis.exists(self.k.cred(estado.usuario)):
                    self.derrubar(link_id, "cortado")
        await self._descarregar_metricas()
        for link_id, estado in list(self._links.items()):
            if not estado.conexoes and agora >= estado.expira:
                self._links.pop(link_id, None)


def _resposta(rep: int) -> bytes:
    # BND.ADDR 0.0.0.0:0 — o celular não precisa (e não deve) saber o
    # endereço do proxy da empresa.
    return b"\x05" + bytes([rep]) + b"\x00\x01\x00\x00\x00\x00\x00\x00"


def _mesmo_ip(host, up_host: str) -> bool:
    try:
        a = host if not isinstance(host, str) else ipaddress.ip_address(host)
        return a == ipaddress.ip_address(up_host)
    except ValueError:
        return False


# ---------------------------------------------------------------- processo


async def rodar(args: argparse.Namespace) -> None:
    chave = os.environ.get("IPCEL_RELAY_KEY", "")
    redis_url = os.environ.get("REDIS_URL", "redis://redis7:6379/0")
    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    sinais = []
    for s in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(s, parar.set)
            sinais.append(s)
    try:
        try:
            cofre = Cofre(chave)
        except ValueError:
            # Sem a chave o recurso fica DESLIGADO: nem escuta a porta.
            log.error("ipcel desligado: falta IPCEL_RELAY_KEY (32+ caracteres)")
            await parar.wait()
            return
        # Pool com teto: 500 celulares autenticando juntos não abrem 500
        # conexões no Redis — esperam a vez (até 5 s; depois, recusa).
        pool = BlockingConnectionPool.from_url(
            redis_url, decode_responses=True, max_connections=32, timeout=5
        )
        redis = Redis(connection_pool=pool)
        relay = Relay(
            redis,
            cofre,
            prefixo=os.environ.get("IPCEL_PREFIXO", PREFIXO_PADRAO),
            bloquear=[os.environ.get("IPCEL_BLOQUEAR", "")],
            permitir_upstream_local=args.so_teste_upstream_local,
            pipeline_upstream=os.environ.get("IPCEL_PIPELINE_UPSTREAM", "") == "1",
        )
        servidor = await relay.iniciar(args.host, args.porta)
        log.info("ipcel escutando porta=%d", args.porta)
        try:
            async with servidor:
                await parar.wait()
        finally:
            servidor.close()
            await servidor.wait_closed()
            await relay.fechar()
            await redis.aclose()
            await pool.aclose()
    finally:
        for s in sinais:
            loop.remove_signal_handler(s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Intermediário SOCKS5 do IP da loja no celular")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - porta pública de propósito
    parser.add_argument(
        "--porta", type=int, default=int(os.environ.get("IPCEL_PORTA", PORTA_PADRAO))
    )
    parser.add_argument(
        "--so-teste-upstream-local",
        action="store_true",
        help="SÓ PARA TESTE LOCAL: aceita proxy da empresa em 127.0.0.1",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("IPCEL_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(rodar(args))


if __name__ == "__main__":
    main()
