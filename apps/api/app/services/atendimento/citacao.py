"""A mensagem CITADA — o "responder" numa mensagem (17/11/2026).

O dono: "a equipe mandou as malas e a pessoa respondeu na mala que queria, mas no
nosso comunicador não mostra em qual mensagem ele respondeu; no Duoke isso
aparecia". Quando o comprador usa o "responder" da plataforma, o balão dele ganha,
em cima, a mensagem citada (a miniatura da foto, ou o texto) e o clique leva até
ela — como no WhatsApp e no Duoke. Aqui é só a LEITURA para a tela: nada grava.

De onde vem (medido em produção, só leitura, 17/11/2026):
- Shopee: `payload.quoted_msg` = a citada INTEIRA, no formato do `get_message`
  (`message_id`, `from_id`, `message_type`, `content`...). 492 citações desde
  30/09, quase todas gravadas na MESMA conversa com `externo_id` = o
  `message_id` (a foto que a loja mandou, a pergunta do próprio comprador).
- Zap (WhatsApp Cloud): `payload.zap.contexto_wamid` (o `context.id` que o
  `zap_entrada` grava) — só o id; o conteúdo vem da nossa linha.
- TikTok, ML, Magalu, Amazon...: o payload não traz citação (17/11/2026).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import cartoes_plataforma, pendente
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    rotulo_tipo_plataforma,
)

logger = structlog.get_logger()

# O `tipo` da citação na tela; o resto vira `outro` (com o rótulo no texto).
TIPO_TEXTO = "texto"
TIPO_IMAGEM = "imagem"
TIPO_VIDEO = "video"
TIPO_PRODUTO = "produto"
TIPO_PEDIDO = "pedido"
TIPO_OUTRO = "outro"
TIPOS = (TIPO_TEXTO, TIPO_IMAGEM, TIPO_VIDEO, TIPO_PRODUTO, TIPO_PEDIDO, TIPO_OUTRO)
# O bloco mostra 1–2 linhas: mais que isto é corte.
TEXTO_MAX = 200
# O id no tamanho da coluna `externo_id` (o mesmo corte do `gravar._cabe`).
EXTERNO_MAX = 191
URL_MAX = 2048
# Onde o anexo de foto guarda o endereço (as mesmas chaves que o balão lê).
_CHAVES_URL = ("url", "link", "src", "image_url", "thumb_url", "url_imagem")
# Os tipos do Zap sem texto: o bloco diz o que é.
_ROTULO_DA_LINHA = {"audio": "Áudio", "arquivo": "Documento", "figurinha": "Figurinha"}


@dataclass(frozen=True)
class Referencia:
    """A citação: o id da citada na plataforma e, na Shopee, a citada crua."""

    externo_id: str
    crua: dict | None = None


def _id(valor: Any) -> str | None:
    if isinstance(valor, bool) or not isinstance(valor, str | int):
        return None
    return str(valor).strip()[:EXTERNO_MAX] or None


def referencia(m: Any) -> Referencia | None:
    """A mensagem cita outra? → o id da citada (e a crua, na Shopee). PURA."""
    payload = getattr(m, "payload", None)
    if not isinstance(payload, dict):
        return None
    crua = payload.get("quoted_msg")
    if isinstance(crua, dict):
        externo = _id(crua.get("message_id"))
        if externo:
            return Referencia(externo, crua)
    zap = payload.get("zap")
    if isinstance(zap, dict):
        externo = _id(zap.get("contexto_wamid"))
        if externo:
            return Referencia(externo)
    return None


def url_da_miniatura(valor: Any) -> str | None:
    """A URL que pode ir para o `<img>` da citação — ou None. PURA.

    A régua do balão (`enviar._url_do_balao`, a do `gravar.url_avatar`):
    `https://` (a CDN da plataforma) ou caminho RELATIVO da própria API (a nossa
    cópia do Zap). `http://` vira `https://` (a tela é https: imagem em http é
    conteúdo misto, como no `enriquecer._https`); `//outro-site`, `javascript:`,
    `data:`, espaço, barra invertida e o id solto que a Shopee põe no
    `thumb_url` do `image_with_text` viram None.
    """
    if not isinstance(valor, str):
        return None
    url = valor.strip()
    if not url or len(url) > URL_MAX or any(c.isspace() for c in url) or "\\" in url:
        return None
    if url[:7].lower() == "http://":
        url = "https://" + url[7:]
    if url[:8].lower() == "https://":
        return url if len(url) > 8 else None
    if url.startswith("/") and not url.startswith("//"):
        return url
    return None


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    return " ".join(valor.split()) or None


def _curto(valor: Any) -> str | None:
    texto = _texto(valor)
    if texto is None or len(texto) <= TEXTO_MAX:
        return texto
    return texto[: TEXTO_MAX - 1].rstrip() + "…"


def _da_crua(crua: dict) -> tuple[str, str | None, str | None]:
    """(tipo, texto, imagem) da citada crua da Shopee (o formato do `get_message`). PURA."""
    tipo = str(crua.get("message_type") or "").strip().lower()
    bruto = crua.get("content")
    conteudo: dict = bruto if isinstance(bruto, dict) else {}
    texto = _texto(conteudo.get("text"))
    if tipo in ("text", "extended_markdown"):
        return TIPO_TEXTO, texto, None
    if tipo == "image":
        # O `thumb_url` vem vazio; o `url` é a foto (medido 17/11/2026).
        imagem = url_da_miniatura(conteudo.get("thumb_url")) or url_da_miniatura(
            conteudo.get("url")
        )
        return TIPO_IMAGEM, None, imagem
    if tipo == "image_with_text":
        # Aqui o `thumb_url` é um id solto, não URL: vale o `image_url`.
        imagem = url_da_miniatura(conteudo.get("thumb_url")) or url_da_miniatura(
            conteudo.get("image_url")
        )
        legenda = texto or _texto(conteudo.get("title")) or _texto(conteudo.get("caption"))
        return TIPO_IMAGEM, legenda, imagem
    if tipo == "video":
        imagem = url_da_miniatura(conteudo.get("thumb_url")) or url_da_miniatura(
            conteudo.get("cover")
        )
        return TIPO_VIDEO, None, imagem
    if tipo == "item":
        # Só o id do anúncio: a foto e o título vêm da nossa linha (o cartão).
        return TIPO_PRODUTO, None, None
    if tipo == "order":
        pedido = _texto(conteudo.get("order_sn"))
        return TIPO_PEDIDO, f"Pedido {pedido}" if pedido else None, None
    # Figurinha, cupom, os cartões da plataforma: o texto do cartão (o mesmo do
    # balão, `cartoes_plataforma`) ou o rótulo em português.
    falsa = SimpleNamespace(
        tipo=TIPO_OUTRO, texto=texto or rotulo_tipo_plataforma(tipo), payload=crua
    )
    return (
        TIPO_OUTRO,
        cartoes_plataforma.texto_para_exibir(falsa),
        url_da_miniatura(conteudo.get("image_url")),
    )


def _da_linha(alvo: Any) -> tuple[str, str | None, str | None]:
    """(tipo, texto, imagem) da NOSSA linha da citada — o que o balão dela mostra. PURA."""
    tipo_linha = str(getattr(alvo, "tipo", None) or "")
    tipo = tipo_linha if tipo_linha in TIPOS else TIPO_OUTRO
    texto = _texto(cartoes_plataforma.texto_para_exibir(alvo))
    imagem: str | None = None
    anexos = getattr(alvo, "anexos", None)
    for a in anexos if isinstance(anexos, list) else []:
        if not isinstance(a, dict):
            continue
        tipo_anexo = str(a.get("tipo") or "").strip().lower()
        if tipo_anexo == TIPO_PRODUTO:
            # O cartão do anúncio: a foto e o título (a mala que a equipe mandou).
            imagem = imagem or url_da_miniatura(a.get("imagem"))
            texto = texto or _texto(a.get("titulo"))
        elif tipo_anexo == TIPO_PEDIDO:
            numero = _texto(a.get("pedido"))
            texto = texto or (f"Pedido {numero}" if numero else None)
            itens = a.get("itens")
            for item in itens if isinstance(itens, list) else []:
                if imagem is None and isinstance(item, dict):
                    imagem = url_da_miniatura(item.get("imagem"))
        elif tipo_anexo in (TIPO_IMAGEM, "figurinha") or (not tipo_anexo and tipo == TIPO_IMAGEM):
            for chave in _CHAVES_URL:
                imagem = imagem or url_da_miniatura(a.get(chave))
    if texto is None and tipo == TIPO_OUTRO and tipo_linha in _ROTULO_DA_LINHA:
        texto = f"[{_ROTULO_DA_LINHA[tipo_linha]}]"
    return tipo, texto, imagem


def _autor(ref: Referencia, alvo: Any, conversa: Any) -> str | None:
    """'cliente' ou 'loja': de quem é a citada (None = não se sabe). PURA.

    Na Shopee, pelo `from_id` da crua contra o comprador da conversa (o comprador
    também tem `from_shop_id` — a loja dele —, então o `from_shop_id` não separa).
    Senão, pela nossa linha: a do comprador (`pendente.do_comprador_no_numero`) é
    dele; `loja` e `sistema` (a resposta automática da loja) são da loja.
    """
    comprador = getattr(conversa, "comprador_id", None)
    if ref.crua is not None:
        de = ref.crua.get("from_id")
        if de not in (None, "") and comprador not in (None, ""):
            return AUTOR_CLIENTE if str(de) == str(comprador) else AUTOR_LOJA
    if alvo is None:
        return None
    if pendente.do_comprador_no_numero(alvo, comprador):
        return AUTOR_CLIENTE
    if getattr(alvo, "autor", None) in (AUTOR_LOJA, AUTOR_SISTEMA):
        return AUTOR_LOJA
    return None


def montar(ref: Referencia, alvo: Any, conversa: Any) -> dict[str, Any]:
    """O campo `citada` da mensagem (`schemas.atendimento.CitadaOut`). PURA.

    `alvo` = a nossa linha da citada na MESMA conversa (None = não está gravada).
    Ela manda — é o balão para onde o clique leva —; o que faltar vem da crua.
    """
    tipo_c: str | None = None
    texto_c: str | None = None
    imagem_c: str | None = None
    if ref.crua is not None:
        tipo_c, texto_c, imagem_c = _da_crua(ref.crua)
    tipo_l: str | None = None
    texto_l: str | None = None
    imagem_l: str | None = None
    if alvo is not None:
        tipo_l, texto_l, imagem_l = _da_linha(alvo)
    tipo = tipo_l if tipo_l not in (None, TIPO_OUTRO) else (tipo_c or tipo_l or TIPO_OUTRO)
    return {
        "externo_id": ref.externo_id,
        "mensagem_id": str(alvo.id) if alvo is not None else None,
        "autor": _autor(ref, alvo, conversa),
        "tipo": tipo,
        "texto": _curto(texto_l or texto_c),
        "imagem_url": imagem_l or imagem_c,
    }


async def citadas_em_lote(
    session: AsyncSession,
    mensagens: Iterable[AtendimentoMensagem],
    conversas: Mapping[UUID, AtendimentoConversa],
) -> dict[UUID, dict[str, Any]]:
    """A `citada` de cada mensagem que cita outra → {id da mensagem: campo}.

    Uma consulta para a página toda (sem N+1), só para as citadas que não estão
    na própria página. `conversas` = as conversas das mensagens (a do detalhe;
    as da família, nas abas). Mensagem sem citação não entra (fica None). Uma
    citação ilegível vai ao log e o balão fica como era — a tela nunca cai por ela.
    """
    lista = list(mensagens)
    refs: list[tuple[AtendimentoMensagem, Referencia]] = []
    for m in lista:
        ref = referencia(m)
        if ref is not None and m.conversa_id in conversas:
            refs.append((m, ref))
    if not refs:
        return {}
    # A citada costuma estar na própria página (a foto que a equipe mandou antes).
    achadas: dict[tuple[UUID, str], AtendimentoMensagem] = {
        (m.conversa_id, m.externo_id): m for m in lista if m.externo_id
    }
    faltam = {(m.conversa_id, ref.externo_id) for m, ref in refs} - achadas.keys()
    if faltam:
        linhas = await session.execute(
            select(AtendimentoMensagem).where(
                AtendimentoMensagem.conversa_id.in_({c for c, _ in faltam}),
                AtendimentoMensagem.externo_id.in_({e for _, e in faltam}),
            )
        )
        for alvo in linhas.scalars():
            if alvo.externo_id:
                achadas.setdefault((alvo.conversa_id, alvo.externo_id), alvo)
    saida: dict[UUID, dict[str, Any]] = {}
    for m, ref in refs:
        try:
            saida[m.id] = montar(
                ref, achadas.get((m.conversa_id, ref.externo_id)), conversas[m.conversa_id]
            )
        except Exception as erro:  # noqa: BLE001 — o balão fica sem a citação
            logger.warning(
                "atendimento_citada_ilegivel", mensagem_id=str(m.id), erro=type(erro).__name__
            )
    return saida
