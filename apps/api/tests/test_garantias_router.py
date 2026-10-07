"""Pós-venda › Garantias (Painel de Garantia Uranyx): critérios de aceite do
documento (§7), RN01–RN07, os pontos do §8, LGPD (§6) e a integração com o
Comunicador (§5)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.historico import nomes
from app.historico import sql as hsql
from app.models import (
    BlingOrder,
    Garantia,
    GarantiaAtendimento,
    GarantiaAtendimentoAnexo,
    GarantiaLog,
    Logistica,
    UserRole,
)
from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.nf import NfNota
from app.models.pricing import StoreInfo
from app.routers import garantias as rota
from app.services import garantia as svc

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 10, 7)
CPF = "52998224725"
CPF_2 = "11144477735"
CPF_3 = "12345678909"

CADASTRA = {"garantias": {"view": True, "edit": True}}
CONSULTA = {"garantias": {"view": True}}
REGISTRA = {"garantias_atendimento": {"view": True, "edit": True}}
VE_CPF = {"garantias": {"view": True}, "garantias_cpf": {"view": True}}
ATENDENTE = {
    "garantias": {"view": True},
    "garantias_atendimento": {"view": True, "edit": True},
}


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Nenhum teste baixa nada de verdade: o CDN responde por aqui."""

    async def _baixar(url):
        if "expirado" in url:
            raise svc.AnexoRecusado("link_expirado")
        return b"\x89PNG-foto-" + url.encode()[-8:], "image/png"

    monkeypatch.setattr(svc, "baixar_anexo", _baixar)


@pytest.fixture(autouse=True)
def teto_cpf(monkeypatch) -> list[tuple[str, int, int]]:
    """O teto da busca por CPF (Redis) responde por aqui: guarda as chamadas."""
    chamadas: list[tuple[str, int, int]] = []

    async def _ok(*, key, limit, window_seconds):
        chamadas.append((key, limit, window_seconds))
        return limit

    monkeypatch.setattr(rota, "sliding_window_check", _ok)
    return chamadas


# ── Fábrica ───────────────────────────────────────────────────────────────────


async def _pedido(
    db,
    numero="300001",
    numeroloja="251007ABCD1234",
    *,
    loja="L1",
    sku="dg053",
    cpf=CPF,
    nome="João da Silva",
    total=1000,
    situacao="15",  # Em andamento (despachado); 83953 = Entregue
):
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=numeroloja,
            loja=loja,
            data=datetime(2026, 9, 28, 15, tzinfo=UTC),
            total=total,
            situacao=situacao,
            item_index=0,
            item_codigo=sku,
            item_descricao="Celular Uranyx U1 128GB" if sku.startswith("dg") else "Outro",
            item_quantidade=1,
            documento_destinatario=cpf,
            nome_destinatario=nome,
        )
    )
    await db.commit()


async def _entrega(
    db,
    numero="300001",
    quando=datetime(2026, 10, 7, 18, tzinfo=UTC),
    *,
    campo="logistics_status",
    valor="LOGISTICS_DELIVERY_DONE",
    fonte="plataforma",
):
    lg = (
        await db.execute(select(Logistica).where(Logistica.pedido_bling == numero))
    ).scalar_one_or_none()
    if lg is None:
        lg = Logistica(pedido_bling=numero, plataforma="Shopee")
        db.add(lg)
    lg.meli_status = {campo: valor}
    lg.status_datas = {campo: {"em": quando.isoformat(), "fonte": fonte}}
    await db.commit()
    return lg


async def _nf(db, pedido="300001", numero="1234", *, serie="1", valor=1000, cpf=CPF, chave=None):
    db.add(
        NfNota(
            chave=chave or str(uuid.uuid4().int)[:44].ljust(44, "7"),
            pedido_bling=pedido,
            numero=numero,
            serie=serie,
            emitente_cnpj="12345678000199",
            destinatario_doc=cpf,
            destinatario_nome="JOAO DA SILVA",
            valor=Decimal(valor),
            data_emissao=datetime(2026, 9, 29, 10, tzinfo=UTC),
            situacao="100",
            xml=b"<nfeProc/>",
        )
    )
    await db.commit()


def _corpo(pedido="300001", nf="1234", nome="João da Silva", cpf="529.982.247-25", **extra):
    return {"pedido": pedido, "nf_numero": nf, "cliente_nome": nome, "cpf": cpf, **extra}


async def _admin(make_user, auth_as):
    u = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com")
    auth_as(u)
    return u


async def _cadastrar(client, **kw):
    r = await client.post("/api/garantias", json=_corpo(**kw))
    assert r.status_code == 201, r.text
    return r.json()


async def _conversa(db, pedido="251007ABCD1234", *, integration_id=None, mensagens=None):
    c = AtendimentoConversa(
        plataforma="shopee",
        canal="chat",
        conta="Loja Teste",
        externo_id=f"conv-{uuid.uuid4().hex[:8]}",
        comprador_nome="Comprador",
        pedido_marketplace=pedido,
        integration_id=integration_id,
        ultima_do_cliente_em=datetime(2026, 10, 7, 13, tzinfo=UTC),
    )
    db.add(c)
    await db.flush()
    padrao = [
        (
            "cliente",
            "cliente",
            "texto",
            "o celular não liga",
            datetime(2026, 10, 6, 12, tzinfo=UTC),
            [],
        ),
        (
            "loja",
            "externo",
            "texto",
            "pode mandar foto?",
            datetime(2026, 10, 6, 13, tzinfo=UTC),
            [],
        ),
        (
            "cliente",
            "cliente",
            "imagem",
            None,
            datetime(2026, 10, 7, 12, tzinfo=UTC),
            [{"tipo": "imagem", "url": "https://img.sp.mms.shopee.sg/foto-1"}],
        ),
        (
            "cliente",
            "cliente",
            "imagem",
            None,
            datetime(2026, 10, 7, 12, 5, tzinfo=UTC),
            [{"tipo": "imagem", "url": "https://img.sp.mms.shopee.sg/expirado-2"}],
        ),
        (
            "sistema",
            "sistema",
            "pedido",
            None,
            datetime(2026, 10, 7, 12, 30, tzinfo=UTC),
            [{"tipo": "pedido", "pedido": pedido}],
        ),
        ("cliente", "cliente", "texto", "e agora?", datetime(2026, 10, 7, 13, tzinfo=UTC), []),
    ]
    msgs = []
    for autor, origem, tipo, texto, quando, anexos in mensagens or padrao:
        m = AtendimentoMensagem(
            conversa_id=c.id,
            externo_id=uuid.uuid4().hex,
            autor=autor,
            origem=origem,
            tipo=tipo,
            texto=texto,
            enviada_em=quando,
            anexos=anexos,
        )
        db.add(m)
        msgs.append(m)
    await db.commit()
    return c, msgs


async def _log(db, garantia_id):
    return list(
        (
            await db.execute(
                select(GarantiaLog)
                .where(GarantiaLog.garantia_id == garantia_id)
                .order_by(GarantiaLog.id)
            )
        ).scalars()
    )


# ── §7: a data inicial vem da entrega e não se edita (RN01) ──────────────────


async def test_informar_o_pedido_preenche_a_entrega_e_os_prazos(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db, quando=datetime(2026, 10, 8, 1, 30, tzinfo=UTC))  # 07/10 22:30 em SP
    await _nf(db, valor=1000)
    await _nf(db, numero="88", serie="2", valor=3)  # a de embalagem
    u = await make_user(permissions={**CADASTRA, "garantias_cpf": {"view": True}})
    auth_as(u)

    # Pelo nº do marketplace (o que o atendente costuma ter na mão).
    r = await client.get("/api/garantias/pedido", params={"numero": "251007ABCD1234"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["pedido_bling"] == "300001"
    assert p["entrega"]["data"] == "2026-10-07"
    assert p["entrega"]["origem"] == "shopee"
    assert p["prazos"] == {
        "data_inicio": "2026-10-07",
        "fim_hardware": "2027-01-07",
        "fim_software": "2027-10-07",
        "status": "ativa",
        "status_rotulo": "Ativa",
        "entregue_sem_data": False,
    }
    assert p["produto_uranyx"] is True
    assert p["nf_sugerida"]["numero"] == "1234"  # a de produto, não a de R$ 3
    assert {n["papel"] for n in p["notas"]} == {"produto", "embalagem"}
    assert p["nome_sugerido"] == "JOAO DA SILVA" and p["nome_origem"] == "nf"
    assert p["cpf_sugerido"] == "529.982.247-25"
    assert p["cpf_sugerido_mascarado"] == "***.982.247-**"

    g = await _cadastrar(client)
    assert g["data_inicio"] == "2026-10-07"
    assert g["fim_hardware"] == "2027-01-07"
    assert g["fim_software"] == "2027-10-07"
    assert g["entrega"]["origem"] == "shopee"
    assert g["status"] == "ativa"
    assert g["nf_chave"] and g["nf_serie"] == "1"
    assert g["itens"] == [
        {"descricao": "Celular Uranyx U1 128GB", "sku": "dg053", "quantidade": 1, "uranyx": True}
    ]
    assert g["avisos"] == []


async def test_data_inicial_e_prazos_nao_vem_da_pessoa(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    for campo in ("data_inicio", "fim_hardware", "fim_software", "criado_em", "status"):
        r = await client.post("/api/garantias", json=_corpo(**{campo: "2020-01-01"}))
        assert r.status_code == 422, campo
    g = await _cadastrar(client)
    for campo in ("data_inicio", "fim_hardware", "fim_software", "criado_em"):
        r = await client.put(f"/api/garantias/{g['id']}", json={campo: "2020-01-01"})
        assert r.status_code == 422, campo
    r = await client.get(f"/api/garantias/{g['id']}")
    assert r.json()["data_inicio"] == "2026-10-07"


async def test_prazos_do_exemplo_30_11(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db, quando=datetime(2026, 11, 30, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    assert (g["data_inicio"], g["fim_hardware"], g["fim_software"]) == (
        "2026-11-30",
        "2027-02-28",
        "2027-11-30",
    )


async def test_data_de_cadastro_e_quem_cadastrou_sao_automaticos(client, db, make_user, auth_as):
    await _pedido(db)  # sem entrega: cadastro não depende dela (RN05)
    u = await make_user(permissions=CADASTRA)
    auth_as(u)
    antes = datetime.now(UTC)
    g = await _cadastrar(client)
    criado = datetime.fromisoformat(g["criado_em"])
    assert antes - timedelta(seconds=5) <= criado <= datetime.now(UTC) + timedelta(seconds=5)
    assert g["criado_por"]["id"] == str(u.id)
    assert g["data_inicio"] is None
    assert g["status"] == "aguardando_entrega"
    assert "aguardando_entrega" in g["avisos"]


# ── §7: NF, nome e CPF obrigatórios; CPF inválido bloqueado (RN06) ────────────


async def test_cadastro_exige_nf_nome_e_cpf(client, db, make_user, auth_as):
    await _pedido(db)
    await _admin(make_user, auth_as)
    for falta in ("nf_numero", "cliente_nome", "cpf", "pedido"):
        corpo = _corpo()
        corpo.pop(falta)
        r = await client.post("/api/garantias", json=corpo)
        assert r.status_code == 422, falta
    r = await client.post("/api/garantias", json=_corpo(nome="  "))
    assert r.status_code == 422


@pytest.mark.parametrize("cpf", ["529.982.247-24", "111.111.111-11", "5299822472a", "1234567890"])
async def test_cpf_invalido_e_bloqueado(client, db, make_user, auth_as, cpf):
    await _pedido(db)
    await _admin(make_user, auth_as)
    r = await client.post("/api/garantias", json=_corpo(cpf=cpf))
    assert r.status_code == 422
    if len(cpf) >= 11:
        assert r.json()["detail"] == {"code": "cpf_invalido", "campo": "cpf"}
    assert (await db.execute(select(func.count()).select_from(Garantia))).scalar_one() == 0


async def test_nf_invalida_e_pedido_inexistente(client, db, make_user, auth_as):
    await _pedido(db)
    await _admin(make_user, auth_as)
    r = await client.post("/api/garantias", json=_corpo(nf="NF abc"))
    assert r.json()["detail"] == {"code": "nf_invalida", "campo": "nf_numero"}
    r = await client.post("/api/garantias", json=_corpo(nf="0000"))
    assert r.json()["detail"]["code"] == "nf_invalida"
    r = await client.post("/api/garantias", json=_corpo(pedido="999999"))
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "pedido_nao_encontrado", "campo": "pedido"}


async def test_cpf_nao_repete_na_mesma_nf(client, db, make_user, auth_as):
    await _pedido(db)
    await _pedido(db, numero="300002", numeroloja="251007ZZZZ0002", cpf=CPF_2)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client, nf="000.001.234")
    assert g["nf_numero"] == "1234"
    r = await client.post("/api/garantias", json=_corpo(nf="1234"))
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "garantia_duplicada",
        "campo": "nf_numero",
        "garantia_id": g["id"],
    }
    # Série informada não escapa da série vazia (é a mesma nota do mesmo CPF).
    r = await client.post("/api/garantias", json=_corpo(nf="1234", nf_serie="1"))
    assert r.status_code == 409
    # Mesmo número de NF de OUTRO cliente (outro emitente): pode.
    await _cadastrar(client, pedido="300002", nf="1234", cpf=CPF_2, nome="Maria Souza")
    # Mesmo CPF, outra NF (segunda compra): pode — uma garantia por NF.
    await _cadastrar(client, nf="1299")


async def test_nf_com_chave_tem_uma_garantia_so(client, db, make_user, auth_as):
    await _pedido(db)
    await _nf(db, numero="1234", chave="3" * 44)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    assert g["nf_chave"] == "3" * 44
    # A mesma NF com outro CPF digitado: é a mesma nota (chave), recusa.
    r = await client.post("/api/garantias", json=_corpo(cpf=CPF_2))
    assert r.status_code == 409


async def test_avisos_que_nao_bloqueiam(client, db, make_user, auth_as):
    await _pedido(db, sku="i15pro", cpf=CPF_2)
    await _nf(db, numero="77", valor=3, cpf=CPF_2)  # embalagem
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client, nf="77")
    assert set(g["avisos"]) == {
        "pedido_sem_produto_uranyx",
        "cpf_diferente_do_pedido",
        "nf_de_embalagem",
    }
    g2 = await _cadastrar(client, nf="78")
    assert "nf_nao_e_do_pedido" in g2["avisos"]
    # O pedido já tinha garantia (a NF 77): o mesmo aparelho com duas — avisa.
    assert "pedido_ja_tem_garantia" in g2["avisos"]
    assert "pedido_ja_tem_garantia" not in g["avisos"]


# ── §7: status muda sozinho com a data ───────────────────────────────────────


async def test_status_muda_sozinho_conforme_a_data(client, db, make_user, auth_as, monkeypatch):
    await _pedido(db)
    await _entrega(db, quando=datetime(2026, 10, 7, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    esperado = [
        (date(2027, 1, 7), "ativa"),  # último dia do hardware, inclusive
        (date(2027, 1, 8), "somente_software"),
        (date(2027, 10, 7), "somente_software"),
        (date(2027, 10, 8), "expirada"),
    ]
    for dia, status in esperado:
        monkeypatch.setattr(svc, "hoje", lambda dia=dia: dia)
        assert (await client.get(f"/api/garantias/{g['id']}")).json()["status"] == status
        lista = (await client.post("/api/garantias/lista", json={"status": status})).json()
        assert [i["id"] for i in lista["itens"]] == [g["id"]], (dia, status)
    # Nada disso foi gravado: o status é da leitura.
    assert "status" not in Garantia.__table__.c


# ── Pontos 2 e 5: antes da entrega, e a entrega corrigida ────────────────────


async def test_aguardando_entrega_e_o_robo_completa_e_corrige(client, db, make_user, auth_as):
    await _pedido(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    assert g["status"] == "aguardando_entrega"

    # A entrega apareceu na Logística: o robô preenche (ponto 2).
    await _entrega(db, quando=datetime(2026, 10, 5, 15, tzinfo=UTC))
    assert await svc.recalcular_todas(db) == {"conferidas": 1, "encontradas": 1, "corrigidas": 0}
    await db.commit()
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert (d["data_inicio"], d["fim_hardware"], d["status"]) == (
        "2026-10-05",
        "2027-01-05",
        "ativa",
    )
    assert d["entrega"]["verificada_em"]

    # A plataforma corrigiu a data: recalcula e registra (ponto 5).
    await _entrega(db, quando=datetime(2026, 10, 4, 15, tzinfo=UTC))
    assert (await svc.recalcular_todas(db))["corrigidas"] == 1
    await db.commit()
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert (d["data_inicio"], d["fim_hardware"], d["fim_software"]) == (
        "2026-10-04",
        "2027-01-04",
        "2027-10-04",
    )
    robo = [x for x in await _log(db, g["id"]) if x.acao == "recalculou"]
    assert [(x.valor_anterior, x.valor_novo, x.user_id) for x in robo] == [
        (None, "05/10/2026", None),
        ("05/10/2026", "04/10/2026", None),
    ]
    assert robo[0].user_nome == svc.ROTULO_SISTEMA
    assert "corrigida" in robo[1].detalhe

    # Mesmo dia de novo: nada muda, nada no log.
    assert (await svc.recalcular_todas(db))["corrigidas"] == 0
    await db.commit()

    # A fonte PERDEU a data (TikTok DELIVERED → COMPLETED): não apaga.
    await _entrega(db, campo="order_status", valor="COMPLETED")
    assert await svc.recalcular_todas(db) == {"conferidas": 1, "encontradas": 0, "corrigidas": 0}
    await db.commit()
    assert (await client.get(f"/api/garantias/{g['id']}")).json()["data_inicio"] == "2026-10-04"
    assert len([x for x in await _log(db, g["id"]) if x.acao == "recalculou"]) == 2


async def test_rastreio_nao_corrige_data_da_plataforma(client, db, make_user, auth_as):
    await _pedido(db)
    lg = await _entrega(db, quando=datetime(2026, 10, 5, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    assert g["entrega"]["origem"] == "shopee"
    # Voltou para devolução: sai o carimbo da Shopee, fica o do 17track.
    lg.meli_status = {"logistics_status": "LOGISTICS_RETURNING"}
    lg.status_datas = {}
    lg.entregue_em = datetime(2026, 10, 6, 15, tzinfo=UTC)
    await db.commit()
    assert (await svc.recalcular_todas(db))["corrigidas"] == 0
    await db.commit()
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert (d["data_inicio"], d["entrega"]["origem"]) == ("2026-10-05", "shopee")
    # O contrário vale: a data da plataforma corrige a do rastreio.
    lg.meli_status = {"ship_status": "delivered"}
    lg.status_datas = {"ship_status": {"em": "2026-10-04T15:00:00+00:00", "fonte": "plataforma"}}
    await db.commit()
    await db.execute(
        text("UPDATE garantias SET entrega_origem = 'rastreio' WHERE id = :i"), {"i": g["id"]}
    )
    await db.commit()
    assert (await svc.recalcular_todas(db))["corrigidas"] == 1
    await db.commit()
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert (d["data_inicio"], d["entrega"]["origem"]) == ("2026-10-04", "ml")


async def test_ponto_5_desligado_so_preenche(client, db, make_user, auth_as, monkeypatch):
    monkeypatch.setattr(svc, "RECALCULAR_QUANDO_ENTREGA_MUDAR", False)
    await _pedido(db)
    await _entrega(db, quando=datetime(2026, 10, 5, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    await _entrega(db, quando=datetime(2026, 10, 4, 15, tzinfo=UTC))
    assert (await svc.recalcular_todas(db))["corrigidas"] == 0
    await db.commit()
    assert (await client.get(f"/api/garantias/{g['id']}")).json()["data_inicio"] == "2026-10-05"


async def test_ponto_2_desligado_recusa_sem_entrega(client, db, make_user, auth_as, monkeypatch):
    monkeypatch.setattr(svc, "CADASTRO_ANTES_DA_ENTREGA", False)
    await _pedido(db)
    await _admin(make_user, auth_as)
    r = await client.post("/api/garantias", json=_corpo())
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "pedido_sem_entrega", "campo": "pedido"}


async def test_conferir_entrega_agora(client, db, make_user, auth_as):
    await _pedido(db)
    u = await make_user(permissions=CADASTRA)
    auth_as(u)
    g = await _cadastrar(client)
    await _entrega(
        db, quando=datetime(2026, 10, 5, 15, tzinfo=UTC), campo="ship_status", valor="delivered"
    )
    r = await client.post(f"/api/garantias/{g['id']}/recalcular")
    assert r.status_code == 200
    assert r.json()["data_inicio"] == "2026-10-05"
    assert r.json()["entrega"]["origem"] == "ml"
    # A resposta de "conferir" não revela o CPF (só o GET do detalhe, que
    # grava "consultou").
    assert (r.json()["cpf"], r.json()["cpf_completo"]) == ("***.982.247-**", False)
    log = [x for x in await _log(db, g["id"]) if x.acao == "recalculou"]
    assert log[0].user_id == u.id


async def test_cron_do_recalculo_esta_registrado():
    from app import worker

    jobs = {c.coroutine.__name__: c for c in worker.WorkerSettings.cron_jobs}
    assert jobs["garantias_recalcular_entrega"].minute == 57
    assert worker.garantias_recalcular_entrega in worker.WorkerSettings.functions


# ── §7: busca por nome, CPF, NF e pedido, e filtro por status ────────────────


async def _tres_garantias(client, db):
    await _pedido(db, nome="João da Silva")
    await _pedido(db, numero="300002", numeroloja="251007ZZZZ0002", cpf=CPF_2, nome="Maria Souza")
    await _pedido(db, numero="300003", numeroloja="251007YYYY0003", cpf=CPF_3, nome="Pedro Lima")
    await _entrega(db, "300001", datetime(2026, 10, 1, 15, tzinfo=UTC))  # ativa
    await _entrega(db, "300002", datetime(2026, 6, 2, 15, tzinfo=UTC))  # só software
    a = await _cadastrar(client)
    b = await _cadastrar(client, pedido="300002", nf="10198", cpf=CPF_2, nome="Maria Souza")
    c = await _cadastrar(client, pedido="251007YYYY0003", nf="555", cpf=CPF_3, nome="Pedro Lima")
    return a, b, c


async def test_busca_por_nome_cpf_nf_e_pedido(client, db, make_user, auth_as):
    await _admin(make_user, auth_as)
    a, b, c = await _tres_garantias(client, db)

    async def ids(**params):
        r = await client.post("/api/garantias/lista", json=params)
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["itens"]]

    assert await ids(busca="joao") == [a["id"]]  # sem acento nem caixa
    assert await ids(busca="SOUZA") == [b["id"]]
    assert await ids(busca="111.444.777-35") == [b["id"]]  # CPF formatado
    assert await ids(busca=CPF_3) == [c["id"]]
    assert await ids(busca="982247") == []  # pedaço de CPF não busca
    assert await ids(busca="10198") == [b["id"]]  # NF
    assert await ids(busca="00010198") == [b["id"]]
    assert await ids(busca="300003") == [c["id"]]  # pedido Bling
    assert await ids(busca="251007ZZZZ0002") == [b["id"]]  # pedido marketplace
    assert sorted(await ids()) == sorted([a["id"], b["id"], c["id"]])


async def test_filtro_por_status_periodo_e_indicadores(client, db, make_user, auth_as, monkeypatch):
    await _admin(make_user, auth_as)
    a, b, c = await _tres_garantias(client, db)

    async def lista(**params):
        r = await client.post("/api/garantias/lista", json=params)
        assert r.status_code == 200, r.text
        return r.json()

    assert [i["id"] for i in (await lista(status="ativa"))["itens"]] == [a["id"]]
    assert [i["id"] for i in (await lista(status="somente_software"))["itens"]] == [b["id"]]
    assert [i["id"] for i in (await lista(status="aguardando_entrega"))["itens"]] == [c["id"]]
    assert (await lista(status="expirada"))["itens"] == []
    assert (await client.post("/api/garantias/lista", json={"status": "x"})).status_code == 422
    # Período pela entrega.
    entrega = await lista(periodo="entrega", de="2026-09-01", ate="2026-10-31")
    assert [i["id"] for i in entrega["itens"]] == [a["id"]]
    # Período pelo cadastro (o dia de hoje em São Paulo).
    agora = datetime.now(svc.SAO_PAULO).date().isoformat()
    cad = await lista(periodo="cadastro", de=agora, ate=agora)
    assert cad["total"] == 3
    ontem = (datetime.now(svc.SAO_PAULO).date() - timedelta(days=1)).isoformat()
    assert (await lista(periodo="cadastro", ate=ontem))["total"] == 0

    ind = (await lista(busca="joao"))["indicadores"]  # o topo ignora os filtros
    assert ind == {
        "ativas": 1,
        "somente_software": 1,
        "hw_vence_30d": 0,
        "atendimentos_no_mes": 0,
        "aguardando_entrega": 1,
        "entregue_sem_data": 0,  # o pedido 300003 está Em andamento no Bling
        "expiradas": 0,
        "total": 3,
    }
    # Entrega 01/10 → hardware até 01/01/2027: em 15/12 faltam ≤ 30 dias.
    monkeypatch.setattr(svc, "hoje", lambda: date(2026, 12, 15))
    ind = (await lista())["indicadores"]
    assert ind["hw_vence_30d"] == 1 and ind["ativas"] == 1
    assert [i["id"] for i in (await lista(status="hw_vence_30d"))["itens"]] == [a["id"]]


async def test_lista_mostra_cpf_mascarado(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    u = await make_user(permissions={**CADASTRA, "garantias_cpf": {"view": True}})
    auth_as(u)
    g = await _cadastrar(client)
    r = await client.post("/api/garantias/lista", json={})
    linha = r.json()["itens"][0]
    assert linha["cpf_mascarado"] == "***.982.247-**"
    assert "cpf" not in linha
    assert CPF not in r.text and "529.982.247-25" not in r.text
    # A busca para vincular também mascara.
    r = await client.post("/api/garantias/busca", json={"q": CPF})
    assert [x["id"] for x in r.json()] == [g["id"]]
    assert CPF not in r.text


# ── §6: CPF completo só no detalhe e para quem pode; log de quem consultou ───


async def test_detalhe_cpf_completo_so_para_autorizado_e_log(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    adm = await _admin(make_user, auth_as)
    g = await _cadastrar(client)

    quem_consulta = await make_user(permissions=CONSULTA)
    auth_as(quem_consulta)
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert d["cpf"] == "***.982.247-**" and d["cpf_completo"] is False
    assert CPF not in str(d)
    assert d["permissoes"] == {
        "cadastrar": False,
        "registrar_atendimento": False,
        "ver_cpf": False,
        "ver_log": False,
    }

    quem_ve = await make_user(permissions=VE_CPF)
    auth_as(quem_ve)
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert d["cpf"] == "529.982.247-25" and d["cpf_completo"] is True

    consultas = [x for x in await _log(db, g["id"]) if x.acao == "consultou"]
    assert [(x.user_id, x.detalhe) for x in consultas] == [
        (quem_consulta.id, "CPF mascarado"),
        (quem_ve.id, "viu o CPF completo"),
    ]
    # O log é do admin.
    r = await client.get(f"/api/garantias/{g['id']}/log")
    assert r.status_code == 403
    auth_as(adm)
    r = await client.get(f"/api/garantias/{g['id']}/log")
    assert r.status_code == 200
    acoes = [x["acao"] for x in r.json()]
    assert acoes[-1] == "cadastrou" and acoes.count("consultou") == 2
    assert CPF not in r.text


async def test_correcao_registra_quem_alterou_com_cpf_mascarado(client, db, make_user, auth_as):
    await _pedido(db)
    await _pedido(db, numero="300002", numeroloja="251007ZZZZ0002", cpf=CPF_2)
    await _entrega(db)
    await _entrega(db, "300002", datetime(2026, 9, 30, 15, tzinfo=UTC))
    u = await make_user(permissions=CADASTRA)
    auth_as(u)
    g = await _cadastrar(client)
    r = await client.put(
        f"/api/garantias/{g['id']}",
        json={"cpf": "111.444.777-35", "cliente_nome": "Maria Souza", "nf_numero": "999"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["cliente_nome"] == "Maria Souza"
    alteracoes = {x.campo: x for x in await _log(db, g["id"]) if x.acao == "alterou"}
    assert alteracoes["cpf"].valor_anterior == "***.982.247-**"
    assert alteracoes["cpf"].valor_novo == "***.444.777-**"
    assert alteracoes["nf_numero"].valor_novo == "999"
    assert alteracoes["cliente_nome"].user_id == u.id
    # Trocar o pedido relê a entrega e recalcula os prazos.
    r = await client.put(f"/api/garantias/{g['id']}", json={"pedido": "251007ZZZZ0002"})
    assert r.status_code == 200, r.text
    assert (r.json()["pedido_bling"], r.json()["data_inicio"]) == ("300002", "2026-09-30")
    campos = [x.campo for x in await _log(db, g["id"]) if x.acao == "alterou"]
    assert "pedido" in campos and "data_inicio" in campos
    # CPF inválido na correção também é bloqueado.
    r = await client.put(f"/api/garantias/{g['id']}", json={"cpf": "111.111.111-11"})
    assert r.json()["detail"]["code"] == "cpf_invalido"


# ── Permissões (§6) ──────────────────────────────────────────────────────────


async def test_permissoes_cadastrar_consultar_registrar(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    vinculo = {"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "troca"}

    ninguem = await make_user()
    auth_as(ninguem)
    assert (await client.post("/api/garantias/lista", json={})).status_code == 403
    assert (await client.get(f"/api/garantias/{g['id']}")).status_code == 403
    assert (await client.post("/api/garantias/busca", json={"q": "x"})).status_code == 403

    consulta = await make_user(permissions=CONSULTA)
    auth_as(consulta)
    assert (await client.post("/api/garantias/lista", json={})).status_code == 200
    assert (await client.post("/api/garantias", json=_corpo(nf="9"))).status_code == 403
    assert (
        await client.put(f"/api/garantias/{g['id']}", json={"nf_numero": "9"})
    ).status_code == 403
    r = await client.post(f"/api/garantias/{g['id']}/atendimentos", json=vinculo)
    assert r.status_code == 403

    # Só o `view` de "Registrar atendimento" (a tela mostra a caixinha): não
    # libera nada — o documento não tem esse nível.
    so_view = await make_user(permissions={"garantias_atendimento": {"view": True}})
    auth_as(so_view)
    assert (await client.post("/api/garantias/busca", json={"q": "300001"})).status_code == 403
    assert (await client.get(f"/api/garantias/conversa/{c.id}")).status_code == 403
    assert (await client.get("/api/garantias/regras")).status_code == 403

    registra = await make_user(permissions=REGISTRA)
    auth_as(registra)
    assert (
        await client.post("/api/garantias/lista", json={})
    ).status_code == 403  # não abre o painel
    assert (await client.post("/api/garantias/busca", json={"q": "300001"})).status_code == 200
    assert (await client.get(f"/api/garantias/conversa/{c.id}")).status_code == 200
    r = await client.post(f"/api/garantias/{g['id']}/atendimentos", json=vinculo)
    assert r.status_code == 201, r.text

    cadastra = await make_user(permissions=CADASTRA)
    auth_as(cadastra)
    assert (await client.post("/api/garantias", json=_corpo(nf="9"))).status_code == 201
    assert (
        await client.get("/api/garantias/pedido", params={"numero": "300001"})
    ).status_code == 200


# ── §5 / §7: vincular no Comunicador e ver na aba Atendimentos ───────────────


async def test_vincular_atendimento_copia_a_conversa(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db, quando=datetime(2026, 10, 1, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, msgs = await _conversa(db)
    atendente = await make_user(permissions=ATENDENTE, email="ana@davinci-test.com")
    auth_as(atendente)
    r = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={
            "conversa_id": str(c.id),
            "tipo_problema": "hardware",
            "solucao": "Enviar para a assistência; etiqueta de postagem gerada.",
        },
    )
    assert r.status_code == 201, r.text
    a = r.json()
    # Data/hora = a 1ª mensagem do cliente no trecho atual da conversa
    # (relógio da plataforma): 06/10 12:00 — a loja respondeu e o cliente
    # voltou no dia seguinte, sem pausa de mais de 7 dias.
    assert datetime.fromisoformat(a["data_atendimento"]) == datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert a["atendente"] == {"id": str(atendente.id), "nome": "ana@davinci-test.com"}
    assert a["conversa_id"] == str(c.id)
    assert a["conversa_link"] == f"/atendimento?conversa={c.id}"
    assert a["conversa_plataforma"] == "shopee" and a["conversa_pedido"] == "251007ABCD1234"
    # Mensagens copiadas, sem a de sistema, em ordem.
    assert [m["texto"] for m in a["mensagens"]] == [
        "o celular não liga",
        "pode mandar foto?",
        None,
        None,
        "e agora?",
    ]
    assert a["resumo"] == "o celular não liga / e agora?"
    # Anexos: o que baixou fica guardado; o link expirado fica com o motivo.
    foto, expirada = a["anexos"]
    assert foto["baixado"] is True and foto["arquivo_url"].startswith("/api/garantias/anexos/")
    assert expirada == {
        "mensagem_id": str(msgs[3].id),
        "tipo": "imagem",
        "nome": None,
        "url_original": "https://img.sp.mms.shopee.sg/expirado-2",
        "baixado": False,
        "motivo": "link_expirado",
        "arquivo_url": None,
        "content_type": None,
        "tamanho": None,
    }
    arq = await client.get(foto["arquivo_url"])
    assert arq.status_code == 200 and arq.content.startswith(b"\x89PNG")
    assert arq.headers["content-type"] == "image/png"
    # Cobertura: 07/10 ≤ fim do hardware (01/01/2027).
    assert (a["cobertura"], a["cobertura_rotulo"], a["fim_considerado"]) == (
        "coberto",
        "Coberto",
        "2027-01-01",
    )
    # §7: aparece na aba Atendimentos da garantia.
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert d["atendimentos"] == 1
    assert [x["id"] for x in d["atendimentos_lista"]] == [a["id"]]
    assert d["atendimentos_lista"][0]["cobertura"] == "coberto"
    assert d["atendimentos_lista"][0]["anexos"][0]["baixado"] is True
    log = [x for x in await _log(db, g["id"]) if x.acao == "registrou_atendimento"]
    assert log[0].user_id == atendente.id
    # Nada foi gravado nas tabelas do /atendimento.
    assert (
        await db.execute(
            select(func.count())
            .select_from(AtendimentoMensagem)
            .where(AtendimentoMensagem.conversa_id == c.id)
        )
    ).scalar_one() == len(msgs)


async def test_cobertura_fora_da_garantia_pelo_tipo(client, db, make_user, auth_as):
    await _pedido(db)
    # Entrega em 01/06/2026: hardware até 01/09/2026, software até 01/06/2027.
    await _entrega(db, quando=datetime(2026, 6, 1, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    hw = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "orçamento"},
    )
    sw = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={"conversa_id": str(c.id), "tipo_problema": "software", "solucao": "atualização"},
    )
    assert hw.json()["cobertura"] == "fora_da_garantia"
    assert hw.json()["cobertura_rotulo"] == "Fora da garantia"
    assert hw.json()["fim_considerado"] == "2026-09-01"
    assert sw.json()["cobertura"] == "coberto"
    assert sw.json()["fim_considerado"] == "2027-06-01"
    lista = (await client.get(f"/api/garantias/{g['id']}")).json()["atendimentos_lista"]
    assert len(lista) == 2  # uma garantia, vários atendimentos


async def test_atendimento_antes_da_entrega_e_cobertura_de_hoje(client, db, make_user, auth_as):
    await _pedido(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    r = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "aguardar"},
    )
    assert r.json()["cobertura"] == "sem_data_de_entrega"
    assert r.json()["cobertura_hoje"] == "sem_data_de_entrega"
    await _entrega(db, quando=datetime(2026, 10, 2, 15, tzinfo=UTC))
    await svc.recalcular_todas(db)
    await db.commit()
    at = (await client.get(f"/api/garantias/{g['id']}")).json()["atendimentos_lista"][0]
    assert at["cobertura"] == "sem_data_de_entrega"  # congelada no vínculo
    assert at["cobertura_hoje"] == "coberto"  # com a data que apareceu


async def test_mensagens_escolhidas_definem_o_que_vai(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, msgs = await _conversa(db)
    r = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={
            "conversa_id": str(c.id),
            "tipo_problema": "software",
            "solucao": "reset de fábrica orientado",
            "resumo": "Cliente relata travamento.",
            "mensagem_ids": [str(msgs[1].id), str(msgs[0].id)],
        },
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert [m["id"] for m in a["mensagens"]] == [str(msgs[0].id), str(msgs[1].id)]
    assert datetime.fromisoformat(a["data_atendimento"]) == datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert a["resumo"] == "Cliente relata travamento."
    assert a["anexos"] == []
    outra, outras_msgs = await _conversa(db)
    r = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={
            "conversa_id": str(c.id),
            "tipo_problema": "software",
            "solucao": "x" * 5,
            "mensagem_ids": [str(outras_msgs[0].id)],  # de outra conversa
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "mensagem_nao_encontrada"


async def test_clique_duplo_nao_duplica_atendimento(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    corpo = {"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "troca"}
    primeiro = await client.post(f"/api/garantias/{g['id']}/atendimentos", json=corpo)
    r = await client.post(f"/api/garantias/{g['id']}/atendimentos", json=corpo)
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "atendimento_repetido",
        "atendimento_id": primeiro.json()["id"],
    }


async def test_conversa_inexistente_ou_instagram(client, db, make_user, auth_as):
    await _pedido(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    for cid in (str(uuid.uuid4()), "ig:123", "lixo"):
        r = await client.post(
            f"/api/garantias/{g['id']}/atendimentos",
            json={"conversa_id": cid, "tipo_problema": "hardware", "solucao": "troca"},
        )
        assert r.status_code == 404, cid
        assert r.json()["detail"]["code"] == "conversa_nao_encontrada"


# ── §5.3: só adicionar, nunca editar nem apagar ──────────────────────────────


async def test_atendimento_so_pode_ser_adicionado(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    a = (
        await client.post(
            f"/api/garantias/{g['id']}/atendimentos",
            json={"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "troca"},
        )
    ).json()
    # Nenhuma rota de editar/apagar.
    base = f"/api/garantias/{g['id']}/atendimentos"
    assert (await client.put(f"{base}/{a['id']}", json={"solucao": "y"})).status_code in (404, 405)
    assert (await client.delete(f"{base}/{a['id']}")).status_code in (404, 405)
    assert (await client.delete(f"/api/garantias/{g['id']}")).status_code == 405
    # E o banco recusa, mesmo por fora da API.
    comandos = [
        "UPDATE garantia_atendimentos SET solucao = 'outra'",
        "DELETE FROM garantia_atendimentos",
        "UPDATE garantia_atendimento_anexos SET nome = 'x'",
        "DELETE FROM garantia_atendimento_anexos",
        "UPDATE garantia_log SET detalhe = 'x'",
        "DELETE FROM garantia_log",
        "TRUNCATE garantia_atendimentos CASCADE",
        "TRUNCATE garantia_log",
    ]
    for comando in comandos:
        with pytest.raises(Exception, match="garantia_so_insercao"):
            await db.execute(text(comando))
        await db.rollback()
    n = (await db.execute(select(func.count()).select_from(GarantiaAtendimento))).scalar_one()
    assert n == 1
    assert (
        await db.execute(select(func.count()).select_from(GarantiaAtendimentoAnexo))
    ).scalar_one() == 1


# ── §5.3: alerta de CPF sem garantia ─────────────────────────────────────────


async def test_alerta_de_cpf_sem_garantia_na_conversa(client, db, make_user, auth_as):
    await _pedido(db)
    await _pedido(db, numero="300009", numeroloja="251007NAOURANYX", sku="i15pro", cpf=CPF_3)
    await _entrega(db)
    c, _ = await _conversa(db)
    atendente = await make_user(permissions=REGISTRA)
    auth_as(atendente)
    r = await client.get(f"/api/garantias/conversa/{c.id}")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["alerta_cpf_sem_garantia"] is True
    assert s["cpf_conhecido"] is True and s["produto_uranyx"] is True
    assert s["busca_sugerida"] == "300001"  # ponto 4: a busca já vem preenchida
    assert s["garantias"] == [] and s["vinculos"] == []
    assert CPF not in r.text and "982.247" not in r.text  # o CPF nunca sai daqui

    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    auth_as(atendente)
    s = (await client.get(f"/api/garantias/conversa/{c.id}")).json()
    assert s["alerta_cpf_sem_garantia"] is False
    assert [x["id"] for x in s["garantias"]] == [g["id"]]

    # Pedido sem produto Uranyx: sem alerta.
    c2, _ = await _conversa(db, pedido="251007NAOURANYX")
    s = (await client.get(f"/api/garantias/conversa/{c2.id}")).json()
    assert s["pedido_encontrado"] is True and s["alerta_cpf_sem_garantia"] is False
    # Conversa sem pedido: sem alerta, sem busca.
    c3, _ = await _conversa(db, pedido=None)
    s = (await client.get(f"/api/garantias/conversa/{c3.id}")).json()
    assert (s["pedido_encontrado"], s["alerta_cpf_sem_garantia"], s["busca_sugerida"]) == (
        False,
        False,
        None,
    )


async def test_vinculos_da_conversa_aparecem_no_bloco(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    a = (
        await client.post(
            f"/api/garantias/{g['id']}/atendimentos",
            json={"conversa_id": str(c.id), "tipo_problema": "software", "solucao": "update"},
        )
    ).json()
    s = (await client.get(f"/api/garantias/conversa/{c.id}")).json()
    assert [(v["atendimento_id"], v["garantia_id"]) for v in s["vinculos"]] == [(a["id"], g["id"])]
    ind = (await client.post("/api/garantias/lista", json={})).json()["indicadores"]
    assert ind["atendimentos_no_mes"] == 1
    lista = (
        await client.post("/api/garantias/lista", json={"com_atendimento_no_mes": True})
    ).json()
    assert [i["id"] for i in lista["itens"]] == [g["id"]]
    assert lista["itens"][0]["atendimentos"] == 1


# ── Escopo por equipe ────────────────────────────────────────────────────────


async def test_equipe_ve_so_as_garantias_das_suas_lojas(client, db, make_user, auth_as):
    dono = await _admin(make_user, auth_as)
    db.add(StoreInfo(user_id=dono.id, platform="shopee", bling_store_id="L1", sales_team=1))
    db.add(StoreInfo(user_id=dono.id, platform="shopee", bling_store_id="L2", sales_team=2))
    await db.commit()
    await _pedido(db)
    await _pedido(db, numero="300002", numeroloja="251007ZZZZ0002", loja="L2", cpf=CPF_2)
    g1 = await _cadastrar(client)
    g2 = await _cadastrar(client, pedido="300002", cpf=CPF_2, nome="Maria Souza")

    equipe2 = await make_user(permissions={**CADASTRA, **REGISTRA})
    equipe2.sales_teams = [2]
    await db.commit()
    auth_as(equipe2)
    lista = (await client.post("/api/garantias/lista", json={})).json()
    assert [i["id"] for i in lista["itens"]] == [g2["id"]]
    assert lista["indicadores"]["total"] == 1
    assert (await client.get(f"/api/garantias/{g1['id']}")).status_code == 404
    assert (await client.post("/api/garantias/busca", json={"q": "300001"})).json() == []
    r = await client.get("/api/garantias/pedido", params={"numero": "300001"})
    assert r.status_code == 404
    r = await client.post("/api/garantias", json=_corpo(nf="77"))
    assert r.json()["detail"]["code"] == "pedido_nao_encontrado"
    # RN06 vale entre equipes, mas o 409 não entrega o id (nem confirma o
    # CPF) de uma garantia que a pessoa não enxerga.
    r = await client.post(
        "/api/garantias", json=_corpo(pedido="300002", nf="1234", nome="Maria Souza")
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "garantia_duplicada", "campo": "nf_numero"}
    r = await client.post(
        "/api/garantias", json=_corpo(pedido="300002", nf="1234", cpf=CPF_2, nome="Maria")
    )
    assert r.json()["detail"]["garantia_id"] == g2["id"]  # a da equipe dela, sim


# ── LGPD no Histórico ────────────────────────────────────────────────────────


def test_historico_nao_copia_nome_cpf_nem_corpo():
    tabelas = ["garantias", "garantia_atendimentos", "garantia_atendimento_anexos", "garantia_log"]
    assert hsql.a_cobrir(tabelas) == []
    for caminho in (
        "/api/garantias",
        "/api/garantias/lista",
        "/api/garantias/busca",
        "/api/garantias/12",
        "/api/garantias/12/atendimentos",
        "/api/garantias/12/recalcular",
    ):
        assert nomes.SEM_CORPO.search(caminho), caminho
    assert ("GET", "/api/garantias/{garantia_id}") in nomes.REVELACOES
    # O "ao informar o pedido" mostra nome/CPF do pedido antes de haver garantia.
    assert ("GET", "/api/garantias/pedido") in nomes.REVELACOES
    assert ("POST", "/api/garantias") in nomes.ACOES
    assert nomes.tela_da_api("/api/garantias/3") == "Pós-venda › Garantias"


async def test_regras_para_a_tela(client, make_user, auth_as):
    u = await make_user(permissions=REGISTRA)
    auth_as(u)
    r = await client.get("/api/garantias/regras")
    assert r.status_code == 200
    j = r.json()
    assert (j["meses_hardware"], j["meses_software"], j["ultimo_dia_coberto"]) == (3, 12, True)
    assert j["dias_pausa_atendimento"] == svc.DIAS_PAUSA_NOVO_ATENDIMENTO == 7
    assert j["status"]["somente_software"] == "Somente software"
    assert j["origens_entrega"]["shopee"]


# ── Correções da revisão (07/10/2026) ────────────────────────────────────────


async def test_entregue_no_bling_sem_data_no_davinci(client, db, make_user, auth_as):
    """Pedido que o Bling dá como Entregue sem data em nenhuma fonte (a
    Logística só tem data desde 15/07/2026): continua sem prazo (RN01), mas a
    tela diz que já foi entregue — não "aguardando"."""
    await _pedido(db, situacao="83953")
    await _pedido(db, numero="300002", numeroloja="251007ZZZZ0002", cpf=CPF_2)  # Em andamento
    await _admin(make_user, auth_as)
    p = (await client.get("/api/garantias/pedido", params={"numero": "300001"})).json()
    assert "entregue_sem_data" in p["avisos"] and "aguardando_entrega" not in p["avisos"]
    assert (
        p["prazos"]["status"],
        p["prazos"]["status_rotulo"],
        p["prazos"]["entregue_sem_data"],
    ) == (
        "aguardando_entrega",
        "Entregue — sem data no DaVinci",
        True,
    )
    a = await _cadastrar(client)
    assert "entregue_sem_data" in a["avisos"]
    assert (a["status"], a["status_rotulo"], a["entregue_sem_data"]) == (
        "aguardando_entrega",
        "Entregue — sem data no DaVinci",
        True,
    )
    b = await _cadastrar(client, pedido="300002", cpf=CPF_2, nome="Maria Souza")
    assert (b["status_rotulo"], b["entregue_sem_data"]) == ("Aguardando entrega", False)
    assert "aguardando_entrega" in b["avisos"]

    lista = (await client.post("/api/garantias/lista", json={})).json()
    por_id = {i["id"]: i for i in lista["itens"]}
    assert por_id[a["id"]]["entregue_sem_data"] is True
    assert por_id[b["id"]]["entregue_sem_data"] is False
    assert (
        lista["indicadores"]["aguardando_entrega"],
        lista["indicadores"]["entregue_sem_data"],
    ) == (
        2,
        1,
    )
    so = (await client.post("/api/garantias/lista", json={"status": "entregue_sem_data"})).json()
    assert [i["id"] for i in so["itens"]] == [a["id"]]
    # A data apareceu: deixa de ser "sem data".
    await _entrega(db, "300001", datetime(2026, 10, 2, 15, tzinfo=UTC))
    await svc.recalcular_todas(db)
    await db.commit()
    d = (await client.get(f"/api/garantias/{a['id']}")).json()
    assert (d["status"], d["entregue_sem_data"]) == ("ativa", False)


async def test_busca_por_cpf_tem_teto_e_fica_no_log(
    client, db, make_user, auth_as, teto_cpf, monkeypatch
):
    """A máscara ***.456.789-** deixa 1.000 candidatos: a busca pelo CPF
    completo tem teto por pessoa e deixa rastro no log da garantia achada."""
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    u = await make_user(permissions=ATENDENTE)
    auth_as(u)
    r = await client.post("/api/garantias/lista", json={"busca": "529.982.247-25"})
    assert [i["id"] for i in r.json()["itens"]] == [g["id"]]
    r = await client.post("/api/garantias/busca", json={"q": CPF})
    assert [i["id"] for i in r.json()] == [g["id"]]
    # Nome, NF, pedido e 11 dígitos que não são CPF não contam no teto.
    for termo in ("joao", "1234", "300001", "52998224724"):
        assert (await client.post("/api/garantias/lista", json={"busca": termo})).status_code == 200
    chaves = [f"garantias:busca_cpf:{u.id}:{janela}" for _lim, janela in svc.LIMITES_BUSCA_CPF]
    assert [k for k, *_ in teto_cpf] == chaves * 2
    assert {(lim, jan) for _k, lim, jan in teto_cpf} == set(svc.LIMITES_BUSCA_CPF)
    buscas = [x for x in await _log(db, g["id"]) if x.acao == "buscou_cpf"]
    assert [(x.user_id, x.detalhe) for x in buscas] == [
        (u.id, "***.982.247-** · busca na lista"),
        (u.id, "***.982.247-** · busca do vínculo"),
    ]

    # Estourou o teto: 429 com o tempo de espera, sem buscar.
    async def _estourou(*, key, limit, window_seconds):
        raise rota.RateLimitError(retry_after=42)

    monkeypatch.setattr(rota, "sliding_window_check", _estourou)
    r = await client.post("/api/garantias/lista", json={"busca": CPF})
    assert r.status_code == 429
    assert r.json()["detail"] == {"code": "muitas_buscas_por_cpf", "retry_after": 42}
    assert r.headers["retry-after"] == "42"
    assert (await client.post("/api/garantias/busca", json={"q": CPF})).status_code == 429
    assert (await client.post("/api/garantias/lista", json={"busca": "joao"})).status_code == 200
    assert len([x for x in await _log(db, g["id"]) if x.acao == "buscou_cpf"]) == 2

    # Redis fora do ar: não trava o atendimento.
    async def _caiu(**_k):
        raise ConnectionError("redis")

    monkeypatch.setattr(rota, "sliding_window_check", _caiu)
    assert (await client.post("/api/garantias/busca", json={"q": CPF})).status_code == 200
    # O termo não anda mais na URL: não há GET da lista nem da busca.
    assert (await client.get("/api/garantias", params={"busca": CPF})).status_code == 405
    assert (await client.get("/api/garantias/busca", params={"q": CPF})).status_code != 200


async def test_cpf_completo_so_no_get_do_detalhe(client, db, make_user, auth_as):
    """Cadastrar, corrigir (mesmo sem mudança) e conferir a entrega
    respondem com o CPF mascarado; só o GET do detalhe revela — e grava
    "consultou"."""
    await _pedido(db)
    await _entrega(db)
    u = await make_user(permissions={**CADASTRA, "garantias_cpf": {"view": True}})
    auth_as(u)
    g = await _cadastrar(client)
    assert (g["cpf"], g["cpf_completo"], g["permissoes"]["ver_cpf"]) == (
        "***.982.247-**",
        False,
        True,
    )
    respostas = [
        await client.put(f"/api/garantias/{g['id']}", json={}),
        await client.put(f"/api/garantias/{g['id']}", json={"cliente_nome": "João Silva"}),
        await client.post(f"/api/garantias/{g['id']}/recalcular"),
    ]
    for r in respostas:
        assert r.status_code == 200, r.text
        assert CPF not in r.text and "529.982.247-25" not in r.text
    assert not [x for x in await _log(db, g["id"]) if x.acao == "consultou"]
    d = (await client.get(f"/api/garantias/{g['id']}")).json()
    assert (d["cpf"], d["cpf_completo"]) == ("529.982.247-25", True)
    consultas = [x.detalhe for x in await _log(db, g["id"]) if x.acao == "consultou"]
    assert consultas == ["viu o CPF completo"]


async def test_data_do_atendimento_e_o_inicio_do_trecho(client, db, make_user, auth_as):
    """Sem mensagens escolhidas, a data é a 1ª do cliente no trecho atual da
    conversa (pausa de até 7 dias não abre trecho novo): a reclamação aberta
    dentro do prazo continua Coberta se o vídeo chega depois do fim, e a
    conversa de 3 meses antes não puxa a data para trás."""
    await _pedido(db)
    await _entrega(db, quando=datetime(2026, 7, 6, 15, tzinfo=UTC))
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    assert g["fim_hardware"] == "2026-10-06"
    msgs = [
        (
            "cliente",
            "cliente",
            "texto",
            "chegou certinho",
            datetime(2026, 7, 7, 12, tzinfo=UTC),
            [],
        ),
        ("loja", "externo", "texto", "obrigado!", datetime(2026, 7, 7, 13, tzinfo=UTC), []),
        ("cliente", "cliente", "texto", "a tela apagou", datetime(2026, 10, 5, 12, tzinfo=UTC), []),
        ("loja", "externo", "texto", "manda um vídeo?", datetime(2026, 10, 5, 13, tzinfo=UTC), []),
        (
            "mediador",
            "sistema",
            "texto",
            "mediação aberta",
            datetime(2026, 10, 6, 9, tzinfo=UTC),
            [],
        ),
        ("cliente", "cliente", "video", None, datetime(2026, 10, 8, 12, tzinfo=UTC), []),
    ]
    c, _ = await _conversa(db, mensagens=msgs)
    r = await client.post(
        f"/api/garantias/{g['id']}/atendimentos",
        json={"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "assistência"},
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert datetime.fromisoformat(a["data_atendimento"]) == datetime(2026, 10, 5, 12, tzinfo=UTC)
    assert (a["cobertura"], a["fim_considerado"]) == ("coberto", "2026-10-06")
    # A fala do mediador (ML) vai na cópia, como na lista de escolha da tela.
    assert "mediação aberta" in [m["texto"] for m in a["mensagens"]]


async def test_anexo_e_servido_sem_poder_rodar_script(client, db, make_user, auth_as):
    await _pedido(db)
    await _entrega(db)
    await _admin(make_user, auth_as)
    g = await _cadastrar(client)
    c, _ = await _conversa(db)
    a = (
        await client.post(
            f"/api/garantias/{g['id']}/atendimentos",
            json={"conversa_id": str(c.id), "tipo_problema": "hardware", "solucao": "troca"},
        )
    ).json()
    foto = await client.get(a["anexos"][0]["arquivo_url"])
    assert foto.headers["content-security-policy"] == "sandbox"
    assert foto.headers["content-disposition"].startswith("inline;")
    assert foto.headers["x-content-type-options"] == "nosniff"
    # Um arquivo que é documento (gravado antes da regra, ou por fora): baixa,
    # com sandbox. PDF abre na aba (o leitor de PDF não abre com sandbox).
    for tipo, disp, csp in (
        ("image/svg+xml", "attachment;", "sandbox"),
        ("text/html", "attachment;", "sandbox"),
        ("application/pdf", "inline;", None),
    ):
        ax = GarantiaAtendimentoAnexo(
            atendimento_id=a["id"],
            tipo="arquivo",
            nome='x"\r\n.svg',
            content_type=tipo,
            tamanho=5,
            blob=b"<svg/>",
        )
        db.add(ax)
        await db.commit()
        r = await client.get(f"/api/garantias/anexos/{ax.id}")
        # Aspas e quebra de linha do nome não escapam do cabeçalho.
        assert r.headers["content-disposition"] == f'{disp} filename="x.svg"', tipo
        assert r.headers.get("content-security-policy") == csp, tipo


def test_log_de_acesso_nao_guarda_a_query_das_garantias():
    import logging

    rota.mascarar_query_no_access_log()
    acesso = logging.getLogger("uvicorn.access")

    def linha(caminho: str) -> str:
        rec = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4:5", "GET", caminho, "1.1", 200),
            None,
        )
        for f in acesso.filters:
            f.filter(rec)
        return rec.getMessage()

    assert linha("/api/garantias/pedido?numero=300001&x=52998224725") == (
        '1.2.3.4:5 - "GET /api/garantias/pedido?numero=***&x=*** HTTP/1.1" 200'
    )
    assert (
        linha("/api/garantias?busca=joao")
        == '1.2.3.4:5 - "GET /api/garantias?busca=*** HTTP/1.1" 200'
    )
    assert linha("/api/outra?busca=joao") == '1.2.3.4:5 - "GET /api/outra?busca=joao HTTP/1.1" 200'
    assert linha("/api/garantias/12") == '1.2.3.4:5 - "GET /api/garantias/12 HTTP/1.1" 200'
