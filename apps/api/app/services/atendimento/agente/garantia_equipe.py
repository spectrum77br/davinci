"""O prazo da garantia como a EQUIPE diz, POR MODELO (16/11/2026). Só lê.

O Eduardo (16/11/2026): "diz como a equipe diz; se está no banco, está certo, ele busca lá e
envia" — por cima da decisão de 27/10/2026 (nenhum prazo na garantia Uranyx, porque a equipe não
dava prazo). Desde o fim de outubro a equipe dá o prazo, e POR MODELO (as respostas enviadas nos
60 dias até 16/11): A18 Pro Max «90 dias pela loja e 9 meses pela marca, completando 1 ano» (5 de
5), A17 Pro Max «90 dias» (4 de 4, "somente o modelo premium A18 Pro Max tem 1 ano"), WP60 90
dias (5 de 5), P1 90 dias (3 de 3) — e se contradiz noutros (F105, F110, F112, C68: ora 90 dias,
ora 1 ano). Um prazo só para toda a Uranyx erraria o A17.

Essas respostas nunca entraram no banco de respostas (o validador as tinha por proibidas: "prazo
de garantia só pode vir do sistema"); a fonte aqui é o que a equipe ENVIOU (origem
`davinci_humano`). Cada resposta com "garantia" e prazo conta para UM modelo — o código no texto
("o modelo a17 pro max tem somente 90 dias") ou, sem código, o do título do anúncio da conversa;
dois códigos não contam. O prazo do modelo sai só quando as últimas respostas dele concordam (2 ou
mais, nenhuma diferente). O modelo da conversa vem do item do PEDIDO (ou do registro do Painel) e,
antes da compra, dos produtos vinculados ao anúncio — nunca do título do anúncio, que mistura
modelos ("A17 Pro Max … ou 16.128 Premium Edition"). Sem prazo da equipe, a regra de 27/10 vale.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import constantes, validador
from app.services.atendimento.agente import recursos_equipe

logger = structlog.get_logger()

NOVENTA_DIAS = "90_dias"
UM_ANO = "1_ano"
JANELA = timedelta(days=60)
# As últimas respostas com prazo de cada modelo (a mais nova primeiro): a correção da equipe
# ("agora é 1 ano") vale depois de poucas respostas, sem esperar a janela inteira passar.
ULTIMAS_DO_MODELO = 6
MINIMO_DE_RESPOSTAS = 2
MAX_MENSAGENS = 3000
CACHE_SEGUNDOS = 3600

# Os números que vão na regra (`validador.prazos_da_regra` os aceita, cada um com o seu par).
PRAZOS_DA_REGRA: dict[str, dict[str, int]] = {
    NOVENTA_DIAS: {"dias": 90},
    UM_ANO: {"garantia_anos": 1, "loja_dias": 90, "uranyx_meses": 9},
}
FALAS: dict[str, str] = {
    NOVENTA_DIAS: "«90 dias de garantia»",
    UM_ANO: "«90 dias de garantia pela loja e 9 meses pela marca, completando 1 ano»",
}
NUNCA_COM_O_PRAZO = (
    "outro prazo que não o de «prazo_como_a_equipe_diz», os meses de hardware ou de software, "
    "datas de fim, o app Uranyx, o site uranyx.com.br/sac e o SAC da Uranyx"
)

_GARANTIA = re.compile(r"\bgarantia")
_APPLE = re.compile(r"\bapple\b")
# A mala tem os seus códigos (a Charlots "P1" é também o "P1" de um celular): a resposta sobre
# mala não conta para o prazo do aparelho.
_DE_MALA = re.compile(r"\bmalas?\b|\bbagagem\b|\bcharlots\b")
_NOVENTA = re.compile(r"(?<![\d.,])(?:90|noventa)\s*dias?\b")
# "9 meses pela marca", "9 meses pela uranyx", "9 meses pela marca do aparelho" — o "9 meses"
# solto ("seu pedido tem 9 meses") não é prazo de garantia.
_NOVE_MESES = re.compile(
    r"(?<![\d.,])(?:9|nove)\s*mes(?:es)?\s+(?:\w+\s+){0,2}?(?:pela|pelo|com|da|do|de)\s+"
    r"(?:a\s+|o\s+)?(?:marca|uranyx|fabrica\w*|fabricante)\b"
)
# "1 ano de garantia", "garantia de 1 ano", "completando 1 ano" — o "seu pedido tem mais de 1
# ano" não é.
_UM_ANO = re.compile(
    r"(?<![\d.,])(?:1|um)\s+ano\s+de\s+garantia\b"
    r"|\bgarantia\s+(?:\w+\s+){0,3}?(?:de\s+)?(?:1|um)\s+ano\b"
    r"|\b(?:completando|totalizando|somando|total\s+de|no\s+total)\s+(?:de\s+)?(?:1|um)\s+ano\b"
)


def classe_do_prazo(texto: str | None) -> str | None:
    """O prazo de garantia que a resposta da equipe dá: `UM_ANO` («90 dias pela loja e 9 meses
    pela marca», «1 ano de garantia»), `NOVENTA_DIAS` («90 dias de garantia») ou None (sem
    garantia, sem prazo, ou a garantia da Apple, que tem o fato aprovado dela). PURA."""
    plano = validador.plano_de(texto or "")
    if not _GARANTIA.search(plano) or _APPLE.search(plano):
        return None
    if _NOVE_MESES.search(plano) or _UM_ANO.search(plano):
        return UM_ANO
    if _NOVENTA.search(plano):
        return NOVENTA_DIAS
    return None


def _codigos(texto: str | None) -> set[str]:
    return {c.upper() for c in recursos_equipe.codigos_do(texto or "")}


def modelo_da_resposta(texto: str | None, titulo: str | None) -> str | None:
    """O código do modelo de que a resposta fala: o ÚNICO código no texto; sem código no texto,
    o ÚNICO do título do anúncio da conversa. Dois códigos = None. PURA."""
    no_texto = _codigos(texto)
    if no_texto:
        return next(iter(no_texto)) if len(no_texto) == 1 else None
    do_titulo = _codigos(titulo)
    return next(iter(do_titulo)) if len(do_titulo) == 1 else None


def modelo_unico(nomes: Iterable[str | None]) -> str | None:
    """O ÚNICO código de modelo nos nomes (o item do pedido, os produtos do anúncio). PURA."""
    codigos: set[str] = set()
    for nome in nomes:
        codigos |= _codigos(nome)
    return next(iter(codigos)) if len(codigos) == 1 else None


def prazos_por_modelo(respostas: Iterable[tuple[str | None, str | None]]) -> dict[str, str]:
    """{código: classe} das respostas (texto, título do anúncio), da MAIS NOVA para a mais
    velha: as `ULTIMAS_DO_MODELO` respostas com prazo de cada modelo, valendo com
    `MINIMO_DE_RESPOSTAS` ou mais e todas iguais. PURA."""
    por: dict[str, list[str]] = {}
    for texto, titulo in respostas:
        classe = classe_do_prazo(texto)
        if classe is None:
            continue
        if _DE_MALA.search(validador.plano_de(f"{texto or ''} {titulo or ''}")):
            continue
        modelo = modelo_da_resposta(texto, titulo)
        if modelo is None:
            continue
        lista = por.setdefault(modelo, [])
        if len(lista) < ULTIMAS_DO_MODELO:
            lista.append(classe)
    return {
        m: lista[0]
        for m, lista in por.items()
        if len(lista) >= MINIMO_DE_RESPOSTAS and len(set(lista)) == 1
    }


def com_o_prazo(regra: Mapping[str, Any], classe: str | None, modelo: str | None) -> dict:
    """A regra Uranyx com o prazo que a equipe diz do modelo; sem prazo, a regra como veio. PURA."""
    nova = dict(regra)
    if classe not in PRAZOS_DA_REGRA or not modelo:
        return nova
    nova.update(PRAZOS_DA_REGRA[classe])
    nova["prazo_como_a_equipe_diz"] = f"{FALAS[classe]} (o {modelo}, a partir da entrega)"
    nova["nunca"] = NUNCA_COM_O_PRAZO
    return nova


_CACHE: dict[datetime, tuple[float, dict[str, str]]] = {}


def limpar_cache() -> None:
    """Os testes começam e terminam sem o cache (`tests/conftest.py`)."""
    _CACHE.clear()


async def prazos_da_equipe(session: AsyncSession, *, as_of: datetime) -> dict[str, str]:
    """{código: classe} das respostas que a equipe ENVIOU nos `JANELA` dias antes de `as_of`
    (a hora cheia: no teste, só o que já tinha sido dito). Uma leitura por hora em cada processo.
    Nunca levanta (sem a leitura, nenhum prazo)."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    hora = as_of.replace(minute=0, second=0, microsecond=0)
    agora = time.monotonic()
    guardado = _CACHE.get(hora)
    if guardado is not None and agora - guardado[0] < CACHE_SEGUNDOS:
        return guardado[1]
    from app.services.atendimento import banco_respostas

    m, c = AtendimentoMensagem, AtendimentoConversa
    try:
        linhas = (
            await session.execute(
                select(m.texto, c.anuncio_titulo, c.anuncio_id)
                .join(c, c.id == m.conversa_id)
                .where(
                    m.origem == constantes.ORIGEM_HUMANO,
                    m.enviada_em >= hora - JANELA,
                    m.enviada_em < hora,
                    m.texto.ilike("%garantia%"),
                )
                .order_by(m.enviada_em.desc(), m.id)
                .limit(MAX_MENSAGENS)
            )
        ).all()
        sem_titulo = {str(x.anuncio_id) for x in linhas if not x.anuncio_titulo and x.anuncio_id}
        titulos = await banco_respostas._titulos(session, sem_titulo) if sem_titulo else {}
    except Exception as e:  # noqa: BLE001 — sem a leitura, a regra sem prazo
        logger.warning("atendimento_garantia_equipe_falhou", err=type(e).__name__)
        return {}
    prazos = prazos_por_modelo(
        (x.texto, x.anuncio_titulo or titulos.get(str(x.anuncio_id or ""))) for x in linhas
    )
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[hora] = (agora, prazos)
    return prazos


async def regra_com_o_prazo(
    session: AsyncSession,
    regra: Mapping[str, Any],
    nomes: Iterable[str | None],
    *,
    as_of: datetime,
) -> dict:
    """A regra Uranyx com o prazo que a equipe diz do modelo ÚNICO dos `nomes` (o item do pedido,
    o produto do registro do Painel, os produtos do anúncio); sem modelo único ou sem prazo
    combinado da equipe, a regra como veio."""
    modelo = modelo_unico(list(nomes))
    if modelo is None:
        return dict(regra)
    prazos = await prazos_da_equipe(session, as_of=as_of)
    return com_o_prazo(regra, prazos.get(modelo), modelo)
