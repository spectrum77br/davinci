"""Cadastros › Redes Sociais — contas por marca e plataforma (15/09/2026).

Recurso de permissão `redes_sociais` (view/edit/delete), separado de
`marcas`: os grids devolvem só um `MarcaRef` (sem o login/e-mail do registro
INPI nem domínios, que ficam atrás da permissão `marcas`). Como na planilha
(aba r.social), fone / usuário(e-mail) / senha das redes são da MARCA
(`sac_*`): esta aba edita essas colunas por PATCH /marca/{marca_id} e revela
a senha da marca por GET /marca/{marca_id}/sac-senha — tudo sob
`redes_sociais:edit`. A conta só guarda e-mail/fone/senha quando DIFEREM
(NULL = herda; os `*_efetivo` do Out já resolvem isso).

Unicidade (índices parciais em models/marca.py): a mesma conta não pode
estar em duas marcas na mesma plataforma (`rede_social_conta_conflict`) e
cada (marca, plataforma) tem no máximo uma linha sem conta
(`rede_social_placeholder_conflict`). Pré-checa pra dar o código certo e
ainda captura IntegrityError (corrida) → 409.

Senha: nunca em listagem, `has_senha`/`has_senha_efetiva`, GET /{id}/senha
sob edit com log (devolve a da conta ou, sem ela, a herdada da marca — com
`origem`).
"""

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission
from app.historico.contexto import identificar_por_id
from app.models import (
    REDES_SOCIAIS_PLATAFORMAS,
    Integration,
    IntegrationPlatform,
    Marca,
    OAuthState,
    RedeSocial,
    RedeSocialToken,
    User,
)
from app.schemas.marcas import (
    ConectarContaIn,
    ConexaoOut,
    ContaExternaOut,
    LojaShopeeOut,
    MarcaRef,
    MarcaSocialPatch,
    RedeSocialCreate,
    RedeSocialOut,
    RedeSocialPatch,
    RedesSociaisGridOut,
    RedesSociaisGridRow,
    SenhaOut,
    ShopeeIniciarIn,
    ShopeeIniciarOut,
)
from app.security.cipher import decrypt, decrypt_json, encrypt, encrypt_json
from app.services.marketing import meta_client, shopee_video, shopee_video_conta

logger = structlog.get_logger()
router = APIRouter(prefix="/api/redes-sociais", tags=["redes_sociais"])

_NAO_NULOS = ("marca_id", "plataforma", "verificacao_status", "ativo")

_view = require_permission("redes_sociais", "view")
_edit = require_permission("redes_sociais", "edit")
_delete = require_permission("redes_sociais", "delete")


def marca_ref(m: Marca) -> MarcaRef:
    out = MarcaRef.model_validate(m)
    out.has_sac_senha = bool(m.sac_senha_enc)
    out.has_logo = bool(m.logo_mime)
    return out


async def _tokens_por_rede(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, RedeSocialToken]:
    """Estado da credencial de cada conta, pra tela mostrar "conectado como
    @x" sem nunca devolver o token. Uma query só (o grid tem N contas)."""
    if not ids:
        return {}
    linhas = (
        await session.execute(
            select(RedeSocialToken).where(RedeSocialToken.rede_social_id.in_(ids))
        )
    ).scalars().all()
    return {t.rede_social_id: t for t in linhas}


def rede_out(
    r: RedeSocial,
    marca: Marca,
    token: RedeSocialToken | None = None,
    lojas: dict[UUID, str] | None = None,
) -> RedeSocialOut:
    out = RedeSocialOut.model_validate(r)
    # Credencial de publicação: só o ESTADO vai pra tela (o token não sai por
    # endpoint nenhum, nem por rota de revelar).
    if token is not None:
        out.has_token = bool(token.token_enc)
        out.token_status = token.status
        out.token_conta_externa = token.external_username or token.external_user_id
        out.token_expires_at = token.token_expires_at
    if r.plataforma == shopee_video.PLATAFORMA_SHOPEE:
        # Na Shopee o blob nasce com o app (partner) ANTES da autorização: só
        # "conectado" depois que a loja autorizou (o user_id volta dela). O
        # partner nunca sai daqui — só o fato de já ter sido digitado.
        out.has_token = shopee_video_conta.autorizada(token)
        out.shopee_app_configurado = shopee_video_conta.tem_partner(
            shopee_video_conta.blob_de(token)
        )
        if r.integration_id is not None:
            out.integration_nome = (lojas or {}).get(r.integration_id)
    out.marca_nome = marca.nome
    out.has_senha = bool(r.senha_enc)
    # Efetivos: o que a conta tem, senão o da marca (planilha: uma credencial
    # por marca, compartilhada pelas redes).
    out.email_efetivo = r.email or marca.sac_email
    out.fone_efetivo = r.fone or marca.sac_fone
    if r.senha_enc:
        out.senha_origem = "conta"
    elif marca.sac_senha_enc:
        out.senha_origem = "marca"
    out.has_senha_efetiva = out.senha_origem is not None
    return out


async def _nomes_das_lojas(session: AsyncSession, redes: list[RedeSocial]) -> dict[UUID, str]:
    """Nome da loja (integração) de cada conta de Shopee Vídeo — uma query."""
    ids = {r.integration_id for r in redes if r.integration_id is not None}
    if not ids:
        return {}
    linhas = (
        await session.execute(
            select(Integration.id, Integration.name).where(Integration.id.in_(ids))
        )
    ).all()
    return {linha[0]: linha[1] for linha in linhas}


async def _valida_loja(
    session: AsyncSession, plataforma: str, integration_id: UUID | None
) -> None:
    """A loja só existe pra Shopee Vídeo, e tem de ser uma integração SHOPEE."""
    if integration_id is None:
        return
    if plataforma != shopee_video.PLATAFORMA_SHOPEE:
        raise HTTPException(422, detail={"code": "loja_so_para_shopee"})
    integ = await session.get(Integration, integration_id)
    if integ is None:
        raise HTTPException(404, detail={"code": "integration_not_found"})
    if integ.platform != IntegrationPlatform.SHOPEE:
        raise HTTPException(422, detail={"code": "loja_nao_e_shopee"})


async def _get_or_404(session: AsyncSession, rede_id: UUID) -> RedeSocial:
    r = (
        await session.execute(select(RedeSocial).where(RedeSocial.id == rede_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail={"code": "rede_social_not_found"})
    return r


async def _marca_or_404(session: AsyncSession, marca_id: UUID) -> Marca:
    m = (await session.execute(select(Marca).where(Marca.id == marca_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})
    return m


async def _checa_conflito(
    session: AsyncSession,
    *,
    marca_id: UUID,
    plataforma: str,
    conta: str | None,
    exceto: UUID | None = None,
) -> None:
    """Mesmas regras dos índices parciais, com código de erro legível."""
    if conta is not None:
        stmt = select(RedeSocial.id).where(
            RedeSocial.plataforma == plataforma,
            func.lower(RedeSocial.conta) == conta.lower(),
        )
        code = "rede_social_conta_conflict"
    else:
        stmt = select(RedeSocial.id).where(
            RedeSocial.marca_id == marca_id,
            RedeSocial.plataforma == plataforma,
            RedeSocial.conta.is_(None),
        )
        code = "rede_social_placeholder_conflict"
    if exceto is not None:
        stmt = stmt.where(RedeSocial.id != exceto)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(409, detail={"code": code})


def _casa_conta(
    contas: list[ContaExternaOut], conta: str | None, *, campo: str, id_campo: str
) -> ContaExternaOut | None:
    """Acha, no que o token enxerga, a linha do @ cadastrado — SÓ no nome igual.

    Nada de "contém" nem de "só tem uma, deve ser essa": as duas heurísticas
    escolhiam sozinhas a conta errada (@charlots casaria com @charlots_oficial,
    e um token colado na linha errada casaria com a única Página que ele vê).
    Publicar na conta errada não tem desfazer, e quem perde é a marca.

    Sem casamento exato o endpoint devolve a LISTA pro operador escolher —
    dois cliques contra um Reel no perfil errado.
    """
    alvo = (conta or "").strip().lower().lstrip("@")
    if not alvo:
        return None
    for c in contas:
        if not getattr(c, id_campo, None):
            continue
        nome = (getattr(c, campo, None) or "").strip().lower().lstrip("@")
        if nome and nome == alvo:
            return c
    return None


def _conflict_code(conta: str | None, e: IntegrityError | None = None) -> str:
    # Uma conta de Shopee Vídeo por loja (índice único parcial da 0383).
    if e is not None and "uq_redes_sociais_integration_id" in str(e.orig):
        return "loja_ja_tem_conta_shopee"
    return "rede_social_conta_conflict" if conta else "rede_social_placeholder_conflict"


def _revela(valor_enc: str | None, *, log_evento: str, **log_kw: str) -> str:
    try:
        senha = decrypt(valor_enc or "")
    except Exception as e:
        logger.error(f"{log_evento}_decrypt_failed", **log_kw)
        raise HTTPException(500, detail={"code": "decrypt_failed"}) from e
    logger.info(log_evento, **log_kw)
    return senha


# ================================================================= linha da marca


@router.patch("/marca/{marca_id}", response_model=MarcaRef)
async def patch_marca_social(
    marca_id: UUID,
    body: MarcaSocialPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> MarcaRef:
    """Colunas da marca que a aba Redes Sociais edita (fone/usuário/senha das
    redes, verificação do Zap, função, tipo, obs) — gate `redes_sociais:edit`."""
    m = await _marca_or_404(session, marca_id)
    data = body.model_dump(exclude_unset=True)
    if "sac_senha" in data:
        pwd = data.pop("sac_senha")
        m.sac_senha_enc = encrypt(pwd) if pwd else None
    if data.get("whatsapp_verificacao_status") is None:
        data.pop("whatsapp_verificacao_status", None)
    for k, v in data.items():
        setattr(m, k, v)
    await session.commit()
    await session.refresh(m)
    return marca_ref(m)


@router.get("/marca/{marca_id}/sac-senha", response_model=SenhaOut)
async def reveal_marca_sac_senha(
    marca_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> SenhaOut:
    m = await _marca_or_404(session, marca_id)
    response.headers["Cache-Control"] = "no-store"
    if not m.sac_senha_enc:
        return SenhaOut(senha="")
    senha = _revela(
        m.sac_senha_enc,
        log_evento="marca_sac_senha_revelada",
        user_id=str(user.id),
        marca_id=str(marca_id),
    )
    return SenhaOut(senha=senha, origem="marca")


# ========================================================================= contas


@router.get("/grid", response_model=RedesSociaisGridOut)
async def redes_sociais_grid(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> RedesSociaisGridOut:
    """Linha = marca (todas, mesmo sem conta), coluna = plataforma."""
    marcas = (await session.execute(select(Marca).order_by(Marca.nome))).scalars().all()
    redes = (
        await session.execute(
            select(RedeSocial).order_by(RedeSocial.plataforma, RedeSocial.conta)
        )
    ).scalars().all()
    by_id = {m.id: m for m in marcas}
    tokens = await _tokens_por_rede(session, [r.id for r in redes])
    lojas = await _nomes_das_lojas(session, list(redes))
    cells_by_marca: dict[UUID, dict[str, list[RedeSocialOut]]] = {
        m.id: {p: [] for p in REDES_SOCIAIS_PLATAFORMAS} for m in marcas
    }
    for r in redes:
        cells = cells_by_marca.get(r.marca_id)
        if cells is None:
            continue
        # Plataforma removida do enum ainda aparece (não some dado da tela).
        cells.setdefault(r.plataforma, []).append(
            rede_out(r, by_id[r.marca_id], tokens.get(r.id), lojas)
        )
    rows = [RedesSociaisGridRow(marca=marca_ref(m), cells=cells_by_marca[m.id]) for m in marcas]
    return RedesSociaisGridOut(plataformas=list(REDES_SOCIAIS_PLATAFORMAS), rows=rows)


@router.get("", response_model=list[RedeSocialOut])
async def list_redes_sociais(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
    marca_id: UUID | None = Query(None),
    plataforma: str | None = Query(None),
    search: str | None = Query(None),
) -> list[RedeSocialOut]:
    stmt = select(RedeSocial, Marca).join(Marca, Marca.id == RedeSocial.marca_id)
    if marca_id is not None:
        stmt = stmt.where(RedeSocial.marca_id == marca_id)
    if plataforma:
        stmt = stmt.where(RedeSocial.plataforma == plataforma.strip().lower())
    if search:
        like = f"%{search.strip().lower().lstrip('@')}%"
        stmt = stmt.where(
            or_(RedeSocial.conta.ilike(like), RedeSocial.email.ilike(like), Marca.nome.ilike(like))
        )
    rows = (
        await session.execute(stmt.order_by(Marca.nome, RedeSocial.plataforma, RedeSocial.conta))
    ).all()
    tokens = await _tokens_por_rede(session, [r.id for r, _ in rows])
    lojas = await _nomes_das_lojas(session, [r for r, _ in rows])
    return [rede_out(r, m, tokens.get(r.id), lojas) for r, m in rows]


@router.get("/lojas-shopee", response_model=list[LojaShopeeOut])
async def lojas_shopee(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> list[LojaShopeeOut]:
    """As lojas Shopee (integrações) pro select da conta de Shopee Vídeo.

    Aqui, e não em /api/integrations, porque quem cadastra rede social pode
    não ter `integracoes:view` — e daqui sai só id e nome, nada de credencial.
    Declarada ANTES de `/{rede_id}`, senão o FastAPI tentaria ler
    "lojas-shopee" como UUID."""
    linhas = (
        await session.execute(
            select(Integration.id, Integration.name, Integration.archived_at)
            .where(Integration.platform == IntegrationPlatform.SHOPEE)
            .order_by(Integration.name)
        )
    ).all()
    return [
        LojaShopeeOut(id=i, nome=n or "", arquivada=a is not None) for i, n, a in linhas
    ]


@router.get("/{rede_id}", response_model=RedeSocialOut)
async def get_rede_social(
    rede_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> RedeSocialOut:
    r = await _get_or_404(session, rede_id)
    m = await _marca_or_404(session, r.marca_id)
    tokens = await _tokens_por_rede(session, [r.id])
    return rede_out(r, m, tokens.get(r.id), await _nomes_das_lojas(session, [r]))


@router.post("", response_model=RedeSocialOut, status_code=status.HTTP_201_CREATED)
async def create_rede_social(
    body: RedeSocialCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> RedeSocialOut:
    m = await _marca_or_404(session, body.marca_id)
    await _checa_conflito(
        session, marca_id=body.marca_id, plataforma=body.plataforma, conta=body.conta
    )
    await _valida_loja(session, body.plataforma, body.integration_id)
    data = body.model_dump(exclude={"senha"})
    r = RedeSocial(senha_enc=encrypt(body.senha) if body.senha else None, **data)
    session.add(r)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": _conflict_code(body.conta, e)}) from e
    await session.refresh(r)
    logger.info(
        "rede_social_created",
        rede_id=str(r.id),
        marca_id=str(r.marca_id),
        plataforma=r.plataforma,
        conta=r.conta,
    )
    return rede_out(r, m, None, await _nomes_das_lojas(session, [r]))


@router.patch("/{rede_id}", response_model=RedeSocialOut)
async def patch_rede_social(
    rede_id: UUID,
    body: RedeSocialPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_edit)],
) -> RedeSocialOut:
    r = await _get_or_404(session, rede_id)
    data = body.model_dump(exclude_unset=True)
    if "senha" in data:
        # Ausente = mantém; ""/null = limpa (volta a herdar da marca); texto = cifra.
        pwd = data.pop("senha")
        r.senha_enc = encrypt(pwd) if pwd else None
    for k in _NAO_NULOS:
        # Colunas NOT NULL: null no body = "não mexe".
        if k in data and data[k] is None:
            data.pop(k)
    if "marca_id" in data:
        await _marca_or_404(session, data["marca_id"])
    novo_marca_id = data.get("marca_id", r.marca_id)
    nova_plataforma = data.get("plataforma", r.plataforma)
    nova_conta = data["conta"] if "conta" in data else r.conta
    nova_loja = data["integration_id"] if "integration_id" in data else r.integration_id
    if nova_plataforma != shopee_video.PLATAFORMA_SHOPEE and "integration_id" not in data:
        # Deixar de ser Shopee solta a loja junto (a coluna é só dela).
        nova_loja = None
        if r.integration_id is not None:
            data["integration_id"] = None
    await _valida_loja(session, nova_plataforma, nova_loja)
    # A LOJA também é identidade na Shopee Vídeo: a autorização é de UMA loja,
    # e o token de outra publicaria o vídeo na loja errada.
    mudou_identidade = (novo_marca_id, nova_plataforma, nova_conta, nova_loja) != (
        r.marca_id,
        r.plataforma,
        r.conta,
        r.integration_id,
    )
    if mudou_identidade:
        await _checa_conflito(
            session,
            marca_id=novo_marca_id,
            plataforma=nova_plataforma,
            conta=nova_conta,
            exceto=r.id,
        )
        # Trocar a marca, a plataforma ou o @ faz a linha apontar pra OUTRA
        # conta — e o token conectado continua sendo o da conta antiga. Deixá-lo
        # ali é o caminho pro robô publicar o vídeo de uma marca no perfil de
        # outra. A credencial cai junto e alguém reconecta conscientemente.
        tok = (
            await session.execute(
                select(RedeSocialToken).where(RedeSocialToken.rede_social_id == r.id)
            )
        ).scalar_one_or_none()
        if tok is not None:
            await session.delete(tok)
            logger.info(
                "rede_social_token_descartado_por_troca",
                rede_id=str(r.id),
                de=f"{r.plataforma}:{r.conta}",
                para=f"{nova_plataforma}:{nova_conta}",
            )
        # Pelo MESMO motivo, o perfil do AdsPower cai junto: ele é o
        # navegador logado na conta ANTIGA. Mantê-lo faria o executor abrir o
        # perfil de uma marca pra publicar o vídeo de outra — só que aqui nem
        # dá erro, o vídeo simplesmente sai no lugar errado.
        if r.adspower_user_id and "adspower_user_id" not in data:
            logger.info(
                "rede_social_adspower_descartado_por_troca",
                rede_id=str(r.id),
                de=f"{r.plataforma}:{r.conta}",
                para=f"{nova_plataforma}:{nova_conta}",
            )
            r.adspower_user_id = None
    for k, v in data.items():
        setattr(r, k, v)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": _conflict_code(nova_conta, e)}) from e
    await session.refresh(r)
    m = await _marca_or_404(session, r.marca_id)
    tokens = await _tokens_por_rede(session, [r.id])
    return rede_out(r, m, tokens.get(r.id), await _nomes_das_lojas(session, [r]))


@router.delete("/{rede_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rede_social(
    rede_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_delete)],
) -> None:
    r = await _get_or_404(session, rede_id)
    await session.delete(r)
    await session.commit()
    logger.info("rede_social_deleted", rede_id=str(rede_id))
    return None


@router.post("/{rede_id}/conectar", response_model=ConexaoOut)
async def conectar_conta(
    rede_id: UUID,
    body: ConectarContaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ConexaoOut:
    """Guarda o token de publicação DESTA conta (cifrado) e descobre sozinho
    o id externo.

    O operador cola na tela o token gerado no Business Suite (System user →
    Generate new token) — é assim que a credencial entra no sistema sem
    passar por chat, e-mail ou log. O backend pergunta à Meta o que o token
    enxerga (Páginas + conta do Instagram ligada a cada uma; ou, na trilha
    Instagram Login, a própria conta), casa com o @ cadastrado e grava.
    """
    r = await _get_or_404(session, rede_id)
    if r.plataforma == shopee_video.PLATAFORMA_SHOPEE:
        # A Shopee não usa token colado: é o app de vídeo + o login da loja
        # (POST /{id}/shopee/iniciar). Colar aqui gravaria lixo no blob dela.
        raise HTTPException(422, detail={"code": "shopee_usa_autorizacao"})
    contas: list[ContaExternaOut] = []
    escolhido = (body.external_user_id or "").strip() or None
    externo_id: str | None = None
    externo_nome: str | None = None
    provedor = meta_client.PROVEDOR_FACEBOOK
    recusa: meta_client.MetaError | None = None
    try:
        achadas = await meta_client.contas_do_token(body.access_token)
    except meta_client.MetaError as e:
        # NÃO é o fim da linha quando a conta é Instagram. O token da trilha
        # "Instagram API with Instagram Login" (conta SEM Página) é emitido por
        # graph.instagram.com e o graph.facebook.com o recusa com code 190 —
        # que parece token inválido e é só host errado. A recusa fica guardada
        # e só vira 422 se a trilha direta também não reconhecer o token.
        if r.plataforma != meta_client.PLATAFORMA_INSTAGRAM:
            raise HTTPException(
                422, detail={"code": "token_recusado_pela_meta", "erro": str(e)[:300]}
            ) from e
        recusa, achadas = e, []
    # O `page_token` NÃO pode entrar no que volta pra tela: ele publica na
    # Página. Sai do dict antes de virar schema e fica só neste dicionário,
    # indexado pelos DOIS ids (a linha do Instagram é escolhida pelo
    # ig_user_id, mas o token é da Página que carrega aquela conta).
    tokens_de_pagina: dict[str, str] = {}
    for c in achadas:
        pt = c.pop("page_token", None)
        if not pt:
            continue
        for chave in (c.get("page_id"), c.get("ig_user_id")):
            if chave:
                tokens_de_pagina[str(chave)] = pt
    contas = [ContaExternaOut(**c) for c in achadas]

    if r.plataforma == meta_client.PLATAFORMA_INSTAGRAM:
        if not contas:
            # Sem Página: trilha "Instagram Login" — o token é da conta, e daí
            # em diante TUDO fala com graph.instagram.com.
            direta = await meta_client.conta_instagram_direta(body.access_token)
            if direta:
                contas = [ContaExternaOut(**direta)]
                provedor = meta_client.PROVEDOR_INSTAGRAM
        if not contas and recusa is not None:
            # As duas trilhas recusaram: agora sim é token ruim.
            raise HTTPException(
                422, detail={"code": "token_recusado_pela_meta", "erro": str(recusa)[:300]}
            ) from recusa
        alvo = _casa_conta(contas, r.conta, campo="ig_username", id_campo="ig_user_id")
        ids_validos = {c.ig_user_id for c in contas if c.ig_user_id}
        externo_id = alvo.ig_user_id if alvo else None
        externo_nome = alvo.ig_username if alvo else None
    else:
        alvo = _casa_conta(contas, r.conta, campo="page_nome", id_campo="page_id")
        ids_validos = {c.page_id for c in contas if c.page_id}
        externo_id = alvo.page_id if alvo else None
        externo_nome = alvo.page_nome if alvo else None

    if escolhido:
        # O operador escolheu na tela. Só vale o que ESTE token enxerga: sem
        # a conferência, um id digitado (ou vindo de outra marca) seria gravado
        # e o robô publicaria em conta alheia.
        if escolhido not in ids_validos:
            raise HTTPException(
                422, detail={"code": "conta_nao_pertence_ao_token"}
            )
        externo_id = escolhido
        for c in contas:
            if escolhido in (c.ig_user_id, c.page_id):
                externo_nome = c.ig_username or c.page_nome
                break

    if not externo_id:
        # Não dá pra adivinhar: a tela mostra o que o token enxerga e o
        # operador escolhe (manda external_user_id na segunda tentativa).
        return ConexaoOut(ok=False, contas=contas)

    tok = (
        await session.execute(
            select(RedeSocialToken).where(RedeSocialToken.rede_social_id == r.id)
        )
    ).scalar_one_or_none()
    if tok is None:
        tok = RedeSocialToken(rede_social_id=r.id)
        session.add(tok)
    agora = datetime.now(UTC)
    # Dois segredos no mesmo blob cifrado: o token colado (serve pra
    # descobrir/reconciliar) e o token da Página (é o que a doc de Content
    # Publishing pede pra publicar de fato). Só existe na trilha do Facebook.
    segredos: dict[str, str] = {"access_token": body.access_token}
    page_token = tokens_de_pagina.get(str(externo_id))
    if page_token:
        segredos["page_access_token"] = page_token
    tok.token_enc = encrypt_json(segredos)
    tok.external_user_id = externo_id
    tok.external_username = externo_nome
    tok.provedor = provedor
    # Validade EM CLARO (só a data) pro cron de renovação achar o que vence sem
    # decifrar nada. None = a Meta não disse (token permanente de System User,
    # ou app sem permissão de inspecionar) — aí quem avisa é a primeira recusa.
    # Só na trilha do Facebook: `debug_token` mora no graph.facebook.com e não
    # inspeciona token emitido pelo graph.instagram.com — chamar ali seria uma
    # ida garantida ao erro, e o log sujo esconderia problema de verdade.
    tok.token_expires_at = (
        await meta_client.validade_do_token(body.access_token)
        if provedor == meta_client.PROVEDOR_FACEBOOK
        else None
    )
    tok.status = "ok"
    tok.last_error = None
    tok.last_ok_at = agora
    tok.connected_at = agora
    tok.connected_by = user.id
    await session.commit()
    await session.refresh(tok)
    logger.info(
        "rede_social_token_conectado",
        rede_id=str(r.id),
        plataforma=r.plataforma,
        externo=externo_id,
        user_id=str(user.id),
    )
    return ConexaoOut(
        ok=True,
        external_user_id=externo_id,
        external_username=externo_nome,
        token_expires_at=tok.token_expires_at,
        contas=contas,
    )


@router.delete("/{rede_id}/conectar", status_code=status.HTTP_204_NO_CONTENT)
async def desconectar_conta(
    rede_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> None:
    """Tira a credencial da conta (o robô para de publicar nela na hora)."""
    r = await _get_or_404(session, rede_id)
    tok = (
        await session.execute(
            select(RedeSocialToken).where(RedeSocialToken.rede_social_id == r.id)
        )
    ).scalar_one_or_none()
    if tok is not None:
        await session.delete(tok)
        await session.commit()
    logger.info("rede_social_token_removido", rede_id=str(rede_id), user_id=str(user.id))
    return None


@router.get("/{rede_id}/senha", response_model=SenhaOut)
async def reveal_rede_social_senha(
    rede_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> SenhaOut:
    """Senha EFETIVA da conta sob demanda (só edit): a própria ou, sem ela, a
    herdada da marca (`origem`). "" se não há nenhuma. Sem cache, com log."""
    r = await _get_or_404(session, rede_id)
    m = await _marca_or_404(session, r.marca_id)
    response.headers["Cache-Control"] = "no-store"
    if r.senha_enc:
        senha = _revela(
            r.senha_enc,
            log_evento="rede_social_senha_revelada",
            user_id=str(user.id),
            rede_id=str(rede_id),
        )
        return SenhaOut(senha=senha, origem="conta")
    if m.sac_senha_enc:
        senha = _revela(
            m.sac_senha_enc,
            log_evento="marca_sac_senha_revelada",
            user_id=str(user.id),
            marca_id=str(m.id),
            via_rede_id=str(rede_id),
        )
        return SenhaOut(senha=senha, origem="marca")
    return SenhaOut(senha="")


# ================================================================ Shopee Vídeo
# Autorização do app de VÍDEO da Shopee ("DaVinci Videos", Shopee Video
# Management) para a loja da conta (Eduardo, 08/10/2026). Molde do OAuth da
# Shopee das integrações (routers/integrations.py): o `state` anda no PATH do
# retorno, porque a Shopee acrescenta `?code=&shop_id=` e não preserva query
# que já existia. Nada aqui toca na integração da loja: o app é OUTRO, com
# partner próprio POR CONTA, e o token dele vive em `redes_sociais_tokens`.

SHOPEE_STATE_TTL_MIN = 10
_PREFIXO_STATE = "rede_social:"
CALLBACK_SHOPEE = "/api/redes-sociais/shopee/callback"


def _shopee_video_base() -> str:
    """A ORIGEM pública do retorno (sem barra no fim). Tem de estar no
    domínio cadastrado no app de vídeo (app.hadken.com)."""
    s = get_settings()
    base = (s.shopee_video_redirect_base or "").strip().rstrip("/")
    if base:
        return base
    if s.shopee_redirect_uri:
        u = urlsplit(s.shopee_redirect_uri)
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    return (s.app_url or "").rstrip("/")


def _shop_id_da_loja(integ: Integration) -> int | None:
    """O shop_id da integração (o que a loja já usa pra pedidos e estoque).
    Só LEITURA do blob da integração, e só deste número."""
    try:
        creds = decrypt_json(integ.credentials) if integ.credentials else {}
    except Exception:  # noqa: BLE001
        return None
    try:
        n = int(str(creds.get("shop_id") or "").strip())
    except ValueError:
        return None
    return n if n > 0 else None


@router.post("/{rede_id}/shopee/iniciar", response_model=ShopeeIniciarOut)
async def shopee_iniciar(
    rede_id: UUID,
    body: ShopeeIniciarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ShopeeIniciarOut:
    """Grava o app de vídeo (se veio) e devolve o link de autorização.

    O partner_id/partner_key vão no CORPO, são cifrados e nunca voltam. Sem
    eles no corpo, reautoriza com o app já salvo. O link leva só partner_id,
    o endereço de volta e o state — a chave nunca sai do servidor."""
    r = await _get_or_404(session, rede_id)
    if r.plataforma != shopee_video.PLATAFORMA_SHOPEE:
        raise HTTPException(422, detail={"code": "conta_nao_e_shopee"})
    if r.integration_id is None:
        raise HTTPException(422, detail={"code": "conta_sem_loja"})
    if (body.partner_id is None) != (body.partner_key is None):
        raise HTTPException(422, detail={"code": "partner_incompleto"})
    integ = await session.get(Integration, r.integration_id)
    if integ is None or integ.platform != IntegrationPlatform.SHOPEE:
        raise HTTPException(422, detail={"code": "loja_nao_e_shopee"})
    if _shop_id_da_loja(integ) is None:
        # Sem o shop_id da loja não há como conferir o retorno — e aceitar
        # qualquer loja é o caminho pro vídeo sair na loja errada.
        raise HTTPException(422, detail={"code": "loja_sem_shop_id"})
    base = _shopee_video_base()
    if not base.startswith("https://") and not base.startswith("http://"):
        raise HTTPException(400, detail={"code": "missing_shopee_video_redirect"})

    if body.partner_id is not None and body.partner_key is not None:
        tok = await shopee_video_conta.salvar_partner(
            session, r, partner_id=body.partner_id, partner_key=body.partner_key, user_id=user.id
        )
    else:
        tok = (
            await session.execute(
                select(RedeSocialToken).where(RedeSocialToken.rede_social_id == r.id)
            )
        ).scalar_one_or_none()
    blob = shopee_video_conta.blob_de(tok)
    if not shopee_video_conta.tem_partner(blob):
        raise HTTPException(422, detail={"code": "conta_sem_app_shopee"})

    state = token_urlsafe(32)
    session.add(
        OAuthState(
            state=state,
            platform=IntegrationPlatform.SHOPEE,
            store_id=None,
            user_id=user.id,
            # O prefixo separa do OAuth das INTEGRAÇÕES (mesma tabela, mesma
            # plataforma): o callback de lá não acha integração com este
            # valor, e o daqui recusa state que não tenha o prefixo.
            code_verifier=f"{_PREFIXO_STATE}{r.id}",
            expires_at=datetime.now(UTC) + timedelta(minutes=SHOPEE_STATE_TTL_MIN),
        )
    )
    await session.commit()
    url = shopee_video.link_autorizacao(
        int(blob["partner_id"]), f"{base}{CALLBACK_SHOPEE}/{state}", state
    )
    logger.info("shopee_video_autorizacao_iniciada", rede_id=str(r.id), user_id=str(user.id))
    return ShopeeIniciarOut(url=url)


def _volta(resultado: str, code: str | None = None) -> RedirectResponse:
    q = {"shopee": resultado}
    if code:
        q["code"] = code
    return RedirectResponse(
        f"{get_settings().app_url.rstrip('/')}/redes-sociais?{urlencode(q)}", status_code=302
    )


@router.get("/shopee/callback/{state}")
async def shopee_callback(
    state: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    shop_id: Annotated[int | None, Query()] = None,
    main_account_id: Annotated[int | None, Query()] = None,
) -> RedirectResponse:
    """Retorno da Shopee: troca o `code` (vale 1 vez, 10 min) e grava.

    Sem login (é o navegador voltando da Shopee) — quem garante é o state:
    existe, não foi usado, não venceu, e é DESTE fluxo. A loja que voltou
    tem de ser a da integração ligada à conta. Erro volta pra tela como
    `?shopee=erro&code=…`, nunca como página de erro crua."""
    row = (
        await session.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalar_one_or_none()
    if (
        row is None
        or row.platform != IntegrationPlatform.SHOPEE
        or not (row.code_verifier or "").startswith(_PREFIXO_STATE)
    ):
        return _volta("erro", "state_not_found")
    if row.consumed_at is not None:
        return _volta("erro", "state_consumed")
    if row.expires_at < datetime.now(UTC):
        return _volta("erro", "state_expired")
    await identificar_por_id(session, row.user_id)
    # Consumido JÁ: o code vale uma vez, e um segundo clique no mesmo link
    # não pode tentar trocar de novo.
    row.consumed_at = datetime.now(UTC)
    await session.commit()
    if not code:
        return _volta("erro", "sem_code")
    try:
        rede_id = UUID(row.code_verifier[len(_PREFIXO_STATE):])
    except ValueError:
        return _volta("erro", "state_not_found")
    r = await session.get(RedeSocial, rede_id)
    if r is None or r.plataforma != shopee_video.PLATAFORMA_SHOPEE or r.integration_id is None:
        return _volta("erro", "conta_sem_loja")
    integ = await session.get(Integration, r.integration_id)
    esperado = _shop_id_da_loja(integ) if integ is not None else None
    if esperado is None:
        return _volta("erro", "loja_sem_shop_id")
    if shop_id is not None and int(shop_id) != esperado:
        logger.warning("shopee_video_loja_errada", rede_id=str(r.id))
        return _volta("erro", "loja_errada")

    tok = (
        await session.execute(
            select(RedeSocialToken)
            .where(RedeSocialToken.rede_social_id == r.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    blob = shopee_video_conta.blob_de(tok)
    if tok is None or not shopee_video_conta.tem_partner(blob):
        return _volta("erro", "conta_sem_app_shopee")
    cliente = shopee_video.ClienteShopeeVideo(int(blob["partner_id"]), str(blob["partner_key"]))
    try:
        resposta = await cliente.trocar_code(
            code,
            shop_id=shop_id if shop_id is not None else None,
            main_account_id=main_account_id if shop_id is None else None,
        )
        uid, sid = shopee_video_conta.escolher_user_id(resposta, esperado)
        shopee_video_conta.gravar_autorizacao(
            tok,
            resposta=resposta,
            user_id=uid,
            shop_id=sid,
            nome_loja=integ.name if integ is not None else None,
        )
    except shopee_video.ShopeeVideoError as e:
        await session.rollback()
        # `rede_id` (o valor), nunca `r.id`: depois do rollback a linha está
        # expirada e ler o atributo seria I/O fora de hora na sessão async.
        logger.warning(
            "shopee_video_troca_falhou",
            rede_id=str(rede_id),
            code=e.code,
            request_id=e.request_id,
        )
        return _volta("erro", "troca_recusada")
    except shopee_video_conta.ContaShopeeError as e:
        await session.rollback()
        logger.warning("shopee_video_autorizacao_recusada", rede_id=str(rede_id), code=e.code)
        return _volta("erro", e.code)
    tok.connected_by = row.user_id
    await session.commit()
    logger.info("shopee_video_autorizada", rede_id=str(rede_id))
    return _volta("ok")
