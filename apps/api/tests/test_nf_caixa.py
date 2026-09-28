"""NF de 100% que vai DENTRO DA CAIXA (28/09/2026).

Eduardo: os pedidos Correios seguem com a NF de 1% (a de sempre, com a
etiqueta) e passam a levar também a de 100%, que vai dentro da caixa — "a
primeira é sempre 1% e a segunda, que é 100%, precisa colocar informação para
distinguir"; "na hora de imprimir não pode aparecer aquela frase escrita, é só
a informação para eles não errarem". O que estes testes seguram:
- /agent/nf-caixa grava à parte da de 1% (uma não apaga a outra) e pode chegar
  antes da etiqueta;
- a impressão sai etiqueta → NF 1% → NF 100%, sem nenhum texto a mais;
- a lista do Controle de Estoque avisa (nf_caixa) só quem tem a segunda nota.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, User, UserRole, UserStatus
from app.models.nf import NfEtiquetaArquivo

_TOKEN = "nf-agent-test-token"
H = {"X-Agent-Token": _TOKEN}
PERM_VIEW = {"controle_estoque": {"view": True, "edit": False, "delete": False}}


def _pdf(*textos: str) -> bytes:
    import fitz

    doc = fitz.open()
    for txt in textos:
        page = doc.new_page(width=300, height=442)
        page.insert_text((20, 20), txt, fontsize=10, fontname="helv")
    return doc.tobytes()


def _paginas(pdf_bytes: bytes) -> list[str]:
    import fitz

    return [p.get_text().strip() for p in fitz.open(stream=pdf_bytes, filetype="pdf")]


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)


@pytest_asyncio.fixture
async def operador(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:cx-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"cx-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _linha(db: AsyncSession, pedido: str) -> NfEtiquetaArquivo:
    db.expire_all()
    return (
        await db.execute(select(NfEtiquetaArquivo).where(NfEtiquetaArquivo.pedido_bling == pedido))
    ).scalar_one()


@pytest.mark.asyncio
async def test_nf_caixa_grava_a_parte_e_nao_apaga_a_de_1(client: AsyncClient, db: AsyncSession):
    r = await client.post(
        "/api/nf-cadastro/agent/nf",
        data={"pedido_bling": "881001"},
        files={"file": ("danfe1.pdf", b"%PDF-UM-POR-CENTO", "application/pdf")},
        headers=H,
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/nf-cadastro/agent/nf-caixa",
        data={"pedido_bling": "881001"},
        files={"file": ("danfe100.pdf", b"%PDF-CEM-POR-CENTO", "application/pdf")},
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert r.json()["nf_caixa_size_bytes"] == len(b"%PDF-CEM-POR-CENTO")
    row = await _linha(db, "881001")
    assert row.nf_pdf == b"%PDF-UM-POR-CENTO"  # a de 1% continua
    assert row.nf_caixa_pdf == b"%PDF-CEM-POR-CENTO"
    assert row.blob == b""  # chegou antes da etiqueta: linha só com as notas
    # Reenviar a de 1% não apaga a da caixa.
    await client.post(
        "/api/nf-cadastro/agent/nf",
        data={"pedido_bling": "881001"},
        files={"file": ("danfe1.pdf", b"%PDF-UM-DE-NOVO", "application/pdf")},
        headers=H,
    )
    row = await _linha(db, "881001")
    assert row.nf_pdf == b"%PDF-UM-DE-NOVO" and row.nf_caixa_pdf == b"%PDF-CEM-POR-CENTO"


@pytest.mark.asyncio
async def test_nf_caixa_exige_token_e_arquivo(client: AsyncClient):
    r = await client.post(
        "/api/nf-cadastro/agent/nf-caixa",
        data={"pedido_bling": "881002"},
        files={"file": ("x.pdf", b"%PDF", "application/pdf")},
        headers={"X-Agent-Token": "errado"},
    )
    assert r.status_code == 401
    r = await client.post(
        "/api/nf-cadastro/agent/nf-caixa",
        data={"pedido_bling": "881002"},
        files={"file": ("x.pdf", b"", "application/pdf")},
        headers=H,
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "nf_pdf_vazia"


@pytest.mark.asyncio
async def test_impressao_etiqueta_depois_1_depois_100_sem_frase(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable[[User | None], None]
):
    db.add(BlingOrder(
        bling_id=881003, numero="881003", item_codigo="sku", item_index=0, situacao="21",
        em_andamento_data=date(2026, 9, 28),
    ))
    db.add(NfEtiquetaArquivo(
        pedido_bling="881003", filename="etiqueta_881003.pdf", content_type="application/pdf",
        size_bytes=1, blob=_pdf("ETIQUETA"), nf_pdf=_pdf("NF UM POR CENTO"), nf_size_bytes=1,
        nf_caixa_pdf=_pdf("NF CEM POR CENTO"), nf_caixa_size_bytes=1,
    ))
    await db.commit()
    auth_as(operador)
    r = await client.get("/api/estoque/pedidos/881003/etiqueta")
    assert r.status_code == 200, r.text
    paginas = _paginas(r.content)
    assert paginas == ["ETIQUETA", "NF UM POR CENTO", "NF CEM POR CENTO"]
    assert not any("CAIXA" in p.upper() for p in paginas)  # o aviso não vai pro papel


@pytest.mark.asyncio
async def test_nf_caixa_invalida_nao_tira_a_de_1(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable[[User | None], None]
):
    db.add(NfEtiquetaArquivo(
        pedido_bling="881004", filename="etiqueta_881004.pdf", content_type="application/pdf",
        size_bytes=1, blob=_pdf("ETIQUETA"), nf_pdf=_pdf("NF UM POR CENTO"), nf_size_bytes=1,
        nf_caixa_pdf=b"isto nao e pdf", nf_caixa_size_bytes=14,
    ))
    await db.commit()
    auth_as(operador)
    r = await client.get("/api/estoque/pedidos/881004/etiqueta")
    assert r.status_code == 200, r.text
    assert _paginas(r.content) == ["ETIQUETA", "NF UM POR CENTO"]


@pytest.mark.asyncio
async def test_lista_avisa_so_quem_tem_a_nf_da_caixa(
    client: AsyncClient, db: AsyncSession, operador: User, auth_as: Callable[[User | None], None]
):
    d = date(2026, 9, 28)
    for n in ("881005", "881006", "881007"):
        db.add(BlingOrder(bling_id=int(n), numero=n, item_codigo="sku", item_index=0, situacao="21",
                          em_andamento_data=d))
    db.add_all([
        # etiqueta + 1% + 100% → avisa
        NfEtiquetaArquivo(pedido_bling="881005", filename="e5.pdf", content_type="application/pdf",
                          size_bytes=1, blob=_pdf("E"), nf_pdf=_pdf("1"), nf_caixa_pdf=_pdf("100")),
        # só etiqueta + 1% → não avisa
        NfEtiquetaArquivo(pedido_bling="881006", filename="e6.pdf", content_type="application/pdf",
                          size_bytes=1, blob=_pdf("E"), nf_pdf=_pdf("1")),
        # notas sem etiqueta ainda → nem Imprimir nem aviso
        NfEtiquetaArquivo(pedido_bling="881007", filename="e7.pdf", content_type="application/pdf",
                          size_bytes=0, blob=b"", nf_pdf=_pdf("1"), nf_caixa_pdf=_pdf("100")),
    ])
    await db.commit()
    auth_as(operador)
    r = await client.get("/api/estoque/pedidos?data_inicio=2026-09-28&data_fim=2026-09-28")
    assert r.status_code == 200, r.text
    por = {p["pedido_bling"]: p for p in r.json()["data"]}
    assert por["881005"]["nf_caixa"] is True and por["881005"]["etiqueta_disponivel"] is True
    assert por["881006"]["nf_caixa"] is False
    assert por["881007"]["nf_caixa"] is False and por["881007"]["etiqueta_disponivel"] is False
