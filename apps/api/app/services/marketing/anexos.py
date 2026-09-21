"""Travas comuns a TODO anexo do marketing — briefing, personagem, entrega.

Este módulo existe porque as mesmas quatro perguntas se repetem em cada rota
nova de arquivo, e errar qualquer uma tem consequência concreta:

1. **Que MIME o banco guarda?** O `Content-Type` do upload é escolhido por
   quem sobe o arquivo. Guardar esse valor e devolvê-lo depois é servir o tipo
   que um terceiro escolheu. Aqui o MIME sai da EXTENSÃO.
2. **O caminho ficou dentro da raiz?** `file_rel` vem do banco, e o banco é
   alimentado por upload. `../../etc/passwd` não pode virar leitura de
   arquivo do servidor.
3. **O arquivo é executável no navegador?** Fora da lista branca desce como
   `octet-stream` + `attachment`, que o navegador não roda.
4. **O upload tem teto?** Sem teto, um POST enche o disco — que é o mesmo da
   API e do Postgres — antes de qualquer validação.

Fica em `services/` e não num router porque três routers usam (criativos,
roteiros e personagens) e um importar do outro criaria o acoplamento que a
separação entre eles existe pra evitar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.config import get_settings

# ─── Imagens de apoio (referência do roteiro, foto do personagem) ──────────
#
# SVG fica DE FORA de propósito, mesmo sendo imagem: SVG é XML e carrega
# <script>. Servido inline com `image/svg+xml` ele roda no domínio de quem
# abriu — no DaVinci com o cookie da sessão, no portal da agência com a sessão
# do site delas. Quem precisa mandar um vetor manda o PNG.
_EXT_IMAGEM: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# O briefing aceita PDF (encarte, ficha do produto); o personagem, não — ali é
# foto de identidade e nada mais, e cada tipo a menos é superfície a menos.
_EXT_REFERENCIA: dict[str, str] = {**_EXT_IMAGEM, ".pdf": "application/pdf"}

MIMES_IMAGEM = frozenset(_EXT_IMAGEM.values())
MIMES_REFERENCIA = frozenset(_EXT_REFERENCIA.values())

# Print de tela não passa de uns poucos MB; 25 é folga e mantém a pasta de
# apoio longe do teto de 200 MB da entrega (routers/marketing_creatives.py).
MAX_BYTES_APOIO = 25 * 1024 * 1024
MAX_ANEXOS_POR_LINHA = 30


def mime_da_extensao(nome: str, *, tabela: dict[str, str]) -> str:
    """MIME pela EXTENSÃO, ou 400. Nunca o `content_type` do uploader."""
    mime = tabela.get(Path(nome).suffix.lower())
    if mime is None:
        raise HTTPException(
            400,
            detail={
                "code": "extensao_nao_aceita",
                "arquivo": nome,
                "aceitas": sorted(tabela),
            },
        )
    return mime


def nome_seguro(bruto: str | None) -> str:
    """Só o nome do arquivo, sem pasta nenhuma. `..` e vazio são recusados."""
    nome = Path(bruto or "arquivo").name
    if not nome or nome in {".", ".."}:
        raise HTTPException(400, detail={"code": "nome_invalido"})
    return nome


def url_de_produto(bruto: str | None) -> str:
    """Só http/https, sem espaço nem controle. LISTA BRANCA, não bloqueio.

    Este texto vira `href` no portal PHP das agências. Um `javascript:` (ou
    `data:text/html`) gravado aqui seria XSS armazenado do lado de fora, num
    site que nem é nosso — e bloquear "javascript:" por nome perde
    `jAvAsCrIpT:`, `java\\tscript:` e companhia. Por isso o que não começa com
    http:// ou https:// é recusado, ponto.
    """
    u = (bruto or "").strip()
    if not u:
        raise HTTPException(400, detail={"code": "link_vazio"})
    if len(u) > 2000:
        raise HTTPException(400, detail={"code": "link_longo_demais"})
    if not u.lower().startswith(("http://", "https://")):
        raise HTTPException(400, detail={"code": "link_invalido"})
    if any(c.isspace() or ord(c) < 32 for c in u):
        raise HTTPException(400, detail={"code": "link_invalido"})
    return u


def caminho_confinado(file_rel: str | None) -> Path | None:
    """Resolve `uploads_dir/file_rel` e prova que não escapou do diretório.

    Segunda tranca: o upload já sanitiza o nome, mas uma linha antiga, um
    import ou um bug futuro que grave "../../etc/passwd" não pode virar
    leitura arbitrária.
    """
    if not (file_rel or "").strip():
        return None
    raiz = Path(get_settings().uploads_dir).resolve()
    try:
        alvo = (raiz / file_rel).resolve()
        alvo.relative_to(raiz)
    except (ValueError, OSError):
        return None
    return alvo


def mime_seguro(mime: str | None, *, permitidos: frozenset[str]) -> tuple[str, str]:
    """(media_type, content_disposition) — nunca devolve o MIME cru do uploader.

    Fora da allowlist o arquivo continua servido (o operador precisa baixar o
    que subiu), mas como `application/octet-stream` + `attachment`, que o
    navegador não executa.
    """
    limpo = (mime or "").split(";")[0].strip().lower()
    if limpo in permitidos:
        return limpo, "inline"
    return "application/octet-stream", "attachment"


def gravar_em_disco(up: UploadFile, destino: Path, *, teto: int, code: str) -> int:
    """Grava em streaming e ABORTA no meio ao passar do teto, sem deixar lixo.

    Devolve o tamanho em bytes. Nunca carrega o arquivo inteiro na memória —
    é o mesmo fluxo do upload de entrega, que recebe vídeos de 40 MB.
    """
    escrito = 0
    with destino.open("wb") as fh:
        while pedaco := up.file.read(1024 * 1024):
            escrito += len(pedaco)
            if escrito > teto:
                fh.close()
                destino.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    detail={
                        "code": code,
                        "arquivo": destino.name,
                        "max_mb": teto // (1024 * 1024),
                    },
                )
            fh.write(pedaco)
    return escrito


def anexo_out(rec: Any) -> dict[str, Any]:
    """Serializador comum de anexo. `file_rel` NUNCA sai: é caminho no disco."""
    return {
        "id": str(rec.id),
        "file_name": rec.file_name,
        "file_mime": rec.file_mime,
        "file_size": rec.file_size,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }
