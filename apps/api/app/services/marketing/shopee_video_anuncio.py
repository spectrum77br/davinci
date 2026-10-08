"""Shopee Vídeo: o ANÚNCIO certo da loja e as guardas do vídeo.

O vídeo da Shopee sai DENTRO de uma loja e leva um anúncio dela junto
(`item_info[].item_id` — por anúncio, sem variação). Vincular errado não tem
conserto: depois de publicado, nem o produto nem a legenda se editam, e
produto "irrelevante" faz a Shopee apagar o vídeo e tirar ponto da conta.

Regras do dono (rotina v2, 06/10/2026, e o pedido de 08/10):

  • o anúncio AVULSO DEDICADO do aparelho do vídeo — nunca kit
    ("dg088.ci+a001.ci"), e nunca a vitrine de vários aparelhos quando o
    dedicado existe;
  • anúncio vivo e com estoque; sem anúncio na loja = o vídeo não sai NELA
    (motivo DO VÍDEO: o robô pula pro próximo);
  • vídeo de 3 a 60 s, 720p ou mais, H.264.

De onde sai o anúncio: `product_links` da integração da conta (o que o sync
de estoque mantém vivo — o estoque ali é o que o DaVinci mandou por último).
`listings` é cópia de maio e fica de fora. O status do anúncio (NORMAL,
UNLIST, BANNED) não é guardado pra Shopee: quem confere isso, ao vivo, é o
publicador, pelo client da integração (`conferir_ao_vivo`), só leitura.

"Dedicado" na prática: na Barbosa cada cor/memória é um SKU-base diferente
(o WP60 tem 6, o F110 2), então "uma base só" não serve. O que separa o
dedicado da vitrine é ser o anúncio MAIS ESPECÍFICO entre os que têm o
aparelho: o F110L está no 58262693089 (2 bases, F110 Pro e F110L) e na
vitrine 58269759596 (14 bases, 8 aparelhos) — o de menos bases ganha.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    Product,
    ProductLink,
    RedeSocial,
)
from app.services.marketing.shopee_video import (
    CODECS_ACEITOS,
    DURACAO_MAX_S,
    DURACAO_MIN_S,
    LADO_MENOR_MIN,
    PLATAFORMA_SHOPEE,
    TAMANHO_MAX,
)

logger = structlog.get_logger()

# Motivos DO VÍDEO (o robô pula pro próximo; o modal mostra na conta).
SEM_ANUNCIO = "sem_anuncio_na_loja"
SO_KIT = "anuncio_so_em_kit"
SEM_ESTOQUE = "anuncio_sem_estoque"
SKU_AMBIGUO = "sku_ambiguo"
SEM_SKU = "criativo_sem_sku"
FORA_DA_DURACAO = "video_fora_da_duracao"
RESOLUCAO_BAIXA = "video_resolucao_baixa"
FORMATO = "video_formato_nao_aceito"
ILEGIVEL = "video_ilegivel"
GRANDE_DEMAIS = "video_grande_demais"
EM_OUTRA_LOJA = "video_ja_na_shopee_em_outra_loja"

MOTIVOS_DO_VIDEO = frozenset(
    {
        SEM_ANUNCIO,
        SO_KIT,
        SEM_ESTOQUE,
        SKU_AMBIGUO,
        SEM_SKU,
        FORA_DA_DURACAO,
        RESOLUCAO_BAIXA,
        FORMATO,
        ILEGIVEL,
        GRANDE_DEMAIS,
        EM_OUTRA_LOJA,
    }
)

# Ordem de "qual motivo explica melhor" quando nenhum SKU do vídeo achou
# anúncio: estoque zerado diz mais que "só kit", que diz mais que "nada".
_PESO = {SEM_ESTOQUE: 3, SO_KIT: 2, SEM_ANUNCIO: 1}

# "dg052-4" é faixa (vários aparelhos): não dá pra escolher UM anúncio.
_RE_FAIXA = re.compile(r"\d\s*-\s*\d")
_RE_SEPARADOR = re.compile(r"[,;/\s]+")


def skus_do_criativo(sku: str | None) -> list[str]:
    """ "dg089.ci, dg088.ci" → ["dg089.ci", "dg088.ci"] (minúsculo, sem repetir)."""
    out: list[str] = []
    for parte in _RE_SEPARADOR.split((sku or "").strip().lower()):
        if parte and parte not in out:
            out.append(parte)
    return out


def _base(sku: str) -> str:
    return sku.split(".")[0]


def _eh_kit(sku: str) -> bool:
    return "+" in sku or "kit" in sku


def _eh_usado(sku: str, nome: str | None) -> bool:
    # `.us` é USADO (e às vezes outro aparelho: dg007.us é o Doogee Fire 6
    # power) — mesmo cuidado de `vinculos._product_id_do_sku`.
    return "us" in sku.split(".")[1:] or "usado" in (nome or "").lower()


@dataclass(slots=True)
class AnuncioEscolhido:
    item_id: int
    titulo: str | None
    skus: list[str]
    estoque: int
    bases_no_anuncio: int
    exato: bool

    def snapshot(self) -> dict[str, Any]:
        """O que vai pro `opcoes.shopee` da postagem: o publicador usa
        EXATAMENTE este item (e confere de novo, ao vivo, antes de postar)."""
        return {
            "item_id": self.item_id,
            "item_titulo": (self.titulo or "")[:200] or None,
            "skus": self.skus,
            "estoque_no_agendamento": self.estoque,
            "bases_no_anuncio": self.bases_no_anuncio,
        }


async def _melhor_item(
    session: AsyncSession, integration_id: UUID, sku: str
) -> AnuncioEscolhido | str:
    """O anúncio da loja para UM SKU do vídeo, ou o motivo de não ter."""
    externo = func.lower(func.btrim(ProductLink.external_sku))
    kit_pedido = "+" in sku
    filtro = (
        externo == sku
        if kit_pedido
        else or_(externo == sku, func.split_part(externo, ".", 1) == _base(sku))
    )
    linhas = (
        await session.execute(
            select(ProductLink.external_id, externo, Product.name)
            .join(Product, Product.id == ProductLink.product_id)
            .where(
                ProductLink.integration_id == integration_id,
                ProductLink.morto_desde.is_(None),
                ProductLink.external_sku.isnot(None),
                filtro,
            )
        )
    ).all()
    if not linhas:
        return SEM_ANUNCIO
    if kit_pedido:
        # O criativo É o kit (vídeo mostra todos os itens): só o kit exato.
        avulsos = [(i, s) for i, s, _n in linhas if s == sku]
        kits: list[Any] = []
    else:
        kits = [(i, s) for i, s, _n in linhas if _eh_kit(s)]
        avulsos = [
            (i, s) for i, s, n in linhas if not _eh_kit(s) and (s == sku or not _eh_usado(s, n))
        ]
    if not avulsos:
        return SO_KIT if kits else SEM_ANUNCIO

    itens = sorted({str(i) for i, _s in avulsos})
    exatos = {str(i) for i, s in avulsos if s == sku}
    # Todas as variações VIVAS de cada candidato: quantos aparelhos (bases)
    # o anúncio carrega e quanto estoque ele tem.
    todas = (
        await session.execute(
            select(
                ProductLink.external_id,
                externo,
                ProductLink.stock,
                ProductLink.listing_title,
            ).where(
                ProductLink.integration_id == integration_id,
                ProductLink.morto_desde.is_(None),
                ProductLink.external_id.in_(itens),
            )
        )
    ).all()
    por_item: dict[str, dict[str, Any]] = {
        i: {"bases": set(), "estoque": 0, "titulo": None} for i in itens
    }
    for item, s, estoque, titulo in todas:
        d = por_item[str(item)]
        if s:
            d["bases"].add(_base(s))
        d["estoque"] += max(0, int(estoque or 0))
        d["titulo"] = d["titulo"] or titulo
    ordem = sorted(
        itens,
        key=lambda i: (
            len(por_item[i]["bases"]) or 999,
            i not in exatos,
            -por_item[i]["estoque"],
            i,
        ),
    )
    melhor = ordem[0]
    d = por_item[melhor]
    if d["estoque"] <= 0:
        # O dedicado sem estoque NÃO cai pra vitrine: vídeo de aparelho
        # vinculado na vitrine é o "irrelevante" que a Shopee apaga.
        return SEM_ESTOQUE
    try:
        item_id = int(melhor)
    except ValueError:
        return SEM_ANUNCIO
    return AnuncioEscolhido(
        item_id=item_id,
        titulo=d["titulo"],
        skus=[sku],
        estoque=int(d["estoque"]),
        bases_no_anuncio=len(d["bases"]),
        exato=melhor in exatos,
    )


async def anuncio_para(
    session: AsyncSession, creative: MarketingCreative, rede: RedeSocial
) -> AnuncioEscolhido | str:
    """O anúncio da loja da conta para o aparelho do vídeo — ou o motivo.

    Vídeo com vários SKUs ("dg089.ci, dg088.ci" = o S5 laranja e o prata)
    vale quando TODOS caem no MESMO anúncio; se caem em anúncios diferentes,
    o vídeo mostra aparelhos diferentes e não há UM produto certo.
    """
    if rede.integration_id is None:
        return "conta_sem_loja"
    skus = skus_do_criativo(creative.sku)
    if not skus:
        return SEM_SKU
    if any(_RE_FAIXA.search(s) for s in skus):
        return SKU_AMBIGUO
    achados: list[AnuncioEscolhido] = []
    motivos: list[str] = []
    for sku in skus:
        r = await _melhor_item(session, rede.integration_id, sku)
        if isinstance(r, str):
            motivos.append(r)
        else:
            achados.append(r)
    if not achados:
        return max(motivos, key=lambda m: _PESO.get(m, 0)) if motivos else SEM_ANUNCIO
    if len({a.item_id for a in achados}) > 1:
        return SKU_AMBIGUO
    escolhido = achados[0]
    escolhido.skus = [s for a in achados for s in a.skus]
    escolhido.exato = any(a.exato for a in achados)
    return escolhido


# ─────────────────────────────────────────────────────────── o arquivo


@dataclass(slots=True, frozen=True)
class InfoVideo:
    largura: int
    altura: int
    duracao: float | None
    codec: str | None


# Cache por (caminho, tamanho, mtime): o modal lista as contas a cada clique e
# a rodada do robô revê os mesmos vídeos de hora em hora. O arquivo do
# criativo não muda depois de subir; se mudar, o mtime muda e a chave também.
_CACHE: OrderedDict[tuple[str, int, int], InfoVideo | None] = OrderedDict()
_CACHE_MAX = 512
TIMEOUT_FFPROBE_S = 20


async def _ffprobe(caminho: Path) -> InfoVideo | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name:format=duration",
            "-of",
            "json",
            str(caminho),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        saida, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_FFPROBE_S)
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    try:
        info = json.loads(saida.decode("utf-8", "replace"))
        trilha = (info.get("streams") or [{}])[0]
        dur = (info.get("format") or {}).get("duration")
        return InfoVideo(
            largura=int(trilha["width"]),
            altura=int(trilha["height"]),
            duracao=float(dur) if dur not in (None, "N/A") else None,
            codec=str(trilha.get("codec_name") or "").lower() or None,
        )
    except (ValueError, KeyError, TypeError, IndexError):
        return None


async def sondar_video(caminho: Path) -> InfoVideo | None:
    """Largura, altura, duração e codec do vídeo (ffprobe), com cache."""
    try:
        st = caminho.stat()
    except OSError:
        return None
    chave = (str(caminho), st.st_size, int(st.st_mtime))
    if chave in _CACHE:
        _CACHE.move_to_end(chave)
        return _CACHE[chave]
    info = await _ffprobe(caminho)
    _CACHE[chave] = info
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return info


def motivo_do_formato(info: InfoVideo | None, tamanho: int | None = None) -> str | None:
    if info is None:
        return ILEGIVEL
    if tamanho is not None and tamanho > TAMANHO_MAX:
        return GRANDE_DEMAIS
    if info.duracao is None or info.duracao <= DURACAO_MIN_S or info.duracao > DURACAO_MAX_S:
        return FORA_DA_DURACAO
    if min(info.largura, info.altura) < LADO_MENOR_MIN:
        return RESOLUCAO_BAIXA
    if (info.codec or "") not in CODECS_ACEITOS:
        return FORMATO
    return None


def caminho_do_arquivo(file: MarketingCreativeFile) -> Path:
    return Path(get_settings().uploads_dir) / (file.file_rel or "")


async def motivo_do_video_na_loja(
    session: AsyncSession,
    creative: MarketingCreative,
    file: MarketingCreativeFile,
    rede: RedeSocial,
    *,
    excluir_id: UUID | None = None,
    com_formato: bool = True,
) -> str | None:
    """Tudo o que impede ESTE vídeo de sair NESTA conta de Shopee Vídeo.

    Barato primeiro: o banco (outra loja, anúncio), depois o ffprobe.
    `com_formato=False` pula o ffprobe (a tela da fila, que lista tudo).
    """
    if (rede.plataforma or "").strip().lower() != PLATAFORMA_SHOPEE:
        return None
    # Rodízio: o mesmo vídeo em DUAS lojas da marca é conteúdo repetido aos
    # olhos da Shopee (rotina v2, regra 2: "uma loja por vídeo"). Conta o que
    # está em voo, no ar ou em dúvida — mesmo conjunto do teto diário.
    from app.services.marketing.postagens import STATUS_OCUPA_CONTA

    mesmo_video = MarketingPostagem.file_id == file.id
    if file.sha256:
        mesmo_video = or_(
            mesmo_video,
            MarketingPostagem.file_id.in_(
                select(MarketingCreativeFile.id).where(MarketingCreativeFile.sha256 == file.sha256)
            ),
        )
    q = (
        select(func.count())
        .select_from(MarketingPostagem)
        .where(
            mesmo_video,
            MarketingPostagem.plataforma == PLATAFORMA_SHOPEE,
            MarketingPostagem.rede_social_id.isnot(None),
            MarketingPostagem.rede_social_id != rede.id,
            MarketingPostagem.status.in_(STATUS_OCUPA_CONTA),
        )
    )
    if excluir_id is not None:
        q = q.where(MarketingPostagem.id != excluir_id)
    if (await session.execute(q)).scalar_one():
        return EM_OUTRA_LOJA

    r = await anuncio_para(session, creative, rede)
    if isinstance(r, str):
        return r
    if not com_formato:
        return None
    caminho = caminho_do_arquivo(file)
    try:
        tamanho = caminho.stat().st_size
    except OSError:
        tamanho = None
    return motivo_do_formato(await sondar_video(caminho), tamanho)


# ────────────────────────────────────────────── conferência ao vivo (loja)


@dataclass(slots=True)
class Conferencia:
    ok: bool
    motivo: str | None = None
    status: str | None = None
    estoque: int | None = None


async def conferir_ao_vivo(
    session: AsyncSession, integration_id: UUID, item_id: int
) -> Conferencia:
    """O anúncio está NORMAL e com estoque AGORA? Pergunta à loja.

    Pelo client da INTEGRAÇÃO (app da loja, só leitura: `get_item_base_info`
    e `get_model_list`, que já existem e já são usados pelo Flex e pelo
    atendimento), montado pelo `cliente_da_integracao` — que renova o token
    da loja sob a trava dela, sem mudar nada do fluxo da integração. O token
    de VÍDEO não serve pra isto (e nem tem permissão de produto).

    Levanta quando não consegue perguntar (a loja fora do ar, token da loja
    com problema): quem chama trata como "tenta de novo", nunca como "ok".
    """
    from app.models import Integration
    from app.services.atendimento.clientes import cliente_da_integracao

    integ = await session.get(Integration, integration_id)
    if integ is None:
        return Conferencia(ok=False, motivo="a loja ligada à conta não existe mais")
    cliente = await cliente_da_integracao(integ)
    itens = await cliente.get_item_base_info([item_id])
    item = next((x for x in itens if str(x.get("item_id")) == str(item_id)), None)
    if item is None:
        return Conferencia(ok=False, motivo=f"anúncio {item_id} não encontrado na loja")
    status = str(item.get("item_status") or "").upper() or None
    if status != "NORMAL":
        return Conferencia(
            ok=False,
            status=status,
            motivo=f"anúncio {item_id} está {status or 'sem status'} na Shopee",
        )
    estoque: int | None = None
    resumo = (item.get("stock_info_v2") or {}).get("summary_info") or {}
    if resumo.get("total_available_stock") is not None:
        estoque = int(resumo.get("total_available_stock") or 0)
    if item.get("has_model") or estoque is None:
        try:
            modelos = (await cliente.get_model_list(item_id)).get("model") or []
        except Exception:  # noqa: BLE001 — sem a lista de variações, fica o resumo
            modelos = []
        if modelos:
            soma = 0
            for m in modelos:
                r = (m.get("stock_info_v2") or {}).get("summary_info") or {}
                soma += max(0, int(r.get("total_available_stock") or 0))
            estoque = soma
    if estoque is None:
        return Conferencia(
            ok=False, status=status, motivo="a Shopee não informou o estoque do anúncio"
        )
    if estoque <= 0:
        return Conferencia(
            ok=False,
            status=status,
            estoque=0,
            motivo=f"anúncio {item_id} está sem estoque na Shopee",
        )
    return Conferencia(ok=True, status=status, estoque=estoque)
