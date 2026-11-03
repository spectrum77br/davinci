"""Item 4, fase 4b — a lista "Ag. cancelamento" (GET /api/atendimento/ag-cancelamento, 05/10/2026).

O router `atendimento_troca` (à parte do painel, para não mexer no teste que
lista as rotas dele). O que estes testes seguram:

- a MESMA trava de acesso da caixa (`_so_admin`) em todas as rotas do
  router novo, registrado pelo `app.main` — desde 07/10/2026 a equipe LÊ
  (a lista e as trocas); desde 03/11/2026 (o Eduardo: "quero que funcione
  para quem também usa o atendimento, não só admin, essa parte de troca, que
  é as 3 equipes que vão gerenciar") quem RESPONDE escreve (a prévia, o
  "Trocar", o "Retomar" e a oferta: `_responde`, `ROTAS_DE_QUEM_RESPONDE`),
  sem a `margem.edit` da decisão (g) — e só nos pedidos e conversas das
  lojas dela (404 no de outra equipe, também pelo id); o operador de
  estoque, não;
- quem não vê a Margem vê o motivo da Margem como "em análise";
- cada pedido traz a troca de produto aberta (`motivo.troca_aberta`);
- a lista é pelo PEDIDO (com ou sem conversa): só os pedidos em 83955 da
  janela, com o motivo do classificador (o mesmo bloco do painel), os
  itens, o prazo de envio e a conversa principal quando houver;
- o filtro `codigo` e os contadores `por_codigo` (antes do filtro);
- as sugestões de troca só na falta de estoque com a chave ligada, e o %
  do custo só para quem vê a Margem;
- SÓ LEITURA E SEM BLING: nenhuma chamada ao Bling (nem as Observações).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import update

from app.config import get_settings
from app.main import app
from app.models import (
    AtendimentoCanal,
    AtendimentoTroca,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    MargemAudit,
    NfFaturamento,
    Product,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.routers import atendimento as rota
from app.routers import atendimento_troca
from app.security.cipher import encrypt_json
from app.services.atendimento import gravar, painel, troca, troca_oferta, troca_sugestoes
from tests.lojas_da_pessoa import dar_lojas

URL = "/api/atendimento/ag-cancelamento"
AGORA = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_troca_sugestoes_ativa",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "atendimento_troca_teto_custo_pct", 5.0)
    monkeypatch.setattr(s, "atendimento_troca_piso_nivel2_pct", -10.0)
    return s


@pytest.fixture(autouse=True)
def _sem_bling(monkeypatch):
    """Qualquer chamada ao Bling quebra o teste: a lista é só leitura do DaVinci."""
    chamadas: list[str] = []

    async def _proibido(session):
        chamadas.append("bling")
        raise AssertionError("a lista Ag. cancelamento não fala com o Bling")

    monkeypatch.setattr(painel, "_cliente_bling", _proibido)
    troca_sugestoes.limpar_memoria()
    yield chamadas
    troca_sugestoes.limpar_memoria()
    assert chamadas == []


@pytest.fixture
async def admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid4().hex[:6]}@davinci-test.com")
    auth_as(u)
    return u


# ─────────────── fábrica ───────────────


async def _pedido(
    db,
    numero,
    *,
    numeroloja=None,
    itens=(("dg053.sp", 1),),
    situacao="83955",
    loja="7001",
    dias=1,
    prazo_horas=None,
    bling_id=None,
):
    for i, (sku, qtd) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=bling_id or int(numero),
                item_index=i,
                numero=numero,
                numeroloja=numeroloja or f"SHP{numero}",
                situacao=situacao,
                loja=loja,
                item_codigo=sku,
                item_descricao=f"Produto {sku}",
                item_quantidade=qtd,
                data=AGORA - timedelta(days=dias),
                marketplace_ship_deadline=(
                    AGORA + timedelta(hours=prazo_horas) if prazo_horas is not None else None
                ),
            )
        )
    await db.commit()


async def _marca_nf(db, numero, status, erro):
    quando = AGORA - timedelta(hours=1)
    db.add(
        NfFaturamento(
            pedido_bling=numero,
            status_faturamento=status,
            erro_faturamento=erro,
            created_at=quando,
            updated_at=quando,
        )
    )
    await db.commit()


async def _catalogo_a17(db, dono):
    for sku, nome, estoque in (
        ("dg053.sp", "Uranyx A17 Pro Max 12.64 - Branco", 0),
        ("dg054.sp", "Uranyx A17 Pro Max 12.64 - Laranja", 9),
    ):
        db.add(
            Product(
                user_id=dono.id,
                sku=sku,
                name=nome,
                stock=estoque,
                formato="S",
                situacao="A",
                bling_cost_price=Decimal("495"),
            )
        )
    await db.commit()


async def _conversa_shopee(db, dono, numeroloja):
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="observar", status="ok"
    )
    db.add(canal)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="kfa",
            bling_store_id="7001",
            integration_id=integ.id,
        )
    )
    await db.commit()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-troca",
        pedido_marketplace=numeroloja,
        comprador_nome="Comprador Fictício",
    )
    await db.commit()
    return conversa


async def _cenario(db, dono):
    """5 pedidos: falta de estoque (com conversa), trava da Margem, restrição, em aberto, velho."""
    await _catalogo_a17(db, dono)
    await _pedido(db, "300101", numeroloja="SHPTROCA1", prazo_horas=20)
    await _marca_nf(
        db, "300101", "sem_estoque", "Aguardando Cancelamento — saldo negativo: dg053.sp"
    )
    await _pedido(db, "300102", prazo_horas=5)
    db.add(
        MargemAudit(
            pedido_bling="300102",
            acao="situacao",
            valor_antigo="6",
            valor_novo="83955",
            origem="margens_auto",
            mudado_por=None,
            created_at=AGORA - timedelta(hours=2),
        )
    )
    await db.commit()
    await db.execute(
        update(BlingOrder).where(BlingOrder.numero == "300102").values(status="Pendente")
    )
    await db.commit()
    await _pedido(db, "300103", itens=(("i223.sa", 1),))
    await _marca_nf(db, "300103", "restricao", "Restrição Shopee — Apple não envia pro RJ")
    await _pedido(db, "300104", situacao="6")
    await _pedido(db, "300105", dias=90)
    return await _conversa_shopee(db, dono, "SHPTROCA1")


# ─────────────── trava ───────────────


# Escrever: a prévia, a troca, o retomar e a OFERTA — que promete a troca ao
# comprador — pedem o mesmo: desde 03/11/2026, quem responde (as 3 equipes).
ESCRITAS = {
    ("/api/atendimento/pedidos/{numero_bling}/troca/previa", ("POST",)),
    ("/api/atendimento/pedidos/{numero_bling}/troca", ("POST",)),
    ("/api/atendimento/trocas/{troca_id}/retomar", ("POST",)),
    ("/api/atendimento/pedidos/{numero_bling}/troca/oferta", ("POST",)),
}


def test_rotas_tem_a_trava_da_caixa():
    rotas = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.endpoint.__module__ == atendimento_troca.__name__
    ]
    assert {(r.path, tuple(sorted(r.methods))) for r in rotas} == {
        (URL, ("GET",)),
        ("/api/atendimento/trocas", ("GET",)),
        *ESCRITAS,
    }
    # A decisão (g) de 07/10/2026 (margem.edit para escrever) saiu em 03/11/2026.
    assert not hasattr(atendimento_troca, "_margem_edit")
    for r in rotas:
        deps = [d.call for d in r.dependant.dependencies]
        assert rota._so_admin in deps, r.path
        if (r.path, tuple(sorted(r.methods))) in ESCRITAS:
            # Escrever: quem responde (a mesma trava do responder), nas lojas dela.
            assert rota._responde in deps, r.path
            assert rota._edit not in deps, r.path
            assert ("POST", r.path) in rota.ROTAS_DE_QUEM_RESPONDE, r.path
        else:
            assert rota._view in deps, r.path


def _pessoa(role=UserRole.USER, *, status=UserStatus.ACTIVE, permissions=None, stock_tags=None):
    return User(
        email=f"p-{uuid4().hex[:6]}@davinci-test.com",
        open_id=f"email:p-{uuid4().hex[:6]}",
        role=role,
        status=status,
        permissions=permissions or {},
        stock_tags=stock_tags or [],
    )


@pytest.mark.parametrize("so_admin", [True, False])
async def test_o_botao_e_a_rota_dizem_o_mesmo(monkeypatch, _chaves, so_admin):
    """`troca.pode_escrever` (o `troca_envio`/`oferta_envio`) = a trava da rota (`_responde`)."""
    monkeypatch.setattr(rota, "SO_ADMIN", so_admin)
    monkeypatch.setattr(_chaves, "atendimento_usuarios", "dono@davinci-test.com")
    dono = _pessoa(UserRole.ADMIN)
    dono.email = "dono@davinci-test.com"
    pessoas = {
        "admin da lista": dono,
        "admin fora da lista": _pessoa(UserRole.ADMIN),
        "equipe sem permissão": _pessoa(),
        "com atendimento.edit": _pessoa(permissions={"atendimento": {"view": True, "edit": True}}),
        "com a Margem": _pessoa(permissions={"margem": {"view": True, "edit": True}}),
        "operador de estoque": _pessoa(
            permissions={"controle_estoque": {"view": True, "edit": True}}, stock_tags=["ci"]
        ),
    }
    for nome, u in pessoas.items():
        try:
            await rota._responde(u)
            passa = True
        except HTTPException as e:
            assert e.status_code == 403, nome
            passa = False
        assert troca.pode_escrever(u) is passa, nome
        assert troca_oferta.pode_escrever(u) is passa, nome
    if so_admin:
        # Na fase de observação: toda pessoa que responde, sem a Margem; o operador, não.
        assert troca.pode_escrever(pessoas["equipe sem permissão"]) is True
        assert troca.pode_escrever(pessoas["admin fora da lista"]) is True
        assert troca.pode_escrever(pessoas["operador de estoque"]) is False
        suspenso = _pessoa(status=UserStatus.SUSPENDED)
        assert troca.pode_escrever(suspenso) is False
    else:
        # Fora dela: o atendimento.edit de sempre (sem a margem.edit).
        assert troca.pode_escrever(pessoas["com atendimento.edit"]) is True
        assert troca.pode_escrever(pessoas["com a Margem"]) is False
    assert troca.pode_escrever(None) is False


async def test_operador_de_estoque_nao_troca_nem_oferece(db, client, make_user, auth_as):
    operador = await make_user(permissions={"controle_estoque": {"view": True, "edit": True}})
    operador.stock_tags = ["ci"]
    await db.commit()
    auth_as(operador)
    corpo = {"sku_antigo": "dg053.sp", "sku_novo": "dg054.sp"}
    chamadas = [
        ("/api/atendimento/pedidos/300101/troca/previa", corpo),
        (
            "/api/atendimento/pedidos/300101/troca",
            {**corpo, "previa_hash": "x" * 32, "idem_key": str(uuid4()), "confirmar": True},
        ),
        (f"/api/atendimento/trocas/{uuid4()}/retomar", None),
        ("/api/atendimento/pedidos/300101/troca/oferta", {"sku_novo": "dg054.sp"}),
    ]
    for caminho, corpo_ in chamadas:
        r = await client.post(caminho, json=corpo_)
        assert r.status_code == 403, (caminho, r.text)
        assert r.json()["detail"] == {"code": "atendimento_restrito"}, caminho
    assert (await client.get(URL)).status_code == 403


# ─────────────── as 3 equipes (03/11/2026) ───────────────


async def _outra_loja(db, dono, numeroloja):
    """A loja de OUTRA equipe (Bling 7002, a integração "vrc") e a conversa do pedido dela."""
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="vrc",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="observar", status="ok"
    )
    db.add(canal)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="vrc",
            bling_store_id="7002",
            integration_id=integ.id,
        )
    )
    await db.commit()
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-outra-equipe",
        pedido_marketplace=numeroloja,
        comprador_nome="Outro Comprador",
    )
    await db.commit()
    return integ, canal, conversa


async def _duas_equipes(db, make_user, monkeypatch, _chaves):
    """londres cuida da loja 7001 (o pedido 300101 e a conversa dele); israel, da 7002
    (o pedido 300201, também em 83955 por falta de estoque, e a conversa dele)."""
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", True)
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", "")
    monkeypatch.setattr(_chaves, "atendimento_troca_folga_prazo_horas", 2.0)
    dono = await make_user()
    conversa = await _cenario(db, dono)
    await _pedido(db, "300201", numeroloja="SHPOUTRA", loja="7002", prazo_horas=20)
    await _marca_nf(
        db, "300201", "sem_estoque", "Aguardando Cancelamento — saldo negativo: dg053.sp"
    )
    integ_outra, canal_outra, conversa_outra = await _outra_loja(db, dono, "SHPOUTRA")
    londres = await make_user(email=f"londres-{uuid4().hex[:6]}@davinci-test.com")
    israel = await make_user(email=f"israel-{uuid4().hex[:6]}@davinci-test.com")
    await dar_lojas(db, londres, conversa.integration_id)
    await dar_lojas(db, israel, integ_outra)

    # Sem Bling: a prévia e o retomar param no "Sem integração do Bling ativa".
    async def _sem_cliente(session):
        return None

    monkeypatch.setattr(troca, "_cliente_bling", _sem_cliente)
    return SimpleNamespace(
        londres=londres,
        israel=israel,
        conversa=conversa,
        integ_outra=integ_outra,
        canal_outra=canal_outra,
        conversa_outra=conversa_outra,
    )


PREVIA = "/api/atendimento/pedidos/{}/troca/previa"
CORPO = {"sku_antigo": "dg053.sp", "sku_novo": "dg054.sp"}


@pytest.mark.lojas_por_pessoa
async def test_equipe_troca_e_oferece_so_nas_lojas_dela(
    db, client, make_user, auth_as, monkeypatch, _chaves
):
    """A equipe (role user, sem permissão nenhuma) que responde: a lista e a prévia das
    lojas dela, o botão liberado; o pedido e a conversa de outra equipe, 404 — também
    pelo id. Sem a Margem: nada de custo nem %."""
    monkeypatch.setattr(_chaves, "atendimento_troca_sugestoes_ativa", True)
    e = await _duas_equipes(db, make_user, monkeypatch, _chaves)
    auth_as(e.londres)

    r = await client.get(URL)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert {p["numero"] for p in corpo["pedidos"]} == {"300101", "300102", "300103"}
    assert corpo["ve_custo"] is False
    falta = {p["numero"]: p for p in corpo["pedidos"]}["300101"]
    assert falta["motivo"]["troca_envio"]["disponivel"] is True, falta["motivo"]["troca_envio"]
    # A oferta: a trava de quem pode passa (o que segura é o envio desligado).
    assert falta["motivo"]["oferta_envio"]["motivo"] != "atendimento_so_leitura"
    [sug] = falta["sugestoes_troca"]["itens"][0]["sugestoes"]
    assert (sug["sku"], sug["dif_custo_pct"]) == ("dg054.sp", None)

    # A prévia do pedido da loja dela: passa a trava e o escopo (e para no Bling).
    r = await client.post(
        PREVIA.format("300101"), json={**CORPO, "conversa_id": str(e.conversa.id)}
    )
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["pode"] is False
    assert "bling_indisponivel" in {t["code"] for t in pv["travas"] if not t["ok"]}
    assert (pv["antes"]["custo"], pv["depois"]["custo"], pv["dif_custo_pct"]) == (None,) * 3
    assert pv["passos_previstos"][2].startswith("Margem Aprovado por ")

    # O pedido de OUTRA equipe: 404 em toda escrita, como o que não existe.
    r = await client.post(PREVIA.format("300201"), json=CORPO)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"
    r = await client.post(
        "/api/atendimento/pedidos/300201/troca",
        json={**CORPO, "previa_hash": "x" * 32, "idem_key": str(uuid4()), "confirmar": True},
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"
    r = await client.post(
        "/api/atendimento/pedidos/300201/troca/oferta", json={"sku_novo": "dg054.sp"}
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"

    # A conversa de OUTRA equipe pelo id, no pedido dela: 404 (a troca, o clique e a oferta).
    outra = str(e.conversa_outra.id)
    r = await client.post(PREVIA.format("300101"), json={**CORPO, "conversa_id": outra})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    r = await client.post(
        "/api/atendimento/pedidos/300101/troca",
        json={
            **CORPO,
            "conversa_id": outra,
            "previa_hash": "x" * 32,
            "idem_key": str(uuid4()),
            "confirmar": True,
        },
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    r = await client.post(
        "/api/atendimento/pedidos/300101/troca/oferta",
        json={"sku_novo": "dg054.sp", "conversa_id": outra},
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    # Uma conversa que não existe também (nada diz se existe).
    r = await client.post(PREVIA.format("300101"), json={**CORPO, "conversa_id": str(uuid4())})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    # A conversa de outra equipe com o MESMO nº do pedido na plataforma: a troca a
    # reconheceria como do pedido (e mostraria as falas dela como aceite) — 404.
    mesma, _ = await gravar.upsert_conversa(
        db,
        canal=e.canal_outra,
        integration=e.integ_outra,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-outra-mesmo-pedido",
        pedido_marketplace="SHPTROCA1",
        comprador_nome="Outro Comprador",
    )
    await db.commit()
    r = await client.post(PREVIA.format("300101"), json={**CORPO, "conversa_id": str(mesma.id)})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"

    # A troca de OUTRA equipe pelo id: 404; a dela, o retomar anda (sem custo).
    t = AtendimentoTroca(
        pedido_bling="300201",
        bling_id=300201,
        motivo_codigo="sem_estoque",
        sku_antigo="dg053.sp",
        sku_novo="dg054.sp",
        produto_novo_id=502,
        quantidade=1,
        nivel=1,
        estado="item_trocado",
        codigo_erro="bling_indisponivel",
        custo_antigo=Decimal("495"),
        custo_novo=Decimal("495"),
        passos=[],
        idem_key=uuid4(),
        criado_por_nome="Israel",
    )
    db.add(t)
    await db.commit()
    r = await client.post(f"/api/atendimento/trocas/{t.id}/retomar")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "troca_nao_encontrada"
    assert (await client.get("/api/atendimento/trocas")).json() == {"itens": []}
    auth_as(e.israel)
    r = await client.post(f"/api/atendimento/trocas/{t.id}/retomar")
    assert r.status_code == 200, r.text
    assert (r.json()["codigo_erro"], r.json()["custo_antigo"]) == ("bling_indisponivel", None)
    [item] = (await client.get("/api/atendimento/trocas")).json()["itens"]
    assert (item["id"], item["custo_novo"]) == (str(t.id), None)
    # israel não vê a loja de londres.
    r = await client.post(PREVIA.format("300101"), json=CORPO)
    assert r.status_code == 404


@pytest.mark.lojas_por_pessoa
async def test_admin_segue_como_antes(db, client, make_user, auth_as, monkeypatch, _chaves):
    """Admin: todas as lojas, o custo e o 422 da conversa que não é do pedido, como antes."""
    e = await _duas_equipes(db, make_user, monkeypatch, _chaves)
    adm = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid4().hex[:6]}@davinci-test.com")
    auth_as(adm)
    r = await client.post(PREVIA.format("300201"), json=CORPO)
    assert r.status_code == 200, r.text
    assert r.json()["antes"]["custo"] == 495.0
    r = await client.post(
        PREVIA.format("300101"), json={**CORPO, "conversa_id": str(e.conversa_outra.id)}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "conversa_de_outro_pedido"
    r = await client.get(URL)
    assert {"300101", "300201"} <= {p["numero"] for p in r.json()["pedidos"]}


async def test_rota_pede_a_permissao_de_ver(db, client, make_user, auth_as, monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    u = await make_user(permissions={"margem": {"view": True}})
    auth_as(u)
    r = await client.get(URL)
    assert r.status_code == 403


# ─────────────── a lista ───────────────


async def test_lista_os_pedidos_em_83955_com_o_motivo(db, client, admin, make_user):
    dono = await make_user()
    conversa = await _cenario(db, dono)

    r = await client.get(URL)
    assert r.status_code == 200, r.text
    corpo = r.json()
    # Em aberto (6) e o pedido de 90 dias ficam de fora.
    assert [p["numero"] for p in corpo["pedidos"]] == ["300102", "300101", "300103"]
    assert corpo["total"] == 3
    assert corpo["por_codigo"] == {"sem_estoque": 1, "margem_trava": 1, "restricao_envio": 1}
    assert (corpo["sugestoes_ativas"], corpo["ve_custo"]) == (False, True)
    por = {p["numero"]: p for p in corpo["pedidos"]}

    falta = por["300101"]
    assert falta["motivo"]["codigo"] == "sem_estoque"
    assert falta["motivo"]["titulo"] == "Falta de estoque"
    assert (falta["motivo"]["pode_sugerir_troca"], falta["motivo"]["skus"]) == (True, ["dg053.sp"])
    assert falta["conversa_id"] == str(conversa.id)
    assert (falta["plataforma"], falta["conta"], falta["loja"]) == ("shopee", "kfa", "7001")
    assert falta["itens"] == [{"sku": "dg053.sp", "descricao": "Produto dg053.sp", "quantidade": 1}]
    assert falta["prazo_envio"] is not None
    # Chave desligada (o padrão): sem sugestões.
    assert falta["sugestoes_troca"] is None

    trava = por["300102"]
    assert (trava["motivo"]["codigo"], trava["motivo"]["etiqueta"]) == ("margem_trava", False)
    assert trava["conversa_id"] is None and trava["sugestoes_troca"] is None
    assert por["300103"]["motivo"]["codigo"] == "restricao_envio"

    # O filtro pelo motivo; os contadores continuam os de antes do filtro.
    r = await client.get(URL, params={"codigo": "sem_estoque"})
    assert [p["numero"] for p in r.json()["pedidos"]] == ["300101"]
    assert r.json()["por_codigo"]["margem_trava"] == 1
    # A janela: com 120 dias o pedido de 90 dias entra (movido à mão).
    r = await client.get(URL, params={"dias": 120})
    assert {p["numero"]: p["motivo"]["codigo"] for p in r.json()["pedidos"]}["300105"] == "manual"
    assert (await client.get(URL, params={"dias": 0})).status_code == 422


async def test_lista_aponta_o_chat_mesmo_com_o_aviso_por_email(db, client, admin, make_user):
    """13/10/2026 (`separa_email`): o aviso da plataforma por e-mail entra na
    conversa de E-MAIL da loja, com o pedido e mais nova que o chat. A lista
    continua apontando o CHAT (a de e-mail mora na aba E-mail) e classifica
    pelo retrato dele (o IN_CANCEL da Shopee = o comprador pediu)."""
    dono = await make_user()
    chat = await _cenario(db, dono)
    chat.dados = {**(chat.dados or {}), "pedido_mkt": {"status": "IN_CANCEL"}}
    await db.commit()
    integ = await db.get(Integration, chat.integration_id)
    email, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma="shopee",
        canal_nome="email",
        externo_id="mail-fio:aviso-1",
        pedido_marketplace="SHPTROCA1",
        dados={"fonte": "tuta", "mail": {"alias": "kfa@tuta.com"}},
    )
    await gravar.gravar_mensagem(
        db,
        email,
        externo_id="mail:aviso-1",
        autor="sistema",
        texto="E-mail da plataforma: pedido em cancelamento",
        enviada_em=AGORA,
        payload={"mail": {"message_id": "aviso-1"}},
    )
    await db.commit()

    r = await client.get(URL)
    assert r.status_code == 200, r.text
    falta = {p["numero"]: p for p in r.json()["pedidos"]}["300101"]
    assert falta["conversa_id"] == str(chat.id)
    assert falta["motivo"]["codigo"] == "pedido_cliente"


async def test_lista_mascara_a_margem_para_quem_nao_ve(db, client, make_user, auth_as):
    """Quem só lê e não vê a Margem: o motivo da Margem vira "em análise" (e conta assim)."""
    u = await make_user(permissions={"atendimento": {"view": True}})
    auth_as(u)
    await _cenario(db, u)
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ve_custo"] is False
    assert corpo["por_codigo"] == {"sem_estoque": 1, "em_analise": 1, "restricao_envio": 1}
    trava = {p["numero"]: p for p in corpo["pedidos"]}["300102"]["motivo"]
    assert (trava["codigo"], trava["titulo"], trava["texto"]) == (
        "em_analise",
        "Em análise",
        "em análise pela equipe",
    )
    assert trava["etiqueta"] is False and trava["conflito"] is None
    # O filtro vale pelo código que a pessoa vê.
    r = await client.get(URL, params={"codigo": "margem_trava"})
    assert r.json()["pedidos"] == []
    r = await client.get(URL, params={"codigo": "em_analise"})
    assert [p["numero"] for p in r.json()["pedidos"]] == ["300102"]


async def test_lista_traz_a_troca_aberta(db, client, admin, make_user):
    dono = await make_user()
    await _cenario(db, dono)
    db.add(
        AtendimentoTroca(
            pedido_bling="300101",
            bling_id=300101,
            motivo_codigo="sem_estoque",
            sku_antigo="dg053.sp",
            sku_novo="dg054.sp",
            produto_novo_id=502,
            quantidade=1,
            nivel=1,
            estado="item_trocado",
            codigo_erro="bling_indisponivel",
            passos=[],
            idem_key=uuid4(),
            criado_por_nome="Fulana",
        )
    )
    await db.commit()
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    por = {p["numero"]: p for p in r.json()["pedidos"]}
    aberta = por["300101"]["motivo"]["troca_aberta"]
    assert (aberta["estado"], aberta["sku_novo"], aberta["pode_retomar"]) == (
        "item_trocado",
        "dg054.sp",
        True,
    )
    assert por["300102"]["motivo"]["troca_aberta"] is None


async def test_lista_com_as_sugestoes_de_troca(db, client, admin, make_user, _chaves, monkeypatch):
    monkeypatch.setattr(_chaves, "atendimento_troca_sugestoes_ativa", True)
    dono = await make_user()
    await _cenario(db, dono)

    r = await client.get(URL)
    assert r.status_code == 200, r.text
    assert r.json()["sugestoes_ativas"] is True
    por = {p["numero"]: p for p in r.json()["pedidos"]}
    st = por["300101"]["sugestoes_troca"]
    [item] = st["itens"]
    assert [(s["sku"], s["nivel"], s["dif_custo_pct"]) for s in item["sugestoes"]] == [
        ("dg054.sp", 1, 0.0)
    ]
    assert "Laranja" in item["texto_oferta"]
    # Só a falta de estoque recebe sugestões.
    assert por["300102"]["sugestoes_troca"] is None
    assert por["300103"]["sugestoes_troca"] is None


async def test_lista_sem_o_custo_para_quem_nao_ve_a_margem(
    db, client, make_user, auth_as, monkeypatch, _chaves
):
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    monkeypatch.setattr(_chaves, "atendimento_troca_sugestoes_ativa", True)
    u = await make_user(permissions={"atendimento": {"view": True}})
    auth_as(u)
    await _cenario(db, u)

    r = await client.get(URL, params={"codigo": "sem_estoque"})
    assert r.status_code == 200, r.text
    assert r.json()["ve_custo"] is False
    sug = r.json()["pedidos"][0]["sugestoes_troca"]["itens"][0]["sugestoes"][0]
    assert (sug["sku"], sug["dif_custo_pct"]) == ("dg054.sp", None)


async def test_lista_sugestao_quebrada_nao_derruba_a_lista(
    db, client, admin, make_user, monkeypatch, _chaves
):
    monkeypatch.setattr(_chaves, "atendimento_troca_sugestoes_ativa", True)
    dono = await make_user()
    await _cenario(db, dono)

    async def _quebra(session):
        raise RuntimeError("catálogo fora")

    monkeypatch.setattr(troca_sugestoes, "_ler_catalogo", _quebra)
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    por = {p["numero"]: p for p in r.json()["pedidos"]}
    assert por["300101"]["sugestoes_troca"]["falhou"] is True
    assert len(por) == 3


async def test_lista_le_o_catalogo_uma_vez_por_requisicao(
    db, client, admin, make_user, monkeypatch, _chaves
):
    """O catálogo é pesado: N pedidos sem estoque = 1 leitura, e a falha não se repete."""
    monkeypatch.setattr(_chaves, "atendimento_troca_sugestoes_ativa", True)
    dono = await make_user()
    await _catalogo_a17(db, dono)
    numeros = [f"30020{i}" for i in range(5)]
    for numero in numeros:
        await _pedido(db, numero)
        await _marca_nf(
            db, numero, "sem_estoque", "Aguardando Cancelamento — saldo negativo: dg053.sp"
        )

    leituras: list[int] = []
    original = troca_sugestoes._ler_catalogo

    async def _quebra(session):
        leituras.append(1)
        raise RuntimeError("catálogo fora")

    monkeypatch.setattr(troca_sugestoes, "_ler_catalogo", _quebra)
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    pedidos = r.json()["pedidos"]
    assert sorted(p["numero"] for p in pedidos) == numeros
    assert all(p["sugestoes_troca"]["falhou"] is True for p in pedidos)
    assert leituras == [1]

    # Lido com sucesso: também uma vez só, e todos recebem as sugestões.
    leituras.clear()
    troca_sugestoes.limpar_memoria()

    async def _contando(session):
        leituras.append(1)
        return await original(session)

    monkeypatch.setattr(troca_sugestoes, "_ler_catalogo", _contando)
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    pedidos = r.json()["pedidos"]
    assert len(pedidos) == 5
    assert all(
        [s["sku"] for s in p["sugestoes_troca"]["itens"][0]["sugestoes"]] == ["dg054.sp"]
        for p in pedidos
    )
    assert leituras == [1]
