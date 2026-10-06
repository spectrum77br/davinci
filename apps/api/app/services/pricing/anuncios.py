"""Resolvedor único de anúncios da Tabela de Preços, por canal (Catálogo ML, 06/10/2026).

Quem decide PARA QUAIS anúncios vai o preço de uma célula (conta × linha da
tabela). O mesmo código serve o envio (`push.push_one`), a informação de cada
célula de catálogo no `/grid` e o `/actual-prices` — antes eram três regras
parecidas e não iguais.

- canal 'kit' (a conta de sempre): a regra que já existia (tipo do anúncio,
  SKU por departamento, kits primeiro no ML celular/eletro) MENOS os anúncios
  marcados como catálogo (decisão D3: a coluna de Kit não manda mais preço
  direto para anúncio de catálogo; o anúncio comum, inclusive o "gêmeo"
  sincronizado, continua recebendo).
- canal 'catalogo' (coluna de catálogo, "filha" de uma conta ML de kit): só
  anúncios com `catalog_listing` TRUE (NULL não serve), do tipo da conta
  (clássico/premium, obrigatório), SKU simples (sem '+'); celular/eletro casam
  pelo código base, mala pelo SKU exato. Cada anúncio volta com o motivo de
  bloqueio, se houver (sincronizado/pausado/em_revisao/encerrado).
  Anúncio de catálogo que casa com MAIS DE UMA linha que manda preço (código
  base igual: uaf001m1 110/220 × 2L) fica de fora das duas — 'mesmo_anuncio'
  (`separar_anuncios_disputados`): senão o último preço enviado vale.

Módulo folha de propósito: `marketplaces/ml.py` importa daqui as mesmas regras
de status para conferir o item vivo antes do PUT.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.services.pricing.sku_match import (
    dedup_links_for_push,
    ml_listing_type_for_account,
    sku_casa_no_departamento,
    variants_of,
)

if TYPE_CHECKING:
    from app.models import ProductLink

CANAL_KIT = "kit"
CANAL_CATALOGO = "catalogo"

# D2 (o dono ainda vai confirmar): anúncio de catálogo SINCRONIZADO com um
# anúncio comum (item_relations não vazio) não recebe o preço da coluna
# Catálogo — o ML replica o preço entre os dois e quem manda é a coluna de Kit
# pelo anúncio comum. Trocar para False libera o envio (vale para a tela, o
# resolvedor e a conferência do item vivo no ML).
CATALOGO_SINCRONIZADO_BLOQUEIA = True

# Status do anúncio (como o ML devolve / a varredura grava) → motivo de não
# enviar ao catálogo (D6). 'inactive' é anúncio parado pelo ML: igual a pausado.
_MOTIVO_POR_STATUS = {
    "paused": "pausado",
    "inactive": "pausado",
    "under_review": "em_revisao",
    "closed": "encerrado",
}

# Anúncio de catálogo que receberia o preço de duas linhas da tabela.
MOTIVO_MESMO_ANUNCIO = "mesmo_anuncio"

# Quando todos os anúncios da célula estão bloqueados, o motivo mostrado é o
# primeiro desta lista que aparecer (o mais "de regra" antes do "de estado").
_ORDEM_BLOQUEIO = (MOTIVO_MESMO_ANUNCIO, "sincronizado", "pausado", "em_revisao", "encerrado")

_TEXTO_MOTIVO = {
    MOTIVO_MESMO_ANUNCIO: (
        "também casa com a linha {outras} e receberia os dois preços — deixe o "
        "preço de catálogo só na linha certa"
    ),
    "sincronizado": "sincronizado com o anúncio comum {rel} — o preço vem da coluna de Kit",
    "pausado": "pausado — não envia",
    "em_revisao": "em revisão no Mercado Livre — não envia",
    "encerrado": "encerrado — não envia",
}


def motivo_por_status(status: str | None) -> str | None:
    """'pausado' / 'em_revisao' / 'encerrado' para o status do anúncio, ou None."""
    return _MOTIVO_POR_STATUS.get((status or "").strip().lower())


def relacionados_do_link(link: ProductLink) -> list[str]:
    """Anúncios comuns ligados ao de catálogo (`catalogo_relacionado`)."""
    return [p.strip() for p in (link.catalogo_relacionado or "").split(",") if p.strip()]


def motivo_bloqueio_catalogo(link: ProductLink) -> str | None:
    """Por que este anúncio de catálogo não recebe o preço (None = recebe)."""
    if link.morto_desde is not None:
        return "encerrado"
    motivo = motivo_por_status(link.anuncio_status)
    if motivo:
        return motivo
    if CATALOGO_SINCRONIZADO_BLOQUEIA and relacionados_do_link(link):
        return "sincronizado"
    return None


@dataclass(slots=True)
class AnuncioBloqueado:
    link: ProductLink
    motivo: str
    # Só em 'mesmo_anuncio': as outras linhas (SKU) que também mandariam preço.
    outras_linhas: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Resolucao:
    # Anúncios que recebem o preço.
    links: list[ProductLink] = field(default_factory=list)
    # Só no canal catálogo: anúncios que casaram mas não recebem (e por quê).
    bloqueados: list[AnuncioBloqueado] = field(default_factory=list)
    # Canal catálogo numa conta sem tipo clássico/premium válido.
    sem_tipo: bool = False

    @property
    def todos(self) -> list[ProductLink]:
        return [*self.links, *(b.link for b in self.bloqueados)]


def _casa_catalogo(sku: str, *, dept: str, full: set[str], base: set[str]) -> bool:
    if not sku or "+" in sku:
        return False
    if dept == "mala":
        return sku in full
    # celular/eletro: código base (parte antes do primeiro ponto)
    return sku.split(".", 1)[0] in base


def resolver_anuncios(
    links: Iterable[ProductLink],
    sku_por_produto: Mapping[UUID, str],
    *,
    pricing_sku: str | None,
    dept: str | None,
    plataforma: str,
    canal: str,
    listing_type_conta: str | None,
) -> Resolucao:
    """Quais anúncios recebem o preço desta célula.

    `links` são os vínculos da integração (a da BASE, no canal catálogo) — pode
    vir um superconjunto; `sku_por_produto` é {products.id: sku} desses
    vínculos. Função pura: quem chama carrega do banco (uma vez por
    integração no /grid)."""
    variantes = variants_of(pricing_sku)
    if not variantes:
        return Resolucao()
    full = {s.lower() for s in variantes}
    base = {s.split(".", 1)[0].lower() for s in variantes}
    dept_lc = (dept or "celular").lower()

    if canal == CANAL_CATALOGO:
        if plataforma != "ml":
            return Resolucao()
        tipo = ml_listing_type_for_account(listing_type_conta)
        if not tipo:
            return Resolucao(sem_tipo=True)
        casados = [
            lk
            for lk in links
            if lk.catalog_listing is True
            and (lk.listing_type or "") == tipo
            and _casa_catalogo(
                (sku_por_produto.get(lk.product_id) or "").lower(),
                dept=dept_lc, full=full, base=base,
            )
        ]
        out = Resolucao()
        for lk in dedup_links_for_push("ml", casados):
            motivo = motivo_bloqueio_catalogo(lk)
            if motivo:
                out.bloqueados.append(AnuncioBloqueado(lk, motivo))
            else:
                out.links.append(lk)
        return out

    # canal kit — a regra de sempre, sem os anúncios de catálogo (D3).
    candidatos = list(links)
    if plataforma == "ml":
        candidatos = [lk for lk in candidatos if lk.catalog_listing is not True]
        tipo = ml_listing_type_for_account(listing_type_conta)
        if tipo:
            candidatos = [lk for lk in candidatos if (lk.listing_type or "") == tipo]
    casados = [
        lk
        for lk in candidatos
        if sku_casa_no_departamento(
            (sku_por_produto.get(lk.product_id) or "").lower(),
            dept=dept_lc, sku_full_set=full, sku_base_set=base,
        )
    ]
    # ML celular/eletro: havendo kit, só os kits (paridade SSH).
    if dept_lc not in ("mala", "catalogo") and plataforma == "ml":
        if any("+" in (sku_por_produto.get(lk.product_id) or "") for lk in casados):
            casados = [lk for lk in casados if "+" in (sku_por_produto.get(lk.product_id) or "")]
    return Resolucao(links=dedup_links_for_push(plataforma, casados))


def celula_manda_preco(outcome: Any, override: Any = None) -> bool:
    """A célula manda preço se for enviada? (tem preço e não é NA). É o que
    faz uma linha disputar um anúncio de catálogo com outra."""
    if getattr(outcome, "price", None) is None:
        return False
    if getattr(outcome, "source", None) == "disabled":
        return False
    cs = getattr(override, "cell_status", None) if override is not None else None
    return getattr(cs, "value", cs) != "NA"


def separar_anuncios_disputados(
    resolucoes: Mapping[Hashable, Resolucao],
    linhas: Mapping[Hashable, str],
) -> None:
    """Canal catálogo, UMA coluna: o anúncio que está em `links` de mais de
    uma linha recebe o preço das duas, e fica o último enviado (Eletro: a
    linha 2L, custo 60, mandaria o preço dela para o catálogo da 110V, custo
    240). Esse anúncio sai das linhas envolvidas e vira bloqueado
    'mesmo_anuncio', com as outras linhas. Muda as resoluções no lugar.

    `resolucoes` = {linha: Resolucao} só das linhas que mandam preço
    (`celula_manda_preco`); `linhas` = {linha: SKU da linha} para o texto."""
    donos: dict[str, list[Hashable]] = defaultdict(list)
    for chave, res in resolucoes.items():
        for lk in res.links:
            donos[lk.external_id].append(chave)
    disputados = {ext: chaves for ext, chaves in donos.items() if len(chaves) > 1}
    if not disputados:
        return
    for chave, res in resolucoes.items():
        livres: list[ProductLink] = []
        for lk in res.links:
            chaves = disputados.get(lk.external_id)
            if not chaves:
                livres.append(lk)
                continue
            outras = sorted(linhas.get(c) or "?" for c in chaves if c != chave)
            res.bloqueados.append(AnuncioBloqueado(lk, MOTIVO_MESMO_ANUNCIO, outras))
        res.links = livres


# ------------------------------------------------------------ célula do /grid

TEXTO_BLOQUEIO = {
    "sem_tipo": (
        "Conta sem tipo (clássico/premium): preencha o tipo da conta de kit para "
        "enviar ao catálogo"
    ),
    "sem_anuncio": "Sem anúncio de catálogo vinculado nesta conta",
    "sem_preco_catalogo": "Produto sem preço de catálogo (coluna Catálogo em Produtos)",
}


def _texto_anuncio(b: AnuncioBloqueado) -> str:
    rel = ", ".join(relacionados_do_link(b.link)) or "?"
    outras = ", ".join(b.outras_linhas) or "?"
    return f"{b.link.external_id} " + _TEXTO_MOTIVO[b.motivo].format(rel=rel, outras=outras)


def info_celula_catalogo(
    resolucao: Resolucao,
    *,
    sem_preco: bool,
    sem_integracao: bool = False,
) -> dict:
    """O campo `catalogo` de uma célula de conta de catálogo no /grid.

    `bloqueio`: None | sem_tipo | sem_anuncio | mesmo_anuncio | sincronizado |
    pausado | em_revisao | encerrado | sem_preco_catalogo; `texto` é a frase
    do tooltip.
    Com parte dos anúncios livres a célula envia (só para os livres) e o texto
    diz quais ficam de fora."""
    anuncios = [
        {
            "external_id": lk.external_id,
            "status": lk.anuncio_status,
            "listing_type": lk.listing_type,
            "sincronizado_com": relacionados_do_link(lk),
            "bloqueio": None,
        }
        for lk in resolucao.links
    ] + [
        {
            "external_id": b.link.external_id,
            "status": b.link.anuncio_status,
            "listing_type": b.link.listing_type,
            "sincronizado_com": relacionados_do_link(b.link),
            "bloqueio": b.motivo,
        }
        for b in resolucao.bloqueados
    ]

    if resolucao.sem_tipo:
        return {"anuncios": [], "bloqueio": "sem_tipo", "texto": TEXTO_BLOQUEIO["sem_tipo"]}
    if not anuncios:
        texto = TEXTO_BLOQUEIO["sem_anuncio"]
        if sem_integracao:
            texto = "Conta de kit sem integração do Mercado Livre — sem anúncio de catálogo"
        return {"anuncios": [], "bloqueio": "sem_anuncio", "texto": texto}
    if not resolucao.links:
        presentes = {b.motivo for b in resolucao.bloqueados}
        motivo = next(m for m in _ORDEM_BLOQUEIO if m in presentes)
        texto = "; ".join(
            "Anúncio de catálogo " + _texto_anuncio(b) for b in resolucao.bloqueados
        )
        return {"anuncios": anuncios, "bloqueio": motivo, "texto": texto}
    if sem_preco:
        return {
            "anuncios": anuncios,
            "bloqueio": "sem_preco_catalogo",
            "texto": TEXTO_BLOQUEIO["sem_preco_catalogo"],
        }
    texto = "Envia para " + ", ".join(lk.external_id for lk in resolucao.links)
    if resolucao.bloqueados:
        texto += "; pula " + "; ".join(_texto_anuncio(b) for b in resolucao.bloqueados)
    return {"anuncios": anuncios, "bloqueio": None, "texto": texto}


def texto_bloqueio_envio(resolucao: Resolucao) -> tuple[str, str] | None:
    """(bloqueio, texto) quando a célula de catálogo não tem para onde enviar."""
    info = info_celula_catalogo(resolucao, sem_preco=False)
    if info["bloqueio"] is None:
        return None
    return info["bloqueio"], info["texto"]
