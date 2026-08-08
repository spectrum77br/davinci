"""Fatia o PDF ÚNICO da impressão em LOTE do Upseller em etiquetas por pedido.

No lote ("Imprimir Etiquetas" com todos os pedidos marcados) o Upseller abre um
PDF só, com as etiquetas de todos os pedidos em sequência — cada pedido ocupa a
página da ETIQUETA + a(s) página(s) da DECLARAÇÃO DE CONTEÚDO logo atrás. Este
módulo é a camada PURA (sem rede, sem banco) que:

1. `fatiar_lote(pdf_bytes)` — quebra o PDF em fatias (uma por pedido). A regra
   de corte: toda página abre uma fatia NOVA, exceto página de "DECLARAÇÃO DE
   CONTEÚDO", que continua a fatia anterior (fica atrás da etiqueta dela).
   De cada fatia extrai o texto completo e os CANDIDATOS a nº do pedido da
   plataforma (`numerolojas`): todos os matches de "Pedido: X" + tokens com o
   formato de order_sn da Shopee — no layout DANFE SIMPLIFICADO o "Pedido:"
   vem seguido do código de triagem (ex. "SOCSP7") e o numeroloja real aparece
   solto no corpo. Quem decide qual candidato vale é o lookup no banco
   (bling_orders.numeroloja) — a fatia nunca chuta.

2. `casa_por_texto(texto, candidatos)` — casamento reserva pra etiqueta SEM o
   nº da plataforma no corpo (TikTok): casa pelo NOME do destinatário + pelo
   menos um SKU da declaração de conteúdo. A etiqueta TikTok imprime o nome
   ABREVIADO ("ana clara" pra "ANA CLARA ANACLETO PINTO"), então aceita
   qualquer PREFIXO por token do nome do Bling. Só devolve pedido quando o
   casamento é INEQUÍVOCO (exatamente um candidato) — ambíguo é melhor não
   casar do que subir a etiqueta no pedido errado.

Quem consome é o endpoint `/agent/etiqueta-lote` (routers/nf.py), que casa
contra `bling_orders`, transforma cada fatia e grava por pedido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz  # PyMuPDF


class EtiquetaLoteError(RuntimeError):
    """Falha ao abrir/fatiar o PDF do lote."""


@dataclass
class EtiquetaFatia:
    """Uma etiqueta do lote (etiqueta + declaração do mesmo pedido)."""

    pdf: bytes
    # Intervalo de páginas no PDF original (0-based, inclusivo) — diagnóstico.
    pagina_ini: int
    pagina_fim: int
    # 1º match de "Pedido: X" — diagnóstico/retrocompat.
    numeroloja: str | None
    # TODOS os candidatos a nº da plataforma (matches de "Pedido:" + tokens no
    # formato de order_sn da Shopee). O router valida contra o banco.
    numerolojas: list[str]
    # Texto completo da fatia (todas as páginas) pro casamento reserva.
    texto: str


# "Pedido: 260808R7G290SQ" na etiqueta Shopee. Aceita dígitos/letras/hífen.
_RE_PEDIDO = re.compile(r"Pedido:\s*([A-Z0-9][A-Z0-9-]{5,})", re.IGNORECASE)

# order_sn da Shopee solto no texto (layout DANFE SIMPLIFICADO): 6 dígitos de
# data (AAMMDD) + 8 alfanuméricos MAIÚSCULOS, como token isolado — não casa
# dentro do código de barras (44 dígitos), do rastreio TikTok (15 dígitos) nem
# do rastreio "BR...Z" (precedido de letra).
_RE_SHOPEE_SN = re.compile(r"(?<![A-Za-z0-9])\d{6}[A-Z0-9]{8}(?![A-Za-z0-9])")


def _numerolojas_do_texto(texto: str) -> list[str]:
    """Candidatos a nº da plataforma, na ordem, sem duplicar."""
    achados = _RE_PEDIDO.findall(texto) + _RE_SHOPEE_SN.findall(texto)
    return list(dict.fromkeys(achados))


def _e_declaracao(texto: str) -> bool:
    t = texto.upper()
    return "DECLARA" in t and "CONTE" in t  # "DECLARAÇÃO DE CONTEÚDO"


def fatiar_lote(pdf_bytes: bytes) -> list[EtiquetaFatia]:
    """Quebra o PDF do lote em fatias (uma por pedido)."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise EtiquetaLoteError(f"pdf_invalido: {exc}") from exc
    if doc.page_count == 0:
        raise EtiquetaLoteError("pdf_vazio")

    textos = [doc[i].get_text("text") for i in range(doc.page_count)]
    grupos: list[list[int]] = []
    for i, t in enumerate(textos):
        if grupos and _e_declaracao(t):
            grupos[-1].append(i)
        else:
            grupos.append([i])

    fatias: list[EtiquetaFatia] = []
    for pages in grupos:
        sub = fitz.open()
        sub.insert_pdf(doc, from_page=pages[0], to_page=pages[-1])
        texto = "\n".join(textos[p] for p in pages)
        m = _RE_PEDIDO.search(texto)
        fatias.append(
            EtiquetaFatia(
                pdf=sub.tobytes(),
                pagina_ini=pages[0],
                pagina_fim=pages[-1],
                numeroloja=m.group(1) if m else None,
                numerolojas=_numerolojas_do_texto(texto),
                texto=texto,
            )
        )
    return fatias


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().upper()


def _sku_no_texto(sku: str, texto: str) -> bool:
    """SKU como token isolado (não substring de outro código)."""
    return (
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(sku)}(?![A-Za-z0-9])",
            texto,
            re.IGNORECASE,
        )
        is not None
    )


def _nome_no_texto(nome: str, texto_norm: str) -> bool:
    """Nome do Bling (ou um PREFIXO dele, por token) presente no texto.

    A etiqueta TikTok imprime o destinatário abreviado ("ana clara") enquanto o
    Bling guarda o nome completo ("ANA CLARA ANACLETO PINTO") — então basta o
    começo do nome aparecer, como palavras inteiras (fronteira: "ANA" não casa
    dentro de "SANTANA"). Prefixo mínimo de 3 caracteres.
    """
    tokens = _norm(nome).split(" ")
    for k in range(len(tokens), 0, -1):
        prefixo = " ".join(tokens[:k])
        if len(prefixo) < 3:
            break  # prefixos seguintes só encurtam
        if re.search(rf"(?<!\w){re.escape(prefixo)}(?!\w)", texto_norm):
            return True
    return False


def casa_por_texto(
    texto: str, candidatos: list[tuple[str, str | None, set[str]]]
) -> str | None:
    """Casamento reserva (TikTok): nome do destinatário + pelo menos um SKU.

    `candidatos` = [(numero, nome_destinatario, skus)]. Devolve o `numero` só
    quando EXATAMENTE UM candidato casa; None se nenhum ou se ambíguo. Candidato
    sem nome ou sem SKU não participa (nome sozinho casaria homônimo). O nome
    casa por prefixo (etiqueta TikTok abrevia) — ver `_nome_no_texto`.
    """
    t = _norm(texto)
    hits: list[str] = []
    for numero, nome, skus in candidatos:
        if not nome or not skus:
            continue
        if not _nome_no_texto(nome, t):
            continue
        if not any(_sku_no_texto(sku, texto) for sku in skus if sku):
            continue
        hits.append(numero)
    unicos = list(dict.fromkeys(hits))
    return unicos[0] if len(unicos) == 1 else None
