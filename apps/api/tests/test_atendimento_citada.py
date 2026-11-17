# ruff: noqa: F811  (fixtures importadas do apoio do Zap)
"""A mensagem CITADA na tela (17/11/2026) — `services/atendimento/citacao.py`.

O dono: "a equipe mandou as malas e a pessoa respondeu na mala que queria, mas no
nosso comunicador não mostra em qual mensagem ele respondeu; no Duoke isso
aparecia". O que se garante:
- a parte pura: de onde vem a citação (Shopee `payload.quoted_msg`, Zap
  `payload.zap.contexto_wamid`), a régua da URL da miniatura, o tipo, o texto
  (até 200), a foto e de quem é (o `from_id` contra o comprador da conversa);
- o detalhe da conversa: a mensagem com `quoted_msg` sai com `citada` — a nossa
  linha achada pelo `externo_id` (a foto da mala, o texto, a mandada pelo
  DaVinci); a que não está gravada vem da crua, sem `mensagem_id`; sem citação, None;
- uma consulta para a página toda (sem N+1), nenhuma com a citada na página;
- as abas (o pré-venda do chat mora noutra aba) também trazem a citada;
- o Zap: a resposta do cliente com `context.id` = o wamid da nossa resposta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import citacao, contexto, enviar, gravar
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph, envio)
    corpo,
    envio,
    graph,
    gravar_corpo,
    processar,
    zap,
)

URL = "/api/atendimento"
AGORA = datetime.now(UTC).replace(microsecond=0)
COMPRA = AGORA - timedelta(days=3)
SHOP = 555
LOJA_USER = 222
COMPRADOR = "777"
SN = "251114MALA01"
FOTO_AZUL = "https://img.sp.mms.shopee.sg/br-11134201-mala-azul"
FOTO_PRETA = "https://img.sp.mms.shopee.sg/br-11134201-mala-preta"


@pytest.fixture(autouse=True)
def _tela(monkeypatch):
    """A permissão fina (a trava de admin tem os testes dela), tudo desligado e o
    contexto do pedido vazio (o Bling é de outro lote)."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    s = get_settings()
    for nome in ("atendimento_envio_ativo", "atendimento_ia_ativa", "atendimento_simulador"):
        monkeypatch.setattr(s, nome, False)

    async def vazio(session, conversa):
        return contexto.vazio()

    monkeypatch.setattr(contexto, "contexto_da_conversa", vazio)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(role=UserRole.ADMIN)
    auth_as(u)
    return u


@pytest.fixture
def contar_consultas():
    contagem = {"n": 0}

    def _antes(*_a, **_kw) -> None:
        contagem["n"] += 1

    motor = _db.engine.sync_engine
    event.listen(motor, "before_cursor_execute", _antes)
    yield contagem
    event.remove(motor, "before_cursor_execute", _antes)


# ─────────────── a parte pura ───────────────


def _crua(tipo: str, content: dict, *, mid: str = "Q1", de: object = LOJA_USER) -> dict:
    """Uma mensagem no formato do `get_message` (o que vem no `quoted_msg`)."""
    da_loja = str(de) != COMPRADOR
    return {
        "message_id": mid,
        "from_id": de,
        "to_id": int(COMPRADOR) if da_loja else LOJA_USER,
        "from_shop_id": SHOP if da_loja else 987654,
        "to_shop_id": 987654 if da_loja else SHOP,
        "message_type": tipo,
        "content": content,
        "conversation_id": "conv-777",
        "status": "normal",
        "quoted_msg": None,
    }


CONVERSA = SimpleNamespace(comprador_id=COMPRADOR)


def test_referencia_vem_do_quoted_msg_ou_do_contexto_do_zap():
    crua = _crua("text", {"text": "oi"}, mid="2447014184345813361")
    ref = citacao.referencia(SimpleNamespace(payload={"quoted_msg": crua}))
    assert ref == citacao.Referencia("2447014184345813361", crua)
    zap_ref = citacao.referencia(SimpleNamespace(payload={"zap": {"contexto_wamid": "wamid.X"}}))
    assert zap_ref == citacao.Referencia("wamid.X")
    # O id numérico vira texto (é como o `externo_id` foi gravado).
    numero = citacao.referencia(SimpleNamespace(payload={"quoted_msg": {"message_id": 123}}))
    assert numero is not None and numero.externo_id == "123"
    # Sem citação (o `quoted_msg: null` de sempre), sem id, payload torto: None.
    for payload in (
        {"quoted_msg": None},
        {"quoted_msg": {"message_id": ""}},
        {"quoted_msg": {"message_id": True}},
        {"quoted_msg": "2447014184345813361"},
        {"zap": {"tipo_meta": "text", "encaminhada": True}},
        {},
        None,
        "lixo",
    ):
        assert citacao.referencia(SimpleNamespace(payload=payload)) is None, payload


def test_url_da_miniatura_e_a_regua_do_balao():
    assert citacao.url_da_miniatura(FOTO_AZUL) == FOTO_AZUL
    assert citacao.url_da_miniatura(f"  {FOTO_AZUL} ") == FOTO_AZUL
    # http vira https (a tela é https: conteúdo misto).
    assert citacao.url_da_miniatura("http://cf.shopee.com.br/file/x") == (
        "https://cf.shopee.com.br/file/x"
    )
    # A nossa cópia do Zap: caminho relativo da própria API.
    copia = "/api/atendimento/zap/midia/0b6f2c1e-3c33-4d4a-9a55-0c5d3b6a8f00"
    assert citacao.url_da_miniatura(copia) == copia
    for ruim in (
        "//outro.site/x.jpg",
        "/\\outro.site/x.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        # O `thumb_url` do `image_with_text` da Shopee: um id solto, não URL.
        "br-11134231-7r98o-lzabc",
        "https://img.sp.mms.shopee.sg/a b",
        "https://",
        "",
        None,
        12,
        "https://x.test/" + "a" * 3000,
    ):
        assert citacao.url_da_miniatura(ruim) is None, ruim


def test_montar_pela_crua_cada_tipo_da_shopee():
    """A citada que não está gravada: tudo vem da crua, sem `mensagem_id`."""
    selo = "https://deo.shopeemobile.com/shopee/sticker/1.png"
    casos = [
        (_crua("text", {"text": "  Temos a\n azul  "}), "texto", "Temos a azul", None),
        (_crua("extended_markdown", {"text": "**Oi**"}), "texto", "**Oi**", None),
        # O `thumb_url` da foto vem vazio (medido 17/11/2026): vale o `url`.
        (_crua("image", {"url": FOTO_AZUL, "thumb_url": ""}), "imagem", None, FOTO_AZUL),
        (
            _crua(
                "image_with_text",
                {"thumb_url": "br-11134231-solto", "image_url": FOTO_PRETA, "title": "Mala ABS"},
            ),
            "imagem",
            "Mala ABS",
            FOTO_PRETA,
        ),
        (
            _crua("video", {"video_url": "https://down-bs-br.vod.susercontent.com/v"}),
            "video",
            None,
            None,
        ),
        (_crua("item", {"item_id": 1, "shop_id": SHOP}), "produto", None, None),
        (_crua("order", {"order_sn": SN}), "pedido", f"Pedido {SN}", None),
        (_crua("sticker", {"image_url": selo, "sticker_id": "1"}), "outro", "[Figurinha]", selo),
        (_crua("voucher", {"voucher_code": "X"}), "outro", "[Cupom]", None),
    ]
    for crua, tipo, texto, imagem in casos:
        c = citacao.montar(citacao.Referencia("Q1", crua), None, CONVERSA)
        assert c == {
            "externo_id": "Q1",
            "mensagem_id": None,
            "autor": "loja",
            "tipo": tipo,
            "texto": texto,
            "imagem_url": imagem,
        }, crua["message_type"]


def test_de_quem_e_a_citada_e_o_texto_curto():
    ref = citacao.Referencia
    # O `from_id` contra o comprador da conversa (o `from_shop_id` não separa: o
    # comprador também tem loja).
    do_comprador = citacao.montar(ref("Q1", _crua("text", {"text": "a"}, de=777)), None, CONVERSA)
    assert do_comprador["autor"] == "cliente"
    assert (
        citacao.montar(ref("Q1", _crua("text", {"text": "a"})), None, CONVERSA)["autor"] == "loja"
    )
    # Sem comprador na conversa: a nossa linha diz; sem ela, não se sabe.
    sem = SimpleNamespace(comprador_id=None)
    assert citacao.montar(ref("Q1", _crua("text", {"text": "a"})), None, sem)["autor"] is None
    linha = SimpleNamespace(
        id="m1", autor="sistema", tipo="texto", texto="Olá!", anexos=[], payload={}
    )
    assert citacao.montar(ref("Q1"), linha, sem)["autor"] == "loja", "a resposta automática da loja"
    linha_cliente = SimpleNamespace(
        id="m2", autor="cliente", tipo="texto", texto="a", anexos=[], payload={}
    )
    assert citacao.montar(ref("Q1"), linha_cliente, sem)["autor"] == "cliente"
    # O texto longo: 200 caracteres, com reticências.
    longo = citacao.montar(ref("Q1", _crua("text", {"text": "x" * 500})), None, CONVERSA)["texto"]
    assert len(longo) == 200 and longo.endswith("…")


def test_a_nossa_linha_manda_cartao_de_produto_e_tipos_do_zap():
    ref = citacao.Referencia("Q1", _crua("item", {"item_id": 9, "shop_id": SHOP}))
    produto = SimpleNamespace(
        id="m1",
        autor="loja",
        tipo="produto",
        texto=None,
        payload={},
        anexos=[
            {"tipo": "produto", "item_id": "9", "titulo": "Mala ABS Azul", "imagem": FOTO_AZUL}
        ],
    )
    c = citacao.montar(ref, produto, CONVERSA)
    assert (c["mensagem_id"], c["tipo"], c["texto"], c["imagem_url"]) == (
        "m1",
        "produto",
        "Mala ABS Azul",
        FOTO_AZUL,
    )
    # Zap: só o id na citação; a foto é a nossa cópia (caminho relativo).
    copia = "/api/atendimento/zap/midia/0b6f2c1e-3c33-4d4a-9a55-0c5d3b6a8f00"
    foto = SimpleNamespace(
        id="m2", autor="loja", tipo="imagem", texto=None, payload={"zap": {}},
        anexos=[{"tipo": "imagem", "midia_id": "0b6f", "url": copia}],
    )  # fmt: skip
    assert citacao.montar(citacao.Referencia("wamid.A"), foto, CONVERSA)["imagem_url"] == copia
    audio = SimpleNamespace(
        id="m3", autor="cliente", tipo="audio", texto=None, payload={"zap": {}},
        anexos=[{"tipo": "audio", "midia_id": "x", "status": "pendente"}],
    )  # fmt: skip
    c = citacao.montar(citacao.Referencia("wamid.B"), audio, CONVERSA)
    assert (c["tipo"], c["texto"], c["imagem_url"], c["autor"]) == (
        "outro",
        "[Áudio]",
        None,
        "cliente",
    )


# ─────────────── o detalhe da conversa (Shopee) ───────────────


async def _loja(db: AsyncSession, dono: User) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="mega",
        credentials=encrypt_json({"access_token": "t", "shop_id": SHOP}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="humano", status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _cena(db: AsyncSession, dono: User, *, pedido: bool = False) -> SimpleNamespace:
    """A equipe mandou as duas malas e perguntou; o comprador respondeu em cima.

    Com `pedido`, tudo isso é ANTES da compra (o pré-venda, que mora noutra aba)
    e há uma troca depois dela (o pós-venda)."""
    integ, canal = await _loja(db, dono)
    c, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-777",
        comprador_id=COMPRADOR,
        comprador_nome="Comprador",
        pedido_marketplace=SN if pedido else None,
        dados={"pedido_mkt": {"pedido": SN, "criado_em": COMPRA.isoformat()}} if pedido else None,
    )
    t = AGORA - timedelta(days=5) if pedido else AGORA - timedelta(hours=2)
    n = SimpleNamespace(conversa=c)

    async def grava(externo: str, autor: str, minuto: int, **campos) -> AtendimentoMensagem:
        m, criada = await gravar.gravar_mensagem(
            db, c, externo_id=externo, autor=autor, enviada_em=t + timedelta(minutes=minuto),
            texto=campos.pop("texto", None), **campos,
        )  # fmt: skip
        assert criada
        return m

    foto_azul = _crua("image", {"url": FOTO_AZUL, "thumb_url": ""}, mid="700001")
    n.azul = await grava(
        "700001", "loja", 0, tipo="imagem", anexos=[{"tipo": "imagem", "url": FOTO_AZUL}],
        payload=foto_azul,
    )  # fmt: skip
    # A preta saiu PELO DAVINCI: o payload é o do envio (sem `from_id`).
    n.preta = AtendimentoMensagem(
        conversa_id=c.id, externo_id="700002", autor="loja", origem="davinci_humano",
        tipo="imagem", anexos=[{"tipo": "imagem", "url": FOTO_PRETA}], status="enviada",
        enviada_em=t + timedelta(minutes=1), payload={"envio": {"message_id": "700002"}},
    )  # fmt: skip
    db.add(n.preta)
    await db.flush()
    pergunta = _crua("text", {"text": "Qual das duas você prefere?"}, mid="700003")
    n.pergunta = await grava(
        "700003", "loja", 2, texto="Qual das duas você prefere?", payload=pergunta
    )

    # As respostas do comprador, cada uma citando uma (o "responder" da Shopee).
    def do_comprador(mid: str, texto: str, citada: dict | None) -> dict:
        return {**_crua("text", {"text": texto}, mid=mid, de=int(COMPRADOR)), "quoted_msg": citada}

    preta_crua = _crua("image", {"url": FOTO_PRETA, "thumb_url": ""}, mid="700002")
    n.quero_azul = await grava(
        "700004", "cliente", 3, texto="quero essa",
        payload=do_comprador("700004", "quero essa", foto_azul),
    )  # fmt: skip
    n.ou_a_preta = await grava(
        "700005", "cliente", 4, texto="ou essa",
        payload=do_comprador("700005", "ou essa", preta_crua),
    )  # fmt: skip
    n.responde_pergunta = await grava(
        "700006", "cliente", 5, texto="a azul",
        payload=do_comprador("700006", "a azul", pergunta),
    )  # fmt: skip
    # Citou a própria pergunta.
    n.cita_a_si = await grava(
        "700007", "cliente", 6, texto="e então?",
        payload=do_comprador("700007", "e então?", n.quero_azul.payload),
    )  # fmt: skip
    # Citou uma mensagem que não está gravada aqui (antes da leitura começar).
    sumida = _crua("text", {"text": "Temos também a vermelha"}, mid="699999")
    n.cita_sumida = await grava(
        "700008", "cliente", 7, texto="tem foto?",
        payload=do_comprador("700008", "tem foto?", sumida),
    )  # fmt: skip
    n.sem_citacao = await grava(
        "700009", "cliente", 8, texto="obrigado",
        payload=do_comprador("700009", "obrigado", None),
    )  # fmt: skip
    if pedido:
        n.pos = await grava(
            "700010", "cliente", 60 * 24 * 4, texto="já enviou?",
            payload=do_comprador("700010", "já enviou?", None),
        )  # fmt: skip
    await db.commit()
    return n


def _esperado(n: SimpleNamespace) -> dict[str, dict | None]:
    return {
        str(n.quero_azul.id): {
            "externo_id": "700001", "mensagem_id": str(n.azul.id), "autor": "loja",
            "tipo": "imagem", "texto": None, "imagem_url": FOTO_AZUL,
        },
        str(n.ou_a_preta.id): {
            "externo_id": "700002", "mensagem_id": str(n.preta.id), "autor": "loja",
            "tipo": "imagem", "texto": None, "imagem_url": FOTO_PRETA,
        },
        str(n.responde_pergunta.id): {
            "externo_id": "700003", "mensagem_id": str(n.pergunta.id), "autor": "loja",
            "tipo": "texto", "texto": "Qual das duas você prefere?", "imagem_url": None,
        },
        str(n.cita_a_si.id): {
            "externo_id": "700004", "mensagem_id": str(n.quero_azul.id), "autor": "cliente",
            "tipo": "texto", "texto": "quero essa", "imagem_url": None,
        },
        str(n.cita_sumida.id): {
            "externo_id": "699999", "mensagem_id": None, "autor": "loja",
            "tipo": "texto", "texto": "Temos também a vermelha", "imagem_url": None,
        },
    }  # fmt: skip


async def test_detalhe_traz_a_citada_de_cada_mensagem(client, db, pessoa):
    n = await _cena(db, pessoa)
    r = await client.get(f"{URL}/conversas/{n.conversa.id}")
    assert r.status_code == 200, r.text
    citadas = {m["id"]: m["citada"] for m in r.json()["mensagens"]}
    esperado = _esperado(n)
    for mid, citada in citadas.items():
        assert citada == esperado.get(mid), mid
    # Sem citação: None (a loja, o "obrigado") — o cliente antigo não quebra.
    for m in (n.azul, n.preta, n.pergunta, n.sem_citacao):
        assert citadas[str(m.id)] is None


async def test_uma_consulta_para_a_pagina_e_nenhuma_com_a_citada_nela(db, pessoa, contar_consultas):
    n = await _cena(db, pessoa)
    conversas = {n.conversa.id: n.conversa}
    respostas = [n.quero_azul, n.ou_a_preta, n.responde_pergunta, n.cita_a_si, n.cita_sumida]
    # Só as respostas na página: as citadas vêm do banco numa consulta só.
    contar_consultas["n"] = 0
    saida = await citacao.citadas_em_lote(db, [*respostas, n.sem_citacao], conversas)
    assert contar_consultas["n"] == 1
    esperado = _esperado(n)
    assert {str(k): v for k, v in saida.items()} == esperado
    # A página inteira, menos a que cita a sumida: tudo à mão, nenhuma consulta.
    pagina = [n.azul, n.preta, n.pergunta, *respostas[:-1], n.sem_citacao]
    contar_consultas["n"] = 0
    saida = await citacao.citadas_em_lote(db, pagina, conversas)
    assert contar_consultas["n"] == 0
    assert len(saida) == 4
    # Sem citação nenhuma: nada.
    assert await citacao.citadas_em_lote(db, [n.azul, n.sem_citacao], conversas) == {}
    assert contar_consultas["n"] == 0


async def test_abas_trazem_a_citada_do_pre_venda(client, db, pessoa):
    """O pré-venda do chat mora noutra aba: a rota das abas também traz a citada."""
    n = await _cena(db, pessoa, pedido=True)
    r = await client.get(f"{URL}/conversas/{n.conversa.id}/abas")
    assert r.status_code == 200, r.text
    corpo_abas = r.json()
    assert corpo_abas["aba_da_conversa"] == "pos_venda"
    pre = next(a for a in corpo_abas["abas"] if a["chave"] == "pre_venda")
    citadas = {m["id"]: m["citada"] for m in pre["mensagens"]}
    assert citadas[str(n.quero_azul.id)] == _esperado(n)[str(n.quero_azul.id)]
    assert citadas[str(n.sem_citacao.id)] is None
    # A citada e quem cita estão na mesma aba: o clique acha a foto ali.
    assert corpo_abas["fora_da_aba"][str(n.azul.id)] == "pre_venda"


# ─────────────── o Zap ───────────────


async def test_zap_resposta_com_contexto_cita_a_nossa_mensagem(db, envio, pessoa, client):
    nossa = await enviar.enviar_resposta(
        db, envio.conversa, "Temos a azul e a preta. Qual prefere?", user=pessoa
    )
    assert nossa.status == "enviada" and nossa.externo_id, (nossa.status, nossa.erro)

    def com_contexto(wamid: str, texto: str, alvo: str) -> dict:
        c = corpo("texto", wamid=wamid, texto=texto, ts=datetime.now(UTC).replace(microsecond=0))
        msg = c["entry"][0]["changes"][0]["value"]["messages"][0]
        msg["context"] = {"from": "5511900000000", "id": alvo}
        return c

    await gravar_corpo(com_contexto("wamid.AZUL", "a azul", nossa.externo_id), app=envio.app)
    await gravar_corpo(com_contexto("wamid.CEL", "e essa?", "wamid.DO_CELULAR"), app=envio.app)
    assert (await processar())["processados"] == 2

    r = await client.get(f"{URL}/conversas/{envio.conversa.id}")
    assert r.status_code == 200, r.text
    por_texto = {m["texto"]: m for m in r.json()["mensagens"]}
    assert por_texto["a azul"]["citada"] == {
        "externo_id": nossa.externo_id,
        "mensagem_id": str(nossa.id),
        "autor": "loja",
        "tipo": "texto",
        "texto": "Temos a azul e a preta. Qual prefere?",
        "imagem_url": None,
    }
    # A citada que não está aqui (mandada pelo celular): só o id.
    assert por_texto["e essa?"]["citada"] == {
        "externo_id": "wamid.DO_CELULAR",
        "mensagem_id": None,
        "autor": None,
        "tipo": "outro",
        "texto": None,
        "imagem_url": None,
    }
    assert por_texto["oi, vocês entregam em São Paulo?"]["citada"] is None
    assert por_texto["Temos a azul e a preta. Qual prefere?"]["citada"] is None
