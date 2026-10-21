"""A régua "ação de pessoa" — os gatilhos que o CÓDIGO detecta (spec 6.3, 20/10/2026).

O assunto não manda para pessoa no caminho do agente: vai para pessoa só
quando alguém precisa AGIR ou DECIDIR (os 15 códigos de
`constantes.ACOES_PESSOA`) ou bate trava dura. O código detecta o que dá e
SEMPRE vence; o modelo só acrescenta (`juntar`). Dois gatilhos nem chamam o
modelo (`antes_do_modelo=True`): a cara de instrução para a IA e o SÓ anexo
que chega depois de a loja pedir foto/vídeo. E o turno só com o cartão do
produto ou do pedido não ganha resposta nenhuma (`gatilhos_antes_do_modelo`,
como a Caixa Humano faz desde 16/10/2026).

O que o código detecta, na ordem da régua (`gatilhos`):

  1  ag_cancelamento     etiqueta Ag. cancelamento ou o pedido em 83955 (o
                         bloco que `ag_cancelamento.classificar` deixou no
                         contexto) — o motivo nunca diz o 83955;
  2  juridico_fraude_ofensa  alerta ou xingamento (`ia.sinais_com_codigo`);
  3  reclamacao_aberta   canal reclamação, etiqueta, reclamação aberta na
                         plataforma (contexto ou `claim_ids`), cartão Cliente;
  4  avaliacao_baixa     canal avaliação de nota 1–3, avaliação pendente de
                         nota baixa, cartão Cliente "avaliou mal";
  5  instrucao           texto com cara de instrução para a IA (ANTES);
  6  pediu_pessoa        pediu atendente (já com a regra da "IA é o
                         atendente": só a insistência conta lá);
  7  segurar_envio       pista de cancelamento (ou a opção 3 do menu) e o
                         pedido AINDA NÃO postado;
  8  alterar_endereco    pista de endereço/destinatário (ou a opção 4) e o
                         pedido ainda não postado — depois de postado é
                         orientação;
  9  enviar_arquivo_nf   `ia.nf_exige_pessoa`;
  10 analisar_evidencia  anexo do cliente DEPOIS de a loja pedir foto/vídeo
                         (só anexo: ANTES do modelo);
  11 abrir_chamado       "não recebi" com o pedido constando entregue
                         (`contexto.entregue_da_logistica`), extravio, ou o
                         status de volta ao remetente;
  12 aprovar_fora_da_plataforma  Pix, depósito, dados bancários, "por fora",
                         reembolso parcial, "pago a diferença" (a diferença
                         vira orientação com a recusa padrão aprovada);
  13 desconto_brinde     pedido de desconto, brinde, amostra, atacado (vira
                         orientação com a recusa padrão aprovada; "quando
                         tem promoção?" e "tem cupom?" não são pedido);
  14 excecao             pós-venda para UF com restrição de envio (o resto
                         da exceção é do modelo);
  15 dado_indisponivel   dúvida de remetente sem o fato aprovado.

`postado` (o que decide 7 e 8) é do CÓDIGO: a data de postagem da
logística, o que a transportadora diz e a situação crua do Bling (6 e 21 =
não saiu). O modelo nunca decide o estado de postagem.

A parte de regra é PURA (`gatilhos`, `gatilhos_antes_do_modelo`): a Caixa
Humano (`humano.triar(..., agente=True)`, spec 6.6) chama a MESMA função —
paridade com o rascunho. A parte com banco (`estado_de_postagem`,
`gatilhos_de_codigo`) só lê.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from app.models import BlingOrder
from app.services.atendimento import constantes, ia, validador
from app.services.atendimento import contexto as contexto_svc
from app.services.atendimento.agente import contexto as agente_ctx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.atendimento.agente.contexto import BaseDoGerar, ContextoAgente

logger = structlog.get_logger()

# O estado de postagem do pedido (`postado` no pacote e no `ver_pedido`).
POSTADO_SIM = "sim"
POSTADO_NAO = "nao"
POSTADO_MESMO_DIA = "mesmo_dia"
POSTADOS = (POSTADO_SIM, POSTADO_NAO, POSTADO_MESMO_DIA)
# O pedido cancelado (12 Cancelado, "excluido"): não há envio a segurar.
POSTADO_CANCELADO = "cancelado"
# Situações do Bling (o id cru de `bling_orders.situacao`) em que o pedido
# ainda não saiu: 6 = Em aberto, 21 = Em digitação (etiqueta pronta).
SITUACOES_NAO_POSTADO = frozenset({"6", "21"})
# As que provam que saiu (produção, 20/10/2026): 9 Atendido, 15 e 37 Em
# andamento (o despacho), 83953 Entregue, 83956 Perdimento e as da
# devolução (83957 Aguardando devolução, 83960, 83961, 83966, 84677,
# 545902).
SITUACOES_POSTADO = frozenset(
    {"9", "15", "37", "83953", "83956", "83957", "83960", "83961", "83966", "84677", "545902"}
)
# As do pedido cancelado (`POSTADO_CANCELADO`). Fora das três listas (83955,
# 83962 Verificar cancelamento, sem espelho, sem pedido): o código não sabe —
# `postado` None. Revisão de 20/10/2026: o None conta como "pode não ter
# saído" nos gatilhos 7 e 8 (o pedido que ainda nem entrou no espelho é o
# mais novo, certamente não postado) — o lado seguro.
SITUACOES_CANCELADO = frozenset({"12", "excluido"})
DETALHE_POSTAGEM_DESCONHECIDA = "estado de postagem desconhecido"

# As chaves (`atendimento_fatos.chave`) dos fatos aprovados que trocam um
# gatilho por orientação: a recusa padrão (spec 5.2) e o remetente (fontes §2).
CHAVE_RECUSA_DESCONTO = "recusa_desconto"
CHAVE_RECUSA_BRINDE = "recusa_brinde_amostra"
CHAVE_RECUSA_TROCA_DIFERENCA = "recusa_troca_pagando_diferenca"
CHAVE_REMETENTE = "remetente_etiqueta"
CHAVES_QUE_TROCAM_GATILHO = (
    CHAVE_RECUSA_DESCONTO,
    CHAVE_RECUSA_BRINDE,
    CHAVE_RECUSA_TROCA_DIFERENCA,
    CHAVE_REMETENTE,
)

# Os motivos que vão junto do rótulo (equipe lê; sem dado pessoal, sem 83955).
DETALHE_UF_RESTRITA = "pedido para UF com restrição de envio"
DETALHE_SO_ANEXO = "o cliente mandou o que a loja pediu — a IA não vê anexo"
DETALHE_ANEXO_COM_TEXTO = "chegou foto/vídeo depois de a loja pedir"
DETALHE_DEVOLVIDO = "a transportadora diz devolução ou extravio"
DETALHE_NAO_RECEBEU = "o pedido consta entregue e o cliente diz que não recebeu"
DETALHE_EXTRAVIO = "o cliente fala de extravio ou roubo"
DETALHE_ENTREGA_ERRADA = "o cliente diz que entregaram no lugar errado ou a outra pessoa"
DETALHE_RASTREIO_PARADO = "o cliente diz que o rastreio está parado há dias"
DETALHE_REMETENTE = "dúvida de remetente sem fato aprovado"
# O turno só com o cartão do produto/pedido: nem rascunho (spec 6.2).
MOTIVO_SO_CARTAO = "o cliente só mandou o cartão do produto ou do pedido — sem resposta"

# O porquê do Ag. cancelamento como a EQUIPE lê (o código de
# `ag_cancelamento.classificar`, já no contexto do pedido).
_MOTIVO_AG_CANCELAMENTO = {
    "sem_estoque": "falta de estoque",
    "restricao_envio": "restrição de envio",
    "margem_trava": "trava interna",
    "margem_reprovada": "reprovado pela equipe",
    "pedido_cliente": "pedido do comprador",
    "cancelado_plataforma": "cancelado na plataforma",
    "pos_nf_manual": "movido à mão",
    "manual": "movido à mão",
}

# O anexo "de verdade" (o mesmo de `humano.TIPOS_ANEXO`: não o cartão, não a figurinha).
_TIPOS_ANEXO = ("imagem", "video", "arquivo", "audio")


@dataclass(frozen=True)
class GatilhoPessoa:
    """Um gatilho de código: `codigo` ∈ `constantes.ACOES_PESSOA`.

    `motivo` = a frase curta da coluna `motivo` e da Caixa Humano (sem dado
    pessoal, sem o 83955 por extenso); `antes_do_modelo` = o agente NÃO roda.
    """

    codigo: str
    motivo: str
    antes_do_modelo: bool


@dataclass(frozen=True)
class AntesDoModelo:
    """O que decide, ANTES do pacote e do modelo, que o agente não roda.

    `gatilhos` = os de pessoa sem chamar o modelo (instrução, só anexo depois
    do pedido de evidência): rascunho sem texto para pessoa; `so_cartao` = o
    turno é só o cartão do produto/pedido: NENHUM rascunho (a Caixa Humano
    também não conta esse turno).
    """

    gatilhos: tuple[GatilhoPessoa, ...] = ()
    so_cartao: bool = False

    @property
    def rodar(self) -> bool:
        return not self.gatilhos and not self.so_cartao

    @property
    def motivo(self) -> str | None:
        if self.gatilhos:
            return "; ".join(motivos(self.gatilhos))
        return MOTIVO_SO_CARTAO if self.so_cartao else None


def motivo_da_acao(codigo: str, detalhe: str | None = None) -> str:
    """ "ação de pessoa: <rótulo>" (+ " — detalhe") — o mesmo começo do motivo do modelo."""
    cabeca = f"ação de pessoa: {constantes.rotulo_acao_pessoa(codigo)}"
    return f"{cabeca} — {detalhe}" if detalhe else cabeca


def _gatilho(codigo: str, detalhe: str | None = None, *, antes: bool = False) -> GatilhoPessoa:
    return GatilhoPessoa(codigo, motivo_da_acao(codigo, detalhe), antes)


# ── Estado de postagem ────────────────────────────────────────────────────

# O que a transportadora diz quando o pacote já saiu da loja. "postado"
# SOZINHO é o que `contexto._status_logistica` escreve quando só há o código
# de rastreio (a etiqueta existe antes da postagem): não prova nada.
_SAIU = re.compile(
    r"\b(?:postad[oa]s?|coletad[oa]s?|em\s+transito|em\s+transferencia|saiu\s+para\s+entrega"
    r"|a\s+caminho|despachad[oa]s?|aguardando\s+retirada|entregue|droppedoff|dropped\s+off"
    r"|shipped|in_transit|delivered)\b"
)
# "Embalado, aguardando postagem", "Etiqueta impressa": ainda na loja.
_NAO_SAIU = re.compile(r"\baguardando\s+(?:postagem|coleta|envio)\b|\bnao\s+(?:postad|coletad)")


def _dia(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return (
            (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).astimezone(ia.SAO_PAULO).date()
        )
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            if "T" in valor:
                return _dia(datetime.fromisoformat(valor))
            return date.fromisoformat(valor.strip()[:10])
        except ValueError:
            return None
    return None


def postado_de(
    situacao_id: str | None, logistica: Mapping[str, Any] | None, *, agora: datetime
) -> str | None:
    """`POSTADO_*` (ou `POSTADO_CANCELADO`) do pedido, ou None (sem pedido ou
    estado que o código não conhece). PURA.

    `situacao_id` = o id CRU do Bling (`bling_orders.situacao`, "6"/"21"...),
    nunca o nome; `logistica` = o bloco `ctx["logistica"]` (`data_envio` =
    a postagem). Na ordem: a data de postagem (no dia de `agora` em São
    Paulo = `mesmo_dia`); a transportadora dizendo que saiu (ou entregue);
    a situação do Bling (6/21 = não; as de `SITUACOES_POSTADO` = sim; 12 e
    "excluido" = cancelado).
    """
    log = logistica if isinstance(logistica, Mapping) else {}
    postagem = _dia(log.get("data_envio"))
    if postagem is not None:
        hoje = (agora if agora.tzinfo else agora.replace(tzinfo=UTC)).astimezone(ia.SAO_PAULO)
        return POSTADO_MESMO_DIA if postagem == hoje.date() else POSTADO_SIM
    status = log.get("status")
    if contexto_svc.entregue_da_logistica(dict(log)):
        return POSTADO_SIM
    if isinstance(status, str):
        plano = " ".join(validador.plano_de(status).split())
        if plano != "postado" and _SAIU.search(plano) and not _NAO_SAIU.search(plano):
            return POSTADO_SIM
    situacao = str(situacao_id or "").strip()
    if situacao in SITUACOES_NAO_POSTADO:
        return POSTADO_NAO
    if situacao in SITUACOES_POSTADO:
        return POSTADO_SIM
    if situacao.lower() in SITUACOES_CANCELADO:
        return POSTADO_CANCELADO
    return None


async def estado_de_postagem(
    session: AsyncSession,
    *,
    numero_bling: str | None,
    logistica: Mapping[str, Any] | None,
    agora: datetime | None = None,
) -> str | None:
    """`postado_de` lendo o id cru da situação em `bling_orders` (só SELECT).

    `ctx["pedido"]["situacao"]` pode ser o NOME (`chamados.lookup_pedido`),
    por isso a leitura própria. Sem `numero_bling` = None. Também usado pelo
    `ver_pedido` (A2) — chame sempre como `acao_pessoa.estado_de_postagem(...)`.
    """
    if not (numero_bling or "").strip():
        return None
    situacao = (
        await session.execute(
            select(BlingOrder.situacao)
            .where(BlingOrder.numero == str(numero_bling).strip())
            .order_by(BlingOrder.item_index)
            .limit(1)
        )
    ).scalar_one_or_none()
    return postado_de(situacao, logistica, agora=agora or datetime.now(UTC))


# ── O pedido de evidência da loja ─────────────────────────────────────────

# A loja pede foto/vídeo/print: o verbo do pedido e o que mandar, perto.
_PEDE_EVIDENCIA = re.compile(
    r"\b(?:envi|mand|encaminh|anex|grav|tir|compartilh|faz|fac|registr)[\w-]*\s+"
    r"(?:[\w-]+\s+){0,5}?(?:fotos?|videos?|imagens?|prints?|evidencias?|filmagem)\b"
    r"|\b(?:fotos?|videos?|imagens?|filmagem)\s+(?:[\w-]+\s+){0,4}?"
    r"(?:do|da|dos|das|mostrando|com|que\s+mostre)\s+(?:[\w-]+\s+){0,2}?"
    r"(?:defeito|problema|produto|item|itens|embalagem|caixa|etiqueta|aparelho|pacote|tela"
    r"|que\s+chegou|que\s+recebeu|avaria|dano)\b"
)
_SEM_PRECISAR = re.compile(r"\bnao\s+(?:precisa|e\s+necessari)\w*\b")


def pede_evidencia(texto: str | None) -> bool:
    """O texto (da loja ou do agente) pede foto, vídeo ou print? PURA."""
    plano = validador.plano_de(texto or "")
    return bool(_PEDE_EVIDENCIA.search(plano)) and not _SEM_PRECISAR.search(plano)


def _momento(m: Any) -> datetime | None:
    quando = getattr(m, "enviada_em", None) or getattr(m, "created_at", None)
    if not isinstance(quando, datetime):
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def ultima_fala_real(recentes: Sequence[Any], gatilho: Any | None) -> Any | None:
    """A última RESPOSTA real da loja antes da rajada do cliente (`ia.resposta_da_loja`). PURA.

    Menu, campanha e automática não contam. Sem rajada: a última da conversa.
    """
    rajada = ia.rajada_do_cliente(list(recentes), gatilho)
    ids = {getattr(m, "id", None) for m in rajada}
    inicio = min((q for m in rajada if (q := _momento(m)) is not None), default=None)
    for m in reversed(list(recentes)):
        if getattr(m, "id", None) in ids or not ia.resposta_da_loja(m):
            continue
        quando = _momento(m)
        if inicio is not None and quando is not None and quando > inicio:
            continue
        return m
    return None


def fala_que_pediu_evidencia(recentes: Sequence[Any], gatilho: Any | None) -> Any | None:
    """A última fala real da loja, quando ela pediu foto/vídeo — ou None. PURA."""
    fala = ultima_fala_real(recentes, gatilho)
    return fala if fala is not None and pede_evidencia(getattr(fala, "texto", None)) else None


def pediu_evidencia(recentes: Sequence[Any], gatilho: Any | None) -> bool:
    """A última fala REAL da loja antes da rajada do cliente pediu foto ou vídeo? PURA.

    É a marca `[a loja pediu evidência]` da transcrição do pacote.
    """
    return fala_que_pediu_evidencia(recentes, gatilho) is not None


def midia_da_mensagem(m: Any) -> str | None:
    """A foto/vídeo que veio JUNTO de um texto ("foto", "video") — ou None. PURA.

    O ML grava texto + foto como `tipo="texto"` com a lista `anexos` (o mime:
    "image/jpeg"); o cartão (`tipo` "produto"/"pedido") não conta (revisão de
    20/10/2026: 22 mensagens assim no pós-venda do ML em 60 dias).
    """
    achada = None
    for a in getattr(m, "anexos", None) or []:
        tipo = str(a.get("tipo") or "").lower() if isinstance(a, dict) else ""
        if tipo.startswith("video"):
            return "video"
        if tipo.startswith("image"):
            achada = "foto"
    return achada


def _e_anexo(m: Any) -> bool:
    return (
        (getattr(m, "tipo", None) or "") in _TIPOS_ANEXO
        and not constantes.e_cartao_do_comprador(getattr(m, "payload", None))
    ) or midia_da_mensagem(m) is not None


def _so_anexo(rajada: Sequence[Any]) -> bool:
    """Sem texto e com foto/vídeo/arquivo/áudio de verdade (`humano._so_anexo`)."""
    if not rajada or any((getattr(m, "texto", None) or "").strip() for m in rajada):
        return False
    return any(_e_anexo(m) for m in rajada)


def _so_cartao(rajada: Sequence[Any]) -> bool:
    """Só o cartão do produto/pedido, sem nada escrito (`ia._so_cartao`)."""
    return (
        bool(rajada)
        and all(constantes.e_cartao_do_comprador(getattr(m, "payload", None)) for m in rajada)
        and not any((getattr(m, "texto", None) or "").strip() for m in rajada)
    )


# ── As pistas de texto (sobre a forma de busca: sem acento, minúscula) ────

_DESTINATARIO = re.compile(
    r"\b(?:alter|mud|troc|corrig)\w*\b.{0,40}\b(?:destinatario|cep|complemento"
    r"|numero\s+da\s+(?:casa|residencia))\b"
    r"|\b(?:destinatario|cep)\b.{0,40}\b(?:errad\w*|incorret\w*|alter\w*|mud\w*|troc\w*)\b"
)
_NAO_RECEBEU = re.compile(
    r"\b(?:nao|n|ainda\s+nao)\s+(?:recebi|recebemos|recebeu|chegou|chegaram|entregaram"
    r"|entregou|foi\s+entregue)\b"
    # (revisão "furos") "não veio nada", "não chegou nada" — com o pedido entregue.
    r"|\bnao\s+(?:veio|chegou|apareceu|tem|tinha)\s+nada\b"
    r"|\bconsta\s+(?:como\s+)?entregue\b"
    r"|\b(?:diz|fala|mostra|aparece|esta)\s+(?:que\s+)?(?:foi\s+|esta\s+|como\s+)?entregue\b"
)
# O cliente AFIRMA que o app mostra entregue (revisão de 20/10/2026): com o
# "não recebi" junto, é chamado mesmo com a logística do espelho atrasada (o
# app da plataforma atualiza antes). Só a forma afirmativa, fora de pergunta
# ("já está entregue? não recebi" não conta).
_CONSTA_ENTREGUE = re.compile(
    r"\bconsta\s+(?:como\s+|que\s+(?:foi\s+)?)?entregue\b"
    r"|\b(?:aparece|mostra|diz|fala|informa|esta|ta)\s+(?:que\s+)?(?:foi\s+|como\s+|ja\s+)?"
    r"(?:entregue|recebido)\b"
    r"|\bmarcad[oa]\s+como\s+entregue\b"
    # (revisão "furos") "já marcou como entregue", "colocaram como entregue".
    r"|\b(?:marc|coloc|bot|deu|deram)\w*\s+(?:\w+\s+)?como\s+entregue\b"
)
_SO_NAO_RECEBI = re.compile(
    r"\b(?:nao|n|ainda\s+nao|nunca)\s+(?:recebi|recebemos|chegou|chegaram)\b"
    # (revisão "furos") "não tá aqui", "não veio", "ninguém recebeu".
    r"|\bnao\s+(?:esta|ta|tá)\s+(?:aqui|comigo|em\s+casa)\b|\bnao\s+(?:veio|apareceu)\b"
    r"|\bninguem\s+(?:recebeu|assinou)\b"
)
_FRASES = re.compile(r"[^.!?\n]+[.!?\n]*")


def afirma_consta_entregue(textos: Iterable[str | None]) -> bool:
    """O cliente diz que consta entregue E que não recebeu, fora de pergunta? PURA."""
    plano = validador.plano_de(" ".join(t for t in textos if t))
    frases = [f.strip() for f in _FRASES.findall(plano)]
    afirmadas = [f for f in frases if f and not f.endswith("?")]
    return any(_CONSTA_ENTREGUE.search(f) for f in afirmadas) and bool(_SO_NAO_RECEBI.search(plano))


_EXTRAVIO = re.compile(
    r"\b(?:extravi\w*|roubad[oa]s?|furtad[oa]s?)\b"
    # (revisão "furos") "roubaram meu pacote", "furtaram a encomenda".
    r"|\b(?:roubaram|furtaram|levaram)\s+(?:o\s+|a\s+|meu\s+|minha\s+)?(?:pacote|encomenda"
    r"|pedido|caixa|produto|mercadoria)\b"
)
# O status da transportadora de volta à loja ("Pedido será devolvido ao
# vendedor", "entregue ao remetente") ou perdido — não a devolução do comprador.
_DEVOLVIDO = re.compile(
    r"\b(?:devolvid[oa]\s+ao\s+(?:remetente|vendedor)|sera\s+devolvid[oa]"
    r"|(?:retorn|entregu)\w*\s+ao\s+remetente|extravi\w*|perdimento)\b"
)
# Dinheiro fora do fluxo do app (o agente nunca pede dado bancário).
# O Pix como o dinheiro POR FORA ("me devolve no pix", "pago a diferença no
# pix", "minha chave pix"); o Pix da COMPRA ("paguei no pix, quando cai o
# reembolso?", "aceita pix?", "o pix não caiu") é orientação (revisão de
# 20/10/2026: o reembolso volta pela mesma forma de pagamento, regra 25).
_PIX = re.compile(r"\bpix\b")
_PIX_DA_COMPRA = re.compile(
    r"\b(?:paguei|pagamos|comprei|compramos|pagamento|pago\s+(?=(?:com|no|via|pelo|por)\b)"
    r"|foi\s+pag[oa]|compra|aceit\w*)\s+(?:\w+\s+){0,2}?(?:(?:no|via|com|pelo|por|em)\s+)?pix\b"
    r"|\bpix\s+(?:ainda\s+)?(?:nao\s+)?(?:caiu|cai|foi\s+aprovad\w*|aprovad\w*|confirmad\w*"
    r"|compensad\w*|pendente)\b"
)
_FORA_DA_PLATAFORMA = re.compile(
    r"\b(?:chave\s+pix|deposit\w*|transferencia\s+bancaria|ted|dados\s+bancarios"
    r"|conta\s+(?:bancaria|corrente|poupanca)|poupanca|agencia\s+e\s+conta"
    r"|reembolso\s+parcial|devolv\w+\s+(?:so\s+)?(?:parte|metade)\s+do\s+(?:valor|dinheiro))\b"
    r"|\b(?:pag|compr|vend|negoci|faz|fech|acert|resolv|troc)\w*\s+(?:\w+\s+){0,2}?por\s+fora\b"
)
_DIFERENCA = re.compile(
    r"\b(?:pag\w*|complet\w*|acert\w*|cobr\w*|dou|daria|dar)\s+(?:a\s+|o\s+)?"
    r"(?:diferenca|valor\s+a\s+mais)\b"
    r"|\bdiferenca\s+(?:do|de|no)\s+(?:valor|preco)\b"
)
# "Cupom de desconto", "frete grátis": não são pedido de desconto nem de brinde.
_NAO_E_PEDIDO = re.compile(
    r"\bcupo(?:m|ns)\s+de\s+desconto\b|\bdesconto\s+do\s+cupom\b"
    r"|\b(?:frete|entrega|envio)\s+(?:(?:e|eh|sai|fica|vai\s+ser)\s+)?(?:gratis|de\s+graca)\b"
    # "A troca é grátis?", "a devolução é de graça?": a política, não pedido.
    r"|\b(?:troca|devolucao|reembolso|postagem|envio\s+da\s+devolucao)\s+(?:\w+\s+){0,2}?"
    r"(?:gratis|de\s+graca|gratuit[oa])\b"
)
_PEDE_DESCONTO = re.compile(
    r"\b(?:desconto|descontinho|abatimento|negoci\w+|atacad\w*"
    # A revenda só com a INTENÇÃO de revender (revisão 2 de 20/10/2026:
    # "vocês revendem produtos originais?" é pergunta de produto).
    r"|(?:quero|queria|gostaria\s+de|pretendo|penso\s+em|vou|para|pra)\s+revend\w*"
    r"|(?:preco|valor|tabela|condic\w+)\s+(?:de|para|pra)\s+(?:revenda|revendedor\w*)"
    r"|(?:sou|somos)\s+revendedor\w*"
    # (revisão "furos" de 20/10/2026) o número INTEIRO ("faz por 100", "faz
    # por r$ 250": o `\\d` sozinho só casava um dígito), "faz 2 por 150",
    # "faz por quanto", "quanto fica 3 unidades", "aceita 450", "mais barato",
    # "valor menor", "sai mais em conta".
    r"|faz(?:er|ia)?\s+(?:por|a)\s+(?:menos|r?\s*\$?\s*\d[\d.,]*)"
    r"|faz(?:er|ia)?\s+\d+\s+(?:unidades?\s+|pecas?\s+|und\s+)?(?:por|a)\s+(?:r?\s*\$?\s*)?\d[\d.,]*"
    r"|faz(?:er|ia)?\s+(?:\w+\s+)?por\s+quanto|fica\s+por\s+(?:menos|quanto\s+se)"
    r"|quanto\s+(?:fica|sai|faz|custa|seria)\s+(?:\w+\s+){0,2}?(?:[2-9]|\d{2,}|duas|dois|tres"
    r"|quatro|cinco|dez)\s+(?:unidades?|pecas?|und|itens|aparelhos|malas|celulares|relogios)"
    r"|aceit\w*\s+(?:r\s*\$\s*)?\d[\d.,]*(?!\s*(?:x|vezes|parcelas?|cartao|cartoes|formas?))"
    r"|(?:faz|fazer|faria|consegue|conseguiria|deixa|deixar|sai|fica|tem\s+como|da\s+(?:pra|para)"
    r"\s+(?:fazer|deixar))\s+(?:\w+\s+){0,2}?mais\s+barat\w*"
    r"|(?:valor|preco)\s+(?:um\s+pouco\s+)?(?:menor|mais\s+baixo)|(?:sai|fica)\s+(?:\w+\s+)?"
    r"(?:mais\s+)?em\s+conta"
    r"|(?:levar|levando|comprar|comprando|levo|compro)\s+(?:\w+\s+)?(?:duas|dois|tres|quatro|cinco"
    r"|dez)\b.{0,30}\b(?:barat\w+|desconto|preco|valor|em\s+conta|quanto)"
    r"|(?:mais\s+)?barat\w+\s+(?:pra|para)\s+mim|(?:preco|valor)\s+(?:especial|melhor|camarada)"
    r"|melhor\s+(?:preco|valor)|condic\w+\s+especial|compr\w+\s+(?:em\s+)?quantidade"
    # (revisão 2) "me faz um precinho", "se eu comprar 10 fica mais barato?"
    r"|precinho|(?:fica|sai)\s+(?:mais\s+)?barat\w+|(?:comprar|levar|comprando|levando|compro"
    r"|levo)\s+(?:\w+\s+)?\d+\b.{0,30}\b(?:barat\w+|desconto|preco|valor))\b"
)

# "Baixar o preço" (rodada 4b, 21/10/2026): só o PEDIDO ("baixa o preço pra
# mim", "consegue baixar o valor?", "me abaixa um pouco o preço") — a pergunta
# impessoal sobre o futuro do preço ("vai baixar o preço no fim de novembro?",
# "quando baixa o preço?", "será que o valor vai baixar?") não pede desconto.
_BAIXAR_O_PRECO = re.compile(
    r"\b(?:abaix|baix)\w*\s+(?:um\s+pouc\w*\s+)?(?:o\s+|esse\s+|este\s+|seu\s+)?(?:preco|valor)\b"
)
_BAIXAR_PEDIDO_ANTES = re.compile(
    r"\b(?:consegue|consegues|conseguiria|conseguem|pode|poderia|podem|poderiam|da\s+(?:pra|para)"
    r"|tem\s+como|teria\s+como|me|nos|faz\s+o\s+favor\s+de|quer|aceita)\s+(?:me\s+|nos\s+)?$"
)
_BAIXAR_FUTURO_ANTES = re.compile(
    r"\b(?:vai|vao|ira|irao|quando|sera\s+que(?:\s+vai)?|costuma|costumam|deve|devem"
    r"|pode\s+ser\s+que)\s+(?:o\s+preco\s+|o\s+valor\s+)?$"
)
_BAIXAR_PARA_MIM = re.compile(r"^\s*(?:\w+\s+){0,2}?(?:pra|para)\s+(?:mim|eu|nos|a\s+gente)\b")


def _pede_desconto(plano: str) -> bool:
    """O texto PEDE desconto (`_PEDE_DESCONTO` ou o "baixar o preço" pedido)? PURA."""
    if _PEDE_DESCONTO.search(plano):
        return True
    for m in _BAIXAR_O_PRECO.finditer(plano):
        antes, depois = plano[: m.start()], plano[m.end() :]
        if _BAIXAR_PEDIDO_ANTES.search(antes) or _BAIXAR_PARA_MIM.search(depois):
            return True
        if not _BAIXAR_FUTURO_ANTES.search(antes):
            return True
    return False


# "Grátis"/"de graça" só como pedido ("manda um de graça", "vem algo grátis?"):
# sozinhos, quase sempre são a política ("a troca é grátis?"). O "mimo" também
# só com o verbo de pedir ("manda um mimo"; "obrigada pelo mimo!" não).
_PEDE_BRINDE = re.compile(
    r"\b(?:brindes?|amostras?|cortesia|presente\s+junto)\b"
    r"|\b(?:mand|envi|ganh|vem|dao|da|dar|tem|ter|sai|poe|coloc|bot)\w*\s+(?:\w+\s+){0,3}?"
    r"(?:de\s+graca|gratis|mimos?|mimozinhos?)\b"
)
# As pistas próprias do agente para a régua (revisão 2 de 20/10/2026) — as
# do legado (`ia.pistas_de_categoria`) não pegam as formas comuns e não mudam:
#   7  segurar o envio: "dá pra segurar o envio?", "me arrependi, não envia";
_PEDE_SEGURAR = re.compile(
    r"\bsegur\w*\s+(?:o\s+|meu\s+|esse\s+|este\s+)?(?:envio|pedido|produto|encomenda|postagem"
    r"|despacho)\b"
    r"|\b(?:nao|n)\s+(?:envie|enviem|envia\s+(?:nao|mais|ainda|por\s+favor|pfv|pf)|mande|mandem"
    r"|manda\s+(?:nao|mais|ainda|por\s+favor|pfv|pf)|despache|despachem|poste|postem)\b"
    r"|\b(?:desist\w*|arrepend\w*)\b"
    # (revisão "furos" de 20/10/2026) "mudei de ideia", "comprei por engano/
    # sem querer/errado", "não precisa mais enviar", "quero desfazer a
    # compra", "não quero receber", "não vou mais precisar", "pode parar o
    # envio?" (em produção, 60 dias: 8 com "comprei/pedi errado").
    r"|\bmudei\s+de\s+ideia\b"
    r"|\b(?:comprei|pedi|fiz\s+o\s+pedido|cliquei|finalizei)\s+(?:\w+\s+){0,2}?(?:errad\w*"
    r"|por\s+engano|sem\s+querer|sem\s+perceber|duplicad\w*|duas\s+vezes|repetid\w*)"
    r"|\b(?:nao\s+)?precisa\s+(?:mais\s+)?(?:enviar|mandar|despachar|postar)\b"
    r"|\bdesfaz\w*\s+(?:a\s+|essa\s+|esta\s+|minha\s+|o\s+|esse\s+|meu\s+)?(?:compra|pedido)\b"
    r"|\bnao\s+(?:quero|queria|desejo)\s+(?:mais\s+)?(?:receber|o\s+produto|o\s+pedido|a\s+compra"
    r"|a\s+encomenda|comprar)\b"
    r"|\bnao\s+vou\s+(?:mais\s+)?(?:precisar|querer|usar)\b"
    r"|\b(?:parar|pare|paralis\w*|suspend\w*|interromp\w*|barr\w*|bloque\w*)\s+(?:o\s+|a\s+)?"
    r"(?:envio|entrega|postagem|despacho|pedido)\b"
)
#   8  outro endereço: "o número da minha casa está errado", "pode entregar
#      na casa da minha mãe?";
_PEDE_ENDERECO = re.compile(
    r"\b(?:numero\s+d[aoe]\s+(?:minha\s+|meu\s+)?(?:casa|residencia|predio|endereco|rua|apto"
    r"|apartamento)|complemento|apto|apartamento|bloco|cep|bairro|rua|endereco)\s+(?:\w+\s+){0,4}?"
    r"(?:errad\w*|incorret\w*|faltando|trocad\w*)\b"
    r"|\b(?:outro|novo)\s+endereco\b|\bendereco\s+(?:errad\w*|incorret\w*|antigo|novo|diferente)\b"
    r"|\bentreg\w*\s+(?:\w+\s+){0,2}?(?:na|no|em)\s+(?:casa|endereco|apartamento|trabalho|empresa"
    r"|servico)\s+d[aeo]s?\b"
    r"|\bentreg\w*\s+(?:\w+\s+){0,2}?(?:no|na)\s+(?:meu|minha)\s+(?:trabalho|servico|empresa"
    r"|escritorio)\b"
    # (revisão "furos") mandar/enviar para a casa de alguém ou o trabalho,
    # esqueci/faltou/errei o complemento/número/apto, "mudei de casa", outra
    # cidade, o nome de quem recebe, "coloca o apto 32".
    r"|\b(?:mand|envi|entreg|lev)\w*\s+(?:\w+\s+){0,2}?(?:para|pra|pro|na|no|em)\s+(?:a\s+|o\s+)?"
    r"(?:casa|trabalho|empresa|servico|escritorio|endereco|apartamento)\s+d[aeo]s?\b"
    r"|\b(?:mand|envi|entreg|lev)\w*\s+(?:\w+\s+){0,2}?(?:pro|pra|para\s+o|no|ao)\s+meu\s+"
    r"(?:trabalho|servico|escritorio|emprego)\b"
    r"|\b(?:esqueci|esquecemos|faltou|faltava|falta|errei|erramos|digitei|coloquei|botei)\s+"
    r"(?:de\s+)?(?:colocar\s+|por\s+|botar\s+|informar\s+|preencher\s+)?(?:\w+\s+){0,2}?"
    r"(?:complemento|numero|apto|apartamento|bloco|endereco|cep|bairro|rua|casa|referencia|lote"
    r"|quadra)\b"
    r"|\bmudei\s+de\s+(?:casa|endereco|cidade|apartamento|ape|estado)\b"
    r"|\b(?:mand|envi|entreg)\w*\s+(?:\w+\s+){0,2}?(?:para|pra|pro|em|na|no)\s+outr[ao]\s+"
    r"(?:cidade|estado|endereco|casa|lugar|rua)\b"
    r"|\bnome\s+d[eo]\s+quem\s+(?:vai\s+)?receb\w*|\b(?:mud|troc|alter|corrig)\w*\s+(?:o\s+)?"
    r"(?:nome\s+)?(?:do\s+|de\s+)?(?:destinatario|recebedor|quem\s+recebe)\b"
    r"|\b(?:coloca|coloque|acrescenta|acrescente|adiciona|adicione|inclui|inclua|bota|bote|poe"
    r"|ponha)\s+(?:o\s+|a\s+)?(?:numero\s+(?:do\s+|da\s+)?)?(?:apto|apartamento|bloco|complemento"
    r"|numero|casa|lote|quadra|ap)\b"
)
#   9  a NF no CNPJ: "preciso da nota no meu cnpj";
_PEDE_NF_CNPJ = re.compile(
    r"\b(?:nota|nf|nfe|nota\s+fiscal|danfe)\b.{0,40}\b(?:cnpj|inscricao\s+estadual|razao\s+social)\b"
    r"|\b(?:cnpj|inscricao\s+estadual|razao\s+social)\b.{0,40}\b(?:nota|nf|nfe|danfe)\b"
    # (revisão "furos") a DANFE, o XML, a nota com o nome errado, a nota em
    # nome da empresa (em produção, 60 dias: 3 com NF + empresa/xml/nome errado).
    r"|\b(?:danfe|xml)\b"
    r"|\b(?:nota|nf|nfe|nota\s+fiscal)\b.{0,40}\b(?:nome\s+(?:\w+\s+)?errad\w*|errad\w*"
    r"|incorret\w*|empresa|razao)\b"
    r"|\b(?:em|no)\s+nome\s+d[ae]\s+(?:minha\s+|da\s+|nossa\s+)?empresa\b"
)
#   11 o entregador disse que entregou e não chegou; o pacote sumiu.
_DISSE_QUE_ENTREGOU = re.compile(
    r"\b(?:disse|diz|falou|fala|alega|alegou|informou|informa|marcou|colocou|botou)\s+(?:que\s+)?"
    r"(?:ja\s+)?(?:foi\s+)?(?:entregou|entregaram|entregue|recebid[oa])\b"
)
#   11 (revisão "furos") entregue no lugar errado ou a outra pessoa, o
#      rastreio parado há dias, "roubaram meu pacote".
_ENTREGA_ERRADA = re.compile(
    r"\bentreg\w*\s+(?:\w+\s+){0,2}?(?:na|no|em|para|pra|pro|a)\s+(?:casa|endereco|lugar"
    r"|pessoa|vizinho|vizinha|apartamento|predio|rua|cidade|bloco)\s+errad\w*"
    r"|\bentreg\w*\s+(?:\w+\s+){0,2}?(?:em|no|na|para|pra|pro|a|ao)\s+outr[ao]\s+(?:casa|endereco"
    r"|lugar|pessoa|vizinho|apartamento|predio|rua|cidade|bloco|cliente)\b"
    r"|\bdeix\w*\s+(?:\w+\s+){0,2}?(?:em|no|na|com)\s+outr[ao]\s+(?:lugar|casa|endereco|pessoa"
    r"|vizinho|apartamento|predio)\b"
    r"|\b(?:casa|endereco|apartamento|predio)\s+errad\w*\b"
)
_RASTREIO_PARADO = re.compile(
    r"\b(?:rastreio|rastreamento|pedido|encomenda|pacote|status|codigo)\s+(?:\w+\s+){0,2}?"
    r"(?:parad\w*|parou|nao\s+(?:atualiza|anda|muda|movimenta|mexe|sai\s+do\s+lugar)\w*"
    r"|sem\s+(?:atualiza|movimenta)\w*)\s+(?:\w+\s+){0,2}?(?:ha|faz|tem|desde|a|por)\s+"
    r"(?:\d+|uns|umas|mais\s+de\s+\d+|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|quinze"
    r"|vinte|muitos|varios)\s*(?:dias?|semanas?|mes|meses)\b"
)
_SUMIU = re.compile(
    r"\b(?:pacote|encomenda|pedido|produto|caixa|entrega|mercadoria)\s+(?:\w+\s+){0,2}?"
    r"(?:sumiu|desapareceu|se\s+perdeu|perdeu|foi\s+perdid[oa]|extraviou)\b"
    r"|\bnao\s+(?:achei|acho|encontrei|encontro|localizei)\s+(?:o\s+|a\s+|meu\s+|minha\s+)?"
    r"(?:pacote|encomenda|caixa|mercadoria)\b"
)


def _plano(textos: Iterable[str | None]) -> str:
    return " ".join(validador.plano_de(" ".join(t for t in textos if t)).split())


def _codigos(sinais: Iterable[tuple[str, str]]) -> set[str]:
    return {codigo for codigo, _frase in sinais}


def etiquetas_da_conversa(conversa: Any) -> set[str]:
    """A etiqueta e as secundárias da conversa (`humano._etiquetas`)."""
    saida = {
        e for e in (getattr(conversa, "etiquetas_secundarias", None) or []) if isinstance(e, str)
    }
    if etiqueta := getattr(conversa, "etiqueta", None):
        saida.add(etiqueta)
    return saida


def _avaliacao_ruim(conversa: Any) -> bool:
    """O canal avaliação com nota 1–3 (sem a nota: na dúvida, entra) — `humano._avaliacao_ruim`."""
    if getattr(conversa, "canal", None) != constantes.CANAL_AVALIACAO:
        return False
    dados = getattr(conversa, "dados", None)
    estrelas = dados.get("estrelas") if isinstance(dados, dict) else None
    if isinstance(estrelas, bool) or not isinstance(estrelas, int):
        return True
    return estrelas <= constantes.NOTA_BAIXA_AVALIACAO


def _sinais_da_rajada(
    recentes: Sequence[Any], rajada: Sequence[Any], *, ia_e_o_atendente: bool
) -> set[str]:
    """Os códigos dos sinais como o `humano.triar` calcula (sem o `BaseDoGerar`)."""
    textos = [m.texto for m in rajada if getattr(m, "texto", None)]
    codigos = _codigos(ia.sinais_com_codigo(textos, agente=True))
    if ia_e_o_atendente:
        codigos.discard(constantes.HUMANO_ATENDENTE)
        if ia.insistiu_em_pessoa(list(recentes)):
            codigos.add(constantes.HUMANO_ATENDENTE)
    return codigos


def _antes(
    recentes: Sequence[Any], gatilho: Any | None, codigos: set[str]
) -> tuple[list[GatilhoPessoa], bool]:
    """Os gatilhos que dispensam o modelo e o "só cartão" (ver `gatilhos_antes_do_modelo`)."""
    rajada = ia.rajada_do_cliente(list(recentes), gatilho)
    saida: list[GatilhoPessoa] = []
    if constantes.HUMANO_INSTRUCAO in codigos:
        saida.append(_gatilho(constantes.ACAO_INSTRUCAO, antes=True))
    if _so_anexo(rajada) and pediu_evidencia(recentes, gatilho):
        saida.append(_gatilho(constantes.ACAO_ANALISAR_EVIDENCIA, DETALHE_SO_ANEXO, antes=True))
    return saida, _so_cartao(rajada)


def gatilhos_antes_do_modelo(
    recentes: Sequence[Any],
    gatilho: Any | None,
    *,
    sinais: Iterable[tuple[str, str]] | None = None,
    ia_e_o_atendente: bool = False,
) -> AntesDoModelo:
    """Quando o agente NÃO roda — sem pacote, sem modelo, sem custo. PURA.

    - texto com cara de instrução para a IA → pessoa (`instrucao`);
    - SÓ anexo depois de a loja pedir foto/vídeo → pessoa (`analisar_evidencia`);
    - turno só com o cartão do produto/pedido → nenhum rascunho (spec 6.2).
    Anexo sem pedido antes: o agente roda (pergunta como pode ajudar).
    `sinais` = o `BaseDoGerar.sinais`; None = calcula da rajada.
    """
    rajada = ia.rajada_do_cliente(list(recentes), gatilho)
    codigos = (
        _codigos(sinais)
        if sinais is not None
        else _sinais_da_rajada(recentes, rajada, ia_e_o_atendente=ia_e_o_atendente)
    )
    antes, so_cartao = _antes(recentes, gatilho, codigos)
    return AntesDoModelo(gatilhos=tuple(antes), so_cartao=so_cartao and not antes)


def _planos_do_turno(
    recentes: Sequence[Any], gatilho: Any | None
) -> tuple[list[Any], list[str], str]:
    """(rajada, os textos do turno — as perguntas pendentes ou a rajada —, o plano)."""
    rajada = ia.rajada_do_cliente(list(recentes), gatilho)
    foco = ia.perguntas_pendentes(list(recentes), gatilho)
    textos = [m.texto or "" for m in (foco or rajada)]
    return rajada, textos, _plano([m.texto for m in rajada] + textos)


def pistas_de_orientacao(recentes: Sequence[Any], gatilho: Any | None) -> list[str]:
    """Os assuntos de ORIENTAÇÃO que o turno do cliente toca, seja qual for o
    assunto que o modelo declarou (revisão de 20/10/2026). PURA.

    As pistas de texto (`ia.pistas_de_categoria`) e a opção sensível do menu
    (`ia.assuntos_sensiveis_do_menu`) que estão em `ASSUNTOS_ORIENTACAO`, na
    ordem de lá. Vão em `fatos.agente.pistas_orientacao`: o envio sozinho
    exige TODAS no `auto_orientacao` do canal ("Cadê meu pedido? Se não chegar
    quero cancelar", declarado rastreio, não leva a orientação de
    cancelamento sem o dono ter liberado).
    """
    recentes = list(recentes)
    _rajada, _textos, plano = _planos_do_turno(recentes, gatilho)
    vistas = (
        set(ia.pistas_de_categoria(plano))
        | set(ia.assuntos_sensiveis_do_menu(recentes, gatilho))
        | _pistas_do_agente(plano)
    )
    return [a for a in constantes.ASSUNTOS_ORIENTACAO if a in vistas]


# Rodada 4b (21/10/2026, achado 2 da revisão r4): as pistas FINAS do turno que
# as pistas de categoria do legado não veem — "vocês emitem nota?" (a nota
# fiscal sem o "fiscal") e "qual o prazo pra chegar?" (a chegada: com pedido, o
# rastreio; sem, o prazo antes da compra). Só valem para a liberação do envio
# sozinho (`pistas_do_turno`); o legado não muda.
_PISTA_NOTA = re.compile(
    r"\b(?:emit\w*|emissao|manda\w*|envia\w*|vem|veem|vai|acompanha\w*|tem|sai|preciso\s+d[ae]"
    r"|precisar\s+d[ae]|com)\s+(?:\w+\s+){0,2}?(?:a\s+)?(?:nota|nf|nfe)\b"
    r"(?!\s+(?:\d|de\s+avaliac|da\s+avaliac|alta|baixa|maxima|dez|cinco|mil|10|5)\b)"
    r"|\bnota\s+(?:fiscal|eletronica)\b|\bnf-?e?\b|\bdanfe\b"
)
_PISTA_CHEGADA = re.compile(
    r"\b(?:prazo|quanto\s+tempo|quantos\s+dias|demora\w*|falta\s+muito|previsao)\s+(?:\w+\s+){0,3}?"
    r"(?:pra|para|de|a|da)?\s*(?:cheg\w*|entreg\w*|receb\w*)"
    r"|\bquando\s+(?:\w+\s+){0,2}?(?:chega|chegara|vai\s+chegar|entrega|entregam|recebo|vou\s+receber"
    r"|sera\s+entregue)\b"
    r"|\b(?:chega|chegam|entrega|entregam)\s+(?:\w+\s+){0,2}?(?:em\s+)?(?:quantos\s+dias|quando)\b"
)


def _pistas_finas(plano: str, *, com_pedido: bool) -> set[str]:
    vistas: set[str] = set()
    if _PISTA_NOTA.search(plano):
        vistas.add("nota_fiscal")
    if _PISTA_CHEGADA.search(plano):
        vistas.add("rastreio" if com_pedido else "prazo_antes_compra")
    return vistas


def pistas_do_turno(
    recentes: Sequence[Any], gatilho: Any | None, *, com_pedido: bool = False
) -> list[str]:
    """TODAS as pistas de assunto do turno do cliente, sem filtrar pela orientação
    (rodada 4b, achado 2 da revisão r4). PURA.

    As pistas de categoria (`ia.pistas_de_categoria`), a opção sensível do menu,
    as pistas próprias do agente e as finas (`_pistas_finas`). Vão em
    `fatos.agente.pistas_turno`: o envio sozinho exige cada uma que o assunto
    declarado não cobre liberada no canal (`ia.trava_do_agente`) — num turno
    com dois assuntos, o não liberado não sai junto com o liberado.
    """
    recentes = list(recentes)
    _rajada, _textos, plano = _planos_do_turno(recentes, gatilho)
    vistas = (
        list(ia.pistas_de_categoria(plano))
        + list(ia.assuntos_sensiveis_do_menu(recentes, gatilho))
        + sorted(_pistas_do_agente(plano))
        + sorted(_pistas_finas(plano, com_pedido=com_pedido))
    )
    return list(dict.fromkeys(vistas))


# As lacunas do rascunho que SÃO o assunto (o código de rastreio, a previsão, a
# nota): o rascunho que as usa trata desse assunto, declarado ou não.
_LACUNAS_DO_ASSUNTO = (
    ("{rastreio}", "rastreio"),
    ("{previsao_entrega}", "rastreio"),
    ("{nf_numero}", "nota_fiscal"),
)


def pistas_do_rascunho(texto: str | None) -> list[str]:
    """Os assuntos que o RASCUNHO trata pelas lacunas (rodada 4b). PURA."""
    return list(dict.fromkeys(a for lacuna, a in _LACUNAS_DO_ASSUNTO if lacuna in (texto or "")))


def _pistas_do_agente(plano: str) -> set[str]:
    """Os assuntos que as pistas PRÓPRIAS do agente tocam (revisão 2 de 20/10/2026)."""
    vistas: set[str] = set()
    if _PEDE_SEGURAR.search(plano):
        vistas.add("cancelamento")
    if _PEDE_ENDERECO.search(plano):
        vistas.add("endereco")
    if (
        (_DISSE_QUE_ENTREGOU.search(plano) and _SO_NAO_RECEBI.search(plano))
        or _SUMIU.search(plano)
        or _ENTREGA_ERRADA.search(plano)
        or _RASTREIO_PARADO.search(plano)
    ):
        vistas.add("entrega_nao_recebida")
    if _pede_desconto(_NAO_E_PEDIDO.sub(" ", plano)):
        vistas.add("desconto")
    return vistas


def gatilhos(
    conversa: Any,
    recentes: Sequence[Any],
    ctx: Mapping[str, Any],
    *,
    gatilho: Any | None,
    postado: str | None,
    chaves_ativas: Collection[str],
    sinais: Iterable[tuple[str, str]] | None = None,
    ia_e_o_atendente: bool = False,
    sinais_do_cliente: Collection[str] = (),
    uf_restrita: bool = False,
) -> list[GatilhoPessoa]:
    """Os gatilhos de código da régua 6.3, na ordem dela. PURA (sem banco, sem modelo).

    `conversa` = `AtendimentoConversa` (canal, dados, etiquetas); `recentes`
    e `gatilho` = os do `_gerar`; `ctx` = `contexto_da_conversa`; `postado`
    = `POSTADO_*`/None; `chaves_ativas` = as `CHAVES_QUE_TROCAM_GATILHO`
    com fato ativo; `sinais` = os (código `HUMANO_*`, frase) já ajustados
    pela "IA é o atendente" (o `BaseDoGerar.sinais`) — None = calcula da
    rajada como o `humano.triar` (com `ia_e_o_atendente`);
    `sinais_do_cliente` = os códigos do cartão Cliente; `uf_restrita` = o
    pedido vai para UF com restrição de envio (`gatilhos_de_codigo` lê).
    """
    recentes = list(recentes)
    ctx = ctx if isinstance(ctx, Mapping) else {}
    rajada, textos, plano = _planos_do_turno(recentes, gatilho)
    codigos = (
        _codigos(sinais)
        if sinais is not None
        else _sinais_da_rajada(recentes, rajada, ia_e_o_atendente=ia_e_o_atendente)
    )
    pistas = set(ia.pistas_de_categoria(plano)) | _pistas_do_agente(plano)
    menu = set(ia.assuntos_sensiveis_do_menu(recentes, gatilho))
    etiquetas = etiquetas_da_conversa(conversa)
    pedido = ctx.get("pedido") if isinstance(ctx.get("pedido"), Mapping) else None
    logistica = ctx.get("logistica") if isinstance(ctx.get("logistica"), Mapping) else None
    ativas = set(chaves_ativas)
    saida: list[GatilhoPessoa] = []

    # 1 — Ag. cancelamento (o porquê sem o número da situação).
    if constantes.ETIQUETA_AG_CANCELAMENTO in etiquetas or ia.em_aguardando_cancelamento(
        dict(pedido) if pedido else None
    ):
        bloco = (pedido or {}).get("ag_cancelamento")
        codigo = bloco.get("codigo") if isinstance(bloco, Mapping) else None
        saida.append(_gatilho(constantes.ACAO_AG_CANCELAMENTO, _MOTIVO_AG_CANCELAMENTO.get(codigo)))
    # 2 — alerta (Procon, Justiça, golpe...), xingamento ou a ameaça de
    # reclamação/disputa/mediação (a pista `reclamacao_forte`, que no legado
    # mandava para pessoa pela pista de assunto só-humano; revisão de 20/10/2026).
    if codigos & {constantes.HUMANO_ALERTA, constantes.HUMANO_XINGAMENTO} or (
        "reclamacao_forte" in pistas
    ):
        saida.append(_gatilho(constantes.ACAO_JURIDICO_FRAUDE_OFENSA))
    # 3 — reclamação/mediação aberta.
    if (
        getattr(conversa, "canal", None) == constantes.CANAL_RECLAMACAO
        or constantes.ETIQUETA_RECLAMACAO in etiquetas
        or ia._reclamacoes_abertas(dict(ctx))
        or constantes.reclamacao_aberta(getattr(conversa, "dados", None))
        or "reclamacao_aberta" in sinais_do_cliente
    ):
        saida.append(_gatilho(constantes.ACAO_RECLAMACAO_ABERTA))
    # 4 — avaliação de nota baixa.
    if (
        _avaliacao_ruim(conversa)
        or any(n <= constantes.NOTA_BAIXA_AVALIACAO for n in ia._avaliacoes_pendentes(dict(ctx)))
        or "avaliou_mal" in sinais_do_cliente
    ):
        saida.append(_gatilho(constantes.ACAO_AVALIACAO_BAIXA))
    # 5 e 10 (só anexo) — os que nem chamam o modelo.
    antes, _ = _antes(recentes, gatilho, codigos)
    saida.extend(g for g in antes if g.codigo == constantes.ACAO_INSTRUCAO)
    # 6 — pediu pessoa (o ajuste da "IA é o atendente" já veio nos sinais).
    if constantes.HUMANO_ATENDENTE in codigos:
        saida.append(_gatilho(constantes.ACAO_PEDIU_PESSOA))
    # 7 e 8 — o que ainda dá para fazer antes da postagem. O estado
    # desconhecido (fora do espelho, 83955) conta como "pode não ter saído" —
    # o lado seguro — só quando HÁ pedido (revisão 2 de 20/10/2026: "posso
    # cancelar se demorar?" antes da compra é orientação).
    if pode_nao_ter_saido(postado, conversa, ctx=ctx, recentes=recentes):
        detalhe = None if postado == POSTADO_NAO else DETALHE_POSTAGEM_DESCONHECIDA
        if "cancelamento" in pistas or "cancelamento" in menu:
            saida.append(_gatilho(constantes.ACAO_SEGURAR_ENVIO, detalhe))
        if "endereco" in pistas or "endereco" in menu or _DESTINATARIO.search(plano):
            saida.append(_gatilho(constantes.ACAO_ALTERAR_ENDERECO, detalhe))
    # 9 — arquivo, correção ou reemissão da NF (e a NF no CNPJ).
    if ia.nf_exige_pessoa(textos) or _PEDE_NF_CNPJ.search(plano):
        saida.append(_gatilho(constantes.ACAO_ENVIAR_ARQUIVO_NF))
    # 10 — a evidência que a loja pediu chegou.
    so_anexo = [g for g in antes if g.codigo == constantes.ACAO_ANALISAR_EVIDENCIA]
    if so_anexo:
        saida.extend(so_anexo)
    elif any(_e_anexo(m) for m in rajada) and pediu_evidencia(recentes, gatilho):
        saida.append(_gatilho(constantes.ACAO_ANALISAR_EVIDENCIA, DETALHE_ANEXO_COM_TEXTO))
    # 11 — consta entregue e não chegou, extravio, volta ao remetente.
    status_log = validador.plano_de(str((logistica or {}).get("status") or ""))
    if (
        (
            _NAO_RECEBEU.search(plano)
            and contexto_svc.entregue_da_logistica(dict(logistica) if logistica else None)
        )
        or afirma_consta_entregue([m.texto for m in rajada] + textos)
        or (_DISSE_QUE_ENTREGOU.search(plano) and _SO_NAO_RECEBI.search(plano))
    ):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_NAO_RECEBEU))
    elif _EXTRAVIO.search(plano) or _SUMIU.search(plano):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_EXTRAVIO))
    elif _ENTREGA_ERRADA.search(plano):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_ENTREGA_ERRADA))
    elif _RASTREIO_PARADO.search(plano) and postado not in (POSTADO_NAO, POSTADO_CANCELADO):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_RASTREIO_PARADO))
    elif _DEVOLVIDO.search(status_log):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_DEVOLVIDO))
    # 12 — dinheiro fora do fluxo do app; a "diferença" cede à recusa padrão.
    sem_pedido_falso = _NAO_E_PEDIDO.sub(" ", plano)
    if (
        _FORA_DA_PLATAFORMA.search(sem_pedido_falso)
        or _PIX.search(_PIX_DA_COMPRA.sub(" ", sem_pedido_falso))
        or (_DIFERENCA.search(sem_pedido_falso) and CHAVE_RECUSA_TROCA_DIFERENCA not in ativas)
    ):
        saida.append(_gatilho(constantes.ACAO_APROVAR_FORA_DA_PLATAFORMA))
    # 13 — desconto/atacado e brinde/amostra: cada um cede à SUA recusa padrão.
    if (_pede_desconto(sem_pedido_falso) and CHAVE_RECUSA_DESCONTO not in ativas) or (
        _PEDE_BRINDE.search(sem_pedido_falso) and CHAVE_RECUSA_BRINDE not in ativas
    ):
        saida.append(_gatilho(constantes.ACAO_DESCONTO_BRINDE))
    # 14 — pós-venda para UF com restrição (a UF nunca vai ao motivo). O pedido
    # já CANCELADO não tem mais o que decidir (eval real de 21/10/2026, dev
    # b546657edb4d: o cliente do pedido cancelado pela restrição perguntou do
    # estorno — a orientação do reembolso é a resposta, e a exceção ia junto).
    if uf_restrita and postado != POSTADO_CANCELADO:
        saida.append(_gatilho(constantes.ACAO_EXCECAO, DETALHE_UF_RESTRITA))
    # 15 — remetente sem o fato aprovado.
    if any(ia.duvida_de_remetente(t) for t in textos) and CHAVE_REMETENTE not in ativas:
        saida.append(_gatilho(constantes.ACAO_DADO_INDISPONIVEL, DETALHE_REMETENTE))

    # Na ordem da régua, um por código (o primeiro motivo fica).
    por_codigo: dict[str, GatilhoPessoa] = {}
    for g in saida:
        por_codigo.setdefault(g.codigo, g)
    return [por_codigo[c] for c in constantes.ACOES_PESSOA if c in por_codigo]


def ha_pedido(
    conversa: Any = None, ctx: Mapping[str, Any] | None = None, recentes: Sequence[Any] = ()
) -> bool:
    """A conversa tem pedido: o do contexto, o `pedido_marketplace` ou o cartão
    de pedido no histórico? PURA (revisão 2 de 20/10/2026)."""
    if isinstance(ctx, Mapping) and ctx.get("pedido"):
        return True
    if (getattr(conversa, "pedido_marketplace", None) or "").strip():
        return True
    return any(_cartao_de_pedido(m) for m in recentes)


def _cartao_de_pedido(m: Any) -> bool:
    """A mensagem traz o cartão de PEDIDO (o anexo enriquecido ou o item cru)?"""
    if any(
        isinstance(a, Mapping) and a.get("tipo") == "pedido" and a.get("pedido")
        for a in getattr(m, "anexos", None) or []
    ):
        return True
    payload = getattr(m, "payload", None)
    if not isinstance(payload, Mapping):
        return False
    return (
        payload.get("message_type") == constantes.TIPO_SHOPEE_CARTAO_PEDIDO
        or payload.get("type") == constantes.TIPO_TIKTOK_CARTAO_PEDIDO
    )


def pode_nao_ter_saido(
    postado: str | None,
    conversa: Any = None,
    *,
    ctx: Mapping[str, Any] | None = None,
    recentes: Sequence[Any] | None = None,
) -> bool:
    """O pedido pode ainda não ter saído (os gatilhos 7 e 8)? PURA.

    "nao" sim; None (o código não sabe: fora do espelho, 83955) também — o
    lado seguro —, menos na pergunta pública do anúncio (antes da compra) e,
    com `ctx`/`recentes` (revisão 2 de 20/10/2026), quando a conversa não tem
    pedido nenhum (`ha_pedido`: "posso cancelar se demorar?" antes da compra
    é orientação — o agente pergunta o número); "sim", "mesmo_dia" e
    "cancelado", não.
    """
    if postado == POSTADO_NAO:
        return True
    if postado is not None or getattr(conversa, "canal", None) == constantes.CANAL_PERGUNTA:
        return False
    if ctx is None and recentes is None:
        return True
    return ha_pedido(conversa, ctx, recentes or ())


# ── Os OUTROS pedidos lidos pelas ferramentas (revisão de 20/10/2026) ─────


def gatilhos_de_outro_pedido(
    ctx_pedido: Mapping[str, Any],
    postado: str | None,
    recentes: Sequence[Any],
    gatilho: Any | None,
) -> list[GatilhoPessoa]:
    """Os gatilhos de ESTADO de um pedido que o agente leu por ferramenta. PURA.

    O `ver_pedido`/`ver_garantia`/`sugerir_troca` de OUTRO pedido do comprador
    mostra situação, reclamação e postagem que a régua do pacote (o pedido da
    conversa) não vê: 1 (Ag. cancelamento), 3 (reclamação aberta), 4 (a
    avaliação baixa pendente dele) e, com as pistas do turno, 7 e 8 (pelo
    `postado` DAQUELE pedido) e 11 (o "não recebi" com a logística DELE;
    revisão 2 de 20/10/2026). Devolução/chamado em andamento é da checagem
    (`checagens.conferir_andamento`).
    """
    ctx_pedido = ctx_pedido if isinstance(ctx_pedido, Mapping) else {}
    pedido = ctx_pedido.get("pedido") if isinstance(ctx_pedido.get("pedido"), Mapping) else None
    logistica = (
        ctx_pedido.get("logistica") if isinstance(ctx_pedido.get("logistica"), Mapping) else None
    )
    recentes = list(recentes)
    _rajada, _textos, plano = _planos_do_turno(recentes, gatilho)
    pistas = set(ia.pistas_de_categoria(plano)) | _pistas_do_agente(plano)
    menu = set(ia.assuntos_sensiveis_do_menu(recentes, gatilho))
    saida: list[GatilhoPessoa] = []
    if ia.em_aguardando_cancelamento(dict(pedido) if pedido else None):
        bloco = (pedido or {}).get("ag_cancelamento")
        codigo = bloco.get("codigo") if isinstance(bloco, Mapping) else None
        saida.append(_gatilho(constantes.ACAO_AG_CANCELAMENTO, _MOTIVO_AG_CANCELAMENTO.get(codigo)))
    if ia._reclamacoes_abertas(dict(ctx_pedido)):
        saida.append(_gatilho(constantes.ACAO_RECLAMACAO_ABERTA))
    if any(
        n <= constantes.NOTA_BAIXA_AVALIACAO for n in ia._avaliacoes_pendentes(dict(ctx_pedido))
    ):
        saida.append(_gatilho(constantes.ACAO_AVALIACAO_BAIXA))
    if pedido and (postado == POSTADO_NAO or postado is None):
        detalhe = None if postado == POSTADO_NAO else DETALHE_POSTAGEM_DESCONHECIDA
        if "cancelamento" in pistas or "cancelamento" in menu:
            saida.append(_gatilho(constantes.ACAO_SEGURAR_ENVIO, detalhe))
        if "endereco" in pistas or "endereco" in menu or _DESTINATARIO.search(plano):
            saida.append(_gatilho(constantes.ACAO_ALTERAR_ENDERECO, detalhe))
    if (
        pedido
        and _NAO_RECEBEU.search(plano)
        and contexto_svc.entregue_da_logistica(dict(logistica) if logistica else None)
    ):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_NAO_RECEBEU))
    return saida


async def uf_restrita(session: AsyncSession, conversa: Any, ctx_pedido: Mapping[str, Any]) -> bool:
    """O pedido da conversa vai para UF com restrição? (fontes §3: pós-venda = pessoa).

    As UFs dos anúncios do pedido (`restricao_envio.restricao_do_anuncio`) e
    as regras da loja e da Apple na Shopee (`pedido_para_uf_restrita`). Só
    True/False — a UF nunca sai daqui.
    """
    from app.services.atendimento import ficha as ficha_svc
    from app.services.atendimento import restricao_envio

    pedido = ctx_pedido.get("pedido") if isinstance(ctx_pedido, Mapping) else None
    numero = (pedido or {}).get("numero") if isinstance(pedido, Mapping) else None
    if not numero:
        return False
    plataforma = ficha_svc.plataforma_do_catalogo(getattr(conversa, "plataforma", None))
    ufs: set[str] = set()
    if plataforma is not None:
        dados = getattr(conversa, "dados", None)
        retrato = dados.get("pedido_mkt") if isinstance(dados, dict) else None
        itens = retrato.get("itens") if isinstance(retrato, dict) else None
        anuncios = list(
            dict.fromkeys(
                str(i)
                for i in [
                    *(it.get("item_id") for it in itens or [] if isinstance(it, dict)),
                    getattr(conversa, "anuncio_id", None),
                ]
                if i
            )
        )[:3]
        for item in anuncios:
            restricao = await restricao_envio.restricao_do_anuncio(
                session, plataforma, getattr(conversa, "integration_id", None), item, None
            )
            ufs.update((restricao or {}).get("ufs") or [])
    return await restricao_envio.pedido_para_uf_restrita(
        session, numero=str(numero), ufs_do_anuncio=sorted(ufs)
    )


async def gatilhos_de_codigo(
    session: AsyncSession,
    conversa: Any,
    gatilho: Any,
    base: BaseDoGerar,
    ctx: ContextoAgente,
    *,
    postado: str | None,
) -> list[GatilhoPessoa]:
    """`gatilhos` com o que vem do banco (as chaves ativas por `fatos_loja.chaves_ativas`).

    Também grava `ctx.acoes_codigo` (os códigos, na ordem da régua). Só lê.
    Falha de leitura nunca derruba o agente: sem as chaves, nenhuma recusa
    padrão vale (o lado seguro: pessoa); sem a restrição, segue sem ela.
    """
    from app.services.atendimento.agente import fatos_loja

    chaves: frozenset[str] = frozenset()
    if not ctx.sem_fatos:
        try:
            async with session.begin_nested():
                chaves = await fatos_loja.chaves_ativas(
                    session,
                    CHAVES_QUE_TROCAM_GATILHO,
                    plataforma=conversa.plataforma,
                    integration_id=getattr(conversa, "integration_id", None),
                    agora=ctx.agora(),
                )
        except Exception as exc:  # noqa: BLE001 — sem as chaves: o lado seguro
            logger.warning(
                "atendimento_agente_chaves_falhou",
                conversa_id=str(ctx.conversa_id),
                erro=type(exc).__name__,
            )
            chaves = frozenset()
    restrita = False
    try:
        async with session.begin_nested():
            restrita = await uf_restrita(session, conversa, base.ctx)
    except Exception as exc:  # noqa: BLE001 — a restrição é a mais; nunca derruba
        logger.warning(
            "atendimento_agente_uf_falhou",
            conversa_id=str(ctx.conversa_id),
            erro=type(exc).__name__,
        )
    lista = gatilhos(
        conversa,
        base.recentes,
        base.ctx,
        gatilho=gatilho,
        postado=postado,
        chaves_ativas=chaves,
        sinais=base.sinais,
        sinais_do_cliente=base.sinais_do_cliente,
        uf_restrita=restrita,
    )
    ctx.acoes_codigo = agente_ctx.ordenar_acoes([g.codigo for g in lista])
    ctx.pistas_orientacao = pistas_de_orientacao(base.recentes, gatilho)
    ctx.pistas_turno = pistas_do_turno(
        base.recentes, gatilho, com_pedido=bool((base.ctx or {}).get("pedido"))
    )
    return lista


def juntar(do_codigo: Iterable[str], do_modelo: Iterable[str]) -> list[str]:
    """A união na ordem da régua: o código sempre entra, o modelo só acrescenta.

    É `contexto.ordenar_acoes(do_codigo, do_modelo)`.
    """
    return agente_ctx.ordenar_acoes(do_codigo, do_modelo)


def motivos(lista: Iterable[GatilhoPessoa]) -> list[str]:
    """As frases dos gatilhos para a coluna `motivo` (sem repetir, na ordem)."""
    return list(dict.fromkeys(g.motivo for g in lista if g.motivo))
