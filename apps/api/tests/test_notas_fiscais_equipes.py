"""Notas fiscais (Pós Vendas) × equipe de vendas (30/10/2026).

Eduardo, 30/10: três equipes — 101 Londres, 102 Israel, 103 Marrocos — e quem
é de uma equipe só enxerga as lojas dela. Na tela Notas fiscais (Pós Vendas):

- a lista traz só os pedidos das lojas da equipe (`BlingOrder.loja`), com as
  MESMAS notas que o admin vê na linha: o casamento roda com todos os pedidos
  e a cerca só escolhe as linhas (senão a equipe ficaria, pelo CPF, com a
  nota do pedido de outra);
- o XML de uma nota só sai se ela for de pedido da equipe (403
  `pedido_de_outra_equipe`): pela chave exata (o pedido que o coletor gravou
  ou o complemento == numeroloja) ou, sem chave exata, pelo CPF de um pedido
  da equipe na janela do casamento;
- a consulta direta às contas de emissão (lista ao vivo do Bling e exports)
  fica só para quem não tem equipe (403 `contas_de_todas_as_equipes`);
- equipe sem loja cadastrada não vê nada. Admin e quem não tem equipe: como
  sempre.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingNota,
    BlingNotaEmitida,
    BlingOrder,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.models.nf import NfNota
from app.routers import notas_fiscais as nf_router

pytestmark = pytest.mark.asyncio

PERM = {"notas_fiscais": {"view": True, "edit": False, "delete": False}}
_DIA = date(2026, 6, 2)
_ATE = _DIA + timedelta(days=1)
LOJA_LONDRES = "8831"
LOJA_ISRAEL = "8832"
LOJA_SEM_CADASTRO = "8839"

CPF_A = "11122233344"  # cliente só da Londres (o pedido grava com pontuação)
CPF_B = "55566677788"  # comprou na Israel E na Londres
CPF_C = "22233344455"  # cliente da Londres; a nota casa só pelo CPF
CPF_D = "33344455566"  # comprou nas duas; a nota vai para o envio mais recente
CPF_E = "99988877766"  # cliente da loja sem cadastro

TODOS = {"874001", "874002", "874003", "874004", "874005", "874011", "874012"}
DA_LONDRES = {"874001", "874004", "874005", "874011"}
DA_ISRAEL = {"874002", "874012"}


async def _user(
    db: AsyncSession,
    nome: str,
    *,
    role: UserRole = UserRole.USER,
    equipes: list[int] | None = None,
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


def _pedido(numero: str, loja: str, doc: str, total: float, *, envio: date = _DIA) -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero),
        numero=numero,
        numeroloja=f"MK{numero}",
        loja=loja,
        item_codigo=f"x{numero[-3:]}.ci",
        item_index=0,
        item_descricao=f"Produto {numero}",
        total=total,
        documento_destinatario=doc,
        situacao="9",
        em_andamento_data=envio,
        data=datetime(envio.year, envio.month, envio.day, 9, tzinfo=UTC),
    )


def _emitida(
    conta: BlingNota,
    numero: str,
    *,
    complemento: str | None,
    cpf: str,
    valor: float,
    emissao: datetime,
) -> BlingNotaEmitida:
    """Nota do espelho do Bling (`bling_notas_emitidas`, horário local naive)."""
    return BlingNotaEmitida(
        conta_id=conta.id,
        bling_id=int(numero),
        numero=numero,
        situacao=5,
        data_emissao=emissao,
        cpf_dest=cpf,
        complemento=complemento,
        valor=valor,
        detalhe_ok=True,
    )


def _coletada(numero: str, *, pedido: str, cpf: str, valor: float, emissao: datetime) -> NfNota:
    """Nota que o coletor trouxe em XML, já casada com o pedido."""
    return NfNota(
        chave=f"{int(numero):044d}",
        pedido_bling=pedido,
        numero=numero,
        emitente_cnpj="44444444000144",
        emitente_nome="Faturador",
        destinatario_doc=cpf,
        valor=valor,
        data_emissao=emissao,
        situacao="100",
        xml=f"<nfe>{numero}</nfe>".encode(),
    )


def _conta(apelido: str, cnpj: str) -> BlingNota:
    """Conta de emissão com token válido (sem refresh: o Bling é de mentira)."""
    return BlingNota(
        nome=f"nf-eq-{apelido}",
        client_id=f"cid-{apelido}",
        basic_auth_b64="eA==",
        access_token=f"tok-{apelido}",
        cnpj=cnpj,
        emitente=apelido.upper(),
    )


async def _limpar_contas(db: AsyncSession) -> None:
    # `bling_notas` não está na limpeza geral do conftest; as emitidas caem
    # junto (FK CASCADE).
    await db.execute(text("DELETE FROM bling_notas WHERE nome LIKE 'nf-eq-%'"))
    await db.commit()


@pytest_asyncio.fixture
async def cenario(db: AsyncSession) -> dict:
    """Loja 8831 = Londres (101), 8832 = Israel (102), 8839 = loja sem
    cadastro. Envios em 02/06 (o 874012 em 03/06):

    874001 Londres A — NF embalagem pelo complemento, NF produto do coletor
    874002 Israel  B — idem
    874003 sem cadastro E — NF embalagem pelo complemento
    874004 Londres C — NF produto só pelo CPF
    874005 Londres B — sem nota (as do B são do 874002, pela chave exata)
    874011 Londres D — sem nota: a do D vai para o 874012, envio mais recente
    874012 Israel  D — NF produto pelo CPF
    """
    await _limpar_contas(db)
    admin = await _user(db, "admin", role=UserRole.ADMIN)
    db.add_all(
        [
            StoreInfo(
                user_id=admin.id,
                platform="ml",
                account_name="loja-londres",
                bling_store_id=LOJA_LONDRES,
                sales_team=101,
                cnpj="11111111000111",
            ),
            StoreInfo(
                user_id=admin.id,
                platform="shopee",
                account_name="loja-israel",
                bling_store_id=LOJA_ISRAEL,
                sales_team=102,
                cnpj="22222222000122",
            ),
            _pedido("874001", LOJA_LONDRES, "111.222.333-44", 200),
            _pedido("874002", LOJA_ISRAEL, CPF_B, 300),
            _pedido("874003", LOJA_SEM_CADASTRO, CPF_E, 100),
            _pedido("874004", LOJA_LONDRES, CPF_C, 150),
            _pedido("874005", LOJA_LONDRES, CPF_B, 120),
            _pedido("874011", LOJA_LONDRES, CPF_D, 250),
            _pedido("874012", LOJA_ISRAEL, CPF_D, 250, envio=_ATE),
        ]
    )
    # Embalagem: a conta da empresa dona da loja. Produto: a avulsa, que
    # fatura para todas as equipes.
    emb_londres = _conta("londres", "11111111000111")
    emb_israel = _conta("israel", "22222222000122")
    avulsa = _conta("avulsa", "33333333000133")
    db.add_all([emb_londres, emb_israel, avulsa])
    await db.flush()

    vespera = datetime(2026, 6, 1, 10, 0)
    notas = {
        "lon_emb": _emitida(
            emb_londres, "5001", complemento="MK874001", cpf=CPF_A, valor=2, emissao=vespera
        ),
        "isr_emb": _emitida(
            emb_israel, "5002", complemento="MK874002", cpf=CPF_B, valor=3, emissao=vespera
        ),
        "e_emb": _emitida(
            emb_londres, "5003", complemento="MK874003", cpf=CPF_E, valor=1, emissao=vespera
        ),
        "c_prod": _emitida(avulsa, "5004", complemento=None, cpf=CPF_C, valor=150, emissao=vespera),
        "d_prod": _emitida(avulsa, "5005", complemento=None, cpf=CPF_D, valor=250, emissao=vespera),
        # Mesmo cliente do 874004, mas emitida 5 dias depois do envio: fora
        # da janela do CPF (e fora da carga da lista).
        "c_fora": _emitida(
            avulsa,
            "5006",
            complemento=None,
            cpf=CPF_C,
            valor=150,
            emissao=datetime(2026, 6, 7, 10, 0),
        ),
        "lon_prod": _coletada(
            "6001",
            pedido="874001",
            cpf=CPF_A,
            valor=200,
            emissao=datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
        ),
        "isr_prod": _coletada(
            "6002",
            pedido="874002",
            cpf=CPF_B,
            valor=300,
            emissao=datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
        ),
    }
    db.add_all(notas.values())
    await db.commit()

    yield {
        "admin": admin,
        "londres": await _user(db, "londres", equipes=[101]),
        "israel": await _user(db, "israel", equipes=[102]),
        # Sem equipe (como o pessoal do galpão): vê tudo, como sempre.
        "sem_equipe": await _user(db, "sem-equipe"),
        # Equipe sem loja cadastrada: não vê nada (nunca "tudo").
        "sem_loja": await _user(db, "sem-loja", equipes=[104]),
        "notas": {k: str(v.id) for k, v in notas.items()},
        "contas": [str(c.id) for c in (emb_londres, emb_israel, avulsa)],
    }
    await _limpar_contas(db)


@pytest.fixture
def bling_falso(monkeypatch: pytest.MonkeyPatch) -> list:
    """O Bling de mentira: o XML de uma nota e a lista ao vivo de uma conta.
    Guarda cada chamada — recusa não pode chegar ao Bling."""
    chamadas: list = []

    async def _fetch(token: str, nota: dict):
        chamadas.append(("xml", nota["id"]))
        return {"chaveAcesso": f"chave{nota['id']}"}, f"<nfe>{nota['id']}</nfe>".encode(), None

    async def _listar(token: str, date_from: date, date_to: date) -> list[dict]:
        chamadas.append(("lista", token))
        return [
            {
                "id": 9000 + len(chamadas),
                "numero": "1",
                "dataEmissao": "2026-06-01 10:00:00",
                "tipo": 1,
                "situacao": 5,
                "contato": {"nome": "Cliente"},
            }
        ]

    monkeypatch.setattr(nf_router, "_fetch_nota_xml", _fetch)
    monkeypatch.setattr(nf_router, "_list_notas_bling", _listar)
    return chamadas


async def _pos_vendas(client: AsyncClient) -> dict[str, dict]:
    r = await client.get(f"/api/notas-fiscais/pos-vendas?date_from={_DIA}&date_to={_ATE}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["items"])
    return {i["pedido_bling"]: i for i in body["items"]}


def _notas_da_linha(linha: dict) -> tuple[str | None, str | None]:
    emb, prod = linha["nf_embalagem"], linha["nf_produto"]
    return (emb and emb["nota_id"], prod and prod["nota_id"])


# ─── A lista (Pós Vendas) ────────────────────────────────────────────────


async def test_pos_vendas_equipe_so_ve_os_pedidos_dela(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["admin"])
    do_admin = await _pos_vendas(client)

    auth_as(cenario["londres"])
    londres = await _pos_vendas(client)
    assert set(londres) == DA_LONDRES
    assert {linha["loja"] for linha in londres.values()} == {"loja-londres"}

    auth_as(cenario["israel"])
    israel = await _pos_vendas(client)
    assert set(israel) == DA_ISRAEL

    # A linha da equipe é a MESMA do admin (mesmas notas, mesma via).
    for linhas in (londres, israel):
        for pedido, linha in linhas.items():
            assert linha == do_admin[pedido], pedido

    n = cenario["notas"]
    assert _notas_da_linha(londres["874001"]) == (n["lon_emb"], n["lon_prod"])
    assert londres["874001"]["nf_embalagem"]["via"] == "pedido"
    assert londres["874001"]["nf_produto"]["via"] == "xml"
    assert _notas_da_linha(londres["874004"]) == (None, n["c_prod"])
    assert londres["874004"]["nf_produto"]["via"] == "cpf"
    assert _notas_da_linha(israel["874002"]) == (n["isr_emb"], n["isr_prod"])


async def test_pos_vendas_casamento_roda_com_todos_os_pedidos(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    """O cliente D comprou nas duas equipes e só há UMA nota cheia dele: o
    casamento (com todos os pedidos) a dá ao envio mais recente, o 874012 da
    Israel. Casar só os pedidos da Londres daria a nota da Israel ao 874011."""
    n = cenario["notas"]
    auth_as(cenario["londres"])
    londres = await _pos_vendas(client)
    assert _notas_da_linha(londres["874011"]) == (None, None)
    # O B também comprou nas duas: as notas dele são do 874002 (chave exata).
    assert _notas_da_linha(londres["874005"]) == (None, None)

    auth_as(cenario["israel"])
    israel = await _pos_vendas(client)
    assert _notas_da_linha(israel["874012"]) == (None, n["d_prod"])
    assert israel["874012"]["nf_produto"]["via"] == "cpf"


async def test_pos_vendas_admin_e_sem_equipe_nao_mudam(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    n = cenario["notas"]
    esperado = {
        "874001": (n["lon_emb"], n["lon_prod"]),
        "874002": (n["isr_emb"], n["isr_prod"]),
        "874003": (n["e_emb"], None),
        "874004": (None, n["c_prod"]),
        "874005": (None, None),
        "874011": (None, None),
        "874012": (None, n["d_prod"]),
    }
    for quem in ("admin", "sem_equipe"):
        auth_as(cenario[quem])
        linhas = await _pos_vendas(client)
        assert set(linhas) == TODOS, quem
        assert {p: _notas_da_linha(linha) for p, linha in linhas.items()} == esperado, quem
        assert linhas["874003"]["loja"] is None, quem


async def test_pos_vendas_equipe_sem_loja_nao_ve_nada(
    client: AsyncClient, auth_as: Callable[[User | None], None], cenario: dict
):
    auth_as(cenario["sem_loja"])
    r = await client.get(f"/api/notas-fiscais/pos-vendas?date_from={_DIA}&date_to={_ATE}")
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "total": 0}


async def test_montar_pos_vendas_sem_cerca_e_o_do_vigia(db: AsyncSession, cenario: dict):
    """O Vigia de Nota Cheia chama sem a cerca: todas as lojas, como antes.
    Cerca vazia = nada; a cerca escolhe as linhas, o casamento é o mesmo."""
    todas = await nf_router.montar_pos_vendas(db, _DIA, _ATE)
    assert {i.pedido_bling for i in todas} == TODOS
    assert await nf_router.montar_pos_vendas(db, _DIA, _ATE, lojas_equipe=set()) == []
    israel = await nf_router.montar_pos_vendas(db, _DIA, _ATE, lojas_equipe={LOJA_ISRAEL})
    assert {i.pedido_bling for i in israel} == DA_ISRAEL
    por_pedido = {i.pedido_bling: i for i in todas}
    for linha in israel:
        assert linha == por_pedido[linha.pedido_bling]


# ─── O XML de uma nota ───────────────────────────────────────────────────


async def _xml(client: AsyncClient, nota_id: str):
    return await client.get(f"/api/notas-fiscais/pos-vendas/nota/{nota_id}/xml")


async def test_xml_da_nota_da_equipe_sai(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict,
    bling_falso: list,
):
    n = cenario["notas"]
    auth_as(cenario["londres"])
    # Pelo complemento == numeroloja (espelho do Bling: o XML vem do Bling).
    r = await _xml(client, n["lon_emb"])
    assert r.status_code == 200, r.text
    assert r.content == b"<nfe>5001</nfe>"
    # Pelo pedido que o coletor gravou (o XML já está guardado aqui).
    r = await _xml(client, n["lon_prod"])
    assert r.status_code == 200, r.text
    assert r.content == b"<nfe>6001</nfe>"
    # Sem chave exata: o CPF de um pedido da equipe na janela do casamento.
    r = await _xml(client, n["c_prod"])
    assert r.status_code == 200, r.text
    assert r.content == b"<nfe>5004</nfe>"

    auth_as(cenario["israel"])
    for chave in ("isr_emb", "isr_prod", "d_prod"):
        r = await _xml(client, n[chave])
        assert r.status_code == 200, (chave, r.text)


async def test_xml_de_nota_de_outra_equipe_403(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict,
    bling_falso: list,
):
    n = cenario["notas"]
    auth_as(cenario["londres"])
    for chave in (
        # Chave exata no pedido da Israel — o CPF B também tem pedido da
        # Londres (874005), mas a chave exata vence.
        "isr_emb",
        "isr_prod",
        # Chave exata em pedido de loja sem cadastro: não dá pra provar.
        "e_emb",
        # Só o CPF, e fora da janela do pedido da equipe.
        "c_fora",
    ):
        r = await _xml(client, n[chave])
        assert r.status_code == 403, (chave, r.text)
        assert r.json()["detail"]["code"] == "pedido_de_outra_equipe", chave

    auth_as(cenario["israel"])
    for chave in ("lon_emb", "lon_prod", "c_prod"):
        r = await _xml(client, n[chave])
        assert r.status_code == 403, (chave, r.text)
        assert r.json()["detail"]["code"] == "pedido_de_outra_equipe", chave

    # Recusa antes de pedir o XML ao Bling.
    assert bling_falso == []


async def test_xml_equipe_sem_loja_403(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict,
    bling_falso: list,
):
    auth_as(cenario["sem_loja"])
    for chave, nota_id in cenario["notas"].items():
        r = await _xml(client, nota_id)
        assert r.status_code == 403, (chave, r.text)
        assert r.json()["detail"]["code"] == "pedido_de_outra_equipe", chave
    assert bling_falso == []


async def test_xml_admin_e_sem_equipe_nao_mudam(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict,
    bling_falso: list,
):
    for quem in ("admin", "sem_equipe"):
        auth_as(cenario[quem])
        for chave, nota_id in cenario["notas"].items():
            r = await _xml(client, nota_id)
            assert r.status_code == 200, (quem, chave, r.text)
        r = await _xml(client, str(uuid.uuid4()))
        assert r.status_code == 404, quem
        assert r.json()["detail"]["code"] == "nota_nao_encontrada", quem

    # Inexistente segue 404 também para quem tem equipe.
    auth_as(cenario["londres"])
    r = await _xml(client, str(uuid.uuid4()))
    assert r.status_code == 404, r.text


# ─── Consulta direta às contas de emissão ────────────────────────────────


async def test_consulta_das_contas_so_sem_equipe(
    client: AsyncClient,
    auth_as: Callable[[User | None], None],
    cenario: dict,
    bling_falso: list,
):
    periodo = f"date_from={_DIA}&date_to={_ATE}"
    job_id = uuid.uuid4()
    for quem in ("londres", "sem_loja"):
        auth_as(cenario[quem])
        for metodo, url, corpo in (
            ("GET", "/api/notas-fiscais/contas", None),
            ("GET", f"/api/notas-fiscais?{periodo}", None),
            ("GET", f"/api/notas-fiscais/export.xml?{periodo}", None),
            ("GET", f"/api/notas-fiscais/export.xlsx?{periodo}", None),
            (
                "POST",
                "/api/notas-fiscais/export-job",
                {"fmt": "xlsx", "date_from": str(_DIA), "date_to": str(_ATE)},
            ),
            ("GET", f"/api/notas-fiscais/export-job/{job_id}/download", None),
        ):
            r = await client.request(metodo, url, json=corpo)
            assert r.status_code == 403, (quem, url, r.text)
            assert r.json()["detail"]["code"] == "contas_de_todas_as_equipes", (quem, url)
    assert bling_falso == []

    contas = "".join(f"&conta={c}" for c in cenario["contas"])
    for quem in ("admin", "sem_equipe"):
        auth_as(cenario[quem])
        r = await client.get("/api/notas-fiscais/contas")
        assert r.status_code == 200, (quem, r.text)
        nomes = [c["nome"] for c in r.json()["items"] if c["nome"].startswith("nf-eq-")]
        assert nomes == ["nf-eq-avulsa", "nf-eq-israel", "nf-eq-londres"], quem
        bling_falso.clear()
        r = await client.get(f"/api/notas-fiscais?{periodo}{contas}")
        assert r.status_code == 200, (quem, r.text)
        assert r.json()["total"] == 3, quem
        assert {c for c in bling_falso if c[0] == "lista"} == {
            ("lista", "tok-avulsa"),
            ("lista", "tok-israel"),
            ("lista", "tok-londres"),
        }, quem
        r = await client.get(f"/api/notas-fiscais/export-job/{job_id}/download")
        assert r.status_code == 404, (quem, r.text)
        assert r.json()["detail"]["code"] == "job_not_found", quem
