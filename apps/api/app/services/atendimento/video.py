"""Vídeo na resposta ao comprador (19/11/2026).

O Eduardo: "tem q deixar enviar vídeo no chat do atendimento tbm em todas
plataformas q são possíveis hj só deixa enviar imagem sendo q no chat da
shopee por exemplo dxa enviar vídeo".

O vídeo sai pelo MESMO caminho da foto (`enviar.enviar_foto`, a rota POST
/conversas/{id}/foto): as mesmas travas do texto (quem responde, envio
ligado, loja em Observar, conversa bloqueada, simulador), a mesma linha em
voo e a mesma régua de resultado — recusa com código = não saiu ("Tentar de
novo" é a pessoa quem clica), sem resposta = AMBÍGUO (`revisar`, nunca se
reenvia sozinho). O que muda é o arquivo: conferido pelos bytes, sondado
pelo ffprobe e CONVERTIDO aqui antes de qualquer coisa sair (`preparar`).

Por plataforma (conferido em 19/11/2026; só entra o que a documentação ou
uma leitura de verdade confirmou):

  Zap (WhatsApp do número)  SIM. Doc da Meta ("Mídia" e "Mensagens de vídeo"):
                       `POST /{pnid}/media` → id → mensagem `type=video` com a
                       legenda OPCIONAL (até 1.024). MP4 ou 3GP, até 16 MB, só
                       H.264 + AAC; H.264 "High" com B-frames não toca no
                       WhatsApp do Android — por isso todo vídeo é convertido
                       para H.264 "Main" sem B-frames, AAC e `faststart`.
  Shopee (chat)        A API manda vídeo (leitura de produção: mensagem
                       `video` da loja com `source=openapi` em 10/11/2026),
                       mas a documentação do sellerchat só abre logada numa
                       conta com o app de chat — o formato do envio ainda não
                       foi lido. Fica FORA até a doc ser lida.
  TikTok (chat)        A mensagem aceita `VIDEO` com `{"vid": ...}` (doc "Send
                       Message"; a visão geral diz "até 100 MB"), mas nenhuma
                       API do atendimento sobe vídeo (só "Upload Image for
                       Buyer Messages"); o "Upload Product File" é de produto e
                       a doc não diz que o id dele serve ao chat. FORA.
  ML (pós-venda)       NÃO: o anexo da mensagem aceita JPG, PNG, PDF, HEIC e
                       GIF (doc "Mensagens pós-venda", lida em 28/10/2026).
  ML (reclamação)      NÃO: JPG, PNG ou PDF até 5 MB (doc da reclamação).
  ML (pergunta)        NÃO: só texto.
  Amazon (e-mail)      NÃO: a ajuda da Amazon aceita PDF, JPG e PNG no anexo
                       ao comprador.
  Magalu, Carrefour, Temu, AliExpress, Instagram: sem anexo pelo DaVinci.

`motivo_sem_video` é a frase que a tela mostra onde o vídeo não sai.

Nada de bytes, nome de arquivo do comprador nem URL no log. As funções que
chamam o ffmpeg são SÍNCRONAS: a rota as roda em thread.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from app.models import AtendimentoConversa
from app.services.atendimento.constantes import (
    CANAL_CHAT,
    CANAL_EMAIL,
    CANAL_POS_VENDA,
    CANAL_RECLAMACAO,
    CANAL_ZAP,
    PLATAFORMA_WHATSAPP,
)
from app.services.atendimento.foto import FotoInvalida

logger = structlog.get_logger()

# ── O que se aceita ───────────────────────────────────────────────────────
VIDEO_MP4 = "video/mp4"
VIDEO_MOV = "video/quicktime"
VIDEO_3GP = "video/3gpp"
# O tipo de VERDADE (pela assinatura) → extensão. O MOV é o do iPhone.
TIPOS_VIDEO: dict[str, str] = {VIDEO_MP4: ".mp4", VIDEO_MOV: ".mov", VIDEO_3GP: ".3gp"}
# O que a TELA pode mandar ao DaVinci (antes da conversão): um vídeo de
# celular de ~30 s em 1080p passa de 40 MB; convertido, cabe nos 16 MB do Zap.
MAX_BYTES_UPLOAD_VIDEO = 50 * 1024 * 1024
# 3 minutos (o teto do vídeo no chat da Shopee pelo app; no Zap o que manda é
# o tamanho). Acima disso a pessoa corta antes de anexar.
MAX_DURACAO_S = 180.0

# (plataforma, caixa) que aceitam vídeo pelo DaVinci — e o teto do arquivo
# convertido em cada uma.
CAIXAS_COM_VIDEO = frozenset({(PLATAFORMA_WHATSAPP, CANAL_ZAP)})
# Cloud API: vídeo até 16 MB (doc "Mídia", lida em 19/11/2026).
MAX_BYTES_PLATAFORMA: dict[str, int] = {PLATAFORMA_WHATSAPP: 16 * 1024 * 1024}

# A conversão: lado MENOR até 720 (720p, deitado ou em pé), até 30 quadros
# por segundo, H.264 Main sem B-frames (o Android do WhatsApp), AAC.
LADO_MENOR_MAX = 720
FPS_MAX = 30.0
# O teto de bitrate do vídeo (capado: o vídeo curto fica pequeno, o longo
# cabe); abaixo do piso, o vídeo não cabe legível no teto da plataforma.
KBPS_VIDEO_MAX = 2500
KBPS_VIDEO_MIN = 300
KBPS_AUDIO = 96
# Folga para o contêiner e o áudio variável (o tamanho final é conferido).
MARGEM_TAMANHO = 0.9
TEMPO_SONDA_S = 30
TEMPO_CONVERSAO_S = 120
# A api divide a máquina com o resto: a conversão não toma todos os núcleos.
THREADS_FFMPEG = "2"

# Marcas do contêiner ISO (o `ftyp`) que NÃO são vídeo: imagem (HEIC/AVIF)
# e áudio (M4A).
_MARCAS_IMAGEM = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis"})
_MARCAS_AUDIO = frozenset({b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "})


@dataclass(frozen=True)
class Video:
    """O vídeo que a pessoa anexou, já conferido (`validar_video`) — ou convertido.

    `sha256` é SEMPRE o do arquivo que a pessoa escolheu (a trava de
    "mesmo anexo há 2 minutos" compara o que ela mandou, não a conversão).
    """

    conteudo: bytes
    nome: str
    mime: str
    sha256: str
    duracao: float | None = None
    largura: int | None = None
    altura: int | None = None

    @property
    def tamanho(self) -> int:
        return len(self.conteudo)


@dataclass(frozen=True)
class InfoVideo:
    """O que o ffprobe disse do arquivo (só o que a conversão usa)."""

    duracao: float | None
    largura: int | None
    altura: int | None
    fps: float | None
    tem_audio: bool


def eh_video(dados: bytes | None) -> bool:
    """O arquivo é um vídeo da lista, pelos BYTES (não pelo nome nem pelo tipo dito)?"""
    return bool(dados) and tipo_pelo_conteudo(dados) is not None


def tipo_pelo_conteudo(dados: bytes) -> str | None:
    """MP4 / MOV / 3GP pela caixa `ftyp` do contêiner ISO; None = não é vídeo da lista."""
    if len(dados) < 12 or dados[4:8] != b"ftyp":
        return None
    marca = dados[8:12]
    if marca in _MARCAS_IMAGEM or marca in _MARCAS_AUDIO:
        return None
    if marca == b"qt  ":
        return VIDEO_MOV
    if marca[:3] in (b"3gp", b"3g2"):
        return VIDEO_3GP
    return VIDEO_MP4


_RE_NOME = re.compile(r"[^A-Za-z0-9._-]+")


def _nome_limpo(nome: str | None, extensao: str) -> str:
    """Nome curto e sem caminho/acento, com a extensão de verdade."""
    base = (nome or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _RE_NOME.sub("_", base).strip("._")[:60] or "video"
    raiz = base.rsplit(".", 1)[0] if "." in base else base
    return f"{raiz or 'video'}{extensao}"


# ── Por plataforma ────────────────────────────────────────────────────────

# Onde há anexo, mas não vídeo: o porquê de cada caixa (19/11/2026).
_SEM_VIDEO: dict[tuple[str, str], str] = {
    ("shopee", CANAL_CHAT): (
        "Shopee: o vídeo pela API do chat ainda não foi conferido na documentação "
        "oficial — por enquanto, mande o vídeo pelo Seller Center (chat da loja)."
    ),
    ("tiktok", CANAL_CHAT): (
        "TikTok: a API do chat não tem como a loja subir vídeo (só imagem). Mande o "
        "vídeo pela Central do Vendedor do TikTok."
    ),
    ("ml", CANAL_POS_VENDA): (
        "O Mercado Livre não aceita vídeo nas mensagens do pedido: só foto (JPG ou PNG) e PDF."
    ),
    ("ml", CANAL_RECLAMACAO): (
        "A reclamação do Mercado Livre não aceita vídeo: só foto (JPG ou PNG) e PDF, até 5 MB."
    ),
    ("amazon", CANAL_EMAIL): (
        "A Amazon não aceita vídeo no e-mail ao comprador: só PDF, JPG e PNG."
    ),
}


def motivo_sem_video(conversa: AtendimentoConversa) -> str | None:
    """Por que o vídeo não sai por esta conversa (e o caminho); None = sai."""
    from app.services.atendimento import foto

    sem_anexo = foto.motivo_sem_foto(conversa)
    if sem_anexo:
        return sem_anexo
    par = (conversa.plataforma, conversa.canal)
    if par in CAIXAS_COM_VIDEO:
        return None
    return _SEM_VIDEO.get(par) or (
        "Mandar vídeo por aqui ainda não está liberado para esta plataforma."
    )


def teto_da_plataforma(conversa: AtendimentoConversa) -> int | None:
    """O teto do vídeo CONVERTIDO nesta conversa; None = vídeo não sai aqui."""
    if motivo_sem_video(conversa):
        return None
    return MAX_BYTES_PLATAFORMA.get(conversa.plataforma)


def legenda_permitida(conversa: AtendimentoConversa) -> bool:
    """O Zap leva a legenda (opcional, até 1.024) junto do vídeo, como da foto."""
    return (conversa.plataforma, conversa.canal) == (PLATAFORMA_WHATSAPP, CANAL_ZAP)


# ── O arquivo ─────────────────────────────────────────────────────────────


def ffmpeg_disponivel() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _programa(nome: str) -> str:
    """O caminho inteiro do ffmpeg/ffprobe (o do PATH do container)."""
    return shutil.which(nome) or nome


def _fps(valor: Any) -> float | None:
    """'30000/1001' → 29.97; '0/0' ou lixo → None."""
    try:
        num, _, den = str(valor or "").partition("/")
        n, d = float(num), float(den or 1)
    except ValueError:
        return None
    if d <= 0 or n <= 0 or not math.isfinite(n / d):
        return None
    return n / d


def _sondar(caminho: Path) -> InfoVideo | None:
    """ffprobe do arquivo; None = não é vídeo que se abra (ou não há ffprobe)."""
    try:
        r = subprocess.run(  # noqa: S603 — argumentos fixos, caminho nosso
            [
                _programa("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                str(caminho),
            ],
            capture_output=True,
            timeout=TEMPO_SONDA_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        corpo = json.loads(r.stdout or b"{}")
    except ValueError:
        return None
    fluxos = [s for s in corpo.get("streams") or [] if isinstance(s, dict)]
    videos = [s for s in fluxos if s.get("codec_type") == "video"]
    if not videos:
        return None
    v = videos[0]
    try:
        duracao = float((corpo.get("format") or {}).get("duration") or "nan")
    except (TypeError, ValueError):
        duracao = float("nan")

    def _inteiro(x: Any) -> int | None:
        try:
            n = int(x)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    return InfoVideo(
        duracao=duracao if math.isfinite(duracao) and duracao > 0 else None,
        largura=_inteiro(v.get("width")),
        altura=_inteiro(v.get("height")),
        fps=_fps(v.get("avg_frame_rate")) or _fps(v.get("r_frame_rate")),
        tem_audio=any(s.get("codec_type") == "audio" for s in fluxos),
    )


def sondar(dados: bytes) -> InfoVideo | None:
    """O ffprobe dos bytes (num arquivo temporário, apagado na saída)."""
    with tempfile.TemporaryDirectory(prefix="atend-video-") as pasta:
        caminho = Path(pasta) / "entrada"
        caminho.write_bytes(dados)
        return _sondar(caminho)


def _minutos(segundos: float) -> str:
    s = int(round(segundos))
    return f"{s // 60}:{s % 60:02d}"


def validar_video(
    dados: bytes | None, nome: str | None, mime_declarado: str | None = None
) -> Video:
    """Confere o vídeo anexado (bytes, tamanho, abre, duração); levanta `FotoInvalida`."""
    if not dados:
        raise FotoInvalida("video_vazio", "O arquivo do vídeo veio vazio.")
    if len(dados) > MAX_BYTES_UPLOAD_VIDEO:
        raise FotoInvalida(
            "video_grande",
            f"O vídeo passa de {MAX_BYTES_UPLOAD_VIDEO // (1024 * 1024)} MB — mande um mais curto.",
        )
    mime = tipo_pelo_conteudo(dados)
    if mime is None:
        raise FotoInvalida("video_tipo", "Só dá para mandar vídeo MP4, MOV ou 3GP.")
    if not ffmpeg_disponivel():
        logger.error("atendimento_video_sem_ffmpeg")
        raise FotoInvalida("video_indisponivel", "O DaVinci não consegue preparar vídeo agora.")
    info = sondar(dados)
    if info is None:
        raise FotoInvalida("video_invalido", "Não consegui abrir este vídeo.")
    if info.duracao is not None and info.duracao > MAX_DURACAO_S:
        raise FotoInvalida(
            "video_longo",
            f"O vídeo tem {_minutos(info.duracao)} — o máximo é "
            f"{_minutos(MAX_DURACAO_S)}. Corte o trecho que importa e anexe de novo.",
        )
    declarado = (mime_declarado or "").split(";")[0].strip().lower()
    if declarado and declarado not in TIPOS_VIDEO and declarado != "application/octet-stream":
        logger.info("atendimento_video_tipo_divergente", declarado=declarado[:40], real=mime)
    return Video(
        conteudo=bytes(dados),
        nome=_nome_limpo(nome, TIPOS_VIDEO[mime]),
        mime=mime,
        sha256=hashlib.sha256(dados).hexdigest(),
        duracao=info.duracao,
        largura=info.largura,
        altura=info.altura,
    )


def kbps_para_caber(duracao: float | None, teto_bytes: int) -> int:
    """O bitrate máximo do vídeo para o arquivo caber no teto (com o áudio e a folga)."""
    if not duracao or duracao <= 0:
        return KBPS_VIDEO_MAX
    total_kbps = teto_bytes * 8 * MARGEM_TAMANHO / duracao / 1000
    return min(KBPS_VIDEO_MAX, int(total_kbps - KBPS_AUDIO))


def argumentos_ffmpeg(entrada: str, saida: str, info: InfoVideo, kbps: int) -> list[str]:
    """A linha do ffmpeg da conversão (pura: o teste confere sem rodar)."""
    filtros = [
        # Lado menor até 720, a proporção mantida, as medidas pares (o H.264 4:2:0 exige).
        f"scale='if(gte(iw,ih),-2,min({LADO_MENOR_MAX},iw))':"
        f"'if(gte(iw,ih),min({LADO_MENOR_MAX},ih),-2)'",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ]
    if info.fps is not None and info.fps > FPS_MAX + 0.5:
        filtros.append(f"fps={int(FPS_MAX)}")
    filtros.append("format=yuv420p")
    audio = (
        ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{KBPS_AUDIO}k", "-ac", "2"]
        if info.tem_audio
        else ["-an"]
    )
    return [
        _programa("ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        THREADS_FFMPEG,
        "-i",
        entrada,
        "-map",
        "0:v:0",
        *audio,
        "-t",
        str(int(MAX_DURACAO_S)),
        "-vf",
        ",".join(filtros),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "main",
        "-bf",
        "0",
        "-crf",
        "23",
        "-maxrate",
        f"{kbps}k",
        "-bufsize",
        f"{kbps * 2}k",
        "-threads",
        THREADS_FFMPEG,
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        saida,
    ]


def preparar(video: Video, teto_bytes: int) -> Video:
    """O vídeo como a plataforma toca: MP4 H.264 Main sem B-frames + AAC, até o teto.

    Síncrona (ffmpeg). Levanta `FotoInvalida` quando não converte ou quando,
    mesmo convertido, passa do teto (a frase diz o que fazer). O `sha256`
    continua o do arquivo original.
    """
    kbps = kbps_para_caber(video.duracao, teto_bytes)
    if kbps < KBPS_VIDEO_MIN:
        raise FotoInvalida(
            "video_grande",
            f"Este vídeo é longo demais para caber em {teto_bytes // (1024 * 1024)} MB — "
            "mande um trecho mais curto.",
        )
    with tempfile.TemporaryDirectory(prefix="atend-video-") as pasta:
        entrada = Path(pasta) / f"entrada{TIPOS_VIDEO.get(video.mime, '.mp4')}"
        saida = Path(pasta) / "saida.mp4"
        entrada.write_bytes(video.conteudo)
        info = _sondar(entrada)
        if info is None:
            raise FotoInvalida("video_invalido", "Não consegui abrir este vídeo.")
        try:
            r = subprocess.run(  # noqa: S603 — argumentos fixos, caminhos nossos
                argumentos_ffmpeg(str(entrada), str(saida), info, kbps),
                capture_output=True,
                timeout=TEMPO_CONVERSAO_S,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise FotoInvalida(
                "video_conversao", "Preparar este vídeo demorou demais — mande um mais curto."
            ) from e
        except OSError as e:
            raise FotoInvalida(
                "video_indisponivel", "O DaVinci não consegue preparar vídeo agora."
            ) from e
        if r.returncode != 0 or not saida.exists() or saida.stat().st_size == 0:
            logger.warning(
                "atendimento_video_conversao_falhou",
                codigo=r.returncode,
                erro=(r.stderr or b"")[-300:].decode("utf-8", "replace"),
            )
            raise FotoInvalida("video_invalido", "Não consegui preparar este vídeo para o envio.")
        convertido = saida.read_bytes()
        final = _sondar(saida)
    if len(convertido) > teto_bytes:
        raise FotoInvalida(
            "video_grande",
            f"Mesmo reduzido, o vídeo passa de {teto_bytes // (1024 * 1024)} MB — "
            "mande um trecho mais curto.",
        )
    raiz = video.nome.rsplit(".", 1)[0] or "video"
    return Video(
        conteudo=convertido,
        nome=f"{raiz}.mp4",
        mime=VIDEO_MP4,
        sha256=video.sha256,
        duracao=(final.duracao if final else None) or video.duracao,
        largura=final.largura if final else None,
        altura=final.altura if final else None,
    )


def validar_para_a_caixa(
    conversa: AtendimentoConversa, dados: bytes | None, nome: str | None, mime: str | None
) -> Video:
    """`validar_video` + `preparar` para o teto desta conversa — síncrona (a rota roda em thread).

    Onde o vídeo não sai, recusa ANTES de abrir o arquivo (`video_nao_suportado`).
    """
    teto = teto_da_plataforma(conversa)
    if teto is None:
        raise FotoInvalida(
            "video_nao_suportado",
            motivo_sem_video(conversa) or "Vídeo não sai por esta conversa.",
        )
    return preparar(validar_video(dados, nome, mime), teto)


# ── Envio pelas lojas ─────────────────────────────────────────────────────


async def enviar_video(
    session: Any,
    conversa: AtendimentoConversa,
    integration: Any,
    cliente: Any,
    video: Video,
    legenda: str | None,
    *,
    para: str | None = None,
) -> Any:
    """Sobe o vídeo e manda a mensagem com ele nas LOJAS. Nunca levanta.

    O Zap não passa por aqui (`zap.enviar_video`, com o destino das travas).
    Hoje nenhuma loja tem o envio de vídeo confirmado (ver o topo do módulo):
    chegar aqui é um caminho que pulou `motivo_sem_video` — nada sai.
    """
    from app.services.atendimento.constantes import ResultadoEnvio

    del session, integration, cliente, video, legenda, para
    return ResultadoEnvio(ok=False, erro=f"video_nao_suportado:{conversa.plataforma}")
