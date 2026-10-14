"""A FICHA DO ANÚNCIO buscada NA LOJA, sob demanda (14/10/2026).

O dono: "quero que ela use as fichas; se necessário acessa o produto na loja
pra pegar os dados". Até aqui a ficha só vinha do que a loja JÁ tinha
devolvido ao enriquecimento do cartão (Redis) ou do catálogo recente
(`ficha.py`). Sem nenhum dos dois, na hora de gerar a sugestão (o cron da IA
e o "Sugerir" da tela), este módulo vai à loja buscar o anúncio:

  - Shopee: `get_item_base_info` (o MESMO método do enriquecimento: título,
    descrição — a normal ou a "estendida" — e `attribute_list`);
  - Mercado Livre: `GET /items/{id}` (título, `attributes`) e
    `GET /items/{id}/description` (`plain_text`) — só anúncio DESTA conta
    (`seller_id` = o `user_id` da credencial; sem `user_id`, não vai). A
    ficha fresca que o enriquecimento guarda do ML não tem descrição (o
    `/items` não traz o texto): aí só a descrição é buscada, UMA vez, e entra
    na mesma chave (`descricao_buscada`), com o tempo que a chave ainda tinha;
  - TikTok: `GET /product/202309/products/{id}` (título, `description` em
    HTML, `product_attributes`, `brand`), assinado com o shop_cipher DA LOJA
    DA CONVERSA — produto de outra loja volta erro.
  - Magalu: não. O portfólio da API (`/seller/v1/portfolios/skus`) é
    catálogo puro (SKU, título, status), sem descrição nem atributo — a
    ficha continua vindo do mesmo produto em outro anúncio (`ficha.py`).
  - Amazon: não. A conversa chega por e-mail, sem anúncio ligado.
  - Temu, AliExpress, redes, site: sem API de anúncio no DaVinci.

O que vem da loja é guardado do MESMO jeito e na MESMA chave da ficha fresca
do enriquecimento (`ficha.chave_ficha`, `ficha.CACHE_FICHA_S`, o formato de
`ficha.ficha_bruta_*`), com `buscada_em` — um aproveita a busca do outro — e
passa pela MESMA limpeza e pelo mesmo teto (`ficha.montar_ficha`/`caber`).

TOKEN. O cliente é o de `clientes.cliente_da_integracao` — o MESMO do sync e
do enriquecimento: a renovação passa pela trava por integração e o token
novo é gravado numa sessão própria. Nada aqui chama `refresh`. Refresh token
da Shopee e do ML é de USO ÚNICO; por isso a busca roda numa tarefa própria
e BLINDADA (como o cartão "Cliente", `cliente.py`): estourou o tempo, a
sugestão segue sem a ficha e a busca termina em segundo plano (e grava o
cache) — cancelar no meio poderia matar uma renovação em andamento.

QUANDO VAI (todas as travas, nesta ordem; qualquer uma → segue sem ficha):
  - leitura das lojas ligada (`atendimento_leitura_ativa`), plataforma com
    busca, conversa com loja;
  - no máximo `MAX_BUSCAS_POR_SUGESTAO` idas por sugestão (`Busca`);
  - sem cache negativo: a loja disse que o anúncio não existe / não é desta
    conta, ou não tem ficha (`CACHE_SEM_FICHA_S`, 6 h); a última ida falhou
    (`CACHE_FALHA_S`, 15 min). A ida só da descrição (a fresca do ML) tem
    o negativo DELA (`chave_sem_descricao`): não trava a busca inteira
    quando a fresca vencer;
  - a cota da RODADA do cron (`rodada()`): até `MAX_BUSCAS_POR_RODADA` idas
    por loja, e até `MAX_ESPERA_POR_RODADA_S` de espera somada (a rodada
    também chama o modelo, dentro do tempo do job);
  - loja não arquivada e canal não `desligado` (o interruptor do 429);
  - uma busca por anúncio de cada vez (SET NX) e um teto por loja por
    minuto (`MAX_BUSCAS_POR_MINUTO`, para o "Sugerir" e vários processos);
    sem Redis não vai (sem trava nem cache, cada sugestão iria de novo).

FALHA NUNCA DERRUBA A SUGESTÃO: erro, tempo estourado e loja recusando viram
"sem ficha" (como antes) e log só com a plataforma e o código. SÓ LEITURA:
nada aqui escreve na loja nem marca conversa como lida. Nada do comprador
vai para a loja nem para o log.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, Integration
from app.models.enums import IntegrationPlatform
from app.redis_client import redis
from app.services.atendimento import clientes
from app.services.atendimento import ficha as ficha_svc

logger = structlog.get_logger()

# ── Tetos ─────────────────────────────────────────────────────────────────

# Quanto a sugestão espera por UM anúncio (a Shopee e a TikTok fazem uma
# chamada; o ML, até duas). Passou disto, segue sem a ficha — a busca termina
# em segundo plano e deixa o cache pronto para a próxima.
TEMPO_MAX_BUSCA_S = 6.0
# O mesmo teto de anúncios da ficha (`ficha.MAX_ANUNCIOS_FICHA`).
MAX_BUSCAS_POR_SUGESTAO = 2
# Por loja: numa rodada do cron da IA, e por minuto (qualquer caminho).
MAX_BUSCAS_POR_RODADA = 10
MAX_BUSCAS_POR_MINUTO = 10
# A espera SOMADA pelas lojas numa rodada do cron (todas as lojas): o pior
# caso (10 conversas × 2 anúncios × 6 s) iria a 120 s dos 300 s do job.
# Passou disto, o resto da rodada segue sem ir à loja (o cache ainda vale).
MAX_ESPERA_POR_RODADA_S = 30.0
JANELA_MINUTO_S = 60

# ── Cache negativo ────────────────────────────────────────────────────────

# A loja disse que o anúncio não existe / não é desta conta / sem acesso, ou
# o devolveu sem ficha nenhuma (só título): de novo em 6 h.
CACHE_SEM_FICHA_S = 6 * 3600
# Falhou agora (rede, 5xx, 429, erro da API): não insiste a cada sugestão.
CACHE_FALHA_S = 15 * 60
# A marca de "busca em andamento" passa do pior caso da busca (o `_request`
# do ML repete 429/5xx; cada ida tem até 30 s): processo morto solta sozinho.
EM_ANDAMENTO_TTL_S = 180

PLATAFORMAS_COM_BUSCA = frozenset(
    {IntegrationPlatform.SHOPEE, IntegrationPlatform.ML, IntegrationPlatform.TIKTOK}
)

RESULTADO_OK = "ok"
RESULTADO_SEM_FICHA = "sem_ficha"  # a loja respondeu: o anúncio não está lá ou não tem ficha
RESULTADO_NAO_ENCONTRADO = "nao_encontrado"  # 403/404, "not found", sem acesso
RESULTADO_ERRO = "erro"

# O interruptor que a equipe usa quando a plataforma começa a devolver 429.
_CANAL_DESLIGADO = "desligado"
# TikTok: o `_get` devolve o HTTP como `code`; 105005 = sem escopo. O 401
# (autenticação) NÃO entra: é falha de agora (15 min), não "o anúncio não existe".
_TIKTOK_SEM_ACESSO = frozenset({403, 404, 105005})
_TIKTOK_AUTENTICACAO = 401
_TIKTOK_NAO_EXISTE = re.compile(r"not\s+(?:exist|found)|does\s*n[o']t\s+exist", re.IGNORECASE)
# O código da Shopee no erro do cliente ("shopee_item_base_info error_x: …").
_CODIGO_SHOPEE = re.compile(r"^\S+ ([a-z][a-z0-9_.]*):")
# O id do anúncio vai no CAMINHO da URL (ML, TikTok): só letra, número, "_" e "-".
_ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Buscas que passaram do tempo e seguem em segundo plano (referência forte,
# senão o coletor de lixo pode matar a tarefa no meio).
_EM_SEGUNDO_PLANO: set[asyncio.Task] = set()


# ── Chaves ────────────────────────────────────────────────────────────────


def chave_sem_ficha(plataforma: str, integration_id: Any, item_id: str) -> str:
    """O cache negativo (o valor é o resultado: `sem_ficha`/`nao_encontrado`/`erro`)."""
    return f"atd:ficha_sem:{plataforma}:{integration_id}:{item_id}"


def chave_sem_descricao(plataforma: str, integration_id: Any, item_id: str) -> str:
    """O cache negativo da ida SÓ da descrição (a fresca do ML): à parte do da busca inteira."""
    return f"{chave_sem_ficha(plataforma, integration_id, item_id)}:descricao"


def _chave_negativa(plataforma: str, integration_id: Any, item_id: str, fresca: dict | None) -> str:
    chave = chave_sem_ficha if fresca is None else chave_sem_descricao
    return chave(plataforma, integration_id, item_id)


def _chave_andamento(plataforma: str, integration_id: Any, item_id: str) -> str:
    return f"atd:ficha_busca:{plataforma}:{integration_id}:{item_id}"


def _chave_minuto(integration_id: Any) -> str:
    return f"atd:ficha_limite:{integration_id}"


# ── A cota da rodada e a da sugestão ──────────────────────────────────────


@dataclass
class _Rodada:
    """A rodada do cron em curso: as idas por loja e a espera somada (segundos)."""

    por_loja: dict[str, int] = field(default_factory=dict)
    espera_s: float = 0.0


# None = fora de rodada (o "Sugerir" da tela).
_RODADA: ContextVar[_Rodada | None] = ContextVar("atd_ficha_rodada", default=None)


@contextmanager
def rodada() -> Iterator[None]:
    """A rodada do cron da IA (`ia.gerar_pendentes`): as cotas de idas e de espera."""
    token = _RODADA.set(_Rodada())
    try:
        yield
    finally:
        _RODADA.reset(token)


@dataclass
class Busca:
    """O que a ficha precisa para ir à loja numa sugestão (`ficha.fichas_da_conversa`)."""

    integration_id: UUID | None
    canal_id: UUID | None
    restam: int = MAX_BUSCAS_POR_SUGESTAO


def falta_descricao(plataforma: IntegrationPlatform, fresca: dict) -> bool:
    """A ficha fresca do ML sem a descrição, que ainda não foi buscada? PURA."""
    return (
        plataforma == IntegrationPlatform.ML
        and not fresca.get("descricao")
        and not fresca.get("descricao_buscada")
    )


# ── Conversões ────────────────────────────────────────────────────────────


def _texto(valor: Any) -> str | None:
    if valor is None or isinstance(valor, bool):
        return None
    valor = str(valor).strip()
    return valor or None


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


class _LojaRecusou(Exception):  # noqa: N818 — é um resultado, não um defeito
    """A loja respondeu com erro no corpo (TikTok): `codigo` vai para o log."""

    def __init__(self, codigo: str, nao_encontrado: bool) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.nao_encontrado = nao_encontrado


def _falha(exc: BaseException) -> tuple[str, str]:
    """(resultado, código para o log) — nunca a mensagem (pode ter URL com token)."""
    if isinstance(exc, _LojaRecusou):
        return (RESULTADO_NAO_ENCONTRADO if exc.nao_encontrado else RESULTADO_ERRO), exc.codigo
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        resultado = RESULTADO_NAO_ENCONTRADO if status in (403, 404) else RESULTADO_ERRO
        return resultado, f"HTTP {status}"
    if isinstance(exc, RuntimeError):
        m = _CODIGO_SHOPEE.match(str(exc))
        if m:
            codigo = m.group(1)
            nao = "not_found" in codigo or "permission" in codigo
            return (RESULTADO_NAO_ENCONTRADO if nao else RESULTADO_ERRO), codigo
    return RESULTADO_ERRO, type(exc).__name__


# ── A ida à loja (uma por plataforma) ─────────────────────────────────────


async def _shopee(cliente: Any, item_id: str, fresca: dict | None) -> dict | None:
    if not item_id.isdigit():
        return None
    for it in await cliente.get_item_base_info([int(item_id)]):
        if _texto(_dict(it).get("item_id")) == item_id:
            return ficha_svc.ficha_bruta_shopee(it)
    return None


async def _ml(cliente: Any, item_id: str, fresca: dict | None) -> dict | None:
    if fresca is not None:
        # A fresca do enriquecimento já tem título e atributos: só o texto.
        descricao = await cliente.descricao_do_item(item_id)
        texto = descricao[: ficha_svc.MAX_CHARS_DESCRICAO_BRUTA] if descricao else None
        return {**fresca, "descricao": texto, "descricao_buscada": True}
    # O `/items` do ML é público: só vale anúncio DESTA conta. Sem o `user_id`
    # na credencial não dá para conferir — não vai.
    conta = _texto(_dict(getattr(cliente, "creds", None)).get("user_id"))
    if conta is None:
        return None
    body = _dict(await cliente.get_item(item_id))
    if _texto(body.get("id")) != item_id or _texto(body.get("seller_id")) != conta:
        return None
    descricao = await cliente.descricao_do_item(item_id)
    bruta = ficha_svc.ficha_bruta_ml(body, descricao)
    return {**bruta, "descricao_buscada": True} if bruta is not None else None


async def _tiktok(cliente: Any, item_id: str, fresca: dict | None) -> dict | None:
    resp = _dict(await cliente.produto_bruto(item_id))
    codigo = resp.get("code")
    if codigo == 0:
        data = _dict(resp.get("data"))
        if _texto(data.get("id")) not in (None, item_id):
            return None
        return ficha_svc.ficha_bruta_tiktok(data)
    nao = codigo != _TIKTOK_AUTENTICACAO and (
        codigo in _TIKTOK_SEM_ACESSO
        or bool(_TIKTOK_NAO_EXISTE.search(str(resp.get("message") or "")))
    )
    raise _LojaRecusou(f"tiktok {codigo}", nao_encontrado=nao)


_BUSCAS: dict[IntegrationPlatform, Callable[[Any, str, dict | None], Awaitable[dict | None]]] = {
    IntegrationPlatform.SHOPEE: _shopee,
    IntegrationPlatform.ML: _ml,
    IntegrationPlatform.TIKTOK: _tiktok,
}


# ── Redis ─────────────────────────────────────────────────────────────────


async def _gravar(chave: str, valor: str, ex: int) -> None:
    try:
        await redis.set(chave, valor, ex=ex)
    except Exception as exc:  # noqa: BLE001 — cache é economia, não correção
        logger.info("atendimento_ficha_loja_cache_indisponivel", erro=type(exc).__name__)


async def _soltar(chave: str) -> None:
    try:
        await redis.delete(chave)
    except Exception:  # noqa: BLE001, S110 — o TTL solta sozinho
        pass


async def _pegar_a_vez(plataforma: str, integration_id: Any, item_id: str) -> str | None:
    """A vez de buscar este anúncio → a chave de "em andamento" (None = não vá agora).

    Não vai: outra busca do mesmo anúncio em andamento (SET NX), a loja já
    passou do teto do minuto, ou o Redis fora do ar.
    """
    andamento = _chave_andamento(plataforma, integration_id, item_id)
    try:
        if not await redis.set(andamento, "1", nx=True, ex=EM_ANDAMENTO_TTL_S):
            return None
        limite = _chave_minuto(integration_id)
        feitas = int(await redis.incr(limite))
        if feitas == 1 or await redis.ttl(limite) < 0:
            await redis.expire(limite, JANELA_MINUTO_S)
    except Exception as exc:  # noqa: BLE001
        logger.info("atendimento_ficha_loja_cache_indisponivel", erro=type(exc).__name__)
        return None
    if feitas > MAX_BUSCAS_POR_MINUTO:
        logger.info("atendimento_ficha_loja_sem_cota", plataforma=plataforma, cota="minuto")
        await _soltar(andamento)
        return None
    return andamento


async def _guardar_completa(chave: str, completa: dict) -> None:
    """A fresca do ML com a descrição, na MESMA chave e com o tempo que ela ainda tinha.

    A chave sumiu no meio (venceu): não grava — a próxima busca é inteira.
    """
    try:
        resta = await redis.ttl(chave)
        if isinstance(resta, int) and resta > 0:
            await redis.set(chave, json.dumps(completa, ensure_ascii=False), ex=resta)
    except Exception as exc:  # noqa: BLE001 — cache é economia, não correção
        logger.info("atendimento_ficha_loja_cache_indisponivel", erro=type(exc).__name__)


async def _buscar_e_guardar(
    cliente: Any,
    plataforma: IntegrationPlatform,
    integration_id: UUID,
    item_id: str,
    fresca: dict | None,
    andamento: str,
) -> dict | None:
    """Vai à loja e grava o cache — também quando a sugestão já desistiu de esperar."""
    plat = plataforma.value
    negativa = _chave_negativa(plat, integration_id, item_id, fresca)
    try:
        try:
            bruta = await _BUSCAS[plataforma](cliente, item_id, fresca)
        except Exception as exc:  # noqa: BLE001 — falha da loja: sugestão sem a ficha
            resultado, codigo = _falha(exc)
            logger.info(
                "atendimento_ficha_loja_falhou",
                plataforma=plat,
                resultado=resultado,
                codigo=codigo,
            )
            await _gravar(
                negativa,
                resultado,
                CACHE_SEM_FICHA_S if resultado == RESULTADO_NAO_ENCONTRADO else CACHE_FALHA_S,
            )
            return None
        if bruta is None:
            logger.info("atendimento_ficha_loja", plataforma=plat, resultado=RESULTADO_SEM_FICHA)
            await _gravar(negativa, RESULTADO_SEM_FICHA, CACHE_SEM_FICHA_S)
            return None
        chave = ficha_svc.chave_ficha(plat, integration_id, item_id)
        if fresca is not None:
            await _guardar_completa(chave, bruta)
        else:
            bruta = {**bruta, "buscada_em": _agora().isoformat(timespec="seconds")}
            await _gravar(chave, json.dumps(bruta, ensure_ascii=False), ficha_svc.CACHE_FICHA_S)
        logger.info("atendimento_ficha_loja", plataforma=plat, resultado=RESULTADO_OK)
        return bruta
    finally:
        await _soltar(andamento)


async def _canal_desligado(session: AsyncSession, canal_id: UUID | None) -> bool:
    if canal_id is None:
        return False
    status = await session.scalar(
        select(AtendimentoCanal.status).where(AtendimentoCanal.id == canal_id)
    )
    return status == _CANAL_DESLIGADO


async def ficha_da_loja(
    session: AsyncSession,
    busca: Busca | None,
    plataforma: IntegrationPlatform,
    item_id: str,
    *,
    fresca: dict | None = None,
) -> dict | None:
    """A ficha crua do anúncio direto da loja (o formato de `ficha.ficha_bruta_*`) — ou None.

    `fresca` (só o ML): a ficha que o enriquecimento guardou, sem descrição —
    a ida busca só a descrição e devolve a fresca completa. As travas estão
    no topo. Problema da loja, do cliente ou do Redis é "sem ficha" (não
    levanta); erro de BANCO sobe, para o `_seguro` da IA desfazer o SAVEPOINT
    (engolido aqui, deixaria a transação abortada para o resto da sugestão).
    """
    if busca is None or busca.integration_id is None or plataforma not in PLATAFORMAS_COM_BUSCA:
        return None
    if not _ID_VALIDO.match(item_id or ""):
        return None
    if not get_settings().atendimento_leitura_ativa or busca.restam <= 0:
        return None
    integ_id = busca.integration_id
    plat = plataforma.value
    try:
        if await redis.get(_chave_negativa(plat, integ_id, item_id, fresca)):
            return None
    except Exception as exc:  # noqa: BLE001 — sem Redis não há cache nem trava: não vai
        logger.info("atendimento_ficha_loja_cache_indisponivel", erro=type(exc).__name__)
        return None
    da_rodada = _RODADA.get()
    if da_rodada is not None:
        if da_rodada.espera_s >= MAX_ESPERA_POR_RODADA_S:
            logger.info("atendimento_ficha_loja_sem_cota", plataforma=plat, cota="espera")
            return None
        if da_rodada.por_loja.get(str(integ_id), 0) >= MAX_BUSCAS_POR_RODADA:
            logger.info("atendimento_ficha_loja_sem_cota", plataforma=plat, cota="rodada")
            return None
    integration = await session.get(Integration, integ_id)
    if (
        integration is None
        or integration.archived_at is not None
        or integration.platform != plataforma
        or await _canal_desligado(session, busca.canal_id)
    ):
        return None
    try:
        # O MESMO cliente do sync e do enriquecimento: credencial DESTA loja,
        # renovação pela trava da integração, token novo gravado na hora.
        cliente = await clientes.cliente_da_integracao(integration)
    except Exception as exc:  # noqa: BLE001 — credencial ilegível: sem ficha
        logger.info("atendimento_ficha_loja_falhou", plataforma=plat, codigo=type(exc).__name__)
        return None
    andamento = await _pegar_a_vez(plat, integ_id, item_id)
    if andamento is None:
        return None
    busca.restam -= 1
    if da_rodada is not None:
        da_rodada.por_loja[str(integ_id)] = da_rodada.por_loja.get(str(integ_id), 0) + 1
    tarefa = asyncio.ensure_future(
        _buscar_e_guardar(cliente, plataforma, integ_id, item_id, fresca, andamento)
    )
    _EM_SEGUNDO_PLANO.add(tarefa)
    tarefa.add_done_callback(_EM_SEGUNDO_PLANO.discard)
    inicio = time.monotonic()
    try:
        return await asyncio.wait_for(asyncio.shield(tarefa), TEMPO_MAX_BUSCA_S)
    except TimeoutError:
        logger.info("atendimento_ficha_loja_demorou", plataforma=plat)
        return None
    except Exception as exc:  # noqa: BLE001 — `_buscar_e_guardar` não levanta; por garantia
        logger.info("atendimento_ficha_loja_falhou", plataforma=plat, codigo=type(exc).__name__)
        return None
    finally:
        if da_rodada is not None:
            da_rodada.espera_s += time.monotonic() - inicio
