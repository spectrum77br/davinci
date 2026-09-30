# ruff: noqa: E501
"""Erro de negócio da NFS-e e o "e agora?" das recusas que voltam da NFE.io.

A NFE.io devolve o motivo em `flowMessage` (muitas vezes o texto da própria
prefeitura, às vezes com o código `[1207]`, `[R0001]` ou `E0xxx` do Emissor
Nacional). Aqui fica só a dica em português de gente, pra a tela mostrar
embaixo da mensagem — o texto original vai sempre junto.
"""

from __future__ import annotations

import re
from typing import Any


class NfseError(Exception):
    """Erro de negócio que vira `{"detail": {"code", "mensagem", ...}}` na rota."""

    def __init__(self, status: int, code: str, mensagem: str, **extra: Any):
        super().__init__(mensagem)
        self.status = status
        self.detail = {"code": code, "mensagem": mensagem, **extra}


# Código entre colchetes da prefeitura ("[1207] ...") ou do Emissor Nacional (E0xxx).
_CODIGO = re.compile(r"\[\s*([A-Z]?\d{2,5})\s*\]|\b(E\d{4})\b")

O_QUE_FAZER: dict[str, str] = {
    "1207": "A prefeitura ainda não autorizou esta empresa a emitir NFS-e. Pedir a autorização na prefeitura (contabilidade) e depois reenviar.",
    "R0001": "A prefeitura não achou a Inscrição Municipal da empresa. Corrigir a IM no cadastro da empresa na NFE.io.",
    "216": "A prefeitura não achou o código de atividade (código do serviço). Confirmar o código com a contabilidade e corrigir na aba Empresas.",
    "E0014": "Esse número já virou nota antes: use 'Atualizar da NFE.io' antes de qualquer reenvio.",
    "E0160": "O regime (Simples/MEI/Lucro Presumido) não bate com a Receita no mês. Conferir o cadastro da empresa na NFE.io com a contabilidade.",
    "E0202": "O tomador é a própria empresa prestadora. Escolher outro tomador.",
    "E0310": "O código do serviço não foi aceito. Confirmar o código com a contabilidade e corrigir na aba Empresas.",
    "E0314": "O código de tributação municipal não existe nesse município. Corrigir o código do serviço na aba Empresas.",
    "E0718": "O certificado cadastrado na NFE.io não é o da empresa. Trocar o certificado no painel da NFE.io.",
    "E0822": "Passou o prazo de cancelamento do município: só por processo na prefeitura.",
    "E0827": "Já tem tributo recolhido nessa nota; o município não deixa cancelar.",
}

# Trechos em inglês que a própria NFE.io devolve (sem código).
_TRECHOS: tuple[tuple[str, str], ...] = (
    (
        "max retry reached on download stage",
        "A nota FOI emitida; só o PDF não ficou pronto na NFE.io. NÃO reemita: peça ao suporte da NFE.io para reprocessar o PDF.",
    ),
    (
        "max retry reached on send",
        "A NFE.io tentou por horas e a prefeitura não respondeu. Confira no site da prefeitura se a nota saiu ANTES de reenviar.",
    ),
    (
        "max retry reached on check",
        "A NFE.io tentou por horas e a prefeitura não respondeu. Confira no site da prefeitura se a nota saiu ANTES de reenviar.",
    ),
    (
        "certificate is not active",
        "O certificado digital da empresa na NFE.io venceu ou não está ativo. Renovar o certificado no painel da NFE.io e reenviar.",
    ),
    (
        "was not found for city code",
        "A prefeitura da empresa não aceita esse código do serviço. Confirmar o código com a contabilidade e corrigir na aba Empresas.",
    ),
    (
        "municipaltaxnumber must not exceed",
        "A Inscrição Municipal cadastrada na NFE.io está no formato errado para a prefeitura. Corrigir no painel da NFE.io.",
    ),
    (
        "company is not active",
        "A empresa não está ativa na NFE.io. Conferir o cadastro no painel da NFE.io.",
    ),
    (
        "municipal tax is not allowed",
        "A Inscrição Municipal da empresa na NFE.io não está liberada para emitir. Conferir no painel da NFE.io.",
    ),
)


def codigo_da_mensagem(texto: str | None) -> str:
    m = _CODIGO.search(texto or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").upper()


def dica(codigo: str | None, texto: str | None) -> str | None:
    d = O_QUE_FAZER.get((codigo or "").upper())
    if d:
        return d
    baixo = (texto or "").lower()
    for trecho, o_que in _TRECHOS:
        if trecho in baixo:
            return o_que
    return None


def explicar(msgs: list[dict] | None) -> list[dict]:
    """Junta a dica em cada mensagem ({codigo, descricao, complemento})."""
    out = []
    for m in msgs or []:
        m = dict(m)
        codigo = m.get("codigo") or codigo_da_mensagem(m.get("descricao"))
        if codigo and not m.get("codigo"):
            m["codigo"] = codigo
        d = dica(codigo, f"{m.get('descricao') or ''} {m.get('complemento') or ''}")
        if d:
            m["o_que_fazer"] = d
        out.append(m)
    return out


def explicar_texto(texto: str | None) -> list[dict]:
    """A `flowMessage` da NFE.io vira a lista de erros que a tela já sabe mostrar."""
    texto = (texto or "").strip()
    if not texto:
        return []
    return explicar([{"codigo": "", "descricao": texto[:2000], "complemento": ""}])
