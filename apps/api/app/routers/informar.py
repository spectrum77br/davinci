"""Botões INFORMAR (admin-only) — resumo sob demanda via Threema.

Três contextos:
- `logistica`: manda a lista dos pedidos ACOMPANHADOS no painel Logística
  (mesma regra de visibilidade do painel, incluindo o passe-livre de
  "Problemas"), no formato `pedido marketplace - conta - status plataforma -
  status bling`.
- `controle_estoque`: manda os pedidos movidos pra Aguardando Cancelamento por
  falta de estoque (marca `sem_estoque` na nf_faturamento) que AINDA estão
  nessa situação no Bling.
- `margem`: manda os pedidos pendentes de análise na aba Pendentes da Margem
  (mesmo WHERE da listagem com status=Pendente), UMA MENSAGEM POR PEDIDO com
  conta, motivo, margem vs mínima e lucro — pedido do Eduardo (02/09): "veio
  todas as margens que deram negativa juntos, tem que ser separado com o nome
  da conta, a diferença de valor".

Há ainda um quarto cadastro SEM envio manual: `margem_auto` guarda quem
recebe o aviso automático que o auto-hold manda NA HORA em que segura um
pedido (services/margem_auto_hold._avisar_threema). Ele aceita GET/PUT como
os demais (o modal da Margem edita os dois), mas `/enviar` não existe pra ele
— quem envia é o robô.

Cada contexto tem seu cadastro de destinatários (`threema_informar_config`),
editado no modal do botão. O diretório de opções vem do CADASTRO DE USUÁRIOS
(campo Threema da tela Usuários — Eduardo 02/09: "eu vou alimentar os
codigos threemas tem que aparecer no para informar das abas que tem"),
completado pelas entradas legadas do `.env` cujo ID ninguém tem no cadastro.
Tudo aqui exige admin — botão, cadastro e envio —
com UMA exceção: os contextos da Margem também liberam o gerente por e-mail
(_EMAILS_EXTRAS; pedido do Eduardo 01/09: "pro cairo que é gerente pode
aparecer informar na aba margem").
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user
from app.models import (
    BlingOrder,
    Logistica,
    NfFaturamento,
    StoreInfo,
    ThreemaInformarConfig,
    User,
    UserRole,
    UserStatus,
)
from app.routers.nf import _SITUACAO_AGUARDANDO_CANCELAMENTO
from app.schemas.informar import InformarConfigIn, InformarConfigOut, InformarEnviarOut
from app.services import aprovar_link, informar, threema
from app.services.logistica_ingest import _ids_pendentes
from app.services.margem_auto_hold import _motivo as _motivo_margem
from app.services.verificar_margem import SNAPSHOT_TABLE

logger = structlog.get_logger()

router = APIRouter(prefix="/api/informar", tags=["informar"])

# Contextos com cadastro (GET/PUT). `margem_auto` NÃO tem envio manual — é a
# lista do aviso automático do auto-hold (ver docstring do módulo).
_CONTEXTOS = ("logistica", "controle_estoque", "margem", "margem_auto")
_CONTEXTOS_ENVIO = ("logistica", "controle_estoque", "margem")

# Não-admins liberados POR CONTEXTO (e-mail minúsculo). Só a Margem tem
# exceção: o gerente (Cairo) vê e usa o botão de lá (incluindo o cadastro do
# aviso automático); Logística e Controle de Estoque seguem admin-only.
# Espelho no front: INFORMAR_MARGEM_USERS em pages/margem.vue — mudou aqui,
# muda lá.
_EMAILS_MARGEM = frozenset({"sa.geral@tutamail.com"})
_EMAILS_EXTRAS: dict[str, frozenset[str]] = {
    "margem": _EMAILS_MARGEM,
    "margem_auto": _EMAILS_MARGEM,
}


def _valida_contexto(contexto: str) -> str:
    if contexto not in _CONTEXTOS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "contexto_desconhecido"}
        )
    return contexto


def _exige_acesso(user: User, contexto: str) -> None:
    """Admin sempre; não-admin só se o e-mail estiver liberado pro contexto."""
    if user.role == UserRole.ADMIN:
        return
    email = (user.email or "").strip().lower()
    if email in _EMAILS_EXTRAS.get(contexto, frozenset()):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "admin_only"})


async def _diretorio(session: AsyncSession) -> list[dict[str, str]]:
    """Opções de destinatário `[{id, nome}]` pro seletor do modal.

    Fonte principal: usuários ATIVOS com o campo Threema preenchido na tela
    Usuários (sem desativados nem usuários-sistema). Completa com as entradas
    legadas do `.env` cujo ID ninguém tem no cadastro — assim nada some
    enquanto o Eduardo alimenta os códigos; quando o dono do código ganhar
    cadastro, a entrada do `.env` dá lugar ao nome real. Ordem alfabética."""
    rows = (
        (
            await session.execute(
                select(User).where(
                    User.threema.is_not(None),
                    func.trim(User.threema) != "",
                    User.status == UserStatus.ACTIVE,
                    User.disabled_at.is_(None),
                    User.open_id.notlike("system:%"),
                )
            )
        )
        .scalars()
        .all()
    )
    por_id: dict[str, str] = {}
    for u in rows:
        # parse_recipients normaliza (maiúsculas, separadores) — aceita o
        # campo como for digitado.
        for rid in threema.parse_recipients(u.threema):
            por_id.setdefault(rid, u.name or u.email)
    s = get_settings()
    env = threema.parse_recipient_directory(
        s.threema_recipient_names, s.threema_recipients
    )
    out = [{"id": rid, "nome": nome} for rid, nome in por_id.items()]
    out += [d for d in env if d["id"] not in por_id]
    out.sort(key=lambda d: (d["nome"] or "").lower())
    return out


async def _config_row(
    session: AsyncSession, contexto: str
) -> ThreemaInformarConfig | None:
    return (
        await session.execute(
            select(ThreemaInformarConfig).where(
                ThreemaInformarConfig.contexto == contexto
            )
        )
    ).scalar_one_or_none()


def _to_config_out(
    contexto: str,
    row: ThreemaInformarConfig | None,
    destinatarios: list[dict[str, str]],
) -> InformarConfigOut:
    return InformarConfigOut(
        contexto=contexto,
        recipients=threema.parse_recipients(row.recipients if row else ""),
        destinatarios=destinatarios,
    )


@router.get("/{contexto}", response_model=InformarConfigOut)
async def get_config(
    contexto: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> InformarConfigOut:
    """Cadastro atual + diretório de destinatários (pro modal do botão)."""
    contexto = _valida_contexto(contexto)
    _exige_acesso(user, contexto)
    return _to_config_out(
        contexto, await _config_row(session, contexto), await _diretorio(session)
    )


@router.put("/{contexto}", response_model=InformarConfigOut)
async def put_config(
    contexto: str,
    body: InformarConfigIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> InformarConfigOut:
    """Salva quem recebe o relatório deste contexto. Aceita só IDs que existem
    no diretório (cadastro de usuários + `.env` legado; o modal manda os
    marcados)."""
    contexto = _valida_contexto(contexto)
    _exige_acesso(user, contexto)
    diretorio = await _diretorio(session)
    validos = {d["id"] for d in diretorio}
    escolhidos = [
        rid
        for rid in threema.parse_recipients(",".join(body.recipients))
        if rid in validos
    ]
    row = await _config_row(session, contexto)
    if row is None:
        row = ThreemaInformarConfig(contexto=contexto, recipients=",".join(escolhidos))
        session.add(row)
    else:
        row.recipients = ",".join(escolhidos)
    await session.commit()
    logger.info("informar_config_salva", contexto=contexto, recipients=escolhidos)
    return _to_config_out(contexto, row, diretorio)


async def _linhas_logistica(session: AsyncSession) -> list[str]:
    """Os pedidos que o painel Logística está MOSTRANDO agora (mesma regra de
    visibilidade das abas, com o passe-livre de "Problemas")."""
    pend = await _ids_pendentes(session)
    ids = [i for v in pend.values() for i in v]
    if not ids:
        return []
    rows = (
        (await session.execute(select(Logistica).where(Logistica.id.in_(ids))))
        .scalars()
        .all()
    )
    return informar.linhas_logistica(rows)


async def _linhas_estoque(session: AsyncSession) -> list[str]:
    """Pedidos com a marca `sem_estoque` que AINDA estão em Aguardando
    Cancelamento no Bling. Rótulo da loja igual ao aviso automático do sweep
    (`plataforma conta equipe N`); SKUs saem do erro gravado na marca."""
    marcados = (
        await session.execute(
            select(NfFaturamento.pedido_bling, NfFaturamento.erro_faturamento).where(
                NfFaturamento.status_faturamento == "sem_estoque"
            )
        )
    ).all()
    if not marcados:
        return []
    numeros = [m.pedido_bling for m in marcados]
    lojas = {
        numero: " ".join(
            p
            for p in (
                plataforma,
                conta,
                f"equipe {equipe}" if equipe is not None else None,
            )
            if p
        )
        for numero, plataforma, conta, equipe in (
            await session.execute(
                select(
                    BlingOrder.numero,
                    func.max(func.lower(StoreInfo.platform)),
                    func.max(StoreInfo.account_name),
                    func.max(StoreInfo.sales_team),
                )
                .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
                .where(BlingOrder.numero.in_(numeros))
                .where(
                    BlingOrder.situacao == str(_SITUACAO_AGUARDANDO_CANCELAMENTO)
                )
                .group_by(BlingOrder.numero)
            )
        ).all()
    }
    entries: list[tuple[str, str, str]] = []
    for m in marcados:
        if m.pedido_bling not in lojas:
            continue  # já saiu de Aguardando Cancelamento — não informa
        skus = (m.erro_faturamento or "").partition("saldo negativo:")[2].strip()
        entries.append((m.pedido_bling, lojas[m.pedido_bling], skus))
    return informar.linhas_estoque(entries)


async def _pedidos_margem(session: AsyncSession) -> list[informar.MargemPedido]:
    """Os pedidos que a aba Pendentes da Margem está MOSTRANDO agora — mesmo
    WHERE da listagem com status=Pendente (routers/margens.py), agregado por
    pedido. O motivo por pedido usa as mesmas palavras do recado do auto-hold
    (`margem_auto_hold._motivo`), com "aguardando saldo da plataforma"
    cobrindo tanto o líquido nulo das não-confiáveis (Amazon pré-settlement)
    quanto o das confiáveis (ML/Shopee/TikTok). Os números por pedido: pior
    margem entre os itens que dispararam o gatilho (e a mínima exigida deles)
    + soma do lucro real de todos os itens."""
    # Import tardio como em margem_auto_hold: a definição canônica de
    # "Pendente" mora em routers/margens.py — buscar lá garante que o relatório
    # mostra EXATAMENTE o que a aba mostra (se a regra mudar, muda junto).
    from app.routers.margens import (
        _ATTENTION_FRETE_SQL,
        _ATTENTION_MARGEM_SQL,
        _ATTENTION_SALDO_AGUARDANDO_SQL,
        _ATTENTION_SALDO_SQL,
        NEEDS_ATTENTION_SQL,
        SITUACAO_REPROVADO,
    )

    sql = f"""
        SELECT v.pedido_bling,
               MAX(COALESCE(v.plataforma_bling, v.plataforma_financeiro))
                                                AS plataforma,
               MAX(v.loja_nome)                 AS conta,
               BOOL_OR({_ATTENTION_MARGEM_SQL}) AS margem_baixa,
               BOOL_OR({_ATTENTION_SALDO_SQL}
                       AND v.marketplace_liquido_base_margem_item IS NOT NULL)
                                                AS saldo_divergente,
               BOOL_OR(({_ATTENTION_SALDO_SQL}
                        AND v.marketplace_liquido_base_margem_item IS NULL)
                       OR {_ATTENTION_SALDO_AGUARDANDO_SQL})
                                                AS saldo_pendente,
               -- ×100: o snapshot guarda margens como FRAÇÃO (0.069 = 6,9%);
               -- a mensagem mostra em % como a aba faz.
               MIN(v.marketplace_margem)
                   FILTER (WHERE {_ATTENTION_MARGEM_SQL}) * 100 AS margem,
               MAX(v.margem_minima)
                   FILTER (WHERE {_ATTENTION_MARGEM_SQL}) * 100 AS minima,
               SUM(v.marketplace_lucro)         AS lucro
        FROM {SNAPSHOT_TABLE} v
        WHERE v.situacao_nome != 'Cancelado'
          AND (v.situacao IS DISTINCT FROM '{SITUACAO_REPROVADO}'
               OR v.bling_status_margem = 'Pendente')
          AND NOT {_ATTENTION_FRETE_SQL}
          AND (v.bling_status_margem = 'Pendente'
               OR (v.bling_status_margem IS NULL AND {NEEDS_ATTENTION_SQL}))
        GROUP BY v.pedido_bling
        ORDER BY v.pedido_bling
    """  # noqa: S608 — fragmentos fixos vindos da aba, sem input de usuário
    rows = (await session.execute(text(sql))).mappings().all()
    return [
        informar.MargemPedido(
            pedido=str(r["pedido_bling"]),
            loja=" ".join(
                p
                for p in (
                    (r["plataforma"] or "").strip(),
                    (r["conta"] or "").strip(),
                )
                if p
            ),
            motivo=_motivo_margem(
                bool(r["margem_baixa"]),
                bool(r["saldo_divergente"]),
                bool(r["saldo_pendente"]),
            ),
            margem=None if r["margem"] is None else float(r["margem"]),
            minima=None if r["minima"] is None else float(r["minima"]),
            lucro=None if r["lucro"] is None else float(r["lucro"]),
        )
        for r in rows
    ]


_CABECALHOS = {
    "logistica": "DaVinci — Logística: pedidos acompanhados",
    "controle_estoque": (
        "DaVinci — Controle de Estoque: Aguardando Cancelamento por falta de estoque"
    ),
    "margem": "DaVinci — Margem: pendente de análise",
}
_VAZIO = {
    "logistica": "DaVinci — Logística: nenhum pedido acompanhado no momento.",
    "controle_estoque": (
        "DaVinci — Controle de Estoque: nenhum pedido em Aguardando Cancelamento"
        " por falta de estoque no momento."
    ),
    "margem": "DaVinci — Margem: nenhum pedido pendente de análise no momento.",
}

_LINHAS = {
    "logistica": _linhas_logistica,
    "controle_estoque": _linhas_estoque,
}


async def _montar_envio(contexto: str, session: AsyncSession) -> tuple[int, list[str]]:
    """(nº de pedidos, mensagens prontas) do contexto. Margem manda UMA
    mensagem por pedido; os demais juntam as linhas numa lista fatiada."""
    if contexto == "margem":
        pedidos = await _pedidos_margem(session)
        mensagens = informar.mensagens_margem(
            pedidos,
            _CABECALHOS["margem"],
            # Mesmo link do aviso automático: aprovar direto do celular.
            rodape_pedido=lambda p: f"Aprovar pelo celular: {aprovar_link.url_aprovar(p)}",
        )
        return len(pedidos), mensagens
    linhas = await _LINHAS[contexto](session)
    return len(linhas), informar.montar_mensagens(
        f"{_CABECALHOS[contexto]} ({len(linhas)})", linhas
    )


@router.post("/{contexto}/enviar", response_model=InformarEnviarOut)
async def enviar(
    contexto: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_active_user)],
) -> InformarEnviarOut:
    """Monta o relatório do contexto e manda pros destinatários cadastrados."""
    if contexto not in _CONTEXTOS_ENVIO:  # margem_auto: só o robô envia
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "contexto_desconhecido"}
        )
    _exige_acesso(user, contexto)
    row = await _config_row(session, contexto)
    recipients = threema.parse_recipients(row.recipients if row else "")
    if not recipients:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "sem_destinatarios"},
        )
    total, mensagens = await _montar_envio(contexto, session)
    mensagens = mensagens or [_VAZIO[contexto]]
    client = threema.ThreemaClient()
    sent_ok: set[str] = set()
    failed: set[str] = set()
    try:
        for msg in mensagens:
            result = await client.send_to_all(msg, recipients)
            sent_ok.update(result.get("sent", []))
            failed.update(result.get("failed", []))
    except threema.ThreemaConfigError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "threema_nao_configurado"},
        ) from exc
    logger.info(
        "informar_enviado",
        contexto=contexto,
        pedidos=total,
        mensagens=len(mensagens),
        sent=sorted(sent_ok - failed),
        failed=sorted(failed),
    )
    return InformarEnviarOut(
        pedidos=total,
        mensagens=len(mensagens),
        sent=sorted(sent_ok - failed),
        failed=sorted(failed),
    )
