"""Adaptador da Amazon POR E-MAIL (lote C, 25/09/2026).

A Amazon não tem API de leitura de mensagem de comprador — nem para o Duoke,
nem para o eDesk, nem para ninguém. Ela manda cada mensagem ao e-mail
cadastrado no Seller Central, vinda de um endereço de RETRANSMISSÃO
(`...@marketplace.amazon.com.br`); a resposta é um e-mail de volta a esse
endereço, saindo do e-mail AUTORIZADO (`settings.atendimento_amazon_remetente`).
A Amazon repassa ao comprador e a resposta aparece no "Mensagens" do Seller
Central — e conta para a métrica de 24 h.

Três coisas desenham este arquivo:

1. NUNCA MARCAR COMO LIDO. A pasta abre em modo leitura (`EXAMINE`) e o
   corpo vem por `BODY.PEEK[]`. Não se move, não se apaga, não se marca
   nada: a caixa continua servindo a quem a olha pelo webmail.

2. UMA CAIXA PARA AS QUATRO CONTAS (kfa, kia, nexus, poofy). O sync chama
   `sincronizar` uma vez por canal Amazon, mas ler a mesma caixa quatro
   vezes por rodada é trabalho e conexão à toa. Quem pega a trava Redis lê
   para todas; os outros devolvem o resultado dessa leitura. O cursor
   (`uidvalidity` + `ultimo_uid`) é gravado IGUAL em todos os canais
   Amazon e relido do banco DENTRO da trava — qualquer canal pode ser o
   leitor da vez. Cada e-mail vai para a conta certa pelo "+" do
   destinatário (`atendimento+kfa@...`), senão pelo pedido no espelho do
   Bling, senão por uma conversa anterior da mesma thread; sem nada disso,
   cai em "Amazon (conta não identificada)" — melhor na fila sem dono do
   que fora dela (o prazo de 24 h corre igual).

3. O TEXTO É SÓ O DO COMPRADOR. O e-mail da Amazon vem embrulhado num
   modelo (cabeçalho, rodapé, avisos) e, quando o comprador responde, com o
   histórico citado. A IA e a tela querem a pergunta, não o embrulho.

4. A THREAD. O e-mail seguinte do comprador pode vir sem o nº do pedido
   (resposta à nossa resposta): ele entra na conversa de que faz parte — pelo
   In-Reply-To/References (o Message-ID de uma mensagem já gravada, nossa ou
   dele) e, sem isso, pela conversa mais recente do mesmo endereço de
   retransmissão. A conversa que nasceu "sem conta" é ADOTADA pela conta
   quando um e-mail seguinte da mesma thread a identifica (senão ficariam
   duas, uma delas aguardando para sempre sem poder ser respondida).

5. "ENVIADA" = O NOSSO SMTP ACEITOU, não a Amazon. A Amazon pode recusar
   depois (remetente não autorizado naquela conta, conteúdo proibido,
   SPF/DMARC) e avisa por e-mail — devolução do provedor (mailer-daemon,
   `multipart/report`) ou aviso da Amazon (no-reply). Esses avisos são lidos
   e casados com o NOSSO Message-ID (In-Reply-To, References ou o cabeçalho
   do e-mail devolvido citado): a mensagem vira `falhou` e a conversa volta
   para a fila.

6. A RESPOSTA DADA PELO SELLER CENTRAL chega como CÓPIA (visto no 1º e-mail
   real, 28/09/2026): `donotreply@amazon.com`, cabeçalho
   `X-Space-Notification-Type: BBC_MESSAGE_CONFIRMATION_TO_MERCHANT`,
   assunto "Seu e-mail para <nome do comprador>", só HTML. Ela não traz
   caso, pedido (na pergunta pré-venda) nem endereço de retransmissão: liga
   à conversa pela conta (o "+conta") e pelo pedido que ela cita ou, sem
   ele, pelo NOME do comprador — em conversa com pergunta anterior à cópia.
   Vira mensagem da LOJA com origem `externo`, no lugar dela pelo Date: se
   é a resposta mais nova, a conversa sai da fila e a IA se cala. Nunca
   cria conversa e nunca chuta: duas conversas do mesmo nome → nenhuma sai
   da fila (a tela avisa "conferir"); cópia da NOSSA resposta (a Amazon
   pode copiar o que sai pelo DaVinci) → só confirma a nossa; pergunta que
   ainda não chegou → a cópia espera no cursor. Os outros avisos do
   `donotreply` continuam ignorados. Resposta dada por um e-mail que não
   passe pela Amazon (nem pelo DaVinci) continua invisível — por isso o
   automático segue sem responder a Amazon sozinho.

7. O RODAPÉ do e-mail do comprador traz dois links úteis: "Solucionar o
   caso" (o "não precisa de resposta" da Amazon, link assinado) e o de
   denúncia, com o id do caso. Vão para `conversa.dados`
   (`amazon_link_sem_resposta`, `amazon_caso_id`, `amazon_link_caso`) para a
   tela abrir o caso no Seller Central — nunca para o texto da mensagem. Só
   valem os do RODAPÉ (depois do marcador de fim): link que o comprador
   escreve na mensagem não vira botão, e o caso sai primeiro do "+caso" do From.

O IMAP e o SMTP da stdlib são bloqueantes: rodam em `asyncio.to_thread`. O
que dá para testar sem rede (interpretar e-mail, extrair texto, montar a
resposta) são funções PURAS, separadas da conversa com o servidor.

Texto, assunto e nome de comprador nunca vão para o log — só UID, contagens
e códigos. Senha nunca aparece (nem no `repr` da configuração).
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import imaplib
import re
import smtplib
import ssl
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email import message_from_bytes
from email import policy as politica_email
from email.message import EmailMessage, Message
from email.utils import formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Store,
    StoreInfo,
)
from app.redis_client import redis
from app.services.atendimento import gravar, lojas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CANAL_EMAIL,
    CONVERSA_FECHADA,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_EXTERNO,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
    ResultadoSync,
)
from app.services.atendimento.enviar import LIMIAR_ENVIOU_IGUAL

logger = structlog.get_logger()

PLATAFORMA = IntegrationPlatform.AMAZON.value
CANAL = CANAL_EMAIL

# Conta da conversa quando nem o "+", nem o pedido, nem a thread disseram de
# quem é. A pessoa vê na lista e sabe que precisa descobrir pela tela.
SEM_CONTA = "Amazon (conta não identificada)"

# Só isto é mensagem de comprador. O resto da caixa (propaganda, aviso de
# conta, e-mail de gente) é ignorado — e contado no log.
DOMINIOS_RETRANSMISSAO = ("marketplace.amazon.com.br", "marketplace.amazon.com")
# Endereço da Amazon que não recebe resposta (aviso automático, não comprador).
_LOCAIS_SEM_RESPOSTA = ("donotreply", "do-not-reply", "noreply", "no-reply")

# ID de pedido da Amazon: 3-7-7 dígitos (ex.: 701-1234567-1234567).
RE_PEDIDO = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")

# E-mail sem nº do pedido e sem In-Reply-To conhecido cai na conversa mais
# recente do mesmo endereço de retransmissão — se ela teve movimento neste
# prazo. Mais velha que isso, é assunto novo.
JANELA_THREAD = timedelta(days=30)

# A cópia da resposta dada no Seller Central só fecha conversa cuja pergunta
# chegou até 7 dias antes dela: mais velha que isso, não é mais "a" pergunta
# que alguém acabou de responder (e o prazo de 24 h já passou de todo jeito).
JANELA_CONFIRMACAO = timedelta(days=7)
# A cópia que chega ANTES da pergunta (o aviso do comprador atrasou uma
# rodada) não tem conversa ainda: fica no cursor e é tentada de novo quando
# entra e-mail de comprador — por um dia e no máximo estas. Depois disso o
# prazo de 24 h já passou de todo jeito.
JANELA_COPIA_PENDENTE = timedelta(hours=24)
MAX_COPIAS_PENDENTES = 50
# A cópia da resposta que saiu PELO DaVinci: a nossa saiu até 2 h antes do
# Date da cópia (a Amazon repassa e só depois copia) ou até 10 min depois
# (relógios diferentes).
JANELA_NOSSA_ANTES = timedelta(hours=2)
JANELA_NOSSA_DEPOIS = timedelta(minutes=10)

# O tipo de notificação da Amazon (cabeçalho `X-Space-Notification-Type`).
# A do comprador é BBC_MESSAGE_SENT_TO_MERCHANT; a cópia do que a loja mandou
# pelo Seller Central, esta.
CABECALHO_NOTIFICACAO = "X-Space-Notification-Type"
TIPO_CONFIRMACAO = "BBC_MESSAGE_CONFIRMATION_TO_MERCHANT"

# "Abrir no Seller Central": a caixa de mensagens filtrada pelo caso.
URL_CASO = "https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId&ss={id}&cc={id}"

# Primeira leitura (ou caixa recriada): uma semana para trás. Mensagem mais
# velha que isso já perdeu o prazo de 24 h de qualquer jeito.
DIAS_PRIMEIRA_LEITURA = 7
# Teto por rodada: a primeira leitura de uma caixa cheia não pode prender o
# worker. O cursor anda pelo último UID lido; o resto vem na próxima rodada.
LIMITE_POR_RODADA = 200
# UIDs por FETCH: comando curto, resposta que cabe na memória.
LOTE_FETCH = 25
TIMEOUT_REDE_S = 30

# A trava da caixa compartilhada. Enquanto um canal lê, vale "lendo" por até
# TRAVA_LEITURA_S (se o processo morrer, a trava se solta sozinha). Terminada
# a leitura, vira o RESULTADO dela por TRAVA_RODADA_S: os outros canais Amazon
# da mesma rodada do cron (de 2 em 2 min) devolvem esse resultado sem abrir
# outra conexão; na rodada seguinte a chave já expirou e alguém lê de novo.
CHAVE_TRAVA = "atendimento:amazon_email:caixa"
TRAVA_LEITURA_S = 240
TRAVA_RODADA_S = 60
_MARCA_LENDO = "lendo"
_MARCA_OK = "ok"
_MARCA_ERRO = "erro:"

# Onde procurar o "+conta" do destinatário. `To` primeiro (é o que a Amazon
# endereçou); os outros cobrem redirecionamento e alias do provedor.
_CABECALHOS_DESTINO = ("To", "Delivered-To", "X-Original-To", "Cc", "Envelope-To")

_MESES_IMAP = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ── Configuração ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigCaixa:
    """A caixa de e-mail da Amazon, lida dos settings `atendimento_amazon_*`.

    O SMTP usa `atendimento_amazon_smtp_usuario`/`_senha` quando preenchidos;
    cada um vazio cai no do IMAP (é a mesma caixa na maioria dos provedores
    com senha de app, mas há provedor com usuário de SMTP próprio).
    """

    imap_host: str
    imap_port: int
    usuario: str
    senha: str = field(repr=False)
    pasta: str = "INBOX"
    smtp_host: str = ""
    smtp_port: int = 587
    remetente: str = ""
    smtp_usuario: str = ""
    smtp_senha: str = field(default="", repr=False)

    @property
    def pode_ler(self) -> bool:
        return bool(self.imap_host and self.usuario and self.senha)

    @property
    def pode_enviar(self) -> bool:
        return bool(self.smtp_host and self.smtp_usuario and self.smtp_senha and self.remetente)


def configuracao() -> ConfigCaixa:
    """Lê os settings na hora (os testes trocam os valores com monkeypatch)."""
    s = get_settings()
    usuario = (s.atendimento_amazon_imap_usuario or "").strip()
    senha = s.atendimento_amazon_imap_senha or ""
    # `getattr`: o par de SMTP é do bloco de settings do núcleo; sem ele (ou
    # vazio), vale o do IMAP.
    smtp_usuario = (getattr(s, "atendimento_amazon_smtp_usuario", "") or "").strip()
    smtp_senha = getattr(s, "atendimento_amazon_smtp_senha", "") or ""
    return ConfigCaixa(
        imap_host=(s.atendimento_amazon_imap_host or "").strip(),
        imap_port=int(s.atendimento_amazon_imap_port or 993),
        usuario=usuario,
        senha=senha,
        pasta=(s.atendimento_amazon_imap_pasta or "INBOX").strip() or "INBOX",
        smtp_host=(s.atendimento_amazon_smtp_host or "").strip(),
        smtp_port=int(s.atendimento_amazon_smtp_port or 587),
        remetente=(s.atendimento_amazon_remetente or "").strip(),
        smtp_usuario=smtp_usuario or usuario,
        smtp_senha=smtp_senha or senha,
    )


def leitura_configurada() -> bool:
    """A caixa pode ser lida? (o sync usa para deixar o canal `desligado`)."""
    return configuracao().pode_ler


# ── Endereços ─────────────────────────────────────────────────────────────


def eh_endereco_de_retransmissao(endereco: str | None) -> bool:
    """Endereço de retransmissão de comprador da Amazon (e não aviso automático)."""
    endereco = (endereco or "").strip().lower()
    local, arroba, dominio = endereco.rpartition("@")
    if not arroba or not local or dominio not in DOMINIOS_RETRANSMISSAO:
        return False
    return not local.startswith(_LOCAIS_SEM_RESPOSTA)


def _enderecos(msg: Message, cabecalho: str) -> list[tuple[str, str]]:
    """(nome, endereço) de um cabeçalho de endereço, tolerante a cabeçalho torto.

    Com a política `default` o cabeçalho já vem decodificado (RFC 2047) e com
    `.addresses`; parsear de novo a string decodificada quebraria em nome com
    vírgula ("Silva, Maria"). Só no fallback é que se parseia texto.
    """
    saida: list[tuple[str, str]] = []
    for valor in msg.get_all(cabecalho, []) or []:
        try:
            enderecos = getattr(valor, "addresses", None)
        except (ValueError, TypeError, IndexError):
            enderecos = None
        if enderecos:
            saida.extend((a.display_name or "", a.addr_spec or "") for a in enderecos)
        else:
            saida.extend(getaddresses([str(valor)]))
    return [(nome, end.strip()) for nome, end in saida if end and "@" in end]


def _tag_do_destinatario(msg: Message) -> str | None:
    """O "+conta" do endereço que recebeu: `atendimento+kfa@...` → `kfa`."""
    for cabecalho in _CABECALHOS_DESTINO:
        for _nome, endereco in _enderecos(msg, cabecalho):
            local = endereco.rpartition("@")[0]
            if "+" in local:
                tag = local.split("+", 1)[1].strip().casefold()
                if tag:
                    return tag
    return None


_RE_SUFIXO_AMAZON = re.compile(
    r"\s*[-–—|]\s*(amazon(\.com)?(\.br)?\s+marketplace|marketplace\s+(da\s+)?amazon"
    r"|amazon(\.com)?(\.br)?)\s*$",
    re.IGNORECASE,
)


def _nome_do_comprador(nome: str | None) -> str | None:
    """"Maria Silva - Amazon Marketplace" → "Maria Silva"."""
    limpo = _RE_SUFIXO_AMAZON.sub("", " ".join((nome or "").split())).strip(" \"'")
    if not limpo or limpo.casefold() in ("amazon", "amazon marketplace", "marketplace"):
        return None
    return limpo


# ── Texto do e-mail ───────────────────────────────────────────────────────


class _TextoDoHtml(HTMLParser):
    """HTML → texto simples, sem biblioteca: parágrafo vira linha, estilo some.

    `blockquote` é o histórico citado da resposta (Gmail, Outlook): fica fora
    — menos com `manter_citacao`, para o e-mail que é da própria Amazon (a
    cópia da resposta da loja), em que um `blockquote` pode ser o próprio
    texto entre os marcadores.
    """

    _IGNORAR = frozenset({"script", "style", "head", "title", "blockquote"})
    _BLOCO = frozenset({
        "p", "div", "tr", "li", "ul", "ol", "table", "section", "article",
        "header", "footer", "center", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    })

    def __init__(self, *, manter_citacao: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorando = 0
        self._ignorar = self._IGNORAR - {"blockquote"} if manter_citacao else self._IGNORAR

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._ignorar:
            self._ignorando += 1
        elif self._ignorando:
            return
        elif tag == "br":
            self.partes.append("\n")
        elif tag in self._BLOCO:
            self._quebra()
        elif tag == "td":
            self.partes.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignorar:
            self._ignorando = max(0, self._ignorando - 1)
        elif not self._ignorando and tag in self._BLOCO:
            self._quebra()

    def _quebra(self) -> None:
        # Fim de um bloco colado no começo do outro (</p><div>) é UMA quebra;
        # linha em branco de propósito só com <br><br>.
        if self.partes and not self.partes[-1].endswith("\n"):
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignorando:
            # Quebra de linha no fonte do HTML não é quebra no texto.
            self.partes.append(re.sub(r"\s+", " ", data))


def html_para_texto(html: str, *, manter_citacao: bool = False) -> str:
    """Texto legível de um corpo HTML (sem tags, sem estilo, sem citação)."""
    parser = _TextoDoHtml(manter_citacao=manter_citacao)
    try:
        parser.feed(html or "")
        parser.close()
    except (AssertionError, ValueError):  # HTML quebrado: fica o que deu para ler
        pass
    return _limpar("".join(parser.partes))


def _limpar(texto: str) -> str:
    """Uma linha em branco no máximo entre parágrafos; sem espaço sobrando."""
    linhas = [" ".join(linha.replace("\xa0", " ").split()) for linha in texto.splitlines()]
    saida: list[str] = []
    for linha in linhas:
        if not linha and (not saida or not saida[-1]):
            continue
        saida.append(linha)
    return "\n".join(saida).strip()


# Os marcadores do modelo da Amazon em volta da mensagem. Os REAIS (e-mails
# de 28/09/2026):
#   comprador:            "------------- Mensagem:  -------------" (dois espaços)
#                         "------------- Encerrar mensagem -------------"
#   cópia da nossa resposta: "------------- Iniciar mensagem -------------"
#                         "------------- Mensagem final -------------"
# e os que já se conheciam: "Begin message"/"End message", "Início da
# mensagem"/"Fim da mensagem", "Message:". Espaço entre as palavras e em
# volta dos traços é qualquer um (inclusive quebra de linha): a cópia vem só
# em HTML, e o HTML convertido pode deixar marcador e texto na MESMA linha —
# por isso a busca é no texto corrido, não linha a linha.
_TRACOS = r"[-–—_=─]{3,}"
_MARCA_INICIO = (
    r"mensagem|message"
    r"|(begin|start)\s+(of\s+)?(the\s+)?message"
    r"|in[ií]cio\s+da\s+mensagem"
    r"|iniciar\s+(a\s+)?mensagem"
)
_MARCA_FIM = (
    r"fim\s+da\s+mensagem|end\s+(of\s+)?(the\s+)?message"
    r"|encerrar\s+(a\s+)?mensagem|mensagem\s+final|final\s+message"
)
_RE_INICIO_MSG = re.compile(rf"{_TRACOS}\s*(?:{_MARCA_INICIO})\s*:?\s*{_TRACOS}", re.IGNORECASE)
_RE_FIM_MSG = re.compile(rf"{_TRACOS}\s*(?:{_MARCA_FIM})\s*:?\s*{_TRACOS}", re.IGNORECASE)

# O rodapé do modelo quando o marcador de FIM não veio: dali para baixo é da
# Amazon ("Este e-mail foi útil?", "Solucionar o caso", "Denunciar atividade
# questionável" — cada um com o seu link), não do comprador.
_RE_RODAPE = re.compile(
    r"^[ \t]*(este\s+e-?mail\s+foi\s+[uú]til|solucionar\s+o\s+caso|denunciar\s+atividade"
    r"|was\s+this\s+e-?mail\s+helpful|resolve\s+(the\s+)?case|report\s+questionable)",
    re.IGNORECASE | re.MULTILINE,
)
# O rodapé em QUALQUER posição da linha. A cópia da resposta da loja vem só
# em HTML e vira um parágrafo único: "... Atenciosamente, Amazon.com.br ...
# Este e-mail foi útil? [commMgrTok:...]" fica na mesma linha do texto, e o
# `_RE_RODAPE` (começo de linha) não pega. São frases que ninguém escreve no
# meio de uma mensagem.
_RE_RODAPE_SOLTO = re.compile(
    r"atenciosamente,?\s+amazon\b|\[commmgrtok:|direitos\s+autorais\s+\d{4}\s+amazon"
    r"|este\s+e-?mail\s+foi\s+[uú]til\s*\?",
    re.IGNORECASE,
)
# Um marcador de traços com uma palavra no meio ("------------- Fim do texto
# -------------"). Com o INÍCIO achado e o fim não, o primeiro depois do
# início é o fim num formato que a Amazon trocou (já trocou uma vez: o
# "Encerrar mensagem" de 28/09). Linha só de traços não conta (é separador),
# nem traços em linhas diferentes.
_RE_MARCADOR_TRACOS = re.compile(rf"{_TRACOS}[ \t]*[^\W\d_][^\n\-–—_=─]{{0,48}}?{_TRACOS}")
# Link do Seller Central nunca é fala de ninguém: sai do texto em qualquer caso.
_RE_LINK_SELLER =re.compile(r"https?://sellercentral[\w.-]*\.amazon\.[\w.]+[^\s<>\"']*", re.I)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in decomposto if not unicodedata.combining(ch))
# Histórico citado: "Em qua., 24 de set. de 2026 às 10:00, Fulano <x@y> escreveu:"
# / "On Wed, Sep 24, 2026 at 10:00 AM Fulano <x@y> wrote:" — às vezes em
# duas linhas. Exige um dígito (data/hora) para não cortar "Em relação ao...".
_RE_CITACAO_ABRE = re.compile(r"^\s*(em|on)\s+\S", re.IGNORECASE)
_RE_CITACAO_FECHA = re.compile(r"(escreveu|wrote)\s*:\s*$", re.IGNORECASE)
_RE_ORIGINAL = re.compile(
    r"^\s*-{2,}\s*(mensagem\s+original|original\s+message|mensagem\s+encaminhada"
    r"|forwarded\s+message)\s*-{2,}\s*$",
    re.IGNORECASE,
)
_RE_DE = re.compile(r"^\s*(de|from)\s*:", re.IGNORECASE)
_RE_ENVIADO = re.compile(r"^\s*(enviado|enviada|sent|data|date)\s*:", re.IGNORECASE)


def _eh_atribuicao(linhas: Sequence[str], i: int) -> bool:
    """A linha i abre o histórico citado ("Em ... escreveu:", "De: ... Enviado:")?"""
    linha = linhas[i]
    if _RE_ORIGINAL.match(linha):
        return True
    if _RE_CITACAO_ABRE.match(linha):
        junta = linha
        if not _RE_CITACAO_FECHA.search(junta) and i + 1 < len(linhas):
            junta = f"{linha} {linhas[i + 1]}"
        if _RE_CITACAO_FECHA.search(junta) and re.search(r"\d", junta):
            return True
    if _RE_DE.match(linha):
        return any(_RE_ENVIADO.match(prox) for prox in linhas[i + 1 : i + 4])
    return False


def _cortar_citacao(linhas: Sequence[str]) -> list[str]:
    """Tira o histórico: linhas com ">" e tudo depois da atribuição."""
    saida: list[str] = []
    for i, linha in enumerate(linhas):
        if _eh_atribuicao(linhas, i):
            break
        if linha.lstrip().startswith(">"):
            continue
        saida.append(linha)
    return saida


@dataclass(frozen=True)
class _Fatias:
    """O corpo cortado pelos marcadores: o que vem antes e o miolo."""

    antes: str  # o que vem antes do marcador de início (ou tudo, sem ele)
    miolo: str  # entre os marcadores (sem o de fim: até o rodapé)
    achou_inicio: bool
    achou_fim: bool


def _fatiar(corpo: str) -> _Fatias:
    """Acha os marcadores do modelo no texto corrido e devolve as fatias."""
    # NFC: "Início" com o acento decomposto (í = i + ´) casaria só com o `i`.
    texto = unicodedata.normalize("NFC", corpo or "").replace("\r\n", "\n").replace("\r", "\n")
    inicio = _RE_INICIO_MSG.search(texto)
    if inicio is not None:
        fim = _RE_FIM_MSG.search(texto, inicio.end())
        miolo = texto[inicio.end() : fim.start() if fim else len(texto)]
        return _Fatias(texto[: inicio.start()], miolo, True, fim is not None)
    fim = _RE_FIM_MSG.search(texto)
    if fim is not None:
        return _Fatias(texto[: fim.start()], texto[: fim.start()], False, True)
    return _Fatias(texto, texto, False, False)


def _texto_do_miolo(fatias: _Fatias) -> str:
    """O miolo limpo: sem rodapé (quando faltou o fim), citação nem link da Amazon.

    Sem o marcador de fim, corta no que vier primeiro: o rodapé em começo de
    linha, o rodapé solto no meio da linha (HTML de um parágrafo só) e — se
    o início foi achado — o próximo marcador de traços, que é o fim num
    formato novo. Sem isso a cópia gravava o rodapé inteiro da Amazon como
    resposta da loja (e virava "exemplo da equipe" para a IA).
    """
    miolo = fatias.miolo
    if not fatias.achou_fim:
        achados = [_RE_RODAPE.search(miolo), _RE_RODAPE_SOLTO.search(miolo)]
        if fatias.achou_inicio:
            achados.append(_RE_MARCADOR_TRACOS.search(miolo))
        cortes = [a.start() for a in achados if a is not None]
        if cortes:
            miolo = miolo[: min(cortes)]
    miolo = _RE_LINK_SELLER.sub("", miolo)
    return _limpar("\n".join(_cortar_citacao(miolo.split("\n"))))


def extrair_mensagem(corpo: str) -> str:
    """Só o que o comprador escreveu, a partir do corpo do e-mail em texto.

    Com os marcadores do modelo da Amazon, pega o que está entre eles. Só o
    de FIM à vista (o de início veio num formato que não conhecemos): corta
    dali para baixo — o rodapé do modelo ("retain all messages...") não é
    fala do comprador. Sem marcador de fim, corta no rodapé conhecido
    ("Este e-mail foi útil?", "Solucionar o caso"...). Sem marcador nenhum, o
    corpo inteiro. Em todos os casos corta o histórico citado e tira link do
    Seller Central (o 1º e-mail real gravou 1.374 caracteres de rodapé com
    três links, porque "Encerrar mensagem" não era conhecido).
    """
    return _texto_do_miolo(_fatiar(corpo))


def _decodificar(parte: Message) -> str:
    """Texto de uma parte, sobrevivendo a charset errado ou desconhecido."""
    try:
        conteudo = parte.get_content()  # type: ignore[attr-defined]
        if isinstance(conteudo, str):
            return conteudo
    except (LookupError, UnicodeError, KeyError, AttributeError, AssertionError):
        pass
    bruto = parte.get_payload(decode=True) or b""
    if not isinstance(bruto, bytes):
        return str(bruto)
    charset = parte.get_content_charset() or "utf-8"
    try:
        return bruto.decode(charset, errors="replace")
    except LookupError:
        return bruto.decode("utf-8", errors="replace")


def _nome_do_arquivo(parte: Message) -> str | None:
    try:
        nome = parte.get_filename()
    except (ValueError, TypeError, LookupError):
        return None
    if not nome:
        return None
    return " ".join(str(nome).split()) or None


def _partes_de_texto(msg: Message) -> tuple[str | None, str | None, list[dict]]:
    """(text/plain, text/html, anexos) — a primeira de cada, sem converter.

    Parte com nome de arquivo é anexo (mesmo um .txt "inline"): o corpo é a
    parte sem nome. Do anexo fica SÓ o nome — baixar arquivo de comprador é
    escopo perdido em v1 (e é dado pessoal parado no banco).
    """
    plain: str | None = None
    html: str | None = None
    anexos: list[dict] = []
    for parte in msg.walk():
        if parte.is_multipart():
            continue
        nome = _nome_do_arquivo(parte)
        if nome or parte.get_content_disposition() == "attachment":
            anexos.append({"tipo": "arquivo", "nome": nome or "anexo"})
            continue
        tipo = parte.get_content_type()
        if tipo == "text/plain" and plain is None:
            plain = _decodificar(parte)
        elif tipo == "text/html" and html is None:
            html = _decodificar(parte)
    return plain, html, anexos


def _corpo(plain: str | None, html: str | None, *, manter_citacao: bool = False) -> str:
    """O corpo em texto: prefere text/plain; sem ele, HTML → texto."""
    if plain and plain.strip():
        return plain
    if html:
        return html_para_texto(html, manter_citacao=manter_citacao)
    return plain or ""


# ── Links do rodapé (caso, "não precisa de resposta") ─────────────────────

# Id de caso aceito para montar URL: letras, dígitos e hífen (o real é um
# UUID). Qualquer outra coisa não entra em link que a tela vai abrir.
_RE_ID_CASO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,63}")
_RE_URL_SELLER = re.compile(r"https://sellercentral[\w.-]*\.amazon\.[\w.]+/[^\s<>\"'()\[\]]+", re.I)
_HOSTS_SELLER = ("sellercentral.amazon.com.br", "sellercentral.amazon.com")


def _id_de_caso(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor if _RE_ID_CASO.fullmatch(valor) else None


def link_do_caso(caso_id: str) -> str:
    """"Abrir no Seller Central": a caixa de mensagens filtrada pelo caso."""
    return URL_CASO.format(id=caso_id)


def _urls_de(texto: str) -> list[str]:
    """Os links do Seller Central de um trecho, na ordem, sem repetir."""
    urls: list[str] = []
    for url in _RE_URL_SELLER.findall(texto):
        url = url.rstrip(".,;:")
        if url not in urls:
            urls.append(url)
    return urls


def _urls_do_rodape(plain: str | None, html: str | None) -> list[str]:
    """Os links do Seller Central do RODAPÉ, na ordem em que valem.

    O comprador escreve ANTES do rodapé e pode colar ali um link do Seller
    Central (de outro caso, ou um "não precisa de resposta" com assinatura
    falsa). O texto gravado nem mostra esses links (`_texto_do_miolo` os
    tira), então a equipe não veria que o botão passou a ser o do comprador.
    Por isso, em cada fonte (texto puro primeiro, depois o HTML, com `&amp;`
    desfeito — no HTML o link mora no `href`):
      - com o marcador de fim à vista, só vale o que vem DEPOIS do último
        (ninguém escreve depois do fim do modelo);
      - sem ele, a última ocorrência vem primeiro (o rodapé fica no fim);
      - link que aparece no trecho do comprador de uma fonte não vale em
        nenhuma — o HTML repete a mensagem, às vezes sem o marcador.
    """
    do_comprador: set[str] = set()
    por_fonte: list[list[str]] = []
    for fonte in (plain or "", html_lib.unescape(html or "")):
        texto = unicodedata.normalize("NFC", fonte)
        fins = list(_RE_FIM_MSG.finditer(texto))
        if fins:
            do_comprador.update(_urls_de(texto[: fins[-1].end()]))
            por_fonte.append(_urls_de(texto[fins[-1].end() :]))
        else:
            por_fonte.append(list(reversed(_urls_de(texto))))
    saida: list[str] = []
    for urls in por_fonte:
        for url in urls:
            if url not in do_comprador and url not in saida:
                saida.append(url)
    return saida


def _links_do_rodape(
    plain: str | None, html: str | None, remetente: str
) -> tuple[str | None, str | None]:
    """(link "não precisa de resposta", id do caso) do e-mail do comprador.

    - o caso: primeiro o que vem depois do "+" no endereço de retransmissão
      (`<id>+<caso>@marketplace.amazon.com.br`) — é o From que a AMAZON
      escreveu, não o texto do comprador; sem ele, o `cc`/`ss` do link de
      denúncia do rodapé (`/messaging/inbox?fi=caseId`);
    - "Solucionar o caso" → `/messaging/no-response-needed?t=...&h=...`:
      link ASSINADO (o `h`), guardado como veio — mexer nele invalida.
    Só links do rodapé (`_urls_do_rodape`), só host do Seller Central, só https.
    """
    sem_resposta: str | None = None
    caso: str | None = None
    local = remetente.rpartition("@")[0]
    if "+" in local:
        caso = _id_de_caso(local.split("+", 1)[1])
    for url in _urls_do_rodape(plain, html):
        partes = urlsplit(url)
        if partes.scheme != "https" or partes.hostname not in _HOSTS_SELLER:
            continue
        caminho = partes.path.rstrip("/").lower()
        if caminho == "/messaging/no-response-needed" and sem_resposta is None:
            sem_resposta = url
        elif caminho == "/messaging/inbox" and caso is None:
            query = parse_qs(partes.query)
            for chave in ("cc", "ss"):
                caso = caso or next(
                    (c for v in query.get(chave, []) if (c := _id_de_caso(v))), None
                )
    return sem_resposta, caso


# ── O e-mail interpretado ─────────────────────────────────────────────────


@dataclass
class EmailAmazon:
    """Uma mensagem de comprador, já tirada do e-mail. Sem rede, sem banco."""

    message_id: str
    remetente: str  # endereço de retransmissão, minúsculo
    remetente_nome: str | None
    tag: str | None  # o "+conta" do destinatário
    assunto: str
    pedido: str | None
    enviada_em: datetime | None
    texto: str
    anexos: list[dict] = field(default_factory=list)
    uid: int | None = None
    # Message-IDs de In-Reply-To/References: é por eles que o e-mail seguinte
    # (sem o nº do pedido) acha a conversa de que faz parte.
    referencias: list[str] = field(default_factory=list)
    # Do rodapé: o "não precisa de resposta" da Amazon e o id do caso.
    link_sem_resposta: str | None = None
    caso_id: str | None = None

    @property
    def conversa_externo_id(self) -> str:
        """Uma conversa por (comprador, pedido): o mesmo comprador com dois
        pedidos são dois assuntos, com dois prazos."""
        return f"{self.remetente}|{self.pedido or '-'}"

    @property
    def link_caso(self) -> str | None:
        return link_do_caso(self.caso_id) if self.caso_id else None


def _message_id(msg: Message, bruto: bytes) -> str:
    """O Message-ID, que é a chave de idempotência da mensagem.

    Sem ele (raro, mas existe), um hash do e-mail cru: o mesmo e-mail relido
    na próxima rodada dá o mesmo id. Longo demais para a coluna, idem.
    """
    valor = "".join(str(msg.get("Message-ID", "") or "").split())
    if valor and len(valor) <= 191:
        return valor
    base = valor.encode() if valor else bruto
    return f"sem-id:{hashlib.sha256(base).hexdigest()[:48]}"


_RE_MSGID = re.compile(r"<[^<>\s]+>")


def _referencias(msg: Message) -> list[str]:
    """Os Message-IDs de In-Reply-To e References, na ordem, sem repetir."""
    vistos: list[str] = []
    for cabecalho in ("In-Reply-To", "References"):
        for valor in msg.get_all(cabecalho, []) or []:
            for mid in _RE_MSGID.findall(str(valor)):
                if mid not in vistos and len(mid) <= 191:
                    vistos.append(mid)
    return vistos


def _data(msg: Message) -> datetime | None:
    """O `Date` do e-mail em UTC — é o relógio da Amazon para o prazo."""
    valor = msg.get("Date")
    if valor is None:
        return None
    quando = getattr(valor, "datetime", None)
    if quando is None:
        try:
            quando = parsedate_to_datetime(str(valor))
        except (TypeError, ValueError, IndexError):
            return None
    if quando is None:
        return None
    if quando.tzinfo is None:
        return quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC)


def interpretar_email(bruto: bytes, *, uid: int | None = None) -> EmailAmazon | None:
    """E-mail cru → mensagem de comprador, ou None se não é de comprador.

    Só remetente `@marketplace.amazon.com.br` / `.com` conta; o resto da caixa
    é ignorado aqui mesmo, sem nem ler o corpo.
    """
    msg = message_from_bytes(bruto, policy=politica_email.default)
    remetentes = _enderecos(msg, "From")
    if not remetentes:
        return None
    nome, endereco = remetentes[0]
    endereco = endereco.strip().lower()
    if not eh_endereco_de_retransmissao(endereco):
        return None

    assunto = " ".join(str(msg.get("Subject", "") or "").split())
    plain, html, anexos = _partes_de_texto(msg)
    corpo = _corpo(plain, html)
    link_sem_resposta, caso_id = _links_do_rodape(plain, html, endereco)
    achado = RE_PEDIDO.search(assunto) or RE_PEDIDO.search(corpo)
    return EmailAmazon(
        link_sem_resposta=link_sem_resposta,
        caso_id=caso_id,
        message_id=_message_id(msg, bruto),
        remetente=endereco,
        remetente_nome=_nome_do_comprador(nome),
        tag=_tag_do_destinatario(msg),
        assunto=assunto,
        pedido=achado.group(0) if achado else None,
        enviada_em=_data(msg),
        texto=extrair_mensagem(corpo),
        anexos=anexos,
        uid=uid,
        referencias=_referencias(msg),
    )


# ── Devolução / recusa do que NÓS mandamos ────────────────────────────────


@dataclass
class DevolucaoAmazon:
    """Aviso de que um e-mail NOSSO não chegou: os Message-IDs que ele cita."""

    referencias: list[str]
    motivo: str
    uid: int | None = None


# Quem manda aviso de devolução: o servidor de e-mail (qualquer domínio) e a
# própria Amazon, de um endereço que não recebe resposta.
_LOCAIS_DEVOLUCAO = ("mailer-daemon", "postmaster")
_DOMINIOS_AMAZON = ("amazon.com", "amazon.com.br")
# O assunto do aviso da Amazon / do provedor (sem acento, minúsculo).
_RE_ASSUNTO_DEVOLUCAO = re.compile(
    r"undeliver|not\s+(be\s+)?delivered|delivery\s+(status|failure|has\s+failed)"
    r"|failure\s+notice|returned\s+mail|mail\s+delivery|message\s+not\s+sent"
    r"|could\s+not\s+be\s+(sent|delivered)|rejected|blocked"
    r"|nao\s+(foi\s+|pode\s+ser\s+)?(entregue|enviad)"
    r"|falha\s+(na|de)\s+entrega|devolvid|recusad|rejeitad|bloquead",
    re.IGNORECASE,
)
_RE_STATUS_DSN = re.compile(r"^\s*status\s*:\s*([245]\.\d{1,3}\.\d{1,3})", re.IGNORECASE | re.M)
_RE_MSGID_CITADO = re.compile(r"^\s*message-id\s*:\s*(<[^<>\s]+>)", re.IGNORECASE | re.M)


def _eh_da_amazon_sem_resposta(endereco: str) -> bool:
    local, _, dominio = endereco.rpartition("@")
    da_amazon = any(dominio == d or dominio.endswith(f".{d}") for d in _DOMINIOS_AMAZON)
    return da_amazon and local.startswith(_LOCAIS_SEM_RESPOSTA)


def aviso_da_amazon(bruto: bytes) -> dict | None:
    """Remetente e assunto de um aviso AUTOMÁTICO da Amazon (domínio amazon.*,
    não retransmissão de comprador), pro log; None pra e-mail de gente.

    02/10/2026 (701-7824777-7251447): a devolução pedida pela cliente não chegou
    ao DaVinci — e a caixa conta "1 ignorado" sem dizer o que era. Antes de
    ler aviso de devolução, é preciso saber QUAIS avisos a Amazon manda pra
    esta caixa (Vinicius: "tudo vem no e-mail mesmo")."""
    msg = message_from_bytes(bruto, policy=politica_email.default)
    remetentes = _enderecos(msg, "From")
    endereco = remetentes[0][1].strip().lower() if remetentes else ""
    dominio = endereco.rpartition("@")[2]
    if not any(dominio == d or dominio.endswith(f".{d}") for d in _DOMINIOS_AMAZON):
        return None
    assunto = " ".join(str(msg.get("Subject", "") or "").split())
    achado = RE_PEDIDO.search(assunto)
    return {
        "remetente": endereco,
        "assunto": assunto[:200],
        "pedido": achado.group(0) if achado else None,
        "tag": _tag_do_destinatario(msg),
    }


def _texto_da_parte(parte: Message) -> str:
    """O texto cru de uma parte, para achar ids e o status da entrega.

    `message/rfc822` (o e-mail devolvido, citado inteiro) e
    `message/delivery-status` (blocos de cabeçalho: `Status: 5.7.1`) vêm
    como mensagens, não como texto — viram texto pelos próprios cabeçalhos.
    """
    if parte.get_content_maintype() == "message":
        interno = parte.get_payload()
        blocos = interno if isinstance(interno, list) else [interno]
        return "\n".join(str(b) for b in blocos if isinstance(b, Message))
    try:
        return _decodificar(parte)
    except Exception:  # noqa: BLE001 — parte torta: fica sem
        return ""


def interpretar_devolucao(bruto: bytes, *, uid: int | None = None) -> DevolucaoAmazon | None:
    """E-mail cru → aviso de devolução/recusa de um e-mail NOSSO, ou None.

    Conta como aviso: relatório de entrega (`multipart/report`), e-mail de
    mailer-daemon/postmaster, ou e-mail de um endereço sem resposta da
    Amazon com assunto de falha de entrega. O que ele cita (In-Reply-To,
    References, o Message-ID do e-mail devolvido) é o que casa com a nossa
    mensagem — quem decide se é nossa é a gravação, pelo banco.
    """
    msg = message_from_bytes(bruto, policy=politica_email.default)
    remetentes = _enderecos(msg, "From")
    endereco = remetentes[0][1].strip().lower() if remetentes else ""
    local = endereco.rpartition("@")[0]
    assunto = _sem_acento(" ".join(str(msg.get("Subject", "") or "").split()))
    relatorio = (
        msg.get_content_type() == "multipart/report"
        and (msg.get_param("report-type") or "").lower() == "delivery-status"
    )
    do_servidor = local.startswith(_LOCAIS_DEVOLUCAO)
    da_amazon = _eh_da_amazon_sem_resposta(endereco) and bool(_RE_ASSUNTO_DEVOLUCAO.search(assunto))
    if not (relatorio or do_servidor or da_amazon):
        return None

    referencias = _referencias(msg)
    status_dsn: str | None = None
    for parte in msg.walk():
        if parte.get_content_maintype() == "multipart":
            continue
        texto = _texto_da_parte(parte)
        if status_dsn is None and (achado := _RE_STATUS_DSN.search(texto)):
            status_dsn = achado.group(1)
        for mid in _RE_MSGID_CITADO.findall(texto):
            if mid not in referencias and len(mid) <= 191:
                referencias.append(mid)
    if status_dsn and not status_dsn.startswith("5"):
        return None  # 4.x.x = atraso, o servidor ainda tenta; 2.x.x = entregue
    if status_dsn:
        motivo = f"devolvido {status_dsn}"
    else:
        motivo = "recusado pela Amazon" if da_amazon else "devolvido"
    # Sem referência nenhuma não há o que casar: o aviso só entra na contagem do log.
    return DevolucaoAmazon(referencias=referencias, motivo=motivo, uid=uid)


# ── Cópia da resposta dada pelo Seller Central ────────────────────────────


@dataclass
class ConfirmacaoAmazon:
    """A cópia que a Amazon manda do que a LOJA escreveu pelo Seller Central."""

    message_id: str
    tag: str | None  # o "+conta" do destinatário: a conta que respondeu
    comprador_nome: str | None  # do assunto "Seu e-mail para <nome>"
    texto: str | None  # a resposta (None: marcador não achado)
    pedido: str | None
    enviada_em: datetime | None
    uid: int | None = None


_RE_ASSUNTO_CONFIRMACAO = re.compile(
    r"^\s*(?:seu\s+e-?mail\s+para|your\s+e-?mail\s+to)\s+(?P<nome>.+?)\s*$", re.IGNORECASE
)
# "Aqui está uma cópia do e-mail que você enviou para <nome>." — o nome de
# reserva, quando o assunto vier diferente.
_RE_COPIA_PARA = re.compile(
    r"(?:c[oó]pia\s+do\s+e-?mail\s+que\s+voc[eê]\s+enviou\s+para"
    r"|copy\s+of\s+the\s+e-?mail\s+you\s+sent\s+to)\s+(?P<nome>[^\n]+?)\s*\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _nome_limpo(nome: str | None) -> str | None:
    limpo = " ".join((nome or "").split()).strip(" \"'.,;:")
    return limpo or None


def _autenticacao_reprovada(msg: Message) -> bool:
    """O provedor da caixa disse que o remetente é FALSO (DMARC reprovado)?

    Qualquer um pode escrever "From: donotreply@amazon.com"; uma cópia falsa
    tiraria da fila uma pergunta que ninguém respondeu. O Gmail (e quase todo
    provedor) carimba `Authentication-Results`; só a reprovação EXPLÍCITA
    barra — sem o cabeçalho (outro provedor, teste), vale o resto das regras.
    """
    resultados = " ".join(str(v) for v in msg.get_all("Authentication-Results", []) or [])
    return bool(re.search(r"\bdmarc\s*=\s*fail\b", resultados, re.IGNORECASE))


def interpretar_confirmacao(bruto: bytes, *, uid: int | None = None) -> ConfirmacaoAmazon | None:
    """E-mail cru → cópia da resposta dada no Seller Central, ou None.

    É cópia: remetente sem resposta da Amazon (`donotreply@amazon.com`) E
    (cabeçalho `X-Space-Notification-Type: BBC_MESSAGE_CONFIRMATION_TO_MERCHANT`
    OU assunto "Seu e-mail para <nome>"). Cabeçalho com OUTRO tipo manda: é
    outro aviso, mesmo com assunto parecido. Os demais avisos do
    `donotreply` não são cópia (None) e seguem ignorados.
    """
    msg = message_from_bytes(bruto, policy=politica_email.default)
    remetentes = _enderecos(msg, "From")
    endereco = remetentes[0][1].strip().lower() if remetentes else ""
    if not _eh_da_amazon_sem_resposta(endereco):
        return None
    tipo = " ".join(str(msg.get(CABECALHO_NOTIFICACAO, "") or "").split()).upper()
    assunto = " ".join(str(msg.get("Subject", "") or "").split())
    pelo_assunto = _RE_ASSUNTO_CONFIRMACAO.match(assunto)
    if tipo and tipo != TIPO_CONFIRMACAO:
        return None
    if tipo != TIPO_CONFIRMACAO and pelo_assunto is None:
        return None
    if _autenticacao_reprovada(msg):
        logger.warning("atendimento_amazon_email_confirmacao_reprovada", uid=uid)
        return None

    plain, html, _anexos = _partes_de_texto(msg)
    # A cópia é da Amazon, não resposta de gente: um `blockquote` ali pode ser
    # o próprio texto da loja.
    fatias = _fatiar(_corpo(plain, html, manter_citacao=True))
    texto = _texto_do_miolo(fatias) if fatias.achou_inicio else ""
    nome = _nome_limpo(pelo_assunto.group("nome")) if pelo_assunto else None
    if nome is None and (achado := _RE_COPIA_PARA.search(fatias.antes)):
        nome = _nome_limpo(achado.group("nome"))
    pedido = RE_PEDIDO.search(texto) or RE_PEDIDO.search(assunto)
    return ConfirmacaoAmazon(
        message_id=_message_id(msg, bruto),
        tag=_tag_do_destinatario(msg),
        comprador_nome=nome,
        texto=texto or None,
        pedido=pedido.group(0) if pedido else None,
        enviada_em=_data(msg),
        uid=uid,
    )


# ── A resposta ────────────────────────────────────────────────────────────

# "Re:", "RE:", "Res:" (Outlook em português), "Re[2]:" — um ou vários.
_RE_PREFIXO_RESPOSTA = re.compile(r"^(\s*(re|res)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)


def assunto_da_resposta(assunto: str | None, pedido: str | None = None) -> str:
    """"Re: <assunto>" — sem "Re: Re:" quando o assunto já vem respondido."""
    base = _RE_PREFIXO_RESPOSTA.sub("", " ".join((assunto or "").split())).strip()
    if not base:
        base = f"Pedido {pedido}" if pedido else "Mensagem do vendedor"
    return f"Re: {base}"


def _dominio(endereco: str) -> str | None:
    return parseaddr(endereco)[1].rpartition("@")[2] or None


def corpo_da_resposta(texto: str | None, pedido: str | None) -> str:
    """O texto que SAI no e-mail: o digitado + "Pedido: <id>" quando ele não cita o pedido.

    Separado do `montar_resposta` porque a leitura da caixa precisa do mesmo
    texto para reconhecer a cópia que a Amazon manda da NOSSA resposta — a
    mensagem gravada guarda só o que a pessoa escreveu.
    """
    corpo = (texto or "").strip()
    if pedido and pedido not in corpo:
        corpo = f"{corpo}\n\nPedido: {pedido}"
    return corpo


def montar_resposta(
    *,
    remetente: str,
    para: str,
    assunto: str | None,
    texto: str,
    pedido: str | None,
    em_resposta_a: str | None,
    message_id: str | None = None,
) -> EmailMessage:
    """O e-mail de resposta, texto puro, pronto para o SMTP.

    - De: o e-mail AUTORIZADO no Seller Central (de outro endereço, a Amazon
      descarta sem avisar); Para: o endereço de retransmissão do comprador.
    - In-Reply-To/References: o último e-mail do comprador — é o que prende
      a resposta na thread do Seller Central.
    - Sem o ID do pedido no texto, acrescenta "Pedido: <id>" (regra da Amazon
      para mensagem ao comprador).
    - Message-ID nosso: vira o `externo_id` da mensagem enviada.
    """
    corpo = corpo_da_resposta(texto, pedido)
    msg = EmailMessage(policy=politica_email.SMTP)
    msg["From"] = remetente
    msg["To"] = para
    msg["Subject"] = assunto_da_resposta(assunto, pedido)
    msg["Date"] = formatdate(usegmt=True)
    # Domínio do remetente: o default do make_msgid é o nome da máquina.
    msg["Message-ID"] = message_id or make_msgid(domain=_dominio(remetente))
    anterior = "".join((em_resposta_a or "").split())
    if anterior:
        msg["In-Reply-To"] = anterior
        msg["References"] = anterior
    # quoted-printable: acento chega inteiro mesmo em servidor sem 8BITMIME.
    msg.set_content(corpo, subtype="plain", charset="utf-8", cte="quoted-printable")
    return msg


# ── IMAP (bloqueante; roda em thread) ─────────────────────────────────────


class ErroCaixa(Exception):  # noqa: N818 — português, como `EnvioRecusado`
    """A caixa respondeu algo que não é OK (pasta que não abre, busca recusada)."""


@dataclass
class LeituraCaixa:
    """O que uma rodada trouxe da caixa: e-mails crus e onde o cursor para.

    `copias_pendentes`: as cópias de resposta do Seller Central que ainda
    esperam a pergunta chegar. Vêm do cursor (não da caixa) e a gravação
    (`_gravar_leitura`) troca a lista pelas que continuam esperando — é ela
    que volta para o cursor, na mesma transação do resto da rodada.
    """

    uidvalidity: int
    ultimo_uid: int | None
    mensagens: list[tuple[int, bytes]] = field(default_factory=list)
    restantes: int = 0
    recomecou: bool = False
    copias_pendentes: list[ConfirmacaoAmazon] = field(default_factory=list)


def _abrir_imap(config: ConfigCaixa) -> imaplib.IMAP4:
    """Conexão IMAP com TLS. Ponto único — os testes trocam por uma caixa falsa."""
    return imaplib.IMAP4_SSL(
        config.imap_host,
        config.imap_port,
        ssl_context=ssl.create_default_context(),
        timeout=TIMEOUT_REDE_S,
    )


def _pasta_imap(pasta: str) -> str:
    """Nome da pasta entre aspas (o imaplib não põe; "[Gmail]/Todos" quebraria)."""
    if len(pasta) >= 2 and pasta.startswith('"') and pasta.endswith('"'):
        return pasta
    return '"' + pasta.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _data_imap(dia: date) -> str:
    """Data no formato do SEARCH (18-Sep-2026) — sem depender do locale."""
    return f"{dia.day:02d}-{_MESES_IMAP[dia.month - 1]}-{dia.year}"


def _uidvalidity(imap: Any, pasta: str) -> int:
    """UIDVALIDITY da pasta aberta: vem no EXAMINE; se não vier, pergunta."""
    _tipo, dados = imap.response("UIDVALIDITY")
    for item in dados or []:
        if item:
            texto = item.decode() if isinstance(item, bytes) else str(item)
            if texto.strip().isdigit():
                return int(texto.strip())
    tipo, dados = imap.status(pasta, "(UIDVALIDITY)")
    achado = re.search(rb"UIDVALIDITY\s+(\d+)", b" ".join(d for d in dados or [] if d))
    if tipo != "OK" or not achado:
        raise ErroCaixa("UIDVALIDITY ausente")
    return int(achado.group(1))


def _uids(dados: Sequence[Any] | None) -> list[int]:
    saida: list[int] = []
    for item in dados or []:
        if not item:
            continue
        texto = item.decode() if isinstance(item, bytes) else str(item)
        saida.extend(int(p) for p in texto.split() if p.isdigit())
    return saida


_RE_UID_FETCH = re.compile(rb"\bUID\s+(\d+)", re.IGNORECASE)


def _extrair_fetch(dados: Sequence[Any] | None) -> list[tuple[int, bytes]]:
    """(uid, e-mail cru) da resposta do UID FETCH.

    O imaplib devolve `[(b'1 (UID 101 BODY[] {123}', b'<e-mail>'), b')', ...]`;
    alguns servidores mandam o UID DEPOIS do corpo (`b' UID 101)'`).
    """
    saida: list[tuple[int, bytes]] = []
    itens = list(dados or [])
    for i, item in enumerate(itens):
        if not (isinstance(item, tuple) and len(item) >= 2):
            continue
        cabeca, corpo = item[0] or b"", item[1]
        if not isinstance(corpo, bytes | bytearray):
            continue
        achado = _RE_UID_FETCH.search(cabeca)
        if achado is None and i + 1 < len(itens) and isinstance(itens[i + 1], bytes):
            achado = _RE_UID_FETCH.search(itens[i + 1])
        if achado is not None:
            saida.append((int(achado.group(1)), bytes(corpo)))
    return saida


def _ler_caixa(config: ConfigCaixa, cursor: dict, limite: int = LIMITE_POR_RODADA) -> LeituraCaixa:
    """Uma rodada de leitura, SÓ LEITURA: EXAMINE + UID SEARCH + BODY.PEEK[].

    Cursor `{"uidvalidity", "ultimo_uid"}`:
      - sem cursor, ou `ultimo_uid` vazio → SEARCH SINCE hoje-7d;
      - com cursor e mesma UIDVALIDITY → UID SEARCH ultimo+1:*;
      - UIDVALIDITY mudou (caixa recriada/migrada: os UIDs antigos não valem
        mais) → recomeça pelos 7 dias. A gravação pula o Message-ID que já
        está em QUALQUER conversa Amazon (`_ja_gravados`), então reler não
        duplica nada.
    """
    imap = _abrir_imap(config)
    try:
        imap.login(config.usuario, config.senha)
        pasta = _pasta_imap(config.pasta)
        tipo, _dados = imap.select(pasta, readonly=True)  # EXAMINE: não marca nada
        if tipo != "OK":
            raise ErroCaixa(f"pasta não abriu ({tipo})")
        uidvalidity = _uidvalidity(imap, pasta)

        mesma_caixa = cursor.get("uidvalidity") == uidvalidity
        ultimo = cursor.get("ultimo_uid") if mesma_caixa else None
        ultimo = ultimo if isinstance(ultimo, int) and ultimo > 0 else None
        if ultimo is not None:
            tipo, dados = imap.uid("SEARCH", "UID", f"{ultimo + 1}:*")
        else:
            desde = datetime.now(UTC).date() - timedelta(days=DIAS_PRIMEIRA_LEITURA)
            tipo, dados = imap.uid("SEARCH", "SINCE", _data_imap(desde))
        if tipo != "OK":
            raise ErroCaixa(f"busca recusada ({tipo})")
        # `N:*` com N acima do maior UID devolve o MAIOR UID (regra do IMAP):
        # por isso o filtro `> ultimo`, senão a última mensagem voltaria sempre.
        uids = sorted({u for u in _uids(dados) if ultimo is None or u > ultimo})
        lote = uids[:limite]

        mensagens: list[tuple[int, bytes]] = []
        for i in range(0, len(lote), LOTE_FETCH):
            parte = lote[i : i + LOTE_FETCH]
            tipo, dados = imap.uid("FETCH", ",".join(str(u) for u in parte), "(BODY.PEEK[])")
            if tipo != "OK":
                raise ErroCaixa(f"fetch recusado ({tipo})")
            mensagens.extend(_extrair_fetch(dados))
        return LeituraCaixa(
            uidvalidity=uidvalidity,
            ultimo_uid=max(lote) if lote else ultimo,
            mensagens=mensagens,
            restantes=len(uids) - len(lote),
            recomecou=bool(cursor.get("uidvalidity")) and not mesma_caixa,
        )
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError) as exc:
            logger.debug("atendimento_amazon_email_logout_falhou", erro=type(exc).__name__)


# ── SMTP (bloqueante; roda em thread) ─────────────────────────────────────

ENVIO_OK = "ok"
ENVIO_RECUSADO = "recusado"
ENVIO_AMBIGUO = "ambiguo"


def _abrir_smtp(config: ConfigCaixa) -> smtplib.SMTP:
    """Conexão SMTP. 465 = TLS direto; o resto (587) = STARTTLS logo depois."""
    if config.smtp_port == 465:
        return smtplib.SMTP_SSL(
            config.smtp_host,
            config.smtp_port,
            timeout=TIMEOUT_REDE_S,
            context=ssl.create_default_context(),
        )
    return smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=TIMEOUT_REDE_S)


def _resposta_smtp(resposta: Any) -> str:
    texto = resposta.decode(errors="replace") if isinstance(resposta, bytes) else str(resposta)
    return " ".join(texto.split())[:160]


def _enviar_smtp(config: ConfigCaixa, mensagem: EmailMessage) -> tuple[str, str | None]:
    """Entrega o e-mail → (ok | recusado | ambiguo, detalhe de operação).

    O envelope vai passo a passo (MAIL, RCPT, DATA) em vez de `send_message`
    para saber EM QUE FASE caiu:
      - antes do DATA, nada saiu: qualquer erro é recusa;
      - o servidor RESPONDEU com código de erro (mesmo no DATA): recusa;
      - a conexão caiu / estourou o tempo DEPOIS de começar o DATA: pode ter
        saído → ambíguo. Mensagem não se desenvia; quem decide é pessoa.
      - aceito (250 no fim do DATA): saiu. Erro no QUIT depois disso não
        muda nada.
    """
    para = str(mensagem["To"])
    conteudo = mensagem.as_bytes()  # política SMTP: linhas com CRLF
    fase = "conexao"
    smtp: smtplib.SMTP | None = None
    try:
        smtp = _abrir_smtp(config)
        smtp.ehlo()
        if config.smtp_port != 465:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        fase = "login"
        smtp.login(config.smtp_usuario, config.smtp_senha)
        fase = "envelope"
        codigo, resposta = smtp.mail(config.remetente)
        if codigo != 250:
            return ENVIO_RECUSADO, f"smtp {codigo} no MAIL FROM: {_resposta_smtp(resposta)}"
        codigo, resposta = smtp.rcpt(para)
        if codigo not in (250, 251):
            return ENVIO_RECUSADO, f"smtp {codigo} no RCPT TO: {_resposta_smtp(resposta)}"
        fase = "dados"
        codigo, resposta = smtp.data(conteudo)
        if codigo != 250:
            return ENVIO_RECUSADO, f"smtp {codigo} no DATA: {_resposta_smtp(resposta)}"
        fase = "aceito"
        return ENVIO_OK, None
    except smtplib.SMTPResponseException as exc:
        # O servidor disse NÃO, com código: nada foi entregue.
        return ENVIO_RECUSADO, f"smtp {exc.smtp_code} em {fase}: {_resposta_smtp(exc.smtp_error)}"
    except Exception as exc:  # noqa: BLE001 — rede/TLS/timeout: a FASE decide
        if fase == "dados":
            return ENVIO_AMBIGUO, f"{type(exc).__name__} durante o DATA"
        return ENVIO_RECUSADO, f"{type(exc).__name__} em {fase}"
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 — o resultado já está decidido
                try:
                    smtp.close()
                except OSError as exc:
                    logger.debug("atendimento_amazon_smtp_close_falhou", erro=type(exc).__name__)


# ── Trava da caixa compartilhada ──────────────────────────────────────────


def _resultado_da_marca(marca: Any) -> ResultadoSync:
    """O que devolve o canal que NÃO leu: o resultado de quem leu."""
    if isinstance(marca, bytes):
        marca = marca.decode(errors="replace")
    if isinstance(marca, str) and marca.startswith(_MARCA_ERRO):
        return ResultadoSync(status="erro", erro=marca[len(_MARCA_ERRO) :] or "imap")
    return ResultadoSync(status="ok")


async def _pegar_trava() -> tuple[bool, ResultadoSync]:
    """(leio eu?, resultado a devolver se não). Redis fora do ar → leio sem trava.

    Sem trava, no pior caso dois canais leem a mesma caixa — a gravação é
    idempotente pelo Message-ID; perder a rodada seria pior.
    """
    try:
        if await redis.set(CHAVE_TRAVA, _MARCA_LENDO, nx=True, ex=TRAVA_LEITURA_S):
            return True, ResultadoSync()
        marca = await redis.get(CHAVE_TRAVA)
    except Exception as exc:  # noqa: BLE001 — Redis não pode parar a leitura
        logger.warning("atendimento_amazon_email_sem_trava", erro=type(exc).__name__)
        return True, ResultadoSync()
    return False, _resultado_da_marca(marca)


async def _soltar_trava(resultado: ResultadoSync | None) -> None:
    """Troca "lendo" pelo resultado, por uma rodada. Exceção no meio → solta já."""
    try:
        if resultado is None:
            await redis.delete(CHAVE_TRAVA)
            return
        marca = (
            _MARCA_OK if resultado.status == "ok" else f"{_MARCA_ERRO}{resultado.erro or ''}"
        )
        await redis.set(CHAVE_TRAVA, marca, ex=TRAVA_RODADA_S)
    except Exception as exc:  # noqa: BLE001 — a trava expira sozinha
        logger.warning("atendimento_amazon_email_trava_nao_soltou", erro=type(exc).__name__)


# ── Cursor compartilhado ──────────────────────────────────────────────────


def _filtro_canais_amazon():
    return (AtendimentoCanal.plataforma == PLATAFORMA, AtendimentoCanal.canal == CANAL)


async def _cursor_compartilhado(session: AsyncSession) -> dict:
    """O cursor da caixa, relido do BANCO (dentro da trava).

    Seleciona só a coluna: um `select(AtendimentoCanal)` devolveria o objeto
    que já está na sessão, com o cursor de quando foi carregado — e o leitor
    anterior pode ter andado com ele depois. Canal novo (cursor vazio) não
    faz a caixa recomeçar: vale o mais adiantado.
    """
    cursores = (
        await session.execute(select(AtendimentoCanal.cursor).where(*_filtro_canais_amazon()))
    ).scalars().all()
    validos = [
        c for c in cursores if isinstance(c, dict) and isinstance(c.get("uidvalidity"), int)
    ]
    if not validos:
        return {}
    escolhido = max(validos, key=lambda c: c.get("ultimo_uid") or 0)
    cursor = {"uidvalidity": escolhido["uidvalidity"], "ultimo_uid": escolhido.get("ultimo_uid")}
    if isinstance(escolhido.get("copias_pendentes"), list):
        cursor["copias_pendentes"] = escolhido["copias_pendentes"]
    return cursor


async def _gravar_cursor(session: AsyncSession, canal: AtendimentoCanal, cursor: dict) -> None:
    """O MESMO cursor em todos os canais Amazon: qualquer um pode ler a seguir."""
    canais = (
        await session.execute(select(AtendimentoCanal).where(*_filtro_canais_amazon()))
    ).scalars().all()
    for c in canais:
        c.cursor = dict(cursor)  # dicionário novo: JSONB mutado no lugar não suja
    if all(c is not canal for c in canais):
        canal.cursor = dict(cursor)
    await session.flush()


# ── De qual conta é o e-mail ──────────────────────────────────────────────


def _nome_canonico(texto: str | None) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(ch for ch in decomposto if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", "", sem_acento.casefold())


def _preferir_ativa(candidatas: list[Integration]) -> Integration | None:
    if not candidatas:
        return None
    ativas = [i for i in candidatas if i.archived_at is None]
    return (ativas or candidatas)[0]


def conta_pela_tag(tag: str | None, integracoes: Iterable[Integration]) -> Integration | None:
    """A integração Amazon cujo nome casa com o "+conta" do destinatário.

    Primeiro o nome inteiro (trim + minúsculas: "kfa" ↔ " KFA "); depois sem
    acento/pontuação ("K.F.A"); por fim a tag como PALAVRA do nome ("Amazon
    KFA") — esta só se uma conta só casar, para nunca chutar entre duas.
    """
    alvo = (tag or "").strip().casefold()
    if not alvo:
        return None
    todas = list(integracoes)
    exatas = [i for i in todas if (i.name or "").strip().casefold() == alvo]
    if exatas:
        return _preferir_ativa(exatas)
    canonico = _nome_canonico(alvo)
    if not canonico:
        return None
    parecidas = [i for i in todas if _nome_canonico(i.name) == canonico]
    if parecidas:
        return _preferir_ativa(parecidas)
    por_palavra = [
        i for i in todas
        if canonico in {_nome_canonico(p) for p in re.split(r"[\s\-_/|.]+", i.name or "")}
    ]
    return por_palavra[0] if len(por_palavra) == 1 else None


def _inteiro(valor: Any) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


async def _integracao_do_pedido(
    session: AsyncSession, pedido: str, amazon: dict[UUID, Integration]
) -> Integration | None:
    """A conta dona do pedido, pelo espelho do Bling (`numeroloja`).

    Do pedido sai a loja do Bling (`loja`) e a `store_id`; delas, a
    integração — pelo `bling_loja_id` da integração, pela loja (Store) ou
    pela ficha da loja (StoreInfo). Só vale integração Amazon.
    """
    linha = (
        await session.execute(
            select(BlingOrder.loja, BlingOrder.store_id)
            .where(BlingOrder.numeroloja == pedido)
            .order_by(BlingOrder.data.desc().nulls_last())
            .limit(1)
        )
    ).first()
    if linha is None:
        return None
    loja, store_id = linha
    loja_int = _inteiro(loja)

    if loja_int is not None:
        for integ in amazon.values():
            if integ.bling_loja_id == loja_int:
                return integ

    lojas = select(Store.id, Store.integration_id)
    if store_id is not None and loja_int is not None:
        lojas = lojas.where((Store.id == store_id) | (Store.bling_store_id == loja_int))
    elif store_id is not None:
        lojas = lojas.where(Store.id == store_id)
    elif loja_int is not None:
        lojas = lojas.where(Store.bling_store_id == loja_int)
    else:
        lojas = None
    if lojas is not None:
        for loja_id, integ_id in (await session.execute(lojas)).all():
            if integ_id in amazon:
                return amazon[integ_id]
            for integ in amazon.values():
                if integ.store_id == loja_id:
                    return integ

    if loja:
        fichas = (
            await session.execute(
                select(StoreInfo.integration_id, StoreInfo.account_name).where(
                    StoreInfo.bling_store_id == str(loja)
                )
            )
        ).all()
        for integ_id, _conta in fichas:
            if integ_id in amazon:
                return amazon[integ_id]
        # Ficha sem integração ligada: o nome da conta (o mesmo que o
        # `chamados.lookup_pedido` mostra) casado com o nome da integração.
        for _integ_id, conta in fichas:
            integ = conta_pela_tag(conta, amazon.values()) if conta else None
            if integ is not None:
                return integ
    return None


async def _integracao_da_thread(
    session: AsyncSession, externo_id: str, amazon: dict[UUID, Integration]
) -> Integration | None:
    """A conta de uma conversa anterior da mesma thread (mesma chave)."""
    ids = (
        await session.execute(
            select(AtendimentoConversa.integration_id).where(
                AtendimentoConversa.plataforma == PLATAFORMA,
                AtendimentoConversa.canal == CANAL,
                AtendimentoConversa.externo_id == externo_id,
                AtendimentoConversa.integration_id.is_not(None),
            )
        )
    ).scalars().all()
    return next((amazon[i] for i in ids if i in amazon), None)


async def _conta_do_email(
    session: AsyncSession,
    email_: EmailAmazon,
    amazon: dict[UUID, Integration],
    cache_pedidos: dict[str, Integration | None],
    externo_id: str,
) -> tuple[Integration | None, str]:
    """(integração, por onde achou): tag → pedido → thread → nenhuma."""
    if email_.tag:
        integ = conta_pela_tag(email_.tag, amazon.values())
        if integ is not None:
            return integ, "tag"
    if email_.pedido:
        if email_.pedido not in cache_pedidos:
            cache_pedidos[email_.pedido] = await _integracao_do_pedido(
                session, email_.pedido, amazon
            )
        integ = cache_pedidos[email_.pedido]
        if integ is not None:
            return integ, "pedido"
    integ = await _integracao_da_thread(session, externo_id, amazon)
    if integ is not None:
        return integ, "conversa"
    return None, "nenhuma"


def _id_do_envio():
    """O nosso Message-ID gravado no payload do envio (o ambíguo não tem `externo_id`)."""
    return AtendimentoMensagem.payload["envio"]["message_id"].astext


async def _conversa_da_thread(
    session: AsyncSession, email_: EmailAmazon, agora: datetime
) -> AtendimentoConversa | None:
    """A conversa de que um e-mail SEM nº do pedido faz parte, se der para saber.

    1. In-Reply-To/References citando uma mensagem já gravada (a do
       comprador ou a nossa resposta) — a prova da thread;
    2. sem isso, a conversa mais recente do mesmo endereço de retransmissão,
       com movimento nos últimos 30 dias.
    Sem nenhuma das duas, None (a conversa é `<endereço>|-`).
    """
    base = select(AtendimentoConversa).where(
        AtendimentoConversa.plataforma == PLATAFORMA, AtendimentoConversa.canal == CANAL
    )
    if email_.referencias:
        achada = (
            await session.execute(
                base.join(
                    AtendimentoMensagem,
                    AtendimentoMensagem.conversa_id == AtendimentoConversa.id,
                )
                .where(
                    or_(
                        AtendimentoMensagem.externo_id.in_(email_.referencias),
                        _id_do_envio().in_(email_.referencias),
                    )
                )
                .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
                .limit(1)
            )
        ).scalar_one_or_none()
        if achada is not None:
            return achada
    return (
        await session.execute(
            base.where(
                AtendimentoConversa.comprador_id == email_.remetente,
                AtendimentoConversa.ultima_mensagem_em >= agora - JANELA_THREAD,
            )
            .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _adotar_sem_conta(
    session: AsyncSession,
    externo_id: str,
    integ: Integration,
    canal: AtendimentoCanal | None,
) -> None:
    """A conversa "sem conta" desta thread passa para a conta que acabou de ser achada.

    O 1º e-mail chegou sem "+conta" e com o pedido ainda fora do espelho do
    Bling (conversa sem integração); o seguinte, da mesma thread, identificou
    a conta. Sem isto nasceria uma SEGUNDA conversa, e a primeira ficaria
    aguardando para sempre — sem poder ser respondida (`sem_integracao`),
    contando em "vencidas" e no alerta. Se as duas já existem, as mensagens
    da sem conta passam para a da conta e ela fecha.

    Quem grava conversa da Amazon é só a leitura da caixa, sob a trava Redis
    da caixa: não há corrida pelo UNIQUE. O SAVEPOINT é o cinto (Redis fora
    do ar = leitura sem trava) — se bater, fica como estava.
    """
    orfa = (
        await session.execute(
            select(AtendimentoConversa).where(
                AtendimentoConversa.plataforma == PLATAFORMA,
                AtendimentoConversa.canal == CANAL,
                AtendimentoConversa.externo_id == externo_id,
                AtendimentoConversa.integration_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if orfa is None:
        return
    dona = (
        await session.execute(
            select(AtendimentoConversa).where(
                AtendimentoConversa.integration_id == integ.id,
                AtendimentoConversa.canal == CANAL,
                AtendimentoConversa.externo_id == externo_id,
            )
        )
    ).scalar_one_or_none()
    if dona is None:
        # O nome da LOJA antes do SAVEPOINT: a consulta não entra nele.
        conta = await lojas.nome_da_loja(session, integ) or integ.name
        await session.flush()  # o pendente alheio fica fora do SAVEPOINT
        try:
            async with session.begin_nested():
                orfa.integration_id = integ.id
                orfa.canal_id = canal.id if canal is not None else None
                orfa.conta = conta
                await session.flush()
        except IntegrityError:
            logger.warning("atendimento_amazon_email_adocao_falhou", conversa_id=str(orfa.id))
            return
        logger.info(
            "atendimento_amazon_email_conversa_adotada",
            conversa_id=str(orfa.id),
            integration_id=str(integ.id),
        )
        return

    ja_na_dona = set(
        (
            await session.execute(
                select(AtendimentoMensagem.externo_id).where(
                    AtendimentoMensagem.conversa_id == dona.id,
                    AtendimentoMensagem.externo_id.is_not(None),
                )
            )
        ).scalars()
    )
    mensagens = (
        (
            await session.execute(
                select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == orfa.id)
            )
        )
        .scalars()
        .all()
    )
    for m in mensagens:
        if m.externo_id is None or m.externo_id not in ja_na_dona:
            m.conversa_id = dona.id
    orfa.situacao = CONVERSA_FECHADA
    orfa.dados = {**(orfa.dados or {}), "juntada_a": str(dona.id)}
    await session.flush()
    await gravar.recalcular_conversa(session, dona)
    await gravar.recalcular_conversa(session, orfa)
    logger.info(
        "atendimento_amazon_email_conversas_juntadas",
        conversa_id=str(dona.id),
        juntada=str(orfa.id),
        mensagens=len(mensagens),
    )


async def _aplicar_devolucoes(
    session: AsyncSession, devolucoes: list[DevolucaoAmazon]
) -> set[UUID]:
    """A nossa mensagem que a Amazon (ou o servidor) devolveu vira `falhou`.

    Casa pelo NOSSO Message-ID: o `externo_id` do envio aceito, ou o do
    payload do envio ambíguo. A conversa volta para a fila (o comprador não
    recebeu nada). Devolve as conversas mexidas.
    """
    motivo_de = {ref: d.motivo for d in devolucoes for ref in d.referencias}
    sem_referencia = sum(1 for d in devolucoes if not d.referencias)
    mexidas: set[UUID] = set()
    if motivo_de:
        envio_id = _id_do_envio()
        linhas = (
            await session.execute(
                select(AtendimentoMensagem, envio_id)
                .join(
                    AtendimentoConversa,
                    AtendimentoConversa.id == AtendimentoMensagem.conversa_id,
                )
                .where(
                    AtendimentoConversa.plataforma == PLATAFORMA,
                    AtendimentoConversa.canal == CANAL,
                    AtendimentoMensagem.autor == AUTOR_LOJA,
                    AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                    AtendimentoMensagem.status != MSG_FALHOU,
                    or_(
                        AtendimentoMensagem.externo_id.in_(list(motivo_de)),
                        envio_id.in_(list(motivo_de)),
                    ),
                )
            )
        ).all()
        for mensagem, id_envio in linhas:
            motivo = motivo_de.get(mensagem.externo_id or "") or motivo_de.get(id_envio or "")
            mensagem.status = MSG_FALHOU
            mensagem.erro = f"email {motivo or 'devolvido'}"[:500]
            mexidas.add(mensagem.conversa_id)
        for conversa_id in mexidas:
            conversa = await session.get(AtendimentoConversa, conversa_id)
            if conversa is not None:
                await gravar.recalcular_conversa(session, conversa)
    if devolucoes:
        logger.info(
            "atendimento_amazon_email_devolucoes",
            avisos=len(devolucoes),
            sem_referencia=sem_referencia,
            mensagens_devolvidas=len(mexidas),
        )
    return mexidas


@dataclass
class _DestinoDaCopia:
    """Para onde vai a cópia: a conversa, ou por que nenhuma.

    `motivo`: pedido | nome (achou) · sem_nome | sem_conversa | outro_pedido
    (nenhuma candidata) · ambigua (duas ou mais: `candidatas`).
    """

    conversa: AtendimentoConversa | None
    motivo: str
    candidatas: list[AtendimentoConversa] = field(default_factory=list)


# Sem candidata porque a pergunta ainda não chegou: a cópia volta a ser
# tentada (ver `JANELA_COPIA_PENDENTE`).
_MOTIVOS_PENDENTES = ("sem_conversa", "outro_pedido")


def _momento_da_mensagem():
    """O relógio da mensagem (o da plataforma; sem ele, quando foi gravada)."""
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _conversa_da_confirmacao(
    session: AsyncSession, confirmacao: ConfirmacaoAmazon, integ: Integration
) -> _DestinoDaCopia:
    """A conversa que a cópia responde — ou nenhuma, sem chutar.

    Candidatas: as conversas Amazon DESTA conta com alguma pergunta do
    comprador ANTES da cópia, de até 7 dias antes dela. Não precisam estar
    aguardando nem ter essa pergunta como a última: a loja manda duas
    mensagens seguidas pelo Seller Central, ou o comprador escreve de novo
    antes de a cópia ser lida — a resposta vai para o histórico do mesmo
    jeito (é ele que a IA lê). Quem decide se a conversa sai da fila é o
    `gravar`, pelos carimbos: cópia mais velha que a pergunta nova não tira
    a conversa da fila.

    Entre as candidatas:
      - a cópia cita um pedido → a conversa DESSE pedido; nenhuma com ele →
        nenhuma (a resposta é de outro assunto, e cair no nome fecharia a
        pergunta errada do mesmo comprador);
      - sem pedido → a do mesmo nome (sem acento, caixa ou pontuação).
    Uma só → ela. Duas ou mais (dois casos do mesmo comprador — cada
    "+caso" do endereço de retransmissão é uma conversa —, ou duas "Maria
    Silva") → nenhuma: a mais recente tiraria da fila a pergunta que
    ninguém respondeu e deixaria a respondida esperando.
    """
    referencia = confirmacao.enviada_em or datetime.now(UTC)
    momento = _momento_da_mensagem()
    tem_pergunta = (
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == AtendimentoConversa.id,
            AtendimentoMensagem.autor == AUTOR_CLIENTE,
            momento >= referencia - JANELA_CONFIRMACAO,
            momento <= referencia,
        )
        .exists()
    )
    candidatas = list(
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(
                    AtendimentoConversa.plataforma == PLATAFORMA,
                    AtendimentoConversa.canal == CANAL,
                    AtendimentoConversa.integration_id == integ.id,
                    tem_pergunta,
                )
                .order_by(AtendimentoConversa.ultima_do_cliente_em.desc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    if confirmacao.pedido:
        grupo = [c for c in candidatas if c.pedido_marketplace == confirmacao.pedido]
        motivo = "pedido" if grupo else "outro_pedido"
    else:
        alvo = _nome_canonico(confirmacao.comprador_nome)
        if not alvo:
            return _DestinoDaCopia(None, "sem_nome")
        grupo = [c for c in candidatas if _nome_canonico(c.comprador_nome) == alvo]
        motivo = "nome" if grupo else "sem_conversa"
    if len(grupo) == 1:
        return _DestinoDaCopia(grupo[0], motivo)
    if not grupo:
        return _DestinoDaCopia(None, motivo)
    return _DestinoDaCopia(None, "ambigua", grupo)


async def _resposta_nossa_da_copia(
    session: AsyncSession, confirmacao: ConfirmacaoAmazon, integ: Integration
) -> AtendimentoMensagem | None:
    """A mensagem que saiu PELO DaVinci de que esta cópia é cópia — se for.

    A Amazon pode mandar cópia também do que sai por e-mail (o DaVinci
    responde por SMTP ao endereço de retransmissão). A cópia não traz o
    nosso Message-ID, nem caso, nem endereço: só texto, nome e hora. Sem
    isto ela seria tratada como resposta dada no Seller Central e, com a
    nossa conversa já fora da fila, cairia em OUTRA conversa do mesmo
    comprador — fechando uma pergunta que ninguém respondeu.

    É nossa: mensagem da loja que saiu pelo DaVinci nesta conta, de 2 h
    antes até 10 min depois do Date da cópia, com o texto que SAIU (o
    `corpo_da_resposta`, com o "Pedido: <id>") igual ao da cópia — o mesmo
    limiar do "enviou igual" — e, quando os dois lados têm nome, o mesmo
    comprador. Duas iguais: a mais perto do Date.
    """
    alvo = gravar.normalizar_para_comparar(confirmacao.texto)
    if not alvo or confirmacao.enviada_em is None:
        return None
    referencia = _utc(confirmacao.enviada_em)
    momento = _momento_da_mensagem()
    linhas = (
        await session.execute(
            select(
                AtendimentoMensagem,
                AtendimentoConversa.pedido_marketplace,
                AtendimentoConversa.comprador_nome,
            )
            .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id)
            .where(
                AtendimentoConversa.plataforma == PLATAFORMA,
                AtendimentoConversa.canal == CANAL,
                AtendimentoConversa.integration_id == integ.id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                AtendimentoMensagem.status.in_((MSG_ENVIADA, MSG_REVISAR, MSG_ENVIANDO)),
                momento >= referencia - JANELA_NOSSA_ANTES,
                momento <= referencia + JANELA_NOSSA_DEPOIS,
            )
        )
    ).all()
    nome = _nome_canonico(confirmacao.comprador_nome)
    achadas: list[tuple[timedelta, AtendimentoMensagem]] = []
    for mensagem, pedido, comprador in linhas:
        do_comprador = _nome_canonico(comprador)
        if nome and do_comprador and nome != do_comprador:
            continue
        textos = {corpo_da_resposta(mensagem.texto, pedido), mensagem.texto or ""}
        if any(
            gravar.similaridade(alvo, gravar.normalizar_para_comparar(t)) >= LIMIAR_ENVIOU_IGUAL
            for t in textos
        ):
            quando = _utc(mensagem.enviada_em or mensagem.created_at) or referencia
            achadas.append((abs(referencia - quando), mensagem))
    return min(achadas, key=lambda a: a[0])[1] if achadas else None


async def _ja_gravados(session: AsyncSession, ids: Iterable[str]) -> set[str]:
    """Os Message-IDs que já são mensagem de ALGUMA conversa Amazon por e-mail.

    A idempotência do `gravar_mensagem` é por conversa: um e-mail relido que
    a thread manda para outra conversa (o mais recente do mesmo endereço
    mudou) seria gravado de novo. Esta é a checagem global, para o e-mail do
    comprador e para a cópia.
    """
    unicos = list({i for i in ids if i})
    if not unicos:
        return set()
    return set(
        (
            await session.execute(
                select(AtendimentoMensagem.externo_id)
                .join(
                    AtendimentoConversa,
                    AtendimentoConversa.id == AtendimentoMensagem.conversa_id,
                )
                .where(
                    AtendimentoConversa.plataforma == PLATAFORMA,
                    AtendimentoConversa.canal == CANAL,
                    AtendimentoMensagem.externo_id.in_(unicos),
                )
            )
        ).scalars()
    )


def _marcar_a_conferir(
    confirmacao: ConfirmacaoAmazon, candidatas: list[AtendimentoConversa]
) -> int:
    """A cópia ambígua deixa aviso nas candidatas que ela PODE ter respondido.

    Nenhuma sai da fila (seria chute), mas a pessoa precisa saber que a
    Central respondeu alguém com este nome — senão responde de novo, ou o
    alerta de prazo dispara para quem já foi atendido. Só nas que aguardam
    com a pergunta anterior à cópia; a tela mostra enquanto a conversa
    aguarda e nenhuma pergunta mais nova chegou (`routers.atendimento`).
    Devolve quantas marcou.
    """
    referencia = confirmacao.enviada_em or datetime.now(UTC)
    marcadas = 0
    for c in candidatas:
        do_cliente = _utc(c.ultima_do_cliente_em)
        if not c.aguardando_resposta or do_cliente is None or do_cliente > referencia:
            continue
        # Dicionário NOVO: JSONB mutado no lugar não marca a coluna como suja.
        c.dados = {
            **(c.dados or {}),
            "amazon_copia_a_conferir": {
                "message_id": confirmacao.message_id,
                "em": referencia.isoformat(),
            },
        }
        marcadas += 1
    return marcadas


@dataclass
class _ResultadoCopias:
    """O que as cópias da rodada fizeram."""

    gravadas: int = 0
    mexidas: set[UUID] = field(default_factory=set)
    # Sem conversa ainda: voltam ao cursor para a próxima tentativa.
    pendentes: list[ConfirmacaoAmazon] = field(default_factory=list)


async def _aplicar_confirmacoes(
    session: AsyncSession,
    confirmacoes: list[ConfirmacaoAmazon],
    amazon: dict[UUID, Integration],
) -> _ResultadoCopias:
    """Grava cada cópia como resposta da LOJA (origem `externo`) na conversa dela.

    A gravação passa pelo `gravar_mensagem` como qualquer resposta de fora:
    se a cópia é mais nova que a última pergunta, a conversa sai da fila
    (recalcula aguardando/prazo) e o rascunho pendente da IA vira
    `substituido` — a IA não responde por cima. Idempotente pelo Message-ID
    da cópia (se ele já está em alguma conversa Amazon, pula).

    Nunca cria conversa e nunca chuta: sem conta → só log; cópia da NOSSA
    resposta → só anota na nossa mensagem (`amazon_confirmacao_mid`);
    ambígua → aviso nas candidatas, nenhuma sai da fila; sem conversa (a
    pergunta ainda não chegou) → volta como pendente.
    """
    resultado = _ResultadoCopias()
    contagem = dict.fromkeys(
        ("sem_conta", "sem_nome", "sem_conversa", "ambiguas", "nossas", "repetidas", "marcadas"), 0
    )
    ja_gravadas = await _ja_gravados(session, (c.message_id for c in confirmacoes))
    for confirmacao in sorted(
        confirmacoes, key=lambda c: (c.enviada_em or datetime.max.replace(tzinfo=UTC), c.uid or 0)
    ):
        integ = conta_pela_tag(confirmacao.tag, amazon.values())
        if integ is None:
            contagem["sem_conta"] += 1
            logger.info("atendimento_amazon_email_confirmacao_sem_conta", uid=confirmacao.uid)
            continue
        if confirmacao.message_id in ja_gravadas:
            contagem["repetidas"] += 1
            continue
        nossa = await _resposta_nossa_da_copia(session, confirmacao, integ)
        if nossa is not None:
            contagem["nossas"] += 1
            if (nossa.payload or {}).get("amazon_confirmacao_mid") != confirmacao.message_id:
                nossa.payload = {
                    **(nossa.payload or {}),
                    "amazon_confirmacao_mid": confirmacao.message_id,
                }
            logger.info(
                "atendimento_amazon_email_confirmacao_nossa",
                mensagem_id=str(nossa.id),
                conversa_id=str(nossa.conversa_id),
                uid=confirmacao.uid,
            )
            continue
        destino = await _conversa_da_confirmacao(session, confirmacao, integ)
        if destino.conversa is None:
            if destino.motivo == "ambigua":
                contagem["ambiguas"] += 1
                contagem["marcadas"] += _marcar_a_conferir(confirmacao, destino.candidatas)
                logger.warning(
                    "atendimento_amazon_email_confirmacao_ambigua",
                    uid=confirmacao.uid,
                    integration_id=str(integ.id),
                    candidatas=len(destino.candidatas),
                )
            elif destino.motivo in _MOTIVOS_PENDENTES:
                contagem["sem_conversa"] += 1
                if confirmacao.enviada_em is not None:
                    resultado.pendentes.append(confirmacao)
                logger.info(
                    "atendimento_amazon_email_confirmacao_sem_conversa",
                    uid=confirmacao.uid,
                    integration_id=str(integ.id),
                    motivo=destino.motivo,
                )
            else:
                contagem["sem_nome"] += 1
                logger.info(
                    "atendimento_amazon_email_confirmacao_sem_nome",
                    uid=confirmacao.uid,
                    integration_id=str(integ.id),
                )
            continue
        conversa = destino.conversa
        _mensagem, nova = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=confirmacao.message_id,
            autor=AUTOR_LOJA,
            origem=ORIGEM_EXTERNO,
            texto=confirmacao.texto,
            enviada_em=confirmacao.enviada_em,
            payload={"imap_uid": confirmacao.uid, "amazon_confirmacao": True},
        )
        if nova:
            ja_gravadas.add(confirmacao.message_id)
            resultado.gravadas += 1
            resultado.mexidas.add(conversa.id)
            logger.info(
                "atendimento_amazon_email_confirmacao_gravada",
                conversa_id=str(conversa.id),
                uid=confirmacao.uid,
                por=destino.motivo,
                sem_texto=confirmacao.texto is None,
            )
    if confirmacoes:
        logger.info(
            "atendimento_amazon_email_confirmacoes",
            copias=len(confirmacoes),
            gravadas=resultado.gravadas,
            pendentes=len(resultado.pendentes),
            **contagem,
        )
    return resultado


# ── Cópias à espera da pergunta (no cursor) ───────────────────────────────


def _copia_para_cursor(c: ConfirmacaoAmazon) -> dict:
    """A cópia pendente como JSON do cursor (dado da loja; nunca vai para log)."""
    return {
        "message_id": c.message_id,
        "tag": c.tag,
        "nome": c.comprador_nome,
        "texto": c.texto,
        "pedido": c.pedido,
        "em": c.enviada_em.isoformat() if c.enviada_em else None,
        "uid": c.uid,
    }


def _copia_do_cursor(dado: Any) -> ConfirmacaoAmazon | None:
    """O contrário, tolerante: item torto (versão velha, edição à mão) some."""
    if not isinstance(dado, dict):
        return None
    message_id, em = dado.get("message_id"), dado.get("em")
    if not isinstance(message_id, str) or not message_id or not isinstance(em, str):
        return None
    try:
        enviada_em = _utc(datetime.fromisoformat(em))
    except ValueError:
        return None

    def _texto(chave: str) -> str | None:
        valor = dado.get(chave)
        return valor if isinstance(valor, str) and valor else None

    uid = dado.get("uid")
    return ConfirmacaoAmazon(
        message_id=message_id,
        tag=_texto("tag"),
        comprador_nome=_texto("nome"),
        texto=_texto("texto"),
        pedido=_texto("pedido"),
        enviada_em=enviada_em,
        uid=uid if isinstance(uid, int) else None,
    )


def _pendentes_em_dia(
    pendentes: Iterable[ConfirmacaoAmazon], agora: datetime
) -> list[ConfirmacaoAmazon]:
    """Sem repetir, só as de até `JANELA_COPIA_PENDENTE`, as mais novas até o teto."""
    por_id: dict[str, ConfirmacaoAmazon] = {}
    for c in pendentes:
        if c.enviada_em is not None and c.enviada_em >= agora - JANELA_COPIA_PENDENTE:
            por_id.setdefault(c.message_id, c)
    em_dia = sorted(por_id.values(), key=lambda c: c.enviada_em or agora)
    return em_dia[-MAX_COPIAS_PENDENTES:]


# ── Gravação ──────────────────────────────────────────────────────────────


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


def _ordem(email_: EmailAmazon) -> tuple:
    """Mais antigo primeiro: o último gravado é o mais novo da conversa."""
    quando = email_.enviada_em or datetime.max.replace(tzinfo=UTC)
    return (quando, email_.uid or 0)


def _dados_do_email(em: EmailAmazon, atuais: dict, *, mais_nova: bool) -> dict:
    """O que um e-mail NOVO do comprador muda em `conversa.dados` (mesclado).

    Só o mais novo manda na thread: a resposta sai "Re: <assunto>" presa ao
    ÚLTIMO e-mail (In-Reply-To), e o "não precisa de resposta" é o do último
    — o link é assinado por mensagem; o de uma anterior dispensaria a
    pergunta errada, então sem link novo o velho vira None. E-mail atrasado
    não volta nada disso. O caso é da conversa: o atrasado só o preenche
    quando ela ainda não tem.
    """
    dados: dict = {}
    if mais_nova:
        dados["assunto"] = em.assunto
        dados["ultimo_message_id"] = em.message_id
        if em.link_sem_resposta:
            dados["amazon_link_sem_resposta"] = em.link_sem_resposta
        elif atuais.get("amazon_link_sem_resposta"):
            dados["amazon_link_sem_resposta"] = None
    if em.caso_id and (mais_nova or not atuais.get("amazon_caso_id")):
        dados["amazon_caso_id"] = em.caso_id
        dados["amazon_link_caso"] = em.link_caso
    return dados


async def _gravar_leitura(session: AsyncSession, leitura: LeituraCaixa) -> ResultadoSync:
    """Grava os e-mails da rodada nas conversas de cada conta. Não commita.

    Troca `leitura.copias_pendentes` pelas cópias que continuam sem conversa
    (quem chama grava essa lista no cursor).
    """
    integracoes = (
        await session.execute(
            select(Integration).where(Integration.platform == IntegrationPlatform.AMAZON)
        )
    ).scalars().all()
    amazon = {i.id: i for i in integracoes}
    canais = (
        await session.execute(select(AtendimentoCanal).where(*_filtro_canais_amazon()))
    ).scalars().all()
    canal_da_conta = {c.integration_id: c for c in canais}

    emails: list[EmailAmazon] = []
    devolucoes: list[DevolucaoAmazon] = []
    confirmacoes: list[ConfirmacaoAmazon] = []
    ignorados = ilegiveis = 0
    for uid, bruto in leitura.mensagens:
        try:
            interpretado = interpretar_email(bruto, uid=uid)
            # A cópia antes da devolução: ela é reconhecida pelo cabeçalho/
            # assunto exatos; a devolução, por palavras no assunto.
            confirmacao = (
                interpretar_confirmacao(bruto, uid=uid) if interpretado is None else None
            )
            devolucao = (
                interpretar_devolucao(bruto, uid=uid)
                if interpretado is None and confirmacao is None
                else None
            )
        except Exception as exc:  # noqa: BLE001 — um e-mail torto não para a caixa
            ilegiveis += 1
            logger.warning(
                "atendimento_amazon_email_ilegivel", uid=uid, erro=type(exc).__name__
            )
            continue
        if confirmacao is not None:
            confirmacoes.append(confirmacao)
            continue
        if devolucao is not None:
            devolucoes.append(devolucao)
            continue
        if interpretado is None:
            ignorados += 1
            try:
                aviso = aviso_da_amazon(bruto)
            except Exception:  # noqa: BLE001 — é só log
                aviso = None
            if aviso is not None:
                logger.info("atendimento_amazon_email_aviso", uid=uid, **aviso)
            continue
        emails.append(interpretado)
    emails.sort(key=_ordem)

    agora = datetime.now(UTC)
    cache_pedidos: dict[str, Integration | None] = {}
    novas: set[UUID] = set()
    atualizadas: set[UUID] = set()
    mensagens_novas = 0
    por_origem: dict[str, int] = {}
    # Reler a caixa (UIDVALIDITY nova, 7 dias de volta) não pode gravar de
    # novo: o e-mail SEM pedido vai para "a conversa mais recente do mesmo
    # endereço", que pode ter mudado desde a 1ª leitura — e a idempotência
    # do `gravar_mensagem` é só dentro da conversa.
    ja_gravados = await _ja_gravados(session, (em.message_id for em in emails))
    repetidos = 0
    for em in emails:
        if em.message_id in ja_gravados:
            repetidos += 1
            continue
        ja_gravados.add(em.message_id)
        # Sem nº do pedido: a thread diz de que conversa o e-mail é.
        anterior = await _conversa_da_thread(session, em, agora) if em.pedido is None else None
        externo_id = anterior.externo_id if anterior is not None else em.conversa_externo_id
        if anterior is not None and anterior.integration_id in amazon:
            integ, achado_por = amazon[anterior.integration_id], "thread"
        else:
            integ, achado_por = await _conta_do_email(
                session, em, amazon, cache_pedidos, externo_id
            )
        canal_da_vez = canal_da_conta.get(integ.id) if integ is not None else None
        if integ is not None:
            await _adotar_sem_conta(session, externo_id, integ, canal_da_vez)
        chave = {
            "canal": canal_da_vez,
            "integration": integ,
            "plataforma": PLATAFORMA,
            "canal_nome": CANAL,
            "externo_id": externo_id,
            # O nome da LOJA (cadastro), não o da integração; sem conta achada,
            # a fila "sem dono".
            "conta": (await lojas.nome_da_loja(session, integ) or None)
            if integ is not None
            else SEM_CONTA,
        }
        conversa, criada = await gravar.upsert_conversa(
            session,
            **chave,
            comprador_id=em.remetente,
            comprador_nome=em.remetente_nome,
            pedido_marketplace=em.pedido,
        )
        anterior = _utc(conversa.ultima_do_cliente_em)
        mais_nova = anterior is None or em.enviada_em is None or em.enviada_em >= anterior
        _mensagem, nova = await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id=em.message_id,
            autor=AUTOR_CLIENTE,
            texto=em.texto or None,
            enviada_em=em.enviada_em,
            tipo="arquivo" if em.anexos and not em.texto else "texto",
            anexos=em.anexos,
            payload={"imap_uid": em.uid, "conta_por": achado_por},
        )
        if not nova:
            continue
        mensagens_novas += 1
        por_origem[achado_por] = por_origem.get(achado_por, 0) + 1
        (novas if criada else atualizadas).add(conversa.id)
        dados = _dados_do_email(em, conversa.dados or {}, mais_nova=mais_nova)
        if dados:
            await gravar.upsert_conversa(session, **chave, dados=dados)

    # Depois dos e-mails de comprador: o aviso de devolução da resposta que
    # acabou de sair pode vir na mesma leva.
    atualizadas |= await _aplicar_devolucoes(session, devolucoes)
    # Por último as cópias: a pergunta que ela responde pode ter chegado
    # nesta mesma leva, e a devolução acima pode ter devolvido a conversa à
    # fila (a pessoa então respondeu pelo Seller Central).
    # As que esperavam a pergunta (cursor) só voltam quando entrou pergunta
    # nova; a cópia desta leva vale mais que a guardada (é a mesma, relida).
    da_leva = {c.message_id for c in confirmacoes}
    guardadas = [c for c in leitura.copias_pendentes if c.message_id not in da_leva]
    tentar_guardadas = mensagens_novas > 0
    a_tentar = confirmacoes + (guardadas if tentar_guardadas else [])
    copias = await _aplicar_confirmacoes(session, a_tentar, amazon)
    mensagens_novas += copias.gravadas
    atualizadas |= copias.mexidas
    leitura.copias_pendentes = _pendentes_em_dia(
        [*copias.pendentes, *([] if tentar_guardadas else guardadas)], agora
    )

    logger.info(
        "atendimento_amazon_email_lido",
        lidos=len(leitura.mensagens),
        de_comprador=len(emails),
        devolucoes=len(devolucoes),
        copias_da_loja=len(confirmacoes),
        copias_pendentes=len(leitura.copias_pendentes),
        repetidos=repetidos,
        ignorados=ignorados,
        ilegiveis=ilegiveis,
        mensagens_novas=mensagens_novas,
        conversas_novas=len(novas),
        conta_por=por_origem,
        restantes=leitura.restantes,
        recomecou=leitura.recomecou,
    )
    return ResultadoSync(
        status="ok",
        conversas_novas=len(novas),
        conversas_atualizadas=len(atualizadas - novas),
        mensagens_novas=mensagens_novas,
    )


def _erro_de_operacao(exc: BaseException) -> str:
    """Erro da caixa para `canal.ultimo_erro`: tipo e resposta do servidor.

    Resposta de IMAP é de operação ("[AUTHENTICATIONFAILED] ..."), não traz
    e-mail de comprador; a senha nunca entra em mensagem de exceção.
    """
    return f"imap {type(exc).__name__}: {' '.join(str(exc).split())[:200]}"


# ── Contrato do adaptador ─────────────────────────────────────────────────


async def sincronizar(
    session: AsyncSession,
    canal: AtendimentoCanal,
    integration: Integration,
    cliente: Any,
) -> ResultadoSync:
    """Lê a caixa IMAP desde o cursor compartilhado (ignora `cliente`). Não commita.

    A caixa é uma para as quatro contas: só quem pega a trava lê, e grava as
    conversas de TODAS as contas nesta sessão; os outros canais devolvem o
    resultado dessa leitura. Erro de rede/IMAP vira `status="erro"`, nunca
    exceção. Sem caixa configurada → `desligado`.
    """
    config = configuracao()
    if not config.pode_ler:
        return ResultadoSync(status="desligado")

    leio, resultado_alheio = await _pegar_trava()
    if not leio:
        return resultado_alheio

    resultado: ResultadoSync | None = None
    try:
        cursor = await _cursor_compartilhado(session)
        try:
            leitura = await asyncio.to_thread(_ler_caixa, config, cursor, LIMITE_POR_RODADA)
        except Exception as exc:  # noqa: BLE001 — rede/IMAP: vira status do canal
            logger.warning(
                "atendimento_amazon_email_falhou",
                canal_id=str(canal.id),
                erro=type(exc).__name__,
            )
            resultado = ResultadoSync(status="erro", erro=_erro_de_operacao(exc))
            return resultado
        leitura.copias_pendentes = [
            c
            for dado in cursor.get("copias_pendentes") or []
            if (c := _copia_do_cursor(dado)) is not None
        ]
        lido = await _gravar_leitura(session, leitura)
        novo_cursor: dict = {"uidvalidity": leitura.uidvalidity, "ultimo_uid": leitura.ultimo_uid}
        if leitura.copias_pendentes:
            novo_cursor["copias_pendentes"] = [
                _copia_para_cursor(c) for c in leitura.copias_pendentes
            ]
        await _gravar_cursor(session, canal, novo_cursor)
        resultado = lido
        return resultado
    finally:
        await _soltar_trava(resultado)


async def enviar_texto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration | None,
    cliente: Any,
    texto: str,
) -> ResultadoEnvio:
    """Responde por SMTP ao endereço de retransmissão. Nunca levanta.

    ok → `externo_id` = o nosso Message-ID. Recusa do servidor (código de
    erro, antes ou no DATA) → ok=False. Queda/timeout depois de começar o
    DATA → `ambiguo=True` (vira `revisar`, nunca se reenvia sozinho).
    """
    config = configuracao()
    if not config.pode_enviar:
        return ResultadoEnvio(ok=False, erro="caixa_nao_configurada")
    para = (conversa.comprador_id or "").strip().lower()
    if not eh_endereco_de_retransmissao(para):
        return ResultadoEnvio(ok=False, erro="sem_endereco_de_retransmissao")

    dados = conversa.dados or {}
    try:
        mensagem = montar_resposta(
            remetente=config.remetente,
            para=para,
            assunto=dados.get("assunto"),
            texto=texto,
            pedido=conversa.pedido_marketplace,
            em_resposta_a=dados.get("ultimo_message_id"),
        )
    except (ValueError, TypeError) as exc:
        return ResultadoEnvio(ok=False, erro=f"email_invalido: {type(exc).__name__}")
    message_id = str(mensagem["Message-ID"])

    try:
        situacao, detalhe = await asyncio.to_thread(_enviar_smtp, config, mensagem)
    except Exception as exc:  # noqa: BLE001 — sem saber a fase, pode ter saído
        situacao, detalhe = ENVIO_AMBIGUO, type(exc).__name__
    payload = {"message_id": message_id, "smtp": detalhe}
    logger.info(
        "atendimento_amazon_email_envio",
        conversa_id=str(conversa.id),
        situacao=situacao,
    )
    if situacao == ENVIO_OK:
        return ResultadoEnvio(ok=True, externo_id=message_id, payload=payload)
    if situacao == ENVIO_AMBIGUO:
        return ResultadoEnvio(ok=False, ambiguo=True, erro=detalhe, payload=payload)
    return ResultadoEnvio(ok=False, erro=detalhe, payload=payload)
