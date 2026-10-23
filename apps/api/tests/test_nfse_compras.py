"""Cadastros › Contabilidade › Compra (23/10/2026).

A empresa do grupo compra embalagem de uma conta Bling de NF, com base no
faturamento do mês; a compra vira a planilha de pedidos do Bling da vendedora.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Company, StoreInfo, User, UserRole
from app.models.compra import CompraPedido, CompraVendedora
from app.models.nf import NfNota
from app.models.nfse import CompanyFiscal
from app.services import compra as svc
from app.services.nf_relatorio import COLUNAS

BASE = "/api/nfse/compras"
CNPJ_VITA = "52133663000188"
CNPJ_RIO = "11444777000161"
CNPJ_SEM = "11222333000181"
CNPJ_BEZERRA = "67285941000190"
CNPJ_TELES = "67041460000139"
CNPJ_VELHA = "66829674000101"

_XML = (
    '<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe><emit><CNPJ>{cnpj}</CNPJ>'
    "<xNome>{nome}</xNome><enderEmit><xLgr>{rua}</xLgr><nro>{nro}</nro>"
    "<xBairro>{bairro}</xBairro><cMun>{cmun}</cMun><xMun>X</xMun><UF>{uf}</UF>"
    "<CEP>{cep}</CEP></enderEmit><IE>{ie}</IE></emit></infNFe></NFe>"
)


async def _nota(
    db: AsyncSession,
    cnpj: str,
    nome: str,
    quando: datetime,
    *,
    rua: str = "Avenida Interlagos",
    nro: str = "54",
    bairro: str = "Jardim Umuarama",
    cmun: str = "3550308",
    uf: str = "SP",
    cep: str = "04661000",
    ie: str = "160311077114",
) -> None:
    db.add(
        NfNota(
            chave=f"35{uuid.uuid4().int % 10**42:042d}",
            numero=str(uuid.uuid4().int % 10000),
            emitente_cnpj=cnpj,
            emitente_nome=nome,
            data_emissao=quando,
            valor=Decimal("10.00"),
            xml=_XML.format(
                cnpj=cnpj,
                nome=nome,
                rua=rua,
                nro=nro,
                bairro=bairro,
                cmun=cmun,
                uf=uf,
                cep=cep,
                ie=ie,
            ).encode(),
        )
    )
    await db.commit()


def _agora() -> datetime:
    return datetime.now(UTC)


@pytest_asyncio.fixture
async def operador(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:compra-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"compra-{uuid.uuid4().hex[:6]}@davinci-test.com",
        name="Operador Compra",
        role=UserRole.USER,
        permissions={"emissao_servico": {"view": True, "edit": True, "delete": True}},
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def cenario(db: AsyncSession, operador: User) -> dict:
    """VITA (endereço pela NFE.io, faturamento em setembro), RIO (endereço pela
    própria nota dela, IE no XML) e SEM (nada cadastrado). Vendedoras: BEZERRA e
    TELES (SP), emitentes recentes de fora do grupo; a VELHA saiu da janela."""
    vita = Company(
        razao_social="VITA ENXOVAIS LTDA",
        apelido="vita",
        cnpj=CNPJ_VITA,
        uf="SP",
        inscricao_estadual="125632321110",
    )
    rio = Company(razao_social="RIO COMERCIO LTDA", apelido="rio", cnpj=CNPJ_RIO, uf="RJ")
    sem = Company(razao_social="SEM CADASTRO LTDA", apelido="sem", cnpj=CNPJ_SEM, uf="MG")
    db.add_all([vita, rio, sem])
    await db.flush()
    db.add(
        CompanyFiscal(
            company_id=vita.id,
            nfeio_resumo={
                "endereco": {
                    "postalCode": "02404-060",
                    "street": "Rua Antonio Pereira de Sousa",
                    "number": 240,
                    "additionalInformation": "",
                    "district": "Santana",
                    "state": "SP",
                    "city": {"code": "3550308", "name": "São Paulo"},
                }
            },
        )
    )
    db.add_all(
        [
            StoreInfo(
                user_id=operador.id,
                platform="shopee",
                account_name="vita",
                cnpj=CNPJ_VITA,
                bling_store_id="111",
            ),
            StoreInfo(
                user_id=operador.id,
                platform="ml",
                account_name="vita",
                cnpj=CNPJ_VITA,
                bling_store_id="222",
            ),
        ]
    )

    def ped(bid: int, loja: str, total: str, sit: str, quando: str, item: int = 0) -> BlingOrder:
        return BlingOrder(
            bling_id=bid,
            loja=loja,
            total=Decimal(total),
            situacao=sit,
            data=datetime.fromisoformat(quando),
            item_index=item,
        )

    db.add_all(
        [
            ped(1, "111", "10000.00", "6", "2026-09-05T10:00:00-03:00", 0),
            ped(1, "111", "10000.00", "6", "2026-09-05T10:00:00-03:00", 1),  # 2º item: não soma
            ped(2, "222", "5050.50", "83953", "2026-09-30T23:30:00-03:00"),
            ped(3, "111", "999.00", "12", "2026-09-10T10:00:00-03:00"),  # cancelado: fora
            ped(4, "111", "70.00", "15", "2026-10-01T00:10:00-03:00"),  # outubro
        ]
    )
    await db.commit()
    agora = _agora()
    db.add_all(
        [
            CompraVendedora(
                cnpj=CNPJ_BEZERRA,
                nome="COMERCIAL BEZERRA",
                uf="SP",
                adspower_serial="k13ksbpr",
                automatico=True,
            ),
            CompraVendedora(cnpj=CNPJ_TELES, nome="COMERCIAL TELES", uf="SP"),
            CompraVendedora(cnpj=CNPJ_VELHA, nome="COMERCIAL VELHA", uf="SP", ativo=False),
        ]
    )
    await db.commit()
    await _nota(db, CNPJ_BEZERRA, "COMERCIAL BEZERRA", agora - timedelta(days=2))
    await _nota(db, CNPJ_TELES, "COMERCIAL TELES", agora - timedelta(days=20), cep="01310100")
    await _nota(db, CNPJ_VELHA, "COMERCIAL VELHA", agora - timedelta(days=120))
    # A RIO é do grupo: a nota dela dá endereço e IE, e ela NÃO vira vendedora.
    await _nota(
        db,
        CNPJ_RIO,
        "RIO COMERCIO LTDA",
        agora - timedelta(days=3),
        rua="Rua do Ouvidor",
        nro="10",
        bairro="Centro",
        cmun="3304557",
        uf="RJ",
        cep="20040030",
        ie="86123456",
    )
    return {"vita": vita.id, "rio": rio.id, "sem": sem.id}


def _compra(cen: dict, **kw) -> dict:
    corpo = {
        "company_id": str(cen["vita"]),
        "vendedor_cnpj": CNPJ_BEZERRA,
        "competencia": "2026-09-01",
        "data_pedido": "2026-10-23",
        "modo": "quantidade",
        "itens": [
            {
                "nome": "Embalagem",
                "sku": "a001",
                "ncm": "3923.21.10",
                "unidade": "UN",
                "quantidade": 450,
                "valor_unitario": "9.30",
            }
        ],
    }
    corpo.update(kw)
    return corpo


def _linhas_csv(conteudo: bytes) -> list[list[str]]:
    assert conteudo.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(conteudo[3:].decode("utf-8")), delimiter=";"))


# --- permissão e trava -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sem_permissao_nao_ve_nem_registra(client: AsyncClient, make_user, auth_as, cenario):
    auth_as(await make_user(permissions={}))
    assert (await client.get(f"{BASE}/opcoes")).status_code == 403
    auth_as(await make_user(permissions={"emissao_servico": {"view": True}}))
    assert (await client.get(f"{BASE}/opcoes")).status_code == 200
    r = await client.post(BASE, json=_compra(cenario))
    assert r.status_code == 403
    r = await client.post(f"{BASE}/produtos", json={"nome": "X", "sku": "x", "ncm": "39232110"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rotas_exigem_a_senha_extra(client: AsyncClient, operador, auth_as):
    """O router inteiro nasce trancado (mesma chave da Emissão de Serviço)."""
    from app.main import app
    from app.security.senha_extra import require_nfse_unlock

    auth_as(operador)
    desvio = app.dependency_overrides.pop(require_nfse_unlock, None)
    try:
        r = await client.get(f"{BASE}/opcoes")
        assert r.status_code == 401 and r.json()["detail"]["code"] == "nfse_locked"
    finally:
        if desvio is not None:
            app.dependency_overrides[require_nfse_unlock] = desvio


# --- produtos -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_produtos_cadastro_validacoes_e_exclusao(
    client: AsyncClient, operador, auth_as, cenario
):
    auth_as(operador)
    r = await client.post(
        f"{BASE}/produtos",
        json={"nome": " Embalagem ", "sku": "a001", "ncm": "3923.21.10", "preco_padrao": "9,30"},
    )
    assert r.status_code == 201, r.text
    emb = r.json()
    assert emb["nome"] == "Embalagem" and emb["ncm"] == "39232110" and emb["cfop"] is None
    assert (
        emb["unidade"] == "UN" and Decimal(emb["preco_padrao"]) == Decimal("9.30") and emb["ativo"]
    )

    r = await client.post(
        f"{BASE}/produtos", json={"nome": "EMBALAGEM", "sku": "b", "ncm": "39232110"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "produto_repetido"
    r = await client.post(f"{BASE}/produtos", json={"nome": "Caixa", "sku": "cx", "ncm": "4819"})
    assert r.status_code == 422
    r = await client.post(
        f"{BASE}/produtos", json={"nome": "Caixa", "sku": "cx", "ncm": "48191000", "cfop": "51"}
    )
    assert r.status_code == 422
    r = await client.post(
        f"{BASE}/produtos",
        json={"nome": "Caixa", "sku": "cx", "ncm": "48191000", "cfop": "5.102", "unidade": "cx"},
    )
    assert r.status_code == 201 and r.json()["cfop"] == "5102" and r.json()["unidade"] == "CX"
    caixa = r.json()

    r = await client.put(f"{BASE}/produtos/{caixa['id']}", json={**caixa, "nome": "Embalagem"})
    assert r.status_code == 409
    r = await client.put(
        f"{BASE}/produtos/{caixa['id']}", json={**caixa, "ativo": False, "preco_padrao": None}
    )
    assert r.status_code == 200 and r.json()["ativo"] is False

    # /opcoes só traz os ativos; /produtos traz todos
    assert [p["nome"] for p in (await client.get(f"{BASE}/opcoes")).json()["produtos"]] == [
        "Embalagem"
    ]
    assert len((await client.get(f"{BASE}/produtos")).json()) == 2

    # produto usado numa compra não se exclui (desativa)
    corpo = _compra(cenario)
    corpo["itens"][0]["produto_id"] = emb["id"]
    assert (await client.post(BASE, json=corpo)).status_code == 201
    r = await client.delete(f"{BASE}/produtos/{emb['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "produto_em_uso"
    assert (await client.delete(f"{BASE}/produtos/{caixa['id']}")).status_code == 204
    listados = (await client.get(f"{BASE}/produtos")).json()
    assert [(p["nome"], p["usado"]) for p in listados] == [("Embalagem", 1)]


# --- quem vende / quem compra / faturamento -------------------------------------------------------


@pytest.mark.asyncio
async def test_vendedoras_sao_as_do_cadastro(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    """Só as vendedoras cadastradas e ativas (as 4 contas Bling de NF): a VELHA está
    desativada e uma conta de fora com nota recente (ex.: 67.COMERCIAL OLIVEIRA, a
    compartilhada) não aparece."""
    auth_as(operador)
    await _nota(db, "67197753000100", "67.COMERCIAL OLIVEIRA", _agora() - timedelta(days=1))
    vend = (await client.get(f"{BASE}/opcoes")).json()["vendedoras"]
    assert [v["cnpj"] for v in vend] == [CNPJ_BEZERRA, CNPJ_TELES]  # automática primeiro
    assert vend[0]["nome"] == "COMERCIAL BEZERRA" and vend[0]["uf"] == "SP"
    assert vend[0]["municipio"] == "São Paulo" and vend[0]["notas_90d"] == 1
    assert vend[0]["automatico"] is True and vend[1]["automatico"] is False


@pytest.mark.asyncio
async def test_comprador_endereco_nfeio_nota_ou_nada(
    client: AsyncClient, operador, auth_as, cenario
):
    auth_as(operador)
    vita = (await client.get(f"{BASE}/comprador/{cenario['vita']}")).json()
    assert vita["endereco_origem"] == "nfeio" and vita["cnpj"] == CNPJ_VITA
    assert vita["inscricao_estadual"] == "125632321110"
    assert vita["endereco"] == {
        "logradouro": "Rua Antonio Pereira de Sousa",
        "numero": "240",
        "complemento": None,
        "bairro": "Santana",
        "cep": "02404060",
        "municipio": "São Paulo",
        "uf": "SP",
    }

    rio = (await client.get(f"{BASE}/comprador/{cenario['rio']}")).json()
    assert rio["endereco_origem"] == "nota" and rio["inscricao_estadual"] == "86123456"
    assert (
        rio["endereco"]["logradouro"] == "Rua do Ouvidor"
        and rio["endereco"]["municipio"] == "Rio de Janeiro"
    )
    assert rio["endereco"]["uf"] == "RJ" and rio["endereco"]["cep"] == "20040030"

    sem = (await client.get(f"{BASE}/comprador/{cenario['sem']}")).json()
    assert sem["endereco_origem"] is None and sem["inscricao_estadual"] is None
    assert sem["endereco"]["uf"] == "MG" and sem["endereco"]["logradouro"] is None

    assert (await client.get(f"{BASE}/comprador/{uuid.uuid4()}")).status_code == 404


@pytest.mark.asyncio
async def test_comprador_sem_cnpj_e_endereco_torto(
    client: AsyncClient, db: AsyncSession, operador, auth_as
):
    auth_as(operador)
    sem_cnpj = Company(razao_social="SEM CNPJ", apelido="semcnpj", cnpj=None)
    torta = Company(razao_social="TORTA LTDA", apelido="torta", cnpj="22333444000155")
    db.add_all([sem_cnpj, torta])
    await db.flush()
    db.add(
        CompanyFiscal(
            company_id=torta.id,
            nfeio_resumo={
                "endereco": {
                    "street": "Rua A",
                    "number": "1",
                    "district": "B",
                    "postalCode": "123",
                    "state": "São Paulo",
                    "city": {"name": "Cidade"},
                }
            },
        )
    )
    await db.commit()
    r = await client.get(f"{BASE}/comprador/{sem_cnpj.id}")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_cnpj"
    r = await client.get(f"{BASE}/comprador/{torta.id}")
    assert r.status_code == 200
    end = r.json()["endereco"]
    assert (
        end["logradouro"] == "Rua A" and end["cep"] is None and end["uf"] is None
    )  # torto fica vazio


@pytest.mark.asyncio
async def test_faturamento_do_mes(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    f = (
        await client.get(
            f"{BASE}/faturamento",
            params={"company_id": str(cenario["vita"]), "competencia": "2026-09-15"},
        )
    ).json()
    assert f["tem_loja"] and Decimal(f["valor"]) == Decimal("15050.50") and f["pedidos"] == 2
    assert f["competencia"] == "2026-09-01" and len(f["lojas"]) == 2
    f = (
        await client.get(
            f"{BASE}/faturamento",
            params={"company_id": str(cenario["sem"]), "competencia": "2026-09-01"},
        )
    ).json()
    assert f["tem_loja"] is False and Decimal(f["valor"]) == 0


def test_contas_de_quantidade_e_cfop():
    assert svc.quantidade_pelo_percentual(Decimal("15050.50"), Decimal("1"), Decimal("9.30")) == 16
    assert svc.quantidade_pelo_percentual(Decimal("41850"), Decimal("10"), Decimal("9.30")) == 450
    assert svc.quantidade_pelo_percentual(Decimal("0"), Decimal("10"), Decimal("9.30")) == 0
    assert svc.total_da_linha(450, Decimal("9.30")) == Decimal("4185.00")
    assert svc.total_da_linha(3, Decimal("0.335")) == Decimal("1.01")
    assert svc.cfop_sugerido("SP", "SP") == "5102"
    assert svc.cfop_sugerido("SP", "rj") == "6102"
    assert svc.cfop_sugerido(None, "RJ") == "5102"


# --- registrar compra -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registrar_compra_quantidade(
    client: AsyncClient, db: AsyncSession, operador, auth_as, cenario
):
    auth_as(operador)
    r = await client.post(BASE, json=_compra(cenario))
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["numero"] >= 8_000_001 and c["status"] == "registrada" and c["cfop"] == "5102"
    assert Decimal(c["total"]) == Decimal("4185.00") and Decimal(c["faturamento"]) == Decimal(
        "15050.50"
    )
    assert c["vendedor_nome"] == "COMERCIAL BEZERRA" and c["vendedor_uf"] == "SP"
    assert c["competencia"] == "2026-09-01" and c["data_pedido"] == "2026-10-23"
    assert c["comprador"]["razao_social"] == "VITA ENXOVAIS LTDA"
    assert c["comprador"]["endereco_origem"] == "nfeio" and c["criado_por"] == "Operador Compra"
    it = c["itens"][0]
    assert it["quantidade"] == 450 and Decimal(it["valor_unitario"]) == Decimal("9.30")
    assert Decimal(it["valor_total"]) == Decimal("4185.00") and it["ncm"] == "39232110"

    r2 = await client.post(BASE, json=_compra(cenario, data_pedido=None))
    assert r2.status_code == 201 and r2.json()["numero"] == c["numero"] + 1
    assert r2.json()["data_pedido"] == svc.hoje_sp().isoformat()


@pytest.mark.asyncio
async def test_cfop_6102_e_endereco_digitado(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    r = await client.post(BASE, json=_compra(cenario, company_id=str(cenario["rio"])))
    assert r.status_code == 201 and r.json()["cfop"] == "6102"  # SP vende para RJ
    assert r.json()["comprador"]["inscricao_estadual"] == "86123456"
    # SEM: sem endereço nenhum → recusa dizendo o que falta; digitado → passa e marca "digitado"
    r = await client.post(BASE, json=_compra(cenario, company_id=str(cenario["sem"])))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "endereco_incompleto"
    assert r.json()["detail"]["faltando"] == ["rua", "número", "bairro", "CEP", "cidade"]
    end = {
        "logradouro": "Rua B",
        "numero": "5",
        "bairro": "Centro",
        "cep": "30110-000",
        "municipio": "Belo Horizonte",
        "uf": "MG",
    }
    r = await client.post(
        BASE,
        json=_compra(
            cenario,
            company_id=str(cenario["sem"]),
            endereco=end,
            inscricao_estadual="isento",
            cfop="5.949",
        ),
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["cfop"] == "5949" and c["comprador"]["inscricao_estadual"] == "ISENTO"
    assert (
        c["comprador"]["endereco_origem"] == "digitado"
        and c["comprador"]["endereco"]["cep"] == "30110000"
    )
    assert c["faturamento"] is None  # SEM não tem loja


@pytest.mark.asyncio
async def test_modo_percentual(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    corpo = _compra(cenario, modo="percentual", percentual="1,5")
    corpo["itens"][0]["quantidade"] = svc.quantidade_pelo_percentual(
        Decimal("15050.50"), Decimal("1.5"), Decimal("9.30")
    )
    r = await client.post(BASE, json=corpo)
    assert r.status_code == 201, r.text
    assert (
        Decimal(r.json()["percentual"]) == Decimal("1.5")
        and r.json()["itens"][0]["quantidade"] == 24
    )
    r = await client.post(BASE, json=_compra(cenario, modo="percentual"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_percentual"
    end = {
        "logradouro": "Rua B",
        "numero": "5",
        "bairro": "Centro",
        "cep": "30110000",
        "municipio": "Belo Horizonte",
        "uf": "MG",
    }
    r = await client.post(
        BASE,
        json=_compra(
            cenario, company_id=str(cenario["sem"]), endereco=end, modo="percentual", percentual="2"
        ),
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_faturamento"
    # modo quantidade nunca grava percentual
    r = await client.post(BASE, json=_compra(cenario, percentual="3"))
    assert r.status_code == 201 and r.json()["percentual"] is None


@pytest.mark.asyncio
async def test_validacoes_da_compra(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)

    async def erro(corpo: dict) -> tuple[int, str | None]:
        r = await client.post(BASE, json=corpo)
        d = r.json().get("detail")
        return r.status_code, (d.get("code") if isinstance(d, dict) else None)

    assert await erro(_compra(cenario, vendedor_cnpj=CNPJ_VELHA)) == (422, "vendedora_invalida")
    assert await erro(_compra(cenario, vendedor_cnpj=CNPJ_RIO)) == (
        422,
        "vendedora_invalida",
    )  # do grupo
    futuro = (svc.hoje_sp().replace(day=1) + timedelta(days=40)).replace(day=1).isoformat()
    assert await erro(_compra(cenario, competencia=futuro)) == (422, "mes_futuro")
    assert await erro(_compra(cenario, company_id=str(uuid.uuid4()))) == (
        404,
        "empresa_nao_encontrada",
    )
    for item in (
        {"quantidade": 0},
        {"quantidade": "4,5"},
        {"valor_unitario": "0"},
        {"ncm": "3923"},
        {"valor_unitario": "9.333"},
    ):
        corpo = _compra(cenario)
        corpo["itens"][0].update(item)
        assert (await client.post(BASE, json=corpo)).status_code == 422, item
    assert (await client.post(BASE, json=_compra(cenario, itens=[]))).status_code == 422
    assert (await client.post(BASE, json=_compra(cenario, cfop="99"))).status_code == 422
    # "1.000" = mil (milhar) e "9,30" com vírgula
    corpo = _compra(cenario)
    corpo["itens"][0].update({"quantidade": "1.000", "valor_unitario": "9,30"})
    r = await client.post(BASE, json=corpo)
    assert r.status_code == 201 and Decimal(r.json()["total"]) == Decimal("9300.00")


@pytest.mark.asyncio
async def test_varios_itens_soma_o_total(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    corpo = _compra(cenario)
    corpo["itens"].append(
        {
            "nome": "Fita",
            "sku": "f01",
            "ncm": "39191000",
            "quantidade": 3,
            "valor_unitario": "12.50",
        }
    )
    r = await client.post(BASE, json=corpo)
    assert r.status_code == 201 and Decimal(r.json()["total"]) == Decimal("4222.50")
    assert [i["nome"] for i in r.json()["itens"]] == ["Embalagem", "Fita"]


# --- planilha do Bling --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planilha_no_formato_do_bling(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    c = (
        await client.post(BASE, json=_compra(cenario, observacoes="Compra de embalagem set/26"))
    ).json()
    r = await client.get(f"{BASE}/{c['id']}/planilha")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f"compra_{c['numero']}_comercial-bezerra.csv" in r.headers["content-disposition"]
    linhas = _linhas_csv(r.content)
    assert linhas[0] == COLUNAS and len(linhas) == 2
    row = dict(zip(COLUNAS, linhas[1], strict=True))
    assert (
        row["Número pedido"] == str(c["numero"]) and row["Nome Comprador"] == "VITA ENXOVAIS LTDA"
    )
    assert row["Data"] == "23/10/2026" and row["CPF/CNPJ Comprador"] == "52.133.663/0001-88"
    assert (
        row["Endereço Comprador"] == "Rua Antonio Pereira de Sousa"
        and row["Número Comprador"] == "240"
    )
    assert row["Bairro Comprador"] == "Santana" and row["CEP Comprador"] == "02404060"
    assert row["Cidade Comprador"] == "São Paulo" and row["UF Comprador"] == "SP"
    assert (row["Produto"], row["SKU"], row["Un"]) == ("Embalagem", "a001", "UN")
    assert (row["Quantidade"], row["Valor Unitário"], row["Valor Total"]) == (
        "450,00",
        "9,30",
        "4.185,00",
    )
    assert row["Total Pedido"] == "4.185,00" and row["Valor Frete Pedido"] == "0,00"
    assert (
        row["Observações"] == "Compra de embalagem set/26"
        and row["Nome Entrega"] == "VITA ENXOVAIS LTDA"
    )


@pytest.mark.asyncio
async def test_planilha_de_varias_compras(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    a = (await client.post(BASE, json=_compra(cenario))).json()
    b = (await client.post(BASE, json=_compra(cenario, company_id=str(cenario["rio"])))).json()
    t = (await client.post(BASE, json=_compra(cenario, vendedor_cnpj=CNPJ_TELES))).json()
    r = await client.post(f"{BASE}/planilha", json={"ids": [b["id"], a["id"], a["id"]]})
    assert r.status_code == 200
    linhas = _linhas_csv(r.content)
    assert [x[0] for x in linhas[1:]] == [
        str(a["numero"]),
        str(b["numero"]),
    ]  # ordem do nº, sem repetir
    r = await client.post(f"{BASE}/planilha", json={"ids": [a["id"], t["id"]]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "vendedoras_diferentes"
    await client.post(f"{BASE}/{b['id']}/cancelar")
    r = await client.post(f"{BASE}/planilha", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 409
    assert (
        await client.post(f"{BASE}/planilha", json={"ids": [str(uuid.uuid4())]})
    ).status_code == 404


# --- vida da compra ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nf_cancelar_reabrir_excluir(
    client: AsyncClient, db: AsyncSession, make_user, operador, auth_as, cenario
):
    auth_as(operador)
    c = (await client.post(BASE, json=_compra(cenario))).json()
    u = f"{BASE}/{c['id']}"
    r = await client.post(f"{u}/nf", json={"nf_numero": "001.234"})
    assert (
        r.status_code == 200
        and r.json()["status"] == "nf_emitida"
        and r.json()["nf_numero"] == "1234"
    )
    assert (await client.put(u, json=_compra(cenario))).status_code == 409
    r = await client.post(f"{u}/cancelar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "tem_nf"
    assert (await client.delete(u)).status_code == 409
    assert (
        await client.get(f"{u}/planilha")
    ).status_code == 200  # com NF ainda baixa (conferência)
    r = await client.post(f"{u}/desfazer-nf")
    assert r.json()["status"] == "registrada" and r.json()["nf_numero"] is None
    assert (await client.post(f"{u}/desfazer-nf")).status_code == 409
    r = await client.post(f"{u}/cancelar")
    assert r.json()["status"] == "cancelada"
    assert (await client.post(f"{u}/nf", json={"nf_numero": "5"})).status_code == 409
    assert (await client.get(f"{u}/planilha")).status_code == 409
    r = await client.post(f"{u}/reabrir")
    assert r.json()["status"] == "registrada"
    assert (await client.post(f"{u}/reabrir")).status_code == 409
    # excluir pede a permissão de excluir
    auth_as(await make_user(permissions={"emissao_servico": {"view": True, "edit": True}}))
    assert (await client.delete(u)).status_code == 403
    auth_as(operador)
    assert (await client.delete(u)).status_code == 204
    assert (
        await db.execute(select(CompraPedido).where(CompraPedido.id == uuid.UUID(c["id"])))
    ).first() is None
    assert (await client.get(u)).status_code == 404


@pytest.mark.asyncio
async def test_editar_compra(client: AsyncClient, db: AsyncSession, operador, auth_as, cenario):
    auth_as(operador)
    c = (await client.post(BASE, json=_compra(cenario))).json()
    corpo = _compra(cenario)
    corpo["itens"][0]["quantidade"] = 500
    r = await client.put(f"{BASE}/{c['id']}", json=corpo)
    assert r.status_code == 200 and Decimal(r.json()["total"]) == Decimal("4650.00")
    assert r.json()["numero"] == c["numero"] and len(r.json()["itens"]) == 1
    # a vendedora da compra foi desativada depois: editar ainda deixa mantê-la
    v = await db.get(CompraVendedora, CNPJ_BEZERRA)
    v.ativo = False
    await db.commit()
    r = await client.put(f"{BASE}/{c['id']}", json=corpo)
    assert r.status_code == 200 and r.json()["vendedor_nome"] == "COMERCIAL BEZERRA"
    # ... mas não dá para TROCAR para outra que não está na lista
    r = await client.put(f"{BASE}/{c['id']}", json=_compra(cenario, vendedor_cnpj=CNPJ_VELHA))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_listar_por_mes_empresa_e_status(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    a = (await client.post(BASE, json=_compra(cenario))).json()
    b = (
        await client.post(
            BASE, json=_compra(cenario, competencia="2026-08-01", company_id=str(cenario["rio"]))
        )
    ).json()
    await client.post(f"{BASE}/{b['id']}/cancelar")
    todos = (await client.get(BASE)).json()
    assert [x["numero"] for x in todos] == [b["numero"], a["numero"]]
    assert [
        x["id"] for x in (await client.get(BASE, params={"competencia": "2026-09-20"})).json()
    ] == [a["id"]]
    assert [
        x["id"] for x in (await client.get(BASE, params={"company_id": str(cenario["rio"])})).json()
    ] == [b["id"]]
    assert [x["id"] for x in (await client.get(BASE, params={"status": "cancelada"})).json()] == [
        b["id"]
    ]


@pytest.mark.asyncio
async def test_numero_comeca_em_8_milhoes(db: AsyncSession, cenario):
    """A faixa do nº não encosta nos pedidos do Bling (6 dígitos) nem nos do ML 100% (9…)."""
    c = await db.get(Company, cenario["vita"])
    p = CompraPedido(
        company_id=c.id,
        vendedor_cnpj=CNPJ_BEZERRA,
        vendedor_nome="X",
        competencia=date(2026, 9, 1),
        data_pedido=date(2026, 10, 1),
        modo="quantidade",
        cfop="5102",
        comprador={},
        total=Decimal("1.00"),
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    assert 8_000_001 <= p.numero < 9_000_000


# --- revisão 23/10: defeitos achados e o que não pode voltar -------------------------


@pytest.mark.asyncio
async def test_editar_mantem_o_endereco_digitado(client: AsyncClient, operador, auth_as, cenario):
    """Editar só a quantidade (sem mandar endereço) mantém o endereço gravado na
    compra — antes voltava ao automático ou dava 422 endereco_incompleto."""
    auth_as(operador)
    end = {
        "logradouro": "Rua B",
        "numero": "5",
        "complemento": "sala 2",
        "bairro": "Centro",
        "cep": "30110000",
        "municipio": "Belo Horizonte",
        "uf": "MG",
    }
    c = (
        await client.post(BASE, json=_compra(cenario, company_id=str(cenario["sem"]), endereco=end))
    ).json()
    corpo = _compra(cenario, company_id=str(cenario["sem"]))
    corpo["itens"][0]["quantidade"] = 10
    r = await client.put(f"{BASE}/{c['id']}", json=corpo)
    assert r.status_code == 200, r.text
    assert r.json()["comprador"]["endereco"]["cep"] == "30110000"
    assert r.json()["comprador"]["endereco_origem"] == "digitado"
    # VITA com o bairro corrigido à mão: editar não volta ao bairro da NFE.io
    v = (
        await client.post(BASE, json=_compra(cenario, endereco={"bairro": "Santana de Cima"}))
    ).json()
    assert v["comprador"]["endereco"]["bairro"] == "Santana de Cima"
    assert (
        v["comprador"]["endereco"]["logradouro"] == "Rua Antonio Pereira de Sousa"
    )  # o resto continua
    r = await client.put(f"{BASE}/{v['id']}", json=_compra(cenario))
    assert r.json()["comprador"]["endereco"]["bairro"] == "Santana de Cima"
    linhas = _linhas_csv((await client.get(f"{BASE}/{v['id']}/planilha")).content)
    assert dict(zip(COLUNAS, linhas[1], strict=True))["Bairro Comprador"] == "Santana de Cima"
    # apagar o complemento vale (antes o campo vazio era ignorado)
    end2 = {**end, "complemento": ""}
    r = await client.put(
        f"{BASE}/{c['id']}", json=_compra(cenario, company_id=str(cenario["sem"]), endereco=end2)
    )
    assert r.status_code == 200 and r.json()["comprador"]["endereco"]["complemento"] is None


@pytest.mark.asyncio
async def test_planilha_de_varias_recusa_compra_com_nf(
    client: AsyncClient, operador, auth_as, cenario
):
    auth_as(operador)
    a = (await client.post(BASE, json=_compra(cenario))).json()
    b = (await client.post(BASE, json=_compra(cenario))).json()
    await client.post(f"{BASE}/{a['id']}/nf", json={"nf_numero": "77"})
    r = await client.post(f"{BASE}/planilha", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "compra_com_nf"
    assert str(a["numero"]) in r.json()["detail"]["mensagem"]
    assert (await client.post(f"{BASE}/planilha", json={"ids": [b["id"]]})).status_code == 200


@pytest.mark.asyncio
async def test_quantidade_uf_tamanhos_e_total(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    for qtd in ("1.5", "4.50", "1,5", "-3", "1.0000"):
        corpo = _compra(cenario)
        corpo["itens"][0]["quantidade"] = qtd
        assert (await client.post(BASE, json=corpo)).status_code == 422, qtd
    corpo = _compra(cenario)
    corpo["itens"][0]["quantidade"] = "12.345"
    r = await client.post(BASE, json=corpo)
    assert r.status_code == 201 and r.json()["itens"][0]["quantidade"] == 12345
    end = {
        "logradouro": "Rua B",
        "numero": "5",
        "bairro": "Centro",
        "cep": "30110000",
        "municipio": "Belo Horizonte",
        "uf": "XX",
    }
    assert (
        await client.post(BASE, json=_compra(cenario, company_id=str(cenario["sem"]), endereco=end))
    ).status_code == 422
    assert (
        await client.post(
            BASE,
            json=_compra(
                cenario, company_id=str(cenario["sem"]), endereco={**end, "uf": "MG", "bairro": "X"}
            ),
        )
    ).status_code == 422
    assert (
        await client.post(
            BASE,
            json=_compra(
                cenario,
                company_id=str(cenario["sem"]),
                endereco={**end, "uf": "MG", "logradouro": "R" * 61},
            ),
        )
    ).status_code == 422
    corpo = _compra(cenario)
    corpo["itens"] = [
        {
            "nome": "Embalagem",
            "sku": "a001",
            "ncm": "39232110",
            "quantidade": 1_000_000,
            "valor_unitario": "9999999.99",
        },
        {
            "nome": "Embalagem",
            "sku": "a001",
            "ncm": "39232110",
            "quantidade": 1_000_000,
            "valor_unitario": "9999999.99",
        },
    ]
    corpo["itens"] = corpo["itens"] * 10
    r = await client.post(BASE, json=corpo)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "total_grande"


@pytest.mark.asyncio
async def test_unidade_do_produto_vai_na_planilha(client: AsyncClient, operador, auth_as, cenario):
    auth_as(operador)
    corpo = _compra(cenario)
    corpo["itens"][0]["unidade"] = "cx"
    c = (await client.post(BASE, json=corpo)).json()
    assert c["itens"][0]["unidade"] == "CX"
    linhas = _linhas_csv((await client.get(f"{BASE}/{c['id']}/planilha")).content)
    assert dict(zip(COLUNAS, linhas[1], strict=True))["Un"] == "CX"
