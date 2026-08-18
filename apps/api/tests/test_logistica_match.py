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
    assert logistica_match.estado_resolvido([], "Entregue") is False
    assert logistica_match.estado_resolvido([curinga], None) is False


def test_estado_resolvido_regra_sem_acao_esconde():
    # Regra sem NENHUMA ação = chave conhecida/ok → esconde. Pra manter a chave
    # à vista o operador marca Monitorar; pra espiar os escondidos existe o
    # "Mostrar tudo" do painel (e "Problemas" fura tudo via problema_bling_visivel).
    vazia = _rule(status_plataforma="Pago | Entregue")
    assert logistica_match.estado_resolvido([vazia], "Entregue") is True
    assert logistica_match.estado_resolvido([vazia], None) is True  # nem depende do status
    # Só monitoramento (sem outra ação): resolvido, mas o front mantém via monitorar.
    so_mon = _rule(monitoramento=True)
    assert logistica_match.estado_resolvido([so_mon], "Entregue") is True
    assert logistica_match.deve_monitorar([so_mon], "Entregue") is True


def test_estado_resolvido_acao_manual_pendente_fica():
    # Mensagem/chamado/reembolso pendentes mantêm a linha visível (há trabalho).
    for kw in (
        {"mensagem_bling": "oi"},
        {"mensagem_chamado": "oi"},
        {"mensagem_threema": "oi"},
        {"abrir_chamado": True},
        {"abrir_reembolso": True},
    ):
        r = _rule(**kw)
        assert logistica_match.estado_resolvido([r], "Entregue") is False


def test_estado_resolvido_acao_de_outro_estado_nao_conta():
    # Caso real 287618: mesma chave com 2 regras. Uma pro estado "Em andamento"
    # (com chamado/reembolso/monitorar/mensagens), outra pro estado "Problemas"
    # (sem ação). O pedido está em "Problemas" → a regra de "Em andamento" NÃO se
    # aplica agora; a vazia aplicável esconde (quem mantém pedido com problema
    # no painel é o passe-livre problema_bling_visivel, aplicado por quem chama).
    acao = _rule(
        status_plataforma="Retido", status_atual="Em andamento",
        abrir_chamado=True, abrir_reembolso=True,
        mensagem_chamado="x", mensagem_threema="x", monitoramento=True,
    )
    sem = _rule(status_plataforma="Retido", status_atual="Problemas")
    assert logistica_match.estado_resolvido([acao, sem], "Problemas") is True
    assert logistica_match.deve_monitorar([acao, sem], "Problemas") is False
    # Mas QUANDO o pedido está em "Em andamento", a regra se aplica → fica visível.
    assert logistica_match.estado_resolvido([acao, sem], "Em andamento") is False
    assert logistica_match.deve_monitorar([acao, sem], "Em andamento") is True


def test_estado_resolvido_alvo_de_outro_estado_nao_bloqueia():
    # Caso real 287924: chave "Cancelado | Não entregue | Devolvido ao hub | Envio"
    # com 2 regras — (1) status_atual "Em andamento" → "Aguardando Devolução";
    # (2) status_atual "Problemas" → NÃO faz nada. O pedido está em "Problemas".
    # A regra aplicável (2) não pede nada → esconde, mesmo a regra (1) tendo um
    # alvo ainda não atingido (ela vale quando o pedido estiver no estado dela).
    r1 = _rule(status_atual="Em andamento", alterar_status_bling="Aguardando Devolução")
    r2 = _rule(status_atual="Problemas")  # sem ação
    assert logistica_match.estado_resolvido([r1, r2], "Problemas") is True
    assert logistica_match.deve_monitorar([r1, r2], "Problemas") is False
    # Em "Em andamento" a regra (1) se aplica e tem transição pendente → fica.
    assert logistica_match.estado_resolvido([r1, r2], "Em andamento") is False


def test_regra_ativa_desambigua_pelo_status_atual():
    # A regra MOSTRADA/aplicada tem que respeitar onde o pedido está (287924).
    r1 = _rule(status_atual="Em andamento", alterar_status_bling="Aguardando Devolução")
    r2 = _rule(status_atual="Problemas")  # sem ação
    assert logistica_match.regra_ativa([r1, r2], "Problemas") is r2
    assert logistica_match.regra_ativa([r1, r2], "Em andamento") is r1
    # Em "Problemas" a regra ativa (r2) não pede nada → sem setinha, resumo vazio.
    assert logistica_match.resumo_acoes(logistica_match.regra_ativa([r1, r2], "Problemas")) == []
    # Estado sem regra exata cai no curinga, senão na primeira.
    curinga = _rule(alterar_status_bling="Cancelado")
    assert logistica_match.regra_ativa([r1, curinga], "Entregue") is curinga
    assert logistica_match.regra_ativa([r1, r2], "Entregue") is r1
    assert logistica_match.regra_ativa([], "Problemas") is None


def test_estado_resolvido_threema_enviado_resolve():
    # Regra só com Mensagem Threema: pendente até enviar; depois de enviado
    # (threema_enviado=True) deixa de contar → resolvido (some).
    so_threema = _rule(status_plataforma="x", mensagem_threema="avisar")
    assert logistica_match.estado_resolvido([so_threema], "Entregue") is False
    assert (
        logistica_match.estado_resolvido([so_threema], "Entregue", threema_enviado=True)
        is True
    )
    # Se ainda houver OUTRA ação (ex. mensagem_bling), enviar o Threema não
    # resolve sozinho — segue visível.
    misto = _rule(mensagem_threema="avisar", mensagem_bling="colar no bling")
    assert (
        logistica_match.estado_resolvido([misto], "Entregue", threema_enviado=True)
        is False
    )


def test_problema_bling_visivel_janela_360():
    # Pedido em "Problemas" no Bling ganha passe-livre no painel por 360 dias:
    # ignora o resolvido das regras. Fora da janela (ou outro status), não.
    from datetime import date, timedelta

    hoje = date.today()
    assert logistica_match.problema_bling_visivel("Problemas", hoje) is True
    assert (
        logistica_match.problema_bling_visivel(" problemas ", hoje - timedelta(days=359))
        is True
    )
    assert (
        logistica_match.problema_bling_visivel("Problemas", hoje - timedelta(days=361))
        is False
    )
    # Sem data = mostra (melhor sobrar que esconder um problema).
    assert logistica_match.problema_bling_visivel("Problemas", None) is True
    assert logistica_match.problema_bling_visivel("Atendido", hoje) is False
    assert logistica_match.problema_bling_visivel(None, hoje) is False


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
