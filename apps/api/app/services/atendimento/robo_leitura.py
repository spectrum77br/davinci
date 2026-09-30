"""O vocabulário comum dos leitores do robô (Temu, AliExpress) — Python puro, sem banco.

O robô do Mac mini (30/09/2026) manda CÓPIAS do que a página do Seller Center
já recebe: respostas de fetch/XHR e quadros de WebSocket, crus, como
`Evento`. Quem entende esse material é o DaVinci, aqui, onde dá para testar:
`robo_temu` e `robo_aliexpress` transformam uma leva de eventos numa
`Leitura` (conversas e mensagens no vocabulário da caixa) e `robo` grava pela
porta única (`gravar`).

TOLERÂNCIA é a regra. Os formatos foram RECONSTRUÍDOS do código das páginas
(as lojas observadas não tinham conversa), então:
  • evento que não se reconhece não derruba a leva: conta em `ignorados`;
  • campo que não se conhece vira CONTAGEM POR NOME (`desconhecidos`), que o
    `robo` põe no log e no canal — nunca o VALOR, que pode ser texto, nome
    ou pedido de comprador;
  • nada aqui guarda o corpo cru nem a URL do evento (a do WebSocket do
    AliExpress leva o token da conexão na query).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.services.atendimento.constantes import AUTOR_CLIENTE, AUTOR_LOJA, AUTOR_SISTEMA

TIPO_TEXTO = "texto"
TIPO_IMAGEM = "imagem"
TIPO_VIDEO = "video"
TIPO_ARQUIVO = "arquivo"
TIPO_PRODUTO = "produto"
TIPO_PEDIDO = "pedido"
TIPO_OUTRO = "outro"

# O que cada evento rendeu. `controle` = reconhecido, mas sem conversa nem
# mensagem (batimento, confirmação de transporte, contador): não é defeito do
# leitor, só não há o que gravar.
USADO = "usado"
CONTROLE = "controle"
IGNORADO = "ignorado"

# Prefixo do id da mensagem que só se conhece pela PRÉVIA da lista de
# conversas (AliExpress: a sessão traz o texto da última mensagem, sem o id
# dela). Quando a mensagem de verdade chega, o `robo` troca o id (adota).
PREFIXO_PREVIA = "previa:"

# Tetos de segurança do garimpo em binário: um quadro esquisito não pode
# prender o worker da API.
_MAX_TENTATIVAS_JSON = 4000
_MAX_DESCOMPRIMIDO = 8 * 1024 * 1024


@dataclass
class Evento:
    """Uma cópia do que a página recebeu (contrato robô → DaVinci)."""

    tipo: str  # http | ws
    url: str = ""
    metodo: str | None = None
    status: int | None = None
    recebido_em: datetime | None = None
    corpo: str = ""


@dataclass
class MensagemLida:
    externo_id: str
    autor: str  # cliente | loja | sistema (constantes.AUTORES)
    tipo: str = TIPO_TEXTO
    texto: str | None = None
    enviada_em: datetime | None = None
    anexos: list = field(default_factory=list)
    # O item cru da plataforma (material de depuração do leitor, como o
    # `payload` dos outros adaptadores). Fica no banco, nunca no log.
    payload: dict = field(default_factory=dict)
    # Resposta automática do ROBÔ DA PLATAFORMA (Temu `context.robot`,
    # AliExpress `im_ai`/`im_open_api`): entra como `sistema`, para não tirar
    # a conversa da fila como se a equipe tivesse respondido.
    robo_plataforma: bool = False
    # Só a prévia da lista de conversas (sem id próprio; ver PREFIXO_PREVIA).
    previa: bool = False


@dataclass
class ConversaLida:
    externo_id: str
    comprador_id: str | None = None
    comprador_nome: str | None = None
    comprador_avatar: str | None = None
    pedido: str | None = None
    nao_lidas: int | None = None
    # Vai para `conversa.dados["robo"]` (grupos, prazo da plataforma, tags):
    # só vocabulário da plataforma, nunca texto de comprador.
    dados: dict = field(default_factory=dict)
    mensagens: dict[str, MensagemLida] = field(default_factory=dict)

    def juntar(self, **campos: Any) -> None:
        """Campo None não apaga o que outro evento da leva já trouxe."""
        for nome, valor in campos.items():
            if valor is not None:
                setattr(self, nome, valor)

    def mensagem(self, m: MensagemLida) -> None:
        """A mesma mensagem em dois eventos (lista + sync) entra uma vez só."""
        atual = self.mensagens.get(m.externo_id)
        if atual is None or (atual.previa and not m.previa):
            self.mensagens[m.externo_id] = m


@dataclass
class Leitura:
    conversas: dict[str, ConversaLida] = field(default_factory=dict)
    usados: int = 0
    controle: int = 0
    ignorados: int = 0
    # Nomes de campo/rota/tipo que o leitor não conhece (nunca valores).
    desconhecidos: Counter = field(default_factory=Counter)
    # O que foi reconhecido, por tipo de evento (para o log da leva).
    reconhecidos: Counter = field(default_factory=Counter)
    # Sinais da LOJA: `precisa_responder` (Temu needReplyCount),
    # `nao_lidas_loja` (AliExpress unreadcount), `erro_plataforma` (só o
    # código: 54001 = captcha na Temu, RGV587/FAIL_SYS_* no AliExpress).
    sinais: dict = field(default_factory=dict)

    def conversa(self, externo_id: str) -> ConversaLida:
        c = self.conversas.get(externo_id)
        if c is None:
            c = self.conversas[externo_id] = ConversaLida(externo_id=externo_id)
        return c

    def contar(self, resultado: str) -> None:
        if resultado == USADO:
            self.usados += 1
        elif resultado == CONTROLE:
            self.controle += 1
        else:
            self.ignorados += 1

    def desconhecido(self, nome: str) -> None:
        # Nome curto e sem espaço: é o que vai para o log.
        self.desconhecidos[re.sub(r"\s+", "_", nome)[:80]] += 1


# ── Valores ───────────────────────────────────────────────────────────────


def texto(valor: Any) -> str | None:
    """Texto aparado; número vira texto (id numérico); o resto é None."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        return str(int(valor)) if float(valor).is_integer() else str(valor)
    if isinstance(valor, str):
        v = valor.strip()
        return v or None
    return None


def inteiro(valor: Any) -> int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str) and re.fullmatch(r"\s*-?\d+\s*", valor):
        return int(valor)
    return None


def quando(valor: Any) -> datetime | None:
    """Carimbo da plataforma → datetime UTC (segundos, milissegundos ou ISO)."""
    n: float | None = None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        n = float(valor)
    elif isinstance(valor, str):
        v = valor.strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", v):
            n = float(v)
        elif v:
            try:
                d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return None
            return d if d.tzinfo else d.replace(tzinfo=UTC)
    if n is None or n <= 0:
        return None
    if n > 1e14:  # microssegundos
        n /= 1e6
    elif n > 1e11:  # milissegundos
        n /= 1e3
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def url_http(valor: Any) -> str | None:
    """Só endereço http(s) vira anexo (a tela põe num <img>/<a>)."""
    v = texto(valor)
    if v and v.lower().startswith(("https://", "http://")) and len(v) <= 4096:
        return v
    return None


def anexo(tipo: str, url: Any, **extra: Any) -> list[dict]:
    """[{"tipo", "url", ...}] — vazio quando não há endereço que sirva."""
    u = url_http(url)
    if u is None:
        return []
    return [{"tipo": tipo, "url": u, **{k: v for k, v in extra.items() if v is not None}}]


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()[:16]


def id_de_previa(
    sessao: str,
    conteudo: str,
    *,
    mensagem_id: str | None = None,
    hora: datetime | None = None,
) -> str:
    """Id estável da prévia: a mesma última mensagem, lida de novo, dá o mesmo id.

    Pelo id da última mensagem, quando a lista traz (a real traz): "Hello"
    de hoje e "Hello" da semana passada são mensagens DIFERENTES, e só o
    texto não separa as duas (revisão 30/09). Sem o id, o hash do texto com
    a hora da mensagem; sem a hora, só o do texto (o `robo` decide, pela
    conversa, se é a mesma). Hash, não o texto: o id aparece em tela de
    depuração e em índice — texto de comprador não.
    """
    if mensagem_id:
        return f"{PREFIXO_PREVIA}{sessao}:{mensagem_id}"
    base = " ".join(conteudo.split())
    if hora is not None:
        base += f"|{int(hora.timestamp() * 1000)}"
    return f"{PREFIXO_PREVIA}{sessao}:{_hash(base)}"


# A pergunta que a plataforma garante que existe (a conversa está "sem
# resposta") mas que o robô não viu: a lista só traz a ÚLTIMA mensagem, e ela
# é a resposta automática do robô da plataforma. Sem o marcador, a conversa
# sairia da fila — a resposta automática é `sistema`, não conta como
# pergunta. O texto é nosso (nunca do comprador).
TEXTO_NAO_VISTA = "[Mensagem do comprador que o robô não viu: leia no Seller Center]"


def marcador_nao_vista(
    conversa_id: str, *, hora: datetime | None, referencia: str, plataforma: str
) -> MensagemLida:
    """O marcador da pergunta não vista (prévia do comprador, sem texto dele).

    `referencia` (a hora da pergunta ou o id da resposta automática) deixa o
    id estável: a lista relida dá o mesmo marcador. Quando a pergunta de
    verdade chega (sync/empurrão), o `robo` ADOTA o marcador.
    """
    return MensagemLida(
        externo_id=f"{PREFIXO_PREVIA}{conversa_id}:naovista:{_hash(referencia)}",
        autor=AUTOR_CLIENTE,
        tipo=TIPO_OUTRO,
        texto=TEXTO_NAO_VISTA,
        enviada_em=hora,
        payload={"fonte": "robo", "plataforma": plataforma, "previa": True, "nao_vista": True},
        previa=True,
    )


def caminho(url: str) -> str:
    """Só o caminho da URL, em minúsculas (sem query: pode ter token)."""
    try:
        return (urlsplit(url or "").path or "").lower().rstrip("/")
    except ValueError:
        return ""


def caminho_para_log(url: str) -> str:
    """O caminho com número trocado por '#' e no máximo 4 partes: vai para o log."""
    partes = [re.sub(r"\d+", "#", p)[:40] for p in caminho(url).split("/") if p][:4]
    return "/" + "/".join(partes)


# ── JSON e binário ────────────────────────────────────────────────────────

_JSONP = re.compile(r"^\s*[\w$.]+\s*\(\s*(?P<corpo>.*)\s*\)\s*;?\s*$", re.S)


def como_json(corpo: Any) -> Any | None:
    """O corpo como JSON (aceita BOM e JSONP `callback({...})`); None se não for."""
    if isinstance(corpo, dict | list):
        return corpo
    if not isinstance(corpo, str):
        return None
    t = corpo.lstrip("﻿").strip()
    if not t:
        return None
    if t[0] in "{[":
        try:
            return json.loads(t)
        except ValueError:
            return None
    m = _JSONP.match(t)
    if m and m.group("corpo").lstrip()[:1] in ("{", "["):
        try:
            return json.loads(m.group("corpo"))
        except ValueError:
            return None
    return None


def json_ou_valor(valor: Any) -> Any:
    """Campo que a plataforma manda como TEXTO com JSON dentro (templateData, bizData)."""
    if isinstance(valor, str):
        t = valor.strip()
        if t[:1] in ("{", "["):
            try:
                return json.loads(t)
            except ValueError:
                return valor
    return valor


def dicionario(valor: Any) -> dict:
    v = json_ou_valor(valor)
    return v if isinstance(v, dict) else {}


_BASE64 = re.compile(r"^[A-Za-z0-9+/_-]+={0,2}$")


def bytes_do_corpo(corpo: str) -> bytes | None:
    """Quadro binário que o robô mandou em base64 (com ou sem prefixo)."""
    if not isinstance(corpo, str):
        return None
    t = corpo.strip()
    for prefixo in ("base64:", "b64:"):
        if t.lower().startswith(prefixo):
            t = t[len(prefixo) :]
            break
    if t.lower().startswith("data:") and ";base64," in t[:100].lower():
        t = t.split(",", 1)[1]
    t = re.sub(r"\s+", "", t)
    if len(t) < 8 or not _BASE64.match(t):
        return None
    t += "=" * (-len(t) % 4)
    try:
        if "-" in t or "_" in t:
            return base64.urlsafe_b64decode(t)
        return base64.b64decode(t)
    except (binascii.Error, ValueError):
        return None


def descomprimir(dados: bytes) -> bytes | None:
    """gzip/zlib inteiro → bytes; None se não for (ou passar do teto)."""
    for janela in (16 + zlib.MAX_WBITS, zlib.MAX_WBITS):
        try:
            d = zlib.decompressobj(janela)
            saida = d.decompress(dados, _MAX_DESCOMPRIMIDO)
        except zlib.error:
            continue
        if saida:
            return saida
    return None


def jsons_embutidos(dados: bytes, *, marcas: tuple[bytes, ...]) -> list[Any]:
    """Os objetos JSON dentro de um quadro binário (protobuf com JSON no meio).

    O quadro da Titan (Temu) é cabeçalho de 16 bytes + protobuf, às vezes com
    gzip; o conteúdo do chat é um JSON UTF-8 guardado como bytes num campo.
    Sem o .proto, garimpa: descomprime cada trecho gzip que achar e tenta ler
    um objeto a partir de cada '{' dos blocos que têm alguma `marca` (o nome
    de uma chave que só o JSON do chat tem). Tetos de tentativa e de tamanho.
    """
    blocos = [dados]
    for m in re.finditer(rb"\x1f\x8b\x08", dados):
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            saida = d.decompress(dados[m.start() :], _MAX_DESCOMPRIMIDO)
        except zlib.error:
            continue
        if saida:
            blocos.append(saida)
    achados: list[Any] = []
    decodificador = json.JSONDecoder()
    tentativas = 0
    for bloco in blocos:
        if not any(marca in bloco for marca in marcas):
            continue
        texto_bloco = bloco.decode("utf-8", errors="replace")
        pos = texto_bloco.find("{")
        while pos != -1 and tentativas < _MAX_TENTATIVAS_JSON:
            tentativas += 1
            try:
                obj, fim = decodificador.raw_decode(texto_bloco, pos)
            except ValueError:
                pos = texto_bloco.find("{", pos + 1)
                continue
            if isinstance(obj, dict):
                achados.append(obj)
            pos = texto_bloco.find("{", fim)
    return achados


def campos_desconhecidos(
    leitura: Leitura, obj: Any, conhecidos: frozenset[str], prefixo: str
) -> None:
    """Conta as CHAVES que o leitor não conhece (o nome, nunca o valor)."""
    if not isinstance(obj, dict):
        return
    for chave in obj:
        if isinstance(chave, str) and chave not in conhecidos:
            leitura.desconhecido(f"{prefixo}.{chave}")


def listas_na_chave(obj: Any, chave: str, *, profundidade: int = 6) -> list[list]:
    """Todas as listas guardadas sob `chave` em qualquer nível (até a profundidade)."""
    achadas: list[list] = []
    if profundidade < 0:
        return achadas
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == chave and isinstance(v, list):
                achadas.append(v)
            elif isinstance(v, dict | list):
                achadas += listas_na_chave(v, chave, profundidade=profundidade - 1)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict | list):
                achadas += listas_na_chave(v, chave, profundidade=profundidade - 1)
    return achadas


__all__ = [
    "AUTOR_CLIENTE",
    "AUTOR_LOJA",
    "AUTOR_SISTEMA",
    "CONTROLE",
    "IGNORADO",
    "PREFIXO_PREVIA",
    "USADO",
    "ConversaLida",
    "Evento",
    "Leitura",
    "MensagemLida",
]
