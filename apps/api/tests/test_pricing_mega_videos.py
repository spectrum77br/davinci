# ruff: noqa: F811 — `editor` e `mega` são fixtures importadas do teste das embalagens
"""Vídeo no painel de mídias da Tabela de Preços (29/09/2026).

Eduardo, com o print da aba Vídeos mostrando só "Airfryer.mp4 — Abrir no
MEGA": "vídeos estão aparecendo para abrir pelo MEGA, não tem como eles
aparecerem aqui também?". Agora a aba Vídeos tem grade com a CAPA (miniatura
de /midias/arquivo) e o visor toca o MP4 que o sidecar converteu uma vez
(/midias/video, com Range). O original continua só no MEGA.

Duas camadas:
  * API (rotas do DaVinci) com o MegaFalso de test_pricing_mega_embalagens e
    um /video de mentira que entende Range;
  * o sidecar DE VERDADE (infra/megacmd/app.py) com `mega-ls`/`mega-get` e o
    ffmpeg de mentira — o que se testa é a regra (converte uma vez, cache,
    Range, falha anotada × falha da hora), não o ffmpeg em si. O ffmpeg real
    foi medido em produção num container descartável (29/09/2026).
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.mega_fotos import MegaError
from tests.test_pricing_mega_embalagens import (  # noqa: F401 — fixtures
    PERM_VIEW,
    SIDECAR,
    MegaFalso,
    _produto,
    _usuario,
    editor,
    mega,
)

# ─────────────── o /video do sidecar como a API o vê ───────────────


class VideoFalso:
    """`sidecar_stream_repasse` de mentira: devolve o "MP4 convertido"
    (b"MP4:" + original) e responde Range como o FileResponse do sidecar."""

    def __init__(self, mega: MegaFalso) -> None:
        self.mega = mega
        self.pedidos: list[tuple[str, dict[str, str], float]] = []
        self.erro: int | None = None
        self.fechados = 0
        # capa (/thumb) de mentira: registra o tempo pedido e a ordem.
        self.thumbs: list[tuple[str, float]] = []
        self.capa_falha_em: str | None = None
        self.em_andamento = 0
        self.max_em_andamento_video = 0

    async def __call__(
        self, path: str, *, params: dict[str, Any], headers: dict[str, str] | None = None,
        timeout: float = 0.0,
    ):
        assert path == "/video"
        alvo = params["path"]
        self.pedidos.append((alvo, dict(headers or {}), timeout))
        if self.erro:
            raise MegaError(f"sidecar HTTP {self.erro}", self.erro)
        if alvo not in self.mega.arquivos:
            raise MegaError("sidecar HTTP 404", 404)
        dados = b"MP4:" + self.mega.arquivos[alvo]
        cab = {
            "content-type": "video/mp4",
            "accept-ranges": "bytes",
            "etag": '"v1"',
            "last-modified": "Tue, 29 Sep 2026 10:00:00 GMT",
            "x-previa-cache": "hit",
        }
        rng = (headers or {}).get("Range")
        if rng:
            ini_s, fim_s = rng.removeprefix("bytes=").split("-")
            ini, fim = int(ini_s), (int(fim_s) if fim_s else len(dados) - 1)
            corpo, status = dados[ini : fim + 1], 206
            cab["content-range"] = f"bytes {ini}-{fim}/{len(dados)}"
        else:
            corpo, status = dados, 200
        cab["content-length"] = str(len(corpo))

        async def pedacos():
            yield corpo

        async def fechar() -> None:
            self.fechados += 1

        return status, cab, pedacos(), fechar


@pytest.fixture
def video(mega: MegaFalso, monkeypatch) -> VideoFalso:
    v = VideoFalso(mega)
    monkeypatch.setattr("app.routers.pricing_mega.sidecar_stream_repasse", v)

    async def thumb(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        alvo = params["path"]
        v.thumbs.append((alvo, timeout))
        eh_video = alvo.lower().rsplit(".", 1)[-1] in ("mp4", "mov", "webm")
        if eh_video:
            v.em_andamento += 1
            v.max_em_andamento_video = max(v.max_em_andamento_video, v.em_andamento)
        try:
            await asyncio.sleep(0.01)  # dá chance de outro pedido entrar junto
            if v.capa_falha_em and alvo.endswith(v.capa_falha_em):
                raise MegaError("sidecar HTTP 422", 422)
            return await mega.bytes_(path, params=params, timeout=timeout)
        finally:
            if eh_video:
                v.em_andamento -= 1

    for mod in ("app.routers.pricing_mega", "app.services.mega_midias"):
        monkeypatch.setattr(f"{mod}.sidecar_bytes", thumb)
    return v


# ─────────────── capa pela miniatura; o original continua no MEGA ───────────────


async def test_capa_de_video_pela_miniatura(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
):
    mega.arquivo("/Celular/X/Airfryer.mp4", conteudo=b"VID")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    url = f"/api/pricing/mega/products/{p.id}/midias/arquivo"

    r = await client.get(url, params={"nome": "Airfryer.mp4", "miniatura": 1})
    assert r.status_code == 200 and r.content == b"MINI:VID"
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith('inline; filename="Airfryer.jpg"')
    assert r.headers["cache-control"] == "private, max-age=86400"
    # A grade NÃO converte (preparar=0: o sidecar só devolve a capa que já
    # existe, na hora) — converter é do play e do pré-aquecimento.
    assert video.thumbs[-1] == ("/Celular/X/Airfryer.mp4", 600.0)
    assert mega.lados[-1] == 320

    # Poster do visor: a capa grande.
    r = await client.get(url, params={"nome": "Airfryer.mp4", "miniatura": 1, "grande": 1})
    assert r.status_code == 200 and mega.lados[-1] == 1600

    # Capa que não sai (sidecar antigo ou vídeo que o ffmpeg não lê): 404
    # sem_previa — a tela volta ao ícone de filme. NUNCA o vídeo inteiro.
    for modo in ("antigo", "nao_abre"):
        mega.thumb = modo
        r = await client.get(url, params={"nome": "Airfryer.mp4", "miniatura": 1})
        assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_previa", modo
    mega.thumb = "fora"
    r = await client.get(url, params={"nome": "Airfryer.mp4", "miniatura": 1})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "mega_sidecar"
    assert not any(c[1] == "/file" for c in mega.chamadas)


@pytest.mark.parametrize(
    "extra",
    [{}, {"baixar": 1}, {"miniatura": 1, "baixar": 1}, {"grande": 1}],
)
async def test_original_de_video_continua_400(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
    extra: dict[str, int],
):
    mega.arquivo("/Celular/X/review.MOV", conteudo=b"VID")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias/arquivo",
                         params={"nome": "review.MOV", **extra})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "video_abre_no_mega"
    assert not any(c[1] in ("/file", "/thumb") for c in mega.chamadas)


# ─────────────── /midias/video: toca no visor, com Range ───────────────


async def test_video_repassa_range_e_devolve_206(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
):
    mega.arquivo("/Celular/X/sub/clip.mp4", conteudo=b"0123456789")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    url = f"/api/pricing/mega/products/{p.id}/midias/video"
    inteiro = b"MP4:0123456789"

    # O <video> começa pedindo "bytes=0-" e pula para o meio com outro Range.
    r = await client.get(url, params={"nome": "sub/clip.mp4"},
                         headers={"Range": "bytes=4-7", "If-Range": '"v1"'})
    assert r.status_code == 206
    assert r.content == inteiro[4:8]
    assert r.headers["content-range"] == f"bytes 4-7/{len(inteiro)}"
    assert r.headers["content-length"] == "4"
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.headers["x-content-type-options"] == "nosniff"
    # Validadores vão junto: o navegador manda If-Range e não emenda pedaços
    # de duas versões do vídeo.
    assert r.headers["etag"] == '"v1"'
    assert "last-modified" in r.headers
    # Header interno do sidecar não vaza.
    assert "x-previa-cache" not in r.headers
    caminho, recebidos, tempo = video.pedidos[-1]
    assert caminho == "/Celular/X/sub/clip.mp4"
    assert recebidos == {"Range": "bytes=4-7", "If-Range": '"v1"'}
    # A 1ª vez converte antes de responder: tempo longo.
    assert tempo == 1800.0
    assert video.fechados == 1  # a conexão com o sidecar é fechada no fim

    # Sem Range: o arquivo inteiro, 200, e nenhum Range inventado.
    r = await client.get(url, params={"nome": "sub/clip.mp4"})
    assert r.status_code == 200 and r.content == inteiro
    assert r.headers["content-length"] == str(len(inteiro))
    assert "content-range" not in r.headers
    assert video.pedidos[-1][1] == {}


@pytest.mark.parametrize(
    "nome",
    [
        "../Outra/x.mp4", "sub/../../x.mp4", "/Celular/Outro/x.mp4", "a//b.mp4",
        "*.mp4", "clip.mp?", "./clip.mp4", "clip", "M1 listrada",
        # Não é vídeo: foto, arte e PDF não saem por aqui.
        "frente.jpg", "Embalagens/caixa.pdf", "Embalagens/caixa.af",
    ],
)
async def test_video_nome_invalido_ou_nao_video_e_400(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
    nome: str,
):
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias/video", params={"nome": nome})
    assert r.status_code == 400, nome
    assert r.json()["detail"]["code"] == "nome_invalido"
    assert video.pedidos == []


@pytest.mark.parametrize(
    ("erro", "status", "code"),
    [
        (404, 404, "arquivo_nao_encontrado"),
        (400, 400, "nome_invalido"),
        # O ffmpeg não leu o arquivo: a tela mostra "Abrir no MEGA".
        (422, 404, "sem_previa"),
        (416, 416, "trecho_invalido"),
        (503, 503, "mega_sidecar"),
        (500, 502, "mega_sidecar"),
    ],
)
async def test_video_erros_do_sidecar(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
    erro: int, status: int, code: str,
):
    mega.arquivo("/Celular/X/clip.mp4")
    p = await _produto(db, editor, nome="X", pasta="/Celular/X")
    video.erro = erro
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias/video",
                         params={"nome": "clip.mp4"})
    assert r.status_code == status
    assert r.json()["detail"]["code"] == code


async def test_video_de_produto_sem_pasta_e_404(
    client: AsyncClient, db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
):
    p = await _produto(db, editor, nome="Sem pasta")
    r = await client.get(f"/api/pricing/mega/products/{p.id}/midias/video",
                         params={"nome": "clip.mp4"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_pasta"
    assert video.pedidos == []


async def test_quem_so_ve_pode_assistir(
    client: AsyncClient, db: AsyncSession, mega: MegaFalso, video: VideoFalso,
    auth_as: Callable[[User | None], None],
):
    """A tabela é vista por quem não edita (compras, agência interna): a aba
    Vídeos precisa tocar para eles também — é a mesma permissão de ver foto."""
    dono = await _usuario(db, {"tabela_precos_produtos": {"view": True, "edit": True}})
    mega.arquivo("/Celular/X/clip.mp4", conteudo=b"VID")
    p = await _produto(db, dono, nome="X", pasta="/Celular/X")
    base = f"/api/pricing/mega/products/{p.id}/midias"

    auth_as(await _usuario(db, PERM_VIEW))
    r = await client.get(f"{base}/video", params={"nome": "clip.mp4"})
    assert r.status_code == 200 and r.content == b"MP4:VID"
    r = await client.get(f"{base}/arquivo", params={"nome": "clip.mp4", "miniatura": 1})
    assert r.status_code == 200 and r.content == b"MINI:VID"

    auth_as(await _usuario(db, {}))
    r = await client.get(f"{base}/video", params={"nome": "clip.mp4"})
    assert r.status_code == 403


# ─────────────── pré-aquecimento: vídeos depois das fotos, um por vez ───────────────


async def test_aquecer_previas_inclui_videos_um_por_vez(
    db: AsyncSession, editor: User, mega: MegaFalso, video: VideoFalso,
):
    """A capa pedida ao sidecar converte o vídeo inteiro — é o que tira da
    tela o "Preparando o vídeo…" da 1ª vez. Um por vez (o sidecar só converte
    um), depois das fotos, com tempo longo e contagem própria no resumo."""
    from app.services.mega_midias import aquecer_previas

    mega.arquivo("/Celular/S7/a.jpg", "/Celular/S7/clip.mp4", "/Celular/S7/sub/making of.MOV")
    # Vídeo dentro de Embalagens não é vídeo do produto (nem prévia de arte).
    mega.arquivo("/Celular/S7/Embalagens/unboxing.mp4", "/Celular/S7/Embalagens/caixa.pdf")
    mega.arquivo("/Malas/ABS/M1/c.jpg", "/Malas/ABS/review.webm", "/Malas/ABS/quebrado.mp4")
    await _produto(db, editor, nome="S7 16", pasta="/Celular/S7")
    await _produto(db, editor, nome="S7 32", pasta="/Celular/S7")
    await _produto(db, editor, nome="ABS", raiz="mala", pasta="/Malas/ABS")
    video.capa_falha_em = "quebrado.mp4"

    r = await aquecer_previas(db, paralelo=2)

    assert r == {
        "pastas": 2, "arquivos": 3, "prontos": 3, "sem_previa": 0, "pastas_com_erro": 0,
        "videos": 4, "videos_prontos": 3, "videos_sem_previa": 1,
    }
    pedidos = [c for c, _t in video.thumbs]
    vids = [c for c in pedidos if c.rsplit(".", 1)[-1].lower() in ("mp4", "mov", "webm")]
    assert sorted(vids) == sorted([
        "/Celular/S7/clip.mp4", "/Celular/S7/sub/making of.MOV",
        "/Malas/ABS/review.webm", "/Malas/ABS/quebrado.mp4",
    ])  # cada um UMA vez, mesmo com duas linhas na pasta do S7
    assert "/Celular/S7/Embalagens/unboxing.mp4" not in pedidos
    # Fotos e artes primeiro; vídeos depois, um de cada vez, com tempo longo.
    primeiro_video = pedidos.index(vids[0])
    assert all(c not in vids for c in pedidos[:primeiro_video])
    assert all(c in vids for c in pedidos[primeiro_video:])
    assert video.max_em_andamento_video == 1
    assert {t for c, t in video.thumbs if c in vids} == {1800.0}


# ─────────────── o cliente da API: Range vai, 206 volta ───────────────


async def test_sidecar_stream_repasse_leva_o_range_e_devolve_o_206(monkeypatch):
    import httpx

    from app.services import mega_fotos

    corpo = bytes(range(256)) * 300
    vistos: list[str | None] = []

    def responder(req: httpx.Request) -> httpx.Response:
        vistos.append(req.headers.get("range"))
        if req.url.params["path"].endswith("some.mp4"):
            return httpx.Response(404, json={"detail": "arquivo não encontrado"})
        return httpx.Response(
            206,
            content=corpo[100:70_100],
            headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 100-70099/{len(corpo)}",
                     "ETag": '"abc"'},
        )

    real = httpx.AsyncClient

    def cliente(**kw):
        return real(transport=httpx.MockTransport(responder), **kw)

    monkeypatch.setattr(mega_fotos.httpx, "AsyncClient", cliente)
    status, cab, pedacos, fechar = await mega_fotos.sidecar_stream_repasse(
        "/video", params={"path": "/C/X/a.mp4"}, headers={"Range": "bytes=100-70099"}
    )
    recebido = b"".join([p async for p in pedacos])
    await fechar()
    assert status == 206 and recebido == corpo[100:70_100]
    assert cab["content-range"] == f"bytes 100-70099/{len(corpo)}"
    assert cab["content-length"] == "70000" and cab["etag"] == '"abc"'
    assert vistos == ["bytes=100-70099"]
    with pytest.raises(MegaError) as e:
        await mega_fotos.sidecar_stream_repasse("/video", params={"path": "/C/X/some.mp4"})
    assert e.value.status_code == 404


# ─────────────── o sidecar de verdade, com MEGA e ffmpeg de mentira ───────────────


class MegaDeVideo:
    """`run` de mentira para o sidecar real: `mega-ls -l` no formato de
    produção, `mega-get` que grava o arquivo, a conversão (`nice … ffmpeg`)
    que escreve b"MP4:" + original no destino, e a capa (`ffmpeg -ss`) que
    grava um PNG — só quando o vídeo tem quadro naquele segundo."""

    def __init__(self, arquivos: dict[str, bytes]) -> None:
        self.arquivos = arquivos
        self.baixados = 0
        self.conversoes: list[list[str]] = []
        self.capas: list[list[str]] = []
        self.conversao_rc, self.conversao_out = 0, ""
        self.mega_get_rc = 0
        self.duracao = 30.0  # segundos com quadro (vídeo curto: < 1)

    def run(self, cmd: list[str], *, timeout: int = 0, input_text: str | None = None):
        if cmd[0] == "mega-ls":
            path = cmd[-1]
            if path not in self.arquivos:
                return 1, "not found"
            nome = path.rsplit("/", 1)[-1]
            return 0, (
                "FLAGS VERS      SIZE            DATE       NAME\n"
                f"----    1 {len(self.arquivos[path]):>12} 29Sep2026 10:00:00 {nome}"
            )
        if cmd[0] == "mega-get":
            self.baixados += 1
            if self.mega_get_rc:
                return self.mega_get_rc, f"timeout after {timeout}s: mega-get"
            path, dest = cmd[1], cmd[2]
            Path(dest, path.rsplit("/", 1)[-1]).write_bytes(self.arquivos[path])
            return 0, ""
        if cmd[0] == "nice":
            self.conversoes.append(cmd)
            if self.conversao_rc:
                return self.conversao_rc, self.conversao_out
            origem = cmd[cmd.index("-i") + 1]
            Path(cmd[-1]).write_bytes(b"MP4:" + Path(origem).read_bytes())
            return 0, ""
        if cmd[0] == "ffmpeg":
            self.capas.append(cmd)
            if float(cmd[cmd.index("-ss") + 1]) >= self.duracao:
                return 0, ""  # como o ffmpeg de verdade: sai 0 e não grava nada
            from PIL import Image

            Image.new("RGB", (1280, 720), (200, 30, 30)).save(cmd[-1], "PNG")
            return 0, ""
        raise AssertionError(f"comando inesperado: {cmd}")


CLIP = "/Celular/X/clip.mp4"
RUIM = "/Celular/X/ruim.mp4"


@pytest.fixture
def sidecar_video(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    m = MegaDeVideo({
        CLIP: b"ORIGINAL" * 50,
        RUIM: b"lixo",
        "/Celular/X/curto.mp4": b"CURTO",
        "/Celular/X/outro.MOV": b"OUTRO",
        "/Celular/X/foto.jpg": b"JPG",
    })
    monkeypatch.setattr(SIDECAR, "run", m.run)
    monkeypatch.setattr(SIDECAR, "_PREVIAS_DIR", str(tmp_path))
    # Sem `with`: o startup (limpeza do cache) não roda no teste.
    c = TestClient(SIDECAR.app)
    c.headers["X-Sidecar-Token"] = SIDECAR.TOKEN
    return m, c


def test_sidecar_video_converte_uma_vez_e_atende_range(sidecar_video, tmp_path):
    m, c = sidecar_video
    esperado = b"MP4:" + b"ORIGINAL" * 50

    r = c.get("/video", params={"path": CLIP})
    assert r.status_code == 200 and r.content == esperado
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["x-previa-cache"] == "miss"
    assert m.baixados == 1 and len(m.conversoes) == 1
    cmd = m.conversoes[0]
    # nice, 2 threads, H.264 com o índice no começo, lado maior limitado.
    assert cmd[:4] == ["nice", "-n", "15", "ffmpeg"]
    assert cmd[cmd.index("-threads") + 1] == "2"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert cmd[cmd.index("-vf") + 1] == SIDECAR._ESCALA_VIDEO
    assert "min(1280,iw)" in SIDECAR._ESCALA_VIDEO and "min(1280,ih)" in SIDECAR._ESCALA_VIDEO

    # Range: 206 com Content-Range, do cache, sem baixar nem converter de novo.
    r = c.get("/video", params={"path": CLIP}, headers={"Range": "bytes=4-11"})
    assert r.status_code == 206 and r.content == esperado[4:12]
    assert r.headers["content-range"] == f"bytes 4-11/{len(esperado)}"
    assert r.headers["x-previa-cache"] == "hit"
    assert m.baixados == 1 and len(m.conversoes) == 1
    # O MP4 mora no cache das prévias; nada de ".tmp" nem do original largado.
    assert len(list(tmp_path.rglob("*.mp4"))) == 1
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/Celular/X/foto.jpg", 400),
        ("/Celular/X/*.mp4", 400),
        ("/Celular/../x.mp4", 400),
        ("/Celular/X/./clip.mp4", 400),
        ("/Celular/X/nao.mp4", 404),
    ],
)
def test_sidecar_video_recusa_o_que_nao_e_video(sidecar_video, path: str, status: int):
    m, c = sidecar_video
    assert c.get("/video", params={"path": path}).status_code == status
    assert m.baixados == 0 and m.conversoes == []


def test_sidecar_video_ruim_fica_anotado_e_falha_da_hora_nao(sidecar_video, tmp_path):
    m, c = sidecar_video
    # Arquivo que o ffmpeg não lê: 422, anotado — nem o /video nem a capa
    # baixam de novo por 7 dias.
    m.conversao_rc, m.conversao_out = 1, "[mov,mp4] moov atom not found"
    assert c.get("/video", params={"path": RUIM}).status_code == 422
    assert c.get("/video", params={"path": RUIM}).status_code == 422
    assert c.get("/thumb", params={"path": RUIM, "lado": 320}).status_code == 422
    assert m.baixados == 1
    assert len(list(tmp_path.rglob("*.falhou"))) == 1

    # Estourou o tempo, foi morto (memória) ou o disco encheu: 503, NADA
    # anotado — a próxima tentativa converte.
    for rc, out in ((124, "timeout after 1800s"), (-9, ""), (1, "No space left on device")):
        m.conversao_rc, m.conversao_out = rc, out
        r = c.get("/video", params={"path": CLIP})
        assert r.status_code == 503, (rc, out)
    assert len(list(tmp_path.rglob("*.falhou"))) == 1
    m.conversao_rc = 0
    assert c.get("/video", params={"path": CLIP}).status_code == 200
    assert not list(tmp_path.rglob("*.tmp"))

    # Download que estoura o tempo não é "arquivo não encontrado".
    m.mega_get_rc = 124
    assert c.get("/video", params={"path": "/Celular/X/outro.MOV"}).status_code == 503
    assert len(list(tmp_path.rglob("*.falhou"))) == 1


def test_sidecar_capa_do_video_sai_do_mp4_convertido(sidecar_video, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    m, c = sidecar_video
    r = c.get("/thumb", params={"path": CLIP, "lado": 320})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-previa-cache"] == "miss"
    assert max(Image.open(io.BytesIO(r.content)).size) == 320
    # A capa sai do MP4 convertido (não do original), a ~1 s.
    capa = m.capas[-1]
    assert capa[capa.index("-ss") + 1] == "1"
    assert capa[capa.index("-i") + 1].endswith(".mp4")
    # A mesma descida deixou pronta a capa grande E o vídeo.
    r = c.get("/thumb", params={"path": CLIP, "lado": 1600})
    assert r.status_code == 200 and r.headers["x-previa-cache"] == "hit"
    r = c.get("/video", params={"path": CLIP})
    assert r.status_code == 200 and r.headers["x-previa-cache"] == "hit"
    assert m.baixados == 1 and len(m.conversoes) == 1

    # Vídeo mais curto que 1 s: sem quadro em 1 s, a capa é o quadro 0.
    m.duracao = 0.5
    r = c.get("/thumb", params={"path": "/Celular/X/curto.mp4", "lado": 320})
    assert r.status_code == 200
    assert [x[x.index("-ss") + 1] for x in m.capas[-2:]] == ["1", "0"]

    # Vídeo convertido pelo /video antes: a capa não baixa de novo.
    m.duracao = 30.0
    antes = m.baixados
    assert c.get("/video", params={"path": "/Celular/X/outro.MOV"}).status_code == 200
    assert c.get("/thumb", params={"path": "/Celular/X/outro.MOV", "lado": 320}).status_code == 200
    assert m.baixados == antes + 1


def test_sidecar_capa_da_grade_nao_converte_video_novo(sidecar_video):
    """Revisão de 29/09: uma grade de 11 vídeos novos enfileirava 11 conversões
    (uma por vez no servidor) e o vídeo clicado esperava atrás de todas. Com
    preparar=0 a capa que não existe dá 404 na hora, sem baixar nada."""
    m, c = sidecar_video
    r = c.get("/thumb", params={"path": CLIP, "lado": 320, "preparar": 0})
    assert r.status_code == 404
    assert m.baixados == 0 and m.conversoes == []
    # O play prepara; depois disso a capa da grade sai do MP4 já convertido.
    pytest.importorskip("PIL")  # a capa falsa é desenhada com o Pillow
    assert c.get("/video", params={"path": CLIP}).status_code == 200
    r = c.get("/thumb", params={"path": CLIP, "lado": 320, "preparar": 0})
    assert r.status_code == 200
    assert m.baixados == 1 and len(m.conversoes) == 1
    # Foto não é afetada pelo preparar=0 (gera normalmente).
    r = c.get("/thumb", params={"path": "/Celular/X/foto.jpg", "preparar": 0})
    assert r.status_code in (200, 422)

