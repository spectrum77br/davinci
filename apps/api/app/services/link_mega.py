"""Link do vídeo da embalagem/expedição: só MEGA (Vinicius, 23/09/2026).

O vídeo que prova "chegou vazio / veio só um" sobe na MEGA e o link é colado
em três lugares: botão Vídeo do Controle de Estoque, resposta ao pedido de
vídeo da Devoluções (`videos-pendentes`) e "Link envio" da Devoluções.

O link da MEGA tem duas partes: `https://mega.nz/file/<id>` é o endereço e o
que vem depois do `#` é a CHAVE que abre o vídeo (a MEGA guarda tudo
criptografado). Sem a chave, quem clica — o analista da Shopee/TikTok/ML —
cai numa tela pedindo "chave de descriptografia" e não vê nada. Por isso o
link só vale com a chave junto; o app tem a opção de mandá-la separada, e
essa pessoa não pode ligar.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

MEGA_HOSTS = frozenset({"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"})
# Chave do arquivo: base64 url-safe, 43 caracteres no link de hoje
# (/file/<id>#<chave>) e no antigo (/#!<id>!<chave>).
_RE_CHAVE = re.compile(r"[-\w]{20,}")

MSG_NAO_MEGA = "Cole o link do vídeo na MEGA (https://mega.nz/file/...)"
MSG_SEM_CHAVE = (
    "Link da MEGA sem a chave (a parte depois do #): assim ninguém abre o vídeo. "
    "Copie o link de novo com a opção de enviar a chave separadamente DESLIGADA"
)
MSG_PASTA = "Esse é o link de uma pasta da MEGA. Abra o vídeo e copie o link do próprio vídeo"


def com_https(link: str | None) -> str:
    link = (link or "").strip()
    if link and not link.lower().startswith(("http://", "https://")):
        link = "https://" + link
    return link


def e_mega(link: str | None) -> bool:
    link = com_https(link)
    return bool(link) and urlparse(link).netloc.lower() in MEGA_HOSTS


def erro_link_mega(link: str | None) -> tuple[str, str] | None:
    """(code, mensagem) quando o link não é um vídeo da MEGA que abre sozinho;
    None quando é."""
    link = com_https(link)
    if not link or any(c.isspace() for c in link) or not e_mega(link):
        return "video_link_nao_mega", MSG_NAO_MEGA
    u = urlparse(link)
    caminho, chave = u.path.rstrip("/"), u.fragment
    if caminho.startswith("/folder/") and "/file/" not in chave:
        return "video_link_pasta_mega", MSG_PASTA
    arquivo = caminho.startswith(("/file/", "/folder/")) or chave.startswith("!")
    if not arquivo:
        return "video_link_nao_mega", MSG_NAO_MEGA
    if not _RE_CHAVE.search(chave):
        return "video_link_sem_chave", MSG_SEM_CHAVE
    return None
