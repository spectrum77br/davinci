"""Botão Atualizar da aba Chamados (09/11/2026, Vinicius: "um botão atualizar aqui
fora, quando eu clicar ele faz a leitura de todos os chamados e atualiza tudo").

Só a leitura pela API (`chamados_devolucao_sync.sync_respostas`, a mesma passada
que o cron faz de hora em hora no :25): Shopee, TikTok e ML. Os casos de TELA
(abertos no Seller Center, Amazon, consulta do Portal) ficam com o robô de
leitura e a cadência dele (3 h / 24 h, `chamados_leitura.INTERVALO`): "ler todos
agora" abriria dezenas de sessões de loja em sequência — o risco de bloqueio
que a cadência existe para evitar. Para um desses, o Atualizar do histórico
fura a fila só daquele caso.

Duas travas no Redis:
  - `CHAVE_PEDIDO` (15 min, quem pediu e o job): o clique repetido — de qualquer
    pessoa — não vira rajada nas plataformas; quem clica de novo nesse tempo
    acompanha a mesma leitura (ou só recarrega a lista, se ela já terminou).
  - `TRAVA_SYNC` (com renovação, no worker): o botão e o cron do :25 nunca leem
    juntos — duas passadas ao mesmo tempo podiam gravar a mesma fala duas vezes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

CHAVE_PEDIDO = "davinci:chamados:atualizar"
TRAVA_SYNC = "davinci:lock:chamados_sync_respostas"
INTERVALO_S = 15 * 60
# A leitura de todos pode passar de minutos (TikTok pagina devagar); o 60 s da
# fila UI a mataria no meio.
TIMEOUT_JOB_S = 1200
JOB = "chamados_atualizar_api"


def valor_do_pedido(*, pedido_em: datetime, por: str, job_id: str | None) -> str:
    return json.dumps({"pedido_em": pedido_em.isoformat(), "por": por, "job_id": job_id})


def ler_pedido(valor: str | None) -> dict[str, Any]:
    """O que está guardado na trava do clique ({} se vazio ou ilegível)."""
    if not valor:
        return {}
    try:
        d = json.loads(valor)
    except (TypeError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    try:
        d["pedido_em"] = datetime.fromisoformat(d["pedido_em"]) if d.get("pedido_em") else None
    except (TypeError, ValueError):
        d["pedido_em"] = None
    return d


def pode_de_novo_em(agora: datetime, ttl_s: int | None) -> datetime:
    return agora + timedelta(seconds=max(int(ttl_s or 0), 0))


def agora_utc() -> datetime:
    return datetime.now(UTC)
