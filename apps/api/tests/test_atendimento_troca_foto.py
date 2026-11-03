# ruff: noqa: F811 — fixtures importadas dos testes da oferta
"""A FOTO do modelo sugerido na oferta de troca (03/11/2026).

O Eduardo: "daria pra gente no copiar anexar uma imagem do modelo de troca já
junto seria melhor". `services/atendimento/troca_foto.py`, o `foto` de cada
sugestão (painel e lista), a rota GET /pedidos/{n}/troca/foto (o "copiar
oferta") e a foto antes do texto no "enviar oferta"
(`troca_oferta.enviar_oferta`). O que estes testes seguram:

- a fonte: o retrato de um pedido da MESMA loja primeiro, depois outra loja e
  o catálogo; o SKU exato antes do principal; a foto de UM modelo antes da
  capa compartilhada (`generica`); só https das CDNs das plataformas;
- o envio: a foto ANTES do texto, pelo caminho da foto (`enviar.enviar_foto`),
  com a marca `oferta_troca_foto`; o texto conferido antes (o validador e as
  travas do envio): reprovado ou desligado = nada sai, nem a foto; a foto que
  não vai (sem foto, não baixou, recusada, genérica sem marcar, desmarcada)
  não segura o texto e a resposta avisa; no ML (legenda obrigatória) a foto vai
  DENTRO da mensagem do texto;
- a rota da cópia: os bytes da foto, 404 sem foto ou de outra equipe, 502 com
  a CDN fora.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.models import Integration, IntegrationPlatform, Listing
from app.security.cipher import encrypt_json
from app.services.atendimento import foto as foto_mod
from app.services.atendimento import gravar, painel, troca_foto, troca_oferta
from app.services.atendimento.constantes import ResultadoEnvio
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_troca_oferta import (  # noqa: F401 — as fixtures (autouse) de lá
    NUMERO,
    URL,
    _cenario,
    _chaves,
    _de_outra_equipe,
    _mensagens,
    _sem_bling,
    _texto_4b,
    _validador_falso,
    chat,
    envio,
)

FOTO_URL = f"/api/atendimento/pedidos/{NUMERO}/troca/foto"
LARANJA = "https://cf.shopee.com.br/file/br-laranja-da-loja_tn"
LARANJA_OUTRA = "https://cf.shopee.com.br/file/br-laranja-outra-loja_tn"
CAPA = "https://cf.shopee.com.br/file/br-capa-varias-cores_tn"


def _png(largura: int = 40, altura: int = 40) -> bytes:
    """Só o cabeçalho de um PNG (assinatura + IHDR): basta para a conferência."""
    ihdr = largura.to_bytes(4, "big") + altura.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ihdr + b"\x00" * 64


PNG = _png()


@pytest.fixture(autouse=True)
def _sugestoes_ligadas(monkeypatch, _chaves):
    """As sugestões de troca no painel e na lista (o padrão de lá é desligado)."""
    monkeypatch.setattr(_chaves, "atendimento_troca_sugestoes_ativa", True)


# ─────────────── as puras ───────────────


def test_chaves_da_foto():
    assert troca_foto.chaves_da_foto("DG088.CI+a001.ci") == (
        "dg088.ci+a001.ci",
        "dg088+a001",
        "dg088",
    )
    assert troca_foto.chaves_da_foto("b049.12.18") == ("b049.12.18",) * 3
    assert troca_foto.chaves_da_foto("uaf001m2.220") == ("uaf001m2.220",) * 3
    assert troca_foto.chaves_da_foto("") == ("", "", "")


def test_urls():
    assert troca_foto.url_permitida("https://cf.shopee.com.br/file/x_tn")
    assert troca_foto.url_permitida("https://down-br.img.susercontent.com/file/x")
    assert troca_foto.url_permitida("https://http2.mlstatic.com/D_1-O.jpg")
    assert not troca_foto.url_permitida("http://cf.shopee.com.br/file/x")
    assert not troca_foto.url_permitida("https://shopee.com.br.evil.com/x")
    assert not troca_foto.url_permitida("https://evilshopee.com.br/x")
    assert not troca_foto.url_permitida("https://169.254.169.254/latest")
    assert not troca_foto.url_permitida(None)
    # A miniatura do catálogo do ML: https e -O; a Shopee vai cheia (sem o _tn).
    assert (
        troca_foto.url_da_previa("http://http2.mlstatic.com/D_9-MLB1_042026-I.jpg")
        == "https://http2.mlstatic.com/D_9-MLB1_042026-O.jpg"
    )
    assert troca_foto.url_para_enviar(LARANJA) == "https://cf.shopee.com.br/file/br-laranja-da-loja"
    assert troca_foto.url_para_enviar("https://cf.shopee.com.br/file/x") == (
        "https://cf.shopee.com.br/file/x"
    )


def test_escolher_a_ordem():
    loja, outra = uuid4(), uuid4()
    agora = datetime.now(UTC)
    cand = troca_foto.Candidata

    def esc(cands, modelos=None, sku="dg054.sp+a001.sp"):
        return troca_foto.escolher(
            cands, sku, integration_id=loja, plataforma="shopee", modelos_da_url=modelos
        )

    da_loja_principal = cand(
        "dg054.ci", "https://cf.shopee.com.br/file/a_tn", "retrato", loja, "shopee", agora
    )
    de_outra_exato = cand(
        "dg054.sp+a001.sp", "https://cf.shopee.com.br/file/b_tn", "retrato", outra, "shopee", agora
    )
    do_catalogo = cand(
        "dg054.sp+a001.sp", "http://http2.mlstatic.com/D_c-I.jpg", "catalogo", None, "ml", agora
    )
    fora = cand("dg054.sp", "https://exemplo.com/d.jpg", "retrato", loja, "shopee", agora)
    outro_produto = cand(
        "dg0541.sp", "https://cf.shopee.com.br/file/e_tn", "retrato", loja, "shopee", agora
    )

    # A loja da conversa vence, mesmo só pelo principal.
    f = esc([do_catalogo, de_outra_exato, da_loja_principal, fora, outro_produto])
    assert (f.url, f.origem, f.generica) == (da_loja_principal.url, "loja", False)
    assert f.url_envio == "https://cf.shopee.com.br/file/a"
    # Sem ela, outra loja; sem as duas, o catálogo (https, -O).
    assert esc([do_catalogo, de_outra_exato]).origem == "outra_loja"
    f = esc([do_catalogo, fora, outro_produto])
    assert (f.origem, f.url) == ("catalogo", "https://http2.mlstatic.com/D_c-O.jpg")
    # Host fora das CDNs e outro produto (dg0541 ≠ dg054): nada.
    assert esc([fora, outro_produto]) is None
    # Na mesma loja, o SKU exato antes do principal; no empate, a mais nova.
    exato_velho = cand(
        "dg054.sp+a001.sp",
        "https://cf.shopee.com.br/file/f_tn",
        "retrato",
        loja,
        "shopee",
        agora - timedelta(days=9),
    )
    assert esc([da_loja_principal, exato_velho]).url == exato_velho.url
    novo = cand(
        "dg054.sp+a001.sp", "https://cf.shopee.com.br/file/g_tn", "retrato", loja, "shopee", agora
    )
    assert esc([exato_velho, novo]).url == novo.url
    # A capa compartilhada (a mesma foto para o dg054 e o dg053) perde até para outra loja…
    modelos = {da_loja_principal.url: {"dg054", "dg053"}}
    f = esc([da_loja_principal, de_outra_exato], modelos)
    assert (f.url, f.generica) == (de_outra_exato.url, False)
    # …e, sozinha, vem marcada.
    f = esc([da_loja_principal], modelos)
    assert (f.url, f.generica) == (da_loja_principal.url, True)


def test_nota_com_a_foto():
    t = troca_oferta.texto_da_nota("a", "b", "Fulana", status="enviada", foto="enviada")
    assert t == "Oferta de troca enviada (a -> b) por Fulana. Foi com a foto do modelo."
    t = troca_oferta.texto_da_nota("a", "b", "Fulana", status="enviada", foto="sem_foto")
    assert t == "Oferta de troca enviada (a -> b) por Fulana."


# ─────────────── o cenário com fotos ───────────────


async def _retrato(db, c, integ, canal, externo, itens):
    """Outra conversa (outro comprador) cujo pedido na plataforma tem estes itens."""
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=externo,
        pedido_marketplace=f"SHP-{externo}",
        comprador_nome="Outro Comprador",
    )
    conversa.dados = {
        "pedido_mkt": {
            "itens": [
                {"sku": s, "imagem": u, "titulo": "x", "item_id": "1", "quantidade": 1}
                for s, u in itens
            ]
        }
    }
    await db.commit()
    return conversa


async def _com_fotos(db, make_user, monkeypatch, *, generica=False):
    """O cenário da oferta (dg053.sp em falta → dg054.sp Laranja) com a foto do dg054.

    A loja da conversa (kfa) tem um pedido do dg054.sp com a foto da variação;
    outra loja (vrc), outra foto. `generica`: na loja, a mesma imagem vale para
    o dg054 e o dg055 (a capa do anúncio).
    """
    c = await _cenario(db, make_user, monkeypatch)
    da_loja = [("dg054.sp", CAPA if generica else LARANJA)]
    if generica:
        da_loja.append(("dg055.sp", CAPA))
    await _retrato(db, c, c.integ, c.canal, "conv-retrato-loja", da_loja)
    integ_outra, canal_outra, _ = await _de_outra_equipe(db, c.dono, "SHPOUTRA")
    if not generica:
        await _retrato(
            db, c, integ_outra, canal_outra, "conv-retrato-outra", [("dg054.sp", LARANJA_OUTRA)]
        )
    return SimpleNamespace(**vars(c), integ_outra=integ_outra)


class _CDN:
    """A CDN falsa: devolve o PNG (ou o status combinado) e guarda as URLs pedidas."""

    def __init__(self, monkeypatch) -> None:
        self.urls: list[str] = []
        self.status = 200
        self.corpo = PNG

        def responde(request: httpx.Request) -> httpx.Response:
            self.urls.append(str(request.url))
            return httpx.Response(
                self.status, content=self.corpo, headers={"content-type": "image/png"}
            )

        monkeypatch.setattr(
            troca_foto,
            "_http",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(responde), follow_redirects=False
            ),
        )


class _Imagem:
    """O envio de imagem da plataforma falso: guarda cada foto e devolve o combinado."""

    def __init__(self, monkeypatch, ordem: list[str]) -> None:
        self.fotos: list[tuple[str, str | None]] = []
        self.resultado = ResultadoEnvio(ok=True, externo_id="plat-foto-1")

        async def enviar_imagem(session, conversa, integration, cliente, foto, legenda, **kw):
            fotos = foto if isinstance(foto, list) else [foto]
            self.fotos.append((fotos[0].sha256, legenda))
            ordem.append("foto")
            return self.resultado

        monkeypatch.setattr(foto_mod, "enviar_imagem", enviar_imagem)


@pytest.fixture
def cdn(monkeypatch) -> _CDN:
    return _CDN(monkeypatch)


@pytest.fixture
def ordem(chat, monkeypatch) -> list[str]:
    """A ordem do que saiu: "foto" e "texto"."""
    saida: list[str] = []
    original = troca_oferta.enviar.enviar_resposta

    async def texto(*a, **kw):
        saida.append("texto")
        return await original(*a, **kw)

    monkeypatch.setattr(troca_oferta.enviar, "enviar_resposta", texto)
    return saida


@pytest.fixture
def imagem(monkeypatch, ordem) -> _Imagem:
    return _Imagem(monkeypatch, ordem)


# ─────────────── a foto no painel e na lista ───────────────


async def test_painel_traz_a_foto_da_loja(db, make_user, monkeypatch, envio):
    c = await _com_fotos(db, make_user, monkeypatch)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    [item] = out["sugestoes_troca"]["itens"]
    [s] = item["sugestoes"]
    assert s["sku"] == "dg054.sp"
    assert s["foto"] == {"url": LARANJA, "origem": "loja", "generica": False}
    # A de fora (sem estoque) não tem oferta, nem foto.
    assert all("foto" not in f for f in item["fora"])
    assert out["ag_cancelamento"]["oferta_envio"]["foto"] is True


async def test_painel_sem_foto_e_a_leitura_quebrada(db, make_user, monkeypatch, envio):
    c = await _cenario(db, make_user, monkeypatch)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    [s] = out["sugestoes_troca"]["itens"][0]["sugestoes"]
    assert s["foto"] is None and s["texto_oferta"]

    async def _quebra(session, skus):
        raise RuntimeError("fora")

    monkeypatch.setattr(troca_foto, "ler_candidatas", _quebra)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    [s] = out["sugestoes_troca"]["itens"][0]["sugestoes"]
    assert s["texto_oferta"] and not s.get("foto")
    assert out["sugestoes_troca"]["falhou"] is False


async def test_lista_traz_a_foto(db, client, auth_as, make_user, monkeypatch, envio):
    c = await _com_fotos(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.get("/api/atendimento/ag-cancelamento")
    assert r.status_code == 200, r.text
    [p] = r.json()["pedidos"]
    [s] = p["sugestoes_troca"]["itens"][0]["sugestoes"]
    assert s["foto"] == {"url": LARANJA, "origem": "loja", "generica": False}


async def test_painel_foto_generica_vem_marcada(db, make_user, monkeypatch, envio):
    c = await _com_fotos(db, make_user, monkeypatch, generica=True)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    [s] = out["sugestoes_troca"]["itens"][0]["sugestoes"]
    assert s["foto"] == {"url": CAPA, "origem": "loja", "generica": True}


async def test_foto_do_catalogo(db, make_user, monkeypatch, envio):
    """Sem retrato: a miniatura do anúncio em `listings` (o ML no -O, em https)."""
    c = await _cenario(db, make_user, monkeypatch)
    integ_ml = Integration(
        user_id=c.dono.id,
        platform=IntegrationPlatform.ML,
        name="ml-cat",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ_ml)
    await db.flush()
    db.add(
        Listing(
            user_id=c.dono.id,
            integration_id=integ_ml.id,
            platform=IntegrationPlatform.ML,
            external_id="MLB1",
            sku="dg054.ci+a001.ci",
            title="A17 Laranja",
            thumbnail_url="http://http2.mlstatic.com/D_9-MLB1_042026-I.jpg",
        )
    )
    await db.commit()
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    [s] = out["sugestoes_troca"]["itens"][0]["sugestoes"]
    assert s["foto"] == {
        "url": "https://http2.mlstatic.com/D_9-MLB1_042026-O.jpg",
        "origem": "catalogo",
        "generica": False,
    }


# ─────────────── o "enviar oferta": a foto antes do texto ───────────────


async def test_oferta_manda_a_foto_e_depois_o_texto(
    db, client, auth_as, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert ordem == ["foto", "texto"]
    # A foto da LOJA, cheia (sem o _tn), baixada pelo servidor; sozinha na Shopee.
    assert cdn.urls == ["https://cf.shopee.com.br/file/br-laranja-da-loja"]
    assert imagem.fotos == [(foto_mod.validar_foto(PNG, "x").sha256, None)]
    assert chat.textos == [_texto_4b()]
    assert (corpo["enviada"], corpo["foto"], corpo["foto_aviso"]) == (True, "enviada", None)

    [m_foto, m_texto] = await _mensagens(db, c.conversa.id, autor="loja")
    assert (m_foto.tipo, m_foto.status) == ("imagem", "enviada")
    assert str(m_foto.id) == corpo["foto_mensagem_id"]
    assert str(m_texto.id) == corpo["mensagem_id"]
    assert m_foto.payload["oferta_troca_foto"]["sku_novo"] == "dg054.sp"
    assert "oferta_troca" not in m_foto.payload
    assert m_texto.payload["oferta_troca"]["sku_novo"] == "dg054.sp"
    # A troca acha a oferta pelo TEXTO.
    assert await troca_oferta.oferta_enviada_id(db, c.conversa.id, NUMERO, "dg054.sp") == m_texto.id
    [nota] = await _mensagens(db, c.conversa.id, tipo="nota")
    assert nota.texto.endswith("por Fulana Atendimento. Foi com a foto do modelo.")


async def test_oferta_sem_foto_vai_so_o_texto(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _cenario(db, make_user, monkeypatch)
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert ordem == ["texto"] and cdn.urls == [] and imagem.fotos == []
    assert (out["enviada"], out["foto"], out["foto_mensagem_id"]) == (True, "sem_foto", None)
    assert out["foto_aviso"] == "O produto não tem foto no DaVinci: a oferta foi só com o texto."


async def test_foto_que_nao_baixa_nao_segura_o_texto(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    cdn.status = 404
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert ordem == ["texto"] and imagem.fotos == []
    assert (out["enviada"], out["foto"]) == (True, "nao_saiu")
    assert out["foto_aviso"].startswith(
        "Não consegui baixar a foto do modelo (A CDN da foto respondeu 404"
    )
    # Não é imagem (o HTML de erro com 200): também só o texto.
    cdn.status, cdn.corpo = 200, b"<html>erro</html>"
    chat.textos.clear()
    [m] = await _mensagens(db, c.conversa.id, autor="loja")
    out = await troca_oferta.enviar_oferta(
        db,
        c.adm,
        numero_bling=NUMERO,
        sku_novo=c.novo,
        texto="Podemos mandar a laranja?",
        ultima_vista_id=m.id,
    )
    assert (out["enviada"], out["foto"]) == (True, "nao_saiu") and chat.textos


async def test_plataforma_recusa_a_foto_e_o_texto_vai(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    imagem.resultado = ResultadoEnvio(ok=False, erro="shopee upload error_param")
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert ordem == ["foto", "texto"]
    assert (out["enviada"], out["foto"], out["foto_mensagem_id"]) == (True, "nao_saiu", None)
    assert (
        out["foto_aviso"]
        == "A plataforma recusou a foto (veja na conversa): a oferta foi só com o texto."
    )
    [nota] = await _mensagens(db, c.conversa.id, tipo="nota")
    assert "foto" not in nota.texto


async def test_foto_generica_so_vai_marcada(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch, generica=True)
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert ordem == ["texto"] and cdn.urls == []
    assert out["foto"] == "generica" and "pode mostrar outra cor" in out["foto_aviso"]
    # A pessoa viu a capa e marcou "mandar a foto": vai.
    [m] = await _mensagens(db, c.conversa.id, autor="loja")
    out = await troca_oferta.enviar_oferta(
        db,
        c.adm,
        numero_bling=NUMERO,
        sku_novo=c.novo,
        texto="Pode ser a laranja?",
        ultima_vista_id=m.id,
        com_foto=True,
    )
    assert ordem == ["texto", "foto", "texto"] and out["foto"] == "enviada"


async def test_desmarcada_vai_so_o_texto(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    out = await troca_oferta.enviar_oferta(
        db, c.adm, numero_bling=NUMERO, sku_novo=c.novo, com_foto=False
    )
    assert ordem == ["texto"] and cdn.urls == []
    assert (out["foto"], out["foto_aviso"]) == ("desmarcada", None)


async def test_texto_reprovado_nao_manda_nem_a_foto(
    db, client, auth_as, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo, "texto": "Chama no WhatsApp"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "texto_invalido"
    assert ordem == [] and imagem.fotos == [] and cdn.urls == []
    assert await _mensagens(db, c.conversa.id, autor="loja") == []


async def test_envio_desligado_nao_manda_nem_a_foto(
    db, client, auth_as, make_user, monkeypatch, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.post(URL, json={"sku_novo": c.novo})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_desligado"
    assert ordem == [] and imagem.fotos == [] and cdn.urls == []


async def test_plataforma_sem_foto_vai_so_o_texto(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    c = await _com_fotos(db, make_user, monkeypatch)
    monkeypatch.setattr(foto_mod, "motivo_sem_foto", lambda conversa: "sem foto aqui")
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert ordem == ["texto"] and cdn.urls == []
    assert out["foto"] == "nao_suportada"


async def test_ml_a_foto_vai_dentro_da_mensagem_do_texto(
    db, make_user, monkeypatch, envio, chat, cdn, imagem, ordem
):
    """Onde a foto leva texto obrigatório (o ML): UMA mensagem, a foto com a oferta."""
    c = await _com_fotos(db, make_user, monkeypatch)
    monkeypatch.setattr(foto_mod, "legenda_obrigatoria", lambda conversa: True)
    out = await troca_oferta.enviar_oferta(db, c.adm, numero_bling=NUMERO, sku_novo=c.novo)
    assert ordem == ["foto"] and chat.textos == []
    assert imagem.fotos[0][1] == _texto_4b()
    [m] = await _mensagens(db, c.conversa.id, autor="loja")
    assert (out["foto"], out["mensagem_id"], out["foto_mensagem_id"]) == ("junto", m.id, None)
    assert m.texto == _texto_4b() and m.payload["oferta_troca"]["sku_novo"] == "dg054.sp"


# ─────────────── a rota da cópia ───────────────


async def test_rota_da_foto_devolve_os_bytes(db, client, auth_as, make_user, monkeypatch, cdn):
    c = await _com_fotos(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.get(FOTO_URL, params={"sku": "DG054.SP", "conversa_id": str(c.conversa.id)})
    assert r.status_code == 200, r.text
    assert r.content == PNG and r.headers["content-type"] == "image/png"
    assert (r.headers["x-foto-origem"], r.headers["x-foto-generica"]) == ("loja", "0")
    assert cdn.urls == ["https://cf.shopee.com.br/file/br-laranja-da-loja"]
    # Sem conversa_id: a principal do pedido (a mesma loja).
    r = await client.get(FOTO_URL, params={"sku": "dg054.sp"})
    assert r.status_code == 200 and r.headers["x-foto-origem"] == "loja"
    # A CDN fora: 502 com o porquê.
    cdn.status = 500
    r = await client.get(FOTO_URL, params={"sku": "dg054.sp"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "foto_indisponivel"


async def test_rota_da_foto_sem_foto_e_fora_do_catalogo(
    db, client, auth_as, make_user, monkeypatch, cdn
):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    r = await client.get(FOTO_URL, params={"sku": "dg054.sp"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "sem_foto"
    r = await client.get(FOTO_URL, params={"sku": "xyz999"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "produto_nao_encontrado"
    assert cdn.urls == []


@pytest.mark.lojas_por_pessoa
async def test_rota_da_foto_so_nas_lojas_da_equipe(
    db, client, auth_as, make_user, monkeypatch, cdn
):
    """A equipe da loja do pedido recebe a foto; a de outra loja, 404 — pelo pedido e
    pela conversa — e nada é baixado."""
    c = await _com_fotos(db, make_user, monkeypatch)
    londres = await make_user(email=f"londres-{uuid4().hex[:6]}@davinci-test.com")
    await dar_lojas(db, londres, c.integ)
    israel = await make_user(email=f"israel-{uuid4().hex[:6]}@davinci-test.com")
    await dar_lojas(db, israel, c.integ_outra)

    auth_as(israel)
    r = await client.get(FOTO_URL, params={"sku": "dg054.sp"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"
    assert cdn.urls == []

    auth_as(londres)
    r = await client.get(FOTO_URL, params={"sku": "dg054.sp"})
    assert r.status_code == 200 and r.headers["x-foto-origem"] == "loja"
