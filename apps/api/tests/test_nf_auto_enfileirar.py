"""Sweep do auto-enfileirador de NF (pedidos Shopee/TikTok "Em aberto").

`run_auto_enfileirar_nf()` faz sozinho o que o botão "Enfileirar" do painel
faz: varre situacao=6 de loja Shopee/TikTok com faturador, confere o estoque
(saldo virtual negativo → Aguardando Cancelamento) e cria os NfCommand de
import_avulsa com source='auto'. Tentativa automática é ÚNICA por pedido:
qualquer status_faturamento pré-existente exclui o pedido do sweep.

O service usa `session_scope()` — o conftest troca o engine module-level, então
dá pra chamar direto e conferir o resultado pela fixture `db` (mesmo schema).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    NfCommand,
    NfFaturador,
    NfFaturamento,
    Product,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt
from app.services import nf_emissao_gerar
from app.services.nf_auto_enfileirar import run_auto_enfileirar_nf


async def _async_return(value: object) -> object:
    """`_bling_client_opt` é async — o monkeypatch devolve a coroutine pronta."""
    return value


class _FakeBlingSituacao:
    """Captura os PATCH de situação que o motor manda pro Bling."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.chamadas.append((bling_order_id, situacao_id))


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_loja(
    db: AsyncSession, admin: User, *, plataforma: str, bling_store_id: str,
    com_faturador: bool = True,
) -> None:
    faturador_id = None
    if com_faturador:
        f = NfFaturador(
            nome=f"faturador {bling_store_id}", modo="bling", nf_cheia=True,
            sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10",
            ads_power="perfil-A", usuario="user-a", senha_enc=encrypt("segredoA"),
        )
        db.add(f)
        await db.flush()
        faturador_id = f.id
    db.add(
        StoreInfo(
            user_id=admin.id, platform=plataforma,
            account_name=f"loja {bling_store_id}",
            bling_store_id=bling_store_id, nf_faturador_id=faturador_id,
        )
    )
    await db.flush()


async def _seed_pedido(
    db: AsyncSession, *, numero: str, loja: str, sku: str,
    situacao: str = "6", bling_id: int = 700001,
    data: datetime | None = None,
) -> None:
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=numero,
            data=data or (datetime.now(UTC) - timedelta(days=1)),
            loja=loja,
            situacao=situacao,
            item_index=0,
            item_codigo=sku,
            item_descricao=f"Produto {sku}",
            item_quantidade=1,
            itemvalor=100,
            nome_destinatario="Cleso Menezes",
            cep_destino="30570050",
            endereco_destino="Rua Emídio Beruto",
            numero_destino="30",
            bairro_destino="Cinquentenário",
            cidade_destino="Belo Horizonte",
            uf_destino="MG",
        )
    )


@pytest.mark.asyncio
async def test_sweep_enfileira_com_estoque(db: AsyncSession, admin: User):
    """Shopee e TikTok 'Em aberto' com faturador viram NfCommand source='auto';
    a 2ª passada não re-pega ninguém (status 'processando' já registrado)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_loja(db, admin, plataforma="tiktok", bling_store_id="930002")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1")
    await _seed_pedido(db, numero="830002", loja="930002", sku="a2")
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=3))
    db.add(Product(user_id=admin.id, sku="a2", name="A2", stock=1))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 2
    assert summary["enfileirados"] == 2
    assert summary["sem_estoque"] == 0

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert {c.status for c in cmds} == {"pending"}
    assert {c.action for c in cmds} == {"import_avulsa"}
    assert {c.source for c in cmds} == {"auto"}
    assert sorted(n for c in cmds for n in c.numeros) == ["830001", "830002"]
    assert all(c.planilha for c in cmds)

    fats = {f.pedido_bling: f for f in (await db.execute(select(NfFaturamento))).scalars().all()}
    assert fats["830001"].status_faturamento == "processando"
    assert fats["830002"].status_faturamento == "processando"

    # 2ª passada: nada novo (dedupe pelo status_faturamento + fila)
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 0
    assert summary2["enfileirados"] == 0
    db.expire_all()
    cmds2 = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds2) == len(cmds)


@pytest.mark.asyncio
async def test_sweep_sem_estoque_vai_para_aguardando_cancelamento(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Saldo virtual negativo: NÃO enfileira; Bling recebe 83955, o espelho
    local acompanha e nf_faturamento fica 'sem_estoque' (tentativa única)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="x1", bling_id=700009)
    db.add(Product(user_id=admin.id, sku="x1", name="X1", stock=-2))
    await db.commit()
    fake = _FakeBlingSituacao()
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["sem_estoque"] == 1
    assert summary["enfileirados"] == 0

    assert fake.chamadas == [(700009, 83955)]
    db.expire_all()
    assert (await db.execute(select(NfCommand))).scalars().all() == []
    situacao = (
        await db.execute(
            select(BlingOrder.situacao).where(BlingOrder.numero == "830001")
        )
    ).scalar()
    assert situacao == "83955"
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert fat.status_faturamento == "sem_estoque"
    assert "Aguardando Cancelamento" in (fat.erro_faturamento or "")

    # tentativa automática única: o sweep NÃO re-pega quem já tem status
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 0


@pytest.mark.asyncio
async def test_sweep_tentativa_unica_por_status_preexistente(
    db: AsyncSession, admin: User
):
    """Qualquer status_faturamento pré-existente (ex. 'erro' de tentativa
    anterior, ou seed manual de exclusão) tira o pedido do automático."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1")
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="erro"))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 0
    db.expire_all()
    assert (await db.execute(select(NfCommand))).scalars().all() == []


@pytest.mark.asyncio
async def test_sweep_filtra_candidatos(db: AsyncSession, admin: User):
    """Fora do escopo: situação != 6, plataforma não coberta, loja sem
    faturador e pedido mais velho que a janela de 7 dias."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_loja(db, admin, plataforma="amazon", bling_store_id="930002")
    await _seed_loja(
        db, admin, plataforma="tiktok", bling_store_id="930003", com_faturador=False
    )
    # situação != 6 (já faturado/andamento)
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1", situacao="15")
    # plataforma amazon (não coberta)
    await _seed_pedido(db, numero="830002", loja="930002", sku="a2")
    # loja sem faturador atribuído
    await _seed_pedido(db, numero="830003", loja="930003", sku="a3")
    # fora da janela de 7 dias
    await _seed_pedido(
        db, numero="830004", loja="930001", sku="a4",
        data=datetime.now(UTC) - timedelta(days=10),
    )
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 0
    db.expire_all()
    assert (await db.execute(select(NfCommand))).scalars().all() == []
    assert (await db.execute(select(NfFaturamento))).scalars().all() == []


@pytest.mark.asyncio
async def test_sweep_pulado_vira_erro(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Pedido pulado pelo gerador vira status 'erro' de uma vez — senão o
    mesmo pedido voltaria a cada tick pra sempre."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1")
    await db.commit()

    async def _fake_gerar(_session, _numeros):
        return nf_emissao_gerar.ResultadoPorFaturador(
            blocos=[],
            pulados=[
                nf_emissao_gerar.PedidoPulado(
                    numero="830001", motivo="loja sem faturador atribuído"
                )
            ],
        )

    monkeypatch.setattr(nf_emissao_gerar, "gerar_por_faturador", _fake_gerar)

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["pulados"] == 1
    assert summary["enfileirados"] == 0

    db.expire_all()
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert fat.status_faturamento == "erro"
    assert fat.erro_faturamento == "loja sem faturador atribuído"

    # e a tentativa única segura: não volta no tick seguinte
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 0
