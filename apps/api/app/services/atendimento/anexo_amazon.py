"""A foto (ou o PDF) que o comprador manda na Amazon, MOSTRADA na conversa (23/11/2026).

O pedido do Eduardo (print de 23/11, conversa da Poofy): "a amazon as imagens
vem como image.jpg mas não ela em si, tem que ver que o da amazon é diferente
e por e-mail né, tem que fazer aparecer as imagens em atendimento".

POR QUE VINHA SÓ "image.jpg". A Amazon não tem API de mensagem: a conversa
chega pelo e-mail do Seller Central (`amazon_email`). A foto vem DENTRO do
e-mail, como parte MIME `attachment` em base64 — conferido em 23/11/2026, só
leitura, nos 15 e-mails com anexo da caixa desde 10/2026: JPEG, PNG e PDF de
18 KB a 3,6 MB, nenhum link para baixar depois, e às vezes com o tipo
declarado errado ("image/jpg"). O leitor guardava SÓ o nome, de propósito
("baixar arquivo de comprador é escopo perdido em v1"): a tela não tinha o
que mostrar além do nome.

Diferente do ML (`anexo_ml`), não há API para buscar o arquivo na hora: o
único lugar dele é o próprio e-mail, e reler a caixa a cada miniatura gastaria
a cota de IMAP do Gmail — a mesma do leitor. Então a cópia fica no disco,
como a mídia do Zap (`zap_midia`):

    `uploads_dir/atendimento/amazon/<2 primeiros>/<sha256>.<ext>`

- O NOME no disco é o sha256 dos bytes + a extensão do tipo de VERDADE, gerado
  AQUI: nada do comprador no caminho (sem path traversal), e o mesmo e-mail
  relido (a rodada que caiu antes do commit, a recuperação dos antigos) grava
  o MESMO arquivo — sem cópia repetida nem órfã.
- SÓ FOTO E PDF (`TIPOS`): o tipo vale pela ASSINATURA dos bytes, nunca pelo
  que o e-mail declara. HEIC, DOC, TXT, HTML, SVG, executável: fica só o nome,
  como antes. Teto por arquivo (`MAX_BYTES`; a mensagem da Amazon vai até
  10 MB) e por e-mail (`MAX_POR_EMAIL`).
- O ANEXO DA MENSAGEM ganha `arquivo` (o nome no disco), `tipo` (o mime de
  verdade, como no ML: o agente vê "[com foto]" como vê no ML) e `tamanho`.
  Sem a cópia (fora da lista, acima do teto, disco cheio) fica o de antes,
  `{"tipo": "arquivo", "nome"}` — o e-mail NUNCA deixa de entrar por causa do
  anexo.
- A ROTA é a do anexo do ML (`GET /api/atendimento/conversas/{id}/anexos/
  {arquivo}`, `routers/atendimento.py`): quem lê a conversa (o escopo da
  equipe); o `arquivo` numa mensagem DESTA conversa (JSONB `@>`, sem nota
  interna); o formato conferido (`_RE_ARQUIVO`); o caminho dentro da pasta; o
  sha256 dos bytes lidos = o nome (arquivo trocado no disco não sai). `inline`
  só a imagem; PDF `attachment` (baixa, nunca abre aqui); `nosniff`,
  `sandbox` e `private` (os cabeçalhos do ML, `anexo_ml.cabecalhos`).
- RETENÇÃO: nenhuma por enquanto (o Zap apaga em 180 dias; para a Amazon, a
  decisão é do Eduardo). O volume é pequeno: 19 arquivos, ~15 MB em 2 meses.
- LOG: só contagens e códigos. Nunca o nome do arquivo, o texto, o endereço.

OS QUE JÁ TINHAM CHEGADO (só o nome): `recuperar_antigos` relê cada e-mail
pelo UID guardado na mensagem (`payload.imap_uid`) como o leitor lê — EXAMINE
+ BODY.PEEK[], nunca marca nada —, confere que é o MESMO e-mail (Message-ID =
`externo_id`) com os MESMOS anexos (os nomes, na ordem), e só então completa
o anexo. Roda à mão, com o OK do Eduardo (`scripts/atendimento_amazon_anexos`);
sem `aplicar`, só conta.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import anexo_ml
from app.services.atendimento.constantes import AUTOR_CLIENTE, CANAL_EMAIL, ORIGEM_NOTA

logger = structlog.get_logger()

PLATAFORMA = "amazon"
CANAL = CANAL_EMAIL
PASTA = "atendimento/amazon"
# O tipo de VERDADE (assinatura) → a extensão no disco. Só o que a tela mostra
# (foto) ou baixa (PDF) — o que a Amazon deixa o comprador anexar.
TIPOS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
# A mensagem da Amazon vai até 10 MB (ajuda do Seller Central); folga para o base64.
MAX_BYTES = 15 * 1024 * 1024
MAX_POR_EMAIL = 10
# "<sha256>.<ext>": o nome que ESTE módulo gera (a tela usa o mesmo regex).
_RE_ARQUIVO = re.compile(r"^[0-9a-f]{64}\.(?:jpg|png|gif|webp|pdf)$")
SUFIXO_TEMP = ".parte"

# O que `recuperar_antigos` conta (só números: nada do comprador).
R_MENSAGENS = "mensagens"
R_RECUPERADAS = "recuperadas"
R_ANEXOS = "anexos_guardados"
R_SO_NOME = "anexos_so_nome"
R_SEM_UID = "sem_uid"
R_NAO_ACHADO = "email_nao_achado"
R_OUTRO_EMAIL = "outro_email"
R_ANEXOS_DIFERENTES = "anexos_diferentes"
R_DISCO = "disco_falhou"


# ═══════════════════════ as réguas (puras) ═══════════════════════════════


@dataclass(frozen=True)
class Copia:
    """Os bytes de um anexo aceito, com o nome que terão no disco."""

    arquivo: str
    mime: str
    dados: bytes = field(repr=False)


def arquivo_valido(arquivo: Any) -> bool:
    """O `arquivo` tem a cara do nome que este módulo gera? PURA."""
    return isinstance(arquivo, str) and bool(_RE_ARQUIVO.match(arquivo))


def tipo_de_verdade(dados: bytes) -> str | None:
    """O tipo pela assinatura dos bytes, só os da lista (HEIC fica de fora). PURA."""
    mime = anexo_ml.tipo_pela_assinatura(dados[:64])
    return mime if mime in TIPOS else None


def so_o_nome(nome: str | None) -> dict:
    """O anexo como era antes de 23/11/2026: só o nome (sem cópia)."""
    return {"tipo": "arquivo", "nome": nome or "anexo"}


def preparar(nome: str | None, dados: bytes | None) -> tuple[dict, Copia | None]:
    """Um anexo do e-mail → (o anexo da mensagem, a cópia a gravar ou None). PURA."""
    if not isinstance(dados, bytes | bytearray) or not dados or len(dados) > MAX_BYTES:
        return so_o_nome(nome), None
    dados = bytes(dados)
    mime = tipo_de_verdade(dados)
    if mime is None:
        return so_o_nome(nome), None
    arquivo = f"{hashlib.sha256(dados).hexdigest()}{TIPOS[mime]}"
    # O nome como a leitura sempre gravou ("anexo" sem nome): é por ele que a
    # recuperação dos antigos confere que o e-mail relido é o mesmo.
    anexo = {
        "tipo": mime,
        "nome": so_o_nome(nome)["nome"],
        "arquivo": arquivo,
        "tamanho": len(dados),
    }
    return anexo, Copia(arquivo=arquivo, mime=mime, dados=dados)


def conversa_aceita(conversa: AtendimentoConversa) -> bool:
    """A conversa da Amazon por e-mail (a única com estes anexos)."""
    return conversa.plataforma == PLATAFORMA and conversa.canal == CANAL


# ═══════════════════════ o disco ═════════════════════════════════════════


def _raiz() -> Path:
    return Path(get_settings().uploads_dir)


def caminho(arquivo: str) -> Path | None:
    """Onde mora a cópia — ou None se o nome não é um dos nossos (sem traversal)."""
    if not arquivo_valido(arquivo):
        return None
    base = (_raiz() / PASTA).resolve()
    alvo = (_raiz() / PASTA / arquivo[:2] / arquivo).resolve()
    if base not in alvo.parents:
        return None
    return alvo


def _gravar(copia: Copia) -> bool:
    """Grava a cópia (se ainda não está lá). True = está no disco. Nunca levanta."""
    final = caminho(copia.arquivo)
    if final is None:
        return False
    try:
        if final.is_file() and final.stat().st_size == len(copia.dados):
            return True  # o mesmo e-mail relido: o mesmo arquivo
        final.parent.mkdir(parents=True, exist_ok=True)
        temp = final.parent / f".{uuid4().hex}{SUFIXO_TEMP}"
        try:
            temp.write_bytes(copia.dados)
            os.replace(temp, final)
        finally:
            temp.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning("atendimento_amazon_anexo_disco_falhou", err=type(exc).__name__)
        return False


def _gravar_todas(anexos: list[dict], copias: Sequence[Copia]) -> list[dict]:
    """Grava as cópias; o anexo cuja cópia não gravou volta a ser só o nome."""
    falhas = {c.arquivo for c in copias if not _gravar(c)}
    if not falhas:
        return list(anexos)
    return [
        so_o_nome(a.get("nome")) if isinstance(a, dict) and a.get("arquivo") in falhas else a
        for a in anexos
    ]


async def guardar(anexos: list[dict], copias: Sequence[Copia]) -> list[dict]:
    """O leitor: grava as cópias (numa thread) e devolve os anexos da mensagem.

    Antes do `gravar_mensagem`: o balão já nasce com a foto. Disco cheio ou
    sem permissão → o anexo fica só com o nome e o e-mail entra do mesmo jeito.
    """
    if not copias:
        return list(anexos)
    return await asyncio.to_thread(_gravar_todas, anexos, copias)


def ler(arquivo: str) -> anexo_ml.Baixado | None:
    """Os bytes da cópia para a rota — None se não existe ou não confere. Nunca levanta.

    Confere de novo o que o nome promete: o tipo pela assinatura bate com a
    extensão e o sha256 dos bytes é o próprio nome (arquivo trocado no disco
    não sai).
    """
    alvo = caminho(arquivo)
    if alvo is None:
        return None
    try:
        if not alvo.is_file() or alvo.stat().st_size > MAX_BYTES:
            return None
        dados = alvo.read_bytes()
    except OSError as exc:
        logger.warning("atendimento_amazon_anexo_leitura_falhou", err=type(exc).__name__)
        return None
    mime = tipo_de_verdade(dados)
    if mime is None or arquivo != f"{hashlib.sha256(dados).hexdigest()}{TIPOS[mime]}":
        logger.warning("atendimento_amazon_anexo_nao_confere")
        return None
    return anexo_ml.Baixado(conteudo=dados, mime=mime)


async def anexo_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, arquivo: str
) -> dict | None:
    """O anexo `arquivo` numa mensagem DESTA conversa (sem nota interna); None se não há."""
    if not conversa_aceita(conversa) or not arquivo_valido(arquivo):
        return None
    return await anexo_ml.anexo_da_conversa(session, conversa, arquivo)


# ═══════════════════════ os que já tinham chegado ════════════════════════


def _sem_copia(anexos: Any) -> bool:
    """A mensagem tem anexo que entrou só pelo nome (sem `arquivo`)?"""
    return isinstance(anexos, list) and any(
        isinstance(a, dict) and not a.get("arquivo") for a in anexos
    )


def _uid(payload: Any) -> int | None:
    valor = payload.get("imap_uid") if isinstance(payload, dict) else None
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor > 0 else None


def _nomes(anexos: Sequence[Any]) -> list[str | None]:
    return [a.get("nome") if isinstance(a, dict) else None for a in anexos]


def _ler_uids(uids: Sequence[int]) -> dict[int, bytes]:
    """Os e-mails crus destes UIDs — SÓ LEITURA, como o leitor (EXAMINE + BODY.PEEK[])."""
    from app.services.atendimento import amazon_email

    config = amazon_email.configuracao()
    if not config.pode_ler:
        raise amazon_email.ErroCaixa("caixa da Amazon não configurada")
    imap = amazon_email._abrir_imap(config)  # noqa: SLF001 — o mesmo ponto único do leitor
    try:
        imap.login(config.usuario, config.senha)
        tipo, _dados = imap.select(amazon_email._pasta_imap(config.pasta), readonly=True)  # noqa: SLF001
        if tipo != "OK":
            raise amazon_email.ErroCaixa(f"pasta não abriu ({tipo})")
        return dict(amazon_email._buscar(imap, sorted(set(uids))))  # noqa: SLF001
    finally:
        try:
            imap.logout()
        except Exception as exc:  # noqa: BLE001 — logout é melhor esforço
            logger.debug("atendimento_amazon_anexo_logout_falhou", err=type(exc).__name__)


async def recuperar_antigos(
    session: AsyncSession, *, aplicar: bool = False, limite: int = 200
) -> dict[str, Any]:
    """Os anexos da Amazon que entraram SÓ PELO NOME: relê o e-mail e guarda a cópia.

    `aplicar=False` (o padrão): lê a caixa e conta o que faria, sem gravar nada
    (nem disco, nem banco). `aplicar=True`: grava as cópias e troca os anexos,
    e commita. Só as mensagens do comprador da Amazon (canal e-mail) com o UID
    no payload; o e-mail relido tem de ser o MESMO (Message-ID) e trazer os
    MESMOS anexos (os nomes, na ordem) — senão a mensagem fica como está.
    Devolve só contagens.
    """
    resumo: dict[str, Any] = {
        "aplicar": aplicar,
        R_MENSAGENS: 0,
        R_RECUPERADAS: 0,
        R_ANEXOS: 0,
        R_SO_NOME: 0,
        R_SEM_UID: 0,
        R_NAO_ACHADO: 0,
        R_OUTRO_EMAIL: 0,
        R_ANEXOS_DIFERENTES: 0,
        R_DISCO: 0,
    }
    linhas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .join(
                    AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id
                )
                .where(
                    AtendimentoConversa.plataforma == PLATAFORMA,
                    AtendimentoConversa.canal == CANAL,
                    AtendimentoMensagem.autor == AUTOR_CLIENTE,
                    AtendimentoMensagem.origem != ORIGEM_NOTA,
                    AtendimentoMensagem.anexos != func.jsonb_build_array(),
                )
                .order_by(AtendimentoMensagem.created_at, AtendimentoMensagem.id)
            )
        )
        .scalars()
        .all()
    )
    alvos = [m for m in linhas if _sem_copia(m.anexos)][: max(1, limite)]
    resumo[R_MENSAGENS] = len(alvos)
    com_uid = [(m, _uid(m.payload)) for m in alvos]
    resumo[R_SEM_UID] = sum(1 for _m, u in com_uid if u is None)
    uids = [u for _m, u in com_uid if u is not None]
    if not uids:
        await session.rollback()
        return resumo
    from app.services.atendimento import amazon_email

    crus = await asyncio.to_thread(_ler_uids, uids)
    for m, uid in com_uid:
        if uid is None:
            continue
        bruto = crus.get(uid)
        if bruto is None:
            resumo[R_NAO_ACHADO] += 1
            continue
        em = amazon_email.interpretar_email(bruto, uid=uid)
        if em is None or em.message_id != m.externo_id:
            resumo[R_OUTRO_EMAIL] += 1
            continue
        if _nomes(em.anexos) != _nomes(m.anexos or []):
            resumo[R_ANEXOS_DIFERENTES] += 1
            continue
        novos = await guardar(em.anexos, em.copias) if aplicar else list(em.anexos)
        guardados = sum(1 for a in novos if isinstance(a, dict) and a.get("arquivo"))
        if aplicar and guardados < len(em.copias):
            resumo[R_DISCO] += len(em.copias) - guardados
        resumo[R_SO_NOME] += len(novos) - guardados
        if not guardados:
            continue
        resumo[R_ANEXOS] += guardados
        resumo[R_RECUPERADAS] += 1
        if aplicar:
            m.anexos = novos  # lista nova: o JSONB fica sujo
    if aplicar:
        await session.commit()
    else:
        await session.rollback()
    logger.info("atendimento_amazon_anexos_recuperados", **resumo)
    return resumo
