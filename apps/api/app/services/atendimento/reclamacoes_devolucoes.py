"""Devoluções e disputas de Shopee e TikTok no atendimento (RF2, 01/10/2026).

A Logística JÁ lê os casos de pós-venda das duas, a loja inteira, em lote
(`logistica_shopee.sweep_pos_venda` e `logistica_tiktok.sweep_pos_venda`):
o caso que vale para o pedido (o VIVO mais recente) fica na assinatura da
linha da Logística — `logistica.meli_status.return_status` (+ `return_type`
na TikTok) — com a data da mudança em `logistica.status_datas.return_status`.
O pedido que passou por "Aguardando Devolução" ganha ainda, em
`devolucao_rastreio` (grão de pedido do Bling), o id do caso na plataforma
(`devolucao_id_auto`), o tipo (`devolucao_tipo_auto`), a ação pendente da
loja e o PRAZO (`acao_auto`/`prazo_acao_auto`) e a abertura
(`devolucao_criada_em`). Nada aqui chama a API das plataformas: é só ligar o
que já está no banco ao atendimento —

  caso da Logística → uma linha em `atendimento_reclamacoes` (`dados.fonte =
  'logistica'`) ligada à conversa do chat daquele pedido (quando existe) →
  `etiqueta.recalcular_etiqueta` nas conversas do pedido.

O TIPO (o que a etiqueta mostra):
  • disputa (a plataforma julgando) = `reclamacao` → etiqueta Reclamação:
    Shopee JUDGING / SELLER_DISPUTE; TikTok REJECT_RECEIVE_PACKAGE (a loja
    recusou o pacote e a TikTok analisa);
  • o resto (pedido de devolução ou de reembolso) = `devolucao` → Devolução.

ABERTA (`encerrada_em IS NULL`) — o "vivo" da Logística, com uma exceção:
  • encerrada: os status finais (`logistica_rules.RETURN_ENCERRADO`: Shopee
    CANCELLED/CLOSED; TikTok cancelado, recusado e concluído);
  • Shopee ACCEPTED/REFUND_PAID = o reembolso JÁ foi pago ao comprador
    (medido em 17/09: a Shopee deixa o caso em ACCEPTED para sempre — a
    Logística o mantém "vivo" por causa do pacote). No atendimento ele só
    conta como aberto enquanto a Shopee ainda espera algo da loja com prazo
    no futuro (conferir o pacote, evidência); o pacote que volta continua
    marcado pelo Bling em Aguardando Devolução, que a etiqueta já lê.

O ID: `devolucao_id_auto` quando a Logística o tem (return_sn da Shopee,
return_id da TikTok); sem ele, `"pedido <nº>"` (`reclamacoes.PREFIXO_SEM_ID`),
trocado pelo id quando ele aparecer. A Logística guarda UM caso por pedido:
o caso novo de um pedido encerra aqui o anterior que ainda estava aberto.

Caso encerrado há mais de `reclamacoes.JANELA_ENCERRADAS` que nunca entrou
aqui fica de fora (história). Cada caso grava num SAVEPOINT: um caso com
problema não derruba os outros. Não commita (salvo `commit_a_cada`, que o
cron usa para não segurar as conversas travadas até o fim).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoReclamacao, DevolucaoRastreio, Integration, Logistica
from app.services import logistica_rules
from app.services.atendimento import etiqueta_fatos, reclamacoes
from app.services.atendimento.constantes import (
    RECLAMACAO_TIPO_DEVOLUCAO,
    RECLAMACAO_TIPO_RECLAMACAO,
)

logger = structlog.get_logger()

FONTE_LOGISTICA = "logistica"
PLATAFORMAS_LIGADAS = ("shopee", "tiktok")
MOTIVO_ETIQUETA = "devolução lida pela Logística"

# Como a Logística escreve a plataforma (`logistica.plataforma`, texto livre).
_ROTULOS = {
    **dict.fromkeys(logistica_rules._SHOPEE_PLATAFORMAS, "shopee"),
    **dict.fromkeys(logistica_rules._TIKTOK_PLATAFORMAS, "tiktok"),
}

# A plataforma julgando: é reclamação (disputa), não só devolução.
STATUS_DISPUTA = {
    "shopee": frozenset({"JUDGING", "SELLER_DISPUTE"}),
    "tiktok": frozenset({"REJECT_RECEIVE_PACKAGE"}),
}
# Shopee: o reembolso já saiu para o comprador (ver o topo).
SHOPEE_REEMBOLSADA = frozenset({"ACCEPTED", "REFUND_PAID"})
# Texto do status ENCERRADO (o `devolucao_status_pt` só traduz o vivo).
_STATUS_ENCERRADO_PT = {
    "shopee": {
        "CANCELLED": "Devolução cancelada",
        "CLOSED": "Devolução encerrada pela Shopee",
        "ACCEPTED": "Devolução aceita — reembolso pago ao comprador",
        "REFUND_PAID": "Reembolso pago pela Shopee",
    },
    "tiktok": logistica_rules.TIKTOK_RETURN_STATUS_LABELS_PT,
}

_LOTE_IN = 1000


def _agora() -> datetime:
    return datetime.now(UTC)


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat(timespec="seconds") if quando else None


def _texto(bruto: Any) -> str:
    return "" if bruto is None else str(bruto).strip()


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def plataforma_da_logistica(rotulo: str | None) -> str | None:
    """'Shopee' / 'TikTok Shop' → 'shopee' / 'tiktok'; outra → None."""
    return _ROTULOS.get(_texto(rotulo).lower())


@dataclass(frozen=True)
class CasoLogistica:
    """O caso de devolução de UM pedido, como a Logística o guarda."""

    plataforma: str
    pedido_marketplace: str
    status: str
    pedido_bling: str | None = None
    conta: str | None = None
    # REFUND (só reembolso) | RETURN_AND_REFUND | REPLACEMENT
    tipo_caso: str | None = None
    # Quando o status do caso mudou (o carimbo da Logística).
    mudou_em: datetime | None = None
    logistica_id: UUID | None = None
    # De `devolucao_rastreio` (só quando o pedido passou por 83957).
    return_id: str | None = None
    acao: str | None = None
    prazo: datetime | None = None
    criada_em: datetime | None = None


def tipo_do_caso(caso: CasoLogistica) -> str:
    if caso.status in STATUS_DISPUTA.get(caso.plataforma, ()):
        return RECLAMACAO_TIPO_RECLAMACAO
    return RECLAMACAO_TIPO_DEVOLUCAO


def caso_aberto(caso: CasoLogistica, agora: datetime) -> bool:
    """O caso ainda pede a atenção da loja? (ver o topo do módulo)"""
    if not caso.status or caso.status in logistica_rules.RETURN_ENCERRADO:
        return False
    if caso.plataforma == "shopee" and caso.status in SHOPEE_REEMBOLSADA:
        return caso.prazo is not None and caso.prazo > agora
    return True


def status_texto(caso: CasoLogistica, aberto: bool) -> str | None:
    """O status em português, o mesmo da aba Acompanhamento quando vivo."""
    if aberto:
        return logistica_rules.devolucao_status_pt(
            caso.plataforma, {"return_status": caso.status, "return_type": caso.tipo_caso or ""}
        )
    return _STATUS_ENCERRADO_PT.get(caso.plataforma, {}).get(caso.status) or "Encerrada"


def motivo_do_caso(caso: CasoLogistica) -> str | None:
    """O tipo do caso, da plataforma: "Só reembolso (produto fica com o cliente)"…"""
    if not caso.tipo_caso:
        return None
    return logistica_rules.TIKTOK_RETURN_TYPE_LABELS_PT.get(caso.tipo_caso.upper())


def externo_id_do_caso(caso: CasoLogistica) -> str:
    return caso.return_id or f"{reclamacoes.PREFIXO_SEM_ID}{caso.pedido_marketplace}"


# ── Leitura do banco ──────────────────────────────────────────────────────


async def casos_da_logistica(
    session: AsyncSession, pedidos: Collection[str] | None = None
) -> list[CasoLogistica]:
    """Um caso por (plataforma, pedido): o da linha com a mudança mais recente."""
    plataforma_col = func.lower(func.trim(Logistica.plataforma))
    status_col = func.upper(func.coalesce(Logistica.meli_status["return_status"].astext, ""))
    consulta = select(
        Logistica.id,
        Logistica.plataforma,
        Logistica.conta,
        Logistica.pedido_bling,
        Logistica.pedido_marketplace,
        Logistica.meli_status,
        Logistica.status_datas,
    ).where(
        plataforma_col.in_(tuple(_ROTULOS)),
        status_col != "",
        func.coalesce(Logistica.pedido_marketplace, "") != "",
    )
    if pedidos is not None:
        consulta = consulta.where(Logistica.pedido_marketplace.in_(list(pedidos)))
    linhas = (await session.execute(consulta)).all()

    melhores: dict[tuple[str, str], tuple[datetime, Any]] = {}
    for linha in linhas:
        plataforma = plataforma_da_logistica(linha.plataforma)
        pedido = _texto(linha.pedido_marketplace)
        if plataforma is None or not pedido:
            continue
        carimbo = (linha.status_datas or {}).get("return_status") or {}
        mudou = reclamacoes.data_ml(carimbo.get("em")) if isinstance(carimbo, dict) else None
        chave = (plataforma, pedido)
        ordem = mudou or datetime.min.replace(tzinfo=UTC)
        if chave not in melhores or ordem > melhores[chave][0]:
            melhores[chave] = (ordem, (linha, mudou))

    rastreios: dict[str, DevolucaoRastreio] = {}
    bling = sorted({_texto(v[1][0].pedido_bling) for v in melhores.values()} - {""})
    for inicio in range(0, len(bling), _LOTE_IN):
        lote = bling[inicio : inicio + _LOTE_IN]
        for dr in (
            await session.execute(
                select(DevolucaoRastreio).where(DevolucaoRastreio.pedido_bling.in_(lote))
            )
        ).scalars():
            rastreios[dr.pedido_bling] = dr

    casos = []
    for (plataforma, pedido), (_, (linha, mudou)) in melhores.items():
        meli = linha.meli_status or {}
        dr = rastreios.get(_texto(linha.pedido_bling))
        # O rastreio da devolução só vale se for DESTA plataforma (o pedido do
        # Bling não muda de plataforma, mas a fonte do automático diz qual leu).
        if dr is not None and _texto(dr.fonte_auto).lower() != plataforma:
            dr = None
        tipo_caso = _texto(meli.get("return_type")).upper() or (
            _texto(dr.devolucao_tipo_auto).upper() if dr is not None else ""
        )
        casos.append(
            CasoLogistica(
                plataforma=plataforma,
                pedido_marketplace=pedido,
                status=_texto(meli.get("return_status")).upper(),
                pedido_bling=_texto(linha.pedido_bling) or None,
                conta=_texto(linha.conta) or None,
                tipo_caso=tipo_caso or None,
                mudou_em=mudou,
                logistica_id=linha.id,
                return_id=(_texto(dr.devolucao_id_auto) or None) if dr is not None else None,
                acao=(_texto(dr.acao_auto) or None) if dr is not None else None,
                prazo=_utc(dr.prazo_acao_auto) if dr is not None else None,
                criada_em=_utc(dr.devolucao_criada_em) if dr is not None else None,
            )
        )
    return casos


async def _existentes(
    session: AsyncSession, casos: list[CasoLogistica]
) -> dict[tuple[str, str], list[AtendimentoReclamacao]]:
    """As linhas que esta fonte já escreveu, por (plataforma, pedido)."""
    saida: dict[tuple[str, str], list[AtendimentoReclamacao]] = {}
    pedidos = sorted({c.pedido_marketplace for c in casos})
    for inicio in range(0, len(pedidos), _LOTE_IN):
        lote = pedidos[inicio : inicio + _LOTE_IN]
        for r in (
            await session.execute(
                select(AtendimentoReclamacao)
                .where(
                    AtendimentoReclamacao.plataforma.in_(PLATAFORMAS_LIGADAS),
                    AtendimentoReclamacao.pedido_marketplace.in_(lote),
                    AtendimentoReclamacao.dados["fonte"].astext == FONTE_LOGISTICA,
                )
                .order_by(AtendimentoReclamacao.created_at.desc())
            )
        ).scalars():
            saida.setdefault((r.plataforma, r.pedido_marketplace or ""), []).append(r)
    return saida


class _Integracoes:
    """A integração da conta da Logística (o mesmo casamento da Logística), com cache."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], Integration | None] = {}

    async def de(self, session: AsyncSession, plataforma: str, conta: str | None) -> UUID | None:
        chave = (plataforma, _texto(conta).lower())
        if not chave[1]:
            return None
        if chave not in self._cache:
            if plataforma == "shopee":
                from app.services.logistica_shopee import _shopee_integration_for_conta as achar
            else:
                from app.services.logistica_tiktok import _tiktok_integration_for_conta as achar
            self._cache[chave] = await achar(session, conta)
        integ = self._cache[chave]
        return integ.id if integ is not None else None


# ── Gravação ──────────────────────────────────────────────────────────────


def _escolher(
    existentes: list[AtendimentoReclamacao], externo_id: str, pedido: str
) -> AtendimentoReclamacao | None:
    """A linha que este caso atualiza (ver "O ID" no topo do módulo)."""
    sem_id = f"{reclamacoes.PREFIXO_SEM_ID}{pedido}"
    for r in existentes:
        if r.externo_id == externo_id:
            return r
    if externo_id != sem_id:
        # O id apareceu: a linha que esperava por ele passa a usá-lo.
        return next((r for r in existentes if r.externo_id == sem_id), None)
    # Sem id agora: a linha mais recente do pedido é a do caso atual.
    return existentes[0] if existentes else None


def _retrato(r: AtendimentoReclamacao) -> tuple:
    return (
        r.externo_id,
        r.tipo,
        r.status,
        r.encerrada_em is None,
        r.prazo_em,
        r.conversa_id,
    )


async def ligar_caso(
    session: AsyncSession,
    caso: CasoLogistica,
    existentes: list[AtendimentoReclamacao],
    agora: datetime,
    integration_id: UUID | None,
) -> tuple[AtendimentoReclamacao | None, bool, bool]:
    """Grava o caso → (linha, criada, mudou). (None, …) = história, fica de fora. Não commita."""
    aberto = caso_aberto(caso, agora)
    if (
        not aberto
        and not existentes
        and (caso.mudou_em is None or caso.mudou_em < agora - reclamacoes.JANELA_ENCERRADAS)
    ):
        return None, False, False
    externo_id = externo_id_do_caso(caso)
    alvo = _escolher(existentes, externo_id, caso.pedido_marketplace)
    if caso.return_id is None and alvo is not None:
        # Sem o id agora (o rastreio da devolução sumiu): a linha não perde o
        # id que já tinha.
        externo_id = alvo.externo_id
    if alvo is None or alvo.externo_id != externo_id:
        # O mesmo caso pode já estar aqui por outro caminho (UNIQUE plataforma
        # + id): é ele que se atualiza — a linha sem id fica encerrada abaixo.
        mesma = (
            await session.execute(
                select(AtendimentoReclamacao).where(
                    AtendimentoReclamacao.plataforma == caso.plataforma,
                    AtendimentoReclamacao.externo_id == externo_id,
                )
            )
        ).scalar_one_or_none()
        if mesma is not None:
            alvo = mesma
    criada = alvo is None
    antes = None if alvo is None else _retrato(alvo)
    if alvo is None:
        alvo = AtendimentoReclamacao(
            plataforma=caso.plataforma, externo_id=externo_id, tipo=tipo_do_caso(caso), dados={}
        )
        session.add(alvo)
    alvo.externo_id = externo_id
    alvo.tipo = tipo_do_caso(caso)
    alvo.status = caso.status
    alvo.motivo = motivo_do_caso(caso)
    alvo.pedido_marketplace = caso.pedido_marketplace
    if integration_id is not None:
        alvo.integration_id = integration_id
    alvo.prazo_em = caso.prazo if aberto else None
    if alvo.aberta_em is None:
        alvo.aberta_em = caso.criada_em or caso.mudou_em or agora
    if aberto:
        alvo.encerrada_em = None
    elif alvo.encerrada_em is None:
        alvo.encerrada_em = caso.mudou_em or agora
    acao_texto = (
        logistica_rules.acao_plataforma_pt(caso.plataforma, caso.acao)
        if aberto and caso.acao
        else None
    )
    alvo.dados = {
        **(alvo.dados or {}),
        "fonte": FONTE_LOGISTICA,
        "logistica_id": str(caso.logistica_id) if caso.logistica_id else None,
        "pedido_bling": caso.pedido_bling,
        "conta": caso.conta,
        "return_id": caso.return_id,
        "tipo_caso": caso.tipo_caso,
        "acao_pendente": caso.acao if aberto else None,
        "acao_texto": acao_texto,
        "status_texto": status_texto(caso, aberto),
        "mudou_em": _iso(caso.mudou_em),
    }
    conversa = await etiqueta_fatos.conversa_do_pedido(
        session, caso.plataforma, caso.pedido_marketplace
    )
    if conversa is not None:
        alvo.conversa_id = conversa.id
    await session.flush()
    # A Logística guarda UM caso por pedido: o que era o caso de antes e
    # ainda estava aberto aqui acabou (foi substituído pelo atual).
    for outra in existentes:
        if outra is alvo or outra.encerrada_em is not None:
            continue
        outra.encerrada_em = agora
        outra.prazo_em = None
        outra.dados = {**(outra.dados or {}), "substituida_por": externo_id}
    await session.flush()
    return alvo, criada, criada or antes != _retrato(alvo)


async def ligar_devolucoes(
    session: AsyncSession,
    *,
    agora: datetime | None = None,
    pedidos: Collection[str] | None = None,
    commit_a_cada: int | None = None,
) -> dict:
    """Liga as devoluções/disputas de Shopee e TikTok da Logística ao atendimento.

    Uma linha por caso em `atendimento_reclamacoes`, ligada à conversa do
    pedido, e a etiqueta recalculada nas conversas do pedido quando o caso
    mudou. `pedidos` limita aos nºs na plataforma dados (o gancho de quem
    acabou de ler um caso). Não commita, a não ser com `commit_a_cada`
    (commit a cada N casos gravados). Devolve as contagens.
    """
    agora = agora or _agora()
    casos = await casos_da_logistica(session, pedidos)
    existentes = await _existentes(session, casos)
    integracoes = _Integracoes()
    resumo = {
        "casos": len(casos),
        "novas": 0,
        "atualizadas": 0,
        "historia": 0,
        "ligadas": 0,
        "etiquetas": 0,
        "erros": 0,
    }
    gravados = 0
    for caso in casos:
        try:
            async with session.begin_nested():
                integration_id = await integracoes.de(session, caso.plataforma, caso.conta)
                linha, criada, mudou = await ligar_caso(
                    session,
                    caso,
                    existentes.get((caso.plataforma, caso.pedido_marketplace), []),
                    agora,
                    integration_id,
                )
                if linha is None:
                    resumo["historia"] += 1
                    continue
                if criada:
                    resumo["novas"] += 1
                elif mudou:
                    resumo["atualizadas"] += 1
                if linha.conversa_id is not None:
                    resumo["ligadas"] += 1
                if mudou:
                    alvo = await reclamacoes.conversas_para_etiqueta(
                        session, caso.plataforma, [caso.pedido_marketplace]
                    )
                    resumo["etiquetas"] += await reclamacoes.recalcular_etiquetas(
                        session, alvo, motivo=MOTIVO_ETIQUETA, agora=agora
                    )
                    gravados += 1
        except Exception as exc:  # noqa: BLE001 — um caso não derruba os outros
            resumo["erros"] += 1
            logger.warning(
                "atendimento_devolucao_ligar_falhou",
                plataforma=caso.plataforma,
                pedido=caso.pedido_marketplace,
                err=type(exc).__name__,
            )
            continue
        if commit_a_cada and gravados and gravados % commit_a_cada == 0:
            await session.commit()
    logger.info("atendimento_devolucoes_ligadas", **resumo)
    return resumo
