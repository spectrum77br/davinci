"""Pós-venda › Atendimento › Automáticas: as mensagens automáticas por loja (05/10/2026).

O motor recria no DaVinci as automações do Duoke, começando em MODO SECO
(`services/atendimento/automacoes.py`; desenho em docs/atendimento-automacoes.md).
Esta é a aba "Automáticas" do /atendimento:

  GET   /api/atendimento/automacoes?plataforma=&dias=7
        O catálogo × as lojas da plataforma: a regra de cada loja (ou o
        padrão do catálogo, se não houver linha), o canal e o acesso, as
        contagens de 24 h e do período (simulado, enviado, pulado por motivo,
        só Duoke), a precisão, a cobertura e a % que bateu, o critério da
        troca DA LOJA (sempre em 7 dias: a troca é loja por loja; o total da
        automação é só informação) — que também pede 7 dias de DADOS da regra
        na loja (`dados_desde`, `faltam_h`: a tela mostra "faltam N dias") —
        e o estado das chaves do `.env`.
  GET   /api/atendimento/automacoes/registro?automacao=&integration_id=&estado=
        &duoke=&so=&limite=&antes=
        As linhas do registro, as mais novas primeiro. SEM TEXTO NENHUM —
        nem do comprador, nem o renderizado (o renderizado leva o usuário
        dele): loja, automação, alvo, horários, estado, motivo e o Duoke.
  GET   /api/atendimento/automacoes/estatisticas?dias=7&automacao=&integration_id=
        A conta por automação × loja e o total da automação.
  PATCH /api/atendimento/automacoes/{automacao}/{integration_id}
        Muda o modo, o texto (partes), o atraso, o horário, as condições e o
        teto da regra da loja (cria a regra se não houver). `enviar` é
        RECUSADO (409) enquanto `ATENDIMENTO_AUTOMACOES_ENVIO` estiver
        desligada, sem o envio geral, na campanha da Shopee sem a resposta
        automática (a chave E o envio por ela no adaptador), na loja sem
        acesso e na regra sem texto — e SEMPRE na automação que só simula
        (`so_simulacao`, também na regra que já estivesse em `enviar`: qualquer
        mudança nela tem de levá-la para simular ou desligado); e pede (422)
        a confirmação "desliguei no Duoke" e, se o critério da troca não
        passou NESTA loja em 7 dias (com os 7 dias de dados da regra nela), a
        confirmação de que a pessoa sabe disso (`troca_sem_criterio`). A chave
        de envio desligada recusa (409) antes de tudo isso. O texto passa no
        validador (422 com os motivos). Sobe a versão, carimba
        `ligada_desde`/`enviar_desde` e, na troca `simular → enviar`, rearma
        as linhas ainda válidas.
  POST  /api/atendimento/automacoes/{automacao}/simular-nas-lojas-do-duoke
        Cria em `simular` a regra ausente e liga a `desligado` nas lojas onde
        o Duoke manda hoje. NUNCA mexe em regra em `simular` ou `enviar`.
  POST  /api/atendimento/automacoes/previa
        Renderiza as partes com o nome de exemplo e devolve os motivos do
        validador. Não envia nada.
  GET   /api/atendimento/automacoes/registro/{registro_id}/previa
        "Como o comprador receberia": as partes EXATAS que sairiam por aquela
        linha (o cartão com o nº do pedido, o texto com o usuário do comprador
        preenchido, a figurinha, a resposta pública), montadas NA HORA — nada é
        gravado, e o registro continua sem texto —, e ao lado as mensagens da
        LOJA que o Duoke mandou de verdade (hora, diferença, se bateu). Nunca o
        texto do comprador (`services/atendimento/automacoes_previa.py`).

A MESMA TRAVA do /atendimento (`_so_admin`: na observação, toda a equipe
lê — e pede a prévia, que só renderiza — e só ATENDIMENTO_USUARIOS muda
regra ou simula nas lojas), a mesma permissão (`_view` para ler, `_edit`
para mudar) e o escopo por equipe. O Histórico registra quem mudou a regra (a
tabela de regras tem o gatilho; o registro não).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.team_scope import TeamScope, resolve_team_scope
from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoAutomacaoRegra,
    AtendimentoCanal,
    Integration,
    User,
)
from app.routers.atendimento import _clausula_escopo, _edit, _no_escopo, _so_admin, _view
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento import automacoes_comparar as comparar
from app.services.atendimento import automacoes_previa, lojas

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)

_R = AtendimentoAutomacaoRegistro
_G = AtendimentoAutomacaoRegra
# O nome de exemplo da prévia e da validação do texto na tela.
NOME_EXEMPLO = "maria.silva"
LIMITE_REGISTRO = 500
# O critério da troca é medido sempre nos últimos 7 dias, por loja (§6.5) — e
# pede 7 dias de dados da regra na loja (`automacoes_comparar.com_os_dias`,
# `automacoes_comparar.CRITERIO_DIAS`).
CRITERIO_DIAS = 7


# ── Chaves e faixa ────────────────────────────────────────────────────────


def chaves() -> dict[str, Any]:
    """O estado das chaves do `.env` que a tela mostra na faixa."""
    from app.services.atendimento import enviar

    s = get_settings()
    return {
        "leitura_ativa": bool(s.atendimento_leitura_ativa),
        "motor_ativo": bool(s.atendimento_leitura_ativa and s.atendimento_automacoes_ativa),
        "envio_automacoes": bool(s.atendimento_automacoes_envio),
        "envio_geral": bool(s.atendimento_envio_ativo),
        "shopee_auto_reply": bool(s.atendimento_automacoes_shopee_auto_reply),
        # O envio por resposta automática existe no adaptador da Shopee? Sem
        # ele, a campanha não sai, com a chave ligada ou não.
        "shopee_auto_reply_adaptador": enviar.auto_reply_no_adaptador("shopee"),
        "shopee_mensagens_comprador": bool(s.shopee_mensagens_comprador),
        "teto_dia": int(s.atendimento_automacoes_teto_dia or 0),
    }


def _faixa(ch: dict[str, Any], regras_em_enviar: int) -> dict[str, str]:
    """O aviso do topo da aba. `erro` = regra em ENVIAR com o envio desligado."""
    if regras_em_enviar and not (ch["envio_automacoes"] and ch["envio_geral"]):
        return {
            "nivel": "erro",
            "texto": (
                "Regra em ENVIAR com a chave de envio desligada: nada sai, e se o Duoke já "
                "foi desligado nessa loja o comprador não recebe."
            ),
        }
    if not ch["motor_ativo"]:
        return {
            "nivel": "aviso",
            "texto": "Motor desligado (ATENDIMENTO_AUTOMACOES_ATIVA): nada é simulado nem enviado.",
        }
    if not ch["envio_automacoes"]:
        return {
            "nivel": "info",
            "texto": (
                "Modo seco: o DaVinci registra o que mandaria e compara com o Duoke. Nada é "
                "enviado (ATENDIMENTO_AUTOMACOES_ENVIO desligada)."
            ),
        }
    return {
        "nivel": "aviso",
        "texto": (
            "Envio ligado: as regras em ENVIAR mandam de verdade, mesmo com a loja em "
            "observar. O freio geral é o ATENDIMENTO_ENVIO_ATIVO."
        ),
    }


# ── Saída ─────────────────────────────────────────────────────────────────


def _hhmm(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


def _regra_out(
    aut: cat.Automacao, regra: AtendimentoAutomacaoRegra | None, nome_integracao: str | None
) -> dict[str, Any]:
    if regra is None:
        padrao = cat.regra_semente(aut, nome_integracao)
        return {
            "id": None,
            "modo": cat.MODO_DESLIGADO,
            "padrao": True,
            "partes": padrao["partes"],
            "atraso_min": padrao["atraso_min"],
            "janela_inicio": _hhmm(padrao["janela_inicio"]),
            "janela_fim": _hhmm(padrao["janela_fim"]),
            "condicoes": padrao["condicoes"],
            "teto_dia": None,
            "versao": 0,
            "ligada_desde": None,
            "enviar_desde": None,
            "disjuntor_em": None,
            "disjuntor_motivo": None,
            "atualizado_em": None,
        }
    return {
        "id": regra.id,
        "modo": regra.modo,
        "padrao": False,
        "partes": list(regra.partes or cat.partes_padrao(aut, nome_integracao)),
        "atraso_min": regra.atraso_min,
        "janela_inicio": _hhmm(regra.janela_inicio),
        "janela_fim": _hhmm(regra.janela_fim),
        "condicoes": {**aut.condicoes, **(regra.condicoes or {})},
        "teto_dia": regra.teto_dia,
        "versao": regra.versao,
        "ligada_desde": regra.ligada_desde,
        "enviar_desde": regra.enviar_desde,
        "disjuntor_em": regra.disjuntor_em,
        "disjuntor_motivo": regra.disjuntor_motivo,
        "atualizado_em": regra.updated_at,
    }


def _por_que_nao_enviar(
    aut: cat.Automacao, canal: AtendimentoCanal | None, ch: dict[str, Any]
) -> list[str]:
    """Os códigos que impedem pôr a regra em `enviar` agora (vazio = pode)."""
    motivos = []
    if aut.so_simular:
        # O pedido não pago com cupom, a resposta da avaliação e o "pedido
        # recebido" do TikTok: só mostram o que mandariam (05/10/2026).
        motivos.append("so_simulacao")
    if aut.travada:
        motivos.append("sem_texto")
    if not ch["envio_automacoes"]:
        motivos.append("envio_desligado")
    if not ch["envio_geral"]:
        motivos.append("envio_geral_desligado")
    if aut.campanha and not (ch["shopee_auto_reply"] and ch["shopee_auto_reply_adaptador"]):
        motivos.append("campanha_sem_auto_reply")
    if aut.plataforma == "shopee" and not ch["shopee_mensagens_comprador"]:
        motivos.append("shopee_mensagens_desligadas")
    if canal is None or canal.status in ("sem_escopo", "desligado"):
        motivos.append("loja_sem_acesso")
    return motivos


def _aut_out(aut: cat.Automacao) -> dict[str, Any]:
    inicio, fim = aut.janela if aut.janela else (None, None)
    return {
        "codigo": aut.codigo,
        "plataforma": aut.plataforma,
        "canal": aut.canal,
        "nome": aut.nome,
        "descricao": aut.descricao,
        "tipo": aut.tipo,
        "gatilho": aut.gatilho,
        "alvo": aut.alvo,
        "familia": aut.familia,
        "campanha": aut.campanha,
        "travada": aut.travada,
        "diferenca_combinada": aut.diferenca_combinada,
        "atraso_min": aut.atraso_min,
        "validade_min": int(aut.validade.total_seconds() // 60),
        "janela_inicio": _hhmm(inicio),
        "janela_fim": _hhmm(fim),
        "condicoes_padrao": dict(aut.condicoes),
        "placeholders": cat.placeholders_de(aut),
        "seguinte": aut.seguinte,
        # Só simula (o porquê, em português): a tela trava o Enviar com ele.
        "so_simulacao": aut.so_simular,
        "so_simulacao_texto": cat.SO_SIMULAR.get(aut.so_simular or ""),
    }


async def _lojas(
    session: AsyncSession, scope: TeamScope, plataforma: str | None
) -> list[tuple[Integration, AtendimentoCanal | None]]:
    """As lojas Shopee/TikTok/ML ativas no escopo, com o canal de cada uma."""
    consulta = select(Integration).where(Integration.archived_at.is_(None))
    cond = _clausula_escopo(scope, Integration.id)
    if cond is not None:
        consulta = consulta.where(cond)
    integracoes = [
        i
        for i in (await session.execute(consulta.order_by(Integration.name))).scalars().all()
        if cat.plataforma_da_integracao(i.platform) in ("shopee", "tiktok", "ml")
        and (plataforma is None or cat.plataforma_da_integracao(i.platform) == plataforma)
    ]
    if not integracoes:
        return []
    canais = (
        (
            await session.execute(
                select(AtendimentoCanal).where(
                    AtendimentoCanal.integration_id.in_([i.id for i in integracoes])
                )
            )
        )
        .scalars()
        .all()
    )
    por_integ: dict[tuple[UUID, str], AtendimentoCanal] = {
        (c.integration_id, c.canal): c for c in canais
    }
    saida = []
    for i in integracoes:
        plat = cat.plataforma_da_integracao(i.platform)
        canal = por_integ.get((i.id, "pos_venda" if plat == "ml" else "chat"))
        saida.append((i, canal))
    return saida


def _ultimo(linhas: list[tuple]) -> dict[tuple[str, UUID], dict[str, Any]]:
    return {
        (codigo, integ): {"devido_em": devido, "estado": estado, "motivo": motivo}
        for codigo, integ, devido, estado, motivo in linhas
    }


@router.get("/automacoes")
async def listar_automacoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    plataforma: Annotated[str | None, Query(pattern="^(shopee|tiktok|ml)$")] = None,
    dias: Annotated[int, Query(ge=1, le=60)] = 7,
) -> dict[str, Any]:
    """O catálogo × as lojas, com a regra, as contagens e o critério da troca."""
    agora = datetime.now(UTC)
    scope = await resolve_team_scope(session, user)
    lojas_canais = await _lojas(session, scope, plataforma)
    ids = [i.id for i, _ in lojas_canais]
    regras = (
        {
            (r.automacao, r.integration_id): r
            for r in (await session.execute(select(_G).where(_G.integration_id.in_(ids))))
            .scalars()
            .all()
        }
        if ids
        else {}
    )
    # O critério também exige os 7 dias de DADOS de cada regra/loja (`com_os_dias`).
    inicios = await comparar.inicio_dos_dados(session)
    e24 = await comparar.estatisticas(
        session, desde=agora - timedelta(hours=24), ate=agora, inicios=inicios
    )
    eper = await comparar.estatisticas(
        session, desde=agora - timedelta(days=dias), ate=agora, inicios=inicios
    )
    # O critério da troca é da LOJA, sempre em 7 dias (o período da tela muda a conta, não ele).
    e7 = (
        eper
        if dias == CRITERIO_DIAS
        else await comparar.estatisticas(
            session, desde=agora - timedelta(days=CRITERIO_DIAS), ate=agora, inicios=inicios
        )
    )
    ultimos = (
        (
            await session.execute(
                select(_R.automacao, _R.integration_id, _R.devido_em, _R.estado, _R.motivo)
                .where(_R.integration_id.in_(ids), _R.devido_em <= agora)
                .distinct(_R.automacao, _R.integration_id)
                .order_by(_R.automacao, _R.integration_id, _R.devido_em.desc())
            )
        ).all()
        if ids
        else []
    )
    ultimo = _ultimo(ultimos)
    ch = chaves()
    nomes = {i.id: await lojas.nome_da_loja(session, i) for i, _ in lojas_canais}
    saida = []
    em_enviar = 0
    for aut in cat.CATALOGO.values():
        if plataforma and aut.plataforma != plataforma:
            continue
        linhas_lojas = []
        h24_lista, per_lista = [], []
        for integ, canal in lojas_canais:
            if cat.plataforma_da_integracao(integ.platform) != aut.plataforma:
                continue
            regra = regras.get((aut.codigo, integ.id))
            em_enviar += int(regra is not None and regra.modo == cat.MODO_ENVIAR)
            h24 = e24.get((aut.codigo, integ.id))
            per = eper.get((aut.codigo, integ.id))
            if h24:
                h24_lista.append(h24)
            if per:
                per_lista.append(per)
            motivos = _por_que_nao_enviar(aut, canal, ch)
            criterio = e7.get((aut.codigo, integ.id)) or comparar.com_os_dias(
                comparar.resumir({}, aut), desde=inicios.get((aut.codigo, integ.id)), agora=agora
            )
            linhas_lojas.append(
                {
                    "integration_id": integ.id,
                    "loja": nomes.get(integ.id) or integ.name,
                    "integracao": integ.name,
                    "canal_status": canal.status if canal else None,
                    "canal_modo": canal.modo if canal else None,
                    "sem_acesso": "loja_sem_acesso" in motivos,
                    "duoke_hoje": cat.nome_normalizado(integ.name) in aut.lojas_duoke,
                    "regra": _regra_out(aut, regra, integ.name),
                    "h24": comparar.sem_internos(h24),
                    "periodo": comparar.sem_internos(per),
                    "ultimo": ultimo.get((aut.codigo, integ.id)),
                    "pode_enviar": not motivos,
                    "por_que_nao_enviar": motivos,
                    "pode_trocar": bool(criterio["pode_trocar"]),
                    "por_que_nao_trocar": list(criterio["por_que_nao"]),
                    # Os 7 dias de dados da regra nesta loja ("faltam N dias" na tela).
                    "dados_desde": criterio["dados_desde"],
                    "dias_de_dados": criterio["dias_de_dados"],
                    "completa_em": criterio["completa_em"],
                    "faltam_h": criterio["faltam_h"],
                }
            )
        saida.append(
            {
                **_aut_out(aut),
                "total_24h": comparar.somar(h24_lista, aut) if h24_lista else None,
                "total_periodo": comparar.somar(per_lista, aut) if per_lista else None,
                "lojas": linhas_lojas,
            }
        )
    return {
        "chaves": ch,
        "faixa": _faixa(ch, em_enviar),
        "dias": dias,
        "motivos": cat.MOTIVOS,
        "divergencias": cat.DIVERGENCIAS,
        "alertas": cat.ALERTAS,
        "automacoes": saida,
    }


# ── Registro ──────────────────────────────────────────────────────────────


def _linha_out(x: AtendimentoAutomacaoRegistro, nomes: dict[UUID, str]) -> dict[str, Any]:
    aut = cat.CATALOGO.get(x.automacao)
    return {
        "id": x.id,
        "automacao": x.automacao,
        "automacao_nome": aut.nome if aut else x.automacao,
        "plataforma": x.plataforma,
        "integration_id": x.integration_id,
        "loja": nomes.get(x.integration_id),
        "alvo": x.alvo,
        "pedido": x.pedido,
        "conversa_id": x.conversa_id,
        "evento_em": x.evento_em,
        "visto_em": x.visto_em,
        "devido_em": x.devido_em,
        "decidido_em": x.decidido_em,
        "estado": x.estado,
        "modo": x.modo,
        "motivo": x.motivo,
        "motivo_texto": cat.MOTIVOS.get(x.motivo or "") if x.motivo else None,
        "erro": x.erro,
        "duoke": x.duoke,
        "duoke_em": x.duoke_em,
        "duoke_diferenca_s": x.duoke_diferenca_s,
        "divergencia": x.divergencia,
        "divergencia_texto": cat.DIVERGENCIAS.get(x.divergencia or "") if x.divergencia else None,
        "alerta": x.alerta,
        "alerta_texto": cat.ALERTAS.get(x.alerta or "") if x.alerta else None,
        "tentativas": x.tentativas,
        "regra_versao": x.regra_versao,
    }


_FILTROS_SO = {
    "so_davinci": lambda: and_(
        _R.estado.in_((cat.ESTADO_SIMULADO, cat.ESTADO_ENVIADO)),
        _R.duoke == cat.DUOKE_NAO_MANDOU,
        _R.divergencia.is_(None),
    ),
    "so_duoke": lambda: and_(
        or_(
            and_(_R.estado == cat.ESTADO_PULADO, _R.duoke == cat.DUOKE_MANDOU),
            _R.estado == cat.ESTADO_SO_DUOKE,
        ),
        _R.divergencia.is_(None),
    ),
    "bateu": lambda: and_(
        or_(
            and_(
                _R.estado.in_((cat.ESTADO_SIMULADO, cat.ESTADO_ENVIADO)),
                _R.duoke == cat.DUOKE_MANDOU,
            ),
            and_(_R.estado == cat.ESTADO_PULADO, _R.duoke == cat.DUOKE_NAO_MANDOU),
        ),
        _R.divergencia.is_(None),
    ),
    "alerta": lambda: _R.alerta.is_not(None),
    "combinada": lambda: _R.divergencia.is_not(None),
}


@router.get("/automacoes/registro")
async def registro(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    automacao: str | None = None,
    integration_id: UUID | None = None,
    estado: Annotated[str | None, Query(pattern="^[a-z_]+$")] = None,
    duoke: Annotated[str | None, Query(pattern="^[a-z_]+$")] = None,
    so: Annotated[
        str | None, Query(pattern="^(so_davinci|so_duoke|bateu|alerta|combinada)$")
    ] = None,
    limite: Annotated[int, Query(ge=1, le=LIMITE_REGISTRO)] = 200,
    antes: datetime | None = None,
) -> dict[str, Any]:
    """O registro recente, sem texto nenhum (as mais novas primeiro; `antes` pagina)."""
    scope = await resolve_team_scope(session, user)
    consulta = select(_R)
    cond = _clausula_escopo(scope, _R.integration_id)
    if cond is not None:
        consulta = consulta.where(cond)
    if automacao:
        consulta = consulta.where(_R.automacao == automacao)
    if integration_id:
        consulta = consulta.where(_R.integration_id == integration_id)
    if estado:
        consulta = consulta.where(_R.estado == estado)
    if duoke:
        consulta = consulta.where(_R.duoke == duoke)
    if so:
        consulta = consulta.where(_FILTROS_SO[so]())
    if antes is not None:
        consulta = consulta.where(_R.devido_em < antes)
    linhas = (
        (await session.execute(consulta.order_by(_R.devido_em.desc()).limit(limite + 1)))
        .scalars()
        .all()
    )
    mais = len(linhas) > limite
    linhas = linhas[:limite]
    integs = {x.integration_id for x in linhas}
    nomes: dict[UUID, str] = {}
    for integ in (
        (await session.execute(select(Integration).where(Integration.id.in_(integs))))
        .scalars()
        .all()
        if integs
        else []
    ):
        nomes[integ.id] = await lojas.nome_da_loja(session, integ)
    return {
        "linhas": [_linha_out(x, nomes) for x in linhas],
        "proximo": linhas[-1].devido_em if mais and linhas else None,
    }


@router.get("/automacoes/estatisticas")
async def estatisticas(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    dias: Annotated[int, Query(ge=1, le=60)] = 7,
    automacao: str | None = None,
    integration_id: UUID | None = None,
) -> dict[str, Any]:
    """A conta por automação × loja (precisão, cobertura, % que bateu) e o total da automação."""
    agora = datetime.now(UTC)
    desde = agora - timedelta(days=dias)
    scope = await resolve_team_scope(session, user)
    inicios = await comparar.inicio_dos_dados(
        session, automacao=automacao, integration_id=integration_id
    )
    por_par = await comparar.estatisticas(
        session,
        desde=desde,
        ate=agora,
        automacao=automacao,
        integration_id=integration_id,
        inicios=inicios,
    )
    por_par = {k: v for k, v in por_par.items() if _no_escopo(scope, k[1])}
    integs = {k[1] for k in por_par}
    nomes: dict[UUID, str] = {}
    for integ in (
        (await session.execute(select(Integration).where(Integration.id.in_(integs))))
        .scalars()
        .all()
        if integs
        else []
    ):
        nomes[integ.id] = await lojas.nome_da_loja(session, integ)
    linhas = [
        {
            "automacao": codigo,
            "integration_id": integ,
            "loja": nomes.get(integ),
            **comparar.sem_internos(conta),
        }
        for (codigo, integ), conta in sorted(
            por_par.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))
        )
    ]
    por_aut: dict[str, list[dict]] = {}
    for (codigo, _), conta in por_par.items():
        por_aut.setdefault(codigo, []).append(conta)
    return {
        "desde": desde,
        "ate": agora,
        "linhas": linhas,
        "por_automacao": [
            {"automacao": codigo, **comparar.somar(lista, cat.CATALOGO.get(codigo))}
            for codigo, lista in sorted(por_aut.items())
        ],
    }


# ── Mudar a regra ─────────────────────────────────────────────────────────


class ParteIn(BaseModel):
    tipo: str = Field(pattern="^(texto|cartao_pedido|figurinha|resposta_publica)$")
    texto: str | None = Field(default=None, max_length=2000)
    figurinha: str | None = Field(default=None, max_length=16)
    pacote: str | None = Field(default=None, max_length=64)


class RegraIn(BaseModel):
    modo: str | None = Field(default=None, pattern="^(desligado|simular|enviar)$")
    partes: list[ParteIn] | None = Field(default=None, max_length=5)
    atraso_min: int | None = Field(default=None, ge=0, le=10080)
    # "HH:MM"; os dois vazios = 24 h. Mandar só um é recusado.
    janela_inicio: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    janela_fim: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    sem_janela: bool = False
    condicoes: dict[str, Any] | None = None
    teto_dia: int | None = Field(default=None, ge=0, le=6000)
    sem_teto: bool = False
    # "Desliguei esta automação desta loja no Duoke" — obrigatório para `enviar`.
    desliguei_no_duoke: bool = False
    # "Sei que o critério da troca não passou nesta loja e quero trocar mesmo
    # assim" — obrigatório para `enviar` quando ele não passou (7 dias, §6.5).
    troca_sem_criterio: bool = False


def _partes_limpas(partes: list[ParteIn]) -> list[dict]:
    saida = []
    for p in partes:
        if p.tipo in cat.TIPOS_COM_TEXTO:
            saida.append({"tipo": p.tipo, "texto": (p.texto or "").strip()})
        elif p.tipo == "figurinha":
            saida.append(
                {
                    "tipo": "figurinha",
                    "figurinha": p.figurinha or "0007",
                    "pacote": p.pacote or "br_shoppito",
                }
            )
        else:
            saida.append({"tipo": "cartao_pedido"})
    return saida


def _hora(valor: str) -> time:
    try:
        h, m = (int(x) for x in valor.split(":"))
        return time(h, m)
    except ValueError as e:
        raise HTTPException(422, detail={"code": "janela_invalida"}) from e


async def _integracao_ou_404(
    session: AsyncSession, integration_id: UUID, scope: TeamScope, aut: cat.Automacao
) -> Integration:
    integ = await session.get(Integration, integration_id)
    if (
        integ is None
        or integ.archived_at is not None
        or not _no_escopo(scope, integ.id)
        or cat.plataforma_da_integracao(integ.platform) != aut.plataforma
    ):
        raise HTTPException(404, detail={"code": "loja_nao_encontrada"})
    return integ


def _automacao_ou_404(codigo: str) -> cat.Automacao:
    aut = cat.automacao(codigo)
    if aut is None:
        raise HTTPException(404, detail={"code": "automacao_nao_encontrada"})
    return aut


async def _canal(
    session: AsyncSession, integ: Integration, aut: cat.Automacao
) -> AtendimentoCanal | None:
    return (
        await session.execute(
            select(AtendimentoCanal).where(
                AtendimentoCanal.integration_id == integ.id, AtendimentoCanal.canal == aut.canal
            )
        )
    ).scalar_one_or_none()


async def rearmar(
    session: AsyncSession, regra: AtendimentoAutomacaoRegra, *, agora: datetime
) -> int:
    """`simular → enviar`: as `simulado` ainda válidas que o Duoke NÃO mandou voltam a `agendado`.

    O caso típico é o entregue que o Duoke só mandaria na madrugada: com ele
    desligado, o comprador não fica sem. A decisão no modo enviar confere de
    novo se o Duoke mandou depois do gatilho (na conversa e, nas do pedido,
    pelo cartão ou pela conversa do pedido), com a leitura da loja em dia:
    o que o Duoke já mandou não sai de novo. Só rearma o que ainda pode sair
    dentro da validade — no HORÁRIO da regra (rearmado às 22h, o entregue
    espera as 9h; a decisão também segura).
    """
    aut = cat.CATALOGO[regra.automacao]
    linhas = (
        (
            await session.execute(
                select(_R).where(
                    _R.automacao == regra.automacao,
                    _R.integration_id == regra.integration_id,
                    _R.estado == cat.ESTADO_SIMULADO,
                    _R.duoke.in_((cat.DUOKE_PENDENTE, cat.DUOKE_NAO_MANDOU)),
                    _R.devido_em >= agora - timedelta(days=2),
                )
            )
        )
        .scalars()
        .all()
    )
    janela = cat.janela_da_regra(regra, aut)
    sai = cat.ajustar_janela(agora, *janela) if janela else agora
    n = 0
    for x in linhas:
        if sai <= cat.validade_ate(aut, x.devido_em, janela):
            x.estado = cat.ESTADO_AGENDADO
            x.decidido_em = None
            x.modo = None
            x.motivo = None
            # Compara de novo depois de sair: o Duoke mandando ainda dispara o disjuntor.
            x.duoke = cat.DUOKE_PENDENTE
            x.comparado_em = None
            n += 1
    return n


@router.patch("/automacoes/{automacao}/{integration_id}")
async def mudar_regra(
    automacao: str,
    integration_id: UUID,
    body: RegraIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> dict[str, Any]:
    """Muda a regra da loja (cria se não houver). `enviar` só com tudo pronto (409)."""
    aut = _automacao_ou_404(automacao)
    scope = await resolve_team_scope(session, user)
    integ = await _integracao_ou_404(session, integration_id, scope, aut)
    canal = await _canal(session, integ, aut)
    agora = datetime.now(UTC)
    regra = (
        await session.execute(
            select(_G)
            .where(_G.automacao == aut.codigo, _G.integration_id == integ.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    nova = regra is None
    if nova:
        padrao = cat.regra_semente(aut, integ.name)
        regra = AtendimentoAutomacaoRegra(
            automacao=aut.codigo,
            integration_id=integ.id,
            plataforma=aut.plataforma,
            modo=cat.MODO_DESLIGADO,
            partes=padrao["partes"],
            atraso_min=padrao["atraso_min"],
            janela_inicio=padrao["janela_inicio"],
            janela_fim=padrao["janela_fim"],
            condicoes=padrao["condicoes"],
            versao=1,
        )
    modo_antes = regra.modo
    modo = body.modo or regra.modo
    if modo != cat.MODO_DESLIGADO and aut.travada:
        raise HTTPException(
            422, detail={"code": "sem_texto", "detail": "Esta opção ainda não tem texto."}
        )
    if modo == cat.MODO_ENVIAR and aut.so_simular:
        # A que só simula nunca fica em `enviar` — nem a regra que já estivesse
        # nele (mexida direto no banco): só sai dele (simular ou desligado).
        raise HTTPException(
            409,
            detail={
                "code": "so_simulacao",
                "motivos": ["so_simulacao"],
                "detail": "Esta automação só simula: ponha a regra em simular ou desligado.",
            },
        )
    if modo == cat.MODO_ENVIAR and modo_antes != cat.MODO_ENVIAR:
        motivos = _por_que_nao_enviar(aut, canal, chaves())
        if motivos:
            raise HTTPException(
                409,
                detail={
                    "code": motivos[0],
                    "motivos": motivos,
                    "detail": "Esta regra não pode ir para ENVIAR agora.",
                },
            )
        if not body.desliguei_no_duoke:
            raise HTTPException(
                422,
                detail={
                    "code": "confirmar_duoke",
                    "detail": "Marque que desligou esta automação desta loja no Duoke.",
                },
            )
        # A troca é LOJA por loja: o critério desta loja nos últimos 7 dias,
        # com os 7 dias de dados da regra nela (`com_os_dias`).
        inicios = await comparar.inicio_dos_dados(
            session, automacao=aut.codigo, integration_id=integ.id
        )
        conta = await comparar.estatisticas(
            session,
            desde=agora - timedelta(days=CRITERIO_DIAS),
            ate=agora,
            automacao=aut.codigo,
            integration_id=integ.id,
            inicios=inicios,
        )
        criterio = conta.get((aut.codigo, integ.id)) or comparar.com_os_dias(
            comparar.resumir({}, aut), desde=inicios.get((aut.codigo, integ.id)), agora=agora
        )
        if not criterio["pode_trocar"] and not body.troca_sem_criterio:
            raise HTTPException(
                422,
                detail={
                    "code": "criterio_nao_passou",
                    "motivos": list(criterio["por_que_nao"]),
                    "detail": "O critério da troca não passou nesta loja nos últimos 7 dias.",
                },
            )
    mudou_versao = False
    if body.partes is not None:
        partes = _partes_limpas(body.partes)
        if aut.alvo != cat.ALVO_AVALIACAO and any(
            p["tipo"] == cat.PARTE_RESPOSTA_PUBLICA for p in partes
        ):
            # A resposta PÚBLICA só existe na resposta da avaliação.
            raise HTTPException(422, detail={"code": "parte_invalida"})
        _, motivos = cat.renderizar(
            partes,
            comprador=NOME_EXEMPLO,
            plataforma=aut.plataforma,
            canal=aut.canal,
            valores=cat.valores_de_exemplo(aut),
        )
        if motivos:
            raise HTTPException(422, detail={"code": "texto_invalido", "motivos": motivos})
        if partes != list(regra.partes or []):
            regra.partes = partes
            mudou_versao = True
    if body.condicoes is not None:
        desconhecidas = sorted(set(body.condicoes) - set(aut.condicoes))
        if desconhecidas:
            raise HTTPException(
                422, detail={"code": "condicao_desconhecida", "condicoes": desconhecidas}
            )
        for k, v in body.condicoes.items():
            if not isinstance(v, bool | int | float) or (
                isinstance(aut.condicoes[k], bool) and not isinstance(v, bool)
            ):
                raise HTTPException(422, detail={"code": "condicao_invalida", "condicao": k})
        condicoes = {**(regra.condicoes or {}), **body.condicoes}
        if condicoes != (regra.condicoes or {}):
            regra.condicoes = condicoes
            mudou_versao = True
    if body.atraso_min is not None:
        regra.atraso_min = body.atraso_min
    if body.sem_janela:
        regra.janela_inicio = regra.janela_fim = None
    elif body.janela_inicio is not None or body.janela_fim is not None:
        if body.janela_inicio is None or body.janela_fim is None:
            raise HTTPException(422, detail={"code": "janela_invalida"})
        inicio, fim = _hora(body.janela_inicio), _hora(body.janela_fim)
        if inicio >= fim:
            raise HTTPException(422, detail={"code": "janela_invalida"})
        regra.janela_inicio, regra.janela_fim = inicio, fim
    if body.sem_teto:
        regra.teto_dia = None
    elif body.teto_dia is not None:
        regra.teto_dia = body.teto_dia
    if mudou_versao and not nova:
        regra.versao = (regra.versao or 1) + 1
    rearmadas = 0
    if modo != modo_antes:
        regra.modo = modo
        if modo_antes == cat.MODO_DESLIGADO:
            regra.ligada_desde = agora
        if modo == cat.MODO_DESLIGADO:
            regra.ligada_desde = None
        if modo == cat.MODO_ENVIAR:
            regra.enviar_desde = agora
            regra.disjuntor_em = None
            regra.disjuntor_motivo = None
        else:
            regra.enviar_desde = None
    regra.atualizado_por = user.id
    regra.updated_at = agora
    if nova:
        session.add(regra)
    await session.flush()
    if modo == cat.MODO_ENVIAR and modo_antes == cat.MODO_SIMULAR:
        rearmadas = await rearmar(session, regra, agora=agora)
    await session.commit()
    logger.info(
        "atendimento_automacao_regra_mudou",
        automacao=aut.codigo,
        integration_id=str(integ.id),
        de=modo_antes,
        para=modo,
        versao=regra.versao,
        rearmadas=rearmadas,
        troca_sem_criterio=bool(modo == cat.MODO_ENVIAR and body.troca_sem_criterio),
        user_id=str(user.id),
    )
    return {
        "integration_id": integ.id,
        "automacao": aut.codigo,
        "regra": _regra_out(aut, regra, integ.name),
        "rearmadas": rearmadas,
        "pode_enviar": not _por_que_nao_enviar(aut, canal, chaves()),
        "por_que_nao_enviar": _por_que_nao_enviar(aut, canal, chaves()),
    }


@router.post("/automacoes/{automacao}/simular-nas-lojas-do-duoke")
async def simular_nas_lojas_do_duoke(
    automacao: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> dict[str, Any]:
    """Cria em `simular` a regra ausente e liga a `desligado` nas lojas do Duoke.

    NUNCA mexe em regra que já está em `simular` ou em `enviar`: numa loja já
    trocada (Duoke desligado), rebaixar para `simular` deixaria o comprador
    sem a mensagem.
    """
    aut = _automacao_ou_404(automacao)
    if aut.travada:
        raise HTTPException(422, detail={"code": "sem_texto"})
    scope = await resolve_team_scope(session, user)
    agora = datetime.now(UTC)
    criadas = ligadas = mantidas = 0
    for integ, _canal_loja in await _lojas(session, scope, aut.plataforma):
        if cat.nome_normalizado(integ.name) not in aut.lojas_duoke:
            continue
        regra = (
            await session.execute(
                select(_G)
                .where(_G.automacao == aut.codigo, _G.integration_id == integ.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if regra is None:
            padrao = cat.regra_semente(aut, integ.name)
            session.add(
                AtendimentoAutomacaoRegra(
                    automacao=aut.codigo,
                    integration_id=integ.id,
                    plataforma=aut.plataforma,
                    modo=cat.MODO_SIMULAR,
                    ligada_desde=agora,
                    partes=padrao["partes"],
                    atraso_min=padrao["atraso_min"],
                    janela_inicio=padrao["janela_inicio"],
                    janela_fim=padrao["janela_fim"],
                    condicoes=padrao["condicoes"],
                    atualizado_por=user.id,
                )
            )
            criadas += 1
        elif regra.modo == cat.MODO_DESLIGADO:
            regra.modo = cat.MODO_SIMULAR
            regra.ligada_desde = agora
            regra.atualizado_por = user.id
            ligadas += 1
        else:
            mantidas += 1
    await session.commit()
    logger.info(
        "atendimento_automacao_simular_lojas_duoke",
        automacao=aut.codigo,
        criadas=criadas,
        ligadas=ligadas,
        mantidas=mantidas,
        user_id=str(user.id),
    )
    return {"criadas": criadas, "ligadas": ligadas, "mantidas": mantidas}


class PreviaIn(BaseModel):
    automacao: str
    integration_id: UUID | None = None
    partes: list[ParteIn] | None = Field(default=None, max_length=5)


@router.post("/automacoes/previa")
async def previa(
    body: PreviaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> dict[str, Any]:
    """Renderiza com o nome de exemplo e devolve os motivos do validador. Não envia nada."""
    aut = _automacao_ou_404(body.automacao)
    nome_integ = None
    if body.integration_id is not None:
        scope = await resolve_team_scope(session, user)
        nome_integ = (await _integracao_ou_404(session, body.integration_id, scope, aut)).name
    partes = (
        _partes_limpas(body.partes)
        if body.partes is not None
        else cat.partes_padrao(aut, nome_integ)
    )
    valores = cat.valores_de_exemplo(aut)
    prontas, motivos = cat.renderizar(
        partes,
        comprador=NOME_EXEMPLO,
        plataforma=aut.plataforma,
        canal=aut.canal,
        valores=valores,
    )
    sem_nome, _ = cat.renderizar(
        partes, comprador=None, plataforma=aut.plataforma, canal=aut.canal, valores=valores
    )
    return {
        "partes": prontas,
        "sem_nome": sem_nome,
        "motivos": motivos,
        "comprador_exemplo": NOME_EXEMPLO,
    }


@router.get("/automacoes/registro/{registro_id}/previa")
async def previa_do_registro(
    registro_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> dict[str, Any]:
    """Como o comprador receberia ESTA linha, ao lado do que o Duoke mandou de verdade.

    Monta na hora (nada é gravado; o registro continua sem texto). Fora da
    equipe, ou linha que não existe: 404.
    """
    scope = await resolve_team_scope(session, user)
    x = await session.get(_R, registro_id)
    if x is None or not _no_escopo(scope, x.integration_id):
        raise HTTPException(404, detail={"code": "registro_nao_encontrado"})
    integ = await session.get(Integration, x.integration_id)
    nomes = {x.integration_id: await lojas.nome_da_loja(session, integ)} if integ else {}
    previa_linha = await automacoes_previa.montar(
        session, x, nome_loja=integ.name if integ is not None else None
    )
    return {
        "linha": _linha_out(x, nomes),
        "automacao": previa_linha["automacao"],
        "davinci": previa_linha["davinci"],
        "duoke": previa_linha["duoke"],
    }
