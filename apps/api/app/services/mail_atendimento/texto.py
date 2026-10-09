"""O texto do e-mail: o NOVO (sem o histórico citado) e o corte (RF5). Texto só.

A Central guarda só TEXTO (a regra dela: "never accepts HTML"; o conector
converte o HTML do Tuta em texto no Mac, com o link como "texto <url>"). Aqui
sai o que vai para a mensagem da conversa: o texto que o cliente escreveu
AGORA, sem o "Em 08/10/2026 10:00, Fulano escreveu:" e as linhas com ">"
de baixo — o histórico inteiro continua na Central e no cartão do e-mail.

Se um agente mandar texto com cara de HTML (não deveria), ele é lido como
HTML e vira texto (nunca é mostrado como HTML). PURO. Veio do wt-tuta
(`tuta/html.py`, só a parte de texto).
"""

from __future__ import annotations

import re

from app.services.atendimento.amazon_email import html_para_texto

# A linha que abre o histórico citado ("Em 08/10/2026 10:00, Fulano escreveu:",
# "On Wed, Oct 8, 2026 at 10:00 AM X wrote:", "-----Mensagem original-----",
# o "De: … Enviado: …" do Outlook).
_INICIO_CITACAO = re.compile(
    r"^\s*(?:"
    r"(?:em|on|le|el)\s.{0,200}(?:escreveu|wrote|a écrit|escribió)\s*:?\s*$"
    r"|-{2,}\s*(?:mensagem original|original message|mensaje original)\s*-{2,}"
    r"|(?:de|from)\s*:\s.{0,200}$"
    r")",
    re.IGNORECASE,
)


def parece_html(corpo: str | None) -> bool:
    return bool(
        re.search(r"<\s*(html|body|div|p|br|table|span|a|b|i|font|img)\b", corpo or "", re.I)
    )


def sem_citacao(texto: str) -> str:
    """Corta o histórico citado no fim da resposta (linhas com ">" e o que vem
    depois de "Em …, X escreveu:"). Se cortar TUDO, devolve o texto inteiro."""
    linhas = (texto or "").splitlines()
    saida: list[str] = []
    for i, linha in enumerate(linhas):
        if linha.lstrip().startswith(">"):
            break
        if _INICIO_CITACAO.match(linha) and i > 0:
            # "De:" sozinho no meio do texto pode ser só texto: só corta se
            # vier seguido de outro cabeçalho ("Enviado:", "Para:", "Assunto:").
            if re.match(r"^\s*(?:de|from)\s*:", linha, re.I):
                prox = " ".join(linhas[i + 1 : i + 4]).lower()
                if not re.search(r"\b(enviad[oa]|sent|para|to|assunto|subject)\s*:", prox):
                    saida.append(linha)
                    continue
            break
        saida.append(linha)
    resultado = "\n".join(saida).strip()
    return resultado or (texto or "").strip()


def legivel(corpo: str | None) -> str:
    """O texto inteiro, como texto (o que tiver cara de HTML é convertido)."""
    fonte = corpo or ""
    return html_para_texto(fonte) if parece_html(fonte) else fonte.strip()


def texto_novo(corpo: str | None) -> str:
    """O texto legível do e-mail, sem o histórico citado."""
    return sem_citacao(legivel(corpo))


def cortar(texto: str, maximo: int, aviso: str = "[…]") -> str:
    """Corta no tamanho, com a marca do corte."""
    if len(texto) <= maximo:
        return texto
    return texto[: max(0, maximo - len(aviso) - 1)].rstrip() + "\n" + aviso
