"""17track — localização física REAL dos envios Correios (`...BR`) da Logística.

O Mercado Livre não expõe o local físico da rede própria; só o rastreio dos
Correios teria. O 17track rastreia os Correios (carrier 2151) e, via webhook
(push), empurra o evento novo assim que o pacote se move — a gente grava
`cidade/UF — descrição` em `logistica.localizacao`, sobrepondo o proxy do ML.

Fluxo:
  1. `register(numbers)` — registra os `...BR` no 17track (1x por número). A
     partir daí o 17track busca nos Correios e passa a empurrar atualizações.
  2. O webhook (`routers/logistica_track.py`) recebe o push e chama
     `parse_push(payload)` -> [(number, localizacao)] pra atualizar as linhas.

O 17track NÃO assina o push (sem HMAC documentado), então o endpoint é
protegido por um segmento secreto no path (`logi_17track_webhook_secret`).

Parser defensivo: o push pode vir no formato v2.2 (`track_info.latest_event`
com `address.city`) OU no v2.4 (`track_info.providers[].events[]` +
`latest_status`). `_fmt_from_track_info` tenta os dois.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

_BASE = "https://api.17track.net/track/v2.2"
# Correios do Brasil no catálogo de carriers do 17track.
CORREIOS_CARRIER = 2151


def _headers() -> dict[str, str]:
    token = (get_settings().logi_17track_token or "").strip()
    return {"17token": token, "Content-Type": "application/json"}


def is_correios(rastreio: str | None) -> bool:
    """Rastreio dos Correios = termina em `BR` (ex. AP178494655BR)."""
    r = (rastreio or "").strip().upper()
    return len(r) >= 4 and r.endswith("BR")


# O /register do 17track aceita no MÁXIMO 40 números por requisição (acima
# disso devolve -18010014 "Request limit exceeded"). Quebramos em lotes.
_REGISTER_BATCH = 40


async def register(numbers: list[str]) -> dict[str, Any]:
    """Registra números Correios no 17track (idempotente do lado deles — repetir
    o mesmo número não gasta quota extra). Quebra em lotes de 40 (limite do
    endpoint). Retorna o consolidado accepted/rejected/errors."""
    nums = [n for n in numbers if n]
    if not nums:
        return {"registered": 0}

    accepted: list[Any] = []
    rejected: list[Any] = []
    errors: list[Any] = []
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _REGISTER_BATCH):
            chunk = nums[i : i + _REGISTER_BATCH]
            payload = [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
            r = await c.post(f"{_BASE}/register", headers=_headers(), json=payload)
            try:
                body = r.json()
            except ValueError:
                body = {"status_code": r.status_code, "text": r.text[:300]}
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                accepted += data.get("accepted") or []
                rejected += data.get("rejected") or []
                errors += data.get("errors") or []
            logger.info(
                "logistica_17track_register", n=len(chunk), status=r.status_code
            )
    return {
        "registered": len(accepted),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "errors": errors,
    }


def _fmt_from_track_info(track_info: dict) -> str | None:
    """Monta `cidade/UF — descrição` do último evento; tenta o formato v2.2
    (`latest_event.address`) e cai no v2.4 (`providers[].events[]`)."""
    ti = track_info or {}

    # v2.2 — latest_event com address estruturado.
    ev = ti.get("latest_event") or {}
    if isinstance(ev, dict) and ev:
        addr = ev.get("address") or {}
        city = (addr.get("city") or "").strip()
        uf = (ev.get("location") or addr.get("state") or "").strip()
        descr = (ev.get("description") or "").strip()
        loc = _compose(city, uf, descr)
        if loc:
            return loc

    # v2.4 — providers[].events[] (o mais recente costuma ser events[0]).
    for p in ti.get("providers") or []:
        evs = p.get("events") if isinstance(p, dict) else None
        if not evs:
            continue
        e0 = evs[0] if isinstance(evs, list) and evs else None
        if not isinstance(e0, dict):
            continue
        where = (e0.get("location") or "").strip()
        descr = (e0.get("description") or "").strip()
        loc = _compose(where, "", descr)
        if loc:
            return loc
    return None


def _compose(city: str, uf: str, descr: str) -> str | None:
    where = "/".join(p for p in (city.strip(), uf.strip()) if p)
    d = descr.strip()
    if where and d:
        return f"{where} — {d}"
    return where or d or None


def parse_push(payload: dict) -> list[tuple[str, str]]:
    """Extrai [(number, localizacao)] de um push do 17track. Aceita o `data`
    como `{accepted:[...]}` ou como item único. Ignora itens sem localização."""
    data = payload.get("data")
    items: list[dict]
    if isinstance(data, dict):
        acc = data.get("accepted")
        items = acc if isinstance(acc, list) else [data]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    out: list[tuple[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        number = (it.get("number") or "").strip()
        if not number:
            continue
        loc = _fmt_from_track_info(it.get("track_info") or {})
        if loc:
            out.append((number, loc))
    return out
