"""Sweep do auto-enfileirador de NF (pedidos Shopee/TikTok/ML "Em aberto").

`run_auto_enfileirar_nf()` faz sozinho o que o botão "Enfileirar" do painel
faz: varre situacao=6 de loja Shopee/TikTok/ML com faturador, confere o estoque
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
from app.config import get_settings
from app.security.cipher import encrypt
from app.services import nf_emissao_gerar, threema
from app.services.nf_auto_enfileirar import _tags_do_sku, run_auto_enfileirar_nf


async def _async_return(value: object) -> object:
    """`_bling_client_opt` é async — o monkeypatch devolve a coroutine pronta."""
    return value


class _FakeBlingSituacao:
    """Captura os PATCH de situação que o motor manda pro Bling e serve o
    saldo VIVO (`get_product`) do check de estoque ao vivo. Pra restrição,
    serve o pedido (`get_order`) e captura o PUT das Observações."""

    def __init__(
        self,
        saldos: dict[int, float] | None = None,
        orders: dict[int, dict] | None = None,
    ) -> None:
        self.chamadas: list[tuple[int, int]] = []
        self.saldos = saldos or {}
        self.orders = orders or {}
        self.puts: list[tuple[int, dict]] = []
        self.eventos: list[tuple[str, int]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.chamadas.append((bling_order_id, situacao_id))
        self.eventos.append(("situacao", bling_order_id))

    async def get_product(self, bling_product_id: int) -> dict:
        return {"estoque": {"saldoVirtualTotal": self.saldos[bling_product_id]}}

    async def get_order(self, bling_order_id: int) -> dict:
        return self.orders[bling_order_id]

    async def update_order(self, bling_order_id: int, body: dict) -> dict:
        self.puts.append((bling_order_id, body))
        self.eventos.append(("put", bling_order_id))
        return body


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
    data: datetime | None = None, item_produto_id: int | None = None,
    item_descricao: str | None = None, uf_destino: str = "MG",
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
            item_produto_id=item_produto_id,
            item_descricao=item_descricao or f"Produto {sku}",
            item_quantidade=1,
            itemvalor=100,
            nome_destinatario="Cleso Menezes",
            cep_destino="30570050",
            endereco_destino="Rua Emídio Beruto",
            numero_destino="30",
            bairro_destino="Cinquentenário",
            cidade_destino="Belo Horizonte",
            uf_destino=uf_destino,
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
async def test_sweep_ml_entra_no_escopo(db: AsyncSession, admin: User):
    """Pedido de loja ML 'Em aberto' com faturador entra no sweep — o fluxo
    do Mercado Livre é o mesmo (importa no Bling destino e a marionete emite)."""
    await _seed_loja(db, admin, plataforma="ml", bling_store_id="930003")
    await _seed_pedido(db, numero="830003", loja="930003", sku="a3")
    db.add(Product(user_id=admin.id, sku="a3", name="A3", stock=3))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["enfileirados"] == 1

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds) == 1
    assert cmds[0].action == "import_avulsa"
    assert cmds[0].numeros == ["830003"]
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830003")
        )
    ).scalar_one()
    assert fat.status_faturamento == "processando"


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
    # motivo visível no painel: o erro nomeia o SKU negativo
    assert "x1" in (fat.erro_faturamento or "")

    # tentativa automática única: o sweep NÃO re-pega quem já tem status
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 0


@pytest.mark.asyncio
async def test_sweep_estoque_ao_vivo_bling_detecta_negativo(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """A fonte do check é o saldo VIVO do Bling: local clampado em 0 (o sync
    faz max(0, saldo)) mas saldoVirtualTotal negativo → sem_estoque."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="dg055.ci",
        bling_id=700009, item_produto_id=555,
    )
    db.add(Product(user_id=admin.id, sku="dg055.ci", name="DG055", stock=0))
    await db.commit()
    fake = _FakeBlingSituacao(saldos={555: -7.0})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["sem_estoque"] == 1
    assert summary["enfileirados"] == 0
    assert (700009, 83955) in fake.chamadas
    db.expire_all()
    assert (await db.execute(select(NfCommand))).scalars().all() == []
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert fat.status_faturamento == "sem_estoque"
    assert "dg055.ci" in (fat.erro_faturamento or "")


@pytest.mark.asyncio
async def test_sweep_estoque_ao_vivo_bling_positivo_vence_local(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Precedência: local negativo (cache podre) mas Bling vivo positivo →
    enfileira normal, sem Aguardando Cancelamento."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="a1",
        bling_id=700009, item_produto_id=555,
    )
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=-2))
    await db.commit()
    fake = _FakeBlingSituacao(saldos={555: 5.0})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["sem_estoque"] == 0
    assert summary["enfileirados"] == 1
    assert fake.chamadas == []  # nenhum PATCH de situação
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds) == 1
    assert cmds[0].numeros == ["830001"]


class _FakeThreema:
    """Captura o send_to_all do aviso de sem_estoque."""

    enviados: list[tuple[str, list[str] | None]] = []

    async def send_to_all(
        self, text: str, recipients: list[str] | None = None
    ) -> dict[str, list[str]]:
        _FakeThreema.enviados.append((text, recipients))
        return {"sent": recipients or [], "failed": []}


@pytest.mark.asyncio
async def test_sweep_sem_estoque_avisa_threema(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Movido pra Aguardando Cancelamento automaticamente → UMA mensagem
    Threema com pedido, loja e SKUs pros IDs configurados."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="x1", bling_id=700009)
    db.add(Product(user_id=admin.id, sku="x1", name="X1", stock=-2))
    await db.commit()
    fake = _FakeBlingSituacao()
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients",
        "7KMPCBS5,M5TT27JA", raising=False,
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 1

    assert len(_FakeThreema.enviados) == 1
    texto, recipients = _FakeThreema.enviados[0]
    assert recipients == ["7KMPCBS5", "M5TT27JA"]
    assert "830001" in texto
    assert "loja 930001" in texto
    assert "x1" in texto
    assert "Aguardando Cancelamento" in texto


@pytest.mark.asyncio
async def test_sweep_sem_estoque_sem_recipients_nao_avisa(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Config vazia (default) = aviso desligado; o sweep segue normal."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="x1", bling_id=700009)
    db.add(Product(user_id=admin.id, sku="x1", name="X1", stock=-2))
    await db.commit()
    fake = _FakeBlingSituacao()
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients", "", raising=False
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 1
    assert _FakeThreema.enviados == []


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


async def _faturador_id(db: AsyncSession, bling_store_id: str):
    return (
        await db.execute(
            select(NfFaturador.id).where(
                NfFaturador.nome == f"faturador {bling_store_id}"
            )
        )
    ).scalar_one()


def _cmd_pending(faturador_id, numeros: list[str]) -> NfCommand:
    """Pending do próprio sweep (source='auto') aguardando lease."""
    return NfCommand(
        faturador_id=faturador_id,
        action="import_avulsa",
        numeros=numeros,
        planilha=b"velho",
        nome_arquivo="velho.csv",
        status="pending",
        source="auto",
    )


@pytest.mark.asyncio
async def test_sweep_funde_backlog_do_mesmo_faturador(db: AsyncSession, admin: User):
    """2 pendings do mesmo faturador (executor atrasado) fundem num comando
    só com planilha nova — mesmo SEM candidato novo no tick."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1", bling_id=700001)
    await _seed_pedido(db, numero="830002", loja="930001", sku="a2", bling_id=700002)
    fat_id = await _faturador_id(db, "930001")
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="processando"))
    db.add(NfFaturamento(pedido_bling="830002", status_faturamento="processando"))
    db.add(_cmd_pending(fat_id, ["830001"]))
    db.add(_cmd_pending(fat_id, ["830002"]))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 0
    assert summary["fundidos"] == 2
    assert summary["comandos"] == 1
    assert summary["enfileirados"] == 2

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds) == 1
    assert cmds[0].status == "pending"
    assert cmds[0].faturador_id == fat_id
    assert sorted(cmds[0].numeros) == ["830001", "830002"]
    assert cmds[0].planilha != b"velho"  # planilha regenerada com os 2


@pytest.mark.asyncio
async def test_sweep_pending_unico_nao_recria(db: AsyncSession, admin: User):
    """Anti-churn: 1 pending por faturador e nada novo → tick não mexe (não
    deleta/recria o mesmo comando a cada 2 min)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1")
    fat_id = await _faturador_id(db, "930001")
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="processando"))
    db.add(_cmd_pending(fat_id, ["830001"]))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 0
    assert summary["fundidos"] == 0
    assert summary["comandos"] == 0

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds) == 1
    assert cmds[0].planilha == b"velho"  # comando original intacto


@pytest.mark.asyncio
async def test_sweep_novo_candidato_funde_com_pending(db: AsyncSession, admin: User):
    """Candidato novo do mesmo faturador funde com o pending existente: o
    comando velho some e nasce UM comando com os dois pedidos."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1", bling_id=700001)
    await _seed_pedido(db, numero="830002", loja="930001", sku="a2", bling_id=700002)
    db.add(Product(user_id=admin.id, sku="a2", name="A2", stock=3))
    fat_id = await _faturador_id(db, "930001")
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="processando"))
    db.add(_cmd_pending(fat_id, ["830001"]))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["fundidos"] == 1
    assert summary["comandos"] == 1
    assert summary["enfileirados"] == 2

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert len(cmds) == 1
    assert sorted(cmds[0].numeros) == ["830001", "830002"]
    assert cmds[0].planilha != b"velho"
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830002")
        )
    ).scalar_one()
    assert fat.status_faturamento == "processando"


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


def test_tags_do_sku_classificacao():
    """Classificador puro das tags de rota (precedência confirmada)."""
    assert _tags_do_sku("dg055.ci") == {"ci"}
    assert _tags_do_sku("dg055.ci+a003.ci") == {"ci"}  # composto real
    assert _tags_do_sku("dg055.ci+b001.20") == {"ci", "mala"}
    assert _tags_do_sku("a010.sa") == {"sa"}
    assert _tags_do_sku("a010.pi") == {"pi"}
    assert _tags_do_sku("a010.sp") == {"sp"}
    assert _tags_do_sku("b001.20") == {"mala"}  # prefixo b+dígito
    assert _tags_do_sku("z0137.mala") == {"mala"}  # .mala vence o z
    assert _tags_do_sku("z0140") == {"usados"}
    assert _tags_do_sku("Z0141.us") == {"usados"}
    assert _tags_do_sku("uaf001m1.110") == {"eletro"}
    assert _tags_do_sku("a001.us") == set()  # sem rota → só grupo geral
    assert _tags_do_sku("a001.cd") == set()
    assert _tags_do_sku("a001") == set()


@pytest.mark.asyncio
async def test_sweep_sem_estoque_roteia_por_tag(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Grupo geral recebe TUDO; responsáveis de tag só o estoque deles:
    pedido .ci → Espanha/Israel/África; pedido mala → Ghost/Marrocos."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="dg055.ci", bling_id=700009
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="b001.20", bling_id=700010
    )
    db.add(Product(user_id=admin.id, sku="dg055.ci", name="CI", stock=-2))
    db.add(Product(user_id=admin.id, sku="b001.20", name="Mala", stock=-1))
    await db.commit()
    fake = _FakeBlingSituacao()
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients",
        "CDSA84BZ,7KMPCBS5", raising=False,
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 2

    # 3 envios: geral (completo) + grupo ci + grupo mala
    assert len(_FakeThreema.enviados) == 3
    texto_geral, dest_geral = _FakeThreema.enviados[0]
    assert dest_geral == ["CDSA84BZ", "7KMPCBS5"]
    assert "830001" in texto_geral and "830002" in texto_geral

    por_dests = {
        frozenset(dests): texto for texto, dests in _FakeThreema.enviados[1:]
    }
    texto_ci = por_dests[frozenset({"5WWUUBTT", "2YSBC69R", "EV6NR2BU"})]
    assert "830001" in texto_ci and "dg055.ci" in texto_ci
    assert "830002" not in texto_ci
    texto_mala = por_dests[frozenset({"HVEMH8NM", "BZMZWXC5"})]
    assert "830002" in texto_mala and "b001.20" in texto_mala
    assert "830001" not in texto_mala


@pytest.mark.asyncio
async def test_sweep_sem_estoque_tag_nao_duplica_geral(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Responsável de tag que também está no grupo geral não recebe 2×."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="dg055.ci", bling_id=700009
    )
    db.add(Product(user_id=admin.id, sku="dg055.ci", name="CI", stock=-2))
    await db.commit()
    fake = _FakeBlingSituacao()
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )
    # Espanha (5WWUUBTT, responsável .ci) também no grupo geral
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients",
        "CDSA84BZ,5WWUUBTT", raising=False,
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 1

    assert len(_FakeThreema.enviados) == 2
    _, dest_geral = _FakeThreema.enviados[0]
    assert dest_geral == ["CDSA84BZ", "5WWUUBTT"]
    _, dest_ci = _FakeThreema.enviados[1]
    assert sorted(dest_ci) == ["2YSBC69R", "EV6NR2BU"]  # Espanha não repete


@pytest.mark.asyncio
async def test_sweep_restricao_apple_rj_bloqueia(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Shopee + produto Apple (pelo nome) + destino RJ: NÃO enfileira;
    "restrição" vai pras Observações do pedido (PUT) ANTES do PATCH pra
    83955, e nf_faturamento fica 'restricao' (tentativa única)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="ap1", bling_id=700009,
        item_descricao="Iphone 15 Apple 128GB", uf_destino="RJ",
    )
    await db.commit()
    fake = _FakeBlingSituacao(
        orders={700009: {"id": 700009, "observacoes": ""}}
    )
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["restricao"] == 1
    assert summary["sem_estoque"] == 0
    assert summary["enfileirados"] == 0

    # ORDEM crítica: Observação (PUT) primeiro, situação (PATCH) depois —
    # um PUT com body stale reverteria o 83955 recém-aplicado.
    assert fake.eventos == [("put", 700009), ("situacao", 700009)]
    assert fake.chamadas == [(700009, 83955)]
    [(_put_id, body)] = fake.puts
    assert "restrição" in (body.get("observacoes") or "")

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
    assert fat.status_faturamento == "restricao"
    assert "Restrição" in (fat.erro_faturamento or "")
    assert "Iphone 15 Apple 128GB" in (fat.erro_faturamento or "")

    # tentativa automática única: não re-pega nem re-escreve no Bling
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 0
    assert len(fake.puts) == 1
    assert len(fake.chamadas) == 1


@pytest.mark.asyncio
async def test_sweep_restricao_fora_do_escopo_enfileira_normal(
    db: AsyncSession, admin: User
):
    """Contra-casos NÃO bloqueiam: Apple→MG, não-Apple→RJ e Apple→RJ mas
    TikTok seguem o fluxo normal de enfileiramento."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_loja(db, admin, plataforma="tiktok", bling_store_id="930002")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="a1", bling_id=700001,
        item_descricao="Iphone 14 Apple", uf_destino="MG",
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="a2", bling_id=700002,
        item_descricao="Capinha Galaxy", uf_destino="RJ",
    )
    await _seed_pedido(
        db, numero="830003", loja="930002", sku="a3", bling_id=700003,
        item_descricao="Ipad Apple 10", uf_destino="RJ",
    )
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=3))
    db.add(Product(user_id=admin.id, sku="a2", name="A2", stock=3))
    db.add(Product(user_id=admin.id, sku="a3", name="A3", stock=3))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 3
    assert summary["restricao"] == 0
    assert summary["enfileirados"] == 3

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == [
        "830001", "830002", "830003"
    ]
