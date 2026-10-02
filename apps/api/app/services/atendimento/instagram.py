"""As DMs do Instagram na caixa de atendimento, SÓ LEITURA.

O Instagram já tem robô e tabelas próprias (`dm_conversas`/`dm_mensagens`,
app/services/instagram_dm.py). Ele NÃO é copiado para `atendimento_*` — se
fosse, dois robôs disputariam a mesma conversa, e o de marketplace (que não
conhece a janela de 24 h da Meta nem a palavra ATENDENTE) responderia DM.
Aqui só se LÊ aquelas tabelas e se apresenta no formato da tela:

  - id textual `ig:<uuid>` — a tela e o router distinguem sem consultar;
  - `somente_leitura=True` — o envio recusa com `somente_leitura`, e a
    pessoa responde pela caixa de entrada do Instagram (onde o robô vê o
    eco e se cala: `auto=False`);
  - direção `recebida` → autor cliente; `enviada` (o robô) e `eco` (alguém
    pela caixa do Instagram) → autor loja.

O "prazo" do Instagram é a janela de 24 h da Meta: passou dela, não se
responde mais por mensagem comum.

Por CONTA (02/10/2026): a barra de lojas mostra uma linha por conta (a
7buyers, a Charlots, a Uranyx — `dm_conversas.rede_social_id`), com a
contagem de cada uma (`contar_por_conta`), e a lista filtra por ela
(`listar_conversas(rede_social_id=...)`). A conta vai sempre com o @
(`arroba`: "@charlots_br", o mesmo nome da linha da barra e da caixa de
comentários da conta). A conversa leva a etiqueta MÍDIA (RF7: "toda
mensagem privada" é Mídia) — só para a tela; o DM não tem etiqueta gravada
nem troca à mão.

Só conta como mensagem da conversa o que existe na plataforma: a entrada, o
eco e a resposta do robô que saiu (ou pode ter saído). `seco` (o que o robô
TERIA dito), `descartada`, `pendente` e `falhou` não aparecem na lista; no
detalhe aparecem as que estão em voo ou falharam, com o estado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DmConversa, DmMensagem, RedeSocial, User
from app.models.instagram_dm import (
    CONVERSA_SILENCIADA,
    DIRECAO_ECO,
    DIRECAO_ENVIADA,
    DIRECAO_RECEBIDA,
    MSG_DESCARTADA,
    MSG_ENVIADA,
    MSG_REVISAR,
    MSG_SECO,
)
from app.services.atendimento import gravar
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CONVERSA_ABERTA,
    CONVERSA_FECHADA,
    CONVERSA_RESPONDIDA,
    ETIQUETA_MIDIA,
    MODO_OBSERVAR,
    ORIGEM_CLIENTE,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
)

PREFIXO_ID = "ig:"
PLATAFORMA = "instagram"
CANAL = "dm"
# A janela da Meta: resposta comum só até 24 h depois da última do cliente.
JANELA = timedelta(hours=24)
VENCENDO = timedelta(hours=2)
LIMITE_CARACTERES = 1000
# Detalhe: as últimas N mensagens (conversa de DM raramente passa disso).
MAX_MENSAGENS_DETALHE = 300
# A linha da barra das DMs cuja conta saiu do cadastro (`rede_social_id`
# vira NULL — SET NULL): ficam juntas, sem filtro próprio.
NOME_SEM_CADASTRO = "Direct (conta fora do cadastro)"

# Linha que EXISTE no Instagram: a entrada, o eco, e a resposta do robô que
# saiu (ou pode ter saído — `revisar`).
_VISIVEL = or_(
    DmMensagem.direcao.in_((DIRECAO_RECEBIDA, DIRECAO_ECO)),
    and_(
        DmMensagem.direcao == DIRECAO_ENVIADA,
        DmMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR)),
    ),
)
_MOMENTO = func.coalesce(DmMensagem.ocorrido_em, DmMensagem.created_at)


def id_textual(conversa_id: UUID) -> str:
    return f"{PREFIXO_ID}{conversa_id}"


def e_instagram(conversa_id: str) -> bool:
    return str(conversa_id).startswith(PREFIXO_ID)


def uuid_do_id(conversa_id: str) -> UUID | None:
    """`ig:<uuid>` → UUID; formato estranho → None (o router devolve 404)."""
    if not e_instagram(conversa_id):
        return None
    try:
        return UUID(str(conversa_id)[len(PREFIXO_ID) :])
    except ValueError:
        return None


def arroba(conta: str | None) -> str | None:
    """'charlots_br' → '@charlots_br': o @ da conta, como a barra e a lista mostram."""
    nome = (conta or "").strip()
    if not nome:
        return None
    return nome if nome.startswith("@") else f"@{nome}"


def _like(texto: str) -> str:
    """Termo do ILIKE com `%`, `_` e `\\` escapados (escape="\\")."""
    return "%" + texto.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _nome(user: User | None) -> str | None:
    if user is None:
        return None
    return (user.name or user.email or "").strip() or None


def _ultimas():
    """Por conversa: a última mensagem visível, a do cliente e a da loja."""
    return (
        select(
            DmMensagem.conversa_id.label("cid"),
            func.max(_MOMENTO).label("em"),
            func.max(case((DmMensagem.direcao == DIRECAO_RECEBIDA, _MOMENTO))).label(
                "do_cliente"
            ),
            func.max(case((DmMensagem.direcao != DIRECAO_RECEBIDA, _MOMENTO))).label(
                "da_loja"
            ),
        )
        .where(_VISIVEL)
        .group_by(DmMensagem.conversa_id)
        .subquery()
    )


def _aguardando(ult):
    return and_(
        ult.c.do_cliente.is_not(None),
        or_(ult.c.da_loja.is_(None), ult.c.do_cliente > ult.c.da_loja),
        DmConversa.status != CONVERSA_SILENCIADA,
    )


def _filtro(ult, filtro: str | None, *, agora: datetime, user_id: UUID | None):
    """O filtro rápido da tela, na linguagem da DM. None = não há o que mostrar."""
    aguardando = _aguardando(ult)
    # prazo = do_cliente + 24 h
    if filtro in (None, "", "todas"):
        return DmConversa.status != CONVERSA_SILENCIADA
    if filtro == "aguardando":
        return aguardando
    if filtro == "vencendo":
        return and_(
            aguardando,
            ult.c.do_cliente >= agora - JANELA,
            ult.c.do_cliente < agora - JANELA + VENCENDO,
        )
    if filtro == "vencidas":
        return and_(aguardando, ult.c.do_cliente < agora - JANELA)
    if filtro == "minhas":
        return DmConversa.assumido_por == user_id if user_id else None
    if filtro == "fechadas":
        return DmConversa.status == CONVERSA_SILENCIADA
    # com_rascunho (a IA do atendimento não escreve DM) ou filtro desconhecido.
    return None


def _resumo(
    conversa: DmConversa,
    *,
    em: datetime | None,
    do_cliente: datetime | None,
    da_loja: datetime | None,
    ultima: DmMensagem | None,
    atribuido_nome: str | None,
) -> dict:
    do_cliente, da_loja = _utc(do_cliente), _utc(da_loja)
    silenciada = conversa.status == CONVERSA_SILENCIADA
    aguardando = (
        do_cliente is not None
        and (da_loja is None or do_cliente > da_loja)
        and not silenciada
    )
    if silenciada:
        situacao = CONVERSA_FECHADA
    else:
        situacao = CONVERSA_ABERTA if aguardando else CONVERSA_RESPONDIDA
    return {
        "id": id_textual(conversa.id),
        "plataforma": PLATAFORMA,
        "canal": CANAL,
        "conta": arroba(conversa.conta),
        "integration_id": None,
        "comprador_nome": conversa.participante_nome,
        # A Graph API não dá a foto do participante na DM: iniciais na tela.
        "comprador_avatar": None,
        "pedido_marketplace": None,
        "anuncio_titulo": None,
        "ultima_mensagem_em": _utc(em),
        "ultima_mensagem_resumo": (
            (gravar.resumo(ultima.texto) or f"[{ultima.tipo or 'outro'}]") if ultima else None
        ),
        "ultima_mensagem_tipo": gravar.tipo_previa(_tipo(ultima)) if ultima else None,
        "ultima_autor": (
            (AUTOR_CLIENTE if ultima.direcao == DIRECAO_RECEBIDA else AUTOR_LOJA)
            if ultima
            else None
        ),
        "aguardando_resposta": aguardando,
        "prazo_resposta_em": do_cliente + JANELA if aguardando else None,
        "situacao": situacao,
        "nao_lidas": 0,
        "tem_rascunho": False,
        "atribuido_a": conversa.assumido_por,
        "atribuido_a_nome": atribuido_nome,
        # O robô do Instagram desligado nesta conversa (alguém assumiu).
        "ia_pausada": not conversa.auto,
        "sem_resposta_necessaria": False,
        "somente_leitura": True,
        # A conta (a linha da barra de lojas) e a etiqueta MÍDIA (RF7).
        "rede_social_id": conversa.rede_social_id,
        "etiqueta": ETIQUETA_MIDIA,
    }


async def _ultima_por_conversa(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, DmMensagem]:
    if not ids:
        return {}
    linhas = (
        (
            await session.execute(
                select(DmMensagem)
                .where(DmMensagem.conversa_id.in_(ids), _VISIVEL)
                .distinct(DmMensagem.conversa_id)
                .order_by(DmMensagem.conversa_id, _MOMENTO.desc(), DmMensagem.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {m.conversa_id: m for m in linhas}


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str | None]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {
        u.id: _nome(u)
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()
    }


async def listar_conversas(
    session: AsyncSession,
    *,
    antes_de: datetime | None = None,
    limite: int = 50,
    q: str | None = None,
    filtro: str | None = None,
    user_id: UUID | None = None,
    rede_social_id: UUID | None = None,
) -> list[dict]:
    """Conversas do Instagram no formato ConversaResumo, mais novas primeiro.

    Mesma paginação da lista do marketplace (`ultima_mensagem_em < antes_de`),
    para o router intercalar as duas fontes numa página só. `rede_social_id`
    = só as da conta (a linha da barra de lojas).
    """
    agora = datetime.now(UTC)
    ult = _ultimas()
    cond = _filtro(ult, filtro, agora=agora, user_id=user_id)
    if cond is None:
        return []
    consulta = (
        select(DmConversa, ult.c.em, ult.c.do_cliente, ult.c.da_loja)
        .join(ult, ult.c.cid == DmConversa.id)
        .where(DmConversa.plataforma == PLATAFORMA, cond)
    )
    if rede_social_id is not None:
        consulta = consulta.where(DmConversa.rede_social_id == rede_social_id)
    if antes_de is not None:
        consulta = consulta.where(ult.c.em < antes_de)
    if q and q.strip():
        # A conta aparece com o @ na tela ("@charlots_br") e fica gravada sem
        # ele: buscar "@charlots" também acha.
        consulta = consulta.where(
            or_(
                DmConversa.participante_nome.ilike(_like(q.strip()), escape="\\"),
                DmConversa.conta.ilike(_like(q.strip().lstrip("@") or q.strip()), escape="\\"),
            )
        )
    linhas = (
        await session.execute(consulta.order_by(ult.c.em.desc()).limit(max(1, limite)))
    ).all()
    ultimas = await _ultima_por_conversa(session, [c.id for c, *_ in linhas])
    nomes = await _nomes(session, {c.assumido_por for c, *_ in linhas})
    return [
        _resumo(
            c,
            em=em,
            do_cliente=do_cliente,
            da_loja=da_loja,
            ultima=ultimas.get(c.id),
            atribuido_nome=nomes.get(c.assumido_por),
        )
        for c, em, do_cliente, da_loja in linhas
    ]


def _contagens(ult, agora: datetime) -> tuple:
    """As colunas do /resumo: aguardando, vencendo, vencidas, total e abertas."""
    aguardando = _aguardando(ult)
    return (
        func.count().filter(aguardando),
        func.count().filter(
            aguardando,
            ult.c.do_cliente >= agora - JANELA,
            ult.c.do_cliente < agora - JANELA + VENCENDO,
        ),
        func.count().filter(aguardando, ult.c.do_cliente < agora - JANELA),
        func.count(),
        # Não silenciadas: as que contam na etiqueta MÍDIA (como a conversa
        # não fechada da caixa).
        func.count().filter(DmConversa.status != CONVERSA_SILENCIADA),
    )


def _numeros(linha) -> dict[str, int]:
    return {
        "aguardando": int(linha[0] or 0),
        "vencendo": int(linha[1] or 0),
        "vencidas": int(linha[2] or 0),
        "total": int(linha[3] or 0),
        "abertas": int(linha[4] or 0),
    }


async def contar(session: AsyncSession) -> dict[str, int]:
    """aguardando / vencendo / vencidas / total / abertas do Instagram, para o /resumo."""
    agora = datetime.now(UTC)
    ult = _ultimas()
    linha = (
        await session.execute(
            select(*_contagens(ult, agora))
            .select_from(DmConversa)
            .join(ult, ult.c.cid == DmConversa.id)
            .where(DmConversa.plataforma == PLATAFORMA)
        )
    ).one()
    return _numeros(linha)


async def contar_por_conta(session: AsyncSession) -> list[dict]:
    """As mesmas contagens de `contar`, uma por CONTA (a linha da barra de lojas).

    `conta` = o @ do CADASTRO (`redes_sociais.conta`, o atual: a conversa
    guarda o @ do dia em que nasceu), ou o da conversa se o cadastro não
    tiver. `rede_social_id` None = conversas de conta que saiu do cadastro:
    ficam numa linha só (`NOME_SEM_CADASTRO`). Contas mais conhecidas
    primeiro (pelo @), a sem cadastro no fim.
    """
    agora = datetime.now(UTC)
    ult = _ultimas()
    linhas = (
        await session.execute(
            select(
                DmConversa.rede_social_id,
                func.max(RedeSocial.conta),
                func.max(DmConversa.conta),
                *_contagens(ult, agora),
            )
            .select_from(DmConversa)
            .join(ult, ult.c.cid == DmConversa.id)
            .outerjoin(RedeSocial, RedeSocial.id == DmConversa.rede_social_id)
            .where(DmConversa.plataforma == PLATAFORMA)
            .group_by(DmConversa.rede_social_id)
        )
    ).all()
    contas = [
        {
            "rede_social_id": rs,
            "conta": (arroba(cadastro) or arroba(da_conversa)) if rs else NOME_SEM_CADASTRO,
            **_numeros(resto),
        }
        for rs, cadastro, da_conversa, *resto in linhas
    ]
    return sorted(contas, key=lambda c: (c["rede_social_id"] is None, (c["conta"] or "").lower()))


def somar(contas: list[dict]) -> dict[str, int]:
    """O total do Instagram (o `contar`) a partir das contas — uma consulta só no /resumo."""
    return {
        k: sum(int(c.get(k) or 0) for c in contas)
        for k in ("aguardando", "vencendo", "vencidas", "total", "abertas")
    }


def _tipo(m: DmMensagem) -> str:
    if m.tipo in ("texto", "story_reply"):
        return "texto"
    if m.tipo == "anexo":
        return {"image": "imagem", "video": "video"}.get(m.anexo_tipo or "", "arquivo")
    return "outro"


def _mensagem(m: DmMensagem, *, comprador_nome: str | None) -> dict:
    if m.direcao == DIRECAO_RECEBIDA:
        autor, origem, autor_nome = AUTOR_CLIENTE, ORIGEM_CLIENTE, comprador_nome
    elif m.direcao == DIRECAO_ECO:
        # Alguém respondeu pela caixa de entrada do Instagram (ou outro app).
        autor, origem, autor_nome = AUTOR_LOJA, ORIGEM_EXTERNO, None
    else:
        # O robô: texto da biblioteca (resposta_modelo_id) = IA; sem ele, humano.
        autor = AUTOR_LOJA
        origem = ORIGEM_IA if m.resposta_modelo_id else ORIGEM_HUMANO
        autor_nome = None
    status = m.status if m.direcao == DIRECAO_ENVIADA else "recebida"
    if status == "pendente":
        status = "enviando"
    return {
        "id": str(m.id),
        "autor": autor,
        "origem": origem,
        "autor_nome": autor_nome,
        "tipo": _tipo(m),
        "texto": m.texto,
        "anexos": (
            [{"tipo": m.anexo_tipo, "url": m.anexo_url}] if m.anexo_url else []
        ),
        "enviada_em": _utc(m.ocorrido_em or m.enviada_em or m.created_at),
        "status": status,
        # `motivo` do robô é texto de operação (por que escalou, qual erro).
        "erro": m.motivo if m.direcao == DIRECAO_ENVIADA and m.status != MSG_ENVIADA else None,
    }


async def detalhe(session: AsyncSession, conversa_id: str) -> dict | None:
    """Conversa `ig:<uuid>` + mensagens no formato da tela; None se não existe."""
    uid = uuid_do_id(conversa_id)
    if uid is None:
        return None
    conversa = await session.get(DmConversa, uid)
    if conversa is None or conversa.plataforma != PLATAFORMA:
        return None
    mensagens = list(
        reversed(
            (
                await session.execute(
                    select(DmMensagem)
                    .where(
                        DmMensagem.conversa_id == uid,
                        DmMensagem.status.not_in((MSG_SECO, MSG_DESCARTADA)),
                    )
                    .order_by(_MOMENTO.desc(), DmMensagem.created_at.desc())
                    .limit(MAX_MENSAGENS_DETALHE)
                )
            )
            .scalars()
            .all()
        )
    )
    visiveis = [
        m
        for m in mensagens
        if m.direcao != DIRECAO_ENVIADA or m.status in (MSG_ENVIADA, MSG_REVISAR)
    ]

    def _momento(m: DmMensagem) -> datetime:
        return _utc(m.ocorrido_em or m.created_at) or datetime.now(UTC)

    do_cliente = max(
        (_momento(m) for m in visiveis if m.direcao == DIRECAO_RECEBIDA), default=None
    )
    da_loja = max(
        (_momento(m) for m in visiveis if m.direcao != DIRECAO_RECEBIDA), default=None
    )
    ultima = visiveis[-1] if visiveis else None
    nomes = await _nomes(session, {conversa.assumido_por})
    resumo = _resumo(
        conversa,
        em=_momento(ultima) if ultima else None,
        do_cliente=do_cliente,
        da_loja=da_loja,
        ultima=ultima,
        atribuido_nome=nomes.get(conversa.assumido_por),
    )
    resumo.update(
        {
            "comprador_id": conversa.participante_id,
            "anuncio_id": None,
            "bloqueio_motivo": None,
            "pode_enviar_ate": do_cliente + JANELA if do_cliente else None,
        }
    )
    return {
        "conversa": resumo,
        "mensagens": [
            _mensagem(m, comprador_nome=conversa.participante_nome) for m in mensagens
        ],
        "rascunho": None,
        # Mesmas chaves do `contexto.vazio()` das lojas: DM não tem pedido.
        "contexto": {
            "pedido": None,
            "logistica": None,
            "chamados": [],
            "devolucoes": [],
            "nota_fiscal": None,
        },
        "envio": {
            "pode_enviar": False,
            "codigo": "somente_leitura",
            "motivo": "Instagram é só leitura aqui: responda pela caixa de entrada do Instagram.",
            "limite_caracteres": LIMITE_CARACTERES,
            "modo": MODO_OBSERVAR,
            "sla_horas": int(JANELA.total_seconds() // 3600),
            # Quem responde é outro (o robô/a caixa do Instagram): o DaVinci só lê.
            "modo_observacao": True,
        },
        # DM não tem pedido nem anúncio, e a IA do atendimento não escreve DM.
        "pedido_mkt": None,
        "produto": None,
        "sugestoes": [],
    }
