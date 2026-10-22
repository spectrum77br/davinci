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
                         e (22/10/2026, itens 22 e 24) a devolução/reembolso
                         que o comprador já abriu na plataforma: o retrato do
                         MESMO pedido em TO_RETURN ou o cartão de devolução da
                         Shopee aberto na conversa (`devolucao_na_plataforma`; o
                         cartão só até a reclamação encerrar depois dele);
  4  avaliacao_baixa     canal avaliação de nota 1–3, avaliação pendente de
                         nota baixa, cartão Cliente "avaliou mal";
  5  instrucao           texto com cara de instrução para a IA (ANTES);
  6  pediu_pessoa        pediu pessoa pela régua do agente (21/10/2026,
                         `pedido_de_pessoa`): recusou o virtual, aceitou a
                         pessoa oferecida, pediu irritado, ou pediu de novo
                         sem nada novo a responder — pedir uma vez não é;
  7  segurar_envio       pista de cancelamento (ou a opção 3 do menu) e o
                         pedido AINDA NÃO postado;
  8  alterar_endereco    pista de endereço/destinatário (ou a opção 4) e o
                         pedido ainda não postado — depois de postado é
                         orientação;
  9  enviar_arquivo_nf   `ia.nf_exige_pessoa`;
  10 analisar_evidencia  anexo do cliente DEPOIS de a loja pedir foto/vídeo
                         (só anexo: ANTES do modelo);
  11 abrir_chamado       "não recebi" com o pedido constando entregue
                         (`pedido_entregue`: a logística OU a situação
                         Entregue do Bling — o mesmo dado do modelo, em toda
                         plataforma, 21/10/2026), extravio, ou o status de
                         volta ao remetente (também o "consta entregue" do
                         retrato do MESMO pedido); e, por ÚLTIMO (22/10/2026,
                         item 22), a entrega atrasada (`atraso_de_entrega`);
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
from app.services.atendimento import cartoes_plataforma, constantes, ia, validador
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
# Item 22 da auditoria ao vivo (22/10/2026 01:27, Barbosa Shopee, rascunho
# 440a83e7): a entrega atrasada — o chamado com a plataforma, sem prometer prazo.
DETALHE_ATRASO = (
    "entrega atrasada e o cliente pede ajuda: abrir chamado com a plataforma para "
    "acompanhar a entrega, sem prometer prazo"
)
# Itens 22 e 24 (22/10/2026): a devolução/reembolso que o comprador já abriu na
# plataforma (o pedido em TO_RETURN, o cartão de devolução da Shopee).
DETALHE_DEVOLUCAO_NA_PLATAFORMA = (
    "o cliente abriu devolução/reembolso na plataforma: responder a solicitação na "
    "central do vendedor"
)
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
    r"\b(?:nao|n|ainda\s+nao)\s+(?:me\s+|nos\s+)?(?:recebi|recebemos|recebeu|chegou|chegaram"
    r"|entregaram|entregou|foi\s+entregue)\b"
    # (revisão "furos") "não veio nada", "não chegou nada" — com o pedido entregue.
    r"|\bnao\s+(?:veio|chegou|apareceu|tem|tinha)\s+nada\b"
    r"|\bconsta\s+(?:como\s+)?entregue\b"
    r"|\b(?:diz|fala|mostra|aparece|esta)\s+(?:que\s+)?(?:foi\s+|esta\s+|como\s+)?entregue\b"
)
# Barbosa ao vivo (22/10/2026 08:33, 8e335ebf): "Não recebi a nota fiscal" com o
# pedido entregue punha o "Abrir chamado (entrega)" ao lado do "Enviar o arquivo da
# NF". Quando o que não chegou é SÓ a nota (e o gatilho 9 já leva a conversa à
# pessoa), o chamado de entrega sai; a conversa segue para pessoa pela NF.
_NAO_RECEBEU_A_NF = re.compile(
    r"\b(?:nao|n|ainda\s+nao)\s+(?:me\s+|nos\s+)?(?:recebi|recebemos|recebeu|chegou|veio)\s+"
    r"(?:a\s+|o\s+|minha\s+|meu\s+)?(?:nota(?:\s+fiscal)?|nf-?e?|danfe|xml)\b"
)


def so_a_nf_nao_chegou(plano: str, codigos: Sequence[str]) -> bool:
    """O "não recebi" é só da nota fiscal e a NF já vai para pessoa (gatilho 9)? PURA."""
    return (
        constantes.ACAO_ENVIAR_ARQUIVO_NF in codigos
        and bool(_NAO_RECEBEU_A_NF.search(plano))
        and not _NAO_RECEBEU.search(_NAO_RECEBEU_A_NF.sub(" ", plano))
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

# O id e o nome da situação "Entregue" do Bling (83953; `garantia.py`).
SITUACAO_ENTREGUE = "83953"


def situacao_entregue(situacao: Any) -> bool:
    """A situação do pedido (o id cru ou o nome) é "Entregue"? PURA (rodada 4b)."""
    texto = " ".join(validador.plano_de(str(situacao or "")).split())
    return texto in (SITUACAO_ENTREGUE, "entregue")


def pedido_entregue(ctx_pedido: Mapping[str, Any] | None) -> bool:
    """O pedido consta ENTREGUE? O MESMO `entregue` que o bloco do pedido mostra ao
    modelo (`pacote.bloco_do_pedido`): a logística (a data ou o status, com a
    correção de `contexto.entregue_da_logistica`) OU a situação Entregue do
    Bling (83953). PURA.

    Eval cego de 21/10/2026 (TikTok, Mini): 2 conversas "consta entregue" + "não
    chegou" saíram sozinhas — o modelo leu `entregue: true` (a situação do
    Bling; o pedido do TikTok costuma não ter a linha da logística) e a régua
    do chamado só olhava a logística. Agora a régua lê o mesmo dado, em toda
    plataforma.
    """
    ctx_pedido = ctx_pedido if isinstance(ctx_pedido, Mapping) else {}
    logistica = ctx_pedido.get("logistica")
    pedido = ctx_pedido.get("pedido")
    return contexto_svc.entregue_da_logistica(
        dict(logistica) if isinstance(logistica, Mapping) else None
    ) or (isinstance(pedido, Mapping) and situacao_entregue(pedido.get("situacao")))


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


# ── A entrega atrasada (item 22 da auditoria ao vivo, 22/10/2026) ─────────
#
# Barbosa Shopee, conversa ce443965…: "Meu pedido não chegou / E a data limite
# foi hoje / Solicito reembolso", o pedido postado em 09/10 e não entregue. Seis
# rascunhos em 4 h 30 (c4228c07, 68f6992d, 4c71796f, 85e722a3, 10064314,
# 440a83e7) responderam "o reembolso é pedido ao receber" — a orientação de quem
# DESISTE de um pedido em dia, não a de quem reclama que não chegou. A prática
# aprovada pelo dono (21/10/2026, Shopee): atraso e o cliente pede ajuda → uma
# pessoa abre o chamado com a plataforma; o agente não promete nada. Desenho da
# revisão (wf_5822ccf2-803, "correcao_ajustada"): o código decide, sem número
# nenhum no pacote (nada de "envio_atrasado" que vire lastro), por último na
# régua 11 (o "devolvido ao remetente" e o "consta entregue" vencem).

# Os dias desde a postagem (São Paulo) a partir dos quais a Shopee conta como
# atraso sem o cliente dizer que a data passou: ~p82 do tempo da coleta até a
# entrega do Shopee Xpress em produção (n=3.960, 60 dias até 21/10/2026: p50 6,0;
# p75 8,3; p90 12,1). Decisão pendente do dono (N=10 e se vale para ML/TikTok).
DIAS_ATRASO_SHOPEE = 10
# O status do retrato da plataforma (`dados.pedido_mkt`) do MESMO pedido que diz
# entregue — o espelho da logística pode estar dias atrasado (3,4% das conversas
# da Shopee com o retrato entregue não têm a entrega na logística nem o 83953).
STATUS_ENTREGUE_NO_RETRATO = frozenset({"TO_CONFIRM_RECEIVE", "COMPLETED"})
STATUS_LOGISTICA_ENTREGUE_NO_RETRATO = frozenset({"LOGISTICS_DELIVERY_DONE", "DELIVERED"})
# O comprador já abriu devolução/reembolso na Shopee ("Em devolução").
STATUS_EM_DEVOLUCAO_NO_RETRATO = frozenset({"TO_RETURN"})

# O cliente reclama que o pedido não chegou ou atrasou. "Não recebi/não chegou"
# com outro objeto (a nota, o código, o reembolso, a mensagem) não é a entrega.
_OBJETO_QUE_NAO_E_A_ENTREGA = (
    r"(?:nota|nf|nfe|danfe|codigo|rastreio|reembolso|estorno|dinheiro|valor|pix|cupom"
    r"|mensage\w*|respost\w*|e-?mail|email|sms|link|boleto|comprovante)"
)
# Revisão da rodada 6 (22/10/2026, os falsos positivos do corpus de 30 dias): "não
# chegou ASSIM", "não veio com o carregador", "não chegou funcionando" — o defeito ou
# o item que falta, não a entrega.
_DO_JEITO_QUE_CHEGOU = (
    r"(?:assim|desse\s+jeito|deste\s+jeito|dessa\s+forma|desta\s+forma|com|sem|funcionando"
    r"|ligando|carregando|cert[oa]|corret[oa]|direito|complet[oa]|quebrad\w*|danificad\w*"
    r"|amassad\w*|errad\w*)"
)
_NAO_CHEGOU = re.compile(
    # Revisão da rodada 6: também "não foi entregue" (o `_NAO_RECEBEU` já o tinha).
    r"\b(?:nao|n|ainda\s+nao|nunca)\s+(?:me\s+|nos\s+)?(?:chegou|chegaram|recebi|recebemos"
    r"|foi\s+entregue)\b"
    r"(?!\s+(?:(?:a|o|as|os|meu|minha|meus|minhas|nenhum|nenhuma|nem)\s+)?"
    rf"{_OBJETO_QUE_NAO_E_A_ENTREGA}\b)"
    rf"(?!\s+{_DO_JEITO_QUE_CHEGOU}\b)"
    # "Não veio" só sozinho ou com o pedido ("não veio o carregador" é item faltando).
    r"|\b(?:nao|n|ainda\s+nao|nunca)\s+(?:me\s+)?(?:veio|vieram)\b(?=\s*(?:$|[.,;!?]|ainda\b"
    r"|ate\b|nada\b|(?:(?:o|a)\s+)?(?:meu\s+|minha\s+)?(?:pedido|encomenda|pacote|compra)\b))"
    # Revisão da rodada 6: "não entregaram meu pedido" — também só sozinho ou com o
    # pedido ("não entregaram o carregador" é item faltando).
    r"|\b(?:nao|n|ainda\s+nao|nunca)\s+(?:me\s+|nos\s+)?(?:entregaram|entregou)\b"
    r"(?=\s*(?:$|[.,;!?]|ainda\b|ate\b|nada\b|(?:(?:o|a)\s+)?(?:meu\s+|minha\s+)?"
    r"(?:pedido|encomenda|pacote|compra|produto|celular|aparelho|mercadoria)\b))"
)
# O objeto logo ANTES do "não chegou" ("a nota fiscal não chegou", "o reembolso
# ainda não chegou"): não é a entrega. "Quero reembolso pois não chegou" é.
_OBJETO_ANTES = re.compile(
    rf"\b{_OBJETO_QUE_NAO_E_A_ENTREGA}(?:\s+(?:fiscal|eletronica|de\s+rastreio|do\s+rastreio"
    r"|do\s+pedido|da\s+compra))?\s+(?:ainda\s+|tambem\s+|ate\s+agora\s+)?$"
)
_RECLAMA_ATRASO = re.compile(
    # Revisão da rodada 6: "cadê O MEU pedido" (o determinante e o possessivo juntos).
    r"\bcade\s+(?:(?:o|a|os|as)\s+)?(?:meu\s+|minha\s+|meus\s+|minhas\s+)?(?:pedido|encomenda"
    r"|pacote|produto|compra|entrega|celular|aparelho|mercadoria)s?\b"
    r"|\b(?:pedido|encomenda|pacote|entrega|rastreio|envio)\s+(?:\w+\s+){0,2}?parad[oa]\b"
    # "Era pra ter chegado", "deveria ter chegado": a reclamação e a data juntas.
    r"|\b(?:era|eram|deveria|deveriam|devia)\s+(?:pra\s+|para\s+)?(?:ter\s+)?(?:chegado"
    r"|sido\s+entregue)\b"
)
# "Atrasado", "demorando": a demora DA ENTREGA. Revisão da rodada 6 (o corpus de 30
# dias): "o reembolso está demorando", "demorando demais a devolução", "demorando
# para confirmar", "demorou pra desligar" — a demora de outra coisa (`_demora_da_entrega`).
_DEMORA = re.compile(
    r"\b(?:atras(?:ad[oa]s?|ou|o|aram|ando)|demor(?:ando|ou|ado|ada|a\s+muito|a\s+demais))\b"
)
# Só o que é a ENTREGA (o "celular" e o "aparelho" também são o defeito: "o celular
# demorou pra desligar").
_SUJEITO_DA_ENTREGA = re.compile(
    r"\b(?:pedido|encomenda|pacote|entrega|envio|compra|mercadoria|rastreio|frete"
    r"|transportadora|correios?)\b"
)
_OBJETO_DA_DEMORA = re.compile(
    rf"\b(?:{_OBJETO_QUE_NAO_E_A_ENTREGA}|devoluc\w*|troca|confirm\w*|respond\w*|atend\w*"
    r"|aprova\w*|analis\w*|carreg\w*|deslig\w*|(?:re)?lig(?:ar|a|ando)|(?:re)?inici\w*"
    r"|abri\w*|instal\w*|baix\w*|atualiz\w*|estorn\w*)\b"
)
# A data que já passou (o "previsão/prazo passou", "a data limite foi hoje", "era
# pra ter chegado"). Sem o marcador de passado, "qual a data limite pra chegar?"
# não é atraso. Revisão da rodada 6 (22/10/2026): o "hoje" sem verbo no passado ("a
# data limite é hoje", "deveria chegar hoje") só vale do fim do dia em diante
# (`_PRAZO_HOJE`, `HORA_FIM_DO_DIA`); a data que MUDOU ("foi atualizada pro dia 30",
# "a previsão mudou") não é a que passou; "qual era a data limite?" é pergunta.
_DATA_DO_PRAZO = r"data\s+(?:limite|prevista|maxima|estimada|de\s+entrega|da\s+entrega)"
_NAO_MUDOU = (
    r"(?!\s+(?:alterad|atualizad|mudad|prorrogad|adiad|estendid|remarcad|reagendad"
    r"|postergad)\w*)"
)
_PASSADO = rf"(?:(?:foi|era){_NAO_MUDOU}|passou|venceu|vencid[oa]|expirou|acabou|estourou)"
_PRAZO_VENCIDO = re.compile(
    rf"\b{_DATA_DO_PRAZO}\b[^.?!\n]{{0,30}}?\b(?:{_PASSADO}|ontem|anteontem)\b"
    rf"|(?<!qual\s)(?<!quando\s)\b(?:{_PASSADO}|ontem)\s+(?:\w+\s+){{0,2}}?(?:a\s+)?"
    rf"{_DATA_DO_PRAZO}\b"
    r"|\b(?:previsao|prazo)\s+(?:\w+\s+){0,3}?(?:ja\s+)?(?:passou|venceu|vencid[oa]|acabou"
    r"|expirou|estourou)\b"
    r"|\b(?:passou|venceu|acabou|estourou|expirou)\s+(?:d[oa]\s+|o\s+|a\s+)?(?:prazo|previsao"
    r"|data)\b"
    r"|\b(?:era|eram|era\s+pra|era\s+para|deveria|deveriam|devia)\s+(?:pra\s+|para\s+)?"
    r"(?:ter\s+)?(?:chegado|chegar\s+(?:ate\s+)?(?:ontem|anteontem)|sido\s+entregue|entregue)\b"
    # Revisão da rodada 6: "estava prevista para ontem", "o prazo era até ontem".
    r"|\b(?:previst[oa]|prazo)\s+(?:\w+\s+){0,2}?(?:para|pra|pro|era|ate)\s+(?:ate\s+)?"
    r"(?:ontem|anteontem)\b"
)
# O "hoje" sem verbo no passado ("a data limite é hoje", "deveria chegar hoje"): a
# entrega ainda pode chegar no dia — só vale a partir de `HORA_FIM_DO_DIA` (São Paulo).
HORA_FIM_DO_DIA = 18
_PRAZO_HOJE = re.compile(
    rf"\b{_DATA_DO_PRAZO}\b[^.?!\n]{{0,30}}?\bhoje\b"
    rf"|\bhoje\s+(?:\w+\s+){{0,2}}?(?:a\s+)?{_DATA_DO_PRAZO}\b"
    r"|\b(?:era|eram|deveria|deveriam|devia)\s+(?:pra\s+|para\s+)?(?:ter\s+)?chegar\s+"
    r"(?:ate\s+)?hoje\b"
    r"|\b(?:previst[oa]|prazo)\s+(?:\w+\s+){0,2}?(?:para|pra|pro|era|e|ate)\s+(?:ate\s+)?hoje\b"
)
# O prazo À FRENTE dito pelo próprio cliente ("o prazo vai até sexta", "a data limite
# é até dia 30"): a régua dos dias da Shopee não vale (revisão da rodada 6).
_DIA_DA_SEMANA = r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-?feira)?"
_PRAZO_A_FRENTE = re.compile(
    r"\b(?:prazo|previsao|estimativa|entrega|data(?:\s+(?:limite|prevista|maxima|estimada"
    r"|de\s+entrega|da\s+entrega))?)\s+(?:\w+\s+){0,2}?(?:vai|e|fica|seria)\s+ate\s+"
    r"(?:(?:a|o)\s+)?(?:proxim[oa]\s+)?"
    rf"(?P<quando>depois\s+de\s+amanha|amanha|{_DIA_DA_SEMANA}|dia\s+\d{{1,2}}(?:/\d{{1,2}})?"
    r"|\d{1,2}/\d{1,2})\b"
)
# A condição do PRÓPRIO cliente (o 7f0c1b4e: "assim que chegar posso devolver?"):
# ele espera a chegada — não é reclamação de atraso.
# "Quando VAI chegar?" é pergunta, não condição: o verbo auxiliar no meio não vale.
# Revisão da rodada 6 (22/10/2026): nem "quando devo/posso/irei/consigo receber?",
# nem a oração que termina na pergunta ("quando o pedido chegar?").
_CONDICAO_DO_CLIENTE = re.compile(
    r"\b(?:assim\s+que|quando|depois\s+que|logo\s+que)\s+"
    r"(?:(?!(?:vai|vao|ira|irao|irei|vou|devo|devem|deve\w*|posso|pode\w*|consig\w*|consegu\w*"
    r"|sera|serei|terei|tenho)\b)\w+\s+){0,3}?"
    r"(?:chegar|chegarem|receber|recebermos|for\s+entregue)\b(?!\s*\?)"
)


def _nao_chegou_a_entrega(plano: str) -> bool:
    """O "não chegou/não recebi/não veio/não entregaram" sem outro objeto antes ou depois."""
    return any(
        not _OBJETO_ANTES.search(plano[max(0, m.start() - 40) : m.start()])
        for m in _NAO_CHEGOU.finditer(plano)
    )


def _demora_da_entrega(plano: str, m: re.Match[str]) -> bool:
    """O "atrasado/demorando" é da entrega? O pedido logo antes (até 3 palavras) diz que
    sim; outra coisa perto (até 3 palavras antes ou depois: o reembolso, a devolução, a
    resposta, ligar/carregar) diz que não. Revisão da rodada 6 (22/10/2026).

    Revisão 2 da rodada 6: só as 3 palavras de cada lado (`rsplit`/`split` com o
    limite) — o `split()` do texto inteiro a cada ocorrência deixava a régua
    quadrática no texto longo (10 mil caracteres: 62 ms no `gatilhos`)."""
    antes = " ".join(plano[: m.start()].rsplit(None, 3)[-3:])
    depois = " ".join(plano[m.end() :].split(None, 3)[:3])
    if _SUJEITO_DA_ENTREGA.search(antes):
        return True
    return not _OBJETO_DA_DEMORA.search(f"{antes} {depois}")


def reclama_que_nao_chegou(plano: str) -> bool:
    """O cliente reclama que o pedido não chegou ou atrasou (o texto na forma de
    busca: sem acento, minúsculo)? PURA. Item 22 (22/10/2026).

    "Não chegou/não recebi/não veio/não foi entregue/não entregaram" sem outro
    objeto (a nota, o código, o reembolso, a mensagem — antes ou depois; nem o
    "não chegou assim" do defeito), "cadê (o) meu pedido", o pedido "parado", e o
    "atrasado"/"demorando" da entrega (não o do reembolso ou da devolução).
    """
    if _nao_chegou_a_entrega(plano) or _RECLAMA_ATRASO.search(plano):
        return True
    return any(_demora_da_entrega(plano, m) for m in _DEMORA.finditer(plano))


def _hora_em_sao_paulo(agora: datetime) -> int:
    return (agora if agora.tzinfo else agora.replace(tzinfo=UTC)).astimezone(ia.SAO_PAULO).hour


def prazo_vencido(plano: str, agora: datetime | None = None) -> bool:
    """O cliente diz que a data limite ou a previsão já passou? PURA. Item 22.

    Revisão da rodada 6 (22/10/2026): com `agora`, o "hoje" sem verbo no passado
    ("a data limite é hoje") também vale — do fim do dia (`HORA_FIM_DO_DIA`) em diante.
    """
    if _PRAZO_VENCIDO.search(plano):
        return True
    return (
        agora is not None
        and _hora_em_sao_paulo(agora) >= HORA_FIM_DO_DIA
        and bool(_PRAZO_HOJE.search(plano))
    )


def cita_prazo_a_frente(plano: str, hoje: date) -> bool:
    """O próprio cliente diz que o prazo vai até um dia que ainda não chegou ("o prazo
    vai até sexta", "é até dia 30")? PURA. Revisão da rodada 6 (22/10/2026)."""
    for m in _PRAZO_A_FRENTE.finditer(plano):
        numeros = [int(n) for n in re.findall(r"\d{1,2}", m.group("quando"))]
        if not numeros:
            return True  # amanhã, sexta: à frente
        try:
            dia = date(hoje.year, numeros[1] if len(numeros) > 1 else hoje.month, numeros[0])
        except ValueError:
            continue
        if dia >= hoje:
            return True
    return False


def condicao_do_cliente(plano: str) -> bool:
    """O cliente fala da chegada como condição ("assim que chegar…")? PURA. Item 22."""
    return bool(_CONDICAO_DO_CLIENTE.search(plano))


def _instante(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        quando = valor
    elif isinstance(valor, str) and valor.strip():
        try:
            quando = datetime.fromisoformat(valor.strip())
        except ValueError:
            return None
    else:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def retrato_do_pedido(
    conversa: Any, ctx: Mapping[str, Any] | None, *, agora: datetime | None = None
) -> Mapping[str, Any] | None:
    """O retrato da plataforma (`dados.pedido_mkt`) só se for do MESMO pedido da
    conversa (o `pedido_marketplace`, o número do contexto, o pack/order do ML). PURA.

    Com `agora` (o `as_of` do replay), o retrato lido DEPOIS dele não vale: o
    replay das 20:47 não sabe do TO_RETURN das 01:25.
    """
    dados = getattr(conversa, "dados", None)
    retrato = dados.get("pedido_mkt") if isinstance(dados, Mapping) else None
    if not isinstance(retrato, Mapping):
        return None
    lido = _instante(retrato.get("atualizado_em"))
    if agora is not None and lido is not None:
        if lido > (agora if agora.tzinfo else agora.replace(tzinfo=UTC)):
            return None
    numero = str(retrato.get("pedido") or "").strip().lower()
    pedido = (ctx or {}).get("pedido") if isinstance(ctx, Mapping) else None
    pedido = pedido if isinstance(pedido, Mapping) else {}
    chaves = {
        str(c).strip().lower()
        for c in (
            getattr(conversa, "pedido_marketplace", None),
            pedido.get("numeroloja"),
            pedido.get("numero"),
            dados.get("pack_id"),
            dados.get("order_id"),
        )
        if c
    }
    return retrato if numero and numero in chaves else None


def _status_do_retrato(retrato: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(retrato, Mapping):
        return "", ""
    logistica = retrato.get("logistica")
    status_log = logistica.get("status") if isinstance(logistica, Mapping) else None
    return str(retrato.get("status") or "").strip().upper(), str(status_log or "").strip().upper()


def entregue_no_retrato(retrato: Mapping[str, Any] | None) -> bool:
    """O retrato do pedido diz entregue (Shopee: TO_CONFIRM_RECEIVE, COMPLETED ou a
    logística LOGISTICS_DELIVERY_DONE; ML: o envio "delivered")? PURA. Item 22."""
    status, status_log = _status_do_retrato(retrato)
    return status in STATUS_ENTREGUE_NO_RETRATO or status_log in (
        STATUS_LOGISTICA_ENTREGUE_NO_RETRATO
    )


def em_devolucao_no_retrato(retrato: Mapping[str, Any] | None) -> bool:
    """O retrato do pedido está em devolução (Shopee TO_RETURN)? PURA. Item 22."""
    return _status_do_retrato(retrato)[0] in STATUS_EM_DEVOLUCAO_NO_RETRATO


def devolucao_na_plataforma(
    conversa: Any,
    recentes: Sequence[Any],
    ctx: Mapping[str, Any] | None,
    *,
    agora: datetime | None = None,
) -> bool:
    """O comprador já abriu devolução/reembolso na plataforma? PURA. Itens 22 e 24.

    O retrato do MESMO pedido em TO_RETURN (a Shopee: "Em devolução" — às 01:25 o
    pedido do ce443965 já estava assim) ou um cartão de devolução/reembolso da
    Shopee ABERTO na conversa (`cartoes_plataforma.devolucao_aberta_nos_cartoes`:
    às 01:37, "Não recebi meu pacote / Apenas Reembolso"). A loja responde a
    solicitação na Central do Vendedor: é a `reclamacao_aberta` da régua.

    Revisão da rodada 6 (22/10/2026, o replay de 90 dias): o cartão é só a PONTE
    até a reclamação chegar ao nosso banco (a sincronização leva ~1 h). Muita
    solicitação encerra sem cartão de encerramento no chat (o "Retornado -
    Pendente validação do vendedor" fica para sempre o último): o cartão que tem,
    no contexto, uma reclamação/devolução da plataforma encerrada DEPOIS dele (e
    até `agora`) já não vale (`_encerrada_depois`) — o 3571bd57 (aceita), o
    4107ce81 e o f706abaf (canceladas pelo comprador) seguravam todo turno.
    """
    if em_devolucao_no_retrato(retrato_do_pedido(conversa, ctx, agora=agora)):
        return True
    ctx = ctx if isinstance(ctx, Mapping) else {}
    return any(
        not _encerrada_depois(ctx, quando, agora)
        for quando in cartoes_plataforma.devolucoes_abertas_nos_cartoes(recentes)
    )


def _encerrada_depois(
    ctx: Mapping[str, Any], desde: datetime | None, agora: datetime | None
) -> bool:
    """Alguma reclamação/devolução da plataforma do contexto (`ctx["reclamacoes"]`)
    encerrou depois de `desde` (o cartão) e até `agora`? Sem a hora do cartão, não. PURA."""
    desde = _instante(desde)
    if desde is None:
        return False
    agora = _instante(agora)
    for r in ctx.get("reclamacoes") or []:
        fim = _instante(r.get("encerrada_em")) if isinstance(r, Mapping) else None
        if fim is not None and fim > desde and (agora is None or fim <= agora):
            return True
    return False


def _hoje(agora: datetime) -> date:
    return (agora if agora.tzinfo else agora.replace(tzinfo=UTC)).astimezone(ia.SAO_PAULO).date()


def dias_desde_o_envio(ctx: Mapping[str, Any] | None, agora: datetime) -> int | None:
    """Os dias (São Paulo) desde a postagem: a data da logística ou, sem ela, o
    `enviado_em` do pedido (na Shopee, o do retrato do mesmo pedido —
    `contexto._ajustar_data_envio_shopee`); None sem a data. PURA. Item 22."""
    ctx = ctx if isinstance(ctx, Mapping) else {}
    logistica = ctx.get("logistica") if isinstance(ctx.get("logistica"), Mapping) else {}
    pedido = ctx.get("pedido") if isinstance(ctx.get("pedido"), Mapping) else {}
    envio = _dia(logistica.get("data_envio")) or _dia(pedido.get("enviado_em"))
    return None if envio is None else (_hoje(agora) - envio).days


def previsao_vencida(ctx: Mapping[str, Any] | None, agora: datetime) -> bool:
    """A previsão de entrega da logística (na Amazon, o "entregar até") já passou
    — o dia dela acabou? PURA. Item 22."""
    ctx = ctx if isinstance(ctx, Mapping) else {}
    logistica = ctx.get("logistica") if isinstance(ctx.get("logistica"), Mapping) else {}
    previsao = _dia(logistica.get("previsao"))
    return previsao is not None and previsao < _hoje(agora)


def atraso_de_entrega(
    conversa: Any,
    ctx: Mapping[str, Any] | None,
    plano: str,
    *,
    postado: str | None,
    agora: datetime,
) -> bool:
    """A entrega atrasada que vira chamado (o `DETALHE_ATRASO` da régua 11). PURA.

    Item 22 da auditoria ao vivo (22/10/2026), o desenho ajustado pela revisão.
    Todas juntas:
      a) há pedido, postado ("sim"/"mesmo_dia") e não entregue — nem pela
         logística/Bling (`pedido_entregue`) nem pelo retrato do MESMO pedido;
      b) o cliente reclama que não chegou ou atrasou (`reclama_que_nao_chegou`);
      c) não é a condição do próprio cliente ("assim que chegar…", o 7f0c1b4e) —
         revisão da rodada 6: quem diz que a data passou reclama, com ou sem o
         "quando chegar";
      d) o pedido não tem chamado aberto (seria o segundo);
      e) uma destas: o cliente diz que a data/previsão passou (`prazo_vencido`,
         qualquer plataforma — a regra rastreio do manual já dizia pessoa); na
         Shopee, `DIAS_ATRASO_SHOPEE` dias ou mais desde a postagem, salvo o
         prazo À FRENTE dito pelo próprio cliente (`cita_prazo_a_frente`,
         revisão da rodada 6); na Amazon, o "entregar até" passou.
    """
    ctx = ctx if isinstance(ctx, Mapping) else {}
    if not ctx.get("pedido") or postado not in (POSTADO_SIM, POSTADO_MESMO_DIA):
        return False
    if pedido_entregue(ctx) or entregue_no_retrato(retrato_do_pedido(conversa, ctx, agora=agora)):
        return False
    if not reclama_que_nao_chegou(plano):
        return False
    vencido = prazo_vencido(plano, agora)
    if condicao_do_cliente(plano) and not vencido:
        return False
    if ctx.get("chamados"):
        return False
    if vencido:
        return True
    plataforma = getattr(conversa, "plataforma", None)
    if plataforma == "shopee":
        if cita_prazo_a_frente(plano, _hoje(agora)):
            return False
        dias = dias_desde_o_envio(ctx, agora)
        return dias is not None and dias >= DIAS_ATRASO_SHOPEE
    if plataforma == "amazon":
        return previsao_vencida(ctx, agora)
    return False


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


# ── O pedido de pessoa no caminho do agente (decisão do dono, 21/10/2026) ─
#
# "Sobre esse aí ele conseguiria responder automaticamente, como se ele fosse o
# atendente; se ele puder e conseguir, ele mesmo responde" (Eduardo, 21/10/2026,
# no chat). O caso real (Kia Shopee, 20–21/10): o comprador perguntou se o pote
# de 2,5 L esgotado ia voltar ao estoque, escolheu a opção 6 do menu ("Falar
# com Atendente"), recebeu o "Em que podemos ajudar?", repetiu a pergunta,
# reclamou que não teve resposta e escolheu o 6 de novo — a régua mandou para
# pessoa com "cliente insistiu em falar com uma pessoa", e o agente sabia
# responder (esgotado no anúncio, sem previsão de reposição).
#
# No canal com o AGENTE, pedir atendente (por texto, "tem alguém aí?", a opção
# 6 do menu, pedir duas vezes) NÃO é, por si, motivo de pessoa: o agente
# responde as perguntas pendentes como o atendimento da loja e, sem pergunta,
# pergunta como pode ajudar. A pergunta sincera "é robô?" é pergunta (o agente
# responde com honestidade: o atendimento virtual da loja, e uma pessoa pode
# assumir se o cliente preferir), não pedido. O pedido de pessoa ainda vai para
# pessoa (`pediu_pessoa`, `pedido_de_pessoa`) só quando:
#   - o cliente recusa o atendimento virtual ("não quero falar com robô");
#   - a loja já disse que é o atendimento virtual e ofereceu a pessoa, e o
#     cliente pede a pessoa (ou só diz "sim", "quero", "prefiro");
#   - o pedido vem com irritação (as palavras fortes, o grito, o "já pedi 3
#     vezes") ou com alerta, xingamento ou reclamação — estes três já são
#     `juridico_fraude_ofensa`, que continua igual;
#   - o pedido é o 3º seguido (`_TETO_DE_PEDIDOS`: este turno e os dois
#     anteriores do cliente, cada um fechado por uma resposta da loja, pediram
#     pessoa), mesmo com pergunta nova — o teto contra o laço;
#   - depois de uma resposta DE VERDADE da loja (não o "em que podemos
#     ajudar?"), o cliente diz que ela não respondeu ("você não respondeu a
#     minha pergunta") e pede pessoa, sem pergunta nova;
#   - o cliente pede DE NOVO depois de a loja já ter respondido um pedido dele
#     e o turno não traz nada novo a responder (`tem_o_que_responder`: nem
#     pergunta nova, nem a pergunta que ficou sem resposta — a dos turnos que
#     a loja fechou só com um "em que podemos ajudar?"). A pergunta que repete
#     a de um turno já respondido de verdade não é nova (revisão de 21/10/2026:
#     "o pote vai voltar? quero falar com atendente" cinco vezes, com a
#     resposta real entre uma e outra, era um laço sem saída).
# O legado (e o canal sem o agente) segue com `ia.insistiu_em_pessoa` e a "IA é
# o atendente" de 14/10/2026, sem mudança. O assunto da resposta é o da
# pergunta (o prompt; o manual base já diz "se há pergunta sem resposta,
# classificar pela pergunta"): a trava do envio sozinho decide como sempre.

MOTIVO_RECUSOU_VIRTUAL = "cliente insistiu em falar com uma pessoa (recusou o atendimento virtual)"
MOTIVO_ACEITOU_A_PESSOA = (
    "cliente insistiu em falar com uma pessoa (pediu a pessoa depois de saber que é o "
    "atendimento virtual)"
)
MOTIVO_PEDIU_IRRITADO = "cliente insistiu em falar com uma pessoa, com irritação ou reclamação"
MOTIVO_PEDIU_3_VEZES = "cliente insistiu em falar com uma pessoa (3º pedido seguido)"
MOTIVO_NAO_RESOLVEU = (
    "cliente insistiu em falar com uma pessoa (disse que a resposta da loja não respondeu)"
)
# O teto: o pedido de pessoa em tantos turnos seguidos do cliente vai para
# pessoa mesmo com pergunta nova (revisão de 21/10/2026).
_TETO_DE_PEDIDOS = 3

# Os verbos que vêm antes do pedido ("quero falar com…", "tem como falar com…").
_VERBOS_DO_PEDIDO = (
    r"(?:\b(?:eu\s+)?(?:quero|queria|gostaria(?:\s+de)?|preciso(?:\s+de)?|precisava(?:\s+de)?"
    r"|necessito(?:\s+de)?|desejo|prefiro|tem\s+como|da\s+(?:pra|para)|consigo|posso|pode"
    r"|poderia|podia|vou\s+querer)\s+(?:me\s+)?)?"
)
# O vocabulário do pedido que o `ia._ATENDENTE` (o do legado) não tem.
_PEDE_PESSOA_AGENTE = re.compile(
    r"\balguem\s+(?:\w+\s+){0,2}?(?:me\s+)?(?:atende\w*|respond\w*|ajud\w*)\b"
    r"|\b(?:quero|queria|preciso|gostaria\s+de)\s+(?:ser\s+)?atendid[oa]\b"
    r"|\bcade\s+(?:o\s+|a\s+|os\s+|as\s+)?(?:atendente\w*|atendimento|vendedor\w*|responsavel)\b"
    r"|\b(?:chama|chame|chamar|passa|passe|passar|transfere|transfira|transferir|encaminha"
    r"|encaminhe|encaminhar)\s+(?:\w+\s+){0,3}?(?:atendente|pessoa|humano|humana|alguem|vendedor\w*"
    r"|responsavel|gerente)\b"
    r"|\bfalar\s+com\s+(?:o\s+|a\s+)?(?:vendedor\w*|dono|dona|loja|equipe|suporte|sac)\b"
    # a cobrança ("ninguém responde?", "cadê vocês?") — o `pede_atendente` do manual
    r"|\bninguem\s+(?:\w+\s+)?(?:me\s+)?(?:respond\w*|atend\w*|ajud\w*)\b"
    r"|\bcade\s+(?:voces|vcs|vc|voce|a\s+loja|o\s+atendimento|o\s+pessoal)\b"
    r"|\b(?:atendimento|suporte)\s+human[oa]\b"
    # revisão de 21/10/2026: "tem alguém online?", "tem algum atendente
    # disponível?", "tem alguém?"; e a pessoa pedida com as palavras mais
    # simples ("quero uma pessoa", "prefiro um humano", "quero alguém de
    # verdade") — "preciso de uma pessoa pra receber" não é pedido.
    r"|\b(?:alguem|atendente\w*|human[oa])\s+(?:disponive\w*|online|on\s*line|livre)\b"
    r"|\btem\s+alguem\b(?=\s*(?:[?!.,;]|$))"
    r"|\b(?:quero|queria|prefiro|pode\s+ser|me\s+passa|me\s+passe|gostaria\s+de|desejo)\s+"
    r"(?:falar\s+com\s+)?(?:um\s+|uma\s+|a\s+|o\s+)?(?:pessoa|human[oa]|atendente\s+human[oa])\b"
    r"(?!\s+(?:para|pra|pro)\s+(?:receber|retirar|buscar|pegar|entregar|usar))"
    r"|\b(?:alguem|gente)\s+(?:de\s+verdade|real)\b"
)
# A mensagem que é só a pessoa: "pessoa", "humano", "uma pessoa por favor".
_SO_PESSOA = re.compile(
    r"(?:(?:sim|quero|prefiro|isso|um|uma|a|o)\s+)*(?:pessoa|human[oa]|atendente\s+human[oa])"
    r"(?:\s+(?:de\s+verdade|real|por\s+favor|pf|pfv|pfvr|sim))*"
)
# A resposta ao "é robô?" que cita a pessoa (aceita a oferta, sem pergunta nova).
_CITA_PESSOA = re.compile(r"\b(?:pessoas?|human[oa]s?|alguem|equipe|atendente\w*)\b")
# A recusa do atendimento virtual (explícita: robô, IA, automático, virtual).
_ALVO_VIRTUAL = (
    r"(?:robo\w*|bot|chatbot|maquina|ia|inteligencia\s+artificial|atendimento\s+(?:automatico"
    r"|virtual|eletronico)|atendente\s+virtual|virtual|automatico|resposta\s+automatica"
    r"|mensage(?:m|ns)\s+automatica)"
)
_RECUSA_VIRTUAL = re.compile(
    r"\bnao\s+(?:quero|gosto\s+de|aceito|vou)\s+(?:mais\s+)?(?:falar|conversar|ser\s+atendid[oa]"
    r"|tratar)?\s*(?:com|por)?\s*(?:um\s+|uma\s+|o\s+|a\s+)?" + _ALVO_VIRTUAL + r"\b"
    r"|\b(?:chega|cansei|para|pare|parem|parar)\s+de\s+(?:falar\s+com\s+)?(?:um\s+|uma\s+)?"
    + _ALVO_VIRTUAL
    + r"\b"
    r"|\bsem\s+(?:um\s+|uma\s+)?(?:robo\w*|bot|resposta\s+automatica)\b"
    r"|\b(?:robo|bot)\s+nao\b"
    r"|\bnao\s+(?:e|eh)\s+(?:com\s+)?(?:um\s+)?(?:robo|bot|maquina)\s+que\s+(?:eu\s+)?quero\b"
    r"|\b(?:pessoa|humano|humana|gente|atendente)(?:\s+de\s+verdade)?\s*,?\s+(?:e\s+)?nao\s+"
    r"(?:com\s+)?(?:um\s+|uma\s+)?(?:robo\w*|bot|maquina)\b"
)
# O pedido inteiro (o verbo antes incluído) — o que sai do texto antes de
# procurar o que mais o cliente quer (`responde_algo`).
# O do agente na frente: "atendente disponível" sai inteiro (o `\batendente\b`
# do legado deixaria o "disponível" como se fosse pergunta).
_PEDIDO_INTEIRO = re.compile(
    _VERBOS_DO_PEDIDO + r"(?:" + _PEDE_PESSOA_AGENTE.pattern + r"|" + ia._ATENDENTE.pattern + r")"
)
# A oferta da pessoa na resposta da loja (a resposta honesta ao "é robô?").
_OFERTA_DE_PESSOA = re.compile(
    r"\b(?:pessoa|atendente|alguem|colega)\s+(?:d[ao]\s+(?:nossa\s+)?(?:equipe|loja|time)\s+)?"
    r"(?:pode|podera|consegue)\s+(?:\w+\s+)?(?:assumir|continuar|seguir|atender)"
    r"|\bposso\s+(?:te\s+|lhe\s+)?(?:chamar|passar|transferir|encaminhar)\s+(?:\w+\s+){0,3}?"
    r"(?:alguem|pessoa|atendente|equipe)\b"
)
# O "sim" de quem aceita a pessoa oferecida.
_SO_SIM = re.compile(
    r"(?:(?:sim|s|ss|quero|queria|prefiro|pode|podem|pode\s+ser|aceito|isso|claro|por\s+favor"
    r"|pf|pfv|pfvr|uhum|aham|com\s+certeza|desejo|gostaria|passa|chama|pode\s+passar"
    r"|pode\s+chamar)\s*)+"
)
# A irritação que acompanha o pedido (as palavras fortes; o grito e o "!!!" são
# lidos no texto cru, `_gritou`). Alerta, xingamento e reclamação vêm pelos
# sinais e pela pista `reclamacao_forte`. "Que demora" sozinho é o
# `pede_atendente` do manual base ("reclama da demora"): fica com o agente.
_IRRITACAO = re.compile(
    r"\b(?:absurd\w*|ridicul\w*|descaso|desrespeit\w*|falta\s+de\s+respeito|pessim[oa]s?"
    r"|horrivel|horriveis|lamentavel|inadmissivel|inaceitavel|revoltad\w*|revoltante"
    r"|indignad\w*|irritad\w*|irritante|estressad\w*|decepcionad\w*|decepcao|frustrad\w*"
    r"|porcaria\w*|cansei|cansad[oa]s?\s+de|nao\s+aguento\s+mais|chega\s+de|enrola\w*"
    r"|enrolac\w*|nunca\s+mais\s+compro|pior\s+(?:loja|atendimento|compra|empresa|vendedor\w*)"
    r"|(?:ja\s+)?(?:pedi|falei|perguntei|mandei|escrevi)\s+(?:isso\s+|isto\s+)?(?:\d+|duas|tres"
    r"|quatro|cinco|varias|mil|muitas)\s+vezes"
    r"|pela\s+(?:\d+\w*|segunda|terceira|quarta|quinta|decima|enesima|milesima)\s+vez)"
)
# "Você não respondeu a minha pergunta": aponta para uma pergunta de antes.
_NAO_RESPONDERAM = re.compile(
    r"\bnao\s+(?:me\s+)?(?:respond\w*|tive\s+resposta|obtive\s+resposta|recebi\s+resposta)"
    r"|\b(?:minha|a\s+minha)\s+(?:pergunta|duvida)\b|\bsem\s+resposta\b"
)
# A resposta da loja que só pergunta como ajudar (não respondeu nada).
_RESPOSTA_GENERICA = re.compile(
    r"\b(?:em\s+que|como)\s+(?:\w+\s+){0,2}?(?:te\s+|lhe\s+)?(?:ajud\w*|ser\s+util)"
    r"|\bqual\s+(?:e\s+)?(?:a\s+)?(?:sua\s+)?duvida\b|\bdescreva\s+(?:a\s+)?sua\s+duvida\b"
    r"|\bconte\s+(?:aqui\s+|pra\s+gente\s+)?o\s+que\s+(?:precisa|aconteceu)\b"
)
_MAX_PALAVRAS_GENERICA = 25
# O que sobra do pedido e não é pergunta: cumprimento, cortesia, o próprio
# pedido, a cobrança ("cadê vocês?").
_ENCHIMENTO = frozenset(
    """
    oi ola opa ei eae eai alo bom boa dia tarde noite tudo bem ok okay certo beleza
    por favor pfv pf pfvr porfavor obrigado obrigada obg grato grata sim s ss nao nn
    ainda de novo novamente urgente urgencia agora ja logo rapido aqui ai ae la
    alguem algum alguma eu me mim comigo voce voces vc vcs gente pessoal ninguem
    com um uma uns umas o a os as e ou so somente apenas mesmo
    quero queria gostaria preciso precisava necessito desejo prefiro
    pode podem poderia podiam podia consegue conseguem ajudar ajuda ajude
    falar conversar atender atende atendam atendem atendimento atendente atendentes
    atendido atendida humano humana pessoa pessoas real verdade
    responder responde respondem responda chama chame passa passe transfere transfira
    seis 6 opcao numero moca moco amigo amiga senhor senhora gentileza
    aguardo aguardando esperando espero estou to ta tou
    isso que pra para pro no na do da de em cade
    tem teria existe atendendo equipe mas
    """.split()
)
# As palavras que não pesam na comparação da pergunta repetida (`_raizes`): a
# palavra de pergunta, o pronome, o artigo com preposição.
_SEM_PESO_NA_REPETICAO = frozenset(
    """
    entao se qual quais quando como onde quanto quantos quantas porque por sobre
    esse essa esses essas este esta estes estas aquele aquela meu minha meus minhas
    seu sua seus suas dele dela ao aos pelo pela pelos pelas num numa dos das nos nas
    ne hein afinal tambem tb mais muito
    """.split()
)
# A marca da pergunta "é robô?" nas raízes (a pergunta repetida também vale).
_RAIZ_ROBO = "<robo>"


def _do_cliente(m: Any) -> bool:
    return getattr(m, "autor", None) == constantes.AUTOR_CLIENTE and bool(
        (getattr(m, "texto", None) or "").strip()
    )


def _plano_de(texto: str | None) -> str:
    return " ".join(validador.plano_de(texto or "").split())


def pede_pessoa(texto: str | None) -> bool:
    """O cliente pede para falar com uma pessoa (o `ia._ATENDENTE` do legado, a
    opção 6 do menu, o vocabulário do agente, a recusa do virtual)? PURA.

    A pergunta sincera "é robô?"/"vc é humano?" NÃO é pedido (o agente responde).
    """
    plano = _plano_de(texto)
    if not plano:
        return False
    if ia._OPCAO_ATENDENTE.match(plano):
        return True
    if _SO_PESSOA.fullmatch(" ".join(re.sub(r"[^\w\s]", " ", plano).split())):
        return True
    sem_a_pergunta = ia._PERGUNTA_DE_ROBO.sub(" ", plano)
    return bool(
        ia._ATENDENTE.search(sem_a_pergunta)
        or _PEDE_PESSOA_AGENTE.search(sem_a_pergunta)
        or _RECUSA_VIRTUAL.search(plano)
    )


def so_pede_pessoa(texto: str | None) -> bool:
    """A mensagem é só o pedido de pessoa e/ou a pergunta "é robô?", sem mais nada a
    responder (o que o assunto `pede_atendente` resolve)? PURA (21/10/2026).

    O "você não respondeu a minha pergunta" aponta para uma pergunta de antes:
    o "em que posso ajudar?" não a resolve.
    """
    plano = _plano_de(texto)
    robo = ia._PERGUNTA_DE_ROBO.search(plano) is not None
    return (
        (robo or pede_pessoa(texto))
        and not _NAO_RESPONDERAM.search(plano)
        and not responde_algo(ia._PERGUNTA_DE_ROBO.sub(" ", plano))
    )


def recusa_o_virtual(texto: str | None) -> bool:
    """O cliente recusa o atendimento virtual ("não quero falar com robô")? PURA."""
    return bool(_RECUSA_VIRTUAL.search(_plano_de(texto)))


def ofereceu_pessoa(texto: str | None) -> bool:
    """A resposta da loja ofereceu a pessoa ("se preferir, uma pessoa da nossa
    equipe pode assumir a conversa")? PURA."""
    return bool(_OFERTA_DE_PESSOA.search(_plano_de(texto)))


def _so_sim(texto: str | None) -> bool:
    """Só o "sim" ("quero", "prefiro", "pode", "por favor"...), sem mais nada."""
    plano = " ".join(re.sub(r"[^\w\s]", " ", _plano_de(texto)).split())
    return bool(plano) and _SO_SIM.fullmatch(plano) is not None


def _gritou(texto: str | None) -> bool:
    """O grito: "!!!" ou o texto em caixa alta (3+ palavras, 12+ letras)."""
    texto = texto or ""
    if "!!!" in texto:
        return True
    letras = [c for c in texto if c.isalpha()]
    return len(letras) >= 12 and len(texto.split()) >= 3 and all(c.isupper() for c in letras)


def irritado(textos: Iterable[str | None]) -> bool:
    """O turno mostra irritação (palavra forte, grito, "já pedi 3 vezes")? PURA."""
    textos = [t for t in textos if t]
    return any(_gritou(t) for t in textos) or bool(_IRRITACAO.search(_plano_de(" ".join(textos))))


def _clausulas(plano: str) -> list[tuple[str, bool]]:
    """(oração, terminou em "?") — a pontuação separa."""
    return [
        (m.group(1), "?" in m.group(2))
        for m in re.finditer(r"([^.!?;\n]+)([.!?;\n]*)", plano)
        if m.group(1).strip()
    ]


def _sem_o_pedido(plano: str) -> str:
    """O texto plano sem o pedido de pessoa (com o verbo antes), a recusa do virtual
    e o "você não respondeu a minha pergunta" (aponta para uma pergunta de antes)."""
    return _NAO_RESPONDERAM.sub(" ", _RECUSA_VIRTUAL.sub(" ", _PEDIDO_INTEIRO.sub(" ", plano)))


def _raizes(palavras: Iterable[str]) -> frozenset[str]:
    """As raízes que pesam na comparação da pergunta repetida (o plural e o fim da
    palavra não contam: "voltar" = "volta", "potes" = "pote")."""
    saida = set()
    for p in palavras:
        if p in _ENCHIMENTO or p in _SEM_PESO_NA_REPETICAO:
            continue
        if len(p) > 3 and p.endswith("s"):
            p = p[:-1]
        saida.add(p[:5])
    return frozenset(saida)


def _perguntas(texto: str | None) -> list[frozenset[str]]:
    """As perguntas da mensagem ALÉM do pedido de pessoa, cada uma pelas raízes. PURA.

    A pergunta "é robô?" conta (o agente responde com honestidade). O pedido
    (com o verbo antes), a recusa do virtual, o "não respondeu", o
    cumprimento, a cortesia e a cobrança saem; sobra palavra e a oração é
    pergunta ("?"), tem pista de assunto ou é demanda concreta
    (`ia.demanda_concreta`) → conta.
    """
    plano = _plano_de(texto)
    if not plano or ia.mensagem_de_continuidade(texto):
        return []
    saida: list[frozenset[str]] = []
    if ia._PERGUNTA_DE_ROBO.search(plano):
        saida.append(frozenset({_RAIZ_ROBO}))
        plano = ia._PERGUNTA_DE_ROBO.sub(" ", plano)
    for oracao, interrogativa in _clausulas(_sem_o_pedido(plano)):
        palavras = [p for p in re.findall(r"[a-z0-9]+", oracao) if p not in _ENCHIMENTO]
        if not palavras:
            continue
        if (
            interrogativa
            or ia.pistas_de_categoria(oracao)
            or ia.demanda_concreta(" ".join(palavras))
        ):
            saida.append(_raizes(palavras))
    return saida


def responde_algo(texto: str | None) -> bool:
    """A mensagem traz o que responder ALÉM do pedido de pessoa (`_perguntas`)? PURA."""
    return bool(_perguntas(texto))


def _resposta_generica(texto: str | None) -> bool:
    """A resposta da loja só perguntou como ajudar ("Olá! Em que podemos ajudar?")."""
    plano = _plano_de(texto)
    return bool(_RESPOSTA_GENERICA.search(plano)) and len(plano.split()) <= _MAX_PALAVRAS_GENERICA


def _indices_da_loja(recentes: Sequence[Any]) -> list[int]:
    return [i for i, m in enumerate(recentes) if ia.resposta_da_loja(m)]


def _ate_o_turno(recentes: list[Any], gatilho: Any | None) -> list[Any]:
    """As mensagens até o fim do turno do `gatilho`: na sugestão pela tela a loja já
    respondeu depois dele, e o que veio depois não é deste turno."""
    if gatilho is None:
        return recentes
    i = next((i for i, m in enumerate(recentes) if m is gatilho), None)
    if i is None:
        return recentes
    fim = next((j for j in range(i + 1, len(recentes)) if ia.resposta_da_loja(recentes[j])), None)
    return recentes if fim is None else recentes[:fim]


def _turnos(recentes: Sequence[Any]) -> list[tuple[list[Any], Any]]:
    """Os turnos do cliente que a loja já fechou, na ordem: (as mensagens do cliente,
    a resposta real da loja que fechou o turno). O turno atual não entra."""
    saida: list[tuple[list[Any], Any]] = []
    atual: list[Any] = []
    for m in recentes:
        if ia.resposta_da_loja(m):
            saida.append((atual, m))
            atual = []
        elif _do_cliente(m):
            atual.append(m)
    return saida


def _respondidas(turnos: Sequence[tuple[list[Any], Any]]) -> list[frozenset[str]]:
    """As raízes de cada turno que a loja fechou com uma resposta DE VERDADE (não só
    o "em que podemos ajudar?"): o que o cliente perguntou ali já foi respondido."""
    saida = []
    for msgs, loja in turnos:
        if not msgs or _resposta_generica(getattr(loja, "texto", None)):
            continue
        raizes: set[str] = set()
        for m in msgs:
            plano = _plano_de(m.texto)
            if ia._PERGUNTA_DE_ROBO.search(plano):
                raizes.add(_RAIZ_ROBO)
                plano = ia._PERGUNTA_DE_ROBO.sub(" ", plano)
            raizes |= _raizes(re.findall(r"[a-z0-9]+", _sem_o_pedido(plano)))
        saida.append(frozenset(raizes))
    return saida


def _pergunta_nova(texto: str | None, respondidas: Sequence[frozenset[str]]) -> bool:
    """A mensagem traz pergunta que NÃO repete a de um turno já respondido de verdade?

    Repete = as raízes da pergunta cabem nas de um turno respondido ("mas vai
    voltar?" depois de "o pote de 2,5 vai voltar ao estoque?" respondido).
    """
    return any(not any(p <= r for r in respondidas) for p in _perguntas(texto))


def _ficou_sem_resposta(
    turnos: Sequence[tuple[list[Any], Any]], respondidas: Sequence[frozenset[str]]
) -> bool:
    """Há pergunta do cliente que a loja deixou só com o "em que podemos ajudar?"?

    Volta pelos turnos fechados só com a resposta genérica (o caso real de
    21/10/2026); a primeira resposta de verdade encerra a busca.
    """
    for msgs, loja in reversed(turnos):
        if not _resposta_generica(getattr(loja, "texto", None)):
            return False
        if any(_pergunta_nova(m.texto, respondidas) for m in msgs):
            return True
    return False


def tem_o_que_responder(recentes: Sequence[Any], gatilho: Any | None) -> bool:
    """O turno do cliente traz algo a responder além do pedido de pessoa? PURA.

    - uma mensagem do turno traz pergunta nova (`_perguntas`) — a que repete a
      de um turno já respondido de verdade não é nova (revisão de 21/10/2026);
    - ficou pergunta sem resposta: a dos turnos que a loja fechou só com um
      "em que podemos ajudar?" (o caso real de 21/10/2026). O "você não
      respondeu a minha pergunta" depois de uma resposta de verdade não traz
      nada novo (`pedido_de_pessoa` → pessoa).
    """
    recentes = _ate_o_turno(list(recentes), gatilho)
    turnos = _turnos(recentes)
    respondidas = _respondidas(turnos)
    rajada = [m for m in ia.rajada_do_cliente(recentes, gatilho) if _do_cliente(m)]
    return any(_pergunta_nova(m.texto, respondidas) for m in rajada) or _ficou_sem_resposta(
        turnos, respondidas
    )


def _pedidos_seguidos(turnos: Sequence[tuple[list[Any], Any]]) -> int:
    """Quantos turnos fechados seguidos, do último para trás, pediram pessoa."""
    n = 0
    for msgs, _loja in reversed(turnos):
        if not msgs:
            continue
        if not any(pede_pessoa(m.texto) for m in msgs):
            break
        n += 1
    return n


def _aceitou_a_oferta(textos: Sequence[str], pede: bool) -> bool:
    """Depois da oferta da pessoa: pediu a pessoa, disse só "sim", ou citou a pessoa
    ("uma pessoa por favor", "a equipe", "humano") sem pergunta nova."""
    return (
        pede
        or all(_so_sim(t) for t in textos)
        or (
            any(_CITA_PESSOA.search(_plano_de(t)) for t in textos)
            and not any(responde_algo(t) for t in textos)
        )
    )


def pedido_de_pessoa(recentes: Sequence[Any], gatilho: Any | None) -> str | None:
    """O pedido de pessoa que AINDA manda para pessoa no agente: o motivo, ou None. PURA.

    Ver o comentário da seção (decisão do dono, 21/10/2026). Na ordem: recusou
    o virtual; pediu a pessoa oferecida (ou disse "sim" à oferta); pediu com
    irritação, alerta, xingamento ou reclamação; o 3º pedido seguido
    (`_TETO_DE_PEDIDOS`); e, sem nada novo a responder: disse que a resposta
    de verdade da loja não respondeu, ou pediu de novo depois da resposta da
    loja (`ia.MOTIVO_INSISTIU_EM_PESSOA`).
    """
    recentes = _ate_o_turno(list(recentes), gatilho)
    textos = [m.texto for m in ia.rajada_do_cliente(recentes, gatilho) if _do_cliente(m)]
    if not textos:
        return None
    if any(recusa_o_virtual(t) for t in textos):
        return MOTIVO_RECUSOU_VIRTUAL
    pede = any(pede_pessoa(t) for t in textos)
    turnos = _turnos(recentes)
    ultima = turnos[-1][1] if turnos else None
    if (
        ultima is not None
        and ofereceu_pessoa(getattr(ultima, "texto", None))
        and _aceitou_a_oferta(textos, pede)
    ):
        return MOTIVO_ACEITOU_A_PESSOA
    if not pede:
        return None
    codigos = _codigos(ia.sinais_com_codigo(textos, agente=True))
    if (
        irritado(textos)
        or codigos & {constantes.HUMANO_ALERTA, constantes.HUMANO_XINGAMENTO}
        or "reclamacao_forte" in ia.pistas_de_categoria(" ".join(textos))
    ):
        return MOTIVO_PEDIU_IRRITADO
    if _pedidos_seguidos(turnos) + 1 >= _TETO_DE_PEDIDOS:
        return MOTIVO_PEDIU_3_VEZES
    respondidas = _respondidas(turnos)
    if any(_pergunta_nova(t, respondidas) for t in textos) or _ficou_sem_resposta(
        turnos, respondidas
    ):
        return None
    if (
        ultima is not None
        and not _resposta_generica(getattr(ultima, "texto", None))
        and any(_NAO_RESPONDERAM.search(_plano_de(t)) for t in textos)
    ):
        return MOTIVO_NAO_RESOLVEU
    if any(pede_pessoa(m.texto) for msgs, _loja in turnos for m in msgs):
        return ia.MOTIVO_INSISTIU_EM_PESSOA
    return None


# ── O cliente só agradece/confirma depois de a loja tratar (Kia ao vivo) ──
#
# Kia ao vivo, 21/10/2026 (85176yln23, rascunho 15da122e): a equipe respondeu
# ("vamos abrir um chamado para te ajudar!"), a cliente só disse "Ok Aguardando"
# e "Obrigada" — o agente repetiu a orientação do rastreio e pediu `abrir_chamado`
# de novo. Regra do dono: o turno que é SÓ reconhecimento ou agradecimento, sem
# pergunta nova, depois de a loja já ter respondido o assunto, não pede
# rascunho (DESCARTADO, "não precisa de resposta"; no máximo a linha curta de
# cortesia que o modelo escrever); nunca se repete a orientação já dada nem se
# pede de novo a ação de pessoa que a equipe já fez ou prometeu — é o
# acompanhamento da própria equipe. A ação de pessoa do CÓDIGO (o estado:
# reclamação aberta, 83955…) continua vencendo.

# As palavras do reconhecimento: o "ok", o agradecimento, a espera, o
# cumprimento. Sem "sim"/"pode": depois de uma pergunta da loja, são resposta.
_RECONHECIMENTO = frozenset(
    """
    ok okay okk oks blz beleza certo certinho ta bom entendi entendido entendo combinado perfeito
    otimo otima show joia legal valeu vlw obrigado obrigada obrigados obrigadas obg obgd brigado
    brigada grato grata agradeco agradecida agradecido muito mt mto muitissimo aguardo aguardando
    aguardar aguardarei espero esperando esperar fico no na o a entao tudo bem tranquilo
    tranquila de nada oi ola bom boa dia tarde noite ate mais amanha logo deus abencoe
    por favor gentileza atencao pela retorno
    """.split()
)


def so_reconhecimento(texto: str | None) -> bool:
    """A mensagem é só reconhecimento, agradecimento ou espera ("Ok Aguardando",
    "Obrigada", "fico no aguardo, obrigado!")? PURA. Pergunta ("?") não é."""
    plano = _plano_de(texto)
    if not plano or "?" in plano:
        return False
    palavras = re.findall(r"[a-z0-9]+", plano)
    return bool(palavras) and all(p in _RECONHECIMENTO for p in palavras)


def turno_so_de_reconhecimento(recentes: Sequence[Any], gatilho: Any | None) -> bool:
    """O turno do cliente é SÓ reconhecimento/agradecimento depois de uma resposta
    real da loja que tratou o assunto (Kia ao vivo, 21/10/2026)? PURA.

    - a rajada (depois da última resposta real da loja) só tem texto de
      reconhecimento (`so_reconhecimento`) — nenhum anexo, cartão nem pergunta;
    - há resposta real da loja antes dela, que não é só o "em que podemos
      ajudar?" (`_resposta_generica`) nem uma pergunta ao cliente (o "ok"
      depois de "quer que eu abra o chamado?" é resposta, não reconhecimento).
    """
    recentes = list(recentes)
    lojas = _indices_da_loja(recentes)
    if not lojas:
        return False
    rajada = ia.rajada_do_cliente(recentes, gatilho)
    if not rajada or any(
        getattr(m, "autor", None) != constantes.AUTOR_CLIENTE
        or _e_anexo(m)
        or constantes.e_cartao_do_comprador(getattr(m, "payload", None))
        or not so_reconhecimento(getattr(m, "texto", None))
        for m in rajada
    ):
        return False
    ultima = recentes[lojas[-1]]
    texto_da_loja = getattr(ultima, "texto", None) or ""
    return not _resposta_generica(texto_da_loja) and "?" not in texto_da_loja


# Calibração v8 com a chave (22/10/2026, viz-responde-03): a pergunta deixada só
# com o "em que podemos ajudar?" da loja continua pendente — com ou sem "?" ("queria
# saber se o laranja volta"), que o `_perguntas` não vê. O placeholder do anexo
# ("[Figurinha]", "[Mensagem]") e a recusa à pergunta genérica ("não, obrigado") não
# deixam nada.
_PLACEHOLDER = re.compile(r"^\s*\[[^\]]{1,40}\]\s*$")
_RECUSA_DA_GENERICA = re.compile(
    r"^\W*(?:nao|n|nn|nada)(?:\W+(?:obrigad[oa]s?|obg|valeu|vlw|era\s+so\s+isso|so\s+isso"
    r"|por\s+enquanto|tudo\s+certo|ta\s+tudo\s+certo|nao))*\W*$"
)


def _fala_sem_pedido(texto: str | None) -> bool:
    return (
        not (texto or "").strip()
        or _PLACEHOLDER.match(texto or "") is not None
        or ia.mensagem_de_continuidade(texto)
        or so_reconhecimento(texto)
        or so_pede_pessoa(texto)
    )


def fala_deixada_com_generica(recentes: Sequence[Any], gatilho: Any | None) -> bool:
    """Uma fala do cliente que pede algo ficou só com o "em que podemos ajudar?" da
    loja (os turnos fechados só com a resposta genérica, do último para trás), e a
    rajada não é a recusa a ela ("não, obrigado")? PURA (viz-responde-03)."""
    recentes = _ate_o_turno(list(recentes), gatilho)
    rajada = [m for m in ia.rajada_do_cliente(recentes, gatilho) if _do_cliente(m)]
    if rajada and all(_RECUSA_DA_GENERICA.match(_plano_de(m.texto)) for m in rajada):
        return False
    for msgs, loja in reversed(_turnos(recentes)):
        if not _resposta_generica(getattr(loja, "texto", None)):
            return False
        if any(not _fala_sem_pedido(m.texto) for m in msgs):
            return True
    return False


def sinais_do_agente(
    sinais: Iterable[tuple[str, str]], recentes: Sequence[Any], gatilho: Any | None
) -> list[tuple[str, str]]:
    """Os sinais do texto (`ia.sinais_com_codigo(..., agente=True)`) com o pedido de
    pessoa pela régua do agente (21/10/2026). PURA.

    O `HUMANO_ATENDENTE` sai sempre ("cliente pediu atendente" e o "é robô?"
    não mandam para pessoa) e volta, na frente e com o motivo, só com
    `pedido_de_pessoa`. É o que o `ia._gerar`, o `replay.montar_base`, a
    `gatilhos` sem os sinais e a Caixa Humano (`humano._triar_do_agente`) usam:
    a mesma régua nos quatro.
    """
    saida = [(c, f) for c, f in sinais if c != constantes.HUMANO_ATENDENTE]
    if motivo := pedido_de_pessoa(recentes, gatilho):
        saida.insert(0, (constantes.HUMANO_ATENDENTE, motivo))
    return saida


def _sinais_da_rajada(recentes: Sequence[Any], gatilho: Any | None) -> set[str]:
    """Os códigos dos sinais como o `humano.triar` calcula (sem o `BaseDoGerar`)."""
    rajada = ia.rajada_do_cliente(list(recentes), gatilho)
    textos = [m.texto for m in rajada if getattr(m, "texto", None)]
    return _codigos(sinais_do_agente(ia.sinais_com_codigo(textos, agente=True), recentes, gatilho))


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
    `sinais` = o `BaseDoGerar.sinais`; None = calcula da rajada
    (`sinais_do_agente`). `ia_e_o_atendente` não muda nada desde 21/10/2026 (a
    régua do pedido de pessoa do agente não depende do `pede_atendente`
    liberado); fica pela assinatura.
    """
    codigos = _codigos(sinais) if sinais is not None else _sinais_da_rajada(recentes, gatilho)
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
    agora: datetime | None = None,
) -> list[GatilhoPessoa]:
    """Os gatilhos de código da régua 6.3, na ordem dela. PURA (sem banco, sem modelo).

    `conversa` = `AtendimentoConversa` (canal, dados, etiquetas); `recentes`
    e `gatilho` = os do `_gerar`; `ctx` = `contexto_da_conversa`; `postado`
    = `POSTADO_*`/None; `chaves_ativas` = as `CHAVES_QUE_TROCAM_GATILHO`
    com fato ativo; `sinais` = os (código `HUMANO_*`, frase) já ajustados
    pela régua do pedido de pessoa do agente (o `BaseDoGerar.sinais`,
    `sinais_do_agente`) — None = calcula da rajada como o `humano.triar`
    (`ia_e_o_atendente` não muda nada desde 21/10/2026);
    `sinais_do_cliente` = os códigos do cartão Cliente; `uf_restrita` = o
    pedido vai para UF com restrição de envio (`gatilhos_de_codigo` lê);
    `agora` = o relógio da régua da entrega atrasada (item 22, 22/10/2026: o
    `ctx.agora()` do rascunho — o `as_of` no replay —; None = agora).
    """
    recentes = list(recentes)
    ctx = ctx if isinstance(ctx, Mapping) else {}
    agora = agora or datetime.now(UTC)
    rajada, textos, plano = _planos_do_turno(recentes, gatilho)
    codigos = _codigos(sinais) if sinais is not None else _sinais_da_rajada(recentes, gatilho)
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
    # 3 — reclamação/mediação aberta. Itens 22 e 24 da auditoria ao vivo
    # (22/10/2026, Barbosa Shopee 01:25–01:37): também a devolução/reembolso que o
    # comprador já abriu na plataforma (o pedido em TO_RETURN, o cartão de
    # devolução da Shopee aberto) — a loja responde a solicitação na Central do
    # Vendedor; o agente só confirma que ela foi aberta, nunca decide.
    devolucao_aberta = devolucao_na_plataforma(conversa, recentes, ctx, agora=agora)
    if (
        getattr(conversa, "canal", None) == constantes.CANAL_RECLAMACAO
        or constantes.ETIQUETA_RECLAMACAO in etiquetas
        or ia._reclamacoes_abertas(dict(ctx))
        or constantes.reclamacao_aberta(getattr(conversa, "dados", None))
        or "reclamacao_aberta" in sinais_do_cliente
    ):
        saida.append(_gatilho(constantes.ACAO_RECLAMACAO_ABERTA))
    elif devolucao_aberta:
        saida.append(_gatilho(constantes.ACAO_RECLAMACAO_ABERTA, DETALHE_DEVOLUCAO_NA_PLATAFORMA))
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
    # 6 — pediu pessoa (a régua do agente já veio nos sinais: `sinais_do_agente`).
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
        # O pedido entregue pelo MESMO dado que o modelo vê (`pedido_entregue`:
        # a logística ou a situação Entregue do Bling — eval do TikTok, 21/10/2026).
        (
            _NAO_RECEBEU.search(plano)
            and pedido_entregue(ctx)
            and not so_a_nf_nao_chegou(plano, [g.codigo for g in saida])
        )
        # Revisão da rodada 6 (22/10/2026): o retrato do MESMO pedido diz entregue
        # (TO_CONFIRM_RECEIVE/COMPLETED; a logística do espelho parada) e o cliente diz
        # que não recebeu A ENTREGA — o retrato que tira o atraso põe o chamado do não
        # recebimento (67 conversas em 30 dias; 13 sem a linha da logística).
        or (
            entregue_no_retrato(retrato_do_pedido(conversa, ctx, agora=agora))
            and _NAO_RECEBEU.search(plano)
            and _nao_chegou_a_entrega(plano)
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
    # Item 22 da auditoria ao vivo (22/10/2026, 440a83e7): a entrega atrasada, por
    # ÚLTIMO (o "devolvido ao remetente" e o "consta entregue" dizem mais) e nunca
    # com a devolução já aberta na plataforma (aí é a reclamação, acima).
    elif not devolucao_aberta and atraso_de_entrega(
        conversa, ctx, plano, postado=postado, agora=agora
    ):
        saida.append(_gatilho(constantes.ACAO_ABRIR_CHAMADO, DETALHE_ATRASO))
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
    if pedido and _NAO_RECEBEU.search(plano) and pedido_entregue(ctx_pedido):
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
        # Item 22 (22/10/2026): o relógio da entrega atrasada — o `as_of` no replay.
        agora=ctx.agora(),
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
