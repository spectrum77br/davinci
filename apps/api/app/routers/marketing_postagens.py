"""Marketing › Criativos — agenda e outbox da postagem automática (15/09/2026).

Eduardo, 15/09/2026: "um robô que fará a postagem desses vídeos do criativo
automaticamente; também teremos a opção de agendar uma data para a postagem
automática do criativo". Este arquivo é só a BORDA desse robô: valida o que dá
pra validar na hora do clique, grava a linha e mostra o que aconteceu. Quem
fala com a Meta é o worker (services/marketing/meta_client.py), no servidor —
subir um Reel leva minutos e nenhum request pode ficar segurando isso.

Toda a REGRA mora em `services/marketing/postagens.py`, nunca aqui: o cron que
promove um agendamento aplica exatamente as mesmas guardas, e regra duplicada
em router é regra que diverge. O router traduz o código do `RoboError` em HTTP
e mais nada.

Permissão: o recurso já existente `marketing_criativos` (view/edit) — a tela é
a mesma, a permissão é a mesma. A trava que importa não é de permissão: é o
`aprovado=True` do criativo (o V do admin é o que libera o vídeo pro mundo). O
escopo de EQUIPE vale igual, com `_user_equipes`/`_ensure_equipe` importados de
marketing_creatives.py — se a regra mudar lá, muda aqui junto.

Regra de ouro: **token de conta NUNCA sai daqui**. A conta aparece com
`has_token` e o motivo da recusa, e não existe endpoint de revelar — diferente
da senha da marca (routers/marcas.py), que é senha de gente; esta é credencial
de máquina, publica sozinha.
"""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    RedeSocial,
    RedeSocialToken,
    User,
)
from app.models.marketing_postagem import STATUS_AGENDADO, STATUS_PENDENTE
from app.routers.marketing_creatives import _ensure_equipe, _user_equipes
from app.schemas.marketing_postagens import (
    ContaParaPostarOut,
    PostagemCreate,
    PostagemOut,
    PostagemPatch,
)
from app.services.marketing import postagens as svc

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/postagens", tags=["marketing_postagens"])

_view = require_permission("marketing_criativos", "view")
_edit = require_permission("marketing_criativos", "edit")

# Código do serviço → HTTP. O padrão é 422 ("o operador conserta no modal":
# criativo sem V, conta sem token, plataforma ainda não suportada). Vira 409 só
# o que é conflito de ESTADO — já tem postagem em voo, o vídeo já saiu por
# outra marca, a conta bateu o teto do dia — e 404 o que sumiu. Dicionário (e
# não `if/elif`) pra o serviço ganhar código novo sem a borda precisar saber de
# cada um; o default 422 já é uma resposta decente pra qualquer recusa.
_HTTP_POR_CODE: dict[str, int] = {
    "arquivo_sumiu": 404,
    "video_ja_usado_em_outra_marca": 409,
    "postagem_em_voo": 409,
    "limite_diario": 409,
    "intervalo_curto": 409,
    "postagem_nao_cancelavel": 409,
    "postagem_nao_falhou": 409,
    "postagem_ja_publicada": 409,
    "postagem_em_revisao": 409,
    "postagem_precisa_conferir_na_meta": 409,
    "conta_de_outra_marca": 409,
    "criativo_sem_marca": 409,
}


def _erro(e: svc.RoboError) -> HTTPException:
    return HTTPException(_HTTP_POR_CODE.get(e.code, 422), detail={"code": e.code})


def _motivo_da_conta(rede: RedeSocial, token: RedeSocialToken | None) -> str | None:
    """Motivo APENAS sobre a conta, pro modal que ainda não escolheu o vídeo.

    É a cauda de `postagens.pode_publicar_local` — aquela começa pelo criativo
    e pelo arquivo, então sem eles devolveria "criativo_nao_aprovado" pra toda
    conta. Os códigos são os mesmos de propósito (o front tem uma tradução só).
    Quando a chamada traz `creative_id`+`file_id`, este atalho nem roda: vale o
    veredito do serviço, o MESMO que o publicador aplica.
    """
    if not rede.ativo:
        return "conta_inativa"
    if token is None or not token.token_enc or token.status == "revogado":
        return "conta_sem_token"
    if (rede.plataforma or "") not in svc.PLATAFORMAS_SUPORTADAS:
        return "plataforma_nao_suportada"
    return None


def _out(
    p: MarketingPostagem,
    *,
    file_name: str | None = None,
    creative: MarketingCreative | None = None,
    marca_nome: str | None = None,
) -> PostagemOut:
    out = PostagemOut.model_validate(p)
    out.file_name = file_name
    # Booleano, nunca o id: o `container_id` é identificador na Meta e não
    # tem por que sair daqui — a tela só precisa saber que existe algo pra
    # conferir antes de republicar.
    out.tem_container = bool(p.container_id)
    # `marca_nome` cai no texto livre `creative.marca` quando o backfill da
    # 0279 não achou o slug — a tela precisa escrever ALGUMA marca na pill.
    out.marca_nome = marca_nome or (creative.marca if creative is not None else None)
    return out


async def _creative_or_404(session: AsyncSession, creative_id: UUID) -> MarketingCreative:
    row = (
        await session.execute(
            select(MarketingCreative).where(MarketingCreative.id == creative_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "creative_not_found"})
    return row


async def _postagem_or_404(
    session: AsyncSession, postagem_id: UUID, user: User
) -> tuple[MarketingPostagem, MarketingCreative]:
    """A postagem + o criativo dono dela, com a equipe já conferida: a postagem
    não tem equipe própria — quem manda é a linha do criativo."""
    p = (
        await session.execute(
            select(MarketingPostagem).where(MarketingPostagem.id == postagem_id)
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "postagem_not_found"})
    creative = await _creative_or_404(session, p.creative_id)
    _ensure_equipe(user, creative)
    return p, creative


async def _out_uma(
    session: AsyncSession, p: MarketingPostagem, creative: MarketingCreative
) -> PostagemOut:
    """PostagemOut de UMA linha, com os dois campos que vêm de fora da tabela."""
    file_name = (
        await session.execute(
            select(MarketingCreativeFile.file_name).where(
                MarketingCreativeFile.id == p.file_id
            )
        )
    ).scalar_one_or_none()
    marca_nome = None
    if creative.marca_id is not None:
        marca_nome = (
            await session.execute(select(Marca.nome).where(Marca.id == creative.marca_id))
        ).scalar_one_or_none()
    return _out(p, file_name=file_name, creative=creative, marca_nome=marca_nome)


@router.get("", response_model=list[PostagemOut])
async def list_postagens(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    creative_id: UUID | None = Query(None),
    status: str | None = Query(None),
    marca_id: UUID | None = Query(None),
) -> list[PostagemOut]:
    """Agenda + histórico pra coluna "Publicação" do grid de Criativos.

    A equipe é filtrada em Python, como `list_creatives` faz: a lista de
    equipes do usuário é curta e a comparação é case-insensitive — não vale
    empurrar isso pro SQL só pra divergir da aba.
    """
    stmt = (
        select(MarketingPostagem, MarketingCreative, MarketingCreativeFile.file_name, Marca.nome)
        .join(MarketingCreative, MarketingCreative.id == MarketingPostagem.creative_id)
        # OUTER nos dois: o arquivo pode ter sido trocado e a marca, apagada —
        # o histórico da postagem sobrevive a ambos (FK SET NULL).
        .outerjoin(MarketingCreativeFile, MarketingCreativeFile.id == MarketingPostagem.file_id)
        .outerjoin(Marca, Marca.id == MarketingCreative.marca_id)
        .order_by(MarketingPostagem.created_at.desc())
    )
    if creative_id is not None:
        stmt = stmt.where(MarketingPostagem.creative_id == creative_id)
    if status:
        stmt = stmt.where(MarketingPostagem.status == status.strip().lower())
    if marca_id is not None:
        stmt = stmt.where(MarketingCreative.marca_id == marca_id)
    rows = (await session.execute(stmt)).all()
    allowed = _user_equipes(user)
    if allowed is not None:
        rows = [r for r in rows if (r[1].equipe or "").strip().lower() in allowed]
    return [_out(p, file_name=fn, creative=c, marca_nome=mn) for p, c, fn, mn in rows]


@router.get("/contas", response_model=list[ContaParaPostarOut])
async def list_contas_para_postar(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    marca_id: Annotated[UUID, Query()],
    creative_id: UUID | None = Query(None),
    file_id: UUID | None = Query(None),
) -> list[ContaParaPostarOut]:
    """Contas da marca pro modal, cada uma com `pode_postar` e, quando não dá,
    o `motivo` (sem token / conta inativa / plataforma ainda não suportada).

    Com `creative_id` E `file_id` o veredito é o do serviço — a MESMA função
    que o publicador roda antes de subir o vídeo, incluindo o que depende do
    banco (vídeo já usado em outra marca, postagem em voo, teto do dia). Assim
    o checkbox desabilitado na tela e a recusa do robô nunca discordam. Sem
    eles sobra o veredito só da conta (`_motivo_da_conta`).

    Sob `marketing_criativos:view` de propósito: quem monta o post não precisa
    da permissão `redes_sociais` (que dá acesso a e-mail/senha das contas) —
    por isso daqui sai o mínimo, e token nenhum.
    """
    m = (await session.execute(select(Marca).where(Marca.id == marca_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "marca_not_found"})

    creative: MarketingCreative | None = None
    arquivo: MarketingCreativeFile | None = None
    if creative_id is not None and file_id is not None:
        creative = await _creative_or_404(session, creative_id)
        _ensure_equipe(user, creative)
        arquivo = next((f for f in creative.files if f.id == file_id), None)
        if arquivo is None:
            raise HTTPException(404, detail={"code": "file_not_found"})

    redes = (
        (
            await session.execute(
                select(RedeSocial)
                .where(RedeSocial.marca_id == marca_id)
                .order_by(RedeSocial.plataforma, RedeSocial.conta)
            )
        )
        .scalars()
        .all()
    )
    tokens = await svc.tokens_por_rede(session, [r.id for r in redes])

    out: list[ContaParaPostarOut] = []
    for r in redes:
        tok = tokens.get(r.id)
        if arquivo is not None:
            # A tela pergunta pelas duas situações: o que impede AGORA (clique
            # do operador) e o que impediria o robô (conta sem postagem_auto).
            motivo = await svc.pode_publicar(creative, arquivo, r, tok, session=session)
        else:
            motivo = _motivo_da_conta(r, tok)
        out.append(
            ContaParaPostarOut(
                rede_social_id=r.id,
                plataforma=r.plataforma,
                conta=r.conta,
                ativo=r.ativo,
                has_token=tok is not None and tok.token_enc is not None,
                postagem_auto=bool(r.postagem_auto),
                postagem_max_dia=r.postagem_max_dia,
                postagem_intervalo_min=r.postagem_intervalo_min,
                pode_postar=motivo is None,
                motivo=motivo,
            )
        )
    return out


@router.post("", response_model=list[PostagemOut], status_code=201)
async def criar_postagens(
    body: PostagemCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> list[PostagemOut]:
    """Agenda UMA postagem por conta marcada — ou põe na fila do próximo tick,
    quando `agendado_para` vem vazio ("publicar agora").

    `agendado_para` chega como ISO: com offset a borda respeita; NAIVE é
    Brasília, porque é isso que o operador digitou no `datetime-local`. Quem
    decide isso é o `para_utc` do serviço, um lugar só (mesmo princípio de
    services/marketing/scheduling.py).

    O resto — aprovado, arquivo no disco, token da conta, hash já usado em
    outra marca, postagem em voo, teto diário, espaçamento — é do `agendar`,
    que recusa a operação INTEIRA no primeiro motivo: o operador marcou N
    contas esperando as N.
    """
    creative = await _creative_or_404(session, body.creative_id)
    _ensure_equipe(user, creative)
    arquivo = next((f for f in creative.files if f.id == body.file_id), None)
    if arquivo is None:
        raise HTTPException(404, detail={"code": "file_not_found"})

    # Na ORDEM que o modal mandou (o schema já tirou repetidos): a primeira
    # conta recusada é a que vira o erro, e ela precisa ser previsível.
    por_id = {
        r.id: r
        for r in (
            await session.execute(
                select(RedeSocial).where(RedeSocial.id.in_(body.rede_social_ids))
            )
        )
        .scalars()
        .all()
    }
    redes: list[RedeSocial] = []
    for rid in body.rede_social_ids:
        r = por_id.get(rid)
        if r is None:
            raise HTTPException(404, detail={"code": "rede_social_not_found"})
        redes.append(r)

    try:
        criadas = await svc.agendar(
            session,
            creative=creative,
            file=arquivo,
            redes=redes,
            legenda=body.legenda,
            agendado_para=body.agendado_para,
            opcoes=body.opcoes,
            user_id=user.id,
        )
    except svc.RoboError as e:
        raise _erro(e) from e

    marca_nome = None
    if creative.marca_id is not None:
        marca_nome = (
            await session.execute(select(Marca.nome).where(Marca.id == creative.marca_id))
        ).scalar_one_or_none()
    logger.info(
        "marketing_postagem_criada",
        creative_id=str(creative.id),
        file_id=str(arquivo.id),
        contas=len(criadas),
        agendada=body.agendado_para is not None,
        user_id=str(user.id),
    )
    return [
        _out(p, file_name=arquivo.file_name, creative=creative, marca_nome=marca_nome)
        for p in criadas
    ]


@router.get("/{postagem_id}", response_model=PostagemOut)
async def get_postagem(
    postagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> PostagemOut:
    p, creative = await _postagem_or_404(session, postagem_id, user)
    return await _out_uma(session, p, creative)


@router.patch("/{postagem_id}", response_model=PostagemOut)
async def patch_postagem(
    postagem_id: UUID,
    body: PostagemPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> PostagemOut:
    """Legenda/horário ENQUANTO está `agendado`.

    Depois disso a linha já é do publicador: editar um `pendente` seria corrida
    com o worker, e um `publicado` não tem o que editar — o post está no ar e a
    legenda de lá não é mais nossa.
    """
    p, creative = await _postagem_or_404(session, postagem_id, user)
    if p.status != STATUS_AGENDADO:
        raise HTTPException(409, detail={"code": "postagem_nao_agendada"})
    data = body.model_dump(exclude_unset=True)
    if "legenda" in data:
        p.legenda = data["legenda"]
    if "agendado_para" in data:
        quando = svc.para_utc(data["agendado_para"])
        # Remarcar re-checa as guardas do NOVO horário (teto diário e
        # espaçamento da conta mudam junto com a hora); `excluir_id` tira a
        # própria linha da conta, senão ela esbarraria em si mesma.
        motivo = await svc.pode_publicar(
            creative,
            next((f for f in creative.files if f.id == p.file_id), None),
            await session.get(RedeSocial, p.rede_social_id) if p.rede_social_id else None,
            (await svc.tokens_por_rede(session, [p.rede_social_id])).get(p.rede_social_id)
            if p.rede_social_id
            else None,
            session=session,
            quando=quando,
            excluir_id=p.id,
        )
        if motivo:
            raise _erro(svc.RoboError(motivo))
        p.agendado_para = quando
        # null = "publicar no próximo tick", mesma semântica do POST. Sem
        # trocar o status a linha ficaria `agendado` sem hora, e o
        # `promover_agendadas` (que filtra por `agendado_para IS NOT NULL`)
        # nunca mais olharia pra ela.
        p.status = STATUS_PENDENTE if quando is None else STATUS_AGENDADO
    await session.commit()
    await session.refresh(p)  # updated_at é onupdate no SQL
    return await _out_uma(session, p, creative)


@router.delete("/{postagem_id}", response_model=PostagemOut)
async def cancelar_postagem(
    postagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> PostagemOut:
    """DELETE aqui é CANCELAR, não apagar: a linha vira `cancelado` e fica.

    Ela é o histórico ("por que esse vídeo nunca saiu?") e é o índice parcial
    `uq_marketing_postagem_em_voo` que impede o post duplicado — sair do estado
    em voo é justamente o que libera a conta pra um novo agendamento. Apagar de
    verdade também jogaria fora o `container_id` que a reconciliação usa quando
    a Meta já aceitou o upload.
    """
    p, creative = await _postagem_or_404(session, postagem_id, user)
    try:
        await svc.cancelar(session, p)
    except svc.RoboError as e:
        raise _erro(e) from e
    await session.refresh(p)
    logger.info("marketing_postagem_cancelada", postagem_id=str(p.id), user_id=str(user.id))
    return await _out_uma(session, p, creative)


@router.post("/{postagem_id}/retentar", response_model=PostagemOut)
async def retentar_postagem(
    postagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> PostagemOut:
    """Devolve pra fila uma postagem que FALHOU — e só ela.

    Publicar não é idempotente: retentar algo em `publicando` podia render dois
    posts iguais na conta da marca, sem desfazer. Quem está preso nesse estado
    é resolvido pela reconciliação (que pergunta à Meta pelo `container_id` /
    `post_external_id` antes de qualquer coisa), nunca por este botão.
    """
    p, creative = await _postagem_or_404(session, postagem_id, user)
    try:
        await svc.retentar(session, p)
    except svc.RoboError as e:
        raise _erro(e) from e
    await session.refresh(p)
    logger.info("marketing_postagem_retentada", postagem_id=str(p.id), user_id=str(user.id))
    return await _out_uma(session, p, creative)
