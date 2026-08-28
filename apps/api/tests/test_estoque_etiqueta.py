"""Landing zone da etiqueta transformada (item 4 da Fase 3b).

A aba Controle de Estoque → Pedidos ganha o flag `etiqueta_disponivel` por
pedido e um endpoint que serve o blob (autenticado por cookie/sessão). O botão
"Imprimir Etiqueta" só habilita quando existe blob pro pedido. A etapa de
transformação (visualização) grava em nf_etiqueta_arquivo; aqui só provamos
que a leitura/serving funciona e é gated por permissão.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, User, UserRole, UserStatus
from app.models.nf import NfEtiquetaArquivo

PERM_VIEW = {"controle_estoque": {"view": True, "edit": False, "delete": False}}

# PDF mínimo válido (header) — só pra provar que o blob volta byte-a-byte.
_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\n"


@pytest_asyncio.fixture
async def admin_view(db: AsyncSession) -> User:
    u = User(
        open_id=f"email:et-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"et-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def dois_pedidos(db: AsyncSession) -> None:
    """Dois pedidos enviados no mesmo dia; só um tem etiqueta transformada."""
    d = date(2026, 5, 28)
    db.add_all([
        BlingOrder(
            bling_id=920001, numero="920001", item_codigo="sku-com",
            item_index=0, situacao="15", em_andamento_data=d,
        ),
        BlingOrder(
            bling_id=920002, numero="920002", item_codigo="sku-sem",
            item_index=0, situacao="15", em_andamento_data=d,
        ),
    ])
    db.add(NfEtiquetaArquivo(
        pedido_bling="920001",
        filename="etiqueta_920001.pdf",
        content_type="application/pdf",
        size_bytes=len(_PDF_BYTES),
        blob=_PDF_BYTES,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_pedidos_flag_etiqueta_disponivel(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """A lista marca etiqueta_disponivel=True só pro pedido que tem blob."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-28&data_fim=2026-05-28"
    )
    assert r.status_code == 200, r.text
    by_numero = {p["pedido_bling"]: p for p in r.json()["data"]}
    assert by_numero["920001"]["etiqueta_disponivel"] is True
    assert by_numero["920002"]["etiqueta_disponivel"] is False
    # o horário de chegada acompanha o flag (é o created_at da linha)
    assert by_numero["920001"]["etiqueta_em"]
    assert by_numero["920002"]["etiqueta_em"] is None


@pytest.mark.asyncio
async def test_serve_etiqueta_blob(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """O endpoint devolve o blob exato + content-type + inline disposition."""
    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920001/etiqueta")
    assert r.status_code == 200, r.text
    assert r.content == _PDF_BYTES
    assert r.headers["content-type"].startswith("application/pdf")
    assert "etiqueta_920001.pdf" in r.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_serve_etiqueta_404(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """Pedido sem etiqueta transformada → 404 (a etapa de visualização não
    rodou pra ele)."""
    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920002/etiqueta")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "nf_etiqueta_nao_encontrada"


@pytest.mark.asyncio
async def test_etiqueta_requer_permissao(
    client: AsyncClient, db: AsyncSession,
    auth_as: Callable[[User | None], None], dois_pedidos: None,
):
    """Usuário sem controle_estoque.view não serve a etiqueta (403)."""
    outsider = User(
        open_id=f"email:out-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"out-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions={},
    )
    db.add(outsider)
    await db.commit()
    await db.refresh(outsider)
    auth_as(outsider)
    r = await client.get("/api/estoque/pedidos/920001/etiqueta")
    assert r.status_code == 403, r.text


def _pdf(*textos: str) -> bytes:
    """PDF com uma página por texto (marca por página pra rastrear ordem)."""
    import fitz  # PyMuPDF

    doc = fitz.open()
    for txt in textos:
        page = doc.new_page(width=300, height=442)
        page.insert_text((20, 20), txt, fontsize=10, fontname="helv")
    return doc.tobytes()


def _paginas_texto(pdf_bytes: bytes) -> list[str]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return [p.get_text().strip() for p in doc]


@pytest.mark.asyncio
async def test_serve_junta_etiqueta_com_nf(
    client: AsyncClient, db: AsyncSession, admin_view: User,
    auth_as: Callable[[User | None], None],
):
    """Fluxo correios/ML: quando há nf_pdf, o serve devolve etiqueta + NF juntadas
    (etiqueta primeiro, NF depois), sem mexer no botão."""
    db.add(BlingOrder(
        bling_id=920003, numero="920003", item_codigo="sku-c",
        item_index=0, situacao="15", em_andamento_data=date(2026, 5, 28),
    ))
    db.add(NfEtiquetaArquivo(
        pedido_bling="920003",
        filename="etiqueta_920003.pdf",
        content_type="application/pdf",
        size_bytes=1,
        blob=_pdf("ETIQUETA"),
        nf_pdf=_pdf("NOTA FISCAL"),
        nf_size_bytes=1,
    ))
    await db.commit()

    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920003/etiqueta")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert _paginas_texto(r.content) == ["ETIQUETA", "NOTA FISCAL"]


@pytest.mark.asyncio
async def test_serve_sem_nf_so_etiqueta(
    client: AsyncClient, db: AsyncSession, admin_view: User,
    auth_as: Callable[[User | None], None],
):
    """Sem nf_pdf (fluxo agência) o serve devolve só a etiqueta, intacta."""
    etq = _pdf("ETIQUETA")
    db.add(BlingOrder(
        bling_id=920004, numero="920004", item_codigo="sku-a",
        item_index=0, situacao="15", em_andamento_data=date(2026, 5, 28),
    ))
    db.add(NfEtiquetaArquivo(
        pedido_bling="920004",
        filename="etiqueta_920004.pdf",
        content_type="application/pdf",
        size_bytes=len(etq),
        blob=etq,
    ))
    await db.commit()

    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920004/etiqueta")
    assert r.status_code == 200, r.text
    assert r.content == etq
    assert _paginas_texto(r.content) == ["ETIQUETA"]


@pytest.mark.asyncio
async def test_serve_so_nf_sem_etiqueta_404(
    client: AsyncClient, db: AsyncSession, admin_view: User,
    auth_as: Callable[[User | None], None],
):
    """A NF chegou antes da etiqueta (blob vazio) → 404, não há o que colar."""
    db.add(BlingOrder(
        bling_id=920005, numero="920005", item_codigo="sku-n",
        item_index=0, situacao="15", em_andamento_data=date(2026, 5, 28),
    ))
    db.add(NfEtiquetaArquivo(
        pedido_bling="920005",
        filename="etiqueta_920005.pdf",
        content_type="application/pdf",
        size_bytes=0,
        blob=b"",
        nf_pdf=_pdf("NOTA FISCAL"),
        nf_size_bytes=1,
    ))
    await db.commit()

    auth_as(admin_view)
    r = await client.get("/api/estoque/pedidos/920005/etiqueta")
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "nf_etiqueta_nao_encontrada"


@pytest_asyncio.fixture
async def tres_com_etiqueta(db: AsyncSession) -> None:
    """Três pedidos com etiqueta; o do meio é do fluxo correios (tem NF)."""
    d = date(2026, 5, 29)
    for n, extra in (
        ("930001", {}),
        ("930002", {"nf_pdf": _pdf("NOTA 2"), "nf_size_bytes": 1}),
        ("930003", {}),
    ):
        db.add(BlingOrder(
            bling_id=int(n), numero=n, item_codigo=f"sku-{n}",
            item_index=0, situacao="15", em_andamento_data=d,
        ))
        db.add(NfEtiquetaArquivo(
            pedido_bling=n,
            filename=f"etiqueta_{n}.pdf",
            content_type="application/pdf",
            size_bytes=1,
            blob=_pdf(f"ETIQUETA {n[-1]}"),
            **extra,
        ))
    await db.commit()


@pytest.mark.asyncio
async def test_lote_junta_na_ordem_da_selecao(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], tres_com_etiqueta: None,
):
    """O PDF do lote sai na ordem enviada e embute a NF de quem é correios."""
    auth_as(admin_view)
    r = await client.post(
        "/api/estoque/pedidos/etiquetas",
        json={"pedidos": ["930003", "930002", "930001"]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["x-etiquetas-total"] == "3"
    assert _paginas_texto(r.content) == [
        "ETIQUETA 3", "ETIQUETA 2", "NOTA 2", "ETIQUETA 1",
    ]


@pytest.mark.asyncio
async def test_lote_carimba_impressao_e_expoe_na_lista(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], tres_com_etiqueta: None,
):
    """Imprimir o lote carimba `impressa_em` e a lista passa a mostrar — é o
    sinal que evita o operador imprimir o mesmo pedido duas vezes."""
    auth_as(admin_view)
    lista = "/api/estoque/pedidos?data_inicio=2026-05-29&data_fim=2026-05-29"
    antes = {p["pedido_bling"]: p for p in (await client.get(lista)).json()["data"]}
    assert antes["930001"]["etiqueta_impressa_em"] is None

    r = await client.post(
        "/api/estoque/pedidos/etiquetas", json={"pedidos": ["930001"]}
    )
    assert r.status_code == 200, r.text

    depois = {p["pedido_bling"]: p for p in (await client.get(lista)).json()["data"]}
    carimbo = depois["930001"]["etiqueta_impressa_em"]
    assert carimbo
    # Quem não entrou no lote continua sem carimbo.
    assert depois["930002"]["etiqueta_impressa_em"] is None

    # Reimprimir NÃO reescreve a data da primeira impressão.
    await client.post("/api/estoque/pedidos/etiquetas", json={"pedidos": ["930001"]})
    de_novo = {p["pedido_bling"]: p for p in (await client.get(lista)).json()["data"]}
    assert de_novo["930001"]["etiqueta_impressa_em"] == carimbo


@pytest.mark.asyncio
async def test_lote_ignora_sem_etiqueta_e_404_quando_nenhum(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], tres_com_etiqueta: None,
):
    """Pedido sem etiqueta é pulado; só 404 quando nenhum selecionado tem."""
    auth_as(admin_view)
    r = await client.post(
        "/api/estoque/pedidos/etiquetas",
        json={"pedidos": ["930001", "inexistente"]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["x-etiquetas-total"] == "1"

    r = await client.post(
        "/api/estoque/pedidos/etiquetas", json={"pedidos": ["inexistente"]}
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "nf_etiqueta_nao_encontrada"


@pytest.mark.asyncio
async def test_serve_individual_tambem_carimba(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], tres_com_etiqueta: None,
):
    """Abrir a etiqueta pelo botão de sempre também conta como impressão."""
    auth_as(admin_view)
    assert (await client.get("/api/estoque/pedidos/930003/etiqueta")).status_code == 200
    lista = "/api/estoque/pedidos?data_inicio=2026-05-29&data_fim=2026-05-29"
    rows = {p["pedido_bling"]: p for p in (await client.get(lista)).json()["data"]}
    assert rows["930003"]["etiqueta_impressa_em"]


@pytest_asyncio.fixture
async def pedido_dividido(db: AsyncSession) -> None:
    """Pedido que sai de dois armazéns (caso do 292860) + um de armazém único.

    A etiqueta é sempre servida INTEIRA — a tela só avisa "estoque
    compartilhado" antes de imprimir o dividido.
    """
    d = date(2026, 5, 30)
    for i, sku in enumerate(("a055.sa", "dg053.ci+a001.ci")):
        db.add(BlingOrder(
            bling_id=940001, numero="940001", item_codigo=sku,
            item_index=i, situacao="15", em_andamento_data=d,
        ))
    db.add(BlingOrder(
        bling_id=940002, numero="940002", item_codigo="a060.sa",
        item_index=0, situacao="15", em_andamento_data=d,
    ))
    etiqueta = _pdf("ETIQUETA DIVIDIDA")
    db.add(NfEtiquetaArquivo(
        pedido_bling="940001",
        filename="etiqueta_940001.pdf",
        content_type="application/pdf",
        size_bytes=len(etiqueta),
        blob=etiqueta,
    ))
    await db.commit()


@pytest_asyncio.fixture
async def user_sa(db: AsyncSession) -> User:
    """Operador cercado no armazém SA."""
    u = User(
        open_id=f"email:sa-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"sa-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        permissions=PERM_VIEW,
        stock_tags=["sa"],
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_lista_marca_estoque_compartilhado(
    client: AsyncClient, admin_view: User,
    auth_as: Callable[[User | None], None], pedido_dividido: None,
):
    """Pedido com itens de 2+ armazéns vem com estoque_compartilhado=True."""
    auth_as(admin_view)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-30&data_fim=2026-05-30"
    )
    assert r.status_code == 200, r.text
    by_numero = {p["pedido_bling"]: p for p in r.json()["data"]}
    assert by_numero["940001"]["estoque_compartilhado"] is True
    assert by_numero["940002"]["estoque_compartilhado"] is False


@pytest.mark.asyncio
async def test_flag_compartilhado_ignora_a_cerca_de_tag(
    client: AsyncClient, user_sa: User,
    auth_as: Callable[[User | None], None], pedido_dividido: None,
):
    """O operador cercado só vê o próprio item, mas o aviso considera o pedido
    inteiro — senão quem despacha do SA nunca saberia da divisão."""
    auth_as(user_sa)
    r = await client.get(
        "/api/estoque/pedidos?data_inicio=2026-05-30&data_fim=2026-05-30"
    )
    assert r.status_code == 200, r.text
    rows = [p for p in r.json()["data"] if p["pedido_bling"] == "940001"]
    assert rows, "operador do SA deveria ver o item dele"
    assert all(p["sku"] == "a055.sa" for p in rows)
    assert all(p["estoque_compartilhado"] is True for p in rows)


@pytest.mark.asyncio
async def test_serve_etiqueta_inteira_mesmo_pro_operador_cercado(
    client: AsyncClient, user_sa: User,
    auth_as: Callable[[User | None], None], pedido_dividido: None,
):
    """A etiqueta do pedido dividido sai INTEIRA (sem recorte por armazém)."""
    auth_as(user_sa)
    r = await client.get("/api/estoque/pedidos/940001/etiqueta")
    assert r.status_code == 200, r.text
    assert _paginas_texto(r.content) == ["ETIQUETA DIVIDIDA"]
