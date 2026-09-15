"""logistica_cliente_mensagens — textos, validação (regras da Amazon) e o robô
que manda UMA mensagem por pedido × evento (sender falso)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, LogisticaMensagemCliente
from app.services import logistica_cliente_mensagens as msgs

HOJE = date(2026, 10, 5)


class FakeSender:
    def __init__(self, falha: bool = False):
        self.enviados: list[dict] = []
        self.falha = falha

    async def send(self, *, to, subject, html, text):
        if self.falha:
            raise RuntimeError("mailjet 500")
        self.enviados.append({"to": to, "subject": subject, "html": html, "text": text})


@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setattr(
        msgs, "get_settings", lambda: SimpleNamespace(amazon_mensagens_cliente=True)
    )


def _linha(**kw) -> Logistica:
    base = {
        "data": date(2026, 9, 12),
        "pedido_bling": "296762",
        "pedido_marketplace": "701-3967231-6921832",
        "plataforma": "Amazon",
        "conta": "kia",
        "meli_status": {"order_status": "Shipped", "fulfillment_channel": "MFN"},
        "amazon_canal": "proprio",
        "servico_envio": "SEDEX",
        "rastreio": "AD912266053BR",
        "localizacao": "Aracaju/SE — Objeto em trânsito",
        "cliente_nome": "rosana vieira de melo",
        "cliente_email": "n340cj40yxfjsq4@marketplace.amazon.com.br",
        "previsao_correios": date(2026, 9, 23),
        "prazo_entrega_amazon": date(2026, 10, 8),
    }
    base.update(kw)
    return Logistica(**base)


# ---- textos ----


def test_templates_padrao_passam_na_validacao():
    for ev, t in msgs.TEMPLATES_PADRAO.items():
        msgs.validar_texto(t["assunto"], t["corpo"])
        assert ev in msgs.EVENTO_LABELS_PT


@pytest.mark.parametrize(
    "corpo, code",
    [
        ("Pedido {pedido_amazon}: veja em https://x.com", "mensagem_com_link"),
        ("Pedido {pedido_amazon}: www.correios.com.br", "mensagem_com_link"),
        ("Pedido {pedido_amazon}: fale com a@b.com", "mensagem_com_email"),
        ("Pedido {pedido_amazon}: <b>oi</b>", "mensagem_com_html"),
        ("Olá, sem número do pedido", "mensagem_sem_pedido"),
        ("Pedido {pedido_amazon} {chave", "mensagem_chaves_invalidas"),
    ],
)
def test_validar_texto_recusa_o_que_a_amazon_bloqueia(corpo, code):
    with pytest.raises(msgs.TemplateInvalidoError) as e:
        msgs.validar_texto("assunto", corpo)
    assert e.value.code == code


def test_renderizar_preenche_campos_e_ignora_chave_desconhecida():
    tpl = msgs.Template(
        evento="entregue",
        assunto="Pedido {pedido_amazon}",
        corpo="Olá, {cliente}. Entregue em {entregue_em}. {nao_existe} Rastreio {rastreio}.",
    )
    row = _linha(entregue_em=datetime(2026, 9, 23, 18, 0, tzinfo=UTC))
    assunto, corpo = msgs.renderizar(tpl, row)
    assert assunto == "Pedido 701-3967231-6921832"
    assert corpo == "Olá, Rosana. Entregue em 23/09/2026.  Rastreio AD912266053BR."


def test_eventos_devidos():
    assert msgs.eventos_devidos(_linha(), HOJE) == [msgs.EVENTO_PREVISAO]
    r = _linha(entregue_em=datetime.now(UTC), problema_correios="Objeto extraviado",
               problema_correios_em=datetime.now(UTC))
    # Entregue não recebe "previsão vencida"; problema e entrega sim.
    assert msgs.eventos_devidos(r, HOJE) == [msgs.EVENTO_PROBLEMA, msgs.EVENTO_ENTREGUE]
    assert msgs.eventos_devidos(_linha(amazon_canal="dba"), HOJE) == []


# ---- banco ----


@pytest.mark.asyncio
async def test_carregar_e_salvar_template(db: AsyncSession):
    t = await msgs.carregar_templates(db)
    assert t["entregue"].padrao and t["entregue"].ativo
    await msgs.salvar_template(
        db,
        "entregue",
        assunto="Pedido {pedido_amazon} entregue",
        corpo="Oi {cliente}, pedido {pedido_amazon}.",
        ativo=False,
    )
    t2 = await msgs.carregar_templates(db)
    assert not t2["entregue"].padrao and not t2["entregue"].ativo
    assert t2["entregue"].assunto == "Pedido {pedido_amazon} entregue"
    with pytest.raises(msgs.TemplateInvalidoError):
        await msgs.salvar_template(db, "xxx", assunto="a", corpo="{pedido_amazon}", ativo=True)


@pytest.mark.asyncio
async def test_run_desligado_nao_manda(db: AsyncSession, monkeypatch):
    monkeypatch.setattr(
        msgs, "get_settings", lambda: SimpleNamespace(amazon_mensagens_cliente=False)
    )
    db.add(_linha())
    await db.commit()
    sender = FakeSender()
    out = await msgs.run(db, sender=sender, hoje=HOJE)
    assert out["desligado"] == 1 and sender.enviados == []


@pytest.mark.asyncio
async def test_run_manda_uma_vez_por_evento_e_registra(db: AsyncSession, ligado):
    db.add(_linha())
    await db.commit()
    sender = FakeSender()

    out = await msgs.run(db, sender=sender, hoje=HOJE)

    assert out["enviadas"] == 1 and out["devidas"] == 1
    env = sender.enviados[0]
    assert env["to"] == "n340cj40yxfjsq4@marketplace.amazon.com.br"
    assert env["html"] == ""  # texto puro: a Amazon recusa HTML
    assert "701-3967231-6921832" in env["text"] and "23/09/2026" in env["text"]
    assert "Rosana" in env["text"]
    hist = (await db.execute(select(LogisticaMensagemCliente))).scalars().all()
    assert len(hist) == 1 and hist[0].evento == "previsao_vencida" and hist[0].enviado_em

    # Segunda rodada: nada novo.
    out2 = await msgs.run(db, sender=sender, hoje=HOJE)
    assert out2["devidas"] == 0 and len(sender.enviados) == 1


@pytest.mark.asyncio
async def test_run_pula_evento_desligado_e_email_que_nao_e_relay(db: AsyncSession, ligado):
    db.add(_linha())
    db.add(_linha(pedido_bling="999", cliente_email="rosana@gmail.com"))
    await msgs.salvar_template(
        db, "previsao_vencida", assunto="x {pedido_amazon}", corpo="{pedido_amazon}", ativo=False
    )
    sender = FakeSender()
    out = await msgs.run(db, sender=sender, hoje=HOJE)
    # Só a linha com e-mail relay entra no alvo; o evento desligado é pulado.
    assert out["pedidos"] == 1 and out["puladas"] == 1 and sender.enviados == []


@pytest.mark.asyncio
async def test_run_falha_registra_erro_e_retenta_ate_o_teto(db: AsyncSession, ligado):
    db.add(_linha())
    await db.commit()
    ruim = FakeSender(falha=True)
    for _ in range(msgs.MAX_TENTATIVAS):
        out = await msgs.run(db, sender=ruim, hoje=HOJE)
        assert out["falhas"] == 1
    hist = (await db.execute(select(LogisticaMensagemCliente))).scalars().one()
    assert hist.tentativas == msgs.MAX_TENTATIVAS and hist.erro and hist.enviado_em is None
    # Estourou o teto: não tenta mais.
    out = await msgs.run(db, sender=ruim, hoje=HOJE)
    assert out["devidas"] == 0
