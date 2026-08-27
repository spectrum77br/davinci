"""Camada de ARQUIVO da emissão (Fase 3a) — monta a planilha de importação
avulsa no layout EXATO do relatório de vendas do Bling.

O motor `nf_emissao` transforma os itens do pedido (regra do faturador) em
`NfLinha`. Aqui essas linhas viram o arquivo que se importa no Bling de
DESTINO (outro Bling / Upseller) como VENDA AVULSA — desacoplada do
intermediador do marketplace (o pulo do gato: não se pega o pedido que já
está lá pra "emitir nota", que carregaria o intermediador; importa-se um
pedido avulso a partir do relatório transformado).

O layout (cabeçalho, ordem das 41 colunas, formato numérico BR, `;` como
separador, aspas em tudo, BOM UTF-8) espelha o CSV que o Bling exporta em
Vendas → então o arquivo gerado importa de volta sem fricção.

Só monta o arquivo; NÃO lê banco nem loga em site (essas camadas ficam por
cima). Os dados de comprador que o davinci não guarda (CPF/telefone/e-mail)
saem em branco; o destinatário do pedido preenche Comprador e Entrega.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal
from datetime import date, datetime

from app.services.nf_emissao import NfLinha

# Ordem FIXA das colunas do relatório de vendas do Bling (não reordenar — é o
# que o importador do Bling espera casar pelo cabeçalho).
COLUNAS: list[str] = [
    "Número pedido",
    "Nome Comprador",
    "Data",
    "CPF/CNPJ Comprador",
    "Endereço Comprador",
    "Bairro Comprador",
    "Número Comprador",
    "Complemento Comprador",
    "CEP Comprador",
    "Cidade Comprador",
    "UF Comprador",
    "Telefone Comprador",
    "Celular Comprador",
    "E-mail Comprador",
    "Produto",
    "SKU",
    "Un",
    "Quantidade",
    "Valor Unitário",
    "Valor Total",
    "Total Pedido",
    "Valor Frete Pedido",
    "Valor Desconto Pedido",
    "Outras despesas",
    "Nome Entrega",
    "Endereço Entrega",
    "Número Entrega",
    "Complemento Entrega",
    "Cidade Entrega",
    "UF Entrega",
    "CEP Entrega",
    "Bairro Entrega",
    "Transportadora",
    "Serviço",
    "Tipo Frete",
    "Observações",
    "Qtd Parcela",
    "Data Prevista",
    "Vendedor",
    "Forma Pagamento",
    "ID Forma Pagamento",
]

CSV_MEDIA = "text/csv"

_UN_PADRAO = "UN"
_QTD_PARCELA_PADRAO = "1"


@dataclass(frozen=True)
class PedidoInfo:
    """Cabeçalho do pedido do Bling principal (uma linha da `bling_orders`,
    campos repetidos entre os itens). O davinci só guarda o destinatário —
    ele preenche tanto o Comprador quanto a Entrega no arquivo."""

    numero: str | None
    data: date | datetime | None
    nome_destinatario: str | None = None
    cep_destino: str | None = None
    endereco_destino: str | None = None
    numero_destino: str | None = None
    complemento_destino: str | None = None
    bairro_destino: str | None = None
    cidade_destino: str | None = None
    uf_destino: str | None = None
    # CPF/CNPJ + tipo de pessoa (F/J) + telefone do destinatário — necessários
    # pra NF-e no Upseller. O davinci não persiste isso na bling_orders (só o
    # nome/documento), então vêm enriquecidos do Bling na camada de banco.
    documento: str | None = None
    tipo_pessoa: str | None = None
    telefone: str | None = None
    # Nome da conta de marketplace (store_info.account_name). Só o arquivo do
    # Upseller usa: cada conta é uma Loja registrada lá. O CSV do Bling ignora.
    loja: str | None = None
    # Texto livre que vai pras Informações Complementares da NF-e (a DUIMP dos
    # produtos importados). Vazio = nota sem observação. Vai na coluna
    # "Observações" do CSV do Bling e na "Observação" do .xlsx do Upseller.
    observacao: str | None = None


def _s(v: object) -> str:
    return "" if v is None else str(v).strip()


def _br(v: Decimal) -> str:
    """Número no formato BR: ponto de milhar, vírgula decimal, 2 casas.
    Ex.: Decimal('1003.37') -> '1.003,37'."""
    q = v.quantize(Decimal("0.01"))
    # {:,.2f} usa vírgula de milhar e ponto decimal (en-US); trocamos.
    txt = f"{q:,.2f}"
    return txt.replace(",", "@").replace(".", ",").replace("@", ".")


def _data_br(v: date | datetime | None) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        v = v.date()
    return v.strftime("%d/%m/%Y")


def montar_linhas(pedido: PedidoInfo, linhas: list[NfLinha]) -> list[list[str]]:
    """As 41 colunas por item (cabeçalho do pedido repetido). Total Pedido =
    soma dos totais das linhas (frete/desconto/outras = 0 na venda avulsa)."""
    total_pedido = sum((l.valor_total for l in linhas), Decimal("0.00"))
    nome = _s(pedido.nome_destinatario)
    endereco = _s(pedido.endereco_destino)
    bairro = _s(pedido.bairro_destino)
    numero_end = _s(pedido.numero_destino)
    complemento = _s(pedido.complemento_destino)
    cep = _s(pedido.cep_destino)
    cidade = _s(pedido.cidade_destino)
    uf = _s(pedido.uf_destino)
    numero_pedido = _s(pedido.numero)
    data = _data_br(pedido.data)
    documento = _s(pedido.documento)
    observacao = _s(pedido.observacao)

    out: list[list[str]] = []
    for l in linhas:
        out.append([
            numero_pedido,              # Número pedido
            nome,                       # Nome Comprador
            data,                       # Data
            documento,                  # CPF/CNPJ Comprador (enriquecido do Bling)
            endereco,                   # Endereço Comprador
            bairro,                     # Bairro Comprador
            numero_end,                 # Número Comprador
            complemento,                # Complemento Comprador
            cep,                        # CEP Comprador
            cidade,                     # Cidade Comprador
            uf,                         # UF Comprador
            "",                         # Telefone Comprador
            "",                         # Celular Comprador
            "",                         # E-mail Comprador
            _s(l.nome),                 # Produto (nome fonte da regra)
            _s(l.sku),                  # SKU (sku fonte da regra)
            _UN_PADRAO,                 # Un
            _br(Decimal(l.quantidade)), # Quantidade
            _br(l.valor_unitario),      # Valor Unitário
            _br(l.valor_total),         # Valor Total
            _br(total_pedido),          # Total Pedido
            "0,00",                     # Valor Frete Pedido
            "0,00",                     # Valor Desconto Pedido
            "0,00",                     # Outras despesas
            nome,                       # Nome Entrega
            endereco,                   # Endereço Entrega
            numero_end,                 # Número Entrega
            complemento,                # Complemento Entrega
            cidade,                     # Cidade Entrega
            uf,                         # UF Entrega
            cep,                        # CEP Entrega
            bairro,                     # Bairro Entrega
            "",                         # Transportadora
            "",                         # Serviço
            "",                         # Tipo Frete
            observacao,                 # Observações
            _QTD_PARCELA_PADRAO,        # Qtd Parcela
            "",                         # Data Prevista
            "",                         # Vendedor
            "",                         # Forma Pagamento
            "",                         # ID Forma Pagamento
        ])
    return out


def montar_csv(pedidos: list[tuple[PedidoInfo, list[NfLinha]]]) -> bytes:
    """CSV no formato do Bling: `;` separador, aspas em tudo, CRLF, BOM UTF-8.
    Aceita vários pedidos (cada um com suas linhas transformadas)."""
    buf = io.StringIO()
    writer = csv.writer(
        buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n"
    )
    writer.writerow(COLUNAS)
    for pedido, linhas in pedidos:
        for row in montar_linhas(pedido, linhas):
            writer.writerow(row)
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
