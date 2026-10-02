"""Devoluções da Amazon pelo relatório da SP-API — TESTE (02/10/2026).

701-7824777-7251447 (KFA, envio pelo vendedor): a cliente pediu devolução em
29/09 ("Não é mais necessário"), a Amazon reembolsou no primeiro escaneamento e
a mala voltou pro galpão — e o DaVinci não sabia de nada: a API de pedidos não
mostra devolução (o pedido segue "Entregue ao cliente"). Quem mostra é a tela
"Gerenciar devoluções" do Seller Central e o relatório dela,
GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE.

A documentação pede os papéis "Inventory and Order Tracking" e "Direct to
Consumer Shipping (Restricted)" e não diz se basta um. Os nossos apps leem
pedidos (têm o primeiro); a KFA não tem o restrito (caso 21731531021, parado
desde 24/08). Este passo só PERGUNTA a cada conta e escreve no log do worker o
que veio — nada é gravado no banco. Vinicius, 02/10: "pode fazer o teste do
relatório".

Log sem dado pessoal: só as colunas do relatório e, por devolução, as colunas
de pedido/datas/status/motivo/rastreio/valores (nada de nome, endereço, e-mail).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform
from app.services.logistica_amazon import _build_amazon_client

logger = structlog.get_logger(__name__)

RELATORIO = "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE"
JANELA_DIAS = 30
MAX_LINHAS_NO_LOG = 40

# Coluna entra no log se o nome tiver um destes pedaços…
_COLUNA_SIM = (
    "order", "date", "status", "reason", "resolution", "tracking", "carrier",
    "label", "refund", "amount", "rma", "sku", "asin", "quantity", "policy",
    "type", "a-to-z", "safet", "claim", "currency", "prime", "category",
)
# …e nenhum destes (dado pessoal do comprador).
_COLUNA_NAO = ("name", "address", "email", "phone", "buyer", "city", "postal", "zip", "cpf")


def _coluna_no_log(nome: str) -> bool:
    n = nome.strip().lower()
    return any(p in n for p in _COLUNA_SIM) and not any(p in n for p in _COLUNA_NAO)


def resumir(tsv: str) -> dict:
    """Colunas + linhas (só as colunas sem dado pessoal) do TSV do relatório."""
    leitor = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    colunas = [c for c in (leitor.fieldnames or []) if c]
    no_log = [c for c in colunas if _coluna_no_log(c)]
    linhas = []
    total = 0
    for row in leitor:
        total += 1
        if len(linhas) < MAX_LINHAS_NO_LOG:
            valores = {c: (row.get(c) or "").strip() for c in no_log}
            linhas.append({c: v for c, v in valores.items() if v})
    return {"colunas": colunas, "total": total, "linhas": linhas}


async def testar_relatorio(session: AsyncSession) -> list[dict]:
    """Pede o relatório de devoluções dos últimos 30 dias a cada conta Amazon
    e loga o resultado (`amazon_devolucoes_teste`). Uma conta falhar não para
    as outras."""
    contas = (
        await session.execute(
            select(Integration)
            .where(
                Integration.platform == IntegrationPlatform.AMAZON,
                Integration.archived_at.is_(None),
            )
            .order_by(Integration.name)
        )
    ).scalars().all()
    fim = datetime.now(UTC).replace(microsecond=0)
    inicio = fim - timedelta(days=JANELA_DIAS)
    saida: list[dict] = []
    for integ in contas:
        conta = (integ.name or "").strip().lower() or str(integ.id)
        try:
            client = _build_amazon_client(session, integ)
            tsv = await client.baixar_relatorio(
                RELATORIO, data_inicio=inicio, data_fim=fim, max_poll_attempts=60
            )
            r = {"conta": conta, "ok": True, **resumir(tsv)}
        except Exception as e:  # noqa: BLE001 — o teste quer o motivo de cada conta
            r = {"conta": conta, "ok": False, "erro": str(e)[:400]}
        await session.commit()  # token renovado pelo client fica gravado
        logger.info("amazon_devolucoes_teste", **r)
        saida.append(r)
    return saida
