"""Chamados × equipe de vendas (30/10/2026).

Eduardo, 30/10: três equipes — 101 Londres, 102 Israel, 103 Marrocos — e quem é
de uma equipe só enxerga as lojas dela, em todas as telas. Na aba Chamados o
chamado é da equipe do PEDIDO (`pedido_bling` numa loja do Bling da equipe; no
chamado em lote, qualquer pedido de `chamado_pedidos`). Sem pedido no espelho,
vale a conta da linha pela plataforma + nome (`team_scope.clausula_conta`). Com
pedido no espelho, o pedido manda (a conta digitada não muda a equipe).

Cobre: lista (linhas, total, dropdowns de plataforma/conta, filtros), leitura e
ações num chamado de outra equipe (403 `pedido_de_outra_equipe`, nada muda),
anexo e lixeira da mensagem, e-mails do chamado, abrir chamado (lookup + POST)
e trocar o pedido pelo PATCH, e a aba IA de Chamado ("esperando", decisões e
✓/✗). Admin e quem não tem equipe não mudam; equipe sem loja não vê nada.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoCerebro,
    ChamadoMensagem,
    ChamadoPedido,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)

pytestmark = pytest.mark.asyncio

PERM = {"chamados": {"view": True, "edit": True, "delete": True}}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
IA = "IA de Chamado"

LOJA_AGUIAR = "8821"  # ML aguiar — Londres (101)
LOJA_INOVA_ML = "8823"  # ML inova — Londres (101)
LOJA_INOVA_SHOPEE = "8822"  # Shopee inova — Israel (102): mesmo nome, outra equipe
LOJA_VORTAN = "8824"  # Shopee vortan — Israel (102)
LOJA_SEM_CADASTRO = "8829"  # loja do Bling sem store_info

TODOS = {"A", "B", "D", "E", "F", "G", "H", "I"}
DE_LONDRES = {"A", "D", "F"}
DE_ISRAEL = {"B", "E", "G", "I"}


async def _user(
    db: AsyncSession, nome: str, *, role: UserRole = UserRole.USER, equipes=None
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
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _pedido(numero: str, loja: str) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero),
        numero=numero,
        numeroloja=f"MK{numero}",
        loja=loja,
        situacao="83960",
        item_index=0,
        item_codigo=f"sku{numero}",
        item_descricao=f"produto {numero}",
        item_quantidade=1,
        data=datetime.now(UTC) - timedelta(days=2),
    )


def _chamado(
    pedido: str | None, plataforma: str | None, conta: str | None, origem: str = "margem"
) -> Chamado:
    return Chamado(
        data=datetime.now(UTC).date(),
        pedido_bling=pedido,
        pedido_marketplace=f"MK{pedido}" if pedido else "MKSEMPEDIDO",
        plataforma=plataforma,
        conta=conta,
        origem=origem,
        canal="manual",
    )


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict:
    """Chamados (letra → linha):
    A  pedido 876001 (loja ML aguiar)            → Londres
    B  pedido 876002 (loja Shopee inova)         → Israel
    D  lote: 876090 (fora do espelho) + 876003 (ML inova, Londres), conta
       "inova" da Shopee (Israel)                 → Londres: o pedido manda
    E  876091 fora do espelho, Shopee "vortan"   → Israel pela conta
    F  876092 fora do espelho, "Mercado Livre" "inova" → Londres pela conta
       (a Shopee inova de Israel não puxa)
    G  pedido 876002 (Israel) com conta ML aguiar digitada → Israel
    H  pedido 876009 de loja sem cadastro        → nenhuma equipe
    I  sem pedido, conta "Shopee Vortan"         → Israel (plataforma na frente)
    """
    admin = await _user(db, "admin", role=UserRole.ADMIN)
    lojas = [
        ("ml", "aguiar", LOJA_AGUIAR, 101),
        ("ml", "inova", LOJA_INOVA_ML, 101),
        ("shopee", "inova", LOJA_INOVA_SHOPEE, 102),
        ("shopee", "vortan", LOJA_VORTAN, 102),
    ]
    for plataforma, conta, loja, equipe in lojas:
        db.add(
            StoreInfo(
                user_id=admin.id,
                platform=plataforma,
                account_name=conta,
                bling_store_id=loja,
                sales_team=equipe,
            )
        )
    db.add_all(
        [
            _pedido("876001", LOJA_AGUIAR),
            _pedido("876002", LOJA_INOVA_SHOPEE),
            _pedido("876003", LOJA_INOVA_ML),
            _pedido("876009", LOJA_SEM_CADASTRO),
        ]
    )
    linhas = {
        "A": _chamado("876001", "ml", "aguiar"),
        "B": _chamado("876002", "shopee", "inova", "devolucao"),
        "D": _chamado("876090", "shopee", "inova", "logistica"),
        "E": _chamado("876091", "shopee", "vortan"),
        "F": _chamado("876092", "Mercado Livre", "inova"),
        "G": _chamado("876002", "ml", "aguiar", "vendas"),
        "H": _chamado("876009", "amazon", "zeta"),
        "I": _chamado(None, None, "Shopee Vortan"),
    }
    db.add_all(linhas.values())
    await db.flush()
    db.add_all(
        [
            ChamadoPedido(chamado_id=linhas["D"].id, pedido_bling="876090"),
            ChamadoPedido(chamado_id=linhas["D"].id, pedido_bling="876003"),
        ]
    )
    # Um histórico e uma foto em A (Londres) e em B (Israel).
    msgs, anexos = {}, {}
    for letra in ("A", "B"):
        m = ChamadoMensagem(
            chamado_id=linhas[letra].id,
            direcao="enviada",
            tipo="replica",
            texto=f"réplica {letra}",
            canal="manual",
            status="registrada",
        )
        db.add(m)
        await db.flush()
        a = ChamadoAnexo(
            chamado_id=linhas[letra].id,
            mensagem_id=m.id,
            filename=f"{letra}.png",
            content_type="image/png",
            size_bytes=len(PNG),
            blob=PNG,
        )
        db.add(a)
        await db.flush()
        msgs[letra], anexos[letra] = m.id, a.id
    await db.commit()
    return {
        "ids": {letra: ch.id for letra, ch in linhas.items()},
        "msgs": msgs,
        "anexos": anexos,
        "admin": admin,
        "londres": await _user(db, "londres", equipes=[101]),
        "israel": await _user(db, "israel", equipes=[102]),
        "sem_equipe": await _user(db, "sem-equipe"),
        # Equipe sem nenhuma loja cadastrada: não vê nada (nunca "tudo").
        "sem_loja": await _user(db, "sem-loja", equipes=[104]),
    }


def _letras(cenario: dict, items: list[dict]) -> set[str]:
    por_id = {str(v): k for k, v in cenario["ids"].items()}
    return {por_id.get(i["id"], i["id"]) for i in items}


async def _lista(client: AsyncClient, **params) -> dict:
    r = await client.get("/api/chamados", params={"mostrar": "todos", **params})
    assert r.status_code == 200, r.text
    return r.json()


def _de_outra_equipe(r) -> None:
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "pedido_de_outra_equipe"


# ─── Lista, total e dropdowns ────────────────────────────────────────────


async def test_lista_so_os_chamados_da_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["londres"])
    body = await _lista(client)
    assert _letras(cenario, body["items"]) == DE_LONDRES
    assert body["total"] == len(DE_LONDRES)
    assert body["plataformas"] == ["mercado livre", "shopee"]
    assert body["contas"] == ["aguiar", "inova"]

    auth_as(cenario["israel"])
    body = await _lista(client)
    assert _letras(cenario, body["items"]) == DE_ISRAEL
    assert body["total"] == len(DE_ISRAEL)
    assert sorted(c.lower() for c in body["contas"]) == [
        "aguiar",
        "inova",
        "shopee vortan",
        "vortan",
    ]
    assert "zeta" not in body["contas"]

    # Admin e quem não tem equipe: nada muda (inclusive a loja sem cadastro).
    for quem in ("admin", "sem_equipe"):
        auth_as(cenario[quem])
        body = await _lista(client)
        assert _letras(cenario, body["items"]) == TODOS, quem
        assert body["total"] == len(TODOS), quem
        assert body["plataformas"] == ["amazon", "mercado livre", "shopee"], quem
        assert "zeta" in body["contas"], quem

    # Equipe sem loja: lista, total e dropdowns vazios.
    auth_as(cenario["sem_loja"])
    body = await _lista(client)
    assert body["items"] == [] and body["total"] == 0
    assert body["plataformas"] == [] and body["contas"] == []


async def test_filtros_continuam_dentro_da_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    auth_as(cenario["londres"])
    assert _letras(cenario, (await _lista(client, conta="inova"))["items"]) == {"D", "F"}
    assert _letras(cenario, (await _lista(client, search="876001"))["items"]) == {"A"}
    # Buscar o pedido da outra equipe não acha nada (nem pelo nº da plataforma).
    for busca in ("876002", "MK876002", "vortan"):
        body = await _lista(client, search=busca)
        assert body["items"] == [] and body["total"] == 0, busca

    # Aba Jurídico (`juridico=true`): a mesma cerca.
    for letra in ("A", "B"):
        ch = await db.get(Chamado, cenario["ids"][letra])
        ch.juridico_enviado_at = datetime.now(UTC)
    await db.commit()
    body = await _lista(client, juridico="true")
    assert _letras(cenario, body["items"]) == {"A"} and body["total"] == 1
    auth_as(cenario["admin"])
    assert _letras(cenario, (await _lista(client, juridico="true"))["items"]) == {"A", "B"}


# ─── Um chamado de outra equipe ──────────────────────────────────────────


async def test_chamado_de_outra_equipe_da_403_e_nada_muda(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    ids, b = cenario["ids"], cenario["ids"]["B"]
    anexo_b, msg_b = cenario["anexos"]["B"], cenario["msgs"]["B"]
    resolver = {"resolvido": True, "valor_recuperado": "0", "situacao": "Resolvido"}
    png = {"file": ("x.png", PNG, "image/png")}
    tentativas = [
        ("GET", f"/api/chamados/{b}/mensagens", {}),
        ("PATCH", f"/api/chamados/{b}", {"json": {"observacao": "mexi"}}),
        ("POST", f"/api/chamados/{b}/mensagens", {"data": {"texto": "oi"}}),
        ("POST", f"/api/chamados/{b}/anexos-auto", {"files": png}),
        ("POST", f"/api/chamados/{b}/instrucao", {"json": {"texto": "responde"}}),
        ("POST", f"/api/chamados/{b}/resolver", {"json": resolver}),
        ("POST", f"/api/chamados/{b}/alterar-status-bling", {"json": {"situacao": "Resolvido"}}),
        ("GET", f"/api/chamados/{b}/exclusao", {}),
        ("POST", f"/api/chamados/{b}/excluir", {"json": {"situacao": "Resolvido"}}),
        ("POST", f"/api/chamados/{b}/reler", {}),
        ("POST", f"/api/chamados/{b}/juridico", {"json": {"observacao": "x"}}),
        ("GET", f"/api/chamados/{b}/emails", {}),
        ("GET", f"/api/chamados/anexos/{anexo_b}", {}),
        ("DELETE", f"/api/chamados/anexos/{anexo_b}", {}),
        ("DELETE", f"/api/chamados/mensagens/{msg_b}", {}),
        ("DELETE", f"/api/chamados/{b}", {}),
        # H: loja do Bling sem cadastro não é de equipe nenhuma.
        ("GET", f"/api/chamados/{ids['H']}/mensagens", {}),
    ]
    auth_as(cenario["londres"])
    for metodo, url, kwargs in tentativas:
        r = await client.request(metodo, url, **kwargs)
        assert r.status_code == 403, (metodo, url, r.text)
        assert r.json()["detail"]["code"] == "pedido_de_outra_equipe", (metodo, url)
    # D: lote com pedido de Londres e conta da Shopee inova (Israel) — o pedido manda.
    auth_as(cenario["israel"])
    _de_outra_equipe(await client.get(f"/api/chamados/{ids['D']}/mensagens"))

    # Nada mudou em B: o chamado, a foto, a mensagem e o histórico ficam.
    # `populate_existing`: lê do banco sem expirar os usuários do cenário.
    ch = await db.get(Chamado, b, populate_existing=True)
    assert ch is not None and ch.observacao is None and ch.resolvido is False
    assert ch.juridico_enviado_at is None
    assert await db.get(ChamadoAnexo, cenario["anexos"]["B"], populate_existing=True)
    m = await db.get(ChamadoMensagem, cenario["msgs"]["B"], populate_existing=True)
    assert m is not None and m.excluida_at is None
    n_msgs = await db.scalar(
        select(func.count()).select_from(ChamadoMensagem).where(ChamadoMensagem.chamado_id == b)
    )
    assert n_msgs == 1
    n_anexos = await db.scalar(
        select(func.count()).select_from(ChamadoAnexo).where(ChamadoAnexo.chamado_id == b)
    )
    assert n_anexos == 1

    # Chamado que não existe continua 404 (não vira 403).
    auth_as(cenario["londres"])
    r = await client.get(f"/api/chamados/{uuid.uuid4()}/mensagens")
    assert r.status_code == 404, r.text


async def test_chamado_da_equipe_continua_funcionando(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    ids, a = cenario["ids"], cenario["ids"]["A"]
    auth_as(cenario["londres"])
    r = await client.get(f"/api/chamados/{a}/mensagens")
    assert r.status_code == 200 and [m["texto"] for m in r.json()] == ["réplica A"]
    r = await client.patch(f"/api/chamados/{a}", json={"observacao": "ok"})
    assert r.status_code == 200 and r.json()["observacao"] == "ok", r.text
    assert (await client.get(f"/api/chamados/anexos/{cenario['anexos']['A']}")).content == PNG
    r = await client.get(f"/api/chamados/{a}/emails")
    assert r.status_code == 200 and r.json()["total"] == 0, r.text
    # Lote (D) pelo pedido de Londres; F pela conta ML inova.
    for letra in ("D", "F"):
        r = await client.get(f"/api/chamados/{ids[letra]}/mensagens")
        assert r.status_code == 200, (letra, r.text)
    # Israel pela conta sem pedido (I, "Shopee Vortan").
    auth_as(cenario["israel"])
    assert (await client.get(f"/api/chamados/{ids['I']}/mensagens")).status_code == 200
    # Admin e sem equipe abrem qualquer um, inclusive o da loja sem cadastro.
    for quem in ("admin", "sem_equipe"):
        auth_as(cenario[quem])
        for letra in ("B", "H"):
            r = await client.get(f"/api/chamados/{ids[letra]}/mensagens")
            assert r.status_code == 200, (quem, letra, r.text)


# ─── Abrir chamado e trocar o pedido ─────────────────────────────────────


async def _chamados_do_pedido(db: AsyncSession, pedido: str) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(Chamado).where(Chamado.pedido_bling == pedido)
        )
        or 0
    )


async def test_abrir_chamado_so_de_pedido_da_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    auth_as(cenario["londres"])
    # O lookup (1º passo da janela) não mostra o pedido da outra equipe.
    for pedido in ("876002", "MK876002", "876009"):
        r = await client.get("/api/chamados/pedido-lookup", params={"pedido": pedido})
        _de_outra_equipe(r)
    r = await client.get("/api/chamados/pedido-lookup", params={"pedido": "876001"})
    assert r.status_code == 200 and r.json()["conta"] == "aguiar", r.text
    r = await client.get("/api/chamados/pedido-lookup", params={"pedido": "999999"})
    assert r.status_code == 404, r.text

    antes = await _chamados_do_pedido(db, "876002")
    for corpo in (
        {"origem": "vendas", "pedido_bling": "876002"},
        # Só o nº da plataforma: o espelho preenche o pedido Bling (de Israel).
        {"origem": "vendas", "pedido_marketplace": "MK876002"},
        # Fora do espelho: vale a conta — a de Israel não serve...
        {"origem": "vendas", "pedido_bling": "876093", "plataforma": "shopee", "conta": "vortan"},
        # ...e sem conta nenhuma não há como dizer que é da equipe.
        {"origem": "vendas", "pedido_bling": "876095"},
    ):
        _de_outra_equipe(await client.post("/api/chamados", json=corpo))
    assert await _chamados_do_pedido(db, "876002") == antes
    assert await _chamados_do_pedido(db, "876093") == 0
    assert await _chamados_do_pedido(db, "876095") == 0

    novo = await client.post("/api/chamados", json={"origem": "vendas", "pedido_bling": "876001"})
    assert novo.status_code == 201, novo.text
    manual = await client.post(
        "/api/chamados",
        json={"origem": "vendas", "pedido_bling": "876094", "plataforma": "ml", "conta": "aguiar"},
    )
    assert manual.status_code == 201, manual.text
    ids = {i["id"] for i in (await _lista(client))["items"]}
    assert {novo.json()["id"], manual.json()["id"]} <= ids

    # Equipe sem loja não abre nada; admin e sem equipe abrem de qualquer loja.
    auth_as(cenario["sem_loja"])
    _de_outra_equipe(
        await client.post("/api/chamados", json={"origem": "vendas", "pedido_bling": "876001"})
    )
    for quem in ("admin", "sem_equipe"):
        auth_as(cenario[quem])
        r = await client.get("/api/chamados/pedido-lookup", params={"pedido": "876002"})
        assert r.status_code == 200, (quem, r.text)
        r = await client.post("/api/chamados", json={"origem": "vendas", "pedido_bling": "876002"})
        assert r.status_code == 201, (quem, r.text)


async def test_trocar_o_pedido_nao_leva_o_chamado_pra_outra_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    a = cenario["ids"]["A"]
    auth_as(cenario["londres"])
    r = await client.patch(f"/api/chamados/{a}", json={"pedido_bling": "876002"})
    _de_outra_equipe(r)
    r = await client.patch(
        f"/api/chamados/{a}",
        json={"pedido_bling": "876096", "plataforma": "shopee", "conta": "vortan"},
    )
    _de_outra_equipe(r)
    ch = await db.get(Chamado, a, populate_existing=True)
    assert (ch.pedido_bling, ch.plataforma, ch.conta) == ("876001", "ml", "aguiar")
    # Dentro da equipe, troca normal.
    r = await client.patch(f"/api/chamados/{a}", json={"conta": "Aguiar"})
    assert r.status_code == 200 and r.json()["conta"] == "Aguiar", r.text


# ─── IA de Chamado ───────────────────────────────────────────────────────


async def test_ia_de_chamado_conta_e_avalia_so_a_equipe(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    db.add(
        ChamadoCerebro(
            nome=IA, token_hash=hashlib.sha256(b"cerebro-equipes").hexdigest(), ligada=True
        )
    )
    agora = datetime.now(UTC)
    analises = {}
    for letra in ("A", "B"):
        cid = cenario["ids"][letra]
        an = ChamadoMensagem(
            chamado_id=cid,
            direcao="sistema",
            tipo="analise",
            texto=f"Análise {letra}",
            canal="manual",
            status="registrada",
            autor_nome=IA,
            created_at=agora - timedelta(hours=2),
        )
        db.add(an)
        # Instrução depois da análise: o caso espera a IA ("esperando").
        db.add(
            ChamadoMensagem(
                chamado_id=cid,
                direcao="sistema",
                tipo="instrucao",
                texto=f"refaz {letra}",
                canal="manual",
                status="registrada",
                created_at=agora - timedelta(hours=1),
            )
        )
        await db.flush()
        analises[letra] = an.id
    await db.commit()

    auth_as(cenario["admin"])
    est = (await client.get("/api/chamados/ia")).json()
    assert est["esperando"] == 2
    assert {d["chamado_id"] for d in est["decisoes"]} == {
        str(cenario["ids"]["A"]),
        str(cenario["ids"]["B"]),
    }

    auth_as(cenario["londres"])
    est = (await client.get("/api/chamados/ia")).json()
    assert est["esperando"] == 1
    assert [d["chamado_id"] for d in est["decisoes"]] == [str(cenario["ids"]["A"])]
    url_b = f"/api/chamados/ia/decisoes/{analises['B']}/avaliacao"
    _de_outra_equipe(await client.put(url_b, json={"certo": False, "correcao": "faz outra"}))
    _de_outra_equipe(await client.delete(url_b))
    url_a = f"/api/chamados/ia/decisoes/{analises['A']}/avaliacao"
    r = await client.put(url_a, json={"certo": True})
    assert r.status_code == 200 and r.json()["avaliacao"]["certo"] is True, r.text
    # O ✗ recusado em B não deixou instrução nova lá.
    n = await db.scalar(
        select(func.count())
        .select_from(ChamadoMensagem)
        .where(
            ChamadoMensagem.chamado_id == cenario["ids"]["B"],
            ChamadoMensagem.tipo == "instrucao",
        )
    )
    assert n == 1

    auth_as(cenario["sem_loja"])
    est = (await client.get("/api/chamados/ia")).json()
    assert est["esperando"] == 0 and est["decisoes"] == []


# ─── Revisão de 30/10: nº da plataforma no campo do Bling; caso de outra equipe ─


async def test_numero_da_plataforma_de_pedido_de_outra_equipe_no_campo_do_bling(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    """O nº da PLATAFORMA (numeroloja) de um pedido da Israel digitado no
    campo do pedido Bling, com uma conta da Londres. A cerca da lista só
    compara o nº do Bling: o chamado nascia na Londres com os dados do pedido
    da Israel (produto, SKU, situação) copiados do espelho. Agora o pedido que
    o espelho ACHA tem de ser da equipe — ao abrir e ao trocar pelo PATCH."""
    auth_as(cenario["londres"])
    antes = await db.scalar(select(func.count()).select_from(Chamado))
    r = await client.post(
        "/api/chamados",
        json={
            "origem": "vendas",
            "pedido_bling": "MK876002",
            "plataforma": "ml",
            "conta": "aguiar",
        },
    )
    _de_outra_equipe(r)
    assert r.json()["detail"]["pedidos"] == ["MK876002"]
    assert "produto 876002" not in r.text and "sku876002" not in r.text
    assert await db.scalar(select(func.count()).select_from(Chamado)) == antes

    a = cenario["ids"]["A"]
    for corpo in (
        {"pedido_bling": "MK876002"},
        {"pedido_bling": None, "pedido_marketplace": "MK876002"},
    ):
        _de_outra_equipe(await client.patch(f"/api/chamados/{a}", json=corpo))
    ch = await db.get(Chamado, a, populate_existing=True)
    assert (ch.pedido_bling, ch.pedido_marketplace, ch.conta) == ("876001", "MK876001", "aguiar")
    # Chamado da Londres sem nº do Bling: trocar só o nº da plataforma também barra.
    sem_bling = _chamado(None, "ml", "aguiar")
    db.add(sem_bling)
    await db.commit()
    _de_outra_equipe(
        await client.patch(f"/api/chamados/{sem_bling.id}", json={"pedido_marketplace": "MK876002"})
    )
    ch = await db.get(Chamado, sem_bling.id, populate_existing=True)
    assert (ch.pedido_bling, ch.pedido_marketplace) == (None, "MKSEMPEDIDO")

    # O nº da plataforma de pedido da PRÓPRIA equipe continua abrindo (e preenchendo).
    r = await client.post(
        "/api/chamados",
        json={
            "origem": "vendas",
            "pedido_bling": "MK876001",
            "plataforma": "ml",
            "conta": "aguiar",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["produto"] == "produto 876001"
    assert await _chamados_do_pedido(db, "MK876001") == 1
    assert await _chamados_do_pedido(db, "MK876002") == 0


async def test_protocolo_do_caso_de_outra_equipe_nao_junta_o_historico(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict, db: AsyncSession
):
    """O histórico é do CASO: as linhas com o mesmo protocolo numérico na
    mesma conta. A Londres punha no A dela o protocolo e a conta do B
    (Israel) e o histórico do A passava a trazer as mensagens do B. Agora o
    PATCH (ou o chamado novo) que juntaria linha de outra equipe é recusado e
    nada muda; dentro da equipe, juntar o caso continua valendo."""
    ids, a = cenario["ids"], cenario["ids"]["A"]
    b = await db.get(Chamado, ids["B"])
    b.chamado = "5123456789"
    # F (Londres pela conta ML inova) com outro protocolo e uma réplica.
    f = await db.get(Chamado, ids["F"])
    f.chamado = "5123456780"
    db.add(
        ChamadoMensagem(
            chamado_id=f.id,
            direcao="enviada",
            tipo="replica",
            texto="réplica F",
            canal="manual",
            status="registrada",
        )
    )
    await db.commit()

    auth_as(cenario["londres"])
    for corpo in (
        {"conta": "inova", "chamado": "5123456789"},
        # Outra grafia da mesma conta e protocolo cai no mesmo caso.
        {"conta": " INOVA ", "chamado": " 5123456789 "},
    ):
        r = await client.patch(f"/api/chamados/{a}", json=corpo)
        _de_outra_equipe(r)
        assert "réplica B" not in r.text
    ch = await db.get(Chamado, a, populate_existing=True)
    assert (ch.conta, ch.chamado) == ("aguiar", None)
    r = await client.get(f"/api/chamados/{a}/mensagens")
    assert r.status_code == 200 and [m["texto"] for m in r.json()] == ["réplica A"], r.text
    # Só o protocolo, num chamado que já está na conta "inova" (o F): idem.
    _de_outra_equipe(await client.patch(f"/api/chamados/{f.id}", json={"chamado": "5123456789"}))
    ch = await db.get(Chamado, f.id, populate_existing=True)
    assert ch.chamado == "5123456780"

    # Chamado novo já no caso da outra equipe: o mesmo 403, nada nasce.
    r = await client.post(
        "/api/chamados",
        json={
            "origem": "vendas",
            "pedido_bling": "876001",
            "conta": "inova",
            "chamado": "5123456789",
        },
    )
    _de_outra_equipe(r)
    n = await db.scalar(
        select(func.count()).select_from(Chamado).where(Chamado.chamado == "5123456789")
    )
    assert n == 1

    # Dentro da equipe: o A entra no caso do F e o histórico junta os dois.
    r = await client.patch(f"/api/chamados/{a}", json={"conta": "inova", "chamado": "5123456780"})
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/chamados/{a}/mensagens")
    assert r.status_code == 200, r.text
    assert sorted(m["texto"] for m in r.json()) == ["réplica A", "réplica F"]

    # E a Israel segue só com o B no histórico dela.
    auth_as(cenario["israel"])
    r = await client.get(f"/api/chamados/{ids['B']}/mensagens")
    assert r.status_code == 200 and [m["texto"] for m in r.json()] == ["réplica B"], r.text
