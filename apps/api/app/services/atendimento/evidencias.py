"""Confere condições comerciais que a IA não pode confirmar por conta própria.

Funções puras: somente os FATOS já escolhidos para esta conversa são prova.
Comprador, exemplos, motivo/confiança do modelo e ficha de outro anúncio não
são prova. O Manual exige título para original/novo/lacrado e para a versão
de produto Apple. Nos demais produtos, a versão pode vir também dos
atributos e da descrição do anúncio da própria conversa.

É uma guarda de vocabulário fechado, não um verificador geral de produto.
Uma negação ("não é original") também afirma uma condição e exige prova.
Pergunta, hipótese e desconhecimento não são confirmação de condição.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.atendimento.validador import plano_de


@dataclass(frozen=True)
class Pendencia:
    codigo: str
    motivo: str


_TERMOS = {
    "original": re.compile(r"\borigina(?:l|is)\b"),
    "novo": re.compile(r"\bnov[oa]s?\b"),
    "lacrado": re.compile(r"\blacrad[oa]s?\b"),
    "versao_americana": re.compile(
        r"\b(?:american[oa]s?|estadunidenses?|norte[- ]american[oa]s?)\b"
        r"|\b(?:versao|modelo|variante|regiao)\s*(?::|=|dos?|de)?\s*"
        r"(?:eua|estados unidos|usa)\b"
    ),
}
_ROTULOS = {
    "original": "originalidade",
    "novo": "condição de novo",
    "lacrado": "condição de lacrado",
    "versao_americana": "versão americana",
}
# As marcas são do código que escolhe a ficha, não do texto do comprador.
_DE_ONDE = frozenset(
    {
        "cartao de produto que o cliente mandou",
        "anuncio ligado a conversa",
        "anuncio da pergunta",
        "item do pedido da conversa",
    }
)
_PARTES = re.compile(
    r"[,;.!?\n]+|\b(?:mas|porem|contudo|entretanto)\b"
    r"|\be\s+(?=(?:e|sao|vem|esta|estao)\s+)"
    r"|\be\s+(?=(?:o|a|os|as|ele|ela|eles|elas)\s+.{0,30}\b(?:e|sao|vem|esta|estao)\b)"
)
_SEM_CONFIRMACAO = re.compile(
    r"\bnao\s+(?:posso|podemos|consigo|conseguimos)\s+"
    r"(?:confirmar|garantir|afirmar|informar|assegurar)\b"
    r"|\bnao\s+(?:sei|sabemos|sabe|sabem|confirmamos|afirmamos|garantimos|informamos)\b"
    r"|\b(?:sem|nao\s+(?:ha|temos|tenho|tem|existe))\s+"
    r"(?:a\s+)?(?:confirmacao|certeza|informacao|informacoes|dados?|evidencia|"
    r"indicacao|comprovacao|registro)\b"
    r"|\bnao\s+(?:esta|foi|e)\s+(?:confirmad[oa]|informad[oa]|verificad[oa])\b"
    r"|\b(?:confirmar|verificar|conferir|confirmamos|verificamos|confirma)\s+"
    r"(?:ainda\s+)?(?:se|como|que\s+seja)\b"
    r"|\b(?:duvida|pergunta)\s+(?:e\s+)?(?:sobre|se|quanto\s+a)\b"
    r"|\b(?:quer|deseja|gostaria\s+de)\s+saber\s+se\b"
    r"|\b(?:pergunte|perguntou|perguntar)\b.{0,60}\bse\b"
    r"|\bse\s+(?:o\s+\w+\s+)?(?:for|forem|fosse|vier|estiver|estiverem)\b"
)
_SEM_CONFIRMACAO_APOS = re.compile(
    r"^[ '\"”’]*\s+(?:ainda\s+)?nao\s+(?:esta|foi|e)\s+"
    r"(?:confirmad[oa]|informad[oa]|comprovad[oa]|verificad[oa])\b"
)
_NEGACAO = re.compile(r"\b(?:nao|nem|nunca|sem)\b")
_NAO_APENAS = re.compile(r"\bnao\s+(?:(?:e|sao)\s+)?(?:so|apenas|somente)\b")
_SIM = re.compile(r"\b(?:sim|confirmamos|afirmamos|garantimos)\b")
_CONFIRMACAO_CURTA = re.compile(
    r"^(?:com certeza|claro(?: que (?:sim|e))?|certamente|sem (?:sombra de )?duvida|"
    r"pode(?:m)? confiar|pode(?:m)? (?:ter certeza|ficar tranquil[oa]s?)|"
    r"positivo|isso mesmo|exatamente|correto|e sim|100\s*%)\s*(?:[!.,;]|$)"
)
_NEGACAO_CURTA = re.compile(r"^(?:nao|negativo|de forma alguma|de jeito nenhum)\s*(?:[!.,;]|$)")
_NOVO_CONTEXTO = re.compile(
    r"^\s+(?:duvida|mensagem|pergunta|compra|conversa|contato|pedido|envio|"
    r"rastreio|prazo|tentativa|atualizacao|anuncio|oferta|promocao|desconto)s?\b"
)
_TEXTO_ORIGINAL = re.compile(r"\b(?:mensagem|texto|pergunta|resposta|anuncio)\s+$")
_ACESSORIO = re.compile(
    r"\b(?:tomada|fonte|carregador|cabo|plugue|adaptador|pulseira|embalagem|"
    r"garantia|marca|empresa|loja|fabricante|lingua)\s+$"
)
_APPLE = re.compile(
    r"\b(?:apple|iphone|ipad|airpods|macbook|imac)\b|\bwatch\s+(?:series|ultra|se)\b"
)


def _plano(texto: str) -> str:
    return " ".join(plano_de(texto).split())


def _continuacao_de_lista(trecho: str) -> bool:
    """Uma lista sem sujeito/verbo novo conserva a ressalva anterior à vírgula."""
    trecho = trecho.strip(" \"'“”‘’")
    if re.match(r"^(?:e|o|a|os|as|ele|ela|eles|elas)\s+", trecho):
        return False
    for expressao in _TERMOS.values():
        trecho = expressao.sub("", trecho)
    trecho = re.sub(r"\b(?:ou|nem|e|versao|da|do|de|na|no)\b", "", trecho)
    return not trecho.strip()


def _afirmacoes(texto: str) -> set[tuple[str, bool]]:
    """Condição e polaridade (True = negativa), sem transformar perguntas em fatos."""
    texto = _plano(texto)
    saida: set[tuple[str, bool]] = set()
    limites = list(_PARTES.finditer(texto))
    inicio = 0
    ressalva_anterior = False
    negativa_anterior = False
    for separador in [*limites, None]:
        fim = separador.start() if separador else len(texto)
        trecho = texto[inicio:fim]
        terminador = re.search(r"[.!?]", texto[inicio:])
        pergunta = terminador is not None and terminador.group() == "?"
        depois = texto[inicio + terminador.end() :] if terminador else ""
        # Uma pergunta retórica seguida de confirmação/negação também
        # afirma uma condição: "É original? Pode confiar!" / "Não.".
        resposta_curta = depois.lstrip(" \"'”’")
        resposta_afirmativa = pergunta and bool(
            _SIM.match(resposta_curta) or _CONFIRMACAO_CURTA.match(resposta_curta)
        )
        resposta_negativa = pergunta and bool(_NEGACAO_CURTA.match(resposta_curta))
        continuacao = _continuacao_de_lista(trecho)
        ressalva = bool(_SEM_CONFIRMACAO.search(trecho)) or (ressalva_anterior and continuacao)
        negativas = False
        for campo, expressao in _TERMOS.items():
            for termo in expressao.finditer(trecho):
                antes, apos = trecho[: termo.start()], trecho[termo.end() :]
                if pergunta and not (resposta_afirmativa or resposta_negativa):
                    continue
                if (
                    _SEM_CONFIRMACAO.search(antes)
                    or _SEM_CONFIRMACAO_APOS.search(apos)
                    or (ressalva_anterior and continuacao)
                ):
                    continue
                if campo == "novo" and (
                    re.search(r"\bde\s+$", antes) or _NOVO_CONTEXTO.match(apos)
                ):
                    continue
                if campo == "original" and _TEXTO_ORIGINAL.search(antes):
                    continue
                if campo == "versao_americana" and _ACESSORIO.search(antes):
                    continue
                antes = _NAO_APENAS.sub("", antes)
                # O título/atributo também pode usar "Original: não".
                depois_nao = bool(re.match(r"\s*[:=]\s*nao\b", apos))
                negativa = (
                    bool(_NEGACAO.search(antes))
                    or depois_nao
                    or resposta_negativa
                    or (negativa_anterior and continuacao)
                )
                negativas |= negativa
                saida.add((campo, negativa))
        virgula = separador is not None and separador.group() == ","
        ressalva_anterior = virgula and ressalva
        negativa_anterior = virgula and negativas
        inicio = separador.end() if separador else len(texto)
    return saida


def _textos_autorizados(fatos: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Títulos próprios e ficha principal; outras chaves nunca entram como prova."""
    titulos: list[str] = []
    ficha: Mapping[str, Any] | None = None
    anuncio = fatos.get("anuncio")
    if isinstance(anuncio, str) and anuncio.strip():
        titulos.append(anuncio)
    fichas = fatos.get("ficha_do_anuncio")
    if isinstance(fichas, list) and fichas and isinstance(fichas[0], Mapping):
        principal = fichas[0]
        de_onde = _plano(str(principal.get("de_onde") or ""))
        fonte = _plano(str(principal.get("fonte") or ""))
        if de_onde in _DE_ONDE and "outro anuncio" not in fonte:
            ficha = principal
            if isinstance(ficha.get("titulo"), str):
                titulos.append(ficha["titulo"])
    textos = list(titulos)
    if ficha is not None:
        descricao = ficha.get("descricao")
        if isinstance(descricao, str):
            textos.append(descricao)
        atributos = ficha.get("atributos")
        if isinstance(atributos, list):
            textos.extend(a for a in atributos if isinstance(a, str))
    return titulos, textos


def conferir_afirmacoes(resposta: str, fatos: Mapping[str, Any]) -> tuple[Pendencia, ...]:
    """Pendências de prova: não reprova pergunta neutra e não libera negação inventada.

    As duas polaridades na fonte são divergência, não autorização para
    escolher uma. A segunda ficha pode ser outro produto/kit e não serve
    para comprovar a condição da ficha principal.
    """
    if not isinstance(fatos, Mapping):
        fatos = {}
    titulos, textos = _textos_autorizados(fatos)
    apple = any(_APPLE.search(_plano(t)) for t in textos)
    prova_titulo = set().union(*(_afirmacoes(t) for t in titulos))
    prova_ficha = set().union(*(_afirmacoes(t) for t in textos))
    pendencias = []
    for campo, negativa in sorted(_afirmacoes(resposta)):
        usa_ficha = campo == "versao_americana" and not apple
        prova = prova_ficha if usa_ficha else prova_titulo
        if (campo, negativa) in prova and (campo, not negativa) not in prova:
            continue
        fonte = "nos fatos autorizados do anúncio" if usa_ficha else "no título do anúncio"
        pendencias.append(
            Pendencia(
                codigo=f"produto_{campo}_sem_evidencia",
                motivo=f"{_ROTULOS[campo]} sem confirmação {fonte}",
            )
        )
    return tuple(pendencias)


def motivos_sem_evidencia(resposta: str, fatos: Mapping[str, Any]) -> list[str]:
    """Forma curta para acrescentar aos motivos de `precisa_humano` da IA."""
    return [p.motivo for p in conferir_afirmacoes(resposta, fatos)]
