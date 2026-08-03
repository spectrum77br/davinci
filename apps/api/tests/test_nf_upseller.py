"""Camada de ARQUIVO da emissão pelo UPSELLER (Fase 3a-4).

`nf_upseller.montar_xlsx(loja_nome, pedidos)` monta o .xlsx no template do
Upseller (aba `order_`, 44 colunas): linhas 1-3 são o cabeçalho do modelo e da
linha 4 em diante um item por linha. O destinatário (obrigatório pra NF-e) só
vai na 1ª linha de cada pedido — o Upseller unifica os itens pelo par Nome da
Loja + Nº do Pedido.
"""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import load_workbook

from app.services import nf_upseller
from app.services.nf_emissao import NfLinha
from app.services.nf_relatorio import PedidoInfo

_HEADERS = nf_upseller._HEADERS


def _c(ws, row: int, header: str):
    return ws.cell(row=row, column=_HEADERS.index(header) + 1).value


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


def test_xlsx_cabecalho_no_template():
    xlsx = nf_upseller.montar_xlsx("Loja Avulsa", [_pedido()])
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb.active
    assert ws.title == "order_"
    # linha 3 = os nomes dos campos que o Upseller casa (verbatim, na ordem).
    header = [ws.cell(row=3, column=i + 1).value for i in range(len(_HEADERS))]
    assert header == _HEADERS


def test_xlsx_primeira_linha_traz_destinatario():
    xlsx = nf_upseller.montar_xlsx("Loja Avulsa", [_pedido()])
    ws = load_workbook(io.BytesIO(xlsx)).active
    # dados começam na linha 4 (1ª linha do pedido)
    assert _c(ws, 4, "Nome da Loja*") == "Loja Avulsa"
    assert _c(ws, 4, "Nº do Pedido da Loja*") == "830001"
    assert _c(ws, 4, "Necessita Emitir NF-e*") == "SIM"
    assert _c(ws, 4, "SKU*") == "a001"
    assert _c(ws, 4, "Quantidade*") == "2"
    assert _c(ws, 4, "Preço Unitário* ") == "161,00"
    assert _c(ws, 4, "Método de Pagamento") == "Dinheiro"
    # destinatário (obrigatório pra NF-e) na 1ª linha
    assert _c(ws, 4, "Nome do Destinatário (Obrigatório para NF-e)") == "Cleso Menezes"
    assert _c(ws, 4, "Tipo de Tributação (Obrigatório para NF-e)") == "CPF"
    assert _c(ws, 4, "Número de Tributação (Obrigatório para NF-e)") == "12345678901"
    assert _c(ws, 4, "CEP (Obrigatório para NF-e)") == "30570050"
    # UF vira nome por extenso
    assert _c(ws, 4, "Estado (Obrigatório para NF-e)") == "Minas Gerais"
    assert _c(ws, 4, "Cidade (Obrigatório para NF-e)") == "Belo Horizonte"
    assert _c(ws, 4, "Bairro (Obrigatório para NF-e)") == "Cinquentenário"
    assert _c(ws, 4, "Número") == "30"
    assert _c(ws, 4, "Endereço 1") == "Rua Emídio Beruto"
    assert _c(ws, 4, "Endereço 2") == "Apto 2"
    assert _c(ws, 4, "Nº de Celular") == "31999990000"


def test_xlsx_itens_seguintes_sem_destinatario():
    xlsx = nf_upseller.montar_xlsx("Loja Avulsa", [_pedido()])
    ws = load_workbook(io.BytesIO(xlsx)).active
    # 2ª linha do pedido (linha 5): SKU/qtd/preço presentes, destinatário vazio
    assert _c(ws, 5, "Nome da Loja*") == "Loja Avulsa"
    assert _c(ws, 5, "Nº do Pedido da Loja*") == "830001"
    assert _c(ws, 5, "SKU*") == "a002"
    assert _c(ws, 5, "Quantidade*") == "1"
    assert _c(ws, 5, "Preço Unitário* ") == "50,00"
    # o bloco do destinatário só na 1ª linha (o Upseller unifica pelo par loja+pedido)
    assert not _c(ws, 5, "Nome do Destinatário (Obrigatório para NF-e)")
    assert not _c(ws, 5, "CEP (Obrigatório para NF-e)")


def test_xlsx_nfe_nao_para_import_ml():
    # Fluxo ML: a NF já saiu do Bling; o Upseller entra só pra puxar a etiqueta,
    # então "Necessita Emitir NF-e = NÃO" (senão o Upseller emitiria 2ª NF). O
    # destinatário continua indo (é quem recebe a etiqueta).
    xlsx = nf_upseller.montar_xlsx("Loja Avulsa", [_pedido()], emitir_nfe=False)
    ws = load_workbook(io.BytesIO(xlsx)).active
    assert _c(ws, 4, "Necessita Emitir NF-e*") == "NÃO"
    assert _c(ws, 4, "Nome do Destinatário (Obrigatório para NF-e)") == "Cleso Menezes"
    assert _c(ws, 4, "SKU*") == "a001"


def test_tipo_tributacao_infere_por_documento():
    # sem tipoPessoa, infere pelo tamanho do documento (14 dígitos = CNPJ)
    assert nf_upseller._tipo_tributacao(None, "12345678000199") == "CNPJ"
    assert nf_upseller._tipo_tributacao(None, "12345678901") == "CPF"
    assert nf_upseller._tipo_tributacao("J", "12345678901") == "CNPJ"
    assert nf_upseller._tipo_tributacao(None, None) == ""


def test_estado_por_extenso():
    assert nf_upseller.estado_por_extenso("sp") == "São Paulo"
    assert nf_upseller.estado_por_extenso("MG") == "Minas Gerais"
    # UF desconhecida devolve o valor cru
    assert nf_upseller.estado_por_extenso("ZZ") == "ZZ"
