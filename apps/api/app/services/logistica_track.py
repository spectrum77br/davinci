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

import asyncio
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

# O 17track documenta limite de 3 req/s (429 acima disso). Uma pausa curta entre
# lotes mantém a rajada abaixo do teto sem atrasar de forma perceptível.
_PAUSA_ENTRE_LOTES = 0.4


# Códigos de erro do /register que o 17track devolve dentro de `rejected`.
# -18019901: o número JÁ está registrado — do nosso ponto de vista é sucesso
# (o 17track já busca nos Correios e vai empurrar os eventos).
ERRO_JA_REGISTRADO = -18019901
# -18019908: "Quota is not enough for use." A conta do 17track ficou sem saldo:
# NADA é aceito e a Localização inteira para de atualizar. Quem chama precisa
# saber disso pra não marcar o número como registrado e pra avisar o operador
# (Eduardo, 04/09: "rastreio e localização de correios não está atualizando" —
# era exatamente isto, com quota_remain negativo).
ERRO_SEM_QUOTA = -18019908


def _erro_code(item: Any) -> int | None:
    err = item.get("error") if isinstance(item, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


async def register(numbers: list[str]) -> dict[str, Any]:
    """Registra números Correios no 17track (idempotente do lado deles — repetir
    o mesmo número não gasta quota extra). Quebra em lotes de 40 (limite do
    endpoint).

    Devolve o consolidado E a lista `ok` com os números que ficaram de fato
    registrados (aceitos agora + os que já estavam), pra quem chama gravar a
    marca só do que passou; `sem_quota` avisa que a conta do 17track está sem
    saldo — nesse caso `ok` vem vazio e não adianta repetir até recarregar.
    """
    nums = [n for n in numbers if n]
    if not nums:
        return {"registered": 0, "ok": [], "sem_quota": False}

    accepted: list[Any] = []
    rejected: list[Any] = []
    errors: list[Any] = []
    ok: list[str] = []
    sem_quota = False
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _REGISTER_BATCH):
            chunk = nums[i : i + _REGISTER_BATCH]
            payload = [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            r = await c.post(f"{_BASE}/register", headers=_headers(), json=payload)
            try:
                body = r.json()
            except ValueError:
                body = {"status_code": r.status_code, "text": r.text[:300]}
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                acc = data.get("accepted") or []
                rej = data.get("rejected") or []
                accepted += acc
                rejected += rej
                errors += data.get("errors") or []
                ok += [
                    n for n in (it.get("number") for it in acc if isinstance(it, dict)) if n
                ]
                for it in rej:
                    code = _erro_code(it)
                    if code == ERRO_SEM_QUOTA:
                        sem_quota = True
                    elif code == ERRO_JA_REGISTRADO:
                        num = it.get("number") if isinstance(it, dict) else None
                        if num:
                            ok.append(num)
            logger.info(
                "logistica_17track_register",
                n=len(chunk),
                status=r.status_code,
                aceitos=len(ok),
                rejeitados=len(rejected),
            )
            if sem_quota:
                # Sem saldo TODO lote seguinte é recusado igual — insistir só
                # queima requisição contra o limite de 3 req/s do 17track.
                logger.warning(
                    "logistica_17track_para_por_falta_de_saldo",
                    enviados=i + len(chunk),
                    restantes=max(0, len(nums) - i - len(chunk)),
                )
                break
    if sem_quota:
        logger.warning("logistica_17track_sem_quota", numeros=len(nums))
    return {
        "registered": len(accepted),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "errors": errors,
        "ok": sorted(set(ok)),
        "sem_quota": sem_quota,
    }


# O /gettrackinfo aceita 40 números por requisição, igual ao /register.
_FETCH_BATCH = 40


async def fetch(numbers: list[str]) -> list[tuple[str, str]]:
    """Consulta o 17track e devolve [(numero, localizacao)] dos que já têm evento.

    É a rede de segurança do push: o webhook dá o tempo real, mas se ele estiver
    fora do ar (ou nem configurado no painel do 17track) a Localização
    congelaria sem ninguém perceber. Leitura pura — não registra número nem
    gasta quota; número não registrado simplesmente volta em `rejected`.
    """
    nums = sorted({(n or "").strip() for n in numbers if (n or "").strip()})
    if not nums:
        return []
    out: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _FETCH_BATCH):
            chunk = nums[i : i + _FETCH_BATCH]
            payload = [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            r = await c.post(f"{_BASE}/gettrackinfo", headers=_headers(), json=payload)
            try:
                body = r.json()
            except ValueError:
                logger.warning("logistica_17track_fetch_resposta_invalida", status=r.status_code)
                continue
            data = body.get("data") if isinstance(body, dict) else None
            aceitos = (data or {}).get("accepted") or []
            for it in aceitos:
                if not isinstance(it, dict):
                    continue
                num = (it.get("number") or "").strip()
                loc = _fmt_from_track_info(it.get("track_info") or {})
                if num and loc:
                    out.append((num, loc))
            logger.info(
                "logistica_17track_fetch", n=len(chunk), status=r.status_code, com_evento=len(out)
            )
    return out


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
