"""Item 4, fase 4b — a lista "Ag. cancelamento" (GET /api/atendimento/ag-cancelamento, 05/10/2026).

O router `atendimento_troca` (à parte do painel, para não mexer no teste que
lista as rotas dele). O que estes testes seguram:

- a MESMA trava de acesso da caixa (`_so_admin`) em todas as rotas do
  router novo, registrado pelo `app.main` — desde 07/10/2026 a equipe LÊ
  (a lista e as trocas) e só quem mexe escreve; as rotas de escrita da troca
  (fase 4c) pedem também `atendimento.edit` e `margem.edit`;
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
from uuid import uuid4

import pytest
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
    UserRole,
)
from app.routers import atendimento as rota
from app.routers import atendimento_troca
from app.security.cipher import encrypt_json
from app.services.atendimento import gravar, painel, troca_sugestoes

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


# Escrever (decisão (g) do dono): a troca, o retomar e a OFERTA — que promete a
# troca ao comprador — pedem o mesmo.
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
    for r in rotas:
        deps = [d.call for d in r.dependant.dependencies]
        assert rota._so_admin in deps, r.path
        if (r.path, tuple(sorted(r.methods))) in ESCRITAS:
            # Escrever: quem mexe na caixa + atendimento.edit + margem.edit
            # (a troca aprova a Margem; a oferta promete a troca).
            assert atendimento_troca._margem_edit in deps, r.path
            assert rota._edit in deps, r.path
        else:
            assert rota._view in deps, r.path


async def test_quem_so_le_ve_a_lista_mas_nao_troca(db, client, make_user, auth_as):
    """07/10/2026 (main 25d84db6): a caixa abriu para a equipe em só leitura."""
    u = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(u)
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/atendimento/pedidos/300101/troca/previa",
        json={"sku_antigo": "dg053.sp", "sku_novo": "dg054.sp"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "atendimento_so_leitura"


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
