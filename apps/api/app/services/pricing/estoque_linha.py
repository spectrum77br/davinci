"""Estoque de uma linha da Tabela de Preços (coluna Estoque, `/stock-map`) e
da Importação (GET /api/importacao/products) — no celular, a soma de todos os
lotes do produto.

Eduardo (02/11/2026): "a coluna Bling deve mostrar a soma do estoque de todos
os depósitos do produto". No celular cada lote é um SKU no Bling (a019.ci,
a019.sp, a019.cd...). Antes a tela mostrava UM lote só — o SKU mais curto e
depois o alfabético, então com .cd aparecia só o .cd: o airtag a019 mostrava
2598 em vez de 2994.

Regras:
  * Índice: só produto ativo (`products.situacao` 'A' ou NULL). SKU repetido
    (outro dono) fica com o maior saldo.
  * Celular, por peça da linha (vírgula = cores): soma o saldo como está
    gravado, negativo também, de todo SKU SIMPLES (sem '+', formato != 'E',
    que é composição/kit no Bling) "peça.lote" — um ponto só depois da peça —
    cujo lote, pela tag de `sku_tags.classify_sku_tag`, esteja em
    `LOTES_SOMADOS`. Ficam de fora o .us (usado; os dg001.us que o override
    manda para 'sa' entram), .kitN, .pp, .lj e afins. Sem lote nenhum: o SKU
    simples exato da peça. Peça repetida na linha conta uma vez.
  * Mala, eletro e catálogo: o SKU exato, como sempre foi (só ativos). Aqui
    entra também a composição do Bling: os conjuntos de mala (b005.12.18) são
    formato 'E' e o saldo deles é o que o Bling calcula pelas peças — tirar
    zeraria essas linhas; os kits com acessório (com '+') vêm 'E' ou 'S'.
  * Cada parte leva a peça da LINHA que a trouxe (`peca`, a mesma string de
    `pecas_da_linha`: no celular a base, dg002; na mala/eletro o SKU,
    b005.20) — a Reposição da Tabela é feita peça a peça.
  * Linha de conjunto (`linha_de_conjunto`): tem partes e TODAS são
    composição — formato 'E' ou kit com '+' (conjunto de mala b005.12.18, kit
    com acessório, base pura de kit completo b005). A compra é pelos tamanhos
    avulsos: a Reposição ali contaria a demanda duas vezes e a Tabela mostra
    "—". No celular nunca acontece: só entra SKU simples.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product
from app.services.sku_tags import classify_sku_tag

# Lotes que somam no estoque da linha de celular (tags de sku_tags).
LOTES_SOMADOS = frozenset({"ci", "pi", "ra", "sa", "sp", "cd"})
DEPARTAMENTO_CELULAR = "celular"
# products.situacao: 'A' = ativo; NULL (desconhecido) também conta.
_SITUACAO_ATIVA = "A"
# products.formato: 'E' = composição/kit no Bling (saldo calculado pelas peças).
_FORMATO_COMPOSICAO = "E"
_SEPARADOR_PECAS = ","
_SEPARADOR_KIT = "+"
_SEPARADOR_LOTE = "."


@dataclass(frozen=True)
class ParteEstoque:
    """Um SKU do Bling que entra na soma, com o saldo dele.

    `composicao` formato 'E' ou kit com '+': o saldo sai das peças;
    `peca`       a peça da linha que trouxe o SKU — `partes_da_linha` preenche
                 (no índice fica vazia)."""

    sku: str
    estoque: int
    composicao: bool = False
    peca: str = ""

    def como_dict(self) -> dict[str, str | int]:
        return {"sku": self.sku, "estoque": self.estoque, "peca": self.peca}


@dataclass
class IndiceEstoque:
    """Saldos por SKU em minúsculas.

    `exato`   todo produto ativo (composição do Bling inclusive);
    `simples` só os simples (sem '+', formato != 'E');
    `lotes`   peça → lotes somáveis dela (dos simples), em ordem de SKU.
    """

    exato: dict[str, ParteEstoque] = field(default_factory=dict)
    simples: dict[str, ParteEstoque] = field(default_factory=dict)
    lotes: dict[str, list[ParteEstoque]] = field(default_factory=dict)


def produto_ativo(situacao: str | None) -> bool:
    return (situacao or "").strip().upper() in ("", _SITUACAO_ATIVA)


def produto_simples(sku: str, formato: str | None) -> bool:
    return (
        _SEPARADOR_KIT not in sku
        and (formato or "").strip().upper() != _FORMATO_COMPOSICAO
    )


def peca_do_lote(sku: str | None) -> str | None:
    """A peça de um SKU de lote somável (`a019.cd` → `a019`), ou None.

    None para kit (`+`), mais de um ponto depois da peça (`z0207.mala.us`) e
    lote fora de `LOTES_SOMADOS` (`a019.us`, `a019.kit2`, `b005.20`)."""
    chave = (sku or "").strip().lower()
    peca, ponto, lote = chave.partition(_SEPARADOR_LOTE)
    if not ponto or not peca or not lote:
        return None
    if _SEPARADOR_LOTE in lote or _SEPARADOR_KIT in chave:
        return None
    if classify_sku_tag(chave) not in LOTES_SOMADOS:
        return None
    return peca


def _guardar_maior(destino: dict[str, ParteEstoque], chave: str, parte: ParteEstoque) -> None:
    atual = destino.get(chave)
    if atual is None or parte.estoque > atual.estoque:
        destino[chave] = parte


def montar_indice(
    produtos: Iterable[tuple[str | None, int | None, str | None, str | None]],
) -> IndiceEstoque:
    """Índice a partir de (sku, estoque, situacao, formato) dos produtos."""
    indice = IndiceEstoque()
    for sku, estoque, situacao, formato in produtos:
        nome = (sku or "").strip()
        chave = nome.lower()
        if not chave or not produto_ativo(situacao):
            continue
        parte = ParteEstoque(
            sku=nome,
            estoque=int(estoque or 0),
            composicao=not produto_simples(chave, formato),
        )
        _guardar_maior(indice.exato, chave, parte)
        if not parte.composicao:
            _guardar_maior(indice.simples, chave, parte)
    for chave, parte in sorted(indice.simples.items()):
        peca = peca_do_lote(chave)
        if peca is not None:
            indice.lotes.setdefault(peca, []).append(parte)
    return indice


async def carregar_indice(
    session: AsyncSession, *filtros: ColumnElement[bool]
) -> IndiceEstoque:
    """Lê os produtos (com os filtros do chamador) e monta o índice."""
    produtos = (
        await session.execute(
            select(Product.sku, Product.stock, Product.situacao, Product.formato).where(
                *filtros
            )
        )
    ).all()
    return montar_indice(produtos)


def pecas_da_linha(sku_linha: str | None) -> list[str]:
    """Peças da linha (vírgula = cores), minúsculas, sem repetir, na ordem."""
    pecas = (p.strip().lower() for p in (sku_linha or "").split(_SEPARADOR_PECAS))
    return list(dict.fromkeys(p for p in pecas if p))


def _partes_da_peca(indice: IndiceEstoque, peca: str, departamento: str) -> list[ParteEstoque]:
    if departamento == DEPARTAMENTO_CELULAR:
        lotes = indice.lotes.get(peca)
        if lotes:
            return lotes
        exata = indice.simples.get(peca)
    else:
        exata = indice.exato.get(peca)
    return [exata] if exata is not None else []


def partes_da_linha(
    indice: IndiceEstoque, sku_linha: str | None, departamento: str
) -> list[ParteEstoque]:
    """Os SKUs que somam o estoque da linha, cada um com a peça da linha que
    o trouxe; vazio = nenhum casou.

    Cada SKU entra uma vez, mesmo que duas peças caiam nele (fica na 1ª)."""
    partes: dict[str, ParteEstoque] = {}
    for peca in pecas_da_linha(sku_linha):
        for parte in _partes_da_peca(indice, peca, departamento):
            chave = parte.sku.lower()
            if chave not in partes:
                partes[chave] = replace(parte, peca=peca)
    return list(partes.values())


def total_das_partes(partes: Iterable[ParteEstoque]) -> int:
    return sum(parte.estoque for parte in partes)


def linha_de_conjunto(partes: Iterable[ParteEstoque]) -> bool:
    """True quando a linha tem partes e todas são composição (conjunto ou
    kit): a compra é pelos tamanhos avulsos, a Reposição fica "—"."""
    composicoes = [parte.composicao for parte in partes]
    return bool(composicoes) and all(composicoes)
