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

2. `casa_por_documento(documentos, texto, candidatos)` — casamento pelo CPF/CNPJ
   impresso na declaração de conteúdo contra `bling_orders.documento_destinatario`.
   É a chave mais específica que temos (mais que nome+SKU) e cobre os layouts
   onde o nº da plataforma não aparece no papel. CPF repetido no lote (mesmo
   cliente, 2 pedidos) desempata por SKU; se continuar ambíguo, não casa.

3. `casa_por_texto(texto, candidatos)` — casamento reserva pra etiqueta SEM o
   nº da plataforma no corpo (TikTok): casa pelo NOME do destinatário + pelo
   menos um SKU da declaração de conteúdo. A etiqueta TikTok imprime o nome
   ABREVIADO ("ana clara" pra "ANA CLARA ANACLETO PINTO"), então aceita
   qualquer PREFIXO por token do nome do Bling. Só devolve pedido quando o
   casamento é INEQUÍVOCO (exatamente um candidato) — ambíguo é melhor não
   casar do que subir a etiqueta no pedido errado.

4. Etiqueta da TEMU (Anjun Express, "DANFE SIMPLIFICADO-ETIQUETA", 18/11/2026):
   não traz CPF, nem o nº do pedido na Temu (o "Pedido: AJ…" é o código da
   Anjun), nem declaração de conteúdo — nenhum dos 3 casamentos acima pega.
   Só entra fatia que é MESMO da Anjun (`e_etiqueta_anjun`: código AJ… e o
   nome da transportadora). `ler_etiqueta_temu(texto)` lê a chave da NF (44
   dígitos), o CEP e o nome do destinatário (a linha ANTES do rótulo
   "DESTINATÁRIO", como no `~/nf-executor/temu_declaracao.py`);
   `casa_por_chave` casa pela chave (`nf_nota.chave`, quando o XML já foi
   coletado — chave conhecida DECIDE sozinha) e `casa_por_cep_nome` pelo
   CEP + nome entre os pedidos Temu que o router passa (`CandidatoTemu`). Os
   dois só casam quando o resultado é INEQUÍVOCO.

Quem consome é o endpoint `/agent/etiqueta-lote` (routers/nf.py), que casa
contra `bling_orders`, transforma cada fatia e grava por pedido.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
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
    # CPF/CNPJ (só dígitos) achados no papel, destinatário PRIMEIRO.
    documentos: list[str]
    # Texto completo da fatia (todas as páginas) pro casamento reserva.
    texto: str


# "Pedido: 260808R7G290SQ" na etiqueta Shopee. Aceita dígitos/letras/hífen.
_RE_PEDIDO = re.compile(r"Pedido:\s*([A-Z0-9][A-Z0-9-]{5,})", re.IGNORECASE)

# order_sn da Shopee solto no texto (layout DANFE SIMPLIFICADO): 6 dígitos de
# data (AAMMDD) + 8 alfanuméricos MAIÚSCULOS, como token isolado — não casa
# dentro do código de barras (44 dígitos), do rastreio TikTok (15 dígitos) nem
# do rastreio "BR...Z" (precedido de letra).
_RE_SHOPEE_SN = re.compile(r"(?<![A-Za-z0-9])\d{6}[A-Z0-9]{8}(?![A-Za-z0-9])")


# "CPF/CNPJ: 123.456.789-01" na declaração de conteúdo. Aceita formatado.
_RE_DOC = re.compile(r"(?:CPF|CNPJ)[^:\d]{0,12}:?\s*([\d.\-/]{11,18})", re.IGNORECASE)


# Etiqueta ML Flex (Marquezini, 09/09): "Pack ID: 20000 14931988905" — o nº da
# venda vem partido por um espaço; juntando dá o numeroloja (2000014931988905).
_RE_PACK_ML = re.compile(r"Pack ID:\s*(\d{5})\s*(\d{11})", re.IGNORECASE)


def _numerolojas_do_texto(texto: str) -> list[str]:
    """Candidatos a nº da plataforma, na ordem, sem duplicar."""
    achados = (
        _RE_PEDIDO.findall(texto)
        + ["".join(m) for m in _RE_PACK_ML.findall(texto)]
        + _RE_SHOPEE_SN.findall(texto)
    )
    return list(dict.fromkeys(achados))


def _documentos_do_texto(texto: str) -> list[str]:
    """CPF/CNPJ do papel, só dígitos, DESTINATÁRIO na frente.

    A declaração traz o documento do remetente e o do destinatário; qual é
    qual se decide pelo rótulo mais próximo ANTES do número no texto extraído.
    Quem não tem 11 (CPF) nem 14 (CNPJ) dígitos é descartado.
    """
    up = texto.upper()
    dest: list[str] = []
    outros: list[str] = []
    for m in _RE_DOC.finditer(texto):
        digitos = "".join(c for c in m.group(1) if c.isdigit())
        if len(digitos) not in (11, 14):
            continue
        eh_dest = up.rfind("DESTINAT", 0, m.start()) > up.rfind("REMET", 0, m.start())
        alvo = dest if eh_dest else outros
        alvo.append(digitos)
    return list(dict.fromkeys(dest + outros))


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
                documentos=_documentos_do_texto(texto),
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


def casa_por_documento(
    documentos: list[str],
    texto: str,
    candidatos: list[tuple[str, str | None, set[str]]],
) -> str | None:
    """Casamento pelo CPF/CNPJ do destinatário impresso na declaração.

    `candidatos` = [(numero, documento, skus)] — `documento` é o
    `bling_orders.documento_destinatario` (só dígitos). Devolve o `numero` do
    ÚNICO pedido com aquele documento; se o mesmo cliente tem 2 pedidos no
    lote, desempata exigindo um SKU da declaração. Ambíguo → None (manual).
    """
    por_doc: dict[str, list[tuple[str, set[str]]]] = {}
    for numero, doc, skus in candidatos:
        if doc:
            por_doc.setdefault(doc, []).append((numero, skus))

    for doc in documentos:
        hits = por_doc.get(doc)
        if not hits:
            continue
        numeros = list(dict.fromkeys(n for n, _ in hits))
        if len(numeros) == 1:
            return numeros[0]
        com_sku = [
            n
            for n, skus in hits
            if any(_sku_no_texto(s, texto) for s in skus if s)
        ]
        unicos = list(dict.fromkeys(com_sku))
        if len(unicos) == 1:
            return unicos[0]
    return None


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


# ---------------------------------------------------------------------------
# Etiqueta da Temu (Anjun Express) — 18/11/2026
# ---------------------------------------------------------------------------

# "CEP:35661-072" logo abaixo do "DESTINATÁRIO" (o CEP do remetente vem sem
# rótulo na Anjun).
_RE_CEP = re.compile(r"CEP:\s*(\d{5})-?(\d{3})", re.IGNORECASE)
# Chave de acesso da NF-e: 44 dígitos como token isolado.
_RE_CHAVE = re.compile(r"(?<!\d)\d{44}(?!\d)")
# Código de rastreio da Anjun ("AJ261111162691701").
_RE_RASTREIO_ANJUN = re.compile(r"(?<![A-Za-z0-9])AJ\d{12,}(?![A-Za-z0-9])")


@dataclass(frozen=True)
class EtiquetaTemu:
    """O que a etiqueta da Temu (Anjun) traz pra casar e pra declaração."""

    # CEP do destinatário, só dígitos (8).
    cep: str | None
    # Nome do destinatário como está no papel.
    nome: str | None
    # Chave(s) de acesso da NF impressas (44 dígitos), na ordem.
    chaves: list[str]
    # Código de rastreio da Anjun (vai na declaração).
    rastreio: str | None


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def ler_etiqueta_temu(texto: str) -> EtiquetaTemu:
    """Lê a etiqueta da Anjun (layout "DANFE SIMPLIFICADO-ETIQUETA").

    O nome do destinatário é a linha ANTES do rótulo "DESTINATÁRIO" (o bloco
    da Anjun é nome → DESTINATÁRIO → CEP → Endereço); a chave da NF, quando
    vem logo antes do nome, não conta como nome. O CEP é o 1º "CEP:" depois
    do rótulo (ou o 1º do texto, se o rótulo não aparecer)."""
    linhas = [ln.strip() for ln in (texto or "").splitlines() if ln.strip()]
    nome: str | None = None
    pos_dest = -1
    for i, ln in enumerate(linhas):
        if _sem_acento(ln).upper() == "DESTINATARIO":
            pos_dest = i
            if i > 0:
                antes = linhas[i - 1]
                # nome tem letra; a chave (44 dígitos) e códigos soltos não
                if re.search(r"[^\W\d_]", antes) and not _RE_CHAVE.search(antes):
                    nome = antes
            break
    cep_m = None
    if pos_dest >= 0:
        cep_m = _RE_CEP.search("\n".join(linhas[pos_dest:]))
    if cep_m is None:
        cep_m = _RE_CEP.search(texto or "")
    rastreio = _RE_RASTREIO_ANJUN.search(texto or "")
    return EtiquetaTemu(
        cep=(cep_m.group(1) + cep_m.group(2)) if cep_m else None,
        nome=nome,
        chaves=list(dict.fromkeys(_RE_CHAVE.findall(texto or ""))),
        rastreio=rastreio.group(0) if rastreio else None,
    )


def e_etiqueta_anjun(texto: str) -> bool:
    """A fatia é a etiqueta da Anjun: traz o código de rastreio AJ… E o nome
    da transportadora ("Anjun Express"). As 45 etiquetas reais de
    `~/nf-executor/saidas/etiquetas/` têm as duas marcas, também depois do
    transform. Sem elas, nada da Temu (casamento por chave ou CEP + nome,
    página da declaração) mexe na etiqueta — outro layout sem declaração
    (Shein, Correios com a NF juntada…) segue o caminho de antes."""
    t = texto or ""
    return bool(_RE_RASTREIO_ANJUN.search(t)) and "ANJUN" in _sem_acento(t).upper()


def identidade_etiqueta(etq: EtiquetaTemu) -> str | None:
    """O que distingue uma etiqueta da Anjun de outra: o código AJ… (senão a
    chave da NF). Duas fatias com a mesma identidade são a MESMA etiqueta
    impressa duas vezes."""
    return etq.rastreio or (etq.chaves[0] if etq.chaves else None)


def _tokens_nome(nome: str | None) -> set[str]:
    """Nome → palavras sem acento, minúsculas, só letras (como o
    `temu_declaracao._norm`)."""
    s = _sem_acento(nome or "").lower()
    return set(re.sub(r"[^a-z ]", " ", s).split())


def _so_digitos(s: str | None) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def chave_conhecida(chaves: list[str], por_chave: Mapping[str, str | None]) -> bool:
    """Alguma chave impressa na etiqueta já está no `nf_nota` com pedido?
    Então a chave DECIDE: casa por ela ou não casa — nunca cai no CEP + nome
    (que poderia pôr a etiqueta de um pedido cancelado no gêmeo ativo do
    mesmo cliente)."""
    return any(c in por_chave for c in chaves)


def casa_por_chave(chaves: list[str], por_chave: Mapping[str, str | None]) -> str | None:
    """Casamento pela chave da NF impressa na etiqueta.

    `por_chave` = {chave → numero do pedido} que o router montou de
    `nf_nota`: o número quando a nota é de pedido Temu fora de Cancelado/
    Aguardando Cancelamento, None quando a nota é de pedido que não serve
    (cancelado, outra plataforma, fora do espelho). Devolve o pedido quando
    as chaves do papel apontam pra UM pedido válido só; senão None."""
    vistos = [por_chave[c] for c in chaves if c in por_chave]
    if not vistos or any(n is None for n in vistos):
        return None
    numeros = list(dict.fromkeys(vistos))
    return numeros[0] if len(numeros) == 1 else None


@dataclass(frozen=True)
class CandidatoTemu:
    """Pedido de loja Temu na janela do lote, pro casamento por CEP + nome."""

    numero: str
    nome: str | None
    cep: str | None
    # Fora de Cancelado (12) e Aguardando Cancelamento (83955). Os de dentro
    # CONCORREM (a etiqueta pode ser deles), mas nunca são escolhidos.
    valido: bool = True
    # Códigos AJ… da etiqueta JÁ gravada do pedido (nf_etiqueta_arquivo):
    # None = sem etiqueta gravada; vazio = gravada sem código AJ.
    rastreios: frozenset[str] | None = None


def casa_por_cep_nome(texto: str, candidatos: Sequence[CandidatoTemu]) -> str | None:
    """Casamento reserva da etiqueta da Temu: CEP + nome do destinatário.

    `candidatos` = os pedidos de loja Temu da janela do lote, em QUALQUER
    situação. Conta como concorrente todo pedido com o nome compatível (as
    palavras de um contidas nas do outro, sem acento/caixa — o critério do
    `temu_declaracao.py`) e o MESMO CEP (ou CEP vazio no espelho: pode ser
    ele). O nome confere SEMPRE — CEP sozinho casaria o vizinho de prédio
    cujo pedido ainda não chegou ao espelho. Então:

    1. reimpressão: se a etiqueta já gravada de um concorrente tem ESTE
       código AJ…, é dele (se ele for o único assim e estiver válido);
    2. senão, o concorrente que já tem etiqueta gravada (com outro código)
       já despachou a dele e sai da disputa — cliente recorrente não fica
       ambíguo pra sempre, e a etiqueta nova não sobrescreve a do pedido
       antigo quando o novo ainda nem chegou ao espelho;
    3. casa quando sobra EXATAMENTE UM concorrente, válido (fora de 12/
       83955) e com o mesmo CEP. Cancelado + repedido do mesmo cliente =
       ambíguo → None (fica pra mão; a chave do `nf_nota` resolve).

    Só etiqueta da Anjun entra (`e_etiqueta_anjun`); fatia COM declaração de
    conteúdo (Shopee/TikTok do Upseller, fluxo manual) não entra."""
    if _e_declaracao(texto) or not e_etiqueta_anjun(texto):
        return None
    etq = ler_etiqueta_temu(texto)
    alvo = _tokens_nome(etq.nome)
    if not etq.cep or not alvo or not etq.rastreio:
        return None
    hits: dict[str, CandidatoTemu] = {}
    for c in candidatos:
        cep = _so_digitos(c.cep)
        if cep and cep != etq.cep:
            continue
        toks = _tokens_nome(c.nome)
        if toks and (alvo <= toks or toks <= alvo):
            hits.setdefault(c.numero, c)

    def _escolhe(cs: list[CandidatoTemu]) -> str | None:
        if len(cs) != 1:
            return None
        c = cs[0]
        return c.numero if c.valido and _so_digitos(c.cep) == etq.cep else None

    reimpressao = [c for c in hits.values() if c.rastreios and etq.rastreio in c.rastreios]
    if reimpressao:
        return _escolhe(reimpressao)
    return _escolhe([c for c in hits.values() if c.rastreios is None])
