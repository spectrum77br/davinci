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
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingKitComponent,
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


class _RespostaSaldos:
    def __init__(self, data: list[dict]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": self._data}


class _FakeBlingSituacao:
    """Captura os PATCH de situação que o motor manda pro Bling e serve o
    saldo VIVO do check de estoque (`GET /estoques/saldos`: virtual e físico
    na mesma resposta). Pra restrição, serve o pedido (`get_order`) e captura
    o PUT das Observações."""

    def __init__(
        self,
        saldos: dict[int, float] | None = None,
        orders: dict[int, dict] | None = None,
        fisicos: dict[int, float] | None = None,
    ) -> None:
        self.chamadas: list[tuple[int, int]] = []
        self.saldos = saldos or {}
        self.fisicos = fisicos or {}
        self.orders = orders or {}
        self.puts: list[tuple[int, dict]] = []
        self.eventos: list[tuple[str, int]] = []
        self.pedidos_saldos: list[list[str]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.chamadas.append((bling_order_id, situacao_id))
        self.eventos.append(("situacao", bling_order_id))

    async def get_product(self, bling_product_id: int) -> dict:
        raise AssertionError("o check de estoque lê tudo do /estoques/saldos")

    async def _request(self, method: str, path: str, params=None):
        assert (method, path) == ("GET", "/estoques/saldos")
        ids = [v for k, v in params or [] if k == "idsProdutos[]"]
        assert len(ids) <= 50
        self.pedidos_saldos.append(ids)
        linhas = []
        for i in map(int, ids):
            if i not in self.saldos and i not in self.fisicos:
                continue
            linha: dict = {"produto": {"id": i}, "saldoVirtualTotal": self.saldos.get(i)}
            if i in self.fisicos:
                linha["saldoFisicoTotal"] = self.fisicos[i]
            linhas.append(linha)
        return _RespostaSaldos(linhas)

    async def get_order(self, bling_order_id: int) -> dict:
        return self.orders[bling_order_id]

    async def update_order(self, bling_order_id: int, body: dict) -> dict:
        self.puts.append((bling_order_id, body))
        self.eventos.append(("put", bling_order_id))
        return body


@pytest.fixture(autouse=True)
def _sem_espera_do_tipo_de_envio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os pedidos destes testes acabaram de cair e ninguém leu o envio: com a
    espera do Flex ligada (flex_espera_envio_min, prioridade_estoque) eles
    ficariam para a próxima rodada. As regras do enfileirar são testadas sem
    ela; a espera tem teste próprio (test_sweep_espera_o_tipo_de_envio)."""
    monkeypatch.setattr(get_settings(), "flex_espera_envio_min", 0)


class _FakeRedis:
    """Redis em memória: SET NX / DELETE (marcas dos avisos) e INCR / TTL /
    EXPIRE (contador de recusas da 83955)."""

    def __init__(self) -> None:
        self.chaves: dict[str, str] = {}
        self.validade: dict[str, int] = {}

    async def set(self, chave: str, valor: str, nx: bool = False, ex: int | None = None):
        if nx and chave in self.chaves:
            return None
        self.chaves[chave] = valor
        if ex:
            self.validade[chave] = ex
        return True

    async def delete(self, *chaves: str) -> int:
        for c in chaves:
            self.chaves.pop(c, None)
            self.validade.pop(c, None)
        return len(chaves)

    async def incr(self, chave: str) -> int:
        n = int(self.chaves.get(chave, "0")) + 1
        self.chaves[chave] = str(n)
        return n

    async def ttl(self, chave: str) -> int:
        if chave not in self.chaves:
            return -2
        return self.validade.get(chave, -1)

    async def expire(self, chave: str, segundos: int) -> bool:
        self.validade[chave] = segundos
        return chave in self.chaves


@pytest.fixture(autouse=True)
def _redis_falso(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Marcas de aviso e contador de recusas da 83955 ficam em memória, um
    Redis novo por teste — o contador do dia nunca vaza entre testes (nem
    entre rodadas da suíte num Redis de verdade)."""
    falso = _FakeRedis()
    monkeypatch.setattr(nf_auto_enfileirar, "redis", falso)
    return falso


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def limpa_kits(db: AsyncSession):
    """`bling_kit_components` não está na limpeza do conftest."""
    yield
    await db.execute(delete(BlingKitComponent))
    await db.commit()


async def _seed_loja(
    db: AsyncSession, admin: User, *, plataforma: str, bling_store_id: str,
    com_faturador: bool = True, excecoes: list[dict] | None = None,
    uf_restrictions: list[str] | None = None, sales_team: int | None = None,
    commercial_team: int | None = None,
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
            sales_team=sales_team, commercial_team=commercial_team,
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
async def test_sweep_espera_o_tipo_de_envio(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Projeto Flex (revisão de 02/10/2026): o pedido Shopee que acabou de cair
    e ainda não teve o envio lido pelo shipment check (sem prazo, sem tipo de
    envio) não vai para a NF agora — pode ser Flex (sai do .sp). Volta na
    rodada seguinte, já lido. TikTok não tem Flex: segue na hora."""
    # Desligados de fábrica desde 05/10/2026 (Flex só para quem vê): aqui ligados.
    monkeypatch.setattr(get_settings(), "flex_pedido_no_sp", True)
    monkeypatch.setattr(get_settings(), "flex_espera_envio_min", 10)
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", lambda s: _async_return(None))
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_loja(db, admin, plataforma="tiktok", bling_store_id="930002")
    await _seed_pedido(db, numero="830001", loja="930001", sku="a1", bling_id=700001)
    await _seed_pedido(db, numero="830002", loja="930002", sku="a2", bling_id=700002)
    db.add(Product(user_id=admin.id, sku="a1", name="A1", stock=3))
    db.add(Product(user_id=admin.id, sku="a2", name="A2", stock=1))
    await db.commit()

    summary = await run_auto_enfileirar_nf()
    assert summary["adiados_prioridade"] == 1
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830002"]
    fats = (await db.execute(select(NfFaturamento.pedido_bling))).scalars().all()
    assert "830001" not in fats  # nem conta a tentativa única

    # O shipment check leu o envio (gravou o prazo; não é Flex): entra.
    await db.execute(
        BlingOrder.__table__.update()
        .where(BlingOrder.numero == "830001")
        .values(marketplace_ship_deadline=datetime.now(UTC) + timedelta(days=1))
    )
    await db.commit()
    summary = await run_auto_enfileirar_nf()
    assert summary.get("adiados_prioridade", 0) == 0
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830001", "830002"]


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


def test_plataformas_ativas_por_flag():
    """Sem flag: só Shopee/TikTok. `nf_auto_ml`: soma o ML (Eduardo 15/09:
    "ligar o automático igual da Shopee", Amazon fora). Mestre: os quatro."""
    f = nf_auto_enfileirar._plataformas_ativas
    assert f(ml_amazon=False, so_ml=False) == ("shopee", "tiktok")
    assert f(ml_amazon=False, so_ml=True) == ("shopee", "tiktok", "ml")
    assert f(ml_amazon=True, so_ml=False) == ("shopee", "tiktok", "ml", "amazon")
    assert f(ml_amazon=True, so_ml=True) == ("shopee", "tiktok", "ml", "amazon")


@pytest.mark.asyncio
async def test_sweep_flag_so_ml_entra_ml_e_nao_amazon(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """NF_AUTO_ML ligada (e a mestre desligada): ML entra no horário da loja
    com a checagem de estoque de sempre; Amazon continua fora."""
    monkeypatch.setattr(get_settings(), "nf_auto_ml_amazon", False, raising=False)
    monkeypatch.setattr(get_settings(), "nf_auto_ml", True, raising=False)
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
    await _seed_pedido(db, numero="830006", loja="930003", sku="a6")
    db.add(Product(user_id=admin.id, sku="a3", name="A3", stock=3))
    db.add(Product(user_id=admin.id, sku="a5", name="A5", stock=2))
    db.add(Product(user_id=admin.id, sku="a6", name="A6", stock=-1))  # sem estoque
    await db.commit()
    # o Bling aceita a 83955 (sem ele o pedido ficaria sem marca, pra tentar de novo)
    fake = _FakeBlingSituacao(orders={700001: {"id": 700001, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 2  # só os dois do ML
    assert summary["enfileirados"] == 1
    assert summary["sem_estoque"] == 1

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(n for c in cmds for n in c.numeros) == ["830003"]
    fats = {
        f.pedido_bling: f
        for f in (await db.execute(select(NfFaturamento))).scalars().all()
    }
    assert fats["830003"].status_faturamento == "processando"
    assert fats["830006"].status_faturamento == "sem_estoque"
    assert "830005" not in fats  # Amazon fora


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
    fake = _FakeBlingSituacao(orders={700009: {"id": 700009, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 1
    assert summary["sem_estoque"] == 1
    assert summary["enfileirados"] == 0

    assert fake.chamadas == [(700009, 83955)]
    # recado nas Observações (13/10) ANTES da situação, com o SKU que travou
    assert fake.eventos == [("put", 700009), ("situacao", 700009)]
    [(_put_id, body)] = fake.puts
    assert body["observacoes"].endswith(" - Sem estoque — saldo negativo: x1")
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


@pytest.mark.asyncio
async def test_sweep_sem_estoque_fifo_so_o_mais_novo_vai(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Queixa do Eduardo (30/10): saldo −1 com dois pedidos da mesma peça. O
    mais antigo tem a peça reservada e SEGUE pra fila; só o mais novo (que
    estourou o saldo) vai pra Aguardando Cancelamento."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    agora = datetime.now(UTC)
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700001,
        item_produto_id=555, data=agora - timedelta(days=2),
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="b1", bling_id=700002,
        item_produto_id=555, data=agora - timedelta(days=1),
    )
    await db.commit()
    fake = _FakeBlingSituacao(
        saldos={555: -1.0}, orders={700002: {"id": 700002, "observacoes": ""}}
    )
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["candidatos"] == 2
    assert summary["sem_estoque"] == 1
    assert summary["enfileirados"] == 1
    assert fake.chamadas == [(700002, 83955)]
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert [n for c in cmds for n in c.numeros] == ["830001"]
    fats = {
        f.pedido_bling: f.status_faturamento
        for f in (await db.execute(select(NfFaturamento))).scalars().all()
    }
    assert fats == {"830001": "processando", "830002": "sem_estoque"}


@pytest.mark.asyncio
async def test_sweep_sem_estoque_fifo_kit_confere_pelo_componente(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None
):
    """Caso real do b026.18 (30/10): os kits b026.18.24 e b026.12.18 e o
    simples b026.18 disputam a MESMA peça (b026.18) — o Bling reserva no
    componente. Saldo da peça −2: vão os 2 pedidos mais novos, qualquer que
    seja o SKU; o kit mais antigo segue (antes iam os três)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    agora = datetime.now(UTC)
    for numero, bling_id, sku, pid, dias in (
        ("830001", 700001, "b026.18.24", 901, 3),
        ("830002", 700002, "b026.12.18", 902, 2),
        ("830003", 700003, "b026.18", 501, 1),
    ):
        await _seed_pedido(
            db, numero=numero, loja="930001", sku=sku, bling_id=bling_id,
            item_produto_id=pid, data=agora - timedelta(days=dias),
        )
    # kit 901 = b026.18 (501) + b026.24 (502); kit 902 = b026.12 (503) + b026.18
    for kit, comp in ((901, 501), (901, 502), (902, 503), (902, 501)):
        db.add(
            BlingKitComponent(
                kit_bling_product_id=kit, component_bling_product_id=comp, quantidade=1
            )
        )
    await db.commit()
    fake = _FakeBlingSituacao(
        saldos={901: -2.0, 902: -2.0, 501: -2.0, 502: 5.0, 503: 40.0},
        orders={
            700002: {"id": 700002, "observacoes": ""},
            700003: {"id": 700003, "observacoes": ""},
        },
    )
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 2
    assert summary["enfileirados"] == 1
    assert sorted(fake.chamadas) == [(700002, 83955), (700003, 83955)]
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert [n for c in cmds for n in c.numeros] == ["830001"]
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830002")
        )
    ).scalar_one()
    # o motivo nomeia o SKU do pedido (o kit), como antes
    assert fat.status_faturamento == "sem_estoque"
    assert "b026.12.18" in (fat.erro_faturamento or "")


@pytest.mark.parametrize(
    ("situacao", "status", "antigo_vai"),
    [
        # o mais novo Em aberto (outra loja, fora do lote) é quem estourou
        ("6", None, False),
        # o mais novo já subiu etiqueta: peça garantida, o déficit é do antigo
        ("21", None, True),
        # o mais novo já está na fila da NF: peça garantida também
        ("6", "processando", True),
        # o mais novo já está em Aguardando Cancelamento: não segura peça
        ("83955", "sem_estoque", True),
    ],
)
@pytest.mark.asyncio
async def test_sem_estoque_fifo_considera_quem_segura_fora_do_lote(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch,
    situacao: str, status: str | None, antigo_vai: bool,
):
    """A fila FIFO é de TODO pedido que segura a peça no Bling (qualquer
    loja/plataforma), não só dos conferidos agora: o pedido mais novo Em
    aberto fica com o déficit (fora do lote: só apontado, nunca movido); Em
    digitação/já enfileirado tem a peça garantida; Aguardando Cancelamento
    não reserva."""
    from app.routers.nf import _pedidos_sem_estoque, _sem_estoque_fifo

    agora = datetime.now(UTC)
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700001,
        item_produto_id=555, data=agora - timedelta(days=2),
    )
    await _seed_pedido(
        db, numero="830009", loja="930009", sku="b1", bling_id=700009,
        item_produto_id=555, data=agora - timedelta(days=1), situacao=situacao,
    )
    if status:
        db.add(NfFaturamento(pedido_bling="830009", status_faturamento=status))
    await db.commit()
    fake = _FakeBlingSituacao(saldos={555: -1.0})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    faltando = await _pedidos_sem_estoque(db, ["830001"])
    assert faltando == ({"830001": ["b1"]} if antigo_vai else {})
    fora = (await _sem_estoque_fifo(db, ["830001"])).fora_do_lote
    assert fora == ({} if antigo_vai else {"830009": ["b1"]})
    assert fake.chamadas == []  # conferir nunca move ninguém


@pytest.mark.asyncio
async def test_sweep_sem_estoque_fifo_ingest_atrasado_antigo_passa(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, limpa_kits: None
):
    """30/10 08:18: o Bling já reservava 9 pedidos da b026.18 (saldo −6), mas
    o espelho só tinha os 2 mais antigos (kit b026.12.18) + 1 Em digitação.
    Físico 4: os conhecidos cabem — o déficit é dos que ainda não entraram.
    Os 2 antigos seguem pra fila (antes iam pra Aguardando Cancelamento)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    agora = datetime.now(UTC)
    await _seed_pedido(
        db, numero="830000", loja="930001", sku="b026.18", bling_id=700000,
        item_produto_id=501, data=agora - timedelta(days=3), situacao="21",
    )
    for numero, bling_id in (("830001", 700001), ("830002", 700002)):
        await _seed_pedido(
            db, numero=numero, loja="930001", sku="b026.12.18", bling_id=bling_id,
            item_produto_id=902, data=agora - timedelta(days=1),
        )
    for comp in (503, 501):
        db.add(
            BlingKitComponent(
                kit_bling_product_id=902, component_bling_product_id=comp, quantidade=1
            )
        )
    await db.commit()
    fake = _FakeBlingSituacao(
        saldos={902: -6.0, 501: -6.0, 503: 197.0}, fisicos={501: 4.0}
    )
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 0
    assert summary["enfileirados"] == 2
    assert fake.chamadas == []
    # UMA leitura: os componentes do kit do lote, virtual e físico juntos (o
    # saldo do próprio kit com composição não decide nada — nem é lido)
    assert fake.pedidos_saldos == [["501", "503"]]


@pytest.mark.asyncio
async def test_sem_estoque_fifo_bling_falhou_nao_marca(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Consulta ao Bling falhando: ninguém vai pra Aguardando Cancelamento
    (nunca sem certeza) — e o pedido também não passa sem conferência: sai
    ADIADO, mesmo com o local dizendo que tem (o cache é clampado em 0)."""
    from app.routers.nf import _pedidos_sem_estoque, _sem_estoque_fifo

    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700001,
        item_produto_id=555,
    )
    db.add(Product(user_id=admin.id, sku="b1", name="B1", stock=0))
    await db.commit()

    class _Falha(_FakeBlingSituacao):
        async def _request(self, method: str, path: str, params=None):
            raise RuntimeError("429")

    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(_Falha())
    )
    assert await _pedidos_sem_estoque(db, ["830001"]) == {}
    res = await _sem_estoque_fifo(db, ["830001"])
    assert res.lote == {}
    assert res.adiados == {"830001": ["b1"]}


class _FakeThreema:
    """Captura o send_to_all do aviso de sem_estoque."""

    enviados: list[tuple[str, list[str] | None]] = []

    def __init__(self, *, contexto: str | None = None) -> None:
        pass

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
        db, admin, plataforma="shopee", bling_store_id="930001", sales_team=101, commercial_team=2
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
async def test_sweep_fifo_ordem_de_entrada_no_bling_nao_a_data(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """A antiguidade é a ordem em que o pedido entrou no Bling (bling_id) — é
    nela que a reserva foi feita. O 830001 foi vendido antes (data mais
    velha), mas entrou no Bling depois: ele é o mais novo e vai."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    agora = datetime.now(UTC)
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700009,
        item_produto_id=555, data=agora - timedelta(days=3),
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="b1", bling_id=700002,
        item_produto_id=555, data=agora - timedelta(days=1),
    )
    await db.commit()
    fake = _FakeBlingSituacao(
        saldos={555: -1.0}, orders={700009: {"id": 700009, "observacoes": ""}}
    )
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 1
    assert fake.chamadas == [(700009, 83955)]
    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert [n for c in cmds for n in c.numeros] == ["830002"]


@pytest.mark.parametrize("fantasma", ["antigo", "excluido"])
@pytest.mark.asyncio
async def test_sem_estoque_fifo_fantasma_nao_absorve_o_deficit(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch, fantasma: str
):
    """Pedido Em aberto no espelho há mais de 30 dias (fantasma: o ingest
    perdeu a saída dele) ou com linha 'excluido' não segura peça. Sem o
    filtro ele seria o "mais novo" e absorveria o déficit, deixando passar o
    pedido do lote sem peça."""
    from app.routers.nf import _sem_estoque_fifo

    agora = datetime.now(UTC)
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700001,
        item_produto_id=555,
    )
    await _seed_pedido(
        db, numero="830009", loja="930009", sku="b1", bling_id=700009,
        item_produto_id=555,
        data=agora - timedelta(days=45 if fantasma == "antigo" else 1),
    )
    if fantasma == "excluido":
        db.add(
            BlingOrder(
                bling_id=700009, numero="830009", loja="930009", situacao="excluido",
                item_index=1, item_codigo="c9", item_quantidade=1,
                data=agora - timedelta(days=1),
            )
        )
    await db.commit()
    fake = _FakeBlingSituacao(saldos={555: -1.0})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    res = await _sem_estoque_fifo(db, ["830001"])
    assert res.lote == {"830001": ["b1"]}
    assert res.fora_do_lote == {}


@pytest.mark.parametrize(("saldo_kit", "vao"), [(-2.0, {"830001", "830002"}), (3.0, set())])
@pytest.mark.asyncio
async def test_sem_estoque_fifo_kit_sem_composicao_desliga_o_fisico(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch,
    saldo_kit: float, vao: set[str],
):
    """Peça 501 com virtual −2 e físico 4: os 2 pedidos que o espelho conhece
    cabem no físico. Mas um kit SEM composição no cache (a sync é semanal)
    segura peça e está com saldo negativo — pode estar prendendo a 501 sem o
    espelho ver. Sem saber qual peça, nada de físico: D = −virtual = 2 e os
    dois vão (o lado seguro). Kit sem composição com saldo positivo não
    prende peça curta: o físico vale e ninguém vai."""
    from app.routers.nf import _pedidos_sem_estoque, _sem_estoque_fifo

    for numero, bling_id in (("830001", 700001), ("830002", 700002)):
        await _seed_pedido(
            db, numero=numero, loja="930001", sku="b026.18", bling_id=bling_id,
            item_produto_id=501,
        )
    await _seed_pedido(
        db, numero="830000", loja="930009", sku="kitnovo", bling_id=700000,
        item_produto_id=950, situacao="21",
    )
    db.add(
        Product(
            user_id=admin.id, sku="kitnovo", name="Kit novo", stock=0,
            formato="E", bling_product_id=950,
        )
    )
    await db.commit()
    fake = _FakeBlingSituacao(saldos={501: -2.0, 950: saldo_kit}, fisicos={501: 4.0})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    assert set(await _pedidos_sem_estoque(db, ["830001", "830002"])) == vao
    # o kit vai na MESMA leitura da peça
    assert fake.pedidos_saldos == [["501", "950"]]
    # quem chama avisa quando o físico foi desligado pelo kit
    res = await _sem_estoque_fifo(db, ["830001", "830002"])
    assert res.kits_sem_composicao == ([950] if vao else [])


@pytest.mark.asyncio
async def test_saldos_bling_em_lotes_de_50():
    """Até 50 ids por chamada; virtual e físico da mesma linha."""
    from app.routers.nf import _saldos_bling

    fake = _FakeBlingSituacao(saldos={i: float(i) for i in range(1, 121)}, fisicos={7: 3.0})
    out = await _saldos_bling(fake, set(range(1, 121)))
    assert [len(x) for x in fake.pedidos_saldos] == [50, 50, 20]
    assert out[7] == (7.0, 3.0)
    assert out[100] == (100.0, None)


@pytest.mark.asyncio
async def test_sweep_sem_estoque_bling_recusou_83955_fica_sem_marca(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """O Bling recusou a 83955: o pedido NÃO leva 'sem_estoque' (nem o motivo)
    no painel — fica sem marca e fora da fila, e a próxima passada tenta de
    novo (igual à trava do ML). Sem aviso de "movido"."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700001,
        item_produto_id=555,
    )
    await db.commit()

    class _Recusa(_FakeBlingSituacao):
        async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
            raise RuntimeError("400 situação inválida")

    recusa = _Recusa(saldos={555: -1.0}, orders={700001: {"id": 700001, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(recusa)
    )
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients", "7KMPCBS5", raising=False
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 0
    assert summary["sem_estoque_pendente"] == 1
    assert summary["enfileirados"] == 0
    assert _FakeThreema.enviados == []
    db.expire_all()
    assert (await db.execute(select(NfCommand))).scalars().all() == []
    assert (
        await db.execute(
            select(NfFaturamento.status_faturamento).where(
                NfFaturamento.pedido_bling == "830001"
            )
        )
    ).scalar_one_or_none() is None
    situacao = (
        await db.execute(select(BlingOrder.situacao).where(BlingOrder.numero == "830001"))
    ).scalar()
    assert situacao == "6"

    # próxima passada: o Bling aceita e aí sim a marca entra
    ok = _FakeBlingSituacao(saldos={555: -1.0}, orders={700001: {"id": 700001, "observacoes": ""}})
    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(ok))
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["candidatos"] == 1
    assert summary2["sem_estoque"] == 1
    assert ok.chamadas == [(700001, 83955)]
    db.expire_all()
    fat = (
        await db.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001"))
    ).scalar_one()
    assert fat.status_faturamento == "sem_estoque"
    assert len(_FakeThreema.enviados) == 1


@pytest.mark.asyncio
async def test_sweep_negativo_fora_do_lote_nao_move_e_avisa_uma_vez(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """O FIFO aponta como negativo um pedido ML (o sweep não confere ML): o
    Shopee mais antigo segue pra fila e o ML NÃO é movido — vira aviso no
    Threema, UMA vez por pedido/SKU/dia (a passada é a cada 2 min)."""
    await _seed_loja(db, admin, plataforma="shopee", bling_store_id="930001")
    await _seed_loja(db, admin, plataforma="ml", bling_store_id="930009", com_faturador=False)
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="b1", bling_id=700001, item_produto_id=555,
    )
    await _seed_pedido(
        db, numero="830009", loja="930009", sku="b1", bling_id=700009, item_produto_id=555,
    )
    await db.commit()
    fake = _FakeBlingSituacao(saldos={555: -1.0})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )
    monkeypatch.setattr(
        get_settings(), "nf_sem_estoque_threema_recipients", "7KMPCBS5", raising=False
    )
    _FakeThreema.enviados = []
    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)
    marcas = _FakeRedis()
    monkeypatch.setattr(nf_auto_enfileirar, "redis", marcas)

    summary = await run_auto_enfileirar_nf()
    assert summary["sem_estoque"] == 0
    assert summary["enfileirados"] == 1
    assert fake.chamadas == []
    [(texto, recipients)] = _FakeThreema.enviados
    assert recipients == ["7KMPCBS5"]
    assert "NÃO foi movido" in texto
    assert "Pedido 830009 (ml loja 930009): b1" in texto

    # 2ª passada com um Shopee novo da mesma peça (saldo −2): ele vai pra
    # 83955; o ML continua apontado, mas o aviso dele já saiu hoje.
    await _seed_pedido(
        db, numero="830010", loja="930001", sku="b1", bling_id=700010, item_produto_id=555,
    )
    await db.commit()
    fake.saldos[555] = -2.0
    fake.orders[700010] = {"id": 700010, "observacoes": ""}
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["sem_estoque"] == 1
    assert fake.chamadas == [(700010, 83955)]
    assert len(_FakeThreema.enviados) == 2
    texto2 = _FakeThreema.enviados[1][0]
    assert "movido pra Aguardando Cancelamento" in texto2
    assert "830009" not in texto2


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


_EXCECOES_RS = [
    {"tipo": "palavra", "termos": ["mala"]},
    {"tipo": "palavra", "termos": ["airfryer"]},
]


@pytest.mark.asyncio
async def test_trava_ml_barra_rs_com_automatico_do_ml_desligado(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Caso real (21/10): conta ML de Correios não envia pro RS, só passa
    mala/airfryer. O automático do ML está DESLIGADO (a NF sai pelo robô do
    Eduardo) e a loja nem tem faturador — mesmo assim o celular pro RS vai
    pra Aguardando Cancelamento (obs antes do PATCH), a mala pro RS e o
    celular pra SP ficam "Em aberto", e nada entra na fila."""
    monkeypatch.setattr(get_settings(), "nf_auto_ml_amazon", False, raising=False)
    monkeypatch.setattr(get_settings(), "nf_auto_ml", False, raising=False)
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930001",
        com_faturador=False, impressao="correios",
        excecoes=_EXCECOES_RS, uf_restrictions=["RS"],
    )
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="c1", bling_id=700001,
        uf_destino="RS", item_descricao="Smartphone Galaxy A15 128GB",
    )
    await _seed_pedido(
        db, numero="830002", loja="930001", sku="m1", bling_id=700002,
        uf_destino="RS", item_descricao="Mala de Bordo 10kg ABS",
    )
    await _seed_pedido(
        db, numero="830003", loja="930001", sku="c2", bling_id=700003,
        uf_destino="SP", item_descricao="Smartphone Galaxy A15 128GB",
    )
    await db.commit()
    fake = _FakeBlingSituacao(orders={700001: {"id": 700001, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["restricao_ml"] == 1
    assert summary["candidatos"] == 0  # automático do ML segue desligado

    assert fake.eventos == [("put", 700001), ("situacao", 700001)]
    assert fake.chamadas == [(700001, 83955)]
    [(_put_id, body)] = fake.puts
    assert "restrição" in (body.get("observacoes") or "")

    db.expire_all()
    assert (await db.execute(select(NfCommand))).scalars().all() == []
    situacoes = dict(
        (
            await db.execute(
                select(BlingOrder.numero, BlingOrder.situacao).where(
                    BlingOrder.numero.in_(["830001", "830002", "830003"])
                )
            )
        ).all()
    )
    assert situacoes == {"830001": "83955", "830002": "6", "830003": "6"}
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert fat.status_faturamento == "restricao"
    assert "não envia pro RS" in (fat.erro_faturamento or "")

    # tentativa única: a passada seguinte não mexe em mais nada
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["restricao_ml"] == 0
    assert len(fake.puts) == 1
    assert len(fake.chamadas) == 1


@pytest.mark.asyncio
async def test_trava_ml_bling_recusou_tenta_de_novo(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Se o Bling recusar a mudança de situação, o pedido NÃO fica marcado
    'restricao' (no ML ninguém mais segura o pedido) e a próxima passada
    tenta de novo."""
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930001",
        impressao="correios", excecoes=_EXCECOES_RS, uf_restrictions=["RS"],
    )
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="c1", bling_id=700001,
        uf_destino="RS", item_descricao="iPhone 13 128GB",
    )
    await db.commit()

    class _BlingRecusa(_FakeBlingSituacao):
        async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
            raise RuntimeError("Bling 429")

    recusa = _BlingRecusa(orders={700001: {"id": 700001, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(recusa)
    )
    summary = await run_auto_enfileirar_nf()
    assert summary["restricao_ml"] == 0
    db.expire_all()
    assert (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one_or_none() is None

    ok = _FakeBlingSituacao(orders={700001: {"id": 700001, "observacoes": ""}})
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(ok)
    )
    summary2 = await run_auto_enfileirar_nf()
    assert summary2["restricao_ml"] == 1
    assert ok.chamadas == [(700001, 83955)]


@pytest.mark.asyncio
async def test_trava_ml_loja_sem_excecao_nao_barra(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Loja ML com Restrição e SEM exceção fica inerte, como no resto do
    sweep: nada vai pro Bling."""
    await _seed_loja(
        db, admin, plataforma="ml", bling_store_id="930001",
        impressao="correios", uf_restrictions=["RS"],
    )
    await _seed_pedido(
        db, numero="830001", loja="930001", sku="c1", bling_id=700001,
        uf_destino="RS", item_descricao="Smartphone Galaxy A15",
    )
    await db.commit()
    fake = _FakeBlingSituacao()
    monkeypatch.setattr(
        nf_emissao_gerar, "_bling_client_opt", lambda _s: _async_return(fake)
    )

    summary = await run_auto_enfileirar_nf()
    assert summary["restricao_ml"] == 0
    assert fake.chamadas == []
    assert fake.puts == []
