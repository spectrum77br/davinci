"""Respostas aprovadas para consultas simples, sem rede, banco ou modelo.

O Manual é a autorização (19/10/2026): uma única regra geral com o FAÇA
exatamente igual à constante. Edição ou especialização devolve a decisão
ao fluxo normal da IA. O chamador conserva classificação, fatos, motivos,
validador e todas as travas de envio.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

REGRA_ESTOQUE = (
    "Consulta simples de disponibilidade de cor, memória, tamanho ou voltagem: orientar "
    "conferir as opções disponíveis para seleção no próprio anúncio, sem afirmar que há "
    "ou que falta estoque de uma variação específica. Só essa consulta pode sair sozinha. "
    "Quantidade de unidades, reserva, limite por pedido, data de reposição ou quando volta: "
    "precisa_humano=true. Nunca prometer estoque, reserva, prazo de reposição, últimas "
    "unidades ou aviso de chegada."
)
REGRA_CONTATO = (
    "Instagram, site da loja ou pedido de contato externo: informar apenas que atendimento "
    "e pagamentos devem permanecer na própria plataforma indicada nos FATOS. Não afirmar "
    "que a loja tem ou não tem perfil ou site, não fornecer links, nomes de usuário, "
    "telefone, e-mail, Pix ou qualquer contato externo. Pedido só de contato pode sair "
    "sozinho com essa orientação; qualquer outra dúvida deve seguir as demais regras do Manual. "
    "Não é preciso mandar dados pessoais; pedir a dúvida. Vídeo que não sobe: pedir trecho "
    "curto ou fotos."
)

RESPOSTA_ESTOQUE = (
    "Olá! As opções disponíveis para compra aparecem para seleção no próprio anúncio. "
    "Confira ali a cor, o tamanho ou a versão desejada."
)

_PLATAFORMAS = {
    "shopee": "pela Shopee",
    "ml": "pelo Mercado Livre",
    "tiktok": "pelo TikTok Shop",
    "amazon": "pela Amazon",
    "magalu": "pelo Magalu",
    "carrefour": "pelo Carrefour",
    "temu": "pela Temu",
    "aliexpress": "pelo AliExpress",
}

# Perguntas mistas continuam no fluxo normal, mesmo que a classificação
# principal seja estoque/contato. Não se interpreta o pedido sensível aqui.
_OUTRA_DEMANDA = re.compile(
    r"\b(?:"
    r"preco\w*|valor\w*|cust\w*|descont\w*|cupom\w*|frete\w*|orcament\w*|"
    r"entreg\w*|envi\w*|rastre\w*|transportador\w*|correio\w*|"
    r"compr(?:ei|ado|ada|amos)|receb\w*|pedido\w*|nota\s+fiscal|"
    r"troca\w*|troc\w*|devol\w*|cancel\w*|reembols\w*|estorn\w*|"
    r"defeit\w*|queb\w*|problema\w*|falt\w*|garant\w*|"
    r"procon|justic\w*|advogad\w*|policia|reclam\w*|indeniz\w*|golpe\w*|"
    r"deposit\w*|pix|boleto\w*|cartao\w*|banco\w*|parcel\w*|"
    r"material\w*|medida\w*|dimens\w*|peso|pesad\w*|polegad\w*|"
    r"funcion\w*|compat\w*|serv(?:e|em|ir)|suport\w*|resist\w*|"
    r"original\w*|lacrad\w*|acompanha\w*|inclu\w*|bivolt|"
    r"nfc|gps|bluetooth|agua|fabric\w*|homolog\w*|anatel|"
    r"ignor\w*|instruc\w*|prompt\w*|sistema|developer|assistant|"
    r"finj\w*|responda|escreva|execute|traduza|revele|retorne|aja|senha\w*|login|"
    r"cpf|cnpj|documento\w*"
    r")\b|\b(?:voce\s+e|nao\s+liga|parou\s+de|quem\s+faz)\b|R\$",
    re.IGNORECASE,
)
_RESERVA_OU_REPOSICAO = re.compile(
    r"\b(?:reserv\w*|separ\w*|guard\w*|segur\w*|repor|repos\w*|"
    r"quando|volt\w*|cheg\w*|previs\w*|data\w*|prazo\w*|amanha|hoje|"
    r"avis\w*|limite\w*|quant\w*|saldo|total|lote\w*|atacado|"
    r"unidade\w*|un|und|uni|peca\w*|pares|duas|dois|tres|quatro|cinco|seis|sete|"
    r"oito|nove|dez)\b|"
    r"\b(?:tem|teria|possuem|possui|ha|preciso|quero)\s+\d+\s*(?:[?!.,]|$)",
)
_DISPONIBILIDADE = re.compile(
    r"\b(?:tem|teria|possuem|possui|ha|existe|vendem|vende|disponivel|disponiveis|"
    r"disponibilidade|estoque)\b"
)
_CONTATO = re.compile(
    r"\b(?:instagram|insta|site|whatsapp|whats|zap|telefone|email|e[-\s]*mail|contato)\b"
)
_OUTRA_DEMANDA_CONTATO = re.compile(
    r"\b(?:estoque|disponivel|disponiveis|tamanho\w*|cor|cores|memoria|voltagem|"
    r"modelo\w*|endereco|fisic\w*)\b|\b\d+\s*(?:gb|tb|v|cm|kg)\b|"
    r"\b(?:p\s*/\s*m|m\s*/\s*g)\b"
)
_OUTRA_DEMANDA_ESTOQUE = re.compile(
    r"\b(?:instagram|insta|site|whatsapp|whats|zap|telefone|email|contato|"
    r"link\w*|foto\w*|video\w*|vem|como|onde|por\s+que|junt[oa]s|combinar|carrinho)\b|"
    r"\b\d+\s*(?:cm|kg|centimetro\w*|metro\w*)\b"
)
_CLAUSULAS = re.compile(
    r"[?!;\n]+|(?<!\d)\.(?!\d)|"
    r"\b(?:e|mas|tambem)\s+(?=(?:qual|quais|quem|quanto|como|onde|por\s+que|ele|ela)\b)"
)
_CORTESIA = re.compile(
    r"^(?:(?:oi|ola|bom\s+dia|boa\s+tarde|boa\s+noite|tudo\s+bem|"
    r"por\s+favor|obrigad[oa])[,\s]*)+$"
)
_NUMERO = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*")


def _so_consulta(pergunta: str, assunto: re.Pattern) -> bool:
    """Uma segunda pergunta de outro assunto não pode ser perdida pelo texto fixo."""
    partes = [p.strip(" ,:") for p in _CLAUSULAS.split(pergunta) if p.strip(" ,:")]
    return bool(partes) and all(_CORTESIA.fullmatch(p) or assunto.search(p) for p in partes)


def _numero_de_quantidade(pergunta: str) -> bool:
    for numero in _NUMERO.finditer(pergunta):
        antes, depois = pergunta[: numero.start()], pergunta[numero.end() :]
        if re.match(r"\s*(?:gb|tb|v)\b", depois):
            continue
        if re.search(r"\b(?:tamanho|tam|numero|numeracao)\s*$", antes):
            continue
        return True
    return False


def _campo(regra: Any, nome: str, padrao: Any = None) -> Any:
    return regra.get(nome, padrao) if isinstance(regra, Mapping) else getattr(regra, nome, padrao)


def _autorizada(categoria: str, regras: list[Any], texto: str) -> bool:
    da_categoria = [
        r
        for r in regras
        if _campo(r, "tipo") == "categoria"
        and _campo(r, "categoria") == categoria
        and _campo(r, "ativa", True) is not False
    ]
    if len(da_categoria) != 1:
        return False
    regra = da_categoria[0]
    return (
        _campo(regra, "plataforma") is None
        and _campo(regra, "canal") is None
        and _campo(regra, "faca") == texto
    )


def _plano(textos: list[str]) -> str:
    return " ".join(
        "".join(
            c
            for c in unicodedata.normalize("NFKD", " ".join(textos))
            if not unicodedata.combining(c)
        )
        .lower()
        .split()
    )


def resposta_aprovada(
    categoria: str | None, textos: list[str], fatos: dict, regras: list[Any]
) -> str | None:
    """Texto aprovado para uma consulta simples; None mantém o caminho normal da IA."""
    if categoria not in ("estoque_variacao", "canal_externo_dados"):
        return None
    if not textos or not all(isinstance(t, str) for t in textos):
        return None
    pergunta = _plano(textos)
    if not pergunta or _OUTRA_DEMANDA.search(pergunta):
        return None
    if categoria == "estoque_variacao":
        if not _autorizada(categoria, regras, REGRA_ESTOQUE):
            return None
        if (
            not _so_consulta(pergunta, _DISPONIBILIDADE)
            or _RESERVA_OU_REPOSICAO.search(pergunta)
            or _OUTRA_DEMANDA_ESTOQUE.search(pergunta)
            or _numero_de_quantidade(pergunta)
        ):
            return None
        return RESPOSTA_ESTOQUE
    if not _autorizada(categoria, regras, REGRA_CONTATO):
        return None
    plataforma = _PLATAFORMAS.get(fatos.get("plataforma"))
    if (
        not plataforma
        or not _so_consulta(pergunta, _CONTATO)
        or _OUTRA_DEMANDA_CONTATO.search(pergunta)
    ):
        return None
    return (
        f"Olá! O atendimento e os pagamentos são realizados somente {plataforma}. "
        "Podemos ajudar você por aqui."
    )
