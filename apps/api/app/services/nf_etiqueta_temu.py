"""Etiqueta da Temu no fluxo automático (18/11/2026).

A etiqueta da Temu vem da Anjun Express ("DANFE SIMPLIFICADO-ETIQUETA") e,
diferente da Shopee/TikTok, não traz CPF, nem o nº do pedido na Temu, nem a
DECLARAÇÃO DE CONTEÚDO. Até aqui o Eduardo mandava o PDF e o
`~/nf-executor/temu_declaracao.py` (no Mac) achava o pedido por CEP + nome e
acrescentava a 2ª página da declaração antes de subir. Com a Temu no robô da
nuvem, a etiqueta entra pelo passe em lote (`/agent/etiqueta-lote`) ou pelo
por-pedido (`/agent/etiqueta`) — este módulo leva aquele script pra API:

- `pagina_declaracao` / `acrescentar_declaracao` (PURO): a página no MESMO
  modelo do `temu_declaracao.pagina_declaracao` — Helvetica 6,5 pt, uma
  célula por campo, o layout "nossa" que a `nf_etiqueta_declaracao` já sabe
  reescrever na impressão. Só entra quando a etiqueta de pedido Temu chegou
  SEM declaração (`reescrever_declaracao` → `sem_declaracao`): a que já vem
  com a página (fluxo manual) fica como está.
- `pedidos_temu`, `candidatos_cep_nome`, `pedidos_por_chave` (banco): os
  pedidos de loja Temu (`bling_orders.loja` → `store_info.bling_store_id`,
  `platform='temu'`) que o casamento e a declaração usam. NUNCA levantam:
  rodam num SAVEPOINT e a falha vira "nenhum pedido" (a etiqueta segue o
  caminho de antes — não casa / sobe sem a página), com warning no log.

Só a etiqueta da ANJUN (`nf_etiqueta_lote.e_etiqueta_anjun`) ganha a página:
pedido Temu que um dia sair por outro layout sem declaração (Correios com a
NF juntada…) fica como estava.

Shopee, TikTok, ML e Amazon não passam por aqui: só pedido de loja Temu entra
nas listas.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import fitz  # PyMuPDF
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, NfEtiquetaArquivo, NfNota, StoreInfo
from app.services.bling_situacoes import (
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
    SITUACAO_CANCELADO,
)
from app.services.nf_etiqueta_declaracao import (
    ItemDeclaracao,
    _gravar_marca,
    _largura,
    _quantidade,
    _quebrar_palavras,
    _quebrar_sku,
    _texto_imprimivel,
    assinatura_itens,
)
from app.services.nf_etiqueta_lote import (
    _RE_RASTREIO_ANJUN,
    CandidatoTemu,
    _e_declaracao,
    _so_digitos,
    e_etiqueta_anjun,
    ler_etiqueta_temu,
)

logger = structlog.get_logger()

PLATAFORMA = "temu"
# Cancelado (12) e Aguardando Cancelamento (83955): etiqueta não casa neles —
# gravar moveria o pedido pra "Em digitação" (21) no Bling.
SITUACOES_FORA: frozenset[str] = frozenset(
    {str(SITUACAO_CANCELADO), SITUACAO_AGUARDANDO_CANCELAMENTO_STR}
)

_BRT = ZoneInfo("America/Sao_Paulo")
_ANTIGO = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class PedidoTemu:
    """O pedido Temu como a declaração e o casamento precisam."""

    numero: str
    numeroloja: str | None = None
    conta: str | None = None  # store_info.account_name (atv/barbosa/eron/jlas)
    nome: str | None = None
    documento: str | None = None
    cep: str | None = None
    situacao: str | None = None


# ---------------------------------------------------------------------------
# Página da declaração (porte do ~/nf-executor/temu_declaracao.py)
# ---------------------------------------------------------------------------

_M = 8.0  # margem lateral
_CINZA = (0.85, 0.85, 0.85)
_PASSO = 1.18  # entrelinha (múltiplo da fonte)


def _linhas(texto: str, larg: float, fs: float, fonte: str, *, sku: bool = False) -> list[str]:
    """Quebra por parágrafo ('\\n') e, dentro dele, por palavra — palavra
    maior que a coluna parte no caractere; SKU quebra depois de '+'/'.'
    (mesmas regras da reescrita, `nf_etiqueta_declaracao`)."""
    out: list[str] = []
    # parágrafo ANTES do _texto_imprimivel (ele troca '\n' por espaço)
    for par in (texto or "").split("\n"):
        par = _texto_imprimivel(par)
        if sku:
            out += _quebrar_sku(par.strip(), larg, fs, fonte)
        else:
            out += _quebrar_palavras(par, larg, fs, fonte)
    return out or [""]


def _fonte(negrito: bool) -> str:
    return "hebo" if negrito else "helv"


# célula = (x0, x1, texto, tamanho, negrito, alinhar: 0 esq / 1 centro / 2 dir)
_Celula = tuple[float, float, str, float, bool, int]


def _altura(cels: Sequence[_Celula], minimo: float = 10.0) -> float:
    """Altura da linha da tabela = a da célula mais alta (como no script)."""
    alturas = []
    for x0, x1, s, tam, neg, _al in cels:
        n = len(_linhas(s, x1 - x0 - 4, tam, _fonte(neg)))
        alturas.append(4 + tam + (n - 1) * tam * _PASSO)
    return max(minimo, max(alturas) + 2)


def _escrever(
    pg: fitz.Page,
    x0: float,
    y0: float,
    x1: float,
    s: str,
    tam: float = 6.5,
    negrito: bool = False,
    alinhar: int = 0,
    *,
    sku: bool = False,
) -> None:
    fonte = _fonte(negrito)
    for k, ln in enumerate(_linhas(s, x1 - x0 - 4, tam, fonte, sku=sku)):
        lw = _largura(ln, fonte, tam)
        if alinhar == 0:
            x = x0 + 2
        elif alinhar == 1:
            x = x0 + (x1 - x0 - lw) / 2
        else:
            x = x1 - 2 - lw
        pg.insert_text(
            (x, y0 + 2 + tam + k * tam * _PASSO),
            ln,
            fontsize=tam,
            fontname=fonte,
            color=(0, 0, 0),
        )


def _linha_tabela(
    pg: fitz.Page, y: float, cels: Sequence[_Celula], fundo=None, *, col_sku: int = -1
) -> float:
    h = _altura(cels)
    for k, (x0, x1, s, tam, neg, al) in enumerate(cels):
        pg.draw_rect(fitz.Rect(x0, y, x1, y + h), color=(0, 0, 0), fill=fundo, width=0.5)
        _escrever(pg, x0, y, x1, s, tam, neg, al, sku=k == col_sku)
    return y + h


def _colunas(larg: float) -> list[float]:
    """x das 6 bordas da tabela de itens (Nº, SKU, DESCRIÇÃO, VARIAÇÃO, QTD)."""
    w = larg - 2 * _M
    larguras = [14, 46, w - 14 - 46 - 40 - 22, 40, 22]
    xs = [_M]
    for cw in larguras:
        xs.append(xs[-1] + cw)
    return xs


def _linhas_da_pagina(
    larg: float,
    pedido: PedidoTemu,
    itens: Sequence[ItemDeclaracao],
    nome_destinatario: str,
    remetente: str,
    agora: datetime,
) -> tuple[list[_Celula], list[_Celula], list[list[_Celula]], list[_Celula]]:
    """As linhas da tabela (sem desenhar) — servem pra medir e pra desenhar."""
    meio = _M + (larg - 2 * _M) / 2
    doc_dest = re.sub(r"\D", "", pedido.documento or "")
    cep = re.sub(r"\D", "", pedido.cep or "")
    rem = f"NOME: {remetente}\nCPF/CNPJ:\nCEP:"
    dst = f"NOME: {nome_destinatario}\nCPF/CNPJ: {doc_dest}\nCEP: {cep}"
    partes: list[_Celula] = [(_M, meio, rem, 6, False, 0), (meio, larg - _M, dst, 6, False, 0)]
    xs = _colunas(larg)
    cab = ["Nº", "SKU", "DESCRIÇÃO", "VARIAÇÃO", "QTD"]
    cabecalho: list[_Celula] = [
        (xs[k], xs[k + 1], cab[k], 6, True, 0 if k < 4 else 2) for k in range(5)
    ]
    linhas: list[list[_Celula]] = []
    for i, it in enumerate(itens, 1):
        vals = [str(i), it.sku, it.descricao, it.variacao, str(_quantidade(it.quantidade))]
        linhas.append(
            [(xs[k], xs[k + 1], vals[k], 6.5, k in (1, 4), 0 if k < 4 else 2) for k in range(5)]
        )
    total = sum(_quantidade(i.quantidade) for i in itens)
    rodape: list[_Celula] = [
        (_M, xs[3], agora.strftime("%d-%m-%Y"), 6, False, 0),
        (xs[3], xs[4], "Total", 6, False, 2),
        (xs[4], xs[5], str(total), 6.5, True, 2),
    ]
    return partes, cabecalho, linhas, rodape


def pagina_declaracao(
    doc: fitz.Document,
    larg: float,
    alt: float,
    *,
    pedido: PedidoTemu,
    itens: Sequence[ItemDeclaracao],
    rastreio: str | None,
    nome_destinatario: str | None = None,
    remetente: str | None = None,
    agora: datetime | None = None,
    plataforma: str = "Temu",
) -> fitz.Page:
    """Acrescenta a página "DECLARAÇÃO DE CONTEÚDO" no modelo do
    `temu_declaracao.pagina_declaracao`: cabeçalho com o nº do pedido, a
    plataforma, o nº do pedido na Temu e a hora; código de rastreamento;
    REMETENTE × DESTINATÁRIO (nome, CPF/CNPJ, CEP); IDENTIFICAÇÃO DOS BENS
    (Nº, SKU, DESCRIÇÃO, VARIAÇÃO, QTD) e o Total.

    `remetente` padrão = "Temu - <Conta>", como o script. Itens já no formato
    do papel (`formatar_item`). Se os itens não cabem na altura da etiqueta,
    a página cresce (a impressão redimensiona) em vez de cortar a tabela."""
    agora = agora or datetime.now(_BRT)
    nome = (nome_destinatario or pedido.nome or "").strip()
    rem = remetente if remetente is not None else f"{plataforma} - {(pedido.conta or '').title()}"
    partes, cabecalho, linhas, rodape = _linhas_da_pagina(larg, pedido, itens, nome, rem, agora)
    ident: list[_Celula] = [(_M, larg - _M, "IDENTIFICAÇÃO DOS BENS", 6.5, True, 1)]
    titulos: list[_Celula] = [
        (_M, _M + (larg - 2 * _M) / 2, "REMETENTE", 6.5, True, 1),
        (_M + (larg - 2 * _M) / 2, larg - _M, "DESTINATÁRIO", 6.5, True, 1),
    ]
    necessaria = (
        50
        + _altura(titulos)
        + _altura(partes)
        + 6
        + _altura(ident)
        + _altura(cabecalho)
        + sum(_altura(lin) for lin in linhas)
        + _altura(rodape)
        + 8
    )
    pg = doc.new_page(width=larg, height=max(alt, necessaria))

    numeroloja = pedido.numeroloja or ""
    _escrever(
        pg,
        _M - 2,
        4,
        larg - _M - 14,
        f"Pedido {pedido.numero} · {plataforma} {numeroloja} · {agora:%d/%m/%Y %H:%M}",
        5.5,
    )
    _escrever(pg, larg - _M - 16, 4, larg - _M + 2, "1/1", 5.5, alinhar=2)
    _escrever(pg, _M, 18, larg - _M, "DECLARAÇÃO DE CONTEÚDO", 12, True, 1)
    _escrever(pg, _M, 34, larg - _M, f"Código de Rastreamento: {rastreio or ''}", 7.5, False, 1)

    y = _linha_tabela(pg, 50, titulos, _CINZA)
    y = _linha_tabela(pg, y, partes)
    y += 6
    y = _linha_tabela(pg, y, ident, _CINZA)
    y = _linha_tabela(pg, y, cabecalho)
    for lin in linhas:
        y = _linha_tabela(pg, y, lin, col_sku=1)
    _linha_tabela(pg, y, rodape)
    return pg


def acrescentar_declaracao(
    pdf_bytes: bytes,
    pedido: PedidoTemu,
    itens: Sequence[ItemDeclaracao] | None,
    *,
    nome_destinatario: str | None = None,
    agora: datetime | None = None,
) -> tuple[bytes, str, str]:
    """Etiqueta da Temu (já transformada) + a página da declaração atrás.

    Devolve (bytes, status, detalhe); só `ok` traz bytes novos — `nao_anjun`
    (a 1ª página não é a etiqueta da Anjun), `ja_tem_declaracao` (alguma
    página já é a declaração: nunca duplica), `sem_itens` e `erro` devolvem os
    de ENTRADA (a etiqueta sobe como veio, nunca trava).

    O REMETENTE da página sai com o nome do DESTINATÁRIO: é a regra 1 da
    visualização (`nf_etiqueta_transform`), que no fluxo manual troca o
    "Temu - <Conta>" do script quando o PDF sobe — aqui a página entra
    depois do transform, então já nasce com a troca feita. O nome vem do
    papel (linha antes do "DESTINATÁRIO"), senão `nome_destinatario`, senão
    o do Bling. Rastreio = código AJ… da Anjun.

    Grava a marca de idempotência da declaração (`davinci-decl:`), como a
    reescrita: na impressão, com os mesmos itens, ela devolve `igual`; com
    item trocado no Bling, reescreve esta tabela (layout "nossa")."""
    lista = list(itens or [])
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            return pdf_bytes, "erro", "pdf_sem_paginas"
        etiqueta = doc[0]
        texto = etiqueta.get_text()
        if not e_etiqueta_anjun(texto):
            return pdf_bytes, "nao_anjun", ""
        if any(_e_declaracao(doc[i].get_text()) for i in range(doc.page_count)):
            return pdf_bytes, "ja_tem_declaracao", ""
        if not lista:
            return pdf_bytes, "sem_itens", ""
        etq = ler_etiqueta_temu(texto)
        nome = (etq.nome or nome_destinatario or pedido.nome or "").strip()
        pagina_declaracao(
            doc,
            etiqueta.rect.width,
            etiqueta.rect.height,
            pedido=pedido,
            itens=lista,
            rastreio=etq.rastreio,
            nome_destinatario=nome,
            remetente=nome,
            agora=agora,
        )
        assinatura = assinatura_itens(lista)
        _gravar_marca(doc, assinatura)
        return (
            doc.tobytes(garbage=3, deflate=True, no_new_id=True),
            "ok",
            f"pagina={doc.page_count - 1} itens={len(lista)} davinci-decl:{assinatura}",
        )
    except Exception as exc:  # noqa: BLE001 — a declaração nunca trava a subida
        return pdf_bytes, "erro", f"{type(exc).__name__}: {exc}"[:300]


# ---------------------------------------------------------------------------
# Banco: pedidos de loja Temu
# ---------------------------------------------------------------------------


async def _pedidos(
    session: AsyncSession,
    *,
    numeros: list[str] | None = None,
    desde: datetime | None = None,
) -> dict[str, PedidoTemu]:
    """numero → PedidoTemu, só pedidos de loja Temu (pelos `numeros` OU pela
    janela `desde`). Usa só as linhas do `bling_id` VIVO de cada número
    (pedido excluído e reimportado não mistura a versão morta) e ignora
    linha `situacao='excluido'`."""
    q = (
        select(
            BlingOrder.numero,
            BlingOrder.bling_id,
            BlingOrder.updated_at,
            BlingOrder.numeroloja,
            BlingOrder.situacao,
            BlingOrder.nome_destinatario,
            BlingOrder.documento_destinatario,
            BlingOrder.cep_destino,
            StoreInfo.account_name,
        )
        .select_from(BlingOrder)
        .join(StoreInfo, StoreInfo.bling_store_id == BlingOrder.loja)
        .where(
            BlingOrder.numero.is_not(None),
            BlingOrder.situacao.is_distinct_from("excluido"),
            func.lower(func.trim(StoreInfo.platform)) == PLATAFORMA,
        )
    )
    if numeros is not None:
        q = q.where(BlingOrder.numero.in_(numeros))
    if desde is not None:
        q = q.where(BlingOrder.data >= desde)
    rows = (await session.execute(q)).all()
    por_numero: dict[str, list] = {}
    for r in rows:
        por_numero.setdefault(r.numero, []).append(r)
    out: dict[str, PedidoTemu] = {}
    for numero, rs in por_numero.items():
        dono = max(rs, key=lambda r: (int(r.bling_id or 0), r.updated_at or _ANTIGO))
        vivos = [r for r in rs if r.bling_id == dono.bling_id]

        def primeiro(campo: str, _vivos: list = vivos) -> str | None:
            return next((getattr(r, campo) for r in _vivos if getattr(r, campo)), None)

        situacoes = {str(r.situacao) for r in vivos if r.situacao is not None}
        fora = situacoes & SITUACOES_FORA
        out[numero] = PedidoTemu(
            numero=numero,
            numeroloja=primeiro("numeroloja"),
            conta=primeiro("account_name"),
            nome=primeiro("nome_destinatario"),
            documento=primeiro("documento_destinatario"),
            cep=primeiro("cep_destino"),
            # cancelado em QUALQUER linha viva conta como cancelado
            situacao=next(iter(fora)) if fora else (str(dono.situacao) if dono.situacao else None),
        )
    return out


def _avisar(evento: str, origem: str, exc: Exception, **extra: object) -> None:
    logger.warning(evento, origem=origem, erro=f"{type(exc).__name__}: {exc}"[:300], **extra)


async def pedidos_temu(
    session: AsyncSession, numeros: Iterable[str | None], *, origem: str
) -> dict[str, PedidoTemu]:
    """numero → PedidoTemu dos `numeros` que são de loja Temu (qualquer
    situação). Quem não é Temu não aparece. NUNCA levanta."""
    nums = [n for n in dict.fromkeys(numeros) if n]
    if not nums:
        return {}
    try:
        async with session.begin_nested():
            return await _pedidos(session, numeros=nums)
    except Exception as exc:  # noqa: BLE001 — a declaração nunca trava o fluxo
        _avisar("nf_etiqueta_temu_pedidos_falhou", origem, exc, pedidos=nums[:20])
        return {}


def _rastreios_do_pdf(blob: bytes) -> frozenset[str]:
    """Códigos AJ… impressos na etiqueta gravada (vazio = sem código ou PDF
    que não abre — conta como "já tem etiqueta" de qualquer jeito)."""
    try:
        doc = fitz.open(stream=blob, filetype="pdf")
        texto = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    except Exception:  # noqa: BLE001 — blob ilegível = etiqueta de outro código
        return frozenset()
    return frozenset(_RE_RASTREIO_ANJUN.findall(texto))


async def candidatos_cep_nome(
    session: AsyncSession, desde: datetime, *, ceps: Iterable[str], origem: str
) -> list[CandidatoTemu]:
    """Os pedidos de loja Temu com data a partir de `desde`, em QUALQUER
    situação (os de Cancelado/Aguardando Cancelamento vêm com
    `valido=False`: concorrem, mas não são escolhidos), com o CEP de alguma
    etiqueta do lote (`ceps`, só dígitos) ou sem CEP no espelho — a lista do
    `nf_etiqueta_lote.casa_por_cep_nome`. Pra cada um, os códigos AJ… da
    etiqueta JÁ gravada (`rastreios`; None = sem etiqueta), lidos só dos
    pedidos desses CEPs. NUNCA levanta (falha → [], nada casa por CEP)."""
    alvo_ceps = {c for c in ceps if c}
    if not alvo_ceps:
        return []
    try:
        async with session.begin_nested():
            pedidos = [
                p
                for p in (await _pedidos(session, desde=desde)).values()
                if not _so_digitos(p.cep) or _so_digitos(p.cep) in alvo_ceps
            ]
            blobs = (
                (
                    await session.execute(
                        select(NfEtiquetaArquivo.pedido_bling, NfEtiquetaArquivo.blob).where(
                            NfEtiquetaArquivo.pedido_bling.in_([p.numero for p in pedidos]),
                            NfEtiquetaArquivo.blob.is_not(None),
                        )
                    )
                ).all()
                if pedidos
                else []
            )
    except Exception as exc:  # noqa: BLE001 — o casamento reserva nunca trava o lote
        _avisar("nf_etiqueta_temu_candidatos_falhou", origem, exc)
        return []
    # blob vazio = sem etiqueta (como no botão Imprimir Etiqueta)
    gravadas = {b.pedido_bling: _rastreios_do_pdf(b.blob) for b in blobs if b.blob}
    return [
        CandidatoTemu(
            numero=p.numero,
            nome=p.nome,
            cep=p.cep,
            valido=p.situacao not in SITUACOES_FORA,
            rastreios=gravadas.get(p.numero),
        )
        for p in pedidos
    ]


async def pedidos_por_chave(
    session: AsyncSession, chaves: Iterable[str], *, origem: str
) -> dict[str, str | None] | None:
    """{chave da NF → numero do pedido} das `chaves` que estão em `nf_nota`
    já casadas com pedido (o XML entra pelo coletor, `run_xml.sh`): o número
    quando o pedido é de loja Temu fora de Cancelado/Aguardando
    Cancelamento, None quando não serve (cancelado, outra plataforma, fora
    do espelho) — a chave conhecida decide sozinha
    (`nf_etiqueta_lote.chave_conhecida`). Chave que não está no `nf_nota`
    não aparece. NUNCA levanta: na falha devolve None (o router não casa
    nada pela Temu nesse lote — sem a chave, o CEP + nome podia errar)."""
    lista = [c for c in dict.fromkeys(chaves) if c]
    if not lista:
        return {}
    try:
        async with session.begin_nested():
            notas = (
                await session.execute(
                    select(NfNota.chave, NfNota.pedido_bling).where(
                        NfNota.chave.in_(lista), NfNota.pedido_bling.is_not(None)
                    )
                )
            ).all()
            if not notas:
                return {}
            pedidos = await _pedidos(session, numeros=list({n.pedido_bling for n in notas}))
    except Exception as exc:  # noqa: BLE001 — o casamento reserva nunca trava o lote
        _avisar("nf_etiqueta_temu_chave_falhou", origem, exc, chaves=len(lista))
        return None
    return {
        n.chave: (
            n.pedido_bling
            if n.pedido_bling in pedidos and pedidos[n.pedido_bling].situacao not in SITUACOES_FORA
            else None
        )
        for n in notas
    }
