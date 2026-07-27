"""Camada de ARQUIVO da emissão — monta a planilha de importação avulsa no
layout do relatório de vendas do Bling (41 colunas, `;`, BR, BOM).

Casa `NfLinha` (já transformada pelo motor) + cabeçalho do pedido nas colunas
certas; garante formato numérico BR e o cabeçalho verbatim que o importador do
Bling espera.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from app.services import nf_relatorio
from app.services.nf_emissao import NfLinha
from app.services.nf_relatorio import COLUNAS, PedidoInfo


def _linha(sku="a001", nome="embalagem", qtd=1, unit="910.00", total="910.00") -> NfLinha:
    return NfLinha(
        sku=sku, nome=nome, ncm="4202.12.10", quantidade=qtd,
        valor_unitario=Decimal(unit), valor_total=Decimal(total),
    )


def _pedido() -> PedidoInfo:
    return PedidoInfo(
        numero="52",
        data=date(2026, 6, 23),
        nome_destinatario="Cleso Menezes Da Silva",
        cep_destino="30570050",
        endereco_destino="Rua Emídio Beruto",
        numero_destino="30",
        complemento_destino="casa fundo",
        bairro_destino="Cinquentenário",
        cidade_destino="Belo Horizonte",
        uf_destino="MG",
    )


def _col(row: list[str], nome: str) -> str:
    return row[COLUNAS.index(nome)]


def test_cabecalho_tem_41_colunas_verbatim():
    assert len(COLUNAS) == 41
    assert COLUNAS[0] == "Número pedido"
    assert COLUNAS[14] == "Produto"
    assert COLUNAS[15] == "SKU"
    assert COLUNAS[-1] == "ID Forma Pagamento"


def test_linha_mapeia_produto_sku_e_destinatario():
    rows = nf_relatorio.montar_linhas(_pedido(), [_linha()])
    assert len(rows) == 1
    r = rows[0]
    assert _col(r, "Número pedido") == "52"
    assert _col(r, "Data") == "23/06/2026"
    assert _col(r, "Produto") == "embalagem"   # nome fonte
    assert _col(r, "SKU") == "a001"            # sku fonte
    assert _col(r, "Un") == "UN"
    assert _col(r, "Nome Comprador") == "Cleso Menezes Da Silva"
    # destinatário preenche Comprador E Entrega
    assert _col(r, "Nome Entrega") == "Cleso Menezes Da Silva"
    assert _col(r, "Cidade Entrega") == "Belo Horizonte"
    assert _col(r, "UF Comprador") == "MG"
    assert _col(r, "CEP Comprador") == "30570050"
    # não temos dados de comprador crus
    assert _col(r, "CPF/CNPJ Comprador") == ""
    assert _col(r, "Telefone Comprador") == ""


def test_numeros_em_formato_br():
    rows = nf_relatorio.montar_linhas(
        _pedido(), [_linha(qtd=2, unit="1003.37", total="2006.74")]
    )
    r = rows[0]
    assert _col(r, "Quantidade") == "2,00"
    assert _col(r, "Valor Unitário") == "1.003,37"   # milhar com ponto
    assert _col(r, "Valor Total") == "2.006,74"
    assert _col(r, "Valor Frete Pedido") == "0,00"
    assert _col(r, "Qtd Parcela") == "1"


def test_total_pedido_soma_das_linhas():
    linhas = [
        _linha(sku="a", unit="100.00", total="100.00"),
        _linha(sku="b", qtd=2, unit="200.00", total="400.00"),
    ]
    rows = nf_relatorio.montar_linhas(_pedido(), linhas)
    # Total Pedido repetido em todas as linhas = 500,00
    assert _col(rows[0], "Total Pedido") == "500,00"
    assert _col(rows[1], "Total Pedido") == "500,00"
    assert _col(rows[0], "SKU") == "a"
    assert _col(rows[1], "SKU") == "b"


def test_csv_bom_ponto_e_virgula_aspas_e_cabecalho():
    data = nf_relatorio.montar_csv([(_pedido(), [_linha()])])
    assert data.startswith(b"\xef\xbb\xbf")  # BOM UTF-8
    texto = data.decode("utf-8-sig")
    linhas = texto.splitlines()
    assert linhas[0].startswith('"Número pedido";"Nome Comprador";')
    # relê com csv pra provar que casa o layout
    reader = list(csv.reader(io.StringIO(texto), delimiter=";"))
    assert reader[0] == COLUNAS
    assert reader[1][COLUNAS.index("SKU")] == "a001"
    assert reader[1][COLUNAS.index("Valor Total")] == "910,00"


def test_csv_multiplos_pedidos():
    p2 = PedidoInfo(numero="53", data=date(2026, 6, 24), nome_destinatario="Fulano")
    data = nf_relatorio.montar_csv([
        (_pedido(), [_linha(sku="a")]),
        (p2, [_linha(sku="b"), _linha(sku="c")]),
    ])
    reader = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    # 1 cabeçalho + 1 + 2 = 4 linhas
    assert len(reader) == 4
    assert reader[1][COLUNAS.index("Número pedido")] == "52"
    assert reader[2][COLUNAS.index("Número pedido")] == "53"
    assert reader[3][COLUNAS.index("Número pedido")] == "53"
