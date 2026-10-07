"""Painel de Garantia Uranyx — as regras puras (services/garantia.py).

RN02–RN04 (prazos com o último dia do mês), status por data, ponto 1 (último
dia inclusive), RN06 (CPF com dígito verificador), máscara do CPF (§6), a
data de entrega pelas fontes da Logística (RN01) e a cobertura (§5.2).
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.models import Garantia, Logistica
from app.routers.sites_estoque import ESCOPOS
from app.services import garantia as svc


def _d(texto: str) -> date:
    dia, mes, ano = texto.split("/")
    return date(int(ano), int(mes), int(dia))


# ── Prazos (RN02, RN03, RN04) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entrega", "fim_hw", "fim_sw"),
    [
        # A tabela "Exemplo de cálculo" do documento.
        ("07/10/2026", "07/01/2027", "07/10/2027"),
        ("30/11/2026", "28/02/2027", "30/11/2027"),
        # RN04 em outros meses curtos, e o bissexto.
        ("31/10/2026", "31/01/2027", "31/10/2027"),
        ("30/11/2027", "29/02/2028", "30/11/2028"),
        ("31/05/2026", "31/08/2026", "31/05/2027"),
        ("31/03/2027", "30/06/2027", "31/03/2028"),
        ("29/02/2028", "29/05/2028", "28/02/2029"),
    ],
)
def test_prazos_hardware_e_software(entrega, fim_hw, fim_sw):
    assert svc.calcular_prazos(_d(entrega)) == (_d(fim_hw), _d(fim_sw))


def test_meses_dos_prazos_sao_os_do_documento():
    assert (svc.MESES_HARDWARE, svc.MESES_SOFTWARE) == (3, 12)


# ── Status (calculado pela data de hoje) ─────────────────────────────────────


def test_status_por_data_com_ultimo_dia_inclusive():
    inicio = _d("07/10/2026")
    hw, sw = svc.calcular_prazos(inicio)
    assert svc.status_em(None, None, None, _d("07/10/2026")) == "aguardando_entrega"
    assert svc.status_em(inicio, hw, sw, inicio) == "ativa"
    assert svc.status_em(inicio, hw, sw, hw) == "ativa"  # ponto 1: vale até o fim
    assert svc.status_em(inicio, hw, sw, hw + timedelta(days=1)) == "somente_software"
    assert svc.status_em(inicio, hw, sw, sw) == "somente_software"
    assert svc.status_em(inicio, hw, sw, sw + timedelta(days=1)) == "expirada"


def test_ponto_1_trocado_termina_no_dia_anterior(monkeypatch):
    monkeypatch.setattr(svc, "ULTIMO_DIA_COBERTO", False)
    inicio = _d("07/10/2026")
    hw, sw = svc.calcular_prazos(inicio)
    assert svc.status_em(inicio, hw, sw, hw - timedelta(days=1)) == "ativa"
    assert svc.status_em(inicio, hw, sw, hw) == "somente_software"
    assert svc.status_em(inicio, hw, sw, sw) == "expirada"


def test_pontos_definidos_pelo_dono():
    """§8, decididos em 07/10/2026 — mudar aqui é mudar a regra."""
    assert svc.ULTIMO_DIA_COBERTO is True
    assert svc.CADASTRO_ANTES_DA_ENTREGA is True
    assert svc.GARANTIA_POR == "nf"
    assert svc.VINCULO_AUTOMATICO is False
    assert svc.RECALCULAR_QUANDO_ENTREGA_MUDAR is True


def test_prazo_info_da_barra_de_dias():
    inicio = _d("07/10/2026")
    hw, _sw = svc.calcular_prazos(inicio)
    info = svc.prazo_info(inicio, hw, _d("17/10/2026"))
    assert info == {"fim": hw, "dias_total": 92, "dias_restantes": 82, "coberto_hoje": True}
    vencido = svc.prazo_info(inicio, hw, _d("10/01/2027"))
    assert vencido["dias_restantes"] == 0 and vencido["coberto_hoje"] is False
    assert svc.prazo_info(None, None, _d("10/01/2027")) is None


# ── CPF (RN06, §6) ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("cpf", ["52998224725", "529.982.247-25", "111.444.777-35", "12345678909"])
def test_cpf_valido(cpf):
    assert svc.cpf_valido(cpf)


@pytest.mark.parametrize(
    "cpf",
    ["52998224724", "529.982.247-2", "11111111111", "00000000000", "", None, "5299822472a5"],
)
def test_cpf_invalido(cpf):
    assert not svc.cpf_valido(cpf)


def test_mascara_e_formato_do_cpf():
    assert svc.mascarar_cpf("52998224725") == "***.982.247-**"
    assert svc.mascarar_cpf("529.982.247-25") == "***.982.247-**"
    assert svc.mascarar_cpf("123") is None
    assert svc.formatar_cpf("52998224725") == "529.982.247-25"


def test_numero_da_nf_normalizado():
    assert svc.normalizar_nf("000.010.234") == "10234"
    assert svc.normalizar_nf(" 1234 ") == "1234"
    assert svc.normalizar_nf("0000") == ""
    assert svc.normalizar_serie("001") == "1"
    assert svc.normalizar_serie(None) == ""


# ── Produto Uranyx ───────────────────────────────────────────────────────────


def test_recorte_uranyx_e_o_mesmo_dos_sites():
    assert svc.SKU_URANYX == ESCOPOS["uranyx"]
    assert svc.e_uranyx("dg053")
    assert svc.e_uranyx("DG120.pi+capa")
    assert svc.e_uranyx("uaf01")
    assert svc.e_uranyx("a012")
    assert not svc.e_uranyx("zdg053")  # salvado de devolução
    assert not svc.e_uranyx("i15pro")
    assert not svc.e_uranyx(None)


# ── Data de entrega (RN01) ───────────────────────────────────────────────────


def _lg(status: dict, datas: dict, entregue_em=None) -> Logistica:
    return Logistica(meli_status=status, status_datas=datas, entregue_em=entregue_em)


def test_entrega_pelas_fontes_da_logistica():
    em = "2026-10-08T01:30:00+00:00"  # 07/10 22:30 em São Paulo
    ml = svc.entrega_da_logistica(
        _lg({"ship_status": "delivered"}, {"ship_status": {"em": em, "fonte": "plataforma"}})
    )
    assert (ml.origem, ml.dia) == ("ml", _d("07/10/2026"))
    shopee = svc.entrega_da_logistica(
        _lg(
            {"logistics_status": "LOGISTICS_DELIVERY_DONE"},
            {"logistics_status": {"em": em, "fonte": "plataforma"}},
        )
    )
    assert shopee.origem == "shopee"
    tiktok = svc.entrega_da_logistica(
        _lg({"order_status": "DELIVERED"}, {"order_status": {"em": em, "fonte": "plataforma"}})
    )
    assert tiktok.origem == "tiktok"
    amazon = svc.entrega_da_logistica(
        _lg({"easyship_status": "Delivered"}, {"easyship_status": {"em": em, "fonte": "aprox"}})
    )
    assert amazon.origem == "amazon"


def test_entrega_recusa_o_que_nao_e_entrega():
    em = "2026-10-08T01:30:00+00:00"
    # COMPLETED do TikTok é a conclusão (~35 dias depois do despacho).
    assert (
        svc.entrega_da_logistica(
            _lg({"order_status": "COMPLETED"}, {"order_status": {"em": em, "fonte": "plataforma"}})
        )
        is None
    )
    # Carimbo "davinci" (quando o DaVinci viu) não é a data da plataforma.
    assert (
        svc.entrega_da_logistica(
            _lg(
                {"logistics_status": "LOGISTICS_DELIVERY_DONE"},
                {"logistics_status": {"em": em, "fonte": "davinci"}},
            )
        )
        is None
    )
    assert svc.entrega_da_logistica(_lg({"ship_status": "shipped"}, {})) is None
    assert svc.entrega_da_logistica(None) is None


def test_entrega_cai_no_rastreio_visto_pelo_davinci():
    visto = datetime(2026, 10, 3, 14, tzinfo=UTC)
    e = svc.entrega_da_logistica(_lg({"ship_status": "delivered"}, {}, entregue_em=visto))
    assert (e.origem, e.dia) == ("rastreio", _d("03/10/2026"))
    # Com a data oficial, a oficial vence.
    oficial = svc.entrega_da_logistica(
        _lg(
            {"ship_status": "delivered"},
            {"ship_status": {"em": "2026-10-02T12:00:00+00:00", "fonte": "plataforma"}},
            entregue_em=visto,
        )
    )
    assert (oficial.origem, oficial.dia) == ("ml", _d("02/10/2026"))


def test_toda_origem_tem_rotulo():
    for fonte in svc.FONTES_ENTREGA:
        assert svc.ORIGEM_ROTULOS[fonte.chave]
    assert svc.ORIGEM_ROTULOS[svc.FONTE_RASTREIO]
    # O rastreio é o instante em que o DaVinci VIU a entrega: o rótulo (que
    # vai para a tela e para o log do recálculo) diz que é aproximada.
    assert "aproximada" in svc.ORIGEM_ROTULOS[svc.FONTE_RASTREIO]


def test_entregue_no_bling_sem_data():
    assert svc.entregue_sem_data(None, "83953") is True  # Entregue
    assert svc.entregue_sem_data(None, "545902") is True  # Resolvido
    assert svc.entregue_sem_data(None, "15") is False  # Em andamento
    assert svc.entregue_sem_data(None, None) is False
    assert svc.entregue_sem_data(_d("02/10/2026"), "83953") is False  # tem data
    assert svc.rotulo_do_status("aguardando_entrega", True) == "Entregue — sem data no DaVinci"
    assert svc.rotulo_do_status("aguardando_entrega") == "Aguardando entrega"
    assert svc.rotulo_do_status("ativa", True) == "Ativa"
    pedido = svc.PedidoInfo("1", None, None, None, None, None, None, None, situacao_id="83953")
    assert pedido.aviso_sem_entrega == "entregue_sem_data"
    pedido.situacao_id = "15"
    assert pedido.aviso_sem_entrega == "aguardando_entrega"


# ── Cobertura do atendimento (§5.2) ──────────────────────────────────────────


def test_cobertura_pelo_tipo_informado():
    g = Garantia(data_inicio=_d("07/10/2026"))
    g.fim_hardware, g.fim_software = svc.calcular_prazos(g.data_inicio)
    no_fim_hw = datetime(2027, 1, 7, 23, 0, tzinfo=svc.SAO_PAULO)
    depois_hw = datetime(2027, 1, 8, 0, 30, tzinfo=svc.SAO_PAULO)
    assert svc.cobertura_em(g, "hardware", no_fim_hw) == ("coberto", _d("07/01/2027"))
    assert svc.cobertura_em(g, "hardware", depois_hw) == ("fora_da_garantia", _d("07/01/2027"))
    assert svc.cobertura_em(g, "software", depois_hw) == ("coberto", _d("07/10/2027"))
    # O dia é o de São Paulo: 02:59 UTC do dia 08 ainda é dia 07 aqui.
    assert svc.cobertura_em(g, "hardware", datetime(2027, 1, 8, 2, 59, tzinfo=UTC))[0] == "coberto"
    sem = Garantia(data_inicio=None)
    assert svc.cobertura_em(sem, "hardware", depois_hw) == ("sem_data_de_entrega", None)


# ── Pedido: sugestões de CPF/nome e papel da NF ──────────────────────────────


def _nota(papel: str, doc: str | None, nome: str | None = None) -> svc.NotaDoPedido:
    return svc.NotaDoPedido(
        numero="1234",
        serie="1",
        chave="1" * 44,
        emitente_cnpj=None,
        destinatario_doc=doc,
        destinatario_nome=nome,
        emitida_em=None,
        valor=None,
        papel=papel,
    )


def test_cpf_e_nome_do_pedido_pela_ordem_das_fontes():
    p = svc.PedidoInfo(
        numero="1",
        numeroloja=None,
        loja=None,
        plataforma=None,
        conta=None,
        data=None,
        situacao=None,
        total=None,
        documento="11144477735",
        nome_destinatario="Recebedor  da Entrega",
        cpf_nota_emitida="12345678909",
    )
    assert p.cpf == "11144477735"
    assert p.nome == ("Recebedor da Entrega", "destinatario_entrega")
    p.notas = [_nota("embalagem", "99999999999"), _nota("produto", "52998224725", "Comprador")]
    assert p.cpf == "52998224725"
    assert p.nome == ("Comprador", "nf")
    p.notas, p.documento = [], ""
    assert p.cpf == "12345678909"
    p.cpf_nota_emitida = "12345678900"  # dígito errado: não sugere
    assert p.cpf is None


def test_papel_da_nota_pela_fracao_do_pedido():
    assert svc._papel_da_nota(1000, 1000) == "produto"
    assert svc._papel_da_nota(700, 1000) == "produto"
    assert svc._papel_da_nota(3, 1000) == "embalagem"
    assert svc._papel_da_nota(450, 1000) == "indefinida"
    assert svc._papel_da_nota(None, 1000) == "indefinida"


# ── Anexos ───────────────────────────────────────────────────────────────────


def test_host_do_anexo():
    assert svc.host_permitido("https://img.sp.mms.shopee.sg/abc")
    assert svc.host_permitido("https://down-tx-br.vod.susercontent.com/v.mp4")
    assert svc.host_permitido("https://p16-oec-sg.ibyteimg.com/x.jpg")
    assert svc.host_permitido("https://v16m-default.tiktokcdn.com/x.mp4")
    assert not svc.host_permitido("http://img.sp.mms.shopee.sg/abc")  # só https
    assert not svc.host_permitido("https://shopee.sg.exemplo.com/abc")
    assert not svc.host_permitido("https://169.254.169.254/latest")
    assert not svc.host_permitido("https://localhost/x")


def test_mes_atual_em_sao_paulo():
    ini, fim = svc.mes_atual(_d("31/12/2026"))
    assert ini == datetime(2026, 12, 1, tzinfo=svc.SAO_PAULO)
    assert fim == datetime(2027, 1, 1, tzinfo=svc.SAO_PAULO)


# ── Data do atendimento: o trecho atual da conversa ─────────────────────────


def _m(autor: str, quando: str):
    em = datetime.fromisoformat(quando).replace(tzinfo=UTC)
    return SimpleNamespace(autor=autor, enviada_em=em, created_at=em)


def test_inicio_do_trecho_atual_da_conversa():
    assert svc.DIAS_PAUSA_NOVO_ATENDIMENTO == 7
    # Reclamação em 05/10, vídeo em 08/10: o trecho começa em 05/10.
    msgs = [
        _m("cliente", "2026-07-07T12:00"),
        _m("loja", "2026-07-07T13:00"),
        _m("cliente", "2026-10-05T12:00"),
        _m("loja", "2026-10-05T13:00"),
        _m("cliente", "2026-10-08T12:00"),
    ]
    assert svc.inicio_do_trecho(msgs) == datetime(2026, 10, 5, 12, tzinfo=UTC)
    # A loja respondendo no meio mantém o trecho vivo (a pausa é entre
    # mensagens, não só as do cliente).
    vivo = [
        _m("cliente", "2026-09-01T12:00"),
        _m("loja", "2026-09-06T12:00"),
        _m("loja", "2026-09-12T12:00"),
        _m("cliente", "2026-09-18T12:00"),
    ]
    assert svc.inicio_do_trecho(vivo) == datetime(2026, 9, 1, 12, tzinfo=UTC)
    # Exatamente 7 dias ainda é o mesmo trecho; 7 dias e 1 minuto, não.
    assert svc.inicio_do_trecho(
        [_m("cliente", "2026-09-01T12:00"), _m("cliente", "2026-09-08T12:00")]
    ) == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert svc.inicio_do_trecho(
        [_m("cliente", "2026-09-01T12:00"), _m("cliente", "2026-09-08T12:01")]
    ) == datetime(2026, 9, 8, 12, 1, tzinfo=UTC)
    # A loja falando depois do cliente não muda nada; sem cliente: None.
    assert svc.inicio_do_trecho(
        [_m("cliente", "2026-09-01T12:00"), _m("loja", "2026-12-01T12:00")]
    ) == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert svc.inicio_do_trecho([_m("loja", "2026-09-01T12:00")]) is None
    assert svc.inicio_do_trecho([]) is None


def test_data_do_atendimento_sem_mensagem_do_cliente():
    agora = datetime(2026, 10, 7, 15, tzinfo=UTC)
    conversa = SimpleNamespace(
        ultima_do_cliente_em=datetime(2026, 10, 1, 12), ultima_mensagem_em=None
    )
    loja = [_m("loja", "2026-10-02T12:00")]
    assert svc.data_do_atendimento(conversa, loja, False, agora) == datetime(
        2026, 10, 1, 12, tzinfo=UTC
    )
    vazia = SimpleNamespace(ultima_do_cliente_em=None, ultima_mensagem_em=None)
    assert svc.data_do_atendimento(vazia, [], False, agora) == agora
    # Escolhidas: a 1ª do cliente entre elas (não o trecho).
    msgs = [_m("cliente", "2026-07-07T12:00"), _m("cliente", "2026-10-05T12:00")]
    assert svc.data_do_atendimento(vazia, msgs, True, agora) == datetime(2026, 7, 7, 12, tzinfo=UTC)


# ── Busca pelo CPF completo ─────────────────────────────────────────────────


def test_cpf_da_busca_so_com_os_11_digitos_validos():
    assert svc.cpf_da_busca("529.982.247-25") == "52998224725"
    assert svc.cpf_da_busca(" 52998224725 ") == "52998224725"
    assert svc.cpf_da_busca("52998224724") is None  # dígito errado (pode ser pedido)
    assert svc.cpf_da_busca("529.982.247") is None  # pedaço não busca
    assert svc.cpf_da_busca("joao 52998224725") is None
    assert svc.cpf_da_busca(None) is None
    assert all(lim > 0 and jan > 0 for lim, jan in svc.LIMITES_BUSCA_CPF)


# ── Anexo: SVG fora e redirect conferido salto a salto ──────────────────────


def test_tipos_de_anexo_aceitos():
    for tipo in ("image/jpeg", "image/png", "image/webp", "video/mp4", "application/pdf"):
        assert svc.tipo_de_anexo_aceito(tipo), tipo
    for tipo in ("image/svg+xml", "text/html", "application/xhtml+xml", "image/svg", ""):
        assert not svc.tipo_de_anexo_aceito(tipo), tipo


@pytest.mark.asyncio
async def test_baixar_anexo_recusa_svg_e_confere_cada_redirect():
    visitados: list[str] = []
    cdn = "https://img.sp.mms.shopee.sg"

    def responder(req: httpx.Request) -> httpx.Response:
        visitados.append(str(req.url))
        rotas = {
            "/foto": httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpg"),
            "/svg": httpx.Response(
                200, headers={"content-type": "image/svg+xml"}, content=b"<svg><script/></svg>"
            ),
            "/pra-dentro": httpx.Response(302, headers={"location": "https://redis7:6379/x"}),
            "/pro-cdn": httpx.Response(
                302, headers={"location": "https://down-br.img.susercontent.com/foto"}
            ),
            "/relativo": httpx.Response(301, headers={"location": "/foto"}),
            "/sem-fim": httpx.Response(302, headers={"location": "/sem-fim"}),
        }
        return rotas.get(req.url.path, httpx.Response(404))

    t = httpx.MockTransport(responder)
    assert await svc.baixar_anexo(f"{cdn}/foto", transport=t) == (b"jpg", "image/jpeg")
    with pytest.raises(svc.AnexoRecusado, match="tipo_nao_suportado"):
        await svc.baixar_anexo(f"{cdn}/svg", transport=t)
    visitados.clear()
    with pytest.raises(svc.AnexoRecusado, match="host_nao_permitido"):
        await svc.baixar_anexo(f"{cdn}/pra-dentro", transport=t)
    assert visitados == [f"{cdn}/pra-dentro"]  # o host interno nunca foi pedido
    assert await svc.baixar_anexo(f"{cdn}/pro-cdn", transport=t) == (b"jpg", "image/jpeg")
    assert await svc.baixar_anexo(f"{cdn}/relativo", transport=t) == (b"jpg", "image/jpeg")
    visitados.clear()
    with pytest.raises(svc.AnexoRecusado, match="erro_ao_baixar"):
        await svc.baixar_anexo(f"{cdn}/sem-fim", transport=t)
    assert len(visitados) == svc.ANEXO_MAX_REDIRECTS + 1
    with pytest.raises(svc.AnexoRecusado, match="link_expirado"):
        await svc.baixar_anexo(f"{cdn}/sumiu", transport=t)
