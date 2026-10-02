"""Avaliações de venda no atendimento (RF8, 02/10/2026): Shopee e Mercado Livre.

Eduardo: "traz as avaliações das plataformas que der já". A avaliação entra
PRESA ao pedido/comprador: a aba ★ Avaliação de qualquer conversa do mesmo
pedido (ou do mesmo comprador) mostra as avaliações dele, lidas de
`atendimento_avaliacoes_loja`; e a avaliação SEM resposta da loja vira
PENDÊNCIA — uma conversa própria (`canal = 'avaliacao'`) e a etiqueta
AVALIAÇÃO no pedido, até ser respondida ou tratada ("depois volta ao status
anterior": o motor da etiqueta devolve o que os outros fatos dão).

O que entra e de onde (medido em produção em 02/10/2026, só GET):

  SHOPEE (14 lojas) — `GET /product/get_comment`, do mais novo para trás
    (decrescente por `create_time`, medido), `order_sn` em 100%. A primeira
    rodada de cada loja importa os últimos `JANELA` (30 dias, ~25 GET nas 14
    lojas); depois, 1 página de 100 por loja (para nas já conhecidas). As
    sem resposta que passaram da carência são RELIDAS pelo `comment_id` (1
    GET cada, com intervalo crescente): é assim que chega a resposta dada
    depois de a avaliação sair da 1ª página (a aguiar responde à mão, de 9
    a 29 dias depois) e a nota que o comprador editou. A loja RESPONDE pela
    API (`reply_comment`, resposta PÚBLICA) — pelo `enviar`, atrás do
    `atendimento_envio_ativo`.
  MERCADO LIVRE (23 contas) — a OPINIÃO do produto (`GET /reviews/item`), que
    traz o `order_id`; a avaliação da venda (`/orders/{id}/feedback`) está
    morta (0 de 60). Não há listagem por conta nem ordem por data, e a lista
    é do PRODUTO (`user_product_id`): anúncios irmãos — até de outra conta
    nossa — devolvem as mesmas opiniões. Então, a cada `INTERVALO_ML`:
    os anúncios vendidos nos últimos `JANELA_VENDAS_ML` (do espelho
    financeiro, `marketplace_order_financials.raw.order`, sem chamada), um
    por produto; 1 GET `limit=1` por produto para a IMPRESSÃO (`total` +
    `rating_levels`); só o produto que mudou é lido inteiro. A opinião é da
    conta DONA DO PEDIDO (o mesmo espelho; fora dele, 1 GET `/orders/{id}`
    e o `seller.id`); a de pedido alheio é ignorada. Sem resposta pela API:
    a pendência sai com "marcar como tratada".

FORA: TikTok (sem endpoint de avaliação para o vendedor — 404 "Invalid
path"), Magalu (sem API; 3 pedidos em 30 dias) e Amazon (só relatório 1–3★
por POST createReport; sem resposta pela API).

PENDENTE (`pendente_desde`) = SEM resposta da loja:
  • Shopee: nota 1–3 há mais de `CARENCIA_NOTA_BAIXA` (1 h) ou 4–5 há mais
    de `CARENCIA_NOTA_ALTA` (24 h), CONFERIDA de novo na Shopee depois da
    carência. Sem a carência, as ~55 avaliações por dia virariam pendência
    por 1 a 3 h e se resolveriam sozinhas (13 das 14 lojas têm um robô de
    fora que responde nesse tempo).
  • ML: nota 1–3 ainda não tratada (4–5 é só informação).
  • só as criadas nos últimos `JANELA` entram (a história não vira fila).
  Resolve com a resposta (de fora, lida aqui, ou a do DaVinci), com o
  "marcar como tratada", ou quando a avaliação some/é ocultada.

A CONVERSA `avaliacao` nasce só na pendência (não para as já respondidas —
seriam 57 por dia): `externo_id` = id da avaliação, a avaliação como
mensagem do cliente (com as fotos), a resposta da loja como mensagem da
loja. Fora de `CANAIS_POR_PLATAFORMA` (como a reclamação). Shopee: nasce
respondível (o envio desligado recusa com "resposta pública"); ML: nasce
BLOQUEADA, com o motivo. A vez da loja é a pendência (`CHAVE_VEZ_DA_LOJA`),
com prazo interno de `PRAZO_RESPOSTA` a partir dela (a plataforma não dá
prazo). Não se reaproveita o chat: só 27% das avaliações da Shopee têm
conversa do pedido, e a resposta é pública, por outro endpoint.

O cron `atendimento_avaliacoes` (o integrador registra no worker, minutos
{12, 42}, `timeout=1500` = a trava) só roda com o interruptor próprio
`ATENDIMENTO_AVALIACOES_ATIVA` E a leitura (`ATENDIMENTO_LEITURA_ATIVA`); uma
rodada por vez (trava no Redis). SÓ LEITURA na plataforma; erro de uma loja
não para as outras; log só com ids e contagens — NUNCA texto do comprador.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import DateTime, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    Integration,
    IntegrationPlatform,
    User,
)
from app.redis_client import redis
from app.services import links_shopee
from app.services.atendimento import etiqueta_fatos, gravar, indexar, indice, reclamacoes
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_AVALIACAO,
    CHAVE_PRAZO_LIDO_EM,
    CHAVE_PRAZO_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    NOTA_BAIXA_AVALIACAO,
    PLATAFORMAS_AVALIACAO,
)

logger = structlog.get_logger()

SHOPEE = "shopee"
ML = "ml"

# ── A janela e a carência ─────────────────────────────────────────────────
# A importação inicial e o que pode virar pendência: os últimos 30 dias.
JANELA = timedelta(days=30)
# O robô de fora responde entre 1,0 e 3 h (13 das 14 lojas; 495 de 496 em
# menos de 24 h na ATV): antes disto a avaliação sem resposta não é pendência.
CARENCIA_NOTA_BAIXA = timedelta(hours=1)
CARENCIA_NOTA_ALTA = timedelta(hours=24)
# O prazo da conversa `avaliacao` (interno: a plataforma não dá prazo para
# responder avaliação), contado de quando ela ficou pendente.
PRAZO_RESPOSTA = timedelta(hours=24)

# ── Shopee ────────────────────────────────────────────────────────────────
PAGINA_SHOPEE = 100  # o máximo do `get_comment`
MAX_PAGINAS_INCREMENTAL = 2
MAX_PAGINAS_IMPORTACAO = 10
# Releituras pelo `comment_id` por loja e rodada (1 GET cada).
MAX_RELEITURAS_LOJA = 30
# A sem resposta é relida com intervalo crescente: a recém-vencida a cada
# rodada; a pendente há dias (a aguiar responde de 9 a 29 dias depois),
# poucas vezes por dia — senão cada pendência seriam ~1.400 GET em 29 dias.
RELEITURA = (
    (timedelta(hours=6), timedelta(minutes=25)),
    (timedelta(days=3), timedelta(hours=3)),
)
RELEITURA_DEPOIS = timedelta(hours=12)
# Leituras pelo id VAZIAS seguidas para a avaliação contar como sumida (uma
# só pode ser falha passageira da lista; a marca tira da fila para sempre).
LEITURAS_VAZIAS_SUMIU = 2
# Marca no Redis: a importação da janela desta loja acabou ("ok") ou o
# cursor de onde continuar.
CHAVE_IMPORTADA_SHOPEE = "atendimento:avaliacoes:shopee:importada:{}"
IMPORTADA_OK = "ok"
IMPORTADA_TTL_S = 400 * 24 * 3600

# ── Mercado Livre ─────────────────────────────────────────────────────────
INTERVALO_ML = timedelta(hours=4)
CHAVE_ULTIMA_ML = "atendimento:avaliacoes:ml:ultima"
# Os anúncios a vigiar: os vendidos nesta janela; e o mapa pedido → conta,
# um pouco maior (a opinião chega dias depois da compra).
JANELA_VENDAS_ML = timedelta(days=45)
JANELA_PEDIDOS_ML = timedelta(days=120)
PAGINA_ML = 50
MAX_PAGINAS_PRODUTO_ML = 6
# Produtos lidos INTEIROS por conta e rodada (o resto fica para a próxima:
# a impressão só é gravada depois da leitura completa).
MAX_PRODUTOS_LIDOS_ML = 40
# `GET /orders/{id}` por conta e rodada (opinião de pedido fora do espelho).
MAX_PEDIDOS_CONSULTADOS_ML = 30
CHAVE_PRODUTO_ML = "atendimento:avaliacoes:ml:produto:{}"
PRODUTO_TTL_S = 14 * 24 * 3600
CHAVE_PEDIDO_ALHEIO_ML = "atendimento:avaliacoes:ml:alheio:{}"
ALHEIO_TTL_S = 30 * 24 * 3600
CONCORRENCIA_CONTAS_ML = 3
STATUS_ML_PUBLICADA = "published"
_NIVEIS_ML = ("one_star", "two_star", "three_star", "four_star", "five_star")

# ── A rodada ──────────────────────────────────────────────────────────────
# Menor que o intervalo do cron (30 min): se o processo morrer, a próxima roda.
TRAVA_TTL_S = 25 * 60
CHAVE_TRAVA = "atendimento:avaliacoes:rodada"

# O acontecimento entre parênteses na linha do tempo da etiqueta.
MOTIVO_ETIQUETA_LEITURA = "leitura das avaliações"
MOTIVO_ETIQUETA_RESPONDIDA = "avaliação respondida"
MOTIVO_ETIQUETA_DAVINCI = "resposta enviada pelo DaVinci"
MOTIVO_ETIQUETA_TRATADA = "avaliação marcada como tratada"

NOME_PLATAFORMA = {SHOPEE: "Shopee", ML: "Mercado Livre"}
# O aviso da caixa de resposta (RF8: "a resposta é pública; avisar").
AVISO_RESPOSTA_PUBLICA = (
    "A resposta à avaliação é PÚBLICA: aparece no anúncio, para qualquer comprador."
)
MOTIVO_ENVIO_DESLIGADO = (
    "Resposta pública (aparece no anúncio): o envio pelo DaVinci está desligado "
    "(ATENDIMENTO_ENVIO_ATIVO)."
)

FabricaCliente = Callable[[Integration], Awaitable[Any]]


def _agora() -> datetime:
    """Relógio do módulo (os testes trocam)."""
    return datetime.now(UTC)


def _erro(exc: BaseException) -> str:
    """Texto de operação para o log: classe, HTTP e código — nunca o corpo."""
    from app.services.atendimento.sync import erro_de_operacao

    return erro_de_operacao(exc)[0]


def _id(bruto: Any) -> str:
    return "" if bruto is None or isinstance(bruto, bool) else str(bruto).strip()


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat(timespec="seconds") if quando else None


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


async def _redis_get(chave: str) -> tuple[str | None, bool]:
    """(valor, o Redis respondeu?). Sem Redis, (None, False)."""
    try:
        bruto = await redis.get(chave)
    except Exception:  # noqa: BLE001
        return None, False
    if isinstance(bruto, bytes):
        bruto = bruto.decode("utf-8", "ignore")
    return (str(bruto) if bruto is not None else None), True


async def _redis_set(chave: str, valor: str, ttl: int) -> None:
    try:
        await redis.set(chave, valor, ex=ttl)
    except Exception as exc:  # noqa: BLE001 — a marca é economia, não correção
        logger.info("atendimento_avaliacoes_redis_falhou", erro=type(exc).__name__)


# ── Mercado Livre: a opinião → a linha (pura) ─────────────────────────────


def midia_ml(bruto: Any) -> list[dict]:
    """`media` da opinião (`[{type, url, thumbnail, ...}]`) → a lista da coluna `midia`."""
    saida: list[dict] = []
    for m in bruto if isinstance(bruto, list) else []:
        if not isinstance(m, dict):
            continue
        tipo = _id(m.get("type")).lower()
        item = {
            "tipo": "video" if tipo.startswith("video") else "imagem",
            "url": m.get("url"),
            "miniatura": m.get("thumbnail") or m.get("preview_url"),
        }
        saida.append(item)
    return saida


def opiniao_ml(rv: dict) -> dict | None:
    """Uma opinião do `/reviews/item` → argumentos de `indice.registrar_avaliacao`.

    Sem a conta, o comprador e o pack (quem chama acha pelo pedido). None =
    sem id, sem nota, sem pedido ou não publicada.
    """
    if not isinstance(rv, dict):
        return None
    rid = _id(rv.get("id"))
    pedido = _id(rv.get("order_id"))
    try:
        nota = int(rv.get("rate"))
    except (TypeError, ValueError):
        return None
    status = _id(rv.get("status")).lower()
    if not rid or not pedido or status not in ("", STATUS_ML_PUBLICADA):
        return None
    objeto = _dict(rv.get("reviewable_object"))
    return {
        "plataforma": ML,
        "comentario_id": rid,
        "pedido": pedido,
        "comprador_nome_loja": None,
        "item_id": _id(objeto.get("id")) or None,
        "estrelas": max(1, min(5, nota)),
        "titulo": rv.get("title") if isinstance(rv.get("title"), str) else None,
        "texto": rv.get("content") if isinstance(rv.get("content"), str) else None,
        "resposta_loja": None,
        "criado_em": reclamacoes.data_ml(rv.get("date_created")),
        "midia": midia_ml(rv.get("media")),
        "modelo_id": _id(rv.get("variation_id")) or None,
    }


def impressao_ml(corpo: dict) -> str:
    """O que muda quando o produto ganha (ou perde) opinião: o total e os níveis."""
    paging = _dict(corpo.get("paging"))
    niveis = _dict(corpo.get("rating_levels"))
    partes = [str(paging.get("total")), str(paging.get("total_pageable"))]
    partes += [str(niveis.get(n)) for n in _NIVEIS_ML]
    return "|".join(partes)


# ── Resumo de uma loja ────────────────────────────────────────────────────


@dataclass
class ResumoLoja:
    lidas: int = 0
    novas: int = 0
    paginas: int = 0
    relidas: int = 0
    sumidas: int = 0
    produtos: int = 0
    produtos_lidos: int = 0
    adiados: int = 0
    truncados: int = 0
    pedidos_consultados: int = 0
    alheias: int = 0
    antigas: int = 0
    erros: int = 0
    erro: str | None = None

    def como_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v or k in ("erros",)}


async def _desfazer(session: AsyncSession, integration: Integration) -> None:
    """Rollback da parte que falhou — e relê a loja, que o rollback expirou."""
    await session.rollback()
    await session.refresh(integration)


# ── Shopee: a leitura ─────────────────────────────────────────────────────


def _intervalo_releitura(idade: timedelta) -> timedelta:
    """De quanto em quanto tempo uma sem resposta é relida, pela idade dela."""
    for ate, intervalo in RELEITURA:
        if idade <= ate:
            return intervalo
    return RELEITURA_DEPOIS


def _carencia(estrelas: int) -> timedelta:
    return CARENCIA_NOTA_BAIXA if estrelas <= NOTA_BAIXA_AVALIACAO else CARENCIA_NOTA_ALTA


def _conferida_em(a: AtendimentoAvaliacaoLoja) -> datetime | None:
    return indice.de_iso(_dict(a.dados).get("conferida_em"))


def _passou_da_carencia(agora: datetime):
    """SQL: a avaliação sem resposta já passou da carência da nota dela."""
    a = AtendimentoAvaliacaoLoja
    return or_(
        and_(a.estrelas <= NOTA_BAIXA_AVALIACAO, a.criado_em <= agora - CARENCIA_NOTA_BAIXA),
        and_(a.estrelas > NOTA_BAIXA_AVALIACAO, a.criado_em <= agora - CARENCIA_NOTA_ALTA),
    )


def _nao_sumiu():
    a = AtendimentoAvaliacaoLoja
    return and_(
        a.dados["sumiu"].astext.is_distinct_from("true"),
        a.dados["oculta"].astext.is_distinct_from("true"),
    )


async def _candidatas_a_reler(
    session: AsyncSession, integration_id: UUID, agora: datetime
) -> list[AtendimentoAvaliacaoLoja]:
    """As sem resposta da loja que passaram da carência e estão na hora de reler."""
    a = AtendimentoAvaliacaoLoja
    linhas = (
        (
            await session.execute(
                select(a)
                .where(
                    a.integration_id == integration_id,
                    a.plataforma == SHOPEE,
                    a.resposta_loja.is_(None),
                    a.tratada_em.is_(None),
                    or_(a.pendente_desde.is_not(None), a.criado_em >= agora - JANELA),
                    _passou_da_carencia(agora),
                    _nao_sumiu(),
                )
                .order_by(a.criado_em.desc())
                .limit(MAX_RELEITURAS_LOJA * 10)
            )
        )
        .scalars()
        .all()
    )
    prontas = []
    for linha in linhas:
        conferida = _conferida_em(linha)
        criada = _utc(linha.criado_em) or agora
        if conferida is not None and conferida >= criada + _carencia(int(linha.estrelas)):
            # Já conferida depois da carência: só de novo no intervalo da idade.
            if agora - conferida < _intervalo_releitura(agora - criada):
                continue
        prontas.append(linha)
    # Nunca conferidas primeiro (são as que podem virar pendência agora).
    prontas.sort(
        key=lambda x: (
            _conferida_em(x) is not None,
            -(_utc(x.criado_em) or agora).timestamp(),
        )
    )
    return prontas[:MAX_RELEITURAS_LOJA]


async def _reler(
    session: AsyncSession,
    integration: Integration,
    cliente: Any,
    a: AtendimentoAvaliacaoLoja,
    agora: datetime,
    resumo: ResumoLoja,
) -> None:
    """1 GET pelo `comment_id`: grava o que mudou (resposta, nota) e carimba a conferência.

    Só carimba `conferida_em` com a avaliação lida E gravada: é a marca que
    deixa a sem resposta virar pendência (`_vira_pendente`).
    """
    resp = _dict(await cliente.get_comments(comment_id=a.comentario_id, page_size=1))
    achada = next(
        (
            c
            for c in resp.get("item_comment_list") or []
            if isinstance(c, dict) and _id(c.get("comment_id")) == a.comentario_id
        ),
        None,
    )
    resumo.relidas += 1
    if achada is None:
        # A Shopee não devolveu a avaliação (apagada, escondida — ou a lista
        # veio vazia por uma falha passageira). "Sumiu" (não é pendência de
        # ninguém e não é mais relida) só na LEITURAS_VAZIAS_SUMIU-ésima vazia
        # seguida; antes disso perde a marca de conferida: volta na próxima
        # rodada e não vira pendência nesse meio-tempo.
        # Cópia: o mesmo dict de volta não conta como mudança para o ORM.
        dados = dict(_dict(a.dados))
        vazias = dados.get("vazias")
        dados["vazias"] = (vazias if isinstance(vazias, int) else 0) + 1
        dados.pop("conferida_em", None)
        if dados["vazias"] >= LEITURAS_VAZIAS_SUMIU:
            dados["sumiu"] = True
            resumo.sumidas += 1
        a.dados = dados
        await session.flush()
        return
    linha = indice.avaliacao_shopee(achada)
    if linha is None or not await indice.registrar_avaliacao(
        session, integration_id=integration.id, **linha
    ):
        # Não deu para ler (nota ilegível) ou gravar (erro de banco, engolido
        # no SAVEPOINT): sem a marca — senão a já respondida na Shopee virava
        # pendência. Tenta de novo na próxima rodada.
        resumo.erros += 1
        return
    await session.flush()
    await session.refresh(a)
    a.dados = {**_dict(a.dados), "conferida_em": _iso(agora)}
    await session.flush()


async def sincronizar_loja_shopee(
    integration: Integration | UUID,
    *,
    fabrica_cliente: FabricaCliente | None = None,
    agora: datetime | None = None,
    importar: bool | None = None,
) -> dict:
    """As avaliações de UMA loja da Shopee, na sessão própria. Nunca levanta.

    A página do topo (ou a importação da janela, na primeira vez da loja —
    `importar=True` força) e as releituras pelo id. Commita por parte.
    """
    from app.services.atendimento import clientes

    fabrica = fabrica_cliente or clientes.cliente_da_integracao
    agora = agora or _agora()
    integration_id = integration.id if isinstance(integration, Integration) else integration
    resumo = ResumoLoja()
    async with _db.SessionLocal() as session:
        integ = await session.get(Integration, integration_id)
        plataforma = getattr(getattr(integ, "platform", None), "value", None)
        if integ is None or integ.archived_at is not None or plataforma != SHOPEE:
            return resumo.como_dict()
        try:
            cliente = await fabrica(integ)
        except Exception as exc:  # noqa: BLE001 — loja sem credencial não para as outras
            resumo.erros += 1
            resumo.erro = _erro(exc)
            logger.warning(
                "atendimento_avaliacoes_cliente_falhou",
                integration_id=str(integration_id),
                erro=resumo.erro,
            )
            return resumo.como_dict()

        chave = CHAVE_IMPORTADA_SHOPEE.format(integration_id)
        marca, redis_ok = await _redis_get(chave)
        if importar is None:
            # Sem Redis não se sabe se já importou: a página do topo basta
            # (a importação de novo a cada rodada seria cota à toa).
            importar = redis_ok and marca != IMPORTADA_OK
        try:
            if importar:
                cursor = marca if marca and marca != IMPORTADA_OK else ""
                leitura = await indexar.indexar_avaliacoes(
                    session,
                    integ,
                    cliente,
                    max_paginas=MAX_PAGINAS_IMPORTACAO,
                    desde=agora - JANELA,
                    parar_nas_conhecidas=False,
                    cursor=cursor,
                    page_size=PAGINA_SHOPEE,
                )
            else:
                leitura = await indexar.indexar_avaliacoes(
                    session,
                    integ,
                    cliente,
                    max_paginas=MAX_PAGINAS_INCREMENTAL,
                    page_size=PAGINA_SHOPEE,
                )
            await session.commit()
            resumo.paginas += leitura.paginas
            resumo.novas += leitura.novas
            if importar:
                await _redis_set(
                    chave, IMPORTADA_OK if leitura.acabou else leitura.cursor, IMPORTADA_TTL_S
                )
        except Exception as exc:  # noqa: BLE001 — a loja que falha não para as outras
            await _desfazer(session, integ)
            resumo.erros += 1
            resumo.erro = _erro(exc)
            logger.warning(
                "atendimento_avaliacoes_shopee_pagina_falhou",
                integration_id=str(integration_id),
                erro=resumo.erro,
            )

        # Só os ids: um rollback no meio expira os objetos lidos antes. A
        # loja cuja página falhou (token, permissão, 5xx) não é relida agora:
        # seriam até `MAX_RELEITURAS_LOJA` erros iguais; a próxima rodada tenta.
        candidatas = (
            []
            if resumo.erro
            else [
                (x.id, x.comentario_id)
                for x in await _candidatas_a_reler(session, integration_id, agora)
            ]
        )
        for aid, cid in candidatas:
            try:
                a = await session.get(AtendimentoAvaliacaoLoja, aid, populate_existing=True)
                if a is None:
                    continue
                await _reler(session, integ, cliente, a, agora, resumo)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 — uma não para a loja
                await _desfazer(session, integ)
                resumo.erros += 1
                logger.info(
                    "atendimento_avaliacoes_releitura_falhou",
                    integration_id=str(integration_id),
                    comentario_id=cid,
                    erro=_erro(exc),
                )
    logger.info(
        "atendimento_avaliacoes_shopee_loja",
        integration_id=str(integration_id),
        **{k: v for k, v in resumo.como_dict().items() if k != "erro"},
    )
    return resumo.como_dict()


async def lojas_shopee(session: AsyncSession) -> list[UUID]:
    """As lojas da Shopee lidas: não arquivadas e sem o canal `desligado` (o do 429)."""
    return list(
        (
            await session.execute(
                select(Integration.id)
                .where(
                    Integration.platform == IntegrationPlatform(SHOPEE),
                    Integration.archived_at.is_(None),
                    ~indexar.canal_desligado(Integration.id),
                )
                .order_by(Integration.name)
            )
        )
        .scalars()
        .all()
    )


async def sincronizar_todas_shopee(
    *,
    fabrica_cliente: FabricaCliente | None = None,
    agora: datetime | None = None,
    importar: bool | None = None,
) -> dict:
    """Todas as lojas da Shopee, uma de cada vez. Nunca levanta.

    `importar=True` força a importação da janela em todas (o script).
    """
    agora = agora or _agora()
    async with _db.SessionLocal() as session:
        ids = await lojas_shopee(session)
    total: dict[str, Any] = {"lojas": len(ids), "lojas_com_erro": 0}
    for integration_id in ids:
        try:
            r = await sincronizar_loja_shopee(
                integration_id, fabrica_cliente=fabrica_cliente, agora=agora, importar=importar
            )
        except Exception as exc:  # noqa: BLE001 — banco fora: as outras seguem
            logger.error(
                "atendimento_avaliacoes_loja_quebrou",
                integration_id=str(integration_id),
                err=type(exc).__name__,
            )
            total["lojas_com_erro"] += 1
            continue
        for k, v in r.items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
        if r.get("erro"):
            total["lojas_com_erro"] += 1
    return total


# ── Mercado Livre: a leitura ──────────────────────────────────────────────


@dataclass(frozen=True)
class PedidoML:
    """O que o espelho financeiro sabe de um pedido do ML (sem nome do comprador)."""

    integration_id: UUID
    comprador_id: str | None
    apelido: str | None
    pack_id: str | None
    titulos: dict[str, str] = field(default_factory=dict)


@dataclass
class RodadaML:
    """O que as contas do ML dividem numa rodada (os produtos já vistos, os mapas)."""

    pedidos: dict[str, PedidoML]
    sellers: dict[str, UUID]
    vistos: set[str] = field(default_factory=set)
    # Produtos lidos inteiros por conta nesta rodada (o script da importação
    # inicial passa mais).
    max_produtos: int = MAX_PRODUTOS_LIDOS_ML


def _sql_pedidos_ml():
    """As linhas-item dos pedidos do ML no espelho financeiro (`raw.order`).

    SQL cru (os `jsonb_array_elements`), com o schema das settings: a conexão
    da api não tem `search_path` (o mesmo cuidado de `logistica_ingest`).
    """
    schema = get_settings().database_schema
    return text(
        f"""
        SELECT m.integration_id,
               m.raw->'order'->>'id'                         AS pedido,
               m.raw->'order'->'buyer'->>'id'                AS comprador,
               m.raw->'order'->'buyer'->>'nickname'          AS apelido,
               COALESCE(NULLIF(m.raw->>'pack_id', ''), m.raw->'order'->>'pack_id') AS pack,
               oi->'item'->>'id'                             AS item,
               oi->'item'->>'user_product_id'                AS produto,
               oi->'item'->>'title'                          AS titulo,
               m.created_at                                  AS criado
          FROM "{schema}".marketplace_order_financials m
          CROSS JOIN LATERAL jsonb_array_elements(
               CASE WHEN jsonb_typeof(m.raw->'order'->'order_items') = 'array'
                    THEN m.raw->'order'->'order_items' ELSE '[]'::jsonb END) AS oi
         WHERE m.platform = 'ml'
           AND m.integration_id IS NOT NULL
           AND m.created_at >= :desde
        """  # noqa: S608 — o schema vem das settings, nunca de fora
    )


async def _linhas_do_espelho(session: AsyncSession, desde: datetime) -> list[Any]:
    """As linhas-item dos pedidos do ML no espelho financeiro (nada de API)."""
    return list((await session.execute(_sql_pedidos_ml(), {"desde": desde})).all())


def mapa_de_pedidos(linhas: Iterable[Any]) -> dict[str, PedidoML]:
    """{order_id: PedidoML} a partir das linhas do espelho. PURA."""
    mapa: dict[str, PedidoML] = {}
    for r in linhas:
        pedido = _id(r.pedido)
        if not pedido:
            continue
        atual = mapa.get(pedido)
        titulos = dict(atual.titulos) if atual else {}
        if _id(r.item) and isinstance(r.titulo, str) and r.titulo.strip():
            titulos[_id(r.item)] = r.titulo.strip()[:300]
        pack = _id(r.pack)
        mapa[pedido] = PedidoML(
            integration_id=r.integration_id,
            comprador_id=_id(r.comprador) or (atual.comprador_id if atual else None),
            apelido=_id(r.apelido) or (atual.apelido if atual else None),
            pack_id=pack if pack and pack != pedido else (atual.pack_id if atual else None),
            titulos=titulos,
        )
    return mapa


def anuncios_por_produto(
    linhas: Iterable[Any], integration_id: UUID, desde: datetime
) -> dict[str, str]:
    """{produto: um anúncio dele} vendidos pela conta desde `desde`. PURA.

    O produto é o `user_product_id` (as opiniões são dele, não do anúncio);
    sem ele, o próprio anúncio.
    """
    saida: dict[str, str] = {}
    for r in linhas:
        if r.integration_id != integration_id:
            continue
        criado = _utc(r.criado)
        if criado is not None and criado < desde:
            continue
        item = _id(r.item)
        if not item:
            continue
        saida.setdefault(_id(r.produto) or item, item)
    return saida


async def sellers_ml(session: AsyncSession) -> dict[str, UUID]:
    """{user_id do vendedor no ML: integração} — para achar a conta dona de um pedido."""
    from app.security.cipher import decrypt_json

    saida: dict[str, UUID] = {}
    for integ in (
        (
            await session.execute(
                select(Integration).where(
                    Integration.platform == IntegrationPlatform(ML),
                    Integration.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    ):
        try:
            uid = _id(_dict(decrypt_json(integ.credentials)).get("user_id"))
        except Exception as exc:  # noqa: BLE001 — credencial ilegível: a conta fica sem mapa
            logger.info(
                "atendimento_avaliacoes_credencial_ilegivel",
                integration_id=str(integ.id),
                erro=type(exc).__name__,
            )
            continue
        if uid:
            saida[uid] = integ.id
    return saida


async def _dono_do_pedido(
    cliente: Any,
    integ: Integration,
    pedido: str,
    rodada: RodadaML,
    resumo: ResumoLoja,
) -> PedidoML | None | bool:
    """A conta dona do pedido da opinião. False = não deu para saber agora (cota).

    O espelho primeiro; fora dele, a marca de "pedido alheio" no Redis; e
    só então 1 GET `/orders/{id}` com o `seller.id` (o pedido de outra conta
    nossa vai para ela; o de outro vendedor é ignorado e marcado).
    """
    conhecido = rodada.pedidos.get(pedido)
    if conhecido is not None:
        return conhecido
    alheio, _ = await _redis_get(CHAVE_PEDIDO_ALHEIO_ML.format(pedido))
    if alheio:
        return None
    if resumo.pedidos_consultados >= MAX_PEDIDOS_CONSULTADOS_ML:
        return False
    resumo.pedidos_consultados += 1
    try:
        corpo = _dict(await cliente.pedido(pedido))
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (403, 404):
            await _redis_set(CHAVE_PEDIDO_ALHEIO_ML.format(pedido), "1", ALHEIO_TTL_S)
            return None
        raise
    seller = _id(_dict(corpo.get("seller")).get("id"))
    dona = rodada.sellers.get(seller)
    if dona is None:
        await _redis_set(CHAVE_PEDIDO_ALHEIO_ML.format(pedido), "1", ALHEIO_TTL_S)
        return None
    comprador = _dict(corpo.get("buyer"))
    titulos = {}
    for ln in corpo.get("order_items") or []:
        item = _dict(_dict(ln).get("item"))
        if _id(item.get("id")) and isinstance(item.get("title"), str):
            titulos[_id(item.get("id"))] = item["title"].strip()[:300]
    pack = _id(corpo.get("pack_id"))
    achado = PedidoML(
        integration_id=dona,
        comprador_id=_id(comprador.get("id")) or None,
        apelido=_id(comprador.get("nickname")) or None,
        pack_id=pack if pack and pack != pedido else None,
        titulos=titulos,
    )
    rodada.pedidos[pedido] = achado
    return achado


async def _gravar_opiniao(
    session: AsyncSession,
    cliente: Any,
    integ: Integration,
    rv: dict,
    produto: str,
    rodada: RodadaML,
    agora: datetime,
    resumo: ResumoLoja,
) -> bool:
    """UMA opinião → a linha da conta dona do pedido. False = adiada (cota de pedidos)."""
    linha = opiniao_ml(rv)
    if linha is None:
        return True
    criado = linha["criado_em"]
    if criado is not None and criado < agora - JANELA:
        resumo.antigas += 1
        return True
    dono = await _dono_do_pedido(cliente, integ, linha["pedido"], rodada, resumo)
    if dono is False:
        return False
    if dono is None:
        resumo.alheias += 1
        return True
    dados: dict[str, Any] = {"fonte": "ml_reviews", "produto": produto}
    if dono.pack_id:
        dados["pack_id"] = dono.pack_id
    titulo = dono.titulos.get(_id(linha["item_id"])) or next(iter(dono.titulos.values()), None)
    if titulo:
        dados["anuncio_titulo"] = titulo
    if await indice.registrar_avaliacao(
        session,
        integration_id=dono.integration_id,
        comprador_id=dono.comprador_id,
        dados=dados,
        **{**linha, "comprador_nome_loja": dono.apelido},
    ):
        resumo.lidas += 1
    return True


async def _ler_produto(
    session: AsyncSession,
    cliente: Any,
    integ: Integration,
    produto: str,
    item: str,
    rodada: RodadaML,
    agora: datetime,
    resumo: ResumoLoja,
) -> None:
    """Um PRODUTO: a impressão (1 GET) e, se mudou, as opiniões inteiras."""
    topo = _dict(await cliente.opinioes_do_anuncio(item, limit=1, offset=0))
    upid = _id(topo.get("user_product_id")) or produto
    if upid != produto:
        # O espelho não tinha o produto do anúncio: o ML diz qual é.
        if upid in rodada.vistos:
            return
        rodada.vistos.add(upid)
    impressao = impressao_ml(topo)
    chave = CHAVE_PRODUTO_ML.format(upid)
    guardada, _ = await _redis_get(chave)
    if guardada == impressao:
        return
    if resumo.produtos_lidos >= rodada.max_produtos:
        resumo.adiados += 1
        return
    resumo.produtos_lidos += 1
    paginavel = _dict(topo.get("paging")).get("total_pageable")
    total = paginavel if isinstance(paginavel, int) else _dict(topo.get("paging")).get("total")
    completo = True
    offset = 0
    for _ in range(MAX_PAGINAS_PRODUTO_ML):
        corpo = _dict(await cliente.opinioes_do_anuncio(item, limit=PAGINA_ML, offset=offset))
        lote = [r for r in corpo.get("reviews") or [] if isinstance(r, dict)]
        for rv in lote:
            if not await _gravar_opiniao(session, cliente, integ, rv, upid, rodada, agora, resumo):
                completo = False
        offset += len(lote)
        if len(lote) < PAGINA_ML or (isinstance(total, int) and offset >= total):
            break
    else:
        # Bateu o teto de páginas (mais de MAX_PAGINAS_PRODUTO_ML × PAGINA_ML
        # opiniões pagináveis): o resto não cabe em rodada nenhuma. Conta à
        # parte e grava a impressão — como "adiado" ele voltava em TODA rodada
        # e segurava a marca do ML (a leitura rodava a cada 30 min, não 4 h).
        resumo.truncados += 1
    await session.commit()
    if completo:
        # Só com a leitura inteira: o que ficou para trás volta na próxima.
        await _redis_set(chave, impressao, PRODUTO_TTL_S)
    else:
        resumo.adiados += 1


async def sincronizar_conta_ml(
    integration: Integration | UUID,
    *,
    rodada: RodadaML,
    linhas: Sequence[Any],
    fabrica_cliente: FabricaCliente | None = None,
    agora: datetime | None = None,
) -> dict:
    """As opiniões dos produtos vendidos por UMA conta do ML. Nunca levanta."""
    from app.services.atendimento import clientes

    fabrica = fabrica_cliente or clientes.cliente_da_integracao
    agora = agora or _agora()
    integration_id = integration.id if isinstance(integration, Integration) else integration
    resumo = ResumoLoja()
    async with _db.SessionLocal() as session:
        integ = await session.get(Integration, integration_id)
        plataforma = getattr(getattr(integ, "platform", None), "value", None)
        if integ is None or integ.archived_at is not None or plataforma != ML:
            return resumo.como_dict()
        produtos = anuncios_por_produto(linhas, integration_id, agora - JANELA_VENDAS_ML)
        resumo.produtos = len(produtos)
        if not produtos:
            return resumo.como_dict()
        try:
            cliente = await fabrica(integ)
        except Exception as exc:  # noqa: BLE001
            resumo.erros += 1
            resumo.erro = _erro(exc)
            logger.warning(
                "atendimento_avaliacoes_cliente_falhou",
                integration_id=str(integration_id),
                erro=resumo.erro,
            )
            return resumo.como_dict()
        for produto, item in produtos.items():
            if produto in rodada.vistos:
                continue  # outra conta (anúncio irmão) já leu este produto
            # Marcado ANTES da ida ao ML: as contas rodam em paralelo.
            rodada.vistos.add(produto)
            try:
                await _ler_produto(session, cliente, integ, produto, item, rodada, agora, resumo)
            except Exception as exc:  # noqa: BLE001 — um produto não para a conta
                await _desfazer(session, integ)
                resumo.erros += 1
                resumo.erro = _erro(exc)
                logger.info(
                    "atendimento_avaliacoes_produto_falhou",
                    integration_id=str(integration_id),
                    item_id=item,
                    erro=resumo.erro,
                )
    logger.info(
        "atendimento_avaliacoes_ml_conta",
        integration_id=str(integration_id),
        **{k: v for k, v in resumo.como_dict().items() if k != "erro"},
    )
    return resumo.como_dict()


async def sincronizar_todas_ml(
    *,
    fabrica_cliente: FabricaCliente | None = None,
    agora: datetime | None = None,
    max_produtos: int | None = None,
) -> dict:
    """Todas as contas do ML com venda na janela, `CONCORRENCIA_CONTAS_ML` por vez.

    `adiados` > 0 no resumo = ficou produto para ler (o teto por conta, ou a
    cota de pedidos): o cron volta ao ML na rodada seguinte, sem esperar
    `INTERVALO_ML` — é assim que a importação inicial anda sozinha.
    """
    agora = agora or _agora()
    async with _db.SessionLocal() as session:
        linhas = await _linhas_do_espelho(session, agora - JANELA_PEDIDOS_ML)
        rodada = RodadaML(
            pedidos=mapa_de_pedidos(linhas),
            sellers=await sellers_ml(session),
            max_produtos=max(
                1, int(MAX_PRODUTOS_LIDOS_ML if max_produtos is None else max_produtos)
            ),
        )
        ids = await reclamacoes.contas_ml(session, agora)
    sem = asyncio.Semaphore(CONCORRENCIA_CONTAS_ML)

    async def _uma(integration_id: UUID) -> dict:
        async with sem:
            return await sincronizar_conta_ml(
                integration_id,
                rodada=rodada,
                linhas=linhas,
                fabrica_cliente=fabrica_cliente,
                agora=agora,
            )

    resultados = await asyncio.gather(*(_uma(i) for i in ids), return_exceptions=True)
    total: dict[str, Any] = {"contas": len(ids), "contas_com_erro": 0}
    for integration_id, r in zip(ids, resultados, strict=True):
        if isinstance(r, BaseException):
            logger.error(
                "atendimento_avaliacoes_conta_quebrou",
                integration_id=str(integration_id),
                err=type(r).__name__,
            )
            total["contas_com_erro"] += 1
            continue
        for k, v in r.items():
            if isinstance(v, int):
                total[k] = total.get(k, 0) + v
        if r.get("erro"):
            total["contas_com_erro"] += 1
    return total


async def _vez_do_ml(agora: datetime) -> bool:
    """Já passou `INTERVALO_ML` desde a última leitura do ML? Sem Redis, não (cota)."""
    ultima, ok = await _redis_get(CHAVE_ULTIMA_ML)
    if not ok:
        return False
    quando = indice.de_iso(ultima)
    return quando is None or agora - quando >= INTERVALO_ML


# ── A pendência e a conversa ──────────────────────────────────────────────


def _hash(*partes: Any) -> str:
    base = "|".join("" if p is None else str(p) for p in partes)
    return hashlib.sha1(base.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def _versao(a: AtendimentoAvaliacaoLoja) -> str:
    """A versão da avaliação (nota, título e texto): a editada pelo comprador é outra."""
    return _hash(a.estrelas, a.titulo, a.texto)


def texto_da_avaliacao(a: AtendimentoAvaliacaoLoja) -> str:
    """A avaliação como o balão mostra: "Avaliação 2★ — título: texto"."""
    cabeca = f"Avaliação {a.estrelas}★"
    partes = [p.strip() for p in (a.titulo, a.texto) if isinstance(p, str) and p.strip()]
    return f"{cabeca} — {': '.join(partes)}" if partes else f"{cabeca} (sem comentário)"


def _anexos(a: AtendimentoAvaliacaoLoja) -> list[dict]:
    saida = []
    for m in a.midia or []:
        if isinstance(m, dict) and isinstance(m.get("url"), str):
            item = {"tipo": m.get("tipo") or "imagem", "url": m["url"]}
            if m.get("miniatura"):
                item["miniatura"] = m["miniatura"]
            saida.append(item)
    return saida


def _dados_da_conversa(a: AtendimentoAvaliacaoLoja, agora: datetime) -> dict[str, Any]:
    """O que a conversa `avaliacao` guarda em `dados` (a vez e o prazo são da pendência)."""
    pendente = _utc(a.pendente_desde)
    dados: dict[str, Any] = {
        "avaliacao_id": str(a.id),
        "comentario_id": a.comentario_id,
        "estrelas": int(a.estrelas),
        # A versão que as mensagens da conversa já mostram (`_gravar_mensagens`).
        "versao": _versao(a),
        "resposta_publica": bool(a.pode_responder),
        # A VEZ é da loja enquanto a avaliação estiver pendente; o prazo é
        # interno (a plataforma não dá prazo para responder avaliação).
        CHAVE_VEZ_DA_LOJA: _iso(pendente),
        CHAVE_PRAZO_PLATAFORMA: _iso(pendente + PRAZO_RESPOSTA) if pendente else None,
        CHAVE_PRAZO_LIDO_EM: _iso(agora),
    }
    pack = _id(_dict(a.dados).get("pack_id"))
    if a.plataforma == ML:
        dados["order_id"] = a.pedido
        if pack:
            dados["pack_id"] = pack
    return dados


async def _comprador_do_pedido(session: AsyncSession, a: AtendimentoAvaliacaoLoja) -> str | None:
    """Shopee: o `buyer_user_id` do pedido, pelo índice de pedidos (o `to_id` do chat)."""
    if a.comprador_id or not a.pedido:
        return a.comprador_id
    return await session.scalar(
        select(AtendimentoPedidoComprador.comprador_id)
        .where(
            AtendimentoPedidoComprador.integration_id == a.integration_id,
            AtendimentoPedidoComprador.pedido == a.pedido,
        )
        .limit(1)
    )


async def _gravar_mensagens(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    a: AtendimentoAvaliacaoLoja,
    agora: datetime,
) -> int:
    """A avaliação (cliente), a resposta (loja) e a marca de tratada (sistema). Idempotente."""
    novas = 0
    itens: list[dict[str, Any]] = [
        {
            # Uma linha por VERSÃO: a nota editada pelo comprador vira outra.
            "externo_id": f"avaliacao:{a.comentario_id}:{_versao(a)}",
            "autor": AUTOR_CLIENTE,
            "texto": texto_da_avaliacao(a),
            "enviada_em": _utc(a.criado_em) or agora,
            "anexos": _anexos(a),
            "payload": {
                "avaliacao": True,
                "estrelas": int(a.estrelas),
                **({"editavel": a.editavel} if a.editavel else {}),
            },
        }
    ]
    if a.resposta_loja:
        itens.append(
            {
                # O mesmo id que o envio pelo DaVinci grava (`shopee.
                # responder_avaliacao`): a resposta que saiu daqui não duplica.
                "externo_id": f"resposta:{a.comentario_id}",
                "autor": AUTOR_LOJA,
                "texto": a.resposta_loja,
                "enviada_em": _utc(a.resposta_em) or agora,
                "anexos": [],
                "payload": {"resposta_avaliacao": True},
            }
        )
    if a.tratada_em is not None:
        itens.append(
            {
                "externo_id": f"tratada:{_iso(_utc(a.tratada_em))}",
                "autor": AUTOR_SISTEMA,
                "texto": "Avaliação marcada como tratada no DaVinci.",
                "enviada_em": _utc(a.tratada_em),
                "anexos": [],
                "payload": {},
            }
        )
    for item in itens:
        _, criada = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=item["externo_id"],
            autor=item["autor"],
            texto=item["texto"],
            enviada_em=item["enviada_em"],
            tipo="texto",
            anexos=item["anexos"],
            payload=item["payload"],
            origem=None,
        )
        novas += int(criada)
    return novas


async def garantir_conversa(
    session: AsyncSession, a: AtendimentoAvaliacaoLoja, agora: datetime
) -> AtendimentoConversa:
    """A conversa `avaliacao` da avaliação (cria se não há), com as mensagens e a vez.

    Não commita.
    """
    integ = await session.get(Integration, a.integration_id)
    comprador = await _comprador_do_pedido(session, a)
    if comprador and not a.comprador_id:
        a.comprador_id = comprador
    dados = _dict(a.dados)
    conversa, _ = await gravar.upsert_conversa(
        session,
        canal=None,
        integration=integ,
        plataforma=a.plataforma,
        canal_nome=CANAL_AVALIACAO,
        externo_id=a.comentario_id,
        # Sem `conta`: o gravar põe o nome da LOJA (num SAVEPOINT — o cadastro
        # de lojas com problema não derruba a pendência).
        conta=None,
        comprador_id=comprador,
        comprador_nome=a.comprador_nome_loja,
        pedido_marketplace=a.pedido,
        anuncio_id=a.item_id,
        anuncio_titulo=titulo if isinstance(titulo := dados.get("anuncio_titulo"), str) else None,
        dados=_dados_da_conversa(a, agora),
    )
    if not a.pode_responder and conversa.situacao != CONVERSA_FECHADA:
        # ML: a plataforma não deixa responder pela API — o motivo no lugar
        # da caixa (RF8), a IA não sugere e o envio recusa.
        motivo = a.motivo_sem_resposta or indice.MOTIVO_SEM_RESPOSTA.get(a.plataforma)
        if conversa.situacao != CONVERSA_BLOQUEADA or conversa.bloqueio_motivo != motivo:
            conversa.situacao = CONVERSA_BLOQUEADA
            conversa.bloqueio_motivo = motivo
    await _gravar_mensagens(session, conversa, a, agora)
    # A vez e o prazo podem ter mudado sem mensagem nova: refaz a fila.
    conversa.dados = {**(conversa.dados or {}), **_dados_da_conversa(a, agora)}
    gravar.recalcular(conversa)
    a.conversa_id = conversa.id
    await session.flush()
    return conversa


async def _recalcular_etiquetas(
    session: AsyncSession,
    a: AtendimentoAvaliacaoLoja,
    conversa: AtendimentoConversa | None,
    *,
    motivo: str,
    agora: datetime,
) -> int:
    """A etiqueta da conversa da avaliação e das conversas do mesmo pedido."""
    pack = _id(_dict(a.dados).get("pack_id")) or None
    alvo = await reclamacoes.conversas_para_etiqueta(
        session, a.plataforma, [a.pedido, pack], [conversa] if conversa is not None else []
    )
    return await reclamacoes.recalcular_etiquetas(session, alvo, motivo=motivo, agora=agora)


def _resolvida():
    """SQL: o que tira a avaliação da pendência."""
    a = AtendimentoAvaliacaoLoja
    return or_(
        a.resposta_loja.is_not(None),
        a.tratada_em.is_not(None),
        a.dados["sumiu"].astext == "true",
        a.dados["oculta"].astext == "true",
        and_(a.pode_responder.is_(False), a.estrelas > NOTA_BAIXA_AVALIACAO),
    )


def _vira_pendente(agora: datetime):
    """SQL: a avaliação que vira pendência agora (ver o topo do módulo)."""
    a = AtendimentoAvaliacaoLoja
    conferida = cast(a.dados["conferida_em"].astext, DateTime(timezone=True))
    shopee = and_(
        a.pode_responder.is_(True),
        a.resposta_loja.is_(None),
        or_(
            and_(
                a.estrelas <= NOTA_BAIXA_AVALIACAO,
                a.criado_em <= agora - CARENCIA_NOTA_BAIXA,
                conferida >= a.criado_em + CARENCIA_NOTA_BAIXA,
            ),
            and_(
                a.estrelas > NOTA_BAIXA_AVALIACAO,
                a.criado_em <= agora - CARENCIA_NOTA_ALTA,
                conferida >= a.criado_em + CARENCIA_NOTA_ALTA,
            ),
        ),
    )
    sem_resposta_possivel = and_(a.pode_responder.is_(False), a.estrelas <= NOTA_BAIXA_AVALIACAO)
    # Loja arquivada não é lida nem respondida: a avaliação dela não vira fila.
    loja_ativa = a.integration_id.in_(
        select(Integration.id).where(Integration.archived_at.is_(None))
    )
    return and_(
        a.plataforma.in_(PLATAFORMAS_AVALIACAO),
        a.pendente_desde.is_(None),
        a.tratada_em.is_(None),
        a.criado_em >= agora - JANELA,
        _nao_sumiu(),
        loja_ativa,
        or_(shopee, sem_resposta_possivel),
    )


async def _marcar_pendente(
    session: AsyncSession, a: AtendimentoAvaliacaoLoja, agora: datetime
) -> AtendimentoConversa:
    a.pendente_desde = agora
    a.atualizado_em = agora
    await session.flush()
    conversa = await garantir_conversa(session, a, agora)
    await _recalcular_etiquetas(session, a, conversa, motivo=MOTIVO_ETIQUETA_LEITURA, agora=agora)
    return conversa


async def resolver(
    session: AsyncSession, a: AtendimentoAvaliacaoLoja, *, motivo: str, agora: datetime
) -> None:
    """Tira da pendência: a conversa ganha a resposta/a marca e sai da fila; a etiqueta volta."""
    a.pendente_desde = None
    a.atualizado_em = agora
    await session.flush()
    conversa = None
    if a.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, a.conversa_id)
        if conversa is not None:
            conversa = await garantir_conversa(session, a, agora)
    await _recalcular_etiquetas(session, a, conversa, motivo=motivo, agora=agora)


def _motivo_da_resolucao(a: AtendimentoAvaliacaoLoja) -> str:
    if a.tratada_em is not None:
        return MOTIVO_ETIQUETA_TRATADA
    if a.resposta_loja:
        return MOTIVO_ETIQUETA_RESPONDIDA
    return MOTIVO_ETIQUETA_LEITURA


async def atualizar_pendencias(*, agora: datetime | None = None) -> dict:
    """A pendência de todas as avaliações (só banco), na sessão própria. Nunca levanta.

    Primeiro as que se RESOLVERAM (resposta chegou, tratada, sumiu), depois
    as que VIRAM pendentes e, por fim, as pendentes que o comprador EDITOU
    (nota, título ou texto): a conversa ganha a versão nova. Commita por
    avaliação: cada uma mexe na etiqueta das conversas do pedido, e uma
    transação só seguraria essas linhas.
    """
    agora = agora or _agora()
    a = AtendimentoAvaliacaoLoja
    resumo = {"pendentes_novas": 0, "resolvidas": 0, "atualizadas": 0, "erros": 0}
    async with _db.SessionLocal() as session:
        for chave, filtro in (
            ("resolvidas", and_(a.pendente_desde.is_not(None), _resolvida())),
            ("pendentes_novas", _vira_pendente(agora)),
        ):
            ids = list(
                (await session.execute(select(a.id).where(filtro).order_by(a.criado_em)))
                .scalars()
                .all()
            )
            for aid in ids:
                try:
                    linha = await session.get(a, aid, populate_existing=True)
                    if linha is None:
                        continue
                    if chave == "resolvidas":
                        await resolver(
                            session, linha, motivo=_motivo_da_resolucao(linha), agora=agora
                        )
                    else:
                        await _marcar_pendente(session, linha, agora)
                    await session.commit()
                    resumo[chave] += 1
                except Exception as exc:  # noqa: BLE001 — uma não para as outras
                    await session.rollback()
                    resumo["erros"] += 1
                    logger.warning(
                        "atendimento_avaliacao_pendencia_falhou",
                        avaliacao_id=str(aid),
                        erro=type(exc).__name__,
                    )
        # Pendentes (poucas) cuja conversa mostra outra versão: só banco.
        pendentes = (
            await session.execute(
                select(a.id, a.estrelas, a.titulo, a.texto, AtendimentoConversa.dados)
                .join(AtendimentoConversa, AtendimentoConversa.id == a.conversa_id)
                .where(a.pendente_desde.is_not(None))
            )
        ).all()
        for aid, estrelas, titulo, texto, dados_conversa in pendentes:
            if _dict(dados_conversa).get("versao") == _hash(estrelas, titulo, texto):
                continue
            try:
                linha = await session.get(a, aid, populate_existing=True)
                if linha is None or linha.pendente_desde is None:
                    continue
                await garantir_conversa(session, linha, agora)
                await session.commit()
                resumo["atualizadas"] += 1
            except Exception as exc:  # noqa: BLE001 — uma não para as outras
                await session.rollback()
                resumo["erros"] += 1
                logger.warning(
                    "atendimento_avaliacao_versao_falhou",
                    avaliacao_id=str(aid),
                    erro=type(exc).__name__,
                )
    return resumo


# ── O plano (seco: só banco, nenhuma chamada) ─────────────────────────────


async def plano(*, agora: datetime | None = None) -> dict:
    """O que a leitura e a pendência fariam AGORA — só contagens, só banco.

    É o `--seco` do script da importação: quantas lojas e produtos seriam
    lidos e quantas avaliações estão perto de virar pendência. Nenhuma
    chamada à plataforma, nada gravado.
    """
    agora = agora or _agora()
    a = AtendimentoAvaliacaoLoja
    async with _db.SessionLocal() as session:
        lojas = await lojas_shopee(session)
        importadas = 0
        for integration_id in lojas:
            marca, _ = await _redis_get(CHAVE_IMPORTADA_SHOPEE.format(integration_id))
            importadas += int(marca == IMPORTADA_OK)
        linhas = await _linhas_do_espelho(session, agora - JANELA_PEDIDOS_ML)
        contas = await reclamacoes.contas_ml(session, agora)
        produtos = sum(
            len(anuncios_por_produto(linhas, c, agora - JANELA_VENDAS_ML)) for c in contas
        )

        async def _contar(*filtros) -> int:
            return int(
                await session.scalar(select(func.count()).select_from(a).where(*filtros)) or 0
            )

        janela = a.criado_em >= agora - JANELA
        return {
            "shopee": {
                "lojas": len(lojas),
                "lojas_importadas": importadas,
                "avaliacoes_30d": await _contar(a.plataforma == SHOPEE, janela),
                "sem_resposta_30d_fora_da_carencia": await _contar(
                    a.plataforma == SHOPEE,
                    janela,
                    a.resposta_loja.is_(None),
                    a.tratada_em.is_(None),
                    _passou_da_carencia(agora),
                    _nao_sumiu(),
                ),
            },
            "ml": {
                "contas": len(contas),
                "pedidos_no_espelho": len(mapa_de_pedidos(linhas)),
                "produtos_a_vigiar": produtos,
                "opinioes_30d": await _contar(a.plataforma == ML, janela),
                "nota_baixa_sem_tratar_30d": await _contar(
                    a.plataforma == ML,
                    janela,
                    a.estrelas <= NOTA_BAIXA_AVALIACAO,
                    a.tratada_em.is_(None),
                ),
            },
            "pendentes_agora": await _contar(a.pendente_desde.is_not(None)),
            "virariam_pendentes_agora": await _contar(_vira_pendente(agora)),
        }


# ── O cron ────────────────────────────────────────────────────────────────


async def _pegar_trava() -> tuple[bool, str | None]:
    token = uuid4().hex
    try:
        pegou = await redis.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        # Sem Redis, roda sem trava: a gravação é idempotente (UNIQUE + ids).
        logger.warning("atendimento_avaliacoes_trava_indisponivel", err=type(exc).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_trava(token: str | None) -> None:
    if token is None:
        return
    try:
        await redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            CHAVE_TRAVA,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("atendimento_avaliacoes_trava_soltar_falhou")


async def atendimento_avaliacoes(
    ctx: dict | None = None, *, fabrica_cliente: FabricaCliente | None = None
) -> dict | None:
    """O CRON (o integrador registra no worker: minutos {12, 42}, `timeout=1500`).

    Só roda com `ATENDIMENTO_AVALIACOES_ATIVA` E `ATENDIMENTO_LEITURA_ATIVA`
    (a leitura já está ligada em produção: o deploy sozinho não liga esta
    rodada); uma rodada por vez. Shopee em toda rodada; ML a cada
    `INTERVALO_ML`; depois a pendência (só banco). Nunca levanta: o erro
    vira log (só o tipo) e `None`.
    """
    s = get_settings()
    if not (s.atendimento_leitura_ativa and s.atendimento_avaliacoes_ativa):
        return None
    pegou, token = await _pegar_trava()
    if not pegou:
        logger.info("atendimento_avaliacoes_ocupado")
        return {"pulado": True}
    try:
        agora = _agora()
        resumo: dict[str, Any] = {
            "shopee": await sincronizar_todas_shopee(fabrica_cliente=fabrica_cliente, agora=agora)
        }
        if await _vez_do_ml(agora):
            resumo["ml"] = await sincronizar_todas_ml(fabrica_cliente=fabrica_cliente, agora=agora)
            if not resumo["ml"].get("adiados"):
                # A marca dura mais que o intervalo (6×): sumiu, o ML lê de
                # novo. Com produto adiado (a importação inicial, o teto), não
                # marca: a rodada seguinte continua.
                await _redis_set(
                    CHAVE_ULTIMA_ML, _iso(agora) or "", int(INTERVALO_ML.total_seconds()) * 6
                )
        resumo["pendencias"] = await atualizar_pendencias(agora=agora)
    except Exception as exc:  # noqa: BLE001
        logger.error("atendimento_avaliacoes_falhou", err=type(exc).__name__)
        return None
    finally:
        await _soltar_trava(token)
    logger.info("atendimento_avaliacoes_tick", **resumo)
    return resumo


# ── Para a tela (a aba ★ Avaliação) ───────────────────────────────────────

MAX_TELA = 20


async def avaliacoes_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, *, limite: int = MAX_TELA
) -> list[tuple[AtendimentoAvaliacaoLoja, bool]]:
    """As avaliações da conversa → [(avaliação, é do pedido desta conversa?)].

    Do PEDIDO: pela `conversa_id` ou por (plataforma, pedido/pack ∈ chaves) —
    a mesma ligação da etiqueta. ANTERIORES do mesmo comprador na mesma
    loja: pelo `comprador_id` (e, na Shopee, pelo apelido = `to_name`).
    Pendentes primeiro (a pior antes), depois da mais nova para a mais velha.
    """
    a = AtendimentoAvaliacaoLoja
    chaves = etiqueta_fatos.chaves_do_pedido(conversa)
    do_pedido = [a.conversa_id == conversa.id]
    if chaves:
        do_pedido.append(
            and_(
                a.plataforma == conversa.plataforma,
                etiqueta_fatos.condicao_avaliacao_do_pedido(chaves),
            )
        )
    do_comprador = []
    if conversa.integration_id is not None:
        if (conversa.comprador_id or "").strip():
            do_comprador.append(a.comprador_id == conversa.comprador_id.strip())
        if conversa.plataforma == SHOPEE and (conversa.comprador_nome or "").strip():
            do_comprador.append(a.comprador_nome_loja == conversa.comprador_nome.strip())
    conds = list(do_pedido)
    if do_comprador:
        conds.append(and_(a.integration_id == conversa.integration_id, or_(*do_comprador)))
    linhas = (
        (
            await session.execute(
                select(a)
                .where(or_(*conds))
                .order_by(
                    a.pendente_desde.is_(None).asc(),
                    a.estrelas.asc(),
                    a.criado_em.desc().nulls_last(),
                )
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )
    pendentes = [x for x in linhas if x.pendente_desde is not None]
    outras = sorted(
        (x for x in linhas if x.pendente_desde is None),
        key=lambda x: _utc(x.criado_em) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return [
        (x, etiqueta_fatos.avaliacao_e_da_conversa(x, conversa, chaves))
        for x in [*pendentes, *outras]
    ]


def url_na_plataforma(
    a: AtendimentoAvaliacaoLoja, ids_shopee: links_shopee.IdsShopee | None = None
) -> str | None:
    """O "Abrir na plataforma": a página da VENDA (os mesmos endereços da reclamação).

    Shopee: a página do pedido só com o order_id INTERNO, quando
    `links_shopee.ids_shopee` o achou com segurança; senão a lista de pedidos
    buscando o order_sn (`links_shopee`).
    """
    pedido = _id(a.pedido)
    if a.plataforma == ML:
        alvo = _id(_dict(a.dados).get("pack_id")) or pedido
        return f"https://www.mercadolivre.com.br/vendas/{alvo}/detalhe" if alvo else None
    if a.plataforma == SHOPEE and pedido:
        order_id = ids_shopee.pedido(a.integration_id, pedido) if ids_shopee else None
        return links_shopee.url_pedido_shopee(pedido, order_id)
    return None


async def ids_shopee_das(
    session: AsyncSession, avaliacoes: Iterable[AtendimentoAvaliacaoLoja]
) -> links_shopee.IdsShopee:
    """Os order_id internos das avaliações Shopee da tela — uma consulta para a lista."""
    return await links_shopee.ids_shopee(
        session,
        pedidos=[(a.integration_id, a.pedido) for a in avaliacoes if a.plataforma == SHOPEE],
    )


def motivo_sem_resposta(a: AtendimentoAvaliacaoLoja, *, envio_ativo: bool) -> str | None:
    """Por que a caixa de resposta não aparece (None = pode responder)."""
    if a.resposta_loja:
        return "Avaliação já respondida."
    if not a.pode_responder:
        return a.motivo_sem_resposta or indice.MOTIVO_SEM_RESPOSTA.get(a.plataforma)
    if not envio_ativo:
        return MOTIVO_ENVIO_DESLIGADO
    return None


def para_tela(
    a: AtendimentoAvaliacaoLoja,
    *,
    do_pedido: bool,
    envio_ativo: bool,
    nomes: dict[UUID, str | None] | None = None,
    ids_shopee: links_shopee.IdsShopee | None = None,
) -> dict[str, Any]:
    """A avaliação no formato da aba ★ (o texto do comprador vai para a TELA, nunca para o log).

    `ids_shopee` (de `ids_shopee_das`): o order_id interno para o "Abrir na
    Shopee"; sem ele, o link é a busca pelo order_sn.
    """
    motivo = motivo_sem_resposta(a, envio_ativo=envio_ativo)
    return {
        "id": a.id,
        "plataforma": a.plataforma,
        "plataforma_nome": NOME_PLATAFORMA.get(a.plataforma, a.plataforma),
        "comentario_id": a.comentario_id,
        "pedido": a.pedido,
        "item_id": a.item_id,
        "anuncio_titulo": _dict(a.dados).get("anuncio_titulo"),
        "estrelas": int(a.estrelas),
        "nota_baixa": int(a.estrelas) <= NOTA_BAIXA_AVALIACAO,
        "titulo": a.titulo,
        "texto": a.texto,
        "midia": [m for m in a.midia or [] if isinstance(m, dict) and m.get("url")],
        "criado_em": _utc(a.criado_em),
        "editada": (a.editavel or "").upper() == "HAVE_EDITED_ONCE",
        "respondida": bool(a.resposta_loja),
        "resposta_loja": a.resposta_loja,
        "resposta_em": _utc(a.resposta_em),
        "resposta_oculta": a.resposta_oculta,
        "pode_responder": bool(a.pode_responder) and motivo is None,
        "motivo_sem_resposta": motivo,
        "pendente": a.pendente_desde is not None,
        "pendente_desde": _utc(a.pendente_desde),
        "tratada_em": _utc(a.tratada_em),
        "tratada_por_nome": (nomes or {}).get(a.tratada_por) if a.tratada_por else None,
        "conversa_id": a.conversa_id,
        "do_pedido": do_pedido,
        "url_plataforma": url_na_plataforma(a, ids_shopee),
    }


async def estrelas_pendentes_em_lote(
    session: AsyncSession, conversas: Sequence[AtendimentoConversa]
) -> dict[UUID, int]:
    """{conversa: a pior nota PENDENTE ligada a ela} — o selo da lista (uma consulta)."""
    if not conversas:
        return {}
    chaves = {c.id: etiqueta_fatos.chaves_do_pedido(c) for c in conversas}
    pendentes = await etiqueta_fatos.avaliacoes_pendentes_de(
        session, [c.id for c in conversas], {k for ks in chaves.values() for k in ks}
    )
    saida: dict[UUID, int] = {}
    for c in conversas:
        da = etiqueta_fatos.pendentes_da_conversa(c, chaves[c.id], pendentes)
        if da:
            saida[c.id] = int(da[0].estrelas)
    return saida


async def resumo_para_contexto(
    session: AsyncSession, conversa: AtendimentoConversa
) -> list[dict[str, Any]]:
    """As avaliações da conversa para o contexto (painel e IA): nota e estado, SEM texto."""
    saida = []
    for a, do_pedido in await avaliacoes_da_conversa(session, conversa, limite=5):
        saida.append(
            {
                "id": str(a.id),
                "plataforma": NOME_PLATAFORMA.get(a.plataforma, a.plataforma),
                "estrelas": int(a.estrelas),
                "pedido": a.pedido,
                "do_pedido": do_pedido,
                "criado_em": _iso(_utc(a.criado_em)),
                "respondida": bool(a.resposta_loja),
                "pendente": a.pendente_desde is not None,
                "tratada": a.tratada_em is not None,
                "pode_responder": bool(a.pode_responder),
            }
        )
    return saida


# ── As ações da tela ──────────────────────────────────────────────────────


class AcaoRecusada(Exception):  # noqa: N818 — como `EnvioRecusado`: o nome é o contrato
    """A ação não aconteceu (código estável para a tela)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


async def marcar_tratada(
    session: AsyncSession,
    a: AtendimentoAvaliacaoLoja,
    *,
    user: User | None,
    motivo: str | None = None,
    agora: datetime | None = None,
) -> AtendimentoAvaliacaoLoja:
    """Marcar como tratada (o ML, que não deixa responder; vale para todas). Não commita.

    Idempotente: a já tratada fica como está. A respondida não tem o que
    tratar (`ja_respondida`). Sai da pendência, a conversa ganha a marca e a
    etiqueta volta ao status anterior. `motivo` (texto da EQUIPE, opcional)
    fica em `dados.tratada_motivo` — nada vai para a plataforma.
    """
    agora = agora or _agora()
    if a.tratada_em is not None:
        return a
    if a.resposta_loja:
        raise AcaoRecusada("ja_respondida", "Esta avaliação já foi respondida.")
    a.tratada_em = agora
    a.tratada_por = user.id if user is not None else None
    a.atualizado_em = agora
    texto = " ".join((motivo or "").split())[:300]
    if texto:
        a.dados = {**_dict(a.dados), "tratada_motivo": texto}
    await session.flush()
    if a.pendente_desde is not None or a.conversa_id is not None:
        await resolver(session, a, motivo=MOTIVO_ETIQUETA_TRATADA, agora=agora)
    logger.info(
        "atendimento_avaliacao_tratada",
        avaliacao_id=str(a.id),
        user_id=str(user.id) if user is not None else None,
    )
    return a


async def conversa_para_responder(
    session: AsyncSession, a: AtendimentoAvaliacaoLoja, *, agora: datetime | None = None
) -> AtendimentoConversa:
    """A conversa por onde a resposta sai (a `avaliacao`; cria se ainda não há). Não commita."""
    agora = agora or _agora()
    if a.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, a.conversa_id)
        if conversa is not None:
            return conversa
    return await garantir_conversa(session, a, agora)


async def avaliacao_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoAvaliacaoLoja | None:
    """A avaliação dona da conversa `avaliacao` (pelo id guardado, ou pelo `externo_id`)."""
    if conversa.canal != CANAL_AVALIACAO:
        return None
    aid = _id(_dict(conversa.dados).get("avaliacao_id"))
    if aid:
        try:
            achada = await session.get(AtendimentoAvaliacaoLoja, UUID(aid))
        except ValueError:
            achada = None
        if achada is not None:
            return achada
    return (
        await session.execute(
            select(AtendimentoAvaliacaoLoja).where(
                AtendimentoAvaliacaoLoja.integration_id == conversa.integration_id,
                AtendimentoAvaliacaoLoja.comentario_id == conversa.externo_id,
            )
        )
    ).scalar_one_or_none()


async def depois_da_resposta(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    *,
    agora: datetime | None = None,
) -> bool:
    """A resposta pública SAIU pelo DaVinci: grava na avaliação e tira da pendência. Commita.

    O envio já terminou (`enviar.enviar_resposta`); isto só adianta o que a
    próxima leitura faria (a Shopee devolve a resposta no `get_comment`).
    True = a avaliação foi atualizada.
    """
    agora = agora or _agora()
    a = await avaliacao_da_conversa(session, conversa)
    if a is None or not mensagem.texto:
        return False
    a.resposta_loja = indice.texto_curto(mensagem.texto, indice.TEXTO_AVALIACAO_MAX)
    a.resposta_em = _utc(mensagem.enviada_em) or agora
    a.dados = {
        **_dict(a.dados),
        "respondida_pelo_davinci": True,
        "respondida_por": str(mensagem.autor_user_id) if mensagem.autor_user_id else None,
    }
    a.atualizado_em = agora
    await session.flush()
    if a.pendente_desde is not None:
        await resolver(session, a, motivo=MOTIVO_ETIQUETA_DAVINCI, agora=agora)
    await session.commit()
    return True
