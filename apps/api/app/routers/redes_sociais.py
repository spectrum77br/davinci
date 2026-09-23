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

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission
from app.models import (
    REDES_SOCIAIS_PLATAFORMAS,
    Marca,
    RedeSocial,
    RedeSocialToken,
    User,
)
from app.schemas.marcas import (
    ConectarContaIn,
    ConexaoOut,
    ContaExternaOut,
    MarcaRef,
    MarcaSocialPatch,
    RedeSocialCreate,
    RedeSocialOut,
    RedeSocialPatch,
    RedesSociaisGridOut,
    RedesSociaisGridRow,
    SenhaOut,
)
from app.security.cipher import decrypt, encrypt, encrypt_json
from app.services.marketing import meta_client

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
    r: RedeSocial, marca: Marca, token: RedeSocialToken | None = None
) -> RedeSocialOut:
    out = RedeSocialOut.model_validate(r)
    # Credencial de publicação: só o ESTADO vai pra tela (o token não sai por
    # endpoint nenhum, nem por rota de revelar).
    if token is not None:
        out.has_token = bool(token.token_enc)
        out.token_status = token.status
        out.token_conta_externa = token.external_username or token.external_user_id
        out.token_expires_at = token.token_expires_at
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


def _conflict_code(conta: str | None) -> str:
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
    cells_by_marca: dict[UUID, dict[str, list[RedeSocialOut]]] = {
        m.id: {p: [] for p in REDES_SOCIAIS_PLATAFORMAS} for m in marcas
    }
    for r in redes:
        cells = cells_by_marca.get(r.marca_id)
        if cells is None:
            continue
        # Plataforma removida do enum ainda aparece (não some dado da tela).
        cells.setdefault(r.plataforma, []).append(
            rede_out(r, by_id[r.marca_id], tokens.get(r.id))
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
    return [rede_out(r, m, tokens.get(r.id)) for r, m in rows]


@router.get("/{rede_id}", response_model=RedeSocialOut)
async def get_rede_social(
    rede_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_view)],
) -> RedeSocialOut:
    r = await _get_or_404(session, rede_id)
    m = await _marca_or_404(session, r.marca_id)
    tokens = await _tokens_por_rede(session, [r.id])
    return rede_out(r, m, tokens.get(r.id))


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
    data = body.model_dump(exclude={"senha"})
    r = RedeSocial(senha_enc=encrypt(body.senha) if body.senha else None, **data)
    session.add(r)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(409, detail={"code": _conflict_code(body.conta)}) from e
    await session.refresh(r)
    logger.info(
        "rede_social_created",
        rede_id=str(r.id),
        marca_id=str(r.marca_id),
        plataforma=r.plataforma,
        conta=r.conta,
    )
    return rede_out(r, m)


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
    mudou_identidade = (novo_marca_id, nova_plataforma, nova_conta) != (
        r.marca_id,
        r.plataforma,
        r.conta,
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
        raise HTTPException(409, detail={"code": _conflict_code(nova_conta)}) from e
    await session.refresh(r)
    m = await _marca_or_404(session, r.marca_id)
    tokens = await _tokens_por_rede(session, [r.id])
    return rede_out(r, m, tokens.get(r.id))


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
