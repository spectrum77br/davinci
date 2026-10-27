"""Zap (WhatsApp Cloud API) — a MÍDIA: baixar, guardar, servir e expirar (RF10, Onda 3).

Desenho: `docs/zap/desenho.md` §5.5 (mídia recebida), §6.8 (foto de saída),
§10.4 (segurança da mídia) e §10.5 (retenção). 27/10/2026.

Contraria DE PROPÓSITO o "nunca o arquivo" de `atendimento_mensagens`, e só
para o Zap: a URL da Meta vale ~5 min, a mídia some da Meta em dias, e depois
da migração do Duoke não há celular onde olhar. A cópia fica em
`uploads_dir/atendimento/zap/<numero>/<aaaa>/<mm>/<uuid>.<ext>` — caminho
GERADO AQUI (nada do cliente entra nele: sem path traversal) — por 180 dias
(`atendimento_zap_retencao_midia_dias`, decisão do Eduardo de 26/10/2026).
O arquivo fica no disco SEM cifra: a retenção e o backup de `/data/uploads`
são decisões explícitas (pergunta 14.2 nº 2).

O BAIXADOR (`baixar`, uma mídia por vez; o job `atendimento_zap_baixar_midia`
e o `varrer` do worker chamam):

  1. PEGA a mídia (`pendente` vencida, ou `baixando` cujo prazo passou — o
     worker que caiu no meio) num UPDATE atômico: duas rodadas nunca baixam a
     mesma. `baixando` leva um prazo de 10 min (`PRAZO_BAIXANDO`).
  2. `GET /{media_id}` na Graph com o token no header → `url`, `mime_type`,
     `sha256`, `file_size`. A URL vale ~5 min: baixa NA HORA.
  3. O Bearer SÓ segue para host da lista (`host_permitido`: https, sem
     usuário na URL, `lookaside.fbsbx.com`, `*.fbcdn.net`,
     `graph.facebook.com` [confirmar os domínios no ensaio]) — conferido A
     CADA redirecionamento (o cliente é `follow_redirects=False`; no máximo 3
     saltos). Protege contra SSRF e contra o token vazar para outro host.
  4. Teto de tamanho (`atendimento_zap_midia_max_mb`): pelo `file_size` da
     Meta (nem baixa), pelo `Content-Length` e DURANTE o download (para no
     byte que passa); acima dele, `grande_demais`. Nada pela metade fica no
     disco.
  5. `sha256` da Meta conferido em hex OU base64 [confirmar o formato:
     §14.3]; divergente = `falhou`. Formato que não é nenhum dos dois só fica
     no log (não dá para conferir — não recusa).
  6. O TIPO vale pela ASSINATURA do arquivo (`detectar_tipo`), nunca pelo
     `mime_type` que o remetente declarou: o HTML que diz ser JPEG é recusado
     (`falhou`, "tipo de arquivo não aceito"); SVG, HTML e executável nunca
     entram. O declarado só desempata o que a assinatura não separa (DOC ×
     XLS × PPT antigos, texto puro).
  7. Grava a cópia, o `sha256_local` e `baixada`, e o anexo da mensagem ganha
     a URL RELATIVA da rota (`/api/atendimento/zap/midia/<id>`), o tipo de
     verdade e o tamanho.
  8. Erro (Meta fora, 5xx, token caído, host fora da lista, link vencido de
     novo): volta a `pendente` com backoff 1 → 5 → 30 min → 2 h → 12 h. 404
     no download = o link venceu: pede outro à Graph NA HORA (uma vez). O
     prazo: 30 dias do registro (`PRAZO_TENTAR`) — ou, passado o
     `expira_meta_em` (7 dias, a régua do desenho), a Graph dizendo que a
     mídia não existe; depois disso, `expirada` e o balão diz "a mídia
     expirou antes de ser baixada". [confirmar] o prazo da Meta: o desenho
     diz 7 dias, a documentação da mídia SUBIDA diz 30; tentar até 30 custa
     um GET a cada 12 h e não perde nada.

A FOTO DE SAÍDA (`guardar_saida`): a cópia do que a pessoa mandou, ANTES de
falar com a Meta — o WhatsApp não devolve URL pública, e o balão usa a nossa
cópia (o mesmo endereço da rota).

A RETENÇÃO (`apagar_vencidas`, para o cron `atendimento_zap_limpeza` da Onda
4): passado o `apagar_em`, o arquivo some e o registro fica `apagada` (o
balão diz "mídia apagada pela política de retenção").

Nunca no log: a URL da Meta, o token, os bytes, o nome do arquivo do cliente.
Só ids, o tipo e o tamanho.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoMensagem,
    AtendimentoZapConta,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
)
from app.services.atendimento import zap_api, zap_contas
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()

# ── Onde mora ─────────────────────────────────────────────────────────────

PASTA = "atendimento/zap"
# O download em andamento: na MESMA pasta-raiz da cópia (o `os.replace` final
# é atômico) e com sufixo próprio, para a limpeza achar a sobra de um worker
# que caiu.
PASTA_TEMP = f"{PASTA}/_partes"
SUFIXO_TEMP = ".parte"
# A rota que serve a cópia (`routers/atendimento_zap.py`). Relativa: a web
# chama pelo mesmo endereço da tela (o proxy `/api/**` do Nuxt).
ROTA_MIDIA = "/api/atendimento/zap/midia"

# ── O que se aceita (§5.5 item 5) ──────────────────────────────────────────
# O tipo de VERDADE (o da assinatura) → (extensão, espécie). A espécie diz
# como o balão mostra e como a rota entrega: imagem/áudio/vídeo `inline`,
# documento SEMPRE `attachment` (baixa, nunca abre no navegador: antivírus
# fica [VETÁVEL] para depois). Nunca SVG, HTML ou executável.
TIPOS_ACEITOS: dict[str, tuple[str, str]] = {
    "image/jpeg": (".jpg", "imagem"),
    "image/png": (".png", "imagem"),
    # A figurinha do WhatsApp.
    "image/webp": (".webp", "imagem"),
    "video/mp4": (".mp4", "video"),
    "video/3gpp": (".3gp", "video"),
    "audio/aac": (".aac", "audio"),
    "audio/amr": (".amr", "audio"),
    "audio/mpeg": (".mp3", "audio"),
    "audio/mp4": (".m4a", "audio"),
    # O áudio de voz do WhatsApp (OGG/Opus).
    "audio/ogg": (".ogg", "audio"),
    "application/pdf": (".pdf", "documento"),
    "application/msword": (".doc", "documento"),
    "application/vnd.ms-excel": (".xls", "documento"),
    "application/vnd.ms-powerpoint": (".ppt", "documento"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ".docx",
        "documento",
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx", "documento"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        ".pptx",
        "documento",
    ),
    "text/plain": (".txt", "documento"),
}
ESPECIES_INLINE = frozenset({"imagem", "audio", "video"})
# DOC/XLS/PPT antigos são todos o mesmo contêiner (OLE2): a assinatura não
# separa, o declarado desempata — e só entre os três (o MSI, instalador, também
# é OLE2 e fica de fora).
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE2_DECLARADOS = (
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
)
# DOCX/XLSX/PPTX são ZIP com a parte principal de cada um; ZIP comum (ou APK,
# JAR) não entra. Com macro (`vbaProject.bin`), também não.
_OOXML = (
    (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
)
# MP4/3GP/M4A: a caixa `ftyp` e a "marca" logo depois. HEIC/AVIF usam a mesma
# caixa e não estão na lista do WhatsApp.
_MARCAS_AUDIO = (b"M4A ", b"M4B ", b"M4P ")
_MARCAS_IMAGEM = (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif", b"avis")
# Texto puro com cara de página/marcação não é "texto": nunca vira documento.
_MARCACAO = (
    b"<html",
    b"<!doctype",
    b"<script",
    b"<svg",
    b"<?xml",
    b"<iframe",
    b"<body",
    b"<object",
    b"<embed",
    b"<meta",
    b"<a ",
)
# Quanto se lê do começo do arquivo para reconhecer o tipo.
_CABECA = 8192

# ── O download (§5.5 itens 2–4) ────────────────────────────────────────────
HOSTS_PERMITIDOS = frozenset({"lookaside.fbsbx.com", "graph.facebook.com"})
SUFIXOS_PERMITIDOS = (".fbcdn.net",)
MAX_SALTOS = 3
# A documentação de download da mídia usa `Bearer` (a da Graph aceita os dois).
# [confirmar] no ensaio se a CDN exige User-Agent (há relatos de página de
# erro em HTML sem ele — o tipo pela assinatura recusaria o HTML).
USER_AGENT = "DaVinci-Atendimento/1.0"
_PEDACO = 64 * 1024

# ── O relógio (§5.5 item 7) ────────────────────────────────────────────────
BACKOFF_S = (60, 300, 1800, 7200, 43200)
PRAZO_TENTAR = timedelta(days=30)
PRAZO_BAIXANDO = timedelta(minutes=10)
LIMITE_ENFILEIRAR = 50
JOB = "atendimento_zap_baixar_midia"

# ── Os estados (o CHECK de `atendimento_zap_midias.status`) ────────────────
M_PENDENTE = "pendente"
M_BAIXANDO = "baixando"
M_BAIXADA = "baixada"
M_GRANDE = "grande_demais"
M_FALHOU = "falhou"
M_EXPIRADA = "expirada"
M_APAGADA = "apagada"
# O que `baixar` devolve além dos estados: não era a vez (outra rodada pegou,
# o backoff não venceu, já terminou), a leitura desligada, o id que não existe.
R_OCUPADA = "ocupada"
R_DESLIGADO = "desligado"
R_NAO_ENCONTRADA = "nao_encontrada"

ERRO_TIPO = "tipo de arquivo não aceito"
ERRO_SHA = "o arquivo chegou diferente do que a Meta disse (sha256)"
ERRO_HOST = "endereço de download fora da lista da Meta"
ERRO_SALTOS = "redirecionamentos demais no download da Meta"
ERRO_EXPIRADA = "a mídia expirou antes de ser baixada"
ERRO_SEM_TOKEN = "sem credencial da Meta (token): o administrador precisa trocar o token"  # noqa: S105
# Conta com estes status não fala com a Meta (a mesma régua do envio).
_CONTA_SEM_CREDENCIAL = ("token_invalido", "sem_permissao")


def _agora() -> datetime:
    return datetime.now(UTC)


def leitura_ligada() -> bool:
    """Baixar é LER: as mesmas duas chaves do processador (nascem desligadas)."""
    s = get_settings()
    return bool(s.atendimento_leitura_ativa and s.atendimento_zap_leitura_ativa)


def teto_bytes() -> int:
    return int(get_settings().atendimento_zap_midia_max_mb) * 1024 * 1024


def url_da_midia(midia_id: UUID | str) -> str:
    """O endereço RELATIVO da cópia (o anexo e o `<img>` da tela)."""
    return f"{ROTA_MIDIA}/{midia_id}"


# ═══════════════════════ as réguas (puras) ═══════════════════════════════


def _mime_puro(valor: object) -> str | None:
    """'audio/ogg; codecs=opus' → 'audio/ogg'."""
    if not isinstance(valor, str):
        return None
    puro = valor.split(";", 1)[0].strip().lower()
    return puro or None


def especie(mime: str | None) -> str | None:
    """imagem / audio / video / documento — ou None (fora da lista)."""
    item = TIPOS_ACEITOS.get(mime or "")
    return item[1] if item else None


def eh_inline(mime: str | None) -> bool:
    """A rota entrega `inline` (o navegador mostra) só imagem, áudio e vídeo da lista."""
    return especie(mime) in ESPECIES_INLINE


def _texto_puro(cabeca: bytes) -> bool:
    """Texto de verdade: sem NUL, sem controle (fora tab/quebra), sem marcação."""
    if not cabeca or b"\x00" in cabeca:
        return False
    if any(b < 0x20 and b not in (0x09, 0x0A, 0x0C, 0x0D) for b in cabeca):
        return False
    minusculo = cabeca.lower()
    if minusculo.lstrip().startswith(b"<"):
        return False
    return not any(m in minusculo for m in _MARCACAO)


def _ooxml(fonte: bytes | Path) -> str | None:
    """DOCX/XLSX/PPTX pela parte principal do ZIP; ZIP comum ou com macro → None."""
    try:
        with zipfile.ZipFile(fonte if isinstance(fonte, Path) else io.BytesIO(fonte)) as z:
            nomes = set(z.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if "[Content_Types].xml" not in nomes or any(n.endswith("vbaProject.bin") for n in nomes):
        return None
    for parte, mime in _OOXML:
        if parte in nomes:
            return mime
    return None


def detectar_tipo(conteudo: bytes | Path, *, declarado: str | None = None) -> str | None:
    """O tipo de VERDADE pelos primeiros bytes (a "assinatura") — None = não aceito. PURA.

    O `declarado` (o `mime_type` da Meta, que é o do remetente) NÃO manda:
    só desempata o que a assinatura não separa (DOC × XLS × PPT antigos) e
    libera o texto puro (que não tem assinatura) quando diz `text/plain`.
    """
    if isinstance(conteudo, Path):
        with conteudo.open("rb") as f:
            cabeca = f.read(_CABECA)
    else:
        cabeca = bytes(conteudo[:_CABECA])
    if not cabeca:
        return None
    decl = _mime_puro(declarado)
    if cabeca.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if cabeca.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if cabeca[:4] == b"RIFF" and cabeca[8:12] == b"WEBP":
        return "image/webp"
    if cabeca[4:8] == b"ftyp":
        marca = cabeca[8:12]
        if marca in _MARCAS_AUDIO:
            return "audio/mp4"
        if marca[:3] in (b"3gp", b"3g2"):
            return "video/3gpp"
        if marca in _MARCAS_IMAGEM:
            return None
        return "video/mp4"
    if cabeca.startswith(b"OggS"):
        return "audio/ogg"
    if cabeca.startswith(b"#!AMR"):
        return "audio/amr"
    if cabeca.startswith(b"ID3"):
        return "audio/mpeg"
    if len(cabeca) >= 2 and cabeca[0] == 0xFF:
        # Quadro de áudio sem cabeçalho: ADTS (AAC, camada 00) ou MPEG (MP3).
        if cabeca[1] & 0xF6 == 0xF0:
            return "audio/aac"
        if cabeca[1] & 0xE0 == 0xE0 and cabeca[1] & 0x06:
            return "audio/mpeg"
    if cabeca.startswith(b"%PDF-"):
        return "application/pdf"
    if cabeca.startswith(_OLE2):
        return decl if decl in _OLE2_DECLARADOS else None
    if cabeca.startswith(b"PK\x03\x04"):
        return _ooxml(conteudo)
    if decl == "text/plain" and _texto_puro(cabeca):
        return "text/plain"
    return None


def host_permitido(url: str) -> bool:
    """O Bearer pode ir para este endereço? https, porta 443, sem usuário, host da lista. PURA."""
    try:
        u = httpx.URL(url or "")
    except (httpx.InvalidURL, TypeError, ValueError):
        return False
    if u.scheme != "https" or u.userinfo or u.port not in (None, 443):
        return False
    host = (u.host or "").lower().rstrip(".")
    if host in HOSTS_PERMITIDOS:
        return True
    return any(host.endswith(s) and len(host) > len(s) for s in SUFIXOS_PERMITIDOS)


_HEX64 = re.compile(r"[0-9a-fA-F]{64}")


def sha256_confere(esperado: str | None, digest: bytes) -> bool | None:
    """O `sha256` da Meta bate com o arquivo? Em hex OU base64. None = formato desconhecido.

    [confirmar] o formato (§14.3): a documentação mostra hex; payloads reais
    já vieram em base64. Os dois valem; o que não é nenhum dos dois não
    recusa o arquivo (fica no log).
    """
    e = (esperado or "").strip()
    if not e:
        return None
    if _HEX64.fullmatch(e):
        return hmac.compare_digest(e.lower(), digest.hex())
    try:
        bruto = base64.b64decode(e, validate=True)
    except (binascii.Error, ValueError):
        try:
            bruto = base64.urlsafe_b64decode(e)
        except (binascii.Error, ValueError):
            return None
    return hmac.compare_digest(bruto, digest) if len(bruto) == 32 else None


def _raiz() -> Path:
    return Path(get_settings().uploads_dir)


def caminho_relativo(
    numero_id: UUID | str, quando: datetime, midia_id: UUID | str, ext: str
) -> str:
    """`atendimento/zap/<numero>/<aaaa>/<mm>/<uuid>.<ext>` — gerado AQUI, sem nada do cliente."""
    q = quando.astimezone(UTC) if quando.tzinfo else quando.replace(tzinfo=UTC)
    return f"{PASTA}/{numero_id}/{q:%Y}/{q:%m}/{midia_id}{ext}"


def caminho_absoluto(rel: str | None) -> Path | None:
    """O arquivo da cópia no disco — ou None se o caminho gravado sair da pasta do Zap.

    Cinto contra path traversal: o caminho é gerado aqui, mas a rota relê do
    banco e confere de novo (um UPDATE errado não serve `/etc/passwd`).
    """
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        return None
    if ".." in Path(rel).parts or not rel.startswith(f"{PASTA}/"):
        return None
    base = (_raiz() / PASTA).resolve()
    alvo = (_raiz() / rel).resolve()
    if base not in alvo.parents:
        return None
    return _raiz() / rel


def remover_arquivo(rel: str | None) -> None:
    """Apaga a cópia (se o caminho é da pasta do Zap). Nunca levanta."""
    alvo = caminho_absoluto(rel)
    if alvo is None:
        return
    try:
        alvo.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("zap_midia_apagar_arquivo_falhou", err=type(exc).__name__)


_RE_ASCII = re.compile(r"[^A-Za-z0-9._-]+")
_ROTULO = {"imagem": "foto", "audio": "audio", "video": "video", "documento": "documento"}


def nome_para_baixar(midia: AtendimentoZapMidia) -> str:
    """O nome que a rota manda: o do cliente (sem caminho/controle) com a extensão de VERDADE."""
    mime = midia.mime_detectado or ""
    ext = TIPOS_ACEITOS.get(mime, ("", ""))[0]
    bruto = (midia.nome_original or "").replace("\\", "/").rsplit("/", 1)[-1]
    limpo = "".join(c for c in bruto if c.isprintable()).strip(" .")
    base = limpo.rsplit(".", 1)[0] if "." in limpo else limpo
    if not base:
        base = f"{_ROTULO.get(especie(mime) or '', 'arquivo')}-{str(midia.id)[:8]}"
    return f"{base[:120]}{ext}"


def content_disposition(tipo: str, nome: str) -> str:
    """`inline`/`attachment` com o nome SEGURO: o ASCII sem aspas e o UTF-8 percent-encoded.

    Nada de CR/LF/aspas cru no cabeçalho (o nome é do cliente).
    """
    ascii_ = _RE_ASCII.sub("_", nome).strip("._") or "arquivo"
    return f"{tipo}; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nome, safe='')}"


def espera(tentativas: int) -> timedelta:
    """O backoff da tentativa n (1 → 1 min, 2 → 5 min … 5+ → 12 h)."""
    return timedelta(seconds=BACKOFF_S[min(max(tentativas, 1), len(BACKOFF_S)) - 1])


def _prazo_final(midia: AtendimentoZapMidia, agora: datetime) -> datetime:
    """Até quando se tenta: 30 dias do REGISTRO (a mídia que chegou)."""
    return (midia.created_at or agora) + PRAZO_TENTAR


# ═══════════════════════ o banco ═════════════════════════════════════════


def _pegavel():
    """O predicado da mídia que pode ser pega AGORA (a fila do baixador)."""
    m = AtendimentoZapMidia
    return and_(
        m.direcao == "entrada",
        m.media_id.is_not(None),
        or_(
            and_(m.status == M_PENDENTE, or_(m.proxima_em.is_(None), m.proxima_em <= func.now())),
            # O worker caiu no meio: o prazo da `baixando` passou.
            and_(m.status == M_BAIXANDO, m.proxima_em <= func.now()),
        ),
    )


async def _pegar(session: AsyncSession, midia_id: UUID) -> AtendimentoZapMidia | None:
    """UPDATE atômico `pendente` → `baixando` (+1 tentativa, prazo de 10 min). Commita."""
    pegou = (
        await session.execute(
            update(AtendimentoZapMidia)
            .where(AtendimentoZapMidia.id == midia_id, _pegavel())
            .values(
                status=M_BAIXANDO,
                tentativas=AtendimentoZapMidia.tentativas + 1,
                proxima_em=func.now() + PRAZO_BAIXANDO,
            )
            .returning(AtendimentoZapMidia.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    await session.commit()
    if pegou is None:
        return None
    return await session.get(AtendimentoZapMidia, midia_id, populate_existing=True)


async def _anexo(session: AsyncSession, midia: AtendimentoZapMidia, **campos: Any) -> None:
    """O anexo do balão que aponta para esta mídia (`midia_id`) ganha o estado novo.

    Trava SÓ as linhas das mensagens (nunca a conversa): o baixador não
    espera o "Enviar" de ninguém, e ninguém que trava a conversa espera por
    ele. A mesma mídia pode estar em mais de uma mensagem da conversa
    (`zap_entrada._registrar_midia`, o `media_id` repetido): todas mudam. A
    `url` só fica com a mídia `baixada`.
    """
    if midia.mensagem_id is None:
        return
    dona = await session.get(AtendimentoMensagem, midia.mensagem_id)
    if dona is None:
        return
    alvo = str(midia.id)
    linhas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == dona.conversa_id,
                    AtendimentoMensagem.anexos.contains([{"midia_id": alvo}]),
                )
                .order_by(AtendimentoMensagem.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for msg in linhas:
        novos = []
        for a in msg.anexos or []:
            if isinstance(a, dict) and a.get("midia_id") == alvo:
                a = {k: v for k, v in a.items() if k != "url"}
                a.update({k: v for k, v in campos.items() if v is not None})
            novos.append(a)
        msg.anexos = novos


async def _apagada_no_meio(session: AsyncSession, midia: AtendimentoZapMidia) -> bool:
    """A mídia foi APAGADA (LGPD ou retenção) enquanto o baixador falava com a Meta?

    Relê o status COM trava (`FOR UPDATE`, até o commit de quem chamou). Quem
    chama trava as MENSAGENS antes (`_anexo`), na mesma ordem do
    `zap_cadastro.apagar_contato` (mensagens → mídias): sem deadlock. Apagada
    → `rollback` (nada volta: nem o arquivo, nem a URL no balão). Revisão de
    27/10/2026: o download que terminava depois da exclusão ressuscitava a foto.
    """
    atual = await session.scalar(
        select(AtendimentoZapMidia.status)
        .where(AtendimentoZapMidia.id == midia.id)
        .with_for_update()
    )
    if atual != M_APAGADA:
        return False
    mid = midia.id
    await session.rollback()
    logger.warning("zap_midia_apagada_no_meio", midia=str(mid))
    return True


async def _terminar(
    session: AsyncSession, midia: AtendimentoZapMidia, status: str, erro: str | None
) -> str:
    """Estado FINAL (`falhou`, `grande_demais`, `expirada`): sai da fila. Commita."""
    await _anexo(session, midia, status=status)
    if await _apagada_no_meio(session, midia):
        return M_APAGADA
    midia.status = status
    midia.erro = (erro or "")[:200] or None
    midia.proxima_em = None
    await session.commit()
    logger.info("zap_midia_fim", midia=str(midia.id), status=status)
    return status


async def _adiar(
    session: AsyncSession,
    midia: AtendimentoZapMidia,
    agora: datetime,
    erro: str,
    *,
    nao_existe: bool = False,
) -> str:
    """De volta a `pendente`, com o backoff — ou `expirada`, se a Meta já apagou. Commita."""
    if nao_existe and midia.expira_meta_em is not None and agora > midia.expira_meta_em:
        return await _terminar(session, midia, M_EXPIRADA, ERRO_EXPIRADA)
    if await _apagada_no_meio(session, midia):
        return M_APAGADA
    midia.status = M_PENDENTE
    midia.erro = erro[:200]
    midia.proxima_em = agora + espera(midia.tentativas)
    await session.commit()
    logger.warning(
        "zap_midia_adiada", midia=str(midia.id), tentativas=midia.tentativas, motivo=erro[:80]
    )
    return M_PENDENTE


async def _guardada(
    session: AsyncSession,
    midia: AtendimentoZapMidia,
    *,
    rel: str,
    mime: str,
    tamanho: int,
    sha: str,
    agora: datetime,
) -> str:
    """A cópia no disco: `baixada` + o anexo com a URL da rota. Commita.

    Apagada no meio do download (`_apagada_no_meio`) → nada grava e devolve
    `apagada` (quem chama apaga o arquivo).
    """
    await _anexo(
        session,
        midia,
        status=M_BAIXADA,
        url=url_da_midia(midia.id),
        mime=mime,
        tamanho=tamanho,
    )
    if await _apagada_no_meio(session, midia):
        return M_APAGADA
    midia.status = M_BAIXADA
    midia.caminho = rel
    midia.mime_detectado = mime
    midia.tamanho = tamanho
    midia.sha256_local = sha
    midia.baixada_em = agora
    midia.erro = None
    midia.proxima_em = None
    await session.commit()
    logger.info("zap_midia_baixada", midia=str(midia.id), mime=mime, bytes=tamanho)
    return M_BAIXADA


def _token(conta: AtendimentoZapConta | None) -> str | None:
    """O token decifrado (só na memória) — None = sem credencial. Nunca levanta."""
    if conta is None or conta.status in _CONTA_SEM_CREDENCIAL:
        return None
    try:
        return zap_contas.token_da_conta(conta)
    except Exception as exc:  # noqa: BLE001 — cifra trocada, token vazio: sem credencial
        logger.warning("zap_midia_token_indisponivel", conta=str(conta.id), err=type(exc).__name__)
        return None


# ═══════════════════════ o download ══════════════════════════════════════


class _Falha(Exception):  # noqa: N818 — português, como `ZapApiErro`
    """O que deu errado nesta tentativa: o estado seguinte e a frase (sem token, sem URL)."""

    def __init__(self, status: str, erro: str, *, nao_existe: bool = False) -> None:
        self.status = status
        self.erro = erro
        self.nao_existe = nao_existe
        super().__init__(erro)


class _LinkVencido(Exception):  # noqa: N818
    """404/410 na CDN: a URL (5 min) venceu — pede outra à Graph."""


@dataclass
class _Baixado:
    temp: Path
    tamanho: int
    digest: bytes
    sha_meta: str | None = None
    mime_meta: str | None = None


def _novo_temp(midia_id: UUID) -> Path:
    pasta = _raiz() / PASTA_TEMP
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / f"{midia_id}-{uuid4().hex}{SUFIXO_TEMP}"


async def _info_da_midia(media_id: str, token: str, http: httpx.AsyncClient) -> dict[str, Any]:
    """`GET /{media_id}` → a URL da CDN e o que a Meta diz do arquivo."""
    try:
        corpo = await zap_api.ler_midia(media_id, token, cliente=http)
    except zap_api.ZapApiErro as exc:
        # [confirmar] o erro da mídia que a Meta já apagou: 404, ou 100 (o
        # objeto não existe). Só vale como "não existe" depois dos 7 dias.
        nao_existe = exc.http_status == 404 or exc.code == 100
        raise _Falha(
            M_PENDENTE, f"a Meta não entregou o link da mídia: {exc}", nao_existe=nao_existe
        ) from None
    url = corpo.get("url")
    if not isinstance(url, str) or not url:
        raise _Falha(M_PENDENTE, "a Meta não mandou o link da mídia")
    return corpo


async def _baixar_da_cdn(
    midia_id: UUID, url: str, token: str, http: httpx.AsyncClient, teto: int
) -> _Baixado:
    """O download em streaming, com o Bearer só para host da lista, a cada salto."""
    atual = url
    for _ in range(MAX_SALTOS + 1):
        if not host_permitido(atual):
            raise _Falha(M_PENDENTE, ERRO_HOST)
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
        try:
            async with http.stream("GET", atual, headers=headers) as resp:
                if resp.is_redirect:
                    atual = str(resp.url.join(resp.headers["location"]))
                    continue
                if resp.status_code in (404, 410):
                    raise _LinkVencido
                if resp.status_code >= 400:
                    raise _Falha(
                        M_PENDENTE, f"a Meta não entregou o arquivo (HTTP {resp.status_code})"
                    )
                declarado = resp.headers.get("content-length", "")
                if declarado.isdigit() and int(declarado) > teto:
                    raise _Falha(M_GRANDE, f"arquivo acima de {teto // (1024 * 1024)} MB")
                temp = _novo_temp(midia_id)
                soma = hashlib.sha256()
                n = 0
                try:
                    with temp.open("wb") as f:
                        async for pedaco in resp.aiter_bytes(_PEDACO):
                            n += len(pedaco)
                            if n > teto:
                                raise _Falha(
                                    M_GRANDE, f"arquivo acima de {teto // (1024 * 1024)} MB"
                                )
                            soma.update(pedaco)
                            f.write(pedaco)
                except BaseException:
                    temp.unlink(missing_ok=True)
                    raise
                return _Baixado(temp=temp, tamanho=n, digest=soma.digest())
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Só o tipo: a mensagem do httpx traz a URL.
            raise _Falha(M_PENDENTE, f"a Meta não respondeu ({type(exc).__name__})") from None
        except httpx.InvalidURL:
            raise _Falha(M_PENDENTE, ERRO_HOST) from None
    raise _Falha(M_PENDENTE, ERRO_SALTOS)


async def _buscar(midia: AtendimentoZapMidia, token: str, teto: int) -> _Baixado:
    """Graph → CDN; o link vencido (404) pede outro NA HORA, uma vez."""
    async with zap_api.novo_cliente() as http:
        for tentativa in range(2):
            info = await _info_da_midia(str(midia.media_id), token, http)
            tamanho = zap_api._inteiro(info.get("file_size"))  # noqa: SLF001 — o mesmo leitor
            if tamanho is not None and tamanho > teto:
                raise _Falha(M_GRANDE, f"arquivo acima de {teto // (1024 * 1024)} MB")
            try:
                baixado = await _baixar_da_cdn(midia.id, str(info["url"]), token, http, teto)
            except _LinkVencido:
                if tentativa == 0:
                    continue
                raise _Falha(M_PENDENTE, "o link da Meta venceu de novo (404)") from None
            sha = info.get("sha256")
            baixado.sha_meta = sha if isinstance(sha, str) else None
            baixado.mime_meta = _mime_puro(info.get("mime_type"))
            return baixado
    raise _Falha(M_PENDENTE, "o link da Meta venceu de novo (404)")  # pragma: no cover


async def _guardar_arquivo(
    session: AsyncSession,
    midia: AtendimentoZapMidia,
    temp: Path,
    *,
    digest: bytes,
    tamanho: int,
    declarado: str | None,
    sha_meta: str | None,
    agora: datetime,
) -> str:
    """Do temporário à cópia: sha256, tipo pela assinatura, caminho final, `baixada`.

    O temporário SEMPRE some (vire cópia ou não).
    """
    try:
        confere = sha256_confere(sha_meta or midia.sha256_meta, digest)
        if confere is False:
            return await _terminar(session, midia, M_FALHOU, ERRO_SHA)
        if confere is None:
            logger.warning("zap_midia_sha_sem_conferencia", midia=str(midia.id))
        mime = detectar_tipo(temp, declarado=declarado or midia.mime)
        if mime is None:
            logger.warning("zap_midia_tipo_recusado", midia=str(midia.id))
            return await _terminar(session, midia, M_FALHOU, ERRO_TIPO)
        rel = caminho_relativo(
            midia.numero_ref, midia.created_at or agora, midia.id, TIPOS_ACEITOS[mime][0]
        )
        final = _raiz() / rel
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, final)
        try:
            estado = await _guardada(
                session, midia, rel=rel, mime=mime, tamanho=tamanho, sha=digest.hex(), agora=agora
            )
        except BaseException:
            # O banco não gravou: a cópia não fica órfã (a próxima rodada baixa de novo).
            final.unlink(missing_ok=True)
            raise
        if estado != M_BAIXADA:
            # Apagada no meio (LGPD/retenção): a cópia não volta para o disco.
            final.unlink(missing_ok=True)
        return estado
    finally:
        temp.unlink(missing_ok=True)


async def baixar(session: AsyncSession, midia_id: UUID, *, agora: datetime | None = None) -> str:
    """UMA tentativa de baixar a mídia recebida. Devolve o estado em que ela ficou.

    `baixada` · `pendente` (tenta de novo no backoff) · `falhou` · `grande_demais`
    · `expirada` — ou `ocupada` (não era a vez: outra rodada pegou, o backoff não
    venceu, já terminou), `desligado` (as chaves de leitura) e `nao_encontrada`.
    Commita (a tentativa é marcada ANTES de falar com a Meta: nenhuma trava de
    banco fica aberta durante o download).
    """
    if not leitura_ligada():
        return R_DESLIGADO
    midia = await _pegar(session, midia_id)
    if midia is None:
        existe = await session.scalar(
            select(AtendimentoZapMidia.id).where(AtendimentoZapMidia.id == midia_id)
        )
        await session.rollback()
        return R_OCUPADA if existe is not None else R_NAO_ENCONTRADA
    agora = agora or _agora()
    if agora >= _prazo_final(midia, agora):
        return await _terminar(session, midia, M_EXPIRADA, ERRO_EXPIRADA)
    numero = await session.get(AtendimentoZapNumero, midia.numero_ref)
    conta = await session.get(AtendimentoZapConta, numero.conta_ref) if numero else None
    token = _token(conta)
    if token is None:
        return await _adiar(session, midia, agora, ERRO_SEM_TOKEN)
    await session.commit()  # nada aberto durante o HTTP
    try:
        baixado = await _buscar(midia, token, teto_bytes())
    except _Falha as f:
        if f.status == M_PENDENTE:
            return await _adiar(session, midia, agora, f.erro, nao_existe=f.nao_existe)
        return await _terminar(session, midia, f.status, f.erro)
    return await _guardar_arquivo(
        session,
        midia,
        baixado.temp,
        digest=baixado.digest,
        tamanho=baixado.tamanho,
        declarado=baixado.mime_meta,
        sha_meta=baixado.sha_meta,
        agora=agora,
    )


async def baixar_job(midia_id: str) -> str | None:
    """O job do worker (`atendimento_zap_baixar_midia`): uma sessão própria, uma mídia."""
    try:
        uid = UUID(str(midia_id))
    except ValueError:
        return None
    import app.db as _db

    async with _db.SessionLocal() as session:
        return await baixar(session, uid)


async def enfileirar_pendentes(session: AsyncSession, *, limite: int = LIMITE_ENFILEIRAR) -> int:
    """O `varrer` (e a rodada do job, logo depois do commit das mensagens): um job por mídia.

    O `_job_id` leva a TENTATIVA: enquanto o job daquela tentativa existe, o
    arq não enfileira de novo; a tentativa seguinte (depois do backoff) é
    outro id. Nunca levanta (enfileirar é melhor esforço: o cron de 1 min
    é a rede).
    """
    linhas = (
        await session.execute(
            select(AtendimentoZapMidia.id, AtendimentoZapMidia.tentativas)
            .where(_pegavel())
            .order_by(
                AtendimentoZapMidia.proxima_em.asc().nulls_first(), AtendimentoZapMidia.created_at
            )
            .limit(limite)
        )
    ).all()
    await session.rollback()
    if not linhas:
        return 0
    try:
        pool = await get_arq_pool()
    except Exception as exc:  # noqa: BLE001 — o cron tenta de novo
        logger.warning("zap_midia_enfileirar_falhou", err=type(exc).__name__)
        return 0
    n = 0
    for midia_id, tentativas in linhas:
        try:
            await pool.enqueue_job(JOB, str(midia_id), _job_id=f"zap-midia:{midia_id}:{tentativas}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("zap_midia_enfileirar_falhou", err=type(exc).__name__)
            break
        n += 1
    return n


# ═══════════════════════ a cópia sem a Meta ══════════════════════════════


@dataclass(frozen=True)
class CopiaDeSaida:
    """A foto que a pessoa mandou, já no disco (o que sobe para a Meta são estes bytes)."""

    midia_id: UUID
    caminho: str
    nome: str
    mime: str
    dados: bytes = field(repr=False)

    @property
    def url(self) -> str:
        return url_da_midia(self.midia_id)


async def guardar_saida(
    session: AsyncSession,
    *,
    numero_id: UUID,
    mensagem_id: UUID,
    nome: str,
    dados: bytes,
    mime: str,
    agora: datetime | None = None,
) -> CopiaDeSaida:
    """A nossa cópia da foto de SAÍDA (§6.8): o arquivo + `atendimento_zap_midias` (`saida`).

    Antes de falar com a Meta: o balão mostra a foto (pela rota) mesmo
    enquanto ela sai, e mesmo se não sair. Não commita (vai junto da linha
    em voo); se a gravação falhar, o arquivo não fica órfão.
    """
    agora = agora or _agora()
    real = detectar_tipo(dados)
    if real is None or especie(real) != "imagem":
        raise ValueError("a foto de saída não é imagem da lista")
    midia_id = uuid4()
    rel = caminho_relativo(numero_id, agora, midia_id, TIPOS_ACEITOS[real][0])
    final = _raiz() / rel
    final.parent.mkdir(parents=True, exist_ok=True)
    temp = _novo_temp(midia_id)
    temp.write_bytes(dados)
    os.replace(temp, final)
    try:
        session.add(
            AtendimentoZapMidia(
                id=midia_id,
                numero_ref=numero_id,
                mensagem_id=mensagem_id,
                direcao="saida",
                mime=mime,
                mime_detectado=real,
                tamanho=len(dados),
                nome_original=(nome or "")[:255] or None,
                caminho=rel,
                sha256_local=hashlib.sha256(dados).hexdigest(),
                status=M_BAIXADA,
                baixada_em=agora,
                apagar_em=agora
                + timedelta(days=get_settings().atendimento_zap_retencao_midia_dias),
            )
        )
        await session.flush()
    except BaseException:
        final.unlink(missing_ok=True)
        raise
    return CopiaDeSaida(midia_id=midia_id, caminho=rel, nome=nome, mime=real, dados=dados)


async def guardar_local(session: AsyncSession, midia_id: UUID, origem: Path) -> str:
    """SÓ o script local: a cópia vem de um arquivo do computador, não da Meta.

    É o "download" do número FICTÍCIO (a Onda 1 não tem chave real: a Meta
    nunca é chamada), com as MESMAS conferências do baixador (teto, tipo
    pela assinatura, caminho gerado aqui). Commita.
    """
    midia = await session.get(AtendimentoZapMidia, midia_id, populate_existing=True)
    if midia is None:
        return R_NAO_ENCONTRADA
    agora = _agora()
    teto = teto_bytes()
    if origem.stat().st_size > teto:
        return await _terminar(
            session, midia, M_GRANDE, f"arquivo acima de {teto // (1024 * 1024)} MB"
        )
    dados = origem.read_bytes()
    temp = _novo_temp(midia.id)
    temp.write_bytes(dados)
    return await _guardar_arquivo(
        session,
        midia,
        temp,
        digest=hashlib.sha256(dados).digest(),
        tamanho=len(dados),
        declarado=midia.mime,
        sha_meta=hashlib.sha256(dados).hexdigest(),
        agora=agora,
    )


# ═══════════════════════ a retenção (Onda 4) ═════════════════════════════


async def apagar_vencidas(
    session: AsyncSession, *, agora: datetime | None = None, limite: int = 500
) -> int:
    """Passado o `apagar_em` (180 dias): o arquivo some, o registro fica `apagada`. Commita.

    Para o cron `atendimento_zap_limpeza` (Onda 4); aqui só a regra. O balão
    diz "mídia apagada pela política de retenção".
    """
    agora = agora or _agora()
    vencidas = (
        (
            await session.execute(
                select(AtendimentoZapMidia)
                .where(
                    AtendimentoZapMidia.apagar_em.is_not(None),
                    AtendimentoZapMidia.apagar_em <= agora,
                    AtendimentoZapMidia.status != M_APAGADA,
                )
                .order_by(AtendimentoZapMidia.apagar_em)
                .limit(limite)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for midia in vencidas:
        remover_arquivo(midia.caminho)
        midia.status = M_APAGADA
        midia.caminho = None
        midia.proxima_em = None
        await _anexo(session, midia, status=M_APAGADA)
    await session.commit()
    if vencidas:
        logger.info("zap_midia_retencao", apagadas=len(vencidas))
    return len(vencidas)
