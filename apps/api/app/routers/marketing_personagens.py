"""Personagens — o elenco da casa, no DaVinci e no portal das agências.

Eduardo, 21/09/2026: "os personagens são ideias de personagens que podem ser
usadas nos vídeos dadas pela gente no DaVinci (...) vão aparecer também no
nosso domínio".

Um personagem tem TRÊS partes, e não uma. O roteiro que já rodava em produção
pede "Preserve the character's face, blonde hair, body proportions and
identity" e chama o personagem por um id de gerador (`<<<48dbb6ed-…>>>`) ou
por apelido (`@Lívia`). Então:

- `nome` — como a equipe fala dele;
- `descricao` — quem é;
- `referencia` — a etiqueta que o GERADOR entende, colada dentro do prompt.

Mais as fotos, que são o que a agência olha.

Catálogo GLOBAL de propósito: personagem não tem dono de equipe. É a única
rota do portal sem recorte por agência, e é uma decisão, não um esquecimento
— quem quiser endereçar personagem cria a coluna no dia em que for pedido,
com o mesmo nome `equipe_destino` do roteiro.

Permissão: `marketing_criativos`, pelo mesmo motivo do router de roteiros —
recurso novo nasceria fechado pra todo mundo menos admin.
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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import MarketingPersonagem, MarketingPersonagemArquivo, User
from app.models.marketing_personagem_requisicao import (
    STATUS_APROVADA,
    STATUS_PENDENTE,
    STATUS_RECUSADA,
    MarketingPersonagemRequisicao,
)
from app.routers.marketing_creatives import _user_equipes
from app.services.marketing.anexos import (
    _EXT_AUDIO,
    _EXT_IMAGEM,
    MAX_ANEXOS_POR_LINHA,
    MAX_BYTES_APOIO,
    MIMES_PERSONAGEM,
    anexo_out,
    caminho_confinado,
    gravar_em_disco,
    mime_da_extensao,
    mime_seguro,
    nome_seguro,
    url_de_produto,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/personagens", tags=["marketing"])

_ver = require_permission("marketing_criativos", "view")
_editar = require_permission("marketing_criativos", "edit")


def _dir(personagem_id: UUID) -> Path:
    return Path(get_settings().uploads_dir) / "personagens" / str(personagem_id)


def _out(row: MarketingPersonagem) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "nome": row.nome,
        "descricao": row.descricao,
        # Atalho pra quem usa a mesma ferramenta que gerou o rosto. Pode
        # quebrar; o arquivo, não.
        "referencia": row.referencia,
        # Como a persona se move e fala (um Shorts, normalmente).
        "video_url": row.video_url,
        "ativo": row.ativo,
        # Separados na saída porque a tela trata cada um de um jeito: foto vira
        # miniatura, voz vira player. Guardados na mesma tabela porque seguem
        # o mesmo caminho até o disco.
        "imagens": [anexo_out(a) for a in row.arquivos if a.tipo == "imagem"],
        "vozes": [anexo_out(a) for a in row.arquivos if a.tipo == "voz"],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _get(session: AsyncSession, personagem_id: UUID) -> MarketingPersonagem:
    row = (
        await session.execute(
            select(MarketingPersonagem).where(MarketingPersonagem.id == personagem_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "personagem_nao_encontrado"})
    return row


@router.get("")
async def listar(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
) -> list[dict[str, Any]]:
    linhas = (
        (
            await session.execute(
                select(MarketingPersonagem).order_by(func.lower(MarketingPersonagem.nome))
            )
        )
        .scalars()
        .all()
    )
    return [_out(r) for r in linhas]


class PersonagemIn(BaseModel):
    nome: str
    descricao: str | None = None
    referencia: str | None = None
    video_url: str | None = None


class PersonagemPatch(BaseModel):
    nome: str | None = None
    descricao: str | None = None
    referencia: str | None = None
    video_url: str | None = None
    ativo: bool | None = None


def _video_limpo(bruto: str | None) -> str | None:
    """Vazio apaga; preenchido passa pela mesma lista branca do link de
    produto (http/https) — este campo vira href no site das agências."""
    return url_de_produto(bruto) if (bruto or "").strip() else None


@router.post("")
async def criar(
    payload: PersonagemIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    nome = payload.nome.strip()[:120]
    if not nome:
        raise HTTPException(400, detail={"code": "nome_obrigatorio"})
    row = MarketingPersonagem(
        id=uuid4(),
        nome=nome,
        descricao=(payload.descricao or "").strip() or None,
        referencia=(payload.referencia or "").strip()[:200] or None,
        video_url=_video_limpo(payload.video_url),
        created_by=user.id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        # `ix_marketing_personagens_nome_unico`: dois "Lívia" no select do
        # roteiro e ninguém sabe qual é qual.
        await session.rollback()
        raise HTTPException(409, detail={"code": "personagem_repetido"}) from exc
    await session.refresh(row)
    logger.info("personagem_criado", personagem_id=str(row.id), user_id=str(user.id))
    return _out(row)


@router.patch("/{personagem_id}")
async def editar(
    personagem_id: UUID,
    payload: PersonagemPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, personagem_id)
    data = payload.model_dump(exclude_unset=True)
    if "nome" in data:
        nome = (data["nome"] or "").strip()[:120]
        if not nome:
            raise HTTPException(400, detail={"code": "nome_obrigatorio"})
        row.nome = nome
    if "descricao" in data:
        row.descricao = (data["descricao"] or "").strip() or None
    if "referencia" in data:
        row.referencia = (data["referencia"] or "").strip()[:200] or None
    if "video_url" in data:
        row.video_url = _video_limpo(data["video_url"])
    if "ativo" in data:
        row.ativo = bool(data["ativo"])
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail={"code": "personagem_repetido"}) from exc
    return _out(row)


@router.delete("/{personagem_id}")
async def apagar(
    personagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, str]:
    row = await _get(session, personagem_id)
    base = _dir(row.id)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    # A FK do elo com roteiro é CASCADE: o vínculo some junto, o roteiro fica.
    await session.delete(row)
    await session.commit()
    logger.info("personagem_apagado", personagem_id=str(personagem_id))
    return {"status": "deleted"}


# Foto e voz seguem a MESMA rota, separadas por `tipo`. O que muda é só a
# lista branca de extensão — e é ela que impede subir um HTML como "voz".
_TABELA_POR_TIPO = {"imagem": _EXT_IMAGEM, "voz": _EXT_AUDIO}


@router.post("/{personagem_id}/arquivo")
async def subir_arquivo(
    personagem_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
    tipo: str = "imagem",
) -> dict[str, Any]:
    """Sobe foto(s) do rosto ou o MP3 da voz.

    É ESTE arquivo que a agência baixa e leva pro gerador dela. A etiqueta
    (`referencia`) é atalho pra quem usa a mesma ferramenta e pode quebrar;
    o arquivo funciona em qualquer uma.
    """
    tabela = _TABELA_POR_TIPO.get(tipo)
    if tabela is None:
        raise HTTPException(400, detail={"code": "tipo_invalido", "aceitos": ["imagem", "voz"]})
    row = await _get(session, personagem_id)
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.arquivos) + len(files) > MAX_ANEXOS_POR_LINHA:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    base = _dir(row.id)
    base.mkdir(parents=True, exist_ok=True)
    for up in files:
        nome = nome_seguro(up.filename)
        mime = mime_da_extensao(nome, tabela=tabela)
        caminho = base / nome
        tamanho = gravar_em_disco(
            up, caminho, teto=MAX_BYTES_APOIO, code="arquivo_grande_demais"
        )
        antigo = next((a for a in row.arquivos if a.file_name == nome), None)
        if antigo is not None:  # mesmo nome substitui
            row.arquivos.remove(antigo)
        row.arquivos.append(
            MarketingPersonagemArquivo(
                id=uuid4(),
                tipo=tipo,
                file_name=nome,
                file_mime=mime,
                file_size=tamanho,
                file_rel=f"personagens/{row.id}/{nome}",
                created_by=user.id,
            )
        )
    await session.commit()
    return _out(row)


@router.get("/{personagem_id}/arquivo/{arquivo_id}")
async def baixar_arquivo(
    personagem_id: UUID,
    arquivo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
    download: bool = False,
) -> FileResponse:
    row = await _get(session, personagem_id)
    rec = next((a for a in row.arquivos if a.id == arquivo_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "arquivo_nao_encontrado"})
    caminho = caminho_confinado(rec.file_rel)
    if caminho is None or not caminho.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})
    media_type, disposicao = mime_seguro(rec.file_mime, permitidos=MIMES_PERSONAGEM)
    return FileResponse(
        caminho,
        filename=rec.file_name,
        media_type=media_type,
        content_disposition_type="attachment" if download else disposicao,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{personagem_id}/arquivo/{arquivo_id}")
async def apagar_arquivo(
    personagem_id: UUID,
    arquivo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    row = await _get(session, personagem_id)
    rec = next((a for a in row.arquivos if a.id == arquivo_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "arquivo_nao_encontrado"})
    alvo = caminho_confinado(rec.file_rel)
    if alvo is not None:
        with contextlib.suppress(OSError):
            alvo.unlink(missing_ok=True)
    row.arquivos.remove(rec)
    await session.commit()
    return _out(row)

# ─────────────────────── requisições vindas das agências ───────────────────────
# O canal externo PROPÕE; aqui é onde a casa decide. O personagem só existe
# depois de alguém olhar a procedência e dizer sim — é esse passo que a Súmula
# 403 do STJ torna caro de pular.


def _requisicao_out(r: MarketingPersonagemRequisicao) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "nome": r.nome,
        "descricao": r.descricao,
        "justificativa": r.justificativa,
        "origem_imagem": r.origem_imagem,
        "origem_voz": r.origem_voz,
        "cessao": r.cessao,
        "cessao_obs": r.cessao_obs,
        "equipe": r.equipe,
        "status": r.status,
        "motivo": r.motivo,
        "personagem_id": str(r.personagem_id) if r.personagem_id else None,
        "criado_em": r.created_at.isoformat() if r.created_at else None,
        "decidido_em": r.decidido_em.isoformat() if r.decidido_em else None,
    }


def _fora_da_equipe(user: User, req: MarketingPersonagemRequisicao) -> bool:
    """O mesmo recorte por equipe que Criativos e Roteiros já aplicam.

    Sem isto, um usuário interno preso à Mindset lia — e DECIDIA — os pedidos
    da Bill Gates. Aprovar cria personagem e recusar é irreversível, então a
    fila não podia ser o único lugar do módulo sem recorte. Admin e usuário
    sem equipe continuam vendo tudo (`_user_equipes` devolve None).
    """
    permitidas = _user_equipes(user)
    if permitidas is None:
        return False
    return (req.equipe or "").strip().lower() not in permitidas


@router.get("/requisicoes")
async def listar_requisicoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_ver)],
    status: str | None = None,
) -> dict[str, Any]:
    """A fila. Sem filtro, vêm só as pendentes — que é o que exige ação."""
    q = select(MarketingPersonagemRequisicao).order_by(
        MarketingPersonagemRequisicao.created_at.desc()
    )
    q = q.where(MarketingPersonagemRequisicao.status == (status or STATUS_PENDENTE))
    permitidas = _user_equipes(user)
    if permitidas is not None:
        q = q.where(func.lower(MarketingPersonagemRequisicao.equipe).in_(permitidas))
    linhas = (await session.execute(q)).scalars().all()
    return {"requisicoes": [_requisicao_out(r) for r in linhas]}


class DecisaoIn(BaseModel):
    motivo: str | None = None


@router.post("/requisicoes/{requisicao_id}/aprovar")
async def aprovar_requisicao(
    requisicao_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    """Aprovar CRIA o personagem — com nome e descrição, sem arquivo.

    A foto e o MP3 sobem depois, pelo caminho normal, porque quem carrega o
    arquivo é quem responde por ele. A requisição fica apontando para o
    personagem criado: é por esse rastro que se audita, meses depois, de onde
    veio aquele rosto.
    """
    req = await session.get(MarketingPersonagemRequisicao, requisicao_id)
    if req is None or _fora_da_equipe(user, req):
        # 404, não 403: para quem não é da equipe, o pedido não existe — é a
        # mesma régua que o portal usa do lado de fora.
        raise HTTPException(404, detail={"code": "requisicao_nao_encontrada"})
    if req.status != STATUS_PENDENTE:
        raise HTTPException(409, detail={"code": "ja_decidida", "status": req.status})

    # O índice de nome único é do banco; conferir aqui é o que transforma a
    # corrida em 409 legível em vez de 500 de constraint.
    existe = (
        await session.execute(
            select(MarketingPersonagem.id).where(
                func.lower(MarketingPersonagem.nome) == req.nome.lower()
            )
        )
    ).first()
    if existe is not None:
        raise HTTPException(409, detail={"code": "personagem_ja_existe"})

    p = MarketingPersonagem(id=uuid4(), nome=req.nome, descricao=req.descricao)
    session.add(p)
    await session.flush()

    req.status = STATUS_APROVADA
    req.personagem_id = p.id
    req.decidido_por = user.id
    req.decidido_em = datetime.now(UTC)
    await session.commit()
    logger.info(
        "requisicao_personagem_aprovada",
        requisicao_id=str(req.id),
        personagem_id=str(p.id),
        user_id=str(user.id),
    )
    return {"requisicao": _requisicao_out(req), "personagem_id": str(p.id)}


@router.post("/requisicoes/{requisicao_id}/recusar")
async def recusar_requisicao(
    requisicao_id: UUID,
    payload: DecisaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_editar)],
) -> dict[str, Any]:
    """Recusar guarda o motivo. Recusa sem motivo é a que volta igual na semana
    seguinte, e aí alguém gasta o mesmo tempo de novo."""
    req = await session.get(MarketingPersonagemRequisicao, requisicao_id)
    if req is None or _fora_da_equipe(user, req):
        # 404, não 403: para quem não é da equipe, o pedido não existe — é a
        # mesma régua que o portal usa do lado de fora.
        raise HTTPException(404, detail={"code": "requisicao_nao_encontrada"})
    if req.status != STATUS_PENDENTE:
        raise HTTPException(409, detail={"code": "ja_decidida", "status": req.status})

    req.status = STATUS_RECUSADA
    req.motivo = (payload.motivo or "").strip() or None
    req.decidido_por = user.id
    req.decidido_em = datetime.now(UTC)
    await session.commit()
    logger.info("requisicao_personagem_recusada", requisicao_id=str(req.id), user_id=str(user.id))
    return _requisicao_out(req)
