"""Simulação de importação: PDF + anexar ao lote + DI + pagamento Bling.

Cobre a spec `simulaçao` (migration 0214):
- cálculo puro (`simulacao_pdf.calcular`): AFRMM 8% do Frete+Seguro,
  taxas BRL entram no espaço USD como valor/câmbio, fator;
- `montar_pdf` gera PDF;
- GET /api/financeiro/simulacao/{id}/pdf + POST anexar-lote;
- GET /api/importacao/lotes/{id}/simulacao-pdf;
- POST /api/importacao/lotes/{id}/di (upload) + GET di-pdf;
- POST /api/importacao/lotes/{id}/di-pagamento (parcelas no Bling).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FinanceiroSimulacao,
    ImportLote,
    User,
    UserRole,
    UserStatus,
)
from app.services.simulacao_pdf import calcular, montar_pdf

PERM = {
    "importacao": {"view": True, "edit": True, "delete": True},
    "financeiro_simulacao": {"view": True, "edit": True, "delete": True},
}


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:sd-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"sd-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


class _Sim:
    """Stub com os atributos da FinanceiroSimulacao usados pelo cálculo."""

    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _sim_kw(**extra: object) -> dict:
    base: dict[str, object] = {
        "taxa_cambio": Decimal("5.0"),
        "frete_seguro_usd": Decimal("1000"),
        "valor_unitario_usd": Decimal("10"),
        "quantidade": 100,
        "aliquota_ii": Decimal("0.16"),
        "aliquota_ipi": Decimal("0.05"),
        "aliquota_pis": Decimal("0.021"),
        "aliquota_cofins": Decimal("0.0965"),
        "taxa_siscomex_brl": Decimal("154.23"),
        "taxa_bl_brl": Decimal("3300"),
        "armazenagem_brl": Decimal("3644.18"),
        "despachante_sda_brl": Decimal("500"),
        "despachante_honorarios_brl": Decimal("1688"),
        "corretagem_cambio_brl": Decimal("70"),
        "inspecao_brl": Decimal("200"),
        "outras_taxas_brl": Decimal("0"),
        "cliente": "Cliente Teste",
        "data": date(2026, 8, 1),
        "descricao": "Mala ABS",
        "ncm": "4202.12.10",
    }
    base.update(extra)
    return base


# ── Cálculo puro ────────────────────────────────────────────────────────


def test_calcular_afrmm_8pct_frete_e_taxas_brl_via_cambio():
    d = calcular(_Sim(**_sim_kw()))
    # total invoice = 10 × 100 = 1000; CIF = 1000 + 1000 = 2000
    assert d["total_invoice"] == pytest.approx(1000.0)
    assert d["cif"] == pytest.approx(2000.0)
    # AFRMM = 8% do Frete+Seguro (NÃO 2% do CIF)
    assert d["afrmm"] == pytest.approx(1000 * 0.08)
    linhas = {ln.label: ln for ln in d["linhas"]}
    # taxa BRL entra no espaço USD como valor / câmbio; BRL mostra o fixo
    assert linhas["Taxa SISCOMEX"].usd == pytest.approx(154.23 / 5.0)
    assert linhas["Taxa SISCOMEX"].brl == pytest.approx(154.23)
    assert linhas["Taxa BL"].usd == pytest.approx(3300 / 5.0)
    assert linhas["Taxa BL"].brl == pytest.approx(3300.0)
    # subtotal = CIF + impostos + AFRMM + taxas BRL/câmbio.
    # Só o IPI tem base (CIF + II); os demais são sobre o CIF.
    # Inspeção (pré-embarque) fica FORA do subtotal (não entra na base
    # das alíquotas) e aparece depois do Frete Nacional.
    ii = 2000 * 0.16
    ipi = (2000 + ii) * 0.05
    assert linhas["IPI"].usd == pytest.approx(ipi)
    impostos = ii + ipi + 2000 * (0.021 + 0.0965)
    taxas = (154.23 + 3300 + 3644.18 + 500 + 1688 + 70) / 5.0
    assert d["subtotal"] == pytest.approx(2000 + impostos + 80 + taxas)
    labels = [ln.label for ln in d["linhas"]]
    assert labels.index("Inspeção (pré-embarque)") == labels.index("Frete Nacional") + 1


def test_calcular_fator_e_valor_unidade():
    d = calcular(_Sim(**_sim_kw(
        aliquota_taxas_gerais=Decimal("0.01"),
        aliquota_icms=Decimal("0.18"),
        aliquota_intermediacao=Decimal("0.02"),
    )))
    esperado = (
        d["subtotal"] * (1 + 0.01 + 0.18)
        + d["cif"] * 0.02
        + 200 / 5.0  # inspeção soma por fora, sem alíquotas
    )
    assert d["total_processo"] == pytest.approx(esperado)
    assert d["valor_unidade"] == pytest.approx(d["total_processo"] / 100)
    # fator = valor_unidade ÷ valor unitário origem (10)
    assert d["fator"] == pytest.approx(d["valor_unidade"] / 10)


def test_calcular_sem_cambio_e_qtd_zero_nao_explode():
    d = calcular(_Sim(**_sim_kw(taxa_cambio=None, quantidade=0)))
    assert d["cambio"] == 1.0  # fallback
    assert d["valor_unidade"] == 0.0
    assert d["fator"] == 0.0


def test_montar_pdf_gera_pdf():
    pdf = montar_pdf(_Sim(**_sim_kw()), lote_nome="ML30")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


# ── Endpoints simulação (financeiro) ───────────────────────────────────


async def _seed_sim(db: AsyncSession, lote_id: uuid.UUID | None = None) -> FinanceiroSimulacao:
    row = FinanceiroSimulacao(id=uuid.uuid4(), lote_id=lote_id, **_sim_kw())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _seed_lote(db: AsyncSession, **kw: object) -> ImportLote:
    lt = ImportLote(
        id=uuid.uuid4(), categoria="mala", nome="ML30",
        abertura=date(2026, 8, 1), **kw,
    )
    db.add(lt)
    await db.commit()
    await db.refresh(lt)
    return lt


@pytest.mark.asyncio
async def test_simulacao_pdf_endpoint(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    row = await _seed_sim(db)
    r = await client.get(f"/api/financeiro/simulacao/{row.id}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_anexar_lote_congela_pdf_no_lote(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    lt = await _seed_lote(db)
    row = await _seed_sim(db, lote_id=lt.id)
    r = await client.post(f"/api/financeiro/simulacao/{row.id}/anexar-lote")
    assert r.status_code == 200, r.text
    # o lote agora serve o PDF congelado
    r2 = await client.get(f"/api/importacao/lotes/{lt.id}/simulacao-pdf")
    assert r2.status_code == 200, r2.text
    assert r2.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_anexar_lote_sem_lote_422(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    row = await _seed_sim(db)  # sem lote_id
    r = await client.post(f"/api/financeiro/simulacao/{row.id}/anexar-lote")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "simulacao_sem_lote"


@pytest.mark.asyncio
async def test_lote_simulacao_pdf_404_sem_anexo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    lt = await _seed_lote(db)
    r = await client.get(f"/api/importacao/lotes/{lt.id}/simulacao-pdf")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "simulacao_pdf_nao_encontrado"


# ── DI: upload + download ──────────────────────────────────────────────

_PDF_MINI = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.mark.asyncio
async def test_di_upload_e_download(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    lt = await _seed_lote(db)
    r = await client.post(
        f"/api/importacao/lotes/{lt.id}/di",
        files={"file": ("di_ml30.pdf", _PDF_MINI, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tem_di_pdf"] is True
    r2 = await client.get(f"/api/importacao/lotes/{lt.id}/di-pdf")
    assert r2.status_code == 200
    assert r2.content == _PDF_MINI
    assert "di_ml30.pdf" in r2.headers["content-disposition"]


@pytest.mark.asyncio
async def test_di_upload_tipo_invalido_e_vazio(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    lt = await _seed_lote(db)
    r = await client.post(
        f"/api/importacao/lotes/{lt.id}/di",
        files={"file": ("di.txt", b"nao sou pdf", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "di_pdf_tipo_invalido"
    r2 = await client.post(
        f"/api/importacao/lotes/{lt.id}/di",
        files={"file": ("di.pdf", b"", "application/pdf")},
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "di_pdf_vazio"


@pytest.mark.asyncio
async def test_di_pdf_404_sem_anexo(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
):
    auth_as(admin_view)
    lt = await _seed_lote(db)
    r = await client.get(f"/api/importacao/lotes/{lt.id}/di-pdf")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "di_pdf_nao_encontrado"


# ── DI: pagamento no Bling ─────────────────────────────────────────────


class _FakeBling:
    def __init__(self, falhar_na: int | None = None) -> None:
        self.contas: list[dict] = []
        self._falhar_na = falhar_na

    async def create_conta_pagar(self, **kw: object) -> dict:
        if self._falhar_na is not None and len(self.contas) + 1 == self._falhar_na:
            raise RuntimeError("bling caiu")
        self.contas.append(kw)
        return {"id": 9000 + len(self.contas)}


@pytest.mark.asyncio
async def test_di_pagamento_parcelas_mensais(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin_view)
    lt = await _seed_lote(db, di_numero="26/1234567-8")
    fake = _FakeBling()

    async def _fake_client(_session):
        return fake

    import app.services.nf_emissao_gerar as gerar

    monkeypatch.setattr(gerar, "_bling_client_opt", _fake_client)
    r = await client.post(
        f"/api/importacao/lotes/{lt.id}/di-pagamento",
        json={
            "valor_total": "1000.01",
            "parcelas": 3,
            "periodo": "mensal",
            "primeiro_vencimento": "2026-08-31",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["di_pagamento"]["status"] == "ok"
    assert body["di_pagamento"]["bling_ids"] == [9001, 9002, 9003]
    assert len(fake.contas) == 3
    # divisão: 333.34 + 333.34 + última ajustada 333.33 = 1000.01
    valores = [c["valor"] for c in fake.contas]
    assert valores[0] == pytest.approx(333.34)
    assert valores[1] == pytest.approx(333.34)
    assert valores[2] == pytest.approx(333.33)
    assert sum(valores) == pytest.approx(1000.01)
    # vencimentos mensais com clamp de fim de mês (31/08 → 30/09 → 31/10)
    vencs = [c["vencimento"] for c in fake.contas]
    assert vencs == ["2026-08-31", "2026-09-30", "2026-10-31"]
    # contato fixo isatrading (id real da API) + histórico com nº da DI
    assert all(c["contato_id"] == 17052389798 for c in fake.contas)
    assert "26/1234567-8" in fake.contas[0]["historico"]
    assert "parcela 1/3" in fake.contas[0]["historico"]
    # categoria "importado" + nº do documento = nome do lote/parcela
    assert all(c["categoria_id"] == 14693438796 for c in fake.contas)
    docs = [c["numero_documento"] for c in fake.contas]
    assert docs == ["ML30/1", "ML30/2", "ML30/3"]


@pytest.mark.asyncio
async def test_di_pagamento_semanal_e_dedupe(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin_view)
    lt = await _seed_lote(db, di_numero="26/1234567-8")
    fake = _FakeBling()

    async def _fake_client(_session):
        return fake

    import app.services.nf_emissao_gerar as gerar

    monkeypatch.setattr(gerar, "_bling_client_opt", _fake_client)
    payload = {
        "valor_total": "200",
        "parcelas": 2,
        "periodo": "semanal",
        "primeiro_vencimento": "2026-08-10",
    }
    r = await client.post(f"/api/importacao/lotes/{lt.id}/di-pagamento", json=payload)
    assert r.status_code == 200, r.text
    vencs = [c["vencimento"] for c in fake.contas]
    assert vencs == ["2026-08-10", "2026-08-17"]
    # 2ª chamada: já lançado → 422 e NÃO cria mais contas
    r2 = await client.post(f"/api/importacao/lotes/{lt.id}/di-pagamento", json=payload)
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "di_pagamento_ja_lancado"
    assert len(fake.contas) == 2


@pytest.mark.asyncio
async def test_di_pagamento_falha_parcial_persiste_erro(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin_view)
    lt = await _seed_lote(db, di_numero="26/1234567-8")
    fake = _FakeBling(falhar_na=2)  # 1ª ok, 2ª explode

    async def _fake_client(_session):
        return fake

    import app.services.nf_emissao_gerar as gerar

    monkeypatch.setattr(gerar, "_bling_client_opt", _fake_client)
    r = await client.post(
        f"/api/importacao/lotes/{lt.id}/di-pagamento",
        json={
            "valor_total": "300",
            "parcelas": 3,
            "periodo": "mensal",
            "primeiro_vencimento": "2026-08-10",
        },
    )
    assert r.status_code == 502
    det = r.json()["detail"]
    assert det["code"] == "di_pagamento_parcial"
    assert det["lancadas"] == 1
    # estado persistido com erro (permite re-tentar: guard só bloqueia "ok")
    await db.refresh(lt)
    assert lt.di_pagamento["status"] == "erro"
    assert lt.di_pagamento["bling_ids"] == [9001]


@pytest.mark.asyncio
async def test_di_pagamento_sem_integracao_422(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
    monkeypatch: pytest.MonkeyPatch,
):
    auth_as(admin_view)
    lt = await _seed_lote(db, di_numero="26/1234567-8")

    async def _sem_client(_session):
        return None

    import app.services.nf_emissao_gerar as gerar

    monkeypatch.setattr(gerar, "_bling_client_opt", _sem_client)
    r = await client.post(
        f"/api/importacao/lotes/{lt.id}/di-pagamento",
        json={
            "valor_total": "100",
            "parcelas": 1,
            "periodo": "mensal",
            "primeiro_vencimento": "2026-08-10",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "bling_sem_integracao"


@pytest.mark.asyncio
async def test_di_pagamento_sem_numero_422(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None], admin_view: User,
    monkeypatch: pytest.MonkeyPatch,
):
    """Sem o nº da DI o histórico sai sem referência → bloqueia antes do Bling."""
    auth_as(admin_view)
    lt = await _seed_lote(db, di_numero="   ")
    fake = _FakeBling()

    async def _fake_client(_session):
        return fake

    import app.services.nf_emissao_gerar as gerar

    monkeypatch.setattr(gerar, "_bling_client_opt", _fake_client)
    r = await client.post(
        f"/api/importacao/lotes/{lt.id}/di-pagamento",
        json={
            "valor_total": "100",
            "parcelas": 1,
            "periodo": "mensal",
            "primeiro_vencimento": "2026-08-10",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "di_sem_numero"
    assert fake.contas == []
    await db.refresh(lt)
    assert lt.di_pagamento is None


# ── Auth ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoints_exigem_auth(
    db: AsyncSession, client: AsyncClient,
    auth_as: Callable[[User | None], None],
):
    auth_as(None)
    fake_id = uuid.uuid4()
    for metodo, path in [
        ("get", f"/api/financeiro/simulacao/{fake_id}/pdf"),
        ("post", f"/api/financeiro/simulacao/{fake_id}/anexar-lote"),
        ("get", f"/api/importacao/lotes/{fake_id}/simulacao-pdf"),
        ("get", f"/api/importacao/lotes/{fake_id}/di-pdf"),
    ]:
        r = await getattr(client, metodo)(path)
        assert r.status_code in (401, 403), path
