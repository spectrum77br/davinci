"""Foto na resposta ao comprador (01/10/2026): Shopee, TikTok e pós-venda do ML.

O que estes testes seguram, na ordem em que um erro custaria mais caro:

- com o envio DESLIGADO (ou a loja em Observar) nada sobe para a plataforma:
  nem o upload acontece;
- a foto passa pelo caminho único de saída: linha em voo, resultado
  enviada / revisar / falhou, "uma em voo por conversa", foto repetida em
  2 min recusada;
- upload que falha = nada saiu (falhou, não ambíguo); mensagem sem resposta
  da plataforma = ambígua (revisar, nunca retenta);
- cada plataforma recebe o que a API dela espera (HTTP falso, cliente de
  verdade): Shopee upload_image + send_message image; TikTok images/upload +
  mensagem IMAGE; ML attachments + mensagem no pack com texto e anexo;
- foto não vira avaliação "escreveu do zero" da sugestão da IA;
- o arquivo é conferido pelos BYTES (JPG/PNG), com teto de tamanho.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, enviar, foto, gravar, validador
from app.services.atendimento.enviar import EnvioRecusado
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient
from app.services.marketplaces.tiktok import TikTokClient

URL = "/api/atendimento"
JPG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"1" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"2" * 200
T_CLIENTE = datetime.now(UTC) - timedelta(minutes=30)
SELLER = 999000111
COMPRADOR_ML = 555000222
_AsyncClientReal = httpx.AsyncClient

# A rota da foto vem do `app.main` (router `atendimento_painel` registrado lá).


@pytest.fixture(autouse=True)
def _validador_falso(monkeypatch):
    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        return [] if texto.strip() else ["Resposta vazia."]

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_auto_ativo", False)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    return s


class _Rede:
    """O httpx de verdade sobre um transporte falso; guarda cada pedido feito."""

    def __init__(self, monkeypatch, handler) -> None:
        self.feitos: list[httpx.Request] = []

        def registrar(request: httpx.Request) -> httpx.Response:
            self.feitos.append(request)
            return handler(request)

        def fabrica(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(registrar)
            return _AsyncClientReal(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", fabrica)

    def caminhos(self) -> list[tuple[str, str]]:
        return [(p.method, p.url.path) for p in self.feitos]


def _cliente(monkeypatch, cliente) -> None:
    async def fabrica(integration):
        return cliente

    monkeypatch.setattr(clientes, "cliente_da_integracao", fabrica)


async def _cenario(
    db,
    make_user,
    *,
    plataforma="shopee",
    canal="chat",
    modo="humano",
    externo="conv-1",
    comprador="555",
):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform(plataforma),
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    c = AtendimentoCanal(
        integration_id=integ.id, plataforma=plataforma, canal=canal, modo=modo, status="ok"
    )
    db.add(c)
    await db.flush()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=c,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal,
        externo_id=externo,
        comprador_id=comprador,
        pedido_marketplace="250925ABC",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="c-1",
        autor="cliente",
        texto="chegou quebrado",
        enviada_em=T_CLIENTE,
    )
    await db.commit()
    return conversa, user


def _shopee_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/sellerchat/upload_image"):
        return httpx.Response(
            200, json={"error": "", "response": {"url": "https://cf.shopee.com.br/file/abc"}}
        )
    if request.url.path.endswith("/sellerchat/send_message"):
        corpo = json.loads(request.content)
        assert corpo["message_type"] == "image"
        assert corpo["content"] == {"image_url": "https://cf.shopee.com.br/file/abc"}
        return httpx.Response(200, json={"error": "", "response": {"message_id": "m-77"}})
    return httpx.Response(404, json={"error": "error_not_found"})


def _shopee_real() -> ShopeeClient:
    return ShopeeClient({"shop_id": 111, "access_token": "t", "expires_at": 9_999_999_999})


# ─────────────── o arquivo ───────────────


def test_validar_foto_pelos_bytes():
    f = foto.validar_foto(JPG, "../../IMG 0001.HEIC", "image/heic")
    assert (f.mime, f.nome) == ("image/jpeg", "IMG_0001.jpg")
    assert len(f.sha256) == 64
    assert foto.validar_foto(PNG, None, "image/png").nome == "foto.png"
    for dados, code in (
        (b"", "foto_vazia"),
        (b"GIF89a....", "foto_tipo"),
        (b"%PDF-1.4", "foto_tipo"),
    ):
        with pytest.raises(foto.FotoInvalida) as e:
            foto.validar_foto(dados, "x", "image/gif")
        assert e.value.code == code
    with pytest.raises(foto.FotoInvalida) as e:
        foto.validar_foto(JPG + b"0" * foto.MAX_BYTES_UPLOAD, "x.jpg", "image/jpeg")
    assert e.value.code == "foto_grande"


def _png_com(largura: int, altura: int) -> bytes:
    """Só o cabeçalho de um PNG (assinatura + IHDR): basta para a resolução."""
    ihdr = largura.to_bytes(4, "big") + altura.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + ihdr + b"\x00" * 64


def _jpg_com(largura: int, altura: int) -> bytes:
    """SOI + APP0 (JFIF) + SOF0 com a resolução + começo dos dados."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x01\x01\x00" + b"\x00" * 6
    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + altura.to_bytes(2, "big")
        + largura.to_bytes(2, "big")
        + b"\x03"
        + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof + b"\xff\xda" + b"\x00" * 64


def test_resolucao_lida_do_cabecalho_e_bomba_recusada():
    # Revisão (01/10/2026): um PNG de 1 MB com 9000×9000 decodificava para
    # mais de 1 GB na redução (síncrona). A resolução é lida do cabeçalho,
    # sem decodificar, e acima de MAX_PIXELS a foto é recusada.
    assert foto.dimensoes(_png_com(1200, 900)) == (1200, 900)
    assert foto.dimensoes(_jpg_com(4000, 3000)) == (4000, 3000)
    assert foto.dimensoes(JPG) is None and foto.dimensoes(PNG) is None
    assert foto.dimensoes(b"\xff\xd8\xff\xd9") is None
    assert foto.validar_foto(_jpg_com(4000, 3000), "a.jpg").mime == "image/jpeg"
    assert foto.validar_foto(_png_com(1200, 900), "a.png").mime == "image/png"
    for dados in (_png_com(9000, 9000), _jpg_com(10000, 8000)):
        with pytest.raises(foto.FotoInvalida) as e:
            foto.validar_foto(dados, "bomba", "image/png")
        assert e.value.code == "foto_grande"
        assert "megapixels" in e.value.detail


async def test_quais_caixas_aceitam_foto(db, make_user):
    shopee, _ = await _cenario(db, make_user)
    assert foto.motivo_sem_foto(shopee) is None
    assert not foto.legenda_obrigatoria(shopee)
    pergunta, _ = await _cenario(db, make_user, plataforma="ml", canal="pergunta", externo="q1")
    assert "não aceita foto" in foto.motivo_sem_foto(pergunta)
    pos, _ = await _cenario(db, make_user, plataforma="ml", canal="pos_venda", externo="p1")
    assert foto.motivo_sem_foto(pos) is None and foto.legenda_obrigatoria(pos)
    amazon, _ = await _cenario(db, make_user, plataforma="amazon", canal="email", externo="a1")
    assert foto.motivo_sem_foto(amazon)


# ─────────────── travas: nada sobe ───────────────


async def test_envio_desligado_nao_sobe_nada(db, make_user, monkeypatch, _chaves):
    monkeypatch.setattr(_chaves, "atendimento_envio_ativo", False)
    rede = _Rede(monkeypatch, _shopee_ok)
    _cliente(monkeypatch, _shopee_real())
    conversa, user = await _cenario(db, make_user)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, conversa, foto.validar_foto(JPG, "a.jpg"), user=user)
    assert e.value.code == enviar.RECUSA_ENVIO_DESLIGADO
    assert rede.feitos == []
    assert await db.scalar(select(func.count()).select_from(AtendimentoMensagem)) == 1


async def test_loja_em_observar_e_ml_pergunta_nao_sobem(db, make_user, monkeypatch):
    rede = _Rede(monkeypatch, _shopee_ok)
    _cliente(monkeypatch, _shopee_real())
    conversa, user = await _cenario(db, make_user, modo="observar")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, conversa, foto.validar_foto(JPG, "a.jpg"), user=user)
    assert e.value.code == enviar.RECUSA_CANAL_EM_OBSERVACAO
    pergunta, user2 = await _cenario(db, make_user, plataforma="ml", canal="pergunta", externo="q9")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, pergunta, foto.validar_foto(JPG, "a.jpg"), user=user2)
    assert e.value.code == enviar.RECUSA_FOTO_NAO_SUPORTADA
    assert rede.feitos == []


async def test_shopee_com_legenda_e_recusada(db, make_user, monkeypatch):
    rede = _Rede(monkeypatch, _shopee_ok)
    _cliente(monkeypatch, _shopee_real())
    conversa, user = await _cenario(db, make_user)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(
            db, conversa, foto.validar_foto(JPG, "a.jpg"), legenda="olha", user=user
        )
    assert e.value.code == enviar.RECUSA_FOTO_INVALIDA
    assert rede.feitos == []


# ─────────────── Shopee ───────────────


async def test_shopee_sobe_e_manda_a_imagem(db, make_user, monkeypatch):
    rede = _Rede(monkeypatch, _shopee_ok)
    _cliente(monkeypatch, _shopee_real())
    conversa, user = await _cenario(db, make_user)
    # Sugestão da IA pendente: a foto não pode virar "escreveu do zero".
    db.add(AtendimentoRascunho(conversa_id=conversa.id, texto="Vamos ver", status="pendente"))
    await db.commit()

    m = await enviar.enviar_foto(db, conversa, foto.validar_foto(JPG, "foto.jpg"), user=user)
    assert (m.status, m.tipo, m.externo_id, m.texto) == ("enviada", "imagem", "m-77", None)
    assert m.anexos[0]["url"] == "https://cf.shopee.com.br/file/abc"
    assert m.payload["foto"]["sha256"]
    assert [c[1] for c in rede.caminhos()] == [
        "/api/v2/sellerchat/upload_image",
        "/api/v2/sellerchat/send_message",
    ]
    # O upload é multipart com o campo `file`.
    assert b'name="file"' in rede.feitos[0].content
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is False  # a foto é resposta da loja
    assert await db.scalar(select(func.count()).select_from(AtendimentoAvaliacao)) == 0

    # A mesma foto de novo em menos de 2 min: recusada, nada sobe.
    antes = len(rede.feitos)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, conversa, foto.validar_foto(JPG, "foto.jpg"), user=user)
    assert e.value.code == enviar.RECUSA_ENVIO_REPETIDO
    assert len(rede.feitos) == antes


async def test_shopee_upload_falhou_nao_saiu(db, make_user, monkeypatch):
    def api(request):
        if request.url.path.endswith("/upload_image"):
            return httpx.Response(200, json={"error": "error_param", "message": "bad image"})
        raise AssertionError("não pode mandar a mensagem sem a imagem")

    _Rede(monkeypatch, api)
    _cliente(monkeypatch, _shopee_real())
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_foto(db, conversa, foto.validar_foto(PNG, "p.png"), user=user)
    assert m.status == "falhou"
    assert m.erro.startswith("shopee upload")
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is True  # o cliente continua esperando


async def test_shopee_sem_resposta_da_mensagem_e_ambiguo(db, make_user, monkeypatch):
    def api(request):
        if request.url.path.endswith("/upload_image"):
            return httpx.Response(200, json={"error": "", "response": {"url": "https://cf/x"}})
        raise httpx.ReadTimeout("lento", request=request)

    _Rede(monkeypatch, api)
    _cliente(monkeypatch, _shopee_real())
    conversa, user = await _cenario(db, make_user)
    m = await enviar.enviar_foto(db, conversa, foto.validar_foto(JPG, "a.jpg"), user=user)
    assert m.status == "revisar"


# ─────────────── TikTok ───────────────


async def test_tiktok_sobe_e_manda_image(db, make_user, monkeypatch):
    def api(request):
        if request.url.path == "/customer_service/202309/images/upload":
            assert b'name="data"' in request.content
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "url": "https://p16.tiktokcdn.com/img.jpeg",
                        "width": 800,
                        "height": 600,
                    },
                },
            )
        if request.url.path == "/customer_service/202309/conversations/777/messages":
            corpo = json.loads(request.content)
            assert corpo["type"] == "IMAGE"
            assert json.loads(corpo["content"]) == {
                "url": "https://p16.tiktokcdn.com/img.jpeg",
                "width": 800,
                "height": 600,
            }
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "t-1"}})
        return httpx.Response(404, json={"code": 404})

    rede = _Rede(monkeypatch, api)
    _cliente(monkeypatch, TikTokClient({"app_key": "k", "app_secret": "s", "access_token": "t"}))
    conversa, user = await _cenario(db, make_user, plataforma="tiktok", externo="777")
    m = await enviar.enviar_foto(db, conversa, foto.validar_foto(JPG, "a.jpg"), user=user)
    assert (m.status, m.externo_id) == ("enviada", "t-1")
    assert m.anexos[0]["url"] == "https://p16.tiktokcdn.com/img.jpeg"
    assert [c[0] for c in rede.caminhos()] == ["POST", "POST"]
    assert not [p for p in rede.feitos if p.url.path.endswith("/read")]


async def test_tiktok_janela_fechada_bloqueia(db, make_user, monkeypatch):
    def api(request):
        if request.url.path.endswith("/images/upload"):
            return httpx.Response(200, json={"code": 0, "data": {"url": "https://x/i.png"}})
        return httpx.Response(200, json={"code": 45109001, "message": "window closed"})

    _Rede(monkeypatch, api)
    _cliente(monkeypatch, TikTokClient({"app_key": "k", "app_secret": "s", "access_token": "t"}))
    conversa, user = await _cenario(db, make_user, plataforma="tiktok", externo="778")
    m = await enviar.enviar_foto(db, conversa, foto.validar_foto(PNG, "a.png"), user=user)
    assert m.status == "falhou"
    await db.refresh(conversa)
    assert conversa.situacao == "bloqueada"


# ─────────────── ML (pós-venda) ───────────────


def _ml_real() -> MercadoLivreClient:
    return MercadoLivreClient({"access_token": "tok", "refresh_token": "rt", "user_id": SELLER})


async def test_ml_sobe_o_anexo_e_manda_com_o_texto(db, make_user, monkeypatch):
    def api(request):
        if request.url.path == "/messages/attachments":
            assert request.url.params["tag"] == "post_sale"
            assert request.url.params["site_id"] == "MLB"
            assert request.headers["authorization"] == "Bearer tok"
            assert b'name="file"' in request.content
            return httpx.Response(200, json={"id": "999_abc.jpg"})
        if request.url.path == f"/messages/packs/2000009999/sellers/{SELLER}":
            corpo = json.loads(request.content)
            assert corpo == {
                "from": {"user_id": SELLER},
                "to": {"user_id": COMPRADOR_ML},
                "text": "Segue a foto da etiqueta.",
                "attachments": ["999_abc.jpg"],
            }
            return httpx.Response(201, json={"id": "msg-ml-1", "status": "available"})
        return httpx.Response(404, json={"error": "not_found"})

    rede = _Rede(monkeypatch, api)
    _cliente(monkeypatch, _ml_real())
    conversa, user = await _cenario(
        db,
        make_user,
        plataforma="ml",
        canal="pos_venda",
        externo="2000009999",
        comprador=str(COMPRADOR_ML),
    )
    m = await enviar.enviar_foto(
        db,
        conversa,
        foto.validar_foto(JPG, "a.jpg"),
        legenda="  Segue a foto da etiqueta. ",
        user=user,
    )
    assert m.status == "enviada", m.erro
    assert m.texto == "Segue a foto da etiqueta."
    assert m.payload["envio"]["ml_anexo"] == "999_abc.jpg"
    assert [c[1] for c in rede.caminhos()] == [
        "/messages/attachments",
        "/messages/packs/2000009999/sellers/999000111",
    ]


async def test_ml_sem_texto_e_recusado_antes_do_upload(db, make_user, monkeypatch):
    rede = _Rede(monkeypatch, lambda r: httpx.Response(500))
    _cliente(monkeypatch, _ml_real())
    conversa, user = await _cenario(
        db,
        make_user,
        plataforma="ml",
        canal="pos_venda",
        externo="2000009998",
        comprador=str(COMPRADOR_ML),
    )
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(
            db, conversa, foto.validar_foto(JPG, "a.jpg"), legenda=" ", user=user
        )
    assert e.value.code == enviar.RECUSA_TEXTO_INVALIDO
    assert rede.feitos == []


async def test_ml_upload_recusado_nao_manda_mensagem(db, make_user, monkeypatch):
    def api(request):
        if request.url.path == "/messages/attachments":
            return httpx.Response(400, json={"code": "invalid_file"})
        raise AssertionError("sem anexo não há mensagem")

    _Rede(monkeypatch, api)
    _cliente(monkeypatch, _ml_real())
    conversa, user = await _cenario(
        db,
        make_user,
        plataforma="ml",
        canal="pos_venda",
        externo="2000009997",
        comprador=str(COMPRADOR_ML),
    )
    m = await enviar.enviar_foto(
        db, conversa, foto.validar_foto(JPG, "a.jpg"), legenda="foto", user=user
    )
    assert m.status == "falhou"
    assert "invalid_file" in m.erro


# ─────────────── a rota ───────────────


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


async def test_rota_foto_envio_desligado_recusa_antes_de_ler(
    db, client, admin, make_user, monkeypatch, _chaves
):
    monkeypatch.setattr(_chaves, "atendimento_envio_ativo", False)
    rede = _Rede(monkeypatch, _shopee_ok)
    conversa, _ = await _cenario(db, make_user)
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/foto",
        files={"arquivo": ("a.jpg", JPG, "image/jpeg")},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "envio_desligado"
    assert [p for p in rede.feitos if "shopee" in str(p.url)] == []


async def test_rota_foto_arquivo_invalido_e_envio(db, client, admin, make_user, monkeypatch):
    conversa, _ = await _cenario(db, make_user)
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/foto",
        files={"arquivo": ("a.gif", b"GIF89a....", "image/gif")},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "foto_invalida"

    rede = _Rede(monkeypatch, _shopee_ok)
    _cliente(monkeypatch, _shopee_real())
    r = await client.post(
        f"{URL}/conversas/{conversa.id}/foto",
        files={"arquivo": ("a.jpg", JPG, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    m = r.json()["mensagem"]
    assert (m["status"], m["tipo"], m["autor"], m["origem"]) == (
        "enviada",
        "imagem",
        "loja",
        "davinci_humano",
    )
    assert m["anexos"][0]["url"] == "https://cf.shopee.com.br/file/abc"
    assert len([p for p in rede.feitos if "sellerchat" in p.url.path]) == 2
