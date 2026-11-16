"""Lado da API do "Usar este IP no celular" (16/11/2026).

Cria o link (credencial temporária amarrada a UMA empresa + link curto de uso
único), lista os links e corta. Quem leva o tráfego é o intermediário
(`app/ip_celular/relay.py`); aqui só se grava e se lê o Redis.

A senha real do proxy é decifrada aqui (CREDENTIALS_KEY) só para ser cifrada
de novo com a chave do intermediário (IPCEL_RELAY_KEY) e ir para o Redis com
validade. Ela não volta em resposta nenhuma, nem em log.

O Redis é o PRÓPRIO do recurso (`IPCEL_REDIS_URL` → `ipcel_redis` no docker
compose): o intermediário, exposto na internet, só alcança esse. Vazio (testes,
máquina local) = o Redis principal.
"""

from __future__ import annotations

import base64
import hashlib
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
    contexto_ver,
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
# O link que venceu sem abrir só é dado como "não aberto" (e a credencial
# apagada) um pouco depois do fim: o GETDEL do iPhone e a marca `aberto_em`
# não são o mesmo comando.
FOLGA_NAO_ABERTO_S = 30

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


_redis_proprio = None


async def _redis():
    """O Redis do recurso (IPCEL_REDIS_URL); vazio = o principal."""
    global _redis_proprio
    url = get_settings().ipcel_redis_url.strip()
    if not url:
        # Import tardio, como em security/senha_extra.py.
        from app.redis_client import redis

        return redis
    if _redis_proprio is None:
        from redis.asyncio import from_url

        _redis_proprio = from_url(url, decode_responses=True)
    return _redis_proprio


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


def _status(cartao: dict, met: dict, codigo_existe: bool, agora: float) -> str:
    """O que muda vem do `met:` (campo a campo): abrir e cortar ao mesmo tempo
    não se apagam, e cortado sempre vence."""
    expira = float(cartao.get("expira") or 0)
    if met.get("cortado_em"):
        return "cortado"
    if agora >= expira:
        return "vencido"
    if met.get("teto"):
        return "limite_de_dados"
    if met.get("aberto_em"):
        return "em_uso"
    if codigo_existe:
        return "aguardando"
    vence = float(cartao.get("link_vence_em") or expira)
    if agora < vence + FOLGA_NAO_ABERTO_S:
        return "aguardando"  # o iPhone pode estar abrindo agora
    return "nao_aberto"


async def _situacao(r, k: Chaves, cartao: dict, *, por: User | None = None) -> dict:
    """O cartão como a tela mostra: status + números. Sem usuário/código.

    Link que venceu SEM ninguém abrir: a credencial é apagada aqui (ninguém
    tem a senha dela) e ele deixa de contar no limite de links."""
    link_id = cartao["link_id"]
    met = await r.hgetall(k.met(link_id)) or {}
    agora = time.time()
    expira = float(cartao.get("expira") or 0)
    existe = bool(await r.exists(k.codigo(cartao["codigo"])))
    status = _status(cartao, met, existe, agora)
    if status == "nao_aberto":
        await r.delete(k.cred(cartao["usuario"]))
    return {
        "link_id": link_id,
        "empresa_id": cartao.get("empresa_id"),
        "empresa": cartao.get("empresa"),
        "nome": cartao.get("nome"),
        "status": status,
        "minutos": cartao.get("minutos"),
        "criado_em": _iso(cartao.get("criado_em")),
        "criado_por": cartao.get("criado_por_nome"),
        "meu": por is not None and cartao.get("criado_por") == str(por.id),
        "expira_em": _iso(expira),
        "ate": hora(expira) if expira else None,
        "aberto_em": _iso(met.get("aberto_em")),
        "aberto_as": hora(float(met["aberto_em"])) if met.get("aberto_em") else None,
        "aberto_ip": met.get("aberto_ip") or None,
        "preso_ip": met.get("preso_ip") or None,
        "outro_ip": met.get("outro_ip") or None,
        "outro_ip_as": hora(float(met["outro_ip_em"])) if met.get("outro_ip_em") else None,
        "outro_ip_n": int(met.get("outro_ip_n") or 0),
        "cortado_em": _iso(met.get("cortado_em")),
        "link_vence_em": _iso(cartao.get("link_vence_em")),
        "conexoes": int(met.get("conexoes") or 0),
        "abertas": int(met.get("abertas") or 0) if status == "em_uso" else 0,
        "recusadas": int(met.get("recusadas") or 0),
        "bytes_sobe": int(met.get("sobe") or 0),
        "bytes_desce": int(met.get("desce") or 0),
    }


# Contam no limite de links (por empresa e no total): os que ainda podem ser
# usados. "Venceu sem abrir" não conta (a credencial foi apagada).
_CONTA_NO_LIMITE = {"aguardando", "em_uso"}


def _valendo(cartao: dict, met: dict, agora: float) -> bool:
    return not met.get("cortado_em") and float(cartao.get("expira") or 0) > agora


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
    situacoes = [await _situacao(r, k, x) for x in await _ler_links(r, k)]
    valendo = [x for x in situacoes if x["status"] in _CONTA_NO_LIMITE]
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
    saida = await _situacao(r, k, cartao, por=por)
    url = url_do_codigo(codigo)
    return {**saida, "url": url, "qr": qr_data_uri(url)}


async def listar(*, company_id: str | None = None, por: User | None = None) -> list[dict]:
    r = await _redis()
    k = chaves()
    itens = [await _situacao(r, k, x, por=por) for x in await _ler_links(r, k)]
    if company_id:
        itens = [x for x in itens if x["empresa_id"] == company_id]
    # Valendo primeiro, depois os que acabaram; mais novos em cima.
    ordem = {"em_uso": 0, "aguardando": 1}
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


async def ver_qr(link_id: str, por: User) -> dict:
    """O QR de novo (janela fechada sem querer) — só para QUEM GEROU e só
    enquanto ninguém abriu. Outro admin não pega o link de um colega para
    gastar primeiro. Fica no Histórico (nomes.REVELACOES)."""
    r = await _redis()
    k = chaves()
    cartao = await _cartao(r, k, link_id)
    if cartao.get("criado_por") != str(por.id):
        raise _erro(403, "so_quem_gerou")
    situacao = await _situacao(r, k, cartao, por=por)
    if situacao["status"] != "aguardando" or not await r.exists(k.codigo(cartao["codigo"])):
        raise _erro(409, "link_nao_esta_aguardando", situacao=situacao["status"])
    url = url_do_codigo(cartao["codigo"])
    return {**situacao, "url": url, "qr": qr_data_uri(url)}


async def cortar(link_id: str, por: User) -> dict:
    r = await _redis()
    k = chaves()
    cartao = await _cartao(r, k, link_id)
    pipe = r.pipeline(transaction=True)
    # Campo a campo (HSETNX): o primeiro corte fica, e a página do iPhone
    # gravando `aberto_em` ao mesmo tempo não apaga o corte.
    pipe.hsetnx(k.met(link_id), "cortado_em", int(time.time()))
    pipe.hsetnx(k.met(link_id), "cortado_por", str(por.id))
    pipe.delete(k.cred(cartao["usuario"]))
    pipe.delete(k.codigo(cartao["codigo"]))
    # O intermediário derruba na hora as conexões abertas deste link.
    pipe.publish(k.canal_cortar, link_id)
    await pipe.execute()
    logger.info("ipcel_link_cortado", link=link_id, company=cartao.get("empresa"), by=str(por.id))
    return await _situacao(r, k, cartao, por=por)


async def soltar_ip(link_id: str, por: User) -> dict:
    """A credencial fica presa ao primeiro IP que entrou. O celular trocou de
    rede (Wi-Fi ↔ 4G): soltar deixa o PRÓXIMO IP que entrar ficar com ela."""
    r = await _redis()
    k = chaves()
    cartao = await _cartao(r, k, link_id)
    met = await r.hgetall(k.met(link_id)) or {}
    if not _valendo(cartao, met, time.time()):
        raise _erro(409, "link_nao_vale_mais")
    await r.hdel(k.met(link_id), "preso_ip", "preso_em", "outro_ip", "outro_ip_em", "outro_ip_n")
    logger.info("ipcel_ip_solto", link=link_id, company=cartao.get("empresa"), by=str(por.id))
    return await _situacao(r, k, cartao, por=por)


# ---------------------------------------------------------------- página do celular


def _resumo_codigo(codigo: str) -> str:
    return hashlib.sha256(f"ipcel:{codigo}".encode()).hexdigest()


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
    met = await r.hgetall(k.met(cartao["link_id"])) or {}
    if not _valendo(cartao, met, time.time()):
        return None
    return {"nome": cartao.get("nome"), "ate": hora(float(cartao["expira"]))}


async def abrir_codigo(codigo: str, *, ip: str | None) -> tuple[str, int] | None:
    """O POST da página: GASTA o link (GETDEL) e guarda o que a página do
    iPhone mostra (a credencial TEMPORÁRIA, cifrada) sob um token novo, que
    vai num cookie só deste endereço. Devolve (token, segundos) ou None
    (inválido, vencido, já usado ou cortado)."""
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
        cf = cofre()
        segredo = cf.decifrar(cod["segredo"], contexto_codigo(codigo))
    except (ValueError, KeyError, HTTPException):
        return None
    link_id = cartao["link_id"]
    agora = time.time()
    # Campo a campo: não apaga um corte que chegue junto.
    pipe = r.pipeline(transaction=True)
    pipe.hset(k.met(link_id), mapping={"aberto_em": int(agora), "aberto_ip": (ip or "?")[:64]})
    pipe.hgetall(k.met(link_id))
    pipe.exists(k.cred(cartao["usuario"]))
    _, met, cred_existe = await pipe.execute()
    if not _valendo(cartao, met or {}, agora) or not cred_existe:
        return None
    restante = int(float(cartao["expira"]) - agora)
    if restante <= 0:
        return None
    token = secrets.token_urlsafe(24)
    ver = {
        "link": link_id,
        "codigo": _resumo_codigo(codigo),
        "segredo": cf.cifrar({"u": segredo["u"], "s": segredo["s"]}, contexto_ver(token)),
    }
    await r.set(k.ver(token), json.dumps(ver), ex=restante)
    # A trilha: quem abriu (IP) fica no cartão da tela e aqui.
    logger.info("ipcel_link_aberto", link=link_id, company=cartao.get("empresa"), ip=ip)
    return token, restante


async def pagina_aberta(token: str | None, codigo: str) -> dict | None:
    """O que a página do iPhone mostra depois de gastar o link (recarregar,
    aba descartada pelo iOS, voltar). Só com o cookie DESTE iPhone.

    None = sem cookie válido para este código. {"acabou": status} = o link
    não vale mais (venceu/cortado/limite): a página mostra só o "Desligar".
    Senão: a credencial temporária e o resto, como na primeira vez."""
    if not token or len(token) > 64 or not codigo_valido(codigo):
        return None
    r = await _redis()
    k = chaves()
    bruto = await r.get(k.ver(token))
    if not bruto:
        return None
    try:
        ver = json.loads(bruto)
        if not secrets.compare_digest(str(ver.get("codigo", "")), _resumo_codigo(codigo)):
            return None
        cartao = await _cartao(r, k, str(ver["link"]))
    except (ValueError, KeyError, TypeError, HTTPException):
        return None
    met = await r.hgetall(k.met(cartao["link_id"])) or {}
    agora = time.time()
    status = _status(cartao, met, False, agora)
    if status not in ("em_uso", "aguardando") or not await r.exists(k.cred(cartao["usuario"])):
        return {"acabou": status, "nome": cartao.get("nome")}
    try:
        segredo = cofre().decifrar(ver["segredo"], contexto_ver(token))
    except (ValueError, KeyError, HTTPException):
        return None
    return {
        "usuario": segredo["u"],
        "senha": segredo["s"],
        "host": host_publico(),
        "porta": get_settings().ipcel_porta_publica,
        "nome": cartao.get("nome") or "DaVinci",
        "ate": hora(float(cartao["expira"])),
        "ip": cartao.get("ip"),
    }


def url_do_modulo() -> str:
    s = get_settings()
    base = (s.ipcel_link_base or s.app_url).rstrip("/")
    return f"{base}/api/ipcel/{NOME_MODULO}"


NOME_MODULO = "davinci-sem-vazamento.sgmodule"
# Módulo do Shadowrocket (sem segredo nenhum, o mesmo para todos). Só
# parâmetros gerais, sem regras: vale também no Roteamento global = Proxy.
#  - udp-policy-not-supported-behaviour = REJECT: o nosso proxy não leva UDP;
#    sem isto o UDP (QUIC, WebRTC) pode sair DIRETO, pelo IP do celular.
#  - block-quic = all-proxy: o HTTP/3 (QUIC) de quem vai pelo proxy é
#    bloqueado; o app volta na hora para o TCP, que passa pelo proxy.
# Desativar STUN não tem parâmetro: é na tela (Ajustes › UDP), a página pede.
MODULO = """#!name=DaVinci - IP da loja sem vazamento
#!desc=UDP e QUIC nao saem pelo IP do celular quando o proxy nao leva UDP.
[General]
udp-policy-not-supported-behaviour = REJECT
block-quic = all-proxy
"""


def links_shadowrocket(d: dict) -> dict:
    """Os formatos de importação (ver docs/ip-celular.md).

    Nativo do Shadowrocket: `socks://BASE64(usuario:senha@host:porta)?remarks=`.
    O botão usa `shadowrocket://add/<link>`; como não está documentado se o
    link de dentro vai cru ou codificado, a página oferece os dois. Mais dois
    formatos de reserva: o dos geradores estilo v2rayN/Sub-Store
    (`socks://BASE64(usuario:senha)@host:porta#nome`) e o `socks5://` cru.
    """
    bruto = f"{d['usuario']}:{d['senha']}@{d['host']}:{d['porta']}"
    b64 = base64.b64encode(bruto.encode()).decode()
    so_cred = base64.b64encode(f"{d['usuario']}:{d['senha']}".encode()).decode()
    nome = urllib.parse.quote(d["nome"], safe="")
    socks = f"socks://{b64}?remarks={nome}"
    modulo = url_do_modulo()
    return {
        "socks": socks,
        "add": f"shadowrocket://add/{socks}",
        "add_codificado": "shadowrocket://add/" + urllib.parse.quote(socks, safe=""),
        "socks_v2": (
            f"socks://{urllib.parse.quote(so_cred, safe='')}@{d['host']}:{d['porta']}#{nome}"
        ),
        "texto": f"socks5://{d['usuario']}:{d['senha']}@{d['host']}:{d['porta']}#{nome}",
        "modulo": modulo,
        "instalar_modulo": "shadowrocket://install?module=" + urllib.parse.quote(modulo, safe=""),
        "selecionar": "shadowrocket://select?s=" + nome,
        "modo_proxy": "shadowrocket://route/proxy",
        "ligar": "shadowrocket://connect",
        "desligar": "shadowrocket://disconnect",
    }
