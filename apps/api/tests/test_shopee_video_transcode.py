# ruff: noqa: F811 — fixtures importadas do test_shopee_video_postagem
"""Shopee Vídeo: o HEVC vira H.264 logo antes do upload (Eduardo, 16/11/2026).

21 dos 139 vídeos aprovados das lojas de celular (ATLAS, Atv, Mega) eram HEVC
e o robô os pulava ("video_formato_nao_aceito"). Agora:

  • a ELEGIBILIDADE aceita HEVC (e os outros codecs de vídeo da lista
    fechada); codec fora dela segue de fora;
  • o PUBLICADOR converte só o que não é H.264 — o H.264 sobe o arquivo
    original, sem ffmpeg, exatamente como antes;
  • o convertido mora num caminho fixo pelo id do arquivo: o tique seguinte
    o reaproveita em vez de converter de novo; ele sai quando a postagem
    termina (publicada, falhou, revisar);
  • erro do ffmpeg, ffmpeg ausente, tempo esgotado ou convertido fora da
    regra = a linha FALHA com a frase, sem subir nada — nunca fica pendurada.

O ffmpeg é de mentira (`_rodar` trocado), menos no último teste, que roda o
de verdade num vídeo de 4 s e é pulado se a máquina não tem ffmpeg/libx265.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.marketing import postagens as svc
from app.services.marketing import shopee_video_anuncio as anuncio
from app.services.marketing import shopee_video_publicador as pub
from app.services.marketing.shopee_video import ShopeeVideoRedeError
from tests.test_shopee_video_postagem import (  # noqa: F401 — fixtures
    _cenario_barbosa,
    _conta_shopee,
    _criativo,
    _limpa,
    _marca,
    _relida,
    _tique,
    _uploads,
    _video_bom,
    shopee,
)

# A sonda de verdade (o `_video_bom` a troca por um retrato fixo).
_SONDAR_REAL = anuncio.sondar_video
_SONDAR_REAL_FFPROBE = anuncio._ffprobe

pytestmark = pytest.mark.asyncio

HEVC = anuncio.InfoVideo(1080, 1920, 24.2, "hevc")
H264 = anuncio.InfoVideo(1080, 1920, 24.2, "h264")


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch) -> Path:
    p = tmp_path / "h264"
    monkeypatch.setattr(pub, "PASTA_CONVERTIDOS", p)
    return p


GB = 1024**3


@pytest.fixture(autouse=True)
def disco(monkeypatch) -> dict:
    """O disco livre que a conversão vê (`shutil.disk_usage`): folgado por
    padrão; cada teste do piso troca `livre`."""
    estado = {"livre": 500 * GB, "medidos": []}

    def uso(caminho):
        estado["medidos"].append(Path(caminho))
        return shutil._ntuple_diskusage(1000 * GB, 1000 * GB - estado["livre"], estado["livre"])

    monkeypatch.setattr(pub.shutil, "disk_usage", uso)
    return estado


@pytest.fixture(autouse=True)
def sonda(monkeypatch, _video_bom, pasta):
    """O original é HEVC; o que está na pasta de conversão é H.264."""
    retratos = {"fonte": HEVC, "convertido": H264}

    async def sondar(caminho: Path):
        return retratos["convertido" if caminho.parent == pasta else "fonte"]

    monkeypatch.setattr(anuncio, "sondar_video", sondar)
    return retratos


class FfmpegFalso:
    """No lugar do `_rodar`: anota cada comando, responde a sonda do áudio e
    'converte' escrevendo bytes no destino (o último argumento)."""

    def __init__(self) -> None:
        self.cmds: list[list[str]] = []
        self.audio = b"aac\n"
        self.rc = 0
        self.erro = b""
        self.levantar: BaseException | None = None
        self.depois: list = []

    async def __call__(self, cmd: list[str], timeout: float):
        self.cmds.append(list(cmd))
        if "ffprobe" in cmd:
            return 0, self.audio, b""
        if self.levantar is not None:
            Path(cmd[-1]).write_bytes(b"meio-arquivo")
            raise self.levantar
        Path(cmd[-1]).write_bytes(b"H264" * 100 if self.rc == 0 else b"lixo")
        for f in self.depois:
            f()
        return self.rc, b"", self.erro

    @property
    def conversoes(self) -> list[list[str]]:
        return [c for c in self.cmds if "ffmpeg" in c]


@pytest.fixture
def ffmpeg(monkeypatch) -> FfmpegFalso:
    falso = FfmpegFalso()
    monkeypatch.setattr(pub, "_rodar", falso)
    return falso


@pytest.fixture
def enviados(monkeypatch, shopee):
    """O que de fato subiu: o arquivo lido em partes e o `init` da Shopee."""
    anotado: dict = {"arquivos": [], "init": []}
    partes_reais = pub.partes_do_arquivo

    def partes(caminho, tamanho):
        anotado["arquivos"].append(Path(caminho))
        anotado["bytes"] = Path(caminho).read_bytes()
        return partes_reais(caminho, tamanho)

    monkeypatch.setattr(pub, "partes_do_arquivo", partes)
    init_real = shopee.iniciar_upload

    async def iniciar(**kw):
        anotado["init"].append(kw)
        return await init_real(**kw)

    shopee.iniciar_upload = iniciar
    return anotado


async def _publicando(db: AsyncSession, make_user, *, nome: str = "IMG_0001.MOV"):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca, nome=nome)
    (p,) = await svc.agendar(db, creative=c, file=f, redes=[rede], legenda="F110L #uranyx")
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id and lease.status == "publicando"
    return p, Path(get_settings().uploads_dir) / f.file_rel, f


# ═════════════════════════════════════════════════════════ a elegibilidade


async def test_elegibilidade_aceita_hevc_e_recusa_codec_fora_da_lista():
    assert anuncio.motivo_do_formato(HEVC, 10_000) is None
    assert anuncio.motivo_do_formato(H264, 10_000) is None
    mjpeg = anuncio.InfoVideo(1080, 1920, 24.2, "mjpeg")
    assert anuncio.motivo_do_formato(mjpeg, 10_000) == anuncio.FORMATO
    # ProRes é sempre 10/12 bits (a regra do HDR o recusaria): fica fora da
    # lista, mesmo quando o ffprobe não informa o pix_fmt.
    prores = anuncio.InfoVideo(1080, 1920, 24.2, "prores")
    assert anuncio.motivo_do_formato(prores, 10_000) == anuncio.FORMATO
    # As outras guardas não mudaram: HEVC longo demais continua de fora.
    longo = anuncio.InfoVideo(1080, 1920, 60.4, "hevc")
    assert anuncio.motivo_do_formato(longo, 10_000) == anuncio.FORA_DA_DURACAO
    assert pub.precisa_converter(HEVC) and not pub.precisa_converter(H264)
    # H.264 4:4:4 também converte (16/11/2026); 4:2:0 e "não informado" sobem direto.
    assert pub.precisa_converter(anuncio.InfoVideo(1080, 1920, 24.2, "h264", "yuv444p"))
    assert not pub.precisa_converter(anuncio.InfoVideo(1080, 1920, 24.2, "h264", "yuv420p"))
    assert not pub.precisa_converter(anuncio.InfoVideo(1080, 1920, 24.2, "h264", "yuvj420p"))


async def test_video_hevc_serve_pra_conta_shopee(db, make_user):
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca, nome="IMG_0002.MOV")
    assert await anuncio.motivo_do_video_na_loja(db, c, f, rede) is None


# ═════════════════════════════════════════════════════════ o publicador


async def test_h264_sobe_o_original_sem_ffmpeg(
    db, make_user, shopee, ffmpeg, enviados, sonda, pasta
):
    sonda["fonte"] = H264
    p, caminho, _f = await _publicando(db, make_user, nome="video.mp4")
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert ffmpeg.cmds == []
    assert enviados["arquivos"] == [caminho]
    assert enviados["init"][0]["file_name"] == "video.mp4"
    assert enviados["init"][0]["file_size"] == caminho.stat().st_size
    assert not pasta.exists() or not any(pasta.iterdir())


async def test_hevc_converte_sobe_o_h264_e_apaga_no_fim(
    db, make_user, shopee, ffmpeg, enviados, pasta
):
    p, caminho, f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    (cmd,) = ffmpeg.conversoes
    texto = " ".join(cmd)
    assert cmd[cmd.index("-i") + 1] == str(caminho)
    for trecho in (
        "-c:v libx264",
        "-preset veryfast",
        "-crf 20",
        "-pix_fmt yuv420p",
        "-movflags +faststart",
        "-c:a copy",  # o áudio já é AAC: vai copiado
    ):
        assert trecho in texto, trecho
    # Teto do que sai: lado menor até 1080 e até 30 fps (o 4K/60 cabe nos 300 s).
    filtro = cmd[cmd.index("-vf") + 1]
    assert filtro == pub.filtro_convertido()
    assert "min(iw,1080)" in filtro and "min(ih,1080)" in filtro and "-2" in filtro
    assert "fps=fps='min(source_fps,30)'" in filtro
    # Pé leve na CPU (4 vCPU divididos com a API): 1 thread no DECODIFICADOR
    # (antes do -i), 1 no filtro (escala/fps), e o x264 com 2 threads de
    # codificação e 1 de lookahead.
    i = cmd.index("-i")
    assert cmd[i - 2 : i] == ["-threads", "1"]
    assert cmd[cmd.index("-filter_threads") + 1] == "1"
    assert cmd.index("-filter_threads") < i
    saida = cmd[i:]
    assert saida[saida.index("-threads") + 1] == "2"
    assert saida[saida.index("-x264-params") + 1] == "threads=2:lookahead-threads=1"
    assert cmd.count("-threads") == 2
    convertido = pub.caminho_convertido(f.id)
    # Subiu o CONVERTIDO (nome do original, extensão do MP4), não o HEVC.
    assert enviados["arquivos"] == [convertido]
    assert enviados["bytes"] == b"H264" * 100
    assert enviados["init"][0]["file_name"] == "IMG_0001.mp4"
    assert enviados["init"][0]["file_size"] == 400
    assert enviados["init"][0]["duracao_s"] == 25
    # Publicado: o H.264 sai da pasta de trabalho.
    assert not convertido.exists()
    assert list(pasta.iterdir()) == []


async def test_audio_que_nao_e_aac_vira_aac_128k(db, make_user, shopee, ffmpeg):
    ffmpeg.audio = b"opus\n"
    p, caminho, _f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    texto = " ".join(ffmpeg.conversoes[0])
    assert "-c:a aac -b:a 128k" in texto and "-c:a copy" not in texto


async def test_conversao_que_come_o_ciclo_sobe_no_proximo_tique_sem_converter_de_novo(
    db, make_user, shopee, ffmpeg, enviados, monkeypatch, pasta
):
    relogio = [datetime.now(UTC)]
    monkeypatch.setattr(pub, "_agora", lambda: relogio[0])

    def demorou():  # a conversão "leva" 30 s do relógio da máquina
        relogio[0] += timedelta(seconds=30)

    ffmpeg.depois.append(demorou)
    p, caminho, f = await _publicando(db, make_user)
    # 40 s de orçamento: dá pra começar (≥ 20), mas depois dos 30 s da
    # conversão não sobra folga pra subir.
    assert await _tique(db, p.id, caminho, orcamento_s=40) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente"
    assert linha.attempts == 0
    assert "convertido para H.264" in (linha.result or "")
    assert "init" not in shopee.chamadas
    convertido = pub.caminho_convertido(f.id)
    assert convertido.exists()
    assert len(ffmpeg.conversoes) == 1

    # Próximo tique: a linha volta pelo lease e sobe o H.264 que já estava pronto.
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert len(ffmpeg.conversoes) == 1  # não converteu de novo
    assert enviados["arquivos"] == [convertido]
    assert shopee.chamadas.count("init") == 1
    assert not convertido.exists()


async def test_convertido_velho_ou_torto_nao_e_reaproveitado(ffmpeg, sonda, pasta, tmp_path):
    chave = uuid.uuid4()
    fonte = tmp_path / "fonte.mov"
    fonte.write_bytes(b"hevc")
    pasta.mkdir(parents=True)
    convertido = pub.caminho_convertido(chave)
    convertido.write_bytes(b"antigo")
    # Mais velho que a fonte (a fonte foi trocada): converte de novo.
    antes = time.time() - 3600
    os.utime(convertido, (antes, antes))
    await pub.convertido_para_shopee(chave, fonte)
    assert len(ffmpeg.conversoes) == 1
    assert convertido.read_bytes() == b"H264" * 100
    # Mais novo, mas fora da regra (ex.: cortado): converte de novo também.
    sonda["convertido"] = anuncio.InfoVideo(1080, 1920, 1.0, "h264")
    with pytest.raises(pub.ConversaoError, match="video_fora_da_duracao"):
        await pub.convertido_para_shopee(chave, fonte)
    assert len(ffmpeg.conversoes) == 2
    assert not convertido.exists()


async def test_varredura_roda_a_cada_visita_de_linha_mesmo_sem_conversao(
    db, make_user, shopee, ffmpeg, sonda, pasta
):
    """O H.264 largado há mais de 24 h (linha cancelada na tela, worker
    morto) sai na próxima visita de QUALQUER linha da Shopee — não só quando
    outro HEVC começa a converter."""
    pasta.mkdir(parents=True)
    esquecido = pasta / f"{uuid.uuid4()}.mp4"
    esquecido.write_bytes(b"x")
    velho = time.time() - pub.VALIDADE_CONVERTIDO.total_seconds() - 60
    os.utime(esquecido, (velho, velho))
    recente = pasta / f"{uuid.uuid4()}.mp4"
    recente.write_bytes(b"y")
    sonda["fonte"] = H264  # esta linha nem converte
    p, caminho, _f = await _publicando(db, make_user, nome="video.mp4")
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert ffmpeg.cmds == []
    assert not esquecido.exists()
    assert recente.exists()


@pytest.mark.parametrize(
    ("preparo", "trecho"),
    [
        (
            {"rc": 1, "erro": b"x.mov: Invalid data found when processing input\n"},
            "o ffmpeg não converteu o vídeo para H.264 (x.mov: Invalid data found",
        ),
        ({"levantar": TimeoutError()}, "a conversão para H.264 passou de 300 s"),
        ({"levantar": FileNotFoundError("ffmpeg")}, "o ffmpeg não está instalado no servidor"),
        ({"rc": 127}, "o ffmpeg não está instalado no servidor"),
    ],
)
async def test_conversao_que_nao_sai_falha_limpo_sem_subir(
    db, make_user, shopee, ffmpeg, pasta, preparo, trecho
):
    for k, v in preparo.items():
        setattr(ffmpeg, k, v)
    p, caminho, _f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert linha.status == "falhou"
    assert linha.container_id is None
    assert trecho in (linha.result or ""), linha.result
    assert "nada foi publicado" in (linha.result or "")
    # Nada foi pra Shopee e nada ficou na pasta (nem o arquivo pela metade).
    assert "init" not in shopee.chamadas
    assert list(pasta.iterdir()) == []


async def test_convertido_fora_da_regra_falha_e_some(db, make_user, shopee, ffmpeg, sonda, pasta):
    sonda["convertido"] = anuncio.InfoVideo(1080, 1920, 61.0, "h264")
    p, caminho, _f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert "não serve pra Shopee (video_fora_da_duracao)" in (linha.result or "")
    assert "init" not in shopee.chamadas
    assert list(pasta.iterdir()) == []


async def test_revisar_tambem_apaga_o_convertido(db, make_user, shopee, ffmpeg, pasta):
    shopee.erro_postar = [ShopeeVideoRedeError("sem_resposta", "ReadTimeout")]
    p, caminho, f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.REVISAR
    assert len(ffmpeg.conversoes) == 1
    assert not pub.caminho_convertido(f.id).exists()


# ─────────────────────────────────────────── disco livre antes de converter


async def test_pouco_disco_nao_converte_e_a_linha_espera(
    db, make_user, shopee, ffmpeg, disco, pasta
):
    disco["livre"] = 1 * GB  # abaixo do piso de 2 GB
    p, caminho, f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
    linha = await _relida(db, p.id)
    assert linha.status == "pendente"
    assert linha.attempts == 0
    assert "pouco disco pra converter" in (linha.result or ""), linha.result
    assert linha.opcoes["shopee"]["progresso"]["falhas_seguidas"] == 1
    assert ffmpeg.conversoes == []  # nem começou
    assert "init" not in shopee.chamadas
    assert disco["medidos"] == [pasta]
    assert not pub.caminho_convertido(f.id).exists()

    # Liberou o disco: o próximo tique converte e publica.
    disco["livre"] = 50 * GB
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id
    assert await _tique(db, p.id, caminho) == pub.PUBLICADO
    assert len(ffmpeg.conversoes) == 1


async def test_pouco_disco_seguido_vira_falha_sem_subir(db, make_user, shopee, ffmpeg, disco):
    disco["livre"] = 1 * GB
    p, caminho, _f = await _publicando(db, make_user)
    for _ in range(pub.MAX_FALHAS_SEGUIDAS - 1):
        assert await _tique(db, p.id, caminho) == pub.AGUARDANDO
        (lease,) = await svc.proximas_para_publicar(db)
        assert lease.id == p.id
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert "pouco disco pra converter" in (linha.result or "")
    assert "nada foi publicado" in (linha.result or "")
    assert ffmpeg.conversoes == [] and "init" not in shopee.chamadas


@pytest.mark.parametrize(
    ("livre_gb", "converte"),
    [(2.5, False), (3.5, True)],
)
async def test_piso_do_disco_e_3x_o_original_quando_passa_de_2_gb(
    ffmpeg, disco, pasta, tmp_path, livre_gb, converte
):
    fonte = tmp_path / "fonte.mov"
    fonte.write_bytes(b"")
    os.truncate(fonte, 1 * GB)  # esparso: 1 GB no `stat`, nada no disco
    disco["livre"] = int(livre_gb * GB)
    if converte:
        await pub.convertido_para_shopee(uuid.uuid4(), fonte)
        assert len(ffmpeg.conversoes) == 1
    else:
        with pytest.raises(pub.PoucoDiscoError, match="pouco disco pra converter"):
            await pub.convertido_para_shopee(uuid.uuid4(), fonte)
        assert ffmpeg.conversoes == []


# ──────────────────────── HDR / gama larga fica de fora; 10 bits SDR converte


@pytest.mark.parametrize(
    ("codec", "pix_fmt", "transfer", "primaries", "motivo"),
    [
        ("hevc", "yuv420p10le", "arib-std-b67", "bt2020", anuncio.FORMATO),  # iPhone HDR (HLG)
        ("hevc", "yuv420p10le", "arib-std-b67", None, anuncio.FORMATO),
        ("hevc", "yuv420p", "arib-std-b67", None, anuncio.FORMATO),  # HLG marcado
        ("hevc", "yuv420p", "smpte2084", None, anuncio.FORMATO),  # PQ / HDR10
        # 10 bits sem curva declarada pode ser HDR sem etiqueta: fica de fora.
        ("vp9", "p010le", None, None, anuncio.FORMATO),
        ("hevc", "yuv420p10le", None, None, anuncio.FORMATO),
        ("hevc", "yuv420p10le", "unknown", None, anuncio.FORMATO),
        # Curva SDR, mas gama larga: a conversão não remapeia a cor.
        ("hevc", "yuv420p10le", "bt2020-10", "bt2020", anuncio.FORMATO),
        ("hevc", "yuv420p10le", "bt709", "bt2020", anuncio.FORMATO),
        ("hevc", "yuv420p10le", "bt709", "smpte432", anuncio.FORMATO),  # Display P3
        ("hevc", "yuv420p", "bt709", "bt2020", anuncio.FORMATO),
        # 17/11/2026: 10 bits SDR declarado (o HEVC 10 bits BT.709 do CapCut /
        # iPhone — a Airfryer BBQ) converte: cair pra 8 bits não muda a cor.
        ("hevc", "yuv420p10le", "bt709", "bt709", None),
        ("hevc", "yuv420p10le", "bt709", None, None),
        ("hevc", "yuv420p10le", "smpte170m", "smpte170m", None),
        ("hevc", "yuv420p10le", "iec61966-2-1", None, None),
        ("vp9", "yuv420p10le", "bt709", "unknown", None),
        # SDR de 8 bits converte normal.
        ("hevc", "yuv420p", "bt709", None, None),
        ("hevc", "yuvj420p", None, None, None),
        ("hevc", "nv12", None, None, None),
        # O H.264 4:2:0 de 8 bits sobe o original, sem ffmpeg: a guarda não vale.
        ("h264", "yuv420p", "arib-std-b67", "bt2020", None),
        ("h264", "yuv420p", None, None, None),
        # O H.264 de 10 bits / 4:4:4 TAMBÉM passa pela conversão (16/11/2026),
        # então a mesma guarda vale pra ele — antes o HLG de 10 bits passava e
        # saía desbotado do ffmpeg.
        ("h264", "yuv420p10le", "arib-std-b67", None, anuncio.FORMATO),
        ("h264", "yuv420p10le", None, None, anuncio.FORMATO),
        ("h264", "yuv420p10le", "bt709", "bt709", None),
        ("h264", "yuv444p", "bt709", None, None),
    ],
)
async def test_so_o_que_precisaria_mapear_cor_na_conversao_fica_de_fora(
    codec, pix_fmt, transfer, primaries, motivo
):
    info = anuncio.InfoVideo(1080, 1920, 24.2, codec, pix_fmt, transfer, primaries)
    assert anuncio.motivo_do_formato(info, 10_000) == motivo
    # A elegibilidade e o publicador usam a MESMA regra de "vai converter".
    assert pub.precisa_converter(info) == anuncio.sobe_convertido(info)


async def test_hevc_hdr_falha_sem_converter_nem_subir(db, make_user, shopee, ffmpeg, sonda, pasta):
    hdr = anuncio.InfoVideo(1080, 1920, 24.2, "hevc", "yuv420p10le", "arib-std-b67")
    # O agendar já recusa (mesma guarda do robô)…
    user = await make_user()
    integ = await _cenario_barbosa(db, user)
    marca = await _marca(db)
    rede = await _conta_shopee(db, marca, integ)
    c, f = await _criativo(db, marca, nome="IMG_HDR.MOV")
    sonda["fonte"] = hdr
    with pytest.raises(svc.RoboError, match="video_formato_nao_aceito"):
        await svc.agendar(db, creative=c, file=f, redes=[rede], legenda="F110L #uranyx")
    # …e a linha que já estava na fila (arquivo trocado depois) falha no
    # publicador sem rodar o ffmpeg.
    sonda["fonte"] = HEVC
    (p,) = await svc.agendar(db, creative=c, file=f, redes=[rede], legenda="F110L #uranyx")
    (lease,) = await svc.proximas_para_publicar(db)
    assert lease.id == p.id
    sonda["fonte"] = hdr
    caminho = Path(get_settings().uploads_dir) / f.file_rel
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert "não serve pra Shopee (video_formato_nao_aceito)" in (linha.result or "")
    assert ffmpeg.cmds == []
    assert "init" not in shopee.chamadas
    assert not pasta.exists() or list(pasta.iterdir()) == []


async def test_sonda_le_pix_fmt_e_color_transfer(monkeypatch, tmp_path):
    """O ffprobe pede `pix_fmt`, `color_transfer` e `color_primaries` e o retrato os carrega;
    sem eles (ffprobe velho, campo ausente) ficam None e nada muda."""
    pedidos: list = []

    class _Proc:
        returncode = 0

        def __init__(self, corpo):
            self.corpo = corpo

        async def communicate(self):
            return json.dumps(self.corpo).encode(), b""

    corpos = [
        {
            "streams": [
                {
                    "width": 1080,
                    "height": 1920,
                    "codec_name": "hevc",
                    "pix_fmt": "yuv420p10le",
                    "color_transfer": "arib-std-b67",
                    "color_primaries": "bt2020",
                }
            ],
            "format": {"duration": "12.5"},
        },
        {"streams": [{"width": 1080, "height": 1920, "codec_name": "hevc"}], "format": {}},
    ]

    async def criar(*cmd, **_kw):
        pedidos.append(cmd)
        return _Proc(corpos.pop(0))

    monkeypatch.setattr(anuncio.asyncio, "create_subprocess_exec", criar)
    hdr = await _SONDAR_REAL_FFPROBE(tmp_path / "a.mov")
    assert (hdr.codec, hdr.pix_fmt, hdr.color_transfer, hdr.color_primaries, hdr.duracao) == (
        "hevc",
        "yuv420p10le",
        "arib-std-b67",
        "bt2020",
        12.5,
    )
    assert anuncio.motivo_do_formato(hdr, 10_000) == anuncio.FORMATO
    simples = await _SONDAR_REAL_FFPROBE(tmp_path / "b.mov")
    assert (simples.pix_fmt, simples.color_transfer, simples.color_primaries) == (None, None, None)
    entradas = pedidos[0][pedidos[0].index("-show_entries") + 1]
    assert all(c in entradas for c in ("pix_fmt", "color_transfer", "color_primaries"))


# ─────────────────────────────────────────── limpeza e conferência do H.264


async def test_convertido_some_quando_a_shopee_recusa_depois_do_upload(
    db, make_user, shopee, ffmpeg, enviados, pasta
):
    """Conversão OK, upload feito, e a Shopee devolve FAILED na
    transcodificação dela: `_falhou` zera o container (nada foi ao ar, o
    vídeo segue livre pras outras lojas) e tira o H.264 da pasta."""
    shopee.status_upload = ["FAILED"]
    p, caminho, f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    assert len(ffmpeg.conversoes) == 1
    assert enviados["arquivos"] == [pub.caminho_convertido(f.id)]
    assert shopee.chamadas.count("init") == 1
    linha = await _relida(db, p.id)
    assert linha.status == "falhou" and linha.container_id is None
    assert "a Shopee recusou o vídeo (FAILED" in (linha.result or "")
    assert not pub.caminho_convertido(f.id).exists()
    assert list(pasta.iterdir()) == []
    # Comprovado que não saiu: não conta como "já na Shopee".
    ja = select(anuncio.ja_na_shopee(f.id, linha.creative_id, f.sha256))
    assert (await db.execute(ja)).scalar() is False


async def test_convertido_que_sai_hevc_e_recusado_e_nada_sobe(
    db, make_user, shopee, ffmpeg, sonda, pasta, tmp_path
):
    sonda["convertido"] = HEVC  # o ffmpeg "converteu" e o resultado não é H.264
    fonte = tmp_path / "fonte.mov"
    fonte.write_bytes(b"hevc")
    with pytest.raises(pub.ConversaoError, match="video_formato_nao_aceito"):
        await pub.convertido_para_shopee(uuid.uuid4(), fonte)
    assert list(pasta.iterdir()) == []

    p, caminho, _f = await _publicando(db, make_user)
    assert await _tique(db, p.id, caminho) == pub.FALHOU
    linha = await _relida(db, p.id)
    assert "o vídeo convertido para H.264 não serve pra Shopee (video_formato_nao_aceito)" in (
        linha.result or ""
    ), linha.result
    assert "init" not in shopee.chamadas
    assert list(pasta.iterdir()) == []


async def test_rodar_mata_o_processo_que_estoura_o_tempo():
    comeco = time.monotonic()
    with pytest.raises(TimeoutError):
        await pub._rodar(["sleep", "30"], 0.3)
    assert time.monotonic() - comeco < 5


# ═════════════════════════════════════════════════════ ffmpeg de verdade


def _tem_libx265() -> bool:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    r = subprocess.run(  # noqa: S603 — ffmpeg local, argumentos do próprio teste
        ["ffmpeg", "-hide_banner", "-encoders"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return b"libx265" in r.stdout and b"libx264" in r.stdout


@pytest.mark.skipif(not _tem_libx265(), reason="sem ffmpeg com libx265/libx264 nesta máquina")
async def test_conversao_de_verdade_hevc_vira_h264_na_mesma_resolucao(
    monkeypatch, tmp_path, pasta
):
    monkeypatch.setattr(anuncio, "sondar_video", _SONDAR_REAL)
    fonte = tmp_path / "hevc.mov"
    subprocess.run(  # noqa: S603 — ffmpeg local, arquivo do próprio teste
        [  # noqa: S607
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx265", "-preset", "ultrafast", "-tag:v", "hvc1",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(fonte),
        ],
        check=True,
        capture_output=True,
    )
    assert (await _SONDAR_REAL(fonte)).codec == "hevc"
    chave = uuid.uuid4()
    destino, info, tamanho = await pub.convertido_para_shopee(chave, fonte)
    assert destino == pub.caminho_convertido(chave)
    assert info.codec == "h264"
    assert (info.largura, info.altura) == (720, 1280)
    assert info.duracao is not None and abs(info.duracao - 4.0) < 0.3
    assert tamanho == destino.stat().st_size > 0
    r = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt",
            "-of", "csv=p=0", str(destino),
        ],
        capture_output=True,
        check=True,
    )
    trilhas = [t.strip(",") for t in r.stdout.decode().split()]
    assert "h264,yuv420p" in trilhas and "aac" in trilhas, trilhas
    # Segunda chamada (o próximo tique): reaproveita, sem rodar o ffmpeg.

    async def proibido(*_a, **_k):
        raise AssertionError("converteu de novo")

    monkeypatch.setattr(pub, "_rodar", proibido)
    assert (await pub.convertido_para_shopee(chave, fonte))[0] == destino
    pub.apagar_convertido(chave)
    assert list(pasta.iterdir()) == []


def _hevc_de_teste(destino: Path, *, tamanho: str, fps: int, segundos: int, extra=()) -> None:
    subprocess.run(  # noqa: S603 — ffmpeg local, arquivo do próprio teste
        [  # noqa: S607
            *("ffmpeg", "-hide_banner", "-loglevel", "error", "-y"),
            *("-f", "lavfi", "-i", f"testsrc2=size={tamanho}:rate={fps}:duration={segundos}"),
            *("-c:v", "libx265", "-preset", "ultrafast", "-tag:v", "hvc1", *extra, str(destino)),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not _tem_libx265(), reason="sem ffmpeg com libx265/libx264 nesta máquina")
async def test_conversao_de_verdade_limita_lado_menor_a_1080_e_30_fps(monkeypatch, tmp_path, pasta):
    """O que vem maior (aqui 1440x2560 a 60 fps, como o 4K/60 do iPhone) sai
    com o lado menor em 1080, a proporção mantida e 30 fps."""
    monkeypatch.setattr(anuncio, "sondar_video", _SONDAR_REAL)
    fonte = tmp_path / "grande.mov"
    _hevc_de_teste(fonte, tamanho="1440x2560", fps=60, segundos=4)
    destino, info, _tamanho = await pub.convertido_para_shopee(uuid.uuid4(), fonte)
    assert info.codec == "h264"
    assert (info.largura, info.altura) == (1080, 1920)
    r = subprocess.run(  # noqa: S603
        [  # noqa: S607
            *("ffprobe", "-v", "error", "-select_streams", "v:0"),
            *("-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(destino)),
        ],
        capture_output=True,
        check=True,
    )
    assert r.stdout.decode().strip() == "30/1"


@pytest.mark.skipif(not _tem_libx265(), reason="sem ffmpeg com libx265/libx264 nesta máquina")
async def test_10_bits_bt709_de_verdade_passa_e_vira_h264_de_8_bits(monkeypatch, tmp_path, pasta):
    """O caso da Airfryer BBQ (17/11/2026): HEVC Main 10 marcado BT.709 —
    serve pra Shopee e sai H.264 4:2:0 de 8 bits, ainda BT.709."""
    monkeypatch.setattr(anuncio, "sondar_video", _SONDAR_REAL)
    fonte = tmp_path / "dez_bits_sdr.mp4"
    _hevc_de_teste(
        fonte,
        tamanho="720x1280",
        fps=30,
        segundos=4,
        extra=(
            *("-pix_fmt", "yuv420p10le"),
            # O libx265 só grava a etiqueta de cor (VUI) pelos próprios parâmetros.
            *("-x265-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:log-level=error"),
        ),
    )
    info = await _SONDAR_REAL(fonte)
    assert (info.codec, info.pix_fmt, info.color_transfer, info.color_primaries) == (
        "hevc",
        "yuv420p10le",
        "bt709",
        "bt709",
    )
    assert anuncio.motivo_do_formato(info, fonte.stat().st_size) is None
    assert pub.precisa_converter(info)
    chave = uuid.uuid4()
    destino, saida, _tamanho = await pub.convertido_para_shopee(chave, fonte)
    assert (saida.codec, saida.pix_fmt) == ("h264", "yuv420p")
    assert saida.color_transfer in (None, "bt709")
    assert anuncio.motivo_do_formato(saida, destino.stat().st_size) is None
    pub.apagar_convertido(chave)


@pytest.mark.skipif(not _tem_libx265(), reason="sem ffmpeg com libx265/libx264 nesta máquina")
async def test_sonda_de_verdade_ve_o_hevc_de_10_bits(tmp_path):
    fonte = tmp_path / "hdr.mov"
    _hevc_de_teste(
        fonte, tamanho="720x1280", fps=30, segundos=4, extra=("-pix_fmt", "yuv420p10le")
    )
    info = await _SONDAR_REAL(fonte)
    assert (info.codec, info.pix_fmt) == ("hevc", "yuv420p10le")
    assert anuncio.motivo_do_formato(info, fonte.stat().st_size) == anuncio.FORMATO
