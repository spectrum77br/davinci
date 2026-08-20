"""Carimbo de "isso é de quando?" pra cada campo do Status Plataforma.

O grid da Logística mostra a assinatura do canal (`Logistica.meli_status`)
traduzida pra PT — "Cancelado | Não entregue | Retido na alfândega | Envio".
Faltava a pergunta que o operador sempre faz olhando pro balãozinho: há quanto
tempo esse pedido está retido? Este módulo guarda, por campo, um carimbo em
`Logistica.status_datas`:

    {"ship_substatus": {"em": "2026-08-12T08:05:00+00:00", "fonte": "aprox"}}

Três fontes, nesta ordem de confiança:

  * ``plataforma`` — data OFICIAL da mudança, veio do canal (ex.:
    `shipment.status_history.date_shipped` do ML, `update_time` da Shopee).
  * ``aprox`` — o canal datou o RECURSO, não aquele campo (ex.:
    `shipment.last_updated` pro substatus, que o ML não data). É a melhor
    estimativa: a última vez que o envio mexeu.
  * ``davinci`` — o canal não deu data nenhuma: carimbamos o instante em que o
    DaVinci VIU o valor mudar (change detection).

Regra de ouro: **campo cujo valor não mudou mantém o carimbo antigo**. Sem
isso, cada 🔄 empurraria todas as datas pra frente e o operador nunca saberia
há quanto tempo o pedido está parado — que é exatamente a informação que ele
quer. Uma data ``plataforma`` sempre substitui um carimbo estimado do mesmo
campo (a oficial é melhor, mesmo que o valor não tenha mudado).

Campo que sumiu da assinatura perde o carimbo — o dicionário acompanha o
`meli_status` atual, sem lixo de status antigo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Fontes possíveis do carimbo (ordem de confiança decrescente).
FONTE_PLATAFORMA = "plataforma"
FONTE_APROX = "aprox"
FONTE_DAVINCI = "davinci"

# Acima disso o número só faz sentido como milissegundos (1e11 s ≈ ano 5138).
_MILLIS_CUTOFF = 100_000_000_000


def iso(valor: Any) -> str | None:
    """Normaliza qualquer data de canal pra ISO-8601 em UTC.

    Aceita `datetime`, string ISO (com ou sem `Z`/offset), epoch em segundos
    (Shopee/TikTok) e epoch em milissegundos (eventos de tracking do TikTok).
    Devolve None quando não dá pra entender — o chamador então cai pra fonte
    seguinte, nunca inventa data."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        dt = valor if valor.tzinfo else valor.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    if isinstance(valor, bool):  # bool é int em Python — não é data.
        return None
    if isinstance(valor, int | float):
        return _from_epoch(float(valor))
    texto = str(valor).strip()
    if not texto:
        return None
    # Epoch mandado como string ("1755000000") — Shopee/TikTok às vezes fazem.
    if texto.lstrip("-").isdigit():
        return _from_epoch(float(texto))
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _from_epoch(n: float) -> str | None:
    if n <= 0:
        return None
    if n >= _MILLIS_CUTOFF:
        n = n / 1000.0
    try:
        return datetime.fromtimestamp(n, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def carimbo(valor: Any, fonte: str) -> dict[str, str] | None:
    """`{"em": ISO, "fonte": ...}` ou None se a data não for legível."""
    em = iso(valor)
    if em is None:
        return None
    return {"em": em, "fonte": fonte}


def propor(
    destino: dict[str, dict[str, str]],
    campo: str,
    valor: Any,
    fonte: str,
) -> None:
    """Registra uma proposta de data pro campo, se ainda não houver uma melhor.

    Açúcar pros `build_enrichment`: chame na ordem de preferência (primeiro a
    data oficial, depois o fallback) que o resto se resolve — proposta oficial
    nunca é sobrescrita por estimativa."""
    novo = carimbo(valor, fonte)
    if novo is None:
        return
    atual = destino.get(campo)
    if atual and atual.get("fonte") == FONTE_PLATAFORMA and fonte != FONTE_PLATAFORMA:
        return
    destino[campo] = novo


def merge_datas(
    *,
    anterior_status: dict[str, str] | None,
    novo_status: dict[str, str] | None,
    propostas: dict[str, dict[str, str]] | None = None,
    anteriores: dict[str, Any] | None = None,
    agora: datetime | None = None,
) -> dict[str, dict[str, str]]:
    """Devolve o `status_datas` novo da linha.

    * `anterior_status` / `novo_status` — o `meli_status` antes e depois.
    * `propostas` — datas que o canal deu agora (`{campo: {"em","fonte"}}`).
    * `anteriores` — o `status_datas` que já estava gravado.
    """
    props = propostas or {}
    velhas = anteriores if isinstance(anteriores, dict) else {}
    antes = anterior_status or {}
    agora_iso = iso(agora or datetime.now(tz=UTC)) or datetime.now(tz=UTC).isoformat()

    out: dict[str, dict[str, str]] = {}
    for campo, valor in (novo_status or {}).items():
        prop = props.get(campo) or {}
        # 1) Data oficial do canal manda sempre.
        if prop.get("em") and prop.get("fonte") == FONTE_PLATAFORMA:
            out[campo] = {"em": str(prop["em"]), "fonte": FONTE_PLATAFORMA}
            continue
        anterior = velhas.get(campo)
        anterior = anterior if isinstance(anterior, dict) else {}
        # 2) Valor não mudou e já tinha carimbo → congela (é o "parado desde").
        if str(antes.get(campo) or "") == str(valor or "") and anterior.get("em"):
            out[campo] = {
                "em": str(anterior["em"]),
                "fonte": str(anterior.get("fonte") or FONTE_DAVINCI),
            }
            continue
        # 3) Mudou (ou é a primeira vez): melhor estimativa do canal, senão agora.
        if prop.get("em"):
            out[campo] = {"em": str(prop["em"]), "fonte": str(prop.get("fonte") or FONTE_APROX)}
            continue
        out[campo] = {"em": agora_iso, "fonte": FONTE_DAVINCI}
    return out


def aplicar(
    row: Any,
    novo_status: dict[str, str],
    propostas: dict[str, dict[str, str]] | None = None,
    *,
    agora: datetime | None = None,
) -> dict[str, dict[str, str]]:
    """Atalho usado pelos `enrich_row`: lê o estado atual da linha, calcula as
    datas e já grava. Chame ANTES de trocar `row.meli_status`."""
    return merge_datas(
        anterior_status=getattr(row, "meli_status", None) or {},
        novo_status=novo_status,
        propostas=propostas,
        anteriores=getattr(row, "status_datas", None) or {},
        agora=agora,
    )
