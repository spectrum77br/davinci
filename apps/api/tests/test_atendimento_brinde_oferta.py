"""A OFERTA do brinde no motor de automações (o dono, 05/11/2026).

3 h depois de um pedido criado e não pago, a oferta: celular ganha 1 AirTag, mala
ganha 1 chaveiro, se finalizar. Aqui, com banco: a classificação pelos textos reais
de `itens_resumo`, a decisão pura, a descoberta e a simulação (nada sai), pago,
cancelado e um por comprador em 7 dias, o cupom que não interfere, a janela das 9h às
21h, a conferência AO VIVO na Shopee antes de enviar, o Enviar sem o acordo com o
Duoke, a loja com a IA no automático e a trava do agente depois da oferta enviada.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.models import AtendimentoPedidoComprador, BlingOrder
from app.services.atendimento import automacoes, clientes, enviar, ia
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import AUTOMACOES_BRINDE, AUTOMACOES_NAO_PAGO_AVISO
from tests.test_atendimento_automacoes_motor import (
    DESDE,
    T,
    _agendada,
    _conversa,
    _dono,
    _linhas,
    _loja,
    _n_mensagens,
    _rodada,
)
from tests.test_atendimento_automacoes_motor import (
    _regra as _regra_motor,
)
from tests.test_atendimento_automacoes_simulacao import (  # noqa: F401 — as fixtures
    _bling_pago,
    _chaves,
    _redis,
    _sem_financeiro,
    pessoa,
    proibido,
    sem_plataforma,
    tudo_ligado,
)

BRINDE = "shopee_nao_pago_brinde"
CELULAR = "Uranyx A17 Pro Max 12.64 ou 12.128 GB ou 16.128 Premium Edition NFC"
MALA = "Mala de Viagem Despacho 24 Polegadas 23kg Material ABS Chapa Dupla Rodas 360"
RELOGIO = "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple"
TEXTO_AIRTAG = (
    "Oi! Vimos que seu pedido ainda está aguardando pagamento. Finalizando o pagamento, "
    "você ganha 1 AirTag Uranyx de presente, enviado junto com o seu celular. 🎁"
)
TEXTO_CHAVEIRO = (
    "Oi! Vimos que seu pedido ainda está aguardando pagamento. Finalizando o pagamento, "
    "você ganha 1 chaveiro exclusivo de presente, enviado junto com a sua mala. 🎁"
)
# 09/11/2026, o dono: o brinde vai às 20 h (às 3 h, o lembrete sem brinde).
ATRASO = timedelta(minutes=180)


async def _regra(db, integ, codigo, modo="simular", **campos):
    """As regras destes testes com o atraso de antes (3 h): o mecanismo da oferta não muda
    com a hora. As 20 h e a madrugada (09/11/2026) têm os testes delas."""
    if codigo in ("shopee_nao_pago_brinde", "tiktok_nao_pago_brinde"):
        campos.setdefault("atraso_min", 180)
    return await _regra_motor(db, integ, codigo, modo, **campos)


async def _nao_pago(
    db,
    integ,
    pedido: str,
    *,
    itens: str | None = CELULAR,
    status: str = "UNPAID",
    criado: datetime = T - ATRASO - timedelta(minutes=10),
    comprador: str = "901",
    total: float = 1200.0,
) -> AtendimentoPedidoComprador:
    p = AtendimentoPedidoComprador(
        integration_id=integ.id,
        plataforma="shopee",
        comprador_id=comprador,
        pedido=pedido,
        criado_em=criado,
        total=total,
        status=status,
        itens_resumo=itens,
        atualizado_em=T - timedelta(minutes=1),
    )
    db.add(p)
    await db.commit()
    return p


def _cliente_ao_vivo(monkeypatch, *, status: str | None = "UNPAID", erro: bool = False) -> list:
    """A Shopee falsa do `get_order_buyer`: anota cada pedido perguntado."""
    pedidos: list[str] = []

    class Cliente:
        async def get_order_buyer(self, sn):
            pedidos.append(sn)
            if erro:
                raise RuntimeError("shopee fora do ar")
            return {} if status is None else {"buyer_user_id": 901, "order_status": status}

    async def _cliente(_integ):
        return Cliente()

    monkeypatch.setattr(clientes, "cliente_da_integracao", _cliente)
    return pedidos


# ── a classificação, com os textos reais do índice (SELECT em produção, 05/11/2026) ──


@pytest.mark.parametrize(
    ("itens", "esperado"),
    [
        (CELULAR, "airtag"),
        ("Uranyx F105 64 GB + 64 Sd Card Resistente", "airtag"),
        ("Smartphones Uranyx Séries C com 64,128,256 e 512 GB Memoria Varios Modelos", "airtag"),
        (
            "1x Uranyx A17 Pro Max 64 ou 128 Gb Memoria 12 ou 16 Gb NFC (Laranja,12 GB / 128 GB)",
            "airtag",
        ),
        # O erro corrigido: o "×" que o índice grava na quantidade (47 pedidos em 30 dias).
        ("2× Uranyx A17 Pro Max 12.64 ou 12.128 GB ou 16.128 Premium Edition NFC", "airtag"),
        ("14× Uranyx A17 Pro Max 64 ou 128 Gb Memoria 12 ou 16 Gb NFC", "airtag"),
        ("Uranyx S7 16.128 GB ou 32.256 GB Android 16; Uranyx S7 16.128 GB ou 32.256 GB", "airtag"),
        ("Aparelhos Celulares Smartphone Usados Uranyx Oukitel Fossibot Doogee Itel", "airtag"),
        (MALA, "chaveiro"),
        (
            "kit Mala e Frasqueira De Viagem Bordo ABS  12 e 18 Polegadas 10kg Padrão ANAC",
            "chaveiro",
        ),
        ("Kit 2 Malas de Viagem Conjunto Bordo e Despacho 18 e 24 Polegadas 10kg 23kg", "chaveiro"),
        ("Conjunto Mala Polipropileno De Despacho 24 Polegadas 23 kg + Frasqueira", "chaveiro"),
        ("2× Malas De Bordo ou Despacho Polegadas Pequenas Médias Avarias Completas", "chaveiro"),
        ("Kit 3 Mala e Frasqueira De Viagem 14 20 24 Pol; 2× Almofada de Pescoço", "chaveiro"),
        # Os erros corrigidos: a roda de reposição e a frasqueira/necessaire sozinhas.
        (
            "Kit 2 Rodinhas 360 Graus Silicone para Mala de Viagem Reposição Roda Sobressalente",
            None,
        ),
        ("Frasqueira Grande Mala De Mão De Viagem 14” Maleta de Maquiagem Polipropileno", None),
        ("Necessaire Rígida de Viagem 8 Polegadas Frasqueira Porta Maquiagem Mala de Mão", None),
        ("Maleta Maquiagem Profissional Grande Frasqueira 12 Polegadas Necessaire Viagem", None),
        # Os erros corrigidos (títulos reais de `product_links`, 05/11/2026): citam "mala",
        # mas não são mala. O título de mala traz "Rodinhas 360" e "Cadeado": âncora no início.
        ("Chaveiro Charlots Pantera de Pelúcia de Mala Mochila e Bolsa Pingente", None),
        ("Balança Digital de Mão 50kg Gancho para Pesar Mala de Viagem", None),
        ("Par de Rodinhas 360 Graus ou Balança Digital para Malas de Viagem Poofy", None),
        ("Kit 4 Unidades de Rodinhas 360 para Mala de Viagem", None),
        ("2× Chaveiro de Pelúcia para Mala", None),
        ("Mala de Viagem Rodinhas 360 Graus com Cadeado TSA e Chaveiro", "chaveiro"),
        ("Kit 2 Malas de Viagem Rodinhas 360 e Balança Digital de Brinde", "chaveiro"),
        (RELOGIO, None),
        ("Relogio Apple Watch Se 2 GPS  - Original Com Nota Fiscal", None),
        ("AirFryer Fritadeira Digital Uranyx 4L 1500w de Vidro Tela Touch 110-220v", None),
        ("Air Fryer Churrasqueira Barbecue Uranyx 8L Fritadeira Elétrica Sem Óleo", None),
        ("Apple Macbook Neo 8.256 GB Memoria - Original Lacrado", None),
        ("", None),
        (None, None),
    ],
)
def test_brinde_do_resumo_com_textos_reais(itens, esperado):
    assert cat.brinde_do_resumo(itens) == esperado


# ── a decisão pura ────────────────────────────────────────────────────────


def test_catalogo_da_oferta():
    aut = cat.CATALOGO[BRINDE]
    assert BRINDE in AUTOMACOES_BRINDE
    assert (aut.tipo, aut.gatilho, aut.alvo, aut.canal, aut.atraso_min) == (
        cat.TIPO_NAO_PAGO_BRINDE,
        cat.GATILHO_NAO_PAGO,
        cat.ALVO_PEDIDO,
        "chat",
        1200,
    )
    assert aut.campanha and aut.duoke_nao_manda and aut.so_simular is None
    assert aut.janela == (time(9, 0), time(21, 0))
    # A madrugada volta para a véspera (`antes_da_noite`): a Shopee cancela com 24 h.
    assert aut.antes_da_noite and aut.validade >= timedelta(hours=12), "cobre a madrugada"
    assert aut.condicoes["um_por_comprador_h"] == 168
    assert [p["tipo"] for p in aut.partes] == ["cartao_pedido", "texto"]
    # Todas as lojas Shopee de produção, com os nomes antigos e os novos.
    for nome in (" Barbosa", "aguiar", "Atlas", "jlas", "Fiore", "kia", "Victor Mei", "vortan"):
        assert cat.nome_normalizado(nome) in aut.lojas_duoke, nome
    # O Duoke não manda: nenhum modelo dele casa com esta automação.
    assert cat.automacao_do_modelo(cat.TIPO_NAO_PAGO, None, "shopee", "chat") == "shopee_nao_pago"
    partes, motivos = cat.renderizar(
        cat.partes_padrao(aut),
        comprador="maria",
        plataforma="shopee",
        canal="chat",
        valores=cat.valores_de_exemplo(aut),
    )
    assert motivos == [] and partes[1]["texto"] == TEXTO_AIRTAG


@pytest.mark.parametrize(
    ("fatos", "motivo"),
    [
        ({"status_pedido": "UNPAID", "brinde": "airtag"}, None),
        ({"status_pedido": "UNPAID", "brinde": "chaveiro"}, None),
        ({"status_pedido": "UNPAID", "brinde": "airtag", "pedido_pago": True}, "pedido_pago"),
        (
            {"status_pedido": "UNPAID", "brinde": "airtag", "pedido_cancelado": True},
            "pedido_cancelado",
        ),
        ({"status_pedido": "READY_TO_SHIP", "brinde": "airtag"}, "status_mudou"),
        ({"status_pedido": None, "brinde": "airtag"}, "status_mudou"),
        ({"status_pedido": "UNPAID", "brinde": None}, "sem_brinde"),
        ({"status_pedido": "UNPAID", "brinde": "airtag", "ja_recebeu": True}, "ja_recebeu"),
        # A loja com a IA no automático: sai também (o dono: em todas as lojas).
        ({"status_pedido": "UNPAID", "brinde": "airtag", "canal_auto": True}, None),
        (
            {"status_pedido": "UNPAID", "brinde": "airtag", "conversa_bloqueada": True},
            "conversa_bloqueada",
        ),
    ],
)
def test_decidir(fatos, motivo):
    assert cat.decidir(cat.CATALOGO[BRINDE], fatos) == motivo


def test_a_condicao_nova_nao_muda_as_outras_automacoes():
    assert cat.decidir(cat.CATALOGO["shopee_nao_pago"], {"canal_auto": True}) == "ia_no_automatico"
    assert cat.decidir(cat.CATALOGO["shopee_menu"], {"canal_auto": True}) == "ia_no_automatico"
    # Nem com a condição na regra: só o brinde lê a condição fora da avaliação.
    com = SimpleNamespace(partes=None, condicoes={"com_ia_no_automatico": True})
    assert cat.decidir(cat.CATALOGO["shopee_menu"], {"canal_auto": True}, com) == "ia_no_automatico"
    # Desligada na regra, o brinde também para (a condição vale).
    sem = SimpleNamespace(partes=None, condicoes={"com_ia_no_automatico": False})
    fatos = {"canal_auto": True, "status_pedido": "UNPAID", "brinde": "airtag"}
    assert cat.decidir(cat.CATALOGO[BRINDE], fatos, sem) == "ia_no_automatico"


# ── a descoberta e a simulação ────────────────────────────────────────────


async def test_as_3_h_simula_com_o_texto_certo_e_nada_sai(db, sem_plataforma, client, pessoa):  # noqa: F811
    barbosa, _ = await _loja(db, pessoa, "barbosa")
    inova, _ = await _loja(db, pessoa, "inova")
    await _regra(db, barbosa, BRINDE)
    await _regra(db, inova, BRINDE)
    await _nao_pago(db, barbosa, "ACEL", comprador="901")
    await _nao_pago(db, inova, "BMALA", itens=MALA, comprador="902", total=300)
    await _nao_pago(db, barbosa, "CREL", itens=RELOGIO, comprador="903")
    await _nao_pago(db, barbosa, "DPAGO", comprador="904")
    await _bling_pago(db, barbosa, "DPAGO", em=T - timedelta(minutes=30))
    # Ainda não deu 3 h: nasce e espera.
    await _nao_pago(db, barbosa, "ECEDO", comprador="905", criado=T - timedelta(minutes=70))
    antes = await _n_mensagens(db)

    resumo = await _rodada()

    assert resumo["nao_pagos"] == 5
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    a = por["ACEL"]
    assert (a.estado, a.modo, a.motivo, a.duoke) == ("simulado", "simular", None, "nao_se_aplica")
    assert a.devido_em == T - timedelta(minutes=10)
    assert (por["BMALA"].estado, por["BMALA"].motivo) == ("simulado", None)
    assert (por["CREL"].estado, por["CREL"].motivo) == ("pulado", "sem_brinde")
    assert (por["DPAGO"].estado, por["DPAGO"].motivo) == ("pulado", "pedido_pago")
    assert por["ECEDO"].estado == "agendado" and por["ECEDO"].devido_em == T + timedelta(
        minutes=110
    )
    assert await _n_mensagens(db) == antes
    assert sem_plataforma == [], "no modo simular, nenhuma chamada à plataforma"
    # A prévia preenche a lacuna pelo item de CADA pedido.
    for pedido, texto in (("ACEL", TEXTO_AIRTAG), ("BMALA", TEXTO_CHAVEIRO)):
        r = await client.get(f"/api/atendimento/automacoes/registro/{por[pedido].id}/previa")
        assert r.status_code == 200, r.text
        assert texto in r.text
        assert "{brinde_oferta}" not in r.text


async def test_cancelado_e_o_status_velho_pulam(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "atv")
    await _regra(db, integ, BRINDE)
    await _nao_pago(db, integ, "ACANC", comprador="901")
    await _nao_pago(db, integ, "BPAGOU", comprador="902")
    await _rodada(T - timedelta(minutes=30))  # nascem agendadas (faltam 20 min)
    for p in (await db.execute(AtendimentoPedidoComprador.__table__.select())).all():
        novo = "CANCELLED" if p.pedido == "ACANC" else "READY_TO_SHIP"
        await db.execute(
            AtendimentoPedidoComprador.__table__.update()
            .where(AtendimentoPedidoComprador.__table__.c.pedido == p.pedido)
            .values(status=novo)
        )
    await db.commit()
    await _rodada()
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert (por["ACANC"].estado, por["ACANC"].motivo) == ("pulado", "pedido_cancelado")
    assert (por["BPAGOU"].estado, por["BPAGOU"].motivo) == ("pulado", "status_mudou")
    assert proibido == []


async def test_um_por_comprador_e_loja_em_7_dias(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    outra, _ = await _loja(db, dono, "vita")
    regra = await _regra(db, integ, BRINDE)
    await _regra(db, outra, BRINDE)
    # Outra oferta do mesmo comprador há 6 dias e 23 h; uma de 8 dias atrás não conta.
    await _agendada(
        db,
        regra,
        evento=T - timedelta(days=7),
        devido=T - timedelta(days=6, hours=23),
        pedido="ZANTIGA",
        estado="simulado",
        comprador_id="901",
    )
    await _agendada(
        db,
        regra,
        evento=T - timedelta(days=8),
        devido=T - timedelta(days=8),
        pedido="YMAISANTIGA",
        estado="simulado",
        comprador_id="902",
    )
    await _nao_pago(db, integ, "APERTO", comprador="901")
    await _nao_pago(db, integ, "BLONGE", comprador="902")
    # Dois não pagos do mesmo comprador no MESMO lote: só o primeiro.
    await _nao_pago(db, integ, "CLOTE1", comprador="903", criado=T - ATRASO - timedelta(minutes=20))
    await _nao_pago(db, integ, "DLOTE2", comprador="903", criado=T - ATRASO - timedelta(minutes=10))
    # O mesmo comprador em OUTRA loja recebe.
    await _nao_pago(db, outra, "EOUTRA", comprador="901")
    await _rodada()
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert (por["APERTO"].estado, por["APERTO"].motivo) == ("pulado", "ja_recebeu")
    assert por["BLONGE"].estado == "simulado"
    assert por["CLOTE1"].estado == "simulado"
    assert (por["DLOTE2"].estado, por["DLOTE2"].motivo) == ("pulado", "ja_recebeu")
    assert por["EOUTRA"].estado == "simulado"
    assert proibido == []


async def test_o_cupom_nao_interfere(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_nao_pago")
    await _regra(db, integ, BRINDE)
    # O cupom sai aos 30 min; o brinde, às 3 h, para o MESMO comprador.
    await _nao_pago(db, integ, "ACEL", comprador="901")
    await _nao_pago(db, integ, "BCEL", comprador="902", criado=T - timedelta(minutes=50))
    await _rodada()
    cupom = {x.pedido: x for x in await _linhas(db, automacao="shopee_nao_pago")}
    brinde = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert cupom["ACEL"].estado == "simulado" and brinde["ACEL"].estado == "simulado"
    assert cupom["ACEL"].chave == brinde["ACEL"].chave, "a mesma chave, automações diferentes"
    assert cupom["BCEL"].estado == "simulado" and brinde["BCEL"].estado == "agendado"
    # O cupom simulado do comprador não conta como "já recebeu" do brinde.
    await _rodada(T + timedelta(hours=2, minutes=15))
    brinde = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert (brinde["BCEL"].estado, brinde["BCEL"].motivo) == ("simulado", None)
    assert proibido == []


async def test_a_madrugada_espera_as_9h_e_nao_vence(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    await _regra(db, integ, BRINDE)
    # Criado às 20h BRT: as 3 h caem às 23h, fora da janela.
    criado = datetime(2026, 11, 4, 23, 0, tzinfo=UTC)
    await _nao_pago(db, integ, "ANOITE", criado=criado)
    noite = criado + ATRASO + timedelta(minutes=1)  # 23h01 BRT
    await _rodada(noite, motor_desde=criado - timedelta(days=1))
    [x] = await _linhas(db, automacao=BRINDE)
    nove = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    assert (x.estado, x.devido_em) == ("agendado", nove)
    await _rodada(nove + timedelta(minutes=1), motor_desde=criado - timedelta(days=1))
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("simulado", None)
    assert proibido == []


async def test_loja_com_a_ia_no_automatico_simula(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "vita")
    canal.modo = "auto"
    await db.commit()
    await _regra(db, integ, BRINDE)
    await _regra(db, integ, "shopee_nao_pago")
    await _nao_pago(db, integ, "ACEL")
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("simulado", None)
    [cupom] = await _linhas(db, automacao="shopee_nao_pago")
    assert (cupom.estado, cupom.motivo) == ("pulado", "ia_no_automatico"), "o cupom segue como era"


# ── quem refez o pedido e pagou (05/11/2026) ──────────────────────────────


async def test_quem_refez_o_pedido_e_pagou_nao_recebe(db, tudo_ligado, monkeypatch):  # noqa: F811
    """O A segue UNPAID na Shopee (89% dos cancelados ainda estão abertos às 3 h), o B está pago."""
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901", pedido="AVELHO")
    await _nao_pago(db, integ, "AVELHO", comprador="901")
    await _nao_pago(
        db,
        integ,
        "BPAGO",
        comprador="901",
        status="READY_TO_SHIP",
        criado=T - ATRASO + timedelta(minutes=10),
    )
    _cliente_ao_vivo(monkeypatch)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.pedido, x.estado, x.motivo) == ("AVELHO", "pulado", "pedido_pago")
    assert tudo_ligado.auto_replies == []


async def test_o_indice_velho_nao_engana_o_bling_ja_tem_o_outro(db, tudo_ligado, monkeypatch):  # noqa: F811
    """O índice do B ainda diz UNPAID (hora em hora), mas o Bling só recebe pedido pago."""
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901", pedido="AVELHO")
    await _nao_pago(db, integ, "AVELHO", comprador="901", criado=T - ATRASO - timedelta(minutes=30))
    await _nao_pago(db, integ, "BNOBLING", comprador="901", criado=T - timedelta(hours=1))
    db.add(
        BlingOrder(
            bling_id=77001, numero="77001", numeroloja="BNOBLING", item_index=0, situacao="6"
        )
    )
    await db.commit()
    _cliente_ao_vivo(monkeypatch)
    await _rodada()
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert (por["AVELHO"].estado, por["AVELHO"].motivo) == ("pulado", "pedido_pago")
    assert tudo_ligado.auto_replies == []


@pytest.mark.parametrize(
    ("status", "bling", "comprador", "criado_ha_h"),
    [
        ("CANCELLED", None, "901", 1),  # o outro foi cancelado: nada pago
        ("IN_CANCEL", None, "901", 1),
        ("UNPAID", "12", "901", 1),  # no Bling, mas cancelado
        ("READY_TO_SHIP", None, "902", 1),  # pago por OUTRO comprador
        ("READY_TO_SHIP", None, "901", 60),  # pago há mais de 48 h
    ],
)
async def test_o_que_nao_conta_como_ter_pago_o_outro(
    db,
    tudo_ligado,  # noqa: F811
    monkeypatch,
    status,
    bling,
    comprador,
    criado_ha_h,
):
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901", pedido="AVELHO")
    await _nao_pago(db, integ, "AVELHO", comprador="901")
    await _nao_pago(
        db,
        integ,
        "BOUTRO",
        comprador=comprador,
        status=status,
        criado=T - timedelta(hours=criado_ha_h),
    )
    if bling:
        db.add(
            BlingOrder(
                bling_id=77002, numero="77002", numeroloja="BOUTRO", item_index=0, situacao=bling
            )
        )
        await db.commit()
    _cliente_ao_vivo(monkeypatch)
    await _rodada()
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert por["AVELHO"].estado == "enviado"


async def test_o_cupom_nao_ganha_a_trava_do_brinde(db, proibido):  # noqa: F811
    """A regra do `shopee_nao_pago` é comparada com o Duoke: fica como era."""
    fatos = await _extras_de(db, "shopee_nao_pago")
    assert fatos["pedido_pago"] is False
    fatos = await _extras_de(db, BRINDE)
    assert fatos["pedido_pago"] is True


async def _extras_de(db, automacao: str) -> dict:
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, f"l{uuid4().hex[:5]}")
    regra = await _regra(db, integ, automacao)
    await _nao_pago(db, integ, f"A{automacao}", comprador="901")
    await _nao_pago(db, integ, f"B{automacao}", comprador="901", status="COMPLETED")
    linha = await _agendada(
        db,
        regra,
        evento=T - ATRASO,
        devido=T,
        pedido=f"A{automacao}",
        comprador_id="901",
    )
    ctx = SimpleNamespace(agora=T, regras={(automacao, integ.id): regra}, conversas={})
    return (await automacoes._extras(db, ctx, [linha]))[linha.id]


# ── o envio, com a conferência AO VIVO ────────────────────────────────────


async def _em_enviar(db, nome="barbosa", enviar_desde=T - timedelta(hours=1)):
    """A regra acabou de ir para enviar: a transição do Duoke não segura esta."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, nome)
    await _regra(db, integ, BRINDE, modo="enviar", enviar_desde=enviar_desde)
    return integ, canal


def test_madrugada_volta_para_a_vespera():
    """09/11/2026, o dono: o brinde às 20 h. A Shopee cancela com 24 h: o que cairia de
    madrugada sai às 20h45 da véspera, nunca na abertura do dia seguinte. 10/11/2026: 20h45
    e não 20h59 — o motor roda nos minutos pares e a 20h59 só era vista às 21h00, fechada."""
    janela = (time(9, 0), time(21, 0))
    sp = ZoneInfo("America/Sao_Paulo")

    def em(dia, hora, minuto=0):
        return datetime(2026, 11, dia, hora, minuto, tzinfo=sp)

    assert cat.devido_antes_da_noite(em(10, 15), janela) == em(10, 15)
    assert cat.devido_antes_da_noite(em(10, 9), janela) == em(10, 9)
    assert cat.devido_antes_da_noite(em(10, 21), janela) == em(10, 20, 45)
    assert cat.devido_antes_da_noite(em(10, 23, 30), janela) == em(10, 20, 45)
    assert cat.devido_antes_da_noite(em(10, 5), janela) == em(9, 20, 45)
    assert cat.devido_antes_da_noite(em(10, 8, 59), janela) == em(9, 20, 45)
    assert cat.devido_antes_da_noite(em(10, 3), None) == em(10, 3)
    # Pedido das 6h: 20 h depois seria 2h, volta para as 20h45 do mesmo dia (15 h).
    criado = em(10, 6)
    devido = cat.devido_antes_da_noite(criado + timedelta(hours=20), janela)
    assert devido == em(10, 20, 45) and criado < devido < criado + timedelta(hours=24)


@pytest.mark.parametrize(
    ("hora", "minuto", "esperado"),
    [
        # 20 h depois cai às 8h30: as 9h vêm com 20h30 de pedido, antes do cancelamento.
        (12, 30, (11, 9, 0)),
        # 20 h depois cai às 5h30: as 9h seriam 23h30 de pedido, volta para a véspera.
        (9, 30, (10, 20, 45)),
        # 20 h depois cai às 2h: volta para as 20h45 do mesmo dia (15 h de pedido).
        (6, 0, (10, 20, 45)),
        # Dentro da janela: 20 h cravadas.
        (15, 0, (11, 11, 0)),
    ],
)
def test_o_brinde_de_20_h_nunca_passa_do_cancelamento(hora, minuto, esperado):
    sp = ZoneInfo("America/Sao_Paulo")
    aut = cat.CATALOGO[BRINDE]
    regra = SimpleNamespace(atraso_min=1200, janela_inicio=time(9, 0), janela_fim=time(21, 0))
    criado = datetime(2026, 11, 10, hora, minuto, tzinfo=sp)
    # Quando sai: o `devido` gravado, andado para a abertura se cair de madrugada.
    sai = cat.ajustar_janela(automacoes._devido_do_nao_pago(aut, regra, criado), time(9), time(21))
    dia, h, m = esperado
    assert sai == datetime(2026, 11, dia, h, m, tzinfo=sp)
    assert sai < criado + timedelta(hours=23)


def test_catalogo_do_lembrete():
    for codigo, plataforma in (
        ("shopee_nao_pago_lembrete", "shopee"),
        ("tiktok_nao_pago_lembrete", "tiktok"),
    ):
        aut = cat.CATALOGO[codigo]
        assert (aut.tipo, aut.gatilho, aut.plataforma, aut.atraso_min) == (
            cat.TIPO_NAO_PAGO_LEMBRETE,
            cat.GATILHO_NAO_PAGO,
            plataforma,
            180,
        )
        assert aut.janela == (time(9, 0), time(21, 0)) and not aut.antes_da_noite
        assert aut.antes_da_noite_desde == timedelta(hours=1)
        assert "brinde_oferta" not in (aut.lacunas or ())
        assert "AirTag" not in str(aut.partes) and "brinde" not in str(aut.partes)
        # Não é oferta: a IA segue respondendo e o botão do brinde não aparece.
        assert codigo not in AUTOMACOES_BRINDE and codigo in AUTOMACOES_NAO_PAGO_AVISO
    assert cat.CATALOGO["tiktok_nao_pago_brinde"].atraso_min == 1200


@pytest.mark.parametrize(
    ("hora", "minuto", "esperado"),
    [
        # 10/11/2026, o dono: o pedido das 18h38 levava o lembrete às 9h04 e o brinde às
        # 14h40. Agora o lembrete sai às 20h45 da mesma noite, longe do brinde.
        (18, 38, (10, 20, 45)),
        # Com 1 h cravada de pedido ainda vai.
        (19, 45, (10, 20, 45)),
        # Menos de 1 h de pedido às 20h45: espera as 9h, como antes.
        (19, 50, (11, 9, 0)),
        (22, 30, (11, 9, 0)),
        # De madrugada: 3 h depois ainda é antes das 9h, sai às 9h (nunca na véspera).
        (5, 0, (10, 9, 0)),
        # Dentro da janela: 3 h cravadas.
        (15, 0, (10, 18, 0)),
        (17, 59, (10, 20, 59)),
    ],
)
def test_lembrete_da_noite_sai_antes_de_fechar(hora, minuto, esperado):
    sp = ZoneInfo("America/Sao_Paulo")
    for codigo in ("shopee_nao_pago_lembrete", "tiktok_nao_pago_lembrete"):
        aut = cat.CATALOGO[codigo]
        regra = SimpleNamespace(atraso_min=180, janela_inicio=time(9, 0), janela_fim=time(21, 0))
        criado = datetime(2026, 11, 10, hora, minuto, tzinfo=sp)
        devido = automacoes._devido_do_nao_pago(aut, regra, criado)
        sai = cat.ajustar_janela(devido, time(9), time(21))
        dia, h, m = esperado
        assert sai == datetime(2026, 11, dia, h, m, tzinfo=sp), codigo
        assert sai >= criado + timedelta(hours=1)


def test_o_nao_pago_das_20h59_ainda_sai_na_rodada_das_21h():
    """10/11/2026: o motor roda nos minutos pares; o que vence às 20h59 só é visto às
    21h00, já fechado, e às 9h o pedido já foi cancelado. Sai até 15 min depois de fechar."""
    sp = ZoneInfo("America/Sao_Paulo")
    janela = (time(9, 0), time(21, 0))

    def em(hora, minuto=0, segundo=0):
        return datetime(2026, 11, 10, hora, minuto, segundo, tzinfo=sp)

    brinde = cat.CATALOGO[BRINDE]
    lembrete = cat.CATALOGO["shopee_nao_pago_lembrete"]
    for aut in (brinde, lembrete):
        assert cat.vale_depois_de_fechar(aut, em(20, 59), em(21, 0, 3), janela)
        assert cat.vale_depois_de_fechar(aut, em(20, 59), em(21, 15), janela)
        assert not cat.vale_depois_de_fechar(aut, em(20, 59), em(21, 15, 1), janela)
        # Contado do fechamento: a de 20h45 que as rodadas até 20h58 não alcançaram.
        assert cat.vale_depois_de_fechar(aut, em(20, 45), em(21, 0, 3), janela)
        assert cat.vale_depois_de_fechar(aut, em(20, 45), em(21, 14), janela)
        # Venceu já fechado (o motor parado, a leitura atrasada): espera a manhã.
        assert not cat.vale_depois_de_fechar(aut, em(21, 2), em(21, 4), janela)
        assert not cat.vale_depois_de_fechar(aut, em(20, 59), em(21, 0), None)
    # As outras automações seguem o horário à risca.
    outra = next(a for a in cat.CATALOGO.values() if a.tipo not in cat.TIPOS_NAO_PAGO_AO_VIVO)
    assert not cat.vale_depois_de_fechar(outra, em(20, 59), em(21, 0, 3), janela)

    regra = SimpleNamespace(atraso_min=1200, janela_inicio=time(9, 0), janela_fim=time(21, 0))
    linha = SimpleNamespace(devido_em=em(20, 59))
    agora = em(21, 0, 3)
    _, abre = automacoes._validade_e_abertura(brinde, regra, linha, agora)
    assert abre == agora
    _, abre = automacoes._validade_e_abertura(brinde, regra, linha, em(21, 20))
    assert abre == datetime(2026, 11, 11, 9, 0, tzinfo=sp)


async def test_o_brinde_de_20_h_nasce_com_a_vespera(db, sem_plataforma):  # noqa: F811
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    regra = await _regra_motor(db, integ, BRINDE)
    assert regra.atraso_min == 1200
    criado = T - timedelta(minutes=70)
    await _nao_pago(db, integ, "ACEL", criado=criado)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "agendado"
    assert x.devido_em == automacoes._devido_do_nao_pago(cat.CATALOGO[BRINDE], regra, criado)
    sai = cat.ajustar_janela(x.devido_em, time(9), time(21))
    assert criado + timedelta(hours=8) < sai < criado + timedelta(hours=23)


async def test_lembrete_das_3_h_sem_brinde(db, tudo_ligado, monkeypatch):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(
        db, integ, "shopee_nao_pago_lembrete", modo="enviar", enviar_desde=T - timedelta(hours=1)
    )
    conversa = await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    # O relógio também recebe: o lembrete não depende de ter brinde.
    await _nao_pago(db, integ, "ACEL", itens=RELOGIO)
    perguntados = _cliente_ao_vivo(monkeypatch)
    await _rodada()
    [x] = await _linhas(db, automacao="shopee_nao_pago_lembrete")
    assert (x.estado, x.conversa_id) == ("enviado", conversa.id)
    assert perguntados == ["ACEL"], "confere na Shopee que segue não pago"
    assert tudo_ligado.partes == ["cartao_pedido"]
    assert tudo_ligado.auto_replies == [cat.TEXTO_NAO_PAGO_LEMBRETE]


async def test_lembrete_nao_vai_para_quem_ja_pagou(db, tudo_ligado, monkeypatch):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(
        db, integ, "shopee_nao_pago_lembrete", modo="enviar", enviar_desde=T - timedelta(hours=1)
    )
    await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    await _nao_pago(db, integ, "ACEL")
    _cliente_ao_vivo(monkeypatch, status="READY_TO_SHIP")
    await _rodada()
    [x] = await _linhas(db, automacao="shopee_nao_pago_lembrete")
    assert (x.estado, x.motivo) == ("pulado", "status_mudou")
    assert tudo_ligado.auto_replies == []


async def test_envia_quando_a_shopee_confirma_unpaid(db, tudo_ligado, monkeypatch):  # noqa: F811
    integ, canal = await _em_enviar(db)
    # A leitura da loja está velha: a transição do Duoke seguraria outra automação.
    canal.ultimo_ok_em = T - timedelta(days=1)
    await db.commit()
    conversa = await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    await _nao_pago(db, integ, "ACEL")
    perguntados = _cliente_ao_vivo(monkeypatch)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.modo, x.conversa_id) == ("enviado", "enviar", conversa.id)
    assert perguntados == ["ACEL"], "UMA conferência na Shopee"
    assert tudo_ligado.partes == ["cartao_pedido"]
    assert tudo_ligado.auto_replies == [TEXTO_AIRTAG]
    assert tudo_ligado.envios == []


@pytest.mark.parametrize(
    ("status", "erro", "estado", "motivo"),
    [
        ("READY_TO_SHIP", False, "pulado", "status_mudou"),
        ("CANCELLED", False, "pulado", "status_mudou"),
        (None, False, "agendado", None),  # a Shopee não devolveu o pedido: não sei
        ("UNPAID", True, "agendado", None),  # erro de API: a próxima rodada
    ],
)
async def test_a_conferencia_ao_vivo_segura(
    db,
    tudo_ligado,  # noqa: F811
    monkeypatch,
    status,
    erro,
    estado,
    motivo,
):
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    await _nao_pago(db, integ, "ACEL")
    perguntados = _cliente_ao_vivo(monkeypatch, status=status, erro=erro)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == (estado, motivo)
    assert perguntados == ["ACEL"]
    assert tudo_ligado.partes == [] and tudo_ligado.auto_replies == []


async def test_erro_de_api_sai_na_rodada_seguinte(db, tudo_ligado, monkeypatch):  # noqa: F811
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    await _nao_pago(db, integ, "ACEL")
    _cliente_ao_vivo(monkeypatch, erro=True)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "agendado"
    _cliente_ao_vivo(monkeypatch)
    await _rodada(T + timedelta(minutes=2))
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "enviado" and tudo_ligado.auto_replies == [TEXTO_AIRTAG]


async def test_loja_com_a_ia_no_automatico_envia(db, tudo_ligado, monkeypatch):  # noqa: F811
    integ, canal = await _em_enviar(db, "vita")
    canal.modo = "auto"
    await db.commit()
    await _conversa(db, integ, canal, comprador="901", pedido="BMALA")
    await _nao_pago(db, integ, "BMALA", itens=MALA, total=300)
    _cliente_ao_vivo(monkeypatch)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "enviado" and tudo_ligado.auto_replies == [TEXTO_CHAVEIRO]


async def test_a_simulada_nao_segura_a_de_verdade_mas_a_enviada_sim(db, tudo_ligado, monkeypatch):  # noqa: F811
    integ, canal = await _em_enviar(db)
    regra = (await automacoes.regras_por_chave(db, ativas=False))[(BRINDE, integ.id)]
    await _agendada(
        db,
        regra,
        evento=T - timedelta(days=1),
        devido=T - timedelta(days=1),
        pedido="ZSIMULADA",
        estado="simulado",
        comprador_id="901",
    )
    await _agendada(
        db,
        regra,
        evento=T - timedelta(days=1),
        devido=T - timedelta(days=1),
        pedido="YENVIADA",
        estado="enviado",
        comprador_id="902",
    )
    await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    await _nao_pago(db, integ, "ACEL", comprador="901")
    await _conversa(db, integ, canal, comprador="902", pedido="BCEL")
    await _nao_pago(db, integ, "BCEL", comprador="902")
    perguntados = _cliente_ao_vivo(monkeypatch)
    await _rodada()
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert por["ACEL"].estado == "enviado"
    assert (por["BCEL"].estado, por["BCEL"].motivo) == ("pulado", "ja_recebeu")
    assert perguntados == ["ACEL"]


async def test_chave_de_auto_reply_desligada_nao_envia(db, tudo_ligado, monkeypatch, _chaves):  # noqa: F811
    _chaves.atendimento_automacoes_shopee_auto_reply = False
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    await _nao_pago(db, integ, "ACEL")
    perguntados = _cliente_ao_vivo(monkeypatch)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "campanha_sem_auto_reply")
    assert perguntados == [] and tudo_ligado.auto_replies == []


# ── a tela: Enviar sem o acordo com o Duoke; as chaves continuam ──────────


async def test_patch_enviar_sem_o_acordo_com_o_duoke(db, client, pessoa, tudo_ligado, _chaves):  # noqa: F811
    integ, _ = await _loja(db, pessoa, "aguiar")
    url = f"/api/atendimento/automacoes/{BRINDE}/{integ.id}"
    r = await client.patch(url, json={"modo": "simular"})
    assert r.status_code == 200, r.text
    # Sem a caixinha e sem o critério de 95%: um clique.
    r = await client.patch(url, json={"modo": "enviar"})
    assert r.status_code == 200, r.text
    assert r.json()["regra"]["modo"] == "enviar"
    r = await client.patch(url, json={"modo": "simular"})
    assert r.status_code == 200, r.text
    # As chaves gerais continuam obrigatórias.
    _chaves.atendimento_automacoes_envio = False
    r = await client.patch(url, json={"modo": "enviar"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_desligado", r.text
    # As outras continuam pedindo a caixinha.
    _chaves.atendimento_automacoes_envio = True
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_avaliacao_boa/{integ.id}",
        json={"modo": "enviar"},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "confirmar_duoke", r.text


async def test_lista_e_botao_de_simular_em_todas(db, client, pessoa):  # noqa: F811
    await _loja(db, pessoa, "barbosa")
    await _loja(db, pessoa, "aguiar")
    r = await client.post(f"/api/atendimento/automacoes/{BRINDE}/simular-nas-lojas-do-duoke")
    assert r.status_code == 200 and r.json()["criadas"] == 2, r.text
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee"})
    assert r.status_code == 200, r.text
    aut = next(a for a in r.json()["automacoes"] if a["codigo"] == BRINDE)
    assert aut["duoke_nao_manda"] is True and aut["so_simulacao"] is None
    assert set(aut["placeholders"]) == {"comprador", "brinde_oferta"}
    for loja in aut["lojas"]:
        assert loja["regra"]["modo"] == "simular"
        assert loja["regra"]["atraso_min"] == 1200
        # Sem critério de troca: nada a comparar com o Duoke.
        assert loja["pode_trocar"] is True and loja["por_que_nao_trocar"] == []


# ── o agente não responde sozinho depois da oferta enviada ────────────────


def _rascunho() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), fatos={}, prompt_versao="ag1", status="pendente")


async def test_a_ia_nao_responde_por_7_dias_depois_da_oferta(db, monkeypatch):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "vita")
    regra = await _regra(db, integ, BRINDE, modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    chamou: list[str] = []

    async def _nao_envia(*_a, **_k):
        chamou.append("enviar_resposta")
        raise AssertionError("a IA não responde depois da oferta")

    monkeypatch.setattr(enviar, "enviar_resposta", _nao_envia)
    monkeypatch.setattr(ia.get_settings(), "atendimento_auto_ativo", False)
    agora = datetime.now(UTC)

    # Sem oferta enviada: a trava não entra (o próximo motivo é o de sempre).
    r = _rascunho()
    assert await ia._talvez_enviar(db, conversa, r) is False
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "auto_desligado"

    # A oferta só simulada não segura; de outra conversa, também não.
    outra = await _conversa(db, integ, canal, comprador="902", pedido="BCEL")
    await _agendada(
        db,
        regra,
        evento=agora - timedelta(hours=4),
        devido=agora - timedelta(hours=1),
        pedido="ZSIM",
        conversa=conversa,
        estado="simulado",
        decidido_em=agora - timedelta(hours=1),
    )
    await _agendada(
        db,
        regra,
        evento=agora - timedelta(hours=4),
        devido=agora - timedelta(hours=1),
        pedido="YOUTRA",
        conversa=outra,
        estado="enviado",
        decidido_em=agora - timedelta(hours=1),
    )
    r = _rascunho()
    assert await ia._talvez_enviar(db, conversa, r) is False
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "auto_desligado"

    # Enviada há mais de 7 dias: já não segura.
    await _agendada(
        db,
        regra,
        evento=agora - timedelta(days=9),
        devido=agora - timedelta(days=8),
        pedido="XVELHA",
        conversa=conversa,
        estado="enviado",
        decidido_em=agora - timedelta(days=8),
    )
    r = _rascunho()
    assert await ia._talvez_enviar(db, conversa, r) is False
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == "auto_desligado"

    # Enviada há 6 dias: fica para pessoa, com o motivo próprio.
    await _agendada(
        db,
        regra,
        evento=agora - timedelta(days=7),
        devido=agora - timedelta(days=6),
        pedido="WVALE",
        conversa=conversa,
        estado="enviado",
        decidido_em=agora - timedelta(days=6),
    )
    r = _rascunho()
    assert await ia._talvez_enviar(db, conversa, r) is False
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == ia.MOTIVO_OFERTA_DE_BRINDE
    assert chamou == []
    assert await ia._oferta_de_brinde_recente(db, outra) is True


@pytest.mark.parametrize("decidido", [True, False])
async def test_a_ia_tambem_segura_a_oferta_em_revisar(db, monkeypatch, decidido):
    """O envio ambíguo pode ter entregue. O preso (`aposentar_enviando_presos`) não tem
    `decidido_em`: quem marca a hora é o `updated_at`."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "vita")
    regra = await _regra(db, integ, BRINDE, modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal, comprador="901", pedido="ACEL")
    monkeypatch.setattr(ia.get_settings(), "atendimento_auto_ativo", False)
    agora = datetime.now(UTC)
    await _agendada(
        db,
        regra,
        evento=agora - timedelta(hours=4),
        devido=agora - timedelta(hours=1),
        pedido="ACEL",
        conversa=conversa,
        estado="revisar",
        decidido_em=agora - timedelta(hours=1) if decidido else None,
        updated_at=agora - timedelta(hours=1),
    )
    r = _rascunho()
    assert await ia._talvez_enviar(db, conversa, r) is False
    assert r.fatos[ia.FATO_AUTO_ENVIO]["motivo"] == ia.MOTIVO_OFERTA_DE_BRINDE
