"""A forma do ColetaDados versao 1 (docs/conferencia-shopee.md §4).

Quem manda os números de uma loja — o executor do Mac (Shopee, rota
/agent/coletas/{id}/resultado) ou um coletor do servidor (ML e Amazon,
coletores/) — passa por aqui ANTES de guardar. Só CONFERE a forma: o que se
guarda é o dict cru que chegou. Campo a mais passa; lista de coisa que não é
objeto, número que não é número ou versão desconhecida → ValidationError (a
rota responde 422; o job do servidor fecha a loja como `erro`): um item torto
guardado derrubaria o cálculo da rodada.

Era o modelo da rota (routers/marketing_conferencia, 06/10/2026); veio para
cá em 07/10/2026 para o job do servidor usar a mesma régua. Itens ganharam
`sku` e `eletro` (opcionais; coletores do ML/Amazon — coletores/__init__).

24/11/2026 (M-36 item 6): cada semana pode trazer `dias` — [{dia, afiliados,
ads}], o investimento de cada dia da semana (comissão de afiliados e gasto de
Ads, R$; null = a Shopee não devolveu o dia) — e `dias_motivos`. Quem lê é o
Faturamento (Tabela de Preços); o relatório da Conferência NÃO lê. Por isso
`limpar_dias` roda ANTES da conferência da forma: dia a dia torto é jogado
fora (a semana fica sem `dias` e o Faturamento usa a semana inteira), nunca
recusa a loja — senão um defeito no dia a dia derrubaria a Conferência.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict

logger = structlog.get_logger()

_Numero = float | None


class _Forma(BaseModel):
    model_config = ConfigDict(extra="allow")


class _AfiliadosTotais(_Forma):
    vendas: _Numero = None
    comissao: _Numero = None
    pedidos: _Numero = None
    # 07/10/2026 (seller_daily → data.clicks). Opcional: o executor antigo não
    # manda e a métrica fica "—" no relatório.
    cliques: _Numero = None


class _AfiliadoItem(_Forma):
    item_id: str | int | None = None
    nome: str | None = None
    categoria_id: int | str | None = None
    vendas: _Numero = None
    comissao: _Numero = None
    pedidos: _Numero = None
    # 07/10/2026 (seller_item_detail → clicks). Opcional, como o de cima.
    cliques: _Numero = None


class _AdsTotais(_Forma):
    impressoes: _Numero = None
    cliques: _Numero = None
    gasto: _Numero = None
    vendas: _Numero = None
    pedidos: _Numero = None


class _AdsItem(_AdsTotais):
    item_id: str | int | None = None
    nome: str | None = None
    tipo: str | None = None
    # ML/Amazon (07/10/2026): o SKU do DaVinci e a marca de eletro do coletor.
    sku: str | None = None
    eletro: bool | None = None


class _VendasTotais(_Forma):
    valor: _Numero = None
    pedidos: _Numero = None


class _VendaItem(_VendasTotais):
    item_id: str | int | None = None
    nome: str | None = None
    sku: str | None = None
    eletro: bool | None = None


class _DiaDados(_Forma):
    # M-36 item 6 (24/11/2026): investimento de um dia (R$). null = sem o dia.
    dia: str
    afiliados: _Numero = None
    ads: _Numero = None


class _SemanaDados(_Forma):
    inicio: str
    fim: str
    afiliados: _AfiliadosTotais | None = None
    afiliados_itens: list[_AfiliadoItem] | None = None
    ads: _AdsTotais | None = None
    ads_itens: list[_AdsItem] | None = None
    vendas: _VendasTotais | None = None
    vendas_itens: list[_VendaItem] | None = None
    avisos: list[str] | None = None
    # M-36 item 6 (24/11/2026): passam por `limpar_dias` antes (ver acima).
    dias: list[_DiaDados] | None = None
    dias_motivos: list[str] | None = None


class _LoginLoja(_Forma):
    username: str | None = None
    shopid: int | str | None = None
    shop_name: str | None = None


class ColetaDadosV1(_Forma):
    versao: Literal[1]
    coletado_em: str | None = None
    duracao_s: _Numero = None
    chamadas: _Numero = None
    login: _LoginLoja | None = None
    login_auto_usado: bool | None = None
    saldo_ads: _Numero = None
    afiliados_ultimo_dia: str | None = None
    semanas: list[_SemanaDados]
    avisos: list[str] | None = None


class ConfereDados(BaseModel):
    # Embrulho só para o erro apontar ("dados", "semanas", 0, …).
    dados: ColetaDadosV1


def confere_coleta_dados(dados: Mapping[str, Any]) -> None:
    """Levanta pydantic.ValidationError se a forma não é a do ColetaDados v1."""
    ConfereDados.model_validate({"dados": dados})


# ───────────────────────────────────────────── dia a dia (M-36 item 6)

_DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# lado do dia → (seção da semana, campo do total) — os mesmos números da linha
# Investimento da Conferência (calculo._SECOES: invest_afiliados / invest_ads).
_LADOS_DO_DIA = {"afiliados": ("afiliados", "comissao"), "ads": ("ads", "gasto")}
# Os dias com número somam o total da semana centavo a centavo (o executor
# reparte os centavos); 1 centavo de folga para o ponto flutuante.
_FOLGA_SOMA = 0.011


def _numero_do_dia(valor: Any) -> tuple[bool, float | None]:
    """(serve?, valor): None serve; número finito serve; o resto não."""
    if valor is None:
        return True, None
    if isinstance(valor, bool) or not isinstance(valor, int | float) or not math.isfinite(valor):
        return False, None
    return True, float(valor)


def _total_da_semana(semana: Mapping, secao: str, campo: str) -> float | None:
    bloco = semana.get(secao)
    valor = bloco.get(campo) if isinstance(bloco, Mapping) else None
    ok, numero = _numero_do_dia(valor)
    return numero if ok else None


_DESCARTADO = "dia a dia descartado pelo servidor"


def _dias_limpos(semana: Mapping) -> tuple[list[dict] | None, list[str]]:
    """(dias, motivos) de UMA semana. dias None = descartar o dia a dia."""
    dias = semana.get("dias")
    inicio, fim = semana.get("inicio"), semana.get("fim")
    if not isinstance(dias, list):
        return None, [f"{_DESCARTADO}: não é lista"]
    if not (
        isinstance(inicio, str) and isinstance(fim, str) and _DIA.match(inicio) and _DIA.match(fim)
    ):
        return None, [f"{_DESCARTADO}: semana sem datas"]
    limpos: list[dict] = []
    vistos: set[str] = set()
    for item in dias:
        if not isinstance(item, Mapping):
            return None, [f"{_DESCARTADO}: item que não é objeto"]
        dia = item.get("dia")
        if not isinstance(dia, str) or not _DIA.match(dia) or not inicio <= dia <= fim:
            return None, [f"{_DESCARTADO}: dia fora da semana ({str(dia)[:20]})"]
        if dia in vistos:
            return None, [f"{_DESCARTADO}: dia repetido ({dia})"]
        ok_af, afiliados = _numero_do_dia(item.get("afiliados"))
        ok_ads, ads = _numero_do_dia(item.get("ads"))
        if not (ok_af and ok_ads):
            return None, [f"{_DESCARTADO}: valor que não é número ({dia})"]
        vistos.add(dia)
        limpos.append({"dia": dia, "afiliados": afiliados, "ads": ads})
    limpos.sort(key=lambda d: d["dia"])
    motivos: list[str] = []
    for lado, (secao, campo) in _LADOS_DO_DIA.items():
        valores = [d[lado] for d in limpos if d[lado] is not None]
        if not valores:
            continue
        total = _total_da_semana(semana, secao, campo)
        if total is None or abs(sum(valores) - total) > _FOLGA_SOMA:
            # O Faturamento soma os dias achando que dão o total da Conferência:
            # dia a dia que não fecha não pode chegar lá.
            for d in limpos:
                d[lado] = None
            motivos.append(f"{lado}: {_DESCARTADO} (não fecha com a semana)")
    return limpos, motivos


def limpar_dias(dados: Any) -> Any:
    """Os `dados` com o dia a dia de cada semana conferido (M-36 item 6,
    24/11/2026). Nunca levanta e nunca mexe no resto: semana sem `dias` fica
    como veio; `dias` torto sai (com o motivo em `dias_motivos`); lado do dia
    (afiliados ou Ads) cujos dias não somam o total da semana vira null.
    Itens limpos só com `dia`, `afiliados` e `ads`."""
    if not isinstance(dados, Mapping) or not isinstance(dados.get("semanas"), list):
        return dados
    semanas: list[Any] = []
    for semana in dados["semanas"]:
        if not isinstance(semana, Mapping) or (
            "dias" not in semana and "dias_motivos" not in semana
        ):
            semanas.append(semana)
            continue
        nova = dict(semana)
        motivos_velhos = semana.get("dias_motivos")
        motivos = (
            [str(m)[:300] for m in motivos_velhos if isinstance(m, str)][:10]
            if isinstance(motivos_velhos, list)
            else []
        )
        if semana.get("dias") is not None:
            dias, novos = _dias_limpos(semana)
            if dias is None:
                nova.pop("dias", None)
            else:
                nova["dias"] = dias
            if novos:
                logger.warning(
                    "conferencia_shopee_dias_descartados",
                    semana=f"{semana.get('inicio')}..{semana.get('fim')}",
                    motivos=novos,
                )
            motivos += novos
        nova["dias_motivos"] = motivos
        semanas.append(nova)
    return {**dados, "semanas": semanas}
