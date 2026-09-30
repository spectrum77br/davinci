"""Troca do SKU dos anúncios do lote .sp para outro lote (ci > ra > pi).

Eduardo, 30/09/2026: trocar o SKU de TODOS os anúncios que estão com lote .sp
para outro lote existente, kit com todas as peças (dg010.sp+a001.sp →
dg010.pi+a001.pi).

Aqui só a lógica PURA (sem rede e sem banco), para dar para testar:
- escolher o SKU novo de cada .sp (ou validar o que veio do mapa em CSV);
- montar o plano a partir dos vínculos (product_links);
- decidir, com o SKU lido AGORA no marketplace, se troca / já trocado /
  SKU inesperado;
- montar o payload mínimo de cada plataforma e conferir a leitura de volta;
- montar o plano de desfazer a partir do log.

Quem fala com o marketplace e com o banco é `scripts/trocar_sku_anuncios.py`.

Payloads (o mínimo que a API precisa, nada além):
- ML sem variação:  PUT /items/{id}
                    {"attributes":[{"id":"SELLER_SKU","value_name":NOVO}]}
- ML com variação:  PUT /items/{id}
                    {"variations":[{"id":V1},{"id":V2,"attributes":[SELLER_SKU]}]}
  — TODAS as variações pelo id (a que fica de fora o ML apaga); o atributo
  só nas trocadas.
- Shopee:           POST /api/v2/product/update_model
                    {"item_id":I,"model":[{"model_id":M,"model_sku":NOVO}]}
  — só os modelos trocados.
- TikTok:           POST /product/202309/products/{id}/partial_edit
                    {"skus":[{"id":S,"seller_sku":...}]}
  — TODOS os SKUs do produto (SKU que fica de fora é APAGADO), o seller_sku
  atual de cada um exatamente como veio (inclusive malformado), trocando só
  os do plano. Montado sempre a partir da versão MAIS RECENTE (a que o
  partial_edit edita: GET ...?return_under_review_version=true), nunca da
  versão no ar.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.services.vinculo_saude import norm_sku

LOTES_PREFERIDOS: tuple[str, ...] = ("ci", "ra", "pi")
PLATAFORMAS_TROCA: tuple[str, ...] = ("ml", "shopee", "tiktok")

# ".sp" no fim de cada peça do kit ("dg010.sp+a001.sp").
_RE_SP = re.compile(r"\.sp(?=\+|$)", re.IGNORECASE)
# Qualquer lote de 2 letras no fim de cada peça (ci/sp/ra/pi/sa...).
_RE_LOTE = re.compile(r"\.[a-z]{2}(?=\+|$)")

# Resultados que o log usa (uma linha por variação).
TROCARIA = "trocaria"  # dry-run: a troca seria feita
TROCADO = "trocado"  # escrito + leitura de volta confirmou + vínculo religado
TROCADO_SEM_RELIGAR = "trocado_sem_religar"  # confirmou, mas o vínculo não mudou
JA_TROCADO = "ja_trocado"
SKU_INESPERADO = "sku_inesperado"
PENDENTE_AUDITORIA = "pendente_auditoria"  # TikTok aceitou, leitura ainda mostra o antigo
NAO_CONFIRMADO = "nao_confirmado"
PULAR_STATUS = "pular_status"
PULAR_FOTOS = "pular_fotos_demais"
CONTA_SEM_ACESSO = "conta_sem_acesso"
ANUNCIO_NAO_ENCONTRADO = "anuncio_nao_encontrado"
VARIACAO_NAO_ENCONTRADA = "variacao_nao_encontrada"
SEM_ALVO = "sem_alvo"
FORA_DO_PLANO = "fora_do_plano"
ERRO_LEITURA = "erro_leitura"
ERRO_ESCRITA = "erro_escrita"
DIVERGENTE = "divergente_pos_escrita"
ERRO_INTERNO = "erro_interno"
# Gravada ANTES de cada escrita (com fsync): se o processo morrer entre o
# envio e o registro do desfecho, o --desfazer ainda sabe o que foi enviado.
ESCREVENDO = "escrevendo"
FERIAS = "ferias"  # conta em modo férias (Integration.vacation_mode)
# TikTok: já há um produto desta rodada esperando a auditoria; os outros só
# com --tiktok-seguir (piloto primeiro).
TIKTOK_AGUARDANDO_PILOTO = "tiktok_aguardando_piloto"
# Linhas de sincronização de estoque (não são de variação).
SYNC_ENFILEIRADO = "sync_enfileirado"
SYNC_FALHOU = "sync_falhou"


# --------------------------------------------------------------------- SKU


def tem_lote_sp(sku: str | None) -> bool:
    return bool(_RE_SP.search(norm_sku(sku)))


def trocar_lote(sku: str, lote: str) -> str:
    """Troca o lote .sp de TODAS as peças do kit, sobre o SKU NORMALIZADO
    (sem espaços, minúsculo): 'dg010.sp + a001.sp' → 'dg010.ci+a001.ci'."""
    return _RE_SP.sub("." + lote, norm_sku(sku))


def base_sem_lote(sku: str | None) -> str:
    """'dg010.sp+a001.sp' → 'dg010+a001' (compara lotes irmãos)."""
    return _RE_LOTE.sub("", norm_sku(sku))


_PECA_COM_LOTE = re.compile(r"[a-z0-9]+\.[a-z]{2}")


def motivo_alvo_invalido(sku_sp: str, sku_alvo: str | None) -> str | None:
    """None se o alvo serve para o .sp; senão o motivo. Vale para o alvo
    recalculado e para o do --mapa: o alvo não pode ter NENHUMA peça .sp e
    tem de ser o mesmo kit em outro lote (base_sem_lote igual)."""
    if not norm_sku(sku_alvo):
        return "alvo_vazio"
    # Cada peça do alvo tem de ter lote ("dg010.pi"): "dg010" (o produto sem
    # lote, pai da família) tem a mesma base e passaria no teste abaixo.
    if not all(_PECA_COM_LOTE.fullmatch(p) for p in norm_sku(sku_alvo).split("+")):
        return f"alvo_sem_lote:{sku_alvo}"
    if tem_lote_sp(sku_alvo):
        return f"alvo_com_peca_sp:{sku_alvo}"
    if base_sem_lote(sku_alvo) != base_sem_lote(sku_sp):
        return f"alvo_de_outro_kit:{sku_alvo}"
    return None


@dataclass(frozen=True)
class ProdutoInfo:
    id: str
    sku: str
    situacao: str | None


class IndiceProdutos:
    """SKU normalizado → produtos. Mesma regra do Vincular Automático
    (auto_link._SkuIndex): SKU em 2+ produtos é ambíguo e não serve de alvo."""

    def __init__(self, produtos: Iterable[ProdutoInfo]):
        self._por_sku: dict[str, list[ProdutoInfo]] = {}
        for p in produtos:
            k = norm_sku(p.sku)
            if k:
                self._por_sku.setdefault(k, []).append(p)

    def resolver_ativo(self, sku: str | None) -> tuple[ProdutoInfo | None, str]:
        """→ (produto, motivo) com motivo ∈ ok / nao_existe / ambiguo / inativo."""
        cands = self._por_sku.get(norm_sku(sku)) or []
        if not cands:
            return None, "nao_existe"
        if len(cands) > 1:
            return None, "ambiguo"
        p = cands[0]
        if (p.situacao or "").upper() != "A":
            return None, "inativo"
        return p, "ok"


@dataclass(frozen=True)
class Alvo:
    produto: ProdutoInfo | None
    lote: str | None
    motivo: str  # "ok" ou por que não tem alvo


def escolher_alvo(
    sku_sp: str, indice: IndiceProdutos, ordem: tuple[str, ...] = LOTES_PREFERIDOS
) -> Alvo:
    """Primeiro lote (ci > ra > pi) cujo SKU inteiro existe ATIVO num produto
    só. Lote que não existe ou está inativo/excluído passa para o próximo;
    lote AMBÍGUO para tudo (cadastro duplicado precisa de gente)."""
    motivos = []
    for lote in ordem:
        cand = trocar_lote(sku_sp, lote)
        p, m = indice.resolver_ativo(cand)
        if p is not None:
            invalido = motivo_alvo_invalido(sku_sp, p.sku)
            if invalido:
                return Alvo(None, None, invalido)
            return Alvo(p, lote, "ok")
        if m == "ambiguo":
            return Alvo(None, None, f"alvo_ambiguo:{cand}")
        motivos.append(f"{lote}={m}")
    return Alvo(None, None, "sem_alvo:" + ",".join(motivos))


def ler_mapa_csv(texto: str) -> dict[str, str]:
    """CSV com colunas sku_sp,alvo (a do sp_mapa_alvos.csv) → {sku_sp normalizado: alvo}.
    Linha sem alvo fica de fora."""
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(texto)):
        sp = (row.get("sku_sp") or "").strip()
        alvo = (row.get("alvo") or "").strip()
        if sp and alvo:
            out[norm_sku(sp)] = alvo
    return out


def alvo_pelo_mapa(sku_sp: str, mapa: dict[str, str], indice: IndiceProdutos) -> Alvo:
    alvo = mapa.get(norm_sku(sku_sp))
    if not alvo:
        return Alvo(None, None, "sem_alvo:fora_do_mapa")
    invalido = motivo_alvo_invalido(sku_sp, alvo)
    if invalido:
        return Alvo(None, None, invalido)
    p, m = indice.resolver_ativo(alvo)
    if p is None:
        return Alvo(None, None, f"alvo_{m}:{alvo}")
    invalido = motivo_alvo_invalido(sku_sp, p.sku)
    if invalido:
        return Alvo(None, None, invalido)
    lote = None
    mm = re.search(r"\.([a-z]{2})(?:\+|$)", norm_sku(p.sku))
    if mm:
        lote = mm.group(1)
    return Alvo(p, lote, "ok")


# --------------------------------------------------------------------- plano


@dataclass(frozen=True)
class LinkInfo:
    """Uma linha de product_links (+ produto e conta), como vem do banco."""

    link_id: str
    plataforma: str
    integration_id: str
    conta: str
    external_id: str
    variation_id: str | None
    external_sku: str | None
    product_id: str
    product_sku: str
    ferias: bool = False  # Integration.vacation_mode quando o plano foi montado


@dataclass
class PlanoLinha:
    """Uma variação/modelo/SKU a trocar (ou fora do plano, com motivo)."""

    plataforma: str
    integration_id: str
    conta: str
    external_id: str
    variation_id: str | None
    sku_esperado: str  # o que o anúncio deve mostrar AGORA (o .sp)
    sku_novo: str | None
    # (link_id, product_id de, product_id para) — para religar e desfazer.
    links: list[tuple[str, str, str | None]] = field(default_factory=list)
    lote: str | None = None
    motivo_fora: str | None = None
    ferias: bool = False

    @property
    def chave_anuncio(self) -> tuple[str, str]:
        return (self.integration_id, self.external_id)

    @property
    def chave_variacao(self) -> str:
        return _var_key(self.variation_id)


def _var_key(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s in ("", "0") else s


def sku_sp_do_link(link: LinkInfo) -> str | None:
    """O .sp que o anúncio deve ter: o SKU do anúncio quando ele é .sp; senão
    o do produto ligado (external_sku vazio ou de outro lote)."""
    if tem_lote_sp(link.external_sku):
        return (link.external_sku or "").strip()
    if tem_lote_sp(link.product_sku):
        return link.product_sku.strip()
    return None


def montar_plano(
    links: Iterable[LinkInfo],
    alvo_de: Callable[[str], Alvo],
) -> tuple[list[PlanoLinha], list[PlanoLinha]]:
    """→ (plano, fora). Uma linha por variação (vínculos duplicados da mesma
    variação viram uma linha com vários link_ids)."""
    por_chave: dict[tuple[str, str, str], PlanoLinha] = {}
    fora: list[PlanoLinha] = []
    for lk in links:
        sku_sp = sku_sp_do_link(lk)
        if sku_sp is None:
            continue
        chave = (lk.integration_id, str(lk.external_id), _var_key(lk.variation_id))
        existente = por_chave.get(chave)
        if existente is not None:
            if norm_sku(existente.sku_esperado) != norm_sku(sku_sp):
                existente.motivo_fora = existente.motivo_fora or "vinculos_duplicados_conflitantes"
            para = existente.links[0][2] if existente.links else None
            existente.links.append((lk.link_id, lk.product_id, para))
            continue
        linha = PlanoLinha(
            plataforma=lk.plataforma,
            integration_id=lk.integration_id,
            conta=lk.conta,
            external_id=str(lk.external_id),
            variation_id=lk.variation_id,
            sku_esperado=sku_sp,
            sku_novo=None,
            ferias=lk.ferias,
        )
        if lk.plataforma == "amazon":
            linha.motivo_fora = "amazon_nao_renomeia"
        elif lk.plataforma not in PLATAFORMAS_TROCA:
            linha.motivo_fora = f"plataforma_fora:{lk.plataforma}"
        para_id: str | None = None
        if linha.motivo_fora is None:
            alvo = alvo_de(sku_sp)
            if alvo.produto is None:
                linha.motivo_fora = alvo.motivo
            else:
                linha.sku_novo = alvo.produto.sku
                linha.lote = alvo.lote
                para_id = alvo.produto.id
        linha.links.append((lk.link_id, lk.product_id, para_id))
        por_chave[chave] = linha

    plano: list[PlanoLinha] = []
    for linha in por_chave.values():
        (fora if linha.motivo_fora else plano).append(linha)
    ordem = lambda x: (x.plataforma, x.conta.lower(), x.external_id, x.chave_variacao)  # noqa: E731
    plano.sort(key=ordem)
    fora.sort(key=ordem)
    return plano, fora


def agrupar_por_anuncio(plano: Iterable[PlanoLinha]) -> dict[tuple[str, str], list[PlanoLinha]]:
    grupos: dict[tuple[str, str], list[PlanoLinha]] = {}
    for linha in plano:
        grupos.setdefault(linha.chave_anuncio, []).append(linha)
    return grupos


def filtrar_plano(
    plano: Iterable[PlanoLinha],
    *,
    plataforma: str | None = None,
    conta: str | None = None,
    item: str | None = None,
) -> list[PlanoLinha]:
    out = []
    for linha in plano:
        if plataforma and linha.plataforma != plataforma:
            continue
        if conta and linha.conta.strip().lower() != conta.strip().lower():
            continue
        if item and linha.external_id != str(item).strip():
            continue
        out.append(linha)
    return out


# --------------------------------------------------------------------- decisão


def classificar_sku(
    sku_live: str | None,
    sku_esperado: str,
    sku_novo: str,
    *,
    aceitar_irmao: bool = False,
    existe_ativo: Callable[[str], bool] | None = None,
) -> str:
    """O SKU lido agora no anúncio → 'trocar' / 'ja_trocado' / 'sku_inesperado'.

    `aceitar_irmao`: aceita como .sp o mesmo kit em OUTRO lote que não seja o
    novo — SÓ quando esse SKU lido NÃO existe ativo no DaVinci (`existe_ativo`
    devolve False). Caso Barbosa: o anúncio foi trocado à mão para dg090.ci,
    que não existe, e o vínculo ficou no dg090.sp. Um anúncio num lote válido
    e ativo (ex. dg010.pi) nunca é sobrescrito. Sem `existe_ativo` não aceita
    lote irmão nenhum."""
    live = norm_sku(sku_live)
    if not live:
        return SKU_INESPERADO
    if live == norm_sku(sku_novo):
        return JA_TROCADO
    if live == norm_sku(sku_esperado):
        return "trocar"
    if (
        aceitar_irmao
        and existe_ativo is not None
        and base_sem_lote(live) == base_sem_lote(sku_esperado)
        and not existe_ativo(live)
    ):
        return "trocar"
    return SKU_INESPERADO


# --------------------------------------------------------------------- ML


def ml_sku_de(obj: dict) -> tuple[str | None, str | None]:
    """SKU do item/variação e de onde veio: ('...', 'SELLER_SKU') ou
    ('...', 'seller_custom_field') ou (None, None)."""
    for attr in obj.get("attributes") or []:
        if (attr.get("id") or "").upper() == "SELLER_SKU":
            v = (attr.get("value_name") or attr.get("value") or "").strip()
            if v:
                return v, "SELLER_SKU"
    scf = (obj.get("seller_custom_field") or "").strip()
    if scf:
        return scf, "seller_custom_field"
    return None, None


def ml_motivo_status(item: dict, *, incluir_waiting_for_patch: bool = False) -> str | None:
    """Motivo para NÃO mexer no anúncio por causa do estado dele, ou None.
    Em revisão / inativo / fechado não aceitam edição (o envio de estoque
    também pula). `incluir_waiting_for_patch` deixa passar o em revisão cujo
    único sub_status é waiting_for_patch (o ML costuma aceitar edição)."""
    status = (item.get("status") or "").lower()
    sub = {str(x).lower() for x in (item.get("sub_status") or [])}
    if status in ("closed", "inactive"):
        return f"ml_{status}" + (f"[{','.join(sorted(sub))}]" if sub else "")
    if status == "under_review":
        if incluir_waiting_for_patch and sub == {"waiting_for_patch"}:
            return None
        return "ml_under_review" + (f"[{','.join(sorted(sub))}]" if sub else "")
    return None


def ml_localizar_variacao(
    item: dict, variation_id: str | None, sku_esperado: str
) -> tuple[dict | None, str]:
    """(variação, como achou): 'id' / 'sku' (id mudou, achou pelo SKU único)
    / 'item' (anúncio sem variação) / 'nao_encontrada'."""
    variacoes = item.get("variations") or []
    vid = _var_key(variation_id)
    if not variacoes:
        return (item, "item") if not vid else (None, "nao_encontrada")
    if vid:
        for v in variacoes:
            if str(v.get("id")) == vid:
                return v, "id"
    achadas = [v for v in variacoes if norm_sku(ml_sku_de(v)[0]) == norm_sku(sku_esperado)]
    if len(achadas) == 1:
        return achadas[0], "sku"
    return None, "nao_encontrada"


def _attr_seller_sku(novo: str) -> dict:
    return {"id": "SELLER_SKU", "value_name": novo}


def ml_payload(item: dict, trocas: dict[str, str]) -> dict:
    """`trocas`: {variation_id: sku_novo}; chave '' = o próprio item (sem variação)."""
    variacoes = item.get("variations") or []
    if not variacoes:
        novo = trocas.get("")
        if not novo:
            raise ValueError("item sem variação precisa da troca na chave ''")
        return {"attributes": [_attr_seller_sku(novo)]}
    ids = {str(v.get("id")) for v in variacoes}
    faltando = set(trocas) - ids
    if faltando:
        raise ValueError(f"variações fora do anúncio: {sorted(faltando)}")
    out = []
    for v in variacoes:
        vid = str(v.get("id"))
        entrada: dict[str, Any] = {"id": int(vid) if vid.isdigit() else vid}
        if vid in trocas:
            entrada["attributes"] = [_attr_seller_sku(trocas[vid])]
        out.append(entrada)
    return {"variations": out}


def _txt(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _attr_valores(obj: dict, *, sem: frozenset[str] = frozenset({"SELLER_SKU"})) -> dict:
    """{id do atributo: (value_id, value_name)} — o SELLER_SKU fica de fora
    (a troca dele é conferida à parte)."""
    out: dict[str, tuple[str, str]] = {}
    for a in obj.get("attributes") or []:
        aid = _txt(a.get("id")).upper()
        if aid and aid not in sem:
            out[aid] = (_txt(a.get("value_id")), _txt(a.get("value_name")))
    return out


def _ml_combinacoes(v: dict) -> list[tuple[str, str, str]]:
    return sorted(
        (
            _txt(a.get("id") or a.get("name")).upper(),
            _txt(a.get("value_id")),
            _txt(a.get("value_name")),
        )
        for a in v.get("attribute_combinations") or []
    )


def _ml_fotos_ids(obj: dict) -> list[str]:
    """picture_ids da variação; no anúncio (ou item sem variação), os ids de
    `pictures`. A ordem conta (a 1ª é a capa)."""
    if "picture_ids" in obj:
        return [_txt(x) for x in obj.get("picture_ids") or []]
    return [_txt(p.get("id")) for p in obj.get("pictures") or [] if isinstance(p, dict)]


def _comparar_attrs(onde: str, antes: dict, depois: dict, problemas: list[str]) -> None:
    a, d = _attr_valores(antes), _attr_valores(depois)
    sumiram = sorted(set(a) - set(d))
    if sumiram:
        problemas.append(f"atributos_sumiram[{onde}]: {sumiram}")
    for aid in sorted(set(a) & set(d)):
        if a[aid] != d[aid]:
            problemas.append(f"atributo_mudou[{onde}]: {aid} {a[aid]} → {d[aid]}")


def _status_mudou(antes: dict, depois: dict, campo: str = "status") -> str | None:
    sa, sd = _txt(antes.get(campo)).lower(), _txt(depois.get(campo)).lower()
    return None if sa == sd else f"status_mudou: {sa or '-'} → {sd or '-'}"


@dataclass
class Conferencia:
    confirmadas: set[str] = field(default_factory=set)  # chaves com o SKU novo
    pendentes: set[str] = field(default_factory=set)  # ainda com o SKU antigo
    problemas: list[str] = field(default_factory=list)  # efeito colateral: PARAR


def _zerou(antes: Any, depois: Any) -> bool:
    """Estoque que era positivo e virou 0 na releitura: assinatura de uma
    escrita que apagou o estoque (a troca de SKU não pode mexer nele)."""
    try:
        return int(antes or 0) > 0 and int(depois or 0) == 0
    except (TypeError, ValueError):
        return False


def ml_conferir(antes: dict, depois: dict, trocas: dict[str, str]) -> Conferencia:
    """Leitura de volta × leitura de antes. Qualquer diferença além do
    SELLER_SKU das variações trocadas é problema (PARA tudo): status do
    anúncio, variações, fotos (picture_ids / pictures), attribute_combinations,
    atributos (id E valor), preço, estoque zerado, SKU de outra variação."""
    c = Conferencia()
    mudou = _status_mudou(antes, depois)
    if mudou:
        c.problemas.append(mudou)
    va = {str(v.get("id")): v for v in antes.get("variations") or []}
    vd = {str(v.get("id")): v for v in depois.get("variations") or []}
    if set(va) != set(vd):
        c.problemas.append(f"variacoes_mudaram: antes={sorted(va)} depois={sorted(vd)}")
        return c
    if not va:
        va, vd = {"": antes}, {"": depois}
    else:
        # O PUT só mandou variações: nada no nível do anúncio pode mudar.
        _comparar_attrs("item", antes, depois, c.problemas)
        if _ml_fotos_ids(antes) != _ml_fotos_ids(depois):
            c.problemas.append("fotos_mudaram[item]")
    for vid, v_antes in va.items():
        v_depois = vd[vid]
        sku_d = ml_sku_de(v_depois)[0]
        if _zerou(v_antes.get("available_quantity"), v_depois.get("available_quantity")):
            c.problemas.append(f"estoque_zerou[{vid}]")
        p_antes, p_depois = v_antes.get("price"), v_depois.get("price")
        if str(p_antes) != str(p_depois):
            c.problemas.append(f"preco_mudou[{vid}]: {p_antes} → {p_depois}")
        if _ml_fotos_ids(v_antes) != _ml_fotos_ids(v_depois):
            c.problemas.append(f"fotos_mudaram[{vid}]")
        if _ml_combinacoes(v_antes) != _ml_combinacoes(v_depois):
            c.problemas.append(f"combinacoes_mudaram[{vid}]")
        _comparar_attrs(vid, v_antes, v_depois, c.problemas)
        if vid in trocas:
            if norm_sku(sku_d) == norm_sku(trocas[vid]):
                c.confirmadas.add(vid)
            else:
                c.pendentes.add(vid)
        elif norm_sku(sku_d) != norm_sku(ml_sku_de(v_antes)[0]):
            c.problemas.append(
                f"sku_mudou_fora_do_plano[{vid}]: {ml_sku_de(v_antes)[0]} → {sku_d}"
            )
    return c


def ml_classificar_erro(status_code: int, texto: str) -> str:
    t = (texto or "").lower()
    if "item.pictures.max" in t:
        return PULAR_FOTOS
    if status_code == 401:
        return CONTA_SEM_ACESSO
    return ERRO_ESCRITA


def ml_fotos(item: dict) -> int:
    return len(item.get("pictures") or [])


# --------------------------------------------------------------------- Shopee

_SHOPEE_STATUS_PULAR = {"BANNED", "SELLER_DELETE", "SHOPEE_DELETE", "REVIEWING"}


def shopee_motivo_status(item_status: str | None) -> str | None:
    s = (item_status or "").upper()
    if s in _SHOPEE_STATUS_PULAR:
        return f"shopee_{s.lower()}"
    return None


def shopee_payload(item_id: str | int, trocas: dict[str, str]) -> dict:
    if not trocas:
        raise ValueError("sem modelos para trocar")
    return {
        "item_id": int(item_id),
        "model": [
            {"model_id": int(mid), "model_sku": novo} for mid, novo in sorted(trocas.items())
        ],
    }


def _shopee_estoque(m: dict) -> Any:
    v2 = ((m.get("stock_info_v2") or {}).get("summary_info") or {}).get("total_available_stock")
    if v2 is not None:
        return v2
    return (m.get("stock_info") or {}).get("total_available_stock") if isinstance(
        m.get("stock_info"), dict
    ) else None


def _shopee_preco(m: dict) -> tuple[str, str] | None:
    pi = m.get("price_info")
    if isinstance(pi, list):
        pi = pi[0] if pi else None
    if not isinstance(pi, dict):
        return None
    return (_txt(pi.get("current_price")), _txt(pi.get("original_price")))


def shopee_conferir(
    antes: list[dict],
    depois: list[dict],
    trocas: dict[str, str],
    *,
    status_antes: str | None = None,
    status_depois: str | None = None,
) -> Conferencia:
    """Modelos lidos de volta × antes: modelos, preço (atual e original),
    estoque zerado, SKU de outro modelo e o item_status do anúncio."""
    c = Conferencia()
    if _txt(status_antes).upper() != _txt(status_depois).upper():
        c.problemas.append(f"status_mudou: {status_antes or '-'} → {status_depois or '-'}")
    ma = {str(m.get("model_id")): m for m in antes}
    md = {str(m.get("model_id")): m for m in depois}
    if set(ma) != set(md):
        c.problemas.append(f"modelos_mudaram: antes={sorted(ma)} depois={sorted(md)}")
        return c
    for mid, m_antes in ma.items():
        sku_d = (md[mid].get("model_sku") or "").strip()
        if _zerou(_shopee_estoque(m_antes), _shopee_estoque(md[mid])):
            c.problemas.append(f"estoque_zerou[{mid}]")
        pa, pd = _shopee_preco(m_antes), _shopee_preco(md[mid])
        if pa != pd:
            c.problemas.append(f"preco_mudou[{mid}]: {pa} → {pd}")
        if mid in trocas:
            (c.confirmadas if norm_sku(sku_d) == norm_sku(trocas[mid]) else c.pendentes).add(mid)
        elif norm_sku(sku_d) != norm_sku(m_antes.get("model_sku")):
            c.problemas.append(
                f"sku_mudou_fora_do_plano[{mid}]: {m_antes.get('model_sku')} → {sku_d}"
            )
    return c


# --------------------------------------------------------------------- TikTok

_TIKTOK_OK = {"ACTIVATE"}
_TIKTOK_REPROVADOS = {"FAILED"}
_TIKTOK_AUDIT_OK = {"", "APPROVED"}
_TIKTOK_AUDIT_REPROVADA = {"REJECTED", "FAILED"}
# Status da versão editada que, se aparecerem depois da escrita, PARAM tudo
# (PENDING = em auditoria, esperado depois de editar; não para).
_TIKTOK_STATUS_RUINS = {
    "FAILED", "DELETED", "FREEZE", "SELLER_DEACTIVATED", "PLATFORM_DEACTIVATED", "DRAFT",
}
# Parâmetro do GET /product/202309/products/{id} que devolve a versão MAIS
# RECENTE (a que está em auditoria depois de uma edição); sem ele vem a versão
# no ar. Doc da TikTok (Get Product 202309): return_under_review_version.
TIKTOK_PARAM_VERSAO_EM_AUDITORIA = {"return_under_review_version": "true"}


def tiktok_auditoria(produto: dict) -> str:
    return _txt((produto.get("audit") or {}).get("status")).upper()


def tiktok_motivo_status(
    produto: dict,
    *,
    incluir_reprovados: bool = False,
    aceitar_em_auditoria: bool = False,
) -> str | None:
    """Padrão: só produto no ar (ACTIVATE) com a última auditoria aprovada.
    `incluir_reprovados`: também o no ar com auditoria REPROVADA e o FAILED
    (fora do ar, reprovado) — nunca um com auditoria em andamento.
    `aceitar_em_auditoria` (só no --desfazer): o no ar com uma edição em
    auditoria (a nossa), para desfazer pela versão em auditoria.
    Nunca rascunho, congelado, desativado ou excluído."""
    status = _txt(produto.get("status")).upper()
    audit = tiktok_auditoria(produto)
    if status in _TIKTOK_OK and audit in _TIKTOK_AUDIT_OK:
        return None
    if incluir_reprovados and audit in _TIKTOK_AUDIT_OK | _TIKTOK_AUDIT_REPROVADA and (
        status in _TIKTOK_OK or status in _TIKTOK_REPROVADOS
    ):
        return None
    if (
        aceitar_em_auditoria
        and status in _TIKTOK_OK
        and audit not in _TIKTOK_AUDIT_OK | _TIKTOK_AUDIT_REPROVADA
    ):
        return None
    sufixo = f"[audit={audit.lower()}]" if audit else ""
    return f"tiktok_{status.lower() or 'sem_status'}{sufixo}"


def tiktok_motivo_nao_religar(
    no_ar: dict, editada: dict, sku_id: str, sku_novo: str
) -> str | None:
    """None se o vínculo pode ir para o produto novo AGORA: produto ACTIVATE,
    auditoria aprovada/vazia (no ar e na versão mais recente) e o SKU novo JÁ
    na versão no ar. Senão o motivo (a edição está em auditoria: a varredura
    diária religa quando a TikTok aprovar)."""
    status = _txt(no_ar.get("status")).upper()
    if status not in _TIKTOK_OK:
        return f"status={status.lower() or '-'}"
    for nome, p in (("no_ar", no_ar), ("versao_recente", editada)):
        a = tiktok_auditoria(p)
        if a not in _TIKTOK_AUDIT_OK:
            return f"auditoria_{nome}={a.lower()}"
    s, _ = tiktok_sku_de(no_ar, sku_id)
    if s is None or norm_sku(s.get("seller_sku")) != norm_sku(sku_novo):
        return "no_ar_ainda_sem_o_sku_novo"
    return None


def tiktok_payload(produto: dict, trocas: dict[str, str]) -> dict:
    """TODOS os SKUs (id + seller_sku atual), trocando só os do plano. O
    seller_sku que não troca vai EXATAMENTE como veio (malformado inclusive);
    SKU sem seller_sku vai só com o id."""
    skus = produto.get("skus") or []
    ids = {str(s.get("id")) for s in skus}
    faltando = set(trocas) - ids
    if faltando:
        raise ValueError(f"SKUs fora do produto: {sorted(faltando)}")
    out = []
    for s in skus:
        sid = str(s.get("id"))
        entrada: dict[str, Any] = {"id": sid}
        if sid in trocas:
            entrada["seller_sku"] = trocas[sid]
        elif s.get("seller_sku") is not None and s.get("seller_sku") != "":
            entrada["seller_sku"] = s.get("seller_sku")
        out.append(entrada)
    return {"skus": out}


def _tk_sales_attrs(s: dict) -> list[tuple[str, str]]:
    return sorted(
        (
            str(a.get("id") or a.get("name") or ""),
            str(a.get("value_id") or a.get("value_name") or ""),
        )
        for a in s.get("sales_attributes") or []
    )


def _tk_estoque(s: dict) -> int:
    total = 0
    for inv in s.get("inventory") or []:
        try:
            total += int(inv.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _tk_preco(s: dict) -> str:
    p = s.get("price") or {}
    return str(p.get("amount") or p.get("sale_price") or "")


def tiktok_conferir(
    antes: dict, depois: dict, trocas: dict[str, str], *, versao: str = "no_ar"
) -> Conferencia:
    """Uma versão do produto lida de volta × a mesma versão lida antes.
    `versao="no_ar"`: qualquer mudança do status do produto PARA tudo.
    `versao="recente"` (a em auditoria): PENDING depois de editar é o
    esperado; só para se virar FAILED/DELETED/FREEZE/desativado/rascunho."""
    c = Conferencia()
    if versao == "no_ar":
        mudou = _status_mudou(antes, depois)
        if mudou:
            c.problemas.append(mudou)
    else:
        sa_, sd_ = _txt(antes.get("status")).upper(), _txt(depois.get("status")).upper()
        if sd_ != sa_ and sd_ in _TIKTOK_STATUS_RUINS:
            c.problemas.append(f"status_versao_recente: {sa_ or '-'} → {sd_}")
    sa = {str(s.get("id")): s for s in antes.get("skus") or []}
    sd = {str(s.get("id")): s for s in depois.get("skus") or []}
    if set(sa) != set(sd):
        c.problemas.append(f"skus_mudaram: antes={sorted(sa)} depois={sorted(sd)}")
        return c
    for sid, s_antes in sa.items():
        s_depois = sd[sid]
        if _tk_sales_attrs(s_antes) != _tk_sales_attrs(s_depois):
            c.problemas.append(f"variacao_mudou[{sid}]")
        if _zerou(_tk_estoque(s_antes), _tk_estoque(s_depois)):
            c.problemas.append(f"estoque_zerou[{sid}]")
        if _tk_preco(s_antes) != _tk_preco(s_depois):
            c.problemas.append(f"preco_mudou[{sid}]: {_tk_preco(s_antes)} → {_tk_preco(s_depois)}")
        sku_d = s_depois.get("seller_sku")
        if sid in trocas:
            (c.confirmadas if norm_sku(sku_d) == norm_sku(trocas[sid]) else c.pendentes).add(sid)
        elif (sku_d or "") != (s_antes.get("seller_sku") or ""):
            c.problemas.append(
                f"sku_mudou_fora_do_plano[{sid}]: {s_antes.get('seller_sku')} → {sku_d}"
            )
    return c


def tiktok_sku_de(produto: dict, sku_id: str | None) -> tuple[dict | None, str]:
    skus = produto.get("skus") or []
    vid = _var_key(sku_id)
    if vid:
        for s in skus:
            if str(s.get("id")) == vid:
                return s, "id"
        return None, "nao_encontrada"
    if len(skus) == 1:
        return skus[0], "unico"
    return None, "nao_encontrada"


# --------------------------------------------------------------------- limite


def eh_limite(plataforma: str, status_code: int | None, corpo: str | None) -> bool:
    """Resposta de 'devagar' (429 / busy / rate limit)."""
    if status_code == 429:
        return True
    t = (corpo or "").lower()
    if plataforma == "shopee":
        return "error_busy" in t or "too many" in t or "rate limit" in t or "error_rate" in t
    if plataforma == "tiktok":
        return "too many" in t or "rate limit" in t or "request limit" in t
    return "too many requests" in t or "local_rate_limited" in t


# --------------------------------------------------------------------- log / desfazer


def registro(
    linha: PlanoLinha,
    resultado: str,
    *,
    modo: str,
    sku_antes: str | None = None,
    sku_depois: str | None = None,
    erro: str | None = None,
    **extra: Any,
) -> dict:
    """Uma linha do JSONL (antes/depois de UMA variação)."""
    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "modo": modo,
        "plataforma": linha.plataforma,
        "conta": linha.conta,
        "integration_id": linha.integration_id,
        "external_id": linha.external_id,
        "variation_id": linha.variation_id,
        "sku_esperado": linha.sku_esperado,
        "sku_novo": linha.sku_novo,
        "sku_antes": sku_antes,
        "sku_depois": sku_depois,
        "resultado": resultado,
        "erro": erro,
        "links": [list(t) for t in linha.links],
        **extra,
    }


# Linhas cuja escrita FOI (ou pode ter sido) enviada ao marketplace. O
# --desfazer relê o anúncio e só escreve se encontrar o SKU novo, então incluir
# uma linha em que a escrita não pegou é inofensivo (sai ja_trocado).
RESULTADOS_COM_ESCRITA = (
    TROCADO,
    TROCADO_SEM_RELIGAR,
    PENDENTE_AUDITORIA,
    NAO_CONFIRMADO,
    DIVERGENTE,
    ERRO_INTERNO,
)
# Desfecho de uma 'escrevendo' que diz que o marketplace RECUSOU a escrita.
RESULTADOS_ESCRITA_RECUSADA = (ERRO_ESCRITA, PULAR_FOTOS, CONTA_SEM_ACESSO)
RESULTADOS_DESFAZIVEIS = (*RESULTADOS_COM_ESCRITA, ESCREVENDO)


def plano_desfazer(registros: Iterable[dict]) -> list[PlanoLinha]:
    """Log de um --executar → plano inverso: o anúncio deve estar com o SKU
    novo e volta para o antigo; o vínculo volta para o produto de antes.

    Só linhas do modo 'executar'. Por variação vale a ÚLTIMA escrita enviada
    (linha 'escrevendo' + o desfecho dela, ou um desfecho com escrita), e não
    a última linha: uma rodada seguinte que viu a variação 'ja_trocado' não
    apaga a troca de antes. Uma 'escrevendo' sem desfecho (o processo morreu
    entre o envio e o registro) entra; uma cujo desfecho foi recusa do
    marketplace (erro_escrita/fotos/sem acesso) não."""
    escolhidas: dict[tuple[str, str, str], tuple[dict | None, dict | None]] = {}
    pendentes: dict[tuple[str, str, str], dict] = {}
    for r in registros:
        if r.get("modo") != "executar" or r.get("tipo") == "sync":
            continue
        if not r.get("integration_id") or not r.get("external_id"):
            continue
        k = (str(r["integration_id"]), str(r["external_id"]), _var_key(r.get("variation_id")))
        res = r.get("resultado")
        if res == ESCREVENDO:
            pendentes[k] = r
            continue
        esc = pendentes.pop(k, None)
        if esc is not None:
            if res not in RESULTADOS_ESCRITA_RECUSADA:
                escolhidas[k] = (esc, r)
        elif res in RESULTADOS_COM_ESCRITA:
            escolhidas[k] = (None, r)
    for k, esc in pendentes.items():
        escolhidas[k] = (esc, None)

    out = []
    for esc, fim in escolhidas.values():
        base = fim or esc or {}
        # sku_antes = o que o anúncio tinha antes da escrita (a 'escrevendo'
        # sempre tem; o erro_interno pode não ter); sku_novo = o que foi escrito.
        antes = (fim or {}).get("sku_antes") or (esc or {}).get("sku_antes")
        depois = base.get("sku_novo") or (esc or {}).get("sku_novo")
        if not antes or not depois or norm_sku(antes) == norm_sku(depois):
            continue
        links_orig = base.get("links") or (esc or {}).get("links") or []
        links = [(lid, para, de) for lid, de, para in links_orig]
        # a chave exata da variação que foi escrita (ML: o id achado pelo SKU
        # quando o variation_id do vínculo estava velho)
        chave = (esc or {}).get("chave", base.get("chave"))
        variation_id = _var_key(chave) or base.get("variation_id")
        out.append(
            PlanoLinha(
                plataforma=base["plataforma"],
                integration_id=str(base["integration_id"]),
                conta=base.get("conta") or "",
                external_id=str(base["external_id"]),
                variation_id=variation_id,
                sku_esperado=depois,
                sku_novo=antes,
                links=links,
            )
        )
    out.sort(key=lambda x: (x.plataforma, x.conta.lower(), x.external_id, x.chave_variacao))
    return out


def resumir(registros: Iterable[dict]) -> dict:
    por = Counter()
    motivos = Counter()
    scf: dict[tuple[str, str, str], dict] = {}
    sync = Counter()
    sync_falhas: list[dict] = []
    for r in registros:
        if r.get("tipo") == "sync":
            sync[r.get("resultado")] += 1
            if r.get("resultado") == SYNC_FALHOU:
                sync_falhas.append({"product_id": r.get("product_id"), "erro": r.get("erro")})
            continue
        por[(r.get("plataforma"), r.get("resultado"))] += 1
        if r.get("resultado") in (FORA_DO_PLANO, PULAR_STATUS, SKU_INESPERADO, ERRO_ESCRITA,
                                  ERRO_LEITURA, VARIACAO_NAO_ENCONTRADA, DIVERGENTE,
                                  NAO_CONFIRMADO, ERRO_INTERNO, PENDENTE_AUDITORIA):
            motivos[(r.get("plataforma"), r.get("resultado"), str(r.get("erro"))[:80])] += 1
        if r.get("seller_custom_field_sp"):
            k = (str(r.get("conta")), str(r.get("external_id")), _var_key(r.get("variation_id")))
            scf[k] = {
                "conta": r.get("conta"),
                "external_id": r.get("external_id"),
                "variation_id": r.get("variation_id"),
                "seller_custom_field": r.get("seller_custom_field"),
            }
    out: dict[str, Any] = {}
    for (plat, res), n in sorted(por.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        out.setdefault(plat, {})[res] = n
    return {
        "por_plataforma": out,
        "motivos": [
            {"plataforma": p, "resultado": r, "motivo": m, "n": n}
            for (p, r, m), n in motivos.most_common()
        ],
        # ML: variações cujo seller_custom_field continua .sp (a troca só
        # mexe no SELLER_SKU). Conferir no Bling o 1º pedido depois do piloto.
        "ml_seller_custom_field_sp": {
            "n": len(scf),
            "lista": sorted(scf.values(), key=lambda x: (str(x["conta"]), str(x["external_id"]))),
        },
        "sync_estoque": {**dict(sync), "falhas": sync_falhas},
    }


def linha_para_dict(linha: PlanoLinha) -> dict:
    return asdict(linha)
