"""Coletores do SERVIDOR da Conferência — Mercado Livre e Amazon (07/10/2026).

A Shopee é coletada pelo executor do Mac (AdsPower, `apps/executor/src/
conferencia.ts`). Mercado Livre e Amazon são coletados AQUI, no worker: o job
`conferencia_coletar_servidor(execucao_id)` (services/conferencia_shopee/
servidor.py) pega as coletas da rodada uma a uma e chama o coletor da
plataforma. Nada de AdsPower nem de executor: o lease do Mac só serve coleta da
Shopee (fila.lease filtra `plataforma = 'shopee'`).

A interface — um módulo por plataforma (`coletores/ml.py`, `coletores/amazon.py`)
com UMA função:

    async def collect(
        conta: ContaColeta, semanas: Sequence[Semana], *, session: AsyncSession
    ) -> ColetaDados

  • `conta` — a loja daquela coleta (foto do nome e do grupo da rodada +
    integração e loja do Bling da lista de contas);
  • `semanas` — as 4 semanas da rodada, S1 (a do relatório) primeiro;
  • `session` — sessão SÓ desta loja (o job faz commit quando o coletor volta
    sem erro e rollback se ele levantar): ler pedidos do Bling, vínculos,
    credenciais. Token renovado da integração (o refresh_token do ML é de uso
    único) se grava numa sessão PRÓPRIA, como `marketing/ml_sync._client` faz —
    o rollback de uma coleta que falhou não pode desfazer a renovação.

Devolve os números no MESMO formato que o executor da Shopee manda
(ColetaDados versao 1, docs/conferencia-shopee.md §4 — conferido por
`dados.confere_coleta_dados` antes de guardar):

    {
      "versao": 1,
      "coletado_em": "2026-10-07T16:31:02Z",        # opcional (o job preenche)
      "semanas": [                                   # as 4, com inicio/fim do job
        {
          "inicio": "2026-09-28", "fim": "2026-10-04",
          "vendas": {"valor": 1234.5, "pedidos": 12},
          "vendas_itens": [
            {"item_id": "dg053.sp", "sku": "dg053.sp", "nome": "…",
             "valor": 99.9, "pedidos": 1, "eletro": False},
          ],
          "ads": {"impressoes": 1000, "cliques": 50, "gasto": 30.0,
                  "vendas": 400.0, "pedidos": 4},
          "ads_itens": [
            {"item_id": "MLB123", "sku": "uaf001m1.110", "nome": "…",
             "impressoes": 10, "cliques": 1, "gasto": 0.5, "vendas": 0.0,
             "pedidos": 0, "eletro": True},
          ],
          "avisos": ["…"],
        },
        …
      ],
      "avisos": ["…"],
    }

Regras do formato (as mesmas da Shopee — calculo.montar_relatorio):
  • Seção que FALHOU fica de fora (sem a chave, ou None): a célula sai "—" em
    Celular e em Eletro. Nunca 0 no lugar de erro.
  • `vendas_itens`/`ads_itens` ausentes com o total presente → o total inteiro
    fica em Celular e a linha da conta ganha o aviso (o cálculo faz sozinho).
    Ex.: conta do ML sem os anúncios por item.
  • `afiliados`, `afiliados_itens` e `saldo_ads`: NÃO mandar. O estado da
    célula ("não coletado", "não se aplica", "aguardando acesso") vem do perfil
    da plataforma (plataformas.ESTADOS), não dos dados.
  • Eletro de cada item, nesta ordem: o SKU (`sku`) pela classificação do
    DaVinci — refeita a cada cálculo, então "Recalcular" pega vínculo/categoria
    nova (classificacao.eletro_por_sku); senão a marca `eletro` que o coletor
    pôs no item; senão o título (`nome`). Sem nada disso: não é eletro.
    `item_id` é a chave do item dentro da seção (vendas: o SKU; Ads do ML: o
    MLB…). Item de Ads sem anúncio (`item_id` None) fica em Celular.
  • Vendas (ML e Amazon, decisão do dono 07/10/2026): itens dos pedidos do
    Bling da semana (pela data do pedido, a mesma da aba Faturamento), valor
    de tabela dos produtos (SEM frete e sem os descontos do pedido), sem os
    pedidos cancelados na hora da coleta.

Erros:
  • a loja inteira falhou → `raise ColetorError("mensagem em pt-BR")` (a coleta
    vira `erro`, sem números, e o relatório fecha com as outras);
  • exceção qualquer → o job trata como `erro` com o nome da exceção;
  • uma parte falhou (ex.: Ads sem permissão) → deixe a seção de fora e ponha
    um aviso: a coleta vira `parcial` sozinha (semana da rodada que não veio,
    ou sem uma seção esperada da plataforma — plataformas.SECOES_ESPERADAS);
  • cada loja tem um limite de tempo (servidor.LIMITE_POR_CONTA_S): passou,
    vira `erro` e a rodada segue.

Teste e substituição: `registrar(plataforma, coletor)` troca o coletor (os
testes usam um falso); sem registro, `obter` importa `coletores.<plataforma>`
e usa a função `collect` de lá.

Os coletores (07/10/2026): `ml.py` (Vendas do Bling + Ads do ML, total e
anúncio a anúncio), `amazon.py` (Vendas do Bling; Ads aguardando o acesso) e a
peça comum `vendas_bling.py` (os itens dos pedidos do Bling da loja na semana).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# O ColetaDados é um dict JSON (versao 1); a forma é conferida em
# services/conferencia_shopee/dados.py.
ColetaDados = dict[str, Any]


class ColetorError(Exception):
    """A loja inteira falhou: a mensagem (pt-BR) vai para o `erro` da coleta."""

    def __init__(self, mensagem: str):
        self.mensagem = mensagem
        super().__init__(mensagem)


# O mesmo, com o nome em português (como em `raise ColetorErro("…")`).
ColetorErro = ColetorError


@dataclass(frozen=True)
class Semana:
    """Uma semana da rodada (datas no fuso de Brasília, fim incluído)."""

    inicio: date
    fim: date

    def chave(self) -> dict[str, str]:
        """{"inicio": "AAAA-MM-DD", "fim": "AAAA-MM-DD"} — o começo de cada
        item de `semanas` no ColetaDados."""
        return {"inicio": self.inicio.isoformat(), "fim": self.fim.isoformat()}


@dataclass(frozen=True)
class ContaColeta:
    """A loja de UMA coleta. `nome` e `grupo` são a foto da rodada; o resto vem
    da lista de contas (`conferencia_shopee_conta`) na hora da coleta."""

    coleta_id: UUID
    execucao_id: UUID
    conta_id: UUID | None
    plataforma: str  # ml | amazon
    nome: str
    grupo: str  # mala | celular
    integration_id: UUID | None
    # Id da loja no Bling (`bling_orders.loja`), em texto.
    bling_loja_id: str | None
    conta_key: str | None


class Coletor(Protocol):
    def __call__(
        self, conta: ContaColeta, semanas: Sequence[Semana], *, session: AsyncSession
    ) -> Awaitable[ColetaDados]: ...


_REGISTRO: dict[str, Callable[..., Awaitable[ColetaDados]]] = {}


def registrar(plataforma: str, coletor: Coletor | None) -> None:
    """Troca (ou, com None, tira) o coletor de uma plataforma."""
    if coletor is None:
        _REGISTRO.pop(plataforma, None)
    else:
        _REGISTRO[plataforma] = coletor


def obter(plataforma: str) -> Coletor:
    """O coletor registrado ou o `collect` de `coletores.<plataforma>`.
    Sem coletor → ColetorError (a coleta vira `erro`, sem derrubar a rodada)."""
    if plataforma in _REGISTRO:
        return _REGISTRO[plataforma]
    try:
        modulo = importlib.import_module(f"{__name__}.{plataforma}")
    except ModuleNotFoundError as e:
        if e.name != f"{__name__}.{plataforma}":
            raise
        raise ColetorError(f"sem coletor para a plataforma {plataforma}") from e
    coletor = getattr(modulo, "collect", None)
    if coletor is None:
        raise ColetorError(f"sem coletor para a plataforma {plataforma}")
    return coletor


def semanas_do_job(semanas: Sequence[Mapping[str, Any]]) -> list[Semana]:
    """As semanas guardadas na execução (`[{"inicio", "fim"}]`) como Semana."""
    return [
        Semana(date.fromisoformat(str(s["inicio"])[:10]), date.fromisoformat(str(s["fim"])[:10]))
        for s in semanas
    ]
