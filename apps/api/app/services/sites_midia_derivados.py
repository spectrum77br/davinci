"""A marca d'água dos sites, e o cache em disco das mídias com marca (01/10/2026).

Cada foto e cada vídeo que vai para a Charlots ou para a Uranyx sai daqui já
com o logo do site queimado, PEQUENO e no canto INFERIOR DIREITO (o Marco:
a marca grande no meio atrapalhava o lojista). A marca é aplicada ANTES de a
mídia sair do DaVinci: o site nunca recebe a versão sem marca.

## A marca

Uma "pílula": o logo em branco sobre uma cápsula preta a 38%, a pílula inteira
a 85% de opacidade (fundo efetivo ≈ 32% de preto, letras ≈ 85% de branco).
Branco com sombra sumia no fundo branco das fotos de produto; a pílula se lê
no claro e no escuro. Largura: 16% do lado MENOR na Charlots e 20% na Uranyx
(logotipo comprido e fino), com piso de `min(96 px, largura // 3)`; margem de
2,5% do lado menor (mínimo 8 px) à direita e embaixo. Foto e vídeo usam a
mesma conta; a capa do vídeo sai do MP4 já marcado. Ex.: foto 1500×1500 da
Charlots → pílula de 240 px a 38 px do canto; vídeo 720×1280 da Uranyx →
144 px a 18 px.

Os PNGs (`app/assets/marcas/{charlots,uranyx}.png`) foram feitos com Pillow
11.3 a partir do logo claro de cada site (`charlots/assets/img/
logo-charlots-light.png`, `uranyx/assets/img/logo-white.png`): glifos
recortados ao conteúdo, normalizados a 1000 px de largura, em branco puro com
a alpha original; cápsula `rounded_rectangle` de raio = altura/2, preto com
alpha 38%, folga horizontal 0,42×h e vertical 0,30×h (h = altura dos
glifos); glifos compostos por cima.

## De onde vem a mídia

Do CACHE do sidecar MEGA, nunca do original: a foto é o `/thumb?lado=1600`
(JPEG ≤1600, 6-23 ms quando já está pronto) e o vídeo é o `/video` (MP4 H.264
≤1280 com faststart, que o sidecar já converteu). Nada desce do MEGA de novo e
a marca custa ~38% menos CPU do que partir do original (HEVC, 4K de iPhone).

## O que fica em disco

`<sites_midia_dir>/<site>/<k[:2]>/<k>.{mini.webp,grande.jpg,mp4,json}`, com
`k = sha1(VERSAO\\0site\\0caminho)`. `VERSAO` inclui as constantes abaixo e os
bytes do PNG do logo: trocou o logo ou uma constante, a chave muda e a
varredura apaga a versão velha (a api na 1ª requisição do processo e a cada
5 min de uso; o script de aquecimento logo no começo, antes de medir o
cache). Toda escrita vai para um `.tmp.` ao lado e termina em `os.replace`
(quem lê no meio nunca pega meio arquivo).

Teto rígido (`SITES_MIDIA_CACHE_MB`, padrão 2 GB) com descarte LRU por grupo, e
piso de disco livre (`SITES_MIDIA_DISCO_MIN_MB`, padrão 3 GB): o disco do VPS
estava 82% usado em 01/10/2026.

## Quando gera

- Foto: na hora do 1º pedido (~0,3 s), sob trava por grupo e no máximo 2 por
  processo.
- Vídeo: numa fila, UM por vez no servidor inteiro (trava de arquivo, que o
  script de aquecimento divide com a api). Enquanto isso a listagem diz
  "preparando".
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.services.mega_fotos import MegaError, sidecar_bytes, sidecar_stream
from app.services.sites_midia import item_id

logger = structlog.get_logger()

# ─────────────── a marca (o ajuste é aqui) ───────────────

ASSETS_MARCAS = Path(__file__).resolve().parent.parent / "assets" / "marcas"
# site → (PNG da pílula, largura da pílula como fração do lado MENOR). A Uranyx
# é um logotipo comprido e fino: precisa de fração maior para a letra ter a
# mesma altura.
MARCAS: dict[str, tuple[str, float]] = {
    "charlots": ("charlots.png", 0.16),
    "uranyx": ("uranyx.png", 0.20),
}
OPACIDADE = 0.85
MARGEM = 0.025  # do lado menor, à direita e embaixo
MARGEM_MIN_PX = 8
LARGURA_MIN_PX = 96  # piso, limitado a 1/3 da largura

# Saídas. `grande` é o JPEG do visor (no vídeo, a capa); `mini` é a grade.
FONTE_LADO = 1600
MINI_LADO = 480
GRANDE_QV = 4  # mjpeg -q:v (2 = melhor, 31 = pior)
MINI_WEBP_QUALIDADE = 72
MINI_JPEG_QV = 5  # só sem libwebp (ver _sufixo_mini)
VIDEO_CRF = 23
VIDEO_MAXRATE = "2500k"
VIDEO_BUFSIZE = "5000k"

TIMEOUT_FOTO_S = 60
TIMEOUT_VIDEO_S = 1800
TIMEOUT_FFPROBE_S = 30
TIMEOUT_CAPA_S = 180
# O /video do sidecar já vem ≤1280 H.264 (o de 44 s tem ~4,5 MB); passou
# disto, não é vídeo de produto: não desce para o disco do VPS.
VIDEO_FONTE_MAX_MB = 400

FALHA_VALE_S = 7 * 86400
CONFERIR_A_CADA_S = 86400
VARRER_A_CADA_S = 300
TMP_VELHO_S = 3600
MB = 1024 * 1024

_VERSAO: dict[str, str] = {}


def versao(site: str) -> str:
    """sha1 (10 hex) das constantes e do PNG: mudou, a chave do cache muda."""
    if site not in _VERSAO:
        arquivo, fator = MARCAS[site]
        constantes = (
            f"2|{arquivo}|{fator}|{OPACIDADE}|canto-inf-dir|{MARGEM}|{MARGEM_MIN_PX}"
            f"|{LARGURA_MIN_PX}|{FONTE_LADO}|{MINI_LADO}|{GRANDE_QV}"
            f"|{MINI_WEBP_QUALIDADE}|{MINI_JPEG_QV}|{VIDEO_CRF}|{VIDEO_MAXRATE}|{VIDEO_BUFSIZE}|"
        )
        dados = constantes.encode() + (ASSETS_MARCAS / arquivo).read_bytes()
        _VERSAO[site] = hashlib.sha1(dados, usedforsecurity=False).hexdigest()[:10]
    return _VERSAO[site]


def largura_logo(site: str, largura: int, altura: int) -> int:
    """Largura da pílula em px: fração do lado menor, com piso de
    `min(LARGURA_MIN_PX, largura // 3)` (mídia pequena não fica com marca
    ilegível, e a marca nunca passa de 1/3 da largura), par e ≥ 2."""
    lw = max(int(min(largura, altura) * MARCAS[site][1]), min(LARGURA_MIN_PX, largura // 3))
    return max(2, lw // 2 * 2)


def margem(largura: int, altura: int) -> int:
    """Distância da pílula às bordas direita e de baixo, em px."""
    return max(MARGEM_MIN_PX, round(min(largura, altura) * MARGEM))


# ─────────────── erros ───────────────


class ErroDeMidia(Exception):  # noqa: N818 — português, como `ErroCaixa`
    status = 404
    codigo = "midia_indisponivel"


class MidiaIndisponivel(ErroDeMidia):
    """A origem sumiu do MEGA, ou a geração falhou de vez (anotado por 7 dias)."""


class MidiaOcupada(ErroDeMidia):
    """Falha da hora: disco abaixo do piso, sidecar fora, ffmpeg estourou o tempo."""

    status = 503
    codigo = "midia_ocupada"


class MidiaPreparando(ErroDeMidia):
    codigo = "midia_preparando"


# ─────────────── o grupo de arquivos de um item ───────────────


def raiz() -> Path:
    return Path(get_settings().sites_midia_dir)


@dataclass(frozen=True)
class Grupo:
    site: str
    caminho: str
    k: str
    dir: Path

    def arq(self, sufixo: str) -> Path:
        return self.dir / f"{self.k}.{sufixo}"

    def tmp(self, sufixo: str) -> Path:
        variante, _, ext = sufixo.rpartition(".")
        return self.dir / f"{self.k}.{variante}.{os.getpid()}-{secrets.token_hex(4)}.tmp.{ext}"

    @property
    def id(self) -> str:
        return item_id(self.site, self.caminho)


def grupo(site: str, caminho: str) -> Grupo:
    k = hashlib.sha1(
        f"{versao(site)}\0{site}\0{caminho}".encode("utf-8", "surrogatepass"),
        usedforsecurity=False,
    ).hexdigest()
    return Grupo(site=site, caminho=caminho, k=k, dir=raiz() / site / k[:2])


# variante → arquivos possíveis (o 1º que existir), com o tipo MIME.
_ARQUIVOS: dict[str, tuple[tuple[str, str], ...]] = {
    "mini": (("mini.webp", "image/webp"), ("mini.jpg", "image/jpeg")),
    "grande": (("grande.jpg", "image/jpeg"),),
    "video": (("mp4", "video/mp4"),),
}


def pronto(g: Grupo, variante: str) -> tuple[Path, str, os.stat_result] | None:
    for sufixo, mime in _ARQUIVOS[variante]:
        p = g.arq(sufixo)
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > 0:
            return p, mime, st
    return None


def foto_pronta(g: Grupo) -> bool:
    return pronto(g, "mini") is not None and pronto(g, "grande") is not None


def video_pronto(g: Grupo) -> bool:
    return foto_pronta(g) and pronto(g, "video") is not None


def marcar_uso(caminho: Path, st: os.stat_result) -> None:
    """Anota o uso no atime (o LRU da varredura). Só o atime: o mtime é o ETag
    do FileResponse. No máximo 1 escrita por hora por arquivo."""
    agora = time.time()
    if agora - st.st_atime > 3600:
        with contextlib.suppress(OSError):
            os.utime(caminho, (agora, st.st_mtime))


# ─────────────── meta ───────────────


def ler_meta(g: Grupo) -> dict[str, Any] | None:
    try:
        meta = json.loads(g.arq("json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _gravar_meta(g: Grupo, meta: dict[str, Any]) -> None:
    g.dir.mkdir(parents=True, exist_ok=True)
    tmp = g.tmp("meta.json")
    try:
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, g.arq("json"))
    finally:
        tmp.unlink(missing_ok=True)


def _meta_nova(g: Grupo, **campos: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "versao": versao(g.site),
        "fonte": None,
        "conferido_em": int(time.time()),
        "largura": None,
        "altura": None,
        "duracao": None,
        "falhou_em": None,
    }
    meta.update(campos)
    return meta


def falhou_recente(meta: dict[str, Any] | None, agora: float | None = None) -> bool:
    if not meta or not meta.get("falhou_em"):
        return False
    agora = time.time() if agora is None else agora
    return agora - float(meta["falhou_em"]) < FALHA_VALE_S


def _marcar_falha(g: Grupo, motivo: str) -> None:
    logger.warning("sites_midia_falhou", site=g.site, id=g.id, motivo=motivo[-300:])
    with contextlib.suppress(OSError):
        _gravar_meta(g, _meta_nova(g, falhou_em=int(time.time())))


def apagar_grupo(g: Grupo) -> int:
    """Apaga todos os arquivos `<k>.*` do grupo (menos `.tmp.` de quem gera agora)."""
    liberado = 0
    try:
        nomes = os.listdir(g.dir)
    except OSError:
        return 0
    for nome in nomes:
        if nome.startswith(g.k + ".") and ".tmp." not in nome:
            p = g.dir / nome
            with contextlib.suppress(OSError):
                liberado += p.stat().st_size
                p.unlink()
    return liberado


# ─────────────── subprocessos ───────────────


@dataclass(frozen=True)
class Saida:
    rc: int
    erro: str
    saida: bytes = b""
    estourou: bool = False

    @property
    def da_hora(self) -> bool:
        """Falha que não é do arquivo: tempo, morto, sem ffmpeg, disco cheio."""
        return self.estourou or self.rc < 0 or self.rc in (126, 127) or "No space left" in self.erro


async def _rodar(cmd: list[str], timeout: float, *, capturar: bool = False) -> Saida:
    """Roda sem shell. Estourou o tempo: kill. Guarda os últimos 600 caracteres
    do stderr para o log."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capturar else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:  # binário ausente
        return Saida(rc=127, erro=str(exc)[-600:])
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Saida(rc=-9, erro="tempo esgotado", estourou=True)
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    return Saida(
        rc=proc.returncode if proc.returncode is not None else -1,
        erro=(err or b"").decode("utf-8", "replace")[-600:],
        saida=out or b"",
    )


_LIBWEBP: bool | None = None


async def _sufixo_mini() -> str:
    """`mini.webp` com o libwebp (o ffmpeg do Debian tem, e o Dockerfile confere
    no build). O ffmpeg do Homebrew não tem: aí a miniatura sai em JPEG, para
    o mesmo código rodar nos testes do Mac."""
    global _LIBWEBP
    if _LIBWEBP is None:
        s = await _rodar(["ffmpeg", "-hide_banner", "-encoders"], 30, capturar=True)
        _LIBWEBP = s.rc == 0 and b"libwebp" in s.saida
        if not _LIBWEBP:
            logger.warning("sites_midia_sem_libwebp", rc=s.rc)
    return "mini.webp" if _LIBWEBP else "mini.jpg"


# Os comandos ficam com os argumentos agrupados por opção (fora do ruff
# format, que poria um por linha): é assim que se confere com a SPEC.
# fmt: off
def _saida_mini(sufixo: str, destino: Path) -> list[str]:
    if sufixo == "mini.webp":
        return ["-c:v", "libwebp", "-quality", str(MINI_WEBP_QUALIDADE),
                "-compression_level", "4", "-f", "webp", str(destino)]
    return ["-c:v", "mjpeg", "-q:v", str(MINI_JPEG_QV), "-f", "image2", "-update", "1",
            str(destino)]


def _filtro_mini(sufixo: str) -> str:
    fmt = "yuv420p" if sufixo == "mini.webp" else "yuvj420p"
    return (
        f"scale=w='if(gte(iw,ih),min({MINI_LADO},iw),-2)'"
        f":h='if(gte(iw,ih),-2,min({MINI_LADO},ih))',format={fmt}"
    )


def _filtro_marca(lw: int) -> str:
    return f"[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={OPACIDADE}[m]"


def _overlay(mg: int) -> str:
    """Canto inferior direito, `mg` px das bordas."""
    return f"overlay=x=W-w-{mg}:y=H-h-{mg}:format=auto"


def cmd_foto(
    fonte: Path, marca: Path, lw: int, mg: int, grande: Path, mini: Path, sufixo_mini: str
) -> list[str]:
    """Uma passada, duas saídas: `grande` (JPEG no tamanho da prévia) e `mini`."""
    filtro = (
        f"{_filtro_marca(lw)};"
        f"[0:v][m]{_overlay(mg)},split=2[g][t];"
        f"[g]format=yuvj420p[g2];"
        f"[t]{_filtro_mini(sufixo_mini)}[t2]"
    )
    return [
        "nice", "-n", "19",
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "1",
        "-i", str(fonte), "-i", str(marca),
        "-filter_complex", filtro,
        "-map", "[g2]", "-frames:v", "1", "-c:v", "mjpeg", "-q:v", str(GRANDE_QV),
        "-f", "image2", "-update", "1", str(grande),
        "-map", "[t2]", "-frames:v", "1", *_saida_mini(sufixo_mini, mini),
    ]


def cmd_video(fonte: Path, marca: Path, lw: int, mg: int, destino: Path) -> list[str]:
    filtro = f"{_filtro_marca(lw)};[0:v:0][m]{_overlay(mg)},format=yuv420p[v]"
    return [
        "nice", "-n", "19",
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(fonte), "-i", str(marca),
        "-filter_complex", filtro,
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(VIDEO_CRF),
        "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE, "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", "-max_muxing_queue_size", "1024",
        "-threads", "2", "-f", "mp4", str(destino),
    ]


def cmd_capas(mp4: Path, inicio: str, grande: Path, mini: Path, sufixo_mini: str) -> list[str]:
    """As capas saem do MP4 que JÁ tem a marca: não se aplica a marca de novo."""
    filtro = f"[0:v:0]split=2[g][t];[g]format=yuvj420p[g2];[t]{_filtro_mini(sufixo_mini)}[t2]"
    return [
        "nice", "-n", "19",
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", inicio, "-i", str(mp4),
        "-filter_complex", filtro,
        "-map", "[g2]", "-frames:v", "1", "-c:v", "mjpeg", "-q:v", str(GRANDE_QV),
        "-f", "image2", "-update", "1", str(grande),
        "-map", "[t2]", "-frames:v", "1", *_saida_mini(sufixo_mini, mini),
    ]


async def ffprobe(arquivo: Path) -> tuple[int, int, float | None] | None:
    """(largura, altura, duração) da 1ª trilha de vídeo, ou None."""
    s = await _rodar(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(arquivo)],
        TIMEOUT_FFPROBE_S,
        capturar=True,
    )
    if s.rc != 0:
        return None
    try:
        info = json.loads(s.saida.decode("utf-8", "replace"))
        trilha = (info.get("streams") or [{}])[0]
        w, h = int(trilha["width"]), int(trilha["height"])
        dur = info.get("format", {}).get("duration")
        return w, h, (round(float(dur), 2) if dur not in (None, "N/A") else None)
    except (ValueError, KeyError, TypeError, IndexError):
        return None
# fmt: on


_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def dimensoes_jpeg(dados: bytes) -> tuple[int, int] | None:
    """(largura, altura) pelo marcador SOF do JPEG, sem decodificar nada."""
    if dados[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(dados)
    while i + 4 <= n:
        if dados[i] != 0xFF:
            return None
        marcador = dados[i + 1]
        if marcador == 0xFF:  # byte de preenchimento
            i += 1
            continue
        if marcador == 0x01 or 0xD0 <= marcador <= 0xD8:  # sem tamanho
            i += 2
            continue
        if marcador == 0xDA:  # começou a imagem e não houve SOF
            return None
        tamanho = int.from_bytes(dados[i + 2 : i + 4], "big")
        if marcador in _SOF:
            if i + 9 > n:
                return None
            altura = int.from_bytes(dados[i + 5 : i + 7], "big")
            largura = int.from_bytes(dados[i + 7 : i + 9], "big")
            return (largura, altura) if largura and altura else None
        if tamanho < 2:
            return None
        i += 2 + tamanho
    return None


# ─────────────── disco ───────────────


def _checar_disco() -> None:
    r = raiz()
    try:
        r.mkdir(parents=True, exist_ok=True)
        livre = shutil.disk_usage(r).free
    except OSError as exc:
        logger.warning("sites_midia_disco_erro", err=str(exc)[:200])
        raise MidiaOcupada from exc
    piso = get_settings().sites_midia_disco_min_mb * MB
    if livre < piso:
        logger.warning("sites_midia_disco_baixo", livre_mb=livre // MB, piso_mb=piso // MB)
        raise MidiaOcupada


def _limpar(*arquivos: Path) -> None:
    for a in arquivos:
        with contextlib.suppress(OSError):
            a.unlink(missing_ok=True)


async def _impressao(caminho: str) -> str | None:
    """sha1 da prévia de 320 do sidecar: a impressão digital para conferir se
    o arquivo foi trocado no MEGA com o mesmo nome."""
    try:
        dados = await sidecar_bytes(
            "/thumb", params={"path": caminho, "lado": 320, "preparar": 0}, timeout=60
        )
    except MegaError:
        return None
    return hashlib.sha1(dados, usedforsecurity=False).hexdigest()


def _erro_do_sidecar(g: Grupo, exc: MegaError) -> ErroDeMidia:
    if exc.status_code == 404:
        return MidiaIndisponivel()
    if exc.status_code == 422:  # o sidecar anotou que não abre: é do arquivo
        _marcar_falha(g, f"sidecar 422: {exc.message}")
        return MidiaIndisponivel()
    logger.warning("sites_midia_sidecar_falhou", site=g.site, id=g.id, status=exc.status_code)
    return MidiaOcupada()


def _erro_do_ffmpeg(g: Grupo, s: Saida, oque: str) -> ErroDeMidia:
    if s.da_hora:
        logger.warning(
            "sites_midia_ffmpeg_da_hora", site=g.site, id=g.id, oque=oque, rc=s.rc, err=s.erro
        )
        return MidiaOcupada()
    _marcar_falha(g, f"{oque} rc={s.rc}: {s.erro}")
    return MidiaIndisponivel()


def _cheio(*arquivos: Path) -> bool:
    try:
        return all(a.stat().st_size > 0 for a in arquivos)
    except OSError:
        return False


# ─────────────── foto ───────────────


async def _gerar_foto(g: Grupo) -> None:
    inicio = time.perf_counter()
    try:
        fonte = await sidecar_bytes(
            "/thumb",
            params={"path": g.caminho, "lado": FONTE_LADO, "preparar": 1},
            timeout=120,
        )
    except MegaError as exc:
        raise _erro_do_sidecar(g, exc) from None
    sufixo_mini = await _sufixo_mini()
    g.dir.mkdir(parents=True, exist_ok=True)
    tmp_fonte, tmp_grande, tmp_mini = g.tmp("fonte.jpg"), g.tmp("grande.jpg"), g.tmp(sufixo_mini)
    try:
        try:
            tmp_fonte.write_bytes(fonte)
        except OSError as exc:
            raise MidiaOcupada from exc
        dims = dimensoes_jpeg(fonte)
        if dims is None:
            info = await ffprobe(tmp_fonte)
            dims = info[:2] if info else None
        if dims is None:
            _marcar_falha(g, "prévia sem dimensões")
            raise MidiaIndisponivel
        largura, altura = dims
        marca = ASSETS_MARCAS / MARCAS[g.site][0]
        lw, mg = largura_logo(g.site, largura, altura), margem(largura, altura)
        s = await _rodar(
            cmd_foto(tmp_fonte, marca, lw, mg, tmp_grande, tmp_mini, sufixo_mini), TIMEOUT_FOTO_S
        )
        if s.rc != 0 or not _cheio(tmp_grande, tmp_mini):
            raise _erro_do_ffmpeg(g, s, "foto")
        bytes_ = tmp_grande.stat().st_size + tmp_mini.stat().st_size
        os.replace(tmp_mini, g.arq(sufixo_mini))
        os.replace(tmp_grande, g.arq("grande.jpg"))
    finally:
        _limpar(tmp_fonte, tmp_grande, tmp_mini)
    _gravar_meta(
        g, _meta_nova(g, fonte=await _impressao(g.caminho), largura=largura, altura=altura)
    )
    logger.info(
        "sites_midia_gerado",
        site=g.site,
        id=g.id,
        variante="foto",
        ms=round((time.perf_counter() - inicio) * 1000),
        bytes=bytes_,
    )


_TRAVAS: dict[str, asyncio.Lock] = {}
_SEM_FOTOS: asyncio.Semaphore | None = None


def _trava(k: str) -> asyncio.Lock:
    if len(_TRAVAS) > 5000:  # não cresce para sempre
        for chave in [c for c, t in _TRAVAS.items() if not t.locked()]:
            del _TRAVAS[chave]
    return _TRAVAS.setdefault(k, asyncio.Lock())


def _sem_fotos() -> asyncio.Semaphore:
    global _SEM_FOTOS
    if _SEM_FOTOS is None:
        _SEM_FOTOS = asyncio.Semaphore(2)
    return _SEM_FOTOS


async def garantir_foto(site: str, caminho: str) -> None:
    """Deixa `mini` e `grande` da foto no cache (gera se faltar)."""
    g = grupo(site, caminho)
    if foto_pronta(g):
        return
    if falhou_recente(ler_meta(g)):
        raise MidiaIndisponivel
    async with _trava(g.k):
        # Quem esperava a vez relê o disco: outro pedido pode ter gerado.
        if foto_pronta(g):
            return
        _checar_disco()
        async with _sem_fotos():
            await _gerar_foto(g)
    agendar_varredura()


_EM_CURSO: dict[str, asyncio.Task[None]] = {}


def tarefa_foto(site: str, caminho: str) -> asyncio.Task[None]:
    """A geração da foto como tarefa compartilhada: 5 pedidos juntos da mesma
    foto esperam a MESMA geração, e quem desiste (teto de 45 s) não a cancela."""
    k = grupo(site, caminho).k
    tarefa = _EM_CURSO.get(k)
    if tarefa is None or tarefa.done():
        tarefa = asyncio.create_task(garantir_foto(site, caminho))
        _EM_CURSO[k] = tarefa

        def _fim(t: asyncio.Task[None], k: str = k) -> None:
            if _EM_CURSO.get(k) is t:
                del _EM_CURSO[k]
            if not t.cancelled():
                t.exception()  # recolhida: ninguém mais pode estar esperando

        tarefa.add_done_callback(_fim)
    return tarefa


# ─────────────── vídeo ───────────────


@contextlib.asynccontextmanager
async def trava_de_video() -> AsyncIterator[None]:
    """UM vídeo por vez no servidor: `flock` em `<raiz>/.video.lock`, dividido
    entre a api e o script de aquecimento. Pedido sem bloquear e repetido a
    cada 0,5 s — cancelar a espera nunca deixa uma thread presa segurando a
    trava depois."""
    r = raiz()
    r.mkdir(parents=True, exist_ok=True)
    fd = os.open(r / ".video.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


async def _baixar_video(g: Grupo, destino: Path) -> None:
    try:
        pedacos, fechar = await sidecar_stream(
            "/video", params={"path": g.caminho}, timeout=TIMEOUT_VIDEO_S
        )
    except MegaError as exc:
        raise _erro_do_sidecar(g, exc) from None
    try:
        recebido = 0
        with open(destino, "wb") as fh:
            async for pedaco in pedacos:
                recebido += len(pedaco)
                if recebido > VIDEO_FONTE_MAX_MB * MB:
                    # O disco tinha 13 GB livres: um vídeo enorme não o enche.
                    _marcar_falha(g, f"vídeo do sidecar passou de {VIDEO_FONTE_MAX_MB} MB")
                    raise MidiaIndisponivel
                fh.write(pedaco)
    except (OSError, httpx.HTTPError) as exc:
        logger.warning("sites_midia_download_falhou", site=g.site, id=g.id, err=str(exc)[:200])
        raise MidiaOcupada from exc
    finally:
        await fechar()


async def _gerar_video(g: Grupo) -> None:
    inicio = time.perf_counter()
    sufixo_mini = await _sufixo_mini()
    g.dir.mkdir(parents=True, exist_ok=True)
    tmp_fonte, tmp_mp4 = g.tmp("fonte.mp4"), g.tmp("video.mp4")
    tmp_grande, tmp_mini = g.tmp("grande.jpg"), g.tmp(sufixo_mini)
    try:
        await _baixar_video(g, tmp_fonte)
        info = await ffprobe(tmp_fonte)
        if info is None:
            _marcar_falha(g, "ffprobe não leu o MP4 do sidecar")
            raise MidiaIndisponivel
        largura, altura, duracao = info
        marca = ASSETS_MARCAS / MARCAS[g.site][0]
        lw, mg = largura_logo(g.site, largura, altura), margem(largura, altura)
        s = await _rodar(cmd_video(tmp_fonte, marca, lw, mg, tmp_mp4), TIMEOUT_VIDEO_S)
        if s.rc != 0 or not _cheio(tmp_mp4):
            raise _erro_do_ffmpeg(g, s, "vídeo")
        _limpar(tmp_fonte)  # o original do sidecar não é mais preciso
        # ~1 s para dentro (o quadro 0 costuma ser preto ou um fade); vídeo
        # mais curto que isso não tem quadro em 1 s, aí vale o quadro 0.
        for ss in ("1", "0"):
            _limpar(tmp_grande, tmp_mini)
            s = await _rodar(
                cmd_capas(tmp_mp4, ss, tmp_grande, tmp_mini, sufixo_mini), TIMEOUT_CAPA_S
            )
            if s.da_hora:
                raise _erro_do_ffmpeg(g, s, "capa")
            if s.rc == 0 and _cheio(tmp_grande, tmp_mini):
                break
        else:
            raise _erro_do_ffmpeg(g, Saida(rc=s.rc or 1, erro=s.erro), "capa")
        bytes_ = sum(p.stat().st_size for p in (tmp_mp4, tmp_grande, tmp_mini))
        os.replace(tmp_mini, g.arq(sufixo_mini))
        os.replace(tmp_grande, g.arq("grande.jpg"))
        os.replace(tmp_mp4, g.arq("mp4"))
    finally:
        _limpar(tmp_fonte, tmp_mp4, tmp_grande, tmp_mini)
    _gravar_meta(
        g,
        _meta_nova(
            g,
            fonte=await _impressao(g.caminho),
            largura=largura,
            altura=altura,
            duracao=duracao,
        ),
    )
    logger.info(
        "sites_midia_gerado",
        site=g.site,
        id=g.id,
        variante="video",
        ms=round((time.perf_counter() - inicio) * 1000),
        bytes=bytes_,
    )


async def gerar_video(site: str, caminho: str) -> None:
    """Deixa `mp4`, `mini` e `grande` do vídeo no cache, sob a trava de arquivo."""
    g = grupo(site, caminho)
    if video_pronto(g):
        return
    if falhou_recente(ler_meta(g)):
        raise MidiaIndisponivel
    _checar_disco()
    async with trava_de_video():
        # Depois de pegar a vez: outro processo pode ter gerado enquanto isso.
        if video_pronto(g):
            return
        await _gerar_video(g)
    agendar_varredura()


_FILA: asyncio.Queue[tuple[str, str, str]] | None = None
_NA_FILA: set[str] = set()
_CONSUMIDOR: asyncio.Task[None] | None = None


async def _consumir_fila() -> None:
    assert _FILA is not None
    while True:
        site, caminho, k = await _FILA.get()
        try:
            await gerar_video(site, caminho)
        except ErroDeMidia as exc:
            logger.info("sites_midia_video_nao_gerado", site=site, motivo=exc.codigo)
        except Exception:  # noqa: BLE001 — a fila não pode morrer por um vídeo
            logger.exception("sites_midia_video_erro", site=site)
        finally:
            # Falha da hora: sai do conjunto, e a próxima listagem põe de volta.
            _NA_FILA.discard(k)
            _FILA.task_done()


def enfileirar_video(site: str, caminho: str) -> bool:
    """Põe o vídeo na fila (uma vez só). A tarefa consumidora nasce no 1º uso."""
    global _FILA, _CONSUMIDOR
    k = grupo(site, caminho).k
    if k in _NA_FILA:
        return False
    if _FILA is None:
        _FILA = asyncio.Queue()
    _NA_FILA.add(k)
    _FILA.put_nowait((site, caminho, k))
    if _CONSUMIDOR is None or _CONSUMIDOR.done():
        _CONSUMIDOR = asyncio.create_task(_consumir_fila())
    return True


# ─────────────── conferência de mudança ───────────────

_CONFERINDO: set[str] = set()
_TENTADA_EM: dict[str, float] = {}
_SEM_CONFERIR: asyncio.Semaphore | None = None
_TAREFAS: set[asyncio.Task[Any]] = set()


async def conferir(site: str, caminho: str) -> str:
    """O arquivo foi trocado no MEGA com o mesmo nome? Compara a prévia de 320
    do sidecar com a impressão guardada. Igual: só renova `conferido_em`.
    Diferente, ou 404 (trocado, e o vídeo novo ainda não convertido): apaga o
    grupo, que se refaz no próximo pedido ou na próxima listagem."""
    g = grupo(site, caminho)
    meta = ler_meta(g)
    if meta is None:
        return "sem_meta"
    try:
        dados = await sidecar_bytes(
            "/thumb", params={"path": caminho, "lado": 320, "preparar": 0}, timeout=60
        )
    except MegaError as exc:
        if exc.status_code == 404:
            apagar_grupo(g)
            return "sumiu"
        return "erro"
    impressao = hashlib.sha1(dados, usedforsecurity=False).hexdigest()
    if meta.get("fonte") in (None, impressao):
        meta["fonte"] = impressao
        meta["conferido_em"] = int(time.time())
        _gravar_meta(g, meta)
        return "igual"
    apagar_grupo(g)
    logger.info("sites_midia_trocado", site=site, id=g.id)
    return "trocado"


def _guardar(tarefa: asyncio.Task[Any]) -> None:
    _TAREFAS.add(tarefa)

    def _fim(t: asyncio.Task[Any]) -> None:
        _TAREFAS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("sites_midia_tarefa_erro", err=repr(t.exception())[:200])

    tarefa.add_done_callback(_fim)


def agendar_conferencia(site: str, caminho: str, meta: dict[str, Any] | None) -> bool:
    """Confere em segundo plano se `conferido_em` passou de 24 h (no máximo 2
    ao mesmo tempo, e uma tentativa por hora por grupo se o sidecar falhar)."""
    global _SEM_CONFERIR
    agora = time.time()
    if not meta or agora - float(meta.get("conferido_em") or 0) < CONFERIR_A_CADA_S:
        return False
    k = grupo(site, caminho).k
    if k in _CONFERINDO or agora - _TENTADA_EM.get(k, 0) < 3600:
        return False
    if len(_TENTADA_EM) > 5000:
        _TENTADA_EM.clear()
    _CONFERINDO.add(k)
    _TENTADA_EM[k] = agora
    if _SEM_CONFERIR is None:
        _SEM_CONFERIR = asyncio.Semaphore(2)
    sem = _SEM_CONFERIR

    async def _com_teto() -> None:
        try:
            async with sem:
                await conferir(site, caminho)
        finally:
            _CONFERINDO.discard(k)

    _guardar(asyncio.create_task(_com_teto()))
    return True


# ─────────────── varredura: teto, LRU, versão velha, tmp largado ───────────────

_ULTIMA_VARREDURA = 0.0
_VARRENDO: asyncio.Task[Any] | None = None


def varrer(agora: float | None = None, *, simular: bool = False) -> dict[str, int]:
    """Soma o cache, apaga a versão velha, o grupo órfão e o tmp largado, e,
    passando do teto, apaga grupos inteiros pelo uso mais antigo até ficar
    abaixo de 85%.

    `simular=True` só conta o que sairia, sem apagar nada (o `--dry-run` do
    aquecimento). Devolve `total` (bytes que ficam), `apagados` (arquivos) e
    `liberado` (bytes)."""
    agora = time.time() if agora is None else agora
    teto = get_settings().sites_midia_cache_mb * MB
    r = raiz()
    grupos: dict[tuple[str, str], list[tuple[Path, os.stat_result]]] = {}
    total = apagados = liberado = 0

    def _sai(arq: Path, tamanho: int) -> bool:
        nonlocal apagados, liberado
        if not simular:
            try:
                arq.unlink()
            except OSError:  # já saiu (outro processo varrendo) ou sem permissão
                return False
        apagados += 1
        liberado += tamanho
        return True

    def _apagar(arquivos: list[tuple[Path, os.stat_result]]) -> int:
        return sum(st.st_size for arq, st in arquivos if _sai(arq, st.st_size))

    for site in MARCAS:
        base = r / site
        if not base.is_dir():
            continue
        for sub in base.iterdir():
            if not sub.is_dir():
                continue
            for arq in sub.iterdir():
                try:
                    st = arq.stat()
                except OSError:
                    continue
                if ".tmp." in arq.name:
                    if agora - st.st_mtime > TMP_VELHO_S and _sai(arq, st.st_size):
                        continue
                    total += st.st_size  # tmp novo: alguém gera agora
                    continue
                total += st.st_size
                k = arq.name.split(".", 1)[0]
                grupos.setdefault((site, k), []).append((arq, st))

    vivos: list[tuple[float, int, list[tuple[Path, os.stat_result]]]] = []
    for (site, _k), arquivos in grupos.items():
        meta_arq = next((a for a, _ in arquivos if a.name.endswith(".json")), None)
        meta = None
        if meta_arq is not None:
            with contextlib.suppress(OSError, ValueError):
                meta = json.loads(meta_arq.read_text(encoding="utf-8"))
        mais_novo = max(st.st_mtime for _, st in arquivos)
        velho = isinstance(meta, dict) and meta.get("versao") != versao(site)
        # Sem meta há mais de 1 h: geração que morreu entre o replace e o meta.
        orfao = meta is None and agora - mais_novo > TMP_VELHO_S
        if velho or orfao:
            total -= _apagar(arquivos)
            continue
        # O uso é o atime da MÍDIA (que só a rota de bytes lê). O do `.json`
        # não serve: a listagem e esta varredura o leem, e com `relatime` a
        # leitura renova o atime — todo grupo pareceria usado agora.
        uso = max(
            (st.st_atime for a, st in arquivos if not a.name.endswith(".json")),
            default=mais_novo,
        )
        vivos.append((uso, sum(st.st_size for _, st in arquivos), arquivos))

    if total > teto:
        alvo = int(teto * 0.85)
        for _uso, _tam, arquivos in sorted(vivos, key=lambda v: v[0]):
            if total <= alvo:
                break
            total -= _apagar(arquivos)
    if apagados and not simular:
        logger.info(
            "sites_midia_varrido",
            apagados=apagados,
            liberado_mb=liberado // MB,
            total_mb=total // MB,
        )
    return {"total": total, "apagados": apagados, "liberado": liberado}


def tamanho_cache() -> int:
    total = 0
    r = raiz()
    for site in MARCAS:
        for dirpath, _dirs, nomes in os.walk(r / site):
            for nome in nomes:
                with contextlib.suppress(OSError):
                    total += os.stat(os.path.join(dirpath, nome)).st_size
    return total


def agendar_varredura(*, forcar: bool = False) -> None:
    """No máximo uma varredura a cada 5 min, numa thread (o loop não espera)."""
    global _ULTIMA_VARREDURA, _VARRENDO
    agora = time.monotonic()
    if _VARRENDO is not None and not _VARRENDO.done():
        return
    if not forcar and agora - _ULTIMA_VARREDURA < VARRER_A_CADA_S:
        return
    _ULTIMA_VARREDURA = agora
    _VARRENDO = asyncio.create_task(asyncio.to_thread(varrer))
    _guardar(_VARRENDO)


_INICIADO = False


def iniciar() -> None:
    """1º uso no processo: uma varredura (apaga `.tmp.` largado há mais de 1 h
    por um container que morreu no meio de uma geração)."""
    global _INICIADO
    if not _INICIADO:
        _INICIADO = True
        agendar_varredura(forcar=True)


def reiniciar_estado() -> None:
    """Zera o estado do processo (testes)."""
    global _LIBWEBP, _SEM_FOTOS, _FILA, _CONSUMIDOR, _SEM_CONFERIR, _VARRENDO
    global _ULTIMA_VARREDURA, _INICIADO
    if _CONSUMIDOR is not None and not _CONSUMIDOR.done():
        _CONSUMIDOR.cancel()
    _VERSAO.clear()
    _TRAVAS.clear()
    _EM_CURSO.clear()
    _NA_FILA.clear()
    _CONFERINDO.clear()
    _TENTADA_EM.clear()
    _LIBWEBP = _SEM_FOTOS = _FILA = _CONSUMIDOR = _SEM_CONFERIR = _VARRENDO = None
    _ULTIMA_VARREDURA = 0.0
    _INICIADO = False
