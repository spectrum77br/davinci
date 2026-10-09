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
import unicodedata
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


# ── Os outros campos do formulário do site (RF6): nome, telefone, pedido ──
#
# O mesmo jeito do "E-mail:" de cima: o rótulo, ":" (ou "=", "-") e o valor
# até o fim da linha — ou até o próximo rótulo conhecido, quando o HTML do
# site virou uma linha só ("Nome: Maria E-mail: m@x Telefone: …"). O modelo
# do e-mail de cada site ainda está para confirmar (RF6 [confirmar]): aqui só
# os rótulos comuns em português. Quem chama passa o texto JÁ PROTEGIDO
# (`codigos.proteger`): nada que a equipe não pode ver chega aqui.
_ROTULOS_DO_FORMULARIO = (
    r"e-?mail|telefone|celular|whats\s?app|fone|tel|"
    r"n[º°o.]*\s*(?:do\s+)?pedido|n[uú]mero\s+do\s+pedido|pedido|"
    r"assunto|mensagem|cpf|cnpj|cpf/cnpj|empresa|cidade|estado|uf|cep|produto|nome"
)
_RE_PROXIMO_ROTULO = re.compile(
    rf"\s(?:{_ROTULOS_DO_FORMULARIO})\s*[:=]",
    re.IGNORECASE,
)
_RE_CAMPO_NOME = re.compile(
    r"(?:^|(?<=[\s|;/]))(?:nome\s+completo|seu\s+nome|nome\s+do\s+cliente|nome)\s*[:=\-]\s*"
    r"([^\n]{1,300})",
    re.IGNORECASE,
)
_RE_CAMPO_TELEFONE = re.compile(
    r"(?:^|(?<=[\s|;/]))(?:telefone|celular|whats\s?app|fone|tel)\.?\s*[:=\-]\s*([^\n]{1,80})",
    re.IGNORECASE,
)
_RE_CAMPO_PEDIDO = re.compile(
    r"(?:^|(?<=[\s|;/]))(?:n[º°o.]*\s*(?:do\s+)?pedido|n[uú]mero\s+do\s+pedido|pedido)"
    r"\s*[:=\-]\s*([^\n]{1,80})",
    re.IGNORECASE,
)
_RE_VALOR_PEDIDO = re.compile(r"#?\s*([A-Za-z0-9][A-Za-z0-9\-/.]{2,39})")
# O começo do texto livre da cliente: o "Pedido:"/"Nome:" escrito DENTRO da
# mensagem ("meu nome: zé", "o pedido: 123 chegou") nunca vira campo.
_RE_ROTULO_MENSAGEM = re.compile(r"(?:^|(?<=[\s|;/]))mensagem\s*[:=]", re.IGNORECASE)
# O que pode haver num telefone escrito: o resto ("ramal 3", "/ outro nº") fica fora.
_RE_COMECO_DO_TELEFONE = re.compile(r"[\d\s()+\-.]+")
NOME_MAX = 120
# O nº do pedido do Bling tem 5+ dígitos (o da loja, 6+): menos que isso é
# enfeite ("000", "123") e nunca serve para dizer que é a mesma cliente.
PEDIDO_MIN_DIGITOS = 5


def _valor_do_campo(bruto: str) -> str:
    """O valor até o próximo rótulo conhecido (a linha única do HTML), sem as pontas."""
    m = _RE_PROXIMO_ROTULO.search(f" {bruto}")
    valor = bruto[: max(0, m.start() - 1)] if m else bruto
    return " ".join(valor.split()).strip(" -–—:;|,.")


def _todos_iguais(digitos: str) -> bool:
    return len(set(digitos)) <= 1


def telefone_digitos(valor: str | None) -> str | None:
    """'+55 (11) 98765-4321' → '11987654321'; sem DDD + número (10–11 dígitos) → None.

    Só dígitos (RF6: "mesmo telefone (só dígitos)"); o 55 do país e o 0 da
    operadora caem para que o mesmo número escrito de dois jeitos bata. O
    número de enfeite ("(00) 00000-0000", "1111111111") e o DDD com zero
    (não existe) ficam de fora: nunca servem para juntar duas clientes."""
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) in (11, 12) and digitos.startswith("0"):
        digitos = digitos[1:]
    if len(digitos) not in (10, 11) or "0" in digitos[:2] or _todos_iguais(digitos):
        return None
    return digitos


def _no_comeco_da_linha(texto: str, pos: int) -> bool:
    return not texto[texto.rfind("\n", 0, pos) + 1 : pos].strip()


def _rotulos_confiaveis(regex: re.Pattern[str], texto: str) -> list[re.Match[str]]:
    """Os rótulos do campo na ordem de confiança: no começo da linha primeiro;
    no meio da linha (o HTML que virou uma linha só) só ANTES da "Mensagem:" —
    o que a cliente escreveu dentro da mensagem nunca vira campo."""
    msg = _RE_ROTULO_MENSAGEM.search(texto)
    inicio_da_mensagem = msg.start() if msg else len(texto)
    linha: list[re.Match[str]] = []
    meio: list[re.Match[str]] = []
    for m in regex.finditer(texto):
        if _no_comeco_da_linha(texto, m.start()):
            linha.append(m)
        elif m.start() < inicio_da_mensagem:
            meio.append(m)
    return [*linha, *meio]


def _nome_do_formulario(texto: str) -> str | None:
    for m in _rotulos_confiaveis(_RE_CAMPO_NOME, texto):
        valor = _valor_do_campo(m.group(1))
        # Nome de gente: com letra, sem @ (o e-mail no lugar errado), sem link
        # e sem o que a proteção trocou ("[link de acesso removido]", "••••").
        if (
            valor
            and re.search(r"[^\W\d_]", valor)
            and not any(x in valor for x in ("@", "://", "•", "[", "]"))
        ):
            return valor[:NOME_MAX]
    return None


def _telefone_do_formulario(texto: str) -> str | None:
    for m in _rotulos_confiaveis(_RE_CAMPO_TELEFONE, texto):
        # Só o número: "(11) 4002-8922 ramal 3" para no "ramal".
        comeco = _RE_COMECO_DO_TELEFONE.match(_valor_do_campo(m.group(1)))
        digitos = telefone_digitos(comeco.group(0) if comeco else "")
        if digitos:
            return digitos
    return None


def _pedido_do_formulario(texto: str) -> str | None:
    """O nº do pedido escrito no campo "Pedido:" (com pelo menos 5 dígitos, nem
    todos iguais; "não tenho", "000" e "123" ficam de fora)."""
    for m in _rotulos_confiaveis(_RE_CAMPO_PEDIDO, texto):
        valor = _valor_do_campo(m.group(1))
        achado = _RE_VALOR_PEDIDO.match(valor)
        if achado is None:
            continue
        numero = achado.group(1).strip(".-/")
        digitos = re.sub(r"\D", "", numero)
        if len(digitos) >= PEDIDO_MIN_DIGITOS and not _todos_iguais(digitos) and "•" not in numero:
            return numero[:40]
    return None


def campos_do_formulario(texto: str | None) -> dict[str, str | None]:
    """Nome, telefone (só dígitos) e nº do pedido do corpo do formulário do site
    (RF6). PURO. Campo que não veio (ou não tem cara do que é) → None."""
    corpo = texto or ""
    return {
        "nome": _nome_do_formulario(corpo),
        "telefone": _telefone_do_formulario(corpo),
        "pedido": _pedido_do_formulario(corpo),
    }


def _palavras_sem_acento(texto: str | None) -> list[str]:
    plano = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return [p for p in "".join(c if c.isalnum() else " " for c in plano.lower()).split() if p]


def mesmo_nome(um: str | None, outro: str | None) -> bool | None:
    """Os dois nomes são da mesma pessoa? O 1º nome igual e, quando os dois têm
    sobrenome, o último também ("Maria Souza" × "MARIA APARECIDA SOUZA" = sim;
    "Ana Lima" × "Bruno Costa" = não). None = falta nome para saber. PURO."""
    a, b = _palavras_sem_acento(um), _palavras_sem_acento(outro)
    if not a or not b:
        return None
    if a[0] != b[0]:
        return False
    if len(a) == 1 or len(b) == 1:
        return True
    return a[-1] == b[-1]
