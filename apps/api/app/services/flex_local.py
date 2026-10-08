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

import re
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
# O local de UMA rodada (motor, emergência, robô de prioridade): fixado no
# começo dela e usado até o fim — trocar o local no meio da rodada não pode
# misturar o lote velho com o novo (revisão de 08/10/2026).
_da_rodada: ContextVar[LocalFlex | None] = ContextVar("flex_local_da_rodada", default=None)


class LocalFlexInvalidoError(ValueError):
    def __init__(self, codigo: str, detalhe: str) -> None:
        super().__init__(detalhe)
        self.codigo = codigo
        self.detalhe = detalhe


def atual() -> LocalFlex:
    """O local da rodada em andamento (`fixar`); fora de rodada, o do cache do
    processo (o padrão se nunca carregou)."""
    fixo = _da_rodada.get()
    if fixo is not None:
        return fixo
    return _cache[1] if _cache is not None else _PADRAO


@contextmanager
def fixar(local: LocalFlex) -> Iterator[LocalFlex]:
    """A rodada inteira usa ESTE local (e as tarefas que ela criar)."""
    token = _da_rodada.set(local)
    try:
        yield local
    finally:
        _da_rodada.reset(token)


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


def local_da_linha(linha: FlexLocal | None) -> LocalFlex:
    """O local de uma linha já lida (o router lê com a trava da linha)."""
    return _da_linha(linha)


def _da_linha(linha: FlexLocal | None) -> LocalFlex:
    if linha is None:
        return _PADRAO
    lote_ok = (linha.lote or "").strip().lower()
    if lote_ok not in LOTES or not (linha.cidade or "").strip():
        # O CHECK do banco não deixa; defesa: valor estranho vira o padrão.
        logger.warning("flex_local_invalido", cidade=linha.cidade, lote=linha.lote)
        return _PADRAO
    return LocalFlex(linha.cidade.strip(), lote_ok, linha.atualizado_em, linha.atualizado_por)


async def carregar(*, forcar: bool = False) -> LocalFlex:
    """Relê do banco se o cache tem mais de `_VALIDADE_S` (ou `forcar`). Usa
    sessão PRÓPRIA: um erro aqui não aborta a transação de quem chamou.
    Banco fora: fica o que já estava no cache (ou o padrão)."""
    fixo = _da_rodada.get()
    if fixo is not None and not forcar:
        return fixo  # dentro de uma rodada: o local dela, sem reler
    if not forcar and _cache is not None and time.monotonic() - _cache[0] < _VALIDADE_S:
        return _cache[1]
    try:
        from app.db import session_scope

        async with session_scope() as s:
            linha = (
                await s.execute(select(FlexLocal).where(FlexLocal.id == 1))
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — sem banco, fica o que se sabia
        logger.warning("flex_local_nao_carregou", erro=str(exc)[:200])
        return _cache[1] if _cache is not None else _PADRAO
    return _guardar(_da_linha(linha))


_UF_NO_FIM = re.compile(r"\s*(?:[-/,(]\s*)[a-z]{2}\)?\s*$")
_SEM_LETRA = re.compile(r"[^0-9a-z]+")


def normalizar_cidade(texto: str | None) -> str:
    """ "São Bernardo do Campo" ≈ "sao-bernardo do CAMPO - SP": sem acento, sem
    diferença de maiúscula, hífen/apóstrofo/ponto viram espaço e a UF no fim
    (" - SP", "/SP", ", SP", "(SP)") sai."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    ).casefold()
    sem_uf = _UF_NO_FIM.sub("", sem_acento.strip())
    return " ".join(_SEM_LETRA.sub(" ", sem_uf).split())


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
    """Troca o local (quem pode é conferido no router). Valida e grava na
    sessão de quem chama; o cache só troca com `usar` depois do commit."""
    # Espaço especial (NBSP do texto copiado do ML, Option+Espaço, TAB) e
    # caractere de controle viram espaço — nunca grudam as palavras.
    limpo = "".join(c if c.isprintable() and not c.isspace() else " " for c in (cidade or ""))
    nome = " ".join(limpo.split())
    if not nome:
        raise LocalFlexInvalidoError("cidade_vazia", "informe a cidade de onde o motoboy sai")
    if "," in nome or ";" in nome:
        raise LocalFlexInvalidoError(
            "cidade_invalida",
            "digite só o nome da cidade, como o Mercado Livre mostra (sem vírgula)",
        )
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
    # O cache do processo só troca DEPOIS do commit (quem chama: `usar`).
    return novo


def usar(local: LocalFlex) -> LocalFlex:
    """Depois do commit da troca: o cache deste processo já sai com o novo."""
    return _guardar(local)
