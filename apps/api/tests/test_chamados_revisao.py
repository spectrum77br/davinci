"""Coluna Revisão da aba Chamados (Vinicius 05/11/2026): Conferido e Revisar valem no
dia, Fixo até a data; a virada é só comparar com hoje; a marca vale pro caso; a
migration 0451 converte as anotações da Observação."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models import Chamado, ChamadoMensagem, ChamadoRevisao, ChamadoRevisaoDia
from app.services import chamados as svc
from app.services import chamados_revisao as rev

HOJE = date(2026, 11, 5)
PERM = {"chamados": {"view": True, "edit": True, "delete": True}}

_migration = Path(__file__).parents[1] / "alembic/versions/0451_chamados_revisao.py"
_spec = importlib.util.spec_from_file_location("migration_0451", _migration)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def _ch(**kw) -> Chamado:
    return Chamado(origem="devolucao", **kw)


def _estado(ch, *, at=None, direcao=None, hoje=HOJE):
    return rev.estado_da_revisao(
        ch, ultima_resposta_at=at, ultima_resposta_direcao=direcao, hoje=hoje
    )


def test_sem_marca_falta_revisar():
    assert _estado(_ch()) == "falta"


@pytest.mark.parametrize("tipo", ["conferido", "revisar"])
def test_conferido_e_revisar_valem_so_no_dia(tipo):
    ch = _ch(revisao_tipo=tipo, revisao_dia=HOJE)
    assert _estado(ch) == tipo
    assert _estado(ch, hoje=HOJE + timedelta(days=1)) == "falta"


def test_fixo_fica_ate_a_data_e_depois_avisa():
    ch = _ch(revisao_tipo="fixo", revisao_dia=HOJE, revisao_ate=date(2026, 11, 25))
    assert _estado(ch) == "fixo"
    assert _estado(ch, hoje=date(2026, 11, 24)) == "fixo"
    assert _estado(ch, hoje=date(2026, 11, 25)) == "prazo_hoje"
    assert _estado(ch, hoje=date(2026, 11, 26)) == "prazo_vencido"


def test_plataforma_respondeu_depois_da_marca_derruba_o_fixo():
    marcado = datetime(2026, 11, 5, 12, tzinfo=UTC)
    ch = _ch(
        revisao_tipo="fixo",
        revisao_dia=HOJE,
        revisao_ate=date(2026, 11, 25),
        revisao_at=marcado,
    )
    depois = marcado + timedelta(hours=1)
    assert _estado(ch, at=depois, direcao="recebida") == "respondeu"
    # Nossa fala não derruba, nem resposta anterior à marca.
    assert _estado(ch, at=depois, direcao="enviada") == "fixo"
    assert _estado(ch, at=marcado - timedelta(hours=1), direcao="recebida") == "fixo"


def test_prazo_vence_antes_de_respondeu():
    marcado = datetime(2026, 11, 5, 12, tzinfo=UTC)
    ch = _ch(
        revisao_tipo="fixo",
        revisao_dia=HOJE,
        revisao_ate=date(2026, 11, 25),
        revisao_at=marcado,
    )
    resposta = marcado + timedelta(hours=1)
    estado = _estado(ch, at=resposta, direcao="recebida", hoje=date(2026, 11, 25))
    assert estado == "prazo_hoje"


# --- API ---------------------------------------------------------------------


async def _chamado(client, **kw) -> dict:
    corpo = {
        "origem": "devolucao",
        "pedido_bling": "301908",
        "plataforma": "shopee",
        "conta": "Shopee Mini",
        "canal": "manual",
        **kw,
    }
    r = await client.post("/api/chamados", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_marcar_conferido_aparece_na_lista_e_vai_pro_registro(client, make_user, auth_as, db):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    ch = await _chamado(client)
    assert ch["revisao_estado"] == "falta"

    r = await client.post(f"/api/chamados/{ch['id']}/revisao", json={"tipo": "conferido"})
    assert r.status_code == 200, r.text
    item = r.json()["itens"][0]
    assert item["revisao_estado"] == "conferido"
    assert item["revisao_dia"] == rev.hoje_sp().isoformat()
    assert item["revisao_por_nome"]

    lista = (await client.get("/api/chamados")).json()
    assert lista["items"][0]["revisao_estado"] == "conferido"

    r = await client.post(f"/api/chamados/{ch['id']}/revisao", json={"tipo": None})
    assert r.json()["itens"][0]["revisao_estado"] == "falta"
    tipos = [
        x.tipo
        for x in (
            await db.execute(
                select(ChamadoRevisao)
                .where(ChamadoRevisao.chamado_id == item["id"])
                .order_by(ChamadoRevisao.created_at)
            )
        ).scalars()
    ]
    assert sorted(tipos) == ["conferido", "limpo"]


@pytest.mark.asyncio
async def test_fixo_exige_data_futura_e_motivo(client, make_user, auth_as):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    ch = await _chamado(client)
    url = f"/api/chamados/{ch['id']}/revisao"
    hoje = rev.hoje_sp()
    amanha = (hoje + timedelta(days=1)).isoformat()

    r = await client.post(url, json={"tipo": "fixo", "motivo": "paga dia 25"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "revisao_data_invalida"
    r = await client.post(url, json={"tipo": "fixo", "ate": hoje.isoformat(), "motivo": "x"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "revisao_data_invalida"
    r = await client.post(url, json={"tipo": "fixo", "ate": amanha, "motivo": "   "})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "revisao_motivo_vazio"

    r = await client.post(url, json={"tipo": "fixo", "ate": amanha, "motivo": "paga dia 25"})
    assert r.status_code == 200, r.text
    item = r.json()["itens"][0]
    assert item["revisao_estado"] == "fixo"
    assert item["revisao_motivo"] == "paga dia 25"


@pytest.mark.asyncio
async def test_marca_vale_pro_caso_mas_nao_pro_concluido(client, make_user, auth_as, db):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    a = await _chamado(client, pedido_bling="292529", conta="kfa", chamado="478538390")
    b = await _chamado(client, pedido_bling="290490", conta="kfa", chamado="478538390")
    c = await _chamado(client, pedido_bling="290397", conta="kfa", chamado="478538390")
    outro = await _chamado(client, pedido_bling="291969", conta="kfa", chamado="478232571")
    fechado = await db.get(Chamado, c["id"])
    fechado.resolvido = True
    await db.commit()

    r = await client.post(f"/api/chamados/{a['id']}/revisao", json={"tipo": "revisar"})
    assert r.status_code == 200, r.text
    ids = {i["id"] for i in r.json()["itens"]}
    assert ids == {a["id"], b["id"]}

    lista = {i["id"]: i for i in (await client.get("/api/chamados")).json()["items"]}
    assert lista[b["id"]]["revisao_estado"] == "revisar"
    assert lista[outro["id"]]["revisao_estado"] == "falta"


@pytest.mark.asyncio
async def test_concluido_nao_entra_na_revisao(client, make_user, auth_as, db):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    ch = await _chamado(client)
    linha = await db.get(Chamado, ch["id"])
    linha.resolvido = True
    await db.commit()
    r = await client.post(f"/api/chamados/{ch['id']}/revisao", json={"tipo": "conferido"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "revisao_chamado_concluido"


@pytest.mark.asyncio
async def test_sem_permissao_de_editar_nao_marca(client, make_user, auth_as):
    dono = await make_user(role="user", permissions=PERM)
    auth_as(dono)
    ch = await _chamado(client)
    so_ve = await make_user(role="user", permissions={"chamados": {"view": True}})
    auth_as(so_ve)
    r = await client.post(f"/api/chamados/{ch['id']}/revisao", json={"tipo": "conferido"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_lista_derruba_fixo_quando_a_plataforma_responde(client, make_user, auth_as, db):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    ch = await _chamado(client)
    ate = (rev.hoje_sp() + timedelta(days=20)).isoformat()
    r = await client.post(
        f"/api/chamados/{ch['id']}/revisao",
        json={"tipo": "fixo", "ate": ate, "motivo": "paga dia 25"},
    )
    assert r.status_code == 200, r.text
    db.add(
        ChamadoMensagem(
            chamado_id=ch["id"],
            direcao="recebida",
            tipo="replica",
            texto="Pagamento adiado",
            canal="manual",
            status="registrada",
            created_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )
    await db.commit()
    item = (await client.get("/api/chamados")).json()["items"][0]
    assert item["revisao_estado"] == "respondeu"
    assert item["revisao_motivo"] == "paga dia 25"


@pytest.mark.asyncio
async def test_totais_por_plataforma_ignoram_o_filtro_de_plataforma(client, make_user, auth_as):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    await _chamado(client, plataforma="ml", pedido_bling="1")
    await _chamado(client, plataforma="mercado livre", pedido_bling="2")
    await _chamado(client, plataforma="shopee", pedido_bling="3")
    r = await client.get("/api/chamados", params={"plataforma": "shopee"})
    corpo = r.json()
    assert corpo["total"] == 1
    assert sum(corpo["plataforma_totais"].values()) == 3
    assert corpo["plataforma_totais"]["shopee"] == 1


# --- conversão da Observação (migration 0451) --------------------------------


@pytest.mark.parametrize(
    ("texto", "tipo", "ate", "motivo", "resto"),
    [
        ("05-ok", "conferido", None, None, None),
        ("5-ok", "conferido", None, None, None),
        ("5 - OK", "conferido", None, None, None),
        ("5-revisar", "revisar", None, None, None),
        ("5 - conferido", "conferido", None, None, None),
        # Outro dia: já venceu, só sai do texto.
        ("04-ok", None, None, None, None),
        ("18 - conferido", None, None, None, None),
        ("revisar", None, None, None, None),
        (
            "14/11 - ate essa data conferir se caiu o valor",
            "fixo",
            date(2026, 11, 14),
            "conferir se caiu o valor",
            None,
        ),
        (
            "25/11 - ate essa data confirmar se caiu o valor.",
            "fixo",
            date(2026, 11, 25),
            "confirmar se caiu o valor",
            None,
        ),
        (
            "28/10 - conferir se teve ajuste",
            "fixo",
            date(2026, 10, 28),
            "conferir se teve ajuste",
            None,
        ),
        ("5-ok\ncérebro: teto de réplicas", "conferido", None, None, "cérebro: teto de réplicas"),
    ],
)
def test_interpreta_as_anotacoes_reais(texto, tipo, ate, motivo, resto):
    r = mig._interpretar(texto, HOJE)
    assert r == {"tipo": tipo, "ate": ate, "motivo": motivo, "resto": resto}


@pytest.mark.parametrize(
    "texto",
    [
        None,
        "",
        "Apreensão fiscal — agrupado no chamado da loja",
        "16 - respondi, verificar se robo enviou a mensagem",
        "Devolveu apenas relógio e fone, ficou com o aparelho",
        "31/02 - data que não existe",
    ],
)
def test_texto_livre_nao_mexe(texto):
    assert mig._interpretar(texto, HOJE) is None


@pytest.mark.asyncio
async def test_converte_no_banco_e_guarda_o_original(db):
    schema = (await db.execute(text("select current_schema()"))).scalar_one()
    aberto = _ch(id=uuid4(), observacao="25/11 - ate essa data confirmar se caiu o valor.")
    hoje_ok = _ch(id=uuid4(), observacao="5-ok")
    velho = _ch(id=uuid4(), observacao="18 - conferido")
    livre = _ch(id=uuid4(), observacao="Apreensão fiscal")
    fechado = _ch(id=uuid4(), observacao="5-ok", resolvido=True)
    db.add_all([aberto, hoje_ok, velho, livre, fechado])
    await db.commit()

    conn = await db.connection()
    feitas = await conn.run_sync(lambda c: mig._converter(c, schema=schema, hoje=HOJE))
    await db.commit()
    assert feitas == 3
    for linha in (aberto, hoje_ok, velho, livre, fechado):
        await db.refresh(linha)

    assert aberto.observacao is None
    assert aberto.revisao_tipo == "fixo"
    assert aberto.revisao_ate == date(2026, 11, 25)
    assert aberto.revisao_motivo == "confirmar se caiu o valor"
    assert aberto.revisao_at is not None
    assert (hoje_ok.observacao, hoje_ok.revisao_tipo, hoje_ok.revisao_dia) == (
        None,
        "conferido",
        HOJE,
    )
    assert (velho.observacao, velho.revisao_tipo, velho.revisao_at) == (None, None, None)
    assert (livre.observacao, livre.revisao_tipo) == ("Apreensão fiscal", None)
    assert (fechado.observacao, fechado.revisao_tipo) == ("5-ok", None)

    log = (
        await db.execute(select(ChamadoRevisao).where(ChamadoRevisao.chamado_id == velho.id))
    ).scalar_one()
    assert (log.tipo, log.observacao_antes) == ("convertido", "18 - conferido")


# --- painel Dias ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_dias_conta_novos_concluidos_e_abertos(client, make_user, auth_as, db):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    agora = datetime.now(UTC)
    ontem = agora - timedelta(days=1)
    velho = _ch(id=uuid4(), created_at=agora - timedelta(days=20))
    fechado_ontem = _ch(
        id=uuid4(), created_at=agora - timedelta(days=20), resolvido=True, resolvido_at=ontem
    )
    novo_ontem = _ch(id=uuid4(), created_at=ontem)
    novo_hoje = _ch(id=uuid4(), created_at=agora)
    db.add_all([velho, fechado_ontem, novo_ontem, novo_hoje])
    await db.commit()

    corpo = (await client.get("/api/chamados/dias", params={"meses": 2})).json()
    hoje, d_ontem, anteontem = corpo["dias"][:3]
    assert (hoje["abertos"], hoje["novos"], hoje["concluidos"]) == (3, 1, 0)
    assert (d_ontem["abertos"], d_ontem["novos"], d_ontem["concluidos"]) == (2, 1, 1)
    assert (anteontem["abertos"], anteontem["novos"]) == (2, 0)
    # Hoje é ao vivo; dia sem foto não tem revisão.
    assert hoje["tem_registro"] is True and hoje["falta"] == 3
    assert set(hoje["faltaram"]) == {str(velho.id), str(novo_ontem.id), str(novo_hoje.id)}
    assert d_ontem["tem_registro"] is False


@pytest.mark.asyncio
async def test_foto_do_dia_aparece_no_painel_e_regrava(client, make_user, auth_as, db):
    from app.routers.chamados import fotografar_revisao

    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    ch = await _chamado(client)
    await client.post(f"/api/chamados/{ch['id']}/revisao", json={"tipo": "conferido"})
    outro = await _chamado(client, pedido_bling="301909")

    await fotografar_revisao(db)
    await db.commit()
    await client.post(f"/api/chamados/{outro['id']}/revisao", json={"tipo": "revisar"})
    await fotografar_revisao(db)
    await db.commit()

    fotos = list((await db.execute(select(ChamadoRevisaoDia))).scalars())
    assert len(fotos) == 1
    assert (fotos[0].conferido, fotos[0].revisar, fotos[0].falta) == (1, 1, 0)

    # A foto de ontem é a que vale pra ontem.
    fotos[0].dia = rev.hoje_sp() - timedelta(days=1)
    db.add(_ch(id=uuid4(), created_at=datetime.now(UTC) - timedelta(days=3)))
    await db.commit()
    corpo = (await client.get("/api/chamados/dias", params={"meses": 2})).json()
    assert corpo["dias"][1]["tem_registro"] is True
    assert corpo["dias"][1]["conferido"] == 1


@pytest.mark.asyncio
async def test_dias_soma_lucro_prejuizo_e_acumula_no_mes(client, make_user, auth_as, db):
    """Vinicius 05/11: lucro e prejuízo pelo dia da conclusão; o acumulado zera no dia 1."""
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    meio_dia = datetime.combine(
        rev.hoje_sp().replace(day=1), time(12), tzinfo=svc.SAO_PAULO
    ).astimezone(UTC)
    fim_do_mes_passado = meio_dia - timedelta(days=1)
    velho = meio_dia - timedelta(days=40)

    def fechado(quando, valor):
        return _ch(
            id=uuid4(), created_at=velho, resolvido=True, resolvido_at=quando,
            valor_recuperado=valor,
        )

    db.add_all(
        [
            fechado(fim_do_mes_passado, Decimal("500.00")),
            fechado(meio_dia, Decimal("100.50")),
            fechado(meio_dia, Decimal("-30.25")),
            fechado(meio_dia, Decimal("0")),
            _ch(id=uuid4(), created_at=velho, resolvido=False, valor_recuperado=Decimal("999")),
        ]
    )
    await db.commit()
    corpo = (await client.get("/api/chamados/dias", params={"meses": 2})).json()
    por_dia = {d["dia"]: d for d in corpo["dias"]}
    dia1 = por_dia[rev.hoje_sp().replace(day=1).isoformat()]
    assert (dia1["lucro"], dia1["prejuizo"], dia1["acumulado"]) == (100.5, -30.25, 70.25)
    assert dia1["concluidos"] == 3
    # O mês anterior fecha no dele e não entra no acumulado deste.
    ultimo = por_dia[(rev.hoje_sp().replace(day=1) - timedelta(days=1)).isoformat()]
    assert (ultimo["lucro"], ultimo["acumulado"]) == (500.0, 500.0)
    assert corpo["dias"][0]["acumulado"] == 70.25


@pytest.mark.asyncio
async def test_dias_comeca_no_primeiro_chamado(client, make_user, auth_as, db):
    user = await make_user(role="user", permissions=PERM)
    auth_as(user)
    db.add(_ch(id=uuid4(), created_at=datetime.now(UTC)))
    await db.commit()
    corpo = (await client.get("/api/chamados/dias", params={"meses": 6})).json()
    assert [d["dia"] for d in corpo["dias"]] == [rev.hoje_sp().isoformat()]


def test_resumo_do_dia_separa_falta_de_prazo():
    class Linha:
        def __init__(self, estado, status="aguard_plataforma"):
            self.id = uuid4()
            self.status_aba = status
            self.revisao_estado = estado

    linhas = [
        Linha("conferido"),
        Linha("revisar"),
        Linha("fixo"),
        Linha("falta"),
        Linha("falta", "analise_humano"),
        Linha("prazo_vencido"),
        Linha("respondeu"),
    ]
    r = rev.resumir_dia(linhas)
    assert (r["conferido"], r["revisar"], r["fixo"], r["falta"], r["prazos"]) == (1, 1, 1, 2, 2)
    assert r["por_status"] == {"aguard_plataforma": 1, "analise_humano": 1}
    assert len(r["faltaram"]) == 4
