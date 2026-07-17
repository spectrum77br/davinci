"""Casador da aba Status — liga cada pedido da Logística à regra que casa.

`find_matching_rule` acha a linha da aba Status cuja chave (`status_plataforma`)
bate com a assinatura PT do pedido (prefere a regra específica da plataforma,
cai na geral); `resumo_acoes` resume o que o sistema faria. O endpoint
`GET /api/logistica` devolve `acao_match`/`acao_status_id`/`acao_resumo` por
pedido.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LogisticaStatus, User, UserRole, UserStatus
from app.services import logistica_match, logistica_rules


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---- find_matching_rule (sem DB) ----


def _rule(**kw) -> LogisticaStatus:
    return LogisticaStatus(**kw)


def test_match_por_chave_normalizada():
    rows = [_rule(status_plataforma="Pago | Entregue", alterar_status_bling="Em andamento")]
    r = logistica_match.find_matching_rule(rows, assinatura="  pago | entregue ", plataforma=None)
    assert r is rows[0]


def test_match_prefere_especifica_sobre_geral():
    geral = _rule(status_plataforma="Pago | Entregue", plataforma=None)
    espec = _rule(status_plataforma="Pago | Entregue", plataforma="Mercado Livre")
    r = logistica_match.find_matching_rule(
        [geral, espec], assinatura="Pago | Entregue", plataforma="Mercado Livre"
    )
    assert r is espec


def test_find_matching_rules_devolve_todas_da_chave():
    # Máquina de estados: mesma chave, duas transições distintas.
    a = _rule(status_plataforma="Pago | Entregue", status_atual="Enviado Etiqueta",
              alterar_status_bling="Em andamento")
    b = _rule(status_plataforma="Pago | Entregue", status_atual="Em andamento",
              alterar_status_bling="Entregue")
    outra = _rule(status_plataforma="Pago | Cancelado", alterar_status_bling="Cancelado")
    got = logistica_match.find_matching_rules(
        [a, b, outra], assinatura="pago | entregue", plataforma=None
    )
    assert got == [a, b]


def test_find_matching_rules_prefere_especificas():
    geral = _rule(status_plataforma="Pago | Entregue", plataforma=None,
                  alterar_status_bling="Entregue")
    espec = _rule(status_plataforma="Pago | Entregue", plataforma="Mercado Livre",
                  alterar_status_bling="Em andamento")
    got = logistica_match.find_matching_rules(
        [geral, espec], assinatura="Pago | Entregue", plataforma="Mercado Livre"
    )
    assert got == [espec]  # havendo específica, ignora a geral


def test_find_matching_rules_vazio_sem_assinatura():
    a = _rule(status_plataforma="Pago | Entregue", alterar_status_bling="Entregue")
    assert logistica_match.find_matching_rules([a], assinatura="", plataforma=None) == []


def test_match_cai_na_geral_quando_plataforma_nao_bate():
    geral = _rule(status_plataforma="Pago | Entregue", plataforma=None)
    outra = _rule(status_plataforma="Pago | Entregue", plataforma="Shopee")
    r = logistica_match.find_matching_rule(
        [outra, geral], assinatura="Pago | Entregue", plataforma="Mercado Livre"
    )
    assert r is geral


def test_match_sem_regra_e_assinatura_vazia():
    rows = [_rule(status_plataforma="Pago | Enviado")]
    assert logistica_match.find_matching_rule(rows, assinatura="Pago | Entregue", plataforma=None) is None
    assert logistica_match.find_matching_rule(rows, assinatura="", plataforma=None) is None
    assert logistica_match.find_matching_rule(rows, assinatura=None, plataforma=None) is None


# ---- resumo_acoes (sem DB) ----


def test_resumo_lista_so_acoes_preenchidas():
    r = _rule(
        alterar_status_bling="Em andamento",
        monitoramento=True,
        abrir_chamado=True,
        abrir_reembolso=False,
        mensagem_chamado="oi",
        mensagem_bling="",
        mensagem_threema="avisar",
    )
    assert logistica_match.resumo_acoes(r) == [
        "Status Bling → Em andamento",
        "Monitorar",
        "Abrir chamado",
        "Mensagem do chamado",
        "Mensagem Threema",
    ]


def test_resumo_vazio_para_regra_sem_acao_e_none():
    vazia = _rule(status_plataforma="Pago | Entregue")
    assert logistica_match.resumo_acoes(vazia) == []
    assert logistica_match.resumo_acoes(None) == []


# ---- deve_monitorar / estado_resolvido (sem DB) ----


def test_deve_monitorar_qualquer_regra_com_monitoramento():
    a = _rule(alterar_status_bling="Entregue", monitoramento=False)
    b = _rule(alterar_status_bling="Em andamento", monitoramento=True)
    assert logistica_match.deve_monitorar([a, b]) is True
    assert logistica_match.deve_monitorar([a]) is False
    assert logistica_match.deve_monitorar([]) is False


def test_estado_resolvido_no_alvo_final():
    # Cadeia: Enviado Etiqueta→Em andamento→Entregue. No alvo final "Entregue"
    # não há mais transição partindo dele → resolvido.
    a = _rule(status_atual="Enviado Etiqueta", alterar_status_bling="Em andamento")
    b = _rule(status_atual="Em andamento", alterar_status_bling="Entregue")
    assert logistica_match.estado_resolvido([a, b], "Entregue") is True
    # No meio da cadeia (Em andamento é alvo de A mas ainda parte de B) → não.
    assert logistica_match.estado_resolvido([a, b], "Em andamento") is False


def test_estado_resolvido_curinga_e_casos_negativos():
    curinga = _rule(alterar_status_bling="Cancelado")  # sem status_atual = vale de qualquer estado
    assert logistica_match.estado_resolvido([curinga], "Cancelado") is True
    assert logistica_match.estado_resolvido([curinga], "Em aberto") is False
    # Regra só com mensagem/monitorar (sem alterar_status_bling) nunca resolve.
    so_msg = _rule(monitoramento=True, mensagem_bling="oi")
    assert logistica_match.estado_resolvido([so_msg], "Entregue") is False
    assert logistica_match.estado_resolvido([], "Entregue") is False
    assert logistica_match.estado_resolvido([curinga], None) is False


# ---- endpoint GET /api/logistica ----


@pytest.mark.asyncio
async def test_list_traz_casador(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "delivered"}
    chave = logistica_rules.assinatura_pt(meli)
    assert chave  # "Pago | Entregue"

    # Regra na aba Status que casa com a chave, com um par de ações.
    rs = await client.post(
        "/api/logistica/status",
        json={
            "status_plataforma": chave,
            "alterar_status_bling": "Em andamento",
            "abrir_chamado": True,
        },
    )
    assert rs.status_code == 201, rs.text
    status_id = rs.json()["id"]

    # Pedido ML com a mesma assinatura.
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text

    r = await client.get("/api/logistica?plataforma=ml")
    assert r.status_code == 200, r.text
    linha = next(x for x in r.json() if x["status_plataforma"] == chave)
    assert linha["acao_match"] is True
    assert linha["acao_status_id"] == status_id
    assert linha["acao_resumo"] == ["Status Bling → Em andamento", "Abrir chamado"]


@pytest.mark.asyncio
async def test_list_sem_regra_acao_match_false(
    client: AsyncClient, admin: User, auth_as: Callable[[User | None], None]
):
    auth_as(admin)
    meli = {"order_status": "paid", "ship_status": "shipped"}
    rc = await client.post(
        "/api/logistica",
        json={"plataforma": "Mercado Livre", "meli_status": meli},
    )
    assert rc.status_code == 201, rc.text
    chave = logistica_rules.assinatura_pt(meli)

    r = await client.get("/api/logistica?plataforma=ml")
    assert r.status_code == 200, r.text
    linha = next(x for x in r.json() if x["status_plataforma"] == chave)
    assert linha["acao_match"] is False
    assert linha["acao_status_id"] is None
    assert linha["acao_resumo"] == []
