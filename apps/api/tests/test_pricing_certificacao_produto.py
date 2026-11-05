"""Tabela de Preços › Produtos: Modelo / Nº do certificado / Validade vindos
da Certificações pelo código no nome do produto (05/11/2026).

Os nomes e os certificados abaixo são os da produção em 05/11/2026."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FinanceiroSuprimentos, Segment, User, UserRole, UserStatus
from app.services.certificacao_produto import (
    Certificacao,
    certificacao_do_produto,
    codigo_do_nome,
)

M = "M1, M2, M3, M4, M5, M6, M7, M8, M9, M10"
CERTS = [
    # (produto, modelo, nome comercial, certificado, número, fim)
    ("airfryer", "UAF001-M1", M, "inmetro", "MODERNA-0069/25", date(2032, 4, 30)),
    ("airfryer barbecue", "UAF002-M1", M, "inmetro", "MODERNA-0069/25", date(2032, 4, 30)),
    ("airtag", "AirTag UAT001", None, "anatel", "06879-26-18234", date(2028, 9, 3)),
    ("bateria a17", None, None, "anatel", None, None),
    ("bateria f109", "F109", None, "anatel", "08214-25-18234", date(2028, 1, 19)),
    ("bateria f110", "F110", None, "anatel", "08201-25-18234", date(2028, 1, 16)),
    ("carregador", "QZ-0180AE2H", None, "anatel", "08217-25-18234", date(2028, 3, 11)),
    ("cofee machine", "UCM001-M1", M, "inmetro", "MODERNA-0070/25", date(2032, 5, 21)),
    ("cooktop de induçao", "UCI001", M, "inmetro", None, None),
    ("cookware", "UCW001", M, "inmetro", None, None),
    ("fone bluetooth", "A9 Pro", "UFB10", "anatel", "06764-25-18234", date(2027, 11, 7)),
    ("fone com fio", "UFF001", M, "isento", None, None),
    ("slushie maker", "USL001-M1", M, "inmetro", "MODERNA-0071/25", date(2032, 7, 30)),
    ("smart cooking", "UCO001-M1", M, "inmetro", "MODERNA-0072/26", date(2032, 7, 30)),
    ("smart glass", "USG001", None, "anatel", "07295-26-18234", date(2028, 8, 27)),
    (
        "smartphone 4G",
        "USM002",
        "FOSSIBOT F110, OUKITEL C2, OUKITEL WP52",
        "anatel",
        "08216-25-18234",
        date(2028, 2, 24),
    ),
    (
        "smartphone 4G",
        "USM003",
        "A17, A18, c17, f112, flat 3c, marine",
        "anatel",
        "07197-26-18234",
        date(2028, 7, 30),
    ),
    (
        "smartphone 4G",
        "S41 Plus",
        "DOOGEE 25, Fire3",
        "anatel",
        "09604-24-17288",
        date(2026, 10, 28),
    ),
    (
        "smartphone 5G",
        "USM001",
        "FOSSIBOT F109, OUKITEL WP52, OUKITEL WP60",
        "anatel",
        "08215-25-18234",
        date(2028, 2, 20),
    ),
    ("smartwatch", "W69", "USW10", "anatel", "06763-25-18234", date(2027, 11, 6)),
    (
        "Transceptor de Radiação Restrita",
        "EarPhone Doogee 25",
        "Doogee H25",
        "anatel",
        "09607-24-17288",
        date(2026, 12, 19),
    ),
]


def _certs() -> list[Certificacao]:
    return [Certificacao(uuid.uuid4(), *c) for c in CERTS]


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        # Celular: código depois do último " - ".
        ("uranyx A17 pro max 12.128 prata/preto/laranja - USM003", ("USM003", "07197-26-18234")),
        (
            "uranyx A18 pro max - S5 edition 16.128 laranja/prata - USM003",
            ("USM003", "07197-26-18234"),
        ),
        ("uranyx F109 5G 24.256 Preto - USM001", ("USM001", "08215-25-18234")),
        ("uranyx F110 pro 5G 20.128 - USM002", ("USM002", "08216-25-18234")),
        ("uranyx airtag - UAT001", ("AirTag UAT001", "06879-26-18234")),
        ("uranyx carregador 20w - QZ-0180AE2H", ("QZ-0180AE2H", "08217-25-18234")),
        ("uranyx fone - UFB10", ("A9 Pro", "06764-25-18234")),
        ("uranyx relogio - USW10", ("W69", "06763-25-18234")),
        ("uranyx smart glass - USG001", ("USG001", "07295-26-18234")),
        ("uranyx fone com fio - UFF001", ("UFF001", None)),
        # Eletro: modelo no meio do nome.
        ("airfryer vidro UAF001 M1 MODERNA-0069/25", ("UAF001-M1", "MODERNA-0069/25")),
        ("cafeteira UCM001 M1 MODERNA-0070/25", ("UCM001-M1", "MODERNA-0070/25")),
        ("cookware UCW001", ("UCW001", None)),
        ("sluchie USL001 M1 MODERNA-0071/25", ("USL001-M1", "MODERNA-0071/25")),
        ("smart cooking UCO001 M1 MODERNA-0072/26", ("UCO001-M1", "MODERNA-0072/26")),
        # Sem código conhecido → sem certificado (não chuta).
        ("airfryer barbecue UAF001 M2 MODERNA-0069/25", None),  # M2 não existe
        ("airfryer pote vidro 4L", None),
        ("apple watch S11 46mm - cinza/prata/preto/rosa", None),
        ("Oukitel C2 4+256 AVULSO", None),  # nome comercial em lista não vale
        ("Oukitel WP52 4+256 AVULSO", None),
        ("uranyx case", None),
        ("Mala Arco P2 tamanho 14 - Azul Escuro", None),
    ],
)
def test_certificado_pelo_nome(nome: str, esperado: tuple[str, str | None] | None):
    cert = certificacao_do_produto(nome, _certs())
    if esperado is None:
        assert cert is None
    else:
        assert cert is not None
        assert (cert.modelo, cert.numero) == esperado


def test_codigo_do_nome():
    assert codigo_do_nome("uranyx fone - UFB10") == "UFB10"
    assert codigo_do_nome("uranyx A18 pro max - S5 edition 16.128 - USM003") == "USM003"
    assert codigo_do_nome("uranyx A18 pro max - S5 edition") is None  # duas palavras
    assert codigo_do_nome("Oukitel C2 4+256 AVULSO") is None
    assert codigo_do_nome(None) is None


def test_renovado_fica_o_de_validade_mais_longa_e_empate_nao_escolhe():
    velho = Certificacao(
        uuid.uuid4(), "smartphone", "USM009", None, "anatel", "1", date(2026, 1, 1)
    )
    novo = Certificacao(uuid.uuid4(), "smartphone", "USM009", None, "anatel", "2", date(2028, 1, 1))
    assert certificacao_do_produto("x - USM009", [velho, novo]) == novo
    # Linha sem número (rascunho) perde para a que tem número.
    rascunho = Certificacao(
        uuid.uuid4(), "smartphone", "USM009", None, "anatel", None, date(2030, 1, 1)
    )
    assert certificacao_do_produto("x - USM009", [rascunho, velho]) == velho
    gemeo = Certificacao(
        uuid.uuid4(), "smartphone", "USM009", None, "anatel", "3", date(2028, 1, 1)
    )
    assert certificacao_do_produto("x - USM009", [novo, gemeo]) is None


# ------------------------------------------------------------------ API

PERM = {
    "tabela_precos": {"view": True, "edit": True, "delete": False},
    "tabela_precos_produtos": {"view": True, "edit": True, "delete": False},
}


@pytest_asyncio.fixture
async def editor(db: AsyncSession) -> User:
    """Edita a Tabela de Preços e NÃO vê Certificações: as colunas vêm
    mesmo assim (modelo/número/validade não são dado restrito)."""
    u = User(
        open_id=f"email:cp-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"cp-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_produtos_trazem_o_certificado_e_acompanham_a_certificacoes(
    db: AsyncSession,
    client: AsyncClient,
    editor: User,
    auth_as: Callable[[User | None], None],
):
    root = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(root)
    await db.commit()
    await db.refresh(root)
    db.add(Segment(name="Cel + Fone", slug="cel-fone", parent_id=root.id, sort_order=1))
    cert = FinanceiroSuprimentos(
        produto="smartphone 4G",
        modelo="USM003",
        certificado="anatel",
        numero="07197-26-18234",
        inicio=date(2026, 7, 30),
        fim=date(2028, 7, 30),
    )
    db.add(cert)
    await db.commit()

    auth_as(editor)
    r = await client.post(
        "/api/pricing/products",
        json={"sku": "dg052", "name": "uranyx A17 pro max 12.128 - USM003", "cost_kit1": "100"},
    )
    assert r.status_code == 201
    assert r.json()["certificacao"] == {
        "id": str(cert.id),
        "produto": "smartphone 4G",
        "modelo": "USM003",
        "certificado": "anatel",
        "numero": "07197-26-18234",
        "fim": "2028-07-30",
    }
    pid = r.json()["id"]

    # Editar o custo devolve a linha inteira — com o certificado junto.
    r = await client.patch(f"/api/pricing/products/{pid}", json={"cost_kit1": "120"})
    assert r.status_code == 200
    assert r.json()["certificacao"]["numero"] == "07197-26-18234"

    # Renovou na Certificações → a Tabela de Preços acompanha.
    cert.numero = "99999-27-18234"
    cert.fim = date(2030, 7, 30)
    await db.commit()
    r = await client.get("/api/pricing/products")
    assert r.status_code == 200
    (linha,) = [p for p in r.json() if p["id"] == pid]
    assert (linha["certificacao"]["numero"], linha["certificacao"]["fim"]) == (
        "99999-27-18234",
        "2030-07-30",
    )

    # Tirou o código do nome → sem certificado.
    r = await client.patch(
        f"/api/pricing/products/{pid}", json={"name": "uranyx A17 pro max 12.128"}
    )
    assert r.status_code == 200
    assert r.json()["certificacao"] is None
