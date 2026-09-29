"""Mídias do produto no MEGA — fotos, vídeos e EMBALAGENS — e a contagem delas.

Eduardo, 29/09/2026: "um campo do lado de fotos, chamado EMBALAGENS, onde
vamos concentrar todas as nossas fotos de embalagens, caixa etc, vai ir para
o mega normal, mesmo processo de fotos, só que um campo separado".

A embalagem mora na subpasta "Embalagens" DENTRO da pasta de fotos da linha
(`{fotos_path}/Embalagens`). Assim a linha continua com UMA pasta no MEGA, a
caixa acompanha quando a pasta muda de lugar, e o sidecar separa na contagem:
o que está lá dentro conta só como embalagem (ver `_classificar` em
infra/megacmd/app.py) e nunca aparece no portal das agências.

Fica em `services/` porque três lugares recontam — o botão da linha, o botão
"Recontar" da aba e o cron da madrugada (worker) — e o worker não importa
router.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PricingProduct, Segment
from app.services.mega_fotos import MegaError, sidecar_bytes, sidecar_request

logger = structlog.get_logger()

# O que cada botão aceita, pela EXTENSÃO. Antes só o `accept` do <input>
# filtrava — e o `accept` é sugestão do navegador, não trava. Imagem e vídeo
# são os mesmos conjuntos da contagem do sidecar (_IMG_EXT/_VID_EXT): o que
# sobe tem de ser o que é contado.
EXT_IMAGEM = frozenset(
    {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "bmp", "tif", "tiff", "avif", "jfif"}
)
EXT_VIDEO = frozenset(
    {"mp4", "mov", "m4v", "avi", "mkv", "webm", "3gp", "mpg", "mpeg", "wmv", "flv"}
)
# Embalagem é foto da caixa E arquivo de gráfica: a arte vem da agência em PDF,
# Illustrator, Photoshop, Corel, EPS/SVG, ou tudo zipado. Vídeo não — vídeo de
# caixa é criativo e vai em Fotos. Affinity (.af e as extensões antigas
# .afdesign/.afphoto/.afpub): é onde as caixas da Uranyx são desenhadas — a
# pasta ~/Downloads/CAIXAS do Eduardo tinha 14 .af editáveis (29/09/2026).
EXT_EMBALAGEM = EXT_IMAGEM | {
    "pdf", "ai", "psd", "eps", "cdr", "svg", "zip", "af", "afdesign", "afphoto", "afpub",
}
# Arquivo de gráfica que ganha PRÉVIA na tela (1ª página do PDF/AI, a prévia
# que o Affinity grava dentro do .af, a imagem composta do PSD) — gerada pelo
# /thumb do sidecar. O resto (EPS, CDR, SVG, ZIP) fica com o ícone.
EXT_COM_PREVIA = frozenset({"pdf", "ai", "psd", "af", "afdesign", "afphoto", "afpub"})
EXT_POR_TIPO: dict[str, frozenset[str]] = {
    "fotos": EXT_IMAGEM | EXT_VIDEO,
    "embalagens": EXT_EMBALAGEM,
}

# Onde nasce a pasta de um produto que ainda não tem. Antes a pasta era
# "{raiz da conta}/{nome}" — com a raiz "/" de produção, o envio de um
# produto sem pasta criava a pasta SOLTA na raiz da conta, fora de /Celular e
# /Malas. As três raízes são onde as linhas já estão (29/09/2026: 43 produtos
# em /Celular, 38 em /Malas, os 5 eletro em /uranyx — que é também o que o
# portal das agências enxerga).
RAIZ_POR_DEPARTAMENTO = {"celular": "/Celular", "mala": "/Malas", "eletro": "/uranyx"}


def raiz_da_conta() -> str:
    return get_settings().mega_fotos_root.strip() or "/"


def extensao(nome: str | None) -> str:
    return Path(nome or "").suffix.lower().lstrip(".")


def extensao_aceita(tipo: str, nome: str | None) -> bool:
    return extensao(nome) in EXT_POR_TIPO.get(tipo, frozenset())


async def _slug_da_raiz(session: AsyncSession, row: PricingProduct) -> str | None:
    """Slug do segmento RAIZ do produto (o produto aponta para a folha)."""
    seg = await session.get(Segment, row.segment_id) if row.segment_id else None
    # Árvore tem 2 níveis hoje; o teto só impede laço se um dia virar ciclo.
    for _ in range(5):
        if seg is None or seg.parent_id is None:
            break
        seg = await session.get(Segment, seg.parent_id)
    return seg.slug if seg is not None else None


async def pasta_nova_do_produto(session: AsyncSession, row: PricingProduct) -> str:
    """Onde criar a pasta de fotos de um produto que ainda não tem.

    Departamento = segmento raiz; se ele não disser nada (segmento sem as
    três raízes), vale a coluna legada `department`. Departamento
    desconhecido continua como era: "{raiz da conta}/{nome}".
    """
    slug = await _slug_da_raiz(session, row)
    legado = row.department.value if row.department is not None else None
    raiz = next(
        (RAIZ_POR_DEPARTAMENTO[s] for s in (slug, legado) if s in RAIZ_POR_DEPARTAMENTO),
        raiz_da_conta(),
    )
    nome = (row.name or row.sku).strip().replace("/", "-")
    return f"{raiz.rstrip('/')}/{nome}"


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def _aplicar(
    linhas: list[PricingProduct],
    contagem: dict[str, Any],
    *,
    agora: datetime,
    links: dict[str, str],
) -> int:
    """Grava a contagem de UMA pasta em todas as linhas que apontam para ela
    e deixa a pasta de embalagens delas igual à que o MEGA tem.

    - Pasta de embalagens achada (inclusive criada à mão, ou renomeada de
      "Embalagens" para "embalagem"): as linhas passam a apontar para ela. O
      link é exportado uma vez por pasta (`links` é o cache da rodada).
    - Pasta sumiu do MEGA: o link morto sai da linha; o próximo envio cria de
      novo.
    - Sidecar antigo (sem a chave "embalagens" na resposta): embalagem não é
      mexida. Sem isso, a janela do deploy em que a API nova fala com o
      container velho apagaria o link de todas as caixas.

    Devolve quantas linhas mudaram de número ou de pasta.
    """
    fotos = _int(contagem.get("fotos"))
    if fotos is None:
        return 0
    videos = _int(contagem.get("videos")) or 0
    sabe_embalagem = "embalagens" in contagem
    embalagens = _int(contagem.get("embalagens")) if sabe_embalagem else None
    pasta_emb = (contagem.get("embalagens_pasta") or None) if sabe_embalagem else None

    url_emb: str | None = None
    if pasta_emb:
        url_emb = links.get(pasta_emb) or next(
            (
                p.embalagens_url
                for p in linhas
                if p.embalagens_path == pasta_emb and p.embalagens_url
            ),
            None,
        )
        if url_emb is None:
            try:
                exp = await sidecar_request(
                    "POST", "/export", json={"path": pasta_emb}, timeout=120.0
                )
                url_emb = str(exp["url"])
            except (MegaError, KeyError, TypeError) as exc:
                # Sem link não dá para apontar a linha para a pasta; fica para
                # a próxima contagem. O número em si é gravado do mesmo jeito.
                logger.warning("mega_embalagens_link_falhou", pasta=pasta_emb, erro=str(exc))
        if url_emb:
            links[pasta_emb] = url_emb

    mudou = 0
    for p in linhas:
        antes = (
            p.fotos_count, p.videos_count, p.embalagens_count, p.embalagens_path, p.embalagens_url
        )
        p.fotos_count, p.videos_count = fotos, videos
        if sabe_embalagem:
            p.embalagens_count = embalagens
            if pasta_emb:
                if url_emb:
                    p.embalagens_path, p.embalagens_url = pasta_emb, url_emb
            else:
                p.embalagens_path = p.embalagens_url = None
        p.midias_contadas_em = agora
        depois = (
            p.fotos_count, p.videos_count, p.embalagens_count, p.embalagens_path, p.embalagens_url
        )
        if antes != depois:
            mudou += 1
    return mudou


async def recontar_pasta(session: AsyncSession, fotos_path: str) -> dict[str, Any]:
    """Reconta UMA pasta e grava em todas as linhas que a dividem (a pasta é
    da linha: variações 4/128 e 8/256 dividem as mesmas fotos e a mesma
    caixa). Não faz commit. Erro do sidecar sobe como MegaError."""
    cnt = await sidecar_request(
        "GET", "/media_counts", params={"path": fotos_path}, timeout=300.0
    )
    linhas = list(
        (
            await session.execute(
                select(PricingProduct).where(PricingProduct.fotos_path == fotos_path)
            )
        )
        .scalars()
        .all()
    )
    await _aplicar(linhas, cnt, agora=datetime.now(UTC), links={})
    return cnt


async def recontar_todas(
    session: AsyncSession, *, root: str | None = None, filtro: Any = None
) -> dict[str, Any]:
    """Reconta todas as pastas de produto. Não faz commit.

    Uma ida ao sidecar lista e conta as pastas-folha da conta (a mesma
    listagem da sincronização). Produto cuja pasta não está nessa listagem
    (pasta fora da raiz, ou um nível mais fundo) é contado sozinho, pasta a
    pasta — antes ele simplesmente nunca era contado.
    """
    root = (root or raiz_da_conta()).strip() or "/"
    listing = await sidecar_request(
        "GET",
        "/folders",
        params={"root": root, "depth": 2, "media_counts": 1},
        timeout=1800.0,
    )
    por_pasta: dict[str, dict[str, Any]] = {}
    for f in listing.get("items", []):
        if f.get("has_children") or not f.get("is_folder", True):
            continue
        if f.get("fotos") is None:
            continue
        por_pasta[f["path"]] = f

    stmt = select(PricingProduct).where(PricingProduct.fotos_path.is_not(None))
    if filtro is not None:
        stmt = stmt.where(filtro)
    produtos = list((await session.execute(stmt)).scalars().all())
    grupos: dict[str, list[PricingProduct]] = {}
    for p in produtos:
        if (p.fotos_path or "").strip():
            grupos.setdefault(p.fotos_path or "", []).append(p)

    agora = datetime.now(UTC)
    links: dict[str, str] = {}
    atualizados = 0
    avulsas = falhas = 0
    for path, linhas in grupos.items():
        contagem = por_pasta.get(path)
        if contagem is None:
            avulsas += 1
            try:
                contagem = await sidecar_request(
                    "GET", "/media_counts", params={"path": path}, timeout=300.0
                )
            except MegaError as exc:
                # Pasta apagada ou renomeada à mão no MEGA: fica o número
                # antigo e o log — o operador religa pela tela.
                falhas += 1
                logger.warning("mega_recontar_pasta_falhou", pasta=path, erro=exc.message)
                continue
        atualizados += await _aplicar(linhas, contagem, agora=agora, links=links)
    return {
        "root": root,
        "folders_counted": len(por_pasta),
        "products_updated": atualizados,
        "products_with_folder": len(produtos),
        "pastas_avulsas": avulsas,
        "pastas_com_erro": falhas,
        "pastas_de_embalagens": len(links),
    }


# ─────────────── pré-aquecimento das prévias ───────────────


async def pastas_de_produto(session: AsyncSession) -> list[str]:
    stmt = select(PricingProduct.fotos_path).where(PricingProduct.fotos_path.is_not(None))
    # Sem strip: a chave do cache no sidecar é o caminho EXATO que a tela pede.
    return sorted({p for p in (await session.execute(stmt)).scalars() if (p or "").strip()})


async def aquecer_previas(
    session: AsyncSession | None = None, *, paralelo: int = 4, pastas: list[str] | None = None
) -> dict[str, Any]:
    """Gera no sidecar a miniatura (e, junto, a prévia grande) de toda foto e
    de toda arte com prévia das pastas de produto — para ninguém esperar o
    MEGA na hora de abrir o painel (Eduardo 29/09: "para todos precisa ser
    rápido e bem otimizado").

    O sidecar guarda em disco e reconhece o que já fez (chave com tamanho e
    data do arquivo no MEGA): depois da 1ª vez, cada item custa um `mega-ls`
    (~70 ms) e só o que é novo ou mudou é baixado. `paralelo` baixo de
    propósito: é o mesmo MEGAcmd que atende a tela e o portal das agências.
    Vídeo fica de fora (abre no MEGA). Não mexe no banco.
    """
    if pastas is None:
        if session is None:
            raise ValueError("aquecer_previas precisa de session ou pastas")
        pastas = await pastas_de_produto(session)
    alvos: list[str] = []
    erros_lista = 0
    for pasta in pastas:
        for tipo in ("imagens", "embalagens"):
            try:
                resp = await sidecar_request(
                    "GET", "/files", params={"path": pasta, "tipo": tipo}, timeout=300.0
                )
            except MegaError:
                erros_lista += 1
                continue
            for a in resp.get("arquivos") or []:
                nome = str(a.get("nome") or "")
                if a.get("imagem") or extensao(nome) in EXT_COM_PREVIA:
                    alvos.append(f"{pasta}/{nome}")

    sem = asyncio.Semaphore(max(1, paralelo))
    feitos = falhas = 0

    async def um(caminho: str) -> None:
        nonlocal feitos, falhas
        async with sem:
            try:
                await sidecar_bytes("/thumb", params={"path": caminho, "lado": 320}, timeout=600.0)
                feitos += 1
            except MegaError:
                falhas += 1  # 422 = sem prévia (fica anotado no sidecar)

    await asyncio.gather(*(um(c) for c in dict.fromkeys(alvos)))
    return {
        "pastas": len(pastas),
        "arquivos": len(set(alvos)),
        "prontos": feitos,
        "sem_previa": falhas,
        "pastas_com_erro": erros_lista,
    }

