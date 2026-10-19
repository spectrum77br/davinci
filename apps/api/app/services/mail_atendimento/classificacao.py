"""Tipo do e-mail e chave externa de atendimento, sem confundir com pedido/pasta.

Só rótulo explícito identifica caso de marketplace. Assunto e texto NOVO
podem ser usados; citações de mensagens antigas devem ser retiradas pelo
chamador. O tipo não muda finalidade, estado, remetente nem canal de envio.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.atendimento.validador import plano_de
from app.services.mail_atendimento.constantes import FINALIDADES_PENDENTES

TIPO_INFORMATIVO = "informativo"
TIPO_CHAMADO = "chamado"
TIPO_VENDA = "venda"
TIPOS_EMAIL = (TIPO_INFORMATIVO, TIPO_CHAMADO, TIPO_VENDA)
ROTULOS_TIPO_EMAIL = {
    TIPO_INFORMATIVO: "Informativo",
    TIPO_CHAMADO: "Chamado",
    TIPO_VENDA: "Venda",
}
PLATAFORMAS_CASO = frozenset({"ml", "shopee", "aliexpress"})
# O índice existente de mail_message_meta.protocolo aceita até 32 caracteres.
MAX_CASO_ID = 32
_ROTULOS = {
    "ml": r"(?:caso|case|reclamacao)(?:\s+(?:id|no\.?|number|numero))?",
    "shopee": (
        r"(?:protocolo(?:\s+de\s+atendimento)?|numero\s+de\s+atendimento|"
        r"(?:id\s+do\s+)?chamado|support\s+case|case|ticket)"
        r"(?:\s+(?:id|no\.?|number|numero))?"
    ),
    "aliexpress": (
        r"(?:caso|case|disputa|dispute|chamado)"
        r"(?:\s+(?:id|no\.?|number|numero))?"
    ),
}
_CASOS = {
    p: re.compile(
        rf"\b{rotulo}\s*(?:(?:n[.º°o]*|numero)\s*)?[:#=\-–—\s]*"
        r"([a-z0-9][a-z0-9_-]{3,})(?![a-z0-9_-])"
    )
    for p, rotulo in _ROTULOS.items()
}
_INFORMATIVO = re.compile(
    r"\b(?:newsletter|novidades|novas ofertas|dicas para|comunicado|"
    r"atualizacao (?:das?|de) politicas?|resumo (?:diario|semanal|mensal)|"
    r"relatorio (?:diario|semanal|mensal)|ofertas da semana)\b"
)
_VENDA = re.compile(
    r"\b(?:voce vendeu|nova venda|venda (?:aprovada|realizada|confirmada)|novo pedido|"
    r"pedido (?:recebido|aprovado|confirmado)|pagamento (?:aprovado|confirmado))\b"
)
_CHAMADO = re.compile(
    r"\b(?:chamado|caso|protocolo|solicitacao|reclamacao|disputa|support case)\b"
    r".{0,35}\b(?:abert[oa]|registrad[oa]|em andamento|em analise|atualizad[oa]|pendente)\b"
    r"|\b(?:abertura|andamento|atualizacao|resposta|retorno)\s+(?:do|da|ao|a)\s+"
    r"(?:(?:seu|sua)\s+)?(?:chamado|caso|solicitacao|reclamacao|disputa)\b"
)
_CHAMADO_CORPO = re.compile(
    r"\b(?:(?:recebemos|registramos|abrimos|analisamos|atualizamos)\s+"
    r"(?:(?:o seu|a sua|seu|sua|um|uma|o|a)\s+)?(?:chamado|solicitacao)|"
    r"responda (?:ao|este|a este)|"
    r"aguardamos (?:a )?sua resposta|precisamos (?:da|de sua) resposta)\b"
)
_LOGISTICA = re.compile(
    r"\b(?:pedido|pacote|envio|mercadoria|entrega)\b.{0,50}"
    r"\b(?:enviad[oa]|postad[oa]|entregue|atrasad[oa]|retid[oa]|em transito|problema)\b"
    r"|\b(?:problema|atraso|tentativa)\s+(?:na|no|de)\s+(?:entrega|envio|mercadoria)\b"
)


@dataclass(frozen=True)
class Classificacao:
    tipo_email: str
    caso_id: str | None = None
    casos_ambiguos: bool = False
    tipo_inferido: bool = False


def _casos_explicitos(plataforma: str | None, *textos: str | None) -> set[str]:
    """Um identificador inequívoco com rótulo; nunca um número solto/pedido.

    Dois identificadores diferentes no próprio conteúdo novo são ambíguos:
    nenhum deles vira chave automática. Não trunca identificador comprido.
    """
    padrao = _CASOS.get(plataforma or "")
    if padrao is None:
        return set()
    encontrados = set()
    for texto in textos:
        for m in padrao.finditer(plano_de(texto or "")):
            numero = m.group(1).upper()
            if not re.search(r"\d", numero):
                continue
            encontrados.add(numero)
    return encontrados


def caso_explicito(plataforma: str | None, *textos: str | None) -> str | None:
    """Um único caso rotulado, sem escolher entre referências ambíguas."""
    encontrados = _casos_explicitos(plataforma, *textos)
    if len(encontrados) != 1:
        return None
    numero = next(iter(encontrados))
    return numero if len(numero) <= MAX_CASO_ID else None


def classificar(
    *,
    plataforma: str | None,
    finalidade: str | None,
    assunto: str | None = None,
    texto: str | None = None,
    remetente_tipo: str | None = None,
) -> Classificacao:
    """O conteúdo pode distinguir informativo/venda/chamado da finalidade da pasta."""
    casos = _casos_explicitos(plataforma, assunto, texto)
    caso = next(iter(casos)) if len(casos) == 1 else None
    if len(casos) > 1 or any(len(c) > MAX_CASO_ID for c in casos):
        return Classificacao(TIPO_CHAMADO, casos_ambiguos=True)
    if caso or plataforma == "site":
        return Classificacao(TIPO_CHAMADO, caso)
    titulo = plano_de(assunto or "")
    corpo = plano_de(texto or "")
    if _CHAMADO.search(titulo):
        return Classificacao(TIPO_CHAMADO)
    if _INFORMATIVO.search(titulo) or _LOGISTICA.search(titulo):
        return Classificacao(TIPO_INFORMATIVO)
    if _VENDA.search(titulo):
        return Classificacao(TIPO_VENDA)
    if _CHAMADO_CORPO.search(corpo):
        return Classificacao(TIPO_CHAMADO)
    if remetente_tipo == "comprador":
        # Atendimento direto continua na fila, mas não inventa caso da
        # plataforma nem se junta a um caso apenas por mencionar o pedido.
        return Classificacao(TIPO_CHAMADO, tipo_inferido=True)
    if finalidade == "vendas":
        return Classificacao(TIPO_VENDA, tipo_inferido=True)
    return Classificacao(TIPO_INFORMATIVO, tipo_inferido=True)


def tipo_da_conversa(mail: Mapping[str, Any], plataforma: str | None) -> str:
    """Metadado novo, ou apresentação compatível do legado, sem reprocessá-lo."""
    if mail.get("tipo_email") in TIPOS_EMAIL:
        return str(mail["tipo_email"])
    if plataforma == "site" or mail.get("caso_id") or mail.get("protocolo"):
        return TIPO_CHAMADO
    if mail.get("finalidade") == "vendas":
        return TIPO_VENDA
    if mail.get("finalidade") in FINALIDADES_PENDENTES:
        return TIPO_CHAMADO
    return TIPO_INFORMATIVO


def tipos_de_meta(meta: Any, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Para o cartão: payload da mensagem quando existe, sem decifrar o corpo.

    O protocolo de site continua em seu próprio campo; `caso_id` é do
    marketplace. Sem payload, finalidade/protocolo dão o fallback do legado.
    """
    mail = dict(payload or {})
    if isinstance(mail.get("mail"), Mapping):
        mail = dict(mail["mail"])
    mail.setdefault("finalidade", getattr(meta, "finalidade", None))
    protocolo = getattr(meta, "protocolo", None)
    plataforma = getattr(meta, "plataforma", None)
    mail.setdefault("protocolo", protocolo)
    caso_id = mail.get("caso_id")
    if not caso_id and plataforma in PLATAFORMAS_CASO:
        caso_id = protocolo
    return {
        "tipo_email": tipo_da_conversa(mail, plataforma),
        "caso_id": str(caso_id) if caso_id else None,
        "tipo_email_inferido": mail.get("tipo_email") not in TIPOS_EMAIL
        or bool(mail.get("tipo_email_inferido")),
    }
