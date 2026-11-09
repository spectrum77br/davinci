"""O brinde do pedido não pago no TIKTOK (o dono, 05/11/2026).

Em cima do motor da Shopee (`test_atendimento_brinde_oferta.py`): o catálogo e as 6 lojas,
os títulos reais de anúncio do TikTok, a rodada própria que alimenta o índice (desligada
por padrão, só as lojas com a regra e o chat ok), a simulação que já conta "sem conversa",
a conferência AO VIVO no TikTok antes de enviar, o envio pela conversa do comprador com o
adaptador de verdade, a abertura de conversa atrás da chave e a trava da NF pelo nº do
TikTok. Tudo com a TikTok falsa: nenhuma chamada de verdade.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoConversa,
    AtendimentoPedidoComprador,
    BlingOrder,
)
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento import brinde, clientes, indexar, indice
from app.services.atendimento.constantes import AUTOMACOES_BRINDE
from app.services.marketplaces.tiktok import TikTokClient
from tests.test_atendimento_automacoes_motor import (
    T,
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
    proibido,
)
from tests.test_atendimento_indexar import RedisFalso

BRINDE = "tiktok_nao_pago_brinde"
PEDIDO = "584000000000000001"
CELULAR = "Uranyx A17 Pro Max 12.128 GB / 12.64 GB Memoria"
MALA = 'Mala De Viagem 20" Pequena De Bordo Material Polipropileno'
TEXTO_AIRTAG = (
    "Oi! Vimos que seu pedido ainda está aguardando pagamento. Finalizando o pagamento, "
    "você ganha 1 AirTag Uranyx de presente, enviado junto com o seu celular. 🎁"
)
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
    pedido: str = PEDIDO,
    *,
    itens: str | None = CELULAR,
    status: str = "UNPAID",
    criado: datetime = T - ATRASO - timedelta(minutes=10),
    comprador: str = "901",
) -> AtendimentoPedidoComprador:
    p = AtendimentoPedidoComprador(
        integration_id=integ.id,
        plataforma="tiktok",
        comprador_id=comprador,
        pedido=pedido,
        criado_em=criado,
        total=1200.0,
        status=status,
        itens_resumo=itens,
        atualizado_em=T - timedelta(minutes=1),
    )
    db.add(p)
    await db.commit()
    return p


class TikTokFalso:
    """A TikTok do envio: anota cada chamada. `status=None` = o pedido não veio."""

    def __init__(self, status: str | None = "UNPAID", erro: bool = False) -> None:
        self.status = status
        self.erro = erro
        self.perguntados: list[str] = []
        self.textos: list[tuple[str, str]] = []
        self.abertas: list[str] = []
        self.resposta_abrir: dict | Exception = {
            "code": 0,
            "data": {"conversation_id": "conv-nova"},
        }

    async def get_order_detail(self, oid):
        self.perguntados.append(oid)
        if self.erro:
            raise RuntimeError("tiktok fora do ar")
        return {} if self.status is None else {"id": oid, "status": self.status, "user_id": "901"}

    async def cs_send_text(self, conversa_id, texto):
        self.textos.append((conversa_id, texto))
        return {"code": 0, "data": {"message_id": f"m{len(self.textos)}"}}

    async def cs_create_conversation(self, comprador):
        self.abertas.append(comprador)
        if isinstance(self.resposta_abrir, Exception):
            raise self.resposta_abrir
        return self.resposta_abrir


@pytest.fixture
def tiktok(monkeypatch, _chaves) -> TikTokFalso:  # noqa: F811
    """As chaves de envio ligadas e o adaptador DE VERDADE, com a API falsa."""
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = True
    monkeypatch.setattr(_chaves, "atendimento_tiktok_criar_conversa", False)
    falso = TikTokFalso()

    async def _cliente(_integ):
        return falso

    monkeypatch.setattr(clientes, "cliente_da_integracao", _cliente)
    return falso


async def _em_enviar(db, nome="mini"):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, nome, "tiktok")
    await _regra(db, integ, BRINDE, modo="enviar", enviar_desde=T - timedelta(hours=1))
    return integ, canal


# ── o catálogo ──────────────────────────────────────────────────────────────


def test_catalogo_igual_ao_da_shopee_sem_o_cartao():
    a = cat.CATALOGO[BRINDE]
    s = cat.CATALOGO["shopee_nao_pago_brinde"]
    assert BRINDE in AUTOMACOES_BRINDE
    assert (a.plataforma, a.tipo, a.gatilho, a.alvo) == (
        "tiktok",
        cat.TIPO_NAO_PAGO_BRINDE,
        cat.GATILHO_NAO_PAGO,
        cat.ALVO_PEDIDO,
    )
    assert (a.atraso_min, a.janela, a.condicoes, a.validade) == (
        s.atraso_min,
        s.janela,
        s.condicoes,
        s.validade,
    )
    assert a.condicoes["um_por_comprador_h"] == 168
    assert list(a.partes) == [p for p in s.partes if p["tipo"] == "texto"]
    assert a.duoke_nao_manda and not a.campanha and not a.so_simular


@pytest.mark.parametrize(
    ("nome", "modo"),
    [
        (" Mini", "simular"),  # o espaço é de produção
        ("Barbosa", "simular"),
        ("injox", "simular"),
        ("jlas", "simular"),
        ("atv", "simular"),
        ("eron", "simular"),
        (" Poofy", "desligado"),  # sem seller.customer_service
        ("inova", "desligado"),
        ("Outra", "desligado"),
    ],
)
def test_nasce_em_simular_so_nas_lojas_conferidas(nome, modo):
    assert cat.modo_semente(cat.CATALOGO[BRINDE], nome) == modo


def test_o_texto_cabe_no_chat_do_tiktok_e_nao_tem_link():
    aut = cat.CATALOGO[BRINDE]
    for oferta in cat.BRINDE_OFERTA.values():
        partes, motivos = cat.renderizar(
            cat.partes_da_regra(aut, None, "mini"),
            comprador=None,
            plataforma="tiktok",
            canal=aut.canal,
            valores={"brinde_oferta": oferta},
            semente="pedido:1",
        )
        assert not motivos
        [texto] = [p["texto"] for p in partes]
        assert len(texto) <= 2000 and "http" not in texto and "{" not in texto


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        # Títulos reais dos anúncios do TikTok (`product_links`, 05/11/2026).
        ("Uranyx Oukitel WP60 5G 24 Gb Memoria 256 Gb", "airtag"),
        ("Uranyx Hotwav A17 Pro Max 64/128 Gb Memoria 12Gb", "airtag"),
        ('Smartphone A5 4G 128GB Tela 6.88" Dual SIM Android', "airtag"),
        ('Mala De Viagem 24" Média De Despacho Material Polipropileno P2', "chaveiro"),
        ("Kit Jogo de Malas Viagem 4 Peças Despacho 23kg Bordo 10kg + Frasqueira", "chaveiro"),
        # Os dois erros que esses títulos mostraram: o próprio AirTag e o acessório.
        ("Uranyx Tag Localizador Rastreador Bluetooth iPhone e Android", None),
        ("Uranyx Cesto De Vidro Para Air Fryers", None),
        ("Frasqueira Mala De Mão De Viagem 10 M4", None),
        ("Uranyx Fone De Ouvido UFB10 Bluetooth Tela Touch Cancelamento Ruido", None),
    ],
)
def test_brinde_nos_titulos_reais_do_tiktok(titulo, esperado):
    assert cat.brinde_do_resumo(titulo) == esperado


# ── o pedido do `search_orders` → o índice ──────────────────────────────────


def test_pedido_tiktok_sem_dado_pessoal_e_sem_a_variacao():
    linha = indice.pedido_tiktok(
        {
            "id": PEDIDO,
            "user_id": "7900",
            "status": "unpaid",
            "create_time": int(T.timestamp()),
            "payment": {"total_amount": "1299.90"},
            "buyer_email": "NAO-GRAVA",
            "recipient_address": {"name": "NAO-GRAVA"},
            "line_items": [
                {"product_name": CELULAR, "sku_name": "Laranja + Fone", "seller_sku": "dg091.sp"},
                {"product_name": CELULAR, "sku_name": "Laranja + Fone", "seller_sku": "dg091.sp"},
            ],
        }
    )
    assert linha == {
        "plataforma": "tiktok",
        "comprador_id": "7900",
        "pedido": PEDIDO,
        "criado_em": T,
        "total": 1299.9,
        "status": "UNPAID",
        "itens_resumo": f"2× {CELULAR}",
    }
    assert cat.brinde_do_resumo(linha["itens_resumo"]) == "airtag", "a variação não tira o brinde"
    assert "NAO-GRAVA" not in str(linha)


@pytest.mark.parametrize("o", [{"id": PEDIDO}, {"user_id": "1"}, {"id": PEDIDO, "user_id": "0"}])
def test_pedido_tiktok_sem_o_essencial(o):
    assert indice.pedido_tiktok(o) is None


async def test_cliente_abre_conversa_na_rota_da_doc(monkeypatch):
    chamadas = []

    async def _post(self, path, body=None, extra_params=None, *, _retried=False):
        chamadas.append((path, body))
        return {"code": 0, "data": {"conversation_id": "1"}}

    monkeypatch.setattr(TikTokClient, "_post", _post)
    cliente = TikTokClient({"app_key": "k", "app_secret": "s", "access_token": "t"})
    await cliente.cs_create_conversation("7900")
    assert chamadas == [("/customer_service/202309/conversations", {"buyer_user_id": "7900"})]


# ── a rodada própria ────────────────────────────────────────────────────────


class BuscaFalsa:
    def __init__(self, pedidos=(), status_map=None, erro=False, proxima=None) -> None:
        self.pedidos = list(pedidos)
        self.status_map = status_map or {}
        self.erro = erro
        self.proxima = proxima
        self.buscas: list[dict] = []
        self.lotes: list[list[str]] = []

    async def search_orders(self, **kw):
        self.buscas.append(kw)
        if self.erro:
            raise RuntimeError("tiktok_search_orders 1: nome do comprador")
        return {"orders": self.pedidos, "next_page_token": self.proxima}

    async def get_order_status_map(self, ids):
        self.lotes.append(list(ids))
        return {i: self.status_map[i] for i in ids if i in self.status_map}


def _ordem(oid: str, comprador: str = "901", titulo: str = CELULAR) -> dict:
    return {
        "id": oid,
        "user_id": comprador,
        "status": "UNPAID",
        "create_time": int((T - timedelta(hours=1)).timestamp()),
        "payment": {"total_amount": "1200"},
        "line_items": [{"product_name": titulo}],
    }


@pytest.fixture
def rodada_tiktok(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_tiktok_nao_pagos_ativa", True)
    r = RedisFalso()
    monkeypatch.setattr(indexar, "redis", r)
    monkeypatch.setattr(indice, "redis", r)
    monkeypatch.setattr(indexar, "_agora", lambda: T)
    return r


def _fabrica(por_loja: dict):
    async def _f(integ):
        return por_loja[integ.id]

    return _f


async def _indice_do(db, integ) -> dict[str, AtendimentoPedidoComprador]:
    linhas = (
        (
            await db.execute(
                select(AtendimentoPedidoComprador)
                .where(AtendimentoPedidoComprador.integration_id == integ.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {x.pedido: x for x in linhas}


def test_as_chaves_novas_nascem_desligadas():
    campos = type(get_settings()).model_fields
    assert campos["atendimento_tiktok_nao_pagos_ativa"].default is False
    assert campos["atendimento_tiktok_criar_conversa"].default is False


async def test_rodada_desligada_nao_chama_nada(db, rodada_tiktok, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_tiktok_nao_pagos_ativa", False)

    async def _nao(_integ):
        raise AssertionError("com a chave desligada, nenhum cliente")

    assert await indexar.indexar_tiktok_nao_pagos(fabrica_cliente=_nao) == {"ligado": False}


async def test_rodada_uma_por_vez(db, rodada_tiktok):
    await rodada_tiktok.set(indexar.CHAVE_TRAVA_TIKTOK, "outra")
    assert await indexar.indexar_tiktok_nao_pagos() == {"pulado": True}


async def test_rodada_so_nas_lojas_com_a_regra_e_o_chat_ok(db, rodada_tiktok):
    dono = await _dono(db)
    mini, _ = await _loja(db, dono, " Mini", "tiktok")
    barbosa, _ = await _loja(db, dono, "barbosa", "tiktok")  # sem regra
    desligada, _ = await _loja(db, dono, "atv", "tiktok")
    sem_chat, _ = await _loja(db, dono, "injox", "tiktok", status="sem_escopo")
    poofy, _ = await _loja(db, dono, "poofy", "tiktok")  # a regra ligada à mão, fora da lista
    await _regra(db, mini, BRINDE)
    await _regra(db, desligada, BRINDE, modo="desligado")
    await _regra(db, sem_chat, BRINDE)
    await _regra(db, poofy, BRINDE)
    falsos = {i.id: BuscaFalsa() for i in (mini, barbosa, desligada, sem_chat, poofy)}
    falsos[mini.id].pedidos = [_ordem(PEDIDO)]

    resumo = await indexar.indexar_tiktok_nao_pagos(fabrica_cliente=_fabrica(falsos))

    assert resumo == {"lojas": 1, "pedidos": 1, "erros": 0}
    busca = falsos[mini.id].buscas
    assert busca == [
        {
            "create_time_ge": int((T - timedelta(hours=4)).timestamp()),
            "create_time_lt": int(T.timestamp()),
            "page_size": 100,
            "order_status": "UNPAID",
        }
    ], "UMA busca por loja"
    for i in (barbosa, desligada, sem_chat, poofy):
        assert falsos[i.id].buscas == []
    linha = (await _indice_do(db, mini))[PEDIDO]
    assert (linha.plataforma, linha.status, linha.comprador_id, linha.itens_resumo) == (
        "tiktok",
        "UNPAID",
        "901",
        CELULAR,
    )
    assert falsos[mini.id].lotes == [], "ninguém saiu de não pago: nenhuma chamada a mais"


async def test_rodada_atualiza_quem_saiu_de_nao_pago(db, rodada_tiktok):
    dono = await _dono(db)
    mini, _ = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, mini, BRINDE)
    await _nao_pago(db, mini, "1PAGOU", criado=T - timedelta(hours=2))
    await _nao_pago(db, mini, "2CANCELOU", criado=T - timedelta(hours=25))
    await _nao_pago(db, mini, "3NAOVEIO", criado=T - timedelta(hours=2))
    await _nao_pago(db, mini, "4VELHO", criado=T - timedelta(hours=31))
    await _nao_pago(db, mini, "5SEGUE", criado=T - timedelta(hours=1))
    falso = BuscaFalsa(
        pedidos=[_ordem("5SEGUE")],
        status_map={
            "1PAGOU": {"status": "ON_HOLD"},
            "2CANCELOU": {"status": "CANCELLED"},
        },
    )

    await indexar.indexar_tiktok_nao_pagos(fabrica_cliente=_fabrica({mini.id: falso}))

    assert len(falso.lotes) == 1 and set(falso.lotes[0]) == {"1PAGOU", "2CANCELOU", "3NAOVEIO"}
    por = await _indice_do(db, mini)
    assert por["1PAGOU"].status == "ON_HOLD"
    assert por["2CANCELOU"].status == "CANCELLED"
    assert por["3NAOVEIO"].status == "UNPAID", "o que não veio fica como está"
    assert por["4VELHO"].status == "UNPAID", "fora das 30 h não gasta chamada"
    assert por["5SEGUE"].status == "UNPAID"


async def test_erro_numa_loja_nao_para_a_outra_e_nao_vaza_no_resumo(db, rodada_tiktok):
    dono = await _dono(db)
    mini, _ = await _loja(db, dono, "mini", "tiktok")
    jlas, _ = await _loja(db, dono, "jlas", "tiktok")
    await _regra(db, mini, BRINDE)
    await _regra(db, jlas, BRINDE)
    falsos = {mini.id: BuscaFalsa(erro=True), jlas.id: BuscaFalsa(pedidos=[_ordem(PEDIDO)])}
    resumo = await indexar.indexar_tiktok_nao_pagos(fabrica_cliente=_fabrica(falsos))
    assert resumo == {"lojas": 2, "pedidos": 1, "erros": 1}
    assert PEDIDO in await _indice_do(db, jlas)


# ── o motor em simular ──────────────────────────────────────────────────────


async def test_simula_as_3_h_pela_conversa_do_comprador_e_nada_sai(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, integ, BRINDE)
    conversa = await _conversa(db, integ, canal, comprador="901")
    await _nao_pago(db, integ)
    await _nao_pago(db, integ, "SEMBRINDE", itens="Uranyx Cesto De Vidro", comprador="902")
    await _conversa(db, integ, canal, comprador="902")
    await _nao_pago(db, integ, "CEDO", comprador="903", criado=T - timedelta(minutes=70))
    await _nao_pago(db, integ, "PAGO", comprador="904")
    await _bling_pago(db, integ, "PAGO", em=T - timedelta(minutes=30))
    antes = await _n_mensagens(db)

    await _rodada()

    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    a = por[PEDIDO]
    assert (a.estado, a.modo, a.motivo, a.conversa_id, a.duoke) == (
        "simulado",
        "simular",
        None,
        conversa.id,
        "nao_se_aplica",
    )
    assert a.devido_em == T - timedelta(minutes=10)
    assert (por["SEMBRINDE"].estado, por["SEMBRINDE"].motivo) == ("pulado", "sem_brinde")
    assert por["CEDO"].estado == "agendado"
    assert (por["PAGO"].estado, por["PAGO"].motivo) == ("pulado", "pedido_pago")
    assert await _n_mensagens(db) == antes
    assert proibido == [], "no modo simular, nenhuma chamada à plataforma"


async def test_simulacao_conta_o_sem_conversa_como_o_enviar_faria(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, integ, BRINDE)
    await _nao_pago(db, integ)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "sem_conversa")
    assert proibido == []


async def test_a_conversa_que_nasceu_depois_vale(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, integ, BRINDE)
    # A linha nasce antes das 3 h, sem conversa; a conversa chega antes de vencer.
    await _nao_pago(db, integ, criado=T - timedelta(minutes=170))
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "agendado" and x.conversa_id is None
    conversa = await _conversa(db, integ, canal, comprador="901")
    await _rodada(T + timedelta(minutes=11))
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.conversa_id) == ("simulado", conversa.id)


async def test_conversa_de_outro_comprador_nunca(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, integ, BRINDE)
    await _conversa(db, integ, canal, comprador="999", pedido=PEDIDO)
    await _nao_pago(db, integ, comprador="901")
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "sem_conversa")


async def test_o_cupom_da_shopee_nao_le_o_tiktok(db, proibido):  # noqa: F811
    dono = await _dono(db)
    shopee, _ = await _loja(db, dono, "barbosa")
    tiktok_loja, _ = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, shopee, "shopee_nao_pago")
    await _regra(db, shopee, "shopee_nao_pago_brinde")
    await _regra(db, tiktok_loja, BRINDE)
    await _nao_pago(db, tiktok_loja)
    await _rodada()
    assert await _linhas(db, automacao="shopee_nao_pago") == []
    assert await _linhas(db, automacao="shopee_nao_pago_brinde") == []
    assert len(await _linhas(db, automacao=BRINDE)) == 1


async def test_um_por_comprador_em_7_dias(db, proibido):  # noqa: F811
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, integ, BRINDE)
    await _conversa(db, integ, canal, comprador="901")
    await _nao_pago(db, integ, "A1", criado=T - ATRASO - timedelta(minutes=20))
    await _nao_pago(db, integ, "A2", criado=T - ATRASO - timedelta(minutes=10))
    await _rodada()
    por = {x.pedido: x for x in await _linhas(db, automacao=BRINDE)}
    assert por["A1"].estado == "simulado"
    assert (por["A2"].estado, por["A2"].motivo) == ("pulado", "ja_recebeu")


# ── o motor em enviar, com o adaptador de verdade ───────────────────────────


async def test_envia_pela_conversa_existente_depois_de_conferir_ao_vivo(db, tiktok):
    integ, canal = await _em_enviar(db)
    conversa = await _conversa(db, integ, canal, comprador="901")
    await _nao_pago(db, integ)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.modo, x.conversa_id) == ("enviado", "enviar", conversa.id)
    assert tiktok.perguntados == [PEDIDO], "UMA conferência no TikTok"
    assert tiktok.textos == [(conversa.externo_id, TEXTO_AIRTAG)], "uma vez, só o texto"
    assert tiktok.abertas == []
    # O pedido é o mesmo da chave única: outra rodada não manda de novo.
    await _rodada(T + timedelta(minutes=2))
    assert len(tiktok.textos) == 1


@pytest.mark.parametrize(
    ("status", "erro", "estado", "motivo"),
    [
        ("ON_HOLD", False, "pulado", "status_mudou"),
        ("AWAITING_SHIPMENT", False, "pulado", "status_mudou"),
        ("CANCELLED", False, "pulado", "status_mudou"),
        (None, False, "agendado", None),  # o TikTok não devolveu o pedido: não sei
        ("UNPAID", True, "agendado", None),  # erro de API: não manda
    ],
)
async def test_a_conferencia_ao_vivo_segura(db, tiktok, status, erro, estado, motivo):
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901")
    await _nao_pago(db, integ)
    tiktok.status, tiktok.erro = status, erro
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == (estado, motivo)
    assert tiktok.textos == []


async def test_pedido_de_outro_id_na_resposta_e_nao_sei(db, tiktok):
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901")
    await _nao_pago(db, integ)

    async def _outro(_oid):
        return {"id": "OUTRO", "status": "UNPAID"}

    tiktok.get_order_detail = _outro
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "agendado" and tiktok.textos == []


async def test_sem_conversa_pula_sem_nenhuma_chamada(db, tiktok):
    integ, _ = await _em_enviar(db)
    await _nao_pago(db, integ)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "sem_conversa")
    assert (tiktok.perguntados, tiktok.abertas, tiktok.textos) == ([], [], [])


async def test_conversa_bloqueada_nao_sai(db, tiktok):
    integ, canal = await _em_enviar(db)
    conversa = await _conversa(db, integ, canal, comprador="901")
    conversa.situacao = "bloqueada"
    await db.commit()
    await _nao_pago(db, integ)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "conversa_bloqueada")
    assert tiktok.textos == []


async def test_recusa_do_conteudo_nao_retenta(db, tiktok):
    integ, canal = await _em_enviar(db)
    await _conversa(db, integ, canal, comprador="901")
    await _nao_pago(db, integ)

    async def _sensivel(conversa_id, texto):
        tiktok.textos.append((conversa_id, texto))
        return {"code": 45101006, "message": "sensitive"}

    tiktok.cs_send_text = _sensivel
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "falhou" and "45101006" in (x.erro or "")
    await _rodada(T + timedelta(minutes=2))
    assert len(tiktok.textos) == 1


# ── abrir a conversa, atrás da chave ────────────────────────────────────────


async def test_com_a_chave_abre_e_manda_na_conversa_nova(db, tiktok, _chaves):  # noqa: F811
    _chaves.atendimento_tiktok_criar_conversa = True
    integ, _ = await _em_enviar(db)
    await _nao_pago(db, integ)
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "enviado"
    assert tiktok.perguntados == [PEDIDO], "confere antes de abrir"
    assert tiktok.abertas == ["901"]
    assert tiktok.textos == [("conv-nova", TEXTO_AIRTAG)]
    conversa = await db.get(AtendimentoConversa, x.conversa_id)
    assert (conversa.externo_id, conversa.comprador_id, conversa.plataforma) == (
        "conv-nova",
        "901",
        "tiktok",
    )


async def test_com_a_chave_pago_nao_abre_conversa(db, tiktok, _chaves):  # noqa: F811
    _chaves.atendimento_tiktok_criar_conversa = True
    integ, _ = await _em_enviar(db)
    await _nao_pago(db, integ)
    tiktok.status = "ON_HOLD"
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "status_mudou")
    assert tiktok.abertas == [] and tiktok.textos == []


async def test_comprador_fora_dos_criterios_pula_sem_retentar(db, tiktok, _chaves):  # noqa: F811
    _chaves.atendimento_tiktok_criar_conversa = True
    integ, _ = await _em_enviar(db)
    await _nao_pago(db, integ)
    tiktok.resposta_abrir = {"code": 45109003, "message": "not eligible"}
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert (x.estado, x.motivo) == ("pulado", "sem_conversa")
    assert "45109003" in (x.erro or "")
    await _rodada(T + timedelta(minutes=2))
    assert tiktok.abertas == ["901"] and tiktok.textos == []


async def test_abrir_sem_resposta_tenta_na_rodada_seguinte(db, tiktok, _chaves):  # noqa: F811
    _chaves.atendimento_tiktok_criar_conversa = True
    integ, _ = await _em_enviar(db)
    await _nao_pago(db, integ)
    tiktok.resposta_abrir = TimeoutError()
    await _rodada()
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "agendado" and tiktok.textos == []
    tiktok.resposta_abrir = {"code": 0, "data": {"conversation_id": "conv-nova"}}
    await _rodada(T + timedelta(minutes=2))
    [x] = await _linhas(db, automacao=BRINDE)
    assert x.estado == "enviado" and tiktok.textos == [("conv-nova", TEXTO_AIRTAG)]


# ── a trava da NF vale pelo nº do TikTok ────────────────────────────────────


async def test_a_trava_da_nf_vale_para_o_pedido_do_tiktok(db):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "mini", "tiktok")
    numero = f"BR{uuid4().hex[:8]}"
    db.add(
        BlingOrder(
            bling_id=991,
            item_index=0,
            numero=numero,
            numeroloja=PEDIDO,
            situacao="6",
            item_codigo="dg091.sp",
            item_descricao="Uranyx",
            item_quantidade=1,
            itemvalor=909,
            data=datetime.now(UTC),
            created_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao=BRINDE,
            integration_id=integ.id,
            plataforma="tiktok",
            alvo="pedido",
            chave=f"pedido:{PEDIDO}",
            pedido=PEDIDO,
            devido_em=datetime.now(UTC),
            estado="enviado",
        )
    )
    await db.commit()
    assert await brinde.segurados_pela_oferta(db, [numero]) == {numero}
