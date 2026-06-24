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
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
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
    EstoqueBlingLocalOut,
    EstoqueBlingSnapshotOut,
    FaturamentoGrpLinhaOut,
    FaturamentoMesSecaoOut,
    NCMOut,
    NCMPatch,
    SaldoMarketplaceCelulaOut,
    SaldoMarketplaceLojaOut,
    SaldoMarketplaceSnapshotOut,
    SimulacaoOut,
    SimulacaoPatch,
    SituacaoLinhaOut,
    SituacaoSecaoOut,
    SuprimentosOut,
    SuprimentosPatch,
    ValuationMesOut,
    ValuationReportOut,
    ValuationUnlockIn,
    ValuationUnlockOut,
)

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
    "83958",     # Enviado Geral RA
    "84678",     # Enviado Geral SA
    "83965",     # Enviado Importado
]
_VAL_SIT_ENTREGUE = "83953"
_VAL_SIT_LABEL = (
    "Em aberto, Em andamento, Entregue, "
    "Enviado Geral SP/CI/PI/RA/SA, Enviado Importado"
)
# Situações problemáticas — seção Eficácia Operacional (total do banco).
_VAL_SIT_EFICACIA = [
    "83955",  # Aguardando Cancelamento
    "83957",  # Aguardando Devolução
    "83960",  # Problemas
    "83961",  # Aguardando Reembolso
    "83964",  # Teste
    "83966",  # Erro no Envio
    "84676",  # Devolvido Estoque Usado
    "84677",  # Manutenção
]


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
    seguro. Faturamento = SUM das situações aplicáveis; custo/rentabilidade =
    só Entregue. Rateio proporcional ao itemvalor quando o pedido tem >1 item.
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
        l.marketplace::text AS marketplace,
        COALESCE(NULLIF(TRIM(p.categoria_nome), ''), 'Sem categoria') AS categoria,
        p.situacao AS situacao_id,
        p.valorbase_prop AS valorbase,
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
    SUM(CASE WHEN situacao_id = :entregue THEN valorbase   ELSE 0 END) AS faturamento_entregue,
    SUM(CASE WHEN situacao_id = :entregue THEN custo_total ELSE 0 END) AS custo_entregue
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


def _agg_to_secoes(rows: list[dict], months: list) -> list[FaturamentoMesSecaoOut]:
    """Agrupa as linhas da _agg_sql em uma seção por mês (3 meses)."""
    by_mes: dict = {m: [] for m in months}
    for r in rows:
        if r["mes"] in by_mes:
            by_mes[r["mes"]].append(r)
    out: list[FaturamentoMesSecaoOut] = []
    for mes in months:
        linhas: list[FaturamentoGrpLinhaOut] = []
        tot_fat = tot_custo = tot_rent = Decimal("0")
        for r in sorted(by_mes[mes], key=lambda x: (x["grp"] or "")):
            if not r["grp"]:
                continue
            fat = Decimal(str(r["faturamento"] or 0))
            fat_ent = Decimal(str(r["faturamento_entregue"] or 0))
            custo = Decimal(str(r["custo_entregue"] or 0))
            rent = fat_ent - custo
            margem = (rent / custo * 100) if custo > 0 else None
            linhas.append(FaturamentoGrpLinhaOut(
                grp=r["grp"], faturamento=_r2(fat), custo=_r2(custo),
                rentabilidade=_r2(rent), margem=_r2(margem),
            ))
            tot_fat += fat
            tot_custo += custo
            tot_rent += rent
        tot_margem = (tot_rent / tot_custo * 100) if tot_custo > 0 else None
        out.append(FaturamentoMesSecaoOut(
            mes=mes, linhas=linhas,
            total_faturamento=_r2(tot_fat), total_custo=_r2(tot_custo),
            total_rentabilidade=_r2(tot_rent), total_margem=_r2(tot_margem),
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
    Seções: Valuation 3 meses, Resumo de ontem, Eficácia operacional,
    Por Marketplace e Por Categoria.
    """
    months = _val_window_months()

    # 1. Valuation por mês: saldos = snapshot do último dia do mês;
    #    rentabilidade = SUM do mês.
    val_saldos_sql = text(f"""
        SELECT DISTINCT ON (date_trunc('month', data))
               date_trunc('month', data)::date AS mes,
               caixa, estoque, receber, data AS data_snapshot
        FROM {_qt("valuation")}
        WHERE data >= date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) - INTERVAL '2 months')::date
          AND data <  date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) + INTERVAL '1 month')::date
        ORDER BY date_trunc('month', data), data DESC
    """)
    val_rent_sql = text(f"""
        SELECT date_trunc('month', data)::date AS mes,
               SUM(COALESCE(rentabilidade, 0)) AS rentabilidade
        FROM {_qt("valuation")}
        WHERE data >= date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) - INTERVAL '2 months')::date
          AND data <  date_trunc('month', ((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) + INTERVAL '1 month')::date
        GROUP BY date_trunc('month', data)
    """)
    val_by_mes: dict = {}
    for r in (await session.execute(val_saldos_sql)).mappings().all():
        val_by_mes[r["mes"]] = dict(r)
    for r in (await session.execute(val_rent_sql)).mappings().all():
        val_by_mes.setdefault(r["mes"], {})["rentabilidade"] = r["rentabilidade"]

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
            total=_r2(total), rentabilidade=_r2(md.get("rentabilidade")),
            data_snapshot=md.get("data_snapshot"),
        ))

    # 2. Resumo de ontem — pedidos por situação (dedup por pedido).
    daily_sql = text(f"""
        WITH por_pedido AS (
            SELECT bo.numero,
                   MAX(bo.situacao) AS situacao,
                   MAX(COALESCE(NULLIF(bo.valorbase, 0), bo.total)) AS valorbase_pedido
            FROM {_qt("bling_orders")} bo
            WHERE (bo.data AT TIME ZONE 'America/Sao_Paulo')::date
                  = (((NOW() AT TIME ZONE 'America/Sao_Paulo')::date) - INTERVAL '1 day')::date
            GROUP BY bo.numero
        )
        SELECT p.situacao AS situacao_id,
               COALESCE(sb.nome, '(sem situacao)') AS situacao_nome,
               COUNT(*) AS pedidos,
               SUM(COALESCE(p.valorbase_pedido, 0)) AS faturamento
        FROM por_pedido p
        LEFT JOIN {_qt("situacao_bling")} sb ON p.situacao = sb.id::text
        WHERE COALESCE(sb.nome, '') !~ '^-+$'
        GROUP BY p.situacao, sb.nome
        ORDER BY faturamento DESC NULLS LAST
    """)
    # 3. Eficácia operacional — situações problemáticas (total do banco).
    efi_sql = text(f"""
        WITH por_pedido AS (
            SELECT bo.numero,
                   MAX(bo.situacao) AS situacao,
                   MAX(COALESCE(NULLIF(bo.valorbase, 0), bo.total)) AS valorbase_pedido
            FROM {_qt("bling_orders")} bo
            WHERE bo.situacao = ANY(:efi_ids)
            GROUP BY bo.numero
        )
        SELECT p.situacao AS situacao_id,
               COALESCE(sb.nome, '(sem situacao)') AS situacao_nome,
               COUNT(*) AS pedidos,
               SUM(COALESCE(p.valorbase_pedido, 0)) AS faturamento
        FROM por_pedido p
        LEFT JOIN {_qt("situacao_bling")} sb ON p.situacao = sb.id::text
        GROUP BY p.situacao, sb.nome
        ORDER BY faturamento DESC NULLS LAST
    """)

    def _build_secao(rows: list[dict], data=None) -> SituacaoSecaoOut:
        linhas: list[SituacaoLinhaOut] = []
        tot_ped = 0
        tot_fat = Decimal("0")
        for r in rows:
            fat = Decimal(str(r["faturamento"] or 0))
            linhas.append(SituacaoLinhaOut(
                situacao_nome=r["situacao_nome"], pedidos=int(r["pedidos"]),
                faturamento=_r2(fat),
            ))
            tot_ped += int(r["pedidos"])
            tot_fat += fat
        return SituacaoSecaoOut(
            data=data, linhas=linhas,
            total_pedidos=tot_ped, total_faturamento=_r2(tot_fat),
        )

    daily_rows = (await session.execute(daily_sql)).mappings().all()
    efi_rows = (await session.execute(efi_sql, {"efi_ids": _VAL_SIT_EFICACIA})).mappings().all()

    from datetime import timedelta
    from zoneinfo import ZoneInfo
    ontem = (datetime.now(ZoneInfo("America/Sao_Paulo")).date() - timedelta(days=1))
    resumo_ontem = _build_secao([dict(r) for r in daily_rows], data=ontem)
    eficacia = _build_secao([dict(r) for r in efi_rows])

    # 4 + 5. Por Marketplace e Por Categoria (3 meses).
    agg_params = {"sit_aplic": _VAL_SIT_APLICAVEIS, "entregue": _VAL_SIT_ENTREGUE}
    mkt_rows = (await session.execute(text(_agg_sql("marketplace")), agg_params)).mappings().all()
    cat_rows = (await session.execute(text(_agg_sql("categoria")), agg_params)).mappings().all()

    return ValuationReportOut(
        gerado_em=datetime.now(UTC),
        situacoes_label=_VAL_SIT_LABEL,
        valuation_meses=valuation_meses,
        resumo_ontem=resumo_ontem,
        eficacia=eficacia,
        por_marketplace=_agg_to_secoes([dict(r) for r in mkt_rows], months),
        por_categoria=_agg_to_secoes([dict(r) for r in cat_rows], months),
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
_SALDO_MKT_ORDEM = ["ml", "shopee", "amazon"]


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

    return SaldoMarketplaceSnapshotOut(
        data=row["data"],
        updated_at=row["updated_at"],
        marketplaces=marketplaces,
        lojas=lojas,
        total_a_receber=float(row["total_a_receber"] or 0),
        total_disponivel=float(row["total_disponivel"] or 0),
    )
