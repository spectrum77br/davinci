"""Reivindicação da Garantia de A a Z e estorno (chargeback) da Amazon por e-mail (16/11/2026).

O pedido 306097 (Amazon Kia, 702-3809368-8549006): a Amazon CONCEDEU ao
comprador uma reivindicação da Garantia de A a Z de R$ 865,59 — debitou da
conta e contou no Índice de pedidos com problemas — e o DaVinci dizia
"Chamados: nenhum". O e-mail "Decisão da reivindicação referente ao pedido …"
(de `atoz-guarantee-no-reply@amazon.com`) tinha entrado só como aviso
INFORMATIVO numa conversa "não precisa de resposta": a Amazon está em
`PLATAFORMAS_EMAIL_SO_AVISO` (o aviso de quem tem API não abre a vez), mas a
reivindicação A a Z e o estorno não têm API nenhuma — o e-mail é o único
lugar onde eles aparecem. O mesmo com o "Informação necessária" do estorno
(`cb-seller-notification@amazon.com.br`, 19/10, pedido 702-8218469-5748230),
que dá 7 dias para a loja responder antes do débito.

Daqui saem duas coisas, as duas PURAS até o `gravar`:

  1. `ler` — o e-mail é uma reivindicação/estorno da Amazon? Pelo REMETENTE
     (`atoz-guarantee-no-reply@amazon.*`, `cb-seller-notification@amazon.*`
     e a família deles: `atoz-…`, `cb-seller-…` num domínio da Amazon)
     ou pelo ASSUNTO ("reivindicação", "garantia de A a Z", "chargeback", e o
     "informação necessária" que fala de estorno) num e-mail da Amazon que
     cite o pedido (a novidade sobre a política da Garantia de A a Z, sem
     pedido, continua informativa). A ponte manda esse e-mail para a FILA de
     pessoa (tipo `chamado`, abre a vez da loja), nunca para informativo — e
     NUNCA cria chamado (regra do dono, 21/10: e-mail não cria chamado). O
     endereço de retransmissão do comprador (`…@marketplace.amazon.*`) não é
     "a Amazon": é o cliente (que já vai para a fila por ser cliente).
  2. `gravar` — o espelho em `atendimento_reclamacoes` (plataforma `amazon`,
     `externo_id` `atoz:<pedido>` ou `cb:<pedido>`), para o caso aparecer no
     "Na plataforma" do resumo do pedido e no cartão da conversa, e a
     conversa do pedido ganhar a etiqueta Reclamação enquanto ele estiver
     ABERTO. Só com os remetentes do modelo (`oficial`) e e-mail não
     suspeito — é fato da plataforma, não palpite. Pelo assunto só vai à
     fila: a cópia do caso que a loja abriu no Suporte ("Retirada do ODR -
     reivindicação A a Z do pedido …", 16/11 20:59) também diz
     "reivindicação", e não pode reabrir a decisão como "a Amazon espera a
     loja".

O STATUS vem do e-mail (assunto + texto do modelo da Amazon):
  aberta                 — reivindicação/estorno aberto, a Amazon espera a loja;
  informacao_necessaria  — a Amazon pede informação (rastreio, comprovante) —
                           com o prazo que o texto der ("dentro de sete (7) dias");
  concedida              — decidida a favor do comprador (o valor é debitado);
                           o recurso ("em até 30 dias corridos") vai em
                           `dados.recurso_ate` e no texto do status;
  negada                 — decidida a favor da loja;
  retirada               — o comprador retirou a reivindicação;
  decidida               — "decisão" sem dizer o lado, ou os dois lados no
                           mesmo e-mail (ver o e-mail);
  sem_noticia            — aberta que nenhum e-mail encerrou (`encerrar_sem_noticia`).
As duas primeiras deixam a linha ABERTA; as outras a ENCERRAM (a decisão é o
fim do caso na Amazon; o recurso, se houver, é outro e-mail). E-mail mais
velho que o último lido não volta o status para trás.

16/11 (Eduardo, revisão): o texto é lido FRASE a FRASE (`status_do_email`).
A frase de HIPÓTESE ("Se a reivindicação for concedida…", "Como posso
recorrer no caso de…?") não decide nada; a NEGAÇÃO ("não debitamos", "não
concedemos", "nenhum valor será debitado") é a favor da loja e sai da frase
antes de procurar "debitamos"; "foi encerrada/resolvida a seu favor" e "foi
retirada" contam. O texto que não diz a situação com nenhuma frase conhecida
(`reconhecido=False`) nunca reabre nem muda o status de uma linha que já
existe — só a cria (aberta) quando é o primeiro e-mail do caso.

16/11 (Eduardo, revisão): a linha aberta não fica aberta para sempre. A
decisão pode não chegar por aqui (outro remetente, só no Gmail, texto que
não reconhecemos — ou nunca, o estorno resolvido sem aviso): a ponte fecha a
aberta sem notícia `SEM_NOTICIA` (30 dias) depois do prazo que o e-mail deu
ou do último e-mail (`encerrar_sem_noticia`), e a etiqueta Reclamação sai.

Só o que é do MODELO da Amazon vai para a linha (status, valor, o motivo que
ela escreve: "o cliente alegou um problema com a entrega…") — nenhum texto do
comprador, nenhum endereço.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoReclamacao
from app.services.atendimento.constantes import RECLAMACAO_TIPO_RECLAMACAO
from app.services.atendimento.validador import plano_de

PLATAFORMA = "amazon"
FONTE_EMAIL = "email"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

TIPO_ATOZ = "atoz"
TIPO_CHARGEBACK = "chargeback"
PREFIXO = {TIPO_ATOZ: "atoz:", TIPO_CHARGEBACK: "cb:"}
NOME_TIPO = {
    TIPO_ATOZ: "Reivindicação A a Z",
    TIPO_CHARGEBACK: "Estorno (chargeback)",
}

STATUS_ABERTA = "aberta"
STATUS_INFORMACAO = "informacao_necessaria"
STATUS_CONCEDIDA = "concedida"
STATUS_NEGADA = "negada"
STATUS_RETIRADA = "retirada"
STATUS_DECIDIDA = "decidida"
STATUS_SEM_NOTICIA = "sem_noticia"
STATUS_ABERTOS = frozenset({STATUS_ABERTA, STATUS_INFORMACAO})

# A aberta sem notícia fecha este tempo depois do prazo (ou do último e-mail).
SEM_NOTICIA = timedelta(days=30)
MOTIVO_ETIQUETA_SEM_NOTICIA = "reivindicação da Amazon sem notícia"
MAX_SEM_NOTICIA_POR_VOLTA = 50

# O remetente (a parte antes do @) de cada um; o domínio tem de ser da Amazon.
_LOCAIS = {
    "atoz-guarantee-no-reply": TIPO_ATOZ,
    "cb-seller-notification": TIPO_CHARGEBACK,
}
# 16/11 (Eduardo, revisão): os dois de cima não foram vistos num e-mail real
# (o remetente não fica em claro no banco). A mesma FAMÍLIA de endereços da
# Amazon também vale ("atoz-guarantee@…", "cb-seller-query@…"): sem isso, um
# endereço um pouco diferente levaria o e-mail à fila pelo assunto, mas nunca
# criaria a linha. O domínio continua tendo de ser da Amazon.
_PREFIXOS_LOCAIS = (
    ("atoz-", TIPO_ATOZ),
    ("a-to-z-", TIPO_ATOZ),
    ("cb-seller-", TIPO_CHARGEBACK),
)
# amazon.com, amazon.com.br, amazon.co.uk… e subdomínios deles.
_DOMINIO_AMAZON = re.compile(r"(?:[a-z0-9-]+\.)*amazon\.(?:com\.br|com|co\.[a-z]{2}|[a-z]{2})")
# A retransmissão do comprador (`…@marketplace.amazon.com.br`): é o cliente.
_RETRANSMISSAO = "marketplace.amazon."
# ID de pedido da Amazon: 3-7-7 dígitos.
_PEDIDO = re.compile(r"(?<![\d-])\d{3}-\d{7}-\d{7}(?![\d-])")

# Tudo casa sobre o texto PLANO (sem acento, minúsculo).
_ASSUNTO_ATOZ = re.compile(
    r"\b(?:reivindicac(?:ao|oes)|garantia de a a z|garantia a-?z|a-?to-?z(?: guarantee)?)\b"
)
# Só "chargeback" e "contestação de estorno": "estorno" sozinho é também o
# reembolso que a PRÓPRIA loja deu (não é caso aberto contra ela).
_ASSUNTO_CHARGEBACK = re.compile(r"\b(?:chargebacks?|contestacao de estorno)\b")
_INFO_NECESSARIA = re.compile(r"\binformac(?:ao|oes) necessaria")
_FALA_DE_ESTORNO = re.compile(r"\b(?:contestacao de estorno|estornos?|chargebacks?)\b")
# O assunto que diz que o caso acabou, sem dizer o lado.
_DECISAO = re.compile(r"\b(?:decis(?:ao|oes)|encerrad[oa]|resolvid[oa]|concluid[oa])\b")

# ── O status, frase a frase (16/11, revisão) ──
# A frase: até o ponto, a exclamação, a interrogação, o ponto e vírgula ou a
# quebra de linha (o ponto do "Amazon.com.br" também corta — não decide nada).
_FRASE = re.compile(r"[^.!?;\n]+[.!?;]?")
# HIPÓTESE: o modelo explica o que acontece em cada lado ("Se a reivindicação
# for concedida, o valor será debitado", "Como posso recorrer no caso de a
# reivindicação ter sido concedida por engano?") — não é o lado do caso.
_HIPOTESE_INICIO = re.compile(
    r"^(?:se|caso|no caso|em caso|quando|sempre que|a menos que|para que|saiba)\b"
)
# No meio da frase, só o "se" e o "caso" de condição ("você pode recorrer se
# a reivindicação foi negada", "…, caso a loja não responda") — nunca o
# substantivo ("neste caso o cliente alegou…") nem o "quando" do relato
# ("debitamos quando o comprador…").
_HIPOTESE_MEIO = re.compile(
    r"(?:\bse|(?<!neste )(?<!nesse )(?<!deste )(?<!desse )(?<!este )(?<!esse )(?<!no )"
    r"(?<!do )(?<!o )\bcaso)\s+"
    r"(?:a|o|as|os|sua|seu|voce|houver|for|forem|nao|ela|ele)\b"
)
# NEGAÇÃO do débito/da concessão = a favor da loja ("não debitamos nenhum
# valor", "não concedemos", "não haverá débito", "nenhum valor será debitado").
# Sai da frase antes de procurar "debitamos"/"concedemos".
# Entre o "não" e o verbo, só auxiliar/pronome ("não foi debitado", "não lhe
# será cobrado", "não haverá débito"): "não recebeu e debitamos" NÃO é negação.
_NEGA = re.compile(
    r"\b(?:nao|nem|nunca)\s+"
    r"(?:(?:foi|foram|sera|serao|seria|havera|houve|ha|lhe|nenhum|nenhuma|qualquer|mais|"
    r"vamos|iremos|e|necessario)\s+){0,3}"
    r"(?:debitamos|debitaremos|debitad[oa]s?|debitar|debito|concedemos|concedid[oa]s?|"
    r"concederemos|cobramos|cobrad[oa]s?|cobraremos)\b"
    r"|\bnenhum valor\s+(?:\w+\s+){0,2}?(?:debitado|cobrado|descontado)\b"
    r"|\bsem (?:nenhum )?debito\b"
)
# Só a decisão no PASSADO ("decidimos", "foi encerrada", "resolvida"…): o
# "decidir a seu favor" do e-mail da aberta não decide nada.
_DECIDIU = (
    r"(?:decidimos|decidiu|decidid[oa]s?|resolvemos|resolveu|resolvid[oa]s?|encerramos|"
    r"encerrou|encerrad[oa]s?|concluimos|concluiu|concluid[oa]s?|julgad[oa]s?|fechad[oa]s?)"
)
_NEGADA = re.compile(
    r"\b(?:negamos|negou|foi negada|foram negadas|indeferimos|indeferiu|foi indeferida)\b"
    rf"|\b{_DECIDIU}\b.{{0,60}}?\ba (?:seu favor|favor d[oa] (?:vendedor|loja|parceiro))"
)
_CONCEDIDA = re.compile(
    r"\b(?:concedemos|concedeu|foi concedida|foram concedidas|debitamos|"
    r"foi debitad[oa]|foram debitad[oa]s)\b"
    rf"|\b{_DECIDIU}\b.{{0,60}}?\ba favor d[oa] (?:cliente|comprador|titular)"
)
# "O comprador retirou/cancelou a reivindicação", "a reivindicação foi
# retirada pelo cliente", "a contestação de estorno foi cancelada". Nunca o
# "Retirada do ODR" (o caso que a LOJA abre para tirar a reivindicação do
# índice) nem "foi retirada do seu ODR".
_RETIRADA = re.compile(
    r"\b(?:retirou|cancelou|desistiu d[aoe])\s+(?:a |o |sua |seu |esta |este |essa |esse )?"
    r"(?:reivindicac|contestac|chargeback|estorno)"
    r"|\b(?:reivindicac|contestac)\w*\b.{0,60}?\bfoi (?:retirad|cancelad)[oa]\b"
    r"(?! d[oa] (?:seu |sua )?(?:odr|indice|taxa|metrica))"
)
_PEDE_INFORMACAO = re.compile(
    r"\b(?:forneca as seguintes informacoes|precisamos (?:de|das) (?:mais )?informac|"
    r"responda a este e-?mail|envie (?:as )?informac)"
)
# A ABERTA dita com todas as letras (sem isso, o status é um palpite).
# Nunca o plural solto ("veja suas reivindicações abertas", rodapé de qualquer
# e-mail da Garantia de A a Z).
_ABERTA = re.compile(
    r"\b(?:registrou|abriu|apresentou|fez|enviou|iniciou)\s+(?:uma |a )?(?:nova )?"
    r"(?:reivindicac|contestac)"
    r"|\b(?:reivindicacao|contestacao)\b.{0,60}?"
    r"\bfoi (?:registrad|abert|apresentad|iniciad|feit)a\b"
    r"|\b(?:recebemos|ha) uma (?:nova )?(?:reivindicac|contestac)"
    r"|\bem nome do cliente\b"
    r"|\bresponda\b.{0,40}?\b(?:em ate|dentro de|no prazo de)\b"
)
# "dentro de sete (7) dias", "em até 30 dias corridos", "no prazo de 3 dias".
_DIAS = re.compile(r"\b(?:dentro de|em ate|no prazo de|ate)\s+(?:[a-z]+\s+)?\(?(\d{1,2})\)?\s+dias")
_RECURSO = re.compile(r"\brecorrer\b[^.]{0,60}?\bem ate\s+(\d{1,2})\s+dias")
_VALOR_BRL = re.compile(r"r\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})")
_VALOR_PONTO = re.compile(r"\bfor\s+(\d+(?:\.\d{1,2})?)\s*brl\b|\b(\d+(?:\.\d{1,2})?)\s*brl\b")
# O motivo que o MODELO da Amazon escreve (não o comprador).
_MOTIVO_ATOZ = re.compile(
    r"o cliente alegou\s+(?:um |uma )?(.{5,200}?)(?:\.\s|\.$|\n|$)", re.IGNORECASE
)
MAX_MOTIVO = 160


@dataclass(frozen=True)
class Reivindicacao:
    """O que o e-mail diz do caso (só o modelo da Amazon)."""

    tipo: str  # atoz | chargeback
    status: str
    pedido: str | None
    # O remetente é a Amazon (domínio dela, não a retransmissão do comprador).
    da_amazon: bool
    # Um dos remetentes do MODELO (`atoz-guarantee-no-reply`,
    # `cb-seller-notification`) da Amazon: só assim vira linha da plataforma.
    oficial: bool = False
    valor: float | None = None
    motivo: str | None = None
    # Dias que o texto dá para a loja agir (aberta/informação) ou recorrer (concedida).
    dias_prazo: int | None = None
    dias_recurso: int | None = None
    # O status saiu de uma frase conhecida do modelo? False = palpite ("aberta"
    # porque nada casou): nunca muda a linha que já existe (`gravar`).
    reconhecido: bool = True

    @property
    def aberta(self) -> bool:
        return self.status in STATUS_ABERTOS

    @property
    def externo_id(self) -> str | None:
        return f"{PREFIXO[self.tipo]}{self.pedido}" if self.pedido else None


def _dominio(endereco: str | None) -> str:
    return (endereco or "").rpartition("@")[2].strip().lower().rstrip(".")


def _local(endereco: str | None) -> str:
    return (endereco or "").rpartition("@")[0].strip().lower()


def e_da_amazon(endereco: str | None) -> bool:
    """O remetente é a Amazon (amazon.com, amazon.com.br…)? A retransmissão do
    comprador (`…@marketplace.amazon.*`) não é: é o cliente escrevendo."""
    dominio = _dominio(endereco)
    if dominio.startswith(_RETRANSMISSAO):
        return False
    return bool(_DOMINIO_AMAZON.fullmatch(dominio))


def _tipo_do_remetente(endereco: str | None) -> str | None:
    """atoz | chargeback pelo remetente do modelo (ou da família dele); None = não é."""
    if not e_da_amazon(endereco):
        return None
    local = _local(endereco)
    if local in _LOCAIS:
        return _LOCAIS[local]
    return next((tipo for prefixo, tipo in _PREFIXOS_LOCAIS if local.startswith(prefixo)), None)


def e_oficial(endereco: str | None) -> bool:
    """Um dos remetentes do modelo da reivindicação/estorno, num domínio da Amazon."""
    return _tipo_do_remetente(endereco) is not None


def _tipo(de: str | None, plataforma: str | None, assunto: str, corpo: str) -> str | None:
    """atoz | chargeback | None — pelo remetente, senão pelo assunto (só da Amazon)."""
    pelo_remetente = _tipo_do_remetente(de)
    if pelo_remetente is not None:
        return pelo_remetente
    if not (e_da_amazon(de) or plataforma == PLATAFORMA):
        return None
    if _ASSUNTO_ATOZ.search(assunto):
        return TIPO_ATOZ
    if _ASSUNTO_CHARGEBACK.search(assunto):
        return TIPO_CHARGEBACK
    if _INFO_NECESSARIA.search(assunto) and _FALA_DE_ESTORNO.search(corpo):
        # "Seu pedido da Amazon.com.br …: Informação necessária" (o estorno).
        return TIPO_CHARGEBACK
    return None


def _frases(plano: str) -> list[str]:
    """As frases que AFIRMAM algo (sem as de hipótese nem as perguntas)."""
    ditas: list[str] = []
    for bruta in _FRASE.findall(plano):
        frase = bruta.strip(" \t\r-–—•*>:,\"'()")
        if not frase or frase.endswith("?"):
            continue
        if _HIPOTESE_INICIO.search(frase) or _HIPOTESE_MEIO.search(frase):
            continue
        ditas.append(frase)
    return ditas


def _lados(frases: list[str]) -> set[str]:
    """Os lados que as frases dizem: negada (a favor da loja), concedida, retirada."""
    lados: set[str] = set()
    for frase in frases:
        # A negação primeiro, e ela SAI da frase: "não debitamos" contém "debitamos".
        resto, negou = _NEGA.subn(" ", frase)
        if negou or _NEGADA.search(resto):
            lados.add(STATUS_NEGADA)
        if _CONCEDIDA.search(resto):
            lados.add(STATUS_CONCEDIDA)
        if _RETIRADA.search(resto):
            lados.add(STATUS_RETIRADA)
    return lados


def ler_status(assunto: str, corpo: str) -> tuple[str, bool]:
    """(status, reconhecido) pelo assunto e pelo texto (os dois PLANOS). PURA.

    O lado vem das frases que afirmam (nunca da hipótese); dois lados no mesmo
    e-mail = `decidida` (ver o e-mail). Sem lado: o assunto de decisão, o
    pedido de informação e a aberta dita com todas as letras. Nada disso =
    `aberta` NÃO reconhecida (palpite: não muda a linha que já existe).
    """
    frases = _frases(f"{assunto}\n{corpo}")
    lados = _lados(frases)
    if len(lados) == 1:
        return next(iter(lados)), True
    if lados:
        return STATUS_DECIDIDA, True
    if _DECISAO.search(assunto):
        return STATUS_DECIDIDA, True
    ditas = "\n".join(frases)
    if _INFO_NECESSARIA.search(assunto) or _PEDE_INFORMACAO.search(ditas):
        return STATUS_INFORMACAO, True
    if _ABERTA.search(ditas):
        return STATUS_ABERTA, True
    return STATUS_ABERTA, False


def status_do_email(assunto: str, corpo: str) -> str:
    """O status pelo assunto e pelo texto (os dois PLANOS) — ver `ler_status`."""
    return ler_status(assunto, corpo)[0]


def _valor(plano: str) -> float | None:
    m = _VALOR_BRL.search(plano)
    if m:
        return float(m.group(1).replace(".", "").replace(",", "."))
    m = _VALOR_PONTO.search(plano)
    if m:
        return float(m.group(1) or m.group(2))
    return None


def _motivo(tipo: str, texto: str, plano: str) -> str | None:
    if tipo == TIPO_ATOZ:
        m = _MOTIVO_ATOZ.search(texto or "")
        if m:
            frase = " ".join(m.group(1).split())
            frase = frase[:1].upper() + frase[1:]
            return frase if len(frase) <= MAX_MOTIVO else frase[: MAX_MOTIVO - 1].rstrip() + "…"
        return None
    if "operadora do cartao" in plano or "contestacao de estorno" in plano:
        return "Contestação de estorno aberta pela operadora do cartão"
    return None


def ler(
    *,
    de: str | None,
    plataforma: str | None,
    assunto: str | None,
    texto: str | None,
) -> Reivindicacao | None:
    """O e-mail é reivindicação A a Z / estorno da Amazon? PURA. None = não é."""
    titulo = plano_de(assunto or "")
    corpo = plano_de(texto or "")
    tipo = _tipo(de, plataforma, titulo, corpo)
    if tipo is None:
        return None
    oficial = e_oficial(de)
    pedidos = list(dict.fromkeys(_PEDIDO.findall(f"{assunto or ''}\n{texto or ''}")))
    if not oficial and not pedidos:
        # Pelo assunto, só o e-mail de UM caso (com o pedido): a novidade sobre a
        # política da Garantia de A a Z, sem pedido, segue o caminho de sempre.
        return None
    status, reconhecido = ler_status(titulo, corpo)
    dias = _DIAS.search(corpo)
    recurso = _RECURSO.search(corpo)
    return Reivindicacao(
        reconhecido=reconhecido,
        tipo=tipo,
        status=status,
        # Um pedido só: dois números no mesmo e-mail não escolhem sozinhos.
        pedido=pedidos[0] if len(pedidos) == 1 else None,
        da_amazon=e_da_amazon(de),
        oficial=oficial,
        valor=_valor(corpo),
        motivo=_motivo(tipo, texto or "", corpo),
        dias_prazo=int(dias.group(1)) if dias and status in STATUS_ABERTOS else None,
        dias_recurso=int(recurso.group(1)) if recurso and status == STATUS_CONCEDIDA else None,
    )


def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _dia(quando: datetime) -> str:
    return quando.astimezone(SAO_PAULO).strftime("%d/%m/%Y")


def status_texto(r: Reivindicacao, quando: datetime) -> str:
    """O status em português, como o resumo do pedido e o cartão mostram."""
    nome = "Estorno" if r.tipo == TIPO_CHARGEBACK else "Reivindicação"
    if r.status == STATUS_ABERTA and not r.reconhecido:
        return f"{nome} na Amazon — o e-mail não diz a situação (ver o e-mail)"
    if r.status == STATUS_ABERTA:
        return f"{nome} aberta na Amazon — a Amazon espera a loja"
    if r.status == STATUS_INFORMACAO:
        return "A Amazon pede informações à loja"
    if r.status == STATUS_CONCEDIDA:
        texto = "Concedida ao comprador"
        if r.valor is not None:
            texto += f" — {_brl(r.valor)} debitado da conta"
        if r.dias_recurso:
            fim = quando + timedelta(days=r.dias_recurso)
            texto += f"; dá para recorrer até {_dia(fim)} no Seller Central"
        return texto
    if r.status == STATUS_NEGADA:
        if r.tipo == TIPO_CHARGEBACK:
            return "Resolvido a favor da loja — sem débito"
        return "Negada — decidida a favor da loja"
    if r.status == STATUS_RETIRADA:
        return "Retirada pelo comprador"
    return "Decidida pela Amazon (ver o e-mail)"


def status_texto_sem_noticia(tipo: str | None, desde: datetime) -> str:
    """O texto da linha que `encerrar_sem_noticia` fechou."""
    nome = "Estorno" if tipo == TIPO_CHARGEBACK else "Reivindicação"
    return (
        f"{nome} sem notícia da Amazon desde {_dia(desde)} — encerrada pelo DaVinci "
        f"depois de {SEM_NOTICIA.days} dias (confira no Seller Central)"
    )


def acao_texto(r: Reivindicacao) -> str | None:
    """O que a Amazon espera da loja (só enquanto aberta)."""
    if r.status == STATUS_INFORMACAO:
        if r.tipo == TIPO_CHARGEBACK:
            return (
                "responder ao e-mail do estorno com o comprovante de envio e de entrega "
                "(senão a Amazon debita o valor)"
            )
        return "mandar à Amazon as informações pedidas (rastreio, comprovante de entrega)"
    if r.status == STATUS_ABERTA and not r.reconhecido:
        return "ler o e-mail da Amazon e conferir o caso no Seller Central"
    if r.status == STATUS_ABERTA:
        return "responder à reivindicação no Seller Central (Garantia de A a Z)"
    return None


def _iso(quando: datetime | None) -> str | None:
    return quando.isoformat() if quando is not None else None


def _de_iso(bruto: Any) -> datetime | None:
    if not isinstance(bruto, str) or not bruto:
        return None
    try:
        quando = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


async def _linha(session: AsyncSession, externo_id: str) -> AtendimentoReclamacao | None:
    return (
        await session.execute(
            select(AtendimentoReclamacao)
            .where(
                AtendimentoReclamacao.plataforma == PLATAFORMA,
                AtendimentoReclamacao.externo_id == externo_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def gravar(
    session: AsyncSession,
    r: Reivindicacao,
    *,
    quando: datetime,
    integration_id: Any = None,
    conversa_id: Any = None,
    assunto: str | None = None,
    mail_message_id: Any = None,
) -> AtendimentoReclamacao | None:
    """O caso em `atendimento_reclamacoes` (cria ou atualiza). Não commita.

    None = não grava (sem pedido, ou remetente que não é o do modelo da
    Amazon — `oficial`). Quem chama garante o resto (não suspeito, loja da
    Amazon) e roda num SAVEPOINT: um erro aqui nunca derruba o e-mail.
    Idempotente: o mesmo e-mail duas vezes regrava o mesmo; a decisão que
    chega depois atualiza o status (e encerra a linha). O e-mail cujo status
    é palpite (`reconhecido=False`) só cria a linha (o primeiro do caso):
    na que já existe, nunca muda o status nem reabre — só completa o valor
    e o motivo e conta como notícia do caso (`visto_em`, para o
    `encerrar_sem_noticia`).
    """
    externo_id = r.externo_id
    if externo_id is None or not r.oficial:
        return None
    criada = False
    linha = await _linha(session, externo_id)
    if linha is None:
        nova = AtendimentoReclamacao(
            plataforma=PLATAFORMA,
            externo_id=externo_id,
            tipo=RECLAMACAO_TIPO_RECLAMACAO,
            pedido_marketplace=r.pedido,
            dados={},
        )
        try:
            async with session.begin_nested():
                session.add(nova)
                await session.flush()
            linha = nova
            criada = True
        except IntegrityError:
            # Outro e-mail do mesmo caso gravou no meio: atualiza o dele.
            linha = await _linha(session, externo_id)
            if linha is None:
                raise
    anteriores = dict(linha.dados or {})
    ultimo = _de_iso(anteriores.get("email_em"))
    visto = _de_iso(anteriores.get("visto_em"))
    if visto is None or quando > visto:
        anteriores["visto_em"] = _iso(quando)
    if linha.aberta_em is None or quando < linha.aberta_em:
        linha.aberta_em = quando
    if integration_id is not None and linha.integration_id is None:
        linha.integration_id = integration_id
    if conversa_id is not None and linha.conversa_id is None:
        linha.conversa_id = conversa_id
    if (ultimo is not None and quando < ultimo) or (not r.reconhecido and not criada):
        # E-mail mais velho que o último lido (reprocessado fora de ordem), ou
        # o texto que não diz a situação: não volta o status para trás nem
        # reabre o caso decidido; só completa o valor e o motivo que faltavam.
        if r.valor is not None and anteriores.get("valor_reembolso") is None:
            anteriores["valor_reembolso"] = r.valor
            anteriores["moeda"] = "BRL"
        if r.motivo and not linha.motivo:
            linha.motivo = r.motivo
        linha.dados = anteriores
        await session.flush()
        return linha
    linha.tipo = RECLAMACAO_TIPO_RECLAMACAO
    linha.status = r.status
    linha.pedido_marketplace = r.pedido
    if r.motivo:
        linha.motivo = r.motivo
    if r.aberta:
        estava_aberta = linha.encerrada_em is None
        linha.encerrada_em = None
        if r.dias_prazo:
            linha.prazo_em = quando + timedelta(days=r.dias_prazo)
        elif not estava_aberta:
            linha.prazo_em = None
        # Aberta e sem prazo no e-mail novo: fica o prazo que o anterior deu.
    else:
        linha.encerrada_em = quando
        linha.prazo_em = None
    recurso = (
        quando + timedelta(days=r.dias_recurso)
        if r.dias_recurso and r.status == STATUS_CONCEDIDA
        else None
    )
    valor = r.valor if r.valor is not None else anteriores.get("valor_reembolso")
    linha.dados = {
        **anteriores,
        "fonte": FONTE_EMAIL,
        "tipo_caso": r.tipo,
        "status_texto": status_texto(r, quando),
        "status_reconhecido": r.reconhecido,
        "acao_texto": acao_texto(r),
        # Quem encerrou: o e-mail (None) ou `encerrar_sem_noticia`.
        "encerrada_por": None,
        "valor_reembolso": valor,
        "moeda": "BRL" if valor is not None else None,
        "recurso_ate": _iso(recurso) or (anteriores.get("recurso_ate") if not r.aberta else None),
        "email_em": _iso(quando),
        "assunto": (assunto or "")[:200] or anteriores.get("assunto"),
        "mail_message_id": str(mail_message_id) if mail_message_id else None,
    }
    await session.flush()
    return linha


# ── A aberta sem notícia (16/11, revisão) ─────────────────────────────────


def vence_em(linha: AtendimentoReclamacao) -> datetime | None:
    """Quando a linha ABERTA fecha por falta de notícia. PURA.

    `SEM_NOTICIA` depois do mais novo entre o prazo que o e-mail deu, o último
    e-mail que mudou o status e o último e-mail do caso (`visto_em`, inclusive
    o que não disse a situação). None = já encerrada (ou sem data nenhuma).
    """
    if linha.encerrada_em is not None:
        return None
    dados = linha.dados if isinstance(linha.dados, dict) else {}
    datas = [
        d
        for d in (
            linha.prazo_em,
            _de_iso(dados.get("email_em")),
            _de_iso(dados.get("visto_em")),
            linha.aberta_em,
        )
        if d is not None
    ]
    return max(datas) + SEM_NOTICIA if datas else None


def _e_do_email() -> Any:
    """A linha da Amazon que o e-mail gravou ("atoz:<pedido>" / "cb:<pedido>")."""
    return and_(
        AtendimentoReclamacao.plataforma == PLATAFORMA,
        or_(*(AtendimentoReclamacao.externo_id.startswith(p) for p in PREFIXO.values())),
    )


async def encerrar_sem_noticia(
    session: AsyncSession,
    *,
    agora: datetime,
    limite: int = MAX_SEM_NOTICIA_POR_VOLTA,
    recalcular: bool = True,
) -> int:
    """Fecha as abertas sem notícia há `SEM_NOTICIA` (`vence_em`). Não commita.

    A decisão pode não passar pela ponte (outro remetente, só no Gmail, numa
    pasta de Chamado, texto que não reconhecemos) ou nunca vir (o estorno
    resolvido sem aviso): sem isto, a linha aberta deixava a etiqueta
    Reclamação nas conversas do pedido, a IA mandando a conversa para pessoa e
    as automações paradas para sempre. Fecha com `sem_noticia` (o texto diz
    desde quando, e manda conferir no Seller Central) e recalcula a etiqueta
    das conversas do pedido (`recalcular`). Só banco: nada sai para a Amazon.
    Travadas com SKIP LOCKED: a linha que um e-mail está gravando agora fica
    para a próxima volta. Devolve quantas fechou.
    """
    linhas = list(
        (
            await session.execute(
                select(AtendimentoReclamacao)
                .where(
                    _e_do_email(),
                    AtendimentoReclamacao.encerrada_em.is_(None),
                    # Condição NECESSÁRIA (`vence_em` ≥ aberta_em + SEM_NOTICIA):
                    # a consulta só traz as que podem ter vencido.
                    AtendimentoReclamacao.aberta_em <= agora - SEM_NOTICIA,
                )
                .order_by(AtendimentoReclamacao.aberta_em.asc())
                .limit(max(1, limite))
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    fechadas: list[AtendimentoReclamacao] = []
    for linha in linhas:
        fim = vence_em(linha)
        if fim is None or fim > agora:
            continue
        dados = dict(linha.dados or {})
        desde = fim - SEM_NOTICIA
        linha.status = STATUS_SEM_NOTICIA
        linha.encerrada_em = agora
        linha.prazo_em = None
        linha.dados = {
            **dados,
            "status_texto": status_texto_sem_noticia(_texto(dados.get("tipo_caso")), desde),
            "acao_texto": None,
            "encerrada_por": STATUS_SEM_NOTICIA,
        }
        fechadas.append(linha)
    if not fechadas:
        return 0
    await session.flush()
    if recalcular:
        await _recalcular_etiquetas(session, fechadas, agora)
    return len(fechadas)


def _texto(valor: Any) -> str | None:
    return valor if isinstance(valor, str) and valor else None


async def _recalcular_etiquetas(
    session: AsyncSession, linhas: list[AtendimentoReclamacao], agora: datetime
) -> None:
    """A etiqueta das conversas do pedido (a Reclamação sai), como a leitura do ML."""
    # Import tardio: `reclamacoes` lê este módulo (o título e o status da tela).
    from app.services.atendimento import reclamacoes

    for linha in linhas:
        extra = []
        if linha.conversa_id is not None:
            conversa = await session.get(AtendimentoConversa, linha.conversa_id)
            if conversa is not None:
                extra.append(conversa)
        alvo = await reclamacoes.conversas_para_etiqueta(
            session, PLATAFORMA, [linha.pedido_marketplace], extra
        )
        await reclamacoes.recalcular_etiquetas(
            session, alvo, motivo=MOTIVO_ETIQUETA_SEM_NOTICIA, agora=agora
        )
