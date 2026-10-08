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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

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
