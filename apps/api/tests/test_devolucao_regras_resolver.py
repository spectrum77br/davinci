"""Regras do Resolver do painel novo (Vinicius, 16/11/2026):

1. "somente adm e o Cairo consegue clicar em resolver uma devolução";
2. "quando tiver estoque lançado de todos os itens da devolução o sistema pode
   resolver sozinho informando que voltou no estoque";
3. "pedidos como perdimento, extraviado tal ele não vai encerrar" — ficam em
   "A finalizar" (o `pronto_resolver`) pro adm/Cairo;
4. "quando a plataforma cancelar o pedido de reembolso, o painel resolve sozinho".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chamado, DevolucaoRastreio, Devolution, UserRole
from app.services import devolucao_resolver_sozinho as sozinho

pytestmark = pytest.mark.asyncio

_SP = ZoneInfo("America/Sao_Paulo")
_SCHEMA = get_settings().database_schema


def _perm(*, resolver: bool = False) -> dict:
    perm = {"devolucoes": {"view": True, "edit": True, "delete": False}}
    if resolver:
        perm["devolucoes_resolver"] = {"view": True, "edit": True, "delete": False}
    return perm


async def _pedido(
    db: AsyncSession,
    numero: str,
    situacao: str,
    *,
    entrada: date | None,
    itens: list[tuple[str, int]] = (("a001.sp", 1),),
) -> None:
    for sku, qtd in itens:
        await db.execute(
            text(
                """
                INSERT INTO bling_orders
                    (id, numero, numeroloja, situacao, aguardando_devolucao_data,
                     item_codigo, item_descricao, item_quantidade)
                VALUES (:id, :numero, :mkt, :situacao, :entrada, :sku, :produto, :qtd)
                """
            ),
            {
                "id": uuid4(),
                "numero": numero,
                "mkt": f"MKT-{numero}",
                "situacao": situacao,
                "entrada": entrada,
                "sku": sku,
                "produto": f"Produto {sku}",
                "qtd": qtd,
            },
        )


async def _visao(db: AsyncSession) -> None:
    """A vw_devolucoes de produção não vem do create_all: aqui uma igual em
    cima do bling_orders (mesmas colunas que a busca de peças lê)."""
    await db.execute(
        text(
            f"""
            CREATE OR REPLACE VIEW "{_SCHEMA}".vw_devolucoes AS
            SELECT now() AS data, bo.numero::text AS pedido_bling,
                   bo.numeroloja::text AS pedido_marketplace, 'Loja'::text AS loja_nome,
                   NULL::bigint AS bling_loja_id, bo.item_codigo::text AS sku,
                   bo.item_descricao::text AS produto, bo.item_quantidade::int AS quantidade,
                   bo.id AS bling_order_item_id, NULL::text AS nome_destinatario,
                   NULL::text AS cep_destino, NULL::text AS endereco_destino,
                   NULL::text AS numero_destino, NULL::text AS complemento_destino,
                   NULL::text AS bairro_destino, NULL::text AS cidade_destino,
                   NULL::text AS uf_destino
            FROM "{_SCHEMA}".bling_orders bo
            WHERE bo.situacao IN ('83957', '83960', '83961', '83966', '84677', '545902')
            """  # noqa: S608
        )
    )


@pytest.fixture
async def visao(db: AsyncSession):
    await _visao(db)
    await db.commit()
    yield
    await db.execute(text(f'DROP VIEW IF EXISTS "{_SCHEMA}".vw_devolucoes'))
    await db.commit()


def _no_estoque(pedido: str, sku: str, **campos) -> Devolution:
    base = {
        "condicao_produto": "Novo",
        "data_devolvido_estoque": datetime.now(UTC),
        "estoque_status": "ok",
        "estoque_mov_sku": f"z001.{sku}",
    }
    base.update(campos)
    return Devolution(pedido_bling=pedido, conta="Shopee KFA", sku=sku, quantidade=1, **base)


async def _estado(db: AsyncSession, pedido: str) -> DevolucaoRastreio | None:
    db.expire_all()
    return await db.get(DevolucaoRastreio, pedido)


# ── 1. Só adm e Cairo resolvem ───────────────────────────────────────────


async def test_so_adm_e_quem_tem_devolucoes_resolver_resolve_e_reabre(
    client, db: AsyncSession, make_user, auth_as
):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "780001", "83957", entrada=hoje)
    await db.commit()
    corpo = {"como_terminou": "Perdimento"}

    # Quem edita a devolução mas não é adm nem Cairo: lança, mas não resolve.
    equipe = await make_user(permissions=_perm())
    auth_as(equipe)
    lista = (await client.get("/api/devolucoes-painel")).json()
    assert lista["pode_resolver"] is False
    r = await client.post("/api/devolucoes-painel/780001/resolver", json=corpo)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "resolver_sem_permissao"

    # O Cairo (recurso devolucoes_resolver): resolve e reabre.
    cairo = await make_user(permissions=_perm(resolver=True))
    auth_as(cairo)
    assert (await client.get("/api/devolucoes-painel")).json()["pode_resolver"] is True
    assert (
        await client.post("/api/devolucoes-painel/780001/resolver", json=corpo)
    ).status_code == 200

    auth_as(equipe)
    r = await client.post("/api/devolucoes-painel/780001/reabrir")
    assert r.status_code == 403

    # Admin sempre pode.
    adm = await make_user(role=UserRole.ADMIN)
    auth_as(adm)
    assert (await client.get("/api/devolucoes-painel")).json()["pode_resolver"] is True
    assert (await client.post("/api/devolucoes-painel/780001/reabrir")).status_code == 200


async def test_ninguem_usa_a_marca_do_resolvido_sozinho(
    client, db: AsyncSession, make_user, auth_as
):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db, "780002", "83957", entrada=datetime.now(_SP).date())
    await db.commit()
    r = await client.post(
        "/api/devolucoes-painel/780002/resolver", json={"como_terminou": sozinho.COMO}
    )
    assert r.status_code == 422


async def test_migration_0487_libera_so_o_cairo() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0487_cairo_devolucoes_resolver.py"
    ).read_text()
    assert 'EMAIL = "sa.geral@tutamail.com"' in src
    assert "'{devolucoes_resolver}'" in src
    assert 'down_revision: str | None = "0486_devolucao_reabre_o_que_nao_terminou"' in src
    assert "len(exatos) != 1" in src and 'alvo.role == "admin"' in src


# ── 2. Resolve sozinho quando tudo voltou pro estoque ─────────────────────


async def test_tudo_no_estoque_resolve_sozinho_e_aparece_em_resolvidas(
    client, db: AsyncSession, make_user, auth_as, visao
):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781001", "545902", entrada=hoje - timedelta(days=4))
    db.add(_no_estoque("781001", "a001.sp"))
    # Chamado aberto não segura (o chamado é outra coisa).
    db.add(Chamado(pedido_bling="781001", origem="devolucao", conta="Shopee KFA"))
    await db.commit()

    resumo = await sozinho.rodada(db)
    assert resumo["resolvidos"] == 1
    row = await _estado(db, "781001")
    assert row is not None and row.resolvido_at is not None
    assert row.resolvido_como == sozinho.COMO and row.resolvido_por is None

    auth_as(await make_user(permissions=_perm()))
    assert (await client.get("/api/devolucoes-painel")).json()["items"] == []
    res = (await client.get("/api/devolucoes-painel?mostrar=resolvidas")).json()["items"]
    assert [(i["pedido_bling"], i["resolvido_como"]) for i in res] == [("781001", sozinho.COMO)]
    # Já resolvido: a próxima rodada não mexe.
    assert (await sozinho.rodada(db))["resolvidos"] == 0


async def test_kit_com_peca_faltando_nao_resolve(db: AsyncSession, visao):
    """Kit a+b: só o `a` voltou pro estoque — falta lançar o `b` (a mesma conta
    da janela, com o kit aberto em peças)."""
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781002", "83957", entrada=hoje, itens=[("a003.pi+a004.pi", 1)])
    db.add(_no_estoque("781002", "a003.pi"))
    await db.commit()
    assert await sozinho.conferir_um(db, "781002") is None
    assert (await _estado(db, "781002")) is None

    db.add(_no_estoque("781002", "a004.pi"))
    await db.commit()
    assert await sozinho.conferir_um(db, "781002") == "resolvido"


async def test_duas_unidades_so_uma_no_estoque_nao_resolve(db: AsyncSession, visao):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781003", "83957", entrada=hoje, itens=[("b056.24", 2)])
    db.add(_no_estoque("781003", "b056.24"))
    await db.commit()
    assert await sozinho.conferir_um(db, "781003") is None


async def test_o_que_nao_entrou_no_estoque_nao_resolve_sozinho(db: AsyncSession, visao):
    hoje = datetime.now(_SP).date()
    casos = {
        # Perdimento / Extraviado: "não vai encerrar" — fica pro adm/Cairo.
        "781010": {
            "condicao_produto": "Sucata",
            "data_devolvido_estoque": None,
            "estoque_status": None,
        },
        "781011": {
            "condicao_produto": "Extraviado",
            "data_devolvido_estoque": None,
            "estoque_status": None,
        },
        # Entrada que o Bling não confirmou.
        "781012": {"estoque_status": "falhou"},
        "781013": {"estoque_status": "conferindo"},
        # Com o técnico.
        "781014": {
            "condicao_produto": "Manutenção",
            "data_devolvido_estoque": None,
            "estoque_status": None,
        },
        # Estornado.
        "781015": {"estoque_mov_revertido_at": datetime.now(UTC)},
        # Novo lançado sem devolver ao estoque.
        "781016": {"data_devolvido_estoque": None, "estoque_status": None},
    }
    for pedido, campos in casos.items():
        await _pedido(db, pedido, "83957", entrada=hoje)
        db.add(_no_estoque(pedido, "a001.sp", **campos))
    await db.commit()
    resumo = await sozinho.rodada(db)
    assert resumo["resolvidos"] == 0
    for pedido in casos:
        assert (await _estado(db, pedido)) is None, pedido


async def test_pedido_fora_da_visao_nao_resolve_sozinho(db: AsyncSession, visao):
    """Situação que a conta das peças não enxerga (ex.: Entregue): sem ter como
    conferir as peças, fica pro adm/Cairo."""
    await _pedido(db, "781020", "15", entrada=datetime.now(_SP).date())
    db.add(_no_estoque("781020", "a001.sp"))
    await db.commit()
    assert await sozinho.conferir_um(db, "781020") is None


async def test_resolvido_por_pessoa_nao_e_tocado_e_estorno_reabre_o_do_robo(
    db: AsyncSession, visao
):
    hoje = datetime.now(_SP).date()
    # Resolvido pelo robô e depois estornado: volta pra lista.
    await _pedido(db, "781030", "545902", entrada=hoje - timedelta(days=2))
    dev = _no_estoque("781030", "a001.sp")
    db.add(dev)
    await db.commit()
    assert await sozinho.conferir_um(db, "781030") == "resolvido"
    dev.estoque_mov_revertido_at = datetime.now(UTC)
    await db.commit()
    resumo = await sozinho.rodada(db)
    assert resumo["reabertos"] == 1
    row = await _estado(db, "781030")
    assert row.resolvido_at is None and row.resolvido_como is None

    # Resolvido por uma pessoa (Perdimento) com lançamento que não está no
    # estoque: o robô não reabre.
    await _pedido(db, "781031", "83956", entrada=hoje - timedelta(days=2))
    db.add(_no_estoque("781031", "a001.sp", condicao_produto="Sucata", data_devolvido_estoque=None))
    db.add(
        DevolucaoRastreio(
            pedido_bling="781031", resolvido_at=datetime.now(UTC), resolvido_como="Perdimento"
        )
    )
    await db.commit()
    assert (await sozinho.rodada(db))["reabertos"] == 0
    assert (await _estado(db, "781031")).resolvido_como == "Perdimento"


async def test_reentrada_em_devolucao_resolve_de_novo_so_com_a_regra(db: AsyncSession, visao):
    """Resolvido antes da entrada ATUAL (voltou a cair em Aguardando Devolução)
    é aberto: com tudo no estoque, o robô resolve de novo."""
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781040", "83957", entrada=hoje)
    db.add(_no_estoque("781040", "a001.sp"))
    db.add(
        DevolucaoRastreio(
            pedido_bling="781040",
            resolvido_at=datetime.now(UTC) - timedelta(days=10),
            resolvido_como="Voltou pro estoque",
        )
    )
    await db.commit()
    assert await sozinho.conferir_um(db, "781040") == "resolvido"
    assert (await _estado(db, "781040")).resolvido_como == sozinho.COMO


async def test_segunda_devolucao_nao_resolve_com_o_estoque_da_primeira(db: AsyncSession, visao):
    """O pedido voltou a cair em Aguardando Devolução hoje; o único lançamento
    (no estoque) é da 1ª devolução, de 10 dias atrás: não resolve sozinho."""
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781050", "83957", entrada=hoje)
    db.add(_no_estoque("781050", "a001.sp", created_at=datetime.now(UTC) - timedelta(days=10)))
    await db.commit()
    assert await sozinho.conferir_um(db, "781050") is None
    assert (await sozinho.rodada(db))["resolvidos"] == 0


async def test_reaberto_por_pessoa_o_robo_nao_fecha_de_novo(
    client, db: AsyncSession, make_user, auth_as, visao
):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781060", "545902", entrada=hoje)
    db.add(_no_estoque("781060", "a001.sp"))
    await db.commit()
    assert await sozinho.conferir_um(db, "781060") == "resolvido"
    auth_as(await make_user(role=UserRole.ADMIN))
    assert (await client.post("/api/devolucoes-painel/781060/reabrir")).status_code == 200
    assert (await sozinho.rodada(db))["resolvidos"] == 0
    # Continua aberto no painel (o "como" de aberto não aparece).
    itens = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert [(i["pedido_bling"], i["resolvido"], i["resolvido_como"]) for i in itens] == [
        ("781060", False, None)
    ]
    row = await _estado(db, "781060")
    assert row.resolvido_at is None and row.resolvido_como == sozinho.REABERTO_A_MAO
    # Uma pessoa resolve de novo: vale como sempre.
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(
        "/api/devolucoes-painel/781060/resolver", json={"como_terminou": "Voltou pro estoque"}
    )
    assert r.status_code == 200
    assert (await _estado(db, "781060")).resolvido_como == "Voltou pro estoque"


async def test_chamado_segurado_ou_pendente_segura_o_resolve_sozinho(db: AsyncSession, visao):
    """Chamado da devolução segurado pela IA (ou esperando foto): o pedido fica
    aberto pra aparecer no "Onde agir"."""
    from app.services import chamados as chs

    hoje = datetime.now(_SP).date()
    await _pedido(db, "781070", "545902", entrada=hoje)
    db.add(_no_estoque("781070", "a001.sp"))
    ch = Chamado(pedido_bling="781070", origem="devolucao", conta="Shopee KFA")
    db.add(ch)
    await db.flush()
    db.add(
        chs.nova_mensagem(ch, texto="x", tipo="abertura", autor_nome="Sistema", status="segurada")
    )
    await db.commit()
    assert await sozinho.conferir_um(db, "781070") is None
    assert (await sozinho.rodada(db))["resolvidos"] == 0


async def test_robo_resolveu_e_apagaram_os_lancamentos_volta(db: AsyncSession, visao):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781080", "545902", entrada=hoje)
    dev = _no_estoque("781080", "a001.sp")
    db.add(dev)
    await db.commit()
    assert await sozinho.conferir_um(db, "781080") == "resolvido"
    await db.delete(dev)
    await db.commit()
    assert (await sozinho.rodada(db))["reabertos"] == 1
    row = await _estado(db, "781080")
    assert row.resolvido_at is None and row.resolvido_como is None


async def test_robo_resolveu_e_apagaram_uma_peca_volta_na_reconferencia(db: AsyncSession, visao):
    """Kit a+b resolvido sozinho; a lançamento do b foi pra lixeira (pra relançar) e
    ninguém relançou: na reconferência da hora o pedido volta pra lista."""
    hoje = datetime.now(_SP).date()
    await _pedido(db, "781090", "545902", entrada=hoje, itens=[("a003.pi+a004.pi", 1)])
    db.add(_no_estoque("781090", "a003.pi"))
    b = _no_estoque("781090", "a004.pi")
    db.add(b)
    await db.commit()
    assert await sozinho.conferir_um(db, "781090") == "resolvido"
    await db.delete(b)
    await db.commit()
    # Fora da hora cheia não reconfere (é pesado pra cada 5 min).
    assert (await sozinho.rodada(db, reconferir=False))["reabertos"] == 0
    assert (await sozinho.rodada(db, reconferir=True))["reabertos"] == 1
    assert (await _estado(db, "781090")).resolvido_at is None
    # E não resolve de novo enquanto falta a peça.
    assert (await sozinho.rodada(db, reconferir=True))["resolvidos"] == 0


async def test_produto_sem_lancamento_conta_kit_e_quantidade() -> None:
    from app.services import devolucao_painel as contas
    from app.services.devolucao_painel import Lancamento as L  # noqa: N817

    f = contas.produto_sem_lancamento
    # Sem lançamento: é "falta lançar", não conta aqui.
    assert f([("a", 1), ("b", 1)], []) is False
    # Mala lançada, rodinha não.
    assert f([("m2", 1), ("rod", 1)], [L(sku="m2", condicao="Sucata")]) is True
    # Kit a+b numa das linhas, lançado peça por peça.
    lancs = [
        L(sku="m2", condicao="Sucata"),
        L(sku="f105", condicao="Sucata"),
        L(sku="a001", condicao="Sucata"),
    ]
    assert f([("m2", 1), ("f105+a001", 1)], lancs) is False
    assert f([("m2", 1), ("f105+a001", 1)], lancs[:2]) is True
    # 2 unidades, 1 lançada (numa linha só também).
    assert f([("b056", 2)], [L(sku="b056", condicao="Usado")]) is True
    assert f([("b056", 2)], [L(sku="b056", condicao="Usado", quantidade=2)]) is False
    # Pedido de um produto que virou outras peças (kit do Bling): não acusa.
    assert (
        f([("b003", 1)], [L(sku="m2-20", condicao="Novo"), L(sku="m2-24", condicao="Novo")])
        is False
    )


# ── 3. "A finalizar": o resto fica pro adm/Cairo ─────────────────────────


async def test_perdimento_e_extraviado_ficam_em_a_finalizar(
    client, db: AsyncSession, make_user, auth_as, visao
):
    hoje = datetime.now(_SP).date()
    await _pedido(db, "782001", "83956", entrada=hoje - timedelta(days=3))
    db.add(_no_estoque("782001", "a001.sp", condicao_produto="Sucata", data_devolvido_estoque=None))
    await _pedido(db, "782002", "83960", entrada=hoje - timedelta(days=3))
    db.add(
        _no_estoque("782002", "a001.sp", condicao_produto="Extraviado", data_devolvido_estoque=None)
    )
    await db.commit()
    await sozinho.rodada(db)

    auth_as(await make_user(permissions=_perm()))
    itens = {
        i["pedido_bling"]: i for i in (await client.get("/api/devolucoes-painel")).json()["items"]
    }
    assert set(itens) == {"782001", "782002"}
    assert itens["782001"]["pronto_resolver"] and itens["782002"]["pronto_resolver"]
    assert not itens["782001"]["resolvido"] and not itens["782002"]["resolvido"]


async def test_produto_sem_lancamento_nao_e_a_finalizar(
    client, db: AsyncSession, make_user, auth_as
):
    """Mala lançada, rodinha não: falta lançar — não é "A finalizar"."""
    hoje = datetime.now(_SP).date()
    await _pedido(
        db,
        "782010",
        "83957",
        entrada=hoje - timedelta(days=3),
        itens=[("m2-24.pt", 1), ("kit2rod", 1)],
    )
    db.add(
        _no_estoque("782010", "m2-24.pt", condicao_produto="Sucata", data_devolvido_estoque=None)
    )
    await db.commit()
    auth_as(await make_user(permissions=_perm()))
    itens = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert {i["pedido_bling"] for i in itens} == {"782010"}
    assert all(i["pronto_resolver"] is None for i in itens)

    db.add(_no_estoque("782010", "kit2rod", condicao_produto="Sucata", data_devolvido_estoque=None))
    await db.commit()
    itens = (await client.get("/api/devolucoes-painel")).json()["items"]
    assert all(i["pronto_resolver"] == "lançada: Perdimento" for i in itens)


# ── 4. Cancelado pela plataforma resolve sozinho (16/11, 308473) ─────────


def _logistica(pedido: str, plataforma: str, status: str, em: str):
    from app.models import Logistica

    return Logistica(
        pedido_bling=pedido,
        plataforma=plataforma,
        meli_status={"return_type": "REFUND", "return_status": status},
        status_datas={"return_status": {"em": em, "fonte": "plataforma"}},
    )


async def test_cancelado_pela_plataforma_resolve_sozinho_e_destrava_o_video(
    client, db: AsyncSession, make_user, auth_as
):
    hoje = datetime.now(_SP).date()
    agora_iso = datetime.now(UTC).isoformat()
    # Shopee: só reembolso cancelado hoje, Bling já em Entregue (a regra da Logística).
    await _pedido(db, "783001", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783001", "shopee", "CANCELLED", agora_iso))
    # TikTok: o comprador cancelou; o robô da Fraude tinha pedido o vídeo à equipe.
    await _pedido(db, "783002", sozinho.BLING_ENTREGUE, entrada=hoje - timedelta(days=2))
    db.add(_logistica("783002", "tiktok", "RETURN_OR_REFUND_REQUEST_CANCEL", agora_iso))
    db.add(DevolucaoRastreio(pedido_bling="783002", video_solicitado_em=datetime.now(UTC)))
    # Não resolvem:
    # Bling ainda em Aguardando Devolução (a Logística ainda não mexeu).
    await _pedido(db, "783003", "83957", entrada=hoje)
    db.add(_logistica("783003", "shopee", "CANCELLED", agora_iso))
    # Cancelamento ANTERIOR à entrada atual (é de outra devolução).
    await _pedido(db, "783004", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783004", "shopee", "CANCELLED", "2026-01-02T10:00:00+00:00"))
    # Alguém já lançou: a pessoa decide.
    await _pedido(db, "783005", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783005", "shopee", "CANCELLED", agora_iso))
    db.add(Devolution(pedido_bling="783005", conta="Shopee KFA", sku="a001.sp"))
    # Chamado da devolução aberto.
    await _pedido(db, "783006", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783006", "tiktok", "RETURN_OR_REFUND_REQUEST_CANCEL", agora_iso))
    db.add(Chamado(pedido_bling="783006", origem="devolucao", conta="TikTok Mini"))
    # Saiu reembolso pro cliente.
    await _pedido(db, "783007", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783007", "shopee", "CANCELLED", agora_iso))
    db.add(DevolucaoRastreio(pedido_bling="783007", reembolso_auto=True))
    # Reaberto por uma pessoa.
    await _pedido(db, "783008", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783008", "shopee", "CANCELLED", agora_iso))
    db.add(DevolucaoRastreio(pedido_bling="783008", resolvido_como=sozinho.REABERTO_A_MAO))
    # "CANCELLED" de outra plataforma (ML) não vale nesta regra.
    await _pedido(db, "783009", sozinho.BLING_ENTREGUE, entrada=hoje)
    db.add(_logistica("783009", "mercado livre", "CANCELLED", agora_iso))
    await db.commit()

    resumo = await sozinho.rodada(db, reconferir=False)
    assert resumo["cancelados"] == 2, resumo
    for pedido in ("783001", "783002"):
        row = await _estado(db, pedido)
        assert row.resolvido_at is not None and row.resolvido_por is None
        assert row.resolvido_como == sozinho.COMO_CANCELADO
    # O pedido de vídeo deixou de travar a aba Pedidos da equipe.
    assert (await _estado(db, "783002")).video_solicitado_em is None
    for pedido in ("783003", "783004", "783005", "783006", "783007", "783008", "783009"):
        row = await _estado(db, pedido)
        assert row is None or row.resolvido_at is None, pedido
    # Já resolvido: a próxima rodada não mexe.
    assert (await sozinho.rodada(db, reconferir=False))["cancelados"] == 0
    # Ninguém escolhe essa marca à mão no Resolver.
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(
        "/api/devolucoes-painel/783003/resolver",
        json={"como_terminou": sozinho.COMO_CANCELADO},
    )
    assert r.status_code == 422


async def test_worker_agenda_o_robo_a_cada_5_minutos() -> None:
    from app.worker import WorkerSettings

    jobs = [c for c in WorkerSettings.cron_jobs if c.name.endswith("devolucao_resolver_sozinho")]
    assert len(jobs) == 1
    assert jobs[0].minute == set(range(4, 60, 5))
