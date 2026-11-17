"""Reembolso ao cliente pela conversa do Atendimento (Novos procedimentos 3/6, 17/11/2026).

As decisões do dono de 17/11/2026, rota a rota (`routers/atendimento_reembolso.py`):

- o bloco da aba Pedido (GET) traz o pedido da PRÓPRIA conversa — a conta no
  formato da tela Reembolso ("Shopee ATV"), o valor pago —, os reembolsos que o
  pedido já tem (qualquer tipo, com a situação) e se a pessoa lança;
- quem lança: quem responde a conversa (loja no escopo dela) E é ADM ou tem
  `reembolso.edit`; fora das lojas dela, 404;
- o limite do ADM: > R$ 100, > 25% do pago ou o valor inteiro → "Aguardando
  ADM", que não pode ser Finalizado (nem pela tela Reembolso); ADM que lança já
  aprova; só ADM aprova;
- a plataforma já reembolsou (a fonte da aba: a devolução com reembolso pago) →
  só ADM lança;
- os dados do Pix vão cifrados, só ADM vê (aqui e na tela Reembolso), nunca no
  export; "outro" pede o detalhe; Pix pede os dados;
- a margem continua como hoje: o lançado nasce em aberto e só o Finalizado vai
  para `bling_orders.reembolso`.

Com a trava da fase de observação LIGADA (`SO_ADMIN`, como em produção) e o
admin dos testes FORA da lista de quem mexe na caixa (o ADM que paga não está
nela).
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from openpyxl import load_workbook
from sqlalchemy import select, text

from app.config import get_settings
from app.historico import nomes as historico_nomes
from app.main import app
from app.models import (
    AtendimentoCanal,
    AtendimentoReclamacao,
    BlingOrder,
    Company,
    Integration,
    IntegrationPlatform,
    Marketplace,
    Refund,
    Store,
    StoreInfo,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services import reembolso_cliente as regras
from app.services.atendimento import gravar
from app.services.atendimento import reembolso_conversa as conversa_svc
from tests.lojas_da_pessoa import dar_lojas

URL = "/api/atendimento"
DONO = "dono-da-caixa@davinci-test.com"
PEDIDO_MKT = "251101REEMB01"
PEDIDO_BLING = "300001"
LOJA_BLING = "204455"
PIX = "Chave 11999990000 - Banco Teste - Maria Compradora"
REEMBOLSO_EDIT = {"reembolso": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _fase_de_observacao(monkeypatch):
    """Como em produção: trava ligada; nada de leitura nem IA de verdade."""
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", DONO)
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_ia_ativa",
        "atendimento_envio_ativo",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_alerta_telegram",
    ):
        monkeypatch.setattr(s, nome, False)
    return s


# ─────────────── fábrica ───────────────


async def _loja(
    db, dono: User, *, plataforma: str = "shopee"
) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform(plataforma),
        name=f"loja-{uuid4().hex[:6]}",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma=plataforma, canal="chat", modo="observar", status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _conversa(
    db,
    integ: Integration,
    canal: AtendimentoCanal,
    *,
    plataforma: str = "shopee",
    pedido: str | None = PEDIDO_MKT,
    retrato: dict | None = None,
):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=plataforma,
        canal_nome="chat",
        externo_id=f"chat-{uuid4().hex[:8]}",
        pedido_marketplace=pedido,
        comprador_nome="Comprador",
    )
    if retrato is not None:
        conversa.dados = {**(conversa.dados or {}), "pedido_mkt": retrato}
    await db.commit()
    return conversa


def _retrato(valor_pago: float | None = 200.0, *, status: str = "COMPLETED") -> dict:
    return {
        "fonte": "shopee",
        "pedido": PEDIDO_MKT,
        "status": status,
        "status_texto": "Concluído" if status == "COMPLETED" else "Cancelado",
        "pago_em": "2026-11-10T10:00:00+00:00",
        "total": valor_pago,
        "valor_pago": valor_pago,
        "itens": [],
    }


async def _pedido_no_bling(
    db, dono: User, *, total: float = 200.0, numero: str = PEDIDO_BLING, loja: str = LOJA_BLING
) -> None:
    """O pedido no espelho do Bling e a loja dele: `stores` ("Shopee ATV") e a ficha."""
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=PEDIDO_MKT,
            bling_id=777001,
            data=datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
            total=total,
            situacao="9",
            loja=loja,
            item_index=0,
            item_codigo="SKU-1",
        )
    )
    empresa = Company(razao_social="Empresa Reembolso LTDA", apelido="Empresa Reembolso")
    db.add(empresa)
    await db.flush()
    db.add(
        Store(
            company_id=empresa.id,
            marketplace=Marketplace.SHOPEE,
            apelido_override="Shopee ATV",
            bling_store_id=int(loja),
        )
    )
    db.add(StoreInfo(user_id=dono.id, platform="shopee", account_name="atv", bling_store_id=loja))
    await db.commit()


@pytest.fixture
async def cenario(db, make_user):
    """Loja Shopee, a conversa do pedido (pago R$ 200) e o pedido no Bling."""
    dono = await make_user(email=DONO, role=UserRole.ADMIN)
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal, retrato=_retrato())
    await _pedido_no_bling(db, dono)
    return {"dono": dono, "integ": integ, "canal": canal, "conversa": conversa}


@pytest.fixture
async def adm(make_user) -> User:
    """Admin FORA da lista de quem mexe na caixa (como quem paga)."""
    return await make_user(role=UserRole.ADMIN)


@pytest.fixture
async def equipe(db, make_user, cenario) -> User:
    """Da equipe do atendimento: a loja da conversa e `reembolso.edit`."""
    u = await make_user(permissions=REEMBOLSO_EDIT)
    u.name = "Atendente Teste"
    await db.commit()
    await dar_lojas(db, u, cenario["integ"])
    return u


async def _reembolso_no_bling(db) -> float:
    """`bling_orders.reembolso` do pedido (a margem do Bling: só os finalizados)."""
    valor = await db.scalar(
        select(BlingOrder.reembolso)
        .where(BlingOrder.numero == PEDIDO_BLING)
        .execution_options(populate_existing=True)
    )
    return float(valor or 0.0)


def _url(conversa) -> str:
    return f"{URL}/conversas/{conversa.id}/reembolsos"


def _corpo(valor: float = 30.0, **extra) -> dict:
    return {"valor": valor, "motivo": "defeito_avaria", "forma": "cupom", **extra}


async def _refunds(db) -> list[Refund]:
    """As linhas como estão no banco agora (a API grava em outra sessão)."""
    return list(
        (
            await db.execute(
                select(Refund).order_by(Refund.created_at).execution_options(populate_existing=True)
            )
        ).scalars()
    )


# ─────────────── as regras puras ───────────────


@pytest.mark.parametrize(
    ("valor", "pago", "precisa"),
    [
        (30.00, 200.00, False),  # 15% e abaixo de R$ 100
        (50.00, 200.00, False),  # exatamente 25%: não passa de 25%
        (50.01, 200.00, True),  # passa de 25%
        (100.00, 1000.00, False),  # exatamente R$ 100: não passa de R$ 100
        (100.01, 1000.00, True),  # passa de R$ 100
        (20.00, 20.00, True),  # o valor inteiro
        (10.00, None, True),  # sem o valor pago: o ADM confere
        (10.00, 0.0, True),  # pago zerado: idem
    ],
)
def test_limite_do_adm(valor, pago, precisa):
    pago_c = regras.centavos(pago) if pago is not None else None
    assert regras.requer_aprovacao(regras.centavos(valor), pago_c) is precisa


def test_situacao_e_centavos():
    class R:
        def __init__(self, conferido, requer, aprovado_em):
            self.conferido, self.requer_aprovacao, self.aprovado_em = conferido, requer, aprovado_em

    agora = datetime.now(UTC)
    assert regras.situacao(R(False, True, None)) == "aguardando_adm"
    assert regras.situacao(R(False, True, agora)) == "em_aberto"
    assert regras.situacao(R(False, False, None)) == "em_aberto"
    assert regras.situacao(R(True, False, None)) == "finalizado"
    # As linhas antigas (sem a coluna preenchida no objeto novo) são "em aberto".
    assert regras.situacao(R(False, None, None)) == "em_aberto"
    assert regras.centavos(10.005) == 1001
    assert regras.centavos("12,5") is None
    assert regras.centavos(None) is None


def test_pix_cifrado_e_legivel_so_com_a_chave():
    cifra = regras.proteger_dados_pagamento(f"  {PIX}  ")
    assert cifra and PIX not in cifra
    assert regras.ler_dados_pagamento(cifra) == PIX
    assert regras.proteger_dados_pagamento("   ") is None
    # A cifra que não abre volta None (e o log só leva o id).
    assert regras.ler_dados_pagamento("não-é-cifra") is None


def _reclamacao(plataforma: str, status: str | None, **kw) -> AtendimentoReclamacao:
    return AtendimentoReclamacao(
        plataforma=plataforma,
        externo_id=uuid4().hex,
        tipo=kw.pop("tipo", "devolucao"),
        status=status,
        pedido_marketplace=kw.pop("pedido", PEDIDO_MKT),
        dados=kw.pop("dados", {}),
        encerrada_em=kw.pop("encerrada_em", None),
        **kw,
    )


def test_quando_a_plataforma_ja_reembolsou():
    sim = conversa_svc.reembolso_da_reclamacao
    assert sim(_reclamacao("shopee", "REFUND_PAID"))
    assert sim(_reclamacao("shopee", "ACCEPTED"))
    assert sim(_reclamacao("shopee", "PROCESSING")) is None
    assert sim(_reclamacao("shopee", "CANCELLED")) is None
    assert sim(_reclamacao("tiktok", "RETURN_OR_REFUND_REQUEST_COMPLETE"))
    assert sim(_reclamacao("tiktok", "BUYER_SHIPPED_ITEM")) is None
    # ML: a devolução com o dinheiro devolvido, ou encerrada a favor do comprador.
    assert sim(_reclamacao("ml", "closed", dados={"devolucao": {"status_dinheiro": "refunded"}}))
    encerrada = datetime.now(UTC)
    assert sim(
        _reclamacao(
            "ml",
            "closed",
            tipo="mediacao",
            encerrada_em=encerrada,
            dados={"resolucao": {"motivo": "coverage_decision", "beneficiado": ["complainant"]}},
        )
    )
    assert (
        sim(
            _reclamacao(
                "ml",
                "closed",
                tipo="mediacao",
                encerrada_em=encerrada,
                dados={"resolucao": {"motivo": "no_bpp", "beneficiado": ["respondent"]}},
            )
        )
        is None
    )
    assert (
        sim(_reclamacao("ml", "opened", dados={"devolucao": {"status_dinheiro": "retained"}}))
        is None
    )
    # O pedido cancelado na plataforma DEPOIS de pago (o status do retrato da aba).
    assert conversa_svc.reembolso_do_retrato("shopee", _retrato(status="CANCELLED"))
    assert conversa_svc.reembolso_do_retrato("shopee", _retrato()) is None
    assert (
        conversa_svc.reembolso_do_retrato(
            "shopee", {**_retrato(status="CANCELLED"), "pago_em": None, "valor_pago": None}
        )
        is None
    )
    assert conversa_svc.reembolso_do_retrato("ml", {"status": "cancelled", "valor_pago": 50})


# ─────────────── a trava da caixa ───────────────


def test_rotas_novas_na_trava_e_na_lista_de_quem_responde():
    novas = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and "reembolso" in r.path and r.path.startswith(URL + "/")
    ]
    moldes = {(m, r.path) for r in novas for m in r.methods}
    assert moldes == {
        ("GET", f"{URL}/conversas/{{conversa_id}}/reembolsos"),
        ("POST", f"{URL}/conversas/{{conversa_id}}/reembolsos"),
        ("POST", f"{URL}/reembolsos/{{refund_id}}/aprovar"),
    }
    for r in novas:
        assert rota._so_admin in [d.call for d in r.dependant.dependencies], r.path
    assert ("POST", f"{URL}/conversas/{{conversa_id}}/reembolsos") in rota.ROTAS_DE_QUEM_RESPONDE
    assert ("POST", f"{URL}/reembolsos/{{refund_id}}/aprovar") in rota.ROTAS_DE_QUEM_RESPONDE
    # O corpo do POST (os dados do Pix) nunca vai para o Histórico.
    assert historico_nomes.SEM_CORPO.search(f"{URL}/conversas/{uuid4()}/reembolsos")


@pytest.mark.parametrize("quem", ["comum_sem_permissao", "admin_fora_da_lista"])
async def test_quem_so_le_passa_da_trava_e_cai_na_rota(client, make_user, auth_as, quem):
    u = await make_user(role=UserRole.ADMIN if quem == "admin_fora_da_lista" else UserRole.USER)
    auth_as(u)
    falsa = uuid4()
    for metodo, caminho in (
        ("GET", f"{URL}/conversas/{falsa}/reembolsos"),
        ("POST", f"{URL}/conversas/{falsa}/reembolsos"),
        ("POST", f"{URL}/reembolsos/{falsa}/aprovar"),
    ):
        r = await client.request(metodo, caminho)
        assert r.status_code not in (401, 403), (metodo, caminho, r.status_code, r.text)
    # O Direct do Instagram não tem pedido: o bloco vazio, sem 404.
    r = await client.get(f"{URL}/conversas/ig:{uuid4()}/reembolsos")
    assert r.status_code == 200
    assert r.json()["pedido"] is None and r.json()["pode_lancar"] is False


# ─────────────── o bloco (GET) ───────────────


async def test_bloco_traz_o_pedido_da_conversa(client, auth_as, cenario, equipe):
    auth_as(equipe)
    r = await client.get(_url(cenario["conversa"]))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "pedido": {
            "bling": PEDIDO_BLING,
            "marketplace": PEDIDO_MKT,
            "plataforma": "shopee",
            "conta": "Shopee ATV",
            "valor_pago": 200.0,
        },
        "plataforma_reembolsou": False,
        "plataforma_detalhe": None,
        "limite": {"valor": 100, "pct": 25},
        "pode_lancar": True,
        "motivo_bloqueio": None,
        "pode_aprovar": False,
        "itens": [],
    }


async def test_conversa_de_outra_loja_e_404(client, db, make_user, auth_as, cenario):
    de_fora = await make_user(permissions=REEMBOLSO_EDIT)
    outra_integ, _ = await _loja(db, cenario["dono"])
    await dar_lojas(db, de_fora, outra_integ)
    auth_as(de_fora)
    assert (await client.get(_url(cenario["conversa"]))).status_code == 404
    r = await client.post(_url(cenario["conversa"]), json=_corpo())
    assert r.status_code == 404
    assert await _refunds(db) == []


async def test_sem_reembolso_edit_nao_lanca(client, db, make_user, auth_as, cenario):
    so_ve = await make_user(permissions={"reembolso": {"view": True}})
    await dar_lojas(db, so_ve, cenario["integ"])
    auth_as(so_ve)
    bloco = (await client.get(_url(cenario["conversa"]))).json()
    assert bloco["pode_lancar"] is False
    assert bloco["motivo_bloqueio"] == conversa_svc.SEM_PERMISSAO
    r = await client.post(_url(cenario["conversa"]), json=_corpo())
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "sem_permissao"
    assert await _refunds(db) == []


async def test_conversa_sem_pedido_e_sem_bling(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    sem = await _conversa(db, cenario["integ"], cenario["canal"], pedido=None)
    bloco = (await client.get(_url(sem))).json()
    assert bloco["pedido"] is None
    assert bloco["pode_lancar"] is False
    assert bloco["motivo_bloqueio"] == "A conversa não tem pedido ligado"
    r = await client.post(_url(sem), json=_corpo())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_pedido"
    # Com o nº na plataforma, mas o pedido ainda fora do Bling: o número, sem lançar.
    fora = await _conversa(db, cenario["integ"], cenario["canal"], pedido="251101NAOBLING")
    bloco = (await client.get(_url(fora))).json()
    assert bloco["pedido"]["bling"] is None
    assert bloco["pedido"]["marketplace"] == "251101NAOBLING"
    assert bloco["pode_lancar"] is False
    assert bloco["motivo_bloqueio"] == conversa_svc.SEM_BLING
    r = await client.post(_url(fora), json=_corpo())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_pedido"
    assert await _refunds(db) == []


async def test_sem_retrato_o_valor_pago_vem_do_bling(client, db, auth_as, cenario, equipe):
    """TikTok/Amazon/Magalu (sem o retrato da plataforma): o total do pedido no Bling."""
    auth_as(equipe)
    sem_retrato = await _conversa(db, cenario["integ"], cenario["canal"], retrato=None)
    bloco = (await client.get(_url(sem_retrato))).json()
    assert bloco["pedido"]["valor_pago"] == 200.0
    assert bloco["pedido"]["conta"] == "Shopee ATV"


# ─────────────── lançar (POST) ───────────────


async def test_lanca_abaixo_do_limite_em_aberto(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    r = await client.post(_url(cenario["conversa"]), json=_corpo(30.0))
    assert r.status_code == 201, r.text
    item = r.json()["item"]
    assert item["tipo"] == "Cliente"
    assert item["valor"] == 30.0
    assert item["motivo"] == "defeito_avaria"
    assert item["forma"] == "cupom"
    assert item["situacao"] == "em_aberto"
    assert item["criado_por"] == "Atendente Teste"
    assert item["aprovado_por"] is None and item["aprovado_em"] is None
    assert item["dados_pagamento"] is None
    assert item["conta"] == "Shopee ATV" and item["origem"] == "atendimento"
    (row,) = await _refunds(db)
    # Na mesma tabela da tela Reembolso, como os 75 lançados à mão.
    assert row.tipo == "Cliente"
    assert row.reembolso == -30.0 and row.prejuizo == 0.0
    assert row.conferido is False and row.requer_aprovacao is False
    assert row.pedido_bling == PEDIDO_BLING and row.pedido_marketplace == PEDIDO_MKT
    assert row.conta == "Shopee ATV" and row.plataforma == "shopee"
    assert row.data == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    assert row.created_by == equipe.id
    assert row.origem == "atendimento" and row.conversa_id == str(cenario["conversa"].id)
    assert row.reembolso_at is not None
    assert row.dados_pagamento is None
    # A margem como hoje: o em aberto não entra em bling_orders.reembolso.
    assert await _reembolso_no_bling(db) == 0.0
    # O bloco agora lista o reembolso.
    bloco = (await client.get(_url(cenario["conversa"]))).json()
    assert [i["id"] for i in bloco["itens"]] == [item["id"]]


async def test_a_conta_digitada_e_ignorada(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    r = await client.post(
        _url(cenario["conversa"]),
        json=_corpo(conta="Shopee Outra", pedido_bling="999", plataforma="ml"),
    )
    assert r.status_code == 201, r.text
    (row,) = await _refunds(db)
    assert (row.conta, row.pedido_bling, row.plataforma) == ("Shopee ATV", PEDIDO_BLING, "shopee")


async def test_validacoes_do_corpo(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    url = _url(cenario["conversa"])
    r = await client.post(url, json=_corpo(motivo="outro"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "detalhe_obrigatorio"
    r = await client.post(url, json=_corpo(motivo="outro", motivo_detalhe="   "))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "detalhe_obrigatorio"
    r = await client.post(url, json=_corpo(forma="pix"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "pix_sem_dados"
    r = await client.post(url, json=_corpo(forma="pix", dados_pagamento="  "))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "pix_sem_dados"
    # Nunca acima do valor pago pelo comprador (R$ 200).
    r = await client.post(url, json=_corpo(200.01))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "valor_acima_do_pago"
    # Valor zero/negativo e motivo/forma fora da lista: a validação do corpo.
    for ruim in (_corpo(0), _corpo(-5), _corpo(motivo="brinde"), _corpo(forma="boleto")):
        assert (await client.post(url, json=ruim)).status_code == 422, ruim
    assert await _refunds(db) == []
    r = await client.post(url, json=_corpo(motivo="outro", motivo_detalhe="Atraso na entrega"))
    assert r.status_code == 201, r.text
    assert r.json()["item"]["motivo_detalhe"] == "Atraso na entrega"


async def test_cupom_nao_guarda_pix(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    r = await client.post(_url(cenario["conversa"]), json=_corpo(dados_pagamento=PIX))
    assert r.status_code == 201
    (row,) = await _refunds(db)
    assert row.forma_pagamento == "cupom" and row.dados_pagamento is None


async def test_pix_cifrado_e_so_adm_ve(client, db, auth_as, cenario, equipe, adm):
    auth_as(equipe)
    r = await client.post(
        _url(cenario["conversa"]), json=_corpo(forma="pix", dados_pagamento=f" {PIX} ")
    )
    assert r.status_code == 201, r.text
    assert r.json()["item"]["dados_pagamento"] is None
    (row,) = await _refunds(db)
    # Gravado cifrado: o texto não está na coluna (nem no Histórico, que copia a linha).
    assert row.dados_pagamento and PIX not in row.dados_pagamento
    assert regras.ler_dados_pagamento(row.dados_pagamento) == PIX
    # A equipe não vê, nem na conversa nem na tela Reembolso.
    assert (await client.get(_url(cenario["conversa"]))).json()["itens"][0][
        "dados_pagamento"
    ] is None
    lista = (await client.get("/api/refunds")).json()["items"]
    assert lista[0]["dados_pagamento"] is None
    assert lista[0]["forma_pagamento"] == "pix" and lista[0]["motivo"] == "defeito_avaria"
    # O ADM vê nos dois lugares.
    auth_as(adm)
    bloco = (await client.get(_url(cenario["conversa"]))).json()
    assert bloco["pode_aprovar"] is True
    assert bloco["itens"][0]["dados_pagamento"] == PIX
    lista = (await client.get("/api/refunds")).json()["items"]
    assert lista[0]["dados_pagamento"] == PIX
    # O export nunca leva o Pix (nem para ADM), e tem as colunas novas.
    r = await client.get("/api/refunds/export.xlsx")
    assert r.status_code == 200
    planilha = load_workbook(io.BytesIO(r.content)).active
    linhas = [list(linha) for linha in planilha.iter_rows(values_only=True)]
    cabecalho = linhas[0]
    assert "Situação" in cabecalho and "Motivo" in cabecalho and "Forma de pagamento" in cabecalho
    assert not any("pix" in str(c).lower() and "dados" in str(c).lower() for c in cabecalho)
    texto = " ".join(str(c) for linha in linhas for c in linha if c is not None)
    assert PIX not in texto and "11999990000" not in texto
    assert "Defeito ou avaria" in texto and "Em aberto" in texto


async def test_acima_do_limite_aguarda_o_adm(client, db, auth_as, cenario, equipe, adm):
    auth_as(equipe)
    # R$ 60 de R$ 200 = 30%: passa dos 25%.
    r = await client.post(_url(cenario["conversa"]), json=_corpo(60.0))
    assert r.status_code == 201
    item = r.json()["item"]
    assert item["situacao"] == "aguardando_adm"
    (row,) = await _refunds(db)
    assert row.requer_aprovacao is True and row.aprovado_em is None
    # Na tela Reembolso: a situação e o 409 ao Finalizar (nem o ADM finaliza antes de aprovar).
    lista = (await client.get("/api/refunds")).json()["items"]
    assert lista[0]["situacao"] == "aguardando_adm" and lista[0]["requer_aprovacao"] is True
    r = await client.patch(f"/api/refunds/{item['id']}", json={"conferido": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "aguardando_adm"
    auth_as(adm)
    r = await client.patch(f"/api/refunds/{item['id']}", json={"conferido": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "aguardando_adm"
    # Outra edição da linha continua livre.
    r = await client.patch(f"/api/refunds/{item['id']}", json={"observacao": "conferindo"})
    assert r.status_code == 200 and r.json()["situacao"] == "aguardando_adm"
    # Só o ADM aprova.
    auth_as(equipe)
    r = await client.post(f"{URL}/reembolsos/{item['id']}/aprovar")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "sem_permissao"
    auth_as(adm)
    r = await client.post(f"{URL}/reembolsos/{item['id']}/aprovar")
    assert r.status_code == 200, r.text
    aprovado = r.json()["item"]
    assert aprovado["situacao"] == "em_aberto"
    assert aprovado["aprovado_por"] == adm.email and aprovado["aprovado_em"]
    r = await client.post(f"{URL}/reembolsos/{item['id']}/aprovar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_aprovado"
    # Aprovado, quem paga Finaliza na tela Reembolso — e aí a margem conta (como hoje).
    auth_as(equipe)
    r = await client.patch(f"/api/refunds/{item['id']}", json={"conferido": True})
    assert r.status_code == 200, r.text
    assert r.json()["situacao"] == "finalizado"
    assert r.json()["aprovado_por_nome"] == adm.email
    assert await _reembolso_no_bling(db) == -60.0
    bloco = (await client.get(_url(cenario["conversa"]))).json()
    assert bloco["itens"][0]["situacao"] == "finalizado"


async def test_valor_inteiro_e_acima_de_cem_aguardam(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    # O valor inteiro (R$ 200 de R$ 200) e acima de R$ 100: os dois pedem o ADM.
    r = await client.post(_url(cenario["conversa"]), json=_corpo(200.0))
    assert r.status_code == 201 and r.json()["item"]["situacao"] == "aguardando_adm"
    r = await client.post(_url(cenario["conversa"]), json=_corpo(45.0))
    assert r.status_code == 201 and r.json()["item"]["situacao"] == "em_aberto"


async def test_sem_valor_pago_vai_para_o_adm(client, db, auth_as, cenario, equipe):
    """Sem retrato e com o total do Bling zerado não há os 25% a conferir: o ADM confere."""
    db.add(
        BlingOrder(
            numero="300002",
            numeroloja="251101SEMPAGO",
            bling_id=777002,
            data=datetime(2026, 11, 11, 12, 0, tzinfo=UTC),
            total=0,
            situacao="9",
            loja=LOJA_BLING,
            item_index=0,
            item_codigo="SKU-2",
        )
    )
    await db.commit()
    conversa = await _conversa(db, cenario["integ"], cenario["canal"], pedido="251101SEMPAGO")
    auth_as(equipe)
    bloco = (await client.get(_url(conversa))).json()
    assert bloco["pedido"]["bling"] == "300002" and bloco["pedido"]["valor_pago"] is None
    assert bloco["pode_lancar"] is True
    r = await client.post(_url(conversa), json=_corpo(10.0))
    assert r.status_code == 201, r.text
    assert r.json()["item"]["situacao"] == "aguardando_adm"


async def test_adm_que_lanca_ja_aprova(client, db, auth_as, cenario, adm):
    auth_as(adm)  # sem permissão nenhuma nem loja: ADM sempre
    r = await client.post(_url(cenario["conversa"]), json=_corpo(150.0))
    assert r.status_code == 201, r.text
    item = r.json()["item"]
    assert item["situacao"] == "em_aberto"
    assert item["aprovado_por"] == adm.email and item["aprovado_em"]
    (row,) = await _refunds(db)
    assert row.requer_aprovacao is True and row.aprovado_por == adm.id
    r = await client.post(f"{URL}/reembolsos/{item['id']}/aprovar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_aprovado"


async def test_aprovar_o_que_nao_existe_ou_nao_precisa(client, db, auth_as, cenario, equipe, adm):
    auth_as(adm)
    r = await client.post(f"{URL}/reembolsos/{uuid4()}/aprovar")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "reembolso_nao_encontrado"
    auth_as(equipe)
    pequeno = (await client.post(_url(cenario["conversa"]), json=_corpo(10.0))).json()["item"]
    auth_as(adm)
    r = await client.post(f"{URL}/reembolsos/{pequeno['id']}/aprovar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_aprovado"


async def test_plataforma_ja_reembolsou_so_adm_lanca(client, db, auth_as, cenario, equipe, adm):
    conversa = cenario["conversa"]
    # A devolução de OUTRA compra no mesmo chat não conta.
    db.add(
        AtendimentoReclamacao(
            integration_id=cenario["integ"].id,
            conversa_id=conversa.id,
            plataforma="shopee",
            externo_id="RET-OUTRO",
            tipo="devolucao",
            status="REFUND_PAID",
            pedido_marketplace="251101OUTRACOMPRA",
            dados={},
        )
    )
    await db.commit()
    auth_as(equipe)
    bloco = (await client.get(_url(conversa))).json()
    assert bloco["plataforma_reembolsou"] is False and bloco["pode_lancar"] is True
    # A deste pedido, com o reembolso pago pela Shopee.
    db.add(
        AtendimentoReclamacao(
            integration_id=cenario["integ"].id,
            conversa_id=None,
            plataforma="shopee",
            externo_id="RET-ESTE",
            tipo="devolucao",
            status="REFUND_PAID",
            pedido_marketplace=PEDIDO_MKT,
            encerrada_em=datetime.now(UTC) - timedelta(days=1),
            dados={"valor_reembolso": 200.0},
        )
    )
    await db.commit()
    bloco = (await client.get(_url(conversa))).json()
    assert bloco["plataforma_reembolsou"] is True
    assert bloco["pode_lancar"] is False
    assert "só o ADM" in bloco["motivo_bloqueio"]
    assert "Shopee" in bloco["plataforma_detalhe"]
    r = await client.post(_url(conversa), json=_corpo())
    assert r.status_code == 403 and r.json()["detail"]["code"] == "so_adm_plataforma_reembolsou"
    assert await _refunds(db) == []
    # O ADM lança (com o aviso).
    auth_as(adm)
    bloco = (await client.get(_url(conversa))).json()
    assert bloco["plataforma_reembolsou"] is True and bloco["pode_lancar"] is True
    r = await client.post(_url(conversa), json=_corpo())
    assert r.status_code == 201, r.text


async def test_avisa_dos_outros_reembolsos_sem_bloquear(client, db, auth_as, cenario, equipe):
    # Um reembolso de outro tipo, lançado antes pela tela Reembolso (finalizado).
    db.add(
        Refund(
            pedido_bling=PEDIDO_BLING,
            conta="Shopee ATV",
            tipo="Manutenção",
            prejuizo=35.0,
            reembolso=-35.0,
            conferido=True,
        )
    )
    # E um de outro pedido, que não aparece.
    db.add(Refund(pedido_bling="999999", conta="Shopee ATV", tipo="Cliente", reembolso=-10.0))
    await db.commit()
    auth_as(equipe)
    bloco = (await client.get(_url(cenario["conversa"]))).json()
    assert [(i["tipo"], i["valor"], i["situacao"]) for i in bloco["itens"]] == [
        ("Manutenção", 35.0, "finalizado")
    ]
    assert bloco["pode_lancar"] is True
    r = await client.post(_url(cenario["conversa"]), json=_corpo())
    assert r.status_code == 201
    bloco = (await client.get(_url(cenario["conversa"]))).json()
    assert [i["tipo"] for i in bloco["itens"]] == ["Cliente", "Manutenção"]


async def test_duplo_clique_grava_uma_vez(client, db, auth_as, cenario, equipe):
    auth_as(equipe)
    a = await client.post(_url(cenario["conversa"]), json=_corpo(25.0))
    b = await client.post(_url(cenario["conversa"]), json=_corpo(25.0))
    assert a.status_code == b.status_code == 201
    assert a.json()["item"]["id"] == b.json()["item"]["id"]
    assert len(await _refunds(db)) == 1
    # Outro valor é outro lançamento.
    c = await client.post(_url(cenario["conversa"]), json=_corpo(26.0))
    assert c.status_code == 201 and c.json()["item"]["id"] != a.json()["item"]["id"]
    assert len(await _refunds(db)) == 2


async def test_tela_reembolso_continua_igual_para_as_linhas_antigas(client, db, make_user, auth_as):
    """Os reembolsos antigos (sem as colunas novas) saem como antes, em aberto/finalizado."""
    u = await make_user(permissions=REEMBOLSO_EDIT)
    auth_as(u)
    r = await client.post(
        "/api/refunds",
        json={"pedido_bling": "C-9", "conta": "Loja X", "tipo": "Cliente", "reembolso": 12},
    )
    assert r.status_code == 201
    novo = r.json()
    assert novo["reembolso"] == -12.0
    assert novo["situacao"] == "em_aberto" and novo["requer_aprovacao"] is False
    assert novo["origem"] is None and novo["dados_pagamento"] is None
    r = await client.patch(f"/api/refunds/{novo['id']}", json={"conferido": True})
    assert r.status_code == 200 and r.json()["situacao"] == "finalizado"
    # A busca de pedido da tela ("adicionar pedido") avisa com a situação.
    schema = get_settings().database_schema
    await db.execute(text(f'DROP VIEW IF EXISTS "{schema}".vw_conciliacao_margens_marketplace'))
    await db.execute(
        text(
            f"""
            CREATE VIEW "{schema}".vw_conciliacao_margens_marketplace AS
            SELECT * FROM (VALUES
                (now(), '1'::text, '1'::text, 'ml'::text, NULL::text, 'X'::text, 1::numeric)
            ) AS t(data, pedido_bling, pedido_marketplace, plataforma_bling,
                   plataforma_financeiro, loja_nome, bling_custo_produtos)
            WHERE false
            """  # noqa: S608
        )
    )
    await db.commit()
    r = await client.get("/api/refunds/order-lookup", params={"pedido": "C-9"})
    assert r.status_code == 200, r.text
    assert r.json()["reembolsos_do_pedido"][0]["situacao"] == "finalizado"


def test_export_nunca_lista_o_pix():
    from app.routers import refunds as refunds_router

    campos = {campo for _, campo in refunds_router._EXPORT_COLUMNS}
    assert "dados_pagamento" not in campos
    assert "dados_pagamento" in refunds_router._FORA_DO_EXPORT
    assert not any(re.search("pix", rotulo, re.I) for rotulo, _ in refunds_router._EXPORT_COLUMNS)
