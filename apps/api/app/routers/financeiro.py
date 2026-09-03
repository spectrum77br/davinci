"""Financeiro CRUD — Consórcio, Suprimentos, Simulação (cotações de
importação) e cache de NCM.

NCM: descrição vem da brasilapi (pública, sem auth); alíquotas II/IPI/
PIS/COFINS NÃO existem em API pública confiável, então o operador
preenche manualmente na primeira consulta de cada NCM e o valor fica
cacheado pras próximas. Isso evita inventar números.
"""

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import aiofiles
import httpx
import structlog
from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_admin, require_permission
from app.models import (
    DNPConfig,
    DNPProduto,
    FinanceiroConsorcio,
    FinanceiroSimulacao,
    FinanceiroSuprimentos,
    ImportLote,
    NCMCache,
    User,
)
from app.schemas.financeiro import (
    ComercialEmpresaOut,
    ComercialMembroOut,
    ComercialSecaoOut,
    ConsorcioOut,
    ConsorcioPatch,
    DNPConfigOut,
    DNPConfigPatch,
    DNPProdutoOut,
    DNPProdutoPatch,
    EstoqueBlingLocalOut,
    EstoqueBlingSnapshotOut,
    FaturamentoGrpLinhaOut,
    FaturamentoMesSecaoOut,
    NCMOut,
    NCMPatch,
    OperacionalLinhaOut,
    OperacionalSecaoOut,
    SaldoMarketplaceCelulaOut,
    SaldoMarketplaceLojaOut,
    SaldoMarketplaceSnapshotOut,
    SimulacaoOut,
    SimulacaoPatch,
    SuprimentosOut,
    SuprimentosPatch,
    ValuationMesOut,
    ValuationReportOut,
    ValuationUnlockIn,
    ValuationUnlockOut,
)
from app.services.bling_situacoes import SITUACOES_ENVIADO_ETIQUETA_STR

logger = structlog.get_logger()
router = APIRouter(prefix="/api/financeiro", tags=["financeiro"])

_SCHEMA = get_settings().database_schema


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
    # Taxas fixas brasileiras (BRL) — spec da aba `simulaçao`. Defaults
    # pré-preenchidos mas editáveis por cotação.
    data.setdefault("taxa_siscomex_brl", Decimal("154.23"))
    data.setdefault("taxa_bl_brl", Decimal("3300"))
    data.setdefault("armazenagem_brl", Decimal("3644.18"))
    data.setdefault("despachante_sda_brl", Decimal("500"))
    data.setdefault("despachante_honorarios_brl", Decimal("1688"))
    data.setdefault("corretagem_cambio_brl", Decimal("70"))
    data.setdefault("inspecao_brl", Decimal("200"))
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


async def _simulacao_or_404(session: AsyncSession, row_id: UUID) -> FinanceiroSimulacao:
    row = (
        await session.execute(
            select(FinanceiroSimulacao).where(FinanceiroSimulacao.id == row_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "simulacao_not_found"})
    return row


@router.get("/simulacao/{row_id}/pdf")
async def simulacao_pdf(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "view"))],
) -> Response:
    """PDF da simulação (imprimível). Sempre gerado ao vivo dos dados
    atuais — o congelado fica no lote (anexar-lote)."""
    from app.services.simulacao_pdf import PDF_MEDIA, montar_pdf

    row = await _simulacao_or_404(session, row_id)
    lote_nome = None
    if row.lote_id is not None:
        lote = await session.get(ImportLote, row.lote_id)
        lote_nome = lote.nome if lote is not None else None
    pdf = montar_pdf(row, lote_nome=lote_nome)
    nome = (row.numero_cotacao or str(row.id)[:8]).replace('"', "")
    return Response(
        content=pdf,
        media_type=PDF_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="simulacao_{nome}.pdf"'
        },
    )


@router.post("/simulacao/{row_id}/anexar-lote", response_model=SimulacaoOut)
async def simulacao_anexar_lote(
    row_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_permission("financeiro_simulacao", "edit"))],
) -> SimulacaoOut:
    """Congela o PDF da simulação no lote vinculado (`import_lotes.
    simulacao_pdf`). Spec: "se não tiver o lote não envia"."""
    from app.services.simulacao_pdf import montar_pdf

    row = await _simulacao_or_404(session, row_id)
    if row.lote_id is None:
        raise HTTPException(422, detail={"code": "simulacao_sem_lote"})
    lote = await session.get(ImportLote, row.lote_id)
    if lote is None:
        raise HTTPException(404, detail={"code": "lote_not_found"})
    lote.simulacao_pdf = montar_pdf(row, lote_nome=lote.nome)
    await session.commit()
    await session.refresh(row)
    return SimulacaoOut.model_validate(row, from_attributes=True)


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


# ── Valuation (relatório 3 meses) ────────────────────────────────────────
# Porta web do antigo PDF faturamento_3meses_pdf.py (projeto ClaudeCode).
# As tabelas davinci.bling_orders e davinci.valuation são atualizadas pela
# rotina diária das 5h; aqui só consultamos ao vivo — nada é agendado.

# Bling situação IDs que entram no FATURAMENTO da janela de 3 meses.
_VAL_SIT_APLICAVEIS = [
    "6", "28",   # Em aberto
    "15", "37",  # Em andamento
    "83953",     # Entregue
    "84674",     # Enviado Geral SP
    "84675",     # Enviado Geral CI
    "83959",     # Enviado Geral PI
    "83958",     # Enviado Fake (nome real na situacao_bling)
    "84678",     # Enviado Geral SA
    # Etiqueta enviada: 21 = Em digitação (canônico desde 03/09/2026);
    # 83965 = Enviado Etiqueta / Enviado Importado (legado).
    *SITUACOES_ENVIADO_ETIQUETA_STR,
]
# Situações que entram em RENTABILIDADE / Custo / Margem: Em aberto (6,28) +
# Em andamento (15,37) + Entregue (83953). Mais amplo que só-Entregue — inclui
# pedidos em trânsito cuja margem ainda não foi realizada. Mesmo conjunto do
# export de rentabilidade (margens.py / rentabilidade_xlsx).
_VAL_SIT_RENTABILIDADE = ["6", "28", "15", "37", "83953"]
# Situações da MARGEM OPERACIONAL por categoria/plataforma (blueprint "status a
# considerar"): Em andamento (15,37) + Entregue (83953) + Perdimento (83956) +
# Resolvido (545902) + Enviado Fake (83958). % = (lucro ÷ faturamento) × 100,
# faturamento e custo somados sobre ESTE mesmo conjunto.
_VAL_SIT_MARGEM = ["15", "37", "83953", "83956", "545902", "83958"]
_VAL_SIT_LABEL = (
    "Em aberto, Em andamento, Entregue, "
    "Enviado Geral SP/CI/PI/RA/SA, Em digitação, Enviado Importado"
)
# Quadro "Operacional — 3 meses" (substitui a antiga Eficácia). Linhas diretas
# de situação (faturamento por mês da data do pedido) + o conjunto que serve de
# denominador (faturamento) da Taxa de Perdimento.
_VAL_SIT_PROBLEMAS = "83960"    # Problemas
_VAL_SIT_PERDIMENTO = "83956"   # Perdimento

# Quadro "Comercial — 3 meses" (irmão do Operacional). Situações de saldo por
# mês da data do pedido + Taxa de Devolução (devoluções ÷ entregues).
_VAL_SIT_AGUARD_DEVOLUCAO = "83957"      # Aguardando Devolução
_VAL_SIT_ENTREGUE = "83953"              # Entregue

# Explicação da fórmula de cada linha — tooltip "informativo" no hover (front).
_OPER_DESCRICOES = {
    "problemas": (
        "Soma do valor base dos pedidos na situação 'Problemas', "
        "agrupada pelo mês da data do pedido."
    ),
    "perdimento": (
        "Soma do preço de custo (preço de custo × quantidade) dos pedidos na situação "
        "'Perdimento', agrupada pelo mês da data do pedido."
    ),
    "reembolso": (
        "Soma dos reembolsos conferidos (positivos somam, negativos subtraem), "
        "agrupada pelo mês em que o reembolso foi marcado como conferido."
    ),
    "usado": (
        "Soma do preço de custo dos produtos devolvidos na condição 'Usado', "
        "agrupada pelo mês de criação da devolução."
    ),
    "manutencao": (
        "Soma do preço de custo dos produtos devolvidos na condição 'Manutenção', "
        "agrupada pelo mês de criação da devolução."
    ),
    "custo_manutencao": (
        "Soma dos custos de manutenção lançados nas devoluções, "
        "agrupada pelo mês de criação da devolução."
    ),
    "giro_venda": (
        "Custo de venda dos produtos do mês (preço de custo × quantidade, "
        "pedidos em Em aberto, Em andamento ou Entregue) dividido pelo valor "
        "do estoque do mês (snapshot do último dia, da tabela valuation), em %. "
        "Indica quanto do estoque girou em vendas no mês."
    ),
}

# Explicação da fórmula de cada linha do quadro "Comercial" (tooltip no hover).
_COM_DESCRICOES = {
    "aguardando_devolucao": (
        "Soma do valor base dos pedidos na situação 'Aguardando Devolução', "
        "pelo mês da data do pedido. Nos quadros por equipe, apenas as lojas da "
        "equipe (loja do Bling → equipe de vendas em store_info.sales_team)."
    ),
    "taxa_devolucao": (
        "Quantidade de PEDIDOS devolvidos (com devolução condição Novo ou Usado, "
        "contados uma vez por número de pedido do Bling, pelo mês de criação da "
        "devolução) dividida pela quantidade de pedidos do faturamento (situações "
        "aplicáveis, pelo mês da data do pedido), em %. Um kit devolvido vira "
        "várias linhas com o mesmo pedido, mas conta 1. A equipe da devolução vem "
        "do pedido de origem (pedido → loja do Bling → equipe)."
    ),
}

# Lojas internas/ignoradas — mesmas que bling_sync.IGNORED_STORES descarta no
# ingest. Não entram em faturamento/custo/rentabilidade da página Valuation.
# loja 0 = sem loja; 205632678 / 205660518 = lojas internas.
_VAL_IGNORED_STORES = ["0", "205632678", "205660518"]


def _qt(name: str) -> str:
    """Tabela qualificada pelo schema (mesma convenção de refunds.py)."""
    return f'"{_SCHEMA}".{name}'


def _r2(v) -> float | None:
    """Arredonda p/ 2 casas em float (JSON number limpo). None → None."""
    if v is None:
        return None
    return round(float(v), 2)


def _agg_sql(group_col: str) -> str:
    """SQL de faturamento/custo/rentabilidade por mês × {marketplace|categoria}.

    Réplica fiel da QUERY_AGG do PDF. `group_col` é literal do nosso código
    ('marketplace' ou 'categoria'), nunca entrada do usuário — interpolar é
    seguro. `faturamento` = SUM das situações aplicáveis; `faturamento_rent`/
    `custo_rent` = Em aberto+Em andamento+Entregue (:sit_rent, alimenta o card
    mensal de rentabilidade); `fat_margem`/`custo_margem` = conjunto da MARGEM
    operacional (:sit_margem = Em andamento+Entregue+Perdimento+Resolvido+
    Enviado Fake), que alimenta as seções por categoria/plataforma com
    lucro = faturamento − custo e % = (lucro ÷ faturamento) × 100. Rateio
    proporcional ao itemvalor quando o pedido tem >1 item.
    """
    bo, stores = _qt("bling_orders"), _qt("stores")
    return f"""
WITH bo_base AS (
    SELECT bo.*,
           (bo.data AT TIME ZONE 'America/Sao_Paulo')::date AS data_sp,
           COALESCE(NULLIF(bo.valorbase, 0), bo.total) AS valorbase_eff
    FROM {bo} bo
    WHERE (bo.data AT TIME ZONE 'America/Sao_Paulo')::date
          >= date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) - INTERVAL '2 months')::date
      AND (bo.data AT TIME ZONE 'America/Sao_Paulo')::date
          <  date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) + INTERVAL '1 month')::date
      AND COALESCE(bo.loja, '') <> ALL(:ignored_stores)
),
order_totals AS (
    SELECT numero,
           COUNT(*) AS total_items,
           SUM(COALESCE(itemvalor, 0)) AS total_itemvalor_pedido
    FROM bo_base
    WHERE (item_index > 0 OR (item_index = 0 AND itemvalor IS NOT NULL))
      AND valorbase_eff > 0
    GROUP BY numero
),
prop AS (
    SELECT
        bo.numero, bo.data_sp, bo.loja, bo.situacao, bo.categoria_nome,
        bo.item_quantidade, bo.preco_custo,
        CASE
            WHEN COALESCE(ot.total_items, 1) = 1 THEN bo.valorbase_eff
            WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                THEN bo.valorbase_eff * (bo.itemvalor / ot.total_itemvalor_pedido)
            ELSE bo.valorbase_eff / COALESCE(ot.total_items, 1)::numeric
        END AS valorbase_prop,
        CASE
            WHEN COALESCE(ot.total_items, 1) = 1 THEN bo.custofrete
            WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                THEN bo.custofrete * (bo.itemvalor / ot.total_itemvalor_pedido)
            ELSE bo.custofrete / COALESCE(ot.total_items, 1)::numeric
        END AS custofrete_prop,
        CASE
            WHEN COALESCE(ot.total_items, 1) = 1 THEN bo.taxacomissao
            WHEN ot.total_itemvalor_pedido > 0 AND bo.itemvalor IS NOT NULL
                THEN bo.taxacomissao * (bo.itemvalor / ot.total_itemvalor_pedido)
            ELSE bo.taxacomissao / COALESCE(ot.total_items, 1)::numeric
        END AS taxacomissao_prop
    FROM bo_base bo
    LEFT JOIN order_totals ot ON bo.numero = ot.numero
    WHERE bo.valorbase_eff > 0
),
base AS (
    SELECT
        date_trunc('month', p.data_sp)::date AS mes,
        COALESCE(NULLIF(TRIM(l.marketplace::text), ''), 'Sem marketplace') AS marketplace,
        -- Buckets da planta não têm "Usado": Celular Usado → Celular e
        -- Mala Usada → Mala (kits já caem em Celular Kit / Mala Kit).
        CASE lower(COALESCE(NULLIF(TRIM(p.categoria_nome), ''), 'sem categoria'))
            WHEN 'celular usado' THEN 'Celular'
            WHEN 'mala usada'    THEN 'Mala'
            ELSE COALESCE(NULLIF(TRIM(p.categoria_nome), ''), 'Sem categoria')
        END AS categoria,
        p.situacao AS situacao_id,
        p.valorbase_prop AS valorbase,
        -- Custo só de PRODUTO (preço custo × qtd), sem frete/comissão —
        -- numerador do Giro por categoria (mesma definição do Giro do
        -- Operacional).
        (COALESCE(p.preco_custo, 0) * COALESCE(p.item_quantidade, 0)) AS custo_produto,
        (COALESCE(p.preco_custo, 0) * COALESCE(p.item_quantidade, 0)
            + COALESCE(p.custofrete_prop, 0)
            + COALESCE(p.taxacomissao_prop, 0)) AS custo_total
    FROM prop p
    LEFT JOIN {stores} l ON l.bling_store_id::text = p.loja
)
SELECT
    mes,
    {group_col} AS grp,
    SUM(CASE WHEN situacao_id = ANY(:sit_aplic) THEN valorbase ELSE 0 END) AS faturamento,
    SUM(CASE WHEN situacao_id = ANY(:sit_rent) THEN valorbase   ELSE 0 END) AS faturamento_rent,
    SUM(CASE WHEN situacao_id = ANY(:sit_rent) THEN custo_total ELSE 0 END) AS custo_rent,
    SUM(CASE WHEN situacao_id = ANY(:sit_margem) THEN valorbase   ELSE 0 END) AS fat_margem,
    SUM(CASE WHEN situacao_id = ANY(:sit_margem) THEN custo_total ELSE 0 END) AS custo_margem,
    SUM(CASE WHEN situacao_id = ANY(:sit_giro) THEN custo_produto ELSE 0 END) AS custo_giro
FROM base
GROUP BY mes, {group_col}
ORDER BY mes, grp
"""


def _val_window_months() -> list:
    """3 primeiros-dias-do-mês (mês-2, mês-1, mês atual), tz São Paulo."""
    from datetime import date as _date
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    first = today.replace(day=1)
    months = []
    for back in (2, 1, 0):
        y, m = first.year, first.month - back
        while m <= 0:
            m += 12
            y -= 1
        months.append(_date(y, m, 1))
    return months


# Locais físicos do Estoque Bling que compõem o bucket "Celular" (o estoque de
# Celular é a SOMA dos 7 armazéns). Mala e Eletro têm bucket próprio no snapshot.
_GIRO_CELULAR_LOCAIS = ("PI", "SA", "SP", "RA", "CD", "CI", "US")


def _estoque_bucket_categoria(grp: str) -> str | None:
    """Bucket de Estoque Bling do Giro por categoria.

    Celular / Celular Kit / Acessórios Celular / Insumos → "Celular" (não têm
    bucket próprio, ficam dentro de Celular); Mala / Mala Kit → "Mala";
    Eletro / Eletro Kit → "Eletro"; qualquer outra → None (sem giro)."""
    base = grp[:-4] if grp.endswith(" Kit") else grp
    b = base.strip().lower()
    if b in ("celular", "acessórios celular", "acessorios celular", "insumos"):
        return "Celular"
    if b == "mala":
        return "Mala"
    if b == "eletro":
        return "Eletro"
    return None


def _agg_to_secoes(
    rows: list[dict],
    months: list,
    estoque_by_mes: dict | None = None,
) -> list[FaturamentoMesSecaoOut]:
    """Agrupa as linhas da _agg_sql em uma seção por mês (3 meses).

    Quando `estoque_by_mes` é passado (bloco por CATEGORIA), calcula o Giro:
    giro = (custo dos produtos vendidos da categoria ÷ estoque Bling do bucket
    da categoria no fim daquele mês) × 100. O estoque vem do snapshot
    `valuation_estoque_bling_diario` (último dia com dado do mês). Celular =
    soma dos 7 armazéns; Mala/Eletro bucket próprio; kit e Acessórios/Insumos
    caem em Celular. Meses sem snapshot (ex. antes de 23/06) ficam sem giro."""
    by_mes: dict = {m: [] for m in months}
    for r in rows:
        if r["mes"] in by_mes:
            by_mes[r["mes"]].append(r)
    out: list[FaturamentoMesSecaoOut] = []
    for mes in months:
        est_mes = estoque_by_mes.get(mes) if estoque_by_mes else None
        # Estoque total da seção (denominador do Giro total) = soma dos buckets
        # distintos com dado neste mês.
        total_estoque = (
            sum(est_mes.values(), Decimal("0")) if est_mes else Decimal("0")
        )
        linhas: list[FaturamentoGrpLinhaOut] = []
        tot_fat = tot_custo = tot_rent = tot_giro_valor = Decimal("0")
        for r in sorted(by_mes[mes], key=lambda x: (x["grp"] or "")):
            if not r["grp"]:
                continue
            # Margem operacional (blueprint): faturamento e custo somados sobre
            # o conjunto _VAL_SIT_MARGEM; lucro = faturamento − custo;
            # % = (lucro ÷ faturamento) × 100.
            fat = Decimal(str(r["fat_margem"] or 0))
            custo = Decimal(str(r["custo_margem"] or 0))
            rent = fat - custo
            margem = (rent / fat * 100) if fat != 0 else None
            giro = None
            if estoque_by_mes is not None:
                giro_valor = Decimal(str(r.get("custo_giro") or 0))
                bucket = _estoque_bucket_categoria(r["grp"])
                est = est_mes.get(bucket) if est_mes and bucket else None
                if est and Decimal(str(est)) != 0:
                    giro = giro_valor / Decimal(str(est)) * 100
                tot_giro_valor += giro_valor
            linhas.append(FaturamentoGrpLinhaOut(
                grp=r["grp"], faturamento=_r2(fat), custo=_r2(custo),
                rentabilidade=_r2(rent), margem=_r2(margem), giro=_r2(giro),
            ))
            tot_fat += fat
            tot_custo += custo
            tot_rent += rent
        tot_margem = (tot_rent / tot_fat * 100) if tot_fat != 0 else None
        tot_giro = None
        if estoque_by_mes is not None and total_estoque != 0:
            tot_giro = tot_giro_valor / total_estoque * 100
        out.append(FaturamentoMesSecaoOut(
            mes=mes, linhas=linhas,
            total_faturamento=_r2(tot_fat), total_custo=_r2(tot_custo),
            total_rentabilidade=_r2(tot_rent), total_margem=_r2(tot_margem),
            total_giro=_r2(tot_giro),
        ))
    return out


# ── Senha extra da página Valuation ──────────────────────────────────────
# Camada secundária acima do require_permission: mesmo admin precisa
# digitar a senha. Token = HMAC(jwt_secret, "valuation:<ts>") com TTL.
# Stateless, sem tabela; valida só HMAC + age. Front guarda em
# sessionStorage e envia em X-Valuation-Token a cada GET.


def _make_valuation_token() -> tuple[str, int]:
    """Devolve (token, expires_in_seconds). Formato `<ts>.<sig_hex>`."""
    s = get_settings()
    ttl = s.valuation_unlock_ttl_seconds
    ts = int(time.time())
    msg = f"valuation:{ts}".encode()
    sig = hmac.new(s.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()
    return f"{ts}.{sig}", ttl


def _valid_valuation_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        ts_str, sig = token.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    s = get_settings()
    if time.time() - ts > s.valuation_unlock_ttl_seconds:
        return False
    expected = hmac.new(
        s.jwt_secret.encode(), f"valuation:{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def require_valuation_unlock(
    x_valuation_token: Annotated[str | None, Header(alias="X-Valuation-Token")] = None,
) -> None:
    """Trava em cima do require_permission: exige header com token válido."""
    if not _valid_valuation_token(x_valuation_token):
        raise HTTPException(401, detail={"code": "valuation_locked"})


@router.get("/valuation", response_model=ValuationReportOut)
async def valuation_report(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_admin)],
    _unlocked: Annotated[None, Depends(require_valuation_unlock)] = None,
) -> ValuationReportOut:
    """Relatório de faturamento dos últimos 3 meses (porta web do PDF diário).

    Consulta ao vivo davinci.bling_orders / valuation / stores / situacao_bling
    — sempre reflete o último estado das tabelas (a rotina das 5h as atualiza).
    Seções: Valuation 3 meses, Operacional 3 meses (situações/reembolso/
    devoluções por mês), Comercial 3 meses (aguardando devolução/cancelamento
    e taxa de devolução por mês, quebrado por equipe de vendas), Por
    Marketplace e Por Categoria.
    """
    months = _val_window_months()

    # 1. Valuation por mês:
    #    • SALDOS (caixa/estoque/receber) = snapshot do último dia do mês,
    #      lidos da tabela `valuation` — posições de um instante, não dá p/
    #      recomputar do bling_orders.
    #    • RENTABILIDADE = ao vivo: somada do mesmo _agg_sql que alimenta as
    #      seções "Por Marketplace"/"Por Categoria" abaixo (Em aberto + Em
    #      andamento + Entregue; base − custo − frete − comissão, rateada por
    #      item). Antes vinha da coluna
    #      `valuation.rentabilidade` carimbada pela rotina diária, que ficava
    #      congelada e já sofreu corrupção por embaralhamento de datas.
    val_saldos_sql = text(f"""
        SELECT DISTINCT ON (date_trunc('month', data))
               date_trunc('month', data)::date AS mes,
               caixa, estoque, receber, data AS data_snapshot
        FROM {_qt("valuation")}
        WHERE data >= date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) - INTERVAL '2 months')::date
          AND data <  date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) + INTERVAL '1 month')::date
        ORDER BY date_trunc('month', data), data DESC
    """)
    val_by_mes: dict = {}
    for r in (await session.execute(val_saldos_sql)).mappings().all():
        val_by_mes[r["mes"]] = dict(r)

    # Agregação ao vivo por mês × marketplace/categoria (também usada nas
    # seções 4 e 5 — por isso executada aqui em cima): a rentabilidade do card
    # mensal é a soma dela.
    agg_params = {
        "sit_aplic": _VAL_SIT_APLICAVEIS,
        "sit_rent": _VAL_SIT_RENTABILIDADE,
        "sit_margem": _VAL_SIT_MARGEM,
        "sit_giro": _VAL_SIT_RENTABILIDADE,
        "ignored_stores": _VAL_IGNORED_STORES,
    }
    mkt_rows = (await session.execute(text(_agg_sql("marketplace")), agg_params)).mappings().all()
    cat_rows = (await session.execute(text(_agg_sql("categoria")), agg_params)).mappings().all()

    # Denominador do Giro por categoria: snapshot de Estoque Bling do último dia
    # com dado de cada mês (tabela valuation_estoque_bling_diario, por_local
    # jsonb: PI/SA/SP/RA/CD/CI/US/Eletro/Mala/Outros). Bucket Celular = soma dos
    # 7 armazéns; Mala/Eletro têm bucket próprio. Meses sem snapshot (antes de
    # 23/06) não entram → giro em branco naquele mês.
    estoque_snap_sql = text(f"""
        SELECT DISTINCT ON (date_trunc('month', data))
               date_trunc('month', data)::date AS mes,
               por_local
        FROM {_qt("valuation_estoque_bling_diario")}
        WHERE data >= :ini
        ORDER BY date_trunc('month', data), data DESC
    """)
    estoque_by_mes: dict = {}
    snap_rows = (await session.execute(
        estoque_snap_sql, {"ini": months[0]}
    )).mappings().all()
    for r in snap_rows:
        por_local = r["por_local"] or {}

        def _loc_valor(key: str) -> Decimal:
            v = por_local.get(key) or {}
            return Decimal(str(v.get("valor") or 0))

        celular = sum((_loc_valor(k) for k in _GIRO_CELULAR_LOCAIS), Decimal("0"))
        buckets: dict[str, Decimal] = {}
        if celular != 0:
            buckets["Celular"] = celular
        mala = _loc_valor("Mala")
        if mala != 0:
            buckets["Mala"] = mala
        eletro = _loc_valor("Eletro")
        if eletro != 0:
            buckets["Eletro"] = eletro
        estoque_by_mes[r["mes"]] = buckets

    # Rentabilidade mensal ao vivo = SUM(faturamento_rent − custo_rent) sobre
    # TODAS as linhas do mês (Em aberto+Em andamento+Entregue). Loja sem
    # marketplace cai no grupo 'Sem marketplace' (não é descartada), então o
    # card é o total real e bate com a soma da tabela Por Marketplace.
    rent_by_mes: dict = {}
    for r in mkt_rows:
        delta = (Decimal(str(r["faturamento_rent"] or 0))
                 - Decimal(str(r["custo_rent"] or 0)))
        rent_by_mes[r["mes"]] = rent_by_mes.get(r["mes"], Decimal("0")) + delta

    valuation_meses: list[ValuationMesOut] = []
    for mes in months:
        md = val_by_mes.get(mes, {})
        caixa = md.get("caixa")
        estoque = md.get("estoque")
        receber = md.get("receber")
        total = None
        if any(v is not None for v in (caixa, estoque, receber)):
            total = (Decimal(str(caixa or 0)) + Decimal(str(estoque or 0))
                     + Decimal(str(receber or 0)))
        valuation_meses.append(ValuationMesOut(
            mes=mes, caixa=_r2(caixa), estoque=_r2(estoque), receber=_r2(receber),
            total=_r2(total), rentabilidade=_r2(rent_by_mes.get(mes)),
            data_snapshot=md.get("data_snapshot"),
        ))

    # 2. Quadro "Operacional — 3 meses" (substitui a Eficácia). Tudo ao vivo,
    #    cada métrica agrupada por mês a partir da SUA data:
    #      • situações (Problemas / Perdimento) pela data do pedido;
    #      • Reembolso por refunds.conferido_at (carimbado no patch);
    #      • Usado/Manutenção/Custo Manutenção pelo devolutions.created_at.
    #    Mesma janela 3 meses (mês-2..mês atual, tz SP) das demais seções.
    # Helpers SQL (tz São Paulo): bucket de mês e filtro da janela de 3 meses
    # (mês-2..mês atual) por coluna de data — evita repetir a expressão longa.
    def _mes(col: str) -> str:
        return f"date_trunc('month', ({col} AT TIME ZONE 'America/Sao_Paulo')::date)::date"

    def _janela(col: str) -> str:
        d = f"({col} AT TIME ZONE 'America/Sao_Paulo')::date"
        hoje = "(NOW() AT TIME ZONE 'America/Sao_Paulo')::date"
        return (
            f"{d} >= date_trunc('month', {hoje} - INTERVAL '2 months')::date "
            f"AND {d} < date_trunc('month', {hoje} + INTERVAL '1 month')::date"
        )

    oper_bo_sql = text(f"""
        WITH por_pedido AS (
            SELECT bo.numero,
                   {_mes('bo.data')} AS mes,
                   MAX(bo.situacao) AS situacao,
                   MAX(COALESCE(NULLIF(bo.valorbase, 0), bo.total)) AS valorbase,
                   SUM(COALESCE(bo.preco_custo, 0)
                       * COALESCE(bo.item_quantidade, 0)) AS custo_produto
            FROM {_qt("bling_orders")} bo
            WHERE {_janela('bo.data')}
            GROUP BY bo.numero, mes
        )
        SELECT mes,
               SUM(valorbase) FILTER (WHERE situacao = :s_prob) AS problemas,
               SUM(custo_produto) FILTER (WHERE situacao = :s_perd) AS perdimento
        FROM por_pedido
        GROUP BY mes
    """)
    oper_ref_sql = text(f"""
        SELECT {_mes('conferido_at')} AS mes,
               SUM(COALESCE(reembolso, 0)) AS reembolso
        FROM {_qt("refunds")}
        WHERE conferido = true AND conferido_at IS NOT NULL
          AND {_janela('conferido_at')}
        GROUP BY mes
    """)
    oper_dev_sql = text(f"""
        SELECT {_mes('created_at')} AS mes,
               SUM(COALESCE(custo_produto, 0))
                   FILTER (WHERE condicao_produto = 'Usado') AS usado,
               SUM(COALESCE(custo_produto, 0))
                   FILTER (WHERE condicao_produto = 'Manutenção') AS manutencao,
               SUM(COALESCE(custo_manutencao, 0)) AS custo_manutencao
        FROM {_qt("devolutions")}
        WHERE {_janela('created_at')}
        GROUP BY mes
    """)
    # Numerador do Giro de Venda: custo de venda (preço de custo × quantidade)
    # dos pedidos que contam como faturamento (situações aplicáveis), por mês.
    # Mesma definição de "faturamento" das demais seções (exclui lojas internas).
    # O denominador (valor de estoque) vem do snapshot `valuation` já carregado.
    oper_giro_sql = text(f"""
        WITH por_pedido AS (
            SELECT bo.numero,
                   {_mes('bo.data')} AS mes,
                   MAX(bo.situacao) AS situacao,
                   SUM(COALESCE(bo.preco_custo, 0)
                       * COALESCE(bo.item_quantidade, 0)) AS custo_produto
            FROM {_qt("bling_orders")} bo
            WHERE {_janela('bo.data')}
              AND COALESCE(bo.loja, '') <> ALL(:ignored_stores)
            GROUP BY bo.numero, mes
        )
        SELECT mes,
               SUM(custo_produto) FILTER (WHERE situacao = ANY(:sit_giro))
                   AS custo_faturamento
        FROM por_pedido
        GROUP BY mes
    """)

    bo_by_mes = {
        r["mes"]: r for r in (await session.execute(oper_bo_sql, {
            "s_prob": _VAL_SIT_PROBLEMAS,
            "s_perd": _VAL_SIT_PERDIMENTO,
        })).mappings().all()
    }
    ref_by_mes = {r["mes"]: r for r in (await session.execute(oper_ref_sql)).mappings().all()}
    dev_by_mes = {r["mes"]: r for r in (await session.execute(oper_dev_sql)).mappings().all()}
    giro_by_mes = {
        r["mes"]: r for r in (await session.execute(oper_giro_sql, {
            "ignored_stores": _VAL_IGNORED_STORES,
            "sit_giro": _VAL_SIT_RENTABILIDADE,
        })).mappings().all()
    }

    def _brl_row(by_mes: dict, col: str) -> list[float | None]:
        # Mês sem linhas → R$ 0,00 (não "—") nas métricas de moeda.
        return [_r2(by_mes.get(m, {}).get(col) or 0) for m in months]

    def _giro_row() -> list[float | None]:
        # (custo de venda do faturamento ÷ valor de estoque) × 100. Sem estoque
        # no snapshot do mês (None ou 0) → None ("—"), não dá pra dividir.
        out: list[float | None] = []
        for m in months:
            custo = giro_by_mes.get(m, {}).get("custo_faturamento")
            estoque = val_by_mes.get(m, {}).get("estoque")
            if custo is None or not estoque or float(estoque) == 0:
                out.append(None)
            else:
                out.append(_r2(float(custo) / float(estoque) * 100))
        return out

    operacional = OperacionalSecaoOut(
        meses=months,
        linhas=[
            OperacionalLinhaOut(chave="problemas", label="Problemas", formato="brl",
                                descricao=_OPER_DESCRICOES["problemas"],
                                valores=_brl_row(bo_by_mes, "problemas")),
            OperacionalLinhaOut(chave="perdimento", label="Perdimento", formato="brl",
                                descricao=_OPER_DESCRICOES["perdimento"],
                                valores=_brl_row(bo_by_mes, "perdimento")),
            OperacionalLinhaOut(chave="reembolso", label="Reembolso", formato="brl",
                                descricao=_OPER_DESCRICOES["reembolso"],
                                valores=_brl_row(ref_by_mes, "reembolso")),
            OperacionalLinhaOut(chave="usado", label="Usado", formato="brl",
                                descricao=_OPER_DESCRICOES["usado"],
                                valores=_brl_row(dev_by_mes, "usado")),
            OperacionalLinhaOut(chave="manutencao", label="Manutenção", formato="brl",
                                descricao=_OPER_DESCRICOES["manutencao"],
                                valores=_brl_row(dev_by_mes, "manutencao")),
            OperacionalLinhaOut(chave="custo_manutencao", label="Custo Manutenção", formato="brl",
                                descricao=_OPER_DESCRICOES["custo_manutencao"],
                                valores=_brl_row(dev_by_mes, "custo_manutencao")),
            OperacionalLinhaOut(chave="giro_venda", label="Giro de Venda", formato="pct",
                                descricao=_OPER_DESCRICOES["giro_venda"],
                                valores=_giro_row()),
        ],
    )

    # 3b. Bloco "Comercial — 3 meses", quebrado por EQUIPE de vendas. Mesma
    #     janela/helpers (_mes/_janela). Equipe da loja = store_info.sales_team
    #     (mapeada por bling_store_id = bling_orders.loja). Métricas por
    #     (mês × equipe); os quadros (Total / Equipe N / Sem equipe) são
    #     montados somando os baldes de equipe em Python.
    #       • Aguardando Devolução/Cancelamento = SUM(valor base) por situação,
    #         pela data do pedido (1 valor por pedido, MAX);
    #       • Taxa de Devolução = qtd de PRODUTOS devolvidos (SUM(quantidade),
    #         condição Novo+Usado, por created_at) ÷ qtd de pedidos do
    #         faturamento (situações aplicáveis, por data do pedido), em %. A
    #         equipe da devolução vem do pedido de origem (pedido_bling → loja →
    #         equipe).
    #     Lojas internas (_VAL_IGNORED_STORES: 0/205632678/205660518) ficam de
    #     fora — mesmas que a rentabilidade do Valuation já ignora. Pedidos/
    #     devoluções sem loja (loja NULL / pedido não casado) CONTINUAM entrando
    #     no "Sem equipe" (COALESCE('') não bate com a lista de ignoradas).
    com_bo_sql = text(f"""
        WITH por_pedido AS (
            SELECT bo.numero,
                   {_mes('bo.data')} AS mes,
                   MAX(bo.situacao) AS situacao,
                   MAX(COALESCE(NULLIF(bo.valorbase, 0), bo.total)) AS valorbase,
                   MAX(bo.loja) AS loja
            FROM {_qt("bling_orders")} bo
            WHERE {_janela('bo.data')}
              AND COALESCE(bo.loja, '') <> ALL(:ignored_stores)
            GROUP BY bo.numero, mes
        )
        SELECT pp.mes, si.sales_team AS equipe,
               SUM(pp.valorbase) FILTER (WHERE pp.situacao = :s_dev) AS aguardando_devolucao,
               COUNT(*)          FILTER (WHERE pp.situacao = ANY(:sit_fat)) AS faturamento_qtd
        FROM por_pedido pp
        LEFT JOIN {_qt("store_info")} si ON si.bling_store_id = pp.loja
        GROUP BY pp.mes, si.sales_team
    """)
    com_dev_sql = text(f"""
        WITH dev AS (
            SELECT d.id, {_mes('d.created_at')} AS mes, d.pedido_bling
            FROM {_qt("devolutions")} d
            WHERE {_janela('d.created_at')}
              AND d.condicao_produto IN ('Novo', 'Usado')
        )
        SELECT dev.mes, si.sales_team AS equipe,
               COUNT(DISTINCT dev.pedido_bling) AS devolucoes
        FROM dev
        LEFT JOIN LATERAL (
            SELECT bo.loja FROM {_qt("bling_orders")} bo
            WHERE bo.numero = dev.pedido_bling LIMIT 1
        ) bo ON true
        LEFT JOIN {_qt("store_info")} si ON si.bling_store_id = bo.loja
        WHERE COALESCE(bo.loja, '') <> ALL(:ignored_stores)
        GROUP BY dev.mes, si.sales_team
    """)

    com_bo_rows = (await session.execute(com_bo_sql, {
        "s_dev": _VAL_SIT_AGUARD_DEVOLUCAO,
        "sit_fat": _VAL_SIT_APLICAVEIS,
        "ignored_stores": _VAL_IGNORED_STORES,
    })).mappings().all()
    com_dev_rows = (await session.execute(com_dev_sql, {
        "ignored_stores": _VAL_IGNORED_STORES,
    })).mappings().all()
    teams = list((await session.execute(text(
        f"SELECT DISTINCT sales_team FROM {_qt('store_info')} "
        "WHERE sales_team IS NOT NULL ORDER BY sales_team"
    ))).scalars().all())

    # Índices (mês, equipe) → métricas. equipe = None quando a loja não tem
    # equipe (lojas internas / não mapeadas) — vira o quadro "Sem equipe".
    bo_idx = {(r["mes"], r["equipe"]): r for r in com_bo_rows}
    dev_idx = {(r["mes"], r["equipe"]): (r["devolucoes"] or 0) for r in com_dev_rows}

    # `_com_series(pred)` devolve as duas séries por mês (Aguardando Devolução em
    # R$ e Taxa de Devolução em %) somando os baldes de equipe que casam com `pred`.
    def _com_series(pred) -> tuple[list[float | None], list[float | None]]:
        aguard = {m: 0.0 for m in months}
        fat = {m: 0 for m in months}
        dev = {m: 0 for m in months}
        for (mes, team), r in bo_idx.items():
            if mes in aguard and pred(team):
                aguard[mes] += float(r["aguardando_devolucao"] or 0)
                fat[mes] += int(r["faturamento_qtd"] or 0)
        for (mes, team), c in dev_idx.items():
            if mes in dev and pred(team):
                dev[mes] += int(c)
        aguard_s = [_r2(aguard[m]) for m in months]
        # Taxa: pedidos devolvidos (Novo+Usado, distinct pedido) ÷ pedidos do
        # faturamento, em %. Sem pedidos de faturamento no mês → None ("—").
        taxa_s = [_r2(dev[m] / fat[m] * 100) if fat[m] else None for m in months]
        return aguard_s, taxa_s

    def _tem_dados(pred) -> bool:
        for (mes, team), r in bo_idx.items():
            if mes in months and pred(team) and (
                (r["aguardando_devolucao"] or 0) or (r["faturamento_qtd"] or 0)
            ):
                return True
        return any(mes in months and pred(team) and c
                   for (mes, team), c in dev_idx.items())

    # Cada `sales_team` codifica empresa.membro no int E*100+M (ex.: 101 = 1.1,
    # 201 = 2.1). A empresa vem de team // 100 e o membro de team % 100 — os
    # membros são agrupados por empresa (uma aba por empresa).
    _teams = sorted(
        set(teams)
        | {t for (_m, t) in bo_idx if t is not None}
        | {t for (_m, t) in dev_idx if t is not None}
    )

    total_aguard, total_taxa = _com_series(lambda t: True)

    _by_empresa: dict[int, list[int]] = {}
    for team in _teams:
        _by_empresa.setdefault(team // 100, []).append(team)

    empresas: list[ComercialEmpresaOut] = []
    for emp in sorted(_by_empresa):
        emp_teams = _by_empresa[emp]
        membros = []
        for team in emp_teams:
            a, t = _com_series(lambda x, team=team: x == team)
            membros.append(ComercialMembroOut(
                label=f"{emp}.{team % 100}", aguardando_devolucao=a, taxa_devolucao=t,
            ))
        team_set = set(emp_teams)
        a, t = _com_series(lambda x, s=team_set: x in s)
        empresas.append(ComercialEmpresaOut(
            empresa=emp, label=f"Empresa {emp}",
            aguardando_devolucao=a, taxa_devolucao=t, membros=membros,
        ))
    # "Sem equipe" só aparece quando houver dados não atribuídos a equipe.
    if _tem_dados(lambda t: t is None):
        a, t = _com_series(lambda x: x is None)
        empresas.append(ComercialEmpresaOut(
            empresa=None, label="Sem equipe",
            aguardando_devolucao=a, taxa_devolucao=t, membros=[],
        ))

    comercial = ComercialSecaoOut(
        meses=months,
        total_aguardando_devolucao=total_aguard,
        total_taxa_devolucao=total_taxa,
        desc_aguardando_devolucao=_COM_DESCRICOES["aguardando_devolucao"],
        desc_taxa_devolucao=_COM_DESCRICOES["taxa_devolucao"],
        empresas=empresas,
    )

    # 4 + 5. Por Marketplace e Por Categoria (3 meses) — mkt_rows/cat_rows já
    # executados acima (a rentabilidade do card mensal é a soma de mkt_rows).
    return ValuationReportOut(
        gerado_em=datetime.now(UTC),
        situacoes_label=_VAL_SIT_LABEL,
        valuation_meses=valuation_meses,
        operacional=operacional,
        comercial=comercial,
        por_marketplace=_agg_to_secoes([dict(r) for r in mkt_rows], months),
        por_categoria=_agg_to_secoes(
            [dict(r) for r in cat_rows], months, estoque_by_mes
        ),
    )


# Ordem fixa dos locais na resposta (mesma do snapshot e da rotina antiga).
_ESTOQUE_LOCAIS_ORDEM = ["PI", "SA", "SP", "RA", "CD", "CI", "US", "Eletro", "Mala", "Outros"]


@router.post("/valuation/unlock", response_model=ValuationUnlockOut)
async def valuation_unlock(
    body: ValuationUnlockIn,
    _u: Annotated[User, Depends(require_admin)],
) -> ValuationUnlockOut:
    """Valida a senha extra e devolve um token (HMAC + TTL). Senha errada =
    401 com sleep curto (mitiga brute-force; sem rate-limit dedicado porque
    o usuário já precisa estar logado e com permissão view)."""
    s = get_settings()
    if not hmac.compare_digest(body.password.strip(), s.valuation_password):
        await asyncio.sleep(0.3)
        raise HTTPException(401, detail={"code": "wrong_password"})
    token, ttl = _make_valuation_token()
    return ValuationUnlockOut(token=token, expires_in=ttl)


@router.get("/valuation/estoque-bling", response_model=EstoqueBlingSnapshotOut)
async def valuation_estoque_bling(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_admin)],
    _unlocked: Annotated[None, Depends(require_valuation_unlock)] = None,
) -> EstoqueBlingSnapshotOut:
    """Último snapshot diário do estoque Bling por local.

    O cron `valuation_estoque_snapshot` (worker arq, ~08h BRT) crawl o Bling
    e grava em `valuation_estoque_bling_diario`. Aqui só lemos a linha mais
    recente. Devolve 404 se nunca rodou."""
    row = (
        await session.execute(
            text(f"""
                SELECT data, updated_at, total_qtd, total_valor, por_local
                FROM {_qt("valuation_estoque_bling_diario")}
                ORDER BY data DESC
                LIMIT 1
            """),
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(
            404, detail={"code": "estoque_bling_sem_snapshot"}
        )

    por_local: dict = row["por_local"] or {}
    # Preserva a ordem fixa; só inclui locais com dado, somando "Outros" no fim.
    locais: list[EstoqueBlingLocalOut] = []
    for l in _ESTOQUE_LOCAIS_ORDEM:
        entry = por_local.get(l)
        if not entry:
            continue
        locais.append(EstoqueBlingLocalOut(
            local=l,
            qtd=int(entry.get("qtd") or 0),
            valor=float(entry.get("valor") or 0),
        ))

    return EstoqueBlingSnapshotOut(
        data=row["data"],
        updated_at=row["updated_at"],
        total_qtd=int(row["total_qtd"] or 0),
        total_valor=float(row["total_valor"] or 0),
        locais=locais,
    )


# Ordem preferida das colunas de marketplace; extras presentes no snapshot
# entram depois, em ordem alfabética (suporta marketplaces futuros).
_SALDO_MKT_ORDEM = ["ml", "shopee", "amazon", "magalu", "tiktok", "shein", "temu", "aliexpress"]


def _f_or_none(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_loja(s: str | None) -> str:
    """Normaliza nome de loja p/ casar o perfil AdsPower com
    `store_info.account_name` (minúsculas, só alfanumérico)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@router.get("/valuation/saldo-marketplace", response_model=SaldoMarketplaceSnapshotOut)
async def valuation_saldo_marketplace(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(require_admin)],
    _unlocked: Annotated[None, Depends(require_valuation_unlock)] = None,
) -> SaldoMarketplaceSnapshotOut:
    """Último snapshot de saldos por loja × marketplace.

    Gravado pela rotina externa AdsPower Contabilidade (desktop-only) em
    `valuation_marketplace_saldo_diario`. Aqui só lemos a linha mais recente.
    Devolve 404 se nunca rodou. Colunas (marketplaces) são dinâmicas."""
    row = (
        await session.execute(
            text(f"""
                SELECT data, updated_at, total_a_receber, total_disponivel, por_loja
                FROM {_qt("valuation_marketplace_saldo_diario")}
                ORDER BY data DESC
                LIMIT 1
            """),
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, detail={"code": "saldo_marketplace_sem_snapshot"})

    por_loja: list = row["por_loja"] or []

    # O snapshot guarda o NOME DO PERFIL AdsPower (ex.: "Luminin - ml sho").
    # Na tela queremos o nome da loja como aparece na página "Lojas"
    # (store_info.account_name). Casamos pelo prefixo antes de " - ",
    # normalizado; se não houver match exato, tentamos um account_name que
    # comece com o prefixo (ex.: "Victor" → "victor mei").
    acct_names = (
        await session.execute(
            text(
                f"SELECT DISTINCT account_name FROM {_qt('store_info')} "
                f"WHERE account_name IS NOT NULL AND account_name <> ''"
            )
        )
    ).scalars().all()
    acct_por_norm: dict[str, str] = {}
    for a in acct_names:
        acct_por_norm.setdefault(_norm_loja(a), a)

    def _resolve_loja(raw: str) -> str:
        prefixo = (raw or "").split(" - ")[0].strip()
        n = _norm_loja(prefixo)
        if not n:
            return raw or "—"
        if n in acct_por_norm:
            return acct_por_norm[n]
        for norm, orig in acct_por_norm.items():
            if norm.startswith(n):
                return orig
        return prefixo or raw or "—"

    # Descobre o conjunto de marketplaces presentes → colunas dinâmicas.
    presentes: set[str] = set()
    for entry in por_loja:
        for k in (entry or {}):
            if k != "loja":
                presentes.add(k)
    marketplaces = [m for m in _SALDO_MKT_ORDEM if m in presentes]
    marketplaces += sorted(presentes - set(marketplaces))

    lojas: list[SaldoMarketplaceLojaOut] = []
    for entry in por_loja:
        entry = entry or {}
        saldos: dict[str, SaldoMarketplaceCelulaOut] = {}
        loja_receber = 0.0
        for mkt in marketplaces:
            cel = entry.get(mkt)
            if not cel:
                continue
            disp = _f_or_none(cel.get("disponivel"))
            arec = _f_or_none(cel.get("a_receber"))
            nota = cel.get("nota") or None
            saldos[mkt] = SaldoMarketplaceCelulaOut(
                disponivel=disp, a_receber=arec, nota=nota,
            )
            loja_receber += arec or 0.0
        lojas.append(SaldoMarketplaceLojaOut(
            loja=_resolve_loja(str(entry.get("loja") or "")),
            saldos=saldos,
            total_a_receber=round(loja_receber, 2),
        ))

    lojas.sort(key=lambda l: l.loja.casefold())

    return SaldoMarketplaceSnapshotOut(
        data=row["data"],
        updated_at=row["updated_at"],
        marketplaces=marketplaces,
        lojas=lojas,
        total_a_receber=float(row["total_a_receber"] or 0),
        total_disponivel=float(row["total_disponivel"] or 0),
    )
