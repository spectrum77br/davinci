"""Botão "Chamado" da aba Pedidos (Controle de Estoque).

Eduardo, 09/09/2026: "um botão de abrir chamado para os pedidos atrasados" —
e, sobre quais pedidos, "eu escolho e clico". O botão manda o pedido pro
formulário de ajuda do Mercado Livre pelo mesmo robô que a aba Logística já
usa; nas outras plataformas não existe caminho automático, então o endpoint
recusa dizendo por quê em vez de criar chamado que ninguém envia.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Chamado, User, UserRole, UserStatus
from app.models.logistica import Logistica

# Quem fala com o marketplace precisa das duas: estar na tela e responder
# pelos chamados.
PERM_EDIT = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": True, "delete": False},
}
PERM_VIEW = {
    "controle_estoque": {"view": True, "edit": True, "delete": False},
    "chamados": {"view": True, "edit": False, "delete": False},
}

pytestmark = pytest.mark.asyncio


async def _user(db: AsyncSession, perms: dict, *, role: UserRole = UserRole.ADMIN) -> User:
    u = User(
        open_id=f"email:ch-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"ch-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=role,
        status=UserStatus.ACTIVE,
        permissions=perms,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def editor(db: AsyncSession) -> User:
    return await _user(db, PERM_EDIT)


async def _pedido(db: AsyncSession, numero: str, *, plataforma: str | None = "Mercado Livre",
                  com_logistica: bool = True, marketplace: str | None = "2000000001",
                  situacao: str = "21", vencido: bool = True,
                  enviado: bool = False) -> None:
    """Por padrão o pedido está no estado que a mensagem afirma: etiqueta
    gerada (21), não enviado e com o prazo de envio já vencido."""
    agora = datetime.now(UTC)
    db.add(BlingOrder(
        bling_id=int(numero), numero=numero, item_codigo="sku-1",
        item_index=0, situacao=situacao,
        em_andamento_data=(agora.date() if enviado else None),
        marketplace_ship_deadline=agora - timedelta(hours=3) if vencido
        else agora + timedelta(days=1),
    ))
    if com_logistica:
        db.add(Logistica(
            pedido_bling=numero,
            pedido_marketplace=marketplace,
            plataforma=plataforma,
        ))
    await db.commit()


async def test_abre_chamado_no_ml_pelo_robo(
    client: AsyncClient, db: AsyncSession, editor: User, auth_as: Callable[[User | None], None],
):
    """Pedido de ML parado vira chamado com a abertura PENDENTE no canal robo —
    é essa tarefa que o robô do formulário de ajuda executa."""
    auth_as(editor)
    await _pedido(db, "930001")

    r = await client.post("/api/estoque/pedidos/930001/abrir-chamado")

    assert r.status_code == 200, r.text
    assert r.json()["canal"] == "robo"
    ch = (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "930001"))
    ).scalar_one()
    assert ch.origem == "logistica"
    assert ch.canal == "robo"


async def test_nao_duplica_chamado_do_mesmo_pedido(
    client: AsyncClient, db: AsyncSession, editor: User, auth_as: Callable[[User | None], None],
):
    """Clicar duas vezes não abre dois chamados nem duas tarefas pro robô."""
    auth_as(editor)
    await _pedido(db, "930002")

    primeiro = await client.post("/api/estoque/pedidos/930002/abrir-chamado")
    segundo = await client.post("/api/estoque/pedidos/930002/abrir-chamado")

    assert primeiro.status_code == 200 and segundo.status_code == 200
    assert primeiro.json()["chamado_id"] == segundo.json()["chamado_id"]
    chamados = (
        await db.execute(select(Chamado).where(Chamado.pedido_bling == "930002"))
    ).scalars().all()
    assert len(chamados) == 1


async def test_nao_afirma_atraso_que_nao_existe(
    client: AsyncClient, db: AsyncSession, editor: User, auth_as: Callable[[User | None], None],
):
    """A mensagem diz ao ML "prazo vencido" e "etiqueta já gerada" — o
    endpoint recusa quando isso não é verdade, em vez de mentir em nome da
    empresa. Pedido de Previsão (situação 6) aparece na MESMA tela."""
    auth_as(editor)
    await _pedido(db, "930010", situacao="6")            # sem etiqueta
    await _pedido(db, "930011", vencido=False)           # ainda no prazo
    await _pedido(db, "930012", enviado=True)            # já saiu

    assert (await client.post("/api/estoque/pedidos/930010/abrir-chamado")).json()[
        "detail"]["code"] == "pedido_sem_etiqueta"
    assert (await client.post("/api/estoque/pedidos/930011/abrir-chamado")).json()[
        "detail"]["code"] == "pedido_no_prazo"
    assert (await client.post("/api/estoque/pedidos/930012/abrir-chamado")).json()[
        "detail"]["code"] == "pedido_ja_enviado"
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_nao_sequestra_chamado_que_ja_tem_protocolo(
    client: AsyncClient, db: AsyncSession, editor: User, auth_as: Callable[[User | None], None],
):
    """Chamado já aberto (protocolo preenchido) não vira tarefa do robô: ele
    leria como "responder dentro do protocolo velho"."""
    auth_as(editor)
    await _pedido(db, "930013")
    db.add(Chamado(
        data=datetime.now(UTC).date(),
        pedido_bling="930013",
        origem="logistica",
        canal="manual",
        chamado="Disputa na venda",
    ))
    await db.commit()

    r = await client.post("/api/estoque/pedidos/930013/abrir-chamado")

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "pedido_ja_tem_chamado"


async def test_recusa_o_que_nao_da_pra_enviar(
    client: AsyncClient, db: AsyncSession, editor: User, auth_as: Callable[[User | None], None],
):
    """Cada recusa tem código próprio pra tela explicar em português."""
    auth_as(editor)
    await _pedido(db, "930003", plataforma="Shopee")
    await _pedido(db, "930004", com_logistica=False)
    await _pedido(db, "930005", marketplace=None)

    assert (await client.post("/api/estoque/pedidos/930003/abrir-chamado")).json()[
        "detail"]["code"] == "pedido_nao_ml"
    assert (await client.post("/api/estoque/pedidos/930004/abrir-chamado")).json()[
        "detail"]["code"] == "pedido_sem_logistica"
    assert (await client.post("/api/estoque/pedidos/930005/abrir-chamado")).json()[
        "detail"]["code"] == "pedido_sem_numero_marketplace"
    r = await client.post("/api/estoque/pedidos/999999/abrir-chamado")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_nao_abre_chamado_de_pedido_de_outra_tag(
    client: AsyncClient, db: AsyncSession, auth_as: Callable[[User | None], None],
):
    """A aba tem cerca por tag: saber o número do pedido não dá direito de
    abrir chamado do time do lado."""
    outro_time = await _user(db, PERM_EDIT, role=UserRole.USER)
    outro_time.stock_tags = ["eletro"]
    await db.commit()
    auth_as(outro_time)
    await _pedido(db, "930007")  # sku-1 não é da tag eletro

    r = await client.post("/api/estoque/pedidos/930007/abrir-chamado")

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "pedido_fora_da_sua_tag"
    assert (await db.execute(select(Chamado))).scalars().all() == []


async def test_precisa_de_permissao_de_edicao(
    client: AsyncClient, db: AsyncSession, auth_as: Callable[[User | None], None],
):
    """Ter permissão do galpão não basta: sem `chamados` edit não fala com o
    marketplace (13 pessoas têm controle_estoque edit, é a permissão de ticar
    conferido)."""
    somente_view = await _user(db, PERM_VIEW, role=UserRole.USER)
    auth_as(somente_view)
    await _pedido(db, "930006")

    r = await client.post("/api/estoque/pedidos/930006/abrir-chamado")

    assert r.status_code == 403
    assert (await db.execute(select(Chamado))).scalars().all() == []
