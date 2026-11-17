"""Peças que a API e o intermediário SOCKS5 dividem (IP da loja no celular).

Este módulo NÃO importa `app.config` (nem nada que importe): o intermediário
roda com o mínimo de segredo — só `REDIS_URL` e `IPCEL_RELAY_KEY` — e o
Settings da API exige DATABASE_URL.

Como os dados ficam no Redis (prefixo `davinci:ipcel:`; tudo com validade):

- `cred:<usuario>`   a credencial temporária que o celular usa no SOCKS5.
                      Guarda só o RESUMO (HMAC) da senha temporária, a validade,
                      os limites e o proxy de verdade da empresa CIFRADO com a
                      chave do intermediário (amarrado ao usuário: não dá para
                      trocar o proxy de uma credencial pelo de outra). Validade
                      = o tempo escolhido. Apagar = cortar.
- `codigo:<codigo>`  o link curto de uso único. Guarda o usuário e a senha
                      temporária cifrados, para a página do celular mostrar
                      UMA vez (GETDEL). Vence em 10 minutos se ninguém abrir.
- `link:<link_id>`   o que a tela mostra (empresa, nome, quem gerou, prazos).
                      Não muda depois de criado (o que muda vai no `met:`).
- `met:<link_id>`    o que muda, campo a campo (HSET, nunca o cartão inteiro:
                      abrir e cortar ao mesmo tempo não se apagam): números que
                      o intermediário soma (bytes, conexões), `aberto_em`/
                      `aberto_ip` (página do iPhone), `cortado_em`/`cortado_por`
                      ("Cortar agora"), `preso_ip` (a credencial só vale do
                      primeiro IP que entrou) e `outro_ip*` (recusas de outro IP).
- `ver:<token>`      a página do iPhone depois de gastar o link: o POST guarda
                      aqui (cifrado) o que ela mostra e põe o token num cookie
                      só daquele endereço. Recarregar ou o iOS descartar a aba
                      não perde a página (sem o cookie, nada aparece).
- `usado:<usuario>`  marca de credencial que existiu (vale 1 dia a mais): o
                      iPhone que insiste com uma credencial vencida não conta
                      como tentativa de senha errada (senão o IP do celular
                      ficaria bloqueado na hora de importar o link novo).
- `ativos`           conjunto dos link_ids, para a lista da tela.
- canal `cortar`     "Cortar agora": o intermediário derruba na hora as
                      conexões abertas do link publicado aqui.

A senha REAL do proxy da empresa nunca vai para o celular, o link, a tela, a
resposta ou o log: ela só existe cifrada no Redis e decifrada dentro do
processo do intermediário.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import time
from collections.abc import Iterable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIXO_PADRAO = "davinci:ipcel:"
PORTA_PADRAO = 21080
TAMANHO_MINIMO_CHAVE = 32
# O link curto vence em 10 minutos se ninguém abrir.
CODIGO_VALIDADE_S = 10 * 60
# A marca `usado:` e o cartão da tela ficam um pouco além do fim.
SOBRA_USADO_S = 24 * 3600
SOBRA_LINK_S = 3600

_RX_USUARIO = re.compile(r"^[A-Za-z0-9]{8,64}$")
_RX_CODIGO = re.compile(r"^[A-Za-z0-9_-]{16,40}$")


class Chaves:
    """Nomes das chaves no Redis, todos com o mesmo prefixo."""

    def __init__(self, prefixo: str = PREFIXO_PADRAO) -> None:
        self.p = prefixo

    def cred(self, usuario: str) -> str:
        return f"{self.p}cred:{usuario}"

    def codigo(self, codigo: str) -> str:
        return f"{self.p}codigo:{codigo}"

    def link(self, link_id: str) -> str:
        return f"{self.p}link:{link_id}"

    def met(self, link_id: str) -> str:
        return f"{self.p}met:{link_id}"

    def usado(self, usuario: str) -> str:
        return f"{self.p}usado:{usuario}"

    def ver(self, token: str) -> str:
        return f"{self.p}ver:{token}"

    def pagina(self, ip: str) -> str:
        return f"{self.p}pagina:{ip}"

    @property
    def ativos(self) -> str:
        return f"{self.p}ativos"

    @property
    def canal_cortar(self) -> str:
        return f"{self.p}cortar"


def usuario_valido(usuario: str) -> bool:
    return bool(_RX_USUARIO.match(usuario or ""))


def codigo_valido(codigo: str) -> bool:
    return bool(_RX_CODIGO.match(codigo or ""))


def _derivar(chave: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"davinci-ipcel-v1", info=info).derive(
        chave
    )


class Cofre:
    """Cifra com a chave do intermediário (`IPCEL_RELAY_KEY`).

    É uma chave SÓ deste recurso, diferente da CREDENTIALS_KEY da API: se o
    intermediário (exposto na internet) for invadido, vaza no máximo o proxy
    dos links ainda válidos — não as senhas de certificados e integrações.
    """

    def __init__(self, chave: str) -> None:
        if not chave or len(chave) < TAMANHO_MINIMO_CHAVE:
            raise ValueError("ipcel_chave_curta")
        bruto = chave.encode()
        self._aes = AESGCM(_derivar(bruto, b"ipcel-aes"))
        self._mac = _derivar(bruto, b"ipcel-mac")

    def cifrar(self, dados: dict, contexto: str) -> str:
        nonce = os.urandom(12)
        claro = json.dumps(dados, separators=(",", ":")).encode()
        cifrado = self._aes.encrypt(nonce, claro, contexto.encode())
        return base64.urlsafe_b64encode(nonce + cifrado).decode()

    def decifrar(self, blob: str, contexto: str) -> dict:
        """Levanta ValueError se o blob foi mexido ou é de outro contexto."""
        try:
            bruto = base64.urlsafe_b64decode(blob.encode())
            if len(bruto) <= 12:
                raise ValueError("curto")
            claro = self._aes.decrypt(bruto[:12], bruto[12:], contexto.encode())
            dados = json.loads(claro)
        except Exception as e:  # noqa: BLE001 - InvalidTag, base64, json: tudo é "não vale"
            raise ValueError("ipcel_blob_invalido") from e
        if not isinstance(dados, dict):
            raise ValueError("ipcel_blob_invalido")
        return dados

    def resumo_senha(self, usuario: str, senha: str) -> str:
        msg = f"{usuario}\0{senha}".encode()
        return hmac.new(self._mac, msg, hashlib.sha256).hexdigest()

    def senha_confere(self, usuario: str, senha: str, resumo: str) -> bool:
        esperado = self.resumo_senha(usuario, senha).encode()
        return hmac.compare_digest(esperado, (resumo or "").encode())


def contexto_upstream(usuario: str) -> str:
    return f"up:{usuario}"


def contexto_codigo(codigo: str) -> str:
    return f"codigo:{codigo}"


def contexto_ver(token: str) -> str:
    return f"ver:{token}"


def chave_do_ip(ip: str) -> str:
    """Onde a credencial fica presa: o IPv4 exato; no IPv6, o /64 (o celular
    troca o fim do endereço sozinho). Texto que não é IP fica como veio."""
    try:
        x = ipaddress.ip_address((ip or "").split("%", 1)[0])
    except ValueError:
        return ip or "?"
    if isinstance(x, ipaddress.IPv6Address):
        if x.ipv4_mapped is not None:
            return str(x.ipv4_mapped)
        return str(ipaddress.ip_network(f"{x}/64", strict=False))
    return str(x)


def rede_do_ip(ip: str) -> str:
    """A faixa do IP para o limite de quem ainda não autenticou: /24 no IPv4,
    /48 no IPv6."""
    try:
        x = ipaddress.ip_address((ip or "").split("%", 1)[0])
    except ValueError:
        return ip or "?"
    if isinstance(x, ipaddress.IPv6Address) and x.ipv4_mapped is not None:
        x = x.ipv4_mapped
    prefixo = 24 if isinstance(x, ipaddress.IPv4Address) else 48
    return str(ipaddress.ip_network(f"{x}/{prefixo}", strict=False))


# ---------------------------------------------------------------- destinos

# Nomes que sempre apontam para dentro (da máquina ou da rede local).
_NOMES_INTERNOS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
}
_SUFIXOS_INTERNOS = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".home.arpa",
    ".corp",
    ".arpa",
)
_RX_NOME = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,62})(\.[a-z0-9_]([a-z0-9_-]{0,62}))*$")
# Portas que nunca saem: 0 não existe, 25 é SMTP (vira envio de spam pelo IP da
# loja e queima o IP).
PORTAS_BLOQUEADAS = frozenset({0, 25})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def redes_extras(textos: Iterable[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """'46.225.188.20, 10.0.0.0/8' → redes. Item inválido é ignorado."""
    redes: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for bruto in textos:
        for item in re.split(r"[\s,;]+", bruto or ""):
            if not item:
                continue
            try:
                redes.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
    return redes


def _ips_embutidos(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """O próprio IP e os IPv4 escondidos dentro de um IPv6 (mapeado, 6to4,
    Teredo, NAT64) — `::ffff:127.0.0.1` é o 127.0.0.1."""
    lista: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            lista.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            lista.append(ip.sixtofour)
        if ip.teredo is not None:
            lista.extend(ip.teredo)
        if ip in _NAT64:
            lista.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return lista


def motivo_ip_bloqueado(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    extras: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network] = (),
) -> str | None:
    """None = pode; senão o motivo. Vale para destino e para o proxy da empresa."""
    extras = list(extras)
    for x in _ips_embutidos(ip):
        if not x.is_global or x.is_multicast or x.is_unspecified or x.is_reserved:
            return "ip_interno"
        if any(x in rede for rede in extras):
            return "ip_do_servidor"
    return None


def _ipv4_disfarcado(nome: str) -> bool:
    """'127.1', '0x7f.1', '2130706433', '017700000001': o sistema aceita como
    IPv4 (inet_aton), mas não é um nome — sempre recusado. (Nome com byte NUL
    levanta ValueError no inet_aton: não é IPv4 — e o _RX_NOME recusa.)"""
    try:
        socket.inet_aton(nome)
    except (OSError, ValueError):
        return False
    return True


def normalizar_nome(bruto: bytes) -> str | None:
    """Nome do destino (ATYP 3) em minúsculas, sem o ponto final. None se não
    for um nome de site em ASCII."""
    try:
        nome = bruto.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return None
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in nome):
        return None  # NUL, espaço, controle: não é nome de site
    if nome.endswith("."):
        nome = nome[:-1]
    if nome.startswith("[") and nome.endswith("]"):
        nome = nome[1:-1]
    return nome or None


def motivo_destino_bloqueado(
    host: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
    porta: int,
    extras: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network] = (),
) -> str | None:
    """Confere o destino pedido pelo celular SEM resolver nome.

    - IP: precisa ser público (nada de 127/8, 10/8, 172.16/12, 192.168/16,
      169.254/16, ::1, fc00::/7, IPv4 escondido em IPv6...) e não pode ser um
      IP do próprio servidor (`extras`).
    - Nome: recusa localhost e afins, nomes sem ponto (os nomes do docker: api,
      redis7, pg18...), sufixos de rede local e IPv4 disfarçado de nome.
    O intermediário NUNCA conecta direto no destino (só no proxy da empresa),
    mas a regra protege também a máquina do proxy de virar porta para a rede
    dela.
    """
    if porta in PORTAS_BLOQUEADAS or not 0 < porta < 65536:
        return "porta_bloqueada"
    if isinstance(host, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return motivo_ip_bloqueado(host, extras)
    nome = host
    try:
        return motivo_ip_bloqueado(ipaddress.ip_address(nome), extras)
    except ValueError:
        pass
    if _ipv4_disfarcado(nome):
        return "ip_disfarcado"
    if nome in _NOMES_INTERNOS or nome.endswith(_SUFIXOS_INTERNOS):
        return "nome_interno"
    if "." not in nome:
        return "nome_sem_dominio"
    if len(nome) > 253 or not _RX_NOME.match(nome):
        return "nome_invalido"
    return None


def motivo_upstream_bloqueado(
    host: str,
    extras: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network] = (),
) -> str | None:
    """O proxy da empresa tem de ser um IP público e não pode ser o servidor:
    é a garantia de que o intermediário nunca abre conexão para dentro."""
    try:
        ip = ipaddress.ip_address((host or "").strip())
    except ValueError:
        return "upstream_nao_e_ip"
    return motivo_ip_bloqueado(ip, extras)


# ---------------------------------------------------------------- gravação


async def gravar_link(
    redis,
    chaves: Chaves,
    cofre: Cofre,
    *,
    link_id: str,
    codigo: str,
    usuario: str,
    senha: str,
    upstream: dict,
    expira: int,
    meta: dict,
    max_conexoes: int,
    banda_bytes_s: int,
    teto_bytes: int,
) -> None:
    """Grava a credencial, o código de uso único, o cartão e os números.

    `upstream` = {"host", "porta", "usuario", "senha"} do proxy REAL da
    empresa; vai cifrado e amarrado ao `usuario` temporário. Todas as chaves
    nascem com validade, então nada fica para trás se ninguém cortar.
    """
    agora = int(time.time())
    restante = expira - agora
    if restante <= 0:
        raise ValueError("ipcel_prazo_no_passado")
    cred = {
        "link": link_id,
        "empresa": str(meta.get("empresa_id") or ""),
        "resumo": cofre.resumo_senha(usuario, senha),
        "expira": expira,
        "up": cofre.cifrar(upstream, contexto_upstream(usuario)),
        "max_con": int(max_conexoes),
        "banda": int(banda_bytes_s),
        "teto": int(teto_bytes),
    }
    cod = {
        "link": link_id,
        "segredo": cofre.cifrar({"u": usuario, "s": senha}, contexto_codigo(codigo)),
    }
    cartao = {**meta, "link_id": link_id, "usuario": usuario, "codigo": codigo, "expira": expira}
    pipe = redis.pipeline(transaction=True)
    pipe.set(chaves.cred(usuario), json.dumps(cred), ex=restante)
    pipe.set(chaves.codigo(codigo), json.dumps(cod), ex=min(CODIGO_VALIDADE_S, restante))
    pipe.set(chaves.link(link_id), json.dumps(cartao), ex=restante + SOBRA_LINK_S)
    pipe.set(chaves.usado(usuario), link_id, ex=restante + SOBRA_USADO_S)
    pipe.hset(chaves.met(link_id), mapping={"sobe": 0, "desce": 0, "conexoes": 0, "abertas": 0})
    pipe.expire(chaves.met(link_id), restante + SOBRA_LINK_S)
    pipe.sadd(chaves.ativos, link_id)
    # O conjunto da lista também vence (bem depois do link mais longo, 4 h).
    pipe.expire(chaves.ativos, 2 * 86400)
    await pipe.execute()
