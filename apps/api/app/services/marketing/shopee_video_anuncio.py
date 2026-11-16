"""Shopee Vídeo: o ANÚNCIO certo da loja e as guardas do vídeo.

O vídeo da Shopee sai DENTRO de uma loja e leva um anúncio dela junto
(`item_info[].item_id` — por anúncio, sem variação). Vincular errado não tem
conserto: depois de publicado, nem o produto nem a legenda se editam, e
produto "irrelevante" faz a Shopee apagar o vídeo e tirar ponto da conta.

Regras do dono (rotina v2, 06/10/2026, o pedido de 08/10 e as decisões de
16/11/2026):

  • o anúncio DEDICADO do aparelho do vídeo — nunca a vitrine de vários
    aparelhos quando o dedicado existe;
  • kit: até 16/11 nunca. Desde 16/11/2026 o vídeo do aparelho PODE ir no kit
    "aparelho + acessório" ("dg088.ci+a001.pr", "dg002.pi+a003.pi+a004.pi"):
    nas lojas de celular (ATLAS, Atv, Mega) quase todo aparelho só está
    anunciado assim, e o fone/relógio que vem junto não muda o produto do
    vídeo. O avulso ganha do kit quando os dois são igualmente dedicados.
    Kit de DOIS aparelhos, ou de aparelho + produto que não é acessório,
    continua recusado (`SO_KIT`);
  • anúncio vivo e com estoque; sem anúncio na loja = o vídeo não sai NELA
    (motivo DO VÍDEO: o robô pula pro próximo);
  • o MESMO vídeo nunca sai duas vezes na Shopee (16/11/2026): nem em outra
    loja, nem de novo na mesma, nem depois de a conta ser apagada;
  • vídeo de 3 a 60 s, 720p ou mais. O H.264 sobe como está; outro codec
    da lista fechada (HEVC do iPhone & cia.) o publicador converte pra H.264
    antes do upload — menos HDR/10 bits, que fica de fora (16/11/2026).

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
Acessório não conta como base: o kit "dg088.ci+a001.ci" carrega UM aparelho.
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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
    CODEC_SHOPEE,
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
# Desde 16/11/2026: só em kit que NÃO vale (dois aparelhos, ou aparelho +
# produto que não é acessório) — o kit "aparelho + acessório" é vinculado.
SO_KIT = "anuncio_so_em_kit"
SEM_ESTOQUE = "anuncio_sem_estoque"
SKU_AMBIGUO = "sku_ambiguo"
SEM_SKU = "criativo_sem_sku"
FORA_DA_DURACAO = "video_fora_da_duracao"
RESOLUCAO_BAIXA = "video_resolucao_baixa"
FORMATO = "video_formato_nao_aceito"
ILEGIVEL = "video_ilegivel"
GRANDE_DEMAIS = "video_grande_demais"
# O vídeo já está (ou esteve) na Shopee — qualquer conta, a mesma inclusive,
# e conta já apagada. O TEXTO do código fica o de antes de propósito: a borda
# (`_HTTP_POR_CODE` → 409) e as frases da tela casam por ele.
JA_NA_SHOPEE = "video_ja_na_shopee_em_outra_loja"
EM_OUTRA_LOJA = JA_NA_SHOPEE  # nome antigo (antes de 16/11/2026 só contava outra loja)

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
        JA_NA_SHOPEE,
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


# Acessório no SKU: "a" + dígitos (a001 fone com fio, a003 fone bluetooth,
# a004 relógio…), com o sufixo de cor/origem que vier ("a001.pr"). É o que
# as lojas de celular põem junto do aparelho no kit.
_RE_ACESSORIO = re.compile(r"a\d+")


def _partes(sku: str) -> list[str]:
    return [p.strip() for p in sku.split("+") if p.strip()]


def _eh_acessorio(parte: str) -> bool:
    return _RE_ACESSORIO.fullmatch(_base(parte)) is not None


def _bases_de_produto(sku: str) -> set[str]:
    """As bases que dizem quão "vitrine" o anúncio é: as de cada parte do SKU
    que NÃO é acessório. "dg088.ci+a001.ci" → {"dg088"} (o fone que vem junto
    não é outro aparelho); "dg088.ci+dg089.ci" → as duas.

    "a" + dígitos só é ACESSÓRIO dentro de um kit que tem um produto que não
    é acessório: sozinho ("a016.pr", o fone vendido avulso) ou num kit só de
    acessórios ("a001.pr+a003.pi"), ele É o produto do anúncio e conta como
    base. Sem isso a vitrine de fones (a016/a012/a004/a003) e o anúncio
    dedicado do a016 empatavam em "zero bases" e o vídeo do fone caía na
    vitrine, que tem mais estoque."""
    partes = _partes(sku)
    produtos = {_base(p) for p in partes if not _eh_acessorio(p)}
    return produtos or {_base(p) for p in partes}


def _aparelho_do_kit(sku_kit: str, base: str) -> str | None:
    """A parte do aparelho num kit "aparelho + acessório(s)" do aparelho
    `base` ("dg088.ci+a001.pr" → "dg088.ci"); None quando o kit não é desse
    tipo — dois aparelhos, aparelho + produto que não é acessório, ou um SKU
    de kit que não dá pra ler por partes ("kit" sem "+")."""
    partes = _partes(sku_kit)
    if len(partes) < 2:
        return None
    produtos = [p for p in partes if not _eh_acessorio(p)]
    if len(produtos) != 1 or _base(produtos[0]) != base:
        return None
    return produtos[0]


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
    # Vinculado pelo kit "aparelho + acessório" (decisão de 16/11/2026), não
    # por um avulso — fica no snapshot pra dar pra conferir depois.
    kit: bool = False

    def snapshot(self) -> dict[str, Any]:
        """O que vai pro `opcoes.shopee` da postagem: o publicador usa
        EXATAMENTE este item (e confere de novo, ao vivo, antes de postar)."""
        return {
            "item_id": self.item_id,
            "item_titulo": (self.titulo or "")[:200] or None,
            "skus": self.skus,
            "estoque_no_agendamento": self.estoque,
            "bases_no_anuncio": self.bases_no_anuncio,
            "kit": self.kit,
        }


async def _melhor_item(
    session: AsyncSession, integration_id: UUID, sku: str
) -> AnuncioEscolhido | str:
    """O anúncio da loja para UM SKU do vídeo, ou o motivo de não ter."""
    externo = func.lower(func.btrim(ProductLink.external_sku))
    kit_pedido = "+" in sku
    base = _base(sku)
    # A base do PRIMEIRO item do SKU (no kit, o aparelho vem primeiro): pega
    # o avulso "dg088.pi" e também o kit "dg088.ci+a001.pr" ou "dg088+a003".
    filtro = (
        externo == sku
        if kit_pedido
        else or_(
            externo == sku,
            func.split_part(func.split_part(externo, "+", 1), ".", 1) == base,
        )
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
    # Candidatos: o avulso do aparelho e (desde 16/11/2026) o kit "aparelho +
    # acessório" dele. Kit de dois aparelhos ou com produto que não é
    # acessório fica em `recusados`: nunca é vinculado (o vídeo mostra UM
    # aparelho), mas entra na conta do "mais específico" abaixo.
    kits_aceitos: list[tuple[Any, str]] = []
    recusados: list[Any] = []
    # O vídeo é do aparelho USADO ("dg007.us"): só o anúncio DESSE SKU serve
    # (avulso ou kit com acessório). O avulso/kit do novo da mesma base é
    # outro produto — às vezes nem o mesmo aparelho (o dg007.us é o Fire 6
    # power) — e nem entra na conta do "mais específico".
    usado_pedido = _eh_usado(sku, None)
    if kit_pedido:
        # O criativo É o kit (vídeo mostra todos os itens): só o kit exato.
        avulsos = [(i, s) for i, s, _n in linhas if s == sku]
    else:
        avulsos = [
            (i, s)
            for i, s, n in linhas
            if not _eh_kit(s) and (s == sku or (not usado_pedido and not _eh_usado(s, n)))
        ]
        for i, s, n in linhas:
            if not _eh_kit(s):
                continue
            if usado_pedido and sku not in _partes(s):
                continue
            aparelho = _aparelho_do_kit(s, base)
            # Usado é o mesmo cuidado do avulso, decidido pela PARTE DO
            # APARELHO: o kit "dg088.us+a001.pr" é de aparelho usado e só vale
            # pro vídeo do "dg088.us"; o "dg088.ci+a001.us" (o acessório é que
            # tem o sufixo) é do aparelho novo. Pelo SKU inteiro o `split(".")`
            # lia "us+a001" e "pr" — o usado passava e o novo era recusado.
            if aparelho is not None and (aparelho == sku or not _eh_usado(aparelho, n)):
                kits_aceitos.append((i, aparelho))
            else:
                recusados.append(i)
    if not avulsos and not kits_aceitos:
        return SO_KIT if recusados else SEM_ANUNCIO

    ids_avulsos = {str(i) for i, _s in avulsos}
    itens = sorted(ids_avulsos | {str(i) for i, _a in kits_aceitos})
    exatos = {str(i) for i, s in avulsos if s == sku} | {
        str(i) for i, a in kits_aceitos if a == sku
    }
    itens_kit = sorted({str(i) for i in recusados} - set(itens))
    # Todas as variações VIVAS de cada candidato (e de cada kit recusado):
    # quantos produtos (bases, sem contar acessório) o anúncio carrega e
    # quanto estoque ele tem.
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
                ProductLink.external_id.in_(itens + itens_kit),
            )
        )
    ).all()
    por_item: dict[str, dict[str, Any]] = {
        i: {"bases": set(), "estoque": 0, "titulo": None} for i in itens + itens_kit
    }
    for item, s, estoque, titulo in todas:
        d = por_item[str(item)]
        if s:
            d["bases"] |= _bases_de_produto(s)
        d["estoque"] += max(0, int(estoque or 0))
        d["titulo"] = d["titulo"] or titulo
    # O mais DEDICADO primeiro (menos aparelhos no anúncio); empatado, o
    # avulso ganha do kit, depois o SKU exato, depois o estoque. Na Barbosa o
    # S5/A18 tem o anúncio dele (58266122162, 2 bases) só em KIT, e o único
    # avulso que o carrega é o "A17 Pro Max" (vários aparelhos): o avulso ali
    # é a vitrine de OUTRO modelo — vídeo de um aparelho no anúncio de outro é
    # o "irrelevante" que a Shopee apaga (e tira ponto da conta). Por isso o
    # avulso só ganha do kit quando é tão dedicado quanto ele.
    ordem = sorted(
        itens,
        key=lambda i: (
            len(por_item[i]["bases"]) or 999,
            i not in ids_avulsos,
            i not in exatos,
            -por_item[i]["estoque"],
            i,
        ),
    )
    melhor = ordem[0]
    d = por_item[melhor]
    # O kit RECUSADO nunca é vinculado — mas ele DIZ qual é o anúncio próprio
    # do aparelho: quando ele é mais específico que o melhor candidato, o
    # candidato é vitrine de outro modelo e o vídeo não sai nesta loja.
    bases_kit = [len(por_item[i]["bases"]) for i in itens_kit if por_item[i]["bases"]]
    if bases_kit and min(bases_kit) < (len(d["bases"]) or 999):
        return SO_KIT
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
        kit=kit_pedido or melhor not in ids_avulsos,
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
    if any(m in (SO_KIT, SEM_ESTOQUE) for m in motivos):
        # Um SKU do vídeo TEM anúncio próprio nesta loja (só que em kit que
        # não vale, ou sem estoque) e outro caiu num anúncio: os SKUs não
        # convergem pro mesmo anúncio dedicado. Vincular o anúncio do outro
        # seria pôr um aparelho do vídeo no anúncio errado. (SKU sem anúncio
        # NENHUM aqui continua não pesando: é a cor que esta loja não vende.)
        return SKU_AMBIGUO
    escolhido = achados[0]
    escolhido.skus = [s for a in achados for s in a.skus]
    escolhido.exato = any(a.exato for a in achados)
    escolhido.kit = any(a.kit for a in achados)
    return escolhido


# ─────────────────────────────────────────────────────────── o arquivo


@dataclass(slots=True, frozen=True)
class InfoVideo:
    largura: int
    altura: int
    duracao: float | None
    codec: str | None
    # Opcionais (16/11/2026): o que diz se o vídeo é HDR / 10 bits. None =
    # o ffprobe não informou (ou retrato montado à mão, nos testes).
    pix_fmt: str | None = None
    color_transfer: str | None = None


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
            "stream=width,height,codec_name,pix_fmt,color_transfer:format=duration",
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
            pix_fmt=str(trilha.get("pix_fmt") or "").lower() or None,
            color_transfer=str(trilha.get("color_transfer") or "").lower() or None,
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


# HDR: HLG (o padrão do iPhone desde o 12) e PQ (HDR10/Dolby Vision).
_TRANSFER_HDR = frozenset({"arib-std-b67", "smpte2084"})
# Mais de 8 bits por canal: "yuv420p10le", "yuv444p12le", "gray10le",
# "p010le"/"p016le"… O "yuv420p"/"nv12" de sempre não casa.
_RE_PIX_FMT_PROFUNDO = re.compile(r"(?:p|gray)1[0-6](?:le|be)?$|^p[0-4]1[0-6](?:le|be)$")


def eh_hdr(info: InfoVideo) -> bool:
    """O vídeo é HDR ou de 10+ bits (pelo `pix_fmt` e pelo `color_transfer`)."""
    pf = (info.pix_fmt or "").strip().lower()
    return (info.color_transfer or "").strip().lower() in _TRANSFER_HDR or bool(
        pf and _RE_PIX_FMT_PROFUNDO.search(pf)
    )


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
    if (info.codec or "") != CODEC_SHOPEE and eh_hdr(info):
        # Só no caminho CONVERTIDO (16/11/2026): HDR/10 bits virando H.264 de
        # 8 bits sai com a cor lavada sem tone-mapping, e na Shopee o vídeo
        # não se edita depois. O H.264 sobe o original, como sempre subiu.
        return FORMATO
    return None


def caminho_do_arquivo(file: MarketingCreativeFile) -> Path:
    return Path(get_settings().uploads_dir) / (file.file_rel or "")


def ja_na_shopee(file_id: Any, creative_id: Any, sha256: Any, *, excluir_id: UUID | None = None):
    """EXISTS: o vídeo já está (ou pode estar) na Shopee — UMA regra só, a do
    robô (`autopostagem._ja_na_shopee`, por coluna: correlacionada com o
    arquivo da consulta de fora) e a da conferência por loja
    (`motivo_do_video_na_loja`, por valor).

    "Mesmo vídeo" (dono, 16/11/2026): o mesmo arquivo, o mesmo conteúdo
    (`sha256` não vazio — o mesmo MP4 subido de novo) ou o mesmo CRIATIVO (um
    criativo = um vídeo na Shopee; outro arquivo dele é outra versão do MESMO
    vídeo). Qualquer conta de Shopee: outra loja, a própria e loja já apagada
    (`rede_social_id` NULL — a postagem fica, o vídeo continua lá).

    "Já está": o estado ocupa conta (`STATUS_OCUPA_CONTA`: em voo, no ar ou em
    dúvida) OU a linha chegou ao upload (`container_id`) ou ao post
    (`post_external_id`). Uma linha `revisar` cancelada na tela guarda o
    upload e pode estar no ar; `falhou` de verdade não guarda — o `_falhou` do
    publicador zera o `container_id` justamente quando está provado que o post
    não saiu, e cancelado/falhou sem upload provam que nada subiu.
    """
    from app.services.marketing.postagens import STATUS_OCUPA_CONTA

    p = aliased(MarketingPostagem)
    usado = aliased(MarketingCreativeFile)
    mesmo_video = [p.file_id == file_id, p.creative_id == creative_id]
    if isinstance(sha256, str):
        # Por valor: hash vazio nunca vira "todos iguais".
        if sha256.strip():
            mesmo_video.append(usado.sha256 == sha256)
    elif sha256 is not None:
        mesmo_video.append(and_(sha256 != "", usado.sha256 == sha256))
    q = (
        select(p.id)
        .outerjoin(usado, usado.id == p.file_id)
        .where(
            func.lower(func.btrim(p.plataforma)) == PLATAFORMA_SHOPEE,
            or_(
                p.status.in_(STATUS_OCUPA_CONTA),
                p.container_id.isnot(None),
                p.post_external_id.isnot(None),
            ),
            or_(*mesmo_video),
        )
    )
    if excluir_id is not None:
        q = q.where(p.id != excluir_id)
    return q.exists()


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

    Barato primeiro: o banco (já na Shopee, anúncio), depois o ffprobe.
    `com_formato=False` pula o ffprobe (a tela da fila, que lista tudo).
    """
    if (rede.plataforma or "").strip().lower() != PLATAFORMA_SHOPEE:
        return None
    # O mesmo vídeo NUNCA sai duas vezes na Shopee (dono, 16/11/2026). Antes
    # (rotina v2, regra 2: "uma loja por vídeo") só contava OUTRA loja ainda
    # existente; agora conta QUALQUER postagem de Shopee deste vídeo — a
    # regra é a MESMA do sorteio do robô (`ja_na_shopee`). Só sai da conta a
    # própria linha que está sendo conferida (`excluir_id`).
    if (
        await session.execute(
            select(ja_na_shopee(file.id, creative.id, file.sha256, excluir_id=excluir_id))
        )
    ).scalar():
        return JA_NA_SHOPEE

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
        except Exception:
            # No anúncio com variação (todos os da Barbosa) o item não traz o
            # estoque: sem a lista de variações NÃO DEU PRA PERGUNTAR — um
            # soluço da API da loja (limite, rede, renovação) não pode virar
            # "sem estoque" definitivo e queimar o vídeo nesta conta. Quem
            # chama trata como "tenta no próximo tique". Com o resumo do item
            # em mãos, ele basta.
            if estoque is None:
                raise
            modelos = []
        if modelos:
            soma = 0
            for m in modelos:
                r = (m.get("stock_info_v2") or {}).get("summary_info") or {}
                soma += max(0, int(r.get("total_available_stock") or 0))
            estoque = soma
    if estoque is None:
        # Mesma coisa: a Shopee respondeu sem o número. Não é "sem estoque" —
        # é "não sei", e o publicador espera e pergunta de novo.
        raise RuntimeError("shopee_estoque_nao_informado")
    if estoque <= 0:
        return Conferencia(
            ok=False,
            status=status,
            estoque=0,
            motivo=f"anúncio {item_id} está sem estoque na Shopee",
        )
    return Conferencia(ok=True, status=status, estoque=estoque)
