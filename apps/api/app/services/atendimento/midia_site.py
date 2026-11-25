"""A foto e o vídeo que o cliente manda pelo SAC do SITE, MOSTRADOS na conversa (25/11/2026).

O pedido do Eduardo (25/11, prints do chamado US-26-0006 da Uranyx): "isso o
site da uranyx". A equipe tinha perguntado "eu consigo ver as fotos e vídeos
desse protocolo pelo atendimento?" e depois: "pra mim o vídeo não está
carregando, quando pego direto do tuta; as fotos consegui ver, mas o vídeo deu
erro".

COMO O AVISO CHEGA (conferido em 25/11/2026, só leitura, nos 4 avisos de chamado
com arquivo que há em produção). O site é OUTRO projeto (o PHP da Uranyx e da
Charlots, na Hostinger; `Sac::planejarAnexos`): o aviso à equipe sai pelo Brevo
com os arquivos ANEXADOS de verdade — parte MIME `attachment` (foto JPG/PNG, PDF,
vídeo MP4/MOV), até 2,9 MB cada e 11 MB no e-mail — e, para cada arquivo, um
link assinado do site (`/sac/arquivo/v1.<token>`, 180 dias) que o Brevo troca
pelo link de RASTREIO de clique dele (`*.sendibt2.com/tr/cl/…`). O Tuta guarda
só o HTML (o texto que a ponte lê vem dele: "IMG_8653.jpg · 262 KBanexado ·
Abrir"); a Central guarda os anexos cifrados (`mail_attachments`). Os 22
arquivos dos 4 avisos vieram anexados (18 JPG, 3 MP4, 1 MOV). Arquivo acima do
teto vai SÓ pelo link (nenhum até hoje): o cartão diz qual
(`arquivos_so_pelo_link`) e a tela manda abrir pelo Tuta.

POR QUE O VÍDEO DAVA ERRO. O do US-26-0006 NÃO é HEVC: é H.264 (Baseline,
480×360, girado 90°) + AAC num contêiner QuickTime do iPhone (`.MOV`, marca
`qt  `), com uma faixa a mais de áudio espacial da Apple (`apac`, 4 canais, que
o ffmpeg nem decodifica) e 5 faixas de metadados (`mebx`). Dentro de um
`<video>` numa página, o Chromium toca esse arquivo; o que falha é ABRIR o link
direto numa aba (o "Abrir"/"Assistir ao vídeo do defeito" do e-mail, pelo
Tuta): a rota do site responde `Content-Type: video/quicktime`, um tipo que o
Chrome não abre sozinho (`canPlayType("video/quicktime")` vazio — vira
download, não player), e com a CSP `sandbox` (sem `allow-same-origin`) o
player da aba abre vazio. A foto (`image/jpeg`) abre na aba, por isso "as
fotos consegui ver". Conferido no Chromium 156 do app com o arquivo real
servido com os mesmos cabeçalhos do site; o Range da rota do site está certo
(206). O conserto do link é no site (relatado, não mexido daqui).

O QUE FAZ AQUI (o padrão da foto da Amazon, `anexo_amazon`: cópia guardada e
rota autenticada no escopo da conversa, nunca link público):
- SÓ na conversa do chamado do site (`conversa_aceita`: plataforma `site`, a
  conversa da ponte). A rota (`GET /api/atendimento/email/conversas/{id}/
  midias/{anexo}`) exige o anexo num e-mail DESTA conversa, com as travas dos
  cartões (escopo da equipe, lixeira, quem só vê a aba nas lojas dele).
- O tipo vale pela ASSINATURA dos bytes, nunca pelo que o e-mail declara
  (`especie_de_verdade`); o declarado só decide o que o cartão OFERECE como
  miniatura (`especie_declarada`). HEIC, PDF, o resto: continuam só para baixar.
- FOTO (JPEG/PNG/GIF/WebP): os bytes da Central, `inline`.
- VÍDEO (MP4/MOV/3GP): convertido UMA vez para MP4 H.264 Main + AAC com
  `faststart` — a MESMA conversão do envio (`video.argumentos_ffmpeg`: só a
  primeira faixa de vídeo e a primeira de áudio; a de áudio espacial e as de
  metadados ficam de fora) — e a CAPA (um quadro em JPEG, a miniatura), no
  disco: `uploads_dir/atendimento/site/<2>/<id do anexo>.mp4|.jpg`. O nome é o
  id do anexo (nada do cliente no caminho; sem traversal: só UUID). A rota
  entrega com Range (o `<video>` pede pedaços). Na primeira vez a conversão
  roda numa thread, no máximo `CONVERSOES_JUNTAS` ao mesmo tempo, uma por
  anexo; se o áudio não converte, tenta sem áudio; se nem assim, marca a falha
  (`.falhou`) e não tenta de novo por `ESPERA_DEPOIS_DA_FALHA_S`.
- Cabeçalhos: `inline`, `nosniff`, a CSP sem script (`sandbox` com
  `allow-same-origin`: o vídeo aberto direto numa aba toca; sem script nada
  roda) e `private` (nunca num cache compartilhado).
- RETENÇÃO: nenhuma por enquanto (como a Amazon; o Zap apaga em 180 dias): a
  decisão é do Eduardo. O vídeo convertido de um e-mail apagado da Central fica
  no disco, mas nenhuma rota o entrega (o anexo não existe mais).
- LOG: só ids, contagens e códigos. Nunca nome de arquivo, texto ou endereço.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

import structlog

from app.config import get_settings
from app.services.atendimento import anexo_ml, video
from app.services.atendimento.constantes import PLATAFORMA_SITE

logger = structlog.get_logger()

PASTA = "atendimento/site"
FOTO = "imagem"
VIDEO = "video"
# O que a tela mostra numa <img> (HEIC não: o Chrome não abre).
TIPOS_FOTO = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
# O que o e-mail DECLARA (só para o cartão oferecer a miniatura; a rota confere os bytes).
_DECLARADOS_FOTO = TIPOS_FOTO | {"image/jpg", "image/pjpeg"}
_DECLARADOS_VIDEO = frozenset({"video/mp4", "video/quicktime", "video/3gpp", "video/x-m4v"})
_GENERICOS = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_EXT_FOTO = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
_EXT_VIDEO = frozenset({".mp4", ".mov", ".m4v", ".3gp"})
# O vídeo que se converte: o mesmo teto do anexo da tela (o do site vem até 2,9 MB).
MAX_BYTES_VIDEO = video.MAX_BYTES_UPLOAD_VIDEO
# O teto do bitrate: para ver na tela não há plataforma a caber — o vídeo de até
# 3 min sai no bitrate máximo da conversão (`video.KBPS_VIDEO_MAX`).
TETO_PREVIA = 256 * 1024 * 1024
# A capa: um quadro do começo (pulando o preto do primeiro instante), até 640 de largura.
CAPA_EM_S = 0.5
CAPA_LARGURA_MAX = 640
TEMPO_CAPA_S = 30
CONVERSOES_JUNTAS = 2
ESPERA_DEPOIS_DA_FALHA_S = 6 * 3600
# As linhas de arquivo do aviso do site, no texto que vem do HTML: "IMG_8653.jpg ·
# 262 KBanexado · Abrir" (anexado) ou "VIDEO.MOV · 25,3 MB · Abrir" (só o link).
_RE_ARQUIVO_DO_AVISO = re.compile(
    r"^[ \t]*(?P<nome>\S[^\n]*?\.[A-Za-z0-9]{2,5})[ \t]+·[ \t]+"
    r"(?P<tamanho>\d{1,4}(?:[.,]\d{1,2})?[ \t]?(?:B|KB|MB|GB))"
    r"[ \t]*(?P<anexado>anexado)?[ \t]*·?[ \t]*Abrir[ \t]*$",
    re.MULTILINE,
)
MAX_ARQUIVOS_DO_AVISO = 30


# ═══════════════════════ as réguas (puras) ═══════════════════════════════


def conversa_aceita(conversa: Any) -> bool:
    """A conversa do CHAMADO DO SITE (a da ponte, plataforma `site`). PURA."""
    from app.services.mail_atendimento import ponte

    return getattr(conversa, "plataforma", None) == PLATAFORMA_SITE and ponte.e_conversa_da_ponte(
        conversa
    )


def _extensao(nome: Any) -> str:
    base = str(nome or "").replace("\\", "/").rsplit("/", 1)[-1]
    return PurePosixPath(base).suffix.lower()


def especie_declarada(nome: Any, tipo: Any) -> str | None:
    """`imagem` | `video` pelo que o e-mail DECLARA (o tipo; o genérico, pela extensão). PURA.

    Só decide o que o cartão oferece como miniatura — a rota confere os bytes.
    """
    declarado = str(tipo or "").split(";", 1)[0].strip().lower()
    ext = _extensao(nome)
    if declarado in _DECLARADOS_FOTO or (declarado in _GENERICOS and ext in _EXT_FOTO):
        return FOTO
    if declarado in _DECLARADOS_VIDEO or (declarado in _GENERICOS and ext in _EXT_VIDEO):
        return VIDEO
    return None


def especie_de_verdade(dados: bytes | None) -> tuple[str, str] | None:
    """(`imagem`|`video`, o mime) pela ASSINATURA dos bytes; None = nem foto nem vídeo. PURA."""
    if not dados:
        return None
    cabeca = bytes(dados[:64])
    mime = anexo_ml.tipo_pela_assinatura(cabeca)
    if mime in TIPOS_FOTO:
        return FOTO, mime
    mime_video = video.tipo_pelo_conteudo(cabeca)
    if mime_video is not None:
        return VIDEO, mime_video
    return None


def arquivos_do_aviso(texto: str | None) -> list[dict[str, Any]]:
    """Os arquivos que o aviso do site lista: nome, tamanho e se veio anexado. PURA."""
    out: list[dict[str, Any]] = []
    for m in _RE_ARQUIVO_DO_AVISO.finditer(texto or ""):
        out.append(
            {
                "nome": m.group("nome").strip()[:200],
                "tamanho": " ".join(m.group("tamanho").split()),
                "anexado": bool(m.group("anexado")),
            }
        )
        if len(out) >= MAX_ARQUIVOS_DO_AVISO:
            break
    return out


def arquivos_so_pelo_link(texto: str | None) -> list[dict[str, str]]:
    """Os arquivos do aviso que NÃO vieram anexados (grandes demais): só pelo link do site."""
    return [
        {"nome": a["nome"], "tamanho": a["tamanho"]}
        for a in arquivos_do_aviso(texto)
        if not a["anexado"]
    ]


def nome_para_mostrar(nome: Any, especie: str, *, capa: bool = False) -> str:
    """O nome do arquivo entregue: o do e-mail (sem caminho), com a extensão do que sai. PURA."""
    base = str(nome or "").replace("\\", "/").rsplit("/", 1)[-1].strip() or "anexo"
    if especie != VIDEO:
        return base[:200]
    raiz = base.rsplit(".", 1)[0] if "." in base else base
    return f"{(raiz or 'video')[:190]}{'-capa.jpg' if capa else '.mp4'}"


# ═══════════════════════ o disco ═════════════════════════════════════════


def _raiz() -> Path:
    return Path(get_settings().uploads_dir)


def caminhos(anexo_id: UUID) -> tuple[Path, Path, Path]:
    """(o MP4, a capa, a marca de falha) do vídeo deste anexo. Só UUID: sem traversal."""
    nome = str(UUID(str(anexo_id)))
    pasta = _raiz() / PASTA / nome[:2]
    return pasta / f"{nome}.mp4", pasta / f"{nome}.jpg", pasta / f"{nome}.falhou"


def video_pronto(anexo_id: UUID) -> Path | None:
    """O MP4 já convertido deste anexo (no disco), ou None."""
    mp4, _capa, _falhou = caminhos(anexo_id)
    try:
        return mp4 if mp4.is_file() and mp4.stat().st_size > 0 else None
    except OSError:
        return None


def capa_pronta(anexo_id: UUID) -> Path | None:
    _mp4, capa, _falhou = caminhos(anexo_id)
    try:
        return capa if capa.is_file() and capa.stat().st_size > 0 else None
    except OSError:
        return None


def _falhou_ha_pouco(marca: Path) -> bool:
    try:
        return time.time() - marca.stat().st_mtime < ESPERA_DEPOIS_DA_FALHA_S
    except OSError:
        return False


def _marcar_falha(marca: Path) -> None:
    try:
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.touch()
    except OSError as exc:
        logger.warning("atendimento_site_midia_marca_falhou", err=type(exc).__name__)


def _rodar(argumentos: list[str], tempo: float) -> bool:
    """Roda o ffmpeg; True = saiu com 0. Nunca levanta."""
    try:
        r = subprocess.run(  # noqa: S603 — argumentos fixos, caminhos nossos
            argumentos, capture_output=True, timeout=tempo, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("atendimento_site_midia_ffmpeg_parou", err=type(exc).__name__)
        return False
    if r.returncode != 0:
        logger.info(
            "atendimento_site_midia_ffmpeg_codigo",
            codigo=r.returncode,
            erro=(r.stderr or b"")[-300:].decode("utf-8", "replace"),
        )
    return r.returncode == 0


def argumentos_capa(entrada: str, saida: str, inicio: float) -> list[str]:
    """A linha do ffmpeg da capa: um quadro, até `CAPA_LARGURA_MAX` de largura. PURA."""
    return [
        video._programa("ffmpeg"),  # noqa: SLF001 — o mesmo ffmpeg da conversão do envio
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, inicio):.2f}",
        "-i",
        entrada,
        "-frames:v",
        "1",
        "-vf",
        f"scale='min({CAPA_LARGURA_MAX},iw)':-2",
        "-q:v",
        "5",
        saida,
    ]


def _parte(final: Path, ext: str) -> Path:
    """O arquivo em construção, na pasta do final (o `os.replace` não cruza disco)."""
    return final.parent / f".{uuid4().hex}.parte{ext}"


def _gravar_capa(mp4: Path, capa: Path, duracao: float | None) -> bool:
    """A capa a partir do MP4 já convertido. True = no disco. Síncrona; nunca levanta."""
    inicios = [CAPA_EM_S, 0.0] if (duracao or 0) > CAPA_EM_S * 3 else [0.0]
    for inicio in inicios:
        temp = _parte(capa, ".jpg")
        try:
            if _rodar(argumentos_capa(str(mp4), str(temp), inicio), TEMPO_CAPA_S) and (
                temp.is_file() and temp.stat().st_size > 0
            ):
                os.replace(temp, capa)
                return True
        except OSError as exc:
            logger.warning("atendimento_site_midia_capa_disco", err=type(exc).__name__)
            return False
        finally:
            temp.unlink(missing_ok=True)
    return False


def converter(dados: bytes, anexo_id: UUID) -> Path | None:
    """O vídeo como a tela toca (MP4 H.264 + AAC) e a capa, no disco. Síncrona; nunca levanta.

    Devolve o MP4 (a capa é o melhor esforço) ou None. O arquivo que não abre ou não
    converte ganha a marca de falha (não se tenta de novo por um tempo); disco ou
    ffmpeg que faltam não marcam (é do servidor, não do arquivo).
    """
    mp4, capa, falhou = caminhos(anexo_id)
    if not video.ffmpeg_disponivel():
        logger.error("atendimento_site_midia_sem_ffmpeg")
        return None
    if len(dados) > MAX_BYTES_VIDEO:
        _marcar_falha(falhou)
        return None
    try:
        mp4.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="atend-site-") as pasta:
            entrada = Path(pasta) / "entrada"
            entrada.write_bytes(dados)
            info = video._sondar(entrada)  # noqa: SLF001 — a mesma sonda da conversão do envio
            if info is None:
                logger.info("atendimento_site_midia_nao_abre", anexo=str(anexo_id))
                _marcar_falha(falhou)
                return None
            if not _converter_para(entrada, mp4, info):
                logger.info("atendimento_site_midia_nao_converteu", anexo=str(anexo_id))
                _marcar_falha(falhou)
                return None
    except OSError as exc:
        logger.warning("atendimento_site_midia_disco", err=type(exc).__name__)
        return None
    if not _gravar_capa(mp4, capa, info.duracao):
        logger.info("atendimento_site_midia_sem_capa", anexo=str(anexo_id))
    falhou.unlink(missing_ok=True)
    logger.info("atendimento_site_midia_convertido", anexo=str(anexo_id))
    return mp4


def _converter_para(entrada: Path, mp4: Path, info: video.InfoVideo) -> bool:
    """A conversão do envio (`video.argumentos_ffmpeg`) gravada em `mp4`. True = no disco.

    A faixa de áudio que o ffmpeg não decodifica (a espacial da Apple, quando vem
    primeiro) derruba a conversão: a segunda tentativa vai sem o áudio.
    """
    kbps = video.kbps_para_caber(info.duracao, TETO_PREVIA)
    tentativas = [info, replace(info, tem_audio=False)] if info.tem_audio else [info]
    temp = _parte(mp4, ".mp4")
    try:
        for tentativa in tentativas:
            if _rodar(
                video.argumentos_ffmpeg(str(entrada), str(temp), tentativa, kbps),
                video.TEMPO_CONVERSAO_S,
            ) and (temp.is_file() and temp.stat().st_size > 0):
                os.replace(temp, mp4)
                return True
        return False
    finally:
        temp.unlink(missing_ok=True)


# ═══════════════════════ a fila da conversão ═════════════════════════════

# O teto de conversões juntas e a trava de cada anexo (dois pedidos do mesmo vídeo
# esperam a MESMA conversão), do event loop que está rodando: em produção é um só;
# os testes abrem um por teste. A trava fica (uma por vídeo pedido, até reiniciar).
_ESTADO: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore, dict[UUID, asyncio.Lock]] | None = None


def _fila() -> tuple[asyncio.Semaphore, dict[UUID, asyncio.Lock]]:
    global _ESTADO
    loop = asyncio.get_running_loop()
    if _ESTADO is None or _ESTADO[0] is not loop:
        _ESTADO = (loop, asyncio.Semaphore(CONVERSOES_JUNTAS), {})
    return _ESTADO[1], _ESTADO[2]


async def preparar_video(anexo_id: UUID, dados: bytes) -> Path | None:
    """O MP4 do vídeo deste anexo — converte na primeira vez (numa thread). None = não deu.

    Os bytes já são de um vídeo pela assinatura (a rota confere antes).
    """
    pronto = video_pronto(anexo_id)
    if pronto is not None:
        return pronto
    _mp4, _capa, falhou = caminhos(anexo_id)
    if _falhou_ha_pouco(falhou):
        return None
    teto, travas = _fila()
    trava = travas.setdefault(anexo_id, asyncio.Lock())
    async with trava:
        pronto = video_pronto(anexo_id)
        if pronto is not None:
            return pronto
        if _falhou_ha_pouco(falhou):
            return None
        async with teto:
            return await asyncio.to_thread(converter, dados, anexo_id)


async def preparar_capa(anexo_id: UUID) -> Path | None:
    """A capa do vídeo já convertido (refeita se sumiu). None = não há."""
    pronta = capa_pronta(anexo_id)
    if pronta is not None:
        return pronta
    mp4 = video_pronto(anexo_id)
    if mp4 is None:
        return None
    _mp4, capa, _falhou = caminhos(anexo_id)
    teto, _travas = _fila()
    async with teto:
        ok = await asyncio.to_thread(_gravar_capa, mp4, capa, None)
    return capa if ok else None


# ═══════════════════════ a resposta ══════════════════════════════════════

# Sem script nenhum (sandbox sem allow-scripts); `allow-same-origin` para o vídeo
# aberto direto numa aba tocar (com o sandbox puro o player da aba abre vazio —
# conferido no Chromium do app, 25/11/2026).
CSP = "default-src 'none'; img-src 'self'; media-src 'self'; sandbox allow-same-origin"
CACHE = "private, max-age=3600"


def cabecalhos(nome: str) -> dict[str, str]:
    """Os cabeçalhos da rota: inline, nosniff, sem script, cache só do navegador."""
    from app.services.atendimento.zap_midia import content_disposition

    return {
        "Content-Disposition": content_disposition("inline", nome),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": CSP,
        "Cache-Control": CACHE,
    }
