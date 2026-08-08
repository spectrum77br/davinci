"""Camada de ARQUIVO da emissão para o UPSELLER — monta a planilha de
importação de pedidos no layout EXATO do template do Upseller ("Baixar o
Modelo" da tela `Importar Pedidos`).

Diferente do Bling (que aceita o CSV do relatório de vendas), o Upseller só
aceita **.xlsx no template dele** (aba `order_`, 44 colunas). O motor
`nf_emissao` transforma os itens do pedido (regra do faturador) em `NfLinha`;
aqui essas linhas viram o arquivo que a marionete sobe no Upseller como pedido
de VENDA AVULSA (uma "loja avulsa"), desacoplado do intermediador do
marketplace — o pulo do gato do fluxo de NF pelo Upseller.

Estrutura do arquivo (fiel ao modelo baixado, com as linhas de exemplo já
removidas):
  - Linha 1: observação/instruções (coluna A).
  - Linha 2: rótulos dos GRUPOS de colunas (cosmético; o import lê a linha 3).
  - Linha 3: NOMES DOS CAMPOS (o cabeçalho que o Upseller casa).
  - Linha 4+: os dados (um item por linha; o destinatário preenche só a 1ª
    linha de cada pedido — linhas com o mesmo Nome da Loja + Nº do Pedido são
    unificadas num único pedido pelo Upseller).

Só monta o arquivo; NÃO lê banco nem loga em site. Os dados de destinatário
(nome, CPF/CNPJ, endereço) vêm enriquecidos do Bling na camada de banco
(`nf_emissao_gerar`); aqui só formata.
"""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import Workbook

from app.services.nf_emissao import NfLinha
from app.services.nf_relatorio import PedidoInfo

UPSELLER_MEDIA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_SHEET = "order_"
_PAGAMENTO_PADRAO = "Dinheiro"
# Valores EXATOS da lista suspensa do Upseller (case-sensitive: "SIM"/"NÃO"
# são rejeitados com "Apenas Sim ou Não são permitidos").
_NFE_SIM = "Sim"
_NFE_NAO = "Não"

# Loja avulsa REGISTRADA na conta Upseller — o import rejeita qualquer nome
# que não exista no sistema ("A loja X não existe ou não está autorizada").
LOJA_AVULSA = "Loja Padrão"

# SKUs GENÉRICOS do catálogo de produtos do Upseller (o SKU do arquivo TEM
# que existir lá; o SKU real do pedido — ex. 'dg053.sp' — é rejeitado).
# Mapa por categoria do Bling (bling_orders.categoria_nome).
_SKU_CELULAR = "e3"
_SKU_MALA = "m200"
_SKU_MALA_KIT = "m100"

# O Upseller REJEITA SKUs repetidos no mesmo pedido ("Não são permitidos SKUs
# duplicados no mesmo pedido") — pedidos multi-item da MESMA família precisam
# alternar SKUs equivalentes do catálogo (regra do usuário 08/08: celular
# e3→e4; mala alterna m200/m100), mantendo o MESMO nº do pedido (unificação).
_SKU_ALTERNATIVAS = {
    _SKU_CELULAR: [_SKU_CELULAR, "e4"],
    _SKU_MALA: [_SKU_MALA, _SKU_MALA_KIT],
    _SKU_MALA_KIT: [_SKU_MALA_KIT, _SKU_MALA],
}

# Texto de observação da linha 1 do modelo (verbatim do "Baixar o Modelo").
_OBS_TEXTO = (
    "Observação:Preencha o modelo de acordo com as regras abaixo para evitar "
    "falhas na importação. Caso a importação falhe, corrija os erros com base "
    "nos motivos informados e reimporte apenas os pedidos que falharam.\n"
    "1. Unificação de Pedidos (Loja + Nº do Pedido):Linhas com o mesmo Nome da "
    "Loja + Nº do Pedido da Loja serão unificados em um único pedido.\n"
    "2. Campos de Lista Suspensa:Os valores dos campos com lista suspensa devem "
    "corresponder exatamente às opções disponíveis.\n"
    "3. Pedidos com Múltiplos Itens: Para combinar vários itens do mesmo "
    "destinatário na mesma loja em um único pedido, mantenha o mesmo Nome da "
    "Loja e Nº do Pedido da Loja, mas utilize informações de SKU diferentes.\n"
    "4. Requisitos de Nota Fiscal para o Mesmo Destinatário: Se o mesmo "
    "destinatário na mesma loja precisar de nota fiscal, todos os campos "
    "marcados como opcional deverão ser preenchidos.\n"
    "5. Necessita Emitir NF-e: Se o campo \"Necessita Emitir Nota Fiscal\" "
    "estiver definido como Sim, todos os campos opcionais tornam-se "
    "obrigatórios.\n"
    "6. Importação Parcial e Tratamento de Falhas: Se a importação for "
    "parcialmente bem-sucedida, os pedidos válidos serão importados diretamente "
    "para \"Pedidos Recentes\". Os pedidos com falha serão exportados para um "
    "arquivo de \"Falhas de Importação\".\n"
    "7. Método de Custo de Envio: Informe o código numérico (0/1/2/3/4/9).\n"
    "8. Formato do Estado: Informe o nome completo do estado (ex.: Acre).\n"
    "9. Moeda: Todos os valores monetários utilizam a moeda local do país/site "
    "configurado para a loja."
)

# Linha 2 (grupos) — verbatim do modelo; posições nos primeiros cells de cada
# grupo. Cosmético: o importador casa pela linha 3.
_ROW2_GRUPOS: list[str] = [""] * 44
for _i, _v in {
    0: "Categoria",
    1: "Informação do Pedido",
    5: "Informação do Destinatário",
    18: "Informação do Produto",
    22: "Informação de Envio",
    34: (
        "Detalhes da Transportador para NF-e (Deixe em branco caso não seja "
        "necessária a emissão de nota fiscal. Caso seja necessária, preencha "
        "conforme aplicável.)"
    ),
    41: "Informação de Pagamento",
}.items():
    _ROW2_GRUPOS[_i] = _v

# Linha 3 — os NOMES DOS CAMPOS (o cabeçalho que o Upseller casa). Verbatim,
# incluindo o espaço final em "Preço Unitário* ". NÃO reordenar.
_HEADERS: list[str] = [
    "Nome do Campo",
    "Nome da Loja*",
    "Nº do Pedido da Loja*",
    "Observação",
    "Necessita Emitir NF-e*",
    "Nome do Destinatário (Obrigatório para NF-e)",
    "Nº de Celular",
    "Tipo de Tributação (Obrigatório para NF-e)",
    "Número de Tributação (Obrigatório para NF-e)",
    "Nome da Empresa",
    "IE",
    "CEP (Obrigatório para NF-e)",
    "Estado (Obrigatório para NF-e)",
    "Cidade (Obrigatório para NF-e)",
    "Bairro (Obrigatório para NF-e)",
    "Número",
    "Endereço 1",
    "Endereço 2",
    "Nome do Armazém",
    "SKU*",
    "Quantidade*",
    "Preço Unitário* ",
    "Método de Custo de Envio",
    "Tipo de Pacote",
    "Número",
    "Método de Envio",
    "Nº de Rastreio",
    "Quantidade de Pacote",
    "Peso Bruto (g)",
    "Peso Líquido (g)",
    "Tamanho do Pacote/Comprimento (cm)",
    "Tamanho do Pacote/Largura (cm)",
    "Tamanho do Pacote/Altura (cm)",
    "Tipo de Tributação (Opcional)",
    "Número de Tributação (Opcional)",
    "Nome (Opcional)",
    "IE",
    "Estado(Opcional)",
    "Cidade",
    "Endereço",
    "Método de Pagamento",
    "Custo do Frete do Comprador",
    "Desconto",
    "Custo do Frete do Vendedor",
]

# UF → nome completo do estado (o Upseller exige o nome por extenso).
_UF_ESTADO: dict[str, str] = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AP": "Amapá",
    "AM": "Amazonas",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais",
    "PA": "Pará",
    "PB": "Paraíba",
    "PR": "Paraná",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul",
    "RO": "Rondônia",
    "RR": "Roraima",
    "SC": "Santa Catarina",
    "SP": "São Paulo",
    "SE": "Sergipe",
    "TO": "Tocantins",
}


def _s(v: object) -> str:
    return "" if v is None else str(v).strip()


def _preco_num(v: Decimal) -> float:
    """Preço como NÚMERO (célula numérica). Texto BR '35,06' é rejeitado pelo
    Upseller ("O preço unitário deve ser um número entre 0,01 e 999999999")."""
    return float(v.quantize(Decimal("0.01")))


def sku_para_categoria(categoria: str | None) -> str:
    """SKU genérico do catálogo Upseller pela categoria do Bling.
    'Mala Kit'→m100, 'Mala'/'Mala Usada'→m200, resto (celular etc.)→e3."""
    c = _s(categoria).lower()
    if "mala" in c:
        return _SKU_MALA_KIT if "kit" in c else _SKU_MALA
    return _SKU_CELULAR


def skus_para_itens(categorias: list) -> list[str]:
    """SKU genérico por ITEM de um mesmo pedido, SEM repetir SKU (o Upseller
    rejeita duplicados no pedido). Cada item pega o SKU base da categoria; se
    já foi usado no pedido, cai na alternativa (e3→e4, m200↔m100). Esgotadas
    as alternativas, repete a base (o import aponta o erro em vez de mascarar)."""
    usados: set[str] = set()
    out: list[str] = []
    for cat in categorias:
        base = sku_para_categoria(cat)
        sku = next(
            (s for s in _SKU_ALTERNATIVAS.get(base, [base]) if s not in usados),
            base,
        )
        usados.add(sku)
        out.append(sku)
    return out


def estado_por_extenso(uf: str | None) -> str:
    """Nome completo do estado a partir da UF; devolve o valor cru se não casar."""
    v = _s(uf).upper()
    return _UF_ESTADO.get(v, _s(uf))


def _tipo_tributacao(tipo_pessoa: str | None, documento: str | None) -> str:
    """'CPF' / 'CNPJ' a partir do tipoPessoa do Bling (F/J) ou, na falta dele,
    inferido pelo tamanho do documento (11 dígitos = CPF, 14 = CNPJ)."""
    tp = _s(tipo_pessoa).upper()
    if tp in {"F", "FISICA", "FÍSICA", "PF"}:
        return "CPF"
    if tp in {"J", "JURIDICA", "JURÍDICA", "PJ"}:
        return "CNPJ"
    digitos = "".join(ch for ch in _s(documento) if ch.isdigit())
    if not digitos:
        return ""
    return "CNPJ" if len(digitos) > 11 else "CPF"


def _linha(
    loja_nome: str,
    pedido: PedidoInfo,
    linha: NfLinha,
    *,
    incluir_destinatario: bool,
    emitir_nfe: bool,
) -> list[object]:
    """Uma linha (item) do arquivo. Nome da Loja / Nº do Pedido / NF-e / SKU /
    pagamento repetem em todo item; o bloco do destinatário só vai na 1ª linha
    do pedido (o Upseller unifica pelo par Loja + Nº do Pedido)."""
    row: list[object] = [""] * 44
    row[1] = _s(loja_nome)                       # Nome da Loja*
    row[2] = _s(pedido.numero)                   # Nº do Pedido da Loja*
    row[4] = _NFE_SIM if emitir_nfe else _NFE_NAO  # Necessita Emitir NF-e*
    row[19] = _s(linha.sku)                      # SKU*
    row[20] = int(linha.quantidade)              # Quantidade* (numérico)
    row[21] = _preco_num(linha.valor_unitario)   # Preço Unitário* (numérico)
    row[40] = _PAGAMENTO_PADRAO                  # Método de Pagamento

    if incluir_destinatario:
        row[5] = _s(pedido.nome_destinatario)                        # Nome
        row[6] = _s(pedido.telefone)                                 # Celular
        row[7] = _tipo_tributacao(pedido.tipo_pessoa, pedido.documento)  # Tipo Trib
        row[8] = _s(pedido.documento)                                # Nº Trib
        row[11] = _s(pedido.cep_destino)                             # CEP
        row[12] = estado_por_extenso(pedido.uf_destino)              # Estado
        row[13] = _s(pedido.cidade_destino)                          # Cidade
        row[14] = _s(pedido.bairro_destino)                          # Bairro
        row[15] = _s(pedido.numero_destino)                          # Número
        row[16] = _s(pedido.endereco_destino)                        # Endereço 1
        row[17] = _s(pedido.complemento_destino)                     # Endereço 2
    return row


def montar_xlsx(
    loja_nome: str,
    pedidos: list[tuple[PedidoInfo, list[NfLinha]]],
    *,
    emitir_nfe: bool = True,
) -> bytes:
    """.xlsx no template do Upseller (aba `order_`): linhas 1-3 = cabeçalho do
    modelo; da linha 4 em diante, um item por linha. A loja de cada pedido é a
    `PedidoInfo.loja` (a conta de marketplace, registrada como Loja no
    Upseller); `loja_nome` é só o fallback de quem não tem conta resolvida.

    `emitir_nfe=True` (padrão) marca "Necessita Emitir NF-e = SIM" (faturador
    Upseller: a NF sai do Upseller). Pro fluxo ML — em que a NF já foi emitida
    pelo Bling e o Upseller entra SÓ pra puxar a etiqueta — usa
    `emitir_nfe=False` ("NÃO"), senão o Upseller emitiria uma 2ª NF."""
    wb = Workbook()
    ws = wb.active
    ws.title = _SHEET
    ws.append([_OBS_TEXTO] + [""] * 43)
    ws.append(_ROW2_GRUPOS)
    ws.append(_HEADERS)
    for pedido, linhas in pedidos:
        # Cada conta de marketplace é uma Loja registrada no Upseller; o arquivo
        # pode misturar lojas (a unificação é pelo par Loja + Nº do Pedido).
        loja_pedido = _s(pedido.loja) or loja_nome
        for i, linha in enumerate(linhas):
            ws.append(
                _linha(
                    loja_pedido,
                    pedido,
                    linha,
                    incluir_destinatario=(i == 0),
                    emitir_nfe=emitir_nfe,
                )
            )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
