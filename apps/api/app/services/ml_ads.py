"""Mercado Livre Product Ads API client — Marketing module's live ML data.

Subclasses `MercadoLivreClient` to reuse its OAuth refresh + 401/429 retry
logic. Tokens without the advertising scope hit 403 on the first ad call —
we raise `MLAdsScopeError` so the orchestrator can mark the account as
"sem permissão" without crashing the rest of the marketing pull.

Endpoints (doc oficial, 06/07/2026:
https://developers.mercadolivre.com.br/pt_br/product-ads-para-catalogo-e-user-products-leitura):

  • anunciante  GET /advertising/advertisers?product_id=PADS   (Api-Version 1)
  • métricas    GET /advertising/{SITE}/advertisers/{ADV}/product_ads/campaigns/search
                    ?date_from&date_to&metrics=...[&aggregation_type=DAILY]
                                                               (api-version 2)
                (traz também nome/status/budget das campanhas; o caminho
                 /marketplace/..., não documentado, só completa o que faltar)

Resposta paginada só vale COMPLETA (todas as páginas até o `paging.total`):
página repetida, página vazia antes do total ou total que muda no meio =
MLAdsError 'paginacao_incompleta' — nunca dado parcial gravado como o dia.

O endpoint que o DaVinci usava para as métricas
(GET /advertising/advertisers/{ADV}/product_ads/campaigns) foi DESLIGADO pelo
ML em 27/05/2026 e responde 404. O código antigo engolia esse 404 e gravava
gasto 0 como se tivesse dado certo (07/10/2026: zerado desde ~15/07). Regra
daqui em diante: erro de API NUNCA vira número zero — sobe como `MLAdsError`
e o orquestrador (services/marketing/ml_sync.py) grava o estado de erro.

Limites do ML: métricas só até 90 dias para trás; atualizadas às 10:00 BRT
(o dia corrente costuma voltar zerado e amadurece nas próximas rodadas);
um `aggregation_type` por chamada. Valores em moeda local (BRL no MLB).

ML does NOT expose a credit balance like Shopee (Product Ads é pós-pago, cai
na fatura). O orquestrador guarda em `credit_balance` o "orçamento diário
restante" — a tela só mostra crédito para a Shopee.
"""
from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog

from app.services.marketplaces.ml import MercadoLivreClient

logger = structlog.get_logger()

ML_SITE_ID_BR = "MLB"
_BRT = ZoneInfo("America/Sao_Paulo")

# Soft rate limit: ML allows up to ~10 req/s but the marketing sync only
# needs a handful of calls per shop so a 0.2s breath after each request
# is plenty and stays well under any per-app cap.
_RATE_LIMIT_SECONDS = 0.2

# O ML só devolve métricas até 90 dias para trás (doc oficial).
ML_ADS_MAX_DIAS = 90

# Métricas pedidas ao campaigns/search (vocabulário exato do ML).
#   cost          → gasto (R$, "investimento": soma do custo dos cliques)
#   prints        → impressões
#   clicks        → cliques
#   total_amount  → vendas atribuídas aos anúncios (diretas + indiretas, janela
#                   de atribuição de 14 dias)
#   units_quantity→ unidades vendidas pelos anúncios
ML_ADS_METRICAS = (
    "cost,prints,clicks,direct_amount,indirect_amount,total_amount,units_quantity"
)

# Página do campaigns/search (o padrão do ML é 50) e teto de páginas por
# chamada lógica — passar disso é resposta estranha, não "mais dados".
_LIMITE_PAGINA = 50
_MAX_PAGINAS = 60

# O DAILY é pedido em pedaços de até 30 dias: o backfill de 90 dias numa
# chamada só pode passar de 50 páginas se o ML devolver uma linha por
# campanha e dia (contas com ~30 campanhas).
_DIAS_POR_CHAMADA = 30

# Mensagem do 404 de "conta sem Publicidade" (doc oficial, busca do anunciante).
_MSG_SEM_PERMISSAO = "no permissions found"


class MLAdsError(RuntimeError):
    """Wraps an ML Ads HTTP error with `status` + `code` so the
    orchestrator can branch on permission/scope vs transient."""

    def __init__(self, status: int, code: str, message: str, path: str):
        super().__init__(f"{status} {code}: {message} (path={path})")
        self.status = status
        self.code = code
        self.message = message
        self.path = path


class MLAdsScopeError(MLAdsError):
    """Raised when ML returns 403 on an /advertising/* endpoint —
    indicates the integration's OAuth token lacks the advertising
    scope. Caller marks the account as "sem permissão"."""


class MLAdsSemPermissaoError(MLAdsError):
    """A conta não tem Product Ads liberado: o ML responde 404 "No
    permissions found for user_id" na busca do anunciante, ou devolve a
    lista de anunciantes vazia. É ação do dono (Meu perfil › Publicidade),
    não falha passageira — o orquestrador grava 'sem_permissao'."""


@dataclass(slots=True)
class MLCampaign:
    campaign_id: str
    name: str
    status: str  # "active" | "paused" | "ended"
    daily_budget: float | None
    spend: float
    impressions: int
    clicks: int
    acos: float | None  # gasto ÷ vendas atribuídas pelo ML × 100
    revenue: float = 0.0  # total_amount (vendas atribuídas aos anúncios)


@dataclass(slots=True)
class MLDailyMetric:
    """Um dia de Product Ads da conta inteira (soma das campanhas)."""

    day: date
    spend: float
    impressions: int
    clicks: int
    revenue: float  # total_amount: vendas atribuídas aos anúncios
    acos: float | None = None
    units: int = 0
    direct_revenue: float = 0.0
    indirect_revenue: float = 0.0


def hoje_brt(agora: datetime | None = None) -> date:
    """Dia corrente no fuso do ML Brasil (as datas das métricas são BRT)."""
    return (agora or datetime.now(_BRT)).astimezone(_BRT).date()


def validar_janela(date_from: date, date_to: date, *, hoje: date | None = None) -> None:
    """Recusa ANTES de chamar a API o que o ML recusaria: início depois do
    fim, ou início além dos 90 dias para trás."""
    if date_from > date_to:
        raise ValueError(f"janela invertida: {date_from} > {date_to}")
    limite = (hoje or hoje_brt()) - timedelta(days=ML_ADS_MAX_DIAS - 1)
    if date_from < limite:
        raise ValueError(
            f"o ML só devolve métricas dos últimos {ML_ADS_MAX_DIAS} dias "
            f"(início mínimo {limite}, pedido {date_from})"
        )


# Sem estas três não há número de Ads para gravar: ausente ou `null` é erro
# (`cost: null` virava gasto 0 gravado por cima do dia guardado).
_METRICAS_OBRIGATORIAS = ("cost", "prints", "clicks")


def _num(m: dict, key: str, path: str = "") -> float:
    """Métrica secundária (vendas/unidades): ausente ou `null` = 0. Valor que
    não é número = resposta fora do formato → erro (nunca vira 0 calado)."""
    v = m.get(key)
    if v is None:
        return 0.0
    if isinstance(v, bool):
        raise MLAdsError(502, "resposta_inesperada", f"'{key}' não é número: {v!r}", path)
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise MLAdsError(
            502, "resposta_inesperada", f"'{key}' não é número: {v!r}"[:200], path
        ) from e


def _faltando(m: dict) -> list[str]:
    return [k for k in _METRICAS_OBRIGATORIAS if m.get(k) is None]


def _metricas_da_linha(row: dict, path: str, *, tolerar_nulo: bool = False) -> dict | None:
    """As métricas vêm soltas na linha (DAILY) ou dentro de `metrics`
    (por campanha). Sem `cost`/`prints`/`clicks` (ou com `null`) não dá para
    confiar no resto: o ML ignorou o `metrics=` (foi assim que o gasto zerou
    calado) — então é erro. `tolerar_nulo=True` (linha do dia de hoje, que o
    ML ainda não fechou) devolve None em vez de erro: o dia fica sem dado."""
    m = row.get("metrics") if isinstance(row.get("metrics"), dict) else row
    faltando = _faltando(m)
    if faltando:
        if tolerar_nulo:
            return None
        raise MLAdsError(
            502, "metricas_ausentes",
            f"o ML respondeu sem {', '.join(repr(k) for k in faltando)} (ou com null) "
            "— métricas não vieram",
            path,
        )
    for k in _METRICAS_OBRIGATORIAS:
        _num(m, k, path)  # valor que não é número → erro
    return m


def parse_metricas_diarias(
    rows: list[dict], *, path: str = "", hoje: date | None = None
) -> dict[date, MLDailyMetric]:
    """Converte as linhas do campaigns/search com aggregation_type=DAILY em
    um MLDailyMetric por dia. Linhas do MESMO dia são somadas (vale tanto se o
    ML devolver uma linha por dia quanto uma por campanha e dia).

    Linha sem `date` ou sem `cost`/`prints`/`clicks` (ou com `null`) = resposta
    fora do formato → MLAdsError (nunca vira zero). Exceção: o dia `hoje` (e
    depois), que o ML ainda não fechou — se alguma linha dele vier sem
    número, o dia inteiro sai da resposta (o chamador mantém o que tinha)."""
    out: dict[date, MLDailyMetric] = {}
    abertos: set[date] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise MLAdsError(502, "resposta_inesperada", f"linha não é objeto: {row!r}"[:200], path)
        raw_day = row.get("date")
        if raw_day is None and isinstance(row.get("metrics"), dict):
            raw_day = row["metrics"].get("date")
        if not raw_day:
            raise MLAdsError(502, "resposta_inesperada", "linha diária sem 'date'", path)
        try:
            day = date.fromisoformat(str(raw_day)[:10])
        except ValueError as e:
            raise MLAdsError(502, "resposta_inesperada", f"data inválida: {raw_day!r}", path) from e
        m = _metricas_da_linha(row, path, tolerar_nulo=hoje is not None and day >= hoje)
        if m is None:
            abertos.add(day)
            continue
        cur = out.get(day)
        if cur is None:
            cur = MLDailyMetric(day=day, spend=0.0, impressions=0, clicks=0, revenue=0.0)
            out[day] = cur
        cur.spend += _num(m, "cost", path)
        cur.impressions += int(_num(m, "prints", path))
        cur.clicks += int(_num(m, "clicks", path))
        cur.revenue += _num(m, "total_amount", path)
        cur.direct_revenue += _num(m, "direct_amount", path)
        cur.indirect_revenue += _num(m, "indirect_amount", path)
        cur.units += int(_num(m, "units_quantity", path))
    for day in abertos:
        # Soma parcial do dia aberto não é número de verdade: fica sem dado.
        out.pop(day, None)
    for d in out.values():
        d.spend = round(d.spend, 2)
        d.revenue = round(d.revenue, 2)
        d.direct_revenue = round(d.direct_revenue, 2)
        d.indirect_revenue = round(d.indirect_revenue, 2)
        d.acos = round(d.spend / d.revenue * 100, 2) if d.revenue > 0 else None
    return out


def parse_metricas_campanhas(rows: list[dict], *, path: str = "") -> dict[str, dict]:
    """Linhas do campaigns/search (sem aggregation_type) → métricas da janela
    por campaign_id. Campanha sem `metrics.cost` = erro (ver acima)."""
    out: dict[str, dict] = {}
    for c in rows:
        if not isinstance(c, dict):
            raise MLAdsError(502, "resposta_inesperada", f"linha não é objeto: {c!r}"[:200], path)
        cid = str(c.get("id") or c.get("campaign_id") or "")
        if not cid:
            continue
        m = _metricas_da_linha(c, path)
        assert m is not None  # sem tolerar_nulo: falta de métrica já levantou
        out[cid] = {
            "spend": round(_num(m, "cost", path), 2),
            "impressions": int(_num(m, "prints", path)),
            "clicks": int(_num(m, "clicks", path)),
            "revenue": round(_num(m, "total_amount", path), 2),
            "status": c.get("status"),
            "name": c.get("name"),
            "budget": (
                c.get("daily_budget") if c.get("daily_budget") is not None else c.get("budget")
            ),
        }
    return out


def dias_sem_dado(
    ads: dict[date, MLDailyMetric], inicio: date, fim: date, *, hoje: date
) -> list[date]:
    """Dias JÁ FECHADOS da janela (antes de `hoje`, que o ML só fecha no dia
    seguinte) que a resposta completa do ML não trouxe. Não é erro (o ML pode
    omitir dia sem anúncio rodando), mas o chamador avisa: nesses dias fica o
    que já estava gravado."""
    out: list[date] = []
    day = inicio
    while day <= fim and day < hoje:
        if day not in ads:
            out.append(day)
        day += timedelta(days=1)
    return out


def orcamento_diario_restante(campaigns: list[MLCampaign], gasto_hoje: float) -> float:
    """ML has no credit pot. `credit_balance` guarda o orçamento diário das
    campanhas ativas menos o gasto de hoje (a tela não mostra para o ML)."""
    total_budget = sum((c.daily_budget or 0) for c in campaigns if c.status == "active")
    return max(round(total_budget - (gasto_hoje or 0.0), 2), 0.0)


class MLAdsClient(MercadoLivreClient):
    """Adds /advertising/* + /marketplace/advertising/* endpoints to the
    existing ML client. Caches `advertiser_id` on the instance so
    subsequent calls in the same sync don't re-discover it.

    `permitir_refresh=False` (simulação/dry-run): o client NÃO renova o token
    — renovar sem gravar o novo refresh_token queimaria a conexão (o refresh
    token do ML é de uso único). Token vencido vira MLAdsError
    'token_expirado'."""

    def __init__(self, creds: dict, on_token_refresh=None, *, permitir_refresh: bool = True):
        super().__init__(creds, on_token_refresh=on_token_refresh)
        self._advertiser_id: str | None = None
        self._site_id: str = ML_SITE_ID_BR
        self._permitir_refresh = permitir_refresh

    async def refresh(self) -> None:
        if not self._permitir_refresh:
            raise MLAdsError(
                401, "token_expirado",
                "token vencido e a renovação está desligada nesta execução (simulação)",
                "/oauth/token",
            )
        await super().refresh()

    async def _ads_request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        api_version: str = "1",
    ) -> dict:
        """Chamada às APIs de Ads com o cabeçalho `Api-Version` certo para
        cada endpoint (o anunciante e os endpoints antigos usam 1; as
        métricas documentadas usam 2). Qualquer status ≥ 400 vira exceção:
        403 → MLAdsScopeError; o resto → MLAdsError com o código do ML."""
        merged_params = dict(params or {})
        if self._expired():
            await self.refresh()
        from app.services.marketplaces.ml import ML_API_BASE  # avoid cycle

        url = f"{ML_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Api-Version": api_version,
        }
        delay = 1.0
        last_resp: httpx.Response | None = None
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.request(method, url, headers=headers, params=merged_params, json=json)
            last_resp = r
            if r.status_code == 401 and attempt == 0:
                await self.refresh()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            if r.status_code in (429, 502, 503, 504):
                logger.warning("ml_ads_retry", attempt=attempt + 1, status=r.status_code, path=path)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            break
        await asyncio.sleep(_RATE_LIMIT_SECONDS)
        r = last_resp  # type: ignore[assignment]
        if r.status_code == 403:
            raise MLAdsScopeError(
                403, "scope_missing",
                "token lacks 'advertising' scope — reconnect ML",
                path,
            )
        if r.status_code >= 400:
            body = r.text[:300]
            try:
                jb = r.json()
                code = str(jb.get("error") or jb.get("status") or "http_error")
                msg = str(jb.get("message") or body)
            except Exception:  # noqa: BLE001
                code = "http_error"
                msg = body
            raise MLAdsError(r.status_code, code, msg, path)
        try:
            return r.json() or {}
        except ValueError as e:
            raise MLAdsError(r.status_code, "resposta_nao_json", r.text[:200], path) from e

    # ─── advertiser discovery ─────────────────────────────────────────────

    async def get_advertiser_id(self) -> str:
        """First call in any ML Ads flow — resolves the advertiser_id the
        rest of the endpoints need (Api-Version 1, "sem alterações" no fluxo
        novo). 404 com a mensagem "No permissions found" (a da doc) ou lista
        vazia = conta sem Product Ads liberado → MLAdsSemPermissaoError.
        Qualquer OUTRO 404 (ex.: o ML desligou/mudou este endereço, como fez
        com o das métricas) é erro de coleta, não "sem permissão" — senão
        todas as contas ficariam amarelas sem alarme e os números parariam."""
        if self._advertiser_id is not None:
            return self._advertiser_id
        path = "/advertising/advertisers"
        try:
            data = await self._ads_request("GET", path, params={"product_id": "PADS"})
        except MLAdsScopeError:
            raise
        except MLAdsError as e:
            if e.status == 404 and _MSG_SEM_PERMISSAO in (e.message or "").lower():
                raise MLAdsSemPermissaoError(404, "sem_permissao", e.message, path) from e
            raise
        advertisers = data.get("advertisers") or []
        # Mais de um anunciante = contas de outros países no mesmo usuário;
        # o DaVinci só lê o do Brasil.
        br = [a for a in advertisers if str(a.get("site_id") or ML_SITE_ID_BR) == ML_SITE_ID_BR]
        if not br:
            raise MLAdsSemPermissaoError(
                404, "sem_anunciante",
                "esta conta do ML não tem anunciante de Product Ads (Publicidade não ativada)",
                path,
            )
        self._advertiser_id = str(br[0].get("advertiser_id") or br[0].get("id") or "")
        if not self._advertiser_id:
            raise MLAdsError(
                502, "bad_advertiser_response",
                f"advertiser shape unexpected: {br[0]}",
                path,
            )
        return self._advertiser_id

    # ─── campaigns (metadata) ─────────────────────────────────────────────

    async def list_campaigns_metadata(
        self, *, limit: int = 50
    ) -> list[dict]:
        """List campaign metadata (id/name/status/budget) — NO metrics.
        Mantido no caminho que o consumidor de comandos já usa
        (/marketplace/advertising/..., Api-Version 1): segue funcionando em
        07/10/2026 e o ajuste de orçamento depende dele."""
        advertiser_id = await self.get_advertiser_id()
        path = (
            f"/marketplace/advertising/{ML_SITE_ID_BR}/advertisers/"
            f"{advertiser_id}/product_ads/campaigns/search"
        )
        out: list[dict] = []
        offset = 0
        while True:
            data = await self._ads_request(
                "GET", path, params={"limit": limit, "offset": offset},
            )
            results = data.get("results") or data.get("campaigns") or []
            if not results:
                break
            out.extend(results)
            if len(results) < limit:
                break
            offset += limit
            if offset > 1000:
                logger.warning("ml_ads_pagination_cap", path=path, offset=offset)
                break
        return out

    # ─── métricas (endpoint documentado, api-version 2) ───────────────────

    def _caminho_campanhas(self, advertiser_id: str) -> str:
        return (
            f"/advertising/{self._site_id}/advertisers/{advertiser_id}"
            "/product_ads/campaigns/search"
        )

    async def _buscar_todas(self, path: str, params: dict) -> list[dict]:
        """Percorre as páginas do campaigns/search e só devolve a resposta
        COMPLETA. Resposta parcial nunca vira dado: ela seria gravada como o
        número do dia (por cima do que estava certo) com a coleta dizendo "ok".

        Com `paging.total` (o normal no ML): segue o offset pelo tamanho da
        página que veio (o ML pode mandar menos que o `limit` pedido) até ter
        `total` linhas. Erro `paginacao_incompleta` se: uma página repete outra
        (offset ignorado), vem página vazia antes do total, ou o total muda no
        meio da leitura. Sem `paging.total`: página menor que o limite = última.
        Resposta sem `results` = erro."""
        out: list[dict] = []
        vistas: set[str] = set()
        offset = 0
        total_esperado: int | None = None
        for _pagina in range(_MAX_PAGINAS):
            data = await self._ads_request(
                "GET", path,
                params={**params, "limit": _LIMITE_PAGINA, "offset": offset},
                api_version="2",
            )
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise MLAdsError(
                    502, "resposta_inesperada",
                    f"resposta sem 'results': {str(data)[:200]}",
                    path,
                )
            results = data["results"]
            paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
            total = paging.get("total")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                total = None
            if total is not None:
                if total_esperado is None:
                    total_esperado = total
                elif total != total_esperado:
                    raise MLAdsError(
                        502, "paginacao_incompleta",
                        f"o total do ML mudou no meio da leitura ({total_esperado} → {total}); "
                        f"lidas {len(out)} linhas",
                        path,
                    )
            if results:
                assinatura = _json.dumps(results, sort_keys=True, default=str)
                if assinatura in vistas:
                    raise MLAdsError(
                        502, "paginacao_incompleta",
                        f"o ML repetiu uma página (offset {offset} ignorado); lidas {len(out)} "
                        f"de {total_esperado if total_esperado is not None else '?'} linhas",
                        path,
                    )
                vistas.add(assinatura)
            out.extend(results)
            cheia = len(results) == _LIMITE_PAGINA
            if total_esperado is not None:
                if len(out) == total_esperado:
                    break
                if len(out) > total_esperado:
                    # O total não é a contagem de linhas: só a página cheia diz
                    # se ainda há mais (repetição continua sendo pega acima).
                    if not cheia:
                        break
                elif not results:
                    raise MLAdsError(
                        502, "paginacao_incompleta",
                        f"o ML parou de mandar linhas antes do total: lidas {len(out)} "
                        f"de {total_esperado}",
                        path,
                    )
            elif len(results) != _LIMITE_PAGINA:
                # Sem total: menor que o limite = última; maior = o ML ignorou
                # a paginação e mandou tudo.
                break
            offset += len(results)
        else:
            raise MLAdsError(
                502, "paginacao_excedida",
                f"mais de {_MAX_PAGINAS} páginas — resposta fora do esperado",
                path,
            )
        return out

    async def fetch_daily_metrics(
        self, *, date_from: date, date_to: date, hoje: date | None = None
    ) -> dict[date, MLDailyMetric]:
        """Gasto/impressões/cliques/vendas por anúncio POR DIA da conta
        (todas as campanhas): campaigns/search com aggregation_type=DAILY,
        em pedaços de até 30 dias. Dia ausente na resposta = o ML não tem
        dado para ele (o chamador decide e avisa — ver `dias_sem_dado`; aqui
        não se inventa zero). Dia fora da janela pedida = resposta fora do
        formato → erro. Qualquer erro → exceção (nada parcial volta)."""
        hoje = hoje or hoje_brt()
        validar_janela(date_from, date_to, hoje=hoje)
        advertiser_id = await self.get_advertiser_id()
        path = self._caminho_campanhas(advertiser_id)
        out: dict[date, MLDailyMetric] = {}
        ini = date_from
        while ini <= date_to:
            fim = min(ini + timedelta(days=_DIAS_POR_CHAMADA - 1), date_to)
            rows = await self._buscar_todas(
                path,
                {
                    "date_from": ini.isoformat(),
                    "date_to": fim.isoformat(),
                    "metrics": ML_ADS_METRICAS,
                    "aggregation_type": "DAILY",
                },
            )
            for day, m in parse_metricas_diarias(rows, path=path, hoje=hoje).items():
                if not ini <= day <= fim:
                    raise MLAdsError(
                        502, "resposta_inesperada",
                        f"o ML devolveu o dia {day} fora da janela pedida ({ini} a {fim})",
                        path,
                    )
                out[day] = m
            ini = fim + timedelta(days=1)
        return out

    async def fetch_campaign_metrics(
        self, *, date_from: date, date_to: date, hoje: date | None = None
    ) -> dict[str, dict]:
        """Métricas da janela por campanha (gasto, impressões, cliques,
        vendas atribuídas) — campaigns/search com metrics, sem agregação
        diária. Erro → exceção (antes devolvia {} e o gasto virava 0)."""
        validar_janela(date_from, date_to, hoje=hoje)
        advertiser_id = await self.get_advertiser_id()
        path = self._caminho_campanhas(advertiser_id)
        rows = await self._buscar_todas(
            path,
            {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "metrics": ML_ADS_METRICAS,
            },
        )
        return parse_metricas_campanhas(rows, path=path)

    async def list_campaigns_with_metrics(
        self, *, date_from: date, date_to: date, hoje: date | None = None
    ) -> list[MLCampaign]:
        """Campanhas da janela SÓ pelo endpoint documentado (campaigns/search,
        api-version 2), que já traz nome, status e orçamento (`budget`) junto
        das métricas. Só entra campanha que o ML devolveu COM métricas — a que
        não veio não aparece aqui e o número guardado dela fica como estava
        (nada de campanha com gasto 0 inventado). Se as métricas falham, a
        exceção sobe.

        O caminho antigo (/marketplace/..., não documentado — "só os endpoints
        publicados têm suporte") vira só complemento: é chamado apenas se faltar
        nome/status/orçamento em alguma campanha, e falha nele não derruba a
        coleta."""
        metrics_by_id = await self.fetch_campaign_metrics(
            date_from=date_from, date_to=date_to, hoje=hoje
        )
        incompletas = [
            cid for cid, m in metrics_by_id.items()
            if not m.get("name") or not m.get("status") or m.get("budget") is None
        ]
        meta_by_id: dict[str, dict] = {}
        if incompletas:
            try:
                meta = await self.list_campaigns_metadata()
            except (MLAdsError, httpx.HTTPError) as e:
                logger.warning(
                    "ml_ads_metadados_indisponiveis", err=str(e)[:300],
                    campanhas_incompletas=len(incompletas),
                )
            else:
                meta_by_id = {
                    str(c.get("id") or c.get("campaign_id") or ""): c
                    for c in meta if isinstance(c, dict)
                }
        out: list[MLCampaign] = []
        for cid, m in metrics_by_id.items():
            extra = meta_by_id.get(cid) or {}
            budget = m.get("budget")
            if budget is None:
                budget = extra.get("daily_budget")
                if budget is None:
                    budget = extra.get("budget")
            out.append(_campanha(cid, {
                "name": m.get("name") or extra.get("name"),
                "status": m.get("status") or extra.get("status"),
                "daily_budget": budget,
            }, m))
        return out

    # ─── edit campaign (agent actions) ────────────────────────────────────

    async def edit_campaign(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
        daily_budget: float | None = None,
    ) -> dict:
        """Update campaign status ('active' | 'paused') and/or daily
        budget. Uses PUT on the campaign resource per ML Product Ads docs."""
        advertiser_id = await self.get_advertiser_id()
        body: dict = {}
        if status is not None:
            body["status"] = status
        if daily_budget is not None:
            body["daily_budget"] = float(daily_budget)
        if not body:
            raise ValueError("edit_campaign requires status or daily_budget")
        return await self._ads_request(
            "PUT",
            f"/advertising/advertisers/{advertiser_id}/product_ads/campaigns/{campaign_id}",
            json=body,
        )


def _campanha(cid: str, meta: dict, m: dict) -> MLCampaign:
    spend = float(m.get("spend") or 0.0)
    revenue = float(m.get("revenue") or 0.0)
    budget = meta.get("daily_budget")
    if budget is None:
        budget = meta.get("budget")
    try:
        daily_budget = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        daily_budget = None
    return MLCampaign(
        campaign_id=cid,
        name=str(meta.get("name") or ""),
        status=str(meta.get("status") or "unknown").lower(),
        daily_budget=daily_budget,
        spend=spend,
        impressions=int(m.get("impressions") or 0),
        clicks=int(m.get("clicks") or 0),
        acos=round(spend / revenue * 100, 2) if revenue > 0 else None,
        revenue=revenue,
    )
