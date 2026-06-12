"""Consulta e export de NF-e das contas `bling_notas`.

A tabela `bling_notas` guarda só as credenciais OAuth por conta — as notas
ficam no Bling e são consultadas on-demand via GET /nfe (lista) e
GET /nfe/{id} (detalhe, que traz o link do XML autorizado).

Fluxo:
  * lista: 1 chamada /nfe por página (100/pg) por conta selecionada.
  * export XLSX: mesma lista, planilha com os campos da listagem.
  * export XML: lista + 1 chamada de detalhe por nota (pra resolver o link
    do XML) + download do arquivo. O detalhe passa pelo rate gate global
    do Bling (5 req/s compartilhado), então o export é limitado a
    MAX_XML_NOTAS por request pra não estourar o timeout do proxy.

Tokens: o cron `bling_notas_token_refresh` renova a cada 5h (AT dura 6h);
se um token estiver vencido aqui, fazemos o refresh on-demand reusando os
helpers do próprio cron (commit imediato — o Bling rotaciona o RT).
"""
from __future__ import annotations

import asyncio
import zipfile
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models import BlingNota, User
from app.services.bling_notas_token_refresh import (
    _apply_token_payload,
    _post_oauth_token,
)
from app.services.marketplaces.bling import (
    BLING_API_BASE,
    _acquire_bling_rate_slot,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/notas-fiscais", tags=["notas_fiscais"])
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Bling pagina /nfe em até 100 itens; trava de segurança por conta.
PAGE_SIZE = 100
MAX_PAGES_PER_CONTA = 60  # 6.000 notas por conta por consulta
# Export XML = 1 chamada de detalhe por nota a 5 req/s globais — acima
# disso o request passa de ~2min e o proxy derruba com 502.
MAX_XML_NOTAS = 500
MAX_RANGE_DAYS = 184

SITUACOES = {
    1: "Pendente",
    2: "Cancelada",
    3: "Aguardando recibo",
    4: "Rejeitada",
    5: "Autorizada",
    6: "Emitida DANFE",
    7: "Registrada",
    8: "Aguardando protocolo",
    9: "Denegada",
    10: "Consulta situação",
    11: "Bloqueada",
}
TIPOS = {0: "Entrada", 1: "Saída"}


class ContaOut(BaseModel):
    id: UUID
    nome: str


class ContasOut(BaseModel):
    items: list[ContaOut]


class NotaOut(BaseModel):
    conta: str
    bling_id: int
    numero: str | None = None
    data_emissao: str | None = None
    data_operacao: str | None = None
    tipo: str | None = None
    situacao: str | None = None
    cliente: str | None = None
    documento: str | None = None
    valor: float | None = None


class NotasPage(BaseModel):
    items: list[NotaOut]
    total: int
    erros: list[str]


# ─── Bling HTTP ───────────────────────────────────────────────────────


async def _bling_get(token: str, path: str, params: dict | None = None) -> dict:
    """GET autenticado na API v3 com o rate gate global e retry leve em
    429/503 (mesma família de erro tratada no BlingClient)."""
    delay = 1.0
    last_status = 0
    for attempt in range(3):
        await _acquire_bling_rate_slot()
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(
                f"{BLING_API_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                params=params,
            )
        last_status = r.status_code
        if r.status_code in (429, 503) and attempt < 2:
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"bling http {r.status_code}: {r.text[:200]}")
        return r.json()
    raise RuntimeError(f"bling http {last_status} (retries esgotados)")


async def _ensure_token(s: AsyncSession, conta: BlingNota) -> str:
    """Access token válido da conta; refresh on-demand se vencido."""
    now = datetime.now(UTC)
    if conta.access_token and (
        conta.token_expires_at is None
        or conta.token_expires_at > now + timedelta(seconds=60)
    ):
        return conta.access_token
    if not conta.refresh_token:
        raise RuntimeError("conta sem refresh_token (reautorizar no Bling)")
    payload = await _post_oauth_token(
        conta.basic_auth_b64,
        {"grant_type": "refresh_token", "refresh_token": conta.refresh_token},
    )
    await _apply_token_payload(s, conta.id, payload, clear_code=False)
    await s.refresh(conta)
    if not conta.access_token:
        raise RuntimeError("refresh não retornou access_token")
    return conta.access_token


async def _list_notas_bling(
    token: str, date_from: date, date_to: date
) -> list[dict]:
    out: list[dict] = []
    pagina = 1
    while pagina <= MAX_PAGES_PER_CONTA:
        payload = await _bling_get(
            token,
            "/nfe",
            {
                "pagina": pagina,
                "limite": PAGE_SIZE,
                "dataEmissaoInicial": f"{date_from.isoformat()} 00:00:00",
                "dataEmissaoFinal": f"{date_to.isoformat()} 23:59:59",
            },
        )
        data = payload.get("data") or []
        out.extend(data)
        if len(data) < PAGE_SIZE:
            return out
        pagina += 1
    raise RuntimeError(
        f"mais de {MAX_PAGES_PER_CONTA * PAGE_SIZE} notas no período — "
        "reduza o intervalo de datas"
    )


# ─── shared fetch ─────────────────────────────────────────────────────


async def _resolve_contas(
    s: AsyncSession, conta_ids: list[UUID]
) -> list[BlingNota]:
    stmt = (
        select(BlingNota)
        .where(BlingNota.status == "active")
        .order_by(BlingNota.nome)
    )
    if conta_ids:
        stmt = stmt.where(BlingNota.id.in_(conta_ids))
    rows = (await s.execute(stmt)).scalars().all()
    if not rows:
        raise HTTPException(422, detail={"code": "nenhuma_conta_selecionada"})
    return list(rows)


def _validate_range(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise HTTPException(422, detail={"code": "periodo_invalido"})
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise HTTPException(
            422,
            detail={
                "code": "periodo_muito_longo",
                "message": f"período máximo de {MAX_RANGE_DAYS} dias",
            },
        )


async def _fetch_all(
    s: AsyncSession,
    conta_ids: list[UUID],
    date_from: date,
    date_to: date,
) -> tuple[list[dict], list[str]]:
    """Notas de todas as contas selecionadas.

    Retorna (rows, erros) — cada row carrega a conta e o token usado (o
    export XML precisa dele pras chamadas de detalhe). Erro em uma conta
    não derruba as demais; vira mensagem em `erros`.
    """
    rows: list[dict] = []
    erros: list[str] = []
    for conta in await _resolve_contas(s, conta_ids):
        try:
            token = await _ensure_token(s, conta)
            notas = await _list_notas_bling(token, date_from, date_to)
        except Exception as e:  # noqa: BLE001
            erros.append(f"{conta.nome}: {str(e)[:200]}")
            logger.warning(
                "nf_fetch_conta_failed", conta=conta.nome, err=str(e)[:300]
            )
            continue
        rows.extend(
            {"conta": conta.nome, "token": token, "nota": n} for n in notas
        )
    rows.sort(key=lambda r: r["nota"].get("dataEmissao") or "", reverse=True)
    return rows, erros


def _to_out(row: dict) -> NotaOut:
    n = row["nota"]
    contato = n.get("contato") or {}
    valor = n.get("valorNota")
    return NotaOut(
        conta=row["conta"],
        bling_id=int(n.get("id") or 0),
        numero=str(n["numero"]) if n.get("numero") is not None else None,
        data_emissao=n.get("dataEmissao"),
        data_operacao=n.get("dataOperacao"),
        tipo=TIPOS.get(n.get("tipo"), str(n.get("tipo") or "")) or None,
        situacao=SITUACOES.get(n.get("situacao"), str(n.get("situacao") or ""))
        or None,
        cliente=contato.get("nome"),
        documento=contato.get("numeroDocumento"),
        valor=float(valor) if valor is not None else None,
    )


_PERM = require_permission("notas_fiscais", "view")
_ContaIds = Annotated[list[UUID], Query(alias="conta")]


# ─── endpoints ────────────────────────────────────────────────────────


@router.get("/contas", response_model=ContasOut)
async def list_contas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_PERM)],
) -> ContasOut:
    rows = (
        await session.execute(
            select(BlingNota)
            .where(BlingNota.status == "active")
            .order_by(BlingNota.nome)
        )
    ).scalars().all()
    return ContasOut(items=[ContaOut(id=r.id, nome=r.nome) for r in rows])


@router.get("", response_model=NotasPage)
async def list_notas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_PERM)],
    date_from: date = Query(...),
    date_to: date = Query(...),
    conta: _ContaIds = [],  # noqa: B006
) -> NotasPage:
    _validate_range(date_from, date_to)
    rows, erros = await _fetch_all(session, conta, date_from, date_to)
    return NotasPage(
        items=[_to_out(r) for r in rows], total=len(rows), erros=erros
    )


@router.get("/export.xlsx")
async def export_xlsx(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_PERM)],
    date_from: date = Query(...),
    date_to: date = Query(...),
    conta: _ContaIds = [],  # noqa: B006
) -> StreamingResponse:
    _validate_range(date_from, date_to)
    rows, erros = await _fetch_all(session, conta, date_from, date_to)

    wb = Workbook()
    ws = wb.active
    ws.title = "Notas Fiscais"
    ws.append(
        [
            "Conta",
            "Número",
            "Data emissão",
            "Data operação",
            "Tipo",
            "Situação",
            "Cliente",
            "Documento",
            "Valor",
        ]
    )
    for r in rows:
        o = _to_out(r)
        ws.append(
            [
                o.conta,
                o.numero or "",
                o.data_emissao or "",
                o.data_operacao or "",
                o.tipo or "",
                o.situacao or "",
                o.cliente or "",
                o.documento or "",
                o.valor if o.valor is not None else "",
            ]
        )
    if erros:
        ws_err = wb.create_sheet("Erros")
        ws_err.append(["Conta com falha"])
        for e in erros:
            ws_err.append([e])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now(SAO_PAULO).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="notas_fiscais_{stamp}.xlsx"'
            )
        },
    )


async def _download_xml(url: str) -> bytes | None:
    """XML fica num link assinado (S3) — fora da API, sem rate gate."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(url)
        if r.status_code >= 400:
            return None
        return r.content
    except httpx.HTTPError:
        return None


def _xml_filename(conta: str, detail: dict, nota: dict) -> str:
    chave = detail.get("chaveAcesso") or ""
    if chave:
        base = chave
    else:
        base = f"nfe_{nota.get('numero') or nota.get('id')}"
    safe_conta = conta.replace("/", "_")
    return f"{safe_conta}/{base}.xml"


@router.get("/export.xml")
async def export_xml(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_PERM)],
    date_from: date = Query(...),
    date_to: date = Query(...),
    conta: _ContaIds = [],  # noqa: B006
) -> StreamingResponse:
    """ZIP com os XMLs das notas do período (uma pasta por conta).

    Notas sem XML disponível (não autorizadas, canceladas antes da
    autorização, etc.) entram no `_avisos.txt` dentro do zip.
    """
    _validate_range(date_from, date_to)
    rows, erros = await _fetch_all(session, conta, date_from, date_to)
    if not rows and erros:
        raise HTTPException(502, detail={"code": "bling_falhou", "erros": erros})
    if len(rows) > MAX_XML_NOTAS:
        raise HTTPException(
            422,
            detail={
                "code": "muitas_notas",
                "message": (
                    f"{len(rows)} notas no período — o export XML é limitado "
                    f"a {MAX_XML_NOTAS} por vez, reduza o período ou as contas"
                ),
            },
        )

    avisos: list[str] = list(erros)
    buf = BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            nota = r["nota"]
            label = f"{r['conta']} nº {nota.get('numero') or nota.get('id')}"
            try:
                detail_payload = await _bling_get(
                    r["token"], f"/nfe/{nota['id']}"
                )
            except Exception as e:  # noqa: BLE001
                avisos.append(f"{label}: erro no detalhe ({str(e)[:120]})")
                continue
            detail: dict[str, Any] = detail_payload.get("data") or {}
            xml_url = detail.get("xml")
            if not xml_url:
                situ = SITUACOES.get(nota.get("situacao"), "?")
                avisos.append(f"{label}: sem XML disponível (situação {situ})")
                continue
            content = await _download_xml(xml_url)
            if content is None:
                avisos.append(f"{label}: falha ao baixar o XML")
                continue
            name = _xml_filename(r["conta"], detail, nota)
            if name in seen:
                name = name.replace(".xml", f"_{nota['id']}.xml")
            seen.add(name)
            zf.writestr(name, content)
        if avisos:
            zf.writestr("_avisos.txt", "\n".join(avisos))

    buf.seek(0)
    stamp = datetime.now(SAO_PAULO).strftime("%Y%m%d_%H%M")
    logger.info(
        "nf_export_xml",
        notas=len(rows),
        avisos=len(avisos),
        date_from=str(date_from),
        date_to=str(date_to),
    )
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="notas_fiscais_xml_{stamp}.zip"'
            )
        },
    )
