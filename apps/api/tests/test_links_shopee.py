"""O "Abrir na Shopee" (`services/links_shopee.py`, 02/10/2026).

O que estes testes seguram:

- a página do pedido só com o order_id INTERNO (só dígitos); sem ele, a
  lista de pedidos buscando o order_sn — NUNCA `/portal/sale/order/<order_sn>`
  (o caso da Vortan, 26092743U4QU7F);
- devolução: `/portal/sale/return/<return_id>` ou a busca pelo return_sn;
- o order_id é a hora em que o pedido nasceu (ms desde 01/01/2019 UTC × 1000)
  e o order_sn começa com o AAMMDD dela em UTC+8 — os pares REAIS de
  produção batem;
- a leitura em lote só aceita o par SEGURO: 1 order_sn e 1 order_id na
  conversa, data exata e hora a até 5 s da criação gravada; conversa com 2
  pedidos, data/hora que não batem, sem hora conhecida e outra integração
  caem na busca;
- return_id: só da conversa da própria reclamação, 1 return_id, o pedido do
  cartão conferido e a data do return_id = a do return_sn;
- uma consulta para a lista inteira (sem N+1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import event

from app.models import (
    AtendimentoAvaliacaoLoja,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    Integration,
    IntegrationPlatform,
)
from app.security.cipher import encrypt_json
from app.services import links_shopee as ls
from app.services.atendimento import avaliacoes, reclamacoes

BASE = "https://seller.shopee.com.br"
# O caso real (Vortan, 02/10/2026): o dono abriu /portal/sale/order/244141571124463.
SN = "26092743U4QU7F"
OID = "244141571124463"
CRIADO = "2026-09-27T01:06:11+08:00"  # = a hora do OID (17:06:11 UTC de 26/09)
EPOCA = datetime(2019, 1, 1, tzinfo=UTC)


def _id_de(quando: datetime, seq: int = 123) -> str:
    """O id interno que a Shopee daria a algo criado em `quando`."""
    return str(int((quando - EPOCA) / timedelta(milliseconds=1)) * 1000 + seq)


# ─────────────── os endereços ───────────────


def test_url_do_pedido():
    assert ls.url_pedido_shopee(SN, OID) == f"{BASE}/portal/sale/order/{OID}"
    assert ls.url_pedido_shopee(SN, int(OID)) == f"{BASE}/portal/sale/order/{OID}"
    # Sem o número interno: a lista de pedidos buscando o order_sn.
    assert ls.url_pedido_shopee(SN) == f"{BASE}/portal/sale/order?search={SN}"
    for ruim in ("12a", "123", "0000000000", True, "", None, " "):
        assert ls.url_pedido_shopee(SN, ruim) == f"{BASE}/portal/sale/order?search={SN}", ruim
    # O order_sn nunca entra no caminho da página do pedido.
    assert f"/portal/sale/order/{SN}" not in (ls.url_pedido_shopee(SN) or "")
    # Nada que mexa na URL.
    for ruim in ("2609?x=1", "26 09", "../x", "", None, "a" * 65):
        assert ls.url_pedido_shopee(ruim) is None, ruim
    assert ls.url_pedido_shopee(None, OID) == f"{BASE}/portal/sale/order/{OID}"


def test_url_da_devolucao():
    rid = "244300000000123"
    assert ls.url_devolucao_shopee("2609280ABCDEFGH", rid) == f"{BASE}/portal/sale/return/{rid}"
    assert ls.url_devolucao_shopee("2609280ABCDEFGH") == (
        f"{BASE}/portal/sale/returnrefundcancel?keyword=2609280ABCDEFGH&keywordType=return_sn"
    )
    assert ls.url_devolucao_shopee("RSN?x", "12") is None
    assert ls.url_devolucao_shopee(None) is None


def test_o_id_interno_e_a_hora_da_criacao():
    assert ls.criado_em_do_id(OID) == datetime(2026, 9, 26, 17, 6, 11, 124000, tzinfo=UTC)
    assert ls.criado_em_do_id("12a") is None
    assert ls.criado_em_do_id("9" * 20) is None  # fora do calendário: não levanta
    assert ls.data_do_sn(SN) == date(2026, 9, 27)
    assert ls.data_do_sn("261399ABCDEFGH") is None  # mês 13
    assert ls.data_do_sn("ABCDEFGH") is None
    assert ls.data_do_sn("260927") is None  # só a data não é order_sn
    # Pares REAIS de produção (lojas diferentes, os 4 tipos de cartão): a data
    # em UTC+8 do order_id é sempre o AAMMDD do order_sn.
    for sn, oid in (
        (SN, OID),
        ("261001EUV60BRF", "244510973193999"),
        ("260921J3WC0GEG", "243622682194384"),
        ("260924T2NJW80A", "243896225157130"),
        ("260924TAR3YWEB", "243904900113867"),
        ("260919EFT356K0", "243498018196064"),
        ("261001F9RBJG46", "244525877198982"),
    ):
        assert ls.mesma_data(sn, oid), sn
    # Par errado real: a conversa do 260925… tinha o cartão de um pedido de 14/09.
    assert not ls.mesma_data("260925V5P3BYXD", "243024924177313")


def test_par_coerente_exige_data_e_hora():
    assert ls.par_coerente(SN, OID, [CRIADO])
    assert ls.par_coerente(SN, OID, [datetime(2026, 9, 26, 17, 6, 14, tzinfo=UTC)])  # 3 s
    # Outro pedido do comprador criado 29 s depois: não é este.
    assert not ls.par_coerente(SN, OID, [datetime(2026, 9, 26, 17, 6, 40, tzinfo=UTC)])
    assert ls.par_coerente(SN, OID, [CRIADO, None])
    # Sem hora conhecida, não aceita (a data sozinha não basta).
    assert not ls.par_coerente(SN, OID, [])
    assert not ls.par_coerente(SN, OID, [None, "", "lixo", "2026-09-27T01:06:11"])  # sem fuso
    # Mesmo dia, outro pedido do comprador (28 min depois): não é este.
    assert not ls.par_coerente(SN, OID, ["2026-09-27T01:34:00+08:00"])
    # Todas as horas conhecidas têm de bater.
    assert not ls.par_coerente(SN, OID, [CRIADO, "2026-09-27T09:00:00+08:00"])
    # Hora certa, mas o order_sn é de outro dia.
    assert not ls.par_coerente("26092643U4QU7F", OID, [CRIADO])


# ─────────────── a leitura em lote ───────────────


async def _integracao(db, dono, nome="Vortan"):
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    return integ


async def _conversa(db, integ, *, pedido=SN, criado=CRIADO, mensagens=(), canal="chat"):
    dados = {"pedido_mkt": {"pedido": pedido, "criado_em": criado}} if criado else {}
    c = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="shopee",
        canal=canal,
        externo_id=f"conv-{uuid4().hex[:8]}",
        pedido_marketplace=pedido,
        dados=dados,
    )
    db.add(c)
    await db.flush()
    for i, payload in enumerate(
        [{"message_type": "order", "content": {"order_sn": pedido}}, *mensagens]
    ):
        db.add(
            AtendimentoMensagem(
                conversa_id=c.id,
                externo_id=f"m{i}",
                autor="loja" if payload.get("message_type") != "text" else "cliente",
                origem="externo",
                tipo="outro",
                payload=payload,
            )
        )
    await db.flush()
    return c


def _crm(oid):
    return {
        "message_type": "crm_order_rate",
        "content": {"unrated_order_reminder": {"order_id": int(oid)}},
    }


def _logistica(oid):
    return {"message_type": "logistics_card", "content": {"common_info": {"order_id": int(oid)}}}


def _rr(oid, rid, tipo="track_rr_status_card"):
    if tipo == "return_refund_card":
        conteudo = {
            "order_info": {"order_id": int(oid)},
            "tracking_info": {"order_id": 0},
            "rr_detail": {"return_id": int(rid), "returnId": int(rid)},
        }
    else:
        conteudo = {
            "order_detail": {"order_id": int(oid)},
            "tracking_info": {"order_id": int(oid)},
            "rr_detail": {"return_id": int(rid), "returnId": int(rid)},
        }
    return {"message_type": tipo, "content": conteudo}


async def test_par_seguro_aceito(db, make_user):
    integ = await _integracao(db, await make_user())
    await _conversa(db, integ, mensagens=[_crm(OID), _logistica(OID)])
    await db.commit()
    ids = await ls.ids_shopee(db, pedidos=[(integ.id, SN)])
    assert ids.pedidos == {(integ.id, SN): OID}
    assert ids.pedido(integ.id, SN) == OID
    assert ids.pedido(None, SN) is None and ids.pedido(integ.id, "OUTRO") is None


async def test_hora_pelo_indice_de_pedidos_do_comprador(db, make_user):
    """Sem o retrato do pedido na conversa, a hora vem de `atendimento_pedidos_comprador`."""
    integ = await _integracao(db, await make_user())
    await _conversa(db, integ, criado=None, mensagens=[_crm(OID)])
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(integ.id, SN)])).pedidos == {}, "sem hora: busca"
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id,
            plataforma="shopee",
            comprador_id="77",
            pedido=SN,
            criado_em=datetime(2026, 9, 26, 17, 6, 11, tzinfo=UTC),
        )
    )
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(integ.id, SN)])).pedidos == {(integ.id, SN): OID}


async def test_conversa_com_dois_pedidos_e_recusada(db, make_user):
    integ = await _integracao(db, await make_user())
    # O comprador veio de outro pedido (source_content) na mesma conversa.
    await _conversa(
        db,
        integ,
        mensagens=[
            _crm(OID),
            {"message_type": "text", "source_content": {"order_sn": "260925V5P3BYXD"}},
        ],
    )
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(integ.id, SN)])).pedidos == {}


async def test_dois_order_ids_na_conversa_e_recusada(db, make_user):
    integ = await _integracao(db, await make_user())
    await _conversa(db, integ, mensagens=[_crm(OID), _logistica("244141571999999")])
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(integ.id, SN)])).pedidos == {}


async def test_data_ou_hora_incoerente_e_recusada(db, make_user):
    integ = await _integracao(db, await make_user())
    # Par errado real: conversa do 260925… com o cartão de um pedido de 14/09.
    sn_errado = "260925V5P3BYXD"
    await _conversa(
        db,
        integ,
        pedido=sn_errado,
        criado="2026-09-25T10:00:00+08:00",
        mensagens=[_crm("243024924177313")],
    )
    # Mesmo dia, outro pedido do comprador (28 min antes do criado gravado).
    outro_dia = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    sn_mesmo_dia = "260927XXXXXXXX"
    await _conversa(
        db,
        integ,
        pedido=sn_mesmo_dia,
        criado=(outro_dia + timedelta(minutes=28)).isoformat(),
        mensagens=[_logistica(_id_de(outro_dia))],
    )
    # 1 dia antes, na véspera às 21:06 UTC+8 (o caso real que o ±3 dias aceitaria).
    sn_vespera = "260922YYYYYYYY"
    vespera = datetime(2026, 9, 21, 13, 6, 5, tzinfo=UTC)
    await _conversa(
        db,
        integ,
        pedido=sn_vespera,
        criado="2026-09-22T10:00:00+08:00",
        mensagens=[_logistica(_id_de(vespera))],
    )
    await db.commit()
    ids = await ls.ids_shopee(
        db, pedidos=[(integ.id, sn_errado), (integ.id, sn_mesmo_dia), (integ.id, sn_vespera)]
    )
    assert ids.pedidos == {}


async def test_outra_integracao_nao_vaza(db, make_user):
    dono = await make_user()
    a = await _integracao(db, dono, "Loja A")
    b = await _integracao(db, dono, "Loja B")
    await _conversa(db, b, mensagens=[_crm(OID)])
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(a.id, SN)])).pedidos == {}
    assert (await ls.ids_shopee(db, pedidos=[(b.id, SN)])).pedidos == {(b.id, SN): OID}


async def test_conversas_do_mesmo_pedido_que_concordam(db, make_user):
    integ = await _integracao(db, await make_user())
    await _conversa(db, integ, mensagens=[_crm(OID)])
    await _conversa(db, integ, mensagens=[_logistica(OID)])
    # Uma terceira, ambígua (2 pedidos), não atrapalha nem conta.
    await _conversa(
        db,
        integ,
        mensagens=[_crm(OID), {"message_type": "order", "content": {"order_sn": "X1"}}],
    )
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(integ.id, SN)])).pedidos == {(integ.id, SN): OID}


async def test_conversas_que_discordam_caem_na_busca(db, make_user):
    """Duas conversas do pedido, cada uma com um order_id coerente (o mesmo
    milissegundo, sequência diferente): não dá para escolher — busca."""
    integ = await _integracao(db, await make_user())
    await _conversa(db, integ, mensagens=[_crm(OID)])
    await _conversa(db, integ, mensagens=[_logistica(str(int(OID) + 1))])
    await db.commit()
    assert (await ls.ids_shopee(db, pedidos=[(integ.id, SN)])).pedidos == {}


async def _reclamacao(db, integ, conversa, return_sn, *, pedido=SN):
    r = AtendimentoReclamacao(
        integration_id=integ.id,
        conversa_id=conversa.id if conversa else None,
        plataforma="shopee",
        externo_id=return_sn or f"{reclamacoes.PREFIXO_SEM_ID}{pedido}",
        tipo="devolucao",
        status="REQUESTED",
        pedido_marketplace=pedido,
        dados={"return_id": return_sn},
    )
    db.add(r)
    await db.flush()
    return r


async def test_return_id_da_conversa_da_reclamacao(db, make_user):
    integ = await _integracao(db, await make_user())
    criada = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)  # 28/09 10:00 em UTC+8
    rid = _id_de(criada)
    conversa = await _conversa(db, integ, mensagens=[_rr(OID, rid)])
    r = await _reclamacao(db, integ, conversa, "2609280ABCDEFGH")
    await db.commit()

    ids = await reclamacoes.ids_shopee_das(db, [r])
    assert ids.devolucoes == {(conversa.id, "2609280ABCDEFGH"): rid}
    assert ids.pedidos == {(integ.id, SN): OID}
    assert reclamacoes.para_tela(r, ids)["url_plataforma"] == f"{BASE}/portal/sale/return/{rid}"
    # Sem a leitura: a lista de devoluções buscando o return_sn.
    assert reclamacoes.url_na_plataforma(r) == (
        f"{BASE}/portal/sale/returnrefundcancel?keyword=2609280ABCDEFGH&keywordType=return_sn"
    )
    # Sem return_sn: o pedido (com o order_id interno conferido).
    sem_rsn = await _reclamacao(db, integ, conversa, None)
    ids = await reclamacoes.ids_shopee_das(db, [sem_rsn])
    assert reclamacoes.url_na_plataforma(sem_rsn, ids) == f"{BASE}/portal/sale/order/{OID}"
    assert reclamacoes.url_na_plataforma(sem_rsn) == f"{BASE}/portal/sale/order?search={SN}"


async def test_return_id_de_outra_devolucao_e_recusado(db, make_user):
    """O pedido teve 2 devoluções: o cartão da conversa é da outra (data do
    return_id ≠ data do return_sn) — caso real de produção."""
    integ = await _integracao(db, await make_user())
    rid_outra = _id_de(datetime(2026, 9, 29, 2, 0, tzinfo=UTC))  # 29/09
    conversa = await _conversa(db, integ, mensagens=[_rr(OID, rid_outra, "return_refund_card")])
    r = await _reclamacao(db, integ, conversa, "2610020ABCDEFGH")  # 02/10
    await db.commit()
    ids = await reclamacoes.ids_shopee_das(db, [r])
    assert ids.devolucoes == {}
    assert reclamacoes.url_na_plataforma(r, ids).startswith(
        f"{BASE}/portal/sale/returnrefundcancel?keyword=2610020ABCDEFGH"
    )


async def test_return_id_ambiguo_ou_de_outro_pedido_e_recusado(db, make_user):
    integ = await _integracao(db, await make_user())
    dia = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)
    # 2 return_id na conversa.
    c1 = await _conversa(db, integ, mensagens=[_rr(OID, _id_de(dia, 1)), _rr(OID, _id_de(dia, 2))])
    r1 = await _reclamacao(db, integ, c1, "2609280AAAAAAAA")
    # O cartão rr é de OUTRO pedido do comprador (data do order_id não bate).
    c2 = await _conversa(
        db,
        integ,
        pedido="260928ZZZZZZZZ",
        criado="2026-09-28T10:00:00+08:00",
        mensagens=[_rr("243024924177313", _id_de(dia, 3))],
    )
    r2 = await _reclamacao(db, integ, c2, "2609280BBBBBBBB", pedido="260928ZZZZZZZZ")
    # Reclamação sem conversa: nada a ler.
    r3 = await _reclamacao(db, integ, None, "2609280CCCCCCCC")
    await db.commit()
    ids = await reclamacoes.ids_shopee_das(db, [r1, r2, r3])
    assert ids.devolucoes == {}


async def test_avaliacoes_uma_consulta_para_a_lista(db, make_user):
    """A lista da aba ★ lê os order_id de todas as avaliações numa consulta só."""
    dono = await make_user()
    integ = await _integracao(db, dono)
    await _conversa(db, integ, mensagens=[_crm(OID)])
    await db.commit()
    lista = [
        AtendimentoAvaliacaoLoja(
            integration_id=integ.id, plataforma="shopee", pedido=pedido, comentario_id=str(i)
        )
        for i, pedido in enumerate((SN, "260925V5P3BYXD", "260930ABCDEF12"))
    ] + [AtendimentoAvaliacaoLoja(integration_id=integ.id, plataforma="ml", pedido="2000012345")]

    consultas = []

    def _conta(conn, cursor, statement, *a):
        consultas.append(statement)

    motor = db.bind.sync_engine
    event.listen(motor, "before_cursor_execute", _conta)
    try:
        ids = await avaliacoes.ids_shopee_das(db, lista)
    finally:
        event.remove(motor, "before_cursor_execute", _conta)
    assert len([q for q in consultas if q.lstrip().upper().startswith("SELECT")]) == 1
    assert [avaliacoes.url_na_plataforma(a, ids) for a in lista[:3]] == [
        f"{BASE}/portal/sale/order/{OID}",
        f"{BASE}/portal/sale/order?search=260925V5P3BYXD",
        f"{BASE}/portal/sale/order?search=260930ABCDEF12",
    ]
    # Nenhuma avaliação Shopee: nenhuma consulta.
    consultas.clear()
    event.listen(motor, "before_cursor_execute", _conta)
    try:
        vazio = await avaliacoes.ids_shopee_das(db, lista[3:])
    finally:
        event.remove(motor, "before_cursor_execute", _conta)
    assert consultas == [] and vazio.pedidos == {}
