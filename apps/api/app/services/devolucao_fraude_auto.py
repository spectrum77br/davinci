# ruff: noqa: E501, S608
"""Fraude automática (Vinicius, 16/11/2026): o caso sem devolução se responde sozinho.

"esses fraude na teoria é toda disputa, reclamação que não vai ter devolução…
o sistema… solicitar o vídeo automático, o vídeo da embalagem, e quando chegar já
responder, seja a disputa seja a devolução, reivindicação… daí ele vai abrir um
chamado já de resposta."

Como o sistema reconhece: é a própria plataforma que diz que o caso é SÓ
REEMBOLSO (TikTok `return_type=REFUND`; Shopee `needs_logistics=false`) — o
mesmo sinal que já põe o pedido no painel Fraude (`_fila`). Não há palpite.

A cada 10 min, para cada pedido na Fraude da TikTok ou da Shopee que a
plataforma ainda espera a NOSSA resposta (prazo correndo), sem lançamento, sem
chamado e sem "Abrir chamado: Não":

1. Vídeo da embalagem: se o pedido já tem (o botão Vídeo da aba Pedidos, gravado
   na expedição), usa. Senão, lê na plataforma o que o comprador alegou e, se for
   um caso que o robô contesta, pede o vídeo à equipe — o mesmo "pedido de vídeo"
   do botão, só que sem ninguém clicar (danificado não trava a equipe à toa).
2. Quando o vídeo existe: relê o caso (ainda é só reembolso esperando a nossa
   resposta?) e lança sozinho "Não devolvido" em cada peça, com o motivo que
   contesta a alegação ("não recebi" → Não recebido, só com a entrega da ida
   confirmada; "veio vazio/errado" → Golpe; "faltou item" → Item faltando) e o
   vídeo no Link envio. Lançamento e chamado gravam juntos. Daí pra frente é o
   caminho de sempre: a IA confere o motivo (lançamento do robô SEM a IA conferir
   não sai — segura pra alguém) e a resposta sai pela API — TikTok recusa o
   reembolso, Shopee abre a disputa com o texto do só reembolso.

Fica pra pessoa (cartão "Fraude: responder à mão"): danificado/defeito e alegação
que o sistema não reconhece, ida não entregue (extravio: é compensação, não
contestação), "não recebi" sem a entrega confirmada, "não tenho o vídeo", pedido
de vídeo que alguém cancelou. O robô não mexe em quem uma pessoa já mexeu
(lançamento, chamado, "Abrir chamado: Não", tirar da Fraude). ML e Amazon ficam de
fora nesta etapa (não há resposta pela API pra mediação sem devolução).

Interruptor: `DEVOLUCAO_FRAUDE_AUTO` = ligado | so_video | desligado.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import bindparam, text

from app.config import get_settings
from app.models import DevolucaoRastreio, Devolution, EstoquePedidoVideo
from app.services import devolucao_conferencia as conferencia

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()
SCHEMA = get_settings().database_schema

LIGADO = "ligado"
SO_VIDEO = "so_video"
DESLIGADO = "desligado"

CONDICAO = "Não devolvido"
# Ações em que a plataforma espera a nossa resposta ao só reembolso. A proposta
# do comprador na Shopee (SHOPEE_RESPONDER_PROPOSTA) é negociação: fica pra pessoa.
ACOES_RESPONDER = ("SELLER_RESPOND_REFUND", "SHOPEE_RESPONDER_SOLICITACAO")
# Lançar no máximo isso por rodada (a 1ª rodada não sai em massa).
TETO_LANCAMENTOS = 5
# Recados na Observação do pedido (interna: a tela mostra, a IA lê, a plataforma não).
MARCA = "Fraude automática"
NAO_RESPONDE = "o sistema não responde esse caso sozinho (responder à mão)"
VIDEO_PEDIDO = "vídeo da embalagem pedido à equipe"
ACHADO_EXPEDICAO = "vídeo da embalagem achado na expedição"
# A tela (cartão "Fraude: responder à mão", pages/devolucoes.vue) procura este pedaço.
CARTAO_A_MAO = "responder à mão"


def modo() -> str:
    m = (get_settings().devolucao_fraude_auto or "").strip().lower()
    return m if m in (LIGADO, SO_VIDEO) else DESLIGADO


# ── O que o comprador alegou → o motivo que contesta ─────────────────────

# Shopee: `reason` do detalhe do caso.
_SHOPEE: dict[str, str | None] = {
    "NOT_RECEIPT": "Não recebido",
    "SUSPICIOUS_PARCEL": "Golpe",  # pacote vazio / violado
    "WRONG_ITEM": "Golpe",
    "MISSING_ITEM": "Item faltando",
    # O vídeo da embalagem não rebate "diferente do anúncio", nem dano/defeito
    # (a plataforma pede foto do produto): pessoa.
    "NOT_AS_DESCRIBED": None,
    "PHYSICAL_DMG": None,
    "FUNCTIONAL_DMG": None,
}
# Texto do motivo (português ou inglês). Ordem importa: o primeiro que casar vale.
_PADROES: tuple[tuple[str, str | None], ...] = (
    (r"danific|quebrad|defeit|nao funciona|damag|broken|defect|not work|anuncio|descri", None),
    (
        r"nao receb|nao cheg|not receiv|didn.?t receiv|never arriv|not arriv|nao foi entreg",
        "Não recebido",
    ),
    (r"vazi|empty", "Golpe"),
    (r"falt|missing|incomplet", "Item faltando"),
    (r"errad|diferent|wrong|different|outro produto", "Golpe"),
)
NAO_RECEBIDO = "Não recebido"


def _sem_acento(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    )


def motivo_da_alegacao(plataforma: str, codigo: str | None, texto: str | None) -> str | None:
    """O motivo do lançamento a partir do que o comprador alegou. None = fica pra
    pessoa (danificado, ou alegação que o sistema não reconhece). Na TikTok o
    motivo padronizado (`codigo`) vale antes da nota livre do comprador. PURA."""
    if plataforma == "shopee":
        cod = (codigo or "").strip().upper()
        if cod:
            # Código que o sistema não conhece: pessoa (não adivinha pelo texto).
            return _SHOPEE.get(cod)
        textos: tuple[str | None, ...] = (texto,)
    else:
        textos = (codigo, texto)
    for t in textos:
        alvo = _sem_acento(t or "")
        if not alvo.strip():
            continue
        for padrao, motivo in _PADROES:
            if re.search(padrao, alvo):
                return motivo
    return None


@dataclass(frozen=True)
class Caso:
    """O caso lido AGORA na plataforma."""

    pendente: bool  # ainda é só reembolso esperando a NOSSA resposta
    codigo: str = ""
    texto: str = ""
    # A ida chegou ao comprador? True / False (não entregue) / None (não se sabe).
    ida_entregue: bool | None = None


def decidir(plataforma: str, caso: Caso) -> tuple[str | None, str | None]:
    """(motivo do lançamento, por que fica pra pessoa). (None, None) = a alegação
    não foi lida — tenta de novo na próxima rodada. PURA."""
    if not (caso.codigo or caso.texto):
        return None, None
    motivo = motivo_da_alegacao(plataforma, caso.codigo, caso.texto)
    if motivo is None:
        return None, "é um tipo de alegação que o sistema não contesta sozinho"
    if caso.ida_entregue is False:
        return None, "a entrega da ida NÃO aconteceu (extravio: pedir compensação, não contestar)"
    if motivo == NAO_RECEBIDO and caso.ida_entregue is not True:
        return None, "o comprador diz que não recebeu e a entrega da ida não está confirmada"
    return motivo, None


# ── Candidatos ───────────────────────────────────────────────────────────

_CANDIDATOS_SQL = f"""
SELECT r.pedido_bling, r.fonte_auto, r.devolucao_id_auto
FROM "{SCHEMA}".devolucao_rastreio r
WHERE lower(COALESCE(r.fonte_auto, '')) IN ('tiktok', 'shopee')
  AND COALESCE(btrim(r.devolucao_id_auto), '') <> ''
  -- Só reembolso pela própria plataforma (o mesmo sinal do painel Fraude) e
  -- ninguém tirou da Fraude. Caso COM pacote que alguém pôs na Fraude na mão
  -- ("chegou vazio") é outro fluxo: fica com a pessoa.
  AND upper(COALESCE(r.devolucao_tipo_auto, '')) = 'REFUND'
  AND lower(COALESCE(r.fila_manual, '')) <> 'acompanhamento'
  AND upper(COALESCE(r.acao_auto, '')) IN :acoes
  AND r.prazo_acao_auto > :agora
  AND r.abrir_chamado IS DISTINCT FROM false
  AND EXISTS (SELECT 1 FROM "{SCHEMA}".bling_orders bo
              WHERE bo.numero = r.pedido_bling AND bo.situacao = '83957')
  -- Aberto no painel (a regra do Resolver: resolvido antes da entrada atual não vale).
  AND (r.resolvido_at IS NULL OR (r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date < (
          SELECT max(bo.aguardando_devolucao_data) FROM "{SCHEMA}".bling_orders bo
          WHERE bo.numero = r.pedido_bling))
  AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d WHERE btrim(d.pedido_bling) = r.pedido_bling)
  AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".chamados c
                  WHERE c.pedido_bling = r.pedido_bling AND c.origem = 'devolucao')
ORDER BY r.prazo_acao_auto
"""
_SQL = text(_CANDIDATOS_SQL).bindparams(bindparam("acoes", expanding=True))
_AINDA_LIVRE = text(
    f"""SELECT NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d WHERE btrim(d.pedido_bling) = :p)
       AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".chamados c
                       WHERE c.pedido_bling = :p AND c.origem = 'devolucao')"""
)


@dataclass
class Resumo:
    candidatos: int = 0
    video_da_expedicao: int = 0
    video_pedido: int = 0
    esperando_video: int = 0
    lancados: int = 0
    pra_pessoa: int = 0
    sem_resposta_pendente: int = 0
    falhas: int = 0
    pedidos: dict[str, str] = field(default_factory=dict)

    def como_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "pedidos"} | {
            "pedidos": dict(self.pedidos)
        }


def _hoje_txt(agora: datetime) -> str:
    from zoneinfo import ZoneInfo

    return agora.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")


def _anotar(rast: DevolucaoRastreio, texto: str) -> None:
    """Recado interno na Observação do pedido. Não repete o mesmo recado."""
    atual = (rast.observacao or "").strip()
    if texto in atual:
        return
    rast.observacao = f"{atual}\n{texto}".strip() if atual else texto


async def _video_da_expedicao(session: AsyncSession, pedido: str) -> str | None:
    """O vídeo da embalagem gravado na expedição (botão Vídeo da aba Pedidos), só se
    foi salvo depois da venda ATUAL — o nº do Bling se repete entre anos."""
    row = await session.get(EstoquePedidoVideo, pedido)
    if row is None or not (row.link or "").strip():
        return None
    venda = (
        await session.execute(
            text(f'SELECT max(bo.data) FROM "{SCHEMA}".bling_orders bo WHERE bo.numero = :p'),
            {"p": pedido},
        )
    ).scalar()
    salvo = row.updated_at or row.created_at
    if venda is not None and salvo is not None and salvo < venda - timedelta(days=1):
        return None
    return row.link.strip()


async def _ler_caso(session: AsyncSession, plataforma: str, rid: str, dev: Devolution) -> Caso:
    """O caso lido agora na plataforma: ainda espera a nossa resposta? O que o
    comprador alegou? A ida chegou?"""
    from app.services import chamados_devolucao as cd
    from app.services import chamados_tiktok_reembolso as vig

    if plataforma == "tiktok":
        client = await cd._tiktok_client_para(session, None, dev)
        caso = cd._caso_so_reembolso(await cd._casos_tiktok(client, dev), rid)
        if caso is None or not vig.e_pendente_de_resposta(caso):
            return Caso(pendente=False)
        rid_atual = str(caso.get("return_id") or rid)
        nota, motivo_txt, _eventos = await vig._nota_e_motivo(client, rid_atual)
        codigo = str(caso.get("return_reason_text") or "").strip() or motivo_txt
        entrega = await vig._entrega_dados(client, (dev.pedido_marketplace or "").strip())
        return Caso(
            pendente=True, codigo=codigo, texto=nota, ida_entregue=True if entrega else None
        )
    client = await cd._shopee_client_para(session, None, dev)
    det = await client.get_return_detail(rid)
    status = str(det.get("status") or "").strip().upper()
    so = str(det.get("return_solution")) == "1" or det.get("needs_logistics") is False
    contestacao = det.get("dispute_reason") or []
    if isinstance(contestacao, str):
        contestacao = [contestacao]
    contestada = any(str(x or "").strip() for x in contestacao)
    comp = str((det.get("seller_compensation") or {}).get("seller_compensation_status") or "")
    comp = comp.upper().replace("COMPENSATION_", "")
    # Só "solicitado" espera a nossa resposta (é onde nasce SHOPEE_RESPONDER_SOLICITACAO):
    # aceito/em andamento = alguém já decidiu no Seller Center.
    if not (
        so
        and status == "REQUESTED"
        and not contestada
        and comp not in ("REQUESTED", "APPROVED", "REJECTED")
    ):
        return Caso(pendente=False)
    entrega = await cd._entrega_shopee(session, dev)
    situacao = entrega.get("situacao") or ""
    ida: bool | None = None
    if situacao in cd._SHOPEE_IDA_NAO_ENTREGUE:
        ida = False
    elif situacao in cd._SHOPEE_IDA_ENTREGUE or entrega.get("entregue_em") is not None:
        ida = True
    return Caso(
        pendente=True,
        codigo=str(det.get("reason") or "").strip(),
        texto=" ".join(str(det.get("text_reason") or "").split()),
        ida_entregue=ida,
    )


async def _pecas(session: AsyncSession, pedido: str) -> list[dict]:
    from app.routers.devolutions import pecas_do_pedido  # tardio: roteador pesado

    return [
        p
        for p in await pecas_do_pedido(session, pedido, exato=True)
        if (p.get("pedido_bling") or "").strip() == pedido and not p.get("ja_devolvido")
    ]


def _linha(peca: dict, *, motivo: str, video: str) -> Devolution:
    return Devolution(
        data=peca.get("data"),
        pedido_bling=(peca.get("pedido_bling") or "").strip(),
        pedido_marketplace=peca.get("pedido_marketplace"),
        conta=peca.get("conta") or "Sem loja",
        sku=peca.get("sku"),
        produtos=peca.get("produtos"),
        custo_produto=peca.get("custo_produto"),
        quantidade=1,
        condicao_produto=CONDICAO,
        motivo_devolucao=motivo,
        link_envio=video,
        # Na Fraude o cliente já foi (ou vai ser) reembolsado pela plataforma.
        reembolso=True,
    )


def _o_que(caso: Caso) -> str:
    return " — ".join(x for x in (caso.codigo, caso.texto) if x)[:200]


async def _avaliar(
    session: AsyncSession,
    linha: Any,
    rast: DevolucaoRastreio,
    pecas: list[dict],
    agora: datetime,
    resumo: Resumo,
) -> tuple[str, Caso] | None:
    """Lê o caso e decide. Devolve (motivo do lançamento, o caso lido), ou None (e
    já conta e anota o porquê)."""
    pedido = linha.pedido_bling
    plataforma = (linha.fonte_auto or "").strip().lower()
    rascunho = _linha(pecas[0], motivo="", video="")
    caso = await _ler_caso(session, plataforma, linha.devolucao_id_auto.strip(), rascunho)
    if not caso.pendente:
        # Já respondido, cancelado, aprovado… o sync de 30 min tira da lista.
        resumo.sem_resposta_pendente += 1
        resumo.pedidos[pedido] = "a plataforma não espera mais resposta"
        return None
    motivo, pessoa = decidir(plataforma, caso)
    if pessoa:
        _anotar(
            rast,
            f'{MARCA} ({_hoje_txt(agora)}): o comprador alega "{_o_que(caso)}" — {pessoa}; '
            f"{NAO_RESPONDE}.",
        )
        await session.commit()
        resumo.pra_pessoa += 1
        resumo.pedidos[pedido] = f"pessoa: {pessoa}"
        return None
    if motivo is None:
        # A leitura voltou vazia (a TikTok devolve vazio quando a API falha).
        resumo.falhas += 1
        resumo.pedidos[pedido] = "alegação não lida"
        return None
    return motivo, caso


async def _pedir_video(
    session: AsyncSession, linha: Any, rast: DevolucaoRastreio, agora: datetime, resumo: Resumo
) -> None:
    pedido = linha.pedido_bling
    pecas = await _pecas(session, pedido)
    if not pecas:
        resumo.pra_pessoa += 1
        resumo.pedidos[pedido] = "sem peças na busca"
        return
    # Antes de pedir: o caso é dos que o robô contesta? (danificado não trava a equipe)
    if await _avaliar(session, linha, rast, pecas, agora, resumo) is None:
        return
    # O mesmo pedido de vídeo do botão (aparece pra equipe do estoque).
    rast.video_solicitado_em = agora
    rast.video_solicitado_por = None
    rast.video_refazer_motivo = None
    _anotar(rast, f"{MARCA} ({_hoje_txt(agora)}): {VIDEO_PEDIDO}.")
    await session.commit()
    resumo.video_pedido += 1
    resumo.pedidos[pedido] = "vídeo pedido"


async def _lancar(
    session: AsyncSession, linha: Any, rast: DevolucaoRastreio, agora: datetime, resumo: Resumo
) -> None:
    from app.routers import devolutions as antigo
    from app.services import chamados_devolucao as cd

    pedido = linha.pedido_bling
    if not conferencia.ligada():
        # Sem a IA não há conferência nenhuma antes do tiro único: não lança.
        resumo.pedidos[pedido] = "IA desligada: não lança sozinho"
        return
    pecas = await _pecas(session, pedido)
    if not pecas:
        resumo.pra_pessoa += 1
        resumo.pedidos[pedido] = "sem peças na busca"
        return
    avaliado = await _avaliar(session, linha, rast, pecas, agora, resumo)
    if avaliado is None:
        return
    motivo, caso = avaliado
    # A leitura da plataforma levou um tempo: alguém lançou ou abriu chamado no meio?
    if not (await session.execute(_AINDA_LIVRE, {"p": pedido})).scalar():
        resumo.pedidos[pedido] = "uma pessoa lançou no meio"
        return
    video = (rast.video_link or "").strip()
    linhas = [_linha(p, motivo=motivo, video=video) for p in pecas]
    for d in linhas:
        d.tag = antigo._sku_tag(d.sku)
        session.add(d)
    _anotar(
        rast,
        f'{MARCA} ({_hoje_txt(agora)}): o comprador alega "{_o_que(caso)}" — '
        f"{conferencia.MARCA_ROBO}: {CONDICAO} / {motivo}, com o vídeo da embalagem; "
        "a IA confere antes de a resposta sair.",
    )
    await session.flush()
    # Lançamento e chamado gravam JUNTOS: sem chamado, não fica lançamento órfão.
    ch = await cd.garantir_chamado(session, linhas[0])
    if ch is None:
        await session.rollback()
        resumo.falhas += 1
        resumo.pedidos[pedido] = "o chamado não abriu"
        return
    await session.commit()
    await cd.agendar_disparo(session, ch, linhas[0])
    resumo.lancados += 1
    resumo.pedidos[pedido] = f"lançado: {motivo}"
    logger.info("devolucao_fraude_auto_lancado", pedido_bling=pedido, motivo=motivo)


async def rodada(session: AsyncSession, *, agora: datetime | None = None) -> dict[str, Any]:
    m = modo()
    resumo = Resumo()
    if m == DESLIGADO:
        return resumo.como_dict() | {"modo": m}
    agora = agora or datetime.now(UTC)
    linhas = (await session.execute(_SQL, {"acoes": list(ACOES_RESPONDER), "agora": agora})).all()
    resumo.candidatos = len(linhas)
    for linha in linhas:
        pedido = linha.pedido_bling
        try:
            rast = await session.get(DevolucaoRastreio, pedido)
            if rast is None:
                continue
            obs = rast.observacao or ""
            if NAO_RESPONDE in obs:
                # Já lido e deixado pra pessoa: não relê a plataforma a cada rodada.
                resumo.pra_pessoa += 1
                resumo.pedidos[pedido] = "pessoa (já avisado)"
                continue
            video = (rast.video_link or "").strip()
            # O da expedição entra uma vez só: depois de "apagar e pedir de novo" (alguém
            # recusou o vídeo) ou de um uso anterior, não volta o mesmo.
            if (
                not video
                and not (rast.video_refazer_motivo or "").strip()
                and ACHADO_EXPEDICAO not in obs
            ):
                achado = await _video_da_expedicao(session, pedido)
                if achado:
                    # Também responde o pedido de vídeo que estiver aberto pra equipe.
                    rast.video_link = achado
                    rast.video_enviado_em = agora
                    rast.video_solicitado_em = rast.video_solicitado_em or agora
                    rast.video_sem_motivo = None
                    _anotar(
                        rast,
                        f"{MARCA} ({_hoje_txt(agora)}): {ACHADO_EXPEDICAO}.",
                    )
                    await session.commit()
                    resumo.video_da_expedicao += 1
                    video = achado
            if not video:
                if (rast.video_sem_motivo or "").strip():
                    resumo.pra_pessoa += 1
                    resumo.pedidos[pedido] = "sem vídeo (equipe)"
                elif rast.video_solicitado_em is not None:
                    resumo.esperando_video += 1
                elif VIDEO_PEDIDO in obs:
                    # O robô pediu e alguém cancelou o pedido: não pede de novo; fica
                    # pra pessoa (aparece no cartão "Fraude: responder à mão").
                    _anotar(
                        rast,
                        f"{MARCA} ({_hoje_txt(agora)}): o pedido de vídeo foi cancelado — "
                        f"{NAO_RESPONDE}.",
                    )
                    await session.commit()
                    resumo.pra_pessoa += 1
                    resumo.pedidos[pedido] = "pedido de vídeo cancelado"
                else:
                    await _pedir_video(session, linha, rast, agora, resumo)
                continue
            if m != LIGADO or resumo.lancados >= TETO_LANCAMENTOS:
                continue
            await _lancar(session, linha, rast, agora, resumo)
        except Exception as e:  # noqa: BLE001 — um pedido ruim não para a rodada
            await session.rollback()
            resumo.falhas += 1
            resumo.pedidos[pedido] = f"falhou: {type(e).__name__}"
            logger.error("devolucao_fraude_auto_falhou", pedido_bling=pedido, err=str(e)[:200])
    return resumo.como_dict() | {"modo": m}


async def candidatos(session: AsyncSession, *, agora: datetime | None = None) -> list[str]:
    """Só a lista (testes e conferência em produção)."""
    agora = agora or datetime.now(UTC)
    rows = (await session.execute(_SQL, {"acoes": list(ACOES_RESPONDER), "agora": agora})).all()
    return [r.pedido_bling for r in rows]
