"""Botões INFORMAR — montagem das mensagens (puro) + gate de admin do endpoint."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Logistica, User, UserRole, UserStatus
from app.services import aprovar_link, informar, logistica_rules


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


# ---- linhas_devolucoes (botão Informar de Devoluções) ----


def test_linhas_devolucoes_formato_e_ordem_preservada():
    """Uma linha por pedido aguardando devolução, NA ORDEM RECEBIDA (a aba
    manda o mais parado primeiro); sem localização → "sem localização";
    singular/plural de "dia"; loja vazia fica de fora."""
    entries = [
        ("310022", "", None, None),
        ("310021", "shopee Loja SP", 3, "Recebido no CD"),
        ("310020", "ml Loja ML", 1, ""),
    ]
    assert informar.linhas_devolucoes(entries) == [
        "Pedido 310022 — sem localização",
        "Pedido 310021 (shopee Loja SP) — 3 dias — Recebido no CD",
        "Pedido 310020 (ml Loja ML) — 1 dia — sem localização",
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


def test_mensagem_margem_pedido_com_produto():
    """Eduardo (03/09): "no informar coloque o nome do produto tem que ser bem
    completinho" — a linha "Produto:" entra logo após o título do pedido."""
    p = informar.MargemPedido(
        pedido="402001",
        loja="ml Loja ML",
        motivo="margem abaixo do mínimo",
        margem=-3.2,
        minima=8,
        lucro=-10,
        produto="Uranyx Fossibot F105 - Preto; Fone UFB10",
    )
    assert informar.mensagem_margem_pedido(p, cabecalho="Cab").splitlines() == [
        "Cab",
        "Pedido 402001 — ml Loja ML",
        "Produto: Uranyx Fossibot F105 - Preto; Fone UFB10",
        "Motivo: margem abaixo do mínimo",
        "Margem: -3,2% (mínimo 8%)",
        "Lucro: R$ -10,00",
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
async def test_informar_diretorio_vem_do_cadastro_de_usuarios(
    client: AsyncClient,
    db: AsyncSession,
    auth_as,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
):
    """Eduardo (02/09): "eu vou alimentar os codigos threemas tem que aparecer
    no para informar das abas que tem" — o campo Threema da tela Usuários vira
    opção no modal. O `.env` continua valendo só pra ID que ninguém tem no
    cadastro; desativados e usuários-sistema ficam de fora."""
    from datetime import UTC, datetime

    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "threema_recipient_names", "LEGA0001:Legado", raising=False
    )
    monkeypatch.setattr(get_settings(), "threema_recipients", "", raising=False)
    db.add_all(
        [
            User(
                open_id="email:zeca@davinci-test.com",
                email="zeca@davinci-test.com",
                name="Zeca",
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
                threema="zzzz9999",  # minúsculo de propósito → normaliza
            ),
            User(
                open_id="email:des@davinci-test.com",
                email="des@davinci-test.com",
                name="Desativado",
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
                threema="XXXX0000",
                disabled_at=datetime.now(UTC),
            ),
            User(
                open_id="system:robo-teste",
                email="robo-teste@hadken.com",
                name="Robô",
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
                threema="RRRR0000",
            ),
        ]
    )
    await db.commit()
    auth_as(admin)

    r = await client.get("/api/informar/margem")
    assert r.status_code == 200
    dest = {d["id"]: d["nome"] for d in r.json()["destinatarios"]}
    assert dest.get("ZZZZ9999") == "Zeca"  # veio do cadastro, normalizado
    assert dest.get("LEGA0001") == "Legado"  # .env sem dono no cadastro fica
    assert "XXXX0000" not in dest  # desativado fora
    assert "RRRR0000" not in dest  # usuário-sistema fora

    # E dá pra salvar o ID cadastrado (vale pro PUT também).
    r = await client.put("/api/informar/margem", json={"recipients": ["ZZZZ9999"]})
    assert r.status_code == 200
    assert r.json()["recipients"] == ["ZZZZ9999"]


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
            # Fração, como em produção: 0.02 = 2% (a query multiplica por 100).
            marketplace_margem=0.02,
            margem_minima=0.08,
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
        marketplace_margem=0.15,
        margem_minima=0.08,
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
    linhas0 = enviados[0].splitlines()
    assert linhas0[:5] == [
        "DaVinci — Margem: pendente de análise (1/2)",
        "Pedido 402001 — ml Loja ML",
        "Motivo: margem abaixo do mínimo",
        "Margem: 2% (mínimo 8%)",
        "Lucro: R$ -15,00",
    ]
    # Cada mensagem fecha com o MESMO link do aviso automático, com token
    # válido apontando pro pedido da mensagem.
    prefixo = "Aprovar pelo celular: http://localhost:3000/api/aprovar/"
    assert linhas0[5].startswith(prefixo)
    assert aprovar_link.validar_token(linhas0[5].removeprefix(prefixo)) == "402001"
    assert len(linhas0) == 6
    linhas1 = enviados[1].splitlines()
    assert linhas1[:3] == [
        "DaVinci — Margem: pendente de análise (2/2)",
        "Pedido 402003",
        "Motivo: pendente de análise",
    ]
    assert linhas1[3].startswith(prefixo)
    assert aprovar_link.validar_token(linhas1[3].removeprefix(prefixo)) == "402003"
    assert len(linhas1) == 4
