"""Camada de ARQUIVO da emissão para o BLING de DESTINO — monta o XML de
importação de PEDIDO no layout oficial do Bling ("Importar → Pedido XML").

Diferente do Upseller (planilha .xlsx) e do relatório de vendas (.csv), o
importador de pedido do Bling só aceita **XML** (a tela rejeita CSV com
"São permitidos apenas os formatos xml"). O schema é o `<pedido>` documentado
do Bling: um pedido por arquivo, com `<cliente>`, `<itens>/<item>` e observações.

O motor `nf_emissao` transforma os itens do pedido (regra do faturador) em
`NfLinha`; aqui essas linhas viram o XML que a marionete sobe no Bling destino
como VENDA AVULSA — desacoplado do intermediador do marketplace (o pulo do gato
do fluxo de NF).

Como o XML é 1 pedido/arquivo, um faturador com N pedidos vira um **.zip** com N
XMLs (`montar_zip`); a marionete descompacta e importa cada um. Assim o grão do
comando continua "um por faturador" (uma sessão de browser).

Só monta o arquivo; NÃO lê banco nem loga em site. O destinatário/CPF vêm
enriquecidos do Bling na camada de banco (`nf_emissao_gerar`); aqui só formata.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

from app.services.nf_emissao import NfLinha
from app.services.nf_relatorio import PedidoInfo

BLING_XML_MEDIA = "application/xml"
BLING_ZIP_MEDIA = "application/zip"

_UN_PADRAO = "UN"


def _s(v: object) -> str:
    return "" if v is None else str(v).strip()


def _digits(v: object) -> str:
    return "".join(ch for ch in _s(v) if ch.isdigit())


def _vlr(v: Decimal) -> str:
    """Valor com PONTO decimal e 2 casas (o XML do Bling usa ponto, ex. '910.00'
    — diferente do CSV/XLSX que usam vírgula BR)."""
    return f"{v.quantize(Decimal('0.01')):.2f}"


def _tipo_pessoa(tipo: str | None, documento: str | None) -> str:
    """'F'/'J' a partir do tipoPessoa do Bling ou, na falta, inferido pelo
    tamanho do documento (<=11 dígitos = CPF/F, senão CNPJ/J)."""
    tp = _s(tipo).upper()
    if tp in {"F", "FISICA", "FÍSICA", "PF"}:
        return "F"
    if tp in {"J", "JURIDICA", "JURÍDICA", "PJ"}:
        return "J"
    digs = _digits(documento)
    if not digs:
        return "F"
    return "J" if len(digs) > 11 else "F"


def _set(parent: Element, tag: str, value: str) -> None:
    """Adiciona um filho de texto só quando há valor (evita tags vazias)."""
    if value:
        SubElement(parent, tag).text = value


def montar_xml(pedido: PedidoInfo, linhas: list[NfLinha]) -> bytes:
    """Um `<pedido>` no schema do Bling. O destinatário preenche `<cliente>`
    (comprador) e a etiqueta de entrega; cada `NfLinha` vira um `<item>` com o
    SKU/nome/quantidade/valor da regra do faturador."""
    root = Element("pedido")

    cliente = SubElement(root, "cliente")
    nome = _s(pedido.nome_destinatario)
    SubElement(cliente, "nome").text = nome
    SubElement(cliente, "tipoPessoa").text = _tipo_pessoa(
        pedido.tipo_pessoa, pedido.documento
    )
    _set(cliente, "cpf_cnpj", _digits(pedido.documento))
    _set(cliente, "endereco", _s(pedido.endereco_destino))
    _set(cliente, "numero", _s(pedido.numero_destino))
    _set(cliente, "complemento", _s(pedido.complemento_destino))
    _set(cliente, "bairro", _s(pedido.bairro_destino))
    _set(cliente, "cep", _s(pedido.cep_destino))
    _set(cliente, "cidade", _s(pedido.cidade_destino))
    _set(cliente, "uf", _s(pedido.uf_destino))
    _set(cliente, "fone", _s(pedido.telefone))

    itens = SubElement(root, "itens")
    for l in linhas:
        item = SubElement(itens, "item")
        SubElement(item, "codigo").text = _s(l.sku)
        SubElement(item, "descricao").text = _s(l.nome)
        SubElement(item, "un").text = _UN_PADRAO
        SubElement(item, "qtde").text = str(int(l.quantidade))
        SubElement(item, "vlr_unit").text = _vlr(l.valor_unitario)

    _set(root, "obs", _s(pedido.numero))
    return tostring(root, encoding="utf-8", xml_declaration=True)


def _nome_xml(pedido: PedidoInfo, i: int) -> str:
    numero = _digits(pedido.numero) or str(i + 1)
    return f"pedido_{numero}.xml"


def montar_zip(pedidos: list[tuple[PedidoInfo, list[NfLinha]]]) -> bytes:
    """.zip com um XML por pedido (`pedido_{numero}.xml`). A marionete
    descompacta e importa cada XML na tela "Pedido XML" do Bling destino."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (pedido, linhas) in enumerate(pedidos):
            zf.writestr(_nome_xml(pedido, i), montar_xml(pedido, linhas))
    return buf.getvalue()
