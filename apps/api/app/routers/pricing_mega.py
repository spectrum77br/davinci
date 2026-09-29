"""Fotos de produtos via MEGA — endpoints da Tabela de Preços (aba Produtos).

Fluxo do operador:
  * GET  /status  → sidecar no ar? conta logada?
  * POST /login   → login na conta MEGA (senha vai direto pro sidecar e não
                    é armazenada; a sessão persiste no volume do container).
  * POST /sync    → casa pastas de fotos ↔ produtos pelo nome (2 níveis:
                    marca/modelo); dry_run devolve a prévia, apply gera
                    link público (mega-export) e grava fotos_url/fotos_path.
  * POST /scaffold → cria a estrutura marca/modelo no MEGA a partir de uma
                    lista de nomes e já preenche os links dos produtos.
  * POST /counts/refresh → reconta fotos/vídeos/embalagens por pasta
                    (mega-find no sidecar) e grava nos produtos. O worker roda
                    a mesma recontagem toda madrugada.
  * POST /products/{id}/{fotos|embalagens}/upload → sobe pro MEGA na pasta
                    do produto (ou na subpasta "Embalagens" dela; cria se não
                    existir), salva o link e atualiza a contagem de todos os
                    produtos que dividem a pasta.
  * POST /products/{id}/recontar → reconta só a pasta daquele produto.
  * GET  /products/{id}/midias(?tipo=) e /midias/arquivo → a tela vê e baixa
                    fotos/embalagens sem sair do DaVinci.
  * GET  /pastas, PUT/DELETE /products/{id}/pasta → trocar ou desligar a
                    pasta de UM produto, escolhendo da lista real do MEGA.

Embalagens (29/09/2026): subpasta "Embalagens" dentro da pasta de fotos — ver
services/mega_midias.py.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.db import get_session
from app.deps.auth import require_permission, user_scope
from app.models import PricingProduct, User
from app.services.marketing.anexos import _EXT_IMAGEM, MIMES_IMAGEM, disposicao_segura, mime_seguro
from app.services.mega_fotos import (
    PASTA_EMBALAGEM_PADRAO,
    MegaError,
    eh_de_embalagens,
    match_products_to_folders,
    sidecar_bytes,
    sidecar_request,
    sidecar_stream,
)
from app.services.mega_midias import (
    EXT_IMAGEM,
    EXT_VIDEO,
    RAIZ_POR_DEPARTAMENTO,
    extensao,
    extensao_aceita,
    pasta_nova_do_produto,
    raiz_da_conta,
    recontar_pasta,
    recontar_todas,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/pricing/mega", tags=["pricing"])


def _root() -> str:
    return raiz_da_conta()


def _sidecar_http_error(exc: MegaError) -> HTTPException:
    # 503 = sidecar fora do ar; o resto (inclusive o status cru que o
    # download repassa) vira 502 — a tela só precisa saber que foi o MEGA.
    return HTTPException(
        status_code=503 if exc.status_code == 503 else 502,
        detail={"code": "mega_sidecar", "message": exc.message},
    )


@router.get("/status")
async def mega_status(
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "view"))
    ],
) -> dict[str, Any]:
    try:
        health = await sidecar_request("GET", "/health", timeout=20.0)
    except MegaError as exc:
        return {
            "available": False,
            "logged_in": False,
            "email": None,
            "error": exc.message,
        }
    return {
        "available": True,
        "logged_in": bool(health.get("logged_in")),
        "email": health.get("email"),
        "root": _root(),
    }


class MegaLoginIn(BaseModel):
    email: str
    password: str
    code: str | None = None


@router.post("/login")
async def mega_login(
    body: MegaLoginIn,
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    try:
        res = await sidecar_request(
            "POST", "/login", json=body.model_dump(), timeout=180.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    logger.info("mega_login", ok=bool(res.get("ok")), user_id=str(user.id))
    return res


class MegaSyncIn(BaseModel):
    dry_run: bool = True
    # only_missing: não sobrescreve fotos_url já preenchido (ex.: link
    # colado à mão) — só completa os produtos sem link.
    only_missing: bool = True
    root: str | None = None


@router.post("/sync")
async def mega_sync(
    body: MegaSyncIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    root = (body.root or _root()).strip() or "/"
    try:
        listing = await sidecar_request(
            "GET", "/folders", params={"root": root, "depth": 2}, timeout=600.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    # Containers de marca (pastas com subpastas, ex.: /Uranyx) ficam fora do
    # matching — os modelos são as subpastas deles.
    folder_items = [
        f for f in listing.get("items", []) if not f.get("has_children")
    ]

    products = (
        (
            await session.execute(
                select(PricingProduct)
                .where(user_scope(PricingProduct, user))
                .order_by(PricingProduct.sku)
            )
        )
        .scalars()
        .all()
    )
    rep = match_products_to_folders(list(products), folder_items)

    applied = 0
    errors: list[str] = []
    if not body.dry_run:
        url_by_path: dict[str, str] = {}
        for p, f in rep["matches"]:
            if body.only_missing and p.fotos_url:
                # Não mexe no link. O caminho só é preenchido quando está
                # VAZIO (link colado à mão, sem pasta conhecida). Quem já tem
                # pasta fica como está: o casamento é por NOME, e trocar o
                # caminho aqui mandava o próximo envio para outra pasta sem
                # trocar o link — medido em 29/09/2026, 3 malas "PP premium"
                # mudariam de pasta. Trocar pasta é pela tela (PUT /pasta).
                if not p.fotos_path:
                    p.fotos_path = f["path"]
                continue
            path = f["path"]
            url = url_by_path.get(path)
            if url is None:
                try:
                    exp = await sidecar_request(
                        "POST", "/export", json={"path": path}, timeout=120.0
                    )
                    url = str(exp["url"])
                    url_by_path[path] = url
                except MegaError as exc:
                    errors.append(f"{f['name']}: {exc.message}")
                    continue
            p.fotos_url = url
            p.fotos_path = path
            applied += 1
        await session.commit()
        logger.info(
            "mega_fotos_sync",
            applied=applied,
            matched=len(rep["matches"]),
            errors=len(errors),
            user_id=str(user.id),
        )

    to_apply = sum(
        1
        for p, _f in rep["matches"]
        if not (body.only_missing and p.fotos_url)
    )
    return {
        "dry_run": body.dry_run,
        "root": root,
        "folders_total": rep["folders_total"],
        "matched_total": len(rep["matches"]),
        "to_apply": to_apply,
        "applied": applied,
        "matched": [
            {
                "sku": p.sku,
                "name": p.name,
                "folder": f["name"],
                "has_url": bool(p.fotos_url),
            }
            for p, f in rep["matches"][:800]
        ],
        "ambiguous": [
            {
                "sku": a["product"].sku,
                "name": a["product"].name,
                "candidates": a["candidates"][:6],
            }
            for a in rep["ambiguous"][:200]
        ],
        "unmatched_products": [
            {"sku": p.sku, "name": p.name}
            for p in rep["unmatched_products"][:500]
        ],
        "unmatched_folders": [
            f.get("path", f["name"]) for f in rep["unmatched_folders"][:500]
        ],
        "errors": errors[:50],
    }


class MegaCountsIn(BaseModel):
    root: str | None = None


@router.post("/counts/refresh")
async def mega_counts_refresh(
    body: MegaCountsIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """Reconta as mídias de cada pasta-folha e espelha nos produtos que
    apontam pra ela (mesma pasta ⇒ mesma contagem em todas as linhas).
    Agora também embalagens e a data da contagem; a lógica mora em
    services/mega_midias.py porque o cron da madrugada faz o mesmo."""
    try:
        res = await recontar_todas(
            session, root=body.root, filtro=user_scope(PricingProduct, user)
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    await session.commit()
    logger.info("mega_fotos_counts_refresh", user_id=str(user.id), **res)
    return res


class MegaScaffoldIn(BaseModel):
    # Cria <root>/<brand>/<name> pra cada nome (uma pasta por modelo) e já
    # preenche fotos_url/fotos_path dos produtos que casarem pelo nome —
    # links de pasta valem mesmo vazias, as fotos entram depois.
    brand: str
    names: list[str]
    only_missing: bool = True


@router.post("/scaffold")
async def mega_scaffold(
    body: MegaScaffoldIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    brand = body.brand.strip().strip("/").replace("/", "-")
    if not brand:
        raise HTTPException(400, detail={"code": "brand_required"})
    seen: set[str] = set()
    names: list[str] = []
    for raw in body.names:
        n = (raw or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            names.append(n)
    if not names:
        raise HTTPException(400, detail={"code": "names_required"})
    if len(names) > 300:
        raise HTTPException(400, detail={"code": "too_many_folders"})

    brand_path = f"{_root().rstrip('/')}/{brand}"
    try:
        res = await sidecar_request(
            "POST",
            "/scaffold",
            json={"root": brand_path, "names": names},
            timeout=1800.0,
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    results = res.get("results", [])
    created = [r for r in results if r.get("url")]
    errors = [f"{r['name']}: {r['error']}" for r in results if r.get("error")]

    products = (
        (
            await session.execute(
                select(PricingProduct)
                .where(user_scope(PricingProduct, user))
                .order_by(PricingProduct.sku)
            )
        )
        .scalars()
        .all()
    )
    folder_items = [
        {"name": r["name"], "path": r["path"], "is_folder": True}
        for r in created
    ]
    rep = match_products_to_folders(list(products), folder_items)
    url_by_path = {r["path"]: r["url"] for r in created}
    applied = 0
    for p, f in rep["matches"]:
        url = url_by_path.get(f["path"])
        if not url:
            continue
        if body.only_missing and p.fotos_url:
            # Mesma regra do /sync: só completa caminho vazio.
            if not p.fotos_path:
                p.fotos_path = f["path"]
            continue
        p.fotos_url = url
        p.fotos_path = f["path"]
        applied += 1
    await session.commit()
    logger.info(
        "mega_fotos_scaffold",
        brand_path=brand_path,
        folders=len(created),
        applied=applied,
        errors=len(errors),
        user_id=str(user.id),
    )
    return {
        "brand_path": brand_path,
        "folders_created": len(created),
        "applied": applied,
        "matched": [
            {"sku": p.sku, "name": p.name, "folder": f["name"]}
            for p, f in rep["matches"][:800]
        ],
        "ambiguous": [
            {
                "sku": a["product"].sku,
                "name": a["product"].name,
                "candidates": a["candidates"][:6],
            }
            for a in rep["ambiguous"][:200]
        ],
        "unmatched_products": [
            {"sku": p.sku, "name": p.name}
            for p in rep["unmatched_products"][:500]
        ],
        "unmatched_folders": [
            f.get("path", f["name"]) for f in rep["unmatched_folders"][:500]
        ],
        "errors": errors[:50],
    }


# ─────────────── mídias de UM produto: fotos, vídeos e embalagens ───────────────


async def _produto(
    session: AsyncSession, user: User, product_id: UUID
) -> PricingProduct:
    row = (
        await session.execute(
            select(PricingProduct).where(
                PricingProduct.id == product_id,
                user_scope(PricingProduct, user),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "not_found"})
    return row


def _midias_out(row: PricingProduct) -> dict[str, Any]:
    """O que a linha da tabela precisa redesenhar depois de mexer na pasta —
    a mesma forma para enviar, recontar, ligar e desligar."""
    return {
        "fotos_url": row.fotos_url,
        "fotos_path": row.fotos_path,
        "embalagens_url": row.embalagens_url,
        "embalagens_path": row.embalagens_path,
        "fotos_count": row.fotos_count,
        "videos_count": row.videos_count,
        "embalagens_count": row.embalagens_count,
        "midias_contadas_em": row.midias_contadas_em,
    }


async def _recontar_sem_falhar(session: AsyncSession, fotos_path: str, **log: Any) -> None:
    """Depois de enviar ou ligar pasta, a contagem é o de menos: o arquivo já
    está no MEGA e o link já foi gravado. Se a contagem falhar, o cron da
    madrugada (ou o botão Recontar) acerta depois."""
    try:
        await recontar_pasta(session, fotos_path)
    except (MegaError, KeyError, TypeError, ValueError) as exc:
        logger.warning("mega_midias_recontagem_falhou", pasta=fotos_path, erro=str(exc), **log)


@router.post("/products/{product_id}/{tipo}/upload")
async def upload_product_midias(
    product_id: UUID,
    tipo: Literal["fotos", "embalagens"],
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """Sobe arquivos para a pasta do produto (`fotos`) ou para a subpasta
    "Embalagens" dela (`embalagens`). `/fotos/upload` é a URL antiga e
    continua valendo.

    Produto sem pasta ganha uma na raiz do departamento (/Celular, /Malas,
    /uranyx). Embalagem em produto sem pasta cria as DUAS (a caixa mora
    dentro da pasta de fotos) e grava os dois links.
    """
    row = await _produto(session, user, product_id)
    if not files:
        raise HTTPException(400, detail={"code": "no_files"})
    if len(files) > 60:
        raise HTTPException(400, detail={"code": "too_many_files"})
    # Trava no servidor: o `accept` do <input> é sugestão do navegador, e
    # arrastar arquivo passa por cima dele. Recusa o lote inteiro e diz quais.
    recusados = [f.filename or "(sem nome)" for f in files if not extensao_aceita(tipo, f.filename)]
    if recusados:
        raise HTTPException(
            400, detail={"code": "tipo_nao_aceito", "arquivos": recusados[:60]}
        )

    pasta_fotos = row.fotos_path or await pasta_nova_do_produto(session, row)
    pasta_fotos_nova = not row.fotos_path
    if tipo == "fotos":
        dest = pasta_fotos
    else:
        dest = row.embalagens_path
        if not dest and not pasta_fotos_nova:
            # A subpasta pode ter sido criada à mão ("embalagem", "EMBALAGENS")
            # e a contagem ainda não passou por ela. Sem esta pergunta o envio
            # criaria uma SEGUNDA pasta "Embalagens" ao lado.
            try:
                cnt = await sidecar_request(
                    "GET", "/media_counts", params={"path": pasta_fotos}, timeout=300.0
                )
                dest = cnt.get("embalagens_pasta") or None
            except MegaError:
                dest = None
        dest = dest or f"{pasta_fotos.rstrip('/')}/{PASTA_EMBALAGEM_PADRAO}"

    files_payload = [
        (
            "files",
            (
                f.filename or "arquivo",
                f.file,
                f.content_type or "application/octet-stream",
            ),
        )
        for f in files
    ]
    link_fotos: str | None = None
    try:
        # O /upload do sidecar faz `mega-mkdir -p`: a pasta de fotos nasce
        # junto com a de embalagens quando o produto não tinha nenhuma.
        up = await sidecar_request(
            "POST",
            "/upload",
            data={"dest": dest},
            files=files_payload,
            timeout=3600.0,
        )
        exp = await sidecar_request(
            "POST", "/export", json={"path": dest}, timeout=120.0
        )
        if tipo == "embalagens" and (pasta_fotos_nova or not row.fotos_url):
            exp_fotos = await sidecar_request(
                "POST", "/export", json={"path": pasta_fotos}, timeout=120.0
            )
            link_fotos = str(exp_fotos["url"])
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    if tipo == "fotos":
        row.fotos_path = dest
        row.fotos_url = str(exp["url"])
    else:
        if link_fotos:
            row.fotos_path = pasta_fotos
            row.fotos_url = link_fotos
        # A caixa é da LINHA: todo produto com a mesma pasta de fotos ganha o
        # link da mesma subpasta.
        irmaos = (
            (
                await session.execute(
                    select(PricingProduct).where(PricingProduct.fotos_path == pasta_fotos)
                )
            )
            .scalars()
            .all()
        )
        for p in {*irmaos, row}:
            p.embalagens_path = dest
            p.embalagens_url = str(exp["url"])
    await session.flush()
    await _recontar_sem_falhar(session, pasta_fotos, sku=row.sku)

    await session.commit()
    logger.info(
        "mega_midias_upload",
        tipo=tipo,
        sku=row.sku,
        uploaded=up.get("uploaded"),
        dest=dest,
        user_id=str(user.id),
    )
    return {"tipo": tipo, "uploaded": up.get("uploaded", len(files)), **_midias_out(row)}


@router.post("/products/{product_id}/recontar")
async def recontar_produto(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """Reconta só a pasta deste produto (e grava nos que dividem a pasta).
    Para quem mexeu direto no MEGA e não quer esperar a madrugada."""
    row = await _produto(session, user, product_id)
    if not row.fotos_path:
        raise HTTPException(400, detail={"code": "sem_pasta"})
    try:
        await recontar_pasta(session, row.fotos_path)
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    await session.commit()
    return _midias_out(row)


# tipo da tela → tipo do /files do sidecar, e a extensão que cada um aceita.
# A extensão é conferida de novo AQUI porque o sidecar antigo (antes do
# rebuild do deploy) ignora `tipo` e devolve só imagens, com embalagem junto.
_TIPO_FILES = {"fotos": "imagens", "videos": "videos", "embalagens": "embalagens"}


@router.get("/products/{product_id}/midias")
async def listar_midias(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "view"))
    ],
    tipo: Literal["fotos", "videos", "embalagens"] = "fotos",
) -> dict[str, Any]:
    """Os arquivos de um tipo, para a tela mostrar sem abrir o MEGA.

    `nome` é RELATIVO à pasta do produto ("M1 listrada/b005.jpg",
    "Embalagens/caixa.pdf") — é ele que volta no /midias/arquivo. `imagem`
    diz se o navegador DESENHA o arquivo (jpg/png/webp/gif): heic, tiff e PDF
    viram ícone com download, não miniatura quebrada.
    """
    row = await _produto(session, user, product_id)
    if not row.fotos_path:
        return {"pasta": None, "url": None, "arquivos": []}
    try:
        resp = await sidecar_request(
            "GET",
            "/files",
            params={"path": row.fotos_path, "tipo": _TIPO_FILES[tipo]},
            timeout=300.0,
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    if resp.get("rc") not in (0, None):
        # O /files devolve 200 com rc≠0 quando o mega-find falha — quase
        # sempre pasta apagada ou renomeada direto no MEGA.
        raise HTTPException(
            502,
            detail={
                "code": "mega_sidecar",
                "message": f"o MEGA não abriu a pasta {row.fotos_path}: "
                + str(resp.get("out") or "")[-300:],
            },
        )

    arquivos: list[dict[str, Any]] = []
    for a in resp.get("arquivos") or []:
        nome = str(a.get("nome") or "")
        if not nome:
            continue
        ext = str(a.get("ext") or extensao(nome)).lower()
        de_embalagem = eh_de_embalagens(nome)
        if tipo == "embalagens":
            if not de_embalagem:
                continue
        elif de_embalagem or ext not in (EXT_IMAGEM if tipo == "fotos" else EXT_VIDEO):
            continue
        arquivos.append({"nome": nome, "ext": ext, "imagem": f".{ext}" in _EXT_IMAGEM})

    if tipo == "embalagens":
        pasta, url = row.embalagens_path, row.embalagens_url
        if pasta is None and arquivos:
            # Pasta criada à mão que a contagem ainda não ligou: o caminho sai
            # da própria listagem; o link só depois de Recontar.
            pasta = f"{row.fotos_path.rstrip('/')}/{arquivos[0]['nome'].split('/', 1)[0]}"
    else:
        pasta, url = row.fotos_path, row.fotos_url
    return {"pasta": pasta, "url": url, "arquivos": arquivos}


def _nome_invalido(nome: str) -> bool:
    # Subpasta é legítima (as malas têm uma por modelo, a caixa mora em
    # "Embalagens/"). Subir de nível não é, e barra no começo viraria caminho
    # absoluto lá do outro lado. Byte de controle trunca caminho no syscall.
    #
    # O /file do sidecar roda `mega-get`, que é RECURSIVO: com nome de pasta
    # ("M1 listrada", "Embalagens", ".", "a//b", "sub/.") ou curinga ("*",
    # "clip.mp?", que o MEGAcmd trata como padrão) ele baixava a pasta
    # inteira da linha — fotos e MP4 — para o disco do servidor, e só depois
    # devolvia 404. Nome que vem da listagem (/files) é sempre arquivo, com
    # extensão, sem trecho vazio ou "." e sem curinga; nada legítimo é barrado.
    # Resta a subpasta com ponto no nome ("M2 v1.5"), que tem "extensão" —
    # essa só se fecha no sidecar, conferindo se o caminho é pasta.
    return (
        not nome.strip()
        or ".." in nome
        or nome.startswith("/")
        or any(ord(c) < 32 for c in nome)
        or any(s.strip() in ("", ".") for s in nome.split("/"))
        or any(c in nome for c in "*?")
        or not extensao(nome)
    )


@router.get("/products/{product_id}/midias/arquivo")
async def baixar_midia(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "view"))
    ],
    nome: Annotated[str, Query(max_length=1000)],
    baixar: bool = False,
    miniatura: bool = False,
) -> Response:
    """Os bytes de UM arquivo da pasta do produto.

    Imagem que o navegador desenha sai inline (miniatura); PDF, AI, PSD, ZIP e
    o resto saem SEMPRE como download (`octet-stream` + `attachment`), que o
    navegador não executa — SVG incluso, porque SVG carrega <script>.
    Vídeo não passa por aqui: são centenas de MB, e o MEGA toca melhor.
    """
    row = await _produto(session, user, product_id)
    if _nome_invalido(nome):
        raise HTTPException(400, detail={"code": "nome_invalido"})
    if not row.fotos_path:
        raise HTTPException(404, detail={"code": "sem_pasta"})
    ext = extensao(nome)
    if ext in EXT_VIDEO:
        raise HTTPException(400, detail={"code": "video_abre_no_mega"})
    # O MIME sai da EXTENSÃO, nunca do que o MEGA disser (mesma regra do
    # portal e dos anexos do Marketing).
    media, disposicao = mime_seguro(_EXT_IMAGEM.get(f".{ext}"), permitidos=MIMES_IMAGEM)
    if baixar:
        disposicao = "attachment"
    elif miniatura and disposicao == "inline":
        # A grade do painel pede a miniatura: 24 fotos do fornecedor eram
        # ~25 MB por aba (medido 29/09/2026 no Fossibot S7). O sidecar reduz
        # para JPEG de 320 px. Sidecar antigo (sem /thumb, 404) ou imagem que
        # não abre (422) → segue para o arquivo inteiro, como antes.
        try:
            menor = await sidecar_bytes(
                "/thumb", params={"path": f"{row.fotos_path}/{nome}", "lado": 320}
            )
        except MegaError as exc:
            if exc.status_code == 400:
                raise HTTPException(400, detail={"code": "nome_invalido"}) from exc
            if exc.status_code not in (404, 422):
                raise _sidecar_http_error(exc) from exc
        else:
            return Response(
                content=menor,
                media_type="image/jpeg",
                headers={
                    "Content-Disposition": disposicao_segura("inline", nome),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, max-age=86400",
                },
            )
    try:
        pedacos, fechar = await sidecar_stream(
            "/file", params={"path": f"{row.fotos_path}/{nome}"}
        )
    except MegaError as exc:
        if exc.status_code == 404:
            raise HTTPException(404, detail={"code": "arquivo_nao_encontrado"}) from exc
        if exc.status_code == 400:
            raise HTTPException(400, detail={"code": "nome_invalido"}) from exc
        raise _sidecar_http_error(exc) from exc
    return StreamingResponse(
        pedacos,
        media_type=media,
        headers={
            "Content-Disposition": disposicao_segura(disposicao, nome),
            "X-Content-Type-Options": "nosniff",
            # Conteúdo de catálogo muda pouco e a volta ao MEGA é cara.
            "Cache-Control": "private, max-age=86400",
        },
        background=BackgroundTask(fechar),
    )


# ─────────────── trocar / desligar a pasta de UM produto ───────────────


# Só pasta DENTRO destas raízes vira pasta de produto. Com a raiz "/" de
# produção, a listagem de 2 níveis traz também /_geral/CONTABIL - servidor,
# /_geral/URANYX AUTORIZACAO, /_geral/Anatel, /Eletro, /charlots park… — e
# elas apareciam PRIMEIRO no seletor ("_" vem antes de "c"). Ligar um produto
# a uma delas gera link PÚBLICO da pasta (mega-export), abre os arquivos dela
# para quem só vê a tabela (/midias/arquivo) e põe a pasta na recontagem da
# madrugada. As raízes são as mesmas onde o 1º envio cria a pasta.
_RAIZES_DE_PRODUTO = tuple(RAIZ_POR_DEPARTAMENTO.values())


def _dentro_das_raizes(path: str) -> bool:
    # Estritamente dentro: a própria /Celular vazia não é pasta de produto.
    return any(path.startswith(f"{r}/") for r in _RAIZES_DE_PRODUTO)


def _pastas_folha(listing: dict[str, Any]) -> list[dict[str, str]]:
    """As pastas que podem ser pasta de produto: as mesmas folhas que a
    sincronização casa por nome (container de marca fica fora), só dentro de
    /Celular, /Malas e /uranyx. Uma pasta "Embalagens" nunca é pasta de
    produto — é subpasta de uma."""
    pastas = [
        {"path": str(f["path"]), "name": str(f["name"])}
        for f in listing.get("items", [])
        if f.get("is_folder", True)
        and not f.get("has_children")
        and not eh_de_embalagens(str(f.get("name") or ""))
        and _dentro_das_raizes(str(f.get("path") or ""))
    ]
    pastas.sort(key=lambda p: p["path"].casefold())
    return pastas


async def _listar_pastas() -> list[dict[str, str]]:
    try:
        listing = await sidecar_request(
            "GET", "/folders", params={"root": _root(), "depth": 2}, timeout=600.0
        )
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc
    return _pastas_folha(listing)


@router.get("/pastas")
async def listar_pastas(
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """As pastas de produto da conta MEGA, para escolher numa lista em vez de
    digitar caminho."""
    return {"pastas": await _listar_pastas()}


class PastaIn(BaseModel):
    path: str


@router.put("/products/{product_id}/pasta")
async def ligar_pasta(
    product_id: UUID,
    body: PastaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """Liga ESTE produto a uma pasta que já existe no MEGA.

    Gera o link, esquece a embalagem da pasta antiga (a nova pode ter a
    dela, e a contagem logo abaixo acha) e reconta. Só esta linha muda — os
    irmãos da pasta antiga continuam onde estão.
    """
    row = await _produto(session, user, product_id)
    path = body.path.rstrip("/")
    # O caminho TEM de ser um da lista do /pastas: sem isso, um PUT à mão
    # ligaria o produto a qualquer pasta da conta (ou criaria link público de
    # uma pasta que não é de produto).
    if not path.startswith("/") or ".." in path or not path.strip("/").strip():
        raise HTTPException(400, detail={"code": "pasta_invalida"})
    if path not in {p["path"] for p in await _listar_pastas()}:
        raise HTTPException(400, detail={"code": "pasta_invalida"})
    try:
        exp = await sidecar_request("POST", "/export", json={"path": path}, timeout=120.0)
    except MegaError as exc:
        raise _sidecar_http_error(exc) from exc

    row.fotos_path = path
    row.fotos_url = str(exp["url"])
    row.embalagens_url = row.embalagens_path = None
    # Números da pasta antiga não valem para a nova; se a contagem abaixo
    # falhar, a tela mostra "não contado" em vez de um número errado.
    row.fotos_count = row.videos_count = row.embalagens_count = None
    row.midias_contadas_em = None
    await session.flush()
    await _recontar_sem_falhar(session, path, sku=row.sku)
    await session.commit()
    logger.info("mega_pasta_ligada", sku=row.sku, pasta=path, user_id=str(user.id))
    return _midias_out(row)


@router.delete("/products/{product_id}/pasta")
async def desligar_pasta(
    product_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[
        User, Depends(require_permission("tabela_precos_produtos", "edit"))
    ],
) -> dict[str, Any]:
    """Desliga a pasta SÓ desta linha. Nada é apagado no MEGA — os arquivos
    continuam lá, e os irmãos que dividem a pasta continuam ligados."""
    row = await _produto(session, user, product_id)
    antiga = row.fotos_path
    row.fotos_url = row.fotos_path = None
    row.embalagens_url = row.embalagens_path = None
    row.fotos_count = row.videos_count = row.embalagens_count = None
    row.midias_contadas_em = None
    await session.commit()
    logger.info("mega_pasta_desligada", sku=row.sku, pasta=antiga, user_id=str(user.id))
    return _midias_out(row)
