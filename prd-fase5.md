# PRD — Fase 5: Webhook Bling + Cron Scheduler

**Versão:** 1.0
**Data:** 2026-05-06
**Owner:** spectrum77@tuta.com
**Status:** Draft para execução
**Dependências:** Fase 4a (sync orchestrator + sync_logs particionada), Fase 4b.ML/Shopee/Amazon (clientes outbound), Fase 1.6 (stores + bling_store_id).
**Estimativa:** 1-1.5 dia.

> **Nota terminologia:** prd.md §11 Fase 5 menciona "APScheduler" mas o stack já adotou **Arq** (`apps/api/app/worker.py` usa `arq.cron`). Esta fase usa **Arq cron**, mantendo coerência com Fases 1/4. APScheduler fica fora do escopo.

---

## 1. Objetivos

1. **Push em tempo real do Bling** — webhook valida assinatura HMAC e enfileira `sync_product` para os marketplaces ligados ao SKU mudado. Resolve gap "estoque defasado entre cron diário".
2. **Crons persistidos** — registrar cron jobs Arq que sobrevivem restart do worker (Redis AOF já habilitado em `docker-compose.yml`).
3. **Manutenção de partições** — criar mensalmente partição `sync_logs_yYYYYmMM` do mês N+1 (sucessor da pré-criação inicial em `0005_sync_logs.py`).
4. **Endpoint de configuração** — `GET /api/settings/webhook-url` para o usuário copiar no painel Bling sem adivinhar host.

## 2. Fora de escopo

- `alerts_cleanup` e `low_stock_polling` registram a função cron mas o **corpo** fica como no-op até Fase 6 (alerts). Razão: tabela `alerts` ainda não existe; criar `if not table_exists: return` evita bind tardio mas mantém schedule ativo.
- `auto_import_link` espera `listings` (Fase 8) — mesmo padrão: cron registrada com guarda.
- Fluxo Telegram (Fase 7).
- UI de "Settings" — Fase 7. Fase 5 entrega só o GET de webhook-url.

## 3. Webhook Bling

### 3.1 Endpoint

`POST /api/webhooks/bling` — pública, sem `require_active_user`.

**Headers esperados** (Bling v3 envia `X-Bling-Signature` SHA-256 HMAC do body com `BLING_WEBHOOK_SECRET`; confirmar nome exato lendo um payload real durante a impl):

```
X-Bling-Signature: sha256=<hex>
X-Bling-Event:     produto.alterado | produto.estoque.alterado | produto.criado
X-Bling-Delivery:  <uuid>     # idempotency anchor
Content-Type:      application/json
```

**Body típico** (Bling v3 — `produto.estoque.alterado`):

```json
{
  "evento": "produto.estoque.alterado",
  "dados": {
    "id": 12345,
    "codigo": "SKU-ABC-01",
    "estoque": {"saldoVirtualTotal": 7, "saldoFisicoTotal": 7},
    "loja": {"id": 99}
  },
  "company_id": "...",   // tenant Bling, ignorado (single-owner)
  "occurred_at": "2026-05-06T13:00:00-03:00"
}
```

### 3.2 Validação

1. Ler `X-Bling-Signature`. Se ausente → 401 `{code:"missing_signature"}`.
2. `expected = "sha256=" + hmac.new(BLING_WEBHOOK_SECRET, body_bytes, sha256).hexdigest()`.
3. `hmac.compare_digest(expected, header)` → 401 `{code:"bad_signature"}` se diferir.
4. Decodificar JSON; eventos não conhecidos respondem **200 ack** (não 400 — Bling repete em erro 4xx/5xx, e queremos absorver eventos novos sem travar).

### 3.3 Idempotência

- Chave: `X-Bling-Delivery` quando presente; fallback `sha256(body)`.
- `SET NX EX 86400` em Redis (`webhook:bling:dedupe:{key}`) — se já existe, responde 200 sem enfileirar.

### 3.4 Resolução do produto

- Localiza por `dados.codigo` (SKU): `SELECT id FROM products WHERE sku = :codigo LIMIT 1`. Se não achar:
  - Se `dados.id` (Bling produto.id) bater com algum `product_links` plataforma=Bling → resolve.
  - Senão grava `sync_logs(action='webhook_unmatched', payload=body, status='skipped')` e responde 200. Não falhar — Bling pode emitir eventos de produtos não importados.
- Atualiza `products.bling_stock` direto no commit do request (não espera o worker) — refresh leve, single UPDATE, evita janela onde queries lêem estoque velho.

### 3.5 Enfileiramento

- Para cada `product_link` ativo (`status IN ('active','requires_review')`) do produto, enfileira `sync_product_run(job_id, user_id, product_id, link_ids=[...])` via `arq_pool.enqueue_job(...)`.
- Cria 1 `background_jobs(type=SYNC_PRODUCT, status=pending, payload={trigger:'webhook_bling', delivery_id})`. Frontend pode acompanhar normalmente.
- Resposta `200 {ack: true, job_id: <uuid>}` em <500ms p95.

### 3.6 Variáveis de ambiente

Já existe `BLING_WEBHOOK_SECRET` em §13 prd.md — reaproveitar.

### 3.7 Job worker novo: `sync_product_run`

Ainda não existe (worker tem `sync_all_run`). Adicionar em `app/worker.py`:

```python
async def sync_product_run(ctx, job_id, user_id, product_id, link_ids=None):
    uid = UUID(user_id); pid = UUID(product_id); jid = UUID(job_id)
    async with session_scope() as s:
        # NÃO usar advisory_lock por user — webhooks chegam concorrentes.
        # Usar pg_advisory_xact_lock(hashtext('sync_product:'||product_id)) granular.
        ...
        product = await s.get(Product, pid)
        job = await s.get(BackgroundJob, jid)
        orch = SyncOrchestrator(s, user_id=uid, job=job)
        await orch.run([product], only_link_ids=link_ids)
```

`SyncOrchestrator.run` recebe novo kwarg opcional `only_link_ids: list[UUID] | None`.

## 4. Cron jobs

### 4.1 Tabela de schedules

| Cron | Schedule (Arq) | Status nesta fase | Observação |
|------|---------------|-------------------|------------|
| `daily_sync_scheduler` | `cron(minute={0,5,10,...,55})` | **ativo** | Lê `user_settings.daily_sync_time` em America/Sao_Paulo; enfileira `sync_all_run` se ainda não rodou hoje |
| `bling_token_refresh` | `cron(minute={0,30})` | **ativo** | Refresh de tokens Bling expirando em < 1h |
| `shopee_token_refresh` | `cron(hour={0,4,8,12,16,20})` | **ativo** | Idem Shopee (B5 indireto — token expira → 403 disfarçado) |
| `shopee_discrepancy_check` | `cron(hour={1,5,9,13,17,21})` | **ativo** | Compara estoque Shopee vs `product_links.stock`, marca divergência |
| `background_jobs_gc` | `cron(hour=6, minute=30)` (= 03:30 BRT) | **ativo** | Marca jobs running sem heartbeat > 5min como `failed` |
| `sync_logs_partition_gc` | `cron(day=15, hour=3)` | **ativo** **(novo, não estava em §8.1)** | Cria partição `sync_logs_yYYYYmMM` do próximo mês; idempotente |
| `auth_codes_cleanup` | já existente (`cron(hour=6, minute=15)`) | mantém | Sem mudança |
| `alerts_cleanup` | `cron(hour=6, minute=0)` (= 03:00 BRT) | **registrada, no-op** | Guarda `if not table_exists('alerts'): return` — Fase 6 remove guarda |
| `low_stock_polling` | `cron(minute={0,2,4,...})` | **registrada, no-op** | Mesma guarda — Fase 6 |
| `auto_import_link` | `cron(minute={0,30})` | **registrada, no-op** | Guarda em `listings` — Fase 8 |

> **Horário BRT:** Arq usa UTC. Brasília = UTC-3 (sem DST). Para 03:00 BRT → `hour=6` UTC. Cravar comentário em cada `cron(...)` indicando intenção BRT para evitar regressão.

### 4.2 Cron skeletons

```python
# apps/api/app/worker.py — adicionar funções

async def daily_sync_scheduler(ctx):
    """A cada 5min: enfileira sync_all para users cujo daily_sync_time bate
    no slot atual em America/Sao_Paulo e que ainda não rodaram hoje."""
    now_sp = datetime.now(ZoneInfo("America/Sao_Paulo"))
    slot = now_sp.replace(second=0, microsecond=0)
    # janela de 5min: aceita daily_sync_time entre [slot-5min, slot]
    async with session_scope() as s:
        rows = await s.execute(
            select(UserSettings).where(
                UserSettings.daily_sync_enabled.is_(True),
                UserSettings.daily_sync_time.between(
                    (slot - timedelta(minutes=5)).time(), slot.time()
                ),
            )
        )
        for us in rows.scalars():
            already = await s.execute(
                select(BackgroundJob.id).where(
                    BackgroundJob.created_by == us.user_id,
                    BackgroundJob.type == BackgroundJobType.SYNC_ALL,
                    BackgroundJob.created_at >= slot.astimezone(UTC).replace(hour=0, minute=0),
                )
            )
            if already.first():
                continue
            job = BackgroundJob(type=BackgroundJobType.SYNC_ALL, status=BackgroundJobStatus.PENDING,
                                created_by=us.user_id, payload={"trigger":"daily_sync"})
            s.add(job); await s.flush()
            pool = await get_arq_pool()
            arq = await pool.enqueue_job("sync_all_run", str(job.id), str(us.user_id), None)
            if arq: job.arq_job_id = arq.job_id

async def bling_token_refresh(ctx):
    """Renova tokens Bling expirando em < 1h."""
    cutoff = int(time.time()) + 3600
    async with session_scope() as s:
        ints = (await s.execute(
            select(Integration).where(
                Integration.platform == IntegrationPlatform.BLING,
                Integration.token_expires_at.is_not(None),
                Integration.token_expires_at <= datetime.fromtimestamp(cutoff, UTC),
            )
        )).scalars().all()
        for it in ints:
            try:
                client = await build_bling_client(s, it)
                await client.refresh()
            except Exception as e:
                logger.warning("bling_refresh_failed", integration_id=str(it.id), err=str(e))

async def shopee_token_refresh(ctx):
    """Mesmo padrão; Shopee tokens duram 4h, refresh proativo."""
    ...

async def shopee_discrepancy_check(ctx):
    """Para cada link Shopee ativo, GET stock real e compara.
    Diff > 0 → grava sync_log(action='shopee_discrepancy', status='requires_review')."""
    ...

async def background_jobs_gc(ctx):
    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    async with session_scope() as s:
        result = await s.execute(
            update(BackgroundJob)
            .where(BackgroundJob.status == BackgroundJobStatus.RUNNING,
                   or_(BackgroundJob.last_heartbeat_at.is_(None),
                       BackgroundJob.last_heartbeat_at < cutoff))
            .values(status=BackgroundJobStatus.FAILED, error="orphan_no_heartbeat",
                    finished_at=datetime.now(UTC))
        )
        logger.info("bg_jobs_gc", marked_failed=result.rowcount)

async def sync_logs_partition_gc(ctx):
    """Cria partição do próximo mês. Idempotente via IF NOT EXISTS."""
    today = datetime.now(UTC)
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    name = f"sync_logs_y{nxt.year:04d}m{nxt.month:02d}"
    end = (nxt + timedelta(days=32)).replace(day=1)
    async with session_scope() as s:
        await s.execute(text(
            f'CREATE TABLE IF NOT EXISTS davinci.{name} '
            f'PARTITION OF davinci.sync_logs '
            f"FOR VALUES FROM ('{nxt.date()}') TO ('{end.date()}')"
        ))
    logger.info("sync_logs_partition_ensured", name=name)

# stubs registrados, no-op até fases seguintes
async def alerts_cleanup(ctx): logger.debug("alerts_cleanup_noop"); return
async def low_stock_polling(ctx): logger.debug("low_stock_noop"); return
async def auto_import_link(ctx): logger.debug("auto_import_noop"); return
```

### 4.3 Registro em `WorkerSettings`

```python
class WorkerSettings:
    ...
    functions = [
        send_otp_email, auth_codes_cleanup, auto_link_run, sync_all_run,
        ml_backfill_run,
        sync_product_run,            # novo
    ]
    cron_jobs = [
        cron(auth_codes_cleanup, hour=6, minute=15),
        cron(daily_sync_scheduler, minute={0,5,10,15,20,25,30,35,40,45,50,55}),
        cron(bling_token_refresh, minute={0,30}),
        cron(shopee_token_refresh, hour={0,4,8,12,16,20}, minute=0),
        cron(shopee_discrepancy_check, hour={1,5,9,13,17,21}, minute=0),
        cron(background_jobs_gc, hour=6, minute=30),       # 03:30 BRT
        cron(sync_logs_partition_gc, day=15, hour=3),       # 00:00 BRT dia 15
        cron(alerts_cleanup, hour=6, minute=0),             # no-op até Fase 6
        cron(low_stock_polling, minute={0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30,32,34,36,38,40,42,44,46,48,50,52,54,56,58}),
        cron(auto_import_link, minute={0,30}),
    ]
```

## 5. Endpoint `GET /api/settings/webhook-url`

### 5.1 Contrato

```
GET /api/settings/webhook-url
→ 200 { "url": "https://api.hadken.com/api/webhooks/bling",
        "secret_hint": "abcd…wxyz",   # primeiros 4 + últimos 4 do BLING_WEBHOOK_SECRET (verificação visual)
        "events": ["produto.alterado","produto.estoque.alterado","produto.criado"] }
```

- Guard: `require_permission("sincronizacoes", "view")`.
- `url = settings.api_url + "/api/webhooks/bling"`.
- Não retornar o secret completo — só hint para o usuário conferir que copiou no Bling o secret certo.

### 5.2 Router

Criar `apps/api/app/routers/settings.py`:

```python
router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("/webhook-url")
async def webhook_url(user = Depends(require_permission("sincronizacoes","view"))):
    s = get_settings()
    secret = s.bling_webhook_secret or ""
    hint = f"{secret[:4]}…{secret[-4:]}" if len(secret) >= 8 else "(não configurado)"
    return {"url": f"{s.api_url}/api/webhooks/bling",
            "secret_hint": hint,
            "events": ["produto.alterado","produto.estoque.alterado","produto.criado"]}
```

Incluir em `main.py`. Fase 7 expande o router com `GET /api/settings` + `PATCH /api/settings`.

## 6. Migrations

### 6.1 `0007_user_settings.py` (mínima — só campos do daily_sync)

Cria `user_settings` agora (Fase 7 expande com low_stock_threshold etc.):

```sql
CREATE TABLE davinci.user_settings (
  user_id              UUID PRIMARY KEY REFERENCES davinci.users(id) ON DELETE CASCADE,
  daily_sync_enabled   BOOLEAN     NOT NULL DEFAULT FALSE,
  daily_sync_time      TIME        NULL,        -- America/Sao_Paulo, HH:MM
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_user_settings_daily ON davinci.user_settings (daily_sync_enabled, daily_sync_time)
  WHERE daily_sync_enabled = TRUE;
```

> Justificativa do "scope creep": daily_sync_scheduler precisa da tabela; criar 2 colunas mínimas agora evita registrar cron sem destino.

### 6.2 `0008_background_jobs_heartbeat.py` (se ainda não existe)

Verificar `models.background_job`. Se faltar `last_heartbeat_at TIMESTAMPTZ NULL`, adicionar — `background_jobs_gc` depende.

### 6.3 Sem migration para webhook

Webhook não tem tabela própria; idempotência mora no Redis.

## 7. Modelos / schemas

### 7.1 Novo modelo `UserSettings`

`apps/api/app/models/user_settings.py` (espelho da migration). `__init__.py` exporta.

### 7.2 Schemas Pydantic

Não precisa expor `UserSettings` em response na Fase 5 (Fase 7 cuida). Apenas o `WebhookUrlOut`:

```python
class WebhookUrlOut(BaseModel):
    url: str
    secret_hint: str
    events: list[str]
```

## 8. Testes

`apps/api/tests/test_webhook_bling.py`:

- `test_signature_invalid_returns_401`
- `test_signature_valid_enqueues_sync_product`
- `test_unknown_sku_returns_200_logs_skip`
- `test_duplicate_delivery_id_dedupes_in_redis` (mock redis NX)
- `test_handler_under_500ms_for_known_sku` (smoke — não-bloqueante de fato)

`apps/api/tests/test_cron_jobs.py`:

- `test_sync_logs_partition_gc_creates_next_month_idempotent`
- `test_background_jobs_gc_marks_orphan_running_as_failed`
- `test_daily_sync_skips_user_already_ran_today`
- `test_bling_token_refresh_calls_refresh_only_for_expiring`

`apps/api/tests/test_settings_webhook_url.py`:

- `test_webhook_url_returns_url_and_hint`
- `test_webhook_url_requires_sincronizacoes_view`

## 9. Bugs do PRD endereçados

| Bug | Como Fase 5 resolve |
|-----|---------------------|
| **B8** (jobs in-memory perdem progresso) | `background_jobs_gc` recupera órfãos; Arq cron sobrevive a restart porque vive no Redis com AOF |
| **B11** (timezone Brasília fora do servidor) | `ZoneInfo("America/Sao_Paulo")` no `daily_sync_scheduler`; comentários explícitos UTC↔BRT em cada cron |
| **B12** (Bling Cloudflare 403 HTML) | `bling_token_refresh` evita o refresh tardio que dispara 401→retry→Cloudflare; rate limiter (Fase futura) é defesa em profundidade |

## 10. Critérios de aceite

1. Configuro webhook no painel Bling (URL vinda de `GET /api/settings/webhook-url`); ao mudar estoque de 1 produto, em < 5s o `sync_logs` mostra `action='update_stock'` para cada link daquele produto, e os marketplaces refletem.
2. Assinatura inválida → 401 e Bling não recebe ack (Bling tentará novamente — comportamento esperado).
3. Reentrega do mesmo `X-Bling-Delivery` em janela de 24h não enfileira segundo job (Redis dedup hit).
4. SKU desconhecido → 200, log `webhook_unmatched`, sem enfileirar.
5. Reinicio o worker no meio de um `sync_all`; após `background_jobs_gc` rodar, o job órfão fica `failed` e usuário pode refazer.
6. No 1º dia do mês posterior ao deploy: partição `sync_logs_yYYYYmMM` do mês N+2 já existe (cron rodou no dia 15 do mês anterior).
7. `daily_sync_time = 06:00` para user X → entre 06:00-06:05 BRT, `sync_all_run` enfileirado uma única vez naquele dia.
8. Token Bling com `expires_at = now+30min` é renovado automaticamente ao próximo tick `:00/:30` sem o usuário pedir.
9. Crons `alerts_cleanup`, `low_stock_polling`, `auto_import_link` rodam sem erro (no-op) — comprova que registro está correto e desbloqueia Fases 6/8.

## 11. Plano de execução (checklist)

### Backend

- [ ] Migration `0007_user_settings.py` (table + index)
- [ ] Migration `0008_*` se faltar `last_heartbeat_at` em `background_jobs`
- [ ] Modelo `UserSettings` + export em `models/__init__.py`
- [ ] Endpoint `POST /api/webhooks/bling` (`apps/api/app/routers/webhooks.py`):
  - [ ] HMAC validation com `hmac.compare_digest`
  - [ ] Redis dedup `webhook:bling:dedupe:{key}` SET NX EX 86400
  - [ ] Resolução produto por SKU/Bling id
  - [ ] UPDATE inline de `products.bling_stock`
  - [ ] Enfileira `sync_product_run`
  - [ ] Cria `background_jobs(type=SYNC_PRODUCT, payload.trigger='webhook_bling')`
- [ ] Função worker `sync_product_run` (`apps/api/app/worker.py`)
- [ ] `SyncOrchestrator.run(only_link_ids=...)` (filter de links)
- [ ] Crons em `worker.py`: `daily_sync_scheduler`, `bling_token_refresh`, `shopee_token_refresh`, `shopee_discrepancy_check`, `background_jobs_gc`, `sync_logs_partition_gc`, stubs no-op de `alerts_cleanup` / `low_stock_polling` / `auto_import_link`
- [ ] Registro em `WorkerSettings.cron_jobs` e `functions`
- [ ] Router `apps/api/app/routers/settings.py` com `GET /api/settings/webhook-url`
- [ ] Include em `main.py`
- [ ] Schema `WebhookUrlOut`
- [ ] Helper `build_bling_client(session, integration)` que cifra/decifra credenciais e passa `on_token_refresh` que persiste em `integrations.credentials` + `token_expires_at`

### Testes

- [ ] `tests/test_webhook_bling.py` (5 casos §8)
- [ ] `tests/test_cron_jobs.py` (4 casos §8)
- [ ] `tests/test_settings_webhook_url.py` (2 casos §8)

### Documentação

- [ ] Atualizar prd.md §11 Fase 5 trocando "APScheduler" por "Arq cron"
- [ ] Adicionar `sync_logs_partition_gc` à tabela §8.1 prd.md

## 12. Riscos

| Risco | Mitigação |
|-------|-----------|
| Bling muda nome do header de assinatura | Ler header de payload real antes de mergear; teste com payload gravado |
| `daily_sync_scheduler` enfileira duplicado em race entre 2 workers | Já protegido por advisory lock dentro de `sync_all_run`; segunda chamada vira `failed/sync_already_running` |
| Cron registrada com função no-op confunde futuro dev | Comentário `# Fase 6 — wired`/`# Fase 8 — wired` em cada stub |
| Webhook recebe burst (1000/s em sync massivo no Bling) | Redis dedup absorve repetição; enfileiramento Arq tem `max_jobs=10` por worker — pode ser necessário `--workers N` em prod (fora desta fase) |
| Tempo BRT vs UTC errado | Comentário UTC=BRT+3 em cada `cron(hour=...)`; teste manual no deploy |
