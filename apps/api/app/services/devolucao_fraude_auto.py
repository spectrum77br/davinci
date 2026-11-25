# ruff: noqa: E501, S608
"""Fraude automática (Vinicius, 16/11/2026): todo pedido da Fraude vira chamado.

"esses fraude… toda disputa, reclamação que não vai ter devolução… solicitar o
vídeo automático… e quando chegar já responder… abrir um chamado já de resposta."
E, no mesmo dia, depois de ver que só os casos ainda no prazo entravam: "é simples:
painel fraude tem que abrir chamado… [a IA] solicita os vídeos e, chegando vídeo,
analisa, faz o lançamento e abre chamado". O chamado é sempre uma DEFESA contra o
que o comprador alegou ("chegou um sabonete"), nunca "não recebemos a devolução".

Quem entra: o pedido do painel Fraude da TikTok ou da Shopee — a própria plataforma
diz que é SÓ REEMBOLSO (TikTok `return_type=REFUND`; Shopee `needs_logistics=false`),
o mesmo sinal do painel — aberto, em Aguardando Devolução, sem lançamento, sem
chamado e sem "Abrir chamado: Não". Com prazo de resposta correndo ou não: o caso
que a plataforma já reembolsou ou está analisando também ganha a defesa.

A cada 10 min, por pedido:

1. Vídeo da embalagem: o gravado na expedição (botão Vídeo da aba Pedidos), se há;
   senão pede à equipe — o mesmo "pedido de vídeo" do botão, sem ninguém clicar.
2. Com o vídeo: relê o caso na plataforma (em que pé está, o que o comprador
   alegou, a entrega do pedido) e a IA (`devolucao_fraude_defesa`) diz o que ele
   alega e escreve a defesa. O robô lança "Não devolvido" em cada peça com o motivo
   da alegação (vazio/outro produto → Golpe; faltou → Item faltando; não chegou →
   Não recebido, só com a entrega confirmada), o vídeo no Link envio e a defesa na
   "Observação pro chamado" — lançamento e chamado gravam juntos. Daí é o caminho de
   sempre: a IA confere o motivo antes de sair (lançamento do robô sem a conferência
   não sai) e a resposta vai pela API quando a plataforma ainda espera a nossa
   resposta (TikTok recusa o reembolso, Shopee abre a disputa); já reembolsado ou em
   análise, vira tarefa da tela (Seller Center) com o mesmo texto.

Dano/defeito e "diferente do anúncio" também viram defesa com o vídeo (Vinicius,
16/11, 307238: "teria que responder já, mostrando vídeo e falando que enviamos
certinho"). Fica pra pessoa (cartão "Fraude: responder à mão"): alegação que não se
entende, entrega do pedido que não aconteceu (extravio: é compensação),
"não recebi" sem a entrega confirmada, proposta de reembolso parcial na Shopee
(negociação com valor menor que o pedido), caso já contestado fora do DaVinci,
reembolso que a própria loja aceitou (TikTok), "não tenho o vídeo" pela 2ª vez (na
1ª o robô pede de novo 12 h depois) e pedido de vídeo cancelado. A defesa aprovada
no "só propor" é a que sai quando ele desliga (a IA não é perguntada de novo). O robô
não mexe em quem uma pessoa já mexeu (lançamento, chamado, "Abrir chamado: Não",
tirar da Fraude). ML e Amazon ficam de fora nesta etapa.

Saiu da Fraude com o vídeo ainda pedido (Vinicius, 25/11/2026 — 308495: a entrega
falhou, o pacote voltou pela ida e o pedido foi pro Acompanhamento, mas o pedido de
vídeo do robô ficou travando a aba Pedidos da equipe por dias): o robô cancela o
pedido de vídeo que ELE fez e anota na Observação. Só quando ninguém está mexendo —
com lançamento ou chamado de devolução o vídeo pode ainda servir, e o pedido fixado na
Fraude à mão é da pessoa: esses ficam. Se o pedido voltar pra Fraude, o robô pede o
vídeo de novo (não trata como "alguém cancelou"), mas só 6 h depois do cancelamento.

Interruptor: `DEVOLUCAO_FRAUDE_AUTO` = ligado | so_video | desligado. Com `SO_PROPOR`
ligado, "ligado" só ESCREVE a defesa proposta na Observação do pedido (nada é lançado
nem vai pra plataforma) — fica desligado: o Vinicius preferiu rodar direto (16/11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import bindparam, text

from app.config import get_settings
from app.models import DevolucaoRastreio, Devolution, EstoquePedidoVideo
from app.services import devolucao_chamado_instrucao as pela_instrucao
from app.services import devolucao_conferencia as conferencia
from app.services import devolucao_fraude_defesa as defesa_ia

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()
SCHEMA = get_settings().database_schema

LIGADO = "ligado"
SO_VIDEO = "so_video"
DESLIGADO = "desligado"
PROPOR = "propor"
# True = só PROPÕE a defesa (Observação do pedido) em vez de lançar. Vinicius,
# 16/11/2026: "não precisa por trava, pode colocar para rodar direto".
SO_PROPOR = False

CONDICAO = "Não devolvido"
# Ações em que a plataforma espera a nossa resposta (vão primeiro na fila da rodada).
ACOES_RESPONDER = ("SELLER_RESPOND_REFUND", "SHOPEE_RESPONDER_SOLICITACAO")
# Por rodada (cron de 10 min, teto de 10 min do worker): lançar no máximo isso (a 1ª
# rodada não sai em massa), perguntar à IA no máximo isso e ler a plataforma no
# máximo isso (a TikTok devolve 429 em rajada). IA fora do ar = para de perguntar.
TETO_LANCAMENTOS = 5
TETO_ANALISES = 5
TETO_LEITURAS = 10
# Recados na Observação do pedido (interna: a tela mostra, a IA lê, a plataforma não).
MARCA = "Fraude automática"
NAO_RESPONDE = "o sistema não responde esse caso sozinho (responder à mão)"
VIDEO_PEDIDO = "vídeo da embalagem pedido à equipe"
VIDEO_PEDIDO_DE_NOVO = "vídeo da embalagem pedido de novo à equipe"
# "Não tenho o vídeo" da equipe (Vinicius, 24/11/2026 — 309043 parou de vez com "Não
# tô em casa"): passadas estas horas da resposta, o robô pede o vídeo de novo, UMA
# vez. Se a resposta vier sem vídeo outra vez, fica pra pessoa como antes.
PEDIR_DE_NOVO_DEPOIS = timedelta(hours=12)
# O pedido saiu da Fraude com o vídeo do robô ainda pendente: o robô cancela (25/11).
VIDEO_CANCELADO_SAIU = "pedido de vídeo cancelado pelo robô"
# Voltou pra Fraude depois desse cancelamento: só pede de novo passado este tempo. A
# leitura do rastreio da ida da Shopee às vezes falha e o tipo volta a "só reembolso"
# até o próximo sync (meia hora) — sem a espera, a equipe travava e destravava a cada
# falha enquanto o pacote volta.
VOLTOU_ESPERA = timedelta(hours=6)
ACHADO_EXPEDICAO = "vídeo da embalagem achado na expedição"
PROPOSTA = "defesa proposta pela IA"
# A tela (cartão "Fraude: responder à mão", pages/devolucoes.vue) procura este pedaço.
CARTAO_A_MAO = "responder à mão"

# Em que pé o caso está na plataforma.
RESPONDER = defesa_ia.RESPONDER
EM_ANALISE = defesa_ia.EM_ANALISE
PAGO = defesa_ia.PAGO
CONTESTADO = "contestado"
CANCELADO = "cancelado"
NEGOCIACAO = "negociacao"
ACEITO_PELA_LOJA = "aceito_pela_loja"
SEM_CASO = ""  # não deu pra ler / não é mais só reembolso: tenta na próxima rodada


def modo() -> str:
    m = (get_settings().devolucao_fraude_auto or "").strip().lower()
    if m == LIGADO and SO_PROPOR:
        return PROPOR
    return m if m in (LIGADO, SO_VIDEO) else DESLIGADO


@dataclass(frozen=True)
class Caso:
    """O caso lido AGORA na plataforma."""

    situacao: str
    codigo: str = ""
    texto: str = ""
    # A ida chegou ao comprador? True / False (não entregue) / None (não se sabe).
    ida_entregue: bool | None = None
    entregue_em: str = ""
    transportadora: str = ""
    comprador_mandou_prova: bool | None = None
    prazo: str = ""
    # Negociação da Shopee: "R$ 30,00 (o pedido era R$ 600,00)" — vai no recado.
    proposta: str = ""


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
-- Primeiro quem a plataforma ainda espera responder (prazo correndo).
ORDER BY COALESCE(upper(COALESCE(r.acao_auto, '')) IN :acoes
                  AND r.prazo_acao_auto > :agora, false) DESC,
         r.prazo_acao_auto NULLS LAST, r.pedido_bling
"""
_SQL = text(_CANDIDATOS_SQL).bindparams(bindparam("acoes", expanding=True))
_AINDA_LIVRE = text(
    f"""SELECT NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d WHERE btrim(d.pedido_bling) = :p)
       AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".chamados c
                       WHERE c.pedido_bling = :p AND c.origem = 'devolucao')
       -- Alguém marcou "Abrir chamado: Não" ou tirou da Fraude durante a leitura.
       AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolucao_rastreio r
                       WHERE r.pedido_bling = :p
                         AND (r.abrir_chamado = false
                              OR lower(COALESCE(r.fila_manual, '')) = 'acompanhamento'))"""
)
# Pedido de vídeo do ROBÔ ainda sem resposta (o botão grava quem pediu; o robô não),
# em pedido sem lançamento e sem chamado de devolução. Quem destes não é candidato na
# rodada saiu da Fraude e tem o pedido de vídeo cancelado (25/11/2026, 308495).
_VIDEO_DO_ROBO = text(
    f"""
SELECT r.pedido_bling,
       upper(COALESCE(r.devolucao_tipo_auto, '')) AS tipo,
       lower(COALESCE(r.fila_manual, '')) AS fila_manual,
       r.abrir_chamado,
       r.resolvido_at,
       EXISTS (SELECT 1 FROM "{SCHEMA}".bling_orders bo
               WHERE bo.numero = r.pedido_bling AND bo.situacao = '83957') AS aguardando
FROM "{SCHEMA}".devolucao_rastreio r
WHERE r.video_solicitado_em IS NOT NULL
  AND r.video_enviado_em IS NULL
  AND r.video_solicitado_por IS NULL
  AND strpos(COALESCE(r.observacao, ''), :marca) > 0
  AND strpos(COALESCE(r.observacao, ''), :pedido_video) > 0
  -- Fixado na Fraude à mão: continua no painel e é da pessoa.
  AND lower(COALESCE(r.fila_manual, '')) <> 'fraude'
  AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d WHERE btrim(d.pedido_bling) = r.pedido_bling)
  AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".chamados c
                  WHERE c.pedido_bling = r.pedido_bling AND c.origem = 'devolucao')
ORDER BY r.pedido_bling
"""
)
# O pedaço comum de VIDEO_PEDIDO e VIDEO_PEDIDO_DE_NOVO (o achado na expedição não
# conta: ele já responde o pedido).
_PEDIDO_VIDEO = "vídeo da embalagem pedido"


@dataclass
class Resumo:
    candidatos: int = 0
    video_da_expedicao: int = 0
    video_pedido: int = 0
    esperando_video: int = 0
    video_cancelado: int = 0
    lancados: int = 0
    propostos: int = 0
    pra_pessoa: int = 0
    sem_resposta_pendente: int = 0
    falhas: int = 0
    analises: int = 0
    leituras: int = 0
    ia_fora: bool = False
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


async def _anotar_e_gravar(session: AsyncSession, rast: DevolucaoRastreio, texto: str) -> None:
    """Relê a Observação antes de escrever (a leitura da plataforma e a IA levam
    segundos: uma pessoa pode ter digitado nela no meio) e grava."""
    await session.refresh(rast, ["observacao"])
    _anotar(rast, texto)
    await session.commit()


# "… defesa proposta pela IA (motivo "Golpe") — o comprador alega "…"; defesa: "…".
# Esperando o OK …" — o que o Vinicius aprovou é o que sai quando o "só propor" desliga.
_RE_PROPOSTA = re.compile(
    r'defesa proposta pela IA \(motivo "(?P<motivo>[^"]+)"\).*?; defesa: "(?P<defesa>.+?)"\. '
    r"Esperando o OK"
)


def proposta_anterior(observacao: str | None) -> tuple[str, str] | None:
    """(motivo, defesa) da última defesa proposta na Observação do pedido. PURA."""
    achados = list(_RE_PROPOSTA.finditer(observacao or ""))
    if not achados:
        return None
    m = achados[-1]
    motivo, defesa = m.group("motivo").strip(), m.group("defesa").strip()
    if motivo not in defesa_ia.ALEGACOES.values() or len(defesa) < 20:
        return None
    return motivo, defesa


def _loja_aceitou(eventos: list[dict]) -> bool:
    """A própria loja aceitou o reembolso na TikTok (não foi a plataforma por prazo).
    PURA."""
    for ev in eventos or []:
        nome = str(ev.get("event") or "").upper()
        if nome.startswith("SELLER_") and "REJECT" not in nome and "TIMEOUT" not in nome:
            if any(p in nome for p in ("AGREE", "AGGREE", "APPROVE", "ACCEPT")):
                return True
    return False


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


# ── O caso na plataforma ─────────────────────────────────────────────────

_TIKTOK_PAGO = ("RETURN_OR_REFUND_REQUEST_COMPLETE", "RETURN_OR_REFUND_REQUEST_SUCCESS")


def _ddmm(fmt: str) -> str:
    """ "10/11 14:30" → "10/11". PURA."""
    return (fmt or "").split(" ")[0]


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _proposta_de_verdade(det: dict, neg: dict) -> str:
    """A negociação da Shopee tem uma proposta de VALOR esperando a loja? Devolve o
    texto ("R$ 30,00 (o pedido era R$ 600,00)") ou "".

    24/11/2026: `negotiation_status == PENDING_RESPOND` sozinho NÃO é proposta — no só
    reembolso da Shopee o próprio pedido do comprador fica como a "oferta" esperando a
    loja. Conferido na tela em 17/11 (308634: "Em análise pela Shopee", nenhuma
    proposta de valor) e foi o que parou o 307496 e o 308570 com vídeo na mão.
    Proposta de verdade = a última oferta (`latest_offer_amount`) MENOR que o
    reembolso pedido (`refund_amount`, ou a soma do `refund_amount` dos itens):
    reembolso parcial, que pode valer mais aceitar do que contestar — fica pra pessoa."""
    if str(neg.get("negotiation_status") or "").strip().upper() != "PENDING_RESPOND":
        return ""
    oferta = _num(neg.get("latest_offer_amount"))
    pedido = _num(det.get("refund_amount"))
    if pedido is None:
        itens = det.get("item") if isinstance(det.get("item"), list) else []
        pedido = _num(sum(_num(i.get("refund_amount")) or 0 for i in itens if isinstance(i, dict)))
    if oferta is None or pedido is None or oferta >= pedido - 0.01:
        return ""
    return f"{_brl(oferta)} (o pedido era {_brl(pedido)})"


async def _ler_caso(session: AsyncSession, plataforma: str, rid: str, dev: Devolution) -> Caso:
    """O caso lido agora na plataforma: em que pé está, o que o comprador alegou,
    a entrega do pedido."""
    from app.services import chamados_devolucao as cd
    from app.services import chamados_tiktok_reembolso as vig

    if plataforma == "tiktok":
        client = await cd._tiktok_client_para(session, None, dev)
        caso = cd._caso_so_reembolso(await cd._casos_tiktok(client, dev), rid)
        if caso is None:
            return Caso(situacao=SEM_CASO)
        status = str(caso.get("return_status") or "").strip().upper()
        if vig.e_pendente_de_resposta(caso):
            situacao = RESPONDER
        elif status == vig.STATUS_RECUSADO or caso.get("arbitration_status"):
            situacao = CONTESTADO
        elif status in _TIKTOK_PAGO:
            situacao = PAGO
        elif status == "RETURN_OR_REFUND_REQUEST_CANCEL":
            situacao = CANCELADO
        else:
            return Caso(situacao=SEM_CASO)
        if situacao in (CONTESTADO, CANCELADO):
            return Caso(situacao=situacao)
        rid_atual = str(caso.get("return_id") or rid)
        nota, motivo_txt, eventos = await vig._nota_e_motivo(client, rid_atual)
        if situacao == PAGO and _loja_aceitou(eventos):
            # Alguém da loja aceitou o reembolso no Seller Center: não contesta sozinho —
            # só com "Sim" + instrução (lê a alegação mesmo assim).
            situacao = ACEITO_PELA_LOJA
        _pedido_em, prova = vig._pedido_do_comprador(eventos)
        entrega = await vig._entrega_dados(client, (dev.pedido_marketplace or "").strip())
        prazo = vig.prazo_resposta(caso)
        return Caso(
            situacao=situacao,
            codigo=str(caso.get("return_reason_text") or "").strip() or motivo_txt,
            texto=nota,
            ida_entregue=True if entrega else None,
            entregue_em=_ddmm(vig._fmt(entrega.get("quando"))) if entrega else "",
            transportadora=str(entrega.get("transp") or "") if entrega else "",
            comprador_mandou_prova=bool(prova),
            prazo=vig._fmt(prazo) if prazo else "",
        )
    client = await cd._shopee_client_para(session, None, dev)
    det = await client.get_return_detail(rid)
    status = str(det.get("status") or "").strip().upper()
    so = str(det.get("return_solution")) == "1" or det.get("needs_logistics") is False
    if not so:
        return Caso(situacao=SEM_CASO)
    contestacao = det.get("dispute_reason") or []
    if isinstance(contestacao, str):
        contestacao = [contestacao]
    contestada = any(str(x or "").strip() for x in contestacao)
    comp = str((det.get("seller_compensation") or {}).get("seller_compensation_status") or "")
    comp = comp.upper().replace("COMPENSATION_", "")
    neg = det.get("negotiation") if isinstance(det.get("negotiation"), dict) else {}
    if contestada or status == "SELLER_DISPUTE" or comp in ("REQUESTED", "APPROVED", "REJECTED"):
        return Caso(situacao=CONTESTADO)
    if status in ("CANCELLED", "CLOSED"):
        return Caso(situacao=CANCELADO)
    proposta = _proposta_de_verdade(det, neg)
    if proposta:
        return Caso(situacao=NEGOCIACAO, proposta=proposta)
    if status == "REQUESTED":
        situacao = RESPONDER
    elif status in ("JUDGING", "PROCESSING"):
        situacao = EM_ANALISE
    elif status in ("ACCEPTED", "REFUND_PAID"):
        situacao = PAGO
    else:
        return Caso(situacao=SEM_CASO)
    entrega = await cd._entrega_shopee(session, dev)
    sit_ida = entrega.get("situacao") or ""
    ida: bool | None = None
    if sit_ida in cd._SHOPEE_IDA_NAO_ENTREGUE:
        ida = False
    elif sit_ida in cd._SHOPEE_IDA_ENTREGUE or entrega.get("entregue_em") is not None:
        ida = True
    prova: bool | None = None
    if "image" in det or "buyer_videos" in det:
        prova = bool(det.get("image") or det.get("buyer_videos"))
    return Caso(
        situacao=situacao,
        codigo=str(det.get("reason") or "").strip(),
        texto=" ".join(str(det.get("text_reason") or "").split()),
        ida_entregue=ida,
        entregue_em=cd._fmt_brt(entrega.get("entregue_em"), "%d/%m")
        if entrega.get("entregue_em")
        else "",
        comprador_mandou_prova=prova,
        prazo=cd._fmt_brt(det.get("due_date")) if situacao == RESPONDER else "",
    )


async def _pecas(session: AsyncSession, pedido: str) -> list[dict]:
    from app.routers.devolutions import pecas_do_pedido  # tardio: roteador pesado

    return [
        p
        for p in await pecas_do_pedido(session, pedido, exato=True)
        if (p.get("pedido_bling") or "").strip() == pedido and not p.get("ja_devolvido")
    ]


def _linha(peca: dict, *, motivo: str, video: str, defesa: str = "") -> Devolution:
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
        # A "Observação pro chamado" da janela (vale pro pedido inteiro): é a defesa
        # que vai no texto da plataforma.
        observacao=defesa or None,
        # Na Fraude o cliente já foi (ou vai ser) reembolsado pela plataforma.
        reembolso=True,
    )


def _o_que(caso: Caso, resumo: str = "") -> str:
    return (resumo or " — ".join(x for x in (caso.codigo, caso.texto) if x))[:200]


_POR_SITUACAO = {
    CONTESTADO: "o caso já foi contestado na plataforma fora do DaVinci — registrar o lançamento e o chamado à mão",
    NEGOCIACAO: "o comprador fez uma proposta na Shopee (negociação) — decidir à mão",
    ACEITO_PELA_LOJA: "a própria loja aceitou o reembolso no Seller Center — conferir se é pra contestar",
}


async def _analisar(
    session: AsyncSession,
    linha: Any,
    rast: DevolucaoRastreio,
    pecas: list[dict],
    agora: datetime,
    resumo: Resumo,
    *,
    aprovada: tuple[str, str] | None = None,
    instrucao: str = "",
    chave_instr: str = "",
) -> tuple[defesa_ia.Decisao, Caso] | None:
    """Lê o caso e pergunta à IA. Devolve (decisão com motivo e defesa, caso), ou
    None (e já conta e anota o porquê). `aprovada` = a defesa que o Vinicius viu no
    "só propor": relê o caso (ainda dá pra contestar?) mas não pergunta de novo.
    `instrucao` = "Abrir chamado: Sim" + "Instrução IA" da equipe: a IA segue o que
    ela manda (e o reembolso aceito pela própria loja pode ser contestado);
    `chave_instr` vai no recado pra mesma instrução não ser relida."""
    pedido = linha.pedido_bling
    plataforma = (linha.fonte_auto or "").strip().lower()
    rascunho = _linha(pecas[0], motivo="", video="")
    resumo.leituras += 1
    caso = await _ler_caso(session, plataforma, linha.devolucao_id_auto.strip(), rascunho)
    if caso.situacao in (SEM_CASO, CANCELADO):
        # Cancelado pelo comprador / não é mais só reembolso / leitura vazia: o sync
        # de 30 min tira da lista; senão, tenta de novo na próxima rodada.
        resumo.sem_resposta_pendente += 1
        resumo.pedidos[pedido] = (
            "o comprador cancelou" if caso.situacao == CANCELADO else "caso não lido agora"
        )
        return None
    if caso.situacao == ACEITO_PELA_LOJA and instrucao:
        caso = Caso(**{**caso.__dict__, "situacao": PAGO})
    if caso.situacao in _POR_SITUACAO:
        porque = _POR_SITUACAO[caso.situacao]
        if caso.situacao == NEGOCIACAO and caso.proposta:
            porque = (
                f"o comprador propôs reembolso parcial de {caso.proposta} na Shopee — decidir à mão"
            )
        await _anotar_e_gravar(
            session,
            rast,
            f"{MARCA} ({_hoje_txt(agora)}): {porque}; {NAO_RESPONDE}."
            + (f" {chave_instr}" if chave_instr else ""),
        )
        resumo.pra_pessoa += 1
        resumo.pedidos[pedido] = f"pessoa: {caso.situacao}"
        return None
    if aprovada is not None:
        motivo, defesa = aprovada
        return defesa_ia.Decisao(motivo=motivo, defesa=defesa, resumo=_o_que(caso)), caso
    if not caso.codigo.strip() and not caso.texto.strip():
        # A TikTok devolve vazio quando a API falha: não pergunta, tenta de novo.
        resumo.falhas += 1
        resumo.pedidos[pedido] = "alegação não lida"
        return None
    resumo.analises += 1
    fatos = defesa_ia.Fatos(
        plataforma="TikTok Shop" if plataforma == "tiktok" else "Shopee",
        situacao=caso.situacao,
        codigo=caso.codigo,
        texto=caso.texto,
        produtos=defesa_ia.produtos_de(pecas),
        ida_entregue=caso.ida_entregue,
        entregue_em=caso.entregue_em,
        transportadora=caso.transportadora,
        comprador_mandou_prova=caso.comprador_mandou_prova,
        prazo=caso.prazo,
        instrucao=instrucao,
    )
    decisao = await defesa_ia.analisar(fatos)
    if decisao.pessoa:
        await _anotar_e_gravar(
            session,
            rast,
            f'{MARCA} ({_hoje_txt(agora)}): o comprador alega "{_o_que(caso, decisao.resumo)}" — '
            f"{decisao.pessoa}; {NAO_RESPONDE}." + (f" {chave_instr}" if chave_instr else ""),
        )
        resumo.pra_pessoa += 1
        resumo.pedidos[pedido] = f"pessoa: {decisao.pessoa}"
        return None
    if not decisao.motivo:
        # A IA não respondeu (fora do ar, sem chave): tenta na próxima rodada e não
        # pergunta mais nesta (cada tentativa pode levar minutos).
        resumo.falhas += 1
        resumo.ia_fora = True
        resumo.pedidos[pedido] = "a IA não respondeu"
        return None
    return decisao, caso


async def _pedir_video(
    session: AsyncSession,
    pedido: str,
    rast: DevolucaoRastreio,
    agora: datetime,
    resumo: Resumo,
    *,
    voltou: bool = False,
) -> None:
    # O mesmo pedido de vídeo do botão (aparece pra equipe do estoque). `voltou` = o
    # robô tinha cancelado porque o pedido saiu da Fraude: recado próprio, pra ficar
    # DEPOIS do cancelamento na Observação (`_cancelado_pelo_robo`).
    rast.video_solicitado_em = agora
    rast.video_solicitado_por = None
    rast.video_refazer_motivo = None
    volta = "o pedido voltou pra Fraude — " if voltou else ""
    _anotar(rast, f"{MARCA} ({_hoje_txt(agora)}): {volta}{VIDEO_PEDIDO}.")
    await session.commit()
    resumo.video_pedido += 1
    resumo.pedidos[pedido] = "vídeo pedido"


async def _pedir_video_de_novo(
    session: AsyncSession, pedido: str, rast: DevolucaoRastreio, agora: datetime, resumo: Resumo
) -> None:
    """A equipe respondeu "não tenho o vídeo" ("Não tô em casa", 309043): pede de novo,
    uma vez. A equipe vê "refazer: …" com a 1ª resposta (aba Pedidos do Controle de
    Estoque), igual ao "pedir de novo" do botão."""
    antes = " ".join((rast.video_sem_motivo or "").split())[:120]
    rast.video_solicitado_em = agora
    rast.video_solicitado_por = None
    rast.video_enviado_em = None
    rast.video_enviado_por = None
    rast.video_link = None
    rast.video_sem_motivo = None
    rast.video_refazer_motivo = f'pedido de novo — a 1ª resposta foi "{antes}"'
    _anotar(
        rast,
        f'{MARCA} ({_hoje_txt(agora)}): a equipe respondeu sem o vídeo ("{antes}") — '
        f"{VIDEO_PEDIDO_DE_NOVO}.",
    )
    await session.commit()
    resumo.video_pedido += 1
    resumo.pedidos[pedido] = "vídeo pedido de novo"


def _cancelado_pelo_robo(obs: str) -> bool:
    """O último recado de vídeo na Observação é o cancelamento do PRÓPRIO robô (o
    pedido saiu da Fraude e voltou): pede o vídeo de novo em vez de tratar como
    "alguém cancelou" (que fica pra pessoa). PURA."""
    cancelou = obs.rfind(VIDEO_CANCELADO_SAIU)
    return cancelou >= 0 and cancelou > obs.rfind(_PEDIDO_VIDEO)


_RE_CANCELOU = re.compile(
    rf"{re.escape(MARCA)} \((\d{{2}})/(\d{{2}}) (\d{{2}}):(\d{{2}})\): [^\n]*"
    rf"{re.escape(VIDEO_CANCELADO_SAIU)}"
)


def _cancelou_ha_pouco(obs: str, agora: datetime) -> bool:
    """O cancelamento do robô (o último) foi há menos de VOLTOU_ESPERA? A data vem do
    próprio recado ("dd/mm HH:MM", hora de São Paulo, sem ano). PURA."""
    from zoneinfo import ZoneInfo

    achados = list(_RE_CANCELOU.finditer(obs or ""))
    if not achados:
        return False
    dia, mes, hora, minuto = (int(x) for x in achados[-1].groups())
    sp = ZoneInfo("America/Sao_Paulo")
    hoje = agora.astimezone(sp)
    try:
        quando = datetime(hoje.year, mes, dia, hora, minuto, tzinfo=sp)
    except ValueError:
        return False
    if quando > hoje + timedelta(days=1):  # recado de dezembro lido em janeiro
        quando = quando.replace(year=hoje.year - 1)
    return hoje - quando < VOLTOU_ESPERA


def _porque_saiu(
    *, tipo: str, fila_manual: str, abrir_chamado: bool | None, aguardando: bool, resolvido: bool
) -> str:
    """Por que o pedido não é mais da Fraude automática (vai no recado). PURA."""
    if fila_manual == "acompanhamento":
        return "o pedido foi movido pro Acompanhamento"
    if tipo != "REFUND":
        return "o caso deixou de ser só reembolso — o pacote volta pra loja"
    if abrir_chamado is False:
        return 'marcaram "Abrir chamado: Não"'
    if not aguardando:
        return "o Bling saiu de Aguardando Devolução"
    if resolvido:
        return "o pedido foi resolvido no painel"
    return "o pedido não está mais na fila da Fraude automática"


async def _cancelar_video_de_quem_saiu(
    session: AsyncSession, agora: datetime, resumo: Resumo
) -> None:
    """Pedido de vídeo do robô ainda pendente em pedido que não é mais candidato: a
    equipe não precisa mais mandar (e a aba Pedidos dela destrava). Mesma limpeza do
    botão ✕ da coluna Vídeo. Pedido com lançamento ou chamado de devolução fica de fora
    na consulta — alguém está trabalhando nele e o vídeo pode servir. A lista de
    candidatos é lida de novo AGORA: a rodada leva minutos (IA, plataforma) e um pedido
    pode ter voltado pra Fraude no meio dela."""
    linhas = (
        await session.execute(_VIDEO_DO_ROBO, {"marca": MARCA, "pedido_video": _PEDIDO_VIDEO})
    ).all()
    if not linhas:
        return
    ainda = set(await candidatos(session, agora=agora))
    for linha in linhas:
        pedido = linha.pedido_bling
        if pedido in ainda:
            continue
        try:
            rast = await session.get(DevolucaoRastreio, pedido)
            if rast is None:
                continue
            # A equipe pode ter respondido (ou uma pessoa pedido de novo) desde a consulta.
            await session.refresh(rast)
            if (
                rast.video_solicitado_em is None
                or rast.video_enviado_em is not None
                or rast.video_solicitado_por is not None
            ):
                continue
            porque = _porque_saiu(
                tipo=linha.tipo,
                fila_manual=linha.fila_manual,
                abrir_chamado=linha.abrir_chamado,
                aguardando=bool(linha.aguardando),
                resolvido=linha.resolvido_at is not None,
            )
            rast.video_solicitado_em = None
            rast.video_solicitado_por = None
            rast.video_enviado_em = None
            rast.video_enviado_por = None
            rast.video_link = None
            rast.video_sem_motivo = None
            rast.video_refazer_motivo = None
            _anotar(
                rast,
                f"{MARCA} ({_hoje_txt(agora)}): {porque} — {VIDEO_CANCELADO_SAIU}, "
                "a equipe não precisa mais mandar.",
            )
            await session.commit()
            resumo.video_cancelado += 1
            resumo.pedidos[pedido] = "fora da Fraude automática: pedido de vídeo cancelado"
            logger.info("devolucao_fraude_auto_video_cancelado", pedido_bling=pedido, porque=porque)
        except Exception as e:  # noqa: BLE001 — um pedido ruim não para a rodada
            await session.rollback()
            resumo.falhas += 1
            resumo.pedidos[pedido] = f"falhou ao cancelar o vídeo: {type(e).__name__}"
            logger.error("devolucao_fraude_auto_falhou", pedido_bling=pedido, err=str(e)[:200])


async def _lancar(
    session: AsyncSession,
    linha: Any,
    rast: DevolucaoRastreio,
    agora: datetime,
    resumo: Resumo,
    *,
    so_propor: bool,
    instrucao: str = "",
    chave_instr: str = "",
) -> None:
    from app.routers import devolutions as antigo
    from app.services import chamados_devolucao as cd

    pedido = linha.pedido_bling
    if not conferencia.ligada():
        # Sem a IA não há defesa nem conferência antes do tiro único: não lança.
        resumo.pedidos[pedido] = "IA desligada: não lança sozinho"
        return
    pecas = await _pecas(session, pedido)
    if not pecas:
        resumo.pra_pessoa += 1
        resumo.pedidos[pedido] = "sem peças na busca"
        return
    aprovada = None if so_propor or instrucao else proposta_anterior(rast.observacao)
    analisado = await _analisar(
        session,
        linha,
        rast,
        pecas,
        agora,
        resumo,
        aprovada=aprovada,
        instrucao=instrucao,
        chave_instr=chave_instr,
    )
    if analisado is None:
        return
    decisao, caso = analisado
    if so_propor:
        await _anotar_e_gravar(
            session,
            rast,
            f'{MARCA} ({_hoje_txt(agora)}): {PROPOSTA} (motivo "{decisao.motivo}") — o comprador '
            f'alega "{_o_que(caso, decisao.resumo)}"; defesa: "{decisao.defesa}". '
            "Esperando o OK pra lançar e abrir o chamado.",
        )
        resumo.propostos += 1
        resumo.pedidos[pedido] = f"proposto: {decisao.motivo}"
        return
    # A leitura da plataforma e a IA levaram um tempo: alguém lançou, marcou "Abrir
    # chamado: Não" ou tirou da Fraude no meio?
    if not (await session.execute(_AINDA_LIVRE, {"p": pedido})).scalar():
        resumo.pedidos[pedido] = "uma pessoa mexeu no meio"
        return
    await session.refresh(rast, ["observacao", "video_link", "abrir_chamado", "fila_manual"])
    video = (rast.video_link or "").strip()
    if not video:
        resumo.pedidos[pedido] = "o vídeo foi apagado no meio"
        return
    linhas = [_linha(p, motivo=decisao.motivo, video=video, defesa=decisao.defesa) for p in pecas]
    for d in linhas:
        d.tag = antigo._sku_tag(d.sku)
        session.add(d)
    _anotar(
        rast,
        f'{MARCA} ({_hoje_txt(agora)}): o comprador alega "{_o_que(caso, decisao.resumo)}" — '
        f"{conferencia.MARCA_ROBO}: {CONDICAO} / {decisao.motivo}, com o vídeo da embalagem e "
        "a defesa escrita pela IA"
        + (" seguindo a instrução da equipe" if instrucao else "")
        + "; a IA confere antes de a resposta sair.",
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
    resumo.pedidos[pedido] = f"lançado: {decisao.motivo}"
    logger.info("devolucao_fraude_auto_lancado", pedido_bling=pedido, motivo=decisao.motivo)


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
            # "Abrir chamado: Sim" + "Instrução IA" (16/11/2026): o que o robô deixou pra
            # pessoa volta a ser analisado, seguindo a instrução — uma vez por instrução.
            instr = (rast.instrucao_ia_pendente or "").strip() if rast.abrir_chamado is True else ""
            chave_instr = (
                pela_instrucao.chave_da_instrucao(rast.instrucao_ia_em, instr) if instr else ""
            )
            com_instrucao = bool(instr) and chave_instr not in obs
            if NAO_RESPONDE in obs and not com_instrucao:
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
                    _anotar(rast, f"{MARCA} ({_hoje_txt(agora)}): {ACHADO_EXPEDICAO}.")
                    await session.commit()
                    resumo.video_da_expedicao += 1
                    video = achado
            if not video:
                if (rast.video_sem_motivo or "").strip():
                    respondido = rast.video_enviado_em
                    if (
                        VIDEO_PEDIDO_DE_NOVO not in obs
                        and respondido is not None
                        and agora - respondido >= PEDIR_DE_NOVO_DEPOIS
                    ):
                        await _pedir_video_de_novo(session, pedido, rast, agora, resumo)
                    else:
                        resumo.pra_pessoa += 1
                        resumo.pedidos[pedido] = "sem vídeo (equipe)"
                elif rast.video_solicitado_em is not None:
                    resumo.esperando_video += 1
                    resumo.pedidos[pedido] = "esperando o vídeo"
                elif VIDEO_PEDIDO in obs and not com_instrucao and not _cancelado_pelo_robo(obs):
                    # O robô pediu e alguém cancelou o pedido: não pede de novo; fica
                    # pra pessoa (aparece no cartão "Fraude: responder à mão"). Se quem
                    # cancelou foi o próprio robô (o pedido saiu da Fraude e voltou), o
                    # `else` pede de novo.
                    _anotar(
                        rast,
                        f"{MARCA} ({_hoje_txt(agora)}): o pedido de vídeo foi cancelado — "
                        f"{NAO_RESPONDE}.",
                    )
                    await session.commit()
                    resumo.pra_pessoa += 1
                    resumo.pedidos[pedido] = "pedido de vídeo cancelado"
                elif _cancelado_pelo_robo(obs) and _cancelou_ha_pouco(obs, agora):
                    # Voltou pra Fraude logo depois do cancelamento do robô: pode ser só
                    # a leitura do rastreio da ida que falhou nesta meia hora.
                    resumo.pedidos[pedido] = "voltou pra Fraude há pouco (espera pra pedir)"
                else:
                    await _pedir_video(
                        session, pedido, rast, agora, resumo, voltou=_cancelado_pelo_robo(obs)
                    )
                continue
            if m not in (LIGADO, PROPOR):
                continue
            if m == PROPOR and PROPOSTA in obs:
                resumo.pedidos[pedido] = "proposta escrita (esperando o OK)"
                continue
            if (
                resumo.lancados >= TETO_LANCAMENTOS
                or resumo.analises >= TETO_ANALISES
                or resumo.leituras >= TETO_LEITURAS
                or resumo.ia_fora
            ):
                resumo.pedidos.setdefault(pedido, "fica pra próxima rodada")
                continue
            await _lancar(
                session,
                linha,
                rast,
                agora,
                resumo,
                so_propor=m == PROPOR,
                instrucao=instr if com_instrucao else "",
                chave_instr=chave_instr if com_instrucao else "",
            )
        except Exception as e:  # noqa: BLE001 — um pedido ruim não para a rodada
            await session.rollback()
            resumo.falhas += 1
            resumo.pedidos[pedido] = f"falhou: {type(e).__name__}"
            logger.error("devolucao_fraude_auto_falhou", pedido_bling=pedido, err=str(e)[:200])
    await _cancelar_video_de_quem_saiu(session, agora, resumo)
    return resumo.como_dict() | {"modo": m}


async def candidatos(session: AsyncSession, *, agora: datetime | None = None) -> list[str]:
    """Só a lista (testes e conferência em produção)."""
    agora = agora or datetime.now(UTC)
    rows = (await session.execute(_SQL, {"acoes": list(ACOES_RESPONDER), "agora": agora})).all()
    return [r.pedido_bling for r in rows]
