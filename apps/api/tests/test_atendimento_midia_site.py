# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""A foto e o vídeo do cliente no CHAMADO DO SITE aparecem e tocam na conversa (25/11/2026).

O Eduardo, com os prints do US-26-0006 (Uranyx): "isso o site da uranyx". A
equipe: "eu consigo ver as fotos e vídeos desse protocolo pelo atendimento?" e
"pra mim o vídeo não está carregando, quando pego direto do tuta; as fotos
consegui ver, mas o vídeo deu erro".

O aviso do site chega pelo Brevo com os arquivos ANEXADOS (MIME) e os links do
site trocados pelo rastreio de clique; a Central guarda os anexos cifrados. O
vídeo do print é H.264 + AAC num .MOV do iPhone, com uma faixa de áudio
espacial (`apac`) e 5 de metadados: o Chrome não abre o `video/quicktime` do
link do site numa aba. O que estes testes seguram (`services/atendimento/
midia_site.py` e a rota GET /api/atendimento/email/conversas/{id}/midias/{anexo}):

- o cartão do e-mail do site marca o anexo que é foto ou vídeo (pelo que o
  e-mail declara) e lista o que o aviso diz que veio SÓ pelo link do site;
- a rota entrega a FOTO como veio (`inline`), e o VÍDEO convertido para MP4
  H.264 Main + AAC com `faststart` (a primeira faixa de áudio só; a de
  metadados fora), com Range (206) e a CAPA (`?capa=1`, JPEG); a segunda vez
  não converte de novo; o tipo vale pelos BYTES (PDF, HEIC, HTML com nome de
  foto: 404);
- só na conversa do chamado do site e só o anexo de um e-mail DESTA conversa;
  a lixeira, o escopo da equipe e a permissão valem como nos cartões;
- a conversão: o áudio que não converte vai sem o áudio; o arquivo que não abre
  marca a falha e não se tenta de novo logo; dois pedidos do mesmo vídeo, uma
  conversão;
- os cabeçalhos: inline, nosniff, CSP sem script (sandbox com
  allow-same-origin, para o vídeo aberto numa aba tocar) e private.

Nada aqui abre rede; o ffmpeg é o da máquina (sem ele, os testes de vídeo pulam).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import MailAttachment, MailLixeira, UserRole
from app.security.cipher import decrypt_json
from app.services.atendimento import midia_site, video
from tests.test_atendimento_email_sites import (
    BREVO_U,
    EMAIL,
    _chamado,
    _com_os_enderecos_do_site,
    _pastas_dos_sites,
)
from tests.test_atendimento_separa_email import admin as admin
from tests.test_mail_ponte import _config, cena, entrar, meta_de, rodar

JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"1" * 400
PNG = b"\x89PNG\r\n\x1a\n" + b"2" * 300
PDF = b"%PDF-1.4\n" + b"3" * 300
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 300
HTML = b"<html><script>alert(1)</script></html>"
# O texto que o aviso do site vira no Tuta (o HTML lido como texto), o do print.
AVISO = (
    "5. Documentação\n"
    "Foto do produto\n"
    "IMG_8652.jpg · 276 KBanexado · Abrir\n"
    "Foto do defeito\n"
    "IMG_8653.jpg · 262 KBanexado · Abrir\n"
    "Vídeo curto demonstrando o defeito\n"
    "81651840771__3B185C8A-DCC5-48F4-AA2F-5A33C9625C81.MOV · 2,1 MBanexado · Abrir\n"
    "Assistir ao vídeo do defeito\n"
    "Links válidos até 15/05/2027. Depois disso, os arquivos ficam no painel.\n"
    "Abrir no painel\n"
)
COM_FFMPEG = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="sem ffmpeg nesta máquina"
)


@pytest.fixture(autouse=True)
def _disco(tmp_path, monkeypatch) -> Path:
    """O `uploads_dir` é uma pasta temporária."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


def _gerar(destino: Path, *args: str) -> bytes:
    ffmpeg = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(  # noqa: S603 — ffmpeg local, arquivo do próprio teste
        [*ffmpeg, *args, str(destino)],  # noqa: S607
        check=True,
        timeout=120,
    )
    return destino.read_bytes()


@pytest.fixture(scope="module")
def videos(tmp_path_factory) -> SimpleNamespace:
    """O vídeo como o do print: H.264 Baseline + AAC num MOV (marca `qt  `), com uma
    SEGUNDA faixa de áudio (a do print é a espacial da Apple, 4 canais) — e um MOV
    sem áudio."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("sem ffmpeg nesta máquina")
    pasta = tmp_path_factory.mktemp("videos-site")
    iphone = _gerar(
        pasta / "iphone.mov",
        *("-f", "lavfi", "-i", "testsrc2=size=480x360:rate=25"),
        *("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"),
        *("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000"),
        *("-map", "0:v", "-map", "1:a", "-map", "2:a", "-t", "2"),
        *("-c:v", "libx264", "-profile:v", "baseline", "-c:a", "aac"),
        *("-ac:a:1", "4", "-f", "mov"),
    )
    mudo = _gerar(
        pasta / "mudo.mov",
        *("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30"),
        *("-t", "1", "-c:v", "libx264", "-an", "-f", "mov"),
    )
    return SimpleNamespace(iphone=iphone, mudo=mudo, pasta=pasta)


def _sonda(dados: bytes, pasta: Path) -> dict:
    arquivo = pasta / f"sonda-{uuid4().hex}.mp4"
    arquivo.write_bytes(dados)
    r = subprocess.run(  # noqa: S603 — ffprobe local, arquivo do próprio teste
        [  # noqa: S607
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,profile,width,height,has_b_frames,channels"
            ":format=format_name",
            "-of",
            "json",
            str(arquivo),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(r.stdout)


# ─────────────── as réguas (puras) ───────────────


def test_o_que_o_cartao_oferece_como_miniatura_pelo_que_o_email_declara():
    assert midia_site.especie_declarada("US-26-0006-04-defeito-2.jpg", "image/jpeg") == "imagem"
    for tipo in ("image/png", "image/gif", "image/webp", "image/jpg", "IMAGE/JPEG; name=x"):
        assert midia_site.especie_declarada("x", tipo) == "imagem", tipo
    assert midia_site.especie_declarada("US-26-0006-05-video.mov", "video/quicktime") == "video"
    for tipo in ("video/mp4", "video/3gpp", "video/x-m4v"):
        assert midia_site.especie_declarada("x", tipo) == "video", tipo
    # O genérico: pela extensão.
    assert midia_site.especie_declarada("IMG_1.JPG", "application/octet-stream") == "imagem"
    assert midia_site.especie_declarada("C:\\fotos\\VID.MOV", "") == "video"
    assert midia_site.especie_declarada("x.webp", None) == "imagem"
    # O resto continua só para baixar.
    for nome, tipo in (
        ("nota.pdf", "application/pdf"),
        ("foto.heic", "image/heic"),
        ("foto.jpg", "text/html"),  # o tipo declarado vence o nome
        ("x.svg", "image/svg+xml"),
        ("audio.m4a", "audio/mp4"),
        ("sem-extensao", "application/octet-stream"),
        (None, None),
    ):
        assert midia_site.especie_declarada(nome, tipo) is None, (nome, tipo)


def test_o_tipo_de_verdade_pelos_bytes():
    assert midia_site.especie_de_verdade(JPG) == ("imagem", "image/jpeg")
    assert midia_site.especie_de_verdade(PNG) == ("imagem", "image/png")
    assert midia_site.especie_de_verdade(b"GIF89a" + b"0" * 20) == ("imagem", "image/gif")
    assert midia_site.especie_de_verdade(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == (
        "imagem",
        "image/webp",
    )
    mov = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "
    assert midia_site.especie_de_verdade(mov) == ("video", "video/quicktime")
    mp4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    assert midia_site.especie_de_verdade(mp4) == ("video", "video/mp4")
    for ruim in (PDF, HEIC, HTML, b"", None, b"\x00\x00\x00\x20ftypM4A " + b"0" * 20):
        assert midia_site.especie_de_verdade(ruim) is None, ruim


def test_os_arquivos_do_aviso_e_o_que_veio_so_pelo_link():
    assert midia_site.arquivos_do_aviso(AVISO) == [
        {"nome": "IMG_8652.jpg", "tamanho": "276 KB", "anexado": True},
        {"nome": "IMG_8653.jpg", "tamanho": "262 KB", "anexado": True},
        {
            "nome": "81651840771__3B185C8A-DCC5-48F4-AA2F-5A33C9625C81.MOV",
            "tamanho": "2,1 MB",
            "anexado": True,
        },
    ]
    assert midia_site.arquivos_so_pelo_link(AVISO) == []
    # O vídeo grande (acima dos 2,9 MB do anexo do site) vem só com o "Abrir".
    grande = AVISO + "Outro vídeo\nVID_0042.MOV · 25,3 MBAbrir\nfoto final.png · 3 MB · Abrir\n"
    assert midia_site.arquivos_so_pelo_link(grande) == [
        {"nome": "VID_0042.MOV", "tamanho": "25,3 MB"},
        {"nome": "foto final.png", "tamanho": "3 MB"},
    ]
    # Texto qualquer não vira arquivo.
    for texto in ("", None, "Abrir no painel", "Assistir ao vídeo do defeito", "x.jpg · Abrir"):
        assert midia_site.arquivos_do_aviso(texto) == [], texto


def test_o_nome_entregue_e_o_caminho_no_disco(_disco):
    assert midia_site.nome_para_mostrar("US-26-0006-05-video.mov", "video") == (
        "US-26-0006-05-video.mp4"
    )
    assert midia_site.nome_para_mostrar("a/b\\VID.MOV", "video", capa=True) == "VID-capa.jpg"
    assert midia_site.nome_para_mostrar("IMG_8653.jpg", "imagem") == "IMG_8653.jpg"
    assert midia_site.nome_para_mostrar(None, "imagem") == "anexo"
    anexo = UUID("6b5327a9-c01b-4f1d-9d7f-44da3921cf6b")
    mp4, capa, falhou = midia_site.caminhos(anexo)
    base = _disco / "atendimento" / "site" / "6b"
    assert (mp4, capa, falhou) == (
        base / f"{anexo}.mp4",
        base / f"{anexo}.jpg",
        base / f"{anexo}.falhou",
    )
    # Só UUID: nada do e-mail no caminho.
    with pytest.raises(ValueError):
        midia_site.caminhos("../../etc/passwd")  # type: ignore[arg-type]


def test_os_cabecalhos_sem_script_e_so_no_navegador():
    h = midia_site.cabecalhos('vídeo "x".mp4')
    assert h["Content-Disposition"].startswith("inline;")
    assert "\r" not in h["Content-Disposition"] and "\n" not in h["Content-Disposition"]
    assert h["X-Content-Type-Options"] == "nosniff"
    csp = h["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "sandbox allow-same-origin" in csp
    assert "allow-scripts" not in csp
    assert h["Cache-Control"].startswith("private")


def test_so_a_conversa_do_chamado_do_site():
    da_ponte = {"fonte": "tuta", "mail": {"caixa": "sac"}}
    site = SimpleNamespace(plataforma="site", canal="email", dados=da_ponte)
    assert midia_site.conversa_aceita(site)
    assert not midia_site.conversa_aceita(
        SimpleNamespace(plataforma="ml", canal="email", dados=da_ponte)
    )
    assert not midia_site.conversa_aceita(
        SimpleNamespace(plataforma="site", canal="chat", dados=da_ponte)
    )
    assert not midia_site.conversa_aceita(
        SimpleNamespace(plataforma="site", canal="email", dados={})
    )


# ─────────────── a conversão (o ffmpeg de verdade) ───────────────


@COM_FFMPEG
def test_o_mov_do_iphone_vira_mp4_que_o_chrome_toca(videos, _disco):
    anexo = uuid4()
    mp4 = midia_site.converter(videos.iphone, anexo)
    assert mp4 is not None and mp4 == midia_site.caminhos(anexo)[0]
    dados = mp4.read_bytes()
    # MP4 de verdade, com o índice na frente (o `faststart`: toca antes de baixar tudo).
    assert midia_site.especie_de_verdade(dados) == ("video", "video/mp4")
    assert dados.find(b"moov") < dados.find(b"mdat")
    sonda = _sonda(dados, videos.pasta)
    fluxos = sonda["streams"]
    assert [s["codec_type"] for s in fluxos] == ["video", "audio"], "só uma faixa de áudio"
    v, a = fluxos
    assert (v["codec_name"], v["profile"], v["has_b_frames"]) == ("h264", "Main", 0)
    assert (v["width"], v["height"]) == (480, 360)
    assert (a["codec_name"], a["channels"]) == ("aac", 2)
    # A capa: um JPEG.
    capa = midia_site.capa_pronta(anexo)
    assert capa is not None and capa.read_bytes().startswith(b"\xff\xd8\xff")
    assert not midia_site.caminhos(anexo)[2].exists(), "sem marca de falha"


@COM_FFMPEG
def test_o_mov_sem_audio_tambem(videos, _disco):
    mp4 = midia_site.converter(videos.mudo, uuid4())
    assert mp4 is not None
    assert [s["codec_type"] for s in _sonda(mp4.read_bytes(), videos.pasta)["streams"]] == ["video"]


def test_o_audio_que_nao_converte_vai_sem_o_audio(monkeypatch, _disco):
    """A faixa de áudio que o ffmpeg não decodifica (a espacial da Apple quando vem
    primeiro) derruba a conversão: a segunda tentativa vai sem o áudio."""
    linhas: list[list[str]] = []

    def rodar(args, _tempo):
        linhas.append(args)
        if "-an" not in args and args[-1].endswith(".mp4"):
            return False  # com o áudio: o ffmpeg falha
        Path(args[-1]).write_bytes(b"\x00\x00\x00\x20ftypisom" + b"0" * 40)
        return True

    monkeypatch.setattr(video, "ffmpeg_disponivel", lambda: True)
    monkeypatch.setattr(
        video,
        "_sondar",
        lambda _c: video.InfoVideo(duracao=13.2, largura=480, altura=360, fps=24.7, tem_audio=True),
    )
    monkeypatch.setattr(midia_site, "_rodar", rodar)
    anexo = uuid4()
    assert midia_site.converter(b"\x00\x00\x00\x14ftypqt  " + b"0" * 40, anexo) is not None
    conversoes = [a for a in linhas if a[-1].endswith(".mp4")]
    assert len(conversoes) == 2
    assert "-an" not in conversoes[0] and "0:a:0" in conversoes[0]
    assert "-an" in conversoes[1] and "0:a:0" not in conversoes[1]
    assert midia_site.capa_pronta(anexo) is not None


async def test_o_que_nao_abre_marca_a_falha_e_nao_tenta_de_novo_logo(monkeypatch, _disco):
    monkeypatch.setattr(video, "ffmpeg_disponivel", lambda: True)
    monkeypatch.setattr(video, "_sondar", lambda _c: None)
    anexo = uuid4()
    assert await midia_site.preparar_video(anexo, b"\x00\x00\x00\x14ftypqt  lixo") is None
    marca = midia_site.caminhos(anexo)[2]
    assert marca.exists()

    def nao_chama(*_a):
        raise AssertionError("não converte de novo logo depois da falha")

    monkeypatch.setattr(midia_site, "converter", nao_chama)
    assert await midia_site.preparar_video(anexo, b"x") is None
    # Passada a espera, tenta de novo.
    velho = time.time() - midia_site.ESPERA_DEPOIS_DA_FALHA_S - 60
    os.utime(marca, (velho, velho))
    chamadas = []

    def converte(dados, aid):
        chamadas.append(aid)
        mp4 = midia_site.caminhos(aid)[0]
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.write_bytes(b"mp4")
        return mp4

    monkeypatch.setattr(midia_site, "converter", converte)
    assert await midia_site.preparar_video(anexo, b"x") == midia_site.caminhos(anexo)[0]
    assert chamadas == [anexo]


async def test_sem_ffmpeg_nao_marca_falha(monkeypatch, _disco):
    monkeypatch.setattr(video, "ffmpeg_disponivel", lambda: False)
    anexo = uuid4()
    assert await midia_site.preparar_video(anexo, b"\x00\x00\x00\x14ftypqt  ") is None
    assert not midia_site.caminhos(anexo)[2].exists()


async def test_dois_pedidos_do_mesmo_video_uma_conversao(monkeypatch, _disco):
    chamadas = []

    def converte(dados, aid):
        chamadas.append(aid)
        time.sleep(0.2)
        mp4 = midia_site.caminhos(aid)[0]
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.write_bytes(b"mp4")
        return mp4

    monkeypatch.setattr(midia_site, "converter", converte)
    anexo = uuid4()
    a, b = await asyncio.gather(
        midia_site.preparar_video(anexo, b"x"), midia_site.preparar_video(anexo, b"x")
    )
    assert a == b == midia_site.caminhos(anexo)[0]
    assert chamadas == [anexo]


# ─────────────── o cartão e a rota ───────────────


async def _chamado_com_arquivos(db, cena, anexos, *, protocolo="US-26-0006", corpo=AVISO):
    await _com_os_enderecos_do_site(db, cena)
    await _pastas_dos_sites(db, cena)
    mid, cid = await _chamado(
        db,
        cena,
        pasta="*uranyx",
        de=BREVO_U,
        para=["sac@uranyx.com.br"],
        reply_to="cliente.teste@exemplo.com.br",
        assunto=f"[{protocolo}] SAC — tigela de vidro — Cliente Teste",
        corpo=corpo,
        anexos=anexos,
    )
    anexos_gravados = (
        await db.execute(select(MailAttachment).where(MailAttachment.message_id == mid))
    ).scalars()
    ids = {decrypt_json(a.metadata_enc)["filename"]: a.id for a in anexos_gravados}
    return mid, cid, ids


def _rota(cid, anexo, capa: bool = False) -> str:
    return f"{EMAIL}/conversas/{cid}/midias/{anexo}" + ("?capa=1" if capa else "")


async def test_o_cartao_marca_foto_e_video_e_o_que_veio_so_pelo_link(db, cena, client, admin):
    corpo = AVISO + "Outro vídeo\nVID_0042.MOV · 25,3 MBAbrir\n"
    _mid, cid, _ids = await _chamado_com_arquivos(
        db,
        cena,
        [
            ("US-26-0006-01-produto.jpg", "image/jpeg", JPG),
            ("US-26-0006-05-video.mov", "video/quicktime", b"\x00\x00\x00\x14ftypqt  "),
            ("nota.pdf", "application/pdf", PDF),
        ],
        corpo=corpo,
    )
    r = await client.get(f"{EMAIL}/conversas/{cid}/emails")
    assert r.status_code == 200, r.text
    [cartao] = r.json()["emails"]
    por_nome = {a["filename"]: a for a in cartao["anexos"]}
    assert por_nome["US-26-0006-01-produto.jpg"]["midia"] == "imagem"
    assert por_nome["US-26-0006-05-video.mov"]["midia"] == "video"
    assert por_nome["nota.pdf"]["midia"] is None
    assert all(
        set(a) == {"id", "tamanho", "filename", "content_type", "midia"} for a in cartao["anexos"]
    )
    assert cartao["arquivos_so_pelo_link"] == [{"nome": "VID_0042.MOV", "tamanho": "25,3 MB"}]


async def test_fora_do_site_o_cartao_fica_como_era(db, cena, client, admin):
    mid = await entrar(
        db,
        cena.geral,
        para=["21max@tuta.com"],
        assunto="Foto do produto",
        corpo="Segue a foto",
        anexos=[("foto.jpg", "image/jpeg", JPG)],
    )
    await rodar(db)
    meta = await meta_de(db, mid)
    assert meta.conversa_id is not None, (meta.estado, meta.motivo)
    r = await client.get(f"{EMAIL}/conversas/{meta.conversa_id}/emails")
    [cartao] = r.json()["emails"]
    [anexo] = cartao["anexos"]
    assert set(anexo) == {"id", "tamanho", "filename", "content_type"}
    assert "arquivos_so_pelo_link" not in cartao
    # E a rota de mídia não serve conversa que não é do chamado do site.
    r = await client.get(_rota(meta.conversa_id, anexo["id"]))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "midia_nao_encontrada"


async def test_a_rota_entrega_a_foto_inline(db, cena, client, admin):
    _mid, cid, ids = await _chamado_com_arquivos(
        db, cena, [("US-26-0006-04-defeito-2.jpg", "image/jpeg", JPG), ("p.png", "image/png", PNG)]
    )
    r = await client.get(_rota(cid, ids["US-26-0006-04-defeito-2.jpg"]))
    assert r.status_code == 200, r.text
    assert r.content == JPG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline;")
    assert 'filename="US-26-0006-04-defeito-2.jpg"' in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == midia_site.CSP
    assert r.headers["cache-control"].startswith("private")
    r = await client.get(_rota(cid, ids["p.png"]))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/png", PNG)
    # A foto não tem "capa": a própria foto.
    r = await client.get(_rota(cid, ids["p.png"], capa=True))
    assert (r.status_code, r.content) == (200, PNG)


@COM_FFMPEG
async def test_a_rota_entrega_o_video_convertido_com_range_e_a_capa(
    db, cena, client, admin, videos, monkeypatch
):
    _mid, cid, ids = await _chamado_com_arquivos(
        db, cena, [("US-26-0006-05-video.mov", "video/quicktime", videos.iphone)]
    )
    anexo = ids["US-26-0006-05-video.mov"]
    r = await client.get(_rota(cid, anexo))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-disposition"].startswith("inline;")
    assert 'filename="US-26-0006-05-video.mp4"' in r.headers["content-disposition"]
    assert r.headers["content-security-policy"] == midia_site.CSP
    inteiro = r.content
    assert midia_site.especie_de_verdade(inteiro) == ("video", "video/mp4")
    # O <video> pede pedaços: Range → 206.
    r = await client.get(_rota(cid, anexo), headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{len(inteiro)}"
    assert r.content == inteiro[:100]
    # A capa: JPEG.
    r = await client.get(_rota(cid, anexo, capa=True))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content.startswith(b"\xff\xd8\xff")
    assert 'filename="US-26-0006-05-video-capa.jpg"' in r.headers["content-disposition"]
    # A segunda vez não converte (o disco já tem) — nem decifra o anexo.

    def nao_converte(*_a):
        raise AssertionError("converteu de novo")

    monkeypatch.setattr(midia_site, "converter", nao_converte)
    r = await client.get(_rota(cid, anexo))
    assert (r.status_code, r.content) == (200, inteiro)
    # A capa que sumiu do disco é refeita do MP4.
    midia_site.caminhos(anexo)[1].unlink()
    r = await client.get(_rota(cid, anexo, capa=True))
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8\xff")


async def test_o_tipo_vale_pelos_bytes(db, cena, client, admin):
    _mid, cid, ids = await _chamado_com_arquivos(
        db,
        cena,
        [
            ("nota.pdf", "application/pdf", PDF),
            ("foto.heic", "image/heic", HEIC),
            ("foto.jpg", "image/jpeg", HTML),  # diz que é foto e não é
        ],
    )
    for nome in ("nota.pdf", "foto.heic", "foto.jpg"):
        r = await client.get(_rota(cid, ids[nome]))
        assert r.status_code == 404, nome
        assert r.json()["detail"]["code"] == "midia_nao_encontrada"


async def test_video_que_nao_converte_e_404_sem_derrubar(db, cena, client, admin, monkeypatch):
    monkeypatch.setattr(midia_site, "converter", lambda *_a: None)
    _mid, cid, ids = await _chamado_com_arquivos(
        db, cena, [("v.mov", "video/quicktime", b"\x00\x00\x00\x14ftypqt  quebrado")]
    )
    r = await client.get(_rota(cid, ids["v.mov"]))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "video_nao_preparado"


async def test_so_o_anexo_de_um_email_desta_conversa(db, cena, client, admin):
    _mid, cid, ids = await _chamado_com_arquivos(db, cena, [("a.jpg", "image/jpeg", JPG)])
    outro_mid = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de=BREVO_U,
        para=["sac@uranyx.com.br"],
        reply_to="outra.cliente@exemplo.com.br",
        assunto="[US-26-0007] SAC — air fryer — Outra Cliente",
        corpo="Foto\nb.jpg · 1 KBanexado · Abrir\n",
        anexos=[("b.jpg", "image/jpeg", PNG)],
    )
    await rodar(db)
    outro = await meta_de(db, outro_mid)
    assert outro.conversa_id is not None and outro.conversa_id != cid
    [anexo_outro] = (
        await db.execute(select(MailAttachment.id).where(MailAttachment.message_id == outro_mid))
    ).scalars()
    # O anexo do outro chamado pela conversa deste: 404.
    assert (await client.get(_rota(cid, anexo_outro))).status_code == 404
    # Pela conversa dele: 200.
    assert (await client.get(_rota(outro.conversa_id, anexo_outro))).status_code == 200
    # Anexo que não existe, conversa que não existe, id torto.
    assert (await client.get(_rota(cid, uuid4()))).status_code == 404
    r = await client.get(_rota(uuid4(), ids["a.jpg"]))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    assert (await client.get(_rota(cid, "nao-e-uuid"))).status_code == 422


async def test_a_lixeira_esconde(db, cena, client, admin):
    mid, cid, ids = await _chamado_com_arquivos(db, cena, [("a.jpg", "image/jpeg", JPG)])
    assert (await client.get(_rota(cid, ids["a.jpg"]))).status_code == 200
    db.add(MailLixeira(message_id=mid, excluido_em=datetime.now(UTC)))
    await db.commit()
    assert (await client.get(_rota(cid, ids["a.jpg"]))).status_code == 404


async def test_travas_de_quem_ve(db, cena, client, make_user, auth_as):
    _mid, cid, ids = await _chamado_com_arquivos(db, cena, [("a.jpg", "image/jpeg", JPG)])
    # Sem login: 401.
    assert (await client.get(_rota(cid, ids["a.jpg"]))).status_code == 401
    # Quem não vê o atendimento: 403.
    operador = await make_user(role=UserRole.USER)
    operador.stock_tags = ["estoque"]
    await db.commit()
    auth_as(operador)
    r = await client.get(_rota(cid, ids["a.jpg"]))
    assert r.status_code == 403
    # O admin vê.
    auth_as(cena.dono)
    assert (await client.get(_rota(cid, ids["a.jpg"]))).status_code == 200
