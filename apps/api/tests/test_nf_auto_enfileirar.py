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
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    NfCommand,
    NfFaturador,
    NfFaturamento,
    NfImpressao,
    Product,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.config import get_settings
from app.security.cipher import encrypt
from app.services import nf_auto_enfileirar, nf_emissao_gerar, threema
from app.services.nf_auto_enfileirar import (
    avaliar_restricao_loja,
    horas_de,
    loja_emite_agora,
    pedido_sai_no_sabado,
    run_auto_enfileirar_nf,
    tags_de,
)


async def _async_return(value: object) -> object:
    """`_bling_client_opt` é async — o monkeypatch devolve a coroutine pronta."""
    return value


# Datas de referência (BRT): 24/08/2026 = segunda, 22/08 = sábado, 23/08 = domingo.
def _BRT(ano: int, mes: int, dia: int, hora: int) -> datetime:
    return datetime(ano, mes, dia, hora, tzinfo=ZoneInfo("America/Sao_Paulo"))


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
    com_faturador: bool = True, excecoes: list[dict] | None = None,
    uf_restrictions: list[str] | None = None, sales_team: int | None = None,
    impressao: str | None = None, etiqueta_horarios: str | None = None,
    etiqueta_sabado_horario: str | None = None,
    etiqueta_sabado_tags: str | None = None,
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
    impressao_id = None
    if impressao:
        imp = NfImpressao(tipo=impressao)
        db.add(imp)
        await db.flush()
        impressao_id = imp.id
    db.add(
        StoreInfo(
            user_id=admin.id, platform=plataforma,
            account_name=f"loja {bling_store_id}",
            bling_store_id=bling_store_id, nf_faturador_id=faturador_id,
            nf_impressao_id=impressao_id,
            etiqueta_horarios=etiqueta_horarios,
            etiqueta_sabado_horario=etiqueta_sabado_horario,
            etiqueta_sabado_tags=etiqueta_sabado_tags,
            excecoes=excecoes, uf_restrictions=uf_restrictions,
            sales_team=sales_team,
        )
    )
    await db.flush()


async def _seed_pedido(
    db: AsyncSession, *, numero: str, loja: str, sku: str,
    situacao: str = "6", bling_id: int = 700001,
    data: datetime | None = None, item_produto_id: int | None = None,
    item_descricao: str | None = None, uf_destino: str = "MG",
    valorbase: float | None = None,
) -> None:
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=numero,
            valorbase=valorbase,
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


def test_horas_e_tags_leem_o_cadastro_da_loja():
    """Os dois campos da tela Lojas são texto solto — vazio/NULL é o default
    (contínuo pros horários, nenhum estoque pras tags)."""
    assert horas_de("10:00, 14:00") == (10, 14)
    assert horas_de("08:00") == (8,)
    assert horas_de("") == ()
    assert horas_de(None) == ()
    assert tags_de("pi, ra") == {"pi", "ra"}
    assert tags_de(None) == set()


def test_loja_emite_agora_continuos():
    """Shopee, TikTok e Amazon não têm regra de horário — emitem sempre,
    inclusive no domingo."""
    for plataforma in ("shopee", "tiktok", "amazon"):
        assert loja_emite_agora(
            plataforma=plataforma, impressao="agencia",
            etiqueta_horarios="10:00", sabado_horario=None,
            agora=_BRT(2026, 8, 23, 3),  # domingo de madrugada
        )


def test_loja_emite_agora_ml_correios_e_continuo():
    """ML de correios segue contínuo todo dia, sábado e domingo inclusive."""
    for dia in (22, 23, 24):  # sábado, domingo, segunda
        assert loja_emite_agora(
            plataforma="ml", impressao="correios",
            etiqueta_horarios="10:00, 14:00", sabado_horario=None,
            agora=_BRT(2026, 8, dia, 12),
        )


def test_loja_emite_agora_ml_agencia_dia_util():
    """Dia útil: a agência só emite nas horas cadastradas; loja SEM horário
    cadastrado fica de fora do automático ("as que estao sem horario etiqueta
    nao faça nada")."""
    def emite(hora: int, horarios: str | None) -> bool:
        return loja_emite_agora(
            plataforma="ml", impressao="agencia",
            etiqueta_horarios=horarios, sabado_horario=None,
            agora=_BRT(2026, 8, 24, hora),
        )

    assert emite(10, "10:00, 14:00")
    assert emite(14, "10:00, 14:00")
    assert not emite(12, "10:00, 14:00")
    assert not emite(12, None)  # sem horário = automático não age
    assert not emite(12, "")


def test_loja_emite_agora_ml_agencia_sabado_e_domingo():
    """Sábado: uma vez só, na hora cadastrada (NULL = não emite).
    Domingo: nunca."""
    def emite(dia: int, hora: int, sabado: str | None) -> bool:
        return loja_emite_agora(
            plataforma="ml", impressao="agencia",
            etiqueta_horarios="10:00, 14:00", sabado_horario=sabado,
            agora=_BRT(2026, 8, dia, hora),
        )

    assert emite(22, 8, "08:00")  # sábado na hora marcada
    assert not emite(22, 10, "08:00")  # nem os horários de dia útil valem
    assert not emite(22, 8, None)  # sem hora de sábado = não emite
    assert not emite(23, 8, "08:00")  # domingo nunca
    assert not emite(23, 10, "08:00")


def test_pedido_sai_no_sabado_exige_todos_os_itens_marcados():
    """No sábado a agência só emite dos estoques marcados; item de estoque
    fechado trava o pedido inteiro (não adianta faturar o que não despacha)."""
    assert pedido_sai_no_sabado(["b001.pi"], {"pi", "ra"})
    assert pedido_sai_no_sabado(["b001.pi", "b002.ra"], {"pi", "ra"})
    assert not pedido_sai_no_sabado(["b001.pi", "b002.sp"], {"pi", "ra"})
    assert not pedido_sai_no_sabado(["b001.pi"], set())  # loja sem tag marcada
    assert not pedido_sai_no_sabado([], {"pi"})


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
async def test_sweep_ml_amazon_entram_na_janela(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Com a flag ligada, no horário da loja (10h de um dia útil), pedidos ML
    (agência) e Amazon 'Em aberto' com faturador entram junto com os contínuos."""
    monkeypatch.setattr(get_settings(), "nf_auto_ml_amazon", True, raising=False)
    monkeypatch.setattr(
        nf_auto_enfileirar, "_agora_brt", lambda: _BRT(2026, 8, 24, 10)  # segunda
    )
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930003",
        impressao="agencia", etiqueta_horarios="10:00, 14:00",
    )
    await _seed_loja(db, admin, plataforma="amazon", bling_store_id="930005")
    await _seed_pedido(db, numero="830003", loja="930003", sku="a3")
    await _seed_pedido(db, numero="830005", loja="930005", sku="a5")
    db.add(Product(user_id=admin.id, sku="a3", name="A3", stock=3))
    db.add(Product(user_id=admin.id, sku="a5", name="A5", stock=2))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 2
    assert summary["enfileirados"] == 2

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert {c.action for c in cmds} == {"import_avulsa"}
    assert sorted(n for c in cmds for n in c.numeros) == ["830003", "830005"]
    fats = {
        f.pedido_bling: f
        for f in (await db.execute(select(NfFaturamento))).scalars().all()
    }
    assert fats["830003"].status_faturamento == "processando"
    assert fats["830005"].status_faturamento == "processando"

    # às 14h também entra
    monkeypatch.setattr(
        nf_auto_enfileirar, "_agora_brt", lambda: _BRT(2026, 8, 24, 14)
    )
    summary14 = await run_auto_enfileirar_nf()
    assert summary14["candidatos"] == 0  # dedupe: já processados


@pytest.mark.asyncio
async def test_sweep_ml_agencia_fora_do_horario_da_loja(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """12h de um dia útil: o ML de agência tem horário 10h/14h na tela Lojas →
    fica de fora. Shopee e Amazon são CONTÍNUOS e entram; o ML de correios
    também é contínuo (não tem regra de horário)."""
    monkeypatch.setattr(get_settings(), "nf_auto_ml_amazon", True, raising=False)
    monkeypatch.setattr(
        nf_auto_enfileirar, "_agora_brt", lambda: _BRT(2026, 8, 24, 12)  # segunda
    )
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930004",
        impressao="agencia", etiqueta_horarios="10:00, 14:00",
    )
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930009",
        impressao="correios", etiqueta_horarios="10:00, 14:00",
    )
    await _seed_loja(db, admin, plataforma="amazon", bling_store_id="930006")
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830004", loja="930004", sku="a4")
    await _seed_pedido(db, numero="830009", loja="930009", sku="a9")
    await _seed_pedido(db, numero="830006", loja="930006", sku="a6")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1")
    for sku in ("a4", "a9", "a6", "a1"):
        db.add(Product(user_id=admin.id, sku=sku, name=sku.upper(), stock=3))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 3  # shopee + amazon + ml correios

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830001", "830006", "830009"]
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830004")
        )
    ).scalar_one_or_none()
    assert fat is None


@pytest.mark.asyncio
async def test_sweep_ml_amazon_flag_desligada_nao_entram(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Flag nf_auto_ml_amazon DESLIGADA (default): mesmo no horário da loja,
    ML e Amazon ficam de fora — pausa pra testar supervisionado."""
    monkeypatch.setattr(get_settings(), "nf_auto_ml_amazon", False, raising=False)
    monkeypatch.setattr(
        nf_auto_enfileirar, "_agora_brt", lambda: _BRT(2026, 8, 24, 10)
    )
    await _seed_loja(db, admin, plataforma="ml", bling_store_id="930007")
    await _seed_loja(db, admin, plataforma="amazon", bling_store_id="930008")
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(db, numero="830007", loja="930007", sku="a7")
    await _seed_pedido(db, numero="830008", loja="930008", sku="a8")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1")
    db.add(Product(user_id=admin.id, sku="a7", name="A7", stock=3))
    db.add(Product(user_id=admin.id, sku="a8", name="A8", stock=3))
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=3))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1  # só o shopee
    assert summary["enfileirados"] == 1

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830001"]


@pytest.mark.asyncio
async def test_sweep_sabado_agencia_so_dos_estoques_marcados(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Sábado 08h: a loja ML de agência emite uma vez, às 08:00, e só pros
    estoques marcados (pi) — o pedido do estoque sp fica pra segunda. O ML de
    correios segue contínuo no sábado, sem regra de estoque."""
    monkeypatch.setattr(get_settings(), "nf_auto_ml_amazon", True, raising=False)
    monkeypatch.setattr(
        nf_auto_enfileirar, "_agora_brt", lambda: _BRT(2026, 8, 22, 8)  # sábado
    )
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930010",
        impressao="agencia", etiqueta_horarios="10:00, 14:00",
        etiqueta_sabado_horario="08:00", etiqueta_sabado_tags="pi",
    )
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930011", impressao="correios",
    )
    await _seed_pedido(db, numero="830010", loja="930010", sku="b001.pi")
    await _seed_pedido(db, numero="830011", loja="930010", sku="b002.sp")
    await _seed_pedido(db, numero="830012", loja="930011", sku="b003.sp")
    for sku in ("b001.pi", "b002.sp", "b003.sp"):
        db.add(Product(user_id=admin.id, sku=sku, name=sku, stock=3))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 2  # agência/pi + correios (contínuo)

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830010", "830012"]
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830011")
        )
    ).scalar_one_or_none()
    assert fat is None  # estoque sp não sai no sábado — segue pra segunda


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
    await _seed_loja(
        db, admin, plataforma="shopee", bling_store_id="930001", sales_team=2
    )
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
    # rótulo = "plataforma conta equipe N", ex. "(shopee vortan equipe 2)"
    assert "(shopee loja 930001 equipe 2)" in texto
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


def test_avaliar_restricao_loja_regras_puras():
    """Motor puro: a Restrição (uf_restrictions) BLOQUEIA a UF; as Exceções
    (valor/sku/palavra) LIBERAM. Dados sujos ignorados sem quebrar."""
    itens = [("A001", "Carregador Apple 20W"), ("B002", "Capinha Galaxy")]
    rj = ["RJ"]

    # valor: libera abaixo do limite, bloqueia daí pra cima
    regra_valor = [{"tipo": "valor", "valor": 700}]
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="RJ", ufs_restricao=rj, valor_total=700, itens=itens
    )
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="rj", ufs_restricao=rj, valor_total=800, itens=itens
    )
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="RJ", ufs_restricao=rj, valor_total=699.99, itens=itens
    ) is None
    # UF fora da Restrição nunca bloqueia
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="MG", ufs_restricao=rj, valor_total=900, itens=itens
    ) is None
    assert avaliar_restricao_loja(
        regra_valor, uf_destino=None, ufs_restricao=rj, valor_total=900, itens=itens
    ) is None
    # loja sem Restrição configurada → nada bloqueia
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="RJ", ufs_restricao=None, valor_total=900, itens=itens
    ) is None
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="RJ", ufs_restricao=[], valor_total=900, itens=itens
    ) is None
    # Restrição com várias UFs: qualquer uma casa
    assert avaliar_restricao_loja(
        regra_valor, uf_destino="MG", ufs_restricao=["RJ", "mg"],
        valor_total=900, itens=itens,
    )
    # regra legada gravada com "uf" segue válida (chave ignorada)
    legada = [{"uf": "SP", "tipo": "valor", "valor": 700}]
    assert avaliar_restricao_loja(
        legada, uf_destino="RJ", ufs_restricao=rj, valor_total=800, itens=itens
    )

    # sku: item na lista LIBERA (exato, case-insensitive); fora dela bloqueia
    regra_sku = [{"tipo": "sku", "termos": ["a001"]}]
    assert avaliar_restricao_loja(
        regra_sku, uf_destino="RJ", ufs_restricao=rj, valor_total=50, itens=itens
    ) is None
    motivo = avaliar_restricao_loja(
        regra_sku, uf_destino="RJ", ufs_restricao=rj, valor_total=50,
        itens=[("A0011", "Outro")],
    )
    assert "RJ" in motivo and "a001" in motivo

    # palavra: substring no nome LIBERA (case-insensitive)
    regra_palavra = [{"tipo": "palavra", "termos": ["apple"]}]
    assert avaliar_restricao_loja(
        regra_palavra, uf_destino="RJ", ufs_restricao=rj, valor_total=50, itens=itens
    ) is None
    assert avaliar_restricao_loja(
        regra_palavra, uf_destino="RJ", ufs_restricao=rj, valor_total=50,
        itens=[(None, "Capinha Galaxy")],
    )
    # basta UM item casar pra liberar o pedido inteiro
    assert avaliar_restricao_loja(
        regra_palavra, uf_destino="RJ", ufs_restricao=rj, valor_total=50,
        itens=[("Z9", "Capinha Galaxy"), ("A1", "Fone Apple")],
    ) is None

    # dados sujos: regra não-dict, valor não numérico, termos vazios, sem
    # regras → loja fica inerte, nunca bloqueia
    sujas = ["lixo", {"tipo": "valor", "valor": "abc"}, {"tipo": "zzz"}]
    assert avaliar_restricao_loja(
        sujas, uf_destino="RJ", ufs_restricao=rj, valor_total=999, itens=itens
    ) is None
    assert avaliar_restricao_loja(
        [{"tipo": "palavra", "termos": []}], uf_destino="RJ", ufs_restricao=rj,
        valor_total=999, itens=itens,
    ) is None
    assert avaliar_restricao_loja(
        [], uf_destino="RJ", ufs_restricao=rj, valor_total=999, itens=itens
    ) is None


@pytest.mark.asyncio
async def test_sweep_restricao_loja_valor_bloqueia(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Loja que não envia pro RJ, com exceção "valor" 700 (libera só abaixo
    disso), bloqueia o pedido de 800: obs "restrição" (PUT) antes do PATCH
    83955, painel 'restricao' com o motivo, tentativa única."""
    await _seed_loja(
        db, admin, plataforma="shopee", bling_store_id="930001",
        excecoes=[{"tipo": "valor", "valor": 700}],
        uf_restrictions=["RJ"],
    )
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="c1", bling_id=700009,
        uf_destino="RJ", valorbase=800,
    )
    await db.commit()
    fake = _FakeBlingSituacao(orders={700009: {"id": 700009, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["restricao_loja"] == 1
    assert summary["restricao"] == 0
    assert summary["enfileirados"] == 0

    # mesma ordem crítica da restrição hardcoded: PUT antes do PATCH
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
    assert "não envia pro RJ" in (fat.erro_faturamento or "")
    assert "R$ 700,00" in (fat.erro_faturamento or "")

    # tentativa automática única
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 0
    assert len(fake.puts) == 1
    assert len(fake.chamadas) == 1


@pytest.mark.asyncio
async def test_sweep_restricao_loja_fora_do_escopo_enfileira_normal(
    db: AsyncSession, admin: User
):
    """Contra-casos passam direto: valor abaixo do limite (exceção LIBERA),
    UF fora da Restrição e loja SEM exceções enfileiram normal."""
    await _seed_loja(
        db, admin, plataforma="shopee", bling_store_id="930001",
        excecoes=[{"tipo": "valor", "valor": 700}],
        uf_restrictions=["RJ"],
    )
    await _seed_loja(db, admin, plataforma="tiktok", bling_store_id="930002")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="a1", bling_id=700001,
        uf_destino="RJ", valorbase=500,
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="a2", bling_id=700002,
        uf_destino="MG", valorbase=900,
    )
    await _seed_pedido(
        db, numero="830003", loja="930002", sku="a3", bling_id=700003,
        uf_destino="RJ", valorbase=900,
    )
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=3))
    db.add(Product(user_id=admin.id, sku="a2", name="A2", stock=3))
    db.add(Product(user_id=admin.id, sku="a3", name="A3", stock=3))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 3
    assert summary["restricao_loja"] == 0
    assert summary["enfileirados"] == 3

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == [
        "830001", "830002", "830003"
    ]


@pytest.mark.asyncio
async def test_sweep_restricao_loja_palavra_libera_o_que_casa(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Caso real: loja não envia pro RS, EXCETO airfryer. O pedido de
    airfryer fatura normal; o de outro produto vai pra 83955."""
    await _seed_loja(
        db, admin, plataforma="shopee", bling_store_id="930001",
        excecoes=[{"tipo": "palavra", "termos": ["airfryer"]}],
        uf_restrictions=["RS"],
    )
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="a1", bling_id=700001,
        uf_destino="RS", item_descricao="Airfryer Vidro UAF001 M1 220v",
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="a2", bling_id=700002,
        uf_destino="RS", item_descricao="Capinha Galaxy",
    )
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=3))
    db.add(Product(user_id=admin.id, sku="a2", name="A2", stock=3))
    await db.commit()
    fake = _FakeBlingSituacao(orders={700002: {"id": 700002, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 2
    assert summary["restricao_loja"] == 1
    assert summary["enfileirados"] == 1

    assert fake.chamadas == [(700002, 83955)]
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830001"]
