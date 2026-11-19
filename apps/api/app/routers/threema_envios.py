"""Cadastros › Envios Threema (tarefa 16): a tela de cada envio do Threema
— quem recebe, se está ligado, do que depende, o último envio — e as rotas
dos robôs do Mac.

Prefixo `/api/threema` (e não `/api/cadastros/...`: `/api/cadastros/{id}`
capturaria o caminho). Tudo é só de admin, menos:

* `GET /envios/{chave}/quem-recebe`: só os NOMES de quem recebe (sem ID), com
  a porta que o catálogo declara para cada envio (`EnvioDef.leitura`): a
  mesma do botão Informar daquele painel ou a permissão de ver o painel. É o
  atalho dos painéis de origem;
* `/agent/*`: os robôs do Mac, com o token de agente (`X-Agent-Token`, o
  mesmo do executor de NF). Leem a lista de cada envio do Mac e gravam no
  registro o que mandaram.

Nenhum envio do servidor está migrado nesta fase: quem recebe cada um é a
configuração antiga, que a tela mostra com o lugar onde ela é editada hoje.
A edição (C7, só admin) vale só para o envio que lê o Cadastro — o do
servidor migrado e o do Mac; o do servidor não migrado responde 422
`envio_nao_migrado` (editar aqui não mudaria nada). Grupos, contatos
externos, o envio de teste e o botão de emergência também são só de admin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_active_user, require_admin
from app.models import User, UserRole
from app.routers.nf import _require_nf_agent_token
from app.schemas.threema_envios import (
    ContatoIn,
    ContatoPatchIn,
    EmergenciaIn,
    EnvioPatchIn,
    GrupoIn,
    GrupoPatchIn,
    RefIn,
    RegistrosRoboIn,
    RegraIn,
)
from app.services import threema
from app.services import threema_envios_edicao as edicao
from app.services import threema_envios_tela as tela
from app.services.threema_catalogo import CATALOGO

router = APIRouter(prefix="/api/threema", tags=["threema"])

Sessao = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(require_admin)]


def _erro(e: tela.TelaError) -> HTTPException:
    return HTTPException(e.status, detail=e.detail())


# --- robôs do Mac (token de agente) --------------------------------------------------


@router.get("/agent/destinatarios", dependencies=[Depends(_require_nf_agent_token)])
async def agent_destinatarios(
    session: Sessao,
    onde_roda: Annotated[str, Query(pattern="^mac$")] = "mac",
) -> dict:
    """`{gerado_em, usar_legado, envios: {chave: {ativo, ids, fonte}}}` dos
    envios do Mac. `fonte: "vazio"` = o Mac usa a reserva do .env dele."""
    return await tela.destinatarios_robo(session, onde_roda)


@router.post("/agent/registros", dependencies=[Depends(_require_nf_agent_token)])
async def agent_registros(body: RegistrosRoboIn, session: Sessao) -> dict:
    """Grava no registro o que o Mac mandou (até 200 itens, um por
    destinatário). Chave desconhecida fica com `envio_chave` vazio e volta em
    `chaves_desconhecidas`."""
    resposta = await tela.gravar_registros_robo(session, body.itens)
    await session.commit()
    return resposta


# --- leitura de admin ---------------------------------------------------------------


@router.get("/envios")
async def listar_envios(
    session: Sessao,
    _admin: Admin,
    painel: Annotated[str | None, Query(max_length=40)] = None,
    onde_roda: Annotated[str | None, Query(pattern="^(servidor|mac|externo)$")] = None,
    situacao: Annotated[str | None, Query(max_length=20)] = None,
    busca: Annotated[str | None, Query(max_length=100)] = None,
    pessoa: UUID | None = None,
) -> dict:
    """O catálogo inteiro com quem recebe (nomes), situação e faixas, filtrado
    por painel, onde roda, situação, busca e pessoa (`?pessoa=` = só os envios
    em que ela está). `contagem` é do catálogo inteiro."""
    chaves = None
    uso = None
    if pessoa is not None:
        try:
            uso = await tela.envios_da_pessoa(session, pessoa)
        except tela.TelaError as e:
            raise _erro(e) from e
        chaves = {x["chave"] for x in uso["envios"]}
    corpo = await tela.listar(
        session,
        painel=painel,
        onde_roda=onde_roda,
        situacao=situacao if situacao in tela.SITUACOES else None,
        busca=busca,
        chaves=chaves,
    )
    corpo["pessoa"] = uso["usuario"] if uso is not None else None
    return corpo


@router.get("/pessoas")
async def listar_pessoas(session: Sessao, _admin: Admin) -> dict:
    """Usuários ativos (com ou sem Threema), grupos e contatos externos."""
    return await tela.pessoas(session)


@router.get("/pessoas/{user_id}/envios")
async def envios_da_pessoa(user_id: UUID, session: Sessao, _admin: Admin) -> dict:
    """Em quais envios a pessoa está e em quais é a única (o aviso de
    Usuários antes de suspender, excluir ou apagar o Threema)."""
    try:
        return await tela.envios_da_pessoa(session, user_id)
    except tela.TelaError as e:
        raise _erro(e) from e


async def _porta_leitura(user: User, chave: str) -> None:
    """A porta do quem-recebe leve: a declarada no catálogo."""
    from app.routers.informar import _exige_acesso

    d = CATALOGO.get(chave)
    if d is None:
        raise HTTPException(404, detail={"code": "envio_desconhecido"})
    if user.role == UserRole.ADMIN:
        return
    tipo, _, alvo = d.leitura.partition(":")
    if tipo == "informar" and alvo:
        _exige_acesso(user, alvo)
        return
    if tipo == "perm" and alvo:
        recurso, _, acao = alvo.partition(":")
        if (user.permissions or {}).get(recurso, {}).get(acao or "view", False):
            return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "admin_only"})


@router.get("/envios/{chave}/quem-recebe")
async def quem_recebe(
    chave: str,
    session: Sessao,
    user: Annotated[User, Depends(require_active_user)],
) -> dict:
    """Só os nomes de quem recebe o envio (sem ID) e se ele usa a
    configuração antiga. Porta: a do catálogo (`EnvioDef.leitura`)."""
    await _porta_leitura(user, chave)
    try:
        return await tela.quem_recebe_leve(session, chave)
    except tela.TelaError as e:
        raise _erro(e) from e


@router.get("/envios/{chave}/registros")
async def registros_do_envio(
    chave: str,
    session: Sessao,
    _admin: Admin,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    antes: datetime | None = None,
) -> dict:
    """Os últimos lotes do envio (mais novo primeiro). `antes` pagina."""
    try:
        return await tela.registros(session, chave, limit=limit, antes=antes)
    except tela.TelaError as e:
        raise _erro(e) from e


@router.get("/envios/{chave}")
async def detalhe_envio(chave: str, session: Sessao, _admin: Admin) -> dict:
    """Um envio com as linhas do Cadastro, o "Antes" (a configuração antiga)
    e, no item 1, as regras de status com Mensagem ou 👤."""
    try:
        return await tela.detalhe(session, chave)
    except tela.TelaError as e:
        raise _erro(e) from e


# --- edição de admin (C7) -----------------------------------------------------------


def _autor(user: User) -> str:
    return (user.name or user.email or "admin")[:120]


def _refs(itens: list[RefIn] | None) -> list[edicao.RefIn] | None:
    if itens is None:
        return None
    return [edicao.RefIn(tipo=r.tipo, id=r.id) for r in itens]


@router.patch("/envios/{chave}")
async def editar_envio(chave: str, body: EnvioPatchIn, session: Sessao, admin: Admin) -> dict:
    """Liga/desliga, observação, quem recebe e o re-aviso (robôs da
    Ouvidoria). Só envio que lê o Cadastro: o do servidor migrado e o do Mac
    (o do servidor não migrado: 422 `envio_nao_migrado`)."""
    enviados = body.model_fields_set
    try:
        avisos = await edicao.editar_envio(
            session,
            chave,
            autor=_autor(admin),
            ativo=body.ativo if "ativo" in enviados else None,
            observacao=body.observacao,
            mudar_observacao="observacao" in enviados,
            destinatarios=_refs(body.destinatarios) if "destinatarios" in enviados else None,
            reaviso_horas=body.reaviso_horas if "reaviso_horas" in enviados else None,
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    corpo = await tela.detalhe(session, chave)
    corpo["avisos_da_edicao"] = avisos
    return corpo


@router.put("/envios/{chave}/regras/{status_id}")
async def editar_regra(
    chave: str, status_id: UUID, body: RegraIn, session: Sessao, admin: Admin
) -> dict:
    """Lista própria de uma regra de status (item 1)."""
    try:
        await edicao.editar_regra(
            session, chave, status_id, autor=_autor(admin), destinatarios=_refs(body.destinatarios)
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    return await tela.detalhe(session, chave)


@router.delete("/envios/{chave}/regras/{status_id}")
async def regra_volta_ao_padrao(chave: str, status_id: UUID, session: Sessao, admin: Admin) -> dict:
    """A regra volta a usar a lista padrão do envio."""
    try:
        await edicao.editar_regra(
            session, chave, status_id, autor=_autor(admin), destinatarios=None
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    return await tela.detalhe(session, chave)


@router.post("/envios/{chave}/teste")
async def testar_envio(chave: str, session: Sessao, admin: Admin) -> dict:
    """Manda "[TESTE] <nome>" para quem recebe agora, pelo remetente do
    envio (mesmo desligado). Fica no registro com origem `teste`. Só para
    envio do servidor que já lê o Cadastro (422 `teste_nao_migrado`)."""
    try:
        return await edicao.testar(session, chave, autor=_autor(admin))
    except tela.TelaError as e:
        raise _erro(e) from e
    except threema.ThreemaConfigError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "threema_config",
                "message": f"O remetente deste envio não está pronto no servidor ({e}).",
            },
        ) from e
    except Exception as e:  # noqa: BLE001 — erro de rede do Threema vira frase
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "threema_falhou",
                "message": f"O Threema não respondeu ({type(e).__name__}). Tente de novo.",
            },
        ) from e


@router.post("/emergencia")
async def emergencia(body: EmergenciaIn, session: Sessao, admin: Admin) -> dict:
    """Liga/desliga a emergência: TODOS os envios voltam à configuração
    antiga no próximo envio, sem recriar container."""
    estado = await edicao.definir_emergencia(
        session, usar_legado=body.usar_legado, autor=_autor(admin)
    )
    await session.commit()
    return estado


@router.get("/grupos")
async def listar_grupos(session: Sessao, _admin: Admin) -> list[dict]:
    return (await tela.pessoas(session))["grupos"]


@router.post("/grupos", status_code=201)
async def criar_grupo(body: GrupoIn, session: Sessao, _admin: Admin) -> dict:
    try:
        g = await edicao.criar_grupo(
            session, nome=body.nome, descricao=body.descricao, membros=_refs(body.membros) or []
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    return edicao.grupo_out(g)


@router.patch("/grupos/{grupo_id}")
async def editar_grupo(grupo_id: UUID, body: GrupoPatchIn, session: Sessao, _admin: Admin) -> dict:
    enviados = body.model_fields_set
    try:
        g = await edicao.editar_grupo(
            session,
            grupo_id,
            nome=body.nome if "nome" in enviados else None,
            descricao=body.descricao,
            mudar_descricao="descricao" in enviados,
            membros=_refs(body.membros) if "membros" in enviados else None,
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    return edicao.grupo_out(g)


@router.delete("/grupos/{grupo_id}", status_code=204)
async def apagar_grupo(grupo_id: UUID, session: Sessao, _admin: Admin) -> None:
    """Grupo em uso por algum envio: 409 com a lista dos envios."""
    try:
        await edicao.apagar_grupo(session, grupo_id)
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()


@router.get("/contatos")
async def listar_contatos(session: Sessao, _admin: Admin) -> list[dict]:
    return (await tela.pessoas(session))["contatos"]


@router.post("/contatos", status_code=201)
async def criar_contato(body: ContatoIn, session: Sessao, _admin: Admin) -> dict:
    """ID que já é de alguém de Usuários: 409 "já é de Fulano"."""
    try:
        c = await edicao.criar_contato(
            session, threema_id=body.threema_id, nome=body.nome, observacao=body.observacao
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    return edicao.contato_out(c)


@router.patch("/contatos/{contato_id}")
async def editar_contato(
    contato_id: UUID, body: ContatoPatchIn, session: Sessao, _admin: Admin
) -> dict:
    enviados = body.model_fields_set
    try:
        c = await edicao.editar_contato(
            session,
            contato_id,
            nome=body.nome if "nome" in enviados else None,
            threema_id=body.threema_id if "threema_id" in enviados else None,
            ativo=body.ativo if "ativo" in enviados else None,
            observacao=body.observacao,
            mudar_observacao="observacao" in enviados,
        )
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
    return edicao.contato_out(c)


@router.delete("/contatos/{contato_id}", status_code=204)
async def apagar_contato(contato_id: UUID, session: Sessao, _admin: Admin) -> None:
    """Contato em uso (num envio ou grupo): 409 com onde ele está."""
    try:
        await edicao.apagar_contato(session, contato_id)
    except tela.TelaError as e:
        await session.rollback()
        raise _erro(e) from e
    await session.commit()
