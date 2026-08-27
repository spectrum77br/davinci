"""O XML autorizado da NF-e vira registro no davinci (`nf_nota`).

O robô da nuvem baixa, pra cada nota emitida, o XML assinado que a SEFAZ
devolveu. O coletor só transporta esse arquivo pra `POST /agent/nf-xml`, que lê
os campos e guarda o XML inteiro. O número do pedido do Bling NÃO está no XML
(o `<xPed>` é o número interno do Upseller), então o casamento vai pelo CPF/CNPJ
do destinatário — e, se houver mais de um candidato, pelo nome.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingOrder, NfNota
from app.services import nf_xml

_TOKEN = "nf-agent-test-token"
_CHAVE = "35260867773725000193550020000002521674103009"


def _xml(
    *,
    chave: str = _CHAVE,
    numero: str = "252",
    doc_tag: str = "CPF",
    doc: str = "01197255419",
    dest_nome: str = "Joel Fernandes Bezerra",
    valor: str = "17.20",
    com_protocolo: bool = True,
) -> bytes:
    """Um `<nfeProc>` reduzido, com a mesma forma do arquivo real do Upseller."""
    prot = (
        f"<protNFe versao='4.00'><infProt><chNFe>{chave}</chNFe>"
        "<nProt>135263554419629</nProt><cStat>100</cStat>"
        "<xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>"
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<nfeProc versao='4.00' xmlns='http://www.portalfiscal.inf.br/nfe'>"
        f"<NFe><infNFe Id='NFe{chave}' versao='4.00'>"
        f"<ide><cUF>35</cUF><mod>55</mod><serie>2</serie><nNF>{numero}</nNF>"
        "<dhEmi>2026-08-27T16:51:31-03:00</dhEmi></ide>"
        "<emit><CNPJ>67773725000193</CNPJ>"
        "<xNome>VORTAN INTERMEDIACOES LTDA</xNome></emit>"
        f"<dest><{doc_tag}>{doc}</{doc_tag}><xNome>{dest_nome}</xNome></dest>"
        "<det nItem='1'><prod><cProd>e3</cProd><xProd>EMBALAGEM</xProd>"
        f"<vProd>{valor}</vProd><xPed>UP2NYY224934</xPed></prod></det>"
        f"<total><ICMSTot><vProd>{valor}</vProd><vNF>{valor}</vNF></ICMSTot></total>"
        "</infNFe></NFe>"
        f"{prot if com_protocolo else ''}"
        "</nfeProc>"
    ).encode()


def test_parse_nota_autorizada():
    nota = nf_xml.parse_nfe(_xml())
    assert nota.chave == _CHAVE
    assert nota.numero == "252"
    assert nota.serie == "2"
    assert nota.emitente_cnpj == "67773725000193"
    assert nota.emitente_nome == "VORTAN INTERMEDIACOES LTDA"
    assert nota.destinatario_doc == "01197255419"
    assert nota.destinatario_nome == "Joel Fernandes Bezerra"
    assert nota.valor == Decimal("17.20")
    assert nota.data_emissao is not None
    assert nota.protocolo == "135263554419629"
    assert nota.situacao == "100"  # autorizada
    assert nota.situacao_motivo == "Autorizado o uso da NF-e"
    # <xPed> é o pedido do UPSELLER, não o do Bling
    assert nota.upseller_pedido == "UP2NYY224934"


def test_parse_nota_sem_protocolo():
    """Nota ainda não transmitida: sem `<protNFe>`, mas o resto continua legível."""
    nota = nf_xml.parse_nfe(_xml(com_protocolo=False))
    assert nota.chave == _CHAVE
    assert nota.protocolo is None
    assert nota.situacao is None


def test_parse_destinatario_pessoa_juridica():
    nota = nf_xml.parse_nfe(_xml(doc_tag="CNPJ", doc="52.223.327/0001-26"))
    assert nota.destinatario_doc == "52223327000126"  # só dígitos


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"nao e xml", "nf_xml_invalido"),
        (b"<outraCoisa/>", "nf_xml_sem_nfe"),
        (
            b"<nfeProc xmlns='http://www.portalfiscal.inf.br/nfe'>"
            b"<NFe><infNFe Id='NFe123'><ide><nNF>1</nNF></ide></infNFe></NFe>"
            b"</nfeProc>",
            "nf_xml_sem_chave",
        ),
        (
            f"<nfeProc xmlns='http://www.portalfiscal.inf.br/nfe'><NFe>"
            f"<infNFe Id='NFe{_CHAVE}'><ide/></infNFe></NFe></nfeProc>".encode(),
            "nf_xml_sem_numero",
        ),
    ],
)
def test_parse_recusa_xml_ruim(raw: bytes, code: str):
    with pytest.raises(nf_xml.NfXmlError) as exc:
        nf_xml.parse_nfe(raw)
    assert exc.value.code == code


async def _seed_pedido(
    db: AsyncSession,
    *,
    numero: str,
    doc: str,
    nome: str,
    bling_id: int,
    dias_atras: int = 1,
) -> None:
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=numero,
            data=datetime.now(UTC) - timedelta(days=dias_atras),
            loja="205527077",
            situacao="15",
            item_index=0,
            item_codigo="e3",
            item_descricao="EMBALAGEM",
            item_quantidade=1,
            itemvalor=17.2,
            nome_destinatario=nome,
            documento_destinatario=doc,
        )
    )
    await db.commit()


async def _subir(client: AsyncClient, raw: bytes):
    return await client.post(
        "/api/nf-cadastro/agent/nf-xml",
        files={"file": (f"{_CHAVE}.xml", raw, "application/xml")},
        headers={"X-Agent-Token": _TOKEN},
    )


@pytest.mark.asyncio
async def test_nf_xml_sem_token(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    r = await client.post(
        "/api/nf-cadastro/agent/nf-xml",
        files={"file": ("x.xml", _xml(), "application/xml")},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_nf_xml_casa_pelo_documento(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Um único pedido com aquele CPF na janela → a nota casa com ele."""
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    await _seed_pedido(
        db,
        numero="292604",
        doc="01197255419",
        nome="Joel Fernandes Bezerra",
        bling_id=900001,
    )

    r = await _subir(client, _xml())
    assert r.status_code == 200, r.text
    assert r.json()["pedido_bling"] == "292604"

    row = (
        await db.execute(select(NfNota).where(NfNota.chave == _CHAVE))
    ).scalar_one()
    assert row.pedido_bling == "292604"
    assert row.numero == "252"
    assert row.valor == Decimal("17.20")
    assert row.xml.startswith(b"<?xml")  # o arquivo assinado fica guardado


@pytest.mark.asyncio
async def test_nf_xml_desempata_pelo_nome(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Mesmo CPF em dois pedidos: o nome do destinatário decide."""
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    await _seed_pedido(
        db, numero="292604", doc="01197255419",
        nome="joel  fernandes bezerra", bling_id=900001,
    )
    await _seed_pedido(
        db, numero="292605", doc="01197255419",
        nome="Outra Pessoa", bling_id=900002,
    )

    r = await _subir(client, _xml())
    assert r.status_code == 200, r.text
    assert r.json()["pedido_bling"] == "292604"


@pytest.mark.asyncio
async def test_nf_xml_ambiguo_fica_sem_pedido(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Dois pedidos com o mesmo CPF E o mesmo nome: guarda a nota sem pedido em
    vez de chutar. Uma passada futura tenta de novo."""
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    await _seed_pedido(
        db, numero="292604", doc="01197255419",
        nome="Joel Fernandes Bezerra", bling_id=900001,
    )
    await _seed_pedido(
        db, numero="292605", doc="01197255419",
        nome="Joel Fernandes Bezerra", bling_id=900002,
    )

    r = await _subir(client, _xml())
    assert r.status_code == 200, r.text
    assert r.json()["pedido_bling"] is None

    row = (
        await db.execute(select(NfNota).where(NfNota.chave == _CHAVE))
    ).scalar_one()
    assert row.pedido_bling is None
    assert row.numero == "252"  # a nota entrou mesmo sem casar


@pytest.mark.asyncio
async def test_nf_xml_ignora_pedido_antigo(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Pedido do mesmo CPF fora da janela não é candidato — evita casar a nota
    de hoje com uma compra de meses atrás."""
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    await _seed_pedido(
        db, numero="270001", doc="01197255419",
        nome="Joel Fernandes Bezerra", bling_id=900001, dias_atras=90,
    )

    r = await _subir(client, _xml())
    assert r.status_code == 200, r.text
    assert r.json()["pedido_bling"] is None


@pytest.mark.asyncio
async def test_nf_xml_resubida_atualiza_mesma_linha(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A chave de acesso é a identidade da nota: subir o mesmo XML de novo
    atualiza a linha, não duplica. E o casamento só avança — uma 2ª passada que
    não decide não apaga o pedido já achado."""
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    r1 = await _subir(client, _xml())
    assert r1.status_code == 200, r1.text
    assert r1.json()["pedido_bling"] is None  # nenhum pedido ainda

    await _seed_pedido(
        db, numero="292604", doc="01197255419",
        nome="Joel Fernandes Bezerra", bling_id=900001,
    )
    r2 = await _subir(client, _xml(numero="253"))
    assert r2.status_code == 200, r2.text
    assert r2.json()["pedido_bling"] == "292604"

    db.expire_all()  # o request comitou em outra sessão
    rows = (
        await db.execute(select(NfNota).where(NfNota.chave == _CHAVE))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].numero == "253"
    assert rows[0].pedido_bling == "292604"


@pytest.mark.asyncio
async def test_nf_xml_vazio_e_invalido(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)
    r = await _subir(client, b"")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "nf_xml_vazio"

    r = await _subir(client, b"isso nao e xml")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "nf_xml_invalido"
