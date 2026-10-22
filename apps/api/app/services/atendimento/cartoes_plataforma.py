"""Os cartões que a PLATAFORMA põe no chat sem texto — o que a tela e o agente leem.

Item 24 da auditoria ao vivo (22/10/2026 01:37, Barbosa Shopee, conversa
ce443965…): a Shopee pôs na conversa o cartão do pedido de devolução/reembolso
que o comprador abriu ("Não recebi meu pacote", "Apenas Reembolso"), e o DaVinci
mostrava "[Mensagem]" — na tela e na transcrição do agente (o rascunho 96f15c48,
das 01:40, não o viu). O `shopee.interpretar` grava esses cartões como `outro`
com o rótulo genérico (`constantes.rotulo_tipo_plataforma`); o conteúdo cru fica
no `payload` (`payload.content`).

Medido em produção (30 dias até 22/10/2026, só SELECT; `tipo = 'outro'` com o
texto "[Mensagem]"):

  variation_card        296  do comprador: o produto e a variação que ele escolheu
  logistics_card        242  da Shopee em nome da loja (`source` "server"): o envio
  crm_order_rate        198  da Shopee (`source` "crm"): o convite para avaliar
  track_rr_status_card   52  da Shopee: a situação do pedido de devolução/reembolso
  return_refund_card     23  (16 do comprador, 7 da loja): o pedido de devolução/
                             reembolso que o comprador abriu
  crm_item_list           5  da Shopee: o lembrete do carrinho

Aqui o TEXTO que se lê no lugar do rótulo — sem mudar a linha gravada (o legado e
toda régua que lê o `texto` do banco continuam iguais). Os dois de devolução
saem com o motivo e a solução em português (a entrada "pt-BR" do
`rr_detail.translated_detail`) e a situação; os outros, genéricos. O valor só
vai para a tela, nunca para o modelo (`para_o_modelo=True`), e o texto que vem
do anúncio (o nome do produto, a variação), da transportadora ou do pedido de
devolução (o motivo, a solução, a situação) passa pelo `limpar` de quem chama (o
agente tira o que parece instrução).

`devolucao_do_cartao` lê o pedido de devolução/reembolso do cartão: o agente o
trata como "devolução/reembolso aberto na plataforma" (a régua da ação de
pessoa: `reclamacao_aberta` — a loja responde a solicitação na Central do
Vendedor). PURO: sem banco, sem rede.

Revisão 2 da rodada 6 (22/10/2026, robustez): o `payload` vem da plataforma e
nada aqui pode derrubar a conversa. Os números só como inteiro de até 18
dígitos (o "²" passava no `isdigit` e quebrava o `int`; o número de 4.301
dígitos quebrava o `int`/`str` do Python 3.14), os textos só como `str`, a lista
de modelos só como lista. E, por cima, a leitura da tela (`texto_para_exibir`) e
a do agente (`campos_do_cartao`, `devolucoes_abertas_nos_cartoes`) nunca
levantam: a tela e o agente ficam com o `texto` gravado ("[Mensagem]"); a
devolução do cartão que não se lê conta como ABERTA (o lado seguro: pessoa). A
falha vai ao log uma vez por mensagem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# O `tipo` em que a plataforma grava o que não é texto, imagem, vídeo, produto
# nem pedido (o `TIPO_OUTRO` dos adaptadores).
TIPO_OUTRO = "outro"
PREFIXO_SHOPEE = "[Shopee]"
# O idioma do cliente nos textos traduzidos da Shopee.
IDIOMA = "pt-BR"

CARTAO_DEVOLUCAO = "return_refund_card"
CARTAO_SITUACAO_DEVOLUCAO = "track_rr_status_card"
CARTOES_DEVOLUCAO = (CARTAO_DEVOLUCAO, CARTAO_SITUACAO_DEVOLUCAO)
CARTAO_LOGISTICA = "logistics_card"
CARTAO_VARIACAO = "variation_card"
CARTAO_AVALIAR = "crm_order_rate"
CARTAO_CARRINHO = "crm_item_list"
CARTOES_CONHECIDOS = (
    *CARTOES_DEVOLUCAO,
    CARTAO_LOGISTICA,
    CARTAO_VARIACAO,
    CARTAO_AVALIAR,
    CARTAO_CARRINHO,
)

# A situação do pedido de devolução/reembolso (`content.tracking_info.rr_status`).
# Medido em produção (90 dias): request_submitted (aberto, inclusive "Em
# devolução", "Retornado - Pendente validação do vendedor", "Disputa com a Shopee
# solicitada"), refund_completed ("Reembolso pago") e request_rejected
# ("Solicitação cancelada pelo comprador"). O que não se conhece conta como
# aberto (o lado seguro: pessoa).
RR_ENCERRADOS = frozenset(
    {"refund_completed", "request_rejected", "request_cancelled", "cancelled", "closed"}
)
_SITUACAO_RR = {
    "request_submitted": "solicitação aberta",
    "refund_completed": "reembolso concluído",
    "request_rejected": "solicitação encerrada",
    "request_cancelled": "solicitação cancelada",
}
# Sem `rr_status`: `rr_detail.status` (1 aberto, 2 reembolsado, 3 encerrado).
_STATUS_ENCERRADOS = frozenset({2, 3})
# O título do cartão de situação: "Status do reembolso feito pelo comprador: X".
_TITULO_SITUACAO = "Status do reembolso feito pelo comprador:"
# Os valores do cartão vêm em centésimos de milésimo (71998000 = R$ 719,98).
_ESCALA_VALOR = 100_000
MAX_CHARS_PARTE = 120
# O maior inteiro que se lê do cartão (o valor, o return_id, o status): 18 dígitos.
_MAX_DIGITOS = 18
# Os modelos do `variation_card` que se percorrem (em produção, poucos).
MAX_MODELOS = 200
# As mensagens cuja falha já foi ao log (uma vez por mensagem; o conjunto zera cheio).
_FALHAS_LOGADAS: set[tuple[str, str]] = set()
_MAX_FALHAS_LOGADAS = 5_000


@dataclass(frozen=True)
class DevolucaoNoCartao:
    """O pedido de devolução/reembolso de um cartão da Shopee.

    `motivo`/`solucao` = os textos em português do cartão ("Não recebi meu
    pacote", "Apenas Reembolso"); `situacao` = o título do cartão de situação
    ("Em análise pela Shopee") ou a do `rr_status`; `encerrada` = reembolsado ou
    cancelado; `devolucao_id` = o `return_id` (junta os cartões da mesma
    solicitação); `valor` = só para a tela.
    """

    motivo: str | None
    solucao: str | None
    situacao: str | None
    encerrada: bool
    devolucao_id: str | None
    valor: float | None


def _dict(valor: Any) -> dict:
    return valor if isinstance(valor, dict) else {}


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = " ".join(valor.split())
    return texto[:MAX_CHARS_PARTE] or None


def _str(valor: Any) -> str:
    """O texto do campo (só `str`; o resto, vazio), sem espaços nas pontas e minúsculo."""
    return valor.strip().lower() if isinstance(valor, str) else ""


def tipo_do_cartao(payload: Any) -> str | None:
    """O `message_type` da Shopee quando é um dos `CARTOES_CONHECIDOS` — ou None. PURA."""
    if not isinstance(payload, Mapping):
        return None
    tipo = _str(payload.get("message_type"))
    return tipo if tipo in CARTOES_CONHECIDOS else None


def _no_idioma(lista: Any, campo: str) -> str | None:
    """O `campo` da entrada em português de uma lista traduzida (`translated_*`)."""
    for item in lista[:MAX_MODELOS] if isinstance(lista, list) else []:
        if isinstance(item, Mapping) and item.get("language") == IDIOMA:
            return _texto(item.get(campo))
    return None


def _titulo_traduzido(bloco: Any) -> str | None:
    """`{"text", "translated_with_lang": [...]}` → o texto em português (ou None)."""
    bloco = _dict(bloco)
    return _no_idioma(bloco.get("translated_with_lang"), "text")


def _inteiro(valor: Any) -> int | None:
    """O inteiro do campo: `int` (não `bool`) de até 18 dígitos, ou o texto só com os
    dígitos ASCII (até 18). O resto — o "²" que passa no `isdigit`, o número enorme —,
    None."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if -(10**_MAX_DIGITOS) < valor < 10**_MAX_DIGITOS else None
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.isascii() and texto.isdigit() and len(texto) <= _MAX_DIGITOS:
            return int(texto)
    return None


def _chave(valor: Any) -> str | None:
    """O id do campo como texto comparável (o `model_id`): inteiro ou texto curtos."""
    numero = _inteiro(valor)
    if numero is not None:
        return str(numero)
    if isinstance(valor, str) and 0 < len(valor.strip()) <= 64:
        return valor.strip()
    return None


def devolucao_do_cartao(payload: Any) -> DevolucaoNoCartao | None:
    """O pedido de devolução/reembolso do cartão (`return_refund_card` ou
    `track_rr_status_card`, com `content.rr_detail`) — ou None. PURA."""
    if tipo_do_cartao(payload) not in CARTOES_DEVOLUCAO:
        return None
    conteudo = _dict(payload.get("content"))
    rr = conteudo.get("rr_detail")
    if not isinstance(rr, Mapping):
        return None
    detalhe = rr.get("translated_detail")
    rr_status = _str(_dict(conteudo.get("tracking_info")).get("rr_status"))
    titulo = _titulo_traduzido(conteudo.get("card_title_for_sender"))
    situacao = None
    if titulo and titulo.startswith(_TITULO_SITUACAO):
        situacao = _texto(titulo[len(_TITULO_SITUACAO) :])
    situacao = situacao or _SITUACAO_RR.get(rr_status)
    if rr_status:
        encerrada = rr_status in RR_ENCERRADOS
    else:
        encerrada = _inteiro(rr.get("status")) in _STATUS_ENCERRADOS
    quantia = _inteiro(rr.get("amount"))
    devolucao_id = _inteiro(rr.get("return_id")) or _inteiro(rr.get("returnId"))
    return DevolucaoNoCartao(
        motivo=_no_idioma(detalhe, "reason"),
        solucao=_no_idioma(detalhe, "solution"),
        situacao=situacao,
        encerrada=encerrada,
        devolucao_id=str(devolucao_id) if devolucao_id else None,
        valor=round(quantia / _ESCALA_VALOR, 2) if quantia and quantia > 0 else None,
    )


def devolucoes_abertas_nos_cartoes(mensagens: Iterable[Any]) -> list[datetime | None]:
    """A hora do último cartão de cada pedido de devolução/reembolso ABERTO nas
    mensagens (None = o cartão sem hora). PURA.

    Na ordem das mensagens: o último cartão de cada solicitação (`devolucao_id`)
    é o que vale — "aberto" seguido de "Reembolso pago" está encerrado. Cartão
    sem o id conta sozinho. A hora (revisão da rodada 6, 22/10/2026) deixa quem
    chama ver se a reclamação da plataforma encerrou DEPOIS do cartão
    (`acao_pessoa.devolucao_na_plataforma`).
    """
    abertas: dict[str, datetime | None] = {}
    sem_id: list[datetime | None] = []
    for m in mensagens:
        quando = getattr(m, "enviada_em", None) or getattr(m, "created_at", None)
        quando = quando if isinstance(quando, datetime) else None
        try:
            d = devolucao_do_cartao(getattr(m, "payload", None))
        except Exception as erro:  # noqa: BLE001 — o cartão que não se lê não derruba o agente
            _registrar_falha(m, "devolucao", erro)
            # O lado seguro: a devolução que não se lê conta como ABERTA (pessoa).
            if tipo_do_cartao_seguro(getattr(m, "payload", None)) in CARTOES_DEVOLUCAO:
                sem_id.append(quando)
            continue
        if d is None:
            continue
        if d.devolucao_id:
            if d.encerrada:
                abertas.pop(d.devolucao_id, None)
            else:
                abertas[d.devolucao_id] = quando
        elif not d.encerrada:
            sem_id.append(quando)
    return [*sem_id, *abertas.values()]


def devolucao_aberta_nos_cartoes(mensagens: Iterable[Any]) -> bool:
    """Há pedido de devolução/reembolso ABERTO nos cartões das mensagens? PURA."""
    return bool(devolucoes_abertas_nos_cartoes(mensagens))


def _valor_br(valor: float) -> str:
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def _texto_da_devolucao(
    d: DevolucaoNoCartao,
    *,
    para_o_modelo: bool,
    limpar: Callable[[str | None], str | None] | None = None,
) -> str:
    cabeca = (
        "Pedido de devolução/reembolso do cliente"
        if d.encerrada
        else "Cliente abriu pedido de devolução/reembolso"
    )
    # Revisão da rodada 6 (22/10/2026): o motivo, a solução e a situação passam pelo
    # mesmo filtro de quem chama que o nome do produto (o agente tira o que parece
    # instrução) — hoje são os textos fixos da Shopee, mas vêm do cartão.
    limpo = limpar or _texto
    motivo, solucao, situacao = limpo(d.motivo), limpo(d.solucao), limpo(d.situacao)
    partes = [f"{PREFIXO_SHOPEE} {cabeca}"]
    if motivo:
        partes.append(f"motivo: {motivo}")
    if solucao:
        partes.append(f"solução: {solucao}")
    if situacao:
        partes.append(f"situação: {situacao}")
    if d.valor is not None and not para_o_modelo:
        partes.append(f"valor: {_valor_br(d.valor)}")
    return " — ".join(partes)


def texto_do_cartao(
    payload: Any,
    *,
    para_o_modelo: bool = False,
    limpar: Callable[[str | None], str | None] | None = None,
) -> str | None:
    """O texto do cartão da plataforma (`CARTOES_CONHECIDOS`) — ou None. PURA.

    `para_o_modelo` = para o agente: sem o valor e sem o texto da transportadora
    (o estado do envio do agente é o do pedido da conversa). `limpar` = o filtro
    de quem chama para o texto que vem do anúncio (o agente tira o que parece
    instrução: `pacote._do_cartao`).
    """
    tipo = tipo_do_cartao(payload)
    if tipo is None:
        return None
    limpo = limpar or _texto
    if tipo in CARTOES_DEVOLUCAO:
        devolucao = devolucao_do_cartao(payload)
        if devolucao is None:
            return f"{PREFIXO_SHOPEE} Cartão de devolução/reembolso do pedido"
        return _texto_da_devolucao(devolucao, para_o_modelo=para_o_modelo, limpar=limpar)
    conteudo = _dict(payload.get("content"))
    if tipo == CARTAO_LOGISTICA:
        texto = f"{PREFIXO_SHOPEE} Cartão do envio do pedido"
        ultimo = _texto(_dict(conteudo.get("logistic_tracking_info")).get("latest_tracking_info"))
        if ultimo and not para_o_modelo:
            texto += f" — {ultimo}"
        return texto
    if tipo == CARTAO_VARIACAO:
        nome, variacao = _nome_e_variacao(conteudo)
        nome, variacao = limpo(nome), limpo(variacao)
        texto = f"{PREFIXO_SHOPEE} Cartão do produto"
        if nome:
            texto += f": {nome}"
        if variacao:
            texto += f" — variação: {variacao}"
        return texto
    if tipo == CARTAO_AVALIAR:
        return f"{PREFIXO_SHOPEE} Convite para avaliar o pedido"
    return f"{PREFIXO_SHOPEE} Lembrete do carrinho"


def _nome_e_variacao(conteudo: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """O nome do produto e o da variação escolhida do `variation_card` (crus)."""
    cartao = _dict(conteudo.get("item_card_v2"))
    nome = _texto(_dict(cartao.get("name")).get("text"))
    modelo_id = conteudo.get("model_id")
    chave = None if modelo_id is None else _chave(modelo_id)
    modelos = cartao.get("item_model_v2")
    modelos = modelos[:MAX_MODELOS] if isinstance(modelos, list) else []
    variacao = next(
        (
            _texto(_dict(m.get("name")).get("text"))
            for m in modelos
            if isinstance(m, Mapping)
            and (modelo_id is None or (chave is not None and _chave(m.get("model_id")) == chave))
        ),
        None,
    )
    return nome, variacao


def _rotulo_generico(texto: str | None) -> bool:
    """O texto gravado é só o rótulo ("[Mensagem]") ou vazio? (O que não é texto, não.)"""
    if texto is not None and not isinstance(texto, str):
        return False
    t = (texto or "").strip()
    return not t or (t.startswith("[") and t.endswith("]") and "\n" not in t and len(t) <= 40)


def texto_para_exibir(
    m: Any,
    *,
    para_o_modelo: bool = False,
    limpar: Callable[[str | None], str | None] | None = None,
) -> str | None:
    """O texto da mensagem como a tela (ou o agente) o mostra. PURA.

    A mensagem `outro` com um cartão conhecido da plataforma e o texto gravado
    só com o rótulo ("[Mensagem]") ganha o texto do cartão (`texto_do_cartao`);
    todo o resto, o `texto` gravado, como sempre.
    """
    texto = getattr(m, "texto", None)
    if getattr(m, "tipo", None) != TIPO_OUTRO or not _rotulo_generico(texto):
        return texto
    try:
        do_cartao = texto_do_cartao(
            getattr(m, "payload", None), para_o_modelo=para_o_modelo, limpar=limpar
        )
    except Exception as erro:  # noqa: BLE001 — a tela e o agente nunca caem por um cartão
        _registrar_falha(m, "texto", erro)
        return texto
    return do_cartao or texto


def campos_do_cartao(m: Any) -> list[str]:
    """Os textos CRUS que o cartão da mensagem põe na leitura do agente — o motivo, a
    solução e a situação da devolução; o nome do produto e a variação —, campo a
    campo, antes do filtro de quem chama. PURA. Revisão da rodada 6 (22/10/2026): a
    trava de injeção do verificador confere o que o modelo leria (o texto montado
    não serve: o "motivo:" da própria moldura casa a régua de instrução).
    """
    if getattr(m, "tipo", None) != TIPO_OUTRO or not _rotulo_generico(getattr(m, "texto", None)):
        return []
    payload = getattr(m, "payload", None)
    try:
        tipo = tipo_do_cartao(payload)
        if tipo in CARTOES_DEVOLUCAO:
            d = devolucao_do_cartao(payload)
            campos: tuple[str | None, ...] = (d.motivo, d.solucao, d.situacao) if d else ()
        elif tipo == CARTAO_VARIACAO:
            campos = _nome_e_variacao(_dict(payload.get("content")))
        else:
            campos = ()
    except Exception as erro:  # noqa: BLE001 — o que não se lê também não vai ao modelo
        # (`texto_para_exibir` fica com o "[Mensagem]" gravado).
        _registrar_falha(m, "campos", erro)
        return []
    return [c for c in campos if c]


def tipo_do_cartao_seguro(payload: Any) -> str | None:
    """`tipo_do_cartao` que nunca levanta (None no que não se lê). PURA."""
    try:
        return tipo_do_cartao(payload)
    except Exception:  # noqa: BLE001
        return None


def _registrar_falha(m: Any, onde: str, erro: BaseException) -> None:
    """A falha de leitura do cartão vai ao log UMA vez por mensagem (e lugar)."""
    chave = (str(getattr(m, "id", None) or id(m)), onde)
    if chave in _FALHAS_LOGADAS:
        return
    if len(_FALHAS_LOGADAS) >= _MAX_FALHAS_LOGADAS:
        _FALHAS_LOGADAS.clear()
    _FALHAS_LOGADAS.add(chave)
    logger.warning(
        "cartao_plataforma_ilegivel",
        mensagem_id=chave[0],
        onde=onde,
        erro=type(erro).__name__,
    )
