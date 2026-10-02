"""As ABAS da conversa aberta (RF2, 02/10/2026): GET /conversas/{id}/abas.

Pré-venda · Pós-venda · Reclamação · Mediador · E-mail · Zap · Avaliação, com
tudo do mesmo comprador e do mesmo pedido, NA MESMA LOJA. Sem rede: as
conversas e mensagens vão direto para o banco de teste, no formato que os
leitores gravam em produção (02/10/2026):

- ML: a pergunta (`q:<id>`, `anuncio_id` = o anúncio, `dados.produto` com
  título e foto), o pack (`pedido_marketplace` = PACK, `dados.pack_id`, o
  retrato `dados.pedido_mkt` com o ORDER, a hora da compra e os itens SEM o
  id do anúncio), a conversa `reclamacao` (gravada pelo ORDER, com o
  mediador) e a `avaliacao`;
- Shopee: o chat (um por comprador, com o pedido e o retrato), a conversa
  `reclamacao` da devolução e a `avaliacao` (sem `comprador_id`, como 6 das
  10 em produção).

O que se mede: cada mensagem na sua aba, a ordem das abas, a contagem, a aba
padrão, o que a conversa aberta tem em outra aba, quem responde em cada aba
(e que continua bloqueado com o envio desligado), a página das mais antigas,
e o principal — comprador diferente e loja diferente NUNCA entram, nem com o
mesmo nº de pedido. E que as consultas não crescem com a família (sem N+1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    Integration,
    IntegrationPlatform,
    StoreInfo,
    UserRole,
)
from app.routers import atendimento as rota_atendimento
from app.security.cipher import encrypt_json
from app.services.atendimento import abas, enviar
from app.services.atendimento.constantes import CANAL_ZAP, FONTE_TUTA

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
URL = "/api/atendimento/conversas/{}/abas"
COMPRA_ML = T0 - timedelta(days=5)
COMPRA_SHOPEE = T0 - timedelta(days=3)

ORDER = "2000018509205724"
PACK = "2000009999999999"
BUYER = "111"
OUTRO = "222"
SN = "250930ABCDEF12"
SN2 = "250930ZZZZZZ99"
MALA = "Mala de Bordo ABS 10kg"
FOTO_MALA = "https://http2.mlstatic.com/D_NQ_NP_mala-F.jpg"
# O e-mail do Tuta, como o contrato com o outro dev manda gravar.
TUTA = {"fonte": FONTE_TUTA}
RELAY = "abc123@marketplace.amazon.com.br"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    # A trava de admin tem teste próprio (test_atendimento_so_admin); aqui
    # vale a permissão fina, como nos outros testes do atendimento.
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)


@pytest.fixture
async def admin(make_user, auth_as):
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


# ─────────────── montagem ───────────────


async def _loja(db: AsyncSession, user, plataforma: IntegrationPlatform, nome: str) -> UUID:
    integ = Integration(
        user_id=user.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r"}),
    )
    db.add(integ)
    await db.commit()
    return integ.id


async def _conversa(
    db: AsyncSession, integ: UUID | None, plataforma: str, canal: str, externo: str, **campos
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        integration_id=integ, plataforma=plataforma, canal=canal, externo_id=externo, **campos
    )
    db.add(c)
    await db.flush()
    return c


async def _msgs(db: AsyncSession, conversa: AtendimentoConversa, *itens) -> list[str]:
    """(autor, hora, texto) → as mensagens, em ordem; devolve os ids."""
    ids = []
    for autor, quando, texto in itens:
        m = AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=uuid4().hex,
            autor=autor,
            origem={"cliente": "cliente", "loja": "externo"}.get(autor, "sistema"),
            tipo="texto",
            texto=texto,
            enviada_em=quando,
            status="recebida" if autor != "loja" else "enviada",
        )
        db.add(m)
        await db.flush()
        ids.append(str(m.id))
    if itens:
        conversa.ultima_mensagem_em = max(q for _, q, _ in itens)
    return ids


def _produto(titulo: str, imagem: str | None = None) -> dict:
    return {"tipo": "produto", "titulo": titulo, "imagem": imagem, "preco": 199.9}


async def _cenario_ml(db: AsyncSession, user) -> SimpleNamespace:
    """O caso da Tela 1: perguntou, comprou, reclamou (com o mediador) e avaliou."""
    a = await _loja(db, user, IntegrationPlatform.ML, "aguiar")
    b = await _loja(db, user, IntegrationPlatform.ML, "outra loja")
    n = SimpleNamespace(loja=a, outra_loja=b)

    # Pré-venda: perguntou no anúncio da mala ANTES de comprar.
    n.p1 = await _conversa(
        db, a, "ml", "pergunta", "q:1", comprador_id=BUYER, anuncio_id="MLB1",
        anuncio_titulo=MALA, ultima_do_cliente_em=T0 - timedelta(days=10),
        dados={"item_id": "MLB1", "produto": _produto(MALA, FOTO_MALA)},
    )  # fmt: skip
    n.p1_msgs = await _msgs(
        db, n.p1,
        ("cliente", T0 - timedelta(days=10), "tem azul?"),
        ("loja", T0 - timedelta(days=10) + timedelta(hours=1), "temos sim"),
    )  # fmt: skip
    # Outro anúncio da mesma mala (variação): casa pelo TÍTULO do item do pedido.
    n.p5 = await _conversa(
        db, a, "ml", "pergunta", "q:5", comprador_id=BUYER, anuncio_id="MLB5",
        anuncio_titulo=f"  {MALA.upper()} ", aguardando_resposta=True,
        ultima_do_cliente_em=T0 - timedelta(days=8),
        dados={"item_id": "MLB5", "produto": _produto(MALA)},
    )  # fmt: skip
    n.p5_msgs = await _msgs(db, n.p5, ("cliente", T0 - timedelta(days=8), "cabe na cabine?"))
    # Depois da compra, no mesmo anúncio: não é pré-venda deste pedido.
    n.p2 = await _conversa(
        db, a, "ml", "pergunta", "q:2", comprador_id=BUYER, anuncio_id="MLB1",
        anuncio_titulo=MALA, ultima_do_cliente_em=T0 - timedelta(days=1),
        dados={"item_id": "MLB1", "produto": _produto(MALA, FOTO_MALA)},
    )  # fmt: skip
    n.p2_msgs = await _msgs(db, n.p2, ("cliente", T0 - timedelta(days=1), "como abre o cadeado?"))
    # Outro anúncio (outro produto), antes da compra: fica de fora.
    n.p3 = await _conversa(
        db, a, "ml", "pergunta", "q:3", comprador_id=BUYER, anuncio_id="MLB9",
        anuncio_titulo="Cadeado TSA", ultima_do_cliente_em=T0 - timedelta(days=12),
        dados={"item_id": "MLB9", "produto": _produto("Cadeado TSA")},
    )  # fmt: skip
    n.p3_msgs = await _msgs(db, n.p3, ("cliente", T0 - timedelta(days=12), "tem preto?"))
    # OUTRO COMPRADOR no mesmo anúncio, antes da compra: NUNCA entra.
    n.p4 = await _conversa(
        db, a, "ml", "pergunta", "q:4", comprador_id=OUTRO, anuncio_id="MLB1",
        anuncio_titulo=MALA, ultima_do_cliente_em=T0 - timedelta(days=9),
        dados={"item_id": "MLB1", "produto": _produto(MALA, FOTO_MALA)},
    )  # fmt: skip
    n.p4_msgs = await _msgs(db, n.p4, ("cliente", T0 - timedelta(days=9), "SEGREDO DO OUTRO"))

    # Pós-venda: o pack (o retrato não guarda o id do anúncio).
    n.pack = await _conversa(
        db, a, "ml", "pos_venda", PACK, comprador_id=BUYER, pedido_marketplace=PACK,
        dados={
            "pack_id": PACK,
            "pedido_mkt": {
                "pedido": ORDER,
                "criado_em": COMPRA_ML.isoformat(),
                "itens": [{"titulo": MALA, "imagem": FOTO_MALA, "sku": "MALA-10"}],
            },
        },
    )  # fmt: skip
    n.pack_msgs = await _msgs(
        db, n.pack,
        ("cliente", T0 - timedelta(days=4), "quando chega?"),
        ("loja", T0 - timedelta(days=4) + timedelta(hours=1), "amanhã"),
        ("cliente", T0 - timedelta(days=3), "chegou riscada"),
    )  # fmt: skip

    # Reclamação: gravada pelo ORDER, com o mediador falando.
    n.recl = await _conversa(
        db, a, "ml", "reclamacao", "5582543195", comprador_id=BUYER, pedido_marketplace=ORDER,
        anuncio_id="MLB1", situacao="bloqueada", bloqueio_motivo="so_leitura",
    )  # fmt: skip
    d2 = T0 - timedelta(days=2)
    n.recl_msgs = await _msgs(
        db, n.recl,
        ("sistema", d2, "Reclamação 5582543195 aberta no Mercado Livre"),
        ("cliente", d2 + timedelta(hours=1), "quero devolver"),
        ("mediador", d2 + timedelta(hours=2), "A loja tem 2 dias para responder"),
        ("loja", d2 + timedelta(hours=3), "pode devolver"),
        ("mediador", T0 - timedelta(days=1), "Devolução aprovada"),
    )  # fmt: skip
    db.add(
        AtendimentoReclamacao(
            integration_id=a, conversa_id=n.recl.id, plataforma="ml", externo_id="5582543195",
            tipo="reclamacao", status="opened", pedido_marketplace=ORDER,
            dados={"pack_id": PACK},
        )
    )  # fmt: skip

    # Avaliação do pedido (pendente: tem conversa).
    n.aval = await _conversa(
        db, a, "ml", "avaliacao", "rev-1", comprador_id=BUYER, pedido_marketplace=ORDER,
        anuncio_id="MLB1", anuncio_titulo=MALA, situacao="bloqueada",
    )  # fmt: skip
    n.aval_msgs = await _msgs(db, n.aval, ("cliente", T0 - timedelta(hours=20), "veio riscada"))

    # ── o que NUNCA pode entrar ──
    # Outra loja, mesmo comprador, MESMO nº de pedido.
    n.x_loja = await _conversa(
        db, b, "ml", "pos_venda", "x-loja", comprador_id=BUYER, pedido_marketplace=ORDER,
        dados={"pack_id": ORDER},
    )  # fmt: skip
    n.x_loja_msgs = await _msgs(db, n.x_loja, ("cliente", T0 - timedelta(days=4), "OUTRA LOJA"))
    # Mesma loja, OUTRO comprador, mesmo nº de pedido (dado torto): fora.
    n.x_comprador = await _conversa(
        db, a, "ml", "pos_venda", "x-comprador", comprador_id=OUTRO, pedido_marketplace=ORDER,
    )  # fmt: skip
    n.x_comprador_msgs = await _msgs(
        db, n.x_comprador, ("cliente", T0 - timedelta(days=4), "OUTRO COMPRADOR")
    )
    # Mesmo comprador, OUTRA compra (outro pack): fora.
    n.x_pedido = await _conversa(
        db, a, "ml", "pos_venda", "PACK-2", comprador_id=BUYER, pedido_marketplace="PACK-2",
        dados={"pack_id": "PACK-2", "pedido_mkt": {"pedido": "ORDER-2", "itens": []}},
    )  # fmt: skip
    n.x_pedido_msgs = await _msgs(
        db, n.x_pedido, ("cliente", T0 - timedelta(days=30), "OUTRA COMPRA")
    )
    await db.commit()
    return n


async def _cenario_shopee(db: AsyncSession, user, *, retrato: bool = True) -> SimpleNamespace:
    s = await _loja(db, user, IntegrationPlatform.SHOPEE, "mega")
    n = SimpleNamespace(loja=s)
    dados = (
        {"pedido_mkt": {"pedido": SN, "criado_em": COMPRA_SHOPEE.isoformat()}} if retrato else {}
    )
    n.chat = await _conversa(
        db, s, "shopee", "chat", "conv-777", comprador_id="777", pedido_marketplace=SN, dados=dados,
    )  # fmt: skip
    n.pre = await _msgs(
        db, n.chat,
        ("cliente", T0 - timedelta(days=5), "tem pronta entrega?"),
        ("loja", T0 - timedelta(days=5) + timedelta(hours=1), "tem sim"),
    )  # fmt: skip
    n.pos = await _msgs(
        db, n.chat,
        ("cliente", T0 - timedelta(days=2), "já enviou?"),
        ("loja", T0 - timedelta(days=1), "enviado"),
    )  # fmt: skip
    n.chat.ultima_mensagem_em = T0 - timedelta(days=1)
    n.devol = await _conversa(
        db, s, "shopee", "reclamacao", "RS123", comprador_id="777", pedido_marketplace=SN,
        situacao="bloqueada",
    )  # fmt: skip
    n.devol_msgs = await _msgs(
        db, n.devol,
        ("sistema", T0 - timedelta(days=1), "Devolução solicitada na Shopee"),
        ("sistema", T0 - timedelta(hours=12), "Devolução aprovada"),
    )  # fmt: skip
    db.add(
        AtendimentoReclamacao(
            integration_id=s, conversa_id=n.devol.id, plataforma="shopee", externo_id="RS123",
            tipo="devolucao", status="REQUESTED", pedido_marketplace=SN,
        )
    )  # fmt: skip
    # A avaliação da Shopee chega sem `comprador_id` (6 de 10 em produção).
    n.aval = await _conversa(
        db, s, "shopee", "avaliacao", "c-1", pedido_marketplace=SN, situacao="aberta",
    )  # fmt: skip
    n.aval_msgs = await _msgs(db, n.aval, ("cliente", T0 - timedelta(hours=6), "★★ demorou"))
    # Outro comprador: o chat dele, a devolução dele — e uma devolução torta
    # com o NOSSO nº de pedido. Nada disso entra.
    n.outro = await _conversa(
        db, s, "shopee", "chat", "conv-888", comprador_id="888", pedido_marketplace=SN2,
    )  # fmt: skip
    n.outro_msgs = await _msgs(db, n.outro, ("cliente", T0 - timedelta(days=2), "SEGREDO DO 888"))
    n.outro_devol = await _conversa(
        db, s, "shopee", "reclamacao", "RS888", comprador_id="888", pedido_marketplace=SN,
    )  # fmt: skip
    n.outro_devol_msgs = await _msgs(
        db, n.outro_devol, ("sistema", T0 - timedelta(days=1), "DEVOLUÇÃO DO 888")
    )
    await db.commit()
    return n


async def _abas(client, conversa_id, **params) -> dict:
    r = await client.get(URL.format(conversa_id), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _aba(corpo: dict, chave: str) -> dict:
    achada = next((a for a in corpo["abas"] if a["chave"] == chave), None)
    assert achada is not None, (chave, [a["chave"] for a in corpo["abas"]])
    return achada


def _ids(corpo: dict) -> set[str]:
    """Todas as mensagens que a resposta mostra, em todas as abas."""
    return {m["id"] for a in corpo["abas"] for m in a["mensagens"]}


def _conversas(corpo: dict) -> set[str]:
    return {c["id"] for a in corpo["abas"] for c in a["conversas"]}


# ─────────────── Mercado Livre ───────────────


async def test_ml_pack_junta_pergunta_reclamacao_mediador_e_avaliacao(db, client, admin):
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.pack.id)

    assert corpo["conversa_id"] == str(n.pack.id)
    assert corpo["aba_da_conversa"] == "pos_venda"
    # Na ordem da tela, só as que têm conteúdo (sem E-mail nem Zap aqui).
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "mediador", "avaliacao",
    ]  # fmt: skip
    assert [a["rotulo"] for a in corpo["abas"]] == [
        "Pré-venda", "Pós-venda", "Reclamação", "Mediador", "Avaliação",
    ]  # fmt: skip
    totais = {a["chave"]: a["total"] for a in corpo["abas"]}
    assert totais == {
        "pre_venda": 3, "pos_venda": 3, "reclamacao": 3, "mediador": 2, "avaliacao": 1,
    }  # fmt: skip
    assert sorted(corpo["pedido"]) == sorted([ORDER, PACK])
    assert corpo["compra_em"].startswith(COMPRA_ML.isoformat()[:19])
    assert corpo["fora_da_aba"] == {}

    # Pré-venda: a pergunta de antes da compra (pelo anúncio) e a da variação
    # (pelo título do item do retrato). Nem a de depois, nem a de outro
    # anúncio, nem a do OUTRO comprador.
    pre = _aba(corpo, "pre_venda")
    assert {c["id"] for c in pre["conversas"]} == {str(n.p1.id), str(n.p5.id)}
    assert {m["id"] for m in pre["mensagens"]} == set(n.p1_msgs + n.p5_msgs)
    assert {m["canal"] for m in pre["mensagens"]} == {"pergunta"}
    assert {c["canal_rotulo"] for c in pre["conversas"]} == {"Pergunta no anúncio"}
    assert next(c for c in pre["conversas"] if c["id"] == str(n.p1.id))["titulo"] == MALA
    # Na aba da conversa ABERTA, as dela não vêm (o detalhe já traz).
    pos = _aba(corpo, "pos_venda")
    assert pos["mensagens"] == [] and pos["total"] == 3
    assert [c["id"] for c in pos["conversas"]] == [str(n.pack.id)]
    assert pos["conversas"][0]["aberta"] is True
    # Reclamação = comprador, loja e o aviso do sistema; Mediador = o mediador.
    recl = _aba(corpo, "reclamacao")
    assert [m["id"] for m in recl["mensagens"]] == [n.recl_msgs[i] for i in (0, 1, 3)]
    assert {m["conversa_id"] for m in recl["mensagens"]} == {str(n.recl.id)}
    assert recl["conversas"][0]["titulo"] == "nº 5582543195"
    med = _aba(corpo, "mediador")
    assert [m["id"] for m in med["mensagens"]] == [n.recl_msgs[2], n.recl_msgs[4]]
    assert {m["autor"] for m in med["mensagens"]} == {"mediador"}
    aval = _aba(corpo, "avaliacao")
    assert [m["id"] for m in aval["mensagens"]] == n.aval_msgs
    # As mensagens vêm da mais antiga para a mais nova, com o texto.
    assert [m["texto"] for m in recl["mensagens"]][1] == "quero devolver"

    # NUNCA: outra loja, outro comprador, outra compra, depois da compra,
    # outro anúncio.
    proibidas = (
        n.x_loja_msgs + n.x_comprador_msgs + n.x_pedido_msgs + n.p2_msgs + n.p3_msgs + n.p4_msgs
    )
    assert not _ids(corpo) & set(proibidas)
    assert not _conversas(corpo) & {
        str(x.id) for x in (n.x_loja, n.x_comprador, n.x_pedido, n.p2, n.p3, n.p4)
    }


async def test_ml_quem_responde_em_cada_aba_continua_bloqueado(db, client, admin):
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.pack.id)
    responde = {a["chave"]: a["responde"] for a in corpo["abas"]}

    # A conversa aberta responde na aba dela; nas outras, a conversa de origem
    # (na pré-venda, a pergunta que espera resposta).
    assert responde["pos_venda"]["conversa_id"] == str(n.pack.id)
    assert responde["pre_venda"]["conversa_id"] == str(n.p5.id)
    assert responde["pre_venda"]["canal"] == "pergunta"
    assert responde["reclamacao"]["conversa_id"] == str(n.recl.id)
    assert responde["avaliacao"]["conversa_id"] == str(n.aval.id)
    assert responde["avaliacao"]["publica"] is True
    assert responde["pos_venda"]["publica"] is False
    # Mediador: só leitura, ninguém responde por aqui.
    med = responde["mediador"]
    assert med["conversa_id"] is None and med["pode_enviar"] is False
    assert med["codigo"] == "somente_leitura"
    assert med["motivo"] == abas.MOTIVO_MEDIADOR
    # Envio desligado: nenhuma aba deixa enviar, todas dizem por quê.
    for chave, r in responde.items():
        assert r["pode_enviar"] is False, chave
        if chave != "mediador":
            assert r["codigo"] == "envio_desligado", (chave, r)
            assert r["limite_caracteres"]
    # O `ultima_vista_id` é a última mensagem da conversa que responde.
    assert responde["reclamacao"]["ultima_vista_id"] == n.recl_msgs[-1]
    assert responde["pre_venda"]["ultima_vista_id"] == n.p5_msgs[-1]


async def test_ultima_vista_e_a_mais_nova_mesmo_em_outra_aba(db, client, admin):
    """A reclamação se divide em Reclamação e Mediador: com a LOJA falando por
    último, a última vista é a da loja (não a do mediador, que vem antes na
    ordem das abas) — senão o envio acusaria `conversa_mudou` à toa."""
    n = await _cenario_ml(db, admin)
    await db.delete(await db.get(AtendimentoMensagem, UUID(n.recl_msgs[4])))
    await db.commit()
    corpo = await _abas(client, n.pack.id)
    assert _aba(corpo, "reclamacao")["responde"]["ultima_vista_id"] == n.recl_msgs[3]
    recl = await db.get(AtendimentoConversa, n.recl.id)
    await enviar._conferir_mudou(db, recl, UUID(n.recl_msgs[3]))  # não levanta
    with pytest.raises(enviar.EnvioRecusado):
        await enviar._conferir_mudou(db, recl, UUID(n.recl_msgs[2]))


async def test_ml_reclamacao_aberta_mostra_o_mediador_na_aba_dele(db, client, admin):
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.recl.id)

    assert corpo["aba_da_conversa"] == "reclamacao"
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "mediador", "avaliacao",
    ]  # fmt: skip
    # O mediador da conversa aberta mora na aba Mediador: a tela o tira da
    # vista da Reclamação e mostra lá.
    assert corpo["fora_da_aba"] == {n.recl_msgs[2]: "mediador", n.recl_msgs[4]: "mediador"}
    assert _aba(corpo, "reclamacao")["mensagens"] == []
    assert [m["id"] for m in _aba(corpo, "mediador")["mensagens"]] == [
        n.recl_msgs[2], n.recl_msgs[4],
    ]  # fmt: skip
    # O pack do mesmo pedido (achado pelo pack da reclamação) é o Pós-venda.
    pos = _aba(corpo, "pos_venda")
    assert [m["id"] for m in pos["mensagens"]] == n.pack_msgs
    assert {m["canal"] for m in pos["mensagens"]} == {"pos_venda"}
    assert pos["responde"]["conversa_id"] == str(n.pack.id)
    assert {m["id"] for m in _aba(corpo, "pre_venda")["mensagens"]} == set(n.p1_msgs + n.p5_msgs)
    assert not _ids(corpo) & set(n.x_loja_msgs + n.x_comprador_msgs + n.p4_msgs)


async def test_ml_pergunta_acha_a_compra_seguinte(db, client, admin):
    """A pergunta não tem pedido: a âncora é a compra seguinte, no mesmo anúncio."""
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.p1.id)

    assert corpo["aba_da_conversa"] == "pre_venda"
    assert sorted(corpo["pedido"]) == sorted([ORDER, PACK])
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "mediador", "avaliacao",
    ]  # fmt: skip
    pre = _aba(corpo, "pre_venda")
    assert pre["total"] == 3
    # Na aba dela, só as OUTRAS (a da variação).
    assert [m["id"] for m in pre["mensagens"]] == n.p5_msgs
    assert pre["responde"]["conversa_id"] == str(n.p1.id)
    assert [m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]] == n.pack_msgs
    assert not _ids(corpo) & set(n.p4_msgs + n.x_loja_msgs + n.x_comprador_msgs)


async def test_ml_pergunta_depois_da_compra_nao_vira_pre_venda_do_pedido(db, client, admin):
    """Comprou ANTES de perguntar: a pergunta não é pré-venda daquele pedido."""
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.p2.id)

    assert corpo["aba_da_conversa"] == "pre_venda"
    assert corpo["pedido"] == []
    assert [a["chave"] for a in corpo["abas"]] == ["pre_venda"]
    # As outras perguntas DELE no MESMO anúncio (a da variação é outro anúncio).
    pre = _aba(corpo, "pre_venda")
    assert {c["id"] for c in pre["conversas"]} == {str(n.p2.id), str(n.p1.id)}
    assert [m["id"] for m in pre["mensagens"]] == n.p1_msgs
    assert not _ids(corpo) & set(n.p4_msgs + n.pack_msgs)


async def test_ml_pergunta_de_outro_comprador_fica_sozinha(db, client, admin):
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.p4.id)
    assert [a["chave"] for a in corpo["abas"]] == ["pre_venda"]
    assert _conversas(corpo) == {str(n.p4.id)}
    assert _ids(corpo) == set()  # a aba dela: as dela vêm no detalhe


async def test_outra_loja_nunca_ve_nada_do_pedido(db, client, admin):
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.x_loja.id)
    assert _conversas(corpo) == {str(n.x_loja.id)}
    assert [a["chave"] for a in corpo["abas"]] == ["pos_venda"]
    corpo = await _abas(client, n.x_comprador.id)
    assert _conversas(corpo) == {str(n.x_comprador.id)}


async def test_email_e_zap_do_pedido_e_do_comprador(db, client, admin):
    """O contrato com o outro dev: e-mail do Tuta e Zap entram pelo nº do pedido
    ou pelo id do comprador NA PLATAFORMA (`dados.comprador_plataforma`)."""
    n = await _cenario_ml(db, admin)
    email = await _conversa(
        db, n.loja, "ml", "email", "msgid-1", comprador_id="fulano@exemplo.com",
        pedido_marketplace=ORDER, dados=TUTA,
    )  # fmt: skip
    email_msgs = await _msgs(db, email, ("cliente", T0 - timedelta(hours=30), "segue a foto"))
    zap = await _conversa(
        db, n.loja, "ml", CANAL_ZAP, "zap-1", comprador_id="5511999990000",
        dados={abas.CHAVE_COMPRADOR_PLATAFORMA: BUYER},
    )  # fmt: skip
    zap_msgs = await _msgs(db, zap, ("cliente", T0 - timedelta(hours=10), "oi, é sobre a mala"))
    # O Zap de OUTRO comprador (o id da plataforma é o do outro): nunca.
    zap_outro = await _conversa(
        db, n.loja, "ml", CANAL_ZAP, "zap-2", comprador_id="5511888880000",
        dados={abas.CHAVE_COMPRADOR_PLATAFORMA: OUTRO}, pedido_marketplace=ORDER,
    )  # fmt: skip
    zap_outro_msgs = await _msgs(
        db, zap_outro, ("cliente", T0 - timedelta(hours=9), "ZAP DO OUTRO")
    )
    await db.commit()

    corpo = await _abas(client, n.pack.id)
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "mediador", "email", "zap", "avaliacao",
    ]  # fmt: skip
    assert [m["id"] for m in _aba(corpo, "email")["mensagens"]] == email_msgs
    assert [m["id"] for m in _aba(corpo, "zap")["mensagens"]] == zap_msgs
    assert _aba(corpo, "zap")["responde"]["conversa_id"] == str(zap.id)
    assert _aba(corpo, "email")["responde"]["canal_rotulo"] == "E-mail"
    assert not _ids(corpo) & set(zap_outro_msgs)

    # Aberto pelo e-mail (só o contato, sem o id da plataforma): o comprador
    # é o do pedido — mas aqui o pedido diz DOIS (o 222 torto e o Zap dele).
    # Sem saber quem é, ninguém com id da plataforma entra.
    corpo = await _abas(client, email.id)
    assert corpo["aba_da_conversa"] == "email"
    assert _conversas(corpo) == {str(email.id)}
    # Sem o dado torto, o pedido diz um comprador só: as abas dele.
    await db.delete(n.x_comprador)
    await db.delete(zap_outro)
    await db.commit()
    corpo = await _abas(client, email.id)
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "mediador", "email", "zap", "avaliacao",
    ]  # fmt: skip
    assert {m["id"] for m in _aba(corpo, "pre_venda")["mensagens"]} == set(n.p1_msgs + n.p5_msgs)
    assert [m["id"] for m in _aba(corpo, "zap")["mensagens"]] == zap_msgs
    assert _aba(corpo, "email")["mensagens"] == []  # a aberta: vem no detalhe
    assert not _ids(corpo) & set(n.p4_msgs + n.x_loja_msgs)

    # Aberto pelo Zap sem venda ligada: só ele (a venda entra pelo nº, quando
    # for transferido para uma venda — RF10).
    corpo = await _abas(client, zap.id)
    assert corpo["aba_da_conversa"] == "zap"
    assert [a["chave"] for a in corpo["abas"]] == ["zap"]


async def test_email_de_aviso_nunca_junta_compradores(db, client, admin):
    """O mesmo remetente de AVISO (o noreply da plataforma) em e-mails do Tuta
    de compradores diferentes: o remetente não é o comprador — nunca junta."""
    n = await _cenario_ml(db, admin)
    await db.delete(n.x_comprador)  # sem o dado torto: o pedido diz um comprador só
    noreply = "noreply@mercadolivre.com"
    e1 = await _conversa(
        db, n.loja, "ml", "email", "tuta-a", comprador_id=noreply, pedido_marketplace=ORDER,
        dados=TUTA,
    )  # fmt: skip
    e1_msgs = await _msgs(db, e1, ("cliente", T0 - timedelta(hours=5), "aviso do pedido do 111"))
    e2 = await _conversa(
        db, n.loja, "ml", "email", "tuta-b", comprador_id=noreply,
        pedido_marketplace="ORDER-DO-333", aguardando_resposta=True, dados=TUTA,
    )  # fmt: skip
    e2_msgs = await _msgs(db, e2, ("cliente", T0 - timedelta(hours=4), "DADOS DO COMPRADOR 333"))
    # O aviso sem nº de pedido nenhum: também não é de ninguém.
    e3 = await _conversa(
        db, n.loja, "ml", "email", "tuta-c", comprador_id="Nao-Responder@MercadoLivre.com",
        aguardando_resposta=True, dados=TUTA,
    )  # fmt: skip
    e3_msgs = await _msgs(db, e3, ("cliente", T0 - timedelta(hours=3), "AVISO GERAL"))
    e4 = await _conversa(
        db, n.loja, "ml", "email", "tuta-d", comprador_id="Nao-Responder@MercadoLivre.com",
        dados=TUTA,
    )  # fmt: skip
    e4_msgs = await _msgs(db, e4, ("cliente", T0 - timedelta(hours=2), "OUTRO AVISO GERAL"))
    await db.commit()

    for aberta in (e1.id, n.pack.id):
        corpo = await _abas(client, aberta)
        assert not _ids(corpo) & set(e2_msgs + e3_msgs), aberta
        assert not _conversas(corpo) & {str(e2.id), str(e3.id)}, aberta
        email = _aba(corpo, "email")
        assert email["responde"]["conversa_id"] == str(e1.id)
    assert [m["id"] for m in _aba(await _abas(client, n.pack.id), "email")["mensagens"]] == e1_msgs
    # Aberto pelo aviso sem pedido: só ele — o outro aviso do mesmo remetente
    # (sem pedido também) não é "o mesmo comprador".
    corpo = await _abas(client, e3.id)
    assert _conversas(corpo) == {str(e3.id)}
    assert not _ids(corpo) & set(e1_msgs + e2_msgs + e4_msgs)


async def test_contato_so_junta_o_sem_vinculo(db, client, admin):
    """O MESMO endereço (de verdade, não de aviso): o e-mail de OUTRO pedido é de
    outra compra; o sem pedido não entra nas abas de um pedido — e, aberto,
    mostra os outros sem vínculo do mesmo endereço."""
    n = await _cenario_ml(db, admin)
    fulano = "fulano@exemplo.com"
    e1 = await _conversa(
        db, n.loja, "ml", "email", "f-1", comprador_id=fulano, pedido_marketplace=ORDER, dados=TUTA,
    )  # fmt: skip
    await _msgs(db, e1, ("cliente", T0 - timedelta(hours=9), "sobre o pedido 1"))
    e2 = await _conversa(
        db, n.loja, "ml", "email", "f-2", comprador_id=fulano, pedido_marketplace="ORDER-2",
        aguardando_resposta=True, dados=TUTA,
    )  # fmt: skip
    e2_msgs = await _msgs(db, e2, ("cliente", T0 - timedelta(hours=8), "SOBRE O ORDER-2"))
    e3 = await _conversa(
        db, n.loja, "ml", "email", "f-3", comprador_id=fulano, aguardando_resposta=True, dados=TUTA,
    )  # fmt: skip
    e3_msgs = await _msgs(db, e3, ("cliente", T0 - timedelta(hours=7), "sem número"))
    e4 = await _conversa(
        db, n.loja, "ml", "email", "f-4", comprador_id=fulano, dados=TUTA,
    )  # fmt: skip
    e4_msgs = await _msgs(db, e4, ("cliente", T0 - timedelta(hours=6), "outro sem número"))
    await db.commit()

    corpo = await _abas(client, e1.id)
    assert not _ids(corpo) & set(e2_msgs + e3_msgs + e4_msgs)
    assert _aba(corpo, "email")["responde"]["conversa_id"] == str(e1.id)
    # O sem vínculo, aberto: os outros sem vínculo do mesmo endereço — nunca
    # os de um pedido.
    corpo = await _abas(client, e3.id)
    assert _conversas(corpo) == {str(e3.id), str(e4.id)}
    assert [m["id"] for m in _aba(corpo, "email")["mensagens"]] == e4_msgs


async def test_zap_de_outra_compra_do_mesmo_comprador_nao_entra(db, client, admin):
    """O Zap do mesmo comprador ligado a OUTRO pedido é de outra compra: não
    entra nas abas deste pedido nem vira quem responde."""
    n = await _cenario_ml(db, admin)
    zap = await _conversa(
        db, n.loja, "ml", CANAL_ZAP, "zap-9", comprador_id="5511777770000",
        pedido_marketplace="PACK-2", aguardando_resposta=True,
        dados={abas.CHAVE_COMPRADOR_PLATAFORMA: BUYER},
    )  # fmt: skip
    zap_msgs = await _msgs(db, zap, ("cliente", T0 - timedelta(hours=1), "SOBRE O PACK-2"))
    await db.commit()
    corpo = await _abas(client, n.pack.id)
    assert "zap" not in [a["chave"] for a in corpo["abas"]]
    assert not set(zap_msgs) & _ids(corpo)
    # Pelo pedido dele (o PACK-2), entra.
    corpo = await _abas(client, n.x_pedido.id)
    assert [m["id"] for m in _aba(corpo, "zap")["mensagens"]] == zap_msgs


async def test_amazon_e_tuta_na_mesma_venda(db, client, admin):
    """Amazon: o e-mail DA AMAZON é o pós-venda da plataforma (aba Pós-venda); o
    e-mail do Tuta da mesma venda (`dados.fonte = 'tuta'`) entra na aba E-mail
    pelo nº. O e-mail da Amazon de OUTRO pedido do mesmo comprador, nunca."""
    a = await _loja(db, admin, IntegrationPlatform.AMAZON, "kfa")
    pedido = "701-1111111-1111111"
    amz = await _conversa(
        db, a, "amazon", "email", "t-1", comprador_id=RELAY, pedido_marketplace=pedido,
    )  # fmt: skip
    amz_msgs = await _msgs(db, amz, ("cliente", T0 - timedelta(days=3), "cadê o pedido?"))
    outro = await _conversa(
        db, a, "amazon", "email", "t-2", comprador_id=RELAY,
        pedido_marketplace="701-2222222-2222222", aguardando_resposta=True,
    )  # fmt: skip
    outro_msgs = await _msgs(db, outro, ("cliente", T0 - timedelta(days=1), "SOBRE O PEDIDO 2"))
    # A pergunta pela Amazon sem pedido, do mesmo comprador: entra pela pessoa.
    sem = await _conversa(db, a, "amazon", "email", "t-3", comprador_id=RELAY)
    sem_msgs = await _msgs(db, sem, ("cliente", T0 - timedelta(days=4), "tem azul?"))
    tuta = await _conversa(
        db, a, "amazon", "email", "tuta-1", comprador_id="fulano@gmail.com",
        pedido_marketplace=pedido, aguardando_resposta=True,
        dados={**TUTA, abas.CHAVE_COMPRADOR_PLATAFORMA: RELAY},
    )  # fmt: skip
    tuta_msgs = await _msgs(db, tuta, ("cliente", T0 - timedelta(days=2), "e-mail pelo Tuta"))
    # Sem a marca do Tuta, um e-mail de outro endereço numa venda Amazon parece
    # o canal da Amazon com OUTRO comprador: fica de fora (por isso a marca).
    sem_marca = await _conversa(
        db, a, "amazon", "email", "x-1", comprador_id="beltrano@gmail.com",
        pedido_marketplace=pedido,
    )  # fmt: skip
    sem_marca_msgs = await _msgs(db, sem_marca, ("cliente", T0 - timedelta(days=2), "SEM MARCA"))
    await db.commit()

    corpo = await _abas(client, amz.id)
    assert corpo["aba_da_conversa"] == "pos_venda"
    assert [a["chave"] for a in corpo["abas"]] == ["pos_venda", "email"]
    pos = _aba(corpo, "pos_venda")
    assert [m["id"] for m in pos["mensagens"]] == sem_msgs  # as da aberta vêm no detalhe
    assert {c["canal_rotulo"] for c in pos["conversas"]} == {abas.ROTULO_EMAIL_AMAZON}
    email = _aba(corpo, "email")
    assert [m["id"] for m in email["mensagens"]] == tuta_msgs
    assert email["responde"]["conversa_id"] == str(tuta.id)
    assert email["responde"]["canal_rotulo"] == "E-mail"
    assert not _ids(corpo) & set(outro_msgs + sem_marca_msgs)

    # Aberto pelo Tuta: a aba dele é E-mail; a Amazon, Pós-venda.
    corpo = await _abas(client, tuta.id)
    assert corpo["aba_da_conversa"] == "email"
    assert {m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]} == set(amz_msgs + sem_msgs)
    assert _aba(corpo, "pos_venda")["responde"]["conversa_id"] == str(amz.id)
    assert not _ids(corpo) & set(outro_msgs + sem_marca_msgs)


# ─────────────── Shopee ───────────────


async def test_shopee_chat_divide_antes_e_depois_do_pedido(db, client, admin):
    n = await _cenario_shopee(db, admin)
    corpo = await _abas(client, n.chat.id)

    assert corpo["aba_da_conversa"] == "pos_venda"
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "avaliacao",
    ]  # fmt: skip
    assert {a["chave"]: a["total"] for a in corpo["abas"]} == {
        "pre_venda": 2, "pos_venda": 2, "reclamacao": 2, "avaliacao": 1,
    }  # fmt: skip
    # O pré-venda do chat aberto mora na outra aba (a tela mostra lá).
    assert corpo["fora_da_aba"] == dict.fromkeys(n.pre, "pre_venda")
    pre = _aba(corpo, "pre_venda")
    assert [m["id"] for m in pre["mensagens"]] == n.pre
    # O chat é um só: responde nas duas abas.
    assert pre["responde"]["conversa_id"] == str(n.chat.id)
    assert _aba(corpo, "pos_venda")["responde"]["conversa_id"] == str(n.chat.id)
    assert [m["id"] for m in _aba(corpo, "reclamacao")["mensagens"]] == n.devol_msgs
    # A avaliação sem `comprador_id` entra pelo pedido.
    assert [m["id"] for m in _aba(corpo, "avaliacao")["mensagens"]] == n.aval_msgs
    assert corpo["compra_em"].startswith(COMPRA_SHOPEE.isoformat()[:19])
    # O 888 nunca: nem o chat, nem a devolução — nem a torta com o nosso pedido.
    assert not _ids(corpo) & set(n.outro_msgs + n.outro_devol_msgs)
    assert not _conversas(corpo) & {str(n.outro.id), str(n.outro_devol.id)}


async def test_shopee_devolucao_aberta_traz_o_chat_dividido(db, client, admin):
    n = await _cenario_shopee(db, admin)
    corpo = await _abas(client, n.devol.id)

    assert corpo["aba_da_conversa"] == "reclamacao"
    assert corpo["fora_da_aba"] == {}
    assert [m["id"] for m in _aba(corpo, "pre_venda")["mensagens"]] == n.pre
    assert [m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]] == n.pos
    assert _aba(corpo, "pre_venda")["responde"]["conversa_id"] == str(n.chat.id)
    assert _aba(corpo, "reclamacao")["mensagens"] == []
    assert _aba(corpo, "reclamacao")["responde"]["conversa_id"] == str(n.devol.id)
    assert not _ids(corpo) & set(n.outro_msgs + n.outro_devol_msgs)


async def test_shopee_avaliacao_sem_comprador_usa_o_comprador_do_pedido(db, client, admin):
    """A avaliação chega sem `comprador_id`: o comprador é o do pedido — se ele
    disser UM só. Com o dado torto (o 888 com o nosso pedido), ninguém com id
    entra; sem ele, o chat (que é do comprador) e a devolução entram."""
    n = await _cenario_shopee(db, admin)
    corpo = await _abas(client, n.aval.id)
    assert corpo["aba_da_conversa"] == "avaliacao"
    assert _conversas(corpo) == {str(n.aval.id)}
    assert not _ids(corpo) & set(n.outro_devol_msgs + n.outro_msgs + n.pre + n.devol_msgs)

    await db.delete(n.outro_devol)
    await db.commit()
    corpo = await _abas(client, n.aval.id)
    assert [a["chave"] for a in corpo["abas"]] == [
        "pre_venda", "pos_venda", "reclamacao", "avaliacao",
    ]  # fmt: skip
    assert [m["id"] for m in _aba(corpo, "pre_venda")["mensagens"]] == n.pre
    assert [m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]] == n.pos
    assert [m["id"] for m in _aba(corpo, "reclamacao")["mensagens"]] == n.devol_msgs
    assert _aba(corpo, "avaliacao")["responde"]["conversa_id"] == str(n.aval.id)
    assert _aba(corpo, "avaliacao")["responde"]["publica"] is True
    assert not _ids(corpo) & set(n.outro_msgs)


async def test_shopee_sem_retrato_usa_a_hora_do_indice(db, client, admin):
    n = await _cenario_shopee(db, admin, retrato=False)
    db.add(
        AtendimentoPedidoComprador(
            integration_id=n.loja, plataforma="shopee", comprador_id="777", pedido=SN,
            criado_em=COMPRA_SHOPEE,
        )
    )  # fmt: skip
    await db.commit()
    corpo = await _abas(client, n.devol.id)
    assert [m["id"] for m in _aba(corpo, "pre_venda")["mensagens"]] == n.pre
    assert [m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]] == n.pos


async def test_shopee_sem_hora_da_compra_o_chat_com_pedido_e_pos_venda(db, client, admin):
    n = await _cenario_shopee(db, admin, retrato=False)
    corpo = await _abas(client, n.devol.id)
    assert corpo["compra_em"] is None
    assert "pre_venda" not in [a["chave"] for a in corpo["abas"]]
    assert [m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]] == n.pre + n.pos


async def test_shopee_comprador_com_duas_compras_corta_na_primeira(db, client, admin):
    """O chat é um só: com uma compra ANTERIOR, o pós-venda dela nunca vira
    "Pré-venda" do pedido de agora. Pré-venda = antes da PRIMEIRA compra do
    comprador na loja (o índice de pedidos); o resto é Pós-venda."""
    n = await _cenario_shopee(db, admin)
    primeira = T0 - timedelta(days=25)
    db.add_all(
        [
            AtendimentoPedidoComprador(
                integration_id=n.loja, plataforma="shopee", comprador_id="777", pedido="SN-ANTIGO",
                criado_em=primeira,
            ),
            AtendimentoPedidoComprador(
                integration_id=n.loja, plataforma="shopee", comprador_id="777", pedido=SN,
                criado_em=COMPRA_SHOPEE,
            ),
            # O 888 comprou antes de todo mundo: não muda nada no corte do 777.
            AtendimentoPedidoComprador(
                integration_id=n.loja, plataforma="shopee", comprador_id="888", pedido="SN-888",
                criado_em=T0 - timedelta(days=90),
            ),
        ]
    )  # fmt: skip
    antes = await _msgs(db, n.chat, ("cliente", T0 - timedelta(days=30), "tem pronta entrega?"))
    antiga = await _msgs(
        db, n.chat, ("cliente", T0 - timedelta(days=20), "meu pedido antigo ainda não chegou")
    )
    n.chat.ultima_mensagem_em = T0 - timedelta(days=1)
    await db.commit()

    corpo = await _abas(client, n.chat.id)
    assert corpo["compra_em"].startswith(COMPRA_SHOPEE.isoformat()[:19])
    assert corpo["primeira_compra_em"].startswith(primeira.isoformat()[:19])
    assert corpo["aba_da_conversa"] == "pos_venda"
    # Só o de antes da primeira compra mora na Pré-venda.
    assert corpo["fora_da_aba"] == dict.fromkeys(antes, "pre_venda")
    assert {a["chave"]: a["total"] for a in corpo["abas"]}["pos_venda"] == 1 + 2 + 2
    # Pela devolução (o pedido de agora): o mesmo corte.
    corpo = await _abas(client, n.devol.id)
    assert [m["id"] for m in _aba(corpo, "pre_venda")["mensagens"]] == antes
    assert [m["id"] for m in _aba(corpo, "pos_venda")["mensagens"]] == antiga + n.pre + n.pos
    assert not _ids(corpo) & set(n.outro_msgs + n.outro_devol_msgs)


# ─────────────── página, erros e trava ───────────────


async def test_pagina_das_mais_antigas(db, client, admin):
    n = await _cenario_ml(db, admin)
    corpo = await _abas(client, n.pack.id, aba="reclamacao", limite=2)
    assert [a["chave"] for a in corpo["abas"]] == ["reclamacao"]
    recl = corpo["abas"][0]
    assert recl["total"] == 3
    assert [m["id"] for m in recl["mensagens"]] == [n.recl_msgs[1], n.recl_msgs[3]]
    assert recl["tem_mais"] is True and recl["proximo"]
    corpo = await _abas(client, n.pack.id, aba="reclamacao", limite=2, antes_de=recl["proximo"])
    recl = corpo["abas"][0]
    assert [m["id"] for m in recl["mensagens"]] == [n.recl_msgs[0]]
    assert recl["tem_mais"] is False and recl["proximo"] is None
    # Sem `aba`, a primeira página de cada uma (padrão 50).
    corpo = await _abas(client, n.pack.id, limite=1)
    assert all(len(a["mensagens"]) <= 1 for a in corpo["abas"])


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"aba": "chat"}, "aba_invalida"),
        ({"antes_de": "lixo"}, "cursor_invalido"),
        (
            {"aba": "reclamacao", "antes_de": "2026-10-01T00:00:00+00:00|nao-e-id"},
            "cursor_invalido",
        ),
        ({"antes_de": f"2026-10-01T00:00:00+00:00|{uuid4()}"}, "cursor_invalido"),
    ],
)
async def test_parametros_invalidos(db, client, admin, params, code):
    n = await _cenario_ml(db, admin)
    r = await client.get(URL.format(n.pack.id), params=params)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code


async def test_404_instagram_e_canais_sem_abas(db, client, admin):
    r = await client.get(URL.format(uuid4()))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    corpo = await _abas(client, "ig:abc")
    assert corpo["abas"] == [] and corpo["aba_da_conversa"] is None
    # Carrinho do site: sem pedido de plataforma, sem abas.
    carrinho = await _conversa(db, None, "site", "carrinho", "lojista:1")
    await _msgs(db, carrinho, ("cliente", T0, "carrinho parado"))
    await db.commit()
    corpo = await _abas(client, carrinho.id)
    assert corpo["abas"] == [] and corpo["aba_da_conversa"] is None


async def test_mesma_trava_do_atendimento(db, client, make_user, auth_as, monkeypatch):
    n = await _cenario_ml(db, await make_user(role=UserRole.ADMIN))
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", True)
    auth_as(await make_user(role=UserRole.USER, permissions={"atendimento": {"view": True}}))
    r = await client.get(URL.format(n.pack.id))
    assert r.status_code == 403 and r.json()["detail"] == {"code": "admin_only"}
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "alguem@davinci-test.com")
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(URL.format(n.pack.id))
    assert r.status_code == 403 and r.json()["detail"] == {"code": "atendimento_restrito"}


async def test_equipe_so_ve_as_abas_das_proprias_lojas(db, client, make_user, auth_as):
    """O escopo por equipe: a conversa de loja de fora da equipe é 404; a da
    equipe tem as abas (a família é toda da mesma loja)."""
    dono = await make_user(role=UserRole.ADMIN)
    n = await _cenario_ml(db, dono)
    db.add(
        StoreInfo(user_id=dono.id, platform="ml", account_name="aguiar", sales_team=7,
                  integration_id=n.loja)
    )  # fmt: skip
    membro = await make_user(permissions={"atendimento": {"view": True}})
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    r = await client.get(URL.format(n.x_loja.id))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    corpo = await _abas(client, n.pack.id)
    assert {a["chave"] for a in corpo["abas"]} >= {"pre_venda", "pos_venda", "reclamacao"}
    assert not _conversas(corpo) & {str(n.x_loja.id)}


@pytest.mark.parametrize("envio_ativo", [False, True])
async def test_consultas_nao_crescem_com_a_familia(
    db, client, admin, contar_consultas, monkeypatch, envio_ativo
):
    """Sem N+1: 3 ou 23 conversas, 10 ou 70 mensagens — o mesmo número de
    consultas, com o envio desligado e LIGADO (aí as travas de cada conversa
    que responde também vão ao banco: no máximo uma conversa por aba)."""
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", envio_ativo)
    n = await _cenario_ml(db, admin)
    contar_consultas["n"] = 0
    await _abas(client, n.pack.id)
    antes = contar_consultas["n"]

    for i in range(20):
        p = await _conversa(
            db, n.loja, "ml", "pergunta", f"q:extra-{i}", comprador_id=BUYER, anuncio_id="MLB1",
            anuncio_titulo=MALA, ultima_do_cliente_em=T0 - timedelta(days=20, hours=i),
            dados={"item_id": "MLB1"},
        )  # fmt: skip
        await _msgs(
            db, p,
            ("cliente", T0 - timedelta(days=20, hours=i), f"pergunta {i}"),
            ("loja", T0 - timedelta(days=20, hours=i) + timedelta(minutes=5), f"resposta {i}"),
            ("cliente", T0 - timedelta(days=20, hours=i) + timedelta(minutes=9), "obrigado"),
        )  # fmt: skip
    await db.commit()
    contar_consultas["n"] = 0
    corpo = await _abas(client, n.pack.id)
    assert _aba(corpo, "pre_venda")["total"] == 3 + 60
    assert contar_consultas["n"] == antes


async def test_consultas_da_shopee_com_o_indice(db, client, admin, contar_consultas):
    """Shopee: a hora da compra e a primeira compra saem do índice numa consulta só."""
    n = await _cenario_shopee(db, admin, retrato=False)
    contar_consultas["n"] = 0
    await _abas(client, n.chat.id)
    antes = contar_consultas["n"]
    for i in range(20):
        db.add(
            AtendimentoPedidoComprador(
                integration_id=n.loja, plataforma="shopee", comprador_id="777",
                pedido=f"SN-EXTRA-{i}", criado_em=T0 - timedelta(days=40 + i),
            )
        )  # fmt: skip
        await _msgs(db, n.chat, ("cliente", T0 - timedelta(days=60 + i), f"pergunta {i}"))
    await db.commit()
    contar_consultas["n"] = 0
    corpo = await _abas(client, n.chat.id)
    assert contar_consultas["n"] == antes
    assert corpo["primeira_compra_em"].startswith((T0 - timedelta(days=59)).isoformat()[:19])
    # Antes da primeira compra (59 dias): as 20 perguntas de 60 a 79 dias.
    assert _aba(corpo, "pre_venda")["total"] == 20


# ─────────────── as peças puras ───────────────


def _c(**campos) -> AtendimentoConversa:
    base = {"id": uuid4(), "plataforma": "shopee", "canal": "chat", "externo_id": "x"}
    return AtendimentoConversa(**{**base, **campos})


@pytest.mark.parametrize(
    ("canal", "autor", "hora", "esperada"),
    [
        ("pergunta", "cliente", None, "pre_venda"),
        ("pos_venda", "cliente", None, "pos_venda"),
        ("sac", "sistema", None, "pos_venda"),
        ("reclamacao", "cliente", None, "reclamacao"),
        ("reclamacao", "loja", None, "reclamacao"),
        ("reclamacao", "sistema", None, "reclamacao"),
        ("reclamacao", "mediador", None, "mediador"),
        ("avaliacao", "cliente", None, "avaliacao"),
        ("email", "cliente", None, "email"),
        ("zap", "cliente", None, "zap"),
        ("chat", "cliente", T0 - timedelta(days=4), "pre_venda"),
        ("chat", "loja", T0 - timedelta(days=2), "pos_venda"),
        ("carrinho", "cliente", None, None),
        ("comentario", "cliente", None, None),
    ],
)
def test_aba_da_mensagem(canal, autor, hora, esperada):
    c = _c(canal=canal, pedido_marketplace="P1")
    assert abas.aba_da_mensagem(c, autor, hora, COMPRA_SHOPEE) == esperada


def test_email_da_amazon_e_pos_venda_e_o_do_tuta_e_email():
    amazon = _c(plataforma="amazon", canal="email", comprador_id=RELAY)
    tuta = _c(plataforma="amazon", canal="email", comprador_id="f@x.com", dados=TUTA)
    assert abas.aba_da_mensagem(amazon, "cliente", T0, None) == "pos_venda"
    assert abas.aba_da_conversa(amazon, [], None) == "pos_venda"
    assert abas.aba_da_mensagem(tuta, "cliente", T0, None) == "email"
    assert abas.rotulo_do_canal(amazon) == abas.ROTULO_EMAIL_AMAZON
    assert abas.rotulo_do_canal(tuta) == "E-mail"
    # Fora da Amazon, o e-mail é sempre de contato (com ou sem a marca).
    assert abas.aba_da_mensagem(_c(canal="email", plataforma="ml"), "cliente", T0, None) == "email"


@pytest.mark.parametrize(
    ("contato", "aviso"),
    [
        ("noreply@mercadolivre.com", True),
        ("no-reply@shopee.com.br", True),
        ("Nao-Responder@qualquer.com", True),
        ("não_responda@loja.com.br", True),
        ("DoNotReply@amazon.com", True),
        ("mailer-daemon@tuta.com", True),
        ("notificacoes@loja.com", True),
        ("vendas@mail.mercadolivre.com", True),
        ("avisos@tiktok.com", True),
        ("fulano@gmail.com", False),
        ("notificacoesdofulano@gmail.com", False),
        (RELAY, False),
        ("5511999990000", False),
        ("", False),
    ],
)
def test_contato_generico(contato, aviso):
    assert abas.contato_generico(contato) is aviso


def test_chat_sem_hora_da_compra_segue_o_filtro():
    assert abas.aba_da_mensagem(_c(pedido_marketplace="P1"), "cliente", T0, None) == "pos_venda"
    assert abas.aba_da_mensagem(_c(pedido_marketplace=None), "cliente", T0, None) == "pre_venda"


def test_ordem_e_rotulos_das_abas():
    assert abas.ORDEM_ABAS == (
        "pre_venda", "pos_venda", "reclamacao", "mediador", "email", "zap", "avaliacao",
    )  # fmt: skip
    assert set(abas.ROTULO_ABA) == set(abas.ORDEM_ABAS)


def test_loja_e_comprador_nunca_misturam():
    loja_a, loja_b, canal = uuid4(), uuid4(), uuid4()
    a = _c(integration_id=loja_a, comprador_id="1")
    assert abas.mesma_loja(a, _c(integration_id=loja_a))
    assert not abas.mesma_loja(a, _c(integration_id=loja_b))
    assert not abas.mesma_loja(a, _c(integration_id=None, canal_id=canal))
    # Lojas do robô (sem integração): pelo canal.
    robo = _c(plataforma="temu", integration_id=None, canal_id=canal)
    assert abas.mesma_loja(robo, _c(plataforma="temu", integration_id=None, canal_id=canal))
    assert not abas.mesma_loja(robo, _c(plataforma="temu", integration_id=None, canal_id=uuid4()))
    assert not abas.mesma_loja(_c(integration_id=None), _c(integration_id=None))
    # Comprador: ids diferentes no mesmo espaço nunca; sem id, vale o pedido.
    assert not abas.compradores_compativeis(a, _c(comprador_id="2"))
    assert abas.compradores_compativeis(a, _c(comprador_id=None))
    assert abas.mesmo_comprador(a, _c(comprador_id="1"))
    assert not abas.mesmo_comprador(a, _c(comprador_id=None))
    # E-mail do Tuta / Zap: o contato não se compara com o id da plataforma...
    zap = _c(canal="zap", comprador_id="5511999990000")
    assert abas.compradores_compativeis(a, zap) and not abas.mesmo_comprador(a, zap)
    # ...a não ser pelo id da plataforma que ele guarda.
    zap_do_1 = _c(canal="zap", comprador_id="55119", dados={"comprador_plataforma": "1"})
    zap_do_2 = _c(canal="zap", comprador_id="55119", dados={"comprador_plataforma": "2"})
    assert abas.mesmo_comprador(a, zap_do_1)
    assert not abas.compradores_compativeis(a, zap_do_2)
    # O e-mail da Amazon É o canal da plataforma (o endereço que a Amazon repassa).
    am = _c(plataforma="amazon", canal="email", comprador_id="x@marketplace.amazon.com.br")
    assert not abas.e_contato(am)
    assert abas.mesmo_comprador(
        am, _c(plataforma="amazon", canal="email", comprador_id=am.comprador_id)
    )
    # O do Tuta numa venda Amazon (`dados.fonte = 'tuta'`) é de contato: o
    # endereço dele não é o id da plataforma, e ele entra pelo que guarda.
    tuta = _c(
        plataforma="amazon", canal="email", comprador_id="f@x.com",
        dados={**TUTA, "comprador_plataforma": am.comprador_id},
    )  # fmt: skip
    assert abas.e_email_tuta(tuta) and abas.e_contato(tuta)
    assert abas.mesmo_comprador(am, tuta)
    # O remetente de aviso não é contato de ninguém.
    aviso = _c(canal="email", comprador_id="noreply@mercadolivre.com", dados=TUTA)
    assert abas.pessoa(aviso).contato == ""
    assert not abas.mesmo_comprador(aviso, _c(canal="email", comprador_id=aviso.comprador_id))


def test_quem_responde():
    aberta = _c(canal="pos_venda")
    velha = _c(canal="pergunta", ultima_mensagem_em=T0 - timedelta(days=3))
    nova = _c(canal="pergunta", ultima_mensagem_em=T0 - timedelta(days=1))
    esperando = _c(
        canal="pergunta", aguardando_resposta=True, ultima_mensagem_em=T0 - timedelta(days=9)
    )
    assert abas.quem_responde("pre_venda", [velha, nova], aberta) is nova
    assert abas.quem_responde("pre_venda", [velha, nova, esperando], aberta) is esperando
    assert abas.quem_responde("pos_venda", [aberta, nova], aberta) is aberta
    assert abas.quem_responde("mediador", [aberta], aberta) is None
    assert abas.quem_responde("email", [], aberta) is None


def test_cursor_ida_e_volta():
    m = abas.Leve(id=uuid4(), conversa_id=uuid4(), autor="cliente", momento=T0, nota=False)
    assert abas.ler_cursor(abas.cursor_de(m)) == (T0, str(m.id))
    assert abas.ler_cursor(None) is None and abas.ler_cursor("  ") is None
    for ruim in ("sem-barra", "|" + str(uuid4()), "2026-10-01|nao-e-uuid"):
        with pytest.raises(ValueError):
            abas.ler_cursor(ruim)
