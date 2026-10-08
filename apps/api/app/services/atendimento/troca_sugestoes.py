"""Sugestões de TROCA DE PRODUTO no pedido em "Aguardando Cancelamento" (item 4, fase 4b).

05/10/2026.

QUANDO: o motivo do 83955 é falta de estoque (`ag_cancelamento.classificar`
→ `pode_sugerir_troca`, com os SKUs em falta que AINDA estão no pedido) e a
chave `atendimento_troca_sugestoes_ativa` está ligada. O painel do pedido e
a lista Ag. cancelamento mostram até 3 produtos parecidos com estoque e o
texto da oferta para copiar. SÓ LEITURA: nenhuma chamada ao Bling e nada
gravado (o botão Trocar, com a conferência ao vivo, é a 4c).

A REGRA (a do Eduardo; calibrada com as 16 trocas feitas à mão de 02/09 a
02/10/2026 — `tests/test_atendimento_troca_sugestoes.py`):
  nível 0  o MESMO produto em outro lote de venda (ci/pi/ra/sa/sp; nunca
           `.us` nem `.cd`) — o kit troca de lote inteiro. Só quando o robô
           de lote já faria o mesmo: a soma por família com o
           redirecionamento ligado para a linha, ou a prioridade de lote da
           Tabela de Preços apontando para esse lote (crítica M1). Senão o
           mesmo produto vem no nível 1 (`mesmo_produto`), com aceite;
  nível 1  o MESMO MODELO em outra cor, no mesmo lote: celular/Apple pela
           linha da Tabela de Preços + a especificação (RAM.ROM do nome;
           sem linha, o nome sem a cor); mala pelo código M/P/ME do nome +
           o tamanho (`_mesmo_tamanho`); eletro pelo `u<tipo><nº>` + a
           voltagem;
  nível 2  OUTRO MODELO com a MESMA especificação, no mesmo lote: mala da
           mesma linha da Tabela de Preços E do mesmo tamanho (crítica M9);
           celular `dg` com a mesma especificação e a mesma marca 5G, de
           outra linha. Apple e eletro não têm nível 2.
  Em todos: ativo (`situacao` 'A'; NULL é desconhecido e fica de fora, como
  no robô de lote), o mesmo `formato` (kit × simples), os mesmos acessórios
  e o mesmo AVULSO. Ficam de fora o salvado (`z*`, "salvad"/"avariad" no
  nome), o `fake.` e o kit com lotes misturados.

O TAMANHO da mala é o número do SKU. Dois nomes valem o mesmo tamanho só
com a prova da Tabela de Preços (`_tamanhos_iguais`): os dois SKUs na MESMA
linha, e a linha juntando os dois tamanhos (o `.8` e o `.10` na "ABS 8"; o
kit de 6 do b012, com o `.8`, custa o mesmo que o do b027, com o `.10`).
SKU com os dois tamanhos (o kit `b045.12.14.16`, ou o `b045.14` e o
`b045.16`) prova que são tamanhos diferentes.

O CUSTO (níveis 1 e 2; o mesmo produto não tem trava de custo):
  o do kit é a SOMA dos componentes (`bling_kit_components` ×
  `bling_cost_price`), só com TODOS os componentes ativos e de custo > 0
  (crítica M10: salvar a estrutura do kit no Bling zera o custo dos
  componentes); o do simples é o `bling_cost_price` dele. O candidato pode
  custar até `teto` % a mais pela soma E pelo custo do próprio SKU (o que a
  Margem carimba: o kit com óculos `a020` custa ~R$ 360 acima da soma); no
  nível 2, não menos que `piso` % (mais barato é rebaixar o produto). Custo
  zero ou desconhecido fica de fora — no original, sem nível 1 nem 2.

A ORDEM: com estoque ≥ a quantidade primeiro; depois o nível (no mesmo
nível, o mesmo produto antes); o que não encarece antes; o maior estoque.
Vão as 3 elegíveis e até 3 de fora (`motivo_fora`: sem_estoque,
custo_acima, custo_abaixo_piso), que a tela mostra esmaecidas.

O ESTOQUE é o do DaVinci (`products.stock`, com a hora da linha): diverge do
saldo ao vivo do Bling (ex.: 114 × 345) — serve para ordenar; quem troca
(4c) confere ao vivo.

O CATÁLOGO (`catalogo`): 3 consultas (produtos, composição dos kits,
Tabela de Preços) e a prioridade de lote (`prioridade_estoque.
_mapa_prioridades`), montados em Python (`montar_catalogo`) e guardados 10
min na memória do processo — a consulta SQL do catálogo inteiro é pesada.

Funções PURAS (sem banco): `chaves`, `montar_catalogo`, `candidatos`,
`sugerir`, `texto_oferta`.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BlingKitComponent, PricingProduct, Product
from app.services.atendimento.constantes import TEXTO_OFERTA_MESMO_PRODUTO, TEXTO_OFERTA_TROCA
from app.services.estoque_familia import LOTES_DE_VENDA, chave_familia, lote_de

logger = structlog.get_logger()

# ── Famílias (o começo do SKU principal) ──────────────────────────────────
CELULAR = "cel"  # dgNNN = modelo + cor
APPLE = "apple"  # iNNN
MALA = "mala"  # bNNN[.tamanhos] (sem tamanho = o kit de 6)
ELETRO = "eletro"  # u<tipo><nº>m<cor>[.<voltagem>]

# ── Níveis da sugestão ────────────────────────────────────────────────────
NIVEL_LOTE = 0  # o mesmo produto em outro lote de venda (sem aceite)
NIVEL_MODELO = 1  # o mesmo modelo em outra cor (ou o mesmo produto, com aceite)
NIVEL_ESPECIFICACAO = 2  # outro modelo com a mesma especificação

# ── Por que a sugestão ficou de fora (`Sugestao.motivo_fora`) ─────────────
FORA_SEM_ESTOQUE = "sem_estoque"
FORA_CUSTO_ACIMA = "custo_acima"
FORA_CUSTO_ABAIXO_PISO = "custo_abaixo_piso"
MOTIVOS_FORA = (FORA_SEM_ESTOQUE, FORA_CUSTO_ACIMA, FORA_CUSTO_ABAIXO_PISO)
# Para quem NÃO vê a Margem (decisão (g): custo é só da Margem), os dois
# motivos do CUSTO saem com este, sem dizer se encarece ou barateia
# (`_sugestao_out`).
FORA_DA_REGRA = "fora_da_regra"
_FORA_PELO_CUSTO = frozenset({FORA_CUSTO_ACIMA, FORA_CUSTO_ABAIXO_PISO})
# Na lista de fora, o que só falta estoque vem antes do que o custo barra.
_ORDEM_FORA = {None: 0, FORA_SEM_ESTOQUE: 1, FORA_CUSTO_ACIMA: 2, FORA_CUSTO_ABAIXO_PISO: 3}
# Sem custo conhecido do candidato: nem aparece (não dá para conferir a regra).
_SEM_CUSTO = "sem_custo"

TETO_CUSTO_PCT_PADRAO = 5.0
PISO_NIVEL2_PCT_PADRAO = -10.0
LIMITE_PADRAO = 3
# A memória do catálogo no processo (s).
CATALOGO_TTL_S = 600
# Folga das comparações de custo (o custo vem em NUMERIC(14,4)).
_FOLGA = 1e-9

# Mala de UM tamanho (sem lote): "b026.10" → "10".
_RE_MALA_UM_TAMANHO = re.compile(r"^b\d+\.(\d+)$")

_RE_DESCARTE = re.compile(r"salvad|avariad", re.I)
_RE_AVULSO = re.compile(r"avulso", re.I)
_RE_5G = re.compile(r"5g", re.I)
# RAM.ROM do nome do aparelho: "12.64", "4+64", "12.64+64" (≠ "12.64").
_RE_SPEC = re.compile(r"\d+\s*[.+]\s*\d+(?:\s*\+\s*\d+)?")
# O código do modelo da mala no nome: M1–M6, P1–P8, ME1–ME2 (um estilo cada).
_RE_CODIGO_MALA = re.compile(r"\b((?:ME|M|P)\d+)\b")
_RE_ELETRO = re.compile(r"^(u[a-z]+\d+)(m\d+)?(?:\.(\d+))?$")
# "Uranyx A17 Pro Max 12.64 - Branco + Fone" → o nome do aparelho, sem o acessório.
_RE_ACESSORIO_NO_NOME = re.compile(r"\s+\+\s.*$")
# "… 12.64 - Branco" → sem a cor (o último " - …").
_RE_COR_NO_NOME = re.compile(r"\s+-\s+[^-]*$")
# "(DT - DTLG115 - DT14)" no fim do nome: código interno, não vai ao comprador.
_RE_CODIGO_NO_FIM = re.compile(r"\s*\([^()]*\)\s*$")


@dataclass(frozen=True)
class ProdutoTroca:
    """Um produto do catálogo com as chaves da regra (`chaves`)."""

    sku: str
    low: str
    nome: str
    formato: str | None
    ativo: bool
    estoque: int
    estoque_em: datetime | None
    bling_product_id: int | None
    # `bling_cost_price` do próprio SKU (o que a Margem carimba no pedido).
    custo_proprio: float | None
    # A soma dos componentes no kit completo; o próprio no simples; None = desconhecido.
    custo: float | None
    semlote: str
    lote: str | None
    main: str
    acess: tuple[str, ...]
    fam: str | None
    linha_id: str | None
    linha_nome: str | None
    modelo: str | None
    spec: str | None
    nome_modelo: str | None
    g5: bool
    avulso: bool
    descartado: bool


@dataclass(frozen=True)
class Catalogo:
    """O catálogo da troca, montado por `montar_catalogo` (ou lido do banco por `catalogo`)."""

    # {sku em minúsculas: produto}
    produtos: Mapping[str, ProdutoTroca]
    # {família: produtos ATIVOS, não descartados}
    por_familia: Mapping[str, tuple[ProdutoTroca, ...]]
    # {base do SKU (sem lote): (id da linha, nome)} — as linhas ATIVAS da Tabela de Preços.
    linhas: Mapping[str, tuple[str, str]]
    # {base do principal: lote prioritário} (`prioridade_estoque._mapa_prioridades`).
    prioridades: Mapping[str, str]
    # A soma por família e o redirecionamento do robô de lote (`estoque_familia`).
    familia_ativa: bool = False
    familia_redireciona: bool = False
    familia_prefixos: tuple[str, ...] = ()
    lido_em: datetime | None = None
    # {par de nomes do mesmo tamanho de mala} (`_tamanhos_iguais`): ex. {"8", "10"}.
    tamanhos_iguais: frozenset[frozenset[str]] = frozenset()


@dataclass(frozen=True)
class Sugestao:
    """Um produto para trocar o item em falta (ou um parecido que ficou de fora)."""

    sku: str
    nome: str
    nivel: int
    estoque: int
    estoque_em: datetime | None
    produto_id: int | None
    # Quanto o NOSSO custo muda (%), pela soma; None sem custo dos dois lados.
    dif_custo_pct: float | None
    # None = elegível; senão sem_estoque, custo_acima ou custo_abaixo_piso.
    motivo_fora: str | None
    # O mesmo produto, de outro lote de venda.
    mesmo_produto: bool = False


# ── Chaves (PURAS) ────────────────────────────────────────────────────────


def _sem_lote(pedaco: str) -> str:
    lote = lote_de(pedaco)
    return pedaco[: -(len(lote) + 1)] if lote else pedaco


def _pedacos(sku: str | None) -> list[str]:
    return [p.strip() for p in (sku or "").strip().lower().split("+") if p.strip()]


def _familia(main: str) -> str | None:
    if re.match(r"^dg\d", main):
        return CELULAR
    if re.match(r"^i\d", main):
        return APPLE
    if re.match(r"^b\d", main):
        return MALA
    if main.startswith("u") and _RE_ELETRO.match(main):
        return ELETRO
    return None


def _tamanhos(main: str) -> list[str]:
    """Os tamanhos do SKU da mala, sem lote ("b036.12.18" → ["12", "18"]; o kit de 6 → [])."""
    return [t for t in main.split(".")[1:] if t]


def _main_e_lote(low: str) -> tuple[str, str | None]:
    pedacos = _pedacos(low)
    lotes = {lote_de(p) for p in pedacos} - {None}
    lote = next(iter(lotes)) if len(lotes) == 1 else None
    return (_sem_lote(pedacos[0]) if pedacos else ""), lote


def chaves(
    sku: str | None,
    nome: str | None,
    formato: str | None = None,
    *,
    linha: tuple[str, str] | None = None,
    nome_aparelho: str | None = None,
) -> dict[str, Any]:
    """As chaves da regra de "parecido" para UM produto. PURA.

    `linha` = (id, nome) da linha da Tabela de Preços do principal;
    `nome_aparelho` = o nome do aparelho (o simples do principal no mesmo
    lote) — sem ele, o nome sem o " + acessório". Devolve: sku (minúsculas),
    lote, semlote, main, acess, fam, linha_id, linha_nome, modelo, spec,
    nome_modelo, g5, avulso, formato e `descartado` (salvado, avariado,
    `z*`, `fake.` ou kit com lotes misturados).
    """
    low = (sku or "").strip().lower()
    pedacos = _pedacos(low)
    lotes = {lote_de(p) for p in pedacos} - {None}
    bases = [_sem_lote(p) for p in pedacos]
    main = bases[0] if bases else ""
    nome = " ".join((nome or "").split())
    linha_id, linha_nome = linha if linha else (None, None)
    fam = _familia(main)
    k: dict[str, Any] = {
        "sku": low,
        "lote": next(iter(lotes)) if len(lotes) == 1 else None,
        "semlote": "+".join(bases),
        "main": main,
        "acess": tuple(sorted(bases[1:])),
        "fam": fam,
        "linha_id": linha_id,
        "linha_nome": linha_nome,
        "modelo": None,
        "spec": None,
        "nome_modelo": None,
        "g5": False,
        "avulso": bool(_RE_AVULSO.search(nome)),
        "formato": (formato or "").strip().upper() or None,
        "descartado": (
            not low
            or low.startswith(("z", "fake."))
            or len(lotes) > 1
            or bool(_RE_DESCARTE.search(nome))
        ),
    }
    if fam in (CELULAR, APPLE):
        aparelho = " ".join((nome_aparelho or "").split()) or _RE_ACESSORIO_NO_NOME.sub("", nome)
        m = _RE_SPEC.search(aparelho)
        k["spec"] = re.sub(r"\s", "", m.group(0)).replace("+", ".") if m else None
        # O WP60 só é 5G no nome da LINHA: a marca olha os dois.
        k["g5"] = bool(_RE_5G.search(aparelho) or _RE_5G.search(linha_nome or ""))
        k["modelo"] = linha_id
        if " - " in aparelho:
            k["nome_modelo"] = _RE_COR_NO_NOME.sub("", aparelho).strip().lower() or None
    elif fam == MALA:
        m = _RE_CODIGO_MALA.search(nome)
        k["modelo"] = m.group(1) if m else None
        # O tamanho como está no SKU: os nomes iguais saem da Tabela de Preços (`_mesmo_tamanho`).
        k["spec"] = ".".join(_tamanhos(main))
    elif fam == ELETRO:
        m = _RE_ELETRO.match(main)
        if m is not None:
            k["modelo"], k["spec"] = m.group(1), m.group(3) or ""
    return k


# ── O catálogo (PURO a partir das linhas) ────────────────────────────────


def _num(valor: Any) -> float | None:
    if valor is None:
        return None
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _quando(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor if valor.tzinfo is not None else valor.replace(tzinfo=UTC)
    if isinstance(valor, str) and valor.strip():
        try:
            return _quando(datetime.fromisoformat(valor.strip()))
        except ValueError:
            return None
    return None


def _ativo(p: Mapping[str, Any]) -> bool:
    """Só a situação 'A': NULL é desconhecida (`Product.situacao`); o robô de lote a ignora."""
    return str(p.get("situacao") or "").strip().upper() == "A"


def _preferencia(p: Mapping[str, Any]) -> tuple[bool, float]:
    """Entre produtos com o mesmo SKU (donos diferentes): o ativo, o mais recente."""
    quando = _quando(p.get("upd"))
    return _ativo(p), quando.timestamp() if quando is not None else float("-inf")


def _custo_do_kit(
    p: Mapping[str, Any],
    componentes: Mapping[int, list[tuple[int, float]]],
    por_bid: Mapping[int, Mapping[str, Any]],
) -> tuple[float | None, float | None]:
    """(custo da regra, custo do próprio SKU): a soma no kit completo; senão o próprio.

    Kit com algum componente ausente, inativo ou de custo zero/vazio = custo
    DESCONHECIDO (None): a soma sairia menor que o real e passaria no teto.
    """
    proprio = _num(p.get("custo"))
    bid = p.get("bid")
    if (p.get("formato") or "").strip().upper() == "E" and bid is not None:
        lista = componentes.get(int(bid))
        if lista:
            soma = 0.0
            for cid, quantidade in lista:
                c = por_bid.get(cid)
                custo = _num(c.get("custo")) if c is not None else None
                if custo is None or custo <= 0:
                    return None, proprio
                soma += quantidade * custo
            return soma, proprio
    return proprio, proprio


def _pares(tamanhos: Iterable[str]) -> set[frozenset[str]]:
    lista = sorted(set(tamanhos))
    return {frozenset((a, b)) for i, a in enumerate(lista) for b in lista[i + 1 :]}


def _tamanhos_iguais(
    entradas_por_linha: Iterable[Sequence[str]], skus: Iterable[str]
) -> frozenset[frozenset[str]]:
    """Os pares de nomes do MESMO tamanho de mala, com a prova da Tabela de Preços. PURA.

    `entradas_por_linha` = as bases (sem lote) de cada linha ATIVA; `skus` =
    os do catálogo. Iguais: uma linha junta malas de UM tamanho com os dois
    (a "ABS 8" tem o `.8` e o `.10`). Diferentes (e isso vence): um SKU com
    os dois tamanhos (o kit `b045.12.14.16`) ou o mesmo estilo com os dois
    (`b045.14` e `b045.16`).
    """
    juntos: set[frozenset[str]] = set()
    diferentes: set[frozenset[str]] = set()
    por_estilo: dict[str, set[str]] = {}

    def _olhar(main: str, um_tamanho: set[str] | None) -> None:
        if _familia(main) != MALA:
            return
        tamanhos = _tamanhos(main)
        if len(tamanhos) > 1:
            diferentes.update(_pares(tamanhos))
        elif tamanhos:
            por_estilo.setdefault(main.split(".", 1)[0], set()).add(tamanhos[0])
            if um_tamanho is not None:
                um_tamanho.add(tamanhos[0])

    for entradas in entradas_por_linha:
        da_linha: set[str] = set()
        for base in entradas:
            _olhar(base.split("+", 1)[0], da_linha)
        juntos.update(_pares(da_linha))
    for low in skus:
        _olhar(_main_e_lote(low)[0], None)
    for tamanhos in por_estilo.values():
        diferentes.update(_pares(tamanhos))
    return frozenset(juntos - diferentes)


def montar_catalogo(
    produtos: Iterable[Mapping[str, Any]],
    kits: Iterable[Mapping[str, Any]],
    linhas: Iterable[Mapping[str, Any]],
    *,
    prioridades: Mapping[str, str] | None = None,
    familia_ativa: bool = False,
    familia_redireciona: bool = False,
    familia_prefixos: Sequence[str] = (),
    lido_em: datetime | None = None,
) -> Catalogo:
    """O catálogo da troca a partir das linhas cruas. PURA.

    `produtos`: sku, name, formato, situacao, stock, custo
    (`bling_cost_price`), bid (`bling_product_id`), upd (`updated_at`).
    `kits`: kit, comp, q (`bling_kit_components`). `linhas`: id, sku (lista
    com vírgulas), name, ativo (`pricing_products`). Um produto por SKU (o
    ativo, o mais recente); a linha de uma base é a de menor id.
    """
    brutos: dict[str, Mapping[str, Any]] = {}
    for p in produtos:
        low = str(p.get("sku") or "").strip().lower()
        if not low:
            continue
        atual = brutos.get(low)
        if atual is None or _preferencia(p) > _preferencia(atual):
            brutos[low] = p
    por_bid = {int(p["bid"]): p for p in brutos.values() if p.get("bid") is not None and _ativo(p)}
    componentes: dict[int, list[tuple[int, float]]] = {}
    for k in kits:
        if k.get("kit") is None or k.get("comp") is None:
            continue
        componentes.setdefault(int(k["kit"]), []).append((int(k["comp"]), _num(k.get("q")) or 1.0))
    linha_de: dict[str, tuple[str, str]] = {}
    entradas_por_linha: list[list[str]] = []
    for pp in sorted((x for x in linhas if x.get("ativo")), key=lambda x: str(x.get("id"))):
        entradas: list[str] = []
        for entrada in str(pp.get("sku") or "").split(","):
            base = "+".join(_sem_lote(x) for x in _pedacos(entrada))
            if base:
                entradas.append(base)
                linha_de.setdefault(base, (str(pp.get("id")), str(pp.get("name") or "")))
        entradas_por_linha.append(entradas)

    saida: dict[str, ProdutoTroca] = {}
    for low, p in brutos.items():
        main, lote = _main_e_lote(low)
        aparelho = brutos.get(f"{main}.{lote}" if lote else main)
        nome = " ".join(str(p.get("name") or "").split())
        k = chaves(
            low,
            nome,
            p.get("formato"),
            linha=linha_de.get(main),
            nome_aparelho=(aparelho.get("name") if aparelho is not None else None),
        )
        custo, proprio = _custo_do_kit(p, componentes, por_bid)
        bid = p.get("bid")
        saida[low] = ProdutoTroca(
            sku=str(p.get("sku") or "").strip(),
            low=low,
            nome=nome,
            formato=k["formato"],
            ativo=_ativo(p),
            estoque=int(_num(p.get("stock")) or 0),
            estoque_em=_quando(p.get("upd")),
            bling_product_id=int(bid) if bid is not None else None,
            custo_proprio=proprio,
            custo=custo,
            semlote=k["semlote"],
            lote=k["lote"],
            main=k["main"],
            acess=k["acess"],
            fam=k["fam"],
            linha_id=k["linha_id"],
            linha_nome=k["linha_nome"],
            modelo=k["modelo"],
            spec=k["spec"],
            nome_modelo=k["nome_modelo"],
            g5=k["g5"],
            avulso=k["avulso"],
            descartado=k["descartado"],
        )
    por_familia: dict[str, list[ProdutoTroca]] = {}
    for prod in saida.values():
        if prod.fam is not None and prod.ativo and not prod.descartado:
            por_familia.setdefault(prod.fam, []).append(prod)
    return Catalogo(
        produtos=saida,
        por_familia={f: tuple(v) for f, v in por_familia.items()},
        linhas=linha_de,
        prioridades=dict(prioridades or {}),
        familia_ativa=familia_ativa,
        familia_redireciona=familia_redireciona,
        familia_prefixos=tuple(familia_prefixos),
        lido_em=lido_em,
        tamanhos_iguais=_tamanhos_iguais(entradas_por_linha, brutos.keys()),
    )


# ── A regra (PURA) ────────────────────────────────────────────────────────


def _original(cat: Catalogo, sku: str | None) -> ProdutoTroca | None:
    """O produto do item em falta; fora do catálogo, só as chaves do SKU (sem nome nem custo)."""
    low = (sku or "").strip().lower()
    if not low:
        return None
    if low in cat.produtos:
        return cat.produtos[low]
    main, _ = _main_e_lote(low)
    k = chaves(low, "", None, linha=cat.linhas.get(main))
    return ProdutoTroca(
        sku=(sku or "").strip(),
        low=low,
        nome="",
        formato=None,
        ativo=False,
        estoque=0,
        estoque_em=None,
        bling_product_id=None,
        custo_proprio=None,
        custo=None,
        semlote=k["semlote"],
        lote=k["lote"],
        main=k["main"],
        acess=k["acess"],
        fam=k["fam"],
        linha_id=k["linha_id"],
        linha_nome=k["linha_nome"],
        modelo=k["modelo"],
        spec=k["spec"],
        nome_modelo=k["nome_modelo"],
        g5=k["g5"],
        avulso=k["avulso"],
        descartado=k["descartado"],
    )


def _serve(o: ProdutoTroca, c: ProdutoTroca) -> bool:
    """O comum a todos os níveis: ativo, a mesma família, formato, acessórios e AVULSO."""
    return (
        c.ativo
        and not c.descartado
        and c.low != o.low
        and c.fam == o.fam
        and c.formato == o.formato
        and c.acess == o.acess
        and c.avulso == o.avulso
    )


def _sku_no_lote(low: str, de: str, para: str) -> str:
    """O kit inteiro no outro lote, como `prioridade_estoque.sku_alvo`.

    dg057.ci+a001.ci → dg057.sp+a001.sp (o pedaço sem lote fica como está).
    """
    return "+".join(p[: -len(de)] + para if lote_de(p) == de else p for p in _pedacos(low))


def _lote_livre(cat: Catalogo, o: ProdutoTroca, lote: str) -> bool:
    """O robô de lote já mandaria o item para este lote (crítica M1)?

    A prioridade de lote da Tabela de Preços aponta para ele, ou a soma por
    família está ligada COM o redirecionamento para a linha
    (`prioridade_estoque._redireciona` = `estoque_familia_redireciona` e
    `estoque_familia.familia_ligada`).
    """
    if cat.prioridades.get(o.main) == lote:
        return True
    if not (cat.familia_ativa and cat.familia_redireciona):
        return False
    base = chave_familia(o.low)
    if base is None:
        return False
    return not cat.familia_prefixos or base.split("+")[0].startswith(cat.familia_prefixos)


def _mesmo_tamanho(cat: Catalogo, o: ProdutoTroca, c: ProdutoTroca) -> bool:
    """A mala do mesmo tamanho: o mesmo número, ou os dois na MESMA linha da Tabela de Preços
    com os nomes juntados por ela (`Catalogo.tamanhos_iguais`: o `.10` e o `.8` na "ABS 8").
    """
    if o.spec == c.spec:
        return True
    if o.linha_id is None or c.linha_id != o.linha_id:
        return False
    a, b = (o.spec or "").split("."), (c.spec or "").split(".")
    return len(a) == len(b) and all(
        x == y or frozenset((x, y)) in cat.tamanhos_iguais for x, y in zip(a, b, strict=True)
    )


def _nivel(cat: Catalogo, o: ProdutoTroca, c: ProdutoTroca) -> int | None:
    """O nível de um candidato de OUTRO principal no mesmo lote; None = não é parecido."""
    if o.fam in (CELULAR, APPLE):
        if o.linha_id is not None:
            if c.linha_id == o.linha_id and c.spec == o.spec:
                return NIVEL_MODELO
        elif o.nome_modelo and c.nome_modelo == o.nome_modelo:
            return NIVEL_MODELO
        if (
            o.fam == CELULAR
            and o.spec
            and c.spec == o.spec
            and c.g5 == o.g5
            and c.linha_id != o.linha_id
        ):
            return NIVEL_ESPECIFICACAO
        return None
    if o.fam == MALA:
        if not _mesmo_tamanho(cat, o, c):
            return None
        if o.modelo and c.modelo == o.modelo:
            return NIVEL_MODELO
        if o.linha_id is not None and c.linha_id == o.linha_id:
            return NIVEL_ESPECIFICACAO
        return None
    if o.fam == ELETRO and o.modelo and c.modelo == o.modelo and c.spec == o.spec:
        return NIVEL_MODELO
    return None


def _dif_pct(o: ProdutoTroca, c: ProdutoTroca) -> float | None:
    if o.custo is None or c.custo is None or o.custo <= 0 or c.custo <= 0:
        return None
    return round(100 * (c.custo / o.custo - 1), 1)


def _motivo_do_custo(
    o: ProdutoTroca, c: ProdutoTroca, nivel: int, teto_pct: float, piso_n2_pct: float
) -> str | None:
    """None = o custo serve; senão custo_acima, custo_abaixo_piso ou sem custo conhecido."""
    if o.custo is None or c.custo is None or o.custo <= 0 or c.custo <= 0:
        return _SEM_CUSTO
    dif = c.custo / o.custo - 1
    if dif > teto_pct / 100 + _FOLGA:
        return FORA_CUSTO_ACIMA
    if (
        c.custo_proprio
        and o.custo_proprio
        and c.custo_proprio > 0
        and o.custo_proprio > 0
        and c.custo_proprio / o.custo_proprio - 1 > teto_pct / 100 + _FOLGA
    ):
        return FORA_CUSTO_ACIMA
    if nivel == NIVEL_ESPECIFICACAO and dif < piso_n2_pct / 100 - _FOLGA:
        return FORA_CUSTO_ABAIXO_PISO
    return None


def _ordem(s: Sugestao) -> tuple:
    """Com estoque antes; o nível; o mesmo produto; o que não encarece; o maior estoque."""
    return (
        s.motivo_fora is not None,
        _ORDEM_FORA.get(s.motivo_fora, 9),
        s.nivel,
        not s.mesmo_produto,
        max(s.dif_custo_pct or 0.0, 0.0),
        -s.estoque,
        s.sku.lower(),
    )


def candidatos(
    cat: Catalogo,
    sku: str | None,
    qtd: int | float | None = 1,
    *,
    teto_pct: float = TETO_CUSTO_PCT_PADRAO,
    piso_n2_pct: float = PISO_NIVEL2_PCT_PADRAO,
) -> list[Sugestao]:
    """TODOS os parecidos do SKU em falta, elegíveis e de fora, na ordem. PURA."""
    o = _original(cat, sku)
    if o is None or o.descartado or o.fam is None:
        return []
    precisa = max(1, math.ceil(_num(qtd) or 1))

    def _sugestao(c: ProdutoTroca, nivel: int, motivo: str | None, mesmo: bool) -> Sugestao:
        if motivo is None and c.estoque < precisa:
            motivo = FORA_SEM_ESTOQUE
        return Sugestao(
            sku=c.sku,
            nome=c.nome,
            nivel=nivel,
            estoque=c.estoque,
            estoque_em=c.estoque_em,
            produto_id=c.bling_product_id,
            dif_custo_pct=_dif_pct(o, c),
            motivo_fora=motivo,
            mesmo_produto=mesmo,
        )

    saida: list[Sugestao] = []
    # O mesmo produto em outro lote de VENDA (o kit inteiro): sem trava de custo.
    if o.lote is not None:
        for lote in sorted(LOTES_DE_VENDA - {o.lote}):
            c = cat.produtos.get(_sku_no_lote(o.low, o.lote, lote))
            if c is None or not _serve(o, c):
                continue
            nivel = NIVEL_LOTE if _lote_livre(cat, o, lote) else NIVEL_MODELO
            saida.append(_sugestao(c, nivel, None, True))
    # Outro principal no mesmo lote — só com o custo do original conhecido.
    if o.custo is not None and o.custo > 0:
        for c in cat.por_familia.get(o.fam or "", ()):
            if c.main == o.main or c.lote != o.lote or not _serve(o, c):
                continue
            nivel = _nivel(cat, o, c)
            if nivel is None:
                continue
            motivo = _motivo_do_custo(o, c, nivel, teto_pct, piso_n2_pct)
            if motivo == _SEM_CUSTO:
                continue
            saida.append(_sugestao(c, nivel, motivo, False))
    saida.sort(key=_ordem)
    return saida


def sugerir(
    cat: Catalogo,
    sku: str | None,
    qtd: int | float | None = 1,
    *,
    teto_pct: float = TETO_CUSTO_PCT_PADRAO,
    piso_n2_pct: float = PISO_NIVEL2_PCT_PADRAO,
    limite: int | None = LIMITE_PADRAO,
) -> list[Sugestao]:
    """As `limite` elegíveis (na ordem) seguidas de até `limite` de fora. PURA.

    `limite=None` = todas. Elegível = `motivo_fora` None.
    """
    todas = candidatos(cat, sku, qtd, teto_pct=teto_pct, piso_n2_pct=piso_n2_pct)
    boas = [s for s in todas if s.motivo_fora is None]
    fora = [s for s in todas if s.motivo_fora is not None]
    if limite is not None:
        boas, fora = boas[:limite], fora[:limite]
    return boas + fora


def _nome_para_o_comprador(nome: str | None) -> str:
    texto = " ".join((nome or "").split())
    while True:
        limpo = _RE_CODIGO_NO_FIM.sub("", texto)
        if limpo == texto:
            return texto
        texto = limpo


def texto_oferta(
    nome_original: str | None, nome_novo: str | None, *, mesmo_produto: bool = False
) -> str:
    """O texto da oferta de troca ao comprador (`TEXTO_OFERTA_TROCA`). PURA.

    Sem número, sem prazo e sem margem: só os nomes (sem o código interno
    entre parênteses no fim), "pelo mesmo valor, sem custo a mais" e "se
    preferir, cancelamos". O mesmo produto de outro lote (ou o mesmo nome)
    tem o texto próprio.
    """
    original = _nome_para_o_comprador(nome_original) or "o item do pedido"
    novo = _nome_para_o_comprador(nome_novo)
    if mesmo_produto or (novo and novo.casefold() == original.casefold()):
        return TEXTO_OFERTA_MESMO_PRODUTO.format(original=original)
    return TEXTO_OFERTA_TROCA.format(original=original, novo=novo or "um produto equivalente")


def motivo_sem_sugestao(cat: Catalogo, sku: str | None) -> str:
    """Por que o item em falta não tem nenhum parecido (a tela mostra). PURA."""
    o = _original(cat, sku)
    if o is None or (sku or "").strip().lower() not in cat.produtos:
        return "o SKU não está no catálogo do DaVinci"
    if o.descartado:
        return "salvado, avariado, fake ou kit com lotes misturados: fica fora da troca"
    if o.fam is None:
        return "produto fora das famílias da troca (celular, Apple, mala, eletro)"
    if o.custo is None or o.custo <= 0:
        return (
            "sem o custo do produto (ou de um componente do kit): só o mesmo produto em outro lote"
        )
    return "nenhum produto parecido no catálogo"


# ── O catálogo do banco (memória de 10 min) ──────────────────────────────

_MEMORIA: dict[str, tuple[float, Catalogo]] = {}


def limpar_memoria() -> None:
    """Esquece o catálogo guardado (testes; a próxima leitura vai ao banco)."""
    _MEMORIA.clear()


async def _ler_catalogo(session: AsyncSession) -> Catalogo:
    """As 3 consultas do catálogo + a prioridade de lote, montadas em Python."""
    produtos = (
        await session.execute(
            select(
                Product.sku,
                Product.name,
                Product.formato,
                Product.situacao,
                Product.stock,
                Product.bling_cost_price,
                Product.bling_product_id,
                Product.updated_at,
            )
            .distinct(func.lower(Product.sku))
            .order_by(
                func.lower(Product.sku),
                # O ativo primeiro; a situação NULL (desconhecida) não conta como ativa.
                (Product.situacao == "A").desc().nulls_last(),
                Product.updated_at.desc().nulls_last(),
            )
        )
    ).all()
    kits = (
        await session.execute(
            select(
                BlingKitComponent.kit_bling_product_id,
                BlingKitComponent.component_bling_product_id,
                BlingKitComponent.quantidade,
            )
        )
    ).all()
    linhas = (
        await session.execute(
            select(PricingProduct.id, PricingProduct.sku, PricingProduct.name).where(
                PricingProduct.is_active.is_(True)
            )
        )
    ).all()
    # Import tardio: a prioridade de estoque puxa o cliente do Bling e a NF.
    from app.services.prioridade_estoque import _mapa_prioridades

    prioridades = await _mapa_prioridades(session)
    s = get_settings()
    return montar_catalogo(
        (
            {
                "sku": r.sku,
                "name": r.name,
                "formato": r.formato,
                "situacao": r.situacao,
                "stock": r.stock,
                "custo": r.bling_cost_price,
                "bid": r.bling_product_id,
                "upd": r.updated_at,
            }
            for r in produtos
        ),
        (
            {"kit": r.kit_bling_product_id, "comp": r.component_bling_product_id, "q": r.quantidade}
            for r in kits
        ),
        ({"id": r.id, "sku": r.sku, "name": r.name, "ativo": True} for r in linhas),
        prioridades=prioridades,
        familia_ativa=bool(getattr(s, "estoque_familia_ativo", False)),
        familia_redireciona=bool(getattr(s, "estoque_familia_redireciona", False)),
        familia_prefixos=tuple(
            p.strip().lower()
            for p in (getattr(s, "estoque_familia_prefixos", "") or "").split(",")
            if p.strip()
        ),
        lido_em=datetime.now(UTC),
    )


async def catalogo(session: AsyncSession, *, forcar: bool = False) -> Catalogo:
    """O catálogo da troca, com memória de 10 min no processo (`forcar` relê)."""
    agora = time.monotonic()
    guardado = _MEMORIA.get("catalogo")
    if guardado is not None and not forcar and agora - guardado[0] < CATALOGO_TTL_S:
        return guardado[1]
    cat = await _ler_catalogo(session)
    _MEMORIA["catalogo"] = (agora, cat)
    logger.info("atendimento_troca_catalogo_lido", produtos=len(cat.produtos))
    return cat


# ── O bloco do painel e da lista ──────────────────────────────────────────


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def _quantidade(valor: Any) -> int:
    n = _num(valor)
    return max(1, math.ceil(n)) if n is not None and n > 0 else 1


def _sugestao_out(s: Sugestao, nome_original: str, *, ve_custo: bool) -> dict:
    """Uma sugestão para a tela.

    O % do custo só para quem vê a Margem — e, para os outros, o motivo de
    fora pelo custo vira `fora_da_regra`; o texto da oferta só na elegível
    que pede o aceite do comprador (o nível 0 é o mesmo produto).
    """
    texto = None
    if s.motivo_fora is None and s.nivel != NIVEL_LOTE:
        texto = texto_oferta(nome_original, s.nome, mesmo_produto=s.mesmo_produto)
    motivo_fora = s.motivo_fora
    if not ve_custo and motivo_fora in _FORA_PELO_CUSTO:
        motivo_fora = FORA_DA_REGRA
    return {
        "sku": s.sku,
        "nome": s.nome or None,
        "nivel": s.nivel,
        "mesmo_produto": s.mesmo_produto,
        "estoque": s.estoque,
        "estoque_em": _iso(s.estoque_em),
        "produto_id": s.produto_id,
        "dif_custo_pct": s.dif_custo_pct if ve_custo else None,
        "motivo_fora": motivo_fora,
        "texto_oferta": texto,
    }


def item_de_troca(
    cat: Catalogo,
    sku: str,
    quantidade: int,
    descricao: str | None,
    *,
    ve_custo: bool,
    teto_pct: float = TETO_CUSTO_PCT_PADRAO,
    piso_n2_pct: float = PISO_NIVEL2_PCT_PADRAO,
) -> dict:
    """O bloco de UM item em falta: as sugestões, as de fora e o texto da 1ª. PURA."""
    o = cat.produtos.get(sku.strip().lower())
    nome_original = (o.nome if o is not None else "") or (descricao or "").strip() or sku
    lista = sugerir(cat, sku, quantidade, teto_pct=teto_pct, piso_n2_pct=piso_n2_pct)
    boas = [s for s in lista if s.motivo_fora is None]
    fora = [s for s in lista if s.motivo_fora is not None]
    sugestoes = [_sugestao_out(s, nome_original, ve_custo=ve_custo) for s in boas]
    sem_parecido = not boas and not fora
    return {
        "sku_original": sku,
        "quantidade": quantidade,
        "nome_original": nome_original,
        "sugestoes": sugestoes,
        "fora": [_sugestao_out(s, nome_original, ve_custo=ve_custo) for s in fora],
        # O texto da 1ª sugestão (o lote irmão do nível 0 não tem: é o mesmo produto).
        "texto_oferta": sugestoes[0]["texto_oferta"] if sugestoes else None,
        "sem_parecido": sem_parecido,
        "motivo_sem_sugestao": motivo_sem_sugestao(cat, sku) if sem_parecido else None,
    }


# O item em falta saiu do pedido (trocado à mão) depois da marca.
AVISO_FORA_DO_PEDIDO = "o item em falta já não está no pedido"


async def sugestoes_do_pedido(
    session: AsyncSession,
    skus_em_falta: Sequence[str],
    itens: Sequence[Mapping[str, Any]],
    *,
    ve_custo: bool,
    cat: Catalogo | None = None,
) -> dict:
    """O bloco `sugestoes_troca` do painel e da lista — só leitura, sem Bling.

    `skus_em_falta` = `Motivo.skus`; `itens` = os itens do pedido (sku,
    descricao, quantidade; o mesmo SKU em duas linhas soma). Vale a
    interseção: o SKU em falta que já não está no pedido vira `aviso`.
    `ve_custo` = quem vê a Margem (`painel.ve_margem`): só ele recebe o %.
    `cat` = o catálogo já lido (a lista lê uma vez para todos os pedidos);
    sem ele, `catalogo` (só se algum SKU em falta ainda está no pedido).
    """
    s = get_settings()
    teto = float(getattr(s, "atendimento_troca_teto_custo_pct", TETO_CUSTO_PCT_PADRAO))
    piso = float(getattr(s, "atendimento_troca_piso_nivel2_pct", PISO_NIVEL2_PCT_PADRAO))
    quantidades: dict[str, int] = {}
    descricoes: dict[str, str | None] = {}
    nomes: dict[str, str] = {}
    for it in itens:
        sku = str(it.get("sku") or "").strip()
        low = sku.lower()
        if not low:
            continue
        quantidades[low] = quantidades.get(low, 0) + _quantidade(it.get("quantidade"))
        descricoes.setdefault(low, it.get("descricao"))
        nomes.setdefault(low, sku)
    em_falta = list(dict.fromkeys(k for k in (str(x or "").strip() for x in skus_em_falta) if k))
    fora_do_pedido = [x for x in em_falta if x.lower() not in quantidades]
    if len(fora_do_pedido) < len(em_falta):
        cat = cat if cat is not None else await catalogo(session)
    else:
        cat = None
    blocos = []
    for sku in em_falta:
        low = sku.lower()
        if low not in quantidades or cat is None:
            continue
        blocos.append(
            item_de_troca(
                cat,
                nomes[low],
                quantidades[low],
                descricoes[low],
                ve_custo=ve_custo,
                teto_pct=teto,
                piso_n2_pct=piso,
            )
        )
    return {
        "itens": blocos,
        "aviso": (
            f"{AVISO_FORA_DO_PEDIDO}: {', '.join(fora_do_pedido)}" if fora_do_pedido else None
        ),
        "catalogo_lido_em": _iso(cat.lido_em) if cat is not None else None,
        "ve_custo": ve_custo,
        "falhou": False,
    }


def sugestoes_falhou(*, ve_custo: bool) -> dict:
    """O bloco quando a montagem quebrou: vazio, com `falhou` (o resto do painel segue)."""
    return {
        "itens": [],
        "aviso": None,
        "catalogo_lido_em": None,
        "ve_custo": ve_custo,
        "falhou": True,
    }
