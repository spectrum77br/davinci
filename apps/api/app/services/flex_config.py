"""Leitura das chaves `flex_*` do .env (app/config.py), já normalizadas — e,
desde 11/11/2026, dos AJUSTES trocados na tela (tabela `flex_ajuste`).

As chaves ficam em texto (como `estoque_familia_prefixos`) para um erro de
digitação no .env não derrubar a api na subida: aqui o valor errado vira o
lado SEGURO — modo desconhecido é "desligado", id de conta inválido é
ignorado (e a lista vazia é "nenhuma conta").

Ajustes da tela (Vinicius, 11/11/2026: só o Eduardo mexia no .env — "e se
precisar tirar de onde tá e colocar onde nós conseguimos ver?"): o modo, a
escrita da Shopee e o pedido Flex no .sp. Campo vazio na tela = vale o .env.
Mesma ideia do local de saída (services/flex_local): o valor fica num cache
do processo, recarregado do banco por `carregar()` — que o `flex_local.carregar`
chama junto (o motor, o robô de prioridade e o /api/flex já relêem por ali) e
os jobs do Flex no worker chamam no começo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import UserRole, UserStatus
from app.models.flex import FLEX_MODOS, FlexAjuste

logger = structlog.get_logger()

MODO_DESLIGADO = "desligado"
MODO_OBSERVAR = "observar"
# Modos em que o DaVinci pode ESCREVER na plataforma (só nas contas
# permitidas, respeitando o teto por rodada).
MODOS_QUE_ESCREVEM = frozenset({"piloto", "ativo"})
# Espera da NF pelo tipo de envio quando o pedido Flex vai para o .sp e o .env
# não diz outra (config.py: "volta a 10 junto com ele").
ESPERA_ENVIO_PADRAO_MIN = 10
_VALIDADE_S = 30.0


# ---- Ajustes da tela (flex_ajuste) -------------------------------------------


@dataclass(frozen=True)
class Ajustes:
    """O que foi trocado na tela; None = vale o .env."""

    modo: str | None = None
    shopee_escrita: bool | None = None
    pedido_no_sp: bool | None = None
    atualizado_em: datetime | None = None
    atualizado_por: UUID | None = None


_SEM_AJUSTE = Ajustes()
_cache: tuple[float, Ajustes] | None = None


class AjusteInvalidoError(ValueError):
    def __init__(self, codigo: str, detalhe: str) -> None:
        super().__init__(detalhe)
        self.codigo = codigo
        self.detalhe = detalhe


def ajustes() -> Ajustes:
    """O que o processo carregou da tela (nada carregado = tudo do .env)."""
    return _cache[1] if _cache is not None else _SEM_AJUSTE


def limpar_cache() -> None:
    """Testes: esquecer o que o processo carregou."""
    global _cache
    _cache = None


def usar(a: Ajustes) -> Ajustes:
    """Guarda no cache do processo (depois do commit de quem salvou)."""
    global _cache
    _cache = (time.monotonic(), a)
    return a


def _da_linha(linha: FlexAjuste | None) -> Ajustes:
    if linha is None:
        return _SEM_AJUSTE
    m = (linha.modo or "").strip().lower() or None
    if m is not None and m not in FLEX_MODOS:
        # O CHECK do banco não deixa; defesa: valor estranho vira "vale o .env".
        logger.warning("flex_ajuste_modo_invalido", modo=linha.modo)
        m = None
    return Ajustes(
        m, linha.shopee_escrita, linha.pedido_no_sp, linha.atualizado_em, linha.atualizado_por
    )


async def carregar(*, forcar: bool = False) -> Ajustes:
    """Relê do banco se o cache tem mais de `_VALIDADE_S` (ou `forcar`). Sessão
    PRÓPRIA; banco fora: fica o que já estava no cache (ou o .env)."""
    if not forcar and _cache is not None and time.monotonic() - _cache[0] < _VALIDADE_S:
        return _cache[1]
    try:
        from app.db import session_scope

        async with session_scope() as s:
            linha = (
                await s.execute(select(FlexAjuste).where(FlexAjuste.id == 1))
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — sem banco, fica o que se sabia
        logger.warning("flex_ajuste_nao_carregou", erro=str(exc)[:200])
        return ajustes()
    return usar(_da_linha(linha))


async def salvar(
    session: AsyncSession,
    *,
    modo_novo: str,
    shopee_escrita_nova: bool,
    pedido_no_sp_novo: bool,
    por: UUID | None,
) -> Ajustes:
    """Grava as três chaves (quem pode é conferido no router). O cache só troca
    com `usar` depois do commit de quem chama."""
    m = (modo_novo or "").strip().lower()
    if m not in FLEX_MODOS:
        raise AjusteInvalidoError("modo_invalido", f"modo {modo_novo!r} não existe")
    linha = await session.get(FlexAjuste, 1, with_for_update=True)
    antes = (modo(), shopee_escrita(), pedido_no_sp())
    agora = datetime.now(UTC)
    if linha is None:
        linha = FlexAjuste(id=1)
        session.add(linha)
    linha.modo = m
    linha.shopee_escrita = bool(shopee_escrita_nova)
    linha.pedido_no_sp = bool(pedido_no_sp_novo)
    linha.atualizado_em = agora
    linha.atualizado_por = por
    await session.flush()
    novo = Ajustes(m, bool(shopee_escrita_nova), bool(pedido_no_sp_novo), agora, por)
    logger.info(
        "flex_ajuste_trocado",
        antes={"modo": antes[0], "shopee_escrita": antes[1], "pedido_no_sp": antes[2]},
        depois={
            "modo": m,
            "shopee_escrita": novo.shopee_escrita,
            "pedido_no_sp": novo.pedido_no_sp,
        },
        por=str(por) if por else None,
    )
    return novo


def origem(campo: str) -> str:
    """ "tela" quando o valor veio do ajuste salvo; "servidor" quando é o .env."""
    return "tela" if getattr(ajustes(), campo, None) is not None else "servidor"


# ---- As chaves -----------------------------------------------------------------


def modo(valor: str | None = None) -> str:
    """O modo do Flex normalizado (o da tela; sem ele, `flex_modo` do .env);
    desconhecido vale "desligado"."""
    if valor is None:
        da_tela = ajustes().modo
        bruto = da_tela if da_tela is not None else get_settings().flex_modo
    else:
        bruto = valor
    m = (bruto or "").strip().lower()
    if m in FLEX_MODOS:
        return m
    logger.warning("flex_modo_desconhecido", valor=str(bruto)[:40])
    return MODO_DESLIGADO


def shopee_escrita() -> bool:
    """O motor liga/desliga a Entrega Direta nos anúncios da Shopee? (a tela;
    sem ela, `flex_shopee_escrita`)."""
    da_tela = ajustes().shopee_escrita
    return bool(get_settings().flex_shopee_escrita) if da_tela is None else bool(da_tela)


def pedido_no_sp() -> bool:
    """O robô de prioridade leva o pedido Flex para o lote do local de saída?
    (a tela; sem ela, `flex_pedido_no_sp`)."""
    da_tela = ajustes().pedido_no_sp
    if da_tela is None:
        return bool(getattr(get_settings(), "flex_pedido_no_sp", False))
    return bool(da_tela)


def espera_envio_min() -> int:
    """Quantos minutos o pedido ML/Shopee novo espera o tipo de envio ser lido
    antes do robô de prioridade e da NF automática. Anda junto com o pedido
    Flex no .sp: trocado na tela, ligado = o do .env (10 se lá for 0) e
    desligado = 0; sem ajuste na tela, o `flex_espera_envio_min` do .env."""
    base = int(getattr(get_settings(), "flex_espera_envio_min", 0) or 0)
    da_tela = ajustes().pedido_no_sp
    if da_tela is None:
        return base
    return (base or ESPERA_ENVIO_PADRAO_MIN) if da_tela else 0


def contas(valor: str | None = None) -> frozenset[UUID]:
    """`flex_contas`: integration_id permitidos. Vazio = nenhuma conta."""
    bruto = get_settings().flex_contas if valor is None else valor
    out: set[UUID] = set()
    for pedaco in (bruto or "").split(","):
        p = pedaco.strip()
        if not p:
            continue
        try:
            out.add(UUID(p))
        except ValueError:
            logger.warning("flex_conta_invalida", valor=p[:60])
    return frozenset(out)


def pode_escrever(modo_atual: str | None = None) -> bool:
    """O modo deixa escrever na plataforma? (observar/desligado: nunca)."""
    return modo(modo_atual) in MODOS_QUE_ESCREVEM


# Além dos admin, quem vê o Flex (Vinicius, 11/11/2026: "libera para cairo e
# churchil, vamos ver e começar a arrumar"). Mesma régua da aba Estoque
# Negativo (routers/estoque): cairo SA pelo e-mail (há outro "cairo"),
# churchill pelo nome.
_VEEM_EMAILS = frozenset({"sa.geral@tutamail.com"})
_VEEM_NOMES = frozenset({"churchill"})
# Quem troca o modo, a escrita da Shopee e o pedido no .sp: admin e o gerente
# (cairo SA) — a mesma régua do "Informar" da Amazon (admins + o gerente).
_AJUSTAM_EMAILS = frozenset({"sa.geral@tutamail.com"})


def pode_ver(user) -> bool:
    """Quem vê o Flex: admin ativo (Eduardo, 05/11/2026: "libere o flex
    somente para os admin") e, desde 11/11/2026, cairo SA e churchill. De
    05/10 a 05/11 era a lista `flex_usuarios` do .env (heisenberg e thorfinn)."""
    if user is None or getattr(user, "status", None) != UserStatus.ACTIVE:
        return False
    if getattr(user, "role", None) == UserRole.ADMIN:
        return True
    email = (getattr(user, "email", None) or "").strip().lower()
    nome = (getattr(user, "name", None) or "").strip().lower()
    return email in _VEEM_EMAILS or nome in _VEEM_NOMES


def pode_ajustar(user) -> bool:
    """Quem troca o modo / a escrita da Shopee / o pedido no .sp: admin ativo
    e o gerente (cairo SA, pelo e-mail)."""
    if user is None or getattr(user, "status", None) != UserStatus.ACTIVE:
        return False
    if getattr(user, "role", None) == UserRole.ADMIN:
        return True
    return (getattr(user, "email", None) or "").strip().lower() in _AJUSTAM_EMAILS
