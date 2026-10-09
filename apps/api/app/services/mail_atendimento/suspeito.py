"""Remetente que pode ser falso (RF5: "alerta quando o remetente não é do domínio oficial").

O alerta do PRÓPRIO Tuta vem pelo conector v2, no bloco `tuta` do e-mail
(`auth_status`, `phishing_status`, `envelope_sender`); o agente v1 (IMAP) não
manda — numa caixa v1 só valem o domínio e o Reply-To. Somam-se as
conferências do DaVinci sobre o From, o Reply-To e os cabeçalhos (os brutos
só quando o v2 mandar):

  • `auth_status` 1, 2, 3 ou 4 (falha forte/fraca, From inválido/ausente);
  • `phishing_status` = 1 (o Tuta marcou como suspeito);
  • o remetente SE DIZ da plataforma (o domínio tem a marca dela, ou o nome
    de exibição tem) mas o domínio não é um dos oficiais — inclusive os
    parecidos (rnercadolivre, mercadolivre-br, shoppe);
  • o domínio é oficial, mas o Reply-To manda a resposta para fora dos
    oficiais;
  • a assinatura DKIM (`d=`) de um remetente oficial é de outro domínio;
  • `Authentication-Results` com dkim/spf/dmarc=fail.

O `differentEnvelopeSender` sozinho NÃO conta (as plataformas mandam por
serviços de envio: o envelope da Shopee/TikTok/Amazon SES é outro): só soma
ao motivo quando já há outro.

Um comprador escrevendo do Gmail não é suspeito: ele não se diz plataforma.

Efeito (quem usa): faixa vermelha "Remetente pode ser falso", NÃO liga ao
pedido sozinho, a IA não pega e a resposta é bloqueada. PURO. Veio do
wt-tuta (`tuta/suspeito.py`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from email.parser import HeaderParser
from email.policy import compat32

from app.services.mail_atendimento.constantes import AUTH_FALHAS, PHISHING_SUSPEITO
from app.services.mail_atendimento.enderecos import dominio

# Domínios oficiais de cada plataforma (e os subdomínios deles). Serviços
# de envio que as plataformas usam no ENVELOPE não entram: o From é que conta.
DOMINIOS_OFICIAIS: dict[str, tuple[str, ...]] = {
    "ml": (
        "mercadolivre.com.br",
        "mercadolivre.com",
        "mercadolibre.com",
        "mercadopago.com",
        "mercadopago.com.br",
        "mercadoenvios.com",
        "mercadoshops.com.br",
    ),
    "shopee": ("shopee.com.br", "shopee.com", "shopeemobile.com", "shopee.io"),
    "tiktok": ("tiktok.com", "tiktokshop.com", "tiktokglobalshop.com", "bytedance.com"),
    "amazon": ("amazon.com.br", "amazon.com", "amazonses.com", "amazonsellerservices.com"),
    "temu": ("temu.com", "temuemail.com", "kuajingmaihuo.com"),
    "aliexpress": ("aliexpress.com", "aliexpress.us", "alibaba.com", "aliexpress.ru"),
    "magalu": ("magalu.com", "magalu.com.br", "magazineluiza.com.br", "luizalabs.com"),
    "shein": ("shein.com", "shein.com.br", "sheingroup.com", "shein.co.uk"),
}
# A "marca" de cada plataforma no domínio e no nome de exibição.
MARCAS: dict[str, tuple[str, ...]] = {
    "ml": ("mercadolivre", "mercadolibre", "mercadopago"),
    "shopee": ("shopee",),
    "tiktok": ("tiktok",),
    "amazon": ("amazon",),
    "temu": ("temu",),
    "aliexpress": ("aliexpress", "alibaba"),
    "magalu": ("magalu", "magazineluiza"),
    "shein": ("shein",),
}
# No nome de exibição ("Mercado Livre <x@golpe.com>").
_NOMES = {
    "ml": ("mercado livre", "mercadolivre", "mercado pago"),
    "shopee": ("shopee",),
    "tiktok": ("tiktok", "tik tok"),
    "amazon": ("amazon",),
    "temu": ("temu",),
    "aliexpress": ("aliexpress",),
    "magalu": ("magalu", "magazine luiza"),
    "shein": ("shein",),
}

M_AUTH = "auth"
M_PHISHING = "phishing"
M_DOMINIO = "dominio_parecido"
M_NOME = "nome_da_plataforma"
M_REPLY_TO = "reply_to_de_fora"
M_DKIM = "dkim_de_outro_dominio"
M_AUTH_RESULTS = "autenticacao_falhou"
M_ENVELOPE = "envelope_diferente"
FRASES = {
    M_AUTH: "o Tuta diz que a autenticação do remetente falhou",
    M_PHISHING: "o Tuta marcou este e-mail como suspeito",
    M_DOMINIO: "o remetente se diz da plataforma, mas o domínio não é o oficial",
    M_NOME: "o nome do remetente é o da plataforma, mas o domínio não é o oficial",
    M_REPLY_TO: "a resposta iria para um domínio de fora da plataforma (Reply-To)",
    M_DKIM: "a assinatura DKIM é de outro domínio",
    M_AUTH_RESULTS: "os cabeçalhos dizem que a verificação (SPF/DKIM/DMARC) falhou",
    M_ENVELOPE: "o envelope veio de outro endereço",
}


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in base if not unicodedata.combining(ch)).lower()


def do_dominio(dom: str, oficiais: tuple[str, ...]) -> bool:
    d = (dom or "").lower().rstrip(".")
    return any(d == o or d.endswith("." + o) for o in oficiais)


def plataforma_do_dominio(dom: str) -> str | None:
    """A plataforma de um domínio OFICIAL (mercadolivre.com.br → ml); senão None."""
    for plataforma, oficiais in DOMINIOS_OFICIAIS.items():
        if do_dominio(dom, oficiais):
            return plataforma
    return None


def _parecido(dom: str) -> str | None:
    """A plataforma que o domínio IMITA (sem ser oficial): marca dentro, ou a 1 letra.

    'rnercadolivre.com' → ml; 'shoppe-br.com' → shopee. Domínio oficial → None.
    """
    if plataforma_do_dominio(dom):
        return None
    rotulos = [r for r in (dom or "").lower().split(".") if r]
    if len(rotulos) < 2:
        return None
    # Os pedaços do nome (sem o TLD), separados também por hífen/sublinhado:
    # "mercadolivre-br" → ["mercadolivre", "br"]. Com as trocas clássicas de
    # letra (rn→m, 0→o, 1→l, vv→w).
    pedacos: set[str] = set()
    for rotulo in rotulos[:-1]:
        for p in [rotulo.replace("-", "").replace("_", ""), *re.split(r"[-_]", rotulo)]:
            if p:
                pedacos.add(p)
                pedacos.add(
                    p.replace("rn", "m").replace("0", "o").replace("1", "l").replace("vv", "w")
                )
    for plataforma, marcas in MARCAS.items():
        for marca in marcas:
            for p in pedacos:
                if p == marca:
                    return plataforma
                # Marca longa no começo ("mercadolivrebr", "aliexpresspromo"):
                # nome de comprador não começa assim. As curtas ("amazon" de
                # "amazonia", "temu", "shein") só iguais ou a 1 letra.
                if len(marca) >= 8 and p.startswith(marca):
                    return plataforma
                if len(marca) >= 5 and _distancia_1(p, marca):
                    return plataforma
    return None


def _distancia_1(a: str, b: str) -> bool:
    """a e b diferem em no máximo uma letra (troca, sobra ou falta) — e não são iguais."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    curto, longo = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(longo)):
        if longo[:i] + longo[i + 1 :] == curto:
            return True
    return False


def _nome_da_plataforma(nome: str) -> str | None:
    plano = _sem_acento(nome)
    for plataforma, nomes in _NOMES.items():
        if any(re.search(rf"\b{re.escape(n)}\b", plano) for n in nomes):
            return plataforma
    return None


def _cabecalhos(texto: str | None):
    if not texto:
        return None
    try:
        return HeaderParser(policy=compat32).parsestr(texto, headersonly=True)
    except Exception:  # noqa: BLE001 — cabeçalho torto não derruba o e-mail
        return None


_RE_DKIM_D = re.compile(r"(?:^|;)\s*d\s*=\s*([a-z0-9.-]+)", re.IGNORECASE)
_RE_FALHA_AUTH = re.compile(r"\b(?:dkim|spf|dmarc)\s*=\s*(?:fail|softfail|permerror)\b", re.I)


@dataclass
class Resultado:
    suspeito: bool = False
    motivos: list[str] = field(default_factory=list)

    def frases(self) -> list[str]:
        return [FRASES.get(m, m) for m in self.motivos]


def avaliar(
    *,
    de_endereco: str | None,
    de_nome: str | None,
    reply_to: list[str],
    auth_status: str | None,
    phishing_status: str | None,
    envelope_diferente: str | None,
    cabecalhos: str | None,
    plataforma_pasta: str | None,
) -> Resultado:
    """O remetente pode ser falso? (e por quê). PURA."""
    motivos: list[str] = []
    if str(auth_status or "") in AUTH_FALHAS:
        motivos.append(M_AUTH)
    if str(phishing_status or "") == PHISHING_SUSPEITO:
        motivos.append(M_PHISHING)
    dom = dominio(de_endereco)
    oficial = plataforma_do_dominio(dom)
    imita = _parecido(dom) if dom else None
    if imita:
        motivos.append(M_DOMINIO)
    nome_plat = _nome_da_plataforma(de_nome or "")
    if nome_plat and not oficial and not imita:
        motivos.append(M_NOME)
    # O que se diz da plataforma: o domínio oficial, o parecido ou o nome.
    diz_plataforma = oficial or imita or nome_plat
    if diz_plataforma:
        oficiais = DOMINIOS_OFICIAIS.get(diz_plataforma, ())
        if oficial and any(r and not do_dominio(dominio(r), oficiais) for r in reply_to):
            motivos.append(M_REPLY_TO)
        msg = _cabecalhos(cabecalhos)
        if msg is not None and oficial:
            assinaturas = [str(v) for v in msg.get_all("DKIM-Signature", []) or []]
            dominios_dkim = [
                m.group(1).lower() for a in assinaturas for m in _RE_DKIM_D.finditer(a)
            ]
            if dominios_dkim and not any(do_dominio(d, oficiais) for d in dominios_dkim):
                motivos.append(M_DKIM)
    msg = _cabecalhos(cabecalhos)
    if msg is not None:
        resultados = " ".join(str(v) for v in msg.get_all("Authentication-Results", []) or [])
        if resultados and _RE_FALHA_AUTH.search(resultados):
            motivos.append(M_AUTH_RESULTS)
    if motivos and envelope_diferente:
        motivos.append(M_ENVELOPE)
    # Sem repetir, na ordem.
    vistos: list[str] = []
    for m in motivos:
        if m not in vistos:
            vistos.append(m)
    return Resultado(suspeito=bool(vistos), motivos=vistos)
