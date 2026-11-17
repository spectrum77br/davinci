"""Regra do Flex olha a coluna Tracken (Vinicius, 17/11/2026, Shopee Flex 309413).

A regra "Shopee Flex · Processado · Em digitação · Localização contém coletado
→ Em andamento" não casava: olhava a Localização da Shopee (que a aba Flex nem
mostra). O "COLETADO" estava no Tracken, a transportadora do Flex. E mesmo
casando, a troca para Em andamento seria barrada porque a Shopee segue
"Processado" depois da coleta.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Logistica,
    LogisticaStatus,
    LogisticaTracken,
    SituacaoBling,
    User,
    UserRole,
    UserStatus,
)
from app.services import logistica_bling, logistica_match, logistica_rules, tracken

_COLETADO = {
    "codpedido": "637300001",
    "ped_codpedido": 637300001,
    "ped_notafiscal": "251117ABCDEF",
    "ped_pedidonf": "BR269481480001U",
    "sts_nome": "MANUSEIO",
    "statushsp": "COLETADO",
    "historico": [
        {"hsp_dtstatus": "2026-11-17", "hsp_hrstatus": "08:10:00", "deschistorico": "ARQUIVO IMPORTADO"},
        {"hsp_dtstatus": "2026-11-17", "hsp_hrstatus": "10:32:00", "deschistorico": "COLETADO"},
    ],
}


# ---- sem banco ----


def test_localizacao_da_linha_no_flex_e_o_tracken():
    flex = Logistica(
        plataforma="Shopee",
        envio_flex=True,
        localizacao="Pedido em preparação",
        localizacao_tracken="MANUSEIO · COLETADO",
    )
    comum = Logistica(
        plataforma="Shopee",
        envio_flex=False,
        localizacao="Pedido em preparação",
        localizacao_tracken="MANUSEIO · COLETADO",
    )
    nao_lido = Logistica(plataforma="Shopee", envio_flex=None, localizacao="Em trânsito")
    assert logistica_match.localizacao_da_linha(flex) == "MANUSEIO · COLETADO"
    assert logistica_match.localizacao_da_linha(comum) == "Pedido em preparação"
    assert logistica_match.localizacao_da_linha(nao_lido) == "Em trânsito"
    # Flex sem leitura do Tracken: nada para a condição casar (a da Shopee não vale).
    sem_tracken = Logistica(plataforma="Shopee", envio_flex=True, localizacao="coletado")
    assert logistica_match.localizacao_da_linha(sem_tracken) is None


def test_localizacao_para_regra_e_status_mais_ultimo_evento():
    tk = LogisticaTracken(achado=True, status="MANUSEIO", ultimo_evento="COLETADO")
    assert tracken.localizacao_para_regra(tk) == "MANUSEIO · COLETADO"
    tk = LogisticaTracken(achado=True, status="ENTREGUE", ultimo_evento="  ")
    assert tracken.localizacao_para_regra(tk) == "ENTREGUE"
    # A transportadora ainda não tem o pedido.
    assert tracken.localizacao_para_regra(LogisticaTracken(achado=False, status="X")) is None
    assert tracken.localizacao_para_regra(None) is None


def test_regra_coletado_casa_pelo_tracken_so_no_flex():
    normal = LogisticaStatus(
        plataforma="Shopee Flex", status_plataforma="Processado", status_atual="Em digitação"
    )
    coletado = LogisticaStatus(
        plataforma="Shopee Flex",
        status_plataforma="Processado",
        status_atual="Em digitação",
        localizacao_contem="coletado",
        alterar_status_bling="Em andamento",
        monitoramento=True,
    )
    regras = [normal, coletado]

    def casa(row: Logistica) -> list[LogisticaStatus]:
        return logistica_match.find_matching_rules(
            regras,
            assinatura="Processado",
            plataforma=logistica_match.plataforma_regra_da_linha(row),
            localizacao=logistica_match.localizacao_da_linha(row),
        )

    antes = Logistica(plataforma="Shopee", envio_flex=True, localizacao="Pedido em preparação")
    assert casa(antes) == [normal]
    depois = Logistica(
        plataforma="Shopee",
        envio_flex=True,
        localizacao="Pedido em preparação",
        localizacao_tracken="MANUSEIO · COLETADO",
    )
    assert casa(depois) == [coletado, normal]
    assert logistica_match.regra_ativa(casa(depois), "Em digitação") is coletado


def test_transportadora_flex_confirmou():
    conf = logistica_bling.transportadora_flex_confirmou
    flex = Logistica(envio_flex=True, localizacao_tracken="MANUSEIO · COLETADO")
    assert conf(flex, regra_com_condicao=True) is True
    # Regra sem condição: a palavra do operador não está em jogo.
    assert conf(flex, regra_com_condicao=False) is False
    # Fora do Flex nada muda.
    comum = Logistica(envio_flex=False, localizacao_tracken="MANUSEIO · COLETADO")
    assert conf(comum, regra_com_condicao=True) is False
    assert conf(Logistica(envio_flex=True), regra_com_condicao=True) is False


# ---- com banco: o caso 309413 de ponta a ponta ----


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


class _FakeBling:
    def __init__(self, orders: dict[int, dict]):
        self._orders = orders
        self.situacao_sets: list[tuple[int, int]] = []

    async def get_order(self, bling_id: int) -> dict:
        return self._orders[bling_id]

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        self.situacao_sets.append((bling_id, situacao_id))
        self._orders[bling_id]["situacao"]["id"] = situacao_id


@pytest.mark.asyncio
async def test_309413_tracken_coletado_leva_a_em_andamento(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    auth_as(admin)
    meli = {"order_status": "PROCESSED"}
    chave = logistica_rules.assinatura_para("Shopee", meli)
    assert chave == "Processado"
    db.add_all([SituacaoBling(id=21, nome="Em digitação"), SituacaoBling(id=15, nome="Em andamento")])
    await db.commit()
    for plataforma in ("Shopee Flex", "Shopee"):
        r = await client.post(
            "/api/logistica/status",
            json={"plataforma": plataforma, "status_plataforma": chave, "status_atual": ["Em digitação"]},
        )
        assert r.status_code == 201, r.text
        r = await client.post(
            "/api/logistica/status",
            json={
                "plataforma": plataforma,
                "status_plataforma": chave,
                "status_atual": ["Em digitação"],
                "localizacao_contem": "coletado",
                "alterar_status_bling": "Em andamento",
                "monitoramento": True,
            },
        )
        assert r.status_code == 201, r.text
        if plataforma == "Shopee Flex":
            regra_coletado = r.json()["id"]

    flex = Logistica(
        plataforma="Shopee",
        pedido_bling="309413",
        pedido_marketplace="251117ABCDEF",
        rastreio="BR269481480001U",
        envio_flex=True,
        data=date.today(),
        meli_status=meli,
        status_bling="Em digitação",
        localizacao="Pedido em preparação",
    )
    # Shopee comum com "coletado" na Localização: a trava continua valendo.
    comum = Logistica(
        plataforma="Shopee",
        pedido_bling="309414",
        envio_flex=False,
        data=date.today(),
        meli_status=meli,
        status_bling="Em digitação",
        localizacao="Pedido coletado pela transportadora",
    )
    db.add_all([flex, comum])
    db.add_all(
        [
            BlingOrder(bling_id=9413, numero="309413", item_index=0, situacao="21", cep_destino="03977012"),
            BlingOrder(bling_id=9414, numero="309414", item_index=0, situacao="21"),
        ]
    )
    await db.commit()
    await db.refresh(flex)
    await db.refresh(comum)

    # Antes do Tracken: a regra normal (sem ação) — e a linha mostra o rastreio.
    linha = next(x for x in (await client.get("/api/logistica?envio=flex")).json() if x["id"] == str(flex.id))
    assert linha["acao_status_id"] != regra_coletado
    assert linha["rastreio"] == "BR269481480001U"

    # A tela lê o Tracken: MANUSEIO · COLETADO.
    r = await client.post(
        "/api/logistica/tracken/resultado",
        json={"itens": [{"logistica_id": str(flex.id), "lista": [_COLETADO]}]},
    )
    assert r.status_code == 200, r.text
    await db.refresh(flex)
    assert flex.localizacao_tracken == "MANUSEIO · COLETADO"

    linha = next(x for x in (await client.get("/api/logistica?envio=flex")).json() if x["id"] == str(flex.id))
    assert linha["acao_status_id"] == regra_coletado
    assert linha["acao_monitorar"] is True
    assert linha["acao_resolvido"] is False

    fake = _FakeBling(
        {
            9413: {"id": 9413, "numero": 309413, "situacao": {"id": 21, "valor": 0}},
            9414: {"id": 9414, "numero": 309414, "situacao": {"id": 21, "valor": 0}},
        }
    )

    async def _fake_client(session):
        return fake

    monkeypatch.setattr(logistica_bling, "_bling_client", _fake_client)
    await logistica_bling.aplicar_status_em_lote(db, [flex.id, comum.id])
    # O Flex foi para Em andamento (a Shopee ainda diz Processado); o comum não.
    assert fake.situacao_sets == [(9413, 15)]
    await db.refresh(flex)
    assert flex.status_bling == "Em andamento"


@pytest.mark.asyncio
async def test_tracken_nao_achou_agora_mantem_a_localizacao(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    auth_as(admin)
    flex = Logistica(
        plataforma="Shopee",
        pedido_bling="309500",
        pedido_marketplace="2511NAOACHOU",
        envio_flex=True,
        data=date.today(),
        meli_status={"order_status": "PROCESSED"},
    )
    db.add(flex)
    await db.commit()
    await db.refresh(flex)

    async def ler(item: dict) -> None:
        r = await client.post("/api/logistica/tracken/resultado", json={"itens": [item]})
        assert r.status_code == 200, r.text
        await db.refresh(flex)

    await ler({"logistica_id": str(flex.id), "lista": []})
    assert flex.localizacao_tracken is None  # a transportadora ainda não tem
    await ler({"logistica_id": str(flex.id), "lista": [_COLETADO]})
    assert flex.localizacao_tracken == "MANUSEIO · COLETADO"
    # Leitura que falhou ou não achou desta vez: o que se sabia fica.
    await ler({"logistica_id": str(flex.id), "erro": "o Tracken respondeu 500"})
    assert flex.localizacao_tracken == "MANUSEIO · COLETADO"
    await ler({"logistica_id": str(flex.id), "lista": []})
    assert flex.localizacao_tracken == "MANUSEIO · COLETADO"
