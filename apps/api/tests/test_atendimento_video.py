# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""Vídeo na resposta ao comprador (19/11/2026, `services/atendimento/video.py`).

O Eduardo: "tem q deixar enviar vídeo no chat do atendimento tbm em todas
plataformas q são possíveis". O que estes testes seguram:

- o arquivo é conferido pelos BYTES (MP4/MOV/3GP; HEIC, M4A e foto não são
  vídeo), com teto de tamanho e de duração;
- o vídeo é CONVERTIDO para o que o WhatsApp toca no Android (doc da Meta:
  H.264 "High" com B-frames não toca): H.264 Main sem B-frames, AAC,
  `faststart`, lado menor até 720, até 30 quadros, sem metadados (o GPS do
  celular); o `sha256` continua o do arquivo da pessoa;
- onde a plataforma não aceita vídeo, a recusa diz POR QUÊ e acontece antes
  de abrir o arquivo (nada sobe); o painel leva a frase para a tela;
- o Zap: `POST /{pnid}/media` com o MP4 e a mensagem `type=video` (legenda
  opcional, pelo validador), pelas MESMAS travas do texto; a nossa cópia no
  disco e a URL relativa no balão (tocável, `inline`); a mesma régua de
  resultado (sem resposta = `revisar`, sem reenviar sozinho) e a trava de
  "o mesmo vídeo há 2 minutos"; o simulador não chama a Meta.

A Graph API é de MENTIRA (`tests/zap_apoio.GraphFalso`); o ffmpeg é o de
verdade (o teste que precisa dele pula sem ele).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import get_settings
from app.models import AtendimentoMensagem, AtendimentoZapMidia, User, UserRole
from app.routers import atendimento as rota
from app.services.atendimento import enviar, foto, painel, video
from app.services.atendimento import zap as zap_servico
from app.services.atendimento.enviar import EnvioRecusado
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph, envio)
    PNID,
    WA_ID,
    conversa_do_numero,
    envio,
    graph,
    zap,
)

URL = "/api/atendimento"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
V = "v26.0"
TOKEN = "token-de-mentira"

COM_FFMPEG = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="sem ffmpeg nesta máquina"
)


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
    """Vídeos de verdade, curtos: o do celular (1080p em pé, 60 quadros, H.264
    High com B-frames e áudio, com GPS nos metadados), um MOV deitado e um
    sem áudio."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("sem ffmpeg nesta máquina")
    pasta = tmp_path_factory.mktemp("videos")
    celular = _gerar(
        pasta / "celular.mp4",
        *("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=60"),
        *("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"),
        *("-t", "2", "-c:v", "libx264", "-profile:v", "high", "-bf", "3"),
        *("-c:a", "aac", "-shortest"),
        *("-metadata", "location=+23.5505-046.6333/", "-metadata", "title=segredo"),
    )
    mov = _gerar(
        pasta / "deitado.mov",
        *("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30"),
        *("-t", "2", "-c:v", "libx264", "-an", "-f", "mov"),
    )
    return SimpleNamespace(celular=celular, mov=mov, pasta=pasta)


def _sonda(dados: bytes, pasta: Path) -> dict:
    arquivo = pasta / "sonda.mp4"
    arquivo.write_bytes(dados)
    r = subprocess.run(  # noqa: S603 — ffprobe local, arquivo do próprio teste
        [  # noqa: S607
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,profile,width,height,has_b_frames,avg_frame_rate"
            ":format_tags",
            "-of",
            "json",
            str(arquivo),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(r.stdout)


# ─────────────── o arquivo ───────────────

MP4_CABECA = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"m" * 64
MOV_CABECA = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00" + b"q" * 64
GP3_CABECA = b"\x00\x00\x00\x14ftyp3gp4\x00\x00\x00\x00" + b"g" * 64
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"h" * 64
M4A = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00" + b"a" * 64
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"j" * 64


def test_video_pelos_bytes():
    assert video.tipo_pelo_conteudo(MP4_CABECA) == "video/mp4"
    assert video.tipo_pelo_conteudo(MOV_CABECA) == "video/quicktime"
    assert video.tipo_pelo_conteudo(GP3_CABECA) == "video/3gpp"
    # Foto do iPhone (HEIC), áudio M4A e JPEG não são vídeo.
    for nao in (HEIC, M4A, JPG, b"", b"ftyp"):
        assert video.tipo_pelo_conteudo(nao) is None
        assert not video.eh_video(nao)
    with pytest.raises(foto.FotoInvalida) as e:
        video.validar_video(b"", "a.mp4")
    assert e.value.code == "video_vazio"
    with pytest.raises(foto.FotoInvalida) as e:
        video.validar_video(JPG, "a.mp4", "video/mp4")
    assert e.value.code == "video_tipo"
    grande = MP4_CABECA + b"\x00" * video.MAX_BYTES_UPLOAD_VIDEO
    with pytest.raises(foto.FotoInvalida) as e:
        video.validar_video(grande, "a.mp4")
    assert e.value.code == "video_grande"


@COM_FFMPEG
def test_cabecalho_de_video_que_nao_abre(tmp_path):
    with pytest.raises(foto.FotoInvalida) as e:
        video.validar_video(MP4_CABECA, "quebrado.mp4", "video/mp4")
    assert e.value.code == "video_invalido"


def test_linha_do_ffmpeg_para_o_whatsapp():
    info = video.InfoVideo(duracao=20.0, largura=1080, altura=1920, fps=60.0, tem_audio=True)
    args = video.argumentos_ffmpeg("in.mov", "out.mp4", info, 2000)
    texto = " ".join(args)
    # H.264 Main SEM B-frames (o Android do WhatsApp), AAC, faststart.
    for trecho in (
        "-c:v libx264",
        "-profile:v main",
        "-bf 0",
        "-c:a aac",
        "-movflags +faststart",
        "-maxrate 2000k",
        # Sem os metadados do celular (o GPS do vídeo não vai ao comprador).
        "-map_metadata -1",
    ):
        assert trecho in texto, trecho
    filtros = args[args.index("-vf") + 1]
    assert "min(720,iw)" in filtros and "min(720,ih)" in filtros
    assert "fps=30" in filtros and filtros.endswith("format=yuv420p")
    # 30 quadros já: sem o filtro; sem áudio: `-an`.
    sem = video.InfoVideo(duracao=5.0, largura=640, altura=360, fps=29.97, tem_audio=False)
    args = video.argumentos_ffmpeg("a", "b", sem, 2000)
    assert "fps=" not in args[args.index("-vf") + 1]
    assert "-an" in args and "aac" not in args


def test_bitrate_para_caber_no_teto():
    teto = 16 * 1024 * 1024
    assert video.kbps_para_caber(10, teto) == video.KBPS_VIDEO_MAX
    assert video.kbps_para_caber(None, teto) == video.KBPS_VIDEO_MAX
    # 3 minutos: o vídeo cabe em 16 MB com ~650 kbps.
    k = video.kbps_para_caber(180, teto)
    assert video.KBPS_VIDEO_MIN < k < 700
    # Longo demais para caber legível: recusa ANTES de converter.
    longo = video.Video(conteudo=b"x", nome="a.mp4", mime="video/mp4", sha256="s", duracao=600)
    with pytest.raises(foto.FotoInvalida) as e:
        video.preparar(longo, teto)
    assert e.value.code == "video_grande"


@COM_FFMPEG
def test_converte_o_video_do_celular(videos):
    original = video.validar_video(videos.celular, "../../Meu Vídeo.MP4", "video/mp4")
    assert original.mime == "video/mp4" and original.nome == "Meu_V_deo.mp4"
    assert 1.5 < (original.duracao or 0) < 2.5
    pronto = video.preparar(original, 16 * 1024 * 1024)
    assert pronto.mime == "video/mp4" and pronto.nome.endswith(".mp4")
    # A trava de repetida compara o arquivo da PESSOA, não a conversão.
    assert pronto.sha256 == original.sha256
    assert pronto.conteudo != videos.celular
    sonda = _sonda(pronto.conteudo, videos.pasta)
    (v,) = [s for s in sonda["streams"] if s["codec_type"] == "video"]
    (a,) = [s for s in sonda["streams"] if s["codec_type"] == "audio"]
    assert (v["codec_name"], v["profile"], v["has_b_frames"]) == ("h264", "Main", 0)
    assert (v["width"], v["height"]) == (720, 1280)
    assert v["avg_frame_rate"] in ("30/1", "30000/1001")
    assert a["codec_name"] == "aac"
    tags = json.dumps(sonda.get("format", {}).get("tags", {}))
    assert "23.5505" not in tags and "segredo" not in tags
    # moov antes de mdat (faststart): o player começa sem baixar tudo.
    assert pronto.conteudo.find(b"moov") < pronto.conteudo.find(b"mdat")


@COM_FFMPEG
def test_mov_deitado_sem_audio(videos):
    original = video.validar_video(videos.mov, "IMG_0001.MOV", "video/quicktime")
    assert original.mime == "video/quicktime" and original.nome == "IMG_0001.mov"
    pronto = video.preparar(original, 16 * 1024 * 1024)
    sonda = _sonda(pronto.conteudo, videos.pasta)
    assert [s["codec_type"] for s in sonda["streams"]] == ["video"]
    (v,) = sonda["streams"]
    assert (v["width"], v["height"]) == (1280, 720)
    assert pronto.nome == "IMG_0001.mp4"


@COM_FFMPEG
def test_video_longo_recusado(tmp_path):
    longo = _gerar(
        tmp_path / "longo.mp4",
        *("-f", "lavfi", "-i", "color=c=black:size=64x64:rate=1"),
        *("-t", str(int(video.MAX_DURACAO_S) + 5), "-c:v", "libx264"),
    )
    with pytest.raises(foto.FotoInvalida) as e:
        video.validar_video(longo, "longo.mp4", "video/mp4")
    assert e.value.code == "video_longo"
    assert "3:00" in e.value.detail


# ─────────────── onde sai e onde não sai ───────────────


def _c(plataforma: str, canal: str, comprador: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(plataforma=plataforma, canal=canal, comprador_id=comprador)


def test_onde_o_video_sai_e_por_que_nao():
    assert video.motivo_sem_video(_c("whatsapp", "zap")) is None
    assert video.teto_da_plataforma(_c("whatsapp", "zap")) == 16 * 1024 * 1024
    esperado = {
        ("shopee", "chat"): "Seller Center",
        ("tiktok", "chat"): "não tem como a loja subir vídeo",
        ("ml", "pos_venda"): "não aceita vídeo nas mensagens do pedido",
        ("ml", "reclamacao"): "reclamação do Mercado Livre não aceita vídeo",
        # A pergunta do ML nem anexo aceita: a frase é a do anexo.
        ("ml", "pergunta"): "Pergunta no anúncio",
        ("magalu", "chat"): "Magalu",
    }
    for (plataforma, canal), trecho in esperado.items():
        motivo = video.motivo_sem_video(_c(plataforma, canal))
        assert motivo and trecho in motivo, (plataforma, canal, motivo)
        assert video.teto_da_plataforma(_c(plataforma, canal)) is None
    # Amazon: só a conversa com comprador tem anexo — e mesmo assim, sem vídeo.
    amazon = _c("amazon", "email", "abc123@marketplace.amazon.com.br")
    assert "não aceita vídeo" in video.motivo_sem_video(amazon)
    # O Zap no formato antigo (com a plataforma da venda) segue sem anexo.
    assert video.motivo_sem_video(_c("shopee", "zap"))


def _envio_ligado(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_auto_ativo", False)
    monkeypatch.setattr(s, "atendimento_usuarios", "")


async def test_enviar_recusa_video_onde_nao_sai(db, make_user, monkeypatch):
    """Cinto do caminho único: um vídeo que chegasse por outra porta numa
    conversa da Shopee é recusado com a frase, antes de qualquer upload."""
    from tests.test_atendimento_foto import _cenario

    _envio_ligado(monkeypatch)
    conversa, user = await _cenario(db, make_user)
    v = video.Video(conteudo=MP4_CABECA, nome="a.mp4", mime="video/mp4", sha256="s" * 64)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, conversa, v, user=user)
    assert e.value.code == "foto_nao_suportada"
    assert "Seller Center" in str(e.value.detail)


# ─────────────── a rota e o Zap ───────────────


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
def uploads(tmp_path, monkeypatch) -> Path:
    """O disco da mídia: uma pasta só deste teste (nunca o /data/uploads)."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


def _upload_ok(graph, media_id: str = "UPV1") -> None:
    graph.quando(
        "POST", f"/{V}/{PNID}/media", lambda req: httpx.Response(200, json={"id": media_id})
    )


def _corpos(graph) -> list[dict]:
    return [
        json.loads(r.content)
        for r in graph.chamadas
        if r.method == "POST" and r.url.path.endswith("/messages")
    ]


@COM_FFMPEG
async def test_video_do_zap_sobe_e_manda_type_video(db, client, envio, pessoa, uploads, videos):
    _upload_ok(envio.graph)
    r = await client.post(
        f"{URL}/conversas/{envio.conversa.id}/foto",
        files={"arquivo": ("demo.mp4", videos.celular, "video/mp4")},
        data={"legenda": "Segue o vídeo de como usar"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["mensagem"]
    assert (m["status"], m["tipo"]) == ("enviada", "video")
    assert m["texto"] == "Segue o vídeo de como usar"
    upload, mensagem = envio.graph.chamadas
    # 1. O upload: o MP4 CONVERTIDO (não o arquivo do celular), tipo video/mp4.
    assert upload.url.path == f"/{V}/{PNID}/media"
    assert b'name="type"' in upload.content and b"video/mp4" in upload.content
    assert videos.celular not in upload.content
    # 2. A mensagem `type=video` com o id da mídia, a legenda e o nosso id.
    assert json.loads(mensagem.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": WA_ID,
        "type": "video",
        "video": {"id": "UPV1", "caption": "Segue o vídeo de como usar"},
        "biz_opaque_callback_data": m["id"],
    }
    for req in envio.graph.chamadas:
        assert TOKEN not in str(req.url) and TOKEN.encode() not in req.content
    # A nossa cópia (o convertido) no disco; o balão TOCA pela rota relativa.
    midia = (
        await db.execute(select(AtendimentoZapMidia).where(AtendimentoZapMidia.direcao == "saida"))
    ).scalar_one()
    assert (midia.status, midia.media_id, midia.mime_detectado) == ("baixada", "UPV1", "video/mp4")
    (anexo,) = m["anexos"]
    assert anexo["tipo"] == "video" and anexo["url"] == f"/api/atendimento/zap/midia/{midia.id}"
    assert anexo["duracao"] > 1
    servida = await client.get(anexo["url"])
    assert servida.status_code == 200
    assert servida.headers["content-type"].startswith("video/mp4")
    assert servida.headers["content-disposition"].startswith("inline")
    assert servida.content == (uploads / midia.caminho).read_bytes()
    linha = await db.get(AtendimentoMensagem, midia.mensagem_id)
    assert linha.payload["video"]["sha256"] and "foto" not in linha.payload


@COM_FFMPEG
async def test_video_sem_legenda_e_o_mesmo_video_em_2_minutos(db, envio, pessoa, uploads, videos):
    _upload_ok(envio.graph)
    conversa = envio.conversa
    v = await _pronto(conversa, videos.mov, "a.mov")
    m = await enviar.enviar_foto(db, conversa, v, user=pessoa)
    assert m.status == "enviada" and m.texto is None and m.tipo == "video"
    (corpo,) = _corpos(envio.graph)
    assert corpo["video"] == {"id": "UPV1"}
    # O mesmo arquivo de novo (mesmo que reconvertido): recusado, nada sobe.
    chamadas = len(envio.graph.chamadas)
    conversa = await conversa_do_numero(db)
    de_novo = await _pronto(conversa, videos.mov, "b.mov")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, conversa, de_novo, user=pessoa)
    assert e.value.code == "envio_repetido" and "mesmo vídeo" in str(e.value.detail)
    assert len(envio.graph.chamadas) == chamadas


async def _pronto(conversa, dados: bytes, nome: str) -> video.Video:
    import asyncio

    return await asyncio.to_thread(video.validar_para_a_caixa, conversa, dados, nome, None)


@COM_FFMPEG
async def test_video_sem_resposta_da_meta_fica_incerto_sem_reenviar(
    db, envio, pessoa, uploads, videos
):
    _upload_ok(envio.graph)
    envio.graph.mensagens(resposta=httpx.ReadTimeout("sem resposta"))
    v = await _pronto(envio.conversa, videos.mov, "a.mov")
    m = await enviar.enviar_foto(db, envio.conversa, v, user=pessoa)
    assert m.status == "revisar"
    # Uma tentativa só: o DaVinci nunca reenvia sozinho o que ficou incerto.
    assert len(_corpos(envio.graph)) == 1


@COM_FFMPEG
async def test_video_upload_recusado_nao_saiu(db, envio, pessoa, uploads, videos):
    envio.graph.quando(
        "POST",
        f"/{V}/{PNID}/media",
        lambda req: httpx.Response(
            400, json={"error": {"code": 131053, "message": "Media upload error"}}
        ),
    )
    v = await _pronto(envio.conversa, videos.mov, "a.mov")
    m = await enviar.enviar_foto(db, envio.conversa, v, user=pessoa)
    # Falha LIMPA (nada chegou ao cliente): o "Tentar de novo" é da pessoa.
    assert m.status == "falhou" and m.erro == "meta:131053"
    assert _corpos(envio.graph) == []


@COM_FFMPEG
async def test_video_legenda_pelo_validador(db, client, envio, pessoa, uploads, videos):
    r = await client.post(
        f"{URL}/conversas/{envio.conversa.id}/foto",
        files={"arquivo": ("a.mp4", videos.celular, "video/mp4")},
        data={"legenda": "faz um pix na chave 11999998888 que eu mando"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "texto_invalido"
    assert envio.graph.chamadas == []
    assert (await db.scalar(select(AtendimentoZapMidia.id))) is None


@COM_FFMPEG
async def test_video_no_simulador_nao_chama_a_meta(db, client, envio, pessoa, uploads, videos):
    s = get_settings()
    envio.settings.atendimento_simulador = True
    try:
        assert enviar.vai_para_o_simulador("whatsapp") and not s.is_prod
        r = await client.post(
            f"{URL}/conversas/{envio.conversa.id}/foto",
            files={"arquivo": ("a.mov", videos.mov, "video/quicktime")},
        )
    finally:
        envio.settings.atendimento_simulador = False
    assert r.status_code == 200, r.text
    m = r.json()["mensagem"]
    assert (m["status"], m["tipo"]) == ("enviada", "video")
    assert m["anexos"][0]["url"].startswith("/api/atendimento/zap/midia/")
    assert envio.graph.chamadas == []


async def test_rota_video_com_envio_desligado_nada_le(db, client, envio, pessoa, uploads):
    envio.settings.atendimento_zap_envio_ativo = False
    try:
        r = await client.post(
            f"{URL}/conversas/{envio.conversa.id}/foto",
            files={"arquivo": ("a.mp4", MP4_CABECA, "video/mp4")},
        )
    finally:
        envio.settings.atendimento_zap_envio_ativo = True
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "zap_envio_desligado"
    assert envio.graph.chamadas == []


async def test_rota_video_onde_nao_sai_diz_por_que(db, client, make_user, auth_as, monkeypatch):
    from tests.test_atendimento_foto import _cenario

    _envio_ligado(monkeypatch)
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    conversa, _ = await _cenario(db, make_user)

    def nao_converte(*a, **k):  # o arquivo nem chega a ser aberto
        raise AssertionError("não devia abrir o vídeo")

    monkeypatch.setattr(video, "validar_video", nao_converte)
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/foto",
        files={"arquivo": ("a.mp4", MP4_CABECA, "video/mp4")},
    )
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert (d["code"], d["motivo"]) == ("foto_invalida", "video_nao_suportado")
    assert "Seller Center" in d["detail"]
    linhas = await db.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == conversa.id, AtendimentoMensagem.tipo == "video"
        )
    )
    assert linhas is None


async def test_rota_video_nunca_junto_de_outro_anexo(db, client, envio, pessoa, uploads):
    r = await client.post(
        f"{URL}/conversas/{envio.conversa.id}/foto",
        files=[
            ("arquivo", ("a.mp4", MP4_CABECA, "video/mp4")),
            ("arquivo", ("b.jpg", JPG, "image/jpeg")),
        ],
    )
    # O Zap manda um anexo por mensagem: dois de uma vez já são recusados.
    assert r.status_code == 422, r.text
    assert envio.graph.chamadas == []


async def test_painel_diz_se_o_video_sai(db, client, envio, pessoa, make_user):
    from tests.test_atendimento_foto import _cenario

    r = await client.get(f"{URL}/conversas/{envio.conversa.id}/painel")
    assert r.status_code == 200, r.text
    ef = r.json()["envio_foto"]
    assert ef["video"] is True and ef["video_motivo"] is None
    assert ef["video_max_bytes"] == video.MAX_BYTES_UPLOAD_VIDEO
    assert set(ef["video_tipos"]) == {"video/mp4", "video/quicktime", "video/3gpp"}
    assert ef["video_max_s"] == 180
    # Na Shopee a foto sai, o vídeo não: o painel leva a frase para a tela.
    shopee, _ = await _cenario(db, make_user)
    ef = await painel.situacao_da_foto(db, shopee)
    assert ef["pode"] is True
    assert ef["video"] is False and "Seller Center" in ef["video_motivo"]


def test_corpo_do_video_do_zap():
    corpo = zap_servico.corpo_imagem("5511", "M1", None, mensagem_id=None, tipo="video")
    assert corpo == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "5511",
        "type": "video",
        "video": {"id": "M1"},
    }
    with pytest.raises(ValueError):
        zap_servico.corpo_imagem("5511", "M1", None, mensagem_id=None, tipo="document")
