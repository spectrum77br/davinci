"""Tabela de Preços › Produtos: Modelo / Nº do certificado / Validade vindos
da Certificações (05/11/2026).

Desde a M-69 (26/11/2026) o produto acha o certificado pela coluna SKU da
Certificações (sem ela, pelo gravado na linha); o código no nome não liga mais
ninguém e a coluna Modelo ficou só para leitura. Os casos da ligação pelo nome
(com os nomes e certificados da produção de 05/11/2026) foram para
test_certificacao_sku_migration.py: conferem a cópia congelada que a conversão
da 0512 usa.

A tabela financeiro_suprimentos (e a coluna SKU, que sai junto) não é limpa
entre os testes (conftest): cada teste apaga o que criou."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FinanceiroSuprimentos,
    FinanceiroSuprimentoSku,
    Segment,
    User,
    UserRole,
    UserStatus,
)
from app.models.pricing import PricingProduct


def _sku() -> str:
    return f"t{uuid.uuid4().hex[:7]}"


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
    sku = _sku()
    cert = FinanceiroSuprimentos(
        produto="smartphone 4G",
        modelo="USM003",
        certificado="anatel",
        numero="07197-26-18234",
        inicio=date(2026, 7, 30),
        fim=date(2028, 7, 30),
    )
    db.add(cert)
    await db.flush()
    db.add(FinanceiroSuprimentoSku(sku=sku, suprimento_id=cert.id))
    await db.commit()

    try:
        auth_as(editor)
        r = await client.post(
            "/api/pricing/products",
            json={"sku": f"{sku},{sku}0", "name": "uranyx A17 pro max 12.128", "cost_kit1": "100"},
        )
        assert r.status_code == 201
        assert r.json()["certificacao"] == {
            "id": str(cert.id),
            "produto": "smartphone 4G",
            "modelo": "USM003",
            "certificado": "anatel",
            "numero": "07197-26-18234",
            "fim": "2028-07-30",
            "garantia_meses": None,
        }
        assert r.json()["certificacao_conflito"] == []
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

        # O código no nome não liga mais (M-69): com "- USM003" e sem o SKU
        # na coluna, fica sem certificado.
        r = await client.patch(
            f"/api/pricing/products/{pid}",
            json={"sku": f"{sku}9", "name": "uranyx A17 pro max 12.128 - USM003"},
        )
        assert r.status_code == 200
        assert r.json()["certificacao"] is None
    finally:
        await db.rollback()
        await db.execute(delete(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id == cert.id))
        await db.commit()


@pytest.mark.asyncio
async def test_certificado_gravado_vale_sem_sku_e_o_modelo_e_so_leitura(
    db: AsyncSession,
    client: AsyncClient,
    editor: User,
    auth_as: Callable[[User | None], None],
):
    """O certificado gravado na linha (0457; desde a 0512 só o que a conversão
    não conseguiu dizer pela coluna SKU) continua valendo quando a coluna SKU
    não fala desse produto. A coluna Modelo é só leitura (M-69)."""
    root = Segment(name="Celular", slug="celular", sort_order=0)
    db.add(root)
    await db.commit()
    await db.refresh(root)
    folha = Segment(name="Cel + Fone", slug="cel-fone", parent_id=root.id, sort_order=1)
    db.add(folha)
    usm003 = FinanceiroSuprimentos(
        produto="smartphone 4G",
        modelo="USM003",
        certificado="anatel",
        numero="07197-26-18234",
        fim=date(2028, 7, 30),
    )
    usm001 = FinanceiroSuprimentos(
        produto="smartphone 5G",
        modelo="USM001",
        certificado="anatel",
        numero="08215-25-18234",
        fim=date(2028, 2, 20),
    )
    db.add_all([usm003, usm001])
    await db.commit()
    p = PricingProduct(
        user_id=editor.id,
        sku=_sku(),
        name="uranyx A17 - USM001",
        segment_id=folha.id,
        certificacao_id=usm003.id,
    )
    db.add(p)
    await db.commit()
    pid = str(p.id)

    try:
        auth_as(editor)
        # O gravado vale; o "- USM001" do nome não conta.
        r = await client.get("/api/pricing/products")
        (linha,) = [x for x in r.json() if x["id"] == pid]
        assert linha["certificacao"]["modelo"] == "USM003"
        assert linha["certificacao_id"] == str(usm003.id)

        # Opções da coluna Modelo continuam lidas sem a permissão de Certificações.
        r = await client.get("/api/pricing/certificacoes")
        assert r.status_code == 200
        assert {c["modelo"] for c in r.json()} >= {"USM003", "USM001"}

        # Escolher (ou tirar) pela Tabela de Preços: 400 com a frase, nada muda.
        for valor in (str(usm001.id), None, str(uuid.uuid4())):
            r = await client.patch(f"/api/pricing/products/{pid}", json={"certificacao_id": valor})
            assert r.status_code == 400
            assert r.json()["detail"]["message"] == (
                "Modelo: definido em Importação › Certificações."
            )
        await db.refresh(p)
        assert p.certificacao_id == usm003.id

        # Certificação excluída → o vínculo vira NULL (ON DELETE SET NULL).
        await db.delete(usm003)
        await db.commit()
        usm003 = None
        r = await client.get("/api/pricing/products")
        (linha,) = [x for x in r.json() if x["id"] == pid]
        assert linha["certificacao_id"] is None and linha["certificacao"] is None
    finally:
        await db.rollback()
        ids = [c.id for c in (usm003, usm001) if c is not None]
        await db.execute(delete(FinanceiroSuprimentos).where(FinanceiroSuprimentos.id.in_(ids)))
        await db.commit()


@pytest.mark.asyncio
async def test_opcoes_de_certificado_pedem_permissao_da_tabela(
    client: AsyncClient,
    db: AsyncSession,
    auth_as: Callable[[User | None], None],
):
    u = User(
        open_id=f"email:sem-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"sem-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(u)
    await db.commit()
    auth_as(u)
    r = await client.get("/api/pricing/certificacoes")
    assert r.status_code == 403
