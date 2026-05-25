"""Financeiro CRUD — Consórcio, Suprimentos, Simulação (cotações de
importação) e cache de NCM.

NCM: descrição vem da brasilapi (pública, sem auth); alíquotas II/IPI/
PIS/COFINS NÃO existem em API pública confiável, então o operador
preenche manualmente na primeira consulta de cada NCM e o valor fica
cacheado pras próximas. Isso evita inventar números.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import aiofiles
import httpx
import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    DNPConfig,
    DNPProduto,
    FinanceiroConsorcio,
    FinanceiroSimulacao,
    FinanceiroSuprimentos,
    NCMCache,
    User,
)
from app.schemas.financeiro import (
    ConsorcioOut,
    ConsorcioPatch,
    DNPConfigOut,
    DNPConfigPatch,
    DNPProdutoOut,
    DNPProdutoPatch,
    NCMOut,
    NCMPatch,
    SimulacaoOut,
    SimulacaoPatch,
    SuprimentosOut,
    SuprimentosPatch,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/financeiro", tags=["financeiro"])


_BRASILAPI_NCM = "https://brasilapi.com.br/api/ncm/v1/{codigo}"


# ── Consórcio ──────────────────────────────────────────────────────────


@router.get("/consorcio", response_model=list[ConsorcioOut])
async def list_consorcio(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_consorcio", "view"))],
) -> list[ConsorcioOut]:
    rows = (
        await session.execute(
            select(FinanceiroConsorcio).order_by(
                FinanceiroConsorcio.emp.asc().nulls_last(),
                FinanceiroConsorcio.grupo.asc().nulls_last(),
                FinanceiroConsorcio.cota.asc().nulls_last(),
                FinanceiroConsorcio.created_at.asc(),
            )
        )
    ).scalars().all()
    return [ConsorcioOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/consorcio", response_model=ConsorcioOut, status_code=status.HTTP_201_CREATED)
async def create_consorcio(
    body: ConsorcioPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_consorcio", "edit"))],
) -> ConsorcioOut:
    row = FinanceiroConsorcio(**body.model_dump(exclude_unset=True))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ConsorcioOut.model_validate(row, from_attributes=True)


@router.patch("/consorcio/{row_id}", response_model=ConsorcioOut)
async def patch_consorcio(
    row_id: UUID,
    body: ConsorcioPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_consorcio", "edit"))],
) -> ConsorcioOut:
    row = (
        await session.execute(
            select(FinanceiroConsorcio).where(FinanceiroConsorcio.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "consorcio_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return ConsorcioOut.model_validate(row, from_attributes=True)


@router.delete("/consorcio/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_consorcio(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_consorcio", "delete"))],
) -> None:
    row = (
        await session.execute(
            select(FinanceiroConsorcio).where(FinanceiroConsorcio.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "consorcio_not_found"})
    await session.delete(row)
    await session.commit()
    return None


# ── Suprimentos ────────────────────────────────────────────────────────


@router.get("/suprimentos", response_model=list[SuprimentosOut])
async def list_suprimentos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_suprimentos", "view"))],
) -> list[SuprimentosOut]:
    rows = (
        await session.execute(
            select(FinanceiroSuprimentos).order_by(
                FinanceiroSuprimentos.produto.asc().nulls_last(),
                FinanceiroSuprimentos.modelo.asc().nulls_last(),
                FinanceiroSuprimentos.created_at.asc(),
            )
        )
    ).scalars().all()
    return [SuprimentosOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/suprimentos", response_model=SuprimentosOut, status_code=status.HTTP_201_CREATED)
async def create_suprimentos(
    body: SuprimentosPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_suprimentos", "edit"))],
) -> SuprimentosOut:
    row = FinanceiroSuprimentos(**body.model_dump(exclude_unset=True))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return SuprimentosOut.model_validate(row, from_attributes=True)


@router.patch("/suprimentos/{row_id}", response_model=SuprimentosOut)
async def patch_suprimentos(
    row_id: UUID,
    body: SuprimentosPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_suprimentos", "edit"))],
) -> SuprimentosOut:
    row = (
        await session.execute(
            select(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "suprimentos_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return SuprimentosOut.model_validate(row, from_attributes=True)


@router.delete("/suprimentos/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suprimentos(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_suprimentos", "delete"))],
) -> None:
    row = (
        await session.execute(
            select(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "suprimentos_not_found"})
    await session.delete(row)
    await session.commit()
    return None


# ── Simulação ─────────────────────────────────────────────────────────


@router.get("/simulacao", response_model=list[SimulacaoOut])
async def list_simulacao(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "view"))],
) -> list[SimulacaoOut]:
    rows = (
        await session.execute(
            select(FinanceiroSimulacao).order_by(
                desc(FinanceiroSimulacao.created_at)
            )
        )
    ).scalars().all()
    return [SimulacaoOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/simulacao/{row_id}", response_model=SimulacaoOut)
async def get_simulacao(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "view"))],
) -> SimulacaoOut:
    row = (
        await session.execute(
            select(FinanceiroSimulacao).where(FinanceiroSimulacao.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "simulacao_not_found"})
    return SimulacaoOut.model_validate(row, from_attributes=True)


@router.post("/simulacao", response_model=SimulacaoOut, status_code=status.HTTP_201_CREATED)
async def create_simulacao(
    body: SimulacaoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "edit"))],
) -> SimulacaoOut:
    # Defaults da planilha-mãe (constantes da empresa) só aplicam se o
    # cliente não mandou nada — uma vez criado, o operador edita à mão.
    data = body.model_dump(exclude_unset=True)
    data.setdefault("aliquota_taxas_gerais", 0.03)
    data.setdefault("aliquota_impostos_fed", 0.035)
    data.setdefault("aliquota_icms", 0.04)
    data.setdefault("aliquota_intermediacao", 0.16)
    row = FinanceiroSimulacao(**data)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return SimulacaoOut.model_validate(row, from_attributes=True)


@router.patch("/simulacao/{row_id}", response_model=SimulacaoOut)
async def patch_simulacao(
    row_id: UUID,
    body: SimulacaoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "edit"))],
) -> SimulacaoOut:
    row = (
        await session.execute(
            select(FinanceiroSimulacao).where(FinanceiroSimulacao.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "simulacao_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return SimulacaoOut.model_validate(row, from_attributes=True)


@router.delete("/simulacao/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_simulacao(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "delete"))],
) -> None:
    row = (
        await session.execute(
            select(FinanceiroSimulacao).where(FinanceiroSimulacao.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "simulacao_not_found"})
    await session.delete(row)
    await session.commit()
    return None


# ── NCM ────────────────────────────────────────────────────────────────


def _normalize_ncm(raw: str) -> str:
    """Aceita '85094010' ou '8509.40.10' — devolve só os dígitos."""
    return "".join(ch for ch in (raw or "") if ch.isdigit())


async def _fetch_brasilapi_descricao(codigo: str) -> str | None:
    """Bate em brasilapi.com.br/api/ncm/v1/{codigo}. Retorna a descrição
    formatada ou None se a API recusar/falhar. Não levanta exceção —
    cair pra None faz o front simplesmente não preencher esse campo."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(_BRASILAPI_NCM.format(codigo=codigo))
        if r.status_code != 200:
            return None
        payload = r.json() or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("ncm_brasilapi_failed", ncm=codigo, err=str(e)[:200])
        return None
    # Brasilapi retorna {codigo, descricao, data_inicio, data_fim,
    # tipo_ato_ini, numero_ato_ini, ano_ato_ini}. Concatenamos numa
    # linha legível pro front.
    desc = (payload.get("descricao") or "").strip()
    vigencia = payload.get("data_inicio")
    if desc and vigencia:
        return f"{desc} — Vigência desde: {vigencia}"
    return desc or None


@router.get("/ncm/{codigo}", response_model=NCMOut)
async def lookup_ncm(
    codigo: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "view"))],
) -> NCMOut:
    """Lookup de NCM. Fluxo:
      1. Procura no cache (davinci.ncm_cache).
      2. Se acha → devolve direto (cached=True).
      3. Se NÃO acha → bate na brasilapi pra descrição, cria a linha
         no cache com alíquotas NULL e devolve (cached=False).
      4. Operador edita as alíquotas via PATCH /ncm/{codigo}.
    """
    ncm = _normalize_ncm(codigo)
    if not ncm:
        raise HTTPException(400, detail={"code": "ncm_vazio"})

    row = (
        await session.execute(select(NCMCache).where(NCMCache.ncm == ncm))
    ).scalar_one_or_none()
    if row is not None:
        out = NCMOut.model_validate(row, from_attributes=True)
        out.cached = True
        return out

    descricao = await _fetch_brasilapi_descricao(ncm)
    row = NCMCache(ncm=ncm, descricao=descricao)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    out = NCMOut.model_validate(row, from_attributes=True)
    out.cached = False
    return out


@router.patch("/ncm/{codigo}", response_model=NCMOut)
async def patch_ncm(
    codigo: str,
    body: NCMPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "edit"))],
) -> NCMOut:
    """Operador edita as alíquotas do NCM cacheado. Persiste pra que a
    próxima cotação com o mesmo NCM já venha preenchida."""
    ncm = _normalize_ncm(codigo)
    row = (
        await session.execute(select(NCMCache).where(NCMCache.ncm == ncm))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "ncm_not_cached"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(row)
    out = NCMOut.model_validate(row, from_attributes=True)
    out.cached = True
    return out


# ── DNP — Desenvolvimento de Produtos ──────────────────────────────────


@router.get("/dnp/config", response_model=DNPConfigOut)
async def get_dnp_config(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "view"))],
) -> DNPConfigOut:
    """Singleton (id=1) com dolar_dia + certificado. A página atualiza
    o dolar via awesomeapi do lado do cliente; o backend só persiste
    o último valor confirmado pelo operador."""
    row = await session.get(DNPConfig, 1)
    if row is None:
        # Defensa: a migration insere a linha, mas se alguém deletou
        # o registro recria com defaults.
        row = DNPConfig(id=1, dolar_dia=None, certificado=10000)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return DNPConfigOut.model_validate(row, from_attributes=True)


@router.patch("/dnp/config", response_model=DNPConfigOut)
async def patch_dnp_config(
    body: DNPConfigPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "edit"))],
) -> DNPConfigOut:
    row = await session.get(DNPConfig, 1)
    if row is None:
        row = DNPConfig(id=1)
        session.add(row)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return DNPConfigOut.model_validate(row, from_attributes=True)


@router.get("/dnp/produtos", response_model=list[DNPProdutoOut])
async def list_dnp(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "view"))],
) -> list[DNPProdutoOut]:
    rows = (
        await session.execute(
            select(DNPProduto).order_by(DNPProduto.created_at.asc())
        )
    ).scalars().all()
    return [DNPProdutoOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/dnp/produtos", response_model=DNPProdutoOut, status_code=status.HTTP_201_CREATED)
async def create_dnp(
    body: DNPProdutoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "edit"))],
) -> DNPProdutoOut:
    # Defaults da planilha-mãe pra novo produto.
    data = body.model_dump(exclude_unset=True)
    data.setdefault("projecao_compra", 500)
    data.setdefault("fator", 2)
    data.setdefault("frete", 30)
    data.setdefault("comissao", 0.14)
    row = DNPProduto(**data)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DNPProdutoOut.model_validate(row, from_attributes=True)


@router.patch("/dnp/produtos/{row_id}", response_model=DNPProdutoOut)
async def patch_dnp(
    row_id: UUID,
    body: DNPProdutoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "edit"))],
) -> DNPProdutoOut:
    row = (
        await session.execute(select(DNPProduto).where(DNPProduto.id == row_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "dnp_produto_not_found"})
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return DNPProdutoOut.model_validate(row, from_attributes=True)


@router.delete("/dnp/produtos/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dnp(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "delete"))],
) -> None:
    row = (
        await session.execute(select(DNPProduto).where(DNPProduto.id == row_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "dnp_produto_not_found"})
    # Best-effort cleanup of the photo on disk. If the file is already
    # gone the delete still succeeds — orphan photos are tolerable, an
    # orphan DB row referencing a missing file is the worse failure
    # mode and the GET endpoint already 404s on FileNotFoundError.
    if row.foto_url:
        await asyncio.to_thread(_unlink_photo, row.foto_url)
    await session.delete(row)
    await session.commit()
    return None


# ── DNP — Photo upload + serve ─────────────────────────────────────────

# Photos land under ${UPLOADS_DIR}/dnp/{row_id}.{ext}. One image per row
# (replacing on re-upload). We store the relative file path in
# `foto_url` — not a URL — so the GET endpoint is the only consumer and
# the field can't accidentally leak a filesystem path to the client. The
# operator hits /api/financeiro/dnp/produtos/{id}/foto to render the
# thumbnail and the lightbox both.
_PHOTO_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB — generous for product shots
_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _unlink_photo(rel_path: str) -> None:
    p = Path(get_settings().uploads_dir) / rel_path
    try:
        p.unlink()
    except FileNotFoundError:
        pass


@router.post(
    "/dnp/produtos/{row_id}/foto",
    response_model=DNPProdutoOut,
    status_code=status.HTTP_200_OK,
)
async def upload_dnp_foto(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "edit"))],
    file: Annotated[UploadFile, File(...)],
) -> DNPProdutoOut:
    row = (
        await session.execute(select(DNPProduto).where(DNPProduto.id == row_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "dnp_produto_not_found"})

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _PHOTO_SUFFIXES:
        raise HTTPException(400, detail={
            "code": "unsupported_image_type",
            "allowed": sorted(_PHOTO_SUFFIXES),
        })

    content = await file.read()
    if len(content) > _PHOTO_MAX_BYTES:
        raise HTTPException(413, detail={"code": "file_too_large"})

    base = Path(get_settings().uploads_dir) / "dnp"
    await asyncio.to_thread(base.mkdir, parents=True, exist_ok=True)
    rel = f"dnp/{row.id}{suffix}"
    abs_path = Path(get_settings().uploads_dir) / rel

    # Drop the previous photo (different extension if the user re-uploads
    # a different format) so we don't accumulate orphans on disk.
    if row.foto_url and row.foto_url != rel:
        await asyncio.to_thread(_unlink_photo, row.foto_url)

    async with aiofiles.open(abs_path, "wb") as f:
        await f.write(content)

    row.foto_url = rel
    await session.commit()
    await session.refresh(row)
    logger.info("dnp_foto_uploaded", row_id=str(row.id), size=len(content), suffix=suffix)
    return DNPProdutoOut.model_validate(row, from_attributes=True)


@router.get("/dnp/produtos/{row_id}/foto")
async def get_dnp_foto(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "view"))],
) -> FileResponse:
    row = (
        await session.execute(select(DNPProduto).where(DNPProduto.id == row_id))
    ).scalar_one_or_none()
    if row is None or not row.foto_url:
        raise HTTPException(404, detail={"code": "no_photo"})
    abs_path = Path(get_settings().uploads_dir) / row.foto_url
    if not abs_path.exists():
        raise HTTPException(404, detail={"code": "photo_missing_on_disk"})
    return FileResponse(abs_path)


@router.delete("/dnp/produtos/{row_id}/foto", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dnp_foto(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_dnp", "edit"))],
) -> None:
    row = (
        await session.execute(select(DNPProduto).where(DNPProduto.id == row_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "dnp_produto_not_found"})
    if row.foto_url:
        await asyncio.to_thread(_unlink_photo, row.foto_url)
        row.foto_url = None
        await session.commit()
    return None
