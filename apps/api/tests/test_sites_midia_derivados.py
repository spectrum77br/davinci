"""A marca d'água e o cache das derivadas dos sites.

ffmpeg DE VERDADE (pulado se a máquina não tem): foto 1500x1500 e MP4 de 3 s
com áudio feitos pelo próprio ffmpeg (`lavfi`), passando pelo código real com o
sidecar falso — a marca tem de sair pequena, no canto inferior direito, e o
meio da imagem intacto. Depois, a varredura (teto, LRU, versão velha, tmp,
simulação), o aquecimento varrendo antes de medir, a conferência de mudança, a
máscara do access log e os PNGs dos logos.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import struct
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings
from app.routers import sites_midia as rota
from app.services import sites_midia_derivados as d
from app.services.mega_fotos import MegaError

SEM_FFMPEG = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "sites_midia_dir", str(tmp_path / "sites-midia"))
    monkeypatch.setattr(s, "sites_midia_disco_min_mb", 0)
    monkeypatch.setattr(s, "sites_midia_cache_mb", 2048)
    d.reiniciar_estado()
    yield
    d.reiniciar_estado()


def _cli(*cmd: str, texto: bool = False) -> subprocess.CompletedProcess:
    """ffmpeg/ffprobe locais, com argumentos do próprio teste."""
    return subprocess.run(cmd, check=True, capture_output=True, text=texto)  # noqa: S603


def _ffmpeg(*args: str) -> None:
    _cli("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args)


def _cinza(arquivo: Path, w: int, h: int) -> bytes:
    """Os pixels em tons de cinza (1 byte por pixel), escalados para w×h."""
    vf = f"scale={w}:{h},format=gray"
    return _cli(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(arquivo), "-vf", vf,
        "-f", "rawvideo", "-",
    ).stdout  # fmt: skip


def _diferenca(a: bytes, b: bytes, w: int, x0: int, y0: int, x1: int, y1: int) -> float:
    soma = n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            soma += abs(a[y * w + x] - b[y * w + x])
            n += 1
    return soma / n


def _dims(arquivo: Path) -> tuple[int, int]:
    out = _cli(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(arquivo), texto=True,
    ).stdout.strip()  # fmt: skip
    w, h = out.split(",")[:2]
    return int(w), int(h)


def _atomos(arquivo: Path) -> list[str]:
    dados = arquivo.read_bytes()
    i, tipos = 0, []
    while i + 8 <= len(dados):
        tam, tipo = struct.unpack(">I4s", dados[i : i + 8])
        tipos.append(tipo.decode("latin-1"))
        if tam == 1:
            tam = struct.unpack(">Q", dados[i + 8 : i + 16])[0]
        if tam < 8:
            break
        i += tam
    return tipos


# ─────────────── ffmpeg de verdade ───────────────


@pytest.mark.skipif(SEM_FFMPEG, reason="sem ffmpeg nesta máquina")
async def test_foto_real_sai_com_a_marca_no_lugar_certo(tmp_path, monkeypatch):
    fonte = tmp_path / "fonte.jpg"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1500x1500", "-frames:v", "1", "-q:v", "3",
            "-update", "1", str(fonte))  # fmt: skip
    assert d.dimensoes_jpeg(fonte.read_bytes()) == (1500, 1500)
    pedidos: list[dict[str, Any]] = []

    async def _sidecar(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        assert path == "/thumb"
        pedidos.append(params)
        return fonte.read_bytes()

    monkeypatch.setattr(d, "sidecar_bytes", _sidecar)
    await d.garantir_foto("charlots", "/Malas/ABS 20/M1/b005.20.jpg")
    g = d.grupo("charlots", "/Malas/ABS 20/M1/b005.20.jpg")
    grande, mini = d.pronto(g, "grande"), d.pronto(g, "mini")
    assert grande and mini
    assert grande[1] == "image/jpeg"
    assert mini[1] in ("image/webp", "image/jpeg")
    assert max(_dims(grande[0])) <= 1600 and _dims(grande[0]) == (1500, 1500)
    assert max(_dims(mini[0])) == 480
    meta = d.ler_meta(g)
    assert meta["versao"] == d.versao("charlots")
    assert (meta["largura"], meta["altura"], meta["falhou_em"]) == (1500, 1500, None)
    assert meta["fonte"]  # a impressão digital da prévia de 320
    assert [p["lado"] for p in pedidos] == [1600, 320]

    # A marca é a pílula pequena no canto INFERIOR DIREITO: 16% do lado menor
    # (240 px) a 38 px da direita e de baixo. Lá a imagem muda; no meio (onde
    # ficava a marca grande) e no canto superior esquerdo, não.
    assert (d.largura_logo("charlots", 1500, 1500), d.margem(1500, 1500)) == (240, 38)
    w = h = 300  # comparar em 1/5 da escala: pílula de 48×16 a 7,6 px das bordas
    antes, depois = _cinza(fonte, w, h), _cinza(grande[0], w, h)
    canto_marca = _diferenca(antes, depois, w, 248, 280, 289, 290)
    meio = _diferenca(antes, depois, w, 90, 120, 210, 200)
    canto_livre = _diferenca(antes, depois, w, 5, 5, 60, 60)
    em_cima_dir = _diferenca(antes, depois, w, 240, 5, 295, 60)
    assert canto_marca > 8, canto_marca
    assert meio < 3 and canto_livre < 3 and em_cima_dir < 3, (meio, canto_livre, em_cima_dir)


@pytest.mark.skipif(SEM_FFMPEG, reason="sem ffmpeg nesta máquina")
async def test_video_real_faststart_capas_audio_copiado_e_duracao(tmp_path, monkeypatch):
    fonte = tmp_path / "fonte.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(fonte),
    )  # fmt: skip

    async def _stream(path: str, *, params: dict[str, Any], timeout: float = 600.0):
        assert path == "/video" and params == {"path": "/Celular/F117/clip.mp4"}

        async def _pedacos():
            dados = fonte.read_bytes()
            for i in range(0, len(dados), 65536):
                yield dados[i : i + 65536]

        async def _fechar() -> None:
            return None

        return _pedacos(), _fechar

    async def _thumb(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        return b"capa-320"

    monkeypatch.setattr(d, "sidecar_stream", _stream)
    monkeypatch.setattr(d, "sidecar_bytes", _thumb)
    await d.gerar_video("uranyx", "/Celular/F117/clip.mp4")
    g = d.grupo("uranyx", "/Celular/F117/clip.mp4")
    assert d.video_pronto(g)
    mp4 = d.pronto(g, "video")[0]
    atomos = _atomos(mp4)
    assert atomos.index("moov") < atomos.index("mdat"), atomos
    assert _dims(mp4) == (720, 1280)
    assert _dims(d.pronto(g, "grande")[0]) == (720, 1280)
    assert max(_dims(d.pronto(g, "mini")[0])) == 480
    codecs = _cli(
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
        "-of", "csv=p=0", str(mp4), texto=True,
    ).stdout.split()  # fmt: skip
    assert "h264,video" in codecs and "aac,audio" in codecs, codecs
    meta = d.ler_meta(g)
    assert (meta["largura"], meta["altura"]) == (720, 1280)
    assert 2.8 <= meta["duracao"] <= 3.2
    # Nenhum tmp largado.
    assert not [p for p in g.dir.iterdir() if ".tmp." in p.name]
    # A marca está no vídeo, no canto inferior direito: pílula da Uranyx de
    # 144 px (20% de 720) a 18 px das bordas. O meio do quadro não muda.
    assert (d.largura_logo("uranyx", 720, 1280), d.margem(720, 1280)) == (144, 18)
    q1, q2 = tmp_path / "q1.png", tmp_path / "q2.png"
    _ffmpeg("-ss", "1.5", "-i", str(fonte), "-frames:v", "1", "-update", "1", str(q1))
    _ffmpeg("-ss", "1.5", "-i", str(mp4), "-frames:v", "1", "-update", "1", str(q2))
    a, b = _cinza(q1, 720, 1280), _cinza(q2, 720, 1280)
    canto_marca = _diferenca(a, b, 720, 562, 1245, 698, 1258)
    meio = _diferenca(a, b, 720, 160, 560, 560, 820)
    em_cima_esq = _diferenca(a, b, 720, 10, 10, 200, 200)
    assert canto_marca > 8, canto_marca
    assert meio < 4 and em_cima_esq < 4, (meio, em_cima_esq)


@pytest.mark.skipif(SEM_FFMPEG, reason="sem ffmpeg nesta máquina")
async def test_dimensoes_jpeg_baseline_progressivo_e_quebrado(tmp_path):
    jpg = tmp_path / "p.jpg"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1600x752", "-frames:v", "1",
            "-update", "1", str(jpg))  # fmt: skip
    dados = jpg.read_bytes()
    assert d.dimensoes_jpeg(dados) == (1600, 752)
    # O mesmo cabeçalho como SOF2 (progressivo, o que o sidecar grava acima de 320).
    assert b"\xff\xc0" in dados
    assert d.dimensoes_jpeg(dados.replace(b"\xff\xc0", b"\xff\xc2", 1)) == (1600, 752)
    assert d.dimensoes_jpeg(b"\x89PNG\r\n") is None
    assert d.dimensoes_jpeg(b"\xff\xd8\xff\xda\x00\x02") is None
    assert d.dimensoes_jpeg(b"\xff\xd8\xff\xe0\x00") is None


def test_largura_do_logo():
    assert d.largura_logo("charlots", 1500, 1500) == 240  # 16% do lado menor
    assert d.largura_logo("uranyx", 1600, 751) == 150  # 20% de 751, par
    assert d.largura_logo("uranyx", 1600, 1600) == 320
    assert d.largura_logo("uranyx", 720, 1280) == 144  # vídeo vertical
    assert d.largura_logo("charlots", 480, 480) == 96  # piso: 16% daria 76
    assert d.largura_logo("charlots", 150, 600) == 50  # piso limitado a 1/3 da largura
    assert d.largura_logo("charlots", 3, 3) == 2


def test_margem():
    assert d.margem(1500, 1500) == 38
    assert d.margem(1000, 1600) == 25
    assert d.margem(1600, 720) == 18
    assert d.margem(200, 200) == 8  # mínimo


# ─────────────── varredura ───────────────


def _grupo_falso(site: str, nome: str, kb: int, *, uso: float, versao: str | None = None):
    g = d.grupo(site, f"/Malas/X/{nome}")
    g.dir.mkdir(parents=True, exist_ok=True)
    for sufixo in ("mini.webp", "grande.jpg"):
        g.arq(sufixo).write_bytes(b"\x00" * (kb * 1024 // 2))
    meta = d._meta_nova(g)
    if versao:
        meta["versao"] = versao
    g.arq("json").write_text(json.dumps(meta))
    for p in g.dir.glob(g.k + ".*"):
        os.utime(p, (uso, uso))
    return g


def test_varrer_teto_lru_versao_velha_e_tmp(monkeypatch):
    monkeypatch.setattr(get_settings(), "sites_midia_cache_mb", 1)  # 1 MB
    agora = time.time()
    velhos = [_grupo_falso("charlots", f"v{i}.jpg", 200, uso=agora - 10000 + i) for i in range(3)]
    novos = [_grupo_falso("uranyx", f"n{i}.jpg", 200, uso=agora - 100 + i) for i in range(3)]
    outra_versao = _grupo_falso("charlots", "ov.jpg", 10, uso=agora, versao="0000000000")
    tmp_novo = velhos[0].dir / f"{velhos[0].k}.mp4.123-abcd.tmp.mp4"
    tmp_novo.write_bytes(b"\x00" * 1024)
    tmp_velho = novos[0].dir / f"{novos[0].k}.mp4.123-dcba.tmp.mp4"
    tmp_velho.write_bytes(b"\x00" * 1024)
    os.utime(tmp_velho, (agora - 7200, agora - 7200))

    r = d.varrer(agora)

    assert not outra_versao.arq("json").exists(), "versão velha sai sempre"
    assert tmp_novo.exists(), "tmp de quem gera agora fica"
    assert not tmp_velho.exists(), "tmp largado há mais de 1 h sai"
    # 6 grupos de ~200 KB num teto de 1 MB: saem os de uso mais antigo (v0, v1)
    # até ficar abaixo de 85% do teto; o resto fica.
    assert r["total"] <= int(MB * 0.85)
    restam = {g.k for g in velhos + novos if g.arq("json").exists()}
    assert restam == {velhos[2].k} | {g.k for g in novos}
    # O grupo sai INTEIRO (mini, grande e meta juntos).
    for g in velhos + novos:
        arquivos = [p for p in g.dir.glob(g.k + ".*") if ".tmp." not in p.name]
        assert len(arquivos) in (0, 3), arquivos


def test_varrer_ignora_o_atime_do_meta(monkeypatch):
    """Ler o meta (listagem, varredura) não pode contar como uso do grupo."""
    monkeypatch.setattr(get_settings(), "sites_midia_cache_mb", 1)
    agora = time.time()
    gs = [_grupo_falso("charlots", f"g{i}.jpg", 300, uso=agora - 1000 * (5 - i)) for i in range(5)]
    # O meta do mais velho foi lido agora há pouco.
    os.utime(gs[0].arq("json"), (agora, agora - 5000))
    d.varrer(agora)
    assert not gs[0].arq("json").exists()


def test_varrer_abaixo_do_teto_nao_apaga(monkeypatch):
    agora = time.time()
    gs = [_grupo_falso("charlots", f"a{i}.jpg", 50, uso=agora - i) for i in range(4)]
    r = d.varrer(agora)
    assert r["apagados"] == 0 and r["liberado"] == 0
    assert all(g.arq("json").exists() for g in gs)


def _bytes_dos(grupos: list[d.Grupo]) -> int:
    return sum(p.stat().st_size for g in grupos for p in g.dir.glob(g.k + ".*"))


def test_trocou_a_marca_a_versao_velha_sai_inteira_e_simular_so_conta(monkeypatch):
    """Muda uma constante (como a troca para o canto): os grupos gerados com a
    versão anterior saem de verdade na varredura — não ficam esperando o LRU."""
    agora = time.time()
    velhos = [_grupo_falso("charlots", f"v{i}.jpg", 100, uso=agora) for i in range(3)]
    velhos.append(_grupo_falso("uranyx", "v.jpg", 100, uso=agora))
    d._VERSAO.clear()
    monkeypatch.setattr(d, "MARGEM", d.MARGEM + 0.01)
    novos = [_grupo_falso("charlots", f"v{i}.jpg", 40, uso=agora - 5) for i in range(2)]
    assert not {g.k for g in novos} & {g.k for g in velhos}, "versão nova, chave nova"
    antes, tam_velho = d.tamanho_cache(), _bytes_dos(velhos)
    esperado = {"total": antes - tam_velho, "apagados": 3 * len(velhos), "liberado": tam_velho}

    assert d.varrer(agora, simular=True) == esperado
    assert d.tamanho_cache() == antes, "simular não apaga nada"
    assert all(g.arq("json").exists() for g in velhos + novos)

    assert d.varrer(agora) == esperado
    assert _bytes_dos(velhos) == 0, "a versão velha sai inteira"
    assert all(d.foto_pronta(g) and g.arq("json").exists() for g in novos)
    assert d.tamanho_cache() == antes - tam_velho
    assert d.varrer(agora) == {"total": antes - tam_velho, "apagados": 0, "liberado": 0}


def test_simular_tambem_conta_tmp_largado_e_lru_sem_apagar(monkeypatch):
    monkeypatch.setattr(get_settings(), "sites_midia_cache_mb", 1)
    agora = time.time()
    gs = [_grupo_falso("charlots", f"g{i}.jpg", 300, uso=agora - 1000 * (5 - i)) for i in range(5)]
    tmp = gs[0].dir / f"{gs[0].k}.mp4.1-abcd.tmp.mp4"
    tmp.write_bytes(b"\0" * 1000)
    os.utime(tmp, (agora - 7200, agora - 7200))
    simulado = d.varrer(agora, simular=True)
    assert tmp.exists() and all(g.arq("json").exists() for g in gs)
    assert simulado["apagados"] > 1 and simulado["total"] <= int(MB * 0.85)
    assert d.varrer(agora) == simulado
    assert not tmp.exists()


async def test_aquecimento_varre_antes_de_medir_o_cache(monkeypatch):
    """A versão velha ainda no disco contaria no teto: a varredura vem antes
    do `tamanho_cache`, e no `--dry-run` só simula."""
    from scripts.sites_midia_aquecer import aquecer

    ordem: list[str] = []
    varrer_de_verdade, tamanho_de_verdade = d.varrer, d.tamanho_cache

    def _varrer(agora: float | None = None, *, simular: bool = False) -> dict[str, int]:
        ordem.append(f"varrer simular={simular}")
        return varrer_de_verdade(agora, simular=simular)

    def _tamanho() -> int:
        ordem.append("tamanho_cache")
        return tamanho_de_verdade()

    monkeypatch.setattr(d, "varrer", _varrer)
    monkeypatch.setattr(d, "tamanho_cache", _tamanho)
    monkeypatch.setattr(get_settings(), "sites_midia_cache_mb", 3)
    velho = _grupo_falso("charlots", "velho.jpg", 2048, uso=time.time(), versao="0000000000")
    linhas: list[str] = []

    assert await aquecer([], so_fotos=False, dry_run=True, saida=linhas.append) == 0
    assert ordem == ["varrer simular=True", "tamanho_cache"]
    assert "varredura: seriam apagados 3 arquivos de versão velha/órfãos (2 MB)" in linhas[0]
    assert "cache=2 MB" in linhas[1]
    assert velho.arq("json").exists(), "dry-run não apaga"

    ordem.clear()
    linhas.clear()
    assert await aquecer([], so_fotos=False, dry_run=False, saida=linhas.append) == 0
    assert ordem == ["varrer simular=False", "tamanho_cache"]
    assert "varredura: 3 arquivos de versão velha/órfãos apagados (2 MB)" in linhas[0]
    assert "cache=0 MB" in linhas[1]
    assert not velho.arq("json").exists()


def test_grupo_sem_meta_ha_mais_de_1h_e_orfao():
    agora = time.time()
    g = d.grupo("uranyx", "/Celular/X/1.jpg")
    g.dir.mkdir(parents=True, exist_ok=True)
    g.arq("grande.jpg").write_bytes(b"x" * 100)
    os.utime(g.arq("grande.jpg"), (agora - 7200, agora - 7200))
    g2 = d.grupo("uranyx", "/Celular/X/2.jpg")
    g2.dir.mkdir(parents=True, exist_ok=True)
    g2.arq("grande.jpg").write_bytes(b"x" * 100)  # recém-escrito, meta a caminho
    d.varrer(agora)
    assert not g.arq("grande.jpg").exists()
    assert g2.arq("grande.jpg").exists()


def test_versao_muda_com_o_logo_e_com_as_constantes(monkeypatch, tmp_path):
    v = d.versao("charlots")
    assert len(v) == 10 and v != d.versao("uranyx")
    k = d.grupo("charlots", "/Malas/a.jpg").k
    for nome, outro in (("OPACIDADE", 0.5), ("MARGEM", 0.03), ("MARGEM_MIN_PX", 12),
                        ("LARGURA_MIN_PX", 120)):  # fmt: skip
        original = getattr(d, nome)
        d._VERSAO.clear()
        monkeypatch.setattr(d, nome, outro)
        assert d.versao("charlots") != v, nome
        assert d.grupo("charlots", "/Malas/a.jpg").k != k, nome
        d._VERSAO.clear()
        monkeypatch.setattr(d, nome, original)
        assert d.versao("charlots") == v, nome
    # O logo: mesmos nomes, outros bytes → outra versão.
    pasta = tmp_path / "marcas"
    pasta.mkdir()
    for arq in d.ASSETS_MARCAS.iterdir():
        (pasta / arq.name).write_bytes(arq.read_bytes())
    (pasta / "charlots.png").write_bytes((pasta / "charlots.png").read_bytes() + b"\0")
    d._VERSAO.clear()
    monkeypatch.setattr(d, "ASSETS_MARCAS", pasta)
    assert d.versao("charlots") != v
    assert len(d.versao("uranyx")) == 10


# ─────────────── conferência de mudança ───────────────


def _com_meta(caminho: str, fonte: str | None) -> d.Grupo:
    g = d.grupo("uranyx", caminho)
    g.dir.mkdir(parents=True, exist_ok=True)
    g.arq("grande.jpg").write_bytes(b"jpg")
    g.arq("mini.webp").write_bytes(b"webp")
    g.arq("json").write_text(
        json.dumps(d._meta_nova(g, fonte=fonte, conferido_em=int(time.time()) - 3 * 86400))
    )
    return g


async def test_conferencia_igual_so_renova_e_diferente_ou_404_apaga(monkeypatch):
    import hashlib

    impressao = hashlib.sha1(b"previa", usedforsecurity=False).hexdigest()
    respostas: dict[str, Any] = {
        "/Celular/X/igual.jpg": b"previa",
        "/Celular/X/trocada.jpg": b"outra previa",
        "/Celular/X/sumiu.mp4": MegaError("sidecar HTTP 404", 404),
        "/Celular/X/fora.jpg": MegaError("sidecar MEGA inacessível", 503),
    }

    async def _thumb(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        assert params["lado"] == 320 and params["preparar"] == 0
        r = respostas[params["path"]]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(d, "sidecar_bytes", _thumb)
    gs = {c: _com_meta(c, impressao) for c in respostas}

    assert await d.conferir("uranyx", "/Celular/X/igual.jpg") == "igual"
    meta = d.ler_meta(gs["/Celular/X/igual.jpg"])
    assert time.time() - meta["conferido_em"] < 60
    assert gs["/Celular/X/igual.jpg"].arq("grande.jpg").exists()

    assert await d.conferir("uranyx", "/Celular/X/trocada.jpg") == "trocado"
    assert not list(gs["/Celular/X/trocada.jpg"].dir.glob(gs["/Celular/X/trocada.jpg"].k + ".*"))

    assert await d.conferir("uranyx", "/Celular/X/sumiu.mp4") == "sumiu"
    assert not gs["/Celular/X/sumiu.mp4"].arq("json").exists()

    # Sidecar fora: não apaga nada, tenta outro dia.
    assert await d.conferir("uranyx", "/Celular/X/fora.jpg") == "erro"
    assert gs["/Celular/X/fora.jpg"].arq("grande.jpg").exists()


async def test_agendar_conferencia_so_depois_de_24h_e_uma_vez(monkeypatch):
    chamadas: list[str] = []

    async def _conferir(site: str, caminho: str) -> str:
        chamadas.append(caminho)
        return "igual"

    monkeypatch.setattr(d, "conferir", _conferir)
    agora = int(time.time())
    assert not d.agendar_conferencia("uranyx", "/a.jpg", {"conferido_em": agora - 3600})
    assert not d.agendar_conferencia("uranyx", "/a.jpg", None)
    assert d.agendar_conferencia("uranyx", "/a.jpg", {"conferido_em": agora - 90000})
    assert not d.agendar_conferencia("uranyx", "/a.jpg", {"conferido_em": agora - 90000})
    await asyncio.sleep(0.05)
    assert chamadas == ["/a.jpg"]


# ─────────────── fila de vídeo ───────────────


async def test_fila_de_video_nao_repete_e_libera_depois(monkeypatch):
    gerados: list[str] = []
    liberar = asyncio.Event()

    async def _gerar(site: str, caminho: str) -> None:
        gerados.append(caminho)
        await liberar.wait()

    monkeypatch.setattr(d, "gerar_video", _gerar)
    assert d.enfileirar_video("uranyx", "/Celular/X/a.mp4")
    assert not d.enfileirar_video("uranyx", "/Celular/X/a.mp4"), "já está na fila"
    assert d.enfileirar_video("uranyx", "/Celular/X/b.mp4")
    await asyncio.sleep(0.05)
    assert gerados == ["/Celular/X/a.mp4"], "um por vez"
    liberar.set()
    await asyncio.sleep(0.05)
    assert gerados == ["/Celular/X/a.mp4", "/Celular/X/b.mp4"]
    # Saiu do conjunto: a próxima listagem pode pôr de novo (falha da hora).
    assert d.enfileirar_video("uranyx", "/Celular/X/a.mp4")


async def test_trava_de_video_e_exclusiva():
    ordem: list[str] = []

    async def _job(nome: str) -> None:
        async with d.trava_de_video():
            ordem.append(f"{nome}+")
            await asyncio.sleep(0.6)
            ordem.append(f"{nome}-")

    await asyncio.gather(_job("a"), _job("b"))
    assert ordem in (["a+", "a-", "b+", "b-"], ["b+", "b-", "a+", "a-"])


# ─────────────── access log, logos ───────────────


def test_mascara_do_access_log():
    rota.mascarar_link_no_access_log()
    log = logging.getLogger("uvicorn.access")
    registro = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/sites/midia/a/QUJDREVGR0hJSktMTU5PUA?baixar=1", "1.1", 200),
        None,
    )  # fmt: skip
    for filtro in log.filters:
        filtro.filter(registro)
    assert registro.args[2] == "/api/sites/midia/a/***?baixar=1"
    assert "QUJD" not in registro.getMessage()


def _png_rgba(caminho: Path) -> tuple[int, int, bytes]:
    """Decodifica PNG RGBA 8 bits sem Pillow (a api não tem): (w, h, pixels)."""
    dados = caminho.read_bytes()
    assert dados[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat = 8, b""
    w = h = 0
    while i < len(dados):
        tam, tipo = struct.unpack(">I4s", dados[i : i + 8])
        corpo = dados[i + 8 : i + 8 + tam]
        if tipo == b"IHDR":
            w, h, prof, cor, _c, _f, entrelace = struct.unpack(">IIBBBBB", corpo)
            assert (prof, cor, entrelace) == (8, 6, 0), "PNG RGBA 8 bits, sem entrelaçar"
        elif tipo == b"IDAT":
            idat += corpo
        i += 12 + tam
    cru, bpp, linha = zlib.decompress(idat), 4, w * 4
    saida = bytearray()
    anterior = bytearray(linha)
    pos = 0
    for _ in range(h):
        filtro, atual = cru[pos], bytearray(cru[pos + 1 : pos + 1 + linha])
        pos += 1 + linha
        for x in range(linha):
            a = atual[x - bpp] if x >= bpp else 0
            b = anterior[x]
            c = anterior[x - bpp] if x >= bpp else 0
            if filtro == 1:
                atual[x] = (atual[x] + a) & 0xFF
            elif filtro == 2:
                atual[x] = (atual[x] + b) & 0xFF
            elif filtro == 3:
                atual[x] = (atual[x] + (a + b) // 2) & 0xFF
            elif filtro == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                atual[x] = (atual[x] + pred) & 0xFF
        saida += atual
        anterior = atual
    return w, h, bytes(saida)


@pytest.mark.parametrize("site", ["charlots", "uranyx"])
def test_logos_existem_rgba_com_branco_opaco_e_transparencia(site: str):
    arquivo = d.ASSETS_MARCAS / d.MARCAS[site][0]
    assert arquivo.is_file()
    w, h, px = _png_rgba(arquivo)
    assert 0 < w <= 1400 and h > 0
    brancos = transparentes = capsula = 0
    for i in range(0, len(px), 4 * 7):  # amostra
        r, g, b, a = px[i : i + 4]
        if a == 255 and r == g == b == 255:
            brancos += 1
        elif a == 0:
            transparentes += 1
        elif r == g == b == 0 and 92 <= a <= 102:  # preto a 38%
            capsula += 1
    assert brancos > 100, "o logo tem de ser branco puro e opaco nos glifos"
    assert transparentes > 100, "e transparente fora da cápsula (os cantos arredondados da pílula)"
    assert capsula > brancos, "a cápsula preta translúcida em volta (lê no fundo branco)"


async def test_video_do_sidecar_grande_demais_nao_enche_o_disco(monkeypatch):
    """Passou de VIDEO_FONTE_MAX_MB: para no meio, apaga o tmp e anota a falha."""
    monkeypatch.setattr(d, "VIDEO_FONTE_MAX_MB", 1)
    fechou: list[bool] = []

    async def _stream(path: str, *, params: dict[str, Any], timeout: float = 600.0):
        async def _pedacos():
            for _ in range(64):  # 4 MB em pedaços de 64 KB
                yield b"\0" * 65536

        async def _fechar() -> None:
            fechou.append(True)

        return _pedacos(), _fechar

    monkeypatch.setattr(d, "sidecar_stream", _stream)
    with pytest.raises(d.MidiaIndisponivel):
        await d.gerar_video("uranyx", "/Celular/F117/enorme.mp4")
    g = d.grupo("uranyx", "/Celular/F117/enorme.mp4")
    assert fechou == [True]
    assert not [p for p in g.dir.iterdir() if ".tmp." in p.name]
    assert d.falhou_recente(d.ler_meta(g))
