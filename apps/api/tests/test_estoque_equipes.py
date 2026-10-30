"""Controle de Estoque × equipe de vendas (30/10/2026).

Eduardo, 30/10: três equipes — 101 Londres, 102 Israel, 103 Marrocos — e quem
é de uma equipe só enxerga as lojas dela. No Controle de Estoque a equipe cerca
os PEDIDOS pela loja do Bling (`BlingOrder.loja`), POR CIMA da cerca de tag
(interseção). Admin e quem não tem equipe (o pessoal do galpão) não mudam.

Cobre: GET /pedidos (lista + atrasados), GET /envios (ledger + vídeos do dia),
vídeos pendentes (lista e resposta), etiqueta (uma e lote), vídeo da embalagem
(PUT/DELETE), carimbo do papel de previsão, botão de chamado e chamados de
atraso em lote. Equipe sem loja cadastrada não vê pedido nenhum.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingEnvioEvento,
    BlingOrder,
    Chamado,
    ChamadoPedido,
    DevolucaoRastreio,
    EstoquePedidoVideo,
    PrevisaoImpressa,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.logistica import Logistica
from app.models.nf import NfEtiquetaArquivo

pytestmark = pytest.mark.asyncio

PERM = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": True, "delete": False},
}
_DIA = date(2026, 6, 2)
_DIA_ATRASO = date(2026, 6, 1)
LOJA_LONDRES = "8811"
LOJA_ISRAEL = "8812"
LOJA_SEM_CADASTRO = "8819"
_MEGA = "https://mega.nz/file/oMV0HRyS#vK2VCD7kON8nv9zplMPFaPfWSzP5jxYrhprVtjQd3V8"


def _pdf(texto: str) -> bytes:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page(width=300, height=442)
    page.insert_text((20, 20), texto, fontsize=10, fontname="helv")
    return doc.tobytes()


async def _user(
    db: AsyncSession,
    nome: str,
    *,
    role: UserRole = UserRole.USER,
    equipes: list[int] | None = None,
    tags: list[str] | None = None,
) -> User:
    email = f"{nome}-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name=nome,
        role=role,
        status=UserStatus.ACTIVE,
        permissions=PERM,
        sales_teams=equipes,
        stock_tags=tags,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _pedido(
    db: AsyncSession,
    numero: str,
    loja: str,
    sku: str,
    *,
    qtd: int = 1,
    situacao: str = "83953",
    em_andamento: date | None = _DIA,
    corte: datetime | None = None,
    evento: bool = True,
) -> None:
    """83953 (Entregue) é verde e não dispara o gatilho do ledger: o evento do
    envio é semeado à parte, com o dia controlado."""
    db.add(
        BlingOrder(
            bling_id=int(numero),
            numero=numero,
            numeroloja=f"MK{numero}",
            loja=loja,
            item_codigo=sku,
            item_index=0,
            item_quantidade=qtd,
            situacao=situacao,
            em_andamento_data=em_andamento,
            marketplace_ship_deadline=corte,
            data=datetime.now(UTC) - timedelta(days=1),
        )
    )
    if evento and em_andamento is not None:
        db.add(
            BlingEnvioEvento(
                bling_id=int(numero),
                item_index=0,
                item_codigo=sku,
                numero=numero,
                occurred_at=datetime(em_andamento.year, em_andamento.month, em_andamento.day, 12),
                shipping_day=em_andamento,
            )
        )
    await db.commit()


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict[str, User]:
    """Loja 8811 = Londres (101), 8812 = Israel (102), 8819 = loja sem
    cadastro. 873001 (.ci) e 873002 (.ra, 2 un.) são de Londres; 873003 (.ci,
    2 un.) é de Israel; 873004 (.ci) é da loja sem cadastro. Atrasados
    (etiqueta de dia passado): 873011 Londres e 873012 Israel."""
    # O vídeo e o carimbo da previsão não estão na limpeza geral do conftest.
    for tabela in ("estoque_pedido_video", "previsao_impressa"):
        await db.execute(text(f"DELETE FROM {tabela} WHERE pedido_bling LIKE '873%'"))  # noqa: S608
    await db.commit()

    admin = await _user(db, "admin", role=UserRole.ADMIN)
    db.add_all(
        [
            StoreInfo(
                user_id=admin.id,
                platform="ml",
                account_name="loja-londres",
                bling_store_id=LOJA_LONDRES,
                sales_team=101,
            ),
            StoreInfo(
                user_id=admin.id,
                platform="shopee",
                account_name="loja-israel",
                bling_store_id=LOJA_ISRAEL,
                sales_team=102,
            ),
        ]
    )
    await db.commit()
    await _pedido(db, "873001", LOJA_LONDRES, "x001.ci")
    await _pedido(db, "873002", LOJA_LONDRES, "x002.ra", qtd=2)
    await _pedido(db, "873003", LOJA_ISRAEL, "x003.ci", qtd=2)
    await _pedido(db, "873004", LOJA_SEM_CADASTRO, "x004.ci")
    await _pedido(
        db,
        "873011",
        LOJA_LONDRES,
        "x011.ci",
        situacao="21",
        em_andamento=_DIA_ATRASO,
        evento=False,
    )
    await _pedido(
        db,
        "873012",
        LOJA_ISRAEL,
        "x012.ci",
        situacao="21",
        em_andamento=_DIA_ATRASO,
        evento=False,
    )
    usuarios = {
        "admin": admin,
        # Equipe Londres com as duas tags (como a gerência de vendas).
        "londres": await _user(db, "londres", equipes=[101], tags=["ci", "ra"]),
        # Equipe Londres com só uma tag: a tag continua cercando DENTRO da equipe.
        "londres_ci": await _user(db, "londres-ci", equipes=[101], tags=["ci"]),
        # Galpão: sem equipe, vê todas as lojas pelas tags (como hoje).
        "galpao": await _user(db, "azeroth", tags=["ci", "ra"]),
        # Equipe sem nenhuma loja cadastrada: não vê pedido nenhum.
        "sem_loja": await _user(db, "sem-loja", equipes=[104], tags=["ci", "ra"]),
    }
    yield usuarios
    for tabela in ("estoque_pedido_video", "previsao_impressa"):
        await db.execute(text(f"DELETE FROM {tabela} WHERE pedido_bling LIKE '873%'"))  # noqa: S608
    await db.commit()


async def _lista(client: AsyncClient) -> dict:
    r = await client.get(f"/api/estoque/pedidos?data_inicio={_DIA}&data_fim={_DIA}")
    assert r.status_code == 200, r.text
    return r.json()


async def _envios_do_dia(client: AsyncClient) -> dict | None:
    r = await client.get(f"/api/estoque/envios?data_inicio={_DIA}&data_fim={_DIA}")
    assert r.status_code == 200, r.text
    return next((i for i in r.json()["data"] if i["data"] == _DIA.isoformat()), None)


# ─── Lista, atrasados e envios ───────────────────────────────────────────


async def test_pedidos_equipe_so_ve_as_lojas_dela(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict[str, User]
):
    auth_as(cenario["londres"])
    body = await _lista(client)
    assert {p["pedido_bling"] for p in body["data"]} == {"873001", "873002"}
    assert body["atrasados"] == [{"date": _DIA_ATRASO.isoformat(), "count": 1}]
    assert body["equipe"] == {"numeros": [101], "nomes": ["Londres"]}

    # Admin e galpão (sem equipe): nada muda — todas as lojas, inclusive a
    # que nem tem cadastro, e a tela não mostra aviso de equipe.
    for quem in ("admin", "galpao"):
        auth_as(cenario[quem])
        body = await _lista(client)
        assert {p["pedido_bling"] for p in body["data"]} == {
            "873001",
            "873002",
            "873003",
            "873004",
        }, quem
        assert body["atrasados"] == [{"date": _DIA_ATRASO.isoformat(), "count": 2}], quem
        assert body["equipe"] is None, quem


async def test_tag_continua_cercando_dentro_da_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict[str, User]
):
    """Equipe é interseção com a tag, nunca no lugar dela: Londres só com .ci
    não vê o 873002 (.ra), mesmo sendo da loja da equipe."""
    auth_as(cenario["londres_ci"])
    body = await _lista(client)
    assert {p["pedido_bling"] for p in body["data"]} == {"873001"}
    assert body["atrasados"] == [{"date": _DIA_ATRASO.isoformat(), "count": 1}]
    dia = await _envios_do_dia(client)
    assert dia is not None and dia["envios"] == 1

    # Tag fora do cadastro dele continua barrada como sempre.
    r = await client.get(f"/api/estoque/pedidos?data_inicio={_DIA}&data_fim={_DIA}&tag=ra")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "tag_not_allowed"


async def test_equipe_sem_loja_nao_ve_pedido_nenhum(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict[str, User]
):
    """Equipe sem loja cadastrada = nada (e nunca "tudo")."""
    auth_as(cenario["sem_loja"])
    body = await _lista(client)
    assert body["data"] == [] and body["atrasados"] == []
    assert body["equipe"] == {"numeros": [104], "nomes": ["Equipe 104"]}
    assert await _envios_do_dia(client) is None
    r = await client.get("/api/estoque/pedidos/873001/etiqueta")
    assert r.status_code == 403, r.text


async def test_sem_tag_continua_403_mesmo_com_equipe(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
):
    """A equipe não substitui a tag: sem stock_tags segue 403 no_stock_tag."""
    auth_as(await _user(db, "vendedor", equipes=[101]))
    r = await client.get(f"/api/estoque/pedidos?data_inicio={_DIA}&data_fim={_DIA}")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "no_stock_tag"


async def test_envios_e_videos_do_dia_por_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict[str, User]
):
    """O ledger não tem a loja: a equipe conta só os eventos de pedidos das
    lojas dela — e os vídeos do dia (mais de 1 unidade) idem."""
    auth_as(cenario["londres"])
    dia = await _envios_do_dia(client)
    assert dia is not None
    assert dia["envios"] == 2
    assert dia["videos"]["necessarios"] == 1
    assert dia["videos"]["pendentes"] == ["873002"]

    for quem in ("admin", "galpao"):
        auth_as(cenario[quem])
        dia = await _envios_do_dia(client)
        assert dia is not None, quem
        assert dia["envios"] == 4, quem
        assert dia["videos"]["necessarios"] == 2, quem
        assert dia["videos"]["pendentes"] == ["873002", "873003"], quem


# ─── Vídeos pendentes (solicitados pela Devoluções) ──────────────────────


async def test_videos_pendentes_por_equipe(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
):
    agora = datetime.now(UTC)
    db.add_all(
        [
            DevolucaoRastreio(pedido_bling="873001", video_solicitado_em=agora),
            DevolucaoRastreio(pedido_bling="873003", video_solicitado_em=agora),
        ]
    )
    await db.commit()

    auth_as(cenario["galpao"])
    r = await client.get("/api/estoque/videos-pendentes")
    assert {p["pedido_bling"] for p in r.json()["data"]} == {"873001", "873003"}

    auth_as(cenario["londres"])
    r = await client.get("/api/estoque/videos-pendentes")
    assert r.status_code == 200, r.text
    assert [p["pedido_bling"] for p in r.json()["data"]] == ["873001"]

    # Responder pelo pedido da outra equipe: 403 e nada gravado.
    r = await client.post("/api/estoque/videos-pendentes/873003", json={"link": _MEGA})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"
    linha = await db.get(DevolucaoRastreio, "873003")
    await db.refresh(linha)
    assert linha.video_enviado_em is None

    r = await client.post("/api/estoque/videos-pendentes/873001", json={"link": _MEGA})
    assert r.status_code == 200, r.text


# ─── Etiqueta (uma e lote), vídeo e papel de previsão ────────────────────


async def test_etiqueta_de_outra_equipe_recusada(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
):
    for numero in ("873001", "873003"):
        blob = _pdf(f"ETIQUETA {numero}")
        db.add(
            NfEtiquetaArquivo(
                pedido_bling=numero,
                filename=f"etiqueta_{numero}.pdf",
                content_type="application/pdf",
                size_bytes=len(blob),
                blob=blob,
            )
        )
    await db.commit()

    auth_as(cenario["londres"])
    # Lote com um pedido da outra equipe: recusa INTEIRO e não carimba nada.
    r = await client.post("/api/estoque/pedidos/etiquetas", json={"pedidos": ["873001", "873003"]})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "pedido_de_outra_equipe", "pedidos": ["873003"]}
    impressa = (
        await db.execute(
            select(NfEtiquetaArquivo.impressa_em).where(NfEtiquetaArquivo.pedido_bling == "873001")
        )
    ).scalar_one()
    assert impressa is None

    # Uma só: a da outra equipe barra; pedido que nem existe também (não dá
    # pra provar que é da equipe) — 403, sem dizer se há etiqueta.
    for numero in ("873003", "873999"):
        r = await client.get(f"/api/estoque/pedidos/{numero}/etiqueta")
        assert r.status_code == 403, (numero, r.text)
        assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"

    r = await client.get("/api/estoque/pedidos/873001/etiqueta?carimbar=false")
    assert r.status_code == 200, r.text
    r = await client.post("/api/estoque/pedidos/etiquetas", json={"pedidos": ["873001"]})
    assert r.status_code == 200, r.text
    assert r.headers["x-etiquetas-total"] == "1"

    # Sem equipe: igual a hoje (etiqueta nunca foi cercada por tag).
    auth_as(cenario["galpao"])
    r = await client.get("/api/estoque/pedidos/873003/etiqueta?carimbar=false")
    assert r.status_code == 200, r.text
    auth_as(cenario["admin"])
    r = await client.post("/api/estoque/pedidos/etiquetas", json={"pedidos": ["873001", "873003"]})
    assert r.status_code == 200, r.text
    assert r.headers["x-etiquetas-total"] == "2"


async def test_video_da_embalagem_de_outra_equipe(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
):
    async def _tem_video(numero: str) -> bool:
        return (
            await db.execute(
                select(EstoquePedidoVideo.pedido_bling).where(
                    EstoquePedidoVideo.pedido_bling == numero
                )
            )
        ).scalar_one_or_none() is not None

    url_israel = "/api/estoque/pedidos/873003/video"
    auth_as(cenario["londres"])
    r = await client.put(url_israel, json={"link": _MEGA})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"
    assert not await _tem_video("873003")

    auth_as(cenario["admin"])
    assert (await client.put(url_israel, json={"link": _MEGA})).status_code == 200

    # Apagar o da outra equipe: 403 antes de dizer se há vídeo; nada some.
    auth_as(cenario["londres"])
    r = await client.delete(url_israel)
    assert r.status_code == 403, r.text
    assert await _tem_video("873003")

    url_londres = "/api/estoque/pedidos/873001/video"
    assert (await client.put(url_londres, json={"link": _MEGA})).status_code == 200
    assert (await client.delete(url_londres)).status_code == 204


async def test_papel_de_previsao_so_carimba_da_equipe(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
):
    """O carimbo do papel aparece na tela da outra equipe como "já separado":
    quem tem equipe só carimba os das lojas dela (os outros são pulados)."""
    auth_as(cenario["londres"])
    r = await client.post(
        "/api/estoque/pedidos/previsoes/impressas", json={"pedidos": ["873001", "873003"]}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"marcados": 1}
    carimbados = set(
        (
            await db.execute(
                select(PrevisaoImpressa.pedido_bling).where(
                    PrevisaoImpressa.pedido_bling.like("873%")
                )
            )
        ).scalars()
    )
    assert carimbados == {"873001"}

    auth_as(cenario["admin"])
    r = await client.post("/api/estoque/pedidos/previsoes/impressas", json={"pedidos": ["873003"]})
    assert r.json() == {"marcados": 1}


# ─── Chamados (botão por pedido e atraso em lote) ────────────────────────


async def test_chamados_so_das_lojas_da_equipe(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
    cenario: dict[str, User],
):
    vencido = datetime.now(UTC) - timedelta(hours=3)
    for numero, loja in (("873021", LOJA_LONDRES), ("873022", LOJA_ISRAEL)):
        await _pedido(db, numero, loja, "x021.ci", situacao="21", em_andamento=None, corte=vencido)
        db.add(
            Logistica(
                pedido_bling=numero,
                pedido_marketplace=f"20000{numero}",
                plataforma="Mercado Livre",
            )
        )
    await db.commit()

    auth_as(cenario["londres"])
    # Botão "Chamado" por pedido: o da outra equipe barra antes de tudo.
    r = await client.post("/api/estoque/pedidos/873022/abrir-chamado")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"
    r = await client.post("/api/estoque/pedidos/873021/abrir-chamado")
    assert r.status_code == 200, r.text
    abertos = (await db.execute(select(Chamado.pedido_bling))).scalars().all()
    assert abertos == ["873021"]

    # Atraso em lote: o da outra equipe fica de fora, sem o nº da plataforma.
    for numero, loja in (("873031", LOJA_LONDRES), ("873032", LOJA_ISRAEL)):
        await _pedido(db, numero, loja, "x031.ci", situacao="6", em_andamento=None, corte=vencido)
    preview = "/api/estoque/pedidos/chamados-atraso/preview"
    r = await client.post(preview, json={"pedidos": ["873031", "873032"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["pedido_bling"] for g in body["grupos"] for p in g["pedidos"]] == ["873031"]
    assert body["excluidos"] == [
        {
            "pedido_bling": "873032",
            "pedido_marketplace": None,
            "motivo": "fora_da_sua_equipe",
            "detalhe": "pedido de loja de outra equipe",
        }
    ]
    r = await client.post(
        "/api/estoque/pedidos/chamados-atraso",
        json={
            "grupos": [
                {
                    "chave": LOJA_ISRAEL,
                    "motivo_outro": "teste",
                    "pedidos": [{"pedido_bling": "873032", "motivo": "outro"}],
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert [a.get("chamado_id") for a in r.json()["abertos"]] == [None]
    assert r.json()["abertos"][0]["excluido"]["motivo"] == "fora_da_sua_equipe"
    ligados = (await db.execute(select(ChamadoPedido.pedido_bling))).scalars().all()
    assert "873032" not in ligados

    # Admin: os dois entram na conferência, cada um no grupo da sua loja.
    auth_as(cenario["admin"])
    r = await client.post(preview, json={"pedidos": ["873031", "873032"]})
    assert sorted(g["chave"] for g in r.json()["grupos"]) == [LOJA_LONDRES, LOJA_ISRAEL]
    assert r.json()["excluidos"] == []
