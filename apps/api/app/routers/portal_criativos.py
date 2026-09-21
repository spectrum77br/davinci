"""Portal do time de criação — a porta estreita para fora (Eduardo, 21/09/2026).

"criaremos no hostinger um dominio php html e nela teremos a tela de marketing
uma tela de roteiros (...) pra eles tambem verem que estao fazendo o criativo
mas sem acesso claro a outras coisas".

Duas agências TERCEIRAS. Elas não têm conta no DaVinci e não vão ter: quem
autentica pessoa é o site delas. O que atravessa a internet é uma chamada
servidor-a-servidor com um token no header, guardado no servidor PHP — o
navegador do criativo nunca o vê, e o CORS do DaVinci não muda.

## Por que um router separado, e não um filtro no de sempre

O `marketing_creatives` também permite PATCH da linha, DELETE do arquivo,
DELETE da linha e o `aprovar` que empurra pro MEGA. Pendurar "mas só se for
agência" em cada um deles é o tipo de guarda que alguém esquece de repetir na
próxima rota. Aqui a superfície é a lista de rotas deste arquivo, e é curta:
ler as linhas da própria equipe, ler os roteiros, anexar arquivo. Nada mais.

## As quatro travas

1. **Fecha por padrão.** Token não configurado, ausente ou diferente → 401,
   com `compare_digest`, antes de qualquer trabalho. É o desenho do
   `_require_agent_token` (routers/marketing.py:975) e o oposto do webhook do
   Bling, que não rejeita nunca — a revisão de 18/09 achou isso lá.
2. **Não devolve `User`.** Sem User não há `role`, e o atalho de admin que
   fura todos os gates não é alcançado nem por acidente.
3. **A equipe vai no WHERE.** Nunca em memória, nunca opcional. E NÃO uso o
   `_user_equipes` do outro router: lá `return teams or None` significa
   "sem restrição" — usuário sem equipe vê tudo. Aqui equipe vazia é 401.
4. **Serializador com lista branca.** O `_row_out` de lá entrega `legenda`,
   `product_id` e `pushed_dest` (o caminho da pasta no MEGA). Nada disso é da
   conta de terceiro.

## O que este portal NÃO faz

Não aprova, não apaga, não edita a linha, não fala com o MEGA. O envio pro
MEGA continua acontecendo só no clique do admin dentro do DaVinci.
"""

from __future__ import annotations

import secrets
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.routers.marketing_creatives import (
    MAX_BYTES_ARQUIVO,
    MAX_FILES_PER_ROW,
    _file_dir,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/portal", tags=["portal-criativos"])

# Teto do que uma listagem devolve. Agência com muito histórico não derruba a
# chamada nem entrega o catálogo inteiro de uma vez.
LIMITE_PADRAO = 200


def _mapa_tokens() -> dict[str, str]:
    """`"tok:equipe,tok:equipe"` → {token: equipe}. Linha torta é ignorada."""
    bruto = get_settings().portal_tokens or ""
    mapa: dict[str, str] = {}
    for parte in bruto.split(","):
        token, _, equipe = parte.partition(":")
        token, equipe = token.strip(), equipe.strip()
        if token and equipe:
            mapa[token] = equipe
    return mapa


async def equipe_do_token(
    x_portal_token: Annotated[str | None, Header(alias="X-Portal-Token")] = None,
) -> str:
    """Devolve a EQUIPE dona do token. Nunca devolve usuário, nunca abre sem token."""
    mapa = _mapa_tokens()
    if not mapa or not x_portal_token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail={"code": "portal_nao_autorizado"}
        )
    # compare_digest contra CADA token cadastrado: comparar por `in` daria a
    # resposta em tempo variável e entregaria o token caractere a caractere.
    for token, equipe in mapa.items():
        if secrets.compare_digest(x_portal_token, token):
            return equipe
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail={"code": "portal_nao_autorizado"}
    )


def _arquivo_out(f: MarketingCreativeFile) -> dict[str, Any]:
    # `file_rel` fica de fora: é caminho no disco do servidor.
    return {
        "id": str(f.id),
        "nome": f.file_name,
        "tamanho": f.file_size,
        "enviado_em": f.created_at.isoformat() if f.created_at else None,
    }


def _linha_out(row: MarketingCreative) -> dict[str, Any]:
    """LISTA BRANCA. Campo novo no modelo não vaza sozinho por aqui."""
    return {
        "id": str(row.id),
        "modelo": row.modelo,
        "marca": row.marca,
        "sku": row.sku,
        "roteiro": row.roteiro,
        # None = ainda não olharam; True = aprovado; False = recusado.
        "aprovado": row.aprovado,
        # Booleano em vez da data e do caminho: a agência precisa saber que
        # foi entregue, não onde o arquivo mora.
        "entregue": row.pushed_at is not None,
        "arquivos": [_arquivo_out(f) for f in row.files],
        "criado_em": row.created_at.isoformat() if row.created_at else None,
    }


def _da_equipe(equipe: str):
    """O filtro, num lugar só — e sempre no WHERE."""
    return func.lower(func.coalesce(MarketingCreative.equipe, "")) == equipe.lower()


async def _linha_da_equipe(
    session: AsyncSession, creative_id: UUID, equipe: str
) -> MarketingCreative:
    """404 (não 403) quando a linha é de outra equipe.

    Dizer "existe, mas não é sua" conta pra agência de fora que a linha
    existe. Do lado de fora, o que não é seu simplesmente não existe.
    """
    row = (
        await session.execute(
            select(MarketingCreative)
            .options(selectinload(MarketingCreative.files))
            .where(MarketingCreative.id == creative_id, _da_equipe(equipe))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return row


@router.get("/criativos")
async def listar_criativos(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A tela de marketing: o que a agência entregou e o que foi aprovado."""
    linhas = (
        (
            await session.execute(
                select(MarketingCreative)
                .options(selectinload(MarketingCreative.files))
                .where(_da_equipe(equipe))
                .order_by(MarketingCreative.created_at.desc())
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )
    return {"equipe": equipe, "criativos": [_linha_out(r) for r in linhas]}


@router.get("/roteiros")
async def listar_roteiros(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A tela de roteiros: só as linhas que têm briefing escrito.

    Quem escreve é a equipe interna, no DaVinci. Aqui é leitura — não existe
    rota de escrita de roteiro neste arquivo, de propósito.
    """
    linhas = (
        (
            await session.execute(
                select(MarketingCreative)
                .options(selectinload(MarketingCreative.files))
                .where(
                    _da_equipe(equipe),
                    MarketingCreative.roteiro.is_not(None),
                    func.length(func.trim(MarketingCreative.roteiro)) > 0,
                )
                .order_by(MarketingCreative.created_at.desc())
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )
    return {"equipe": equipe, "roteiros": [_linha_out(r) for r in linhas]}


@router.post("/criativos/{creative_id}/arquivo")
async def enviar_arquivo(
    creative_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Anexa vídeo numa linha da PRÓPRIA equipe.

    Mesmas guardas do caminho interno, e os mesmos tetos importados de lá pra
    não divergirem com o tempo: grava em streaming (nunca o arquivo inteiro na
    memória), aborta e apaga ao passar do teto, e o sha256 sai do mesmo fluxo
    de bytes — é ele que denuncia o mesmo vídeo publicado em duas marcas.
    """
    row = await _linha_da_equipe(session, creative_id, equipe)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.files) + len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    base = _file_dir(row)
    base.mkdir(parents=True, exist_ok=True)
    existentes = {f.file_name: f for f in row.files}
    entraram: list[str] = []

    for up in files:
        nome = Path(up.filename or "arquivo").name
        if not nome or nome in {".", ".."}:
            raise HTTPException(400, detail={"code": "nome_invalido"})
        caminho = base / nome
        digest = sha256()
        escrito = 0
        with caminho.open("wb") as fh:
            while pedaco := up.file.read(1024 * 1024):
                escrito += len(pedaco)
                if escrito > MAX_BYTES_ARQUIVO:
                    fh.close()
                    caminho.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        detail={
                            "code": "arquivo_grande_demais",
                            "arquivo": nome,
                            "max_mb": MAX_BYTES_ARQUIVO // (1024 * 1024),
                        },
                    )
                digest.update(pedaco)
                fh.write(pedaco)

        antigo = existentes.get(nome)
        if antigo is not None:
            row.files.remove(antigo)
        rec = MarketingCreativeFile(
            id=uuid4(),
            file_name=nome,
            file_mime=up.content_type or "application/octet-stream",
            file_size=caminho.stat().st_size,
            file_rel=f"creatives/{row.id}/{nome}",
            sha256=digest.hexdigest(),
        )
        row.files.append(rec)
        existentes[nome] = rec
        entraram.append(nome)

    # Arquivo novo volta a linha pra "pendente" — mesmo comportamento do
    # caminho interno. A agência precisa ver isso na tela dela, senão parece
    # que a aprovação foi desfeita sem motivo.
    row.aprovado = None
    await session.commit()
    logger.info("portal_upload", creative_id=str(row.id), equipe=equipe, arquivos=entraram)
    return _linha_out(row)
