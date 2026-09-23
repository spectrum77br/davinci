"""Aba Roteiros — o briefing, fora da linha de produção.

Eduardo, 21/09/2026: "criaremos em marketing uma aba roteiro e lá poderemos
escrever o roteiro pro item (...) em roteiro talvez a gente já mande pra
alguém específico (...) uma regra também de que se não preenchido vai para
os 2".

## As duas regras de NULL, que são OPOSTAS

`MarketingRoteiro.equipe_destino` NULL = **as duas agências veem**.
`MarketingCreative.equipe` NULL = **ninguém de fora vê**.

Elas convivem no mesmo banco de propósito: uma diz "pra quem é este
briefing", a outra diz "de quem é esta linha de produção". O nome diferente
é a trava — `grep equipe_destino` acha todo uso da regra invertida.

## Permissão: `marketing_criativos`, não um recurso novo

Recurso novo nasce False pra todo mundo menos admin, e não existe migration
de backfill de permissão neste repositório. Criar `marketing_roteiros` seria
entregar no dia do deploy uma aba que ninguém consegue escrever até o admin
passar usuário por usuário. Mesmo precedente das Legendas e das Postagens,
que também penduram no recurso de Criativos.

## Recorte interno por equipe

Quem tem equipe de marketing continua vendo só o que é seu — MAIS os
roteiros sem destino, que são de todos por definição. Sem isso, um usuário
restrito passaria a ler o briefing de todas as equipes no dia do deploy, sem
ninguém conceder nada: hoje o texto mora na linha do criativo, e a linha já é
filtrada (`list_creatives`).
"""

from __future__ import annotations

import contextlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    MarketingCreative,
    MarketingIdeiaRequisicao,
    MarketingPersonagem,
    MarketingRoteiro,
    MarketingRoteiroPersonagem,
    MarketingRoteiroRef,
    User,
)
from app.models.marketing_ideia_requisicao import (
    STATUS_APROVADA as IDEIA_APROVADA,
)
from app.models.marketing_ideia_requisicao import (
    STATUS_PENDENTE as IDEIA_PENDENTE,
)
from app.models.marketing_ideia_requisicao import (
    STATUS_RECUSADA as IDEIA_RECUSADA,
)
from app.routers.marketing_creatives import (
    _marca_id_do_texto,
    _product_id_do_sku,
    _user_equipes,
)
from app.services.marketing.anexos import (
    _EXT_REFERENCIA,
    MAX_ANEXOS_POR_LINHA,
    MAX_BYTES_APOIO,
    MIMES_REFERENCIA,
    anexo_out,
    caminho_confinado,
    gravar_em_disco,
    mime_da_extensao,
    mime_seguro,
    nome_seguro,
    url_de_produto,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/roteiros", tags=["marketing"])

_ver = require_permission("marketing_criativos", "view")
_editar = require_permission("marketing_criativos", "edit")

TITULO_MAX = 160


def _ref_dir(roteiro_id: UUID) -> Path:
    return Path(get_settings().uploads_dir) / "roteiro_refs" / str(roteiro_id)


def _ref_out(r: MarketingRoteiroRef) -> dict[str, Any]:
    # `file_rel` fica de fora: é caminho no disco do servidor.
    return {
        "id": str(r.id),
        "tipo": r.tipo,
        "titulo": r.titulo,
        "url": r.url,
        "file_name": r.file_name,
        "file_mime": r.file_mime,
        "file_size": r.file_size,
    }


def _personagem_curto(p: MarketingPersonagem) -> dict[str, Any]:
    """O personagem visto de dentro do roteiro: o bastante pra escrever o
    prompt (a `referencia`) e pra reconhecer de quem se trata."""
    return {
        "id": str(p.id),
        "nome": p.nome,
        "descricao": p.descricao,
        "referencia": p.referencia,
        "video_url": p.video_url,
        "imagens": [anexo_out(a) for a in p.arquivos if a.tipo == "imagem"],
        "vozes": [anexo_out(a) for a in p.arquivos if a.tipo == "voz"],
    }


def _roteiro_out(row: MarketingRoteiro) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "titulo": row.titulo,
        "texto": row.texto,
        "marca": row.marca,
        "marca_id": str(row.marca_id) if row.marca_id else None,
        "sku": row.sku,
        "product_id": str(row.product_id) if row.product_id else None,
        # None = as DUAS agências. Ver o docstring do módulo.
        "equipe_destino": row.equipe_destino,
        # Preenchido quando a linha é a VERSÃO que uma agência escreveu em cima
        # de uma ideia da casa. Sem isto na resposta, a versão chega na tela
        # parecendo uma ideia duplicada e ninguém sabe de onde saiu.
        "origem_id": str(row.origem_id) if row.origem_id else None,
        "ativo": row.ativo,
        "referencias": [_ref_out(r) for r in row.refs],
        "personagens": [
            _personagem_curto(v.personagem) for v in row.personagens if v.personagem
        ],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _get(session: AsyncSession, roteiro_id: UUID, user: User) -> MarketingRoteiro:
    row = (
        await session.execute(
            select(MarketingRoteiro).where(MarketingRoteiro.id == roteiro_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "roteiro_nao_encontrado"})
    permitidas = _user_equipes(user)
    # NULL = de todos. É a regra invertida, e é o único lugar interno que a
    # aplica — por isso ela está escrita aqui e não espalhada por rota.
    if permitidas is not None and row.equipe_destino is not None:
        if row.equipe_destino.strip().lower() not in permitidas:
            raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})
    return row


@router.get("")
async def listar(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
) -> list[dict[str, Any]]:
    q = select(MarketingRoteiro).order_by(MarketingRoteiro.created_at.desc())
    permitidas = _user_equipes(user)
    if permitidas is not None:
        q = q.where(
            or_(
                MarketingRoteiro.equipe_destino.is_(None),
                func.lower(MarketingRoteiro.equipe_destino).in_(permitidas),
            )
        )
    return [_roteiro_out(r) for r in (await session.execute(q)).scalars().all()]


@router.get("/destinos")
async def destinos(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
) -> list[str]:
    """As agências que podem receber um roteiro.

    Sai da MESMA união que alimenta a coluna Equipe dos criativos
    (`/api/marketing/creatives/equipes`) pra não existirem dois vocabulários
    de equipe no mesmo módulo — um destino que não case com o nome gravado no
    `PORTAL_TOKENS` do DaVinci é um roteiro endereçado a ninguém.
    """
    from app.routers.marketing_creatives import list_equipes

    todas = await list_equipes(session, user)
    # Usuário restrito só enxerga (e só consegue gravar) as próprias equipes.
    # Oferecer as outras no select seria mostrar uma opção que responde 403 —
    # e contar pra ele o nome das agências que não são dele.
    permitidas = _user_equipes(user)
    if permitidas is None:
        return todas
    return [e for e in todas if e.strip().lower() in permitidas]


class RoteiroIn(BaseModel):
    titulo: str
    texto: str | None = None
    marca: str | None = None
    sku: str | None = None
    # Vazio/ausente = as DUAS agências (regra do Eduardo).
    equipe_destino: str | None = None


class RoteiroPatch(BaseModel):
    titulo: str | None = None
    texto: str | None = None
    marca: str | None = None
    sku: str | None = None
    equipe_destino: str | None = None
    ativo: bool | None = None


def _destino_limpo(bruto: str | None) -> str | None:
    """`"  "` e `""` viram NULL — senão "vazio = as duas" viraria um destino
    chamado espaço em branco, que não casa com token nenhum e some."""
    return (bruto or "").strip() or None


def _agencias_do_portal() -> list[str]:
    """As agências que TÊM portal — a lista do `PORTAL_TOKENS`, não a do
    `/destinos`.

    `/destinos` sai da união `users.marketing_teams` + equipes já gravadas nos
    criativos: ela serve pra OFERECER opção na tela. Aqui a pergunta é outra
    — "pra quem dá pra abrir uma entrega?" — e a única resposta honesta é
    quem consegue entrar no portal. Abrir entrega pra uma equipe sem token é
    criar uma linha que ninguém de fora jamais vai ver.
    """
    from app.routers.portal_criativos import _mapa_tokens

    vistas: dict[str, str] = {}
    for equipe in _mapa_tokens().values():
        vistas.setdefault(equipe.strip().lower(), equipe.strip())
    return sorted(vistas.values(), key=str.lower)


async def _sincronizar_entregas(session: AsyncSession, row: MarketingRoteiro) -> None:
    """Abre (e fecha) a linha de entrega das agências endereçadas pelo roteiro.

    Era o elo que faltava. O roteiro nascia sem par: a agência lia o briefing
    na aba Roteiros do portal e não tinha ONDE subir o vídeo, porque a aba
    Entregas lista `marketing_creatives` filtrado por equipe e nada criava
    essa linha. Em produção (22/09/2026) dava pra ver: o roteiro 39ff92b2
    estava no ar pras duas agências e `GET /api/portal/criativos` da Mindset
    respondia `"criativos":[]`.

    Três regras, nesta ordem:

    1. **Só sincroniza o que o portal MOSTRA.** O alvo é vazio enquanto o
       roteiro estiver desligado ou sem texto — as mesmas condições de
       `portal_criativos._visivel_pra_fora`. Sem isso o "Novo roteiro" (que
       nasce "Roteiro sem título", sem texto) abriria uma entrega em branco na
       tela da agência antes de alguém escrever o briefing.
    2. **`equipe_destino` NULL = as DUAS.** É a regra invertida do módulo, e
       aqui ela vira DUAS entregas, uma por agência: `marketing_creatives.equipe`
       não comporta "ambas" — lá NULL significa o oposto, ninguém de fora vê.
    3. **Abre sem duplicar, fecha sem destruir.** Criar casa por
       `roteiro_id` + `equipe`, então rodar de novo não gera linha repetida.
       Apagar alcança SÓ a linha que este helper poderia ter aberto: com
       equipe, sem arquivo, sem aprovação, sem envio pro MEGA e sem legenda
       nem feedback escritos à mão. Reendereçar um roteiro (ou desligá-lo)
       nunca pode evaporar o vídeo que a agência mandou — nem a linha interna
       que alguém montou na mão e ligou neste briefing.

    NÃO faz backfill: quem sincroniza é o POST e o PATCH. Roteiro que já
    estava no banco antes deste deploy só ganha entrega quando for salvo de
    novo.
    """
    destino = (row.equipe_destino or "").strip()
    visivel = row.ativo and bool((row.texto or "").strip())
    alvos = ([destino] if destino else _agencias_do_portal()) if visivel else []
    por_equipe = {e.strip().lower(): e.strip() for e in alvos if e.strip()}

    # `files` tem lazy="selectin" no modelo, então vem junto e o `.files` lá
    # embaixo não estoura MissingGreenlet em contexto async.
    atuais = (
        (
            await session.execute(
                select(MarketingCreative).where(MarketingCreative.roteiro_id == row.id)
            )
        )
        .scalars()
        .all()
    )

    mudou = False
    vistas: set[str] = set()
    for linha in atuais:
        chave = (linha.equipe or "").strip().lower()
        vistas.add(chave)
        if chave in por_equipe:
            continue
        # Só apaga o que ESTE helper poderia ter criado. Linha sem equipe é
        # linha INTERNA — `create_creative` deixa `equipe` NULL pra admin — e
        # nunca foi entrega de agência nenhuma. Apagá-la porque alguém
        # corrigiu um typo no título do roteiro é jogar fora trabalho alheio.
        if not chave:
            continue
        if linha.files or linha.aprovado is not None or linha.pushed_at is not None:
            continue  # alguém já encostou: fica.
        # `legenda` e `feedback` são texto DIGITADO à mão nesta linha. Pro
        # portal ela continua virgem (sem vídeo, sem V), mas tem conteúdo que
        # não volta — então ela também fica, e o pior caso vira lixo visível
        # na grade, não perda silenciosa.
        if linha.legenda or linha.feedback:
            continue
        await session.delete(linha)
        mudou = True

    for chave, equipe in por_equipe.items():
        if chave in vistas:
            continue
        session.add(
            MarketingCreative(
                id=uuid4(),
                modelo=row.titulo,
                marca=row.marca,
                marca_id=row.marca_id,
                sku=row.sku,
                product_id=row.product_id,
                equipe=equipe,
                roteiro_id=row.id,
                created_by=row.created_by,
            )
        )
        mudou = True

    if not mudou:
        return
    await session.commit()
    logger.info(
        "roteiro_entregas_sincronizadas",
        roteiro_id=str(row.id),
        equipes=sorted(por_equipe.values()),
    )


@router.post("")
async def criar(
    payload: RoteiroIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    titulo = payload.titulo.strip()[:TITULO_MAX]
    if not titulo:
        raise HTTPException(400, detail={"code": "titulo_obrigatorio"})
    # MESMA regra do PATCH (`editar`), e ANTES de gravar qualquer coisa.
    # Faltando aqui, um usuário restrito endereçava briefing pra uma agência
    # que ele nem enxerga — e pior: o `session.commit()` acontecia antes de o
    # `_get` estourar o 403, então a linha FICAVA no banco, visível pra
    # agência errada, e a própria autora não conseguia mais apagar.
    destino = _destino_limpo(payload.equipe_destino)
    permitidas = _user_equipes(user)
    if permitidas is not None and (destino is None or destino.lower() not in permitidas):
        raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})
    marca = (payload.marca or "").strip() or None
    sku = (payload.sku or "").strip() or None
    row = MarketingRoteiro(
        id=uuid4(),
        titulo=titulo,
        texto=(payload.texto or "").strip() or None,
        marca=marca,
        marca_id=await _marca_id_do_texto(session, marca),
        sku=sku,
        product_id=await _product_id_do_sku(session, sku),
        equipe_destino=destino,
        created_by=user.id,
    )
    session.add(row)
    await session.commit()
    row = await _get(session, row.id, user)
    # Roteiro criado JÁ com texto (o import, a cópia) abre a entrega na hora.
    # O "Novo roteiro" da tela nasce sem texto: nada acontece aqui, e a
    # entrega abre no PATCH em que o briefing for escrito.
    await _sincronizar_entregas(session, row)
    logger.info("roteiro_criado", roteiro_id=str(row.id), user_id=str(user.id))
    return _roteiro_out(row)


@router.patch("/{roteiro_id}")
async def editar(
    roteiro_id: UUID,
    payload: RoteiroPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, roteiro_id, user)
    data = payload.model_dump(exclude_unset=True)
    if "titulo" in data:
        titulo = (data["titulo"] or "").strip()[:TITULO_MAX]
        if not titulo:
            raise HTTPException(400, detail={"code": "titulo_obrigatorio"})
        row.titulo = titulo
    if "texto" in data:
        # `.strip()` na borda: sem isso `"   "` entra no banco e o portal, que
        # filtra por texto não-vazio, mostra um card em branco pra agência.
        row.texto = (data["texto"] or "").strip() or None
    if "marca" in data:
        row.marca = (data["marca"] or "").strip() or None
        # O id acompanha o texto SEMPRE — inclusive virando NULL.
        row.marca_id = await _marca_id_do_texto(session, row.marca)
    if "sku" in data:
        row.sku = (data["sku"] or "").strip() or None
        row.product_id = await _product_id_do_sku(session, row.sku)
    if "equipe_destino" in data:
        novo = _destino_limpo(data["equipe_destino"])
        permitidas = _user_equipes(user)
        # Quem é restrito não pode endereçar pra fora da própria equipe, nem
        # soltar pra "as duas" — isso seria publicar pra uma agência que ele
        # nem enxerga.
        if permitidas is not None and (novo is None or novo.lower() not in permitidas):
            raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})
        row.equipe_destino = novo
    if "ativo" in data:
        row.ativo = bool(data["ativo"])
    await session.commit()
    # Depois do commit: escrever o texto, ligar o `ativo` ou trocar o destino
    # são exatamente os três eventos que mudam PRA QUEM este briefing existe.
    await _sincronizar_entregas(session, row)
    return _roteiro_out(row)


@router.delete("/{roteiro_id}")
async def apagar(
    roteiro_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, str]:
    """`edit` e não `delete`: é a norma do módulo (Criativos apaga com edit), e
    mudar a régua só aqui faria quem apaga criativo não apagar roteiro."""
    row = await _get(session, roteiro_id, user)

    # Apagar a ideia de partida apagava o SENTIDO da versão, não a versão: a FK
    # é SET NULL, então a linha da agência ficava na lista com o mesmo título,
    # `origem_id` nulo e o texto dela — indistinguível de uma ideia da casa. A
    # etiqueta "versão da agência" some justamente quando mais faz falta, e quem
    # editasse acharia que estava mexendo no briefing.
    versoes = (
        await session.execute(
            select(func.count())
            .select_from(MarketingRoteiro)
            .where(MarketingRoteiro.origem_id == row.id)
        )
    ).scalar_one()
    if versoes:
        raise HTTPException(
            409,
            detail={
                "code": "tem_versoes",
                "message": (
                    f"{versoes} versão(ões) de agência saíram deste roteiro. "
                    "Apague as versões primeiro, ou deixe a ideia no lugar."
                ),
            },
        )

    base = _ref_dir(row.id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    # A FK de `marketing_creatives.roteiro_id` é SET NULL: a linha de produção
    # e a entrega que a agência já mandou sobrevivem ao briefing.
    await session.delete(row)
    await session.commit()
    logger.info("roteiro_apagado", roteiro_id=str(roteiro_id), user_id=str(user.id))
    return {"status": "deleted"}


# ─── referências do briefing ───────────────────────────────────────────────


@router.post("/{roteiro_id}/referencia")
async def subir_referencia(
    roteiro_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, roteiro_id, user)
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.refs) + len(files) > MAX_ANEXOS_POR_LINHA:
        raise HTTPException(400, detail={"code": "muitas_referencias"})

    base = _ref_dir(row.id)
    base.mkdir(parents=True, exist_ok=True)
    entraram: list[str] = []
    for up in files:
        nome = nome_seguro(up.filename)
        mime = mime_da_extensao(nome, tabela=_EXT_REFERENCIA)
        caminho = base / nome
        tamanho = gravar_em_disco(
            up, caminho, teto=MAX_BYTES_APOIO, code="referencia_grande_demais"
        )
        antigo = next(
            (r for r in row.refs if r.tipo == "imagem" and r.file_name == nome), None
        )
        if antigo is not None:  # mesmo nome substitui
            row.refs.remove(antigo)
        row.refs.append(
            MarketingRoteiroRef(
                id=uuid4(),
                tipo="imagem",
                file_name=nome,
                file_mime=mime,
                file_size=tamanho,
                file_rel=f"roteiro_refs/{row.id}/{nome}",
                created_by=user.id,
            )
        )
        entraram.append(nome)
    await session.commit()
    logger.info("roteiro_ref_upload", roteiro_id=str(row.id), arquivos=entraram)
    return _roteiro_out(row)


class LinkIn(BaseModel):
    url: str
    titulo: str | None = None


@router.post("/{roteiro_id}/referencia/link")
async def add_link(
    roteiro_id: UUID,
    payload: LinkIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    """Link do produto preso ao briefing. Só http/https — este texto vira
    `href` no site PHP das agências."""
    row = await _get(session, roteiro_id, user)
    if len(row.refs) >= MAX_ANEXOS_POR_LINHA:
        raise HTTPException(400, detail={"code": "muitas_referencias"})
    row.refs.append(
        MarketingRoteiroRef(
            id=uuid4(),
            tipo="link",
            url=url_de_produto(payload.url),
            titulo=(payload.titulo or "").strip()[:200] or None,
            created_by=user.id,
        )
    )
    await session.commit()
    return _roteiro_out(row)


@router.get("/{roteiro_id}/referencia/{ref_id}")
async def baixar_referencia(
    roteiro_id: UUID,
    ref_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
    download: bool = False,
) -> FileResponse:
    row = await _get(session, roteiro_id, user)
    rec = next((r for r in row.refs if r.id == ref_id and r.tipo == "imagem"), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "referencia_nao_encontrada"})
    caminho = caminho_confinado(rec.file_rel)
    if caminho is None or not caminho.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})
    # O MIME gravado já veio da EXTENSÃO; a régua é aplicada de novo na
    # leitura pra uma linha antiga ou um import não transformarem esta rota
    # num servidor de text/html no domínio do DaVinci.
    media_type, disposicao = mime_seguro(rec.file_mime, permitidos=MIMES_REFERENCIA)
    return FileResponse(
        caminho,
        filename=rec.file_name or "referencia",
        media_type=media_type,
        content_disposition_type="attachment" if download else disposicao,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{roteiro_id}/referencia/{ref_id}")
async def apagar_referencia(
    roteiro_id: UUID,
    ref_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, roteiro_id, user)
    rec = next((r for r in row.refs if r.id == ref_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "referencia_nao_encontrada"})
    if rec.tipo == "imagem":
        alvo = caminho_confinado(rec.file_rel)
        if alvo is not None:
            with contextlib.suppress(OSError):
                alvo.unlink(missing_ok=True)
    row.refs.remove(rec)
    await session.commit()
    return _roteiro_out(row)


# ─── "neste vídeo use a Lívia" ─────────────────────────────────────────────


class PersonagemIn(BaseModel):
    personagem_id: UUID


@router.post("/{roteiro_id}/personagem")
async def ligar_personagem(
    roteiro_id: UUID,
    payload: PersonagemIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, roteiro_id, user)
    existe = await session.scalar(
        select(MarketingPersonagem.id).where(
            MarketingPersonagem.id == payload.personagem_id
        )
    )
    if existe is None:
        raise HTTPException(404, detail={"code": "personagem_nao_encontrado"})
    session.add(
        MarketingRoteiroPersonagem(
            id=uuid4(), roteiro_id=row.id, personagem_id=payload.personagem_id
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # `uq_roteiro_personagem` — ligar duas vezes é clique repetido, não
        # erro do operador: responde a lista como está.
        await session.rollback()
    # `expire` antes de reler: o elo entrou por `session.add`, e sem isto o
    # identity map devolve o roteiro com a coleção velha (vazia) e a tela
    # mostra "nenhum personagem" logo depois de ligar um.
    session.expire(row)
    return _roteiro_out(await _get(session, roteiro_id, user))


@router.delete("/{roteiro_id}/personagem/{personagem_id}")
async def desligar_personagem(
    roteiro_id: UUID,
    personagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, roteiro_id, user)
    elo = next((v for v in row.personagens if v.personagem_id == personagem_id), None)
    if elo is None:
        raise HTTPException(404, detail={"code": "personagem_nao_encontrado"})
    row.personagens.remove(elo)
    await session.commit()
    return _roteiro_out(row)


# ──────────────── ideias propostas pelas agências ────────────────
# A agência propõe; aqui é onde a casa libera. Aprovar CRIA o briefing
# endereçado a quem pediu — é o sim que autoriza produzir.


def _req_ideia_out(r: MarketingIdeiaRequisicao) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "titulo": r.titulo,
        "descricao": r.descricao,
        "justificativa": r.justificativa,
        "marca": r.marca,
        "sku": r.sku,
        "equipe": r.equipe,
        "status": r.status,
        "motivo": r.motivo,
        "roteiro_id": str(r.roteiro_id) if r.roteiro_id else None,
        "criado_em": r.created_at.isoformat() if r.created_at else None,
        "decidido_em": r.decidido_em.isoformat() if r.decidido_em else None,
    }


def _ideia_fora_da_equipe(user: User, req: MarketingIdeiaRequisicao) -> bool:
    """O mesmo recorte de Criativos, Roteiros e da fila de personagem.

    Aprovar cria briefing e recusar é irreversível: a fila não pode ser o único
    lugar do módulo onde um usuário preso a uma agência decide pela outra.
    """
    permitidas = _user_equipes(user)
    if permitidas is None:
        return False
    return (req.equipe or "").strip().lower() not in permitidas


@router.get("/requisicoes")
async def listar_requisicoes_de_ideia(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
    status: str | None = None,
) -> dict[str, Any]:
    """A fila. Sem filtro, vêm só as pendentes — que é o que exige ação."""
    q = select(MarketingIdeiaRequisicao).order_by(MarketingIdeiaRequisicao.created_at.desc())
    q = q.where(MarketingIdeiaRequisicao.status == (status or IDEIA_PENDENTE))
    permitidas = _user_equipes(user)
    if permitidas is not None:
        q = q.where(func.lower(MarketingIdeiaRequisicao.equipe).in_(permitidas))
    linhas = (await session.execute(q)).scalars().all()
    return {"requisicoes": [_req_ideia_out(r) for r in linhas]}


class DecisaoIdeiaIn(BaseModel):
    motivo: str | None = None


@router.post("/requisicoes/{requisicao_id}/aprovar")
async def aprovar_requisicao_de_ideia(
    requisicao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    """Aprovar CRIA o briefing, endereçado só a quem pediu.

    `equipe_destino = equipe` e não NULL: a ideia é dela, e mandar a proposta de
    uma agência para as duas entregaria o trabalho de pensar de uma à outra. A
    regra invertida do módulo (NULL = as duas) vale para o que a CASA escreve.

    Nasce `ativo=True` porque o sim já é a liberação — não faz sentido aprovar
    e deixar desligado, que seria pedir duas permissões para a mesma coisa.
    """
    req = await session.get(MarketingIdeiaRequisicao, requisicao_id)
    if req is None or _ideia_fora_da_equipe(user, req):
        raise HTTPException(404, detail={"code": "requisicao_nao_encontrada"})
    if req.status != IDEIA_PENDENTE:
        raise HTTPException(409, detail={"code": "ja_decidida", "status": req.status})

    # Mesmo caminho do roteiro escrito à mão: o texto de marca e SKU vira id
    # aqui, no servidor, e não vem resolvido do formulário da agência.
    row = MarketingRoteiro(
        id=uuid4(),
        titulo=req.titulo[:160],
        texto=req.descricao,
        marca=req.marca,
        marca_id=await _marca_id_do_texto(session, req.marca),
        sku=req.sku,
        product_id=await _product_id_do_sku(session, req.sku),
        equipe_destino=req.equipe,
        ativo=True,
        created_by=user.id,
    )
    session.add(row)
    await session.flush()
    # Abre a linha de entrega da agência endereçada, como em qualquer briefing
    # que nasce visível — senão a ideia aprovada não teria onde receber o vídeo.
    await _sincronizar_entregas(session, row)

    req.status = IDEIA_APROVADA
    req.roteiro_id = row.id
    req.decidido_por = user.id
    req.decidido_em = datetime.now(UTC)
    await session.commit()
    logger.info(
        "requisicao_ideia_aprovada",
        requisicao_id=str(req.id),
        roteiro_id=str(row.id),
        equipe=req.equipe,
        user_id=str(user.id),
    )
    return {"requisicao": _req_ideia_out(req), "roteiro_id": str(row.id)}


@router.post("/requisicoes/{requisicao_id}/recusar")
async def recusar_requisicao_de_ideia(
    requisicao_id: UUID,
    payload: DecisaoIdeiaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    """Recusar guarda o motivo, e a agência lê. Recusa sem porquê é a que volta
    igual na semana seguinte."""
    req = await session.get(MarketingIdeiaRequisicao, requisicao_id)
    if req is None or _ideia_fora_da_equipe(user, req):
        raise HTTPException(404, detail={"code": "requisicao_nao_encontrada"})
    if req.status != IDEIA_PENDENTE:
        raise HTTPException(409, detail={"code": "ja_decidida", "status": req.status})

    req.status = IDEIA_RECUSADA
    req.motivo = (payload.motivo or "").strip() or None
    req.decidido_por = user.id
    req.decidido_em = datetime.now(UTC)
    await session.commit()
    logger.info("requisicao_ideia_recusada", requisicao_id=str(req.id), user_id=str(user.id))
    return _req_ideia_out(req)
