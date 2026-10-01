"""Fotos e vídeos com marca d'água para os sites — a porta, o recorte, a
listagem, o link cifrado e os bytes.

Mesma ordem dos testes do estoque: primeiro o que trava a porta, depois o
recorte de cada site (o token diz o site; o SKU só estreita dentro dele; a
pasta tem de estar na raiz do site), a listagem (o que entra e o que NUNCA
sai na resposta), o link (qualquer adulteração é o mesmo 404) e os bytes.

O sidecar MEGA é falso: `sidecar_request` (listagem) do serviço e
`sidecar_bytes`/`sidecar_stream`/ffmpeg das derivadas, por monkeypatch.
"""

from __future__ import annotations

import asyncio
import json
import time
import unicodedata
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.routers import sites_midia as rota
from app.services import sites_midia as midia
from app.services import sites_midia_derivados as derivados
from app.services.mega_fotos import MegaError
from app.services.rate_limit import RateLimitError

URL = "/api/sites/midia"
TOK_CH = "tok-site-charlots-0123456789"
TOK_UR = "tok-site-uranyx-9876543210"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


CH = _auth(TOK_CH)
UR = _auth(TOK_UR)

# JPEG mínimo com o marcador SOF0 (1500x1500): o bastante para o parser de
# dimensões e para o ffmpeg falso.
_JPEG = (
    b"\xff\xd8\xff\xc0\x00\x11\x08\x05\xdc\x05\xdc\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9"
)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "sites_estoque_tokens", f"{TOK_CH}:charlots, {TOK_UR}:uranyx")
    monkeypatch.setattr(s, "sites_midia_dir", str(tmp_path / "sites-midia"))
    monkeypatch.setattr(s, "sites_midia_disco_min_mb", 0)
    midia.limpar_caches()
    derivados.reiniciar_estado()
    yield
    midia.limpar_caches()
    derivados.reiniciar_estado()


@pytest.fixture(autouse=True)
def chamadas_do_limite(monkeypatch) -> list[str]:
    """Sem Redis nos testes: o limite vira um contador das chaves pedidas."""
    chaves: list[str] = []

    async def _ok(*, key: str, limit: int, window_seconds: int) -> int:
        chaves.append(key)
        return limit

    monkeypatch.setattr(rota, "sliding_window_check", _ok)
    return chaves


class Sidecar:
    """O `/files` do sidecar: {pasta: {"imagens": [...], "videos": [...]}}."""

    def __init__(self) -> None:
        self.pastas: dict[str, dict[str, list[str]]] = {}
        self.rc_falho: set[str] = set()
        self.fora: set[str] = set()
        self.pedidos: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, *, params=None, timeout=120.0, **_k):
        assert method == "GET" and path == "/files"
        pasta, tipo = params["path"], params["tipo"]
        self.pedidos.append((pasta, tipo))
        if pasta in self.fora:
            raise MegaError("sidecar MEGA inacessível", 503)
        if pasta in self.rc_falho:
            return {"rc": 1, "out": "not found", "arquivos": []}
        nomes = self.pastas.get(pasta, {}).get(tipo, [])
        return {
            "rc": 0,
            "arquivos": [{"nome": n, "ext": n.rsplit(".", 1)[-1].lower()} for n in nomes],
        }


@pytest.fixture
def sidecar(monkeypatch) -> Sidecar:
    s = Sidecar()
    monkeypatch.setattr(midia, "sidecar_request", s.request)
    return s


@pytest.fixture
def fila(monkeypatch) -> list[tuple[str, str]]:
    """A fila de vídeo, sem gerar nada: só anota o que entrou."""
    entrou: list[tuple[str, str]] = []

    def _enfileirar(site: str, caminho: str) -> bool:
        entrou.append((site, caminho))
        return True

    monkeypatch.setattr(derivados, "enfileirar_video", _enfileirar)
    return entrou


async def _produto(
    db: AsyncSession,
    make_user,
    *,
    sku: str,
    pasta: str | None,
    segmento: str = "teste",
    fotos: int | None = 10,
):
    from app.models.pricing import PricingProduct
    from app.models.segment import Segment

    dono = await make_user()
    seg = Segment(user_id=dono.id, name=segmento, slug=f"seg-{uuid4().hex[:8]}")
    db.add(seg)
    await db.flush()
    db.add(
        PricingProduct(
            user_id=dono.id,
            segment_id=seg.id,
            sku=sku,
            name=f"Produto {sku[:40]}",
            fotos_path=pasta,
            fotos_count=fotos,
        )
    )
    await db.commit()


async def _listar(client: AsyncClient, headers: dict[str, str], *skus: str):
    q = "&".join(f"sku={quote(s)}" for s in skus)
    return await client.get(f"{URL}?{q}" if q else URL, headers=headers)


def _semear(site: str, caminho: str, *, video: bool = False, **meta: Any) -> derivados.Grupo:
    """Põe no cache as derivadas de um item, como se já tivessem sido geradas."""
    g = derivados.grupo(site, caminho)
    g.dir.mkdir(parents=True, exist_ok=True)
    g.arq("mini.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 200)
    g.arq("grande.jpg").write_bytes(_JPEG + b"\x00" * 500)
    if video:
        g.arq("mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40)
    m = derivados._meta_nova(g, largura=1500, altura=1500, fonte="f" * 40)
    m.update(meta)
    g.arq("json").write_text(json.dumps(m))
    return g


def _ids(r) -> list[str]:
    return [i["id"] for i in r.json()["itens"]]


# ─────────────── a porta ───────────────


async def test_sem_token_e_token_errado_401(client: AsyncClient, sidecar: Sidecar):
    for headers in ({}, _auth("chute"), {"Authorization": TOK_CH}, _auth(TOK_CH[:-1])):
        r = await client.get(f"{URL}?sku=b005.20", headers=headers)
        assert r.status_code == 401
        assert r.json()["detail"] == {"code": "sites_nao_autorizado"}
    assert sidecar.pedidos == []


async def test_sem_configuracao_fechada(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "sites_estoque_tokens", "")
    assert (await _listar(client, CH, "b005.20")).status_code == 401


async def test_422_sem_sku_sku_invalido_e_demais(client: AsyncClient, sidecar: Sidecar):
    r = await client.get(URL, headers=CH)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sites_sku_invalido"
    for ruim in ("b005 20", "b005.20\n", "", "-b005", "b" * 101, "../x"):
        r = await client.get(URL, params={"sku": ruim}, headers=CH)
        assert r.status_code == 422, ruim
        assert r.json()["detail"]["code"] == "sites_sku_invalido"
    r = await client.get(URL, params=[("sku", f"b{i:03d}.20") for i in range(201)], headers=CH)
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "sites_skus_demais", "max": 200}
    # 200 em ponto passa.
    r = await client.get(URL, params=[("sku", f"b{i:03d}.20") for i in range(200)], headers=CH)
    assert r.status_code == 200


async def test_429_vira_retry_after(client: AsyncClient, monkeypatch, sidecar: Sidecar):
    async def _estourou(*, key: str, limit: int, window_seconds: int) -> int:
        assert key == "sites_midia:rl:charlots" and limit == 60
        raise RateLimitError(retry_after=17)

    monkeypatch.setattr(rota, "sliding_window_check", _estourou)
    r = await _listar(client, CH, "b005.20")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "17"
    assert r.json()["detail"] == {"code": "sites_limite"}


async def test_redis_fora_falha_aberto(client: AsyncClient, monkeypatch, sidecar: Sidecar):
    async def _caiu(**_k):
        raise ConnectionError("redis")

    monkeypatch.setattr(rota, "sliding_window_check", _caiu)
    assert (await _listar(client, CH, "b005.20")).status_code == 200


# ─────────────── o recorte ───────────────


async def test_uranyx_nao_lista_pasta_de_mala(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    """`a0NN` está no recorte dos dois sites; a PASTA diz de quem é."""
    await _produto(db, make_user, sku="a006", pasta="/Malas/Acessorios")
    sidecar.pastas["/Malas/Acessorios"] = {"imagens": ["cadeado.jpg"]}
    r = await _listar(client, UR, "a006")
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert sidecar.pedidos == []
    r = await _listar(client, CH, "a006")
    assert r.json()["total"] == 1


async def test_sku_de_outro_site_e_ignorado(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="dg048", pasta="/Celular/Fossibot F117")
    sidecar.pastas["/Celular/Fossibot F117"] = {"imagens": ["1.jpg"]}
    r = await _listar(client, CH, "dg048", "b005.20")
    assert r.status_code == 200 and r.json()["total"] == 0
    assert sidecar.pedidos == []
    assert (await _listar(client, UR, "dg048")).json()["total"] == 1


async def test_token_exato_b005_2_nao_casa_b005_20(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="b005.20, b006.20,b005.20+a075", pasta="/Malas/ABS 20")
    sidecar.pastas["/Malas/ABS 20"] = {"imagens": ["a.jpg"]}
    assert (await _listar(client, CH, "b005.2")).json()["total"] == 0
    assert (await _listar(client, CH, "b005")).json()["total"] == 0
    # Com espaço depois da vírgula, maiúsculas e o kit com `+`: casam.
    assert (await _listar(client, CH, "B006.20")).json()["total"] == 1
    midia.limpar_caches()
    assert (await _listar(client, CH, "b005.20+a075")).json()["total"] == 1


async def test_lote_cai_para_a_base(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="dg053", pasta="/Celular/Oukitel")
    sidecar.pastas["/Celular/Oukitel"] = {"imagens": ["1.jpg"]}
    assert (await _listar(client, UR, "dg053.cd")).json()["total"] == 1
    # Lote desconhecido não cai.
    assert (await _listar(client, UR, "dg053.xx")).json()["total"] == 0


async def test_segmento_apple_fica_de_fora(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="dg900", pasta="/Celular/apple iphone", segmento="Apple")
    sidecar.pastas["/Celular/apple iphone"] = {"imagens": ["1.jpg"]}
    assert (await _listar(client, UR, "dg900")).json()["total"] == 0
    assert sidecar.pedidos == []


async def test_pasta_fora_da_raiz_fica_de_fora(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="b001.20", pasta="/Financeiro/notas")
    await _produto(db, make_user, sku="b002.20", pasta="/Malas2/ABS")  # prefixo não é raiz
    await _produto(db, make_user, sku="b003.20", pasta="/Malas/_detalhes/M1")
    await _produto(db, make_user, sku="b004.20", pasta="/Malas/Referência concorrente")
    for p in (
        "/Financeiro/notas",
        "/Malas2/ABS",
        "/Malas/_detalhes/M1",
        "/Malas/Referência concorrente",
    ):
        sidecar.pastas[p] = {"imagens": ["1.jpg"]}
    r = await _listar(client, CH, "b001.20", "b002.20", "b003.20", "b004.20")
    assert r.json()["total"] == 0
    assert sidecar.pedidos == []


async def test_dedupe_de_pasta_ordem_do_pedido_e_teto_de_12(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    for i in range(14):
        await _produto(db, make_user, sku=f"b{i:03d}.20,b{i:03d}.24", pasta=f"/Malas/P{i:02d}")
        sidecar.pastas[f"/Malas/P{i:02d}"] = {"imagens": ["x.jpg"]}
    pedidos = ["b013.20", "b013.24"] + [f"b{i:03d}.20" for i in range(13)]
    r = await _listar(client, CH, *pedidos)
    assert r.json()["total"] == 12
    pastas = [p for p, t in sidecar.pedidos if t == "imagens"]
    assert len(pastas) == 12 and pastas[0] == "/Malas/P13"


# ─────────────── a listagem ───────────────


async def test_o_que_fica_de_fora(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="uaf001m1.110", pasta="/uranyx/airfryer vidro UAF001 M1")
    nfc = unicodedata.normalize("NFC", "Imagem 1 gerada - cópia 2.png")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    sidecar.pastas["/uranyx/airfryer vidro UAF001 M1"] = {
        "imagens": [
            "ok.jpg",
            "Embalagens/caixa.jpg",
            "embalagem/frente.png",
            "_ocultos/a.jpg",
            " _rascunho/b.jpg",
            "referencia/ninja.webp",
            "Referência/s-l1600.webp",
            "Concorrentes/x.jpg",
            "Não usar/y.jpg",
            "fotos internas/z.jpg",  # "internas" não é "interno": fica
            "material interno/w.jpg",
            "avariadas/q.jpg",
            "notas.txt",
            "sem_extensao",
            "a//b.jpg",
            "../fora.jpg",
            "x*.jpg",
            nfc,
            nfd,
        ],
        "videos": ["clip.mp4", "caixa.pdf", "Embalagens/unbox.mp4"],
    }
    r = await _listar(client, UR, "uaf001m1.110")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["fotos"], corpo["videos"]) == (3, 1), corpo
    esperado = [
        midia.item_id("uranyx", f"/uranyx/airfryer vidro UAF001 M1/{n}")
        for n in ("fotos internas/z.jpg", nfc, "ok.jpg", "clip.mp4")
    ]
    assert sorted(_ids(r)) == sorted(esperado)


async def test_ordem_natural_fotos_antes_dos_videos_e_ordem_das_pastas(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="dg060", pasta="/Celular/B")
    await _produto(db, make_user, sku="dg061", pasta="/Celular/A")
    sidecar.pastas["/Celular/B"] = {"imagens": ["10.jpg", "2.jpg", "1.jpg"], "videos": ["v.mp4"]}
    sidecar.pastas["/Celular/A"] = {"imagens": ["z.jpg"]}
    r = await _listar(client, UR, "dg060", "dg061")
    caminhos = [
        "/Celular/B/1.jpg",
        "/Celular/B/2.jpg",
        "/Celular/B/10.jpg",
        "/Celular/B/v.mp4",
        "/Celular/A/z.jpg",
    ]
    assert _ids(r) == [midia.item_id("uranyx", c) for c in caminhos]
    assert [i["tipo"] for i in r.json()["itens"]] == ["foto"] * 3 + ["video", "foto"]


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("M1 listrada/b005 M1 listrada preto/b005.20.jpg", ["b005"]),
        ("M3 chanfrada/b025 M3 chanfrada preto  /20B025.jpg", ["b025"]),
        ("P1 brilho listrada/b057 P1 brilho listrada preto/14.20.b057.jpg", ["b057"]),
        ("M5 mista/b045 M5 mista preto  /B045.12.jpg", ["b045"]),
        ("mochila/bp003 preta/bp003.jpg", ["bp003"]),
        ("b0051.jpg", []),
        ("ab005.jpg", []),
    ],
)
def test_codigos_nos_formatos_de_nome_de_mala(nome: str, esperado: list[str]):
    assert midia.codigos("charlots", nome) == esperado


def test_codigos_so_na_charlots():
    """Na Uranyx o nome é livre: um uuid de vídeo daria um `b512` fantasma."""
    assert midia.codigos("uranyx", "hf_20260915_211922_ed806f71-4520-b512-a7b4.mp4") == []


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Black/画板 1.jpg", ["preto"]),
        ("white background images/1.jpg", []),
        ("White Background Images/Orange/2.jpg", ["laranja"]),
        ("F112 Pro 5G Azul sem marca.jpg", ["azul"]),
        ("b015 M1 listrada branca /b015.20.png", ["branco"]),
        ("golden color/1.jpg", ["dourado"]),
        ("Black (2)/Blue/1.jpg", ["preto", "azul"]),
        ("Prateada.jpg", ["prata"]),
        ("predicado/ored.jpg", []),  # palavra inteira: "red" dentro de outra não conta
    ],
)
def test_cores(nome: str, esperado: list[str]):
    assert midia.cores(nome) == esperado


async def test_resposta_sem_nome_de_arquivo_nem_de_pasta(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar, fila
):
    await _produto(db, make_user, sku="b005.20", pasta="/Malas/ABS 20")
    sidecar.pastas["/Malas/ABS 20"] = {
        "imagens": [
            "M1 listrada/b005 M1 listrada preto/b005.20.jpg",
            "M1 listrada/b015 M1 listrada branca /画板 1.jpg",
        ],
        "videos": ["Videos/hf_20260915_211922.mp4"],
    }
    r = await _listar(client, CH, "b005.20")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "private, no-store"
    assert r.headers["Vary"] == "Authorization"
    texto = r.text
    for proibido in (
        "ABS 20",
        "Malas",
        "M1 listrada",
        "b005.20",
        "画板",
        "hf_2026",
        "Videos",
        "listrada",
        ".jpg",
        ".mp4",
        "fotos_path",
        "pasta",
        "nome",
    ):
        assert proibido not in texto, proibido
    corpo = r.json()
    assert set(corpo) == {
        "site",
        "gerado_em",
        "links_validos_ate",
        "links_validos_s",
        "total",
        "fotos",
        "videos",
        "preparando",
        "itens",
    }
    assert corpo["site"] == "charlots"
    assert 3 * 3600 < corpo["links_validos_s"] <= 6 * 3600
    foto = corpo["itens"][0]
    assert set(foto) == {
        "id",
        "tipo",
        "estado",
        "codigos",
        "cores",
        "largura",
        "altura",
        "duracao",
        "urls",
    }
    assert foto["estado"] == "pronto" and foto["codigos"] == ["b005"]
    assert foto["cores"] == ["preto"]
    assert set(foto["urls"]) == {"mini", "grande"}
    for url in foto["urls"].values():
        assert url.startswith("/api/sites/midia/a/") and 20 <= len(url) - 19 <= 2000
    # A mesma URL na próxima listagem dentro da janela (o navegador reaproveita o cache).
    r2 = await _listar(client, CH, "b005.20")
    assert r2.json()["itens"][0]["urls"] == foto["urls"]


async def test_video_sem_derivada_fica_preparando_e_entra_na_fila(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar, fila
):
    await _produto(db, make_user, sku="dg048", pasta="/Celular/F117")
    sidecar.pastas["/Celular/F117"] = {"videos": ["a.mp4", "b.mov"]}
    _semear("uranyx", "/Celular/F117/b.mov", video=True, duracao=15.1, largura=720, altura=1280)
    r = await _listar(client, UR, "dg048")
    corpo = r.json()
    assert (corpo["videos"], corpo["preparando"]) == (2, 1)
    a, b = corpo["itens"]
    assert a["estado"] == "preparando" and a["urls"] == {} and a["duracao"] is None
    assert b["estado"] == "pronto" and set(b["urls"]) == {"mini", "grande", "video"}
    assert (b["largura"], b["altura"], b["duracao"]) == (720, 1280, 15.1)
    assert fila == [("uranyx", "/Celular/F117/a.mp4")]


async def test_falha_recente_omite_o_item_e_a_velha_volta(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar, fila
):
    await _produto(db, make_user, sku="dg048", pasta="/Celular/F117")
    sidecar.pastas["/Celular/F117"] = {"imagens": ["ok.jpg", "quebrada.jpg", "velha.jpg"]}
    agora = int(time.time())
    for nome, quando in (("quebrada.jpg", agora - 3600), ("velha.jpg", agora - 8 * 86400)):
        g = derivados.grupo("uranyx", f"/Celular/F117/{nome}")
        g.dir.mkdir(parents=True, exist_ok=True)
        g.arq("json").write_text(json.dumps(derivados._meta_nova(g, falhou_em=quando)))
    r = await _listar(client, UR, "dg048")
    assert r.json()["total"] == 2
    assert midia.item_id("uranyx", "/Celular/F117/quebrada.jpg") not in _ids(r)


async def test_pasta_com_rc_pula_so_ela_e_todas_dao_503(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="dg060", pasta="/Celular/Boa")
    await _produto(db, make_user, sku="dg061", pasta="/Celular/Renomeada")
    await _produto(db, make_user, sku="dg062", pasta="/Celular/Fora")
    sidecar.pastas["/Celular/Boa"] = {"imagens": ["1.jpg"]}
    sidecar.rc_falho.add("/Celular/Renomeada")
    sidecar.fora.add("/Celular/Fora")
    r = await _listar(client, UR, "dg060", "dg061", "dg062")
    assert r.status_code == 200 and r.json()["total"] == 1
    r = await _listar(client, UR, "dg061", "dg062")
    assert r.status_code == 503
    assert r.json()["detail"] == {"code": "midia_indisponivel"}


async def test_listagem_fica_em_cache_por_pasta(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar
):
    await _produto(db, make_user, sku="dg060", pasta="/Celular/Boa")
    sidecar.pastas["/Celular/Boa"] = {"imagens": ["1.jpg"]}
    await _listar(client, UR, "dg060")
    await _listar(client, UR, "dg060")
    assert sidecar.pedidos.count(("/Celular/Boa", "imagens")) == 1


async def test_listagem_agenda_conferencia_de_item_velho(
    client: AsyncClient, db: AsyncSession, make_user, sidecar: Sidecar, monkeypatch
):
    await _produto(db, make_user, sku="dg060", pasta="/Celular/Boa")
    sidecar.pastas["/Celular/Boa"] = {"imagens": ["velho.jpg", "novo.jpg"]}
    _semear("uranyx", "/Celular/Boa/velho.jpg", conferido_em=int(time.time()) - 2 * 86400)
    _semear("uranyx", "/Celular/Boa/novo.jpg")
    conferidos: list[str] = []

    async def _conferir(site: str, caminho: str) -> str:
        conferidos.append(caminho)
        return "igual"

    monkeypatch.setattr(derivados, "conferir", _conferir)
    await _listar(client, UR, "dg060")
    await asyncio.sleep(0.05)
    assert conferidos == ["/Celular/Boa/velho.jpg"]


# ─────────────── o link ───────────────


def _link(site: str, pasta: str, nome: str, variante: str = "grande", exp: int | None = None):
    exp = midia.expiracao(time.time()) if exp is None else exp
    return f"/api/sites/midia/a/{midia.cifrar(site, {'s': site, 'p': pasta, 'n': nome, 'v': variante, 'e': exp})}"  # noqa: E501


async def test_link_valido_da_200(client: AsyncClient):
    _semear("charlots", "/Malas/ABS 20/M1/b005.20.jpg")
    r = await client.get(_link("charlots", "/Malas/ABS 20", "M1/b005.20.jpg"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"


async def test_link_adulterado_ou_fora_da_regra_da_404(client: AsyncClient, monkeypatch):
    _semear("charlots", "/Malas/ABS 20/a.jpg")
    _semear("uranyx", "/Celular/F117/a.jpg")
    bom = _link("charlots", "/Malas/ABS 20", "a.jpg")
    token = bom.rsplit("/", 1)[1]
    agora = int(time.time())
    trocado = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
    ruins = {
        "payload trocado": f"/api/sites/midia/a/{trocado}",
        "cortado": f"/api/sites/midia/a/{token[:-4]}",
        "lixo": "/api/sites/midia/a/" + "x" * 40,
        "assinado por outro site": "/api/sites/midia/a/"
        + midia.cifrar(
            "uranyx",
            {
                "s": "charlots",
                "p": "/Malas/ABS 20",
                "n": "a.jpg",
                "v": "grande",
                "e": midia.expiracao(agora),
            },
        ),
        "vencido": _link("charlots", "/Malas/ABS 20", "a.jpg", exp=agora - 1),
        "e a mais de 7 h": _link("charlots", "/Malas/ABS 20", "a.jpg", exp=agora + 7 * 3600 + 60),
        "p fora da raiz": _link("charlots", "/Celular/F117", "a.jpg"),
        "p com _": _link("charlots", "/Malas/_detalhes", "a.jpg"),
        "n de embalagem": _link("charlots", "/Malas/ABS 20", "Embalagens/a.jpg"),
        "n com ..": _link("charlots", "/Malas/ABS 20", "../a.jpg"),
        "video com nome de foto": _link("charlots", "/Malas/ABS 20", "a.jpg", "video"),
        "variante desconhecida": _link("charlots", "/Malas/ABS 20", "a.jpg", "original"),
    }
    for motivo, url in ruins.items():
        r = await client.get(url)
        assert r.status_code == 404, motivo
        assert r.json()["detail"] == {"code": "midia_link_invalido"}, motivo
    assert (await client.get(bom)).status_code == 200

    # Token do site trocado (revogação): todos os links emitidos morrem.
    monkeypatch.setattr(
        get_settings(), "sites_estoque_tokens", f"tok-novo-charlots-000:charlots,{TOK_UR}:uranyx"
    )
    r = await client.get(bom)
    assert r.status_code == 404
    assert r.json()["detail"] == {"code": "midia_link_invalido"}


async def test_link_nao_carrega_nome_legivel():
    """Cifrado, não só assinado: nem em base64 o nome chega à página."""
    import base64

    url = _link("uranyx", "/Celular/Fossibot F117", "画板 21.jpg")
    token = url.rsplit("/", 1)[1]
    bruto = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    for pedaco in (b"Fossibot", "画板".encode(), b"Celular", b'"p"'):
        assert pedaco not in bruto


# ─────────────── os bytes ───────────────


async def test_hit_traz_os_cabecalhos_certos(client: AsyncClient):
    _semear("charlots", "/Malas/ABS 20/a.jpg")
    r = await client.get(_link("charlots", "/Malas/ABS 20", "a.jpg", "mini"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "private, max-age=21600"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["accept-ranges"] == "bytes"
    assert "etag" in r.headers
    # Parâmetro desconhecido (o novo pedido do JS) é ignorado.
    url = _link("charlots", "/Malas/ABS 20", "a.jpg", "mini")
    assert (await client.get(url + "?r=1")).status_code == 200


async def test_baixar_com_nome_valido_e_com_nome_injetado(client: AsyncClient):
    _semear("charlots", "/Malas/ABS 20/a.jpg")
    mini = _link("charlots", "/Malas/ABS 20", "a.jpg", "mini")
    r = await client.get(mini, params={"baixar": "1", "nome": "mala-20-abs-03"})
    assert r.status_code == 200
    # `baixar` com `mini` serve o `grande`.
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith('attachment; filename="mala-20-abs-03.jpg"')
    r = await client.get(mini, params={"baixar": "1", "nome": "x\r\nSet-Cookie: a=b"})
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert r.headers["content-disposition"].startswith('attachment; filename="charlots-midia.jpg"')
    r = await client.get(mini, params={"baixar": "1", "nome": "../../etc/passwd"})
    assert 'filename="charlots-midia.jpg"' in r.headers["content-disposition"]


async def test_range_no_mp4_da_206(client: AsyncClient):
    g = _semear("uranyx", "/Celular/F117/v.mp4", video=True)
    tamanho = g.arq("mp4").stat().st_size
    url = _link("uranyx", "/Celular/F117", "v.mp4", "video")
    r = await client.get(url, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{tamanho}"
    assert r.headers["content-type"] == "video/mp4"
    assert len(r.content) == 100
    r = await client.get(url, params={"baixar": "1", "nome": "f117-video-01"})
    assert r.headers["content-disposition"].startswith('attachment; filename="f117-video-01.mp4"')


class FfmpegFalso:
    """Escreve as saídas que o comando pede; conta as gerações de foto e vídeo."""

    def __init__(self) -> None:
        self.fotos = 0
        self.videos = 0

    async def __call__(self, cmd: list[str], timeout: float, *, capturar: bool = False):
        if "-encoders" in cmd:
            return derivados.Saida(rc=0, erro="", saida=b" V..... libwebp ")
        if cmd[0] == "ffprobe":
            info = {"streams": [{"width": 720, "height": 1280}], "format": {"duration": "15.1"}}
            return derivados.Saida(rc=0, erro="", saida=json.dumps(info).encode())
        if "-update" in cmd:  # foto, ou capas do vídeo: grande + mini
            if "-ss" not in cmd:
                self.fotos += 1
            await asyncio.sleep(0.05)
            grande = cmd[cmd.index("-update") + 2]
            open(grande, "wb").write(_JPEG)  # noqa: SIM115
            open(cmd[-1], "wb").write(b"RIFF....WEBP")  # noqa: SIM115
        elif "-filter_complex" in cmd:  # vídeo
            self.videos += 1
            open(cmd[-1], "wb").write(b"\x00" * 4096)  # noqa: SIM115
        return derivados.Saida(rc=0, erro="")


async def test_miss_de_foto_gera_uma_vez_com_5_pedidos_juntos(client: AsyncClient, monkeypatch):
    ff = FfmpegFalso()
    pedidos_thumb: list[dict[str, Any]] = []

    async def _bytes(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        pedidos_thumb.append(params)
        return _JPEG

    monkeypatch.setattr(derivados, "_rodar", ff)
    monkeypatch.setattr(derivados, "sidecar_bytes", _bytes)
    url = _link("charlots", "/Malas/ABS 20", "M1/b005.20.jpg", "mini")
    respostas = await asyncio.gather(*(client.get(url) for _ in range(5)))
    assert [r.status_code for r in respostas] == [200] * 5
    assert ff.fotos == 1
    # A fonte é a prévia de 1600 do sidecar (nunca o original do MEGA).
    assert pedidos_thumb[0] == {"path": "/Malas/ABS 20/M1/b005.20.jpg", "lado": 1600, "preparar": 1}
    meta = derivados.ler_meta(derivados.grupo("charlots", "/Malas/ABS 20/M1/b005.20.jpg"))
    assert (meta["largura"], meta["altura"]) == (1500, 1500)
    assert meta["fonte"] and meta["falhou_em"] is None


async def test_miss_de_foto_origem_sumiu_404_e_ffmpeg_quebrado_anota(
    client: AsyncClient, monkeypatch
):
    async def _sumiu(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        raise MegaError("sidecar HTTP 404", 404)

    monkeypatch.setattr(derivados, "sidecar_bytes", _sumiu)
    r = await client.get(_link("charlots", "/Malas/ABS 20", "x.jpg"))
    assert r.status_code == 404 and r.json()["detail"] == {"code": "midia_indisponivel"}

    async def _ok(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        return _JPEG

    async def _quebrado(cmd: list[str], timeout: float, *, capturar: bool = False):
        if "-encoders" in cmd:
            return derivados.Saida(rc=0, erro="", saida=b"libwebp")
        return derivados.Saida(rc=1, erro="Invalid data found when processing input")

    monkeypatch.setattr(derivados, "sidecar_bytes", _ok)
    monkeypatch.setattr(derivados, "_rodar", _quebrado)
    r = await client.get(_link("charlots", "/Malas/ABS 20", "y.jpg"))
    assert r.status_code == 404
    meta = derivados.ler_meta(derivados.grupo("charlots", "/Malas/ABS 20/y.jpg"))
    assert meta["falhou_em"]

    # Estourou o tempo: é da hora — 503, sem anotar.
    async def _lento(cmd: list[str], timeout: float, *, capturar: bool = False):
        if "-encoders" in cmd:
            return derivados.Saida(rc=0, erro="", saida=b"libwebp")
        return derivados.Saida(rc=-9, erro="tempo esgotado", estourou=True)

    monkeypatch.setattr(derivados, "_rodar", _lento)
    r = await client.get(_link("charlots", "/Malas/ABS 20", "z.jpg"))
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "5"
    assert r.json()["detail"] == {"code": "midia_ocupada"}
    assert derivados.ler_meta(derivados.grupo("charlots", "/Malas/ABS 20/z.jpg")) is None


async def test_miss_de_video_da_404_preparando_e_enfileira(client: AsyncClient, fila):
    for variante in ("video", "mini", "grande"):
        r = await client.get(_link("uranyx", "/Celular/F117", "v.mp4", variante))
        assert r.status_code == 404
        assert r.json()["detail"] == {"code": "midia_preparando"}
        assert r.headers["cache-control"] == "no-store"
    assert fila[0] == ("uranyx", "/Celular/F117/v.mp4")


async def test_disco_abaixo_do_piso_da_503(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "sites_midia_disco_min_mb", 10**9)
    chamou = False

    async def _bytes(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        nonlocal chamou
        chamou = True
        return _JPEG

    monkeypatch.setattr(derivados, "sidecar_bytes", _bytes)
    r = await client.get(_link("charlots", "/Malas/ABS 20", "a.jpg"))
    assert r.status_code == 503
    assert r.json()["detail"] == {"code": "midia_ocupada"}
    assert not chamou, "abaixo do piso não pode nem buscar a fonte"
    # O que já está em cache continua saindo.
    _semear("charlots", "/Malas/ABS 20/b.jpg")
    assert (await client.get(_link("charlots", "/Malas/ABS 20", "b.jpg"))).status_code == 200


# ─────────────── o script de aquecimento ───────────────


async def test_aquecimento_dry_run_rodada_e_teto(
    db: AsyncSession, make_user, sidecar: Sidecar, monkeypatch
):
    from scripts.sites_midia_aquecer import aquecer

    await _produto(db, make_user, sku="b005.20", pasta="/Malas/ABS 20")
    await _produto(db, make_user, sku="dg048", pasta="/Celular/F117")
    await _produto(db, make_user, sku="dg900", pasta="/Celular/apple", segmento="Apple")
    sidecar.pastas["/Malas/ABS 20"] = {"imagens": ["1.jpg", "2.jpg", "Embalagens/c.jpg"]}
    sidecar.pastas["/Celular/F117"] = {"imagens": ["a.jpg"], "videos": ["v.mp4"]}
    sidecar.pastas["/Celular/apple"] = {"imagens": ["x.jpg"]}
    ff = FfmpegFalso()

    async def _bytes(path: str, *, params: dict[str, Any], timeout: float = 600.0) -> bytes:
        return _JPEG

    async def _stream(path: str, *, params: dict[str, Any], timeout: float = 600.0):
        async def _pedacos():
            yield b"\x00" * 1000

        async def _fechar() -> None:
            return None

        return _pedacos(), _fechar

    monkeypatch.setattr(derivados, "_rodar", ff)
    monkeypatch.setattr(derivados, "sidecar_bytes", _bytes)
    monkeypatch.setattr(derivados, "sidecar_stream", _stream)
    linhas: list[str] = []

    assert await aquecer(["charlots", "uranyx"], so_fotos=False, dry_run=True,
                         saida=linhas.append) == 0  # fmt: skip
    assert any("charlots: pastas=1 fotos=2 (prontas 0) videos=0" in x for x in linhas), linhas
    assert any("uranyx: pastas=1 fotos=1 (prontas 0) videos=1 (prontos 0)" in x for x in linhas)
    assert (ff.fotos, ff.videos) == (0, 0), "dry-run não gera nada"

    linhas.clear()
    assert await aquecer(["charlots", "uranyx"], so_fotos=False, dry_run=False,
                         saida=linhas.append) == 0  # fmt: skip
    assert (ff.fotos, ff.videos) == (3, 1)
    for site, caminho in (("charlots", "/Malas/ABS 20/1.jpg"), ("charlots", "/Malas/ABS 20/2.jpg"),
                          ("uranyx", "/Celular/F117/a.jpg")):  # fmt: skip
        assert derivados.foto_pronta(derivados.grupo(site, caminho))
    assert derivados.video_pronto(derivados.grupo("uranyx", "/Celular/F117/v.mp4"))
    assert "erros=0" in linhas[-1]
    texto = "\n".join(linhas)
    for proibido in (TOK_CH, TOK_UR, "ABS 20", "F117", "1.jpg"):
        assert proibido not in texto

    # Já pronto: a 2ª rodada não gera de novo.
    await aquecer(["uranyx"], so_fotos=False, dry_run=False, saida=linhas.append)
    assert (ff.fotos, ff.videos) == (3, 1)

    # Teto: para antes de passar de 90%, com aviso.
    midia.limpar_caches()
    sidecar.pastas["/Malas/ABS 20"]["imagens"].append("3.jpg")
    monkeypatch.setattr(get_settings(), "sites_midia_cache_mb", 0)
    linhas.clear()
    await aquecer(["charlots"], so_fotos=False, dry_run=False, saida=linhas.append)
    assert ff.fotos == 3
    assert any("AVISO: parei em 90% do teto" in x for x in linhas)


@pytest.mark.parametrize(
    "pasta",
    [
        "/Malas/../Celular/F117",
        "/Malas/./b005",
        "/Malas/ .. /x",
        "/Malas/b005\n",
        "/Celular/../Malas/b005",
    ],
)
def test_pasta_com_ponto_ponto_ou_controle_nunca_vale(pasta):
    site = "uranyx" if pasta.startswith("/Celular") else "charlots"
    assert not midia.pasta_do_site(site, pasta)
    assert midia.pasta_do_site("charlots", "/Malas/b006 M1 listrada verde claro  ")
