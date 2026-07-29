"""Camada de ARQUIVO da emissão pelo BLING de DESTINO (Fase 3a-4).

`nf_bling_xml.montar_xml(pedido, linhas)` monta UM `<pedido>` no layout oficial
do importador de Pedido XML do Bling (a tela só aceita XML). Como é 1 pedido por
arquivo, `montar_zip(pedidos)` empacota N XMLs num .zip que a marionete
descompacta e importa um a um.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from xml.etree.ElementTree import fromstring

from app.services import nf_bling_xml
from app.services.nf_emissao import NfLinha
from app.services.nf_relatorio import PedidoInfo


def _pedido() -> tuple[PedidoInfo, list[NfLinha]]:
    info = PedidoInfo(
        numero="830001",
        data=None,
        nome_destinatario="Cleso Menezes",
        cep_destino="30570050",
        endereco_destino="Rua Emídio Beruto",
        numero_destino="30",
        complemento_destino="Apto 2",
        bairro_destino="Cinquentenário",
        cidade_destino="Belo Horizonte",
        uf_destino="MG",
        documento="12345678901",
        tipo_pessoa="F",
        telefone="31999990000",
    )
    linhas = [
        NfLinha(sku="a001", nome="embalagem", ncm=None,
                quantidade=2, valor_unitario=Decimal("161.00"), valor_total=Decimal("322.00")),
        NfLinha(sku="a002", nome="capa", ncm=None,
                quantidade=1, valor_unitario=Decimal("50.00"), valor_total=Decimal("50.00")),
    ]
    return info, linhas


def test_xml_cliente_e_itens():
    info, linhas = _pedido()
    root = fromstring(nf_bling_xml.montar_xml(info, linhas))
    assert root.tag == "pedido"

    cliente = root.find("cliente")
    assert cliente.findtext("nome") == "Cleso Menezes"
    assert cliente.findtext("tipoPessoa") == "F"
    # cpf_cnpj só dígitos
    assert cliente.findtext("cpf_cnpj") == "12345678901"
    assert cliente.findtext("endereco") == "Rua Emídio Beruto"
    assert cliente.findtext("numero") == "30"
    assert cliente.findtext("complemento") == "Apto 2"
    assert cliente.findtext("bairro") == "Cinquentenário"
    assert cliente.findtext("cep") == "30570050"
    assert cliente.findtext("cidade") == "Belo Horizonte"
    assert cliente.findtext("uf") == "MG"
    assert cliente.findtext("fone") == "31999990000"

    itens = root.find("itens").findall("item")
    assert len(itens) == 2
    assert itens[0].findtext("codigo") == "a001"
    assert itens[0].findtext("descricao") == "embalagem"
    assert itens[0].findtext("un") == "UN"
    assert itens[0].findtext("qtde") == "2"
    # valor com PONTO decimal (o XML do Bling usa ponto, não vírgula)
    assert itens[0].findtext("vlr_unit") == "161.00"
    assert itens[1].findtext("codigo") == "a002"
    assert itens[1].findtext("vlr_unit") == "50.00"

    # obs carrega o número do pedido de origem
    assert root.findtext("obs") == "830001"


def test_tipo_pessoa_infere_por_documento():
    # sem tipoPessoa, infere pelo tamanho do documento
    assert nf_bling_xml._tipo_pessoa(None, "12345678000199") == "J"
    assert nf_bling_xml._tipo_pessoa(None, "12345678901") == "F"
    assert nf_bling_xml._tipo_pessoa("J", "12345678901") == "J"
    # sem documento cai em F
    assert nf_bling_xml._tipo_pessoa(None, None) == "F"


def test_tags_vazias_sao_omitidas():
    info = PedidoInfo(numero="900001", data=None, nome_destinatario="Fulano")
    root = fromstring(nf_bling_xml.montar_xml(info, []))
    cliente = root.find("cliente")
    # nome e tipoPessoa sempre presentes; campos vazios não geram tag
    assert cliente.findtext("nome") == "Fulano"
    assert cliente.find("cpf_cnpj") is None
    assert cliente.find("endereco") is None
    assert cliente.find("cep") is None


def test_zip_um_xml_por_pedido():
    p1 = _pedido()
    info2 = PedidoInfo(numero="830002", data=None, nome_destinatario="Outro")
    linhas2 = [NfLinha(sku="x1", nome="Produto X", ncm=None,
                       quantidade=1, valor_unitario=Decimal("10.00"),
                       valor_total=Decimal("10.00"))]
    blob = nf_bling_xml.montar_zip([p1, (info2, linhas2)])
    assert blob.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        nomes = sorted(zf.namelist())
        assert nomes == ["pedido_830001.xml", "pedido_830002.xml"]
        r1 = fromstring(zf.read("pedido_830001.xml"))
        assert r1.find("cliente").findtext("nome") == "Cleso Menezes"
        r2 = fromstring(zf.read("pedido_830002.xml"))
        assert r2.find("cliente").findtext("nome") == "Outro"
