"""Botões INFORMAR — montagem das mensagens (puro) + gate de admin do endpoint."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, User, UserRole, UserStatus
from app.services import informar, logistica_rules


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def usuario(db: AsyncSession) -> User:
    email = f"usr-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.USER, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---- linhas_logistica ----


def test_linhas_logistica_formato_e_ordem_por_plataforma():
    ml = Logistica(
        plataforma="Mercado Livre",
        conta="loja a",
        pedido_marketplace="2000014548125021",
        pedido_bling="290662",
        meli_status={"order_status": "cancelled"},
        status_bling="Problemas",
    )
    shopee = Logistica(
        plataforma="Shopee",
        conta="vortan",
        pedido_marketplace="SPX123",
        pedido_bling="290001",
        meli_status={},
        status_bling="Aguardando Cancelamento",
    )
    linhas = informar.linhas_logistica([shopee, ml])
    assinatura_ml = logistica_rules.assinatura_para(
        "Mercado Livre", {"order_status": "cancelled"}
    )
    # Ordena por plataforma (ML antes de Shopee) e segue o formato pedido:
    # pedido marketplace - conta - status plataforma - status bling.
    assert linhas == [
        f"2000014548125021 - loja a - {assinatura_ml} - Problemas",
        "SPX123 - vortan - - - Aguardando Cancelamento",
    ]


def test_linhas_logistica_cai_no_pedido_bling_e_tracos():
    r = Logistica(
        plataforma="Amazon",
        conta=None,
        pedido_marketplace=None,
        pedido_bling="123",
        meli_status={},
        status_bling=None,
    )
    assert informar.linhas_logistica([r]) == ["123 - - - - - -"]


# ---- linhas_estoque ----


def test_linhas_estoque_espelha_aviso_do_sweep():
    entries = [
        ("290002", "shopee vortan equipe 2", "b035.28"),
        ("290001", "", ""),
    ]
    assert informar.linhas_estoque(entries) == [
        "Pedido 290001",
        "Pedido 290002 (shopee vortan equipe 2): b035.28",
    ]


# ---- mensagens da margem (uma por pedido) ----


def test_mensagem_margem_pedido_completa():
    p = informar.MargemPedido(
        pedido="402001",
        loja="ml Loja ML",
        motivo="margem abaixo do mínimo",
        margem=-3.2,
        minima=8,
        lucro=-1234.5,
    )
    msg = informar.mensagem_margem_pedido(p, cabecalho="Cab", rodape="Rod")
    assert msg.splitlines() == [
        "Cab",
        "Pedido 402001 — ml Loja ML",
        "Motivo: margem abaixo do mínimo",
        "Margem: -3,2% (mínimo 8%)",
        "Lucro: R$ -1.234,50",
        "Rod",
    ]


def test_mensagem_margem_pedido_sem_numeros_nem_loja():
    """Pedido segurado sem gatilho de margem (números NULL) e sem loja: só
    cabeçalho, pedido e motivo."""
    p = informar.MargemPedido(pedido="1", loja="", motivo="pendente de análise")
    assert informar.mensagem_margem_pedido(p, cabecalho="X").splitlines() == [
        "X",
        "Pedido 1",
        "Motivo: pendente de análise",
    ]


def test_mensagens_margem_numera_e_ordena_por_pedido():
    a = informar.MargemPedido(pedido="2", loja="l2", motivo="m")
    b = informar.MargemPedido(pedido="1", loja="l1", motivo="m")
    msgs = informar.mensagens_margem([a, b], "Cab")
    assert len(msgs) == 2
    assert msgs[0].startswith("Cab (1/2)\nPedido 1")
    assert msgs[1].startswith("Cab (2/2)\nPedido 2")
    # Um pedido só → cabeçalho sem numeração.
    assert informar.mensagens_margem([a], "Cab")[0].startswith("Cab\nPedido 2")
    assert informar.mensagens_margem([], "Cab") == []


# ---- montar_mensagens ----


def test_montar_mensagens_uma_parte_sem_rotulo():
    assert informar.montar_mensagens("Cabeçalho (2)", ["a", "b"]) == ["Cabeçalho (2)\na\nb"]


def test_montar_mensagens_sem_linhas_devolve_vazio():
    assert informar.montar_mensagens("X", []) == []


def test_montar_mensagens_fatia_por_bytes_e_preserva_linhas():
    linhas = ["x" * 100 for _ in range(10)]
    msgs = informar.montar_mensagens("Head", linhas, max_bytes=350)
    # 3 linhas por bloco (3×101 bytes ≤ 350; a 4ª estoura) → 3+3+3+1.
    assert len(msgs) == 4
    assert msgs[0].startswith("Head — parte 1/4\n")
    assert msgs[3].startswith("Head — parte 4/4\n")
    corpo: list[str] = []
    for m in msgs:
        corpo.extend(m.split("\n")[1:])
    assert corpo == linhas


# ---- gate de admin do endpoint ----


@pytest.mark.asyncio
async def test_informar_config_exige_admin(
    client: AsyncClient, auth_as, admin: User, usuario: User
):
    auth_as(usuario)
    r = await client.get("/api/informar/logistica")
    assert r.status_code == 403

    auth_as(admin)
    r = await client.get("/api/informar/logistica")
    assert r.status_code == 200
    body = r.json()
    assert body["contexto"] == "logistica"
    assert body["recipients"] == []

    # contexto desconhecido → 404 (não existe rota solta pra outros nomes)
    r = await client.get("/api/informar/outra-coisa")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_informar_put_salva_so_ids_do_diretorio(
    client: AsyncClient, auth_as, admin: User, monkeypatch: pytest.MonkeyPatch
):
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "threema_recipient_names", "AAAA1111:Ana,BBBB2222:Bia",
        raising=False,
    )
    monkeypatch.setattr(get_settings(), "threema_recipients", "", raising=False)
    auth_as(admin)
    r = await client.put(
        "/api/informar/controle_estoque",
        json={"recipients": ["aaaa1111", "ZZZZ9999"]},
    )
    assert r.status_code == 200
    # Normaliza pra maiúsculas e descarta quem não está no diretório.
    assert r.json()["recipients"] == ["AAAA1111"]


@pytest.mark.asyncio
async def test_informar_margem_libera_gerente_por_email(
    client: AsyncClient, db: AsyncSession, auth_as
):
    """O gerente (e-mail em _EMAILS_EXTRAS['margem']) usa o Informar da
    Margem sem ser admin; Logística e Controle de Estoque seguem admin-only
    até pra ele."""
    email = "sa.geral@tutamail.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    auth_as(u)

    assert (await client.get("/api/informar/margem")).status_code == 200
    assert (await client.get("/api/informar/margem_auto")).status_code == 200
    assert (await client.get("/api/informar/logistica")).status_code == 403
    assert (await client.get("/api/informar/controle_estoque")).status_code == 403
    # margem_auto não tem envio manual — quem envia é o robô do auto-hold.
    assert (await client.post("/api/informar/margem_auto/enviar")).status_code == 404


# ---- contexto margem (relatório dos pendentes) ----


async def _seed_margem(db: AsyncSession, **cols: object) -> None:
    """Uma linha-item no snapshot verificar_margem; ausentes ficam NULL."""
    from sqlalchemy import text

    cols.setdefault("bling_order_item_id", str(uuid.uuid4()))
    names = ", ".join(cols)
    binds = ", ".join(f":{c}" for c in cols)
    await db.execute(
        text(f"INSERT INTO verificar_margem ({names}) VALUES ({binds})"),  # noqa: S608
        cols,
    )
    await db.commit()


@pytest.mark.asyncio
async def test_informar_margem_enviar_lista_pendentes_com_motivo(
    client: AsyncClient,
    db: AsyncSession,
    auth_as,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
):
    """O relatório da Margem manda EXATAMENTE o que a aba Pendentes mostra,
    UMA MENSAGEM POR PEDIDO (dedup de itens) com conta, motivo, margem vs
    mínima e lucro somado; aprovado fica de fora e 'Pendente' gravado sem
    gatilho vira "pendente de análise" sem linhas de número."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.services import threema

    await db.execute(text("DELETE FROM verificar_margem"))
    await db.commit()
    # Pendente por margem baixa (2 itens do MESMO pedido → uma mensagem só,
    # lucro somado: -10 + -5 = -15).
    for sku, lucro in (("sku-a", -10), ("sku-b", -5)):
        await _seed_margem(
            db,
            pedido_bling="402001",
            sku=sku,
            plataforma_bling="ml",
            loja_nome="Loja ML",
            situacao="6",
            situacao_nome="Em aberto",
            marketplace_margem=2,
            margem_minima=8,
            marketplace_lucro=lucro,
        )
    # Sem gatilho nenhum → Aprovado derivado → fora do relatório.
    await _seed_margem(
        db,
        pedido_bling="402002",
        sku="sku-a",
        plataforma_bling="shopee",
        loja_nome="Loja SP",
        situacao="6",
        situacao_nome="Em aberto",
        marketplace_margem=15,
        margem_minima=8,
    )
    # 'Pendente' GRAVADO sem gatilho ativo (hold manual) → entra, motivo padrão.
    await _seed_margem(
        db,
        pedido_bling="402003",
        sku="sku-a",
        situacao="6",
        situacao_nome="Em aberto",
        bling_status_margem="Pendente",
    )

    enviados: list[str] = []

    class _FakeThreema:
        def __init__(self, *a: object, **k: object) -> None: ...

        async def send_to_all(self, msg: str, recipients: list[str]) -> dict:
            enviados.append(msg)
            return {"sent": list(recipients), "failed": []}

    monkeypatch.setattr(threema, "ThreemaClient", _FakeThreema)
    monkeypatch.setattr(
        get_settings(), "threema_recipient_names", "AAAA1111:Ana", raising=False
    )
    monkeypatch.setattr(get_settings(), "threema_recipients", "", raising=False)

    auth_as(admin)
    r = await client.put("/api/informar/margem", json={"recipients": ["AAAA1111"]})
    assert r.status_code == 200
    r = await client.post("/api/informar/margem/enviar")

    assert r.status_code == 200
    body = r.json()
    assert body["pedidos"] == 2
    assert body["mensagens"] == 2
    assert body["sent"] == ["AAAA1111"]
    assert len(enviados) == 2
    assert enviados[0].splitlines() == [
        "DaVinci — Margem: pendente de análise (1/2)",
        "Pedido 402001 — ml Loja ML",
        "Motivo: margem abaixo do mínimo",
        "Margem: 2% (mínimo 8%)",
        "Lucro: R$ -15,00",
    ]
    assert enviados[1].splitlines() == [
        "DaVinci — Margem: pendente de análise (2/2)",
        "Pedido 402003",
        "Motivo: pendente de análise",
    ]
