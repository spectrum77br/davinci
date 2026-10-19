"""Resposta literal de um atributo próprio e fresco; sem inferência do modelo.

Opt-in pelo texto exato do Manual vigente. Qualquer ambiguidade mantém o
fluxo que encaminha a ficha para pessoa. Não cobre condição, versão,
garantia, resistência, estoque, acessórios nem atributo de outra variação.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.services.atendimento.politicas_atendimento import REGRA_PULSEIRA
from app.services.atendimento.validador import plano_de

REGRAS_APROVADAS = {
    "espec_celular": (
        "Responder com o dado do título ou da ficha do anúncio nos FATOS (atributos e descrição) "
        "ou com estes fatos aprovados; sem dado, ou com título e ficha divergindo, "
        "precisa_humano=true. Fatos aprovados: o celular é desbloqueado, não vem preso a "
        "operadora; quando o anúncio soma RAM física e virtual, separar as duas (a virtual usa "
        "parte do armazenamento); Wi-Fi de 5 GHz não é rede móvel 5G; o Uranyx S5 tem NFC. "
        "Recurso que o modelo não tem: dizer só que este modelo não tem; indicar outro anúncio: "
        "precisa_humano=true."
    ),
    "espec_mala": (
        "Responder com o dado do título ou da ficha do anúncio nos FATOS ou com o fato aprovado: "
        "os códigos de modelo com letra M e número são nomes de design (formato e cor), não de "
        "tamanho. Medidas, quilos, bordo, material, rodas, TSA e expansão sem dado no título nem "
        "na ficha, ou com os dois divergindo: precisa_humano=true. Sobre bordo, lembrar que a "
        "regra final é da companhia aérea."
    ),
    "espec_eletro": (
        "Fato aprovado: a air fryer Uranyx não é bivolt; há versões 110 V e 220 V separadas, "
        "escolhidas na variação antes de finalizar. Outros eletroportáteis e demais dados "
        "(potência, temperatura, material, papel ou silicone): só com o título ou a ficha do "
        "anúncio nos FATOS; Inmetro só com o título; sem dado ou divergindo, precisa_humano=true. "
        "Não comparar com outra marca."
    ),
    "espec_outros": REGRA_PULSEIRA["faca"],
}
_ORIGENS = {
    "cartao de produto que o cliente mandou",
    "anuncio ligado a conversa",
    "anuncio da pergunta",
    "item do pedido da conversa",
}
_PLATAFORMAS = {"shopee": "Shopee", "ml": "Mercado Livre", "tiktok": "TikTok Shop"}
_SUFIXO = (
    r"(?:\s+(?:do|da|desse|dessa|deste|desta|o|a|esse|essa|este|esta))?"
    r"(?:\s+(?:produto|mala|celular|aparelho|relogio|notebook|tablet|air\s*fryer))?"
)
_PERGUNTAS = {
    "peso": re.compile(r"(?:(?:qual\s+(?:e\s+)?o\s+peso)|(?:quanto\s+pesa))" + _SUFIXO),
    "dimensoes": re.compile(r"(?:quais\s+(?:sao\s+)?(?:as\s+)?(?:dimensoes|medidas))" + _SUFIXO),
    "idioma": re.compile(
        r"(?:qual\s+(?:e\s+)?o\s+idioma|quais\s+(?:sao\s+)?(?:os\s+)?idiomas)" + _SUFIXO
    ),
}
_ATRIBUTOS = {
    "peso": {"peso", "peso do produto", "peso liquido", "product weight", "net weight"},
    "dimensoes": {"dimensoes", "dimensoes do produto", "medidas", "product dimensions"},
    "idioma": {"idioma", "idiomas", "linguagem", "language", "languages"},
}
_ROTULOS = {"peso": "peso do produto", "dimensoes": "dimensões do produto", "idioma": "idiomas"}
_PESO = re.compile(r"\d+(?:[.,]\d+)?\s*(?:kg|g)")
_DIMENSOES = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?(?:\s*(?:x|×)\s*\d+(?:[.,]\d+)?)?\s*(?:cm|mm|m)"
)
_IDIOMAS = {
    "portugues",
    "ingles",
    "espanhol",
    "frances",
    "alemao",
    "italiano",
    "japones",
    "chines",
    "coreano",
    "arabe",
}


def _plano(texto: Any) -> str:
    return " ".join(plano_de(str(texto or "")).split())


def _campo(regra: Any, nome: str, padrao: Any = None) -> Any:
    return regra.get(nome, padrao) if isinstance(regra, Mapping) else getattr(regra, nome, padrao)


def _autorizada(categoria: str, regras: list[Any]) -> bool:
    aprovadas = [
        r
        for r in regras
        if _campo(r, "tipo") == "categoria"
        and _campo(r, "categoria") == categoria
        and _campo(r, "ativa", True) is not False
    ]
    return (
        len(aprovadas) == 1
        and all(_campo(aprovadas[0], k) is None for k in ("plataforma", "canal"))
        and _campo(aprovadas[0], "faca") == REGRAS_APROVADAS.get(categoria)
    )


def _pergunta(textos: list[str]) -> str | None:
    if not textos or any(not isinstance(t, str) for t in textos):
        return None
    pergunta = _plano(" ".join(textos))
    if pergunta.count("?") > 1 or re.search(
        r"[\d/]|\b(?:variacao|versao|kit|combo|interna|externa|embalagem|grande|pequena|media)\b",
        pergunta,
    ):
        return None
    pergunta = re.sub(r"^(?:(?:ola|oi|bom dia|boa tarde|boa noite)[!,.: ]*)+", "", pergunta)
    pergunta = re.sub(r"[?!. ,]+$", "", pergunta)
    return next((k for k, regex in _PERGUNTAS.items() if regex.fullmatch(pergunta)), None)


def _valor(ficha: Mapping[str, Any], campo: str) -> str | None:
    valores = []
    for atributo in ficha.get("atributos") or []:
        if not isinstance(atributo, str) or ":" not in atributo:
            continue
        chave, valor = atributo.split(":", 1)
        if _plano(chave) in _ATRIBUTOS[campo]:
            valores.append(valor.strip())
    if not valores or len({_plano(v) for v in valores}) != 1:
        return None
    valor = valores[0]
    plano = _plano(valor)
    if len(valor) > 80 or re.search(
        r"\b(?:nao|sem|padrao|varia|consulte|indisponivel|aplicavel|outro)\b", plano
    ):
        return None
    if campo == "peso" and not _PESO.fullmatch(plano):
        return None
    if campo == "dimensoes" and not _DIMENSOES.fullmatch(plano):
        return None
    if campo in ("peso", "dimensoes") and any(
        float(n.replace(",", ".")) <= 0 for n in re.findall(r"\d+(?:[.,]\d+)?", plano)
    ):
        return None
    if campo == "idioma":
        tokens = set(re.findall(r"[a-z]+", plano)) - {"e"}
        idioma = "(?:" + "|".join(sorted(_IDIOMAS)) + ")"
        if (
            not tokens
            or not tokens.issubset(_IDIOMAS)
            or not re.fullmatch(idioma + r"(?:\s*(?:[,/;]|\be\b)\s*" + idioma + ")*", plano)
        ):
            return None
    return valor


def _contradicao(ficha: Mapping[str, Any], campo: str, valor: str) -> bool:
    texto = _plano(f"{ficha.get('titulo') or ''} {ficha.get('descricao') or ''}")
    termos = {"peso": r"peso|pesa", "dimensoes": r"dimensoes|medidas", "idioma": r"idioma\w*"}[
        campo
    ]
    valor_regex = re.escape(_plano(valor)).replace(r"\ ", r"\s*")
    if campo == "idioma":
        valor_regex = "|".join(sorted(_IDIOMAS))
    if re.search(r"\b(?:nao|sem|nunca)\b.{0,60}(?:" + termos + "|" + valor_regex + ")", texto):
        return True
    if campo in ("peso", "dimensoes"):
        regex = _PESO if campo == "peso" else _DIMENSOES
        # Sem converter unidades, nem deduzir que a medida é de outra peça.
        encontrados = {_plano(m.group()) for m in regex.finditer(texto)}
        return any(x != _plano(valor) for x in encontrados)
    idiomas = set(re.findall(r"[a-z]+", texto)) & _IDIOMAS
    return bool(idiomas - set(re.findall(r"[a-z]+", _plano(valor))))


def resposta_literal(
    categoria: str | None, textos: list[str], fatos: dict, regras: list[Any]
) -> str | None:
    if categoria not in REGRAS_APROVADAS or not _autorizada(categoria, regras):
        return None
    campo = _pergunta(textos)
    fichas = fatos.get("ficha_do_anuncio")
    if (
        campo is None
        or not isinstance(fichas, list)
        or len(fichas) != 1
        or not isinstance(fichas[0], Mapping)
    ):
        return None
    ficha = fichas[0]
    titulo = _plano(ficha.get("titulo"))
    plataforma = _PLATAFORMAS.get(fatos.get("plataforma"))
    if (
        plataforma is None
        or _plano(ficha.get("de_onde")) not in _ORIGENS
        or _plano(ficha.get("fonte")) != _plano(f"loja ({plataforma}), agora")
    ):
        return None
    if not titulo or re.search(r"\bou\b|[/]|\b(?:kit|combo|variacoes|tamanhos)\b", titulo):
        return None
    anuncio = fatos.get("anuncio")
    if anuncio and _plano(anuncio) != titulo:
        return None
    valor = _valor(ficha, campo)
    if valor is None or _contradicao(ficha, campo, valor):
        return None
    return f"Conforme a ficha deste anúncio, {_ROTULOS[campo]}: {valor}."
