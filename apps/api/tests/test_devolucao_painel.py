"""Painel novo de Devoluções (Vinicius, 16/11/2026).

- contas puras da tela (services/devolucao_painel): etapa, condição mostrada,
  prazo da etapa, conta do reembolso, "pronto pra resolver";
- GET /api/devolucoes-painel: entra quem caiu em Aguardando Devolução e fica
  até o Resolver, em qualquer situação do Bling;
- POST …/resolver e …/reabrir; reentrada em devolução reabre;
- custo e conta só pra admin; resumo por mês a partir da virada.

O painel antigo (/api/devolutions/acompanhamento) tem os testes dele e não
pode mudar — a lista nova reusa a mesma leitura com outra fonte.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from app.models import Chamado, DevolucaoAnexo, Devolution, UserRole
from app.routers.devolutions import PAINEL_LARGADA
from app.services import devolucao_painel as contas
from app.services.devolucao_painel import Lancamento
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SP = ZoneInfo("America/Sao_Paulo")


def _perm(*, edit: bool = True) -> dict:
    return {"devolucoes": {"view": True, "edit": edit, "delete": False}}


# ── Contas puras ─────────────────────────────────────────────────────────


async def test_condicao_da_tela_so_novo_usado_perdimento() -> None:
    assert contas.condicao_da_tela(Lancamento(sku="a", condicao="Novo")) == "Novo"
    assert contas.condicao_da_tela(Lancamento(sku="a", condicao="Sucata")) == "Perdimento"
    # Manutenção com o técnico é etapa, não condição; voltou → vale o destino.
    assert contas.condicao_da_tela(Lancamento(sku="a", condicao="Manutenção")) is None
    assert (
        contas.condicao_da_tela(
            Lancamento(sku="a", condicao="Manutenção", manutencao_destino="Usado")
        )
        == "Usado"
    )
    assert (
        contas.condicao_da_tela(
            Lancamento(sku="a", condicao="Manutenção", manutencao_destino="Sucata")
        )
        == "Perdimento"
    )
    assert (
        contas.condicao_da_tela(Lancamento(sku="a", condicao="Trocado", troca_condicao="Usado"))
        == "Usado"
    )
    # Pacote que não chegou segue com o nome que tem (o Bling distingue).
    assert contas.condicao_da_tela(Lancamento(sku="a", condicao="Extraviado")) == "Extraviado"
    assert contas.condicao_da_tela(Lancamento(sku="a", condicao=None)) is None


async def test_etapa_manutencao_ganha_de_fraude_e_de_chegou() -> None:
    com_tecnico = [Lancamento(sku="a", condicao="Manutenção")]
    assert (
        contas.etapa(fila="fraude", situacao_bling="83957", chegou_em=None, lancamentos=com_tecnico)
        == "manutencao"
    )
    assert (
        contas.etapa(fila="acompanhamento", situacao_bling="84677", chegou_em=None, lancamentos=[])
        == "manutencao"
    )
    assert (
        contas.etapa(fila="fraude", situacao_bling="83957", chegou_em=None, lancamentos=[])
        == "fraude"
    )
    assert (
        contas.etapa(
            fila="acompanhamento",
            situacao_bling="83957",
            chegou_em=date(2026, 11, 14),
            lancamentos=[],
        )
        == "chegou"
    )
    # Lançamento de produto recebido prova a chegada; "Não devolvido" não.
    assert (
        contas.etapa(
            fila="acompanhamento",
            situacao_bling="83957",
            chegou_em=None,
            lancamentos=[Lancamento(sku="a", condicao="Usado")],
        )
        == "chegou"
    )
    assert (
        contas.etapa(
            fila="acompanhamento",
            situacao_bling="83957",
            chegou_em=None,
            lancamentos=[Lancamento(sku="a", condicao="Não devolvido")],
        )
        == "aguardando"
    )


async def test_falta_lancar_so_quando_chegou_sem_lancamento() -> None:
    assert contas.falta_lancar("chegou", []) is True
    assert contas.falta_lancar("chegou", [Lancamento(sku="a", condicao="Novo")]) is False
    assert contas.falta_lancar("aguardando", []) is False


async def test_prazo_da_etapa_pega_o_mais_proximo() -> None:
    agora = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    # Fraude com disputa vencendo em 3 h: urgente, e a palavra é "disputa".
    p = contas.prazo_da_etapa(
        etapa_="fraude",
        agora=agora,
        prazo_resposta=agora + timedelta(hours=3),
        acao_resposta="responder ao reembolso",
        prazo_45_ate=None,
        prazo_45_estado=None,
        amazon_cancelar=False,
        lancamentos=[],
    )
    assert (p["prazo_tipo"], p["prazo_estado"]) == ("disputa", "urgente")
    assert p["prazo_dica"] == "responder ao reembolso"
    # Aguardando: 45 dias longe = sem estado; nos 5 dias finais = urgente.
    longe = contas.prazo_da_etapa(
        etapa_="aguardando",
        agora=agora,
        prazo_resposta=None,
        acao_resposta=None,
        prazo_45_ate=date(2026, 12, 22),
        prazo_45_estado=None,
        amazon_cancelar=False,
        lancamentos=[],
    )
    assert (longe["prazo_tipo"], longe["prazo_estado"]) == ("pacote chegar", None)
    perto = contas.prazo_da_etapa(
        etapa_="aguardando",
        agora=agora,
        prazo_resposta=None,
        acao_resposta=None,
        prazo_45_ate=date(2026, 11, 19),
        prazo_45_estado="vence_logo",
        amazon_cancelar=False,
        lancamentos=[],
    )
    assert perto["prazo_estado"] == "urgente"
    # No dia 45 a régua já diz "passou do prazo", mesmo antes da meia-noite.
    venceu = contas.prazo_da_etapa(
        etapa_="aguardando",
        agora=agora,
        prazo_resposta=None,
        acao_resposta=None,
        prazo_45_ate=date(2026, 11, 16),
        prazo_45_estado="vencido",
        amazon_cancelar=False,
        lancamentos=[],
    )
    assert venceu["prazo_estado"] == "vencido"
    # Manutenção: o prazo do técnico; vencido quando já passou.
    tecnico = contas.prazo_da_etapa(
        etapa_="manutencao",
        agora=agora,
        prazo_resposta=None,
        acao_resposta=None,
        prazo_45_ate=None,
        prazo_45_estado=None,
        amazon_cancelar=False,
        lancamentos=[
            Lancamento(sku="a", condicao="Manutenção", prazo_manutencao=agora - timedelta(days=1))
        ],
    )
    assert (tecnico["prazo_tipo"], tecnico["prazo_estado"]) == ("técnico", "vencido")
    # Entre dois prazos correndo, vale o que vence antes.
    dois = contas.prazo_da_etapa(
        etapa_="chegou",
        agora=agora,
        prazo_resposta=agora + timedelta(days=3),
        acao_resposta=None,
        prazo_45_ate=None,
        prazo_45_estado=None,
        amazon_cancelar=False,
        lancamentos=[
            Lancamento(sku="a", condicao="Usado", prazo_contestacao=agora + timedelta(days=1))
        ],
    )
    assert dois["prazo_tipo"] == "contestar"
    # Alarme falso que NÃO pode aparecer (revisão de 16/11):
    comum = dict(  # noqa: C408
        agora=agora,
        acao_resposta=None,
        prazo_45_ate=None,
        prazo_45_estado=None,
        amazon_cancelar=False,
    )
    # …contestação depois que o motivo foi escolhido (a contestação já saiu);
    ja_contestou = contas.prazo_da_etapa(
        etapa_="chegou",
        prazo_resposta=None,
        lancamentos=[
            Lancamento(
                sku="a",
                condicao="Usado",
                motivo="Golpe",
                prazo_contestacao=agora + timedelta(hours=2),
            )
        ],
        **comum,
    )
    assert ja_contestou["prazo_tipo"] is None
    # …ou com chamado no pedido;
    com_chamado = contas.prazo_da_etapa(
        etapa_="chegou",
        prazo_resposta=None,
        tem_chamado=True,
        lancamentos=[
            Lancamento(sku="a", condicao="Usado", prazo_contestacao=agora + timedelta(hours=2))
        ],
        **comum,
    )
    assert com_chamado["prazo_tipo"] is None
    # …prazo de resposta com o pedido fora de Aguardando Devolução (congelado:
    # o robô só relê a plataforma de quem está lá);
    congelado = contas.prazo_da_etapa(
        etapa_="manutencao",
        prazo_resposta=agora + timedelta(hours=2),
        lancamentos=[],
        situacao_bling="84677",
        **comum,
    )
    assert congelado["prazo_tipo"] is None
    # …e prazo vencido há mais de 2 dias (a plataforma já decidiu sozinha).
    velho = contas.prazo_da_etapa(
        etapa_="fraude", prazo_resposta=agora - timedelta(days=3), lancamentos=[], **comum
    )
    assert velho["prazo_tipo"] is None
    recente = contas.prazo_da_etapa(
        etapa_="fraude", prazo_resposta=agora - timedelta(hours=5), lancamentos=[], **comum
    )
    assert (recente["prazo_tipo"], recente["prazo_estado"]) == ("disputa", "vencido")
    # Nada correndo.
    nada = contas.prazo_da_etapa(
        etapa_="chegou",
        agora=agora,
        prazo_resposta=None,
        acao_resposta=None,
        prazo_45_ate=None,
        prazo_45_estado=None,
        amazon_cancelar=False,
        lancamentos=[],
    )
    assert nada["prazo_tipo"] is None and nada["prazo_em"] is None
    amazon = contas.prazo_da_etapa(
        etapa_="aguardando",
        agora=agora,
        prazo_resposta=None,
        acao_resposta=None,
        prazo_45_ate=None,
        prazo_45_estado=None,
        amazon_cancelar=True,
        lancamentos=[],
    )
    assert (amazon["prazo_tipo"], amazon["prazo_estado"]) == ("cancelar na Amazon", "urgente")


async def test_conta_do_reembolso_exemplo_do_vinicius_700_menos_100() -> None:
    """ "o chamado ganhar 700 reais mas ter um custo de manutenção de 100 então
    ficaria 600" """
    conta = contas.conta_do_reembolso(
        lancamentos=[Lancamento(sku="a", condicao="Manutenção", custo_manutencao=100)],
        chamado_resolvido=True,
        chamado_valor=700,
    )
    assert conta == {
        "recebido": 700.0,
        "custo_produto": 0.0,
        "custo_manutencao": 100.0,
        "resultado": 600.0,
    }


async def test_conta_do_reembolso_nao_dobra_o_prejuizo() -> None:
    # Perdimento com o chamado ainda aberto: só o custo do produto.
    perd = [Lancamento(sku="a", condicao="Sucata", custo_produto=612)]
    assert (
        contas.conta_do_reembolso(lancamentos=perd, chamado_resolvido=False, chamado_valor=None)[
            "resultado"
        ]
        == -612.0
    )
    # Chamado concluído com PREJUÍZO −612: recebido conta 0 (o prejuízo do
    # chamado já é o produto perdido) — fica −612, não −1.224.
    conta = contas.conta_do_reembolso(lancamentos=perd, chamado_resolvido=True, chamado_valor=-612)
    assert conta["recebido"] == 0.0 and conta["resultado"] == -612.0
    # Custo do produto só conta em Perdimento/Extraviado (Novo voltou inteiro).
    assert (
        contas.conta_do_reembolso(
            lancamentos=[Lancamento(sku="a", condicao="Novo", custo_produto=612)],
            chamado_resolvido=False,
            chamado_valor=None,
        )
        is None
    )
    # Chamado aberto com valor sugerido não entra.
    assert (
        contas.conta_do_reembolso(lancamentos=[], chamado_resolvido=False, chamado_valor=700)
        is None
    )


async def test_pronto_pra_resolver() -> None:
    usado_ok = [Lancamento(sku="a", condicao="Usado", estoque_status="ok")]
    base = {"tem_chamado_aberto": False, "chamado_resolvido": False, "chamado_valor": None}
    # Bling já saiu de Aguardando Devolução / Manutenção.
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="545902", situacao_nome="Resolvido", lancamentos=[], **base
        )
        == "o Bling já está em Resolvido"
    )
    # Lançada com estoque confirmado.
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="83957",
            situacao_nome="Aguardando Devolução",
            lancamentos=usado_ok,
            **base,
        )
        == "lançada: Usado"
    )
    # Estoque que não entrou no Bling segura.
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="83957",
            situacao_nome=None,
            lancamentos=[Lancamento(sku="a", condicao="Usado", estoque_status="falhou")],
            **base,
        )
        is None
    )
    # Com o técnico segura (inclusive com o Bling em Manutenção).
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="84677",
            situacao_nome="Manutenção",
            lancamentos=[Lancamento(sku="a", condicao="Manutenção")],
            **base,
        )
        is None
    )
    # Chamado aberto segura tudo: falta a plataforma responder.
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="545902",
            situacao_nome="Resolvido",
            lancamentos=usado_ok,
            tem_chamado_aberto=True,
            chamado_resolvido=False,
            chamado_valor=None,
        )
        is None
    )
    # …mesmo com o chamado já ganho ou o Bling já em outra situação.
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="84677",
            situacao_nome="Manutenção",
            lancamentos=[Lancamento(sku="a", condicao="Manutenção")],
            tem_chamado_aberto=False,
            chamado_resolvido=True,
            chamado_valor=700,
        )
        is None
    )
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="545902",
            situacao_nome="Resolvido",
            lancamentos=[Lancamento(sku="a", condicao="Novo", estoque_status="falhou")],
            **base,
        )
        is None
    )
    # "ou ganhar reembolso": chamado concluído com valor.
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="83957",
            situacao_nome=None,
            lancamentos=[],
            tem_chamado_aberto=False,
            chamado_resolvido=True,
            chamado_valor=389,
        )
        == "chamado concluído com valor ganho"
    )
    assert (
        contas.pronto_pra_resolver(
            situacao_bling="83957", situacao_nome=None, lancamentos=[], **base
        )
        is None
    )


async def test_lancamento_da_linha() -> None:
    mala = Lancamento(sku="m2-24.pt", condicao="Usado")
    kit = Lancamento(sku="kit2rod", condicao="Novo")
    assert contas.lancamento_da_linha("M2-24.PT", [kit, mala], linhas_do_pedido=2) == (mala, 0)
    # Pedido de 2 produtos: a linha sem lançamento do seu SKU fica vazia.
    assert contas.lancamento_da_linha("outro", [kit, mala], linhas_do_pedido=2) == (None, 0)
    # Kit que virou peças no lançamento: 1 linha no pedido mostra a 1ª e conta o resto.
    assert contas.lancamento_da_linha("kit6", [kit, mala], linhas_do_pedido=1) == (kit, 1)


# ── Rotas ────────────────────────────────────────────────────────────────


async def _pedido(
    db: AsyncSession,
    numero: str,
    situacao: str,
    *,
    entrada: date | None,
    sku: str = "a001.sp",
    itens: int = 1,
) -> None:
    for i in range(itens):
        await db.execute(
            text(
                """
                INSERT INTO bling_orders
                    (id, numero, numeroloja, situacao, aguardando_devolucao_data,
                     item_codigo, item_descricao, item_quantidade)
                VALUES (:id, :numero, :mkt, :situacao, :entrada, :sku, :produto, 1)
                """
            ),
            {
                "id": uuid4(),
                "numero": numero,
                "mkt": f"MKT-{numero}",
                "situacao": situacao,
                "entrada": entrada,
                "sku": sku if i == 0 else f"{sku}-{i}",
                "produto": f"Produto {numero}-{i}",
            },
        )


async def _resolvido(db: AsyncSession, numero: str, quando: datetime, como: str) -> None:
    await db.execute(
        text(
            "INSERT INTO devolucao_rastreio (pedido_bling, resolvido_at, resolvido_como) "
            "VALUES (:p, :q, :c)"
        ),
        {"p": numero, "q": quando, "c": como},
    )


async def test_lista_pega_quem_caiu_em_devolucao_e_so_sai_no_resolver(
    client, db: AsyncSession, make_user, auth_as
):
    auth_as(await make_user(permissions=_perm(edit=False)))
    hoje = datetime.now(_SP).date()
    agora = datetime.now(UTC)
    await _pedido(db, "700001", "83957", entrada=hoje - timedelta(days=5), itens=2)
    # Saiu de Aguardando Devolução (lançamento mandou pra Resolvido): CONTINUA.
    await _pedido(db, "700002", "545902", entrada=hoje - timedelta(days=9))
    # Em Manutenção sem nunca ter carimbo de entrada: entra.
    await _pedido(db, "700003", "84677", entrada=None)
    # Resolvido pelo botão: sai das abertas.
    await _pedido(db, "700004", "545902", entrada=hoje - timedelta(days=20))
    await _resolvido(db, "700004", agora - timedelta(days=1), "Voltou pro estoque")
    # Histórico de antes do painel novo: nunca aparece.
    await _pedido(db, "700005", "545902", entrada=hoje - timedelta(days=40))
    await _resolvido(db, "700005", agora - timedelta(days=1), PAINEL_LARGADA)
    # Nunca passou por devolução: fora.
    await _pedido(db, "700006", "9", entrada=None)
    await db.commit()

    r = await client.get("/api/devolucoes-painel")
    assert r.status_code == 200, r.text
    body = r.json()
    por_pedido: dict[str, list[dict]] = {}
    for i in body["items"]:
        por_pedido.setdefault(i["pedido_bling"], []).append(i)
    assert set(por_pedido) == {"700001", "700002", "700003"}
    assert body["total_pedidos"] == 3
    assert body["ve_custo"] is False and body["com_cerca_de_equipe"] is False
    assert "Voltou pro estoque" in body["como_terminou"]

    a = por_pedido["700001"]
    assert len(a) == 2 and [x["primeira_do_pedido"] for x in a] == [True, False]
    assert a[0]["etapa"] == "aguardando" and a[0]["dias_em_devolucao"] == 5
    assert a[0]["linhas_do_pedido"] == 2 and a[0]["resolvido"] is False
    # Aguardando sem nada lançado: o prazo dos 45 dias aparece sempre.
    assert a[0]["prazo_tipo"] == "pacote chegar" and a[0]["prazo_estado"] is None
    assert a[0]["prazo_em"][:10] in (
        (hoje + timedelta(days=40)).isoformat(),
        (hoje + timedelta(days=41)).isoformat(),
    )
    b = por_pedido["700002"][0]
    assert b["situacao_bling_id"] == "545902"
    assert b["pronto_resolver"] and b["pronto_resolver"].startswith("o Bling já está em")
    assert por_pedido["700003"][0]["etapa"] == "manutencao"

    resolvidas = (await client.get("/api/devolucoes-painel?mostrar=resolvidas")).json()
    assert {i["pedido_bling"] for i in resolvidas["items"]} == {"700004"}
    assert resolvidas["items"][0]["resolvido_como"] == "Voltou pro estoque"
    todas = (await client.get("/api/devolucoes-painel?mostrar=todas")).json()
    assert {i["pedido_bling"] for i in todas["items"]} == {"700001", "700002", "700003", "700004"}


async def test_resolver_tira_da_lista_e_reabrir_devolve(
    client, db: AsyncSession, make_user, auth_as
):
    user = await make_user(permissions=_perm())
    auth_as(user)
    hoje = datetime.now(_SP).date()
    await _pedido(db, "710001", "83957", entrada=hoje - timedelta(days=3))
    await db.commit()

    r = await client.post(
        "/api/devolucoes-painel/710001/resolver", json={"como_terminou": "Cliente não devolveu"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["resolvido"] is True and out["resolvido_como"] == "Cliente não devolveu"
    assert out["situacao_bling_id"] == "83957"  # sem situacao_id não mexe no Bling
    assert (await client.get("/api/devolucoes-painel")).json()["items"] == []
    res = (await client.get("/api/devolucoes-painel?mostrar=resolvidas")).json()["items"]
    assert res[0]["resolvido"] is True and res[0]["resolvido_por_nome"] == user.name

    r = await client.post("/api/devolucoes-painel/710001/reabrir")
    assert r.status_code == 200 and r.json()["resolvido"] is False
    abertas = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert [i["pedido_bling"] for i in abertas] == ["710001"]
    # Reabrir o que não está resolvido não faz nada.
    assert (await client.post("/api/devolucoes-painel/710001/reabrir")).status_code == 409


async def test_resolver_exige_permissao_de_editar_e_como_terminou(
    client, db: AsyncSession, make_user, auth_as
):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "720001", "83957", entrada=hoje)
    await db.commit()
    auth_as(await make_user(permissions=_perm(edit=False)))
    r = await client.post(
        "/api/devolucoes-painel/720001/resolver", json={"como_terminou": "Perdimento"}
    )
    assert r.status_code == 403
    auth_as(await make_user(permissions=_perm()))
    r = await client.post("/api/devolucoes-painel/720001/resolver", json={"como_terminou": ""})
    assert r.status_code == 422
    r = await client.post(
        "/api/devolucoes-painel/999999/resolver", json={"como_terminou": "Perdimento"}
    )
    assert r.status_code == 404


async def test_voltou_pra_devolucao_depois_de_resolvido_reabre_sozinho(
    client, db: AsyncSession, make_user, auth_as
):
    """Reentrada em Aguardando Devolução re-carimba a data de entrada: é uma
    devolução nova do mesmo pedido."""
    auth_as(await make_user(permissions=_perm()))
    hoje = datetime.now(_SP).date()
    await _pedido(db, "730001", "83957", entrada=hoje)
    await _resolvido(db, "730001", datetime.now(UTC) - timedelta(days=10), "Voltou pro estoque")
    await db.commit()
    items = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert [i["pedido_bling"] for i in items] == ["730001"]
    assert items[0]["resolvido"] is False and items[0]["resolvido_como"] is None


async def test_data_digitada_nao_muda_quem_esta_resolvido(
    client, db: AsyncSession, make_user, auth_as
):
    """Aberto/resolvido sai de UM lugar (o SQL, pela entrada do Bling). A data
    "em devolução desde" digitada à mão é só a que a tela mostra: mesmo sendo
    de DEPOIS do Resolver, o pedido continua resolvido — e aparece como
    resolvido, não como aberto no meio das resolvidas."""
    auth_as(await make_user(permissions=_perm()))
    hoje = datetime.now(_SP).date()
    await _pedido(db, "735001", "545902", entrada=hoje - timedelta(days=20))
    await db.execute(
        text(
            "INSERT INTO devolucao_rastreio (pedido_bling, resolvido_at, resolvido_como, "
            "entrada_manual) VALUES ('735001', :q, 'Voltou pro estoque', :m)"
        ),
        {"q": datetime.now(UTC) - timedelta(days=5), "m": hoje},
    )
    await db.commit()
    assert (await client.get("/api/devolucoes-painel")).json()["items"] == []
    res = (await client.get("/api/devolucoes-painel?mostrar=resolvidas")).json()["items"]
    assert [i["pedido_bling"] for i in res] == ["735001"]
    assert res[0]["resolvido"] is True and res[0]["resolvido_como"] == "Voltou pro estoque"
    assert res[0]["aguardando_devolucao_data"] == hoje.isoformat()


async def test_excecao_dos_45_dias_nao_vira_prazo_vencido(
    client, db: AsyncSession, make_user, auth_as
):
    """Passou de 45 dias mas a observação diz que foi extraviado: fica fora da
    regra — não é "venceu · pacote chegar" nem entra em "Passou de 45 dias"."""
    auth_as(await make_user(permissions=_perm()))
    hoje = datetime.now(_SP).date()
    await _pedido(db, "736001", "83957", entrada=hoje - timedelta(days=50))
    await db.execute(
        text(
            "INSERT INTO devolucao_rastreio (pedido_bling, observacao) "
            "VALUES ('736001', 'pacote extraviado pelos Correios')"
        )
    )
    await _pedido(db, "736002", "83957", entrada=hoje - timedelta(days=50))
    await db.commit()
    itens = (await client.get("/api/devolucoes-painel")).json()["items"]
    por = {i["pedido_bling"]: i for i in itens}
    assert por["736001"]["prazo_tipo"] is None and por["736001"]["prazo_estado"] is None
    assert "fora da regra" in (por["736001"]["prazo_texto"] or "")
    assert (por["736002"]["prazo_tipo"], por["736002"]["prazo_estado"]) == (
        "pacote chegar",
        "vencido",
    )


async def test_anexo_que_nao_e_foto_vem_com_o_tipo(client, db: AsyncSession, make_user, auth_as):
    auth_as(await make_user(permissions=_perm()))
    hoje = datetime.now(_SP).date()
    await _pedido(db, "737001", "83957", entrada=hoje - timedelta(days=2))
    dev = Devolution(pedido_bling="737001", conta="Shopee KFA", sku="a001.sp", quantidade=1)
    db.add(dev)
    await db.flush()
    for nome, tipo in (("foto.jpg", "image/jpeg"), ("abertura.mp4", "video/mp4")):
        db.add(
            DevolucaoAnexo(
                devolution_id=dev.id, filename=nome, content_type=tipo, size_bytes=3, blob=b"abc"
            )
        )
    await db.commit()
    item = (await client.get("/api/devolucoes-painel")).json()["items"][0]
    tipos = [a["content_type"] for a in item["lancamentos"][0]["anexos"]]
    assert tipos == ["image/jpeg", "video/mp4"]


async def test_resolver_recusa_a_marca_da_largada(client, db: AsyncSession, make_user, auth_as):
    auth_as(await make_user(permissions=_perm()))
    await _pedido(db, "738001", "83957", entrada=datetime.now(_SP).date())
    await db.commit()
    r = await client.post(
        "/api/devolucoes-painel/738001/resolver", json={"como_terminou": PAINEL_LARGADA}
    )
    assert r.status_code == 422
    itens = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert [i["pedido_bling"] for i in itens] == ["738001"]


async def test_lancamento_chamado_e_conta_so_o_admin_ve_custo(
    client, db: AsyncSession, make_user, auth_as
):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "740001", "84677", entrada=hoje - timedelta(days=24), sku="g1s.ra", itens=2)
    await db.execute(
        text(
            """
            INSERT INTO devolutions
                (pedido_bling, conta, sku, quantidade, condicao_produto, custo_manutencao,
                 custo_produto, tecnico, motivo_devolucao)
            VALUES ('740001', 'Shopee Mini', 'g1s.ra', 1, 'Manutenção', 100, 512, 'Shark',
                    'Dano funcional / Não funciona')
            """
        )
    )
    db.add(
        Chamado(
            pedido_bling="740001",
            origem="devolucao",
            conta="Shopee Mini",
            resolvido=True,
            resolvido_at=datetime.now(UTC),
            valor_recuperado=700,
            chamado="CH-1",
        )
    )
    await db.commit()

    auth_as(await make_user(role=UserRole.ADMIN))
    body = (await client.get("/api/devolucoes-painel")).json()
    assert body["ve_custo"] is True
    primeira, segunda = body["items"]
    assert primeira["etapa"] == "manutencao"
    # Com o técnico: sem condição na tela (Manutenção é etapa).
    assert primeira["condicao_tela"] is None
    lanc = primeira["lancamentos"][0]
    assert lanc["condicao"] == "Manutenção" and lanc["manutencao_pendente"] is True
    assert lanc["custo_manutencao"] == 100 and lanc["tecnico"] == "Shark"
    assert primeira["chamado"]["resolvido"] is True and primeira["tem_chamado_aberto"] is False
    # A conta é do PEDIDO: aparece uma vez (700 − 100 = 600), não em cada linha.
    assert primeira["conta"] == {
        "recebido": 700.0,
        "custo_produto": 0.0,
        "custo_manutencao": 100.0,
        "resultado": 600.0,
    }
    assert segunda["conta"] is None and segunda["primeira_do_pedido"] is False

    auth_as(await make_user(permissions=_perm()))
    body = (await client.get("/api/devolucoes-painel")).json()
    assert body["ve_custo"] is False
    assert all(i["conta"] is None for i in body["items"])
    lanc = body["items"][0]["lancamentos"][0]
    assert lanc["custo_manutencao"] is None and lanc["custo_produto"] is None


async def test_resumo_comeca_na_virada(client, db: AsyncSession, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    hoje = datetime.now(_SP).date()
    agora = datetime.now(UTC)
    # Sem virada marcada não há o que contar.
    assert (await client.get("/api/devolucoes-painel/resumo")).json() == {
        "desde": None,
        "dias": [],
        "ve_custo": False,
    }
    await _pedido(db, "750000", "545902", entrada=hoje - timedelta(days=30))
    await _resolvido(db, "750000", agora - timedelta(days=2), PAINEL_LARGADA)  # a virada
    await _pedido(db, "750001", "83957", entrada=hoje)  # nova hoje, aberta
    await _pedido(db, "750002", "83957", entrada=hoje - timedelta(days=1))  # nova ontem
    await _pedido(db, "750003", "545902", entrada=hoje - timedelta(days=8))
    await _resolvido(db, "750003", agora, "Voltou pro estoque")  # resolvida hoje
    await db.execute(
        text(
            """
            INSERT INTO devolutions (pedido_bling, conta, sku, quantidade, condicao_produto,
                                     custo_produto)
            VALUES ('750003', 'Shopee KFA', 'a001.sp', 2, 'Usado', 50),
                   ('750002', 'Shopee KFA', 'b002.sp', 1, 'Sucata', 612)
            """
        )
    )
    await db.commit()

    body = (await client.get("/api/devolucoes-painel/resumo")).json()
    assert body["ve_custo"] is True
    dias = {d["dia"]: d for d in body["dias"]}
    virada = (agora - timedelta(days=2)).astimezone(_SP).date()
    assert body["desde"] == virada.isoformat()
    assert min(dias) == virada.isoformat() and max(dias) == hoje.isoformat()
    d0 = dias[hoje.isoformat()]
    # Hoje: 2 abertas (750001 e 750002); 1 nova; 1 resolvida no botão.
    assert (d0["abertas"], d0["novas"], d0["resolvidas"]) == (2, 1, 1)
    assert (d0["estoque_usado"], d0["perdimento"]) == (2, 1)
    assert d0["custos"] == 612.0 and d0["resultado"] == -612.0
    # Ontem terminou com 2 abertas: 750002 (entrou ontem) e 750003 (ainda não resolvida).
    ontem = dias[(hoje - timedelta(days=1)).isoformat()]
    assert (ontem["abertas"], ontem["novas"], ontem["resolvidas"]) == (2, 1, 0)

    # Quem não é admin não recebe dinheiro.
    auth_as(await make_user(permissions=_perm()))
    body = (await client.get("/api/devolucoes-painel/resumo")).json()
    assert body["ve_custo"] is False
    assert all(d["custos"] is None and d["recebido"] is None for d in body["dias"])


async def test_busca_por_pedido_acha_no_historico_inteiro(
    client, db: AsyncSession, make_user, auth_as
):
    """`?pedido=` traz UM pedido de qualquer lugar: resolvido, da largada, ou
    que nunca caiu em Aguardando Devolução (pra lançar pela janela) — pelo nº
    do Bling ou pelo da plataforma."""
    auth_as(await make_user(permissions=_perm()))
    hoje = datetime.now(_SP).date()
    agora = datetime.now(UTC)
    await _pedido(db, "760001", "545902", entrada=hoje - timedelta(days=40))
    await _resolvido(db, "760001", agora - timedelta(days=3), PAINEL_LARGADA)
    await _pedido(db, "760002", "9", entrada=None)  # nunca passou por devolução
    await _pedido(db, "760003", "83957", entrada=hoje)
    await db.commit()

    lista = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert [i["pedido_bling"] for i in lista] == ["760003"]
    antigo = (await client.get("/api/devolucoes-painel?pedido=760001")).json()["items"]
    assert [i["pedido_bling"] for i in antigo] == ["760001"]
    assert antigo[0]["resolvido"] is True and antigo[0]["resolvido_como"] == PAINEL_LARGADA
    fora = (await client.get("/api/devolucoes-painel?pedido=MKT-760002")).json()["items"]
    assert [i["pedido_bling"] for i in fora] == ["760002"]
    assert fora[0]["resolvido"] is False and fora[0]["dias_em_devolucao"] is None
    assert (await client.get("/api/devolucoes-painel?pedido=999999")).json()["items"] == []


async def test_largada_das_lancadas_segue_a_regua_do_pronto_pra_resolver(db: AsyncSession):
    """Migration 0484 ("os pedidos que já estava com estoque lançado antigo
    deixa tudo como finalizado"): nasce finalizado só o pedido em que TUDO
    que foi lançado já terminou — nada com o técnico, nenhuma entrada de
    estoque pendente, nenhum chamado aberto, todas as peças com condição."""
    hoje = datetime.now(_SP).date()

    async def lanc(pedido: str, sku: str, **campos) -> None:
        db.add(Devolution(pedido_bling=pedido, conta="Shopee KFA", sku=sku, quantidade=1, **campos))

    await _pedido(db, "770001", "83957", entrada=hoje - timedelta(days=60))
    await lanc("770001", "a", condicao_produto="Novo", estoque_status="ok")  # terminou
    await _pedido(db, "770002", "84677", entrada=hoje - timedelta(days=60))
    await lanc("770002", "a", condicao_produto="Novo", estoque_status="ok")
    await lanc("770002", "b", condicao_produto="Manutenção")  # celular com o técnico: fica
    await _pedido(db, "770003", "83957", entrada=hoje - timedelta(days=60))
    await lanc(
        "770003", "a", condicao_produto="Usado", estoque_status="falhou"
    )  # estoque não entrou
    await _pedido(db, "770004", "83957", entrada=hoje - timedelta(days=60))
    await lanc("770004", "a", condicao_produto="Extraviado")
    db.add(Chamado(pedido_bling="770004", origem="devolucao", conta="Shopee KFA"))  # chamado aberto
    await _pedido(db, "770005", "83957", entrada=hoje - timedelta(days=60))
    await lanc("770005", "a", condicao_produto=None)  # sem condição
    await _pedido(db, "770006", "84677", entrada=hoje - timedelta(days=60))
    await lanc(
        "770006",
        "a",
        condicao_produto="Manutenção",
        manutencao_destino="Usado",
        estoque_status="ok",
    )
    await _pedido(db, "770007", "83957", entrada=hoje - timedelta(days=2))  # sem lançamento: fica
    await db.commit()

    fonte = (
        Path(__file__).parents[1] / "alembic/versions/0484_devolucao_painel_lancadas_finalizadas.py"
    ).read_text()
    sql = re.search(r'op\.execute\(\s*f"""(.*?)"""', fonte, re.S).group(1)
    sql = sql.replace('"{SCHEMA}".', "").replace("{LARGADA}", PAINEL_LARGADA)
    await db.execute(text(sql))
    await db.commit()
    marcados = (
        (
            await db.execute(
                text(
                    "SELECT pedido_bling FROM devolucao_rastreio "
                    "WHERE resolvido_at IS NOT NULL ORDER BY 1"
                )
            )
        )
        .scalars()
        .all()
    )
    assert marcados == ["770001", "770006"]
