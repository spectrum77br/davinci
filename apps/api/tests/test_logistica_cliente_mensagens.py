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

    async def send(self, *, to, subject, html, text, attachments=None):
        if self.falha:
            raise RuntimeError("mailjet 500")
        self.enviados.append(
            {
                "to": to,
                "subject": subject,
                "html": html,
                "text": text,
                "attachments": attachments,
            }
        )


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
        "postagem_data": date(2026, 9, 14),
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
    # Postado (rastreio + saída + previsão do Bling) e previsão vencida em 05/10.
    assert msgs.eventos_devidos(_linha(), HOJE) == [msgs.EVENTO_RASTREIO, msgs.EVENTO_PREVISAO]
    # Antes da data de saída não avisa; sem previsão também não.
    assert msgs.eventos_devidos(_linha(postagem_data=date(2026, 10, 9)), HOJE) == [
        msgs.EVENTO_PREVISAO
    ]
    assert msgs.eventos_devidos(_linha(previsao_correios=None), HOJE) == []
    r = _linha(entregue_em=datetime.now(UTC), problema_correios="Objeto extraviado",
               problema_correios_em=datetime.now(UTC))
    # Entregue não recebe "postado" nem "previsão vencida"; problema e entrega sim.
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

    # Postado (aviso de rastreio) + previsão vencida: dois e-mails, uma vez cada.
    assert out["enviadas"] == 2 and out["devidas"] == 2
    env = sender.enviados[0]
    assert env["to"] == "n340cj40yxfjsq4@marketplace.amazon.com.br"
    assert env["html"] == ""  # texto puro: a Amazon recusa HTML
    assert "AD912266053BR" in env["text"] and "14/09/2026" in env["text"] and "SEDEX" in env["text"]
    assert "701-3967231-6921832" in sender.enviados[1]["text"]
    assert "23/09/2026" in sender.enviados[1]["text"] and "Rosana" in sender.enviados[1]["text"]
    hist = (await db.execute(select(LogisticaMensagemCliente))).scalars().all()
    assert sorted(h.evento for h in hist) == ["previsao_vencida", "rastreio"]
    assert all(h.enviado_em for h in hist)

    # Segunda rodada: nada novo.
    out2 = await msgs.run(db, sender=sender, hoje=HOJE)
    assert out2["devidas"] == 0 and len(sender.enviados) == 2


@pytest.mark.asyncio
async def test_run_pula_evento_desligado_e_email_que_nao_e_relay(db: AsyncSession, ligado):
    db.add(_linha())
    db.add(_linha(pedido_bling="999", cliente_email="rosana@gmail.com"))
    await msgs.salvar_template(
        db, "previsao_vencida", assunto="x {pedido_amazon}", corpo="{pedido_amazon}", ativo=False
    )
    await msgs.salvar_template(
        db, "rastreio", assunto="x {pedido_amazon}", corpo="{pedido_amazon}", ativo=False
    )
    sender = FakeSender()
    out = await msgs.run(db, sender=sender, hoje=HOJE)
    # Só a linha com e-mail relay entra no alvo; os eventos desligados são pulados.
    assert out["pedidos"] == 1 and out["puladas"] == 2 and sender.enviados == []


@pytest.mark.asyncio
async def test_run_falha_registra_erro_e_retenta_ate_o_teto(db: AsyncSession, ligado):
    db.add(_linha())
    await db.commit()
    ruim = FakeSender(falha=True)
    for _ in range(msgs.MAX_TENTATIVAS):
        out = await msgs.run(db, sender=ruim, hoje=HOJE)
        assert out["falhas"] == 2  # rastreio + previsão vencida
    for hist in (await db.execute(select(LogisticaMensagemCliente))).scalars().all():
        assert hist.tentativas == msgs.MAX_TENTATIVAS and hist.erro and hist.enviado_em is None
    # Estourou o teto: não tenta mais.
    out = await msgs.run(db, sender=ruim, hoje=HOJE)
    assert out["devidas"] == 0


@pytest.mark.asyncio
async def test_enviar_teste_vai_para_meu_email_nunca_para_o_cliente(db: AsyncSession):
    db.add(_linha())
    await db.commit()
    sender = FakeSender()
    res = await msgs.enviar_teste(
        db, "entregue", email="Eu@Empresa.com.br", pedido_bling="296762", sender=sender
    )
    assert res["assunto"].startswith("[TESTE] Pedido 701-3967231-6921832")
    assert "Rosana" in res["corpo"] and "o cliente NÃO recebeu" in res["corpo"]
    assert sender.enviados[0]["to"] == "eu@empresa.com.br" and sender.enviados[0]["html"] == ""
    # Sem pedido: usa o exemplo.
    res2 = await msgs.enviar_teste(
        db, "problema_correios", email="eu@empresa.com.br", sender=sender
    )
    assert res2["pedido"] == "000000" and "Objeto extraviado" in res2["corpo"]
    # Endereço de retransmissão da Amazon é recusado (seria mensagem real).
    with pytest.raises(msgs.TemplateInvalidoError) as e:
        await msgs.enviar_teste(
            db, "entregue", email="x@marketplace.amazon.com.br", sender=sender
        )
    assert e.value.code == "teste_nao_vai_para_cliente"
    with pytest.raises(msgs.TemplateInvalidoError):
        await msgs.enviar_teste(
            db, "entregue", email="eu@empresa.com.br", pedido_bling="999", sender=sender
        )


# ---- cartão de rastreio anexado (22/09/2026) ----
#
# A Amazon não aceita link na mensagem, então o histórico dos Correios vai
# como IMAGEM anexada. O 17track é a fonte dos eventos.


def _track_info(eventos: list[dict], *, aninhado: bool = True) -> dict:
    """Resposta do 17track: `tracking.providers[]` (v2.2) ou solto (v2.4)."""
    providers = [{"events": eventos}]
    return {"tracking": {"providers": providers}} if aninhado else {"providers": providers}


_EV_POSTADO = {
    "time_iso": "2026-09-21T16:18:00-03:00",
    "description": "Objeto postado",
    "location": "SP",
}
_EV_TRANSFERENCIA = {
    "time_iso": "2026-09-22T07:08:00-03:00",
    "description": "Objeto em transferência - por favor aguarde",
    "location": "MG",
}
_EV_ETIQUETA = {
    "time_iso": "2026-09-21T08:49:00-03:00",
    "description": "Etiqueta emitida",
    "location": "BR",
}


@pytest.mark.parametrize("aninhado", [True, False])
def test_eventos_do_17track_vem_do_mais_novo_pro_mais_antigo(aninhado):
    from app.services import logistica_track

    evs = logistica_track.eventos_de_track_info(
        _track_info([_EV_POSTADO, _EV_TRANSFERENCIA, _EV_ETIQUETA], aninhado=aninhado)
    )
    assert [e.descricao for e in evs] == [
        "Objeto em transferência - por favor aguarde",
        "Objeto postado",
        "Etiqueta emitida",
    ]
    assert evs[0].local == "MG"
    # Evento sem data ou sem descrição não tem como ser mostrado ao comprador.
    assert logistica_track.eventos_de_track_info(
        _track_info([{"description": "sem data"}, {"time_iso": "2026-09-21T10:00:00-03:00"}])
    ) == []


def test_eventos_repetidos_em_dois_providers_contam_uma_vez():
    from app.services import logistica_track

    ti = {"tracking": {"providers": [{"events": [_EV_POSTADO]}, {"events": [_EV_POSTADO]}]}}
    assert len(logistica_track.eventos_de_track_info(ti)) == 1


_LOGO_COR = (214, 32, 39)


def _logo_fake() -> bytes:
    """PNG chapado, do tamanho do logo dos Correios (205x161)."""
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 205, 161), False)
    pix.set_rect(pix.irect, _LOGO_COR)
    return pix.tobytes("png")


def _canto_superior_direito(png: bytes) -> tuple[int, int, int]:
    """Cor de um ponto dentro da caixa do logo do cartão já renderizado."""
    import pymupdf

    pix = pymupdf.Pixmap(png)
    # A caixa do logo vai de ~1014 a 1172 px (escala 2); 1140 cai dentro dela.
    return pix.pixel(pix.width - 100, 90)


def test_cartao_sai_mesmo_so_com_etiqueta_emitida():
    """Vinicius, 22/09: pedido que ainda não andou também leva a imagem."""
    from app.services import logistica_cartao_rastreio as cartao

    png = cartao.gerar(
        codigo="AD942982423BR",
        pedido="702-8932109-4506652",
        servico="SEDEX",
        previsao=date(2026, 9, 23),
        eventos=[
            cartao.Evento(
                quando=datetime(2026, 9, 21, 11, 49, tzinfo=UTC),
                titulo="Etiqueta emitida",
                local="BR",
            )
        ],
        consultado_em=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )
    assert png.startswith(b"\x89PNG\r\n")


def test_cartao_desenha_png_com_o_historico():
    from app.services import logistica_cartao_rastreio as cartao

    png = cartao.gerar(
        codigo="AD942982423BR",
        pedido="702-8932109-4506652",
        servico="SEDEX",
        previsao=date(2026, 9, 23),
        eventos=[
            cartao.Evento(
                quando=datetime(2026, 9, 23, 14, 32, tzinfo=UTC),
                titulo="Objeto entregue ao destinatário",
                local="MG",
                entregue=True,
            ),
            cartao.Evento(
                quando=datetime(2026, 9, 21, 19, 18, tzinfo=UTC),
                titulo="Objeto postado",
                local="SP",
            ),
        ],
        consultado_em=datetime(2026, 9, 23, 15, 10, tzinfo=UTC),
    )
    assert png.startswith(b"\x89PNG\r\n")
    assert 5_000 < len(png) < 2_000_000
    with pytest.raises(ValueError):
        cartao.gerar(
            codigo="AD942982423BR", pedido="1", servico="", previsao=None,
            eventos=[], consultado_em=datetime(2026, 9, 23, tzinfo=UTC),
        )


def _patch_eventos(monkeypatch, retorno=None, erro: Exception | None = None):
    from app.services import logistica_track

    async def fake(numbers):
        if erro:
            raise erro
        return retorno or {}

    monkeypatch.setattr(logistica_track, "eventos", fake)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "eventos", [[_EV_ETIQUETA], [_EV_TRANSFERENCIA, _EV_POSTADO, _EV_ETIQUETA]]
)
async def test_montar_cartao_com_qualquer_evento(db: AsyncSession, monkeypatch, eventos):
    """Basta o 17track ter UM evento — inclusive só a etiqueta emitida."""
    from app.services import logistica_track

    row = _linha()
    _patch_eventos(
        monkeypatch,
        {row.rastreio: logistica_track.eventos_de_track_info(_track_info(eventos))},
    )
    cartao = await msgs.montar_cartao(db, row)
    assert cartao is not None
    nome, mime, png = cartao
    assert (nome, mime) == ("rastreio.png", "image/png") and png.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_montar_cartao_nao_derruba_a_mensagem(db: AsyncSession, monkeypatch):
    """17track fora do ar, número desconhecido ou rastreio que não é dos
    Correios: a mensagem vai, só que sem imagem."""
    row = _linha()
    _patch_eventos(monkeypatch, erro=RuntimeError("17track 500"))
    assert await msgs.montar_cartao(db, row) is None
    _patch_eventos(monkeypatch, {})
    assert await msgs.montar_cartao(db, row) is None
    _patch_eventos(monkeypatch, erro=AssertionError("não devia nem consultar"))
    assert await msgs.montar_cartao(db, _linha(rastreio="TIKTOK123")) is None


@pytest.mark.asyncio
async def test_cartao_leva_o_logo_guardado_no_banco(db: AsyncSession, monkeypatch):
    """Vinicius, 22/09: a figura do canto sai da linha de `imagem_publica` (o
    mesmo id que o link /api/imagens/… abre). Sem a linha — e com uma imagem
    ilegível — o cartão continua saindo, só que sem o logo."""
    from app.models import ImagemPublica
    from app.services import imagem_publica, logistica_track

    monkeypatch.setattr(imagem_publica, "_cache", {})
    row = _linha()
    _patch_eventos(
        monkeypatch,
        {row.rastreio: logistica_track.eventos_de_track_info(_track_info([_EV_ETIQUETA]))},
    )
    sem_logo = await msgs.montar_cartao(db, row)
    assert sem_logo is not None  # tabela vazia: cartão sai sem a figura

    logo = _logo_fake()
    db.add(
        ImagemPublica(
            id=imagem_publica.LOGO_CORREIOS,
            nome="correios.png",
            content_type="image/png",
            size_bytes=len(logo),
            blob=logo,
        )
    )
    await db.commit()
    com_logo = await msgs.montar_cartao(db, row)
    assert com_logo is not None
    assert _canto_superior_direito(com_logo[2]) == _LOGO_COR
    assert _canto_superior_direito(sem_logo[2]) == (255, 255, 255)

    # Lixo no lugar da imagem: a mensagem é o que importa, o cartão vai sem ela.
    monkeypatch.setattr(imagem_publica, "_cache", {imagem_publica.LOGO_CORREIOS: b"nao e png"})
    quebrado = await msgs.montar_cartao(db, row)
    assert quebrado is not None
    assert _canto_superior_direito(quebrado[2]) == (255, 255, 255)


@pytest.mark.asyncio
async def test_robo_anexa_o_cartao_na_mensagem(db: AsyncSession, ligado, monkeypatch):
    from app.services import logistica_track

    db.add(_linha(entregue_em=datetime(2026, 9, 22, 12, 0, tzinfo=UTC)))
    await db.commit()
    _patch_eventos(
        monkeypatch,
        {
            "AD912266053BR": logistica_track.eventos_de_track_info(
                _track_info([_EV_TRANSFERENCIA, _EV_POSTADO])
            )
        },
    )
    sender = FakeSender()
    out = await msgs.run(db, sender=sender, hoje=HOJE)
    assert out["enviadas"] >= 1
    for enviado in sender.enviados:
        anexos = enviado["attachments"]
        assert anexos and anexos[0][0] == "rastreio.png"
        assert anexos[0][2].startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_numero_que_o_17track_nao_conhece_manda_mensagem_sem_anexo(
    db: AsyncSession, ligado, monkeypatch
):
    """Sem evento nenhum não há o que desenhar — mas a mensagem vai."""
    db.add(_linha())
    await db.commit()
    _patch_eventos(monkeypatch, {})
    sender = FakeSender()
    out = await msgs.run(db, sender=sender, hoje=HOJE)
    assert out["enviadas"] >= 1
    assert all(e["attachments"] is None for e in sender.enviados)


# ---- disparo controlado (vai pro comprador de verdade) ----


@pytest.mark.asyncio
async def test_enviar_agora_vai_pro_comprador_com_o_cartao(
    db: AsyncSession, ligado, monkeypatch
):
    from app.services import logistica_track

    db.add(_linha())
    await db.commit()
    _patch_eventos(
        monkeypatch,
        {
            "AD912266053BR": logistica_track.eventos_de_track_info(
                _track_info([_EV_TRANSFERENCIA, _EV_POSTADO])
            )
        },
    )
    sender = FakeSender()
    res = await msgs.enviar_agora(
        db, pedido_bling="296762", evento="rastreio", sender=sender
    )
    assert res["destinatario"] == "n340cj40yxfjsq4@marketplace.amazon.com.br"
    assert res["cartao"] is True
    enviado = sender.enviados[0]
    assert enviado["to"] == res["destinatario"] and enviado["html"] == ""
    assert enviado["attachments"][0][0] == "rastreio.png"
    # Fica no histórico do pedido, como qualquer mensagem do robô.
    m = (
        await db.execute(
            select(LogisticaMensagemCliente).where(
                LogisticaMensagemCliente.evento == "rastreio"
            )
        )
    ).scalar_one()
    assert m.enviado_em is not None and m.erro is None

    # Reenvia de propósito: sem isso não dava pra testar duas vezes.
    await msgs.enviar_agora(db, pedido_bling="296762", evento="rastreio", sender=sender)
    assert len(sender.enviados) == 2


@pytest.mark.asyncio
async def test_reenvio_do_painel_nao_gasta_o_teto_do_robo(
    db: AsyncSession, ligado, monkeypatch
):
    """O ⟳ da linha fica ao lado do "(falhou)" — é lá que a pessoa clica quando
    o Mailjet está fora. Se o clique gastasse uma tentativa do robô, ele
    desistiria daquele aviso pra sempre (nada zera o contador)."""
    _patch_eventos(monkeypatch, {})  # sem cartão: o que está em teste é o contador
    db.add(_linha())
    await db.commit()
    ruim = FakeSender(falha=True)
    await msgs.run(db, sender=ruim, hoje=HOJE)
    await msgs.run(db, sender=ruim, hoje=HOJE)  # robô já queimou 2 das 3

    for _ in range(2):  # a pessoa aperta o ⟳ com o Mailjet ainda fora
        with pytest.raises(RuntimeError):
            await msgs.enviar_agora(
                db, pedido_bling="296762", evento="rastreio", sender=ruim
            )

    bom = FakeSender()
    out = await msgs.run(db, sender=bom, hoje=HOJE)  # Mailjet voltou
    assert out["enviadas"] >= 1
    m = (
        await db.execute(
            select(LogisticaMensagemCliente).where(
                LogisticaMensagemCliente.evento == "rastreio"
            )
        )
    ).scalar_one()
    assert m.enviado_em is not None and m.erro is None


@pytest.mark.asyncio
async def test_enviar_agora_recusa_o_que_nao_e_mensagem_ao_comprador(
    db: AsyncSession, ligado, monkeypatch
):
    _patch_eventos(monkeypatch, {})
    db.add(_linha(pedido_bling="296763", amazon_canal="dba"))
    db.add(_linha(pedido_bling="296764", cliente_email="pessoa@gmail.com"))
    await db.commit()
    sender = FakeSender()
    for pedido, code in (
        ("999999", "pedido_nao_encontrado"),
        ("296763", "pedido_nao_e_envio_proprio"),
        ("296764", "pedido_sem_email_do_comprador"),
    ):
        with pytest.raises(msgs.TemplateInvalidoError) as e:
            await msgs.enviar_agora(db, pedido_bling=pedido, evento="rastreio", sender=sender)
        assert e.value.code == code
    with pytest.raises(msgs.TemplateInvalidoError) as e:
        await msgs.enviar_agora(db, pedido_bling="296762", evento="xpto", sender=sender)
    assert e.value.code == "evento_desconhecido"
    assert sender.enviados == []


@pytest.mark.asyncio
async def test_enviar_agora_exige_a_chave_do_servidor_ligada(db: AsyncSession, monkeypatch):
    """Chave desligada = remetente não aprovado no Seller Central: a Amazon
    descartaria e o teste não provaria nada."""
    monkeypatch.setattr(
        msgs, "get_settings", lambda: SimpleNamespace(amazon_mensagens_cliente=False)
    )
    db.add(_linha())
    await db.commit()
    sender = FakeSender()
    with pytest.raises(msgs.TemplateInvalidoError) as e:
        await msgs.enviar_agora(db, pedido_bling="296762", evento="rastreio", sender=sender)
    assert e.value.code == "envio_desligado"
    assert sender.enviados == []
