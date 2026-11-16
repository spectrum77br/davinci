"""Lado da API do "Usar este IP no celular" (16/11/2026).

Cria o link (credencial temporária amarrada a UMA empresa + link curto de uso
único), lista os links e corta. Quem leva o tráfego é o intermediário
(`app/ip_celular/relay.py`); aqui só se grava e se lê o Redis.

A senha real do proxy é decifrada aqui (CREDENTIALS_KEY) só para ser cifrada
de novo com a chave do intermediário (IPCEL_RELAY_KEY) e ir para o Redis com
validade. Ela não volta em resposta nenhuma, nem em log.
"""

from __future__ import annotations

import base64
import json
import secrets
import string
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import segno
import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ip_celular.comum import (
    Chaves,
    Cofre,
    codigo_valido,
    contexto_codigo,
    gravar_link,
    motivo_upstream_bloqueado,
)
from app.models import Company, User
from app.models.pricing import StoreInfo
from app.security.cipher import decrypt_bytes

logger = structlog.get_logger()

MINUTOS_PERMITIDOS = (15, 60, 240)
FUSO = ZoneInfo("America/Sao_Paulo")
_ALFABETO = string.ascii_letters + string.digits

# Como a plataforma aparece no nome do proxy no Shadowrocket.
_PLATAFORMAS = {
    "ml": "ML",
    "mercadolivre": "ML",
    "shopee": "Shopee",
    "amazon": "Amazon",
    "tiktok": "TikTok",
    "temu": "Temu",
    "magalu": "Magalu",
    "shein": "Shein",
    "aliexpress": "AliExpress",
    "carrefour": "Carrefour",
    "netshoes": "Netshoes",
    "site": "Site",
}


@dataclass
class Alvo:
    empresa: Company
    nome: str  # "<Loja>" do nome no Shadowrocket


def _erro(status: int, code: str, **extra) -> HTTPException:
    return HTTPException(status, detail={"code": code, **extra})


def chaves() -> Chaves:
    return Chaves(get_settings().ipcel_prefixo)


def cofre() -> Cofre:
    try:
        return Cofre(get_settings().ipcel_relay_key)
    except ValueError:
        raise _erro(503, "ipcel_desligado") from None


async def _redis():
    # Import tardio, como em security/senha_extra.py.
    from app.redis_client import redis

    return redis


def _norm(s: str | None) -> str:
    return "".join((s or "").split()).lower()


def hora(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, FUSO).strftime("%H:%M")


def _iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(float(epoch), UTC).isoformat()


# ---------------------------------------------------------------- alvo


async def resolver_alvo(
    session: AsyncSession, *, company_id: uuid.UUID | None, store_info_id: uuid.UUID | None
) -> Alvo:
    """Empresa (pela tela Empresas) ou loja (pela tela Lojas → empresa pelo
    apelido, a mesma ligação da grade e do serviço do AdsPower)."""
    if (company_id is None) == (store_info_id is None):
        raise _erro(422, "escolha_empresa_ou_loja")
    if company_id is not None:
        c = (
            await session.execute(select(Company).where(Company.id == company_id))
        ).scalar_one_or_none()
        if c is None:
            raise _erro(404, "company_not_found")
        return Alvo(empresa=c, nome=c.apelido)
    si = (
        await session.execute(select(StoreInfo).where(StoreInfo.id == store_info_id))
    ).scalar_one_or_none()
    if si is None:
        raise _erro(404, "loja_nao_encontrada")
    conta = _norm(si.account_name)
    if not conta:
        raise _erro(409, "loja_sem_empresa")
    todas = (await session.execute(select(Company))).scalars().all()
    empresas = [c for c in todas if _norm(c.apelido) == conta]
    if not empresas:
        raise _erro(409, "loja_sem_empresa")
    if len(empresas) > 1:
        # Duas empresas com o mesmo apelido: não dá para saber qual IP é.
        raise _erro(409, "empresa_ambigua")
    plat = _PLATAFORMAS.get((si.platform or "").strip().lower(), (si.platform or "").strip())
    nome_conta = (si.account_name or "").strip()
    if nome_conta.islower():
        nome_conta = nome_conta[:1].upper() + nome_conta[1:]
    return Alvo(empresa=empresas[0], nome=" ".join(x for x in (plat, nome_conta) if x))


def motivo_empresa_sem_ip_celular(c: Company) -> str | None:
    """Por que esta empresa não pode gerar link (None = pode)."""
    if not (c.ip or "").strip():
        return "empresa_sem_ip"
    if (c.proxy_tipo or "socks5") != "socks5":
        return "proxy_nao_socks5"
    if not c.proxy_configurado:
        # As 6 empresas com IP mas sem senha no DaVinci: a senha só existe no
        # AdsPower.
        return "proxy_sem_senha"
    if motivo_upstream_bloqueado(c.ip or "", ()) is not None:
        return "ip_nao_publico"
    return None


# ---------------------------------------------------------------- links


def _credenciais(host: str, porta: int) -> tuple[str, str]:
    """Usuário e senha temporários só com letras e números. Procura uma dupla
    cujo base64 de `usuario:senha@host:porta` não tenha '+', '/' nem '=' (o
    link do Shadowrocket fica sem nada para codificar)."""
    melhor: tuple[str, str] | None = None
    for _ in range(400):
        usuario = "dv" + "".join(secrets.choice(_ALFABETO) for _ in range(14))
        senha = "".join(secrets.choice(_ALFABETO) for _ in range(28 + secrets.randbelow(6)))
        b64 = base64.b64encode(f"{usuario}:{senha}@{host}:{porta}".encode()).decode()
        if not any(ch in b64 for ch in "+/="):
            return usuario, senha
        melhor = melhor or (usuario, senha)
    assert melhor is not None
    return melhor


def host_publico() -> str:
    s = get_settings()
    return s.ipcel_host_publico.strip() or (urllib.parse.urlparse(s.app_url).hostname or "")


def url_do_codigo(codigo: str) -> str:
    s = get_settings()
    base = (s.ipcel_link_base or s.app_url).rstrip("/")
    return f"{base}/api/ipcel/{codigo}"


def qr_data_uri(texto: str) -> str:
    return segno.make(texto, error="m").svg_data_uri(scale=6, border=2)


async def _ler_links(r, k: Chaves) -> list[dict]:
    ids = sorted(await r.smembers(k.ativos))
    if not ids:
        return []
    brutos = await r.mget([k.link(i) for i in ids])
    links: list[dict] = []
    sumidos: list[str] = []
    for i, bruto in zip(ids, brutos, strict=True):
        if not bruto:
            sumidos.append(i)
            continue
        try:
            links.append(json.loads(bruto))
        except ValueError:
            sumidos.append(i)
    if sumidos:
        await r.srem(k.ativos, *sumidos)
    return links


async def _situacao(r, k: Chaves, cartao: dict) -> dict:
    """O cartão como a tela mostra: status + números. Sem usuário/código."""
    link_id = cartao["link_id"]
    met = await r.hgetall(k.met(link_id)) or {}
    agora = time.time()
    expira = float(cartao.get("expira") or 0)
    if cartao.get("cortado_em"):
        status = "cortado"
    elif agora >= expira:
        status = "vencido"
    elif met.get("teto"):
        status = "limite_de_dados"
    elif cartao.get("aberto_em"):
        status = "em_uso"
    elif await r.exists(k.codigo(cartao["codigo"])):
        status = "aguardando"
    else:
        status = "nao_aberto"
    return {
        "link_id": link_id,
        "empresa_id": cartao.get("empresa_id"),
        "empresa": cartao.get("empresa"),
        "nome": cartao.get("nome"),
        "status": status,
        "minutos": cartao.get("minutos"),
        "criado_em": _iso(cartao.get("criado_em")),
        "criado_por": cartao.get("criado_por_nome"),
        "expira_em": _iso(expira),
        "ate": hora(expira) if expira else None,
        "aberto_em": _iso(cartao.get("aberto_em")),
        "cortado_em": _iso(cartao.get("cortado_em")),
        "link_vence_em": _iso(cartao.get("link_vence_em")),
        "conexoes": int(met.get("conexoes") or 0),
        "abertas": int(met.get("abertas") or 0) if status == "em_uso" else 0,
        "recusadas": int(met.get("recusadas") or 0),
        "bytes_sobe": int(met.get("sobe") or 0),
        "bytes_desce": int(met.get("desce") or 0),
    }


def _valendo(cartao: dict, agora: float) -> bool:
    return not cartao.get("cortado_em") and float(cartao.get("expira") or 0) > agora


async def criar(session: AsyncSession, alvo: Alvo, minutos: int, por: User) -> dict:
    if minutos not in MINUTOS_PERMITIDOS:
        raise _erro(422, "tempo_invalido")
    s = get_settings()
    cf = cofre()
    c = alvo.empresa
    motivo = motivo_empresa_sem_ip_celular(c)
    if motivo is not None:
        raise _erro(409, motivo)
    host = host_publico()
    if not host:
        raise _erro(503, "ipcel_sem_host")
    r = await _redis()
    k = chaves()
    agora = time.time()
    valendo = [x for x in await _ler_links(r, k) if _valendo(x, agora)]
    if len(valendo) >= s.ipcel_max_links_total:
        raise _erro(409, "muitos_links")
    if sum(1 for x in valendo if x.get("empresa_id") == str(c.id)) >= s.ipcel_max_links_empresa:
        raise _erro(409, "muitos_links_empresa")
    try:
        senha_real = decrypt_bytes(c.proxy_senha_enc).decode()
    except Exception as e:  # noqa: BLE001 - blob corrompido/chave errada
        logger.error("ipcel_senha_proxy_ilegivel", company=c.apelido)
        raise _erro(500, "decrypt_failed") from e

    link_id = uuid.uuid4().hex[:12]
    codigo = secrets.token_urlsafe(12)
    usuario, senha = _credenciais(host, s.ipcel_porta_publica)
    expira = int(agora) + minutos * 60
    nome = f"{alvo.nome} · até {hora(expira)}"
    link_vence = min(expira, int(agora) + 600)
    meta = {
        "empresa_id": str(c.id),
        "empresa": c.apelido,
        "nome": nome,
        "ip": (c.ip or "").strip(),
        "minutos": minutos,
        "criado_em": int(agora),
        "criado_por": str(por.id),
        "criado_por_nome": por.name or por.email,
        "link_vence_em": link_vence,
    }
    await gravar_link(
        r,
        k,
        cf,
        link_id=link_id,
        codigo=codigo,
        usuario=usuario,
        senha=senha,
        upstream={
            "host": (c.ip or "").strip(),
            "porta": int(c.proxy_porta or 0),
            "usuario": c.proxy_usuario,
            "senha": senha_real,
        },
        expira=expira,
        meta=meta,
        max_conexoes=s.ipcel_max_conexoes,
        banda_bytes_s=s.ipcel_banda_bytes_s,
        teto_bytes=s.ipcel_teto_bytes,
    )
    del senha_real
    logger.info(
        "ipcel_link_criado",
        link=link_id,
        company=c.apelido,
        minutos=minutos,
        by=str(por.id),
    )
    cartao = json.loads(await r.get(k.link(link_id)))
    saida = await _situacao(r, k, cartao)
    url = url_do_codigo(codigo)
    return {**saida, "url": url, "qr": qr_data_uri(url)}


async def listar(*, company_id: str | None = None) -> list[dict]:
    r = await _redis()
    k = chaves()
    itens = [await _situacao(r, k, x) for x in await _ler_links(r, k)]
    if company_id:
        itens = [x for x in itens if x["empresa_id"] == company_id]
    # Valendo primeiro, depois os que acabaram; mais novos em cima.
    ordem = {"em_uso": 0, "aguardando": 1, "nao_aberto": 2}
    itens.sort(key=lambda x: x["criado_em"] or "", reverse=True)
    itens.sort(key=lambda x: ordem.get(x["status"], 3))
    return itens


async def _cartao(r, k: Chaves, link_id: str) -> dict:
    if not link_id.isalnum() or len(link_id) > 40:
        raise _erro(404, "link_nao_encontrado")
    bruto = await r.get(k.link(link_id))
    if not bruto:
        raise _erro(404, "link_nao_encontrado")
    return json.loads(bruto)


async def ver_qr(link_id: str) -> dict:
    """O QR de novo (janela fechada sem querer) — só enquanto ninguém abriu."""
    r = await _redis()
    k = chaves()
    cartao = await _cartao(r, k, link_id)
    situacao = await _situacao(r, k, cartao)
    if situacao["status"] != "aguardando":
        raise _erro(409, "link_nao_esta_aguardando", situacao=situacao["status"])
    url = url_do_codigo(cartao["codigo"])
    return {**situacao, "url": url, "qr": qr_data_uri(url)}


async def cortar(link_id: str, por: User) -> dict:
    r = await _redis()
    k = chaves()
    cartao = await _cartao(r, k, link_id)
    if not cartao.get("cortado_em"):
        cartao["cortado_em"] = int(time.time())
        cartao["cortado_por"] = str(por.id)
    ttl = await r.ttl(k.link(link_id))
    pipe = r.pipeline(transaction=True)
    pipe.delete(k.cred(cartao["usuario"]))
    pipe.delete(k.codigo(cartao["codigo"]))
    pipe.set(k.link(link_id), json.dumps(cartao), ex=ttl if ttl and ttl > 0 else 3600)
    # O intermediário derruba na hora as conexões abertas deste link.
    pipe.publish(k.canal_cortar, link_id)
    await pipe.execute()
    logger.info("ipcel_link_cortado", link=link_id, company=cartao.get("empresa"), by=str(por.id))
    return await _situacao(r, k, cartao)


# ---------------------------------------------------------------- página do celular


async def ver_codigo(codigo: str) -> dict | None:
    """Para o GET da página (prévia do WhatsApp/iMessage também faz GET): só
    olha, NÃO gasta o link."""
    if not codigo_valido(codigo):
        return None
    r = await _redis()
    k = chaves()
    bruto = await r.get(k.codigo(codigo))
    if not bruto:
        return None
    try:
        cod = json.loads(bruto)
        cartao = await _cartao(r, k, str(cod["link"]))
    except (ValueError, KeyError, HTTPException):
        return None
    if not _valendo(cartao, time.time()):
        return None
    return {"nome": cartao.get("nome"), "ate": hora(float(cartao["expira"]))}


async def abrir_codigo(codigo: str, *, ip: str | None) -> dict | None:
    """O POST da página: GASTA o link (GETDEL) e devolve o que o Shadowrocket
    precisa — a credencial TEMPORÁRIA. None = inválido, vencido ou já usado."""
    if not codigo_valido(codigo):
        return None
    r = await _redis()
    k = chaves()
    bruto = await r.getdel(k.codigo(codigo))
    if not bruto:
        return None
    try:
        cod = json.loads(bruto)
        cartao = await _cartao(r, k, str(cod["link"]))
        segredo = cofre().decifrar(cod["segredo"], contexto_codigo(codigo))
    except (ValueError, KeyError, HTTPException):
        return None
    if not _valendo(cartao, time.time()):
        return None
    cartao["aberto_em"] = int(time.time())
    ttl = await r.ttl(k.link(cartao["link_id"]))
    await r.set(k.link(cartao["link_id"]), json.dumps(cartao), ex=ttl if ttl and ttl > 0 else 3600)
    logger.info("ipcel_link_aberto", link=cartao["link_id"], company=cartao.get("empresa"), ip=ip)
    return {
        "usuario": segredo["u"],
        "senha": segredo["s"],
        "host": host_publico(),
        "porta": get_settings().ipcel_porta_publica,
        "nome": cartao.get("nome") or "DaVinci",
        "ate": hora(float(cartao["expira"])),
        "ip": cartao.get("ip"),
    }


def links_shadowrocket(d: dict) -> dict:
    """Os formatos de importação (ver docs/ip-celular.md).

    Nativo do Shadowrocket: `socks://BASE64(usuario:senha@host:porta)?remarks=`.
    O botão usa `shadowrocket://add/<link>`; como não está documentado se o
    link de dentro vai cru ou codificado, a página oferece os dois.
    """
    bruto = f"{d['usuario']}:{d['senha']}@{d['host']}:{d['porta']}"
    b64 = base64.b64encode(bruto.encode()).decode()
    nome = urllib.parse.quote(d["nome"], safe="")
    socks = f"socks://{b64}?remarks={nome}"
    return {
        "socks": socks,
        "add": f"shadowrocket://add/{socks}",
        "add_codificado": "shadowrocket://add/" + urllib.parse.quote(socks, safe=""),
        "texto": f"socks5://{d['usuario']}:{d['senha']}@{d['host']}:{d['porta']}#{nome}",
        "selecionar": "shadowrocket://select?s=" + nome,
        "modo_proxy": "shadowrocket://route/proxy",
        "ligar": "shadowrocket://connect",
        "desligar": "shadowrocket://disconnect",
    }
