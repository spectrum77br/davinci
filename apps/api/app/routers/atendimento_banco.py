"""Pós-venda › Atendimento: a TELA DO BANCO DE RESPOSTAS (13/11/2026), só admin.

O Eduardo (13/11/2026): o agente tem o BANCO DE RESPOSTAS da equipe (`atendimento_respostas`,
services/atendimento/banco_respostas.py) e o tipo de pergunta só sai sozinho com a aprovação
da equipe (`regua.py`). Esta tela é a revisão diária do que a IA mandou sozinha e a busca no
banco (a % da régua fica na aba Métricas, por equipe):

  GET  /api/atendimento/banco/revisao?dia=AAAA-MM-DD — o que a IA mandou SOZINHA no dia
                                                      (padrão: hoje em São Paulo)
  POST /api/atendimento/banco/revisao/{rascunho_id}  — 👍/👎 do envio: {nota, correcao?}
  GET  /api/atendimento/banco/respostas              — as respostas ativas do banco
                                                      (?tipo=&integration_id=&q=&limite=50)

ACESSO: a MESMA trava da caixa (`_so_admin`: na observação, a escrita é de quem MEXE) e, por
cima, só ADMIN — o critério das abas de admin do atendimento (`role == admin`, o `ehAdmin` da
tela, como o aprovar/reprovar fato): 403 `so_admin` para os outros.

O QUE SAIU SOZINHO: o envio da IA (`enviar._avaliar_rascunho`, origem `davinci_ia`) marca o
rascunho `enviado` e NÃO grava avaliação — fica a MENSAGEM da loja com origem `davinci_ia`,
`enviada` ou `revisar` (até alguém conferir, conta como resposta), ligada ao rascunho pelo
`rascunho_id`. Um item por rascunho (a NF automática manda o arquivo e a frase do mesmo
rascunho). O dia é o de São Paulo, pelo relógio da plataforma (`enviada_em`; sem ele, a
chegada). Fica fora o rascunho que uma PESSOA já avaliou na conversa (o 👍/👎 antigo: enviou
igual, editou, descartou, escreveu do zero); a nota provisória da observação (`observou`)
vira a da revisão.

A AMOSTRA: todos os sensíveis (`tipos_pergunta.SENSIVEIS`, a régua de 90%) + até 20 dos
outros — os já revisados nunca saem da lista; o resto por sorteio com a semente do dia (o
sha1 do dia e do rascunho): o mesmo dia mostra os mesmos.

A PERGUNTA mostrada é o turno do cliente logo antes do envio (as mensagens seguidas dele, a
última incluída — `banco_respostas.turno_do_cliente_antes`): a mesma que vai ao banco com a
correção. Sem o turno (fora da janela de 2 dias), o texto da mensagem que gerou o rascunho.

A REVISÃO grava a `AtendimentoAvaliacao(acao=automatico)` (uma por rascunho; refazer troca a
nota): 👍 = acerto, 👎 = erro com a correção obrigatória (até 4.000; igual ao que saiu é 422).
O juiz a conta na régua do assunto. A correção entra no banco como resposta AJUSTADA por pessoa
(`banco_respostas.gravar_correcao`, as regras de sempre); o 👍 depois do 👎 a tira (aposentada,
nunca apagada). Depois, como o 👍/👎 da conversa, o `enviar.par_na_hora` refaz os pares do
agente daquela conversa (a correção vira o par `correcao`; sem a chave Claude, nada).

Nada de texto de cliente no log: só ids, códigos e contagens.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    User,
    UserRole,
)
from app.models.atendimento import AtendimentoResposta
from app.routers.atendimento import _nomes, _nomes_das_lojas, _so_admin
from app.schemas.atendimento_banco import (
    BancoDaCorrecaoOut,
    LojaContagemOut,
    RespostaBancoOut,
    RespostasBancoOut,
    RevisaoContagensOut,
    RevisaoIn,
    RevisaoItemOut,
    RevisaoNotaOut,
    RevisaoOut,
    RevisaoSalvaOut,
    TipoContagemOut,
)
from app.services.atendimento import (
    banco_respostas,
    busca_texto,
    constantes,
    duvida,
    enviar,
    gravar,
    tipos_pergunta,
)
from app.services.atendimento import manual as manual_svc

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento/banco", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)

FUSO = ZoneInfo("America/Sao_Paulo")
# Os não sensíveis da amostra do dia.
MAX_OUTROS = 20
# A pergunta: as mensagens da conversa até 2 dias antes do envio (a IA só manda sozinha a
# resposta de pergunta recente — `gatilho_antigo` é de 3 h).
JANELA_PERGUNTA = timedelta(days=2)
MAX_LIMITE_RESPOSTAS = 200

SO_ADMIN_BANCO = {
    "code": "so_admin",
    "detail": "O banco de respostas é só de admin.",
}
# As revisões que a tela pode criar ou refazer: a dela e a nota provisória da observação.
_ACOES_DA_REVISAO = (constantes.AVALIACAO_AUTOMATICO, constantes.AVALIACAO_OBSERVOU)
# Por que a correção não entrou no banco (os códigos de `banco_respostas`), para a tela.
FRASE_FORA_DO_BANCO: dict[str, str] = {
    "sem_texto": "a pergunta ou a correção ficou sem texto.",
    "dado_pessoal": "tem cara de dado pessoal (nome, contato, endereço, documento).",
    "inseguro": "a conversa tem sinal que o agente não pode aprender (instrução, alerta).",
    "proibido": "a correção ensina o que o agente não pode dizer.",
    "so_cortesia": "a pergunta do cliente é só cumprimento.",
    "sem_conteudo": "a correção não ensina nada a outro cliente (cumprimento, um pedido só, "
    "“veja o anúncio”).",
    "curta": "a correção é curta demais.",
    banco_respostas.CORRECAO_FORA_DO_BANCO: "esta plataforma não entra no banco.",
    banco_respostas.CORRECAO_SEM_ENVIO: "não achei o envio da IA na conversa.",
    banco_respostas.CORRECAO_SEM_PERGUNTA: "não achei a pergunta do cliente antes do envio.",
    banco_respostas.CORRECAO_INJECAO: "a conversa tem cara de instrução para a IA.",
    "falhou": "a gravação no banco falhou (a revisão ficou salva).",
}


async def _admin(user: Annotated[User, Depends(require_active_user)]) -> User:
    """Só admin (o critério das abas de admin do atendimento: `role == admin`)."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, detail=SO_ADMIN_BANCO)
    return user


Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(_admin)]


# ── O dia e a amostra (puras) ──────────────────────────────────────────────────


def hoje() -> date:
    return datetime.now(FUSO).date()


def intervalo_do_dia(dia: date) -> tuple[datetime, datetime]:
    """[início, fim) do dia em São Paulo, em UTC. PURA."""
    inicio = datetime.combine(dia, time.min, tzinfo=FUSO)
    fim = datetime.combine(dia + timedelta(days=1), time.min, tzinfo=FUSO)
    return inicio.astimezone(UTC), fim.astimezone(UTC)


def sorteio(dia: date, rascunho_id: UUID) -> str:
    """A posição do rascunho no sorteio do dia (a semente é o dia). PURA."""
    return hashlib.sha1(
        f"{dia.isoformat()}|{rascunho_id}".encode(), usedforsecurity=False
    ).hexdigest()


@dataclass(frozen=True)
class Envio:
    """Um rascunho que a IA mandou sozinha, com a revisão (se houver)."""

    rascunho_id: UUID
    conversa_id: UUID
    categoria: str | None
    gatilho_id: UUID | None
    enviada_em: datetime
    acao: str | None = None
    nota: str | None = None
    correcao: str | None = None
    user_id: UUID | None = None
    avaliado_em: datetime | None = None

    @property
    def sensivel(self) -> bool:
        return self.categoria in tipos_pergunta.SENSIVEIS

    @property
    def revisado(self) -> bool:
        return self.acao == constantes.AVALIACAO_AUTOMATICO and self.nota is not None


def escolher(envios: Sequence[Envio], dia: date, *, maximo: int = MAX_OUTROS) -> list[Envio]:
    """Todos os sensíveis + até `maximo` dos outros: os já revisados primeiro (nunca saem da
    lista), o resto pelo sorteio do dia. Os sensíveis vêm antes; dentro, pela hora. PURA."""
    sensiveis = [e for e in envios if e.sensivel]
    outros = [e for e in envios if not e.sensivel]
    revisados = [e for e in outros if e.revisado]
    resto = sorted((e for e in outros if not e.revisado), key=lambda e: sorteio(dia, e.rascunho_id))
    escolhidos = revisados + resto[: max(0, maximo - len(revisados))]

    def hora(e: Envio) -> tuple[datetime, str]:
        return e.enviada_em, str(e.rascunho_id)

    return sorted(sensiveis, key=hora) + sorted(escolhidos, key=hora)


# ── As consultas ───────────────────────────────────────────────────────────────

_m = AtendimentoMensagem
_MOMENTO = func.coalesce(_m.enviada_em, _m.created_at)


def _saiu_sozinha_sql() -> Any:
    """O SQL de `banco_respostas.saiu_sozinha`."""
    return and_(
        _m.origem == constantes.ORIGEM_IA,
        _m.autor == constantes.AUTOR_LOJA,
        _m.status.in_(banco_respostas.STATUS_QUE_SAIRAM),
        _m.rascunho_id.is_not(None),
    )


def _no_intervalo_sql(inicio: datetime, fim: datetime) -> Any:
    """`coalesce(enviada_em, created_at)` em [início, fim), pelo índice de `enviada_em`."""
    return or_(
        and_(_m.enviada_em >= inicio, _m.enviada_em < fim),
        and_(_m.enviada_em.is_(None), _m.created_at >= inicio, _m.created_at < fim),
    )


async def _envios(
    session: AsyncSession,
    *,
    intervalo: tuple[datetime, datetime] | None = None,
    rascunho: AtendimentoRascunho | None = None,
) -> list[Envio]:
    """Os rascunhos que a IA mandou sozinha (no intervalo, ou um só), com a revisão — sem os
    que uma pessoa já avaliou na conversa. O intervalo vai pelo índice de `enviada_em`; o
    rascunho, pelo da conversa (`rascunho_id` não tem índice)."""
    r, a = AtendimentoRascunho, AtendimentoAvaliacao
    filtros = [_saiu_sozinha_sql()]
    if intervalo is not None:
        filtros.append(_no_intervalo_sql(*intervalo))
    if rascunho is not None:
        filtros += [_m.conversa_id == rascunho.conversa_id, _m.rascunho_id == rascunho.id]
    linhas = (
        await session.execute(
            select(
                r.id,
                r.conversa_id,
                r.categoria,
                r.mensagem_gatilho_id,
                func.min(_MOMENTO).label("enviada_em"),
                a.acao,
                a.nota,
                a.correcao,
                a.user_id,
                a.updated_at,
            )
            .select_from(_m)
            .join(r, r.id == _m.rascunho_id)
            .outerjoin(a, a.rascunho_id == r.id)
            .where(*filtros)
            .group_by(r.id, a.id)
        )
    ).all()
    return [
        Envio(
            rascunho_id=linha.id,
            conversa_id=linha.conversa_id,
            categoria=linha.categoria,
            gatilho_id=linha.mensagem_gatilho_id,
            enviada_em=linha.enviada_em,
            acao=linha.acao,
            nota=linha.nota,
            correcao=linha.correcao,
            user_id=linha.user_id,
            avaliado_em=linha.updated_at,
        )
        for linha in linhas
        if linha.acao is None or linha.acao in _ACOES_DA_REVISAO
    ]


async def _envio_da_ia(session: AsyncSession, rascunhos: dict[UUID, UUID]) -> dict[UUID, list]:
    """Por rascunho (`rascunho_id → conversa_id`): as mensagens que a IA mandou sozinha, em
    ordem (o texto que saiu). A conversa entra no filtro pelo índice dela."""
    if not rascunhos:
        return {}
    por: dict[UUID, list] = defaultdict(list)
    for linha in (
        await session.execute(
            select(_m.id, _m.rascunho_id, _m.texto, _m.anexos, _MOMENTO.label("momento"))
            .where(
                _m.conversa_id.in_(set(rascunhos.values())),
                _m.rascunho_id.in_(list(rascunhos)),
                _saiu_sozinha_sql(),
            )
            .order_by(_m.rascunho_id, _MOMENTO, _m.created_at)
        )
    ).all():
        por[linha.rascunho_id].append(linha)
    return por


def texto_do_envio(mensagens: Sequence[Any]) -> str:
    """O texto que saiu: as mensagens da IA do rascunho, em ordem. PURA."""
    return "\n\n".join(t for t in ((m.texto or "").strip() for m in mensagens) if t)


async def _perguntas(
    session: AsyncSession,
    envios: Sequence[Envio],
    da_ia: dict[UUID, list],
    conversas: dict[UUID, Any],
) -> dict[UUID, str | None]:
    """Por rascunho: o turno do cliente logo antes do envio; sem ele, a mensagem-gatilho."""
    primeiros = {e.rascunho_id: da_ia[e.rascunho_id][0] for e in envios if da_ia.get(e.rascunho_id)}
    saida: dict[UUID, str | None] = {}
    if primeiros:
        desde = min(p.momento for p in primeiros.values()) - JANELA_PERGUNTA
        ate = max(p.momento for p in primeiros.values())
        por_conversa: dict[UUID, list] = defaultdict(list)
        for linha in (
            await session.execute(
                select(
                    _m.id,
                    _m.conversa_id,
                    _m.autor,
                    _m.origem,
                    _m.tipo,
                    _m.texto,
                    _m.payload,
                    _m.status,
                    _m.enviada_em,
                    _m.created_at,
                    _m.rascunho_id,
                )
                .where(
                    _m.conversa_id.in_({e.conversa_id for e in envios}),
                    _MOMENTO >= desde,
                    _MOMENTO <= ate,
                )
                .order_by(_m.conversa_id, _MOMENTO, _m.created_at)
            )
        ).all():
            por_conversa[linha.conversa_id].append(linha)
        for e in envios:
            if e.rascunho_id not in primeiros:
                continue
            c = conversas.get(e.conversa_id)
            turno = banco_respostas.turno_do_cliente_antes(
                por_conversa.get(e.conversa_id, []),
                [m.id for m in da_ia[e.rascunho_id]],
                comprador=(c.comprador_nome, c.comprador_id) if c is not None else (),
            )
            if turno is not None:
                partes = [
                    (m.texto or "").strip() or (f"[{m.tipo}]" if m.tipo != "texto" else "")
                    for m in turno.mensagens
                ]
                saida[e.rascunho_id] = "\n".join(p for p in partes if p) or None
    faltam = {e.gatilho_id for e in envios if not saida.get(e.rascunho_id) and e.gatilho_id}
    if faltam:
        textos = dict(
            (await session.execute(select(_m.id, _m.texto).where(_m.id.in_(faltam)))).all()
        )
        for e in envios:
            if not saida.get(e.rascunho_id) and e.gatilho_id:
                saida[e.rascunho_id] = textos.get(e.gatilho_id)
    return saida


async def _nomes_dos_assuntos(session: AsyncSession) -> dict[str, str]:
    """O nome de cada assunto no manual (`atendimento_categorias`; vazia, as constantes)."""
    try:
        return {
            str(c["id"]): str(c["nome"])
            for c in await manual_svc.categorias_ativas(session)
            if c.get("id") and c.get("nome")
        }
    except Exception as e:  # noqa: BLE001 — o nome nunca derruba a tela
        logger.warning("atendimento_banco_nomes_falhou", err=type(e).__name__)
        return {}


def nome_do_assunto(tipo: str | None, nomes: dict[str, str]) -> str:
    """O nome do manual; sem ele, o de `duvida.nome_do_assunto`. PURA."""
    if not tipo:
        return "Sem assunto"
    return nomes.get(tipo) or duvida.nome_do_assunto(tipo)


async def _itens(session: AsyncSession, envios: Sequence[Envio]) -> list[RevisaoItemOut]:
    """Os envios escolhidos, prontos para a tela (o texto que saiu, a pergunta, a revisão)."""
    if not envios:
        return []
    da_ia = await _envio_da_ia(session, {e.rascunho_id: e.conversa_id for e in envios})
    # Linhas, não objetos: o SAVEPOINT do nome da loja não expira nada que se leia depois.
    c_ = AtendimentoConversa
    conversas = {
        linha.id: linha
        for linha in (
            await session.execute(
                select(
                    c_.id,
                    c_.integration_id,
                    c_.conta,
                    c_.plataforma,
                    c_.canal,
                    c_.comprador_nome,
                    c_.comprador_id,
                ).where(c_.id.in_({e.conversa_id for e in envios}))
            )
        ).all()
    }
    perguntas = await _perguntas(session, envios, da_ia, conversas)
    lojas = await _nomes_das_lojas(session, {c.integration_id for c in conversas.values()})
    assuntos = await _nomes_dos_assuntos(session)
    quem = await _nomes(session, {e.user_id for e in envios if e.revisado})
    # A correção do 👎 está no banco? (a chave do envio: a conversa e a 1ª mensagem da IA)
    chaves = {
        e.rascunho_id: banco_respostas.chave_de(e.conversa_id, da_ia[e.rascunho_id][0].id)
        for e in envios
        if e.revisado and e.nota == constantes.NOTA_ERRO and da_ia.get(e.rascunho_id)
    }
    no_banco: set[str] = set()
    if chaves:
        t = AtendimentoResposta
        no_banco = set(
            (
                await session.scalars(
                    select(t.chave).where(
                        t.chave.in_(list(chaves.values())),
                        t.status == banco_respostas.STATUS_ATIVA,
                    )
                )
            ).all()
        )
    saida: list[RevisaoItemOut] = []
    for e in envios:
        c = conversas.get(e.conversa_id)
        if c is None:
            continue
        mensagens = da_ia.get(e.rascunho_id, [])
        avaliacao = None
        if e.revisado:
            avaliacao = RevisaoNotaOut(
                nota=e.nota or "",
                correcao=e.correcao,
                por=quem.get(e.user_id) if e.user_id else None,
                em=e.avaliado_em,
                no_banco=(chaves.get(e.rascunho_id) in no_banco)
                if e.nota == constantes.NOTA_ERRO
                else None,
            )
        saida.append(
            RevisaoItemOut(
                rascunho_id=e.rascunho_id,
                conversa_id=e.conversa_id,
                integration_id=c.integration_id,
                loja=lojas.get(c.integration_id) or c.conta,
                plataforma=c.plataforma,
                canal=c.canal,
                assunto=e.categoria,
                assunto_nome=nome_do_assunto(e.categoria, assuntos),
                sensivel=e.sensivel,
                pergunta=perguntas.get(e.rascunho_id),
                resposta=texto_do_envio(mensagens) or None,
                com_anexo=any(m.anexos for m in mensagens),
                enviada_em=mensagens[0].momento if mensagens else e.enviada_em,
                avaliacao=avaliacao,
            )
        )
    return saida


# ── As rotas ───────────────────────────────────────────────────────────────────


@router.get("/revisao", response_model=RevisaoOut)
async def revisao_do_dia(
    session: Session,
    user: Admin,
    dia: Annotated[date | None, Query()] = None,
) -> RevisaoOut:
    """O que a IA mandou SOZINHA no dia (padrão: hoje em São Paulo): todos os sensíveis + até
    20 dos outros (o sorteio do dia), com a revisão de cada um e as contagens do dia."""
    agora = hoje()
    dia = dia or agora
    envios = await _envios(session, intervalo=intervalo_do_dia(dia))
    itens = await _itens(session, escolher(envios, dia))
    return RevisaoOut(
        dia=dia,
        hoje=agora,
        amostra=MAX_OUTROS,
        contagens=RevisaoContagensOut(
            total=len(envios),
            sensiveis=sum(1 for e in envios if e.sensivel),
            revisados=sum(1 for e in envios if e.revisado),
            erros=sum(1 for e in envios if e.revisado and e.nota == constantes.NOTA_ERRO),
            mostrados=len(itens),
        ),
        itens=itens,
    )


async def _avaliacao_da_revisao(
    session: AsyncSession, rascunho: AtendimentoRascunho
) -> AtendimentoAvaliacao:
    """A avaliação da revisão deste rascunho: a que existe (a da revisão ou a provisória da
    observação) ou uma nova. A de pessoa (enviou igual, editou, descartou, escreveu do zero)
    é 409 `avaliado_por_pessoa`."""

    def _de_pessoa(av: AtendimentoAvaliacao) -> bool:
        return av.acao not in _ACOES_DA_REVISAO

    a = AtendimentoAvaliacao
    av = await session.scalar(select(a).where(a.rascunho_id == rascunho.id))
    if av is None:
        nova = AtendimentoAvaliacao(rascunho_id=rascunho.id, acao=constantes.AVALIACAO_AUTOMATICO)
        await session.flush()  # o que está pendente fora do SAVEPOINT
        try:
            # A nota da conversa no mesmo instante: o UNIQUE barra o segundo INSERT.
            async with session.begin_nested():
                session.add(nova)
                await session.flush()
            return nova
        except IntegrityError:
            av = await session.scalar(select(a).where(a.rascunho_id == rascunho.id))
            if av is None:
                raise
    if _de_pessoa(av):
        raise HTTPException(
            409,
            detail={
                "code": "avaliado_por_pessoa",
                "detail": "Uma pessoa já avaliou esta resposta na conversa — a nota dela fica.",
            },
        )
    return av


@router.post("/revisao/{rascunho_id}", response_model=RevisaoSalvaOut)
async def revisar(
    rascunho_id: UUID,
    body: RevisaoIn,
    session: Session,
    user: Admin,
) -> RevisaoSalvaOut:
    """👍/👎 no que a IA mandou sozinha: cria ou refaz a avaliação `automatico` do rascunho.

    No 👎 a correção (obrigatória) entra no banco como resposta ajustada por pessoa; no 👍, a
    correção que este envio tinha no banco sai (aposentada). Só para o rascunho que saiu
    sozinho e que nenhuma pessoa avaliou na conversa."""
    rascunho = await session.scalar(
        select(AtendimentoRascunho).where(AtendimentoRascunho.id == rascunho_id).with_for_update()
    )
    if rascunho is None:
        raise HTTPException(404, detail={"code": "rascunho_nao_encontrado"})
    mensagens = (await _envio_da_ia(session, {rascunho.id: rascunho.conversa_id})).get(
        rascunho.id, []
    )
    if not mensagens:
        raise HTTPException(
            409,
            detail={
                "code": "nao_saiu_sozinho",
                "detail": "Esta resposta não foi mandada sozinha pela IA.",
            },
        )
    enviado = texto_do_envio(mensagens)
    if (
        body.nota == constantes.NOTA_ERRO
        and enviado
        and gravar.similaridade(body.correcao, enviado) >= enviar.LIMIAR_ENVIOU_IGUAL
    ):
        raise HTTPException(
            422,
            detail={
                "code": "correcao_igual_ao_enviado",
                "detail": "A correção está igual ao que a IA mandou: mude o texto ou marque 👍.",
            },
        )
    conversa = await session.get(AtendimentoConversa, rascunho.conversa_id)
    if conversa is None:
        raise HTTPException(404, detail={"code": "rascunho_nao_encontrado"})
    av = await _avaliacao_da_revisao(session, rascunho)
    nota_antes = av.nota if av.acao == constantes.AVALIACAO_AUTOMATICO else None
    av.acao = constantes.AVALIACAO_AUTOMATICO
    av.nota = body.nota
    av.correcao = body.correcao
    av.texto_final = enviado or None
    av.similaridade = None
    av.user_id = user.id
    await session.flush()

    chave = banco_respostas.chave_de(conversa.id, mensagens[0].id)
    banco = BancoDaCorrecaoOut(entrou=False)
    try:
        async with session.begin_nested():
            if body.nota == constantes.NOTA_ERRO:
                nova, motivo = await banco_respostas.gravar_correcao(
                    session, conversa=conversa, rascunho=rascunho, correcao=body.correcao or ""
                )
                banco = BancoDaCorrecaoOut(
                    entrou=nova is not None,
                    motivo=None if nova is not None else motivo,
                    frase=None if nova is not None else FRASE_FORA_DO_BANCO.get(motivo),
                )
            else:
                tirou = await banco_respostas.retirar_correcao(session, chave)
                banco = BancoDaCorrecaoOut(entrou=False, retirada=tirou > 0)
    except Exception as e:  # noqa: BLE001 — a revisão fica; o banco espera a próxima
        logger.warning(
            "atendimento_banco_correcao_falhou", rascunho_id=str(rascunho.id), err=type(e).__name__
        )
        banco = BancoDaCorrecaoOut(
            entrou=False, motivo="falhou", frase=FRASE_FORA_DO_BANCO["falhou"]
        )
    # Os pares do agente da conversa, como o 👍/👎 da conversa (nunca derruba a revisão).
    await enviar.par_na_hora(session, conversa)
    await session.commit()
    logger.info(
        "atendimento_banco_revisado",
        rascunho_id=str(rascunho.id),
        conversa_id=str(conversa.id),
        nota=body.nota,
        nota_antes=nota_antes,
        assunto=rascunho.categoria,
        no_banco=banco.entrou,
        motivo=banco.motivo,
        retirada=banco.retirada,
        user_id=str(user.id),
    )
    itens = await _itens(session, (await _envios(session, rascunho=rascunho))[:1])
    if not itens:  # não acontece: o rascunho saiu sozinho e a revisão acabou de ser gravada
        raise HTTPException(404, detail={"code": "rascunho_nao_encontrado"})
    return RevisaoSalvaOut(item=itens[0], banco=banco)


@router.get("/respostas", response_model=RespostasBancoOut)
async def respostas_do_banco(
    session: Session,
    user: Admin,
    tipo: Annotated[str | None, Query(max_length=32)] = None,
    integration_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limite: Annotated[int, Query(ge=1, le=MAX_LIMITE_RESPOSTAS)] = 50,
) -> RespostasBancoOut:
    """As respostas ATIVAS do banco, as mais novas primeiro — com `q`, as que têm todas as
    palavras (o `tsv` do banco, `busca_texto`), as mais parecidas primeiro. Mais os assuntos e
    as lojas do banco (com quantas respostas), para os filtros."""
    t = AtendimentoResposta
    filtros: list[Any] = [t.status == banco_respostas.STATUS_ATIVA]
    if tipo and tipo.strip():
        filtros.append(t.tipo == tipo.strip())
    if integration_id is not None:
        filtros.append(t.integration_id == integration_id)
    consulta = busca_texto.consulta_de(q, todas=True) if (q or "").strip() else None
    if consulta is not None:
        filtros.append(t.tsv.op("@@")(consulta))
    total = int(await session.scalar(select(func.count()).select_from(t).where(*filtros)) or 0)
    ordem: list[Any] = [t.respondido_em.desc(), t.id.desc()]
    if consulta is not None:
        ordem.insert(0, func.ts_rank_cd(t.tsv, consulta).desc())
    # Linhas, não objetos (o SAVEPOINT do nome da loja, abaixo). A mensagem da resposta diz se
    # ela é a correção da revisão (a 1ª do envio da IA) e a conversa, se a linha não a tem.
    linhas = (
        await session.execute(
            select(
                t.id,
                t.tipo,
                t.integration_id,
                t.loja,
                t.plataforma,
                t.canal,
                t.modelos,
                t.pergunta,
                t.resposta,
                t.fonte,
                t.respondido_em,
                func.coalesce(t.conversa_id, _m.conversa_id).label("conversa_id"),
                _m.origem.label("origem_msg"),
            )
            .outerjoin(_m, _m.id == t.resposta_msg_id)
            .where(*filtros)
            .order_by(*ordem)
            .limit(limite)
        )
    ).all()
    por_tipo = (
        await session.execute(
            select(t.tipo, func.count())
            .where(t.status == banco_respostas.STATUS_ATIVA)
            .group_by(t.tipo)
        )
    ).all()
    por_loja = (
        await session.execute(
            select(t.integration_id, t.plataforma, func.max(t.loja), func.count())
            .where(t.status == banco_respostas.STATUS_ATIVA, t.integration_id.is_not(None))
            .group_by(t.integration_id, t.plataforma)
        )
    ).all()
    lojas = await _nomes_das_lojas(
        session,
        {r.integration_id for r in linhas} | {integ for integ, *_ in por_loja},
    )
    assuntos = await _nomes_dos_assuntos(session)
    itens = [
        RespostaBancoOut(
            id=r.id,
            tipo=r.tipo,
            tipo_nome=nome_do_assunto(r.tipo, assuntos),
            integration_id=r.integration_id,
            loja=lojas.get(r.integration_id) or r.loja,
            plataforma=r.plataforma,
            canal=r.canal,
            modelos=[str(x) for x in (r.modelos or [])],
            pergunta=r.pergunta,
            resposta=r.resposta,
            fonte=r.fonte,
            da_revisao=r.origem_msg == constantes.ORIGEM_IA,
            respondido_em=r.respondido_em,
            conversa_id=r.conversa_id,
        )
        for r in linhas
    ]
    tipos = sorted(
        (
            TipoContagemOut(tipo=tp, nome=nome_do_assunto(tp, assuntos), n=int(n))
            for tp, n in por_tipo
        ),
        key=lambda x: (-x.n, x.nome.casefold()),
    )
    lojas_out = sorted(
        (
            LojaContagemOut(
                integration_id=integ,
                plataforma=plataforma,
                loja=lojas.get(integ) or loja or str(integ),
                n=int(n),
            )
            for integ, plataforma, loja, n in por_loja
        ),
        key=lambda x: (x.plataforma, x.loja.casefold()),
    )
    return RespostasBancoOut(total=total, itens=itens, tipos=tipos, lojas=lojas_out)
