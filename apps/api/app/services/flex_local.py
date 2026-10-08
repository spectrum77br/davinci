"""Local de saída do Flex: a cidade de onde o motoboy sai e o lote do estoque
de lá (Eduardo, 08/10/2026: "o motoboy vai sair de São Bernardo" e "não vai
ser pra sempre fixo em São Bernardo, eu quero poder alterar").

Uma linha só (`flex_local`), editada na aba Flex por um admin. Sem linha vale
São Bernardo do Campo / .sp. Quem usa:

  • o motor (services/flex_motor): o LOTE é o estoque que liga e desliga o
    Flex (o "saldo Flex") e a CIDADE é a trava de origem — só mexe na conta
    do ML cuja saída do Flex (a `origin.city.name` da assinatura) é nela;
  • o robô de prioridade (services/prioridade_estoque) e a detecção do pedido
    Flex (services/flex_envio): o pedido Flex vai para esse lote;
  • os textos da tela (services/flex_textos e a aba Flex).

O motor e os robôs são código síncrono no meio de uma rodada: o valor fica
num cache do processo, recarregado do banco por `carregar()` no começo de
cada rodada/pedido (no máximo a cada `_VALIDADE_S`). A API recarrega a cada
pedido do /api/flex e atualiza o cache na hora em que alguém salva; o worker
vê a troca na rodada seguinte.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flex import FLEX_LOTES, FlexLocal

logger = structlog.get_logger()

PADRAO_CIDADE = "São Bernardo do Campo"
PADRAO_LOTE = "sp"
LOTES = FLEX_LOTES
_VALIDADE_S = 30.0
_TAMANHO_CIDADE = 100


@dataclass(frozen=True)
class LocalFlex:
    cidade: str = PADRAO_CIDADE
    lote: str = PADRAO_LOTE
    atualizado_em: datetime | None = None
    atualizado_por: UUID | None = None


_PADRAO = LocalFlex()
_cache: tuple[float, LocalFlex] | None = None


class LocalFlexInvalidoError(ValueError):
    def __init__(self, codigo: str, detalhe: str) -> None:
        super().__init__(detalhe)
        self.codigo = codigo
        self.detalhe = detalhe


def atual() -> LocalFlex:
    """O local do cache do processo (o padrão se nunca carregou)."""
    return _cache[1] if _cache is not None else _PADRAO


def cidade() -> str:
    return atual().cidade


def lote() -> str:
    return atual().lote


def limpar_cache() -> None:
    """Testes: esquecer o que o processo carregou."""
    global _cache
    _cache = None


def _guardar(local: LocalFlex) -> LocalFlex:
    global _cache
    _cache = (time.monotonic(), local)
    return local


def _da_linha(linha: FlexLocal | None) -> LocalFlex:
    if linha is None:
        return _PADRAO
    lote_ok = (linha.lote or "").strip().lower()
    if lote_ok not in LOTES or not (linha.cidade or "").strip():
        # O CHECK do banco não deixa; defesa: valor estranho vira o padrão.
        logger.warning("flex_local_invalido", cidade=linha.cidade, lote=linha.lote)
        return _PADRAO
    return LocalFlex(linha.cidade.strip(), lote_ok, linha.atualizado_em, linha.atualizado_por)


async def carregar(session: AsyncSession | None = None, *, forcar: bool = False) -> LocalFlex:
    """Relê do banco se o cache tem mais de `_VALIDADE_S` (ou `forcar`).
    Banco fora: fica o que já estava no cache (ou o padrão)."""
    if not forcar and _cache is not None and time.monotonic() - _cache[0] < _VALIDADE_S:
        return _cache[1]
    try:
        if session is not None:
            linha = await session.get(FlexLocal, 1)
        else:
            from app.db import session_scope

            async with session_scope() as s:
                linha = (
                    await s.execute(select(FlexLocal).where(FlexLocal.id == 1))
                ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — sem banco, fica o que se sabia
        logger.warning("flex_local_nao_carregou", erro=str(exc)[:200])
        return atual()
    return _guardar(_da_linha(linha))


def normalizar_cidade(texto: str | None) -> str:
    """ "São Bernardo do Campo" ≈ "sao  bernardo do campo": sem acento, sem
    diferença de maiúscula, espaços juntos."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return " ".join(sem_acento.casefold().split())


def mesma_cidade(a: str | None, b: str | None) -> bool:
    na, nb = normalizar_cidade(a), normalizar_cidade(b)
    return bool(na) and na == nb


def cidades_da_origem(origem_cidade: str | None) -> list[str]:
    """`flex_conta.origem_cidade` guarda as cidades separadas por ", "."""
    return [c.strip() for c in (origem_cidade or "").split(",") if c.strip()]


def origem_ok(origem_cidade: str | None, local: LocalFlex | None = None) -> bool:
    """TODAS as saídas do Flex da conta são na cidade do local? Sem cidade
    lida = False (não se sabe de onde o motoboy sai)."""
    alvo = (local or atual()).cidade
    cidades = cidades_da_origem(origem_cidade)
    return bool(cidades) and all(mesma_cidade(c, alvo) for c in cidades)


async def salvar(session: AsyncSession, *, cidade: str, lote: str, por: UUID | None) -> LocalFlex:
    """Troca o local (quem pode é conferido no router). Valida e grava; o
    cache deste processo já sai com o novo."""
    nome = " ".join((cidade or "").split())
    if not nome:
        raise LocalFlexInvalidoError("cidade_vazia", "informe a cidade de onde o motoboy sai")
    if len(nome) > _TAMANHO_CIDADE:
        raise LocalFlexInvalidoError("cidade_longa", "nome de cidade comprido demais")
    lote_ok = (lote or "").strip().lower().lstrip(".")
    if lote_ok not in LOTES:
        raise LocalFlexInvalidoError(
            "lote_invalido", f"lote .{lote_ok or '?'} não é um lote de venda"
        )
    linha = await session.get(FlexLocal, 1)
    agora = datetime.now(UTC)
    antes = _da_linha(linha)
    if linha is None:
        linha = FlexLocal(id=1, cidade=nome, lote=lote_ok, atualizado_em=agora, atualizado_por=por)
        session.add(linha)
    else:
        linha.cidade = nome
        linha.lote = lote_ok
        linha.atualizado_em = agora
        linha.atualizado_por = por
    await session.flush()
    novo = LocalFlex(nome, lote_ok, agora, por)
    logger.info(
        "flex_local_trocado",
        cidade_antes=antes.cidade,
        lote_antes=antes.lote,
        cidade=nome,
        lote=lote_ok,
        por=str(por) if por else None,
    )
    return _guardar(novo)
