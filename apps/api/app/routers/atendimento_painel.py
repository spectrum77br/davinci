"""Atendimento › painel do pedido, nota interna, foto e AdsPower (item 3, 01/10/2026).

Rotas novas da caixa `/atendimento`, separadas do `routers/atendimento.py`
(que já tem 3 mil linhas), com a MESMA trava de acesso (`_so_admin`: na
fase de observação toda a equipe lê e só ATENDIMENTO_USUARIOS mexe — a nota
e a foto são de quem mexe; o registro do AdsPower passa para quem lê) e o
mesmo escopo por equipe (`_conversa_ou_404`):

  GET  /api/atendimento/conversas/{id}/painel   estoque por item (lote comprado,
       lotes irmãos, kit), margem (a da aba Margem), Observações do Bling (GET
       ao vivo, 5 min de memória; `?atualizar=1` relê), links "Abrir no Bling"
       / "Abrir na plataforma", perfil do AdsPower e se a foto pode sair agora;
       e, com o pedido em 83955, o porquê (`ag_cancelamento`, item 4).
  POST /api/atendimento/conversas/{id}/notas    nota interna (só a equipe vê).
  POST /api/atendimento/conversas/{id}/foto     UMA foto ao comprador (multipart:
       `arquivo`, `legenda` só no ML, `ultima_vista_id`, `confirmar`) — pelo
       caminho único de saída (`enviar.enviar_foto`): as mesmas travas e os
       mesmos códigos do POST /responder (409/422).
  POST /api/atendimento/adspower/aberto         registra quem abriu qual perfil
       do AdsPower e quando (o DaVinci não fala com o AdsPower: quem abre é o
       navegador de quem clicou, na API local do computador dele).

Registro no `main.py`: `app.include_router(atendimento_painel_router.router)`
(o integrador encaixa; ver as pendências da frente C).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.team_scope import resolve_team_scope
from app.models import User
from app.routers.atendimento import (
    _conversa_ou_404,
    _edit,
    _mensagem_out,
    _nome,
    _quem_le,
    _recusa_http,
    _so_admin,
    _somente_leitura,
    _view,
)
from app.schemas.atendimento import ResponderOut
from app.schemas.atendimento_painel import (
    AdsPowerAbertoIn,
    AdsPowerAbertoOut,
    NotaIn,
    NotaOut,
    PainelOut,
)
from app.services.atendimento import enviar, foto, instagram, painel
from app.services.atendimento.constantes import PLATAFORMA_SITE, PLATAFORMAS_EXTERNAS
from app.services.atendimento.enviar import EnvioRecusado

logger = structlog.get_logger()

router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)


def _painel_do_instagram() -> PainelOut:
    """Direct do Instagram: não é loja de marketplace — sem pedido, sem perfil, sem foto."""
    return PainelOut(
        adspower={
            "motivo": "Direct do Instagram: não há perfil de loja no AdsPower.",
            "codigo": "sem_perfil",
        },
        envio_foto={
            "pode": False,
            "motivo": "Direct do Instagram é só leitura aqui.",
            "codigo": "somente_leitura",
        },
    )


def _painel_externo(plataforma: str) -> PainelOut:
    """Carrinho do site e comentário das redes (02/10/2026): o cartão é outro.

    Não há pedido no Bling nem perfil de loja no AdsPower, e nada sai pela
    caixa (`enviar` recusa): o painel não procura nada disso.
    """
    origem = "Carrinho do site" if plataforma == PLATAFORMA_SITE else "Comentário de rede social"
    return PainelOut(
        adspower={
            "motivo": f"{origem}: não há perfil de loja no AdsPower.",
            "codigo": "sem_perfil",
        },
        envio_foto={
            "pode": False,
            "motivo": f"{origem}: nada sai pela caixa do DaVinci.",
            "codigo": "somente_leitura",
        },
    )


@router.get("/conversas/{conversa_id}/painel", response_model=PainelOut)
async def painel_do_pedido(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    atualizar: Annotated[bool, Query()] = False,
) -> PainelOut:
    """O painel do pedido da conversa. Cada bloco falha sozinho; nunca 500 por um deles.

    `atualizar=1` relê as Observações no Bling na hora (pula a memória de 5 min).
    """
    if instagram.e_instagram(conversa_id):
        return _painel_do_instagram()
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    if c.plataforma in PLATAFORMAS_EXTERNAS:
        return _painel_externo(c.plataforma)
    dados = await painel.painel_da_conversa(session, c, user=user, forcar_observacoes=atualizar)
    # Só leitura: nada a gravar (os blocos rodaram em SAVEPOINTs).
    await session.rollback()
    return PainelOut(**dados)


@router.post(
    "/conversas/{conversa_id}/notas", response_model=NotaOut, status_code=status.HTTP_201_CREATED
)
async def criar_nota(
    conversa_id: str,
    body: NotaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> NotaOut:
    """Nota interna: nunca é enviada, não conta como resposta, a IA não lê."""
    if instagram.e_instagram(conversa_id):
        raise _somente_leitura()
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    try:
        m = await painel.criar_nota(session, c, body.texto, user_id=user.id)
    except painel.NotaInvalida as e:
        raise HTTPException(422, detail={"code": e.code, "detail": e.detail}) from e
    await session.commit()
    out = _mensagem_out(m, conversa=c, nomes={user.id: _nome(user)})
    # O `_mensagem_out` só põe nome de quem ENVIOU (davinci_humano): a nota
    # leva o nome de quem escreveu.
    out.autor_nome = _nome(user)
    return NotaOut(mensagem=out)


@router.post("/conversas/{conversa_id}/foto", response_model=ResponderOut)
async def enviar_foto(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
    arquivo: Annotated[UploadFile, File()],
    legenda: Annotated[str | None, Form(max_length=10_000)] = None,
    ultima_vista_id: Annotated[UUID | None, Form()] = None,
    confirmar: Annotated[bool, Form()] = False,
) -> ResponderOut:
    """Manda UMA foto ao comprador (Shopee, TikTok, pós-venda do ML).

    Trava → 409 com o `code` (texto/foto reprovados → 422), como o
    /responder. Erro da PLATAFORMA não é erro HTTP: volta 200 com a mensagem
    em `falhou`/`revisar`. Com o envio desligado, recusa ANTES de olhar o
    arquivo — nada sobe para a plataforma.
    """
    if instagram.e_instagram(conversa_id):
        raise _somente_leitura()
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    recusa = await enviar.motivo_para_nao_enviar(session, c)
    if recusa is not None:
        raise _recusa_http(recusa)
    motivo = foto.motivo_sem_foto(c)
    if motivo:
        raise _recusa_http(EnvioRecusado(enviar.RECUSA_FOTO_NAO_SUPORTADA, motivo))
    dados = await arquivo.read(foto.MAX_BYTES_UPLOAD + 1)
    try:
        imagem = foto.validar_foto(dados, arquivo.filename, arquivo.content_type)
    except foto.FotoInvalida as e:
        raise HTTPException(
            422,
            detail={"code": enviar.RECUSA_FOTO_INVALIDA, "detail": e.detail, "motivo": e.code},
        ) from e
    try:
        m = await enviar.enviar_foto(
            session,
            c,
            imagem,
            legenda=legenda,
            user=user,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
        )
    except EnvioRecusado as e:
        logger.info(
            "atendimento_foto_recusada", conversa_id=str(c.id), code=e.code, user_id=str(user.id)
        )
        raise _recusa_http(e) from e
    return ResponderOut(mensagem=_mensagem_out(m, conversa=c, nomes={user.id: _nome(user)}))


@router.post("/adspower/aberto", response_model=AdsPowerAbertoOut)
async def adspower_aberto(
    body: AdsPowerAbertoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_quem_le)],
) -> AdsPowerAbertoOut:
    """Registra quem abriu (ou tentou abrir) qual perfil do AdsPower, e quando.

    Na fase de observação quem só lê também registra (`ROTAS_DE_QUEM_LE`): o
    "Abrir na plataforma" abre o perfil no computador dela, e o log não pode
    falhar calado.

    O perfil é RELIDO aqui pela conversa (não vem da tela): o registro diz
    o perfil de verdade daquela loja. Só ids e códigos no registro. Exige a
    mesma permissão do cadastro de Lojas (`painel.ve_lojas`) que o painel
    pede para mostrar o perfil.
    """
    if not painel.ve_lojas(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "resource": "lojas_info", "action": "view"},
        )
    # O id ANTES do rollback abaixo: o `user` foi lido na MESMA sessão (o
    # `get_current_user` usa a do pedido) e o rollback expira tudo — ler
    # `user.id` depois disso é ida ao banco fora do greenlet (MissingGreenlet
    # → 500 em todo clique; produção, 04/10/2026).
    user_id = str(user.id)
    perfil = None
    store_info_id = None
    conversa_id = None
    if body.conversa_id is not None:
        scope = await resolve_team_scope(session, user)
        c = await _conversa_ou_404(session, str(body.conversa_id), scope)
        conversa_id = str(c.id)
        dados = await painel.perfil_adspower(session, c)
        perfil = dados.get("perfil")
        store_info_id = dados.get("store_info_id")
    await session.rollback()
    logger.info(
        "atendimento_adspower_aberto",
        user_id=user_id,
        conversa_id=conversa_id,
        store_info_id=store_info_id,
        perfil=perfil,
        resultado=body.resultado,
        codigo=body.codigo,
    )
    return AdsPowerAbertoOut(registrado=True, perfil=perfil)
