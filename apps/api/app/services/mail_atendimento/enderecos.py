"""Endereços de e-mail: normalizar, quem é nosso, quem é aviso, e o cadastro da loja.

Tudo PURO (sem banco). O cadastro de Lojas guarda em `store_info.email` quase
sempre só a parte antes do @ (ML Barbosa = "21max" → 21max@tuta.com); a
decisão padrão de 08/10/2026 é NÃO mexer no cadastro e somar `@tuta.com`
quando falta o domínio. As 16 linhas "sac@marca" sem o fim do domínio
("sac@poofy", "sac@uranyx") viram um endereço PARCIAL (`parcial_do_cadastro`:
a parte antes do @ e a palavra da marca) que casa com o endereço da caixa
"sac@poofy.com.br" (`casa_parcial`) — o cadastro da marca não serve (em
produção a marca "poofy" tem o domínio charlots.com.br). O que não dá para ler
nem assim, a saúde avisa "cadastro incompleto" em vez de pular a loja calada.
Veio do wt-tuta (`tuta/enderecos.py`).
"""

from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr

from app.services.atendimento.abas import contato_generico
from app.services.mail_atendimento.constantes import (
    DOMINIO_TUTA_PADRAO,
    DOMINIOS_DE_SERVICO,
    DOMINIOS_TUTA,
    LOCAIS_DE_SERVICO,
    PREFIXOS_LOCAIS_DE_SERVICO,
)

_RE_ENDERECO = re.compile(r"^[^@\s<>()\[\],;:\"]{1,128}@[a-z0-9.-]{1,253}\.[a-z]{2,24}$")
# Remetente técnico que não é pessoa nem plataforma (devolução, auto-resposta).
_TECNICOS = ("mailer-daemon", "postmaster", "mailerdaemon")


def normalizar(valor: object) -> str:
    """'  <Fulano@Gmail.COM> ' → 'fulano@gmail.com'; inválido → ''."""
    if not isinstance(valor, str):
        return ""
    bruto = valor.strip()
    if bruto.lower().startswith("mailto:"):
        bruto = bruto[7:]
    _, endereco = parseaddr(bruto)
    endereco = (endereco or bruto).strip().strip("<>").strip().lower()
    return endereco if _RE_ENDERECO.match(endereco) else ""


def dominio(endereco: str | None) -> str:
    return (endereco or "").rpartition("@")[2].strip().lower().rstrip(".")


def local(endereco: str | None) -> str:
    return (endereco or "").rpartition("@")[0].strip().lower()


def lista(bruto: object) -> list[str]:
    """Os endereços válidos de uma lista ou de um texto "a@b, c@d", sem repetir, na ordem."""
    itens: list[str] = []
    if isinstance(bruto, str):
        itens = [e for _, e in getaddresses([bruto])]
    elif isinstance(bruto, list | tuple):
        for item in bruto:
            if isinstance(item, dict):
                itens.append(str(item.get("endereco") or item.get("address") or ""))
            elif isinstance(item, str):
                itens.append(item)
    saida: list[str] = []
    for e in itens:
        n = normalizar(e)
        if n and n not in saida:
            saida.append(n)
    return saida


def endereco_do_cadastro(valor: str | None) -> str:
    """O e-mail do cadastro da loja como endereço: '16tr' → '16tr@tuta.com'; vazio → ''."""
    bruto = (valor or "").strip()
    if not bruto:
        return ""
    if "@" not in bruto:
        bruto = f"{bruto}@{DOMINIO_TUTA_PADRAO}"
    return normalizar(bruto)


def palavra_do_dominio(dom: str | None) -> str:
    """A marca dentro do domínio: 'uranyx.com.br' → 'uranyx'; 'poofy' → 'poofy'."""
    return (dom or "").strip().lower().split(".")[0]


_RE_PARCIAL = re.compile(r"^[^@\s<>()\[\],;:\"]{1,128}@[a-z0-9-]{1,63}$")


def parcial_do_cadastro(valor: str | None) -> tuple[str, str] | None:
    """O cadastro "sac@poofy" (sem o fim do domínio) → ('sac', 'poofy'); senão None."""
    bruto = (valor or "").strip().lower()
    if not _RE_PARCIAL.match(bruto):
        return None
    parte, _, palavra = bruto.rpartition("@")
    return parte, palavra


def casa_parcial(endereco: str | None, parcial: tuple[str, str]) -> bool:
    """'sac@poofy.com.br' é o "sac@poofy" do cadastro? (a parte e a marca batem)."""
    return local(endereco) == parcial[0] and palavra_do_dominio(dominio(endereco)) == parcial[1]


def e_do_tuta(endereco: str | None) -> bool:
    return dominio(endereco) in DOMINIOS_TUTA


def ambiguo_no_tuta(endereco: str, aliases: set[str]) -> bool:
    """O cadastro sem domínio virou '16tr@tuta.com', mas há outro alias '16tr@<outro do Tuta>'?

    Aí '16tr' não diz qual dos dois: ambíguo (C4).
    """
    parte = local(endereco)
    return any(local(a) == parte and dominio(a) in DOMINIOS_TUTA and a != endereco for a in aliases)


def e_tecnico(endereco: str | None) -> bool:
    """mailer-daemon/postmaster: a devolução do servidor."""
    parte = re.sub(r"[^a-z]", "", local(endereco))
    return any(re.sub(r"[^a-z]", "", t) in parte for t in _TECNICOS)


def e_aviso(endereco: str | None) -> bool:
    """noreply, "não responder", o domínio da plataforma, mailer-daemon: não é o comprador."""
    if not endereco:
        return False
    return contato_generico(endereco) or e_tecnico(endereco)


def de_servico(endereco: str | None) -> bool:
    """O remetente é de um SERVIÇO (domínio de serviço, ou a parte antes do @ de
    máquina: security@, account@, notification@, noreply…)? Então não é pessoa."""
    dom = dominio(endereco)
    if any(dom == d or dom.endswith(f".{d}") for d in DOMINIOS_DE_SERVICO):
        return True
    pedacos = [p for p in re.split(r"[^a-z0-9]+", local(endereco)) if p]
    return any(p in LOCAIS_DE_SERVICO or p.startswith(PREFIXOS_LOCAIS_DE_SERVICO) for p in pedacos)


def cara_de_pessoa(endereco: str | None) -> bool:
    """O remetente tem cara de PESSOA (o cliente escrevendo)? Nem aviso, nem
    "não responder", nem serviço."""
    return bool(endereco) and not (
        e_aviso(endereco) or nao_responde(endereco) or de_servico(endereco)
    )


def nao_responde(endereco: str | None) -> bool:
    """O endereço avisa que ninguém lê a resposta (noreply, nao-responder, donotreply)."""
    parte = re.sub(r"[^a-z0-9]", "", local(endereco))
    return any(
        t in parte
        for t in ("noreply", "naoresponda", "naoresponder", "donotreply", "mailerdaemon", "nreply")
    )


_RE_EMAIL_NO_TEXTO = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+", re.UNICODE)
_RE_CAMPO_EMAIL = re.compile(
    r"(?:e-?mail|email do cliente|seu e-?mail)\s*[:\-=]\s*"
    r"([\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+)",
    re.IGNORECASE,
)


def email_do_formulario(texto: str | None, nossos: set[str]) -> str:
    """O e-mail do CLIENTE escrito no corpo do formulário do site ("E-mail: x@y") — E5.

    O formulário chega DE sac@ PARA sac@ (sem Reply-To): o Para da resposta
    sai do campo do formulário, calculado AQUI no servidor (nunca vem do
    navegador). Primeiro o campo "E-mail:"; senão o primeiro endereço do
    texto que não é nosso nem aviso. Sem nenhum → ''.
    """
    corpo = texto or ""
    for m in _RE_CAMPO_EMAIL.finditer(corpo):
        e = normalizar(m.group(1))
        if e and e not in nossos and not e_aviso(e):
            return e
    for m in _RE_EMAIL_NO_TEXTO.finditer(corpo):
        e = normalizar(m.group(0))
        if e and e not in nossos and not e_aviso(e):
            return e
    return ""
