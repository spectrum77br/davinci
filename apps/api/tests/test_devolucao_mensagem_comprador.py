# ruff: noqa: E501
"""Mensagem ao COMPRADOR pedindo a senha (services/devolucao_mensagem_comprador).

Vinicius 22/09: "quando o pessoal faz o lançamento no painel Devoluções e coloca
motivo Bloqueado, além de abrir chamado, também envie mensagem para o cliente
solicitando a senha … pode incluir a foto se conseguir".

Tudo aqui entra pelo HTTP de verdade (POST/PATCH/anexos da aba Devoluções), que é
o que dispara o gancho do router — o serviço sozinho não prova que a tela pede.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.config import get_settings
from app.models import (
    Chamado,
    ChamadoMensagem,
    DevolucaoAnexo,
    DevolucaoMensagemComprador,
    DevolucaoRastreio,
    Devolution,
)
from app.services import chamados_devolucao
from app.services import devolucao_cartao_video as cartao
from app.services import devolucao_mensagem_comprador as svc
from app.services.marketplaces.base import TestResult as _TestResult
from tests.test_chamados_devolucao import PNG_1PX, _FakeShopee, _perms, _seed_pedido

# `asyncio_mode = auto` no pyproject: os testes async rodam sem marca.

COMPRADOR = 7788990011
LOJA = "ATV Oficial"


# ---------------------------------------------------------------- fakes


class _FakeChat(_FakeShopee):
    """O fake da contestação + o chat com o comprador (sellerchat, 22/09).

    `falhar_envio` / `falhar_upload` reproduzem a Shopee recusando (token
    vencido, cota do dia, imagem grande demais) sem nenhuma chamada de rede.
    """

    def __init__(
        self,
        *,
        buyer: dict | None = None,
        loja: str = LOJA,
        falhar_envio: str = "",
        falhar_upload: str = "",
    ):
        super().__init__()
        self.buyer = {"buyer_user_id": COMPRADOR, "buyer_username": "m*****r"} if buyer is None else buyer
        self.loja = loja
        self.falhar_envio = falhar_envio
        self.falhar_upload = falhar_upload
        self.buyers: list[str] = []
        self.enviadas: list[dict] = []
        self.chat_uploads: list[tuple[str, int, str]] = []

    async def test_connection(self):
        return _TestResult(ok=True, info={"shop_id": 4242, "name": self.loja})

    async def get_order_buyer(self, order_sn):
        self.buyers.append(str(order_sn))
        return dict(self.buyer)

    async def chat_upload_image(self, filename, content, mime="image/jpeg"):
        if self.falhar_upload:
            raise RuntimeError(self.falhar_upload)
        self.chat_uploads.append((filename, len(content), mime))
        return f"https://cf.shopee/chat/{filename}"

    async def chat_send_message(self, to_id, *, text="", image_url=""):
        # Mesma trava do client de verdade: texto OU imagem, nunca os dois.
        if bool(text) == bool(image_url):
            raise ValueError("chat_send_message: mande texto OU imagem")
        if self.falhar_envio:
            raise RuntimeError(self.falhar_envio)
        self.enviadas.append({"to_id": to_id, "text": text, "image_url": image_url})
        return {"message_id": f"msg{len(self.enviadas)}", "conversation_id": "conv-99"}

    @property
    def textos(self) -> list[dict]:
        return [m for m in self.enviadas if m["text"]]

    @property
    def imagens(self) -> list[dict]:
        return [m for m in self.enviadas if m["image_url"]]


@pytest.fixture
def inline(monkeypatch):
    """Sem Redis: disparo da contestação e envio ao comprador rodam inline."""
    monkeypatch.setattr(chamados_devolucao, "ENFILEIRAR", False)
    monkeypatch.setattr(svc, "ENFILEIRAR", False)


@pytest.fixture
def shopee(monkeypatch, inline) -> _FakeChat:
    fake = _FakeChat()

    async def _client(session, ch, dev):
        return fake

    monkeypatch.setattr(chamados_devolucao, "_shopee_client_para", _client)
    return fake


@pytest.fixture
def adiar(monkeypatch):
    """O job real sai com `_defer_by=JANELA_FOTOS` (60 s). Aqui o `agendar` vira
    no-op: a linha fica `pendente` e o teste decide quando o envio acontece."""

    async def _noop(session, linha):
        return None

    monkeypatch.setattr(svc, "agendar", _noop)


@pytest_asyncio.fixture(autouse=True)
async def _limpa_mensagens(db):
    """A tabela nova não está no `_CLEANUP_TABLES` do conftest e as FKs são SET
    NULL — sem isso a linha de um teste sobrevive e polui o `processar_pendentes`
    do teste seguinte."""
    await db.execute(delete(DevolucaoMensagemComprador))
    await db.commit()
    yield
    await db.execute(delete(DevolucaoMensagemComprador))
    await db.commit()


# ---------------------------------------------------------------- helpers


async def _linha(db, pedido: str) -> DevolucaoMensagemComprador | None:
    db.expire_all()  # o request tem sessão própria; força reler do banco
    return (
        await db.execute(
            select(DevolucaoMensagemComprador).where(
                DevolucaoMensagemComprador.pedido_bling == pedido
            )
        )
    ).scalars().first()


async def _chamado(db, pedido: str) -> Chamado | None:
    db.expire_all()
    return (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == pedido))
    ).scalars().first()


async def _eventos_comprador(db, chamado_id) -> list[ChamadoMensagem]:
    """Eventos de sistema do histórico que falam da mensagem ao comprador."""
    db.expire_all()
    rows = (
        await db.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == chamado_id)
            .order_by(ChamadoMensagem.created_at)
        )
    ).scalars().all()
    return [m for m in rows if "ao comprador" in (m.texto or "")]


async def _lancar(client, db, user, *, pedido, order_sn, sku="dg050.sa",
                  motivo="Bloqueado", conta="atv", loja="88", platform="shopee",
                  produtos="Galaxy A15 128GB", **extra) -> dict:
    await _seed_pedido(db, user, numero=pedido, numeroloja=order_sn,
                       platform=platform, conta=conta, loja=loja)
    if platform == "shopee" and await db.get(DevolucaoRastreio, pedido) is None:
        # O Acompanhamento (sync de 30 min) já conhece o return_sn: sem isso a
        # contestação varre a returns API e o barulho dela cai no histórico do
        # chamado, que é o que o teste da coluna Status compara.
        db.add(DevolucaoRastreio(pedido_bling=pedido, devolucao_id_auto=f"RSN{pedido}",
                                 fonte_auto="shopee"))
        await db.commit()
    body = {
        "conta": conta,
        "pedido_bling": pedido,
        "pedido_marketplace": order_sn,
        "sku": sku,
        "produtos": produtos,
        "condicao_produto": "Não devolvido",
        "motivo_devolucao": motivo,
    }
    body.update(extra)
    r = await client.post("/api/devolutions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- 1. o caminho feliz


async def test_bloqueado_na_shopee_manda_uma_mensagem_pedindo_a_senha(
    client, make_user, auth_as, db, shopee
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295001", order_sn="2609045AM9GK01")

    # a linha da tela já mostra o estado do envio
    assert body["senha_status"] == "enviada"
    assert body["senha_erro"] is None and body["senha_enviada_at"] is not None

    # UMA mensagem de texto, pro comprador que veio do PEDIDO (não do cadastro)
    assert shopee.buyers == ["2609045AM9GK01"]
    assert len(shopee.textos) == 1 and shopee.imagens == []
    msg = shopee.textos[0]
    assert msg["to_id"] == COMPRADOR
    assert f"Aqui é a loja {LOJA}." in msg["text"]
    assert "senha de desbloqueio da tela" in msg["text"]
    assert "2609045AM9GK01" in msg["text"]

    linha = await _linha(db, "295001")
    assert linha is not None
    assert (linha.status, linha.erro, linha.tentativas) == ("enviada", None, 0)
    assert linha.evento == "senha" and linha.plataforma == "shopee"
    assert linha.destinatario_id == str(COMPRADOR)
    assert (linha.conversa_id, linha.mensagem_id) == ("conv-99", "msg1")
    assert linha.enviada_at is not None and linha.texto == msg["text"]
    assert linha.anexo_id is None and linha.conta == "atv"
    assert linha.devolution_id is not None and linha.chamado_id is not None

    # e o histórico do chamado conta o que saiu — como EVENTO DE SISTEMA
    ch = await _chamado(db, "295001")
    eventos = await _eventos_comprador(db, ch.id)
    assert len(eventos) == 1
    ev = eventos[0]
    assert ev.direcao == "sistema" and ev.tipo == "sistema"
    assert "chat da Shopee" in ev.texto and "pedindo a senha do produto" in ev.texto
    assert "com a foto" not in ev.texto


# ---------------------------------------------------------------- 2. dedupe


async def test_repetir_o_save_e_subir_fotos_nao_manda_de_novo(
    client, make_user, auth_as, db, shopee
):
    """A tela salva a linha e SÓ DEPOIS sobe as fotos, uma a uma — o gancho roda
    em todos esses passos. O comprador não pode receber quatro mensagens."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295002", order_sn="2609045AM9GK02")
    dev_id = body["id"]

    p = await client.patch(f"/api/devolutions/{dev_id}",
                           json={"motivo_devolucao": "Bloqueado"})
    assert p.status_code == 200, p.text
    assert p.json()["senha_status"] == "enviada"
    for nome in ("frente.jpg", "traseira.jpg"):
        up = await client.post(f"/api/devolutions/{dev_id}/anexos",
                               files={"file": (nome, PNG_1PX, "image/jpeg")})
        assert up.status_code == 201, up.text
        assert up.json()["senha_status"] == "enviada"

    assert len(shopee.textos) == 1
    assert len(shopee.buyers) == 1  # nem o comprador foi consultado de novo
    linhas = (await db.execute(select(DevolucaoMensagemComprador))).scalars().all()
    assert len(linhas) == 1 and linhas[0].tentativas == 0


# ---------------------------------------------------------------- 3. kit


async def test_kit_com_tres_linhas_do_mesmo_pedido_manda_uma_so(
    client, make_user, auth_as, db, shopee
):
    """Kit = 3 linhas de devolução, um comprador só. A UNIQUE (pedido, conta,
    evento) é o que segura isso."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    for sku, produto in (("dg050.sa", "Galaxy A15"), ("b001.26", "Mala 26"),
                         ("u010", "Fone")):
        await _lancar(client, db, user, pedido="295003", order_sn="2609045AM9GK03",
                      sku=sku, produtos=produto)

    assert len(shopee.textos) == 1
    linhas = (await db.execute(select(DevolucaoMensagemComprador))).scalars().all()
    assert len(linhas) == 1 and linhas[0].status == "enviada"

    # a listagem marca as TRÊS linhas do pedido como já pedidas
    lst = await client.get("/api/devolutions")
    assert lst.status_code == 200, lst.text
    itens = [i for i in lst.json()["items"] if i["pedido_bling"] == "295003"]
    assert len(itens) == 3
    assert {i["senha_status"] for i in itens} == {"enviada"}
    assert all(i["senha_enviada_at"] is not None for i in itens)


# ---------------------------------------------------------------- 4. troca de motivo


async def test_motivo_trocado_antes_do_envio_cancela_a_linha(
    client, make_user, auth_as, db, shopee, adiar
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295004", order_sn="2609045AM9GK04")
    assert body["senha_status"] == "pendente"

    p = await client.patch(
        f"/api/devolutions/{body['id']}",
        json={"motivo_devolucao": "Danificado (Outros)",
              "link_envio": "https://mega.nz/file/expedicao#K3yDoVideoNaMega0123456789abcdefghij"},
    )
    assert p.status_code == 200, p.text
    assert p.json()["senha_status"] == "cancelada"

    linha = await _linha(db, "295004")
    assert (linha.status, linha.erro) == ("cancelada", "motivo_mudou")
    assert shopee.enviadas == [] and shopee.buyers == []
    # e o cron não ressuscita o que foi cancelado
    assert (await svc.processar_pendentes(db))["verificados"] == 0
    assert shopee.enviadas == []


async def test_motivo_trocado_depois_do_envio_continua_enviada(
    client, make_user, auth_as, db, shopee
):
    """Mensagem que já chegou no chat do comprador não tem como ser desfeita."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295005", order_sn="2609045AM9GK05")
    assert body["senha_status"] == "enviada"

    p = await client.patch(
        f"/api/devolutions/{body['id']}",
        json={"motivo_devolucao": "Danificado (Outros)",
              "link_envio": "https://mega.nz/file/expedicao#K3yDoVideoNaMega0123456789abcdefghij"},
    )
    assert p.status_code == 200, p.text
    assert p.json()["senha_status"] == "enviada"
    linha = await _linha(db, "295005")
    assert (linha.status, linha.erro) == ("enviada", None)
    assert len(shopee.textos) == 1


# ---------------------------------------------------------------- 5. foto


async def test_foto_da_linha_vai_junto_como_imagem(
    client, make_user, auth_as, db, shopee, adiar
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295006", order_sn="2609045AM9GK06")
    up = await client.post(f"/api/devolutions/{body['id']}/anexos",
                           files={"file": ("aparelho.jpg", PNG_1PX, "image/jpeg")})
    assert up.status_code == 201, up.text
    assert up.json()["senha_status"] == "pendente"  # o envio ainda não saiu

    assert (await svc.processar_pendentes(db))["enviadas"] == 1
    # texto primeiro, imagem depois — imagem sem contexto não resolve nada
    assert [bool(m["text"]) for m in shopee.enviadas] == [True, False]
    assert shopee.chat_uploads == [("aparelho.jpg", len(PNG_1PX), "image/jpeg")]
    assert shopee.imagens[0]["image_url"] == "https://cf.shopee/chat/aparelho.jpg"
    assert shopee.imagens[0]["to_id"] == COMPRADOR

    anexo_id = (await db.execute(select(DevolucaoAnexo.id))).scalars().one()
    linha = await _linha(db, "295006")
    assert linha.status == "enviada" and linha.anexo_id == anexo_id
    ch = await _chamado(db, "295006")
    assert "(com a foto do produto)" in (await _eventos_comprador(db, ch.id))[0].texto


async def test_foto_que_nao_sobe_nao_derruba_o_texto_ja_enviado(
    client, make_user, auth_as, db, shopee, adiar
):
    shopee.falhar_upload = "shopee_chat_upload 3: image too large"
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295007", order_sn="2609045AM9GK07")
    up = await client.post(f"/api/devolutions/{body['id']}/anexos",
                           files={"file": ("aparelho.jpg", PNG_1PX, "image/jpeg")})
    assert up.status_code == 201, up.text

    assert (await svc.processar_pendentes(db))["enviadas"] == 1
    assert len(shopee.textos) == 1 and shopee.imagens == []
    linha = await _linha(db, "295007")
    assert (linha.status, linha.erro, linha.tentativas) == ("enviada", None, 0)
    assert linha.anexo_id is None


async def test_cartao_do_video_e_video_nunca_vao_pro_comprador(
    client, make_user, auth_as, db, shopee, adiar
):
    """O cartão (PNG com o QR da expedição, `created_by` nulo) é prova PRA
    PLATAFORMA; mandar o QR do nosso Drive pro comprador seria vazamento."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295008", order_sn="2609045AM9GK08")
    up = await client.post(f"/api/devolutions/{body['id']}/anexos",
                           files={"file": ("expedicao.mp4", b"\x00\x00mp4", "video/mp4")})
    assert up.status_code == 201, up.text
    db.add(
        DevolucaoAnexo(
            devolution_id=body["id"],
            filename=cartao.CARTAO_VIDEO_NOME,
            content_type="image/png",
            size_bytes=len(PNG_1PX),
            blob=PNG_1PX,
            created_by=None,  # é o que marca o cartão como gerado por nós
        )
    )
    await db.commit()

    assert (await svc.processar_pendentes(db))["enviadas"] == 1
    assert len(shopee.textos) == 1
    assert shopee.imagens == [] and shopee.chat_uploads == []
    linha = await _linha(db, "295008")
    assert linha.status == "enviada" and linha.anexo_id is None


# ---------------------------------------------------------------- 6. sem canal


@pytest.mark.parametrize(
    ("platform", "conta", "loja", "pedido", "order_sn"),
    [
        ("ml", "aguiar", "55", "295011", "2000099001"),
        ("tiktok", "injox", "77", "295012", "585585025945338891"),
        ("amazon", "poofy", "99", "295013", "701-9431449-5435416"),
    ],
)
async def test_plataforma_sem_canal_nao_finge_que_pediu(
    client, make_user, auth_as, db, shopee, platform, conta, loja, pedido, order_sn
):
    """ML fecha o chat com o pedido cancelado, a TikTok não deu o escopo e a
    Amazon só tem e-mail: a linha nasce `sem_canal` pra tela dizer "mandar na
    mão" — e NADA é chamado no chat."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido=pedido, order_sn=order_sn,
                          conta=conta, loja=loja, platform=platform)

    assert body["senha_status"] == "sem_canal"
    assert body["senha_erro"] == f"sem_canal_{platform}"
    assert body["senha_enviada_at"] is None
    linha = await _linha(db, pedido)
    assert (linha.status, linha.plataforma) == ("sem_canal", platform)
    assert linha.destinatario_id is None and linha.enviada_at is None
    assert shopee.enviadas == [] and shopee.buyers == []
    # o cron só olha `pendente` — nunca vai tentar mandar isso
    assert (await svc.processar_pendentes(db))["verificados"] == 0


# ---------------------------------------------------------------- 7. falha e retentativa


async def test_falha_na_shopee_fica_pendente_e_o_cron_retenta_ate_desistir(
    client, make_user, auth_as, db, shopee
):
    shopee.falhar_envio = "shopee_chat_send 53: quota exceeded"
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295009", order_sn="2609045AM9GK09")

    assert body["senha_status"] == "pendente"
    assert "quota exceeded" in body["senha_erro"]
    linha = await _linha(db, "295009")
    assert (linha.status, linha.tentativas) == ("pendente", 1)

    # 2ª tentativa (cron :25) — ainda pendente
    assert await svc.processar_pendentes(db) == {
        "verificados": 1, "enviadas": 0, "pendentes": 1, "falhas": 0,
    }
    assert (await _linha(db, "295009")).tentativas == 2

    # 3ª — esgotou, vira `falhou` e para de tentar
    assert await svc.processar_pendentes(db) == {
        "verificados": 1, "enviadas": 0, "pendentes": 0, "falhas": 1,
    }
    linha = await _linha(db, "295009")
    assert (linha.status, linha.tentativas) == ("falhou", 3)
    assert "quota exceeded" in linha.erro

    shopee.falhar_envio = ""  # Shopee voltou: mesmo assim não sai sozinho
    assert (await svc.processar_pendentes(db))["verificados"] == 0
    assert shopee.enviadas == []


async def test_envio_pelo_worker_e_pelo_id_da_linha_nao_duplica(
    client, make_user, auth_as, db, shopee, adiar
):
    """Com Redis o envio é o job `devolucao_mensagem_comprador_enviar`, que só
    tem o id da linha. Create e upload de foto enfileiram dois jobs do mesmo
    pedido — o segundo tem que ver a linha já `enviada` e calar a boca."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="295016", order_sn="2609045AM9GK16")
    linha = await _linha(db, "295016")

    assert await svc.enviar_por_id(db, "nao-e-uuid") is None
    assert await svc.enviar_por_id(db, str(uuid4())) is None
    assert shopee.enviadas == []

    r = await svc.enviar_por_id(db, str(linha.id))
    await db.commit()
    assert r.status == "enviada" and len(shopee.textos) == 1
    assert (await svc.enviar_por_id(db, str(linha.id))).status == "enviada"
    assert len(shopee.textos) == 1


async def test_pedido_sem_numero_do_marketplace_nao_tem_como_achar_o_comprador(
    client, make_user, auth_as, db, shopee
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295010", order_sn="2609045AM9GK10",
                          pedido_marketplace="")
    assert body["senha_status"] == "pendente"
    assert body["senha_erro"] == "sem_pedido_marketplace"
    assert shopee.buyers == [] and shopee.enviadas == []
    assert (await _linha(db, "295010")).tentativas == 1


# ---------------------------------------------------------------- 8. freio de mão


async def test_flag_desligada_nao_manda_nada(
    client, make_user, auth_as, db, shopee, monkeypatch
):
    monkeypatch.setattr(get_settings(), "shopee_mensagens_comprador", False, raising=False)
    user = await make_user(permissions=_perms())
    auth_as(user)
    body = await _lancar(client, db, user, pedido="295014", order_sn="2609045AM9GK14")

    assert shopee.enviadas == [] and shopee.buyers == []
    assert body["senha_status"] == "pendente"
    assert body["senha_erro"] == "envio_desligado"
    linha = await _linha(db, "295014")
    # freio de mão, não falha: não gasta tentativa (volta a sair quando religar)
    assert (linha.status, linha.tentativas) == ("pendente", 0)
    assert (await svc.processar_pendentes(db))["pendentes"] == 1
    assert shopee.enviadas == []

    ch = await _chamado(db, "295014")
    assert await _eventos_comprador(db, ch.id) == []


# ---------------------------------------------------------------- 9. o texto


def _dev(sku: str, **kw) -> Devolution:
    base = {"conta": "atv", "motivo_devolucao": "Bloqueado", "sku": sku,
            "pedido_bling": "295100", "pedido_marketplace": "2609045AM9GKXX"}
    base.update(kw)
    return Devolution(**base)


# Nunca, em nenhuma variação: pedir a senha da CONTA é pedir credencial.
_PEDIDOS_DE_CREDENCIAL = (
    "senha da conta",
    "senha da sua conta google",
    "senha da sua conta icloud",
    "senha do icloud",
    "senha do google",
    "senha do id apple",
    "senha da apple",
    "email e senha",
)


def _sem_pedir_credencial(texto: str) -> None:
    baixo = texto.lower()
    for frase in _PEDIDOS_DE_CREDENCIAL:
        assert frase not in baixo, f"texto pede credencial de conta: {frase!r}"
    if "senha da sua conta" in baixo:
        assert "senha da sua conta você não precisa enviar" in baixo


def test_texto_da_mala_fala_em_segredo_do_cadeado():
    t = svc.texto_para(_dev("b001.26"), loja="ATV")
    assert svc._tipo_produto("b001.26") == "mala"
    assert t.startswith("Olá! Aqui é a loja ATV.")
    assert "mala do pedido 2609045AM9GKXX" in t
    assert "cadeado" in t and "segredo" in t
    assert "senha" not in t.lower()  # mala não tem senha, tem segredo
    _sem_pedir_credencial(t)


def test_texto_do_android_pede_a_senha_da_tela_e_a_saida_da_conta():
    t = svc.texto_para(_dev("dg050.sa"))
    assert svc._tipo_produto("dg050.sa") == "aparelho"
    assert t.startswith("Olá! Aqui é a loja do seu pedido.")
    assert "senha de desbloqueio da tela" in t
    assert "conta Google ou iCloud" in t and "removê-lo da conta" in t
    assert "a senha da sua conta você não precisa enviar" in t
    _sem_pedir_credencial(t)


def test_texto_da_apple_manda_remover_pelo_buscar():
    t = svc.texto_para(_dev("i014.sa", produtos="iPhone 11"))
    assert svc._tipo_produto("i014.sa") == "apple"
    assert "senha de desbloqueio da tela" in t
    assert "app Buscar" in t and "conta iCloud" in t
    assert "Google" not in t
    _sem_pedir_credencial(t)


def test_tipo_do_produto_pelo_primeiro_item_do_kit():
    assert svc._tipo_produto("b001.26+dg050") == "mala"
    assert svc._tipo_produto("dg050.sa,b001.26") == "aparelho"
    assert svc._tipo_produto(None) == "aparelho"  # sem SKU, o texto genérico
    assert svc.motivo_pede_senha(_dev("dg050.sa")) is True
    assert svc.motivo_pede_senha(_dev("dg050.sa", motivo_devolucao="mudou de ideia")) is True
    assert svc.motivo_pede_senha(_dev("dg050.sa", motivo_devolucao="Golpe")) is False


# ---------------------------------------------------------------- 10. guarda de regressão


async def test_mensagem_ao_comprador_nao_mexe_na_coluna_status_da_aba_chamados(
    client, make_user, auth_as, db, shopee, adiar
):
    """A coluna Status (e a "Últ. resposta") sai da última FALA `enviada`/
    `recebida` da contestação. Falar com o COMPRADOR é outra conversa — por isso
    o evento é `direcao="sistema"`. Se um dia virar fala, a aba passa a mostrar
    "respondemos" num chamado que a plataforma nunca respondeu."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="295015", order_sn="2609045AM9GK15")

    def _foto(payload: dict) -> dict:
        item = next(i for i in payload["items"] if i["pedido_bling"] == "295015")
        return {k: item[k] for k in (
            "status_aba", "status_aba_at", "status_aba_motivo",
            "ultima_resposta_at", "ultima_resposta_direcao", "ultima_resposta_autor",
            "status_plataforma", "status_plataforma_at", "resolvido",
        )} | {"mensagens": item["mensagens_total"]}

    antes_resp = await client.get("/api/chamados", params={"origem": "devolucao"})
    assert antes_resp.status_code == 200, antes_resp.text
    antes = _foto(antes_resp.json())
    assert antes["status_aba"]  # a linha já tem um status de verdade

    assert (await svc.processar_pendentes(db))["enviadas"] == 1
    assert len(shopee.textos) == 1

    depois = _foto((await client.get("/api/chamados", params={"origem": "devolucao"})).json())
    assert depois["mensagens"] == antes["mensagens"] + 1  # o evento entrou mesmo
    assert depois["ultima_resposta_at"] is None  # não virou "Últ. resposta"
    assert {k: v for k, v in depois.items() if k != "mensagens"} == {
        k: v for k, v in antes.items() if k != "mensagens"
    }

    ch = await _chamado(db, "295015")
    ev = (await _eventos_comprador(db, ch.id))[0]
    assert ev.direcao == "sistema" and ev.status != "enviada"


# ---------------------------------------------------------------- 11. varredura dos atrasados


async def test_varredura_pega_lancamento_antigo_que_nunca_teve_pedido_de_senha(
    client, make_user, auth_as, db, shopee
):
    """A funcionalidade nasceu em 22/09; o que foi lançado antes (eram ~40
    pedidos de Bloqueado da Shopee nos últimos 30 dias) nunca teve mensagem. A
    varredura do cron :25 é quem alcança esses — e é ela que substituiu o botão
    que existiu por algumas horas em 22/09."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="295101", order_sn="260904ANTIGO1")
    # Apaga a linha da mensagem: é o estado de um lançamento anterior à entrega.
    await db.execute(delete(DevolucaoMensagemComprador))
    await db.commit()
    shopee.enviadas.clear()
    shopee.buyers.clear()

    r = await svc.varrer_sem_mensagem(db, dias=30, limite=20)
    assert r == {"candidatos": 1, "criadas": 1, "enviadas": 1, "sem_envio": {}}
    assert len(shopee.textos) == 1
    linha = await _linha(db, "295101")
    assert linha is not None and linha.status == "enviada"

    # a segunda rodada não manda de novo (é o que impede a enxurrada diária)
    shopee.enviadas.clear()
    r2 = await svc.varrer_sem_mensagem(db, dias=30, limite=20)
    assert r2 == {"candidatos": 0, "criadas": 0, "enviadas": 0, "sem_envio": {}}
    assert shopee.textos == []


async def test_varredura_respeita_o_teto_a_janela_e_quem_ja_falhou(
    client, make_user, auth_as, db, shopee
):
    user = await make_user(permissions=_perms())
    auth_as(user)
    for i, pedido in enumerate(("295201", "295202", "295203")):
        await _lancar(client, db, user, pedido=pedido, order_sn=f"26090{i}TETO{i}")
    # Estado de partida: nenhuma mensagem, como nos lançamentos antigos.
    await db.execute(delete(DevolucaoMensagemComprador))
    await db.commit()
    shopee.enviadas.clear()

    # teto por rodada: manda 2 agora, o resto na hora seguinte
    r = await svc.varrer_sem_mensagem(db, dias=30, limite=2)
    assert (r["candidatos"], r["enviadas"]) == (2, 2)
    assert len(shopee.textos) == 2
    r2 = await svc.varrer_sem_mensagem(db, dias=30, limite=2)
    assert (r2["candidatos"], r2["enviadas"]) == (1, 1)

    # quem já esgotou as tentativas não é ressuscitado pela varredura
    linha = await _linha(db, "295201")
    linha.status, linha.tentativas, linha.erro = "falhou", 3, "shopee fora do ar"
    await db.commit()
    shopee.enviadas.clear()
    r3 = await svc.varrer_sem_mensagem(db, dias=30, limite=10)
    assert r3 == {"candidatos": 0, "criadas": 0, "enviadas": 0, "sem_envio": {}}
    assert shopee.textos == []


async def test_plataforma_sai_do_nome_da_conta_quando_o_resto_esta_calado(
    client, make_user, auth_as, db, shopee
):
    """Duas rodadas da varredura em produção (22/09) voltaram "candidatos=20,
    criadas=0": lançamento antigo, sem chamado e sem espelho do pedido, ficava
    sem plataforma e era marcado "sem canal" — sendo que a conta se chama
    "Shopee ATV" e diz de qual plataforma é. O nome da conta é o último recurso."""
    assert svc._plataforma_pelo_nome_da_conta("Shopee ATV") == "shopee"
    assert svc._plataforma_pelo_nome_da_conta("shopee marquezini") == "shopee"
    assert svc._plataforma_pelo_nome_da_conta("TikTok Mini") == "tiktok"
    assert svc._plataforma_pelo_nome_da_conta("ML Injox") == "ml"
    assert svc._plataforma_pelo_nome_da_conta("Mercado Livre Kia") == "ml"
    assert svc._plataforma_pelo_nome_da_conta("Amazon KFA") == "amazon"
    # não inventa plataforma pra loja que não diz (ex.: "Loja 206081922")
    assert svc._plataforma_pelo_nome_da_conta("Loja 206081922") is None
    assert svc._plataforma_pelo_nome_da_conta("") is None

    # e ponta a ponta: devolução SEM chamado e SEM espelho do pedido continua
    # alcançando o comprador porque a conta diz "Shopee".
    user = await make_user(permissions=_perms())
    auth_as(user)
    dev = Devolution(
        conta="Shopee Marquezini",
        pedido_bling="295301",
        pedido_marketplace="260904SEMESPELHO",
        sku="dg050.sa",
        motivo_devolucao="Bloqueado",
    )
    db.add(dev)
    await db.commit()
    linha = await svc.garantir(db, dev)
    assert linha is not None and linha.status == "pendente"
    assert linha.plataforma == "shopee"


async def test_linha_sem_canal_volta_a_ser_avaliada_quando_a_plataforma_aparece(
    client, make_user, auth_as, db, shopee
):
    """Rodadas de 14h25 e 15h25 de 22/09: antes de o nome da conta virar último
    recurso, lançamento sem chamado e sem espelho do pedido nascia `sem_canal`.
    Se a varredura pulasse essas linhas pra sempre, aqueles pedidos nunca mais
    receberiam mensagem — mesmo depois do conserto."""
    user = await make_user(permissions=_perms())
    auth_as(user)
    await _lancar(client, db, user, pedido="295401", order_sn="260904SEMCANAL")
    linha = await _linha(db, "295401")
    assert linha is not None
    # estado em que aquelas linhas ficaram: marcadas como sem canal
    linha.status, linha.erro, linha.plataforma = "sem_canal", "sem_canal_plataforma", None
    await db.commit()
    shopee.enviadas.clear()

    r = await svc.varrer_sem_mensagem(db, dias=30, limite=10)

    assert (r["candidatos"], r["enviadas"]) == (1, 1)
    linha = await _linha(db, "295401")
    assert linha is not None and (linha.status, linha.plataforma) == ("enviada", "shopee")
    assert len(shopee.textos) == 1
