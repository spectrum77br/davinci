"""Etiqueta da Temu (Anjun Express) no fluxo automático — 18/11/2026.

A etiqueta da Anjun ("DANFE SIMPLIFICADO-ETIQUETA") não traz CPF, nem o nº
do pedido na Temu, nem declaração de conteúdo. O `/agent/etiqueta-lote` casa
pela chave da NF (`nf_nota`) ou por CEP + nome entre os pedidos de loja Temu,
e a etiqueta de pedido Temu sem declaração ganha a 2ª página no modelo do
`~/nf-executor/temu_declaracao.py` (lote e `/agent/etiqueta`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import fitz
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    BlingOrder,
    NfCommand,
    NfEtiquetaArquivo,
    NfFaturamento,
    NfNota,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import nf_emissao_gerar, nf_etiqueta_temu
from app.services.nf_etiqueta_declaracao import (
    ItemDeclaracao,
    assinatura_itens,
    formatar_item,
    ler_declaracao,
    reescrever_declaracao,
    total_declaracao,
)
from app.services.nf_etiqueta_temu import PedidoTemu, acrescentar_declaracao, pagina_declaracao
from app.services.nf_etiqueta_transform import transformar_etiqueta

_TOKEN = "nf-agent-test-token"  # noqa: S105
_CHAVE = "35261159882322000154550020000020281154723552"
_RASTREIO = "AJ261111162691701"
_LOJA_TEMU = "205925390"  # barbosa
_LOJA_SHOPEE = "205925391"
_LOJA_AMAZON = "205925392"
_DESC = "Mala Minecraft P6 tamanho 14 - Dourado (OM - PPB014 - Khaki)"


# --- PDFs sintéticos ----------------------------------------------------------


def _linhas_anjun(
    nome: str = "Veralucia Soares Goncalves Soares",
    cep: str = "35661-072",
    chave: str = _CHAVE,
    rastreio: str = _RASTREIO,
) -> list[str]:
    """Etiqueta da Anjun, nas linhas e na ordem que o PyMuPDF extrai do PDF
    real (`~/nf-executor/saidas/etiquetas/1111z/`)."""
    return [
        "DANFE SIMPLIFICADO-ETIQUETA",
        "Saída NF: 1",
        "Série: 1",
        "Emissão: 11-11-2026",
        chave,
        nome,
        "DESTINATÁRIO",
        f"CEP:{cep}",
        "Endereço:  Avenida Padre José Viegas/Recanto da Lagoa",
        rastreio,
        "MG-W-FQ002",
        f"Pedido: {rastreio[:15]}",
        "REMETENTE",
        "Madalena",
        "13400-820",
        "Nome:",
        "DOC:",
        "Anjun Express Log. e Transp. LTDA.",
    ]


def _pdf(paginas: list[list[str]]) -> bytes:
    doc = fitz.open()
    for linhas in paginas:
        page = doc.new_page(width=283, height=435)
        y = 20
        for linha in linhas:
            page.insert_text((12, y), linha, fontsize=8, fontname="helv")
            y += 14
    return doc.tobytes()


def _anjun(**kw) -> bytes:
    return _pdf([_linhas_anjun(**kw)])


# --- seeds ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _lojas(db: AsyncSession, admin: User) -> None:
    db.add_all(
        [
            StoreInfo(
                user_id=admin.id, platform="temu", account_name="barbosa", bling_store_id=_LOJA_TEMU
            ),
            StoreInfo(
                user_id=admin.id,
                platform="shopee",
                account_name="barbosa",
                bling_store_id=_LOJA_SHOPEE,
            ),
            StoreInfo(
                user_id=admin.id,
                platform="amazon",
                account_name="poofy",
                bling_store_id=_LOJA_AMAZON,
            ),
        ]
    )
    await db.flush()


_BLING_ID = iter(range(990001, 999999))


def _pedido(
    numero: str,
    *,
    loja: str = _LOJA_TEMU,
    nome: str | None = "VERALUCIA SOARES GONCALVES SOARES",
    cep: str | None = "35661072",
    situacao: str = "15",
    data: datetime | None = None,
    sku: str = "b101.14",
    descricao: str = _DESC,
    numeroloja: str | None = None,
) -> BlingOrder:
    return BlingOrder(
        bling_id=next(_BLING_ID),
        numero=numero,
        numeroloja=numeroloja or f"PO-029-0336759772735{numero}",
        data=data or datetime.now(UTC),
        loja=loja,
        situacao=situacao,
        item_index=0,
        item_codigo=sku,
        item_descricao=descricao,
        item_quantidade=1,
        itemvalor=100,
        nome_destinatario=nome,
        documento_destinatario="79882030610",
        cep_destino=cep,
    )


class _FakeBling:
    def __init__(self) -> None:
        self.chamadas: list[tuple[int, int]] = []

    async def update_order_situacao(self, bling_order_id: int, situacao_id: int) -> None:
        self.chamadas.append((bling_order_id, situacao_id))


@pytest.fixture
def fake_bling(monkeypatch: pytest.MonkeyPatch) -> _FakeBling:
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    fake = _FakeBling()

    async def _cliente(_session):
        return fake

    monkeypatch.setattr(nf_emissao_gerar, "_bling_client_opt", _cliente)
    return fake


async def _lote(client: AsyncClient, pdf: bytes) -> dict:
    r = await client.post(
        "/api/nf-cadastro/agent/etiqueta-lote",
        files={"file": ("lote.pdf", pdf, "application/pdf")},
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _blob(db: AsyncSession, numero: str) -> bytes:
    db.expire_all()
    return (
        await db.execute(
            select(NfEtiquetaArquivo.blob).where(NfEtiquetaArquivo.pedido_bling == numero)
        )
    ).scalar_one()


# --- declaração (puro) ----------------------------------------------------------

_PED = PedidoTemu(
    numero="309001",
    numeroloja="PO-029-03367597727351782",
    conta="barbosa",
    nome="VERALUCIA SOARES GONCALVES SOARES",
    documento="798.820.306-10",
    cep="35661072",
    situacao="15",
)
_ITENS = [formatar_item("b101.14", _DESC, 1)]
_AGORA = datetime(2026, 11, 18, 10, 30)


def test_pagina_declaracao_no_modelo_do_script():
    """Mesmo modelo do `temu_declaracao.pagina_declaracao`: cabeçalho com o
    pedido, a Temu e o PO; rastreio; remetente "Temu - <Conta>"; destinatário
    com nome/CPF/CEP; SKU, descrição, variação e quantidade; Total."""
    doc = fitz.open()
    pagina_declaracao(doc, 283, 435, pedido=_PED, itens=_ITENS, rastreio=_RASTREIO, agora=_AGORA)
    txt = doc[0].get_text()
    assert "Pedido 309001 · Temu PO-029-03367597727351782 · 18/11/2026 10:30" in txt
    assert "DECLARAÇÃO DE CONTEÚDO" in txt
    assert f"Código de Rastreamento: {_RASTREIO}" in txt
    assert "NOME: Temu - Barbosa" in txt
    # nome comprido quebra dentro da célula (não invade a coluna do lado)
    assert "NOME: VERALUCIA SOARES GONCALVES\nSOARES" in txt
    assert "CPF/CNPJ: 79882030610" in txt
    assert "CEP: 35661072" in txt
    pdf = doc.tobytes()
    assert ler_declaracao(pdf) == [
        ItemDeclaracao("b101.14", "Mala Minecraft P6 tamanho 14", "Dourado", 1)
    ]
    assert total_declaracao(pdf) == 1


def test_acrescentar_declaracao_atras_da_etiqueta():
    """Etiqueta transformada + página da declaração atrás (mesmo tamanho),
    remetente = destinatário (regra 1 da visualização), marca de
    idempotência gravada — a impressão devolve `igual` e, com item trocado
    no Bling, reescreve a tabela (layout "nossa")."""
    etiqueta = transformar_etiqueta(_anjun(), "VERALUCIA SOARES GONCALVES SOARES")
    out, status, _det = acrescentar_declaracao(etiqueta, _PED, _ITENS, agora=_AGORA)
    assert status == "ok"
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 2
    assert doc[1].rect == doc[0].rect
    txt = doc[1].get_text()
    assert f"Código de Rastreamento: {_RASTREIO}" in txt
    assert "Temu - Barbosa" not in txt  # o remetente não expõe a loja
    assert txt.count("NOME: Veralucia Soares Goncalves Soares") == 2  # nome do papel
    assert f"davinci-decl:{assinatura_itens(_ITENS)}" in doc.metadata["keywords"]
    assert reescrever_declaracao(out, _ITENS)[1] == "igual"
    novos = [formatar_item("a076", "Cadeado TSA - Preto", 3)]
    out2, st2, _ = reescrever_declaracao(out, novos)
    assert st2 == "ok"
    assert ler_declaracao(out2) == novos
    assert total_declaracao(out2) == 3


def test_acrescentar_declaracao_sem_itens_nao_mexe():
    etiqueta = _anjun()
    assert acrescentar_declaracao(etiqueta, _PED, []) == (etiqueta, "sem_itens", "")


def test_acrescentar_declaracao_muitos_itens_a_pagina_cresce():
    """Itens que não cabem na altura da etiqueta: a página cresce (a
    impressão redimensiona) em vez de cortar a tabela."""
    itens = [formatar_item(f"b10{i}.14", _DESC, 1) for i in range(30)]
    out, status, _ = acrescentar_declaracao(_anjun(), _PED, itens, agora=_AGORA)
    assert status == "ok"
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc[1].rect.height > 435
    assert len(ler_declaracao(out)) == 30
    assert total_declaracao(out) == 30


# --- lote: casamento ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_lote_casa_etiqueta_temu_por_cep_nome_e_grava_com_declaracao(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """Etiqueta da Anjun casa no pedido Temu pelo CEP + nome; a fatia sai com
    2 páginas (etiqueta + declaração), status_etiqueta='ok', o comando
    `imprimir_etiqueta` fecha e o pedido vai pra 21 no Bling."""
    await _lojas(db, admin)
    db.add(_pedido("309001"))
    db.add(_pedido("309002", nome="Maria Aparecida Silva", cep="01001000"))
    db.add(
        NfCommand(
            action="imprimir_etiqueta",
            numeros=["309001"],
            planilha=b"",
            nome_arquivo="",
            status="pending",
            source="auto",
        )
    )
    await db.commit()

    body = await _lote(client, _anjun())
    assert body["casadas"] == ["309001"]
    assert body["nao_casadas"] == []
    assert body["falhas"] == []
    assert body["comandos_resolvidos"] == 1

    blob = await _blob(db, "309001")
    doc = fitz.open(stream=blob, filetype="pdf")
    assert doc.page_count == 2  # etiqueta + declaração
    assert _CHAVE not in doc[0].get_text()  # o transform continua valendo (sem NF)
    txt = doc[1].get_text()
    assert "DECLARAÇÃO DE CONTEÚDO" in txt
    assert "Pedido 309001 · Temu PO-029-" in txt
    assert f"Código de Rastreamento: {_RASTREIO}" in txt
    assert "CPF/CNPJ: 79882030610" in txt
    assert ler_declaracao(blob) == [
        ItemDeclaracao("b101.14", "Mala Minecraft P6 tamanho 14", "Dourado", 1)
    ]

    fat = (
        await db.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == "309001"))
    ).scalar_one()
    assert fat.status_etiqueta == "ok"
    assert [s for _id, s in fake_bling.chamadas] == [21]


@pytest.mark.asyncio
async def test_lote_dois_temu_no_mesmo_cep_desempata_pelo_nome(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    await _lojas(db, admin)
    db.add(_pedido("309001", nome="Vera Lucia Pereira"))
    db.add(_pedido("309002"))  # VERALUCIA SOARES GONCALVES SOARES, mesmo CEP
    await db.commit()

    body = await _lote(
        client,
        _pdf(
            [
                _linhas_anjun(),
                _linhas_anjun(nome="Vera Lúcia Pereira", rastreio="AJ261111162691702"),
            ]
        ),
    )
    assert body["casadas"] == ["309002", "309001"]
    assert body["nao_casadas"] == []


@pytest.mark.asyncio
async def test_lote_ambiguo_nao_casa(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """Mesmo cliente com 2 pedidos Temu no mesmo CEP → fica pra mão."""
    await _lojas(db, admin)
    db.add(_pedido("309001"))
    db.add(_pedido("309005"))
    await db.commit()

    body = await _lote(client, _anjun())
    assert body["casadas"] == []
    assert len(body["nao_casadas"]) == 1
    assert body["nao_casadas"][0]["numeroloja"] == "AJ2611111626917"
    assert fake_bling.chamadas == []


@pytest.mark.asyncio
async def test_lote_pedido_shopee_no_mesmo_cep_e_ignorado(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """O casamento por CEP + nome é SÓ entre pedidos Temu: o mesmo cliente
    com pedido na Shopee não casa — e não desempata o Temu também."""
    await _lojas(db, admin)
    db.add(_pedido("309010", loja=_LOJA_SHOPEE, numeroloja="261111ABCDEFGH"))
    await db.commit()
    body = await _lote(client, _anjun())
    assert body["casadas"] == []
    assert len(body["nao_casadas"]) == 1

    db.add(_pedido("309001"))  # agora com o Temu: casa nele, não no da Shopee
    await db.commit()
    body = await _lote(client, _anjun())
    assert body["casadas"] == ["309001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("situacao", ["12", "83955"])
async def test_lote_temu_cancelado_nao_casa(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling, situacao: str
):
    """Cancelado (12) e Aguardando Cancelamento (83955) ficam de fora — gravar
    a etiqueta mandaria o pedido pra 21 no Bling."""
    await _lojas(db, admin)
    db.add(_pedido("309001", situacao=situacao))
    await db.commit()
    body = await _lote(client, _anjun())
    assert body["casadas"] == []
    assert len(body["nao_casadas"]) == 1
    assert fake_bling.chamadas == []


@pytest.mark.asyncio
async def test_lote_temu_fora_da_janela_nao_casa_por_cep(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    await _lojas(db, admin)
    db.add(_pedido("309001", data=datetime.now(UTC) - timedelta(days=40)))
    await db.commit()
    body = await _lote(client, _anjun())
    assert body["casadas"] == []


@pytest.mark.asyncio
async def test_lote_casa_pela_chave_da_nf_antes_do_cep(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """Com o XML já no `nf_nota`, a chave impressa decide — inclusive o caso
    que por CEP + nome seria ambíguo (mesmo cliente, 2 pedidos)."""
    await _lojas(db, admin)
    db.add(_pedido("309001"))
    db.add(_pedido("309005"))
    db.add(NfNota(chave=_CHAVE, pedido_bling="309005", numero="2028", serie="2", xml=b"<x/>"))
    await db.commit()
    body = await _lote(client, _anjun())
    assert body["casadas"] == ["309005"]
    assert fitz.open(stream=await _blob(db, "309005"), filetype="pdf").page_count == 2


@pytest.mark.asyncio
async def test_lote_chave_de_pedido_que_nao_e_temu_nao_casa(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """A chave só vale pra pedido Temu fora de 12/83955."""
    await _lojas(db, admin)
    db.add(_pedido("309010", loja=_LOJA_SHOPEE, numeroloja="261111ABCDEFGH"))
    db.add(_pedido("309011", situacao="12", nome="Outra Pessoa"))
    db.add(NfNota(chave=_CHAVE, pedido_bling="309010", numero="1", xml=b"<x/>"))
    db.add(NfNota(chave="1" * 44, pedido_bling="309011", numero="2", xml=b"<x/>"))
    await db.commit()
    body = await _lote(client, _pdf([_linhas_anjun(), _linhas_anjun(chave="1" * 44)]))
    assert body["casadas"] == []
    assert len(body["nao_casadas"]) == 2


@pytest.mark.asyncio
async def test_lote_consulta_temu_que_falha_nao_derruba_o_lote(
    db: AsyncSession,
    client: AsyncClient,
    admin: User,
    fake_bling: _FakeBling,
    monkeypatch: pytest.MonkeyPatch,
):
    """Erro de banco na consulta dos pedidos Temu: a etiqueta só não casa
    (como antes) — o lote responde normal."""
    from sqlalchemy import text

    async def _aborta(session, **_kw):
        await session.execute(text("SELECT 1/0"))
        return {}

    await _lojas(db, admin)
    db.add(_pedido("309001"))
    await db.commit()
    monkeypatch.setattr(nf_etiqueta_temu, "_pedidos", _aborta)
    body = await _lote(client, _anjun())
    assert body["casadas"] == []
    assert len(body["nao_casadas"]) == 1


@pytest.mark.asyncio
async def test_lote_nao_temu_sem_declaracao_nao_ganha_pagina(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """Etiqueta SEM declaração de pedido que não é Temu (Amazon/SEDEX) segue
    como antes: 1 página, sem a declaração da Temu."""
    await _lojas(db, admin)
    db.add(_pedido("309020", loja=_LOJA_AMAZON, numeroloja="701-1234567-1234567"))
    await db.commit()
    body = await _lote(client, _pdf([["DESTINATÁRIO", "Fulano", "Pedido: 701-1234567-1234567"]]))
    assert body["casadas"] == ["309020"]
    assert fitz.open(stream=await _blob(db, "309020"), filetype="pdf").page_count == 1


# --- upload por pedido (/agent/etiqueta) --------------------------------------------


async def _upload(client: AsyncClient, numero: str, pdf: bytes) -> None:
    r = await client.post(
        "/api/nf-cadastro/agent/etiqueta",
        data={"pedido_bling": numero},
        files={"file": ("etq.pdf", pdf, "application/pdf")},
        headers={"X-Agent-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_upload_por_pedido_temu_ganha_declaracao(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    await _lojas(db, admin)
    db.add(_pedido("309001"))
    await db.commit()
    await _upload(client, "309001", _anjun())
    blob = await _blob(db, "309001")
    assert fitz.open(stream=blob, filetype="pdf").page_count == 2
    assert [i.sku for i in ler_declaracao(blob)] == ["b101.14"]


@pytest.mark.asyncio
async def test_upload_por_pedido_nao_temu_sem_declaracao_fica_como_esta(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    await _lojas(db, admin)
    db.add(_pedido("309020", loja=_LOJA_AMAZON, numeroloja="701-1234567-1234567"))
    await db.commit()
    await _upload(client, "309020", _anjun())
    assert fitz.open(stream=await _blob(db, "309020"), filetype="pdf").page_count == 1


@pytest.mark.asyncio
async def test_upload_temu_que_ja_vem_com_declaracao_nao_duplica(
    db: AsyncSession, client: AsyncClient, admin: User, fake_bling: _FakeBling
):
    """Fluxo manual (o PDF já sai do `temu_declaracao.py` com a 2ª página):
    a declaração é reescrita com o item do Bling, sem 3ª página."""
    from tests.declaracao_sintetica import declaracao_nossa

    await _lojas(db, admin)
    db.add(_pedido("309001"))
    await db.commit()
    await _upload(client, "309001", declaracao_nossa([("x999", "Outro Produto - Azul", 1)]))
    blob = await _blob(db, "309001")
    assert fitz.open(stream=blob, filetype="pdf").page_count == 2
    assert [i.sku for i in ler_declaracao(blob)] == ["b101.14"]
