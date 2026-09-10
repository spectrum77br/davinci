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
from datetime import UTC, datetime
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


class Track17Error(Exception):
    """O 17track não respondeu direito (rede, 429/5xx, corpo inválido, code != 0).
    Quem decide gastar crédito NÃO pode tratar isso como "sem dados": abortar a
    rodada é mais barato do que forçar reconsulta em tudo por engano."""


# Códigos que o 17track devolve em `rejected` e que mudam a decisão.
ERRO_NAO_REGISTRADO = -18019902  # o 17track não conhece o número
ERRO_RETRACK_SO_PARADO = -18019904  # retrack em número que ainda está rastreando
ERRO_RETRACK_SO_UMA_VEZ = -18019905  # o "retomar" gratuito já foi usado
ERRO_JA_PARADO = -18019906  # stoptrack em número que já está parado

_TENTATIVAS = 3


async def _chamar(c: httpx.AsyncClient, endpoint: str, payload: Any) -> dict:
    """POST no 17track com retry curto pra 429/5xx/rede. Levanta `Track17Error`
    quando, mesmo assim, não veio uma resposta válida (`code == 0`)."""
    ultimo = ""
    for tentativa in range(_TENTATIVAS):
        if tentativa:
            await asyncio.sleep(1.5 * tentativa)
        try:
            r = await c.post(f"{_BASE}/{endpoint}", headers=_headers(), json=payload)
        except httpx.HTTPError as e:
            ultimo = f"rede: {str(e)[:120]}"
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ultimo = f"HTTP {r.status_code}"
            continue
        try:
            body = r.json()
        except ValueError:
            ultimo = f"HTTP {r.status_code} corpo inválido"
            continue
        if r.status_code != 200 or not isinstance(body, dict) or body.get("code", 0) != 0:
            code = body.get("code") if isinstance(body, dict) else None
            raise Track17Error(f"{endpoint}: HTTP {r.status_code} code={code}")
        return body
    raise Track17Error(f"{endpoint}: {ultimo or 'sem resposta'}")


def _aceitos_e_recusados(body: dict) -> tuple[list[str], dict[str, int | None]]:
    """(números aceitos, {número recusado: código de erro})."""
    data = body.get("data") if isinstance(body, dict) else None
    acc = (data or {}).get("accepted") or []
    rej = (data or {}).get("rejected") or []
    aceitos = [n for n in (it.get("number") for it in acc if isinstance(it, dict)) if n]
    recusados = {
        it["number"]: _erro_code(it) for it in rej if isinstance(it, dict) and it.get("number")
    }
    return aceitos, recusados


def _sync_at(track_info: dict) -> datetime | None:
    """Quando o 17track consultou os Correios pela última vez
    (`tracking.providers[].latest_sync_time`, ISO em UTC)."""
    for p in ((track_info or {}).get("tracking") or {}).get("providers") or []:
        raw = (p or {}).get("latest_sync_time") if isinstance(p, dict) else None
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


# Pacote que não vai mais se mover: reconsultar não traz nada e custaria crédito.
STATUS_ENCERRADO = frozenset({"Delivered", "Expired"})
SUBSTATUS_ENCERRADO = frozenset(
    {"Exception_Returned", "Exception_Cancel", "Exception_Destroyed", "Exception_Lost"}
)


def encerrado(info: dict | None) -> bool:
    d = info or {}
    return d.get("status") in STATUS_ENCERRADO or d.get("sub_status") in SUBSTATUS_ENCERRADO


async def fetch_detalhado(numbers: list[str]) -> dict[str, Any]:
    """Como `fetch`, mas devolve também QUANDO o 17track consultou os Correios
    (`sync_at`) e o status do pacote. Leitura pura, sem gastar quota.

    Devolve {"info": {número: {localizacao, sync_at, status, sub_status}},
    "desconhecidos": [números que o 17track diz não conhecer]}. Levanta
    `Track17Error` se a resposta não for confiável."""
    nums = sorted({(n or "").strip() for n in numbers if (n or "").strip()})
    info: dict[str, dict[str, Any]] = {}
    desconhecidos: list[str] = []
    if not nums:
        return {"info": info, "desconhecidos": desconhecidos}
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _FETCH_BATCH):
            chunk = nums[i : i + _FETCH_BATCH]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            body = await _chamar(
                c, "gettrackinfo", [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
            )
            data = body.get("data") or {}
            for it in data.get("accepted") or []:
                if not isinstance(it, dict) or not it.get("number"):
                    continue
                ti = it.get("track_info") or {}
                st = ti.get("latest_status") or {}
                info[str(it["number"]).strip()] = {
                    "localizacao": _fmt_from_track_info(ti),
                    "sync_at": _sync_at(ti),
                    "status": (st.get("status") or "").strip(),
                    "sub_status": (st.get("sub_status") or "").strip(),
                }
            _acc, rec = _aceitos_e_recusados(body)
            desconhecidos += [n for n, code in rec.items() if code == ERRO_NAO_REGISTRADO]
    return {"info": info, "desconhecidos": sorted(set(desconhecidos))}


async def estado_numeros(numbers: list[str]) -> dict[str, dict[str, Any]]:
    """Situação de cada número no 17track via `gettracklist` filtrado por
    `number` (aceita vários separados por vírgula): `tracking_status`
    (Tracking/Stopped), `is_retracked` (já usou o retomar gratuito — o 17track
    só permite UMA vez, -18019905), `stop_reason`, `package_status`. Número que
    o 17track não conhece simplesmente não vem."""
    nums = sorted({(n or "").strip() for n in numbers if (n or "").strip()})
    out: dict[str, dict[str, Any]] = {}
    if not nums:
        return out
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _FETCH_BATCH):
            chunk = nums[i : i + _FETCH_BATCH]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            body = await _chamar(
                c, "gettracklist", {"number": ",".join(chunk), "page_size": _FETCH_BATCH}
            )
            for it in (body.get("data") or {}).get("accepted") or []:
                if not isinstance(it, dict) or not it.get("number"):
                    continue
                out[str(it["number"]).strip()] = {
                    "tracking_status": (it.get("tracking_status") or "").strip(),
                    "is_retracked": bool(it.get("is_retracked")),
                    "stop_reason": it.get("stop_track_reason"),
                    "package_status": it.get("package_status"),
                }
    return out


async def parar_e_retomar(numbers: list[str]) -> dict[str, list[str]]:
    """Força o 17track a consultar os Correios AGORA sem gastar crédito:
    `stoptrack` + `retrack`, LOTE A LOTE (parar tudo e só depois retomar
    deixaria dezenas de números parados se a rede falhasse no meio). Testado
    em 08/09 (AD877240392BR): a consulta veio em menos de 1 min e o push do
    webhook chegou em seguida.

    Devolve: `retomados`; `ja_retomados` (o gratuito já foi usado — só apagar e
    registrar de novo força, 1 crédito); `nao_registrados`; `parados` (parou
    mas não conseguiu retomar por falha de rede — ficam Stopped e a rodada
    seguinte os pega pelo `estado_numeros`)."""
    nums = sorted({(n or "").strip() for n in numbers if (n or "").strip()})
    res: dict[str, list[str]] = {
        "retomados": [],
        "ja_retomados": [],
        "nao_registrados": [],
        "parados": [],
    }
    if not nums:
        return res
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _REGISTER_BATCH):
            chunk = nums[i : i + _REGISTER_BATCH]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            try:
                body = await _chamar(
                    c, "stoptrack", [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
                )
            except Track17Error as e:
                logger.warning("logistica_17track_stoptrack_falhou", n=len(chunk), err=str(e))
                continue  # nada parou: o lote fica como estava
            parados, rec = _aceitos_e_recusados(body)
            res["nao_registrados"] += [n for n, code in rec.items() if code == ERRO_NAO_REGISTRADO]
            # Já estava parado (rodada anterior interrompida): retoma junto.
            parados += [n for n, code in rec.items() if code == ERRO_JA_PARADO]
            if not parados:
                continue
            await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            try:
                body = await _chamar(
                    c, "retrack", [{"number": n, "carrier": CORREIOS_CARRIER} for n in parados]
                )
            except Track17Error as e:
                logger.warning("logistica_17track_retrack_falhou", n=len(parados), err=str(e))
                res["parados"] += parados
                continue
            retomados, rec = _aceitos_e_recusados(body)
            res["retomados"] += retomados
            res["ja_retomados"] += [n for n, code in rec.items() if code == ERRO_RETRACK_SO_UMA_VEZ]
            outros = [
                n for n in parados if n not in retomados and rec.get(n) != ERRO_RETRACK_SO_UMA_VEZ
            ]
            if outros:
                logger.warning(
                    "logistica_17track_retrack_recusado", numeros=outros[:10], n=len(outros)
                )
                res["parados"] += outros
    logger.info("logistica_17track_parar_e_retomar", **{k: len(v) for k, v in res.items()})
    return res


async def retomar_parados(numbers: list[str]) -> dict[str, list[str]]:
    """`retrack` direto pra número que JÁ está parado no 17track (rodada
    anterior interrompida, ou parado pelo próprio 17track após 30 dias sem
    evento). Mesmo retorno de `parar_e_retomar`."""
    nums = sorted({(n or "").strip() for n in numbers if (n or "").strip()})
    res: dict[str, list[str]] = {
        "retomados": [],
        "ja_retomados": [],
        "nao_registrados": [],
        "parados": [],
    }
    if not nums:
        return res
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _REGISTER_BATCH):
            chunk = nums[i : i + _REGISTER_BATCH]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            try:
                body = await _chamar(
                    c, "retrack", [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
                )
            except Track17Error as e:
                logger.warning("logistica_17track_retrack_falhou", n=len(chunk), err=str(e))
                res["parados"] += chunk
                continue
            retomados, rec = _aceitos_e_recusados(body)
            res["retomados"] += retomados
            res["ja_retomados"] += [n for n, code in rec.items() if code == ERRO_RETRACK_SO_UMA_VEZ]
            res["nao_registrados"] += [n for n, code in rec.items() if code == ERRO_NAO_REGISTRADO]
            res["parados"] += [
                n
                for n in chunk
                if n not in retomados
                and rec.get(n) not in (ERRO_RETRACK_SO_UMA_VEZ, ERRO_NAO_REGISTRADO)
            ]
    return res


async def reregistrar(numbers: list[str]) -> dict[str, Any]:
    """Força a consulta pra número que JÁ usou o retomar gratuito: `deletetrack`
    + `register` (1 crédito por número), LOTE A LOTE — apagar tudo antes de
    registrar deixaria números sem rastreio se o saldo acabasse no meio.
    Testado em 08/09 (AP440389505BR): a consulta aos Correios veio em 30s, e
    o número volta com `is_retracked=False` (o retomar gratuito vale de novo).

    Devolve `ok` (registrados de novo), `apagados_sem_registro` (o 17track
    apagou e o register falhou — quem chama PRECISA zerar `rastreio_17track`
    pra o sync de 15 min registrar de novo) e `sem_quota`."""
    nums = sorted({(n or "").strip() for n in numbers if (n or "").strip()})
    res: dict[str, Any] = {"ok": [], "apagados_sem_registro": [], "sem_quota": False}
    if not nums:
        return res
    async with httpx.AsyncClient(timeout=40.0) as c:
        for i in range(0, len(nums), _REGISTER_BATCH):
            chunk = nums[i : i + _REGISTER_BATCH]
            if i:
                await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            try:
                body = await _chamar(
                    c, "deletetrack", [{"number": n, "carrier": CORREIOS_CARRIER} for n in chunk]
                )
            except Track17Error as e:
                logger.warning("logistica_17track_deletetrack_falhou", n=len(chunk), err=str(e))
                continue  # nada apagado: lote fica como estava
            apagados, _rec = _aceitos_e_recusados(body)
            if not apagados:
                continue
            await asyncio.sleep(_PAUSA_ENTRE_LOTES)
            try:
                reg = await register(apagados)
            except Exception as e:  # noqa: BLE001 — apagou e não registrou: avisar quem chama
                logger.warning(
                    "logistica_17track_reregistrar_register_falhou",
                    n=len(apagados),
                    err=str(e)[:200],
                )
                res["apagados_sem_registro"] += apagados
                break
            ok = set(reg.get("ok") or [])
            res["ok"] += [n for n in apagados if n in ok]
            res["apagados_sem_registro"] += [n for n in apagados if n not in ok]
            if reg.get("sem_quota"):
                res["sem_quota"] = True
                break  # não apaga mais nada sem saldo pra registrar de volta
    logger.info(
        "logistica_17track_reregistrar",
        ok=len(res["ok"]),
        apagados_sem_registro=len(res["apagados_sem_registro"]),
        sem_quota=res["sem_quota"],
    )
    return res


async def quota_restante() -> int | None:
    """Créditos que sobram na conta (`getquota`); None se não deu pra saber."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            body = await _chamar(c, "getquota", {})
        return int((body.get("data") or {}).get("quota_remain"))
    except (Track17Error, TypeError, ValueError) as e:
        logger.warning("logistica_17track_getquota_falhou", err=str(e)[:160])
        return None


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


def entregue_no_push(track_info: dict | None) -> bool:
    """O push do 17track diz ENTREGUE? Só o estado final de entrega conta —
    "Expired" e os outros encerramentos do `encerrado()` não são entrega.

    Usado pra carimbar a chegada do pacote de DEVOLUÇÃO (Eduardo, 10/09):
    onde o retorno vai pelos Correios (100% do TikTok, parte da Shopee), o
    evento físico é a prova mais direta de que o pacote chegou no vendedor."""
    ti = track_info or {}
    status = str(
        (ti.get("latest_status") or {}).get("status") or ti.get("status") or ""
    ).strip()
    return status == "Delivered"


def parse_push_entregues(payload: dict) -> set[str]:
    """Números do push cujo estado é ENTREGUE (mesmo desempacotamento do
    `parse_push`, sem mexer no contrato dele)."""
    data = payload.get("data")
    if isinstance(data, dict):
        acc = data.get("accepted")
        items = acc if isinstance(acc, list) else [data]
    elif isinstance(data, list):
        items = data
    else:
        items = []
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        number = (it.get("number") or "").strip()
        if number and entregue_no_push(it.get("track_info") or {}):
            out.add(number)
    return out


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
