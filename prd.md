# PRD — DaVinci (Nuxt 3 + FastAPI + PostgreSQL 18)

**Versão:** 1.1
**Data:** 2026-05-05
**Owner:** spectrum77@tuta.com
**Status:** Draft para execução
**Nome da aplicação:** DaVinci (substitui "Stock Sync Hub" legado)

---

## 1. Visão geral

### 1.1 Contexto

Stock Sync Hub é uma aplicação que orquestra sincronização de estoque entre o ERP **Bling** (fonte da verdade) e 6 marketplaces (Mercado Livre, Shopee, Amazon, TikTok, Temu, Aliexpress), com:

- Precificação multinível por departamento/produto/conta (com push de preço)
- Sincronização automática (cron diário + webhook em tempo real do Bling)
- Auditoria via planilha Excel
- Suporte a múltiplos anúncios por produto (variations / contas múltiplas)
- Notificações via Telegram + alertas in-app
- 7 background jobs

Stack atual: **React 19 + Vite + tRPC + Drizzle + MySQL + Express**, monolito.

### 1.2 Por que migrar

A versão atual está **cheia de bugs** (sync ML zerando estoque, links sem `lastSyncAt`, OAuth instável, jobs in-memory perdem estado em restart, SKU mapping frágil). A migração é a oportunidade de:

1. Trocar stack para **Nuxt 3 + FastAPI + PostgreSQL 18** self-hosted (schema `davinci` já desenhado em `migration.sql` / `sql_completo.sql` — referência para a baseline Alembic).
2. Resolver dívidas técnicas: jobs persistidos, retry/backoff consistente, observabilidade.
3. Arquitetura limpa que aceita novos marketplaces sem cirurgia geral.

### 1.3 Objetivos não-funcionais

| Métrica | Meta |
|---------|------|
| Sync de 1 produto em 1 marketplace | < 3s p95 |
| Sync completo (500 produtos × 5 marketplaces) | < 15min |
| Push de preço | < 2s p95 |
| Webhook Bling end-to-end | < 1s reconhecimento, sync async |
| Disponibilidade jobs background | 99.5% (jobs persistidos sobrevivem restart) |
| Tipagem ponta a ponta | OpenAPI gerado pelo FastAPI consumido no Nuxt via tipo |

### 1.4 Fora de escopo desta migração

- Reimplementar TikTok/Temu integrações (estão parciais hoje; mantemos stubs).
- Reescrever AI Chat / ManusDialog / Map (componentes legados não-essenciais).
- Multi-tenancy real (mantemos modelo single-owner via `OWNER_OPEN_ID`).

---

## 2. Stack alvo

### 2.1 Frontend — Nuxt 3

- **Package manager:** [`pnpm`](https://pnpm.io/) (workspaces no monorepo, `pnpm-lock.yaml` versionado, store global no Mac/CI)
- **Nuxt 3** (Vue 3 + Vite + Nitro), TypeScript estrito
- **UI:** [shadcn-vue](https://www.shadcn-vue.com/) (port direto do shadcn-react usado hoje, mesma DX Radix-style) + **Tailwind CSS 4**
- **Estado/Data:** [`@tanstack/vue-query`](https://tanstack.com/query/latest/docs/framework/vue/overview) + Pinia para estado global pequeno (auth, toasts)
- **HTTP:** `$fetch` do Nuxt + cliente OpenAPI gerado a partir do FastAPI ([`openapi-typescript`](https://openapi-ts.dev/) + wrapper)
- **Forms:** [VeeValidate](https://vee-validate.logaretm.com/v4/) + [Zod](https://zod.dev/) (mesmo schema usado no ML)
- **Routing:** file-based do Nuxt (`pages/`)
- **i18n:** `@nuxtjs/i18n` (PT-BR padrão, ganho gratuito para futuro)
- **Toast:** [`vue-sonner`](https://github.com/xiaoluoboding/vue-sonner)

### 2.2 Backend — Python / FastAPI

- **Package manager / runner:** [`uv`](https://docs.astral.sh/uv/) (gerencia venv, lock determinístico `uv.lock`, instala 10x+ mais rápido que pip; usado tanto local quanto no Dockerfile via `uv sync --frozen`)
- **Python 3.12** + **FastAPI 0.115+**
- **ORM:** [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) (async) + [Alembic](https://alembic.sqlalchemy.org/) para migrations
- **Validação:** **Pydantic v2** (schemas request/response)
- **Banco:** PostgreSQL 18 self-hosted via docker compose (schema `davinci`)
- **Cache / fila:** Redis 7 self-hosted via docker compose (usos: queue de jobs, rate limit por integração, cache de tokens OAuth, cache de respostas pesadas como `getCatalogListings`)
- **Auth:** JWT em cookie HttpOnly, SameSite=Lax (downgrade do `none` atual, melhor segurança; OAuth callbacks usam state token assinado)
- **HTTP client externo:** [`httpx`](https://www.python-httpx.org/) async com retry via [`tenacity`](https://tenacity.readthedocs.io/)
- **Background jobs:** [**Arq**](https://arq-docs.helpmanual.io/) (Redis-based, async-first, com cron nativo, retries e dead-letter queue). Worker em processo separado (`apps/worker/`). Tabela `background_jobs` no Postgres mantém o **registro auditável** dos jobs (status, progresso, payload, resultado) que o frontend lê via polling — Arq cuida da execução, Postgres cuida do estado durável visível para o usuário.
- **Logs:** [`structlog`](https://www.structlog.org/) JSON
- **Settings:** [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) lendo `.env`

### 2.3 Infra — Hetzner VPS + Docker Compose unificado

VPS única na **Hetzner Cloud** (recomendado: CCX13/CCX23 dedicado, Ubuntu 24.04 LTS). **Sem Coolify** — `docker compose` puro com **Traefik standalone** como reverse proxy + TLS automático Let's Encrypt. Mesmo `docker-compose.yml` roda local (Mac) e prod (Hetzner) com overrides via `docker-compose.override.yml` (dev) e `docker-compose.prod.yml` (prod).

| Serviço (compose) | Imagem / Build | Porta | Volume local (host) | Notas |
|-------------------|----------------|-------|---------------------|-------|
| `traefik` | `traefik:v3.1` | 80, 443 | `./data/traefik/letsencrypt` | Só em prod; dev usa portas diretas |
| `postgres` | `postgres:18-alpine` | 5432 (interno; expõe 5432 só em dev) | `./data/postgres` | `POSTGRES_DB=davinci`, schema `davinci` criado pela primeira migration |
| `redis` | `redis:7-alpine` com `--appendonly yes` | 6379 (interno; expõe em dev) | `./data/redis` | AOF persistido |
| `api` | Build `apps/api/Dockerfile` (uv + uvicorn) | 8000 | — | Labels Traefik: `api.davinci.<dominio>` |
| `worker` | Mesmo build do `api`, `command: arq app.worker.WorkerSettings` | — | — | Sem porta exposta |
| `web` | Build `apps/web/Dockerfile` (Nuxt SSR Node) | 3000 | — | Labels Traefik: `app.davinci.<dominio>` |

**Storage de uploads (audit):** **volume local** montado em `./data/uploads/` (mapeado para `/data/uploads` dentro do `api`). Cliente Python usa `aiofiles` + path local; abstração `Storage` mantida para troca futura por S3 sem mudar callsites. Sem MinIO.

**Rede:** rede `davinci_net` interna; só `traefik` exposto em 80/443 públicas. `postgres`, `redis` ficam acessíveis apenas pelos containers `api` e `worker`. Em **dev local** o compose expõe `5432` e `6379` no host para conectar via DBeaver/psql.

**Domínios prod (sugestão, ajustar):**
- `app.hadken.com` → `web`
- `api.hadken.com` → `api`

**Localhost (Mac):**
- `pnpm dev` no `apps/web/` (HMR Nitro/Vite) apontando para `API_URL=http://localhost:8000`
- `uv run uvicorn app.main:app --reload` no `apps/api/` (ou `docker compose up api worker` se quiser tudo em containers)
- `docker compose up -d postgres redis` sempre (resto pode rodar host nativo durante dev)

**Deploy do Mac → Hetzner via SSH:**
- Chave `~/.ssh/id_ed25519` do Mac autorizada em `/root/.ssh/authorized_keys` da VPS (provisionada via `hetzner-cli` ou painel na criação do servidor)
- `~/.ssh/config` no Mac com host `davinci` apontando IP da VPS, `IdentityFile ~/.ssh/id_ed25519`
- Script `scripts/deploy.sh` (executado do Mac) faz: `git push` → `ssh davinci 'cd /opt/davinci && git pull && docker compose pull && docker compose up -d --build'`
- Migrations: `ssh davinci 'docker compose exec api alembic upgrade head'`

**Backups:**
- Cron na VPS (root): `pg_dump` diário 03:00 BRT → `/opt/backups/davinci-$(date +%F).sql.gz`, retenção 14 dias
- Snapshots Hetzner semanais (painel) cobrem volumes (`./data/postgres`, `./data/redis`, `./data/uploads`)
- Restauração testada na Fase 13

---

## 3. Arquitetura

```
                 ┌────────── Hetzner VPS (docker compose) ──────────┐
                 │                                                  │
┌────────────┐   │  ┌──────────┐    ┌──────────────────┐            │
│ Nuxt 3     │◀──┼──│ traefik  │◀──▶│ web (SSR)        │            │
│ (browser)  │   │  │ TLS LE   │    │ Nitro server     │            │
└─────┬──────┘   │  └────┬─────┘    └────────┬─────────┘            │
      │ HTTPS    │       │                   │ $fetch (rede compose)│
      │ cookie   │       ▼                   ▼                      │
      └──────────┼─▶ ┌──────────────────────────────┐               │
                 │   │ api (FastAPI, uv+uvicorn)    │               │
                 │   │  routers/services/schemas/   │               │
                 │   └──┬──────────┬────────────────┘               │
                 │      │ asyncpg  │ redis-py async                 │
                 │      ▼          ▼                                │
                 │  ┌────────┐ ┌────────┐                           │
                 │  │postgres│ │ redis  │◀── arq worker (mesmo      │
                 │  │  18    │ │   7    │    image, CMD diferente)  │
                 │  └────┬───┘ └────────┘                           │
                 │       │      ┌────────────────────┐              │
                 │       └─────▶│ ./data/uploads     │ (volume host │
                 │              │ (audit XLSX local) │  bind-mount) │
                 │              └────────────────────┘              │
                 └──────────────────────────────────────────────────┘
                       ▲
                       │ ssh davinci (id_ed25519 do Mac)
                       │ git pull + docker compose up -d --build
                  [Mac local: dev com pnpm/uv, deploy via script]
```

Princípios:

- **REST tipado** (não tRPC). OpenAPI é a fonte de verdade do contrato.
- **Services puros**: cada marketplace = um módulo isolado, interface comum (`MarketplaceClient` ABC).
- **Idempotência**: toda operação de push/sync aceita `idempotency_key` (header) para evitar duplicação em retry.
- **Job model único**: `background_jobs` table (tipo, status, progresso, payload, resultado) consultada pelo frontend via polling ou SSE.

---

## 4. Modelo de dados (PostgreSQL — schema `davinci`)

Migração 1:1 do MySQL atual para Postgres, com normalizações pendentes.

### 4.1 Tabelas a criar (Alembic migrations sequenciais)

> **Nota:** já existe migration parcial em `/Users/juninhoomar/Downloads/migration.sql` (enum `department` com `catalogo`, enum `pricing_platform` com `magalu`, colunas `blingCostPrice`, `observation2/3`). Essas alterações vão para a primeira revision Alembic como baseline.

| # | Tabela | Origem (Drizzle) | Mudanças vs. atual |
|---|--------|------------------|--------------------|
| 1 | `users` | `users` | `openId` → `open_id` (formato `email:<email>`, gerado no primeiro login OTP); campos snake_case; `email` UNIQUE NOT NULL; **manter** `role` enum em `('admin','user')` (admin: bypass total + único que vê página de usuários; user: só recursos liberados pelo admin); **adicionar** `status` enum `('pending','active','suspended')` DEFAULT `'pending'`; **adicionar** `tuta` text, `upseller` text, `bling_login` text, `adspower` text (logins externos do colaborador, texto livre); **adicionar** `permissions` jsonb NOT NULL DEFAULT `'{}'::jsonb` (ver §5.5); índice GIN em `permissions`; índice em `email` |
| 2 | `integrations` | `integrations` | `credentials` permanece JSON criptografado (AES-GCM via `cryptography`); adicionar `token_expires_at` separado; **adicionar** `store_id` FK→stores NULL UNIQUE (toda integração nasce de uma loja; UNIQUE garante 1:1 store↔integration); na criação via OAuth, vincula automaticamente à `store` selecionada |
| 3 | `products` | `products` | snake_case; adicionar índice composto `(user_id, sku)` UNIQUE; remover colunas `*_id`/`*_stock` por marketplace (passa tudo para `product_links`) — **breaking change**, ver §11 |
| 4 | `product_links` | `product_links` | passa a ser a única fonte de estoque por marketplace; índice `(user_id, platform, integration_id, external_id, variation_id)` UNIQUE; campo `last_sync_status` enum; **adicionar** `store_id` FK→stores (NULL ok no início, populado quando empresa/loja conhecida — facilita relatórios "estoque por empresa") |
| 5 | `sync_queue` | `syncQueue` | renomear para `sync_queue_items`; status enum |
| 6 | `sync_logs` | `syncLogs` | particionada por mês (Postgres declarative partitioning) para performance |
| 7 | `alerts` | `alerts` | adicionar índice `(user_id, read_at NULLS FIRST, created_at DESC)`; job de cleanup > 60 dias |
| 8 | `user_settings` | `userSettings` | mantém estrutura |
| 9 | `listings` | `listings` | índice `(integration_id, external_id) UNIQUE` |
| 10 | `listing_requests` | `listingRequests` | mantém |
| 11 | `pricing_accounts` | `pricing_accounts` | já tem `observation2/3` da migration.sql |
| 12 | `pricing_products` | `pricing_products` | já tem `bling_cost_price` (camelCase no JSON) |
| 13 | `pricing_overrides` | `pricing_overrides` | mantém |
| 14 | `store_info` | `storeInfo` | mantém |
| 15 | `background_jobs` | **NOVA** | id, type, status, total, processed, payload jsonb, result jsonb, error text, created_by, started_at, finished_at |
| 16 | `oauth_states` | **NOVA** | state token assinado para callbacks OAuth (substitui `state` em memória) |
| 17 | `audit_dismissed_skus` | derivada da audit | sku, user_id, dismissed_at |
| 18 | `companies` | **NOVA** (aba Excel "empresas") | id, razao_social, apelido (a "conta"), responsavel_id FK→users (NULL ok), uf, cnpj UNIQUE, inscricao_estadual, site_url, obs, created/updated |
| 19 | `stores` | **NOVA** (linhas × colunas marketplace do print) | id, company_id FK→companies CASCADE, marketplace enum (`ml`, `shopee`, `amazon`, `aliexpress`, `temu`, `tiktok`, `shein`, `magalu`, `site`), apelido_override TEXT NULL (NULL = usa `companies.apelido`), status enum (`active`, `inactive`, `closing`, `banned`, `pending`, `under_review`), integration_id FK→integrations NULL (vinculo OAuth quando conectada), **`bling_store_id` BIGINT NULL** (ID da loja correspondente no Bling — `loja.id` da API v3 do Bling; usado em `PUT /produtos/{id}/estoque?idLoja=...` e endpoints de preço para que a alteração reflita no canal certo dentro do Bling), notes TEXT (suporta obs como "shopee esta como marquezini"), created/updated. **UNIQUE (company_id, marketplace)**. Índices em `(integration_id)` e `(bling_store_id)` |
| 20 | `cadastros` | **NOVA** (aba Excel "cadastro") | id, tipo enum (`fone`, futuramente `email`, `dominio`, etc.), provedor TEXT (tim, vivo, …), responsavel_id FK→users NULL, codigo TEXT (número/identificador), label TEXT (rótulo amigável), status enum (`active`, `inactive`, `excluded`), obs TEXT, created/updated. Índice em `(tipo, codigo)` |
| 21 | `cadastros_stores` | **NOVA** (N:N entre cadastros e stores) | cadastro_id FK→cadastros CASCADE, store_id FK→stores CASCADE, alias TEXT NULL (suporta override por loja como "fils inativa", "farias"), assigned_at. PK composta `(cadastro_id, store_id)` |
| 22 | `auth_codes` | **NOVA** (Email-OTP, ver §5.1) — substitui a versão simples (`code` em texto plano, sem nonce) que existe no schema atual `sql_completo.sql` | id, email TEXT NOT NULL, code_hash TEXT NOT NULL (bcrypt do código de 8 chars), prefix VARCHAR(4) NOT NULL (mostrado ao usuário antes do envio, anti-phishing), session_nonce TEXT NOT NULL (também salvo em cookie HttpOnly antes do envio), expires_at timestamptz NOT NULL, attempts INT DEFAULT 0, ip INET, user_agent TEXT, consumed_at timestamptz NULL, created_at. Índices: `(email, created_at DESC)`, `(expires_at)` para limpeza |

> **Tabelas adiadas (fora do escopo desta migração):** `margens`, `freight_recon` (conciliação de frete), `devolucoes`, `tarefas`, `reembolso`. Os recursos correspondentes existem no enum de permissões (§5.5) com `view/edit/delete = false` por padrão, prontos para quando as telas forem construídas — não bloqueiam o cutover.

### 4.2 Convenções

- snake_case nos nomes de tabelas/colunas; camelCase só na API (Pydantic com `alias`).
- Todas as FK com `ON DELETE CASCADE` quando filho não faz sentido sem pai (ex.: `product_links` → `products`).
- `created_at`/`updated_at` com `timestamptz` e default `now()`. Trigger genérico para `updated_at`.
- Enums Postgres nativos (`CREATE TYPE`), não check-constraints.

### 4.3 Migration plan (Alembic)

- `0001_baseline_schema.py` — cria tudo do zero (não derivamos do MySQL existente; é migração greenfield).
- `0002_seed_enums.py` — popula enums `department`, `platform`, `pricing_platform`.
- `0003_partitioning_sync_logs.py` — converte `sync_logs` para particionada.
- Toda migration é **forward + downgrade**.

### 4.4 Divergências propositais vs. schema atual (`sql_completo.sql`)

O arquivo `/Users/juninhoomar/Downloads/sql_completo.sql` é o schema **atual** (Postgres já no formato camelCase com aspas). As decisões abaixo aplicam o que muda no novo PRD:

| Item do schema atual | Decisão no novo PRD | Motivo |
|----------------------|---------------------|--------|
| Nomes em camelCase com aspas (`"userId"`, `"createdAt"`) | **snake_case** (`user_id`, `created_at`) com Pydantic `alias_generator=to_camel` na API | snake_case é convenção Postgres; reduz necessidade de aspas; API mantém JSON em camelCase para o front |
| `users.passwordHash` + `password_reset_tokens` | **descartar** (não migrar) | Login é Email-OTP puro (§5.1) — confirmado pelo usuário em 2026-05-05 |
| `users.role enum('user','admin')` | **mantido** (`'admin'`/`'user'`) | Confirmado em 2026-05-05: granularidade fica no `permissions` jsonb; role serve só para gating do `/users` (admin-only) e bypass total |
| `users.status VARCHAR(20)` | enum `user_status('pending','active','suspended')` | Type-safety + bootstrap pending/approval |
| `auth_codes.code` (10 chars, plain text) | `code_hash` bcrypt + `prefix` + `session_nonce` + `ip` + `user_agent` (§5.1) | Anti-phishing (prefixo), brute-force resistance (bcrypt + attempts), proteção cross-device (nonce), forensics (ip/UA) |
| `freight_recon` e `devolucoes` | **Adiadas** — não migrar agora (confirmado em 2026-05-05) | Não fazem parte do escopo desta migração; recursos de permissão ficam reservados em §5.5 para quando forem implementadas |
| Sem `companies`, `stores`, `cadastros`, `cadastros_stores` | Criadas (§4.1 #18-21) | Pedido novo do usuário (gestão de empresas) |
| Sem coluna `bling_store_id` em nada | Adicionada em `stores` (§4.1 #19) | Necessária para o `idLoja` da API Bling |
| `pricing_accounts.password VARCHAR(256)` (texto) | **cifrar** com `CredentialsCipher` AES-GCM | Senhas de marketplace não devem viver em plaintext |
| `store_info.password VARCHAR(256)` (texto) | idem cifrar | mesma razão |
| `listings.price BIGINT` (centavos implícito) | manter `BIGINT` em centavos; Pydantic converte para `Decimal` na API | evita float em dinheiro |
| Faltam UNIQUEs (ex.: `(integration_id, external_id)` em listings, `(user_id, sku)` em products) | Adicionar como definido em §4.1 | resolve B6 (SKU vazio) e duplicações silenciosas |
| Sem particionamento em `sync_logs` | Particionada por mês (§4.1 #6) | Tabela cresce muito; performance de query e cleanup |

---

## 5. Autenticação e segurança

### 5.1 Login do usuário — Email-OTP + JWT

O login dos **usuários** do app (operadores) é por **OTP por e-mail** (não OAuth de marketplace — esse continua existindo, mas só serve para conectar credenciais de Bling/ML/Shopee/Amazon, ver §6 e Fase 2).

**Fluxo (3 endpoints):**

1. `POST /api/auth/request` — body `{ email, turnstile_token? }`
   - Valida formato do e-mail e (se `TURNSTILE_SECRET_KEY` definido) o token Cloudflare Turnstile.
   - Aplica rate limit Redis: chaves `otp:rl:ip:{ip}` e `otp:rl:email:{email}` com janela de 1h, limites `OTP_RATE_PER_IP` e `OTP_RATE_PER_EMAIL` (default 3 cada). Resposta 429 com `Retry-After` se excedido.
   - Gera **código de 8 chars** (alfabeto seguro `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, sem ambíguos) + **prefixo de 4 chars** (mostrado ao usuário antes de enviar).
   - Gera **session_nonce** aleatório (32 bytes base64).
   - `code_hash = bcrypt(code, rounds=12)`.
   - Insere em `auth_codes` com `expires_at = now() + OTP_CODE_TTL_MS` (default 10min), `ip`, `user_agent`.
   - Seta cookie HttpOnly `otp_nonce={session_nonce}` (SameSite=Strict, Secure, max-age = TTL).
   - Enfileira job Arq `send_otp_email` (worker manda e-mail via Mailjet, não bloqueia o request).
   - Resposta: `{ prefix, expires_at }`. Frontend mostra `Verifique se o e-mail começa com o prefixo {prefix}` (anti-phishing — usuário só digita o código se o prefixo bate).

2. `POST /api/auth/verify` — body `{ email, code }` + cookie `otp_nonce`
   - Lê o `auth_codes` mais recente para o e-mail, ainda não consumido, ainda não expirado.
   - Valida `session_nonce` do cookie contra o do registro (impede iniciar OTP num device e consumir em outro).
   - Incrementa `attempts`. Se `attempts > OTP_MAX_ATTEMPTS` (default 5), invalida e devolve 429.
   - `bcrypt.checkpw(code, row.code_hash)`. Se inválido, devolve 401.
   - Marca `consumed_at = now()`.
   - **Upsert no `users`** por `email`: se não existir, cria com `open_id = "email:" + email`, `role = 'user'`, `status = 'pending'`. Se o e-mail bater com `OWNER_OPEN_ID` (formato `email:...`), promove a `role='admin'` e `status='active'` (bootstrap do dono — único caminho para virar admin).
   - Se `status = 'suspended'`, devolve 403.
   - Emite JWT HS256 com `JWT_SECRET` (TTL 7d), payload `{ sub: open_id, role, jti }`.
   - Seta cookie `davinci_session={jwt}` HttpOnly, Secure, SameSite=Lax, `Path=/`.
   - Limpa cookie `otp_nonce`.
   - Resposta: `{ user: {id, email, name, role, status}, requires_approval: status == 'pending' }`.

3. `POST /api/auth/logout`
   - Limpa cookies `davinci_session` e `otp_nonce`.
   - (Opcional, futuro) adiciona `jti` em blocklist Redis com TTL = restante do JWT.

**Dependency `get_current_user`:** lê cookie, valida JWT, busca user pelo `sub` (open_id). `require_user` falha em 401 se ausente/inválido. `require_active_user` falha em 403 se `status != active`.

**`PendingApproval`:** quando `status='pending'`, frontend mostra tela "Aguardando aprovação do administrador" e bloqueia navegação para qualquer página exceto `/logout`. Admin aprova mudando `status='active'` em `PATCH /api/users/{id}` (Fase 1.5).

**Cleanup:** cron Arq `auth_codes_cleanup` diário às 03:15 BRT deleta `auth_codes` com `expires_at < now() - 7 days`.

### 5.2 Envio de e-mail (OTP e futuras notificações)

- Abstração `EmailSender` com 1 implementação:
  - `MailjetSender` via REST `https://api.mailjet.com/v3.1/send` (env: `MAILJET_API_KEY`, `MAILJET_SECRET_KEY`, `EMAIL_FROM`, `EMAIL_FROM_NAME`). Basic auth `MAILJET_API_KEY:MAILJET_SECRET_KEY`. Lógica portada de `export_stocksync/stock_sync_hub/server/_core/email.ts`.
- Template do OTP em Jinja2 (`apps/api/app/email_templates/otp.html`), exibe o **prefixo** com destaque + código + tempo de expiração.
- Remetente: `EMAIL_FROM` (ex.: `DaVinci <no-reply@hadken.com>`).
- Job Arq `send_otp_email(email, prefix, code)` retry com backoff (3 tentativas).

### 5.3 Criptografia de credenciais OAuth (marketplaces)

Independente do login do usuário, as credenciais OAuth dos marketplaces (Bling, Shopee, ML, Amazon) ficam em `integrations.credentials`:

- Serviço `CredentialsCipher` (AES-GCM com chave em `CREDENTIALS_KEY` env, derivada com HKDF).
- Encrypt-on-write, decrypt-on-read. Endpoints nunca expõem credenciais em response.
- `oauth_states` (§4.1) guarda token assinado dos callbacks OAuth de marketplace, expira em 10min.

### 5.4 Headers e CORS

- `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options: DENY`.
- CORS restrito ao domínio do Nuxt em prod (não `*`).

### 5.5 Permissões granulares (RBAC simples)

Apenas dois `role`s, comportamento bem definido:

| Role | O que pode |
|------|-----------|
| `admin` | **Bypass total** — passa por qualquer guard `require_permission(...)`, vê todos os menus, é o **único** que pode acessar `/users` e editar permissões de outros usuários. O bootstrap inicial (cujo email == `OWNER_OPEN_ID`) nasce admin já `active`. |
| `user` | Tem acesso **apenas** ao que o admin liberar caixa-a-caixa na matriz `permissions` (jsonb). Defaults na criação: tudo `false`. Após login, admin abre `/users/{id}` e marca o que esse usuário pode `view`/`edit`/`delete` em cada recurso. |

`permissions` jsonb continua sendo a fonte de verdade efetiva para users do role `user`. Para `admin`, o jsonb é ignorado em runtime (bypass).

A própria página `/users` é gateada por `role == 'admin'` (não pelo recurso `permissoes` do jsonb), garantindo que mesmo que alguém marque acidentalmente `permissoes:view` num user comum, ele continua sem acesso.

Cada `users.permissions` é um **jsonb** com a forma:

```jsonc
{
  "produtos":              {"view": true, "edit": true, "delete": false},
  "anuncios":              {"view": true, "edit": true, "delete": false},
  "tabela_precos":         {"view": true, "edit": false, "delete": false},
  "tabela_precos_contas":  {"view": true, "edit": false, "delete": false},
  "tabela_precos_produtos":{"view": true, "edit": false, "delete": false},
  "conciliacao_frete":     {"view": false, "edit": false, "delete": false},
  "sincronizacoes":        {"view": true, "edit": false, "delete": false},
  "devolucoes":            {"view": false, "edit": false, "delete": false},
  "reembolso":             {"view": false, "edit": false, "delete": false},
  "tarefas":               {"view": false, "edit": false, "delete": false},
  "margem":                {"view": false, "edit": false, "delete": false},
  "empresa":               {"view": false, "edit": false, "delete": false},
  "cadastro":              {"view": false, "edit": false, "delete": false},
  "permissoes":            {"view": false, "edit": false, "delete": false}
}
```

**Recursos** (14): `produtos`, `anuncios`, `tabela_precos`, `tabela_precos_contas`, `tabela_precos_produtos`, `conciliacao_frete`, `sincronizacoes`, `devolucoes`, `reembolso`, `tarefas`, `margem`, `empresa`, `cadastro`, `permissoes`.

**Ações** (3): `view`, `edit`, `delete`.

**Schema Pydantic** (`apps/api/app/schemas/permissions.py`):

```python
RESOURCES = Literal[
  "produtos", "anuncios", "tabela_precos",
  "tabela_precos_contas", "tabela_precos_produtos",
  "conciliacao_frete", "sincronizacoes",
  "devolucoes", "reembolso", "tarefas",
  "margem", "empresa", "cadastro", "permissoes",
]
class ResourcePerm(BaseModel):
    view: bool = False
    edit: bool = False
    delete: bool = False
class Permissions(RootModel[dict[RESOURCES, ResourcePerm]]):
    @field_validator("root")
    def fill_defaults(cls, v):
        return {r: v.get(r, ResourcePerm()) for r in get_args(RESOURCES)}
```

**Regras:**

- `role = "admin"` ignora o jsonb e tem **tudo permitido** (bypass total).
- `edit=true` implica `view=true` automaticamente (validador Pydantic levanta para `view`).
- `delete=true` implica `edit=true` e `view=true`.
- O bootstrap inicial cria o owner (vindo de `OWNER_OPEN_ID`) com `role=admin`.

**Backend — guards:**

```python
def require_permission(resource: str, action: Literal["view","edit","delete"]):
    async def dep(user: User = Depends(require_active_user)) -> User:
        if user.role == "admin": return user
        perm = (user.permissions or {}).get(resource, {})
        if not perm.get(action, False):
            raise HTTPException(403, {"code": "forbidden", "resource": resource, "action": action})
        return user
    return dep

def require_admin(user: User = Depends(require_active_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, {"code": "admin_only"})
    return user
```

Uso:
- `Depends(require_permission("produtos", "edit"))` em endpoints de domínio.
- `Depends(require_admin)` em **toda** a router de `/api/users` (lista, criação, edição cadastral, edição de matriz de permissões, exclusão).

**Frontend — composables:**

```ts
// composables/useCan.ts
export const useCan = (resource: Resource, action: Action) => {
  const { user } = useAuth()
  return computed(() =>
    user.value?.role === 'admin' ||
    user.value?.permissions?.[resource]?.[action] === true
  )
}

// composables/useIsAdmin.ts
export const useIsAdmin = () => {
  const { user } = useAuth()
  return computed(() => user.value?.role === 'admin')
}
```

Uso em template:
- `<Button v-if="useCan('produtos','delete')">Excluir</Button>` para ações dentro de páginas.
- `<NuxtLink v-if="useIsAdmin()" to="/users">Usuários</NuxtLink>` para esconder o item de menu.

Páginas admin-only declaram:
```ts
definePageMeta({ middleware: ['auth', 'admin'] })  // pages/users/*
```
Outras páginas usam `permission` quando a checagem for por recurso:
```ts
definePageMeta({ middleware: ['auth', 'permission'], permission: { resource: 'produtos', action: 'view' } })
```

---

## 6. Backend — endpoints REST

Convenção: `/api/{recurso}` em REST. OpenAPI tags por router.

### 6.1 Mapa tRPC → REST

| tRPC procedure (atual) | REST (novo) |
|------------------------|-------------|
| `integrations.list` | `GET /api/integrations` |
| `integrations.get` | `GET /api/integrations/{id}` |
| `integrations.create` | `POST /api/integrations` |
| `integrations.update` | `PATCH /api/integrations/{id}` |
| `integrations.delete` | `DELETE /api/integrations/{id}` |
| `integrations.testConnection` | `POST /api/integrations/{id}/test` |
| `products.list` | `GET /api/products?search=&integration_id=&page=&page_size=` |
| `products.get` | `GET /api/products/{id}` |
| `products.create/update/delete` | `POST/PATCH/DELETE /api/products[/{id}]` |
| `products.bulkDelete` | `POST /api/products/bulk-delete` |
| `products.deleteLink` | `DELETE /api/product-links/{id}` |
| `products.fetchFromBling` | `GET /api/products/preview/bling` |
| `products.importFromBling` | `POST /api/products/import/bling` |
| `products.importCsv` | `POST /api/products/import/csv` |
| `products.updateNamesFromBling` | `POST /api/products/update-names` |
| `products.getProductLinks` | `GET /api/product-links` |
| `products.startAutoLink` | `POST /api/jobs/auto-link` (retorna `job_id`) |
| `sync.getProgress` | `GET /api/jobs/{job_id}` |
| `sync.syncAll` | `POST /api/jobs/sync-all` |
| `sync.syncProductSelected` | `POST /api/sync/product/{id}` |
| `sync.getLogs` | `GET /api/sync-logs?limit=&platform=&status=` |
| `sync.getStats` | `GET /api/sync-logs/stats` |
| `alerts.*` | `GET/POST /api/alerts/*` |
| `settings.get/update` | `GET/PATCH /api/settings` |
| `settings.webhookUrl` | `GET /api/settings/webhook-url` |
| `listings.*` | `GET/POST/DELETE /api/listings*` |
| `audit.uploadSpreadsheet` | `POST /api/audit/uploads` (multipart) |
| `audit.parseSheet` | `POST /api/audit/parse` |
| `audit.startAudit` | `POST /api/jobs/audit` |
| `audit.fixPrice` | `POST /api/audit/fix-price` |
| `audit.fixPrices` | `POST /api/audit/fix-prices` |
| `discrepancies.list` | `GET /api/discrepancies` |
| `pricing.getAccounts` | `GET /api/pricing/accounts?department=` |
| `pricing.create/update/deleteAccount` | `POST/PATCH/DELETE /api/pricing/accounts[/{id}]` |
| `pricing.autoMatchIntegrations` | `POST /api/pricing/accounts/auto-match` |
| `pricing.getProducts` | `GET /api/pricing/products?department=` |
| `pricing.create/update/deleteProduct` | `POST/PATCH/DELETE /api/pricing/products[/{id}]` |
| `pricing.importProducts` | `POST /api/pricing/products/import` |
| `pricing.toggleCatalog` | `POST /api/pricing/products/{id}/catalog` |
| `pricing.getOverrides` | `GET /api/pricing/overrides` |
| `pricing.setOverride` | `PUT /api/pricing/overrides` |
| `pricing.setCellStatus` | `PUT /api/pricing/overrides/cell-status` |
| `pricing.removeOverride` | `DELETE /api/pricing/overrides` |
| `pricing.pushPrice` | `POST /api/pricing/push` |
| `pricing.getCatalogListings` | `GET /api/pricing/catalog-listings` |
| `pricing.pushCatalogPrice` | `POST /api/pricing/push-catalog` |
| `pricing.sendPushReport` | `POST /api/pricing/push-report` |
| `pricing.testTelegram` | `POST /api/pricing/test-telegram` |
| `pricing.getSkuAudit` | `GET /api/pricing/sku-audit` |
| `pricing.dismiss/undismissAuditSku` | `POST /api/pricing/sku-audit/{sku}/dismiss` (e `/undismiss`) |
| `pricing.fetchActualPrices` | `GET /api/pricing/actual-prices?integration_id=` |
| `pricing.searchCompetitorPrices` | `GET /api/pricing/competitor-prices?q=` |
| `pricing.syncBlingCosts` | `POST /api/jobs/sync-bling-costs` |
| `storeInfo.*` | `GET/POST/PATCH/DELETE /api/store-info[/{id}]` |
| `auth.me` | `GET /api/auth/me` |
| `auth.logout` | `POST /api/auth/logout` |
| **(novo) — Login OTP** | `POST /api/auth/request` (gera OTP, envia e-mail; rate-limited) |
| | `POST /api/auth/verify` (valida nonce + código, emite JWT cookie) |
| | `POST /api/auth/resend` (reenvia código se ainda dentro do TTL; também rate-limited) |
| **(novo)** | `GET /api/users` (lista — `permissoes:view`) |
| **(novo)** | `GET /api/users/{id}` |
| **(novo)** | `POST /api/users` (cria — `cadastro:edit`) |
| **(novo)** | `PATCH /api/users/{id}` (edita campos cadastrais) |
| **(novo)** | `PATCH /api/users/{id}/permissions` (edita matriz — `permissoes:edit`) |
| **(novo)** | `DELETE /api/users/{id}` (`cadastro:delete`) |
| **(novo)** | `GET /api/users/me/permissions` (matriz efetiva do usuário logado, usado pelo frontend para gate de UI) |
| **(novo) — Empresas** | `GET /api/companies` (lista, com `?marketplace=ml` opcional para filtro) |
| | `GET /api/companies/{id}` (com `stores` aninhadas) |
| | `POST /api/companies` |
| | `PATCH /api/companies/{id}` |
| | `DELETE /api/companies/{id}` (cascade em stores e cadastros_stores) |
| | `GET /api/companies/grid` (formato matriz idêntica ao print 1: linhas = empresas, colunas = marketplaces, célula = `{exists, status, label}`) |
| **(novo) — Lojas** | `GET /api/stores?company_id=&marketplace=&status=` |
| | `POST /api/stores` (cria loja vazia em marketplace para uma empresa; status default `pending`) |
| | `PATCH /api/stores/{id}` (mudar status, apelido_override, notes, **bling_store_id**) |
| | `GET /api/integrations/{id}/bling-stores` (lista lojas/canais do Bling via `GET /Api/v3/lojas`; usado para popular o select de `bling_store_id` em cada `store`) |
| | `DELETE /api/stores/{id}` |
| | `POST /api/stores/{id}/link-integration` (vincula integration_id existente, ou abre OAuth) |
| | `POST /api/stores/{id}/unlink-integration` |
| **(novo) — Cadastros** | `GET /api/cadastros?tipo=&store_id=&search=` |
| | `GET /api/cadastros/{id}` (com lojas vinculadas) |
| | `POST /api/cadastros` (cria + opcional `store_ids[]` para vincular) |
| | `PATCH /api/cadastros/{id}` |
| | `DELETE /api/cadastros/{id}` |
| | `PUT /api/cadastros/{id}/stores` (substitui o conjunto de vínculos: `[{store_id, alias?}, ...]`) |
| | `GET /api/cadastros/grid` (formato matriz do print 2: linhas = cadastros, colunas = marketplace, célula = alias da loja vinculada ou vazio) |

### 6.2 Webhooks (continuam REST puro)

- `POST /api/webhooks/bling` — produto criado/atualizado, estoque mudado
- `POST /api/webhooks/telegram` — bot commands
- `GET /api/oauth/{provider}/callback` — Bling, Shopee, Mercado Livre, Amazon
- `GET /api/oauth/{provider}/start?integration_id=` — gera URL e state, redireciona

### 6.3 Convenções de endpoint

- Paginação: `?page=1&page_size=50`, response `{items: [...], total, page, page_size}`.
- Erros: padrão FastAPI HTTPException com `detail: {code, message, ...}`.
- Idempotência: header `Idempotency-Key` em mutations destrutivas/financeiras (push de preço).

---

## 7. Frontend — páginas Nuxt

Convenção: cada página em `app/pages/`, layout default em `app/layouts/default.vue` (sidebar + header igual ao `DashboardLayout` atual).

### 7.1 Mapa de páginas

| Página atual (React) | Página Nuxt | Rota |
|----------------------|-------------|------|
| `Dashboard.tsx` | `pages/index.vue` | `/` |
| `Integrations.tsx` | `pages/integrations.vue` | `/integrations` |
| `Products.tsx` | `pages/products/index.vue` (+ modais em components) | `/products` |
| `SyncLogs.tsx` | `pages/sync-logs.vue` | `/sync-logs` |
| `Alerts.tsx` | `pages/alerts.vue` | `/alerts` |
| `Settings.tsx` | `pages/settings.vue` | `/settings` |
| `Pricing.tsx` | `pages/pricing/[tab].vue` (tabs como rotas: contas/produtos/overrides/auditoria/concorrencia) | `/pricing/contas` etc. |
| `Audit.tsx` | `pages/audit.vue` | `/audit` |
| `Onboarding.tsx` | `pages/onboarding.vue` (middleware redireciona se incompleto) | `/onboarding` |
| **(nova)** Login OTP | `pages/login.vue` (2 etapas: email → código) | `/login` |
| **(nova)** Pendente de aprovação | `pages/pending-approval.vue` | `/pending-approval` |
| **(nova)** Gestão de Usuários | `pages/users/index.vue` + `pages/users/[id].vue` | `/users`, `/users/{id}` |
| **(nova)** Empresas | `pages/companies/index.vue` + `pages/companies/[id].vue` | `/companies`, `/companies/{id}` |
| **(nova)** Cadastros | `pages/cadastros/index.vue` | `/cadastros` |
| `NotFound.tsx` | `error.vue` | — |
| `Home.tsx` | `pages/index.vue` faz o trabalho | — |
| `ComponentShowcase.tsx` | descartar | — |

### 7.2 Composables compartilhados

- `useAuth()` — estado do usuário, logout
- `useApi()` — wrapper sobre `$fetch` injetando cookie + tratando 401
- `useJobPolling(jobId)` — polling de `GET /api/jobs/{job_id}` com `useQuery` + intervalo dinâmico (250ms enquanto running, para quando completa)
- `useToast()` — re-export de `vue-sonner`

---

## 8. Background jobs (Arq + Redis)

Toda execução assíncrona roda no **worker Arq** (processo separado, mesma imagem do `api`). O `api` apenas **enfileira** via `await arq_pool.enqueue_job(...)` e cria o registro espelho em `background_jobs` (Postgres) para o frontend acompanhar.

### 8.1 Cron jobs (Arq cron)

Definidos em `apps/api/app/worker.py` via `cron(...)` no `WorkerSettings`.

| Função Arq | Schedule | O que faz |
|------------|----------|-----------|
| `daily_sync_scheduler` | a cada 5min | Verifica `user_settings.daily_sync_time` em America/Sao_Paulo; enfileira `sync_all_user(user_id)` se ainda não rodou hoje |
| `low_stock_polling` | a cada 2min | Cria alertas para produtos abaixo do threshold |
| `shopee_discrepancy_check` | a cada 4h | Compara estoque Shopee vs esperado |
| `shopee_token_refresh` | a cada 4h | Renova access_token Shopee |
| `bling_token_refresh` | a cada 30min | Renova tokens Bling expirando em < 1h |
| `auto_import_link` | a cada 30min | Tenta linkar `listings` órfãs por SKU |
| `alerts_cleanup` | diário 03:00 BRT | Deleta alerts > 60d **(novo)** |
| `background_jobs_gc` | diário 03:30 BRT | Marca jobs `running` órfãos (sem heartbeat há > 5min) como `failed` **(novo)** |

### 8.2 Long-running jobs (enfileirados sob demanda)

Tipos: `sync_all`, `sync_product`, `auto_link`, `audit`, `sync_bling_costs`, `import_listings`, `import_bling_products`, `push_prices_batch`.

**Fluxo:**

1. Endpoint REST cria registro em `background_jobs` (status=`pending`, `arq_job_id` preenchido com o id retornado pelo enqueue).
2. Worker Arq pega o job, atualiza `background_jobs.status=running`, escreve heartbeat (`last_heartbeat_at`) a cada 10s.
3. Worker incrementa `processed/total` e adiciona linhas em `details` (jsonb array) conforme processa.
4. Frontend faz polling em `GET /api/jobs/{id}` (ou SSE via `GET /api/jobs/{id}/stream` na Fase 13).
5. Em restart do worker: Arq retoma jobs pendentes (Redis persiste); o `background_jobs_gc` recupera os que ficaram sem heartbeat marcando `failed` para retentativa explícita do usuário.

### 8.3 Configurações de retry

Arq config global no `WorkerSettings`:

```python
class WorkerSettings:
    functions = [sync_all_user, sync_product, auto_link, audit_run, ...]
    cron_jobs = [daily_sync_scheduler, low_stock_polling, ...]
    redis_settings = RedisSettings.from_dsn(env.REDIS_URL)
    max_jobs = 10                # concorrência por worker
    job_timeout = 1800           # 30min hard timeout
    keep_result = 3600           # 1h
    max_tries = 3                # 3 tentativas com backoff exponencial
    retry_jobs = True
```

### 8.4 Rate limit por integração (Redis)

Cada `integration_id` tem um token bucket no Redis (chave `ratelimit:{integration_id}`). Antes de cada chamada externa, o cliente faz `await rate_limiter.acquire(integration_id)`. Limites por marketplace (configuráveis em `config/rate_limits.yml`):

- Bling: 60/min (resolve B12 — backoff vira raro porque limite é respeitado proativamente)
- Shopee: 100/min global + 3 concorrentes por shop
- Mercado Livre: 200/min
- Amazon SP-API: 5/s burst, 1/s sustained

---

## 9. Services backend (marketplaces)

Cada marketplace implementa interface comum:

```python
class MarketplaceClient(ABC):
    async def test_connection(self) -> ConnTestResult: ...
    async def list_products(self, *, page: int = 1) -> AsyncIterator[ExternalListing]: ...
    async def get_stock(self, external_id: str, variation_id: str | None = None) -> int: ...
    async def update_stock(self, external_id: str, qty: int, variation_id: str | None = None) -> None: ...
    async def update_price(self, external_id: str, price: Decimal, variation_id: str | None = None) -> None: ...
```

Implementações: `BlingClient`, `ShopeeClient`, `MercadoLivreClient`, `AmazonClient`, `TikTokClient` (stub), `TemuClient` (stub).

### 9.1 Bibliotecas / utilitários comuns

- `RetryPolicy` (tenacity): exponential backoff, retry em 429/503/Cloudflare HTML.
- `RateLimiter` por integração (token bucket via Redis se quiser; v1 in-memory).
- `OAuthRefresher` middleware: ao 401, tenta refresh token e reexecuta.

---

## 10. Bugs do app atual a resolver durante a migração

Pegados da auditoria do código + `todo.md`. Todos viram critérios de aceite das fases.

| # | Bug | Resolução na nova arquitetura |
|---|-----|-------------------------------|
| B1 | Auto-Link ML zera estoque | Refatorar `update_stock` ML: nunca enviar `available_quantity=0` se `bling_stock>0`; assert antes do PUT; cobrir com unit test |
| B2 | 521 ML links com `stock=0`/`last_sync_at=NULL` | Job `backfill_ml_stock` (one-shot), depois log estruturado para identificar links que falham silenciosamente |
| B3 | ML "variation_not_found" intermitente | Auto-fix migra: se variations mudaram, atualiza `product_links.variation_id` baseado em `seller_sku`; se SKU também mudou, alerta e marca link como `requires_review` |
| B4 | OAuth ML callback não salvava token (form-urlencoded) | Já corrigido logicamente; mover lógica para `httpx` com `data=` automático |
| B5 | Shopee 403 produto banido vs erro real | Mapear códigos Shopee para enum `LinkStatus` (`active`, `suspended`, `banned`, `unknown_error`); criar alerta separado |
| B6 | Produtos com SKU vazio impedem auto-link | Validação Pydantic recusa criação sem SKU; UI de "Produtos sem SKU" para correção em massa |
| B7 | Múltiplos anúncios por produto sem UI | Página de produto: tabela expansível mostrando todos `product_links` com botão de sync individual |
| B8 | Jobs in-memory perdem progresso em restart | Arq + Redis (jobs persistidos no Redis com AOF); `background_jobs` no Postgres mantém estado visível ao usuário; `background_jobs_gc` recupera jobs órfãos |
| B9 | Telegram webhook cai sem reconexão | Health check no startup + retry; opcional usar long polling se webhook falhar |
| B10 | `alerts` cresce indefinidamente | Job `alerts_cleanup` (>60d) |
| B11 | Timezone Brasília incorreto fora do servidor BRT | `pendulum` (ou `zoneinfo`) com timezone explícito em todo schedule |
| B12 | Bling Cloudflare 403 HTML | Rate limiter Redis respeita 60/min proativamente; `RetryPolicy` detecta `<html>` em response e retenta com backoff como segunda linha |
| B13 | Push de preço duplicado em retry | `Idempotency-Key` em `POST /api/pricing/push` |
| B14 | Catálogo ML mistura com anúncios normais (depto `catalogo` recém adicionado) | Pricing page filtra por `department=catalogo` e usa endpoint específico `pushCatalogPrice` |
| B15 | Rate limit OTP em memória zera no restart (multi-instância impossível) | Sliding window no Redis (chaves `otp:rl:ip:*`, `otp:rl:email:*`); funciona com N réplicas |
| B16 | OTP enviado de um device e consumido em outro (phishing/troca) | `session_nonce` em cookie HttpOnly Strict gravado antes do envio; verify exige cookie + DB match |

---

## 11. Plano de execução (fases)

Cada fase é entregável independente. Ordem permite ter algo rodando cedo.

### Fase 0 — Setup e infraestrutura Hetzner + Docker Compose (2-3 dias)

**Provisionar VPS Hetzner**
- [ ] Criar servidor Hetzner Cloud (CCX13/CCX23, Ubuntu 24.04 LTS, datacenter `fsn1`/`hel1`)
- [ ] **Adicionar chave SSH `~/.ssh/id_ed25519.pub` do Mac no painel Hetzner** na criação (acesso root passwordless do Mac)
- [ ] No Mac: `~/.ssh/config` add host `davinci` (`HostName <ip>`, `User root`, `IdentityFile ~/.ssh/id_ed25519`)
- [ ] Hardening básico: `ufw allow 22,80,443`, `unattended-upgrades`, fail2ban
- [ ] Instalar Docker Engine + Compose v2 (`curl -fsSL https://get.docker.com | sh`)
- [ ] DNS A records: `app.<dominio>`, `api.<dominio>` → IP da VPS
- [ ] Diretório `/opt/davinci` clonado do repo, owner do user de deploy

**Repo / monorepo**
- [ ] Layout: `apps/api/` (FastAPI + uv), `apps/web/` (Nuxt + pnpm), `packages/api-client/` (gerado), `infra/` (compose, traefik config), `scripts/` (deploy, db dump)
- [ ] `pnpm-workspace.yaml` com `apps/web` e `packages/*`
- [ ] `apps/api/pyproject.toml` + `uv.lock` (commit)
- [ ] `.env.example` versionado; `.env` gitignored

**Docker Compose unificado**
- [ ] `docker-compose.yml` (base): `postgres:18-alpine`, `redis:7-alpine`, `api`, `worker`, `web`. Volumes em `./data/{postgres,redis,uploads}`
- [ ] `docker-compose.override.yml` (dev local Mac): expõe portas 5432/6379/8000/3000 no host; bind-mount do código fonte para hot-reload; sem Traefik
- [ ] `docker-compose.prod.yml` (Hetzner): adiciona `traefik:v3.1` com Let's Encrypt resolver, labels nos services `web`/`api`, sem expor portas internas
- [ ] `apps/api/Dockerfile` base `python:3.12-slim`, instala `uv`, `uv sync --frozen`, `CMD ["uvicorn", ...]`. Worker reaproveita imagem com `command:` diferente no compose
- [ ] `apps/web/Dockerfile` base `node:20-alpine`, `pnpm install --frozen-lockfile`, `pnpm build`, `CMD ["node", ".output/server/index.mjs"]`
- [ ] Healthchecks no compose: `api` → `curl -f localhost:8000/api/health`, `postgres` → `pg_isready`, `redis` → `redis-cli ping`

**Backend**
- [ ] FastAPI base com `/api/health` (checa Postgres + Redis)
- [ ] SQLAlchemy async (asyncpg) → `postgresql+asyncpg://davinci:<pwd>@postgres:5432/davinci`, schema `davinci`
- [ ] Alembic configurado, revision `0001_baseline_schema.py` com TODAS as tabelas (§4) + cria `SCHEMA davinci` na primeira execução
- [ ] Cliente Redis (`redis.asyncio`) + helpers de cache e rate limit
- [ ] Arq `WorkerSettings` em `apps/api/app/worker.py` com cron jobs vazios
- [ ] structlog + middleware de request id
- [ ] Storage local: helper `LocalStorage` (path `/data/uploads`) com interface igual à futura S3 — guarda XLSX da auditoria

**Frontend**
- [ ] Nuxt 3 boot via `pnpm create nuxt apps/web`, layout default (sidebar placeholders)
- [ ] shadcn-vue instalado, tema escuro/claro
- [ ] Pinia + `vue-sonner` + `vue-query` plugins
- [ ] Geração do client OpenAPI no `pnpm postinstall` (script roda contra `API_URL`)

**Deploy**
- [ ] `scripts/deploy.sh` (Mac) — `git push origin main` + `ssh davinci 'cd /opt/davinci && git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build'`
- [ ] `scripts/migrate.sh` — `ssh davinci 'docker compose exec api alembic upgrade head'`
- [ ] `scripts/db-dump.sh` — pull do `/opt/backups/*.sql.gz` mais recente para Mac
- [ ] Cron na VPS: `pg_dump` diário 03:00 BRT em `/opt/backups/`
- [ ] `.env` em `/opt/davinci/.env` na VPS (chmod 600, owner deploy user); nunca commitado

**Critério de aceite:**
1. `docker compose up` no Mac sobe `postgres+redis+api+worker+web`; `/api/health` responde 200 com `{postgres: ok, redis: ok}`
2. `./scripts/deploy.sh` do Mac → Hetzner sobe a stack com Traefik servindo `https://app.<dominio>` e `https://api.<dominio>` com TLS válido
3. `pnpm dev` (web) e `uv run uvicorn ...` (api) rodam direto no Mac contra `postgres`/`redis` em containers

### Fase 1 — Autenticação Email-OTP (1.5-2 dias)

**Backend**
- [ ] Migration cria `auth_codes` + enum `user_status` (`pending`,`active`,`suspended`); enum `user_role` permanece `('admin','user')`
- [ ] `POST /api/auth/request` (gera OTP, hash bcrypt, salva, seta cookie nonce, enfileira `send_otp_email`)
- [ ] `POST /api/auth/verify` (valida nonce + código, upsert user, emite JWT, seta cookie sessão)
- [ ] `POST /api/auth/resend` (mesmo rate limit; reaproveita `session_nonce` se existente)
- [ ] `POST /api/auth/logout`
- [ ] `GET /api/auth/me` (retorna `user`, inclui `status`, `permissions` efetivas)
- [ ] Dependency `require_user` + `require_active_user` (bloqueia `pending`/`suspended`)
- [ ] `CredentialsCipher` AES-GCM (para integrações de marketplace, usado a partir da Fase 2)
- [ ] Rate limiter Redis (`apps/api/app/services/rate_limit.py`) com sliding window
- [ ] Validador opcional Cloudflare Turnstile (skip se `TURNSTILE_SECRET_KEY` vazio)
- [ ] `EmailSender` com driver Mailjet (`httpx` REST); template Jinja2 `otp.html`
- [ ] Job Arq `send_otp_email` (3 tentativas, backoff)
- [ ] Cron Arq `auth_codes_cleanup` diário 03:15 BRT
- [ ] Bootstrap admin: ao subir API, garantir que existe `users` com `email = OWNER_OPEN_ID.split('email:')[1]`, `role='admin'`, `status='active'`

**Frontend**
- [ ] Página `pages/login.vue` em 2 etapas:
  1. Form com campo `email` + (se ativado) widget Turnstile → submit chama `/api/auth/request`. Mostra **prefixo retornado** com destaque: *"Confirme que o e-mail recebido começa com `XYZ4`. Se não bater, NÃO digite o código."*
  2. Form com input do código de 8 chars (mascarado em maiúsculas, sem ambíguos), botão "Reenviar código" (desabilitado por 30s), countdown de expiração.
- [ ] Página `pages/pending-approval.vue` — mostrada quando `user.status='pending'`; bloqueia navegação
- [ ] Composable `useAuth` (Pinia store): `user`, `isAuthenticated`, `requestOtp(email)`, `verifyOtp(code)`, `logout()`
- [ ] Middleware global `auth.global.ts`: redireciona não-autenticados para `/login`; redireciona `pending` para `/pending-approval`; permite `/login` e `/pending-approval` sem auth
- [ ] Plugin `vue-query` configurado com `credentials: 'include'` no `$fetch`

**Aceite:**
1. Pedido OTP em `/login` envia e-mail real em < 5s; resposta inclui prefixo.
2. Verifico código correto → cookie `davinci_session` setado, redirect para `/`.
3. Código errado 5x → bloqueio por 1h (rate limit).
4. Tentativa de OTP de IP diferente do que pediu (cookie nonce não bate) → 401.
5. Login com email = `OWNER_OPEN_ID` cria usuário admin já `active`.
6. Login com outro email cria user `pending`; sem aprovação, `/api/auth/me` retorna `requires_approval=true` e Nuxt mostra `/pending-approval`.
7. Reload da página mantém sessão (cookie HttpOnly persiste).

### Fase 1.5 — Gestão de Usuários e Permissões (1-2 dias)

Cobre a nova tela de admin (não existia no app antigo). Precisa vir antes da Fase 2 para que os endpoints subsequentes já apliquem `require_permission(...)`.

**Backend (router `users`) — toda router gateada com `Depends(require_admin)`**
- [ ] `GET /api/users` (lista, paginado)
- [ ] `GET /api/users/{id}`
- [ ] `POST /api/users` (campos: `name`, `email`, `tuta`, `upseller`, `bling_login`, `adspower`, `permissions`; **`role` sempre `'user'`** — promoção a admin só via env `OWNER_OPEN_ID` no bootstrap ou direto no DB, nunca via UI)
- [ ] `PATCH /api/users/{id}` (campos cadastrais; **bloquear** mudança de `role` na API)
- [ ] `PATCH /api/users/{id}/permissions` (matriz inteira; valida implicações `delete→edit→view`; **403 se tentar editar permissions de outro admin**)
- [ ] `DELETE /api/users/{id}` (soft delete: marca `disabled_at`; impede deletar último admin; impede deletar a si mesmo)
- [ ] `GET /api/users/me/permissions` (matriz efetiva, **acessível sem `require_admin`** — é o próprio user vendo o que pode)
- [ ] `Permissions` schema Pydantic (§5.5) com defaults para todos os 14 recursos
- [ ] `require_permission(resource, action)` e `require_admin` dependencies
- [ ] Bootstrap: ao subir API, se não existir nenhum user com `role=admin`, promover o user cujo `open_id == OWNER_OPEN_ID` (criado no primeiro login OTP) para `role=admin` + `status=active`

**Frontend `pages/users/index.vue`** (admin-only via `middleware: ['auth','admin']`)
- [ ] Tabela de usuários (nome, e-mail, tuta, upseller, bling, adspower, role, status, último login, ações)
- [ ] Badge `admin`/`user` na coluna role (read-only — não editável pela UI)
- [ ] Botão "Novo usuário" (modal com campos texto, sem campo de role; user criado nasce `user`/`pending`)
- [ ] Coluna "Permissões" abre `pages/users/[id].vue`
- [ ] Ação "Aprovar" para users em `status=pending` (PATCH status para `active`)

**Frontend `pages/users/[id].vue`** (admin-only)
- [ ] Form de campos cadastrais (`name`, `email`, `tuta`, `upseller`, `bling`, `adspower`); role exibido mas não editável
- [ ] **Tabela de permissões idêntica ao print:** linhas = 14 recursos, colunas = `visualizar | editar | excluir`, células = `<Checkbox>` controlado, com tooltip explicando a implicação cascata
- [ ] Se o user sendo editado é `admin`, mostrar mensagem "Admins têm bypass total — matriz não se aplica" e desabilitar checkboxes
- [ ] Botão "Marcar tudo" / "Desmarcar tudo" por linha e por coluna
- [ ] Salvar dispara `PATCH /api/users/{id}/permissions`
- [ ] Botão "Excluir usuário" — desabilitado se `id == currentUser.id` ou se for o último admin

**Frontend — middlewares e composables**
- [ ] `composables/useCan.ts` e `composables/useIsAdmin.ts` (§5.5)
- [ ] `middleware/auth.global.ts` redireciona não-autenticados para `/login`
- [ ] `middleware/admin.ts` redireciona para `/403` se `role != admin`
- [ ] `middleware/permission.ts` lê `to.meta.permission`, redireciona para `/403` se não autorizado
- [ ] Sidebar:
  - Item "Usuários" só aparece se `useIsAdmin()`
  - Demais itens condicionais ao recurso correspondente ter `view` no permissions (ou role admin)
- [ ] Botões "Editar"/"Excluir" usam `v-if="useCan(...)"`
- [ ] Página `/403` simples

**Aceite:**
1. Login com user admin (`OWNER_OPEN_ID`) → vê tudo, inclui menu "Usuários".
2. User comum (role=user) NÃO vê o menu "Usuários" e ao acessar `/users` direto pela URL é redirecionado para `/403`. Backend `GET /api/users` retorna 403.
3. Admin cria user "João" sem nenhuma permissão → João loga e vê só o Dashboard básico; sidebar vazia para módulos.
4. Admin abre `/users/joao`, marca `produtos:view+edit` e `tabela_precos:view`. João recarrega, sidebar agora mostra "Produtos" e "Tabela de Preços"; em produtos pode editar mas não excluir.
5. Marcar `produtos:delete` automaticamente liga `edit` e `view` na UI antes de salvar.
6. Tentativa de excluir o próprio admin (último) → backend retorna 409 e UI mostra erro.
7. API rejeita `PATCH /api/users/{id}` com campo `role` — usuário promovido a admin **só** via `OWNER_OPEN_ID` no bootstrap ou edição direta no DB.

### Fase 1.6 — Empresas, Lojas e Cadastros (2-3 dias)

Página principal de gestão. Vem antes de Integrações porque cada `integration` agora nasce vinculada a uma `store`. Resolve a aba "empresas" e "cadastro" do Excel; reserva tabelas vazias para `margem` e `conciliacao_frete` (futuras).

**Backend — schemas e migrations**
- [ ] Migration cria enums: `marketplace` (com `ml`, `shopee`, `amazon`, `aliexpress`, `temu`, `tiktok`, `shein`, `magalu`, `site`), `store_status` (`active`, `inactive`, `closing`, `banned`, `pending`, `under_review`), `cadastro_tipo` (`fone`, ...), `cadastro_status`
- [ ] Tabelas `companies`, `stores`, `cadastros`, `cadastros_stores` (§4.1 #18-21)
- [ ] Tabelas reservadas vazias `margens` e `conciliacao_frete` (apenas `id`, `created_at` — só para FKs futuras existirem)
- [ ] Adicionar `integrations.store_id` UNIQUE NULL e `product_links.store_id` NULL

**Backend — router `companies`**
- [ ] CRUD (`GET/POST/PATCH/DELETE /api/companies[/{id}]`) com guard `empresa:view|edit|delete`
- [ ] `GET /api/companies/grid` retorna estrutura pronta para a tela:
      ```json
      { "marketplaces": ["ml","shopee",...],
        "rows": [{
          "company": {"id":1,"razao_social":"AGUIAR...","apelido":"aguiar","cnpj":"...","uf":"SP",...},
          "stores": {"ml":{"id":99,"status":"active","label":"aguiar"},
                     "shopee":null, "amazon":null, ...}
        }] }
      ```
- [ ] Validação CNPJ (algoritmo de DV, biblioteca `validate-docbr` no front, `python-stdnum` no back)
- [ ] UNIQUE em `cnpj` retorna 409 com mensagem amigável

**Backend — router `stores`**
- [ ] CRUD com guard `empresa:edit` (criar/editar loja é parte da gestão de empresa)
- [ ] `POST /api/stores/{id}/link-integration` — opções:
  - body `{integration_id}` para vincular existente
  - body `{provider}` retorna URL de start OAuth com `state` carregando `store_id` (o callback amarra automaticamente)
- [ ] `POST /api/stores/{id}/unlink-integration` (não deleta integration, só desvincula)
- [ ] Mudança de `status` é logada em `sync_logs` (action=`store_status_change`)

**Backend — router `cadastros`**
- [ ] CRUD com guard `cadastro:view|edit|delete`
- [ ] `PUT /api/cadastros/{id}/stores` aceita `[{store_id, alias?}, ...]`, faz upsert/delete em `cadastros_stores` numa transação
- [ ] `GET /api/cadastros/grid` formato matriz pronto para a tela (colunas = marketplaces, célula = `{store_id, alias, store_status}`)

**Frontend `pages/companies/index.vue`**
- [ ] Tabela igual ao print 1: colunas fixas `EMPRESAS | RESPONSAVEL | UF | CNPJ | I.E. | conta`, depois 1 coluna por marketplace, depois `site | obs`
- [ ] Célula de marketplace renderiza:
  - vazio (sem loja) → botão `+` (clique cria `store` em status `pending` com modal de confirmação)
  - `status=active` → "X" verde
  - `status=banned` → "banido" vermelho
  - `status=closing` → "fechar" amarelo
  - `status=under_review` → "?" cinza
- [ ] Filtros: por marketplace, por UF, busca por razão social/CNPJ/apelido
- [ ] Botão "Nova empresa" abre modal com formulário cadastral
- [ ] Linha clicável → `/companies/{id}`

**Frontend `pages/companies/[id].vue`**
- [ ] Card com dados cadastrais editáveis (`razao_social`, `apelido`, `responsavel_id` (select de users), `uf`, `cnpj`, `inscricao_estadual`, `site_url`, `obs`)
- [ ] Tabela "Lojas desta empresa" — uma linha por marketplace possível (9 fixas), mostra status, badge de "integração conectada" quando `integration_id != null`, botão "Conectar via OAuth" quando suportado, dropdown de status, campo `apelido_override` opcional, `notes`, **select `Loja no Bling`** que carrega via `GET /api/integrations/{bling_integration_id}/bling-stores` e popula `bling_store_id` (com filtro de busca; mostra `nome (id: 12345)`)
- [ ] Painel "Cadastros vinculados" mostra cadastros (telefones etc.) que tocam alguma loja desta empresa
- [ ] Botão "Excluir empresa" (com confirmação dupla, alerta sobre cascade)

**Frontend `pages/cadastros/index.vue`**
- [ ] Tabela igual ao print 2: colunas fixas `tipo | provedor | responsavel | codigo`, depois 1 coluna por marketplace
- [ ] Célula de marketplace mostra:
  - vazio → célula clicável que abre seletor de loja
  - preenchido → label do `cadastros_stores.alias` (fallback: `companies.apelido`); status visual (riscado se `store.status='inactive'`, fundo amarelo se `closing`)
- [ ] Botão "Novo cadastro" abre modal: tipo (select), provedor (autocomplete de valores existentes), responsável (select users), código (text), e bloco de "Vincular a lojas" com checkboxes agrupadas por empresa
- [ ] Filtro por tipo, provedor, responsável, busca por código
- [ ] Edit inline de alias da célula (double-click)

**Frontend — composable**
- [ ] `useMarketplaces()` retorna lista canônica + helpers de label/icon
- [ ] `useCompanyGrid()` envolve `GET /api/companies/grid` com vue-query

**Aceite:**
1. Cadastro a empresa "AGUIAR INTERMEDIACOES LTDA", CNPJ, UF=SP, apelido "aguiar". Marco "X" em ML clicando na célula → cria `store(company=aguiar, marketplace=ml, status=active)`.
2. Em `/companies/aguiar` clico "Conectar OAuth" no card ML → fluxo OAuth Mercado Livre completa e a `store.integration_id` fica preenchido. No mesmo card, escolho no select "Loja no Bling" o canal correspondente — `store.bling_store_id` salvo; um sync posterior envia `idLoja` correto na chamada PUT do Bling.
3. Crio cadastro `fone` com código `11951091238`, vinculo às lojas `ml/aguiar` e `ml/aguiar2` — aparece no grid de cadastros corretamente nas duas células ML (a segunda mostra "aguiar2" como alias).
4. Marco status da loja shopee/Mega como `notes="shopee esta como marquezini"` e fica visível em ambas as telas.
5. Tentativa de criar empresa com CNPJ duplicado → 409 com mensagem clara.
6. Usuário sem `empresa:edit` não vê botão `+` nas células nem pode mudar status.

### Fase 2 — Integrações (1-2 dias)

Cobre `Integrations.tsx`. Agora as integrações nascem amarradas a uma `store` (Fase 1.6).

**Backend (router `integrations`)**
- [ ] `GET /api/integrations` (sem credenciais; inclui `store_id`, `company_id` derivado)
- [ ] `GET /api/integrations/{id}`
- [ ] `POST /api/integrations` (exige `store_id`; valida que store existe e ainda não tem integration; cifra credenciais)
- [ ] `PATCH /api/integrations/{id}`
- [ ] `DELETE /api/integrations/{id}` (também limpa `store.integration_id`)
- [ ] `POST /api/integrations/{id}/test` (chama `MarketplaceClient.test_connection`)
- [ ] `GET /api/oauth/{provider}/start?store_id=` (Bling, Shopee, ML, Amazon) — `state` codifica `store_id`
- [ ] `GET /api/oauth/{provider}/callback` cria/atualiza integration e amarra à store
- [ ] `BlingClient.test_connection` implementado

**Frontend `pages/integrations.vue`**
- [ ] Listagem em cards com badge de status, agrupados por empresa
- [ ] Form criar/editar (modal) com campos por plataforma + select de loja (filtrado por marketplace)
- [ ] Botão "Conectar via OAuth" para providers que suportam (parte das ações também presente na página da empresa)
- [ ] Botão "Testar conexão"

**Aceite:** consigo conectar Bling, Shopee e ML via OAuth a partir da página da empresa OU desta página, e a `store.integration_id` reflete; rotação de token ao 401 funciona.

### Fase 3 — Produtos e Product Links (3-4 dias)

Cobre `Products.tsx` (a maior página, ~1500 linhas) — quebrar em sub-tarefas.

**Backend (router `products`)**
- [ ] `GET /api/products` paginado, filtros `search`, `integration_id`, `low_stock`
- [ ] `GET /api/products/{id}` enriquecido com `product_links`
- [ ] `POST /api/products`, `PATCH /api/products/{id}`, `DELETE /api/products/{id}`
- [ ] `POST /api/products/bulk-delete`
- [ ] `DELETE /api/product-links/{id}`
- [ ] `GET /api/products/preview/bling` (paginação interna do Bling)
- [ ] `POST /api/products/import/bling` (sync importa produtos selecionados)
- [ ] `POST /api/products/import/csv` (multipart, parse com pandas)
- [ ] `POST /api/products/update-names` (refresh nomes do Bling)
- [ ] `GET /api/product-links` (lista com nomes integrações)
- [ ] `POST /api/jobs/auto-link` (cria background_job tipo `auto_link`; popula `product_links.store_id` derivando de `integration_id → store_id`)

**Service `BlingClient`**
- [ ] `list_products()` paginado com retry/backoff
- [ ] `list_lojas()` → canais cadastrados no Bling (usado pelo endpoint `GET /api/integrations/{id}/bling-stores`)
- [ ] `update_stock(product_id, qty, *, bling_store_id: int | None = None)` — quando `bling_store_id` informado, envia `idLoja=...` para o Bling refletir no canal correto
- [ ] `update_price(product_id, price, *, bling_store_id: int | None = None)` — idem
- [ ] Tratar Cloudflare 403 HTML

**Frontend `pages/products/index.vue`**
- [ ] Tabela com filtros, busca, paginação 50/pág
- [ ] Modal "Importar do Bling" com seleção
- [ ] Modal "Importar CSV"
- [ ] Modal "Auto-link" com progress (usa `useJobPolling`)
- [ ] Linha expansível mostrando todos os `product_links` (resolve B7)
- [ ] Bulk-delete

**Aceite:** importo 50 produtos do Bling, auto-link cria links em ML/Shopee, vejo progresso em tempo real, links visíveis na linha expandida.

### Fase 4 — Sincronização e Sync Logs

Fatiada para desacoplar plataforma de sync (orchestrator + UI + logs) de cada adapter de marketplace. Cutover parcial possível: liga marketplace por marketplace conforme cada sub-fase passa.

#### Fase 4a — Plataforma de sync (1-2 dias)

Schema, orchestrator, ABC fechada, UI/polling/logs. Adapter de saída funcional somente para Bling (refresh de `bling_stock` em `product_links`, sem push outbound — `BlingClient.update_stock` já existe da Fase 3 e fica disponível para sub-fases 4b).

**Migration `0005_sync_logs`**
- [ ] Cria `sync_logs` particionada por mês (Postgres declarative partitioning, `PARTITION BY RANGE (created_at)`)
- [ ] Colunas: `id`, `user_id`, `job_id` FK→background_jobs NULL, `product_id` FK→products NULL, `product_link_id` FK→product_links NULL, `platform`, `action` (`refresh_bling`, `update_stock`, `update_price`, `store_status_change`, `auto_link`, ...), `status` (reaproveita `link_sync_status` enum), `qty_before`, `qty_after`, `error_code`, `error_detail`, `payload` JSONB, `created_at`
- [ ] Cria partições para mês corrente + 2 próximos; job de cron (Fase 5) cria partições futuras
- [ ] Índices `(user_id, created_at DESC)`, `(platform, status, created_at DESC)`, `(product_id, created_at DESC)`

**Service `SyncOrchestrator` (skeleton)**
- [ ] Loop produtos → `product_links` ativos → dispatch por `platform` via `client_for(...)`
- [ ] Classifica resultado em `SyncResult{status: ok|skipped|retryable|fatal, qty_before, qty_after, error_code, error_detail}`
- [ ] Persiste `SyncLog` por link processado, atualiza `product_link.last_sync_*`
- [ ] Heartbeat em `background_jobs.last_heartbeat_at` a cada N links; escreve `details[]` truncado
- [ ] Lock por `user_id` via `pg_advisory_lock` (mata `syncLock.ts`)
- [ ] Adapter Bling: refresh-only (puxa `bling_stock` de `/produtos/{id}` → grava em `product_links.stock` e `products.stock`). Outbound `update_stock` Bling continua disponível mas só é exercitado quando 4b precisar (ex.: store com `bling_store_id` para empurrar canal certo)
- [ ] Marketplaces ML/Shopee/Amazon/TikTok/Temu/Aliexpress não implementados → orchestrator marca `skipped` com `error_code='platform_not_implemented'`

**ABC `MarketplaceClient` (fechada nesta fase)**

```python
class MarketplaceClient(Protocol):
    async def test_connection(self) -> TestResult: ...
    async def update_stock(
        self, link: ProductLink, qty: int, *, bling_store_id: int | None = None
    ) -> SyncResult: ...
```

Assinatura imutável a partir daqui — sub-fases 4b implementam, não alteram.

**Backend (router `sync`)**
- [ ] `POST /api/jobs/sync-all` (cria job `sync_all`)
- [ ] `POST /api/sync/product/{id}` (síncrono se rápido, senão job)
- [ ] `GET /api/sync-logs` paginado
- [ ] `GET /api/sync-logs/stats` (sucesso/erro/skipped últimas 24h)
- [ ] `GET /api/jobs/{job_id}` polling
- [ ] Job persistido em `background_jobs`, atualiza `processed`, escreve `details[]`

**Frontend `pages/sync-logs.vue`**
- [ ] Tabela com filtros plataforma/status/data/SKU
- [ ] Drawer com diff antes/depois de estoque
- [ ] Página `Products.tsx` ganha botão "Sync Bling" (refresh estoque) e "Sync All" (enfileira `sync_all`)

**Aceite 4a:** refresh de 500 SKU Bling termina, `sync_logs` particionada grava registros, advisory lock barra concorrência (segunda chamada retorna 409 ou agrega ao job em andamento), polling reflete progresso, links ML/Shopee/Amazon registrados como `skipped/platform_not_implemented` (não falham).

#### Fase 4b.ML — Mercado Livre (2 dias) — **prioridade B1/B3**

**Service `MercadoLivreClient`**
- [ ] OAuth + refresh
- [ ] `update_stock(link, qty, *, bling_store_id=None)`: nunca enviar `available_quantity=0` se `bling_stock>0` — guard com assert antes do PUT (B1)
- [ ] Auto-fix `variation_id` por `seller_sku`: se variations mudaram, atualiza `product_links.variation_id`; se SKU também mudou, marca `last_sync_status='requires_review'` + alerta (B3)
- [ ] Map de erros ML para `LinkSyncStatus` + `error_code`
- [ ] Job one-shot `backfill_ml_stock`: roda sobre links com `stock=0`/`last_sync_at=NULL` (B2)
- [ ] Test connection via `GET /users/me`

**Testes regressivos**
- [ ] `test_ml_update_stock_never_zeroes_when_bling_positive` (B1)
- [ ] `test_ml_variation_remap_by_seller_sku` (B3)
- [ ] `test_backfill_ml_stock_repopulates_links` (B2)

**Aceite 4b.ML:** sync completo de 100 SKU Bling→ML termina sem zerar estoque, todos os links com `last_sync_at` populado, regressão B1/B2/B3 verde.

#### Fase 4b.Shopee — Shopee (2 dias)

**Service `ShopeeClient`**
- [ ] OAuth (signed requests Shopee)
- [ ] `update_stock(link, qty, *, bling_store_id=None)`
- [ ] Map códigos Shopee → `LinkStatus` (`active`, `suspended`, `banned`, `unknown_error`); 403 banido vs 403 erro real ficam em buckets separados (B5)
- [ ] Alerta dedicado para `banned` (canal "produto banido")

**Testes regressivos**
- [ ] `test_shopee_403_banned_vs_unknown` (B5)

**Aceite 4b.Shopee:** sync 100 SKU Bling→Shopee, links banidos vão para `last_sync_status='requires_review'` + alerta, demais erros 403 vão para `retryable` ou `fatal` corretamente.

#### Fase 4b.Amazon — Amazon SP-API (1-2 dias)

**Service `AmazonClient`**
- [ ] LWA + assinatura SP-API
- [ ] `update_stock(link, qty, *, bling_store_id=None)` via Feeds API ou Listings API (decidir conforme volume)
- [ ] Test connection

**Aceite 4b.Amazon:** sync 100 SKU Bling→Amazon termina, logs gravados.

#### Fase 4b.stubs — TikTok / Temu / Aliexpress (0.5 dia)

- [ ] `TikTokClient`, `TemuClient`, `AliexpressClient` retornam `SyncResult(status=skipped, error_code='platform_not_implemented')` em `update_stock`; `test_connection` retorna `ok=False, detail='not_implemented'`
- [ ] Permite criar `integration` desses tipos sem quebrar orchestrator (já tratado em 4a, sub-fase só promove os stubs a clients reais quando vier escopo)

### Fase 5 — Webhook Bling + scheduler (1 dia)

**Backend**
- [ ] `POST /api/webhooks/bling` valida assinatura, enfileira `sync_product` async
- [ ] APScheduler jobs: `daily_sync_scheduler`, `low_stock_polling`, `shopee_token_refresh`, `bling_token_refresh`, `shopee_discrepancy_check`, `auto_import_link`, `alerts_cleanup`, `background_jobs_gc`
- [ ] `GET /api/settings/webhook-url` retorna URL pronta para colar no Bling

**Aceite:** mudo estoque no Bling, em < 5s vejo log de sync nos marketplaces.

### Fase 6 — Alertas (0.5 dia)

**Backend (router `alerts`)**
- [ ] `GET /api/alerts?limit=` paginado
- [ ] `GET /api/alerts/last-daily-sync`
- [ ] `GET /api/alerts/unread-count`
- [ ] `POST /api/alerts/{id}/read`
- [ ] `POST /api/alerts/read-all`

**Frontend `pages/alerts.vue`**
- [ ] Lista com badge severidade, marcar lido, contador no header

**Aceite:** badge no header reflete unread; marcar como lido funciona.

### Fase 7 — Settings + Telegram (0.5 dia)

**Backend (router `settings`)**
- [ ] `GET /api/settings` (defaults se não existir)
- [ ] `PATCH /api/settings`
- [ ] `GET /api/settings/webhook-url`
- [ ] `TelegramClient.send_message` + `bot_listen` opcional

**Frontend `pages/settings.vue`**
- [ ] Form com sync interval, daily time, low stock threshold, preferências de notificação

**Aceite:** mudar `daily_sync_time` reflete no scheduler na próxima janela de 5min.

### Fase 8 — Listings + auto-import (1 dia)

**Backend (router `listings`)**
- [ ] CRUD + `POST /api/listings/import` (background job)
- [ ] `listing_requests` CRUD

**Frontend `pages/listings.vue`** (nova, ou parte de `Products`)
- [ ] Tabela filtrável, busca, ações em massa

**Aceite:** importo anúncios da Shopee, auto-link encontra produtos por SKU.

### Fase 9 — Pricing (3-5 dias, mais complexa)

Cobre `Pricing.tsx`. Quebrar por tab.

**Backend (router `pricing`)**
- [ ] `accounts` CRUD + `auto-match` + `setDepartment` (em `store-info`)
- [ ] `products` CRUD + `import` + `toggle-catalog`
- [ ] `overrides` CRUD com `setCellStatus`
- [ ] `pushPrice` (com `Idempotency-Key`, resolve B13)
- [ ] `pushCatalogPrice` (resolve B14)
- [ ] `getCatalogListings`
- [ ] `sendPushReport` (Telegram)
- [ ] `getSkuAudit` + `dismiss`/`undismiss`
- [ ] `fetchActualPrices`
- [ ] `searchCompetitorPrices` (ML public API)
- [ ] `POST /api/jobs/sync-bling-costs`

**Frontend `pages/pricing/[tab].vue`**
- [ ] Tab `contas` — tabela editável, ações em massa
- [ ] Tab `produtos` — tabela com colunas Kit1-4, custo Bling, toggle catálogo
- [ ] Tab `overrides` — tabela cruzada produto × conta com células editáveis (use `vue-virtual-scroller` para >1000 linhas)
- [ ] Tab `auditoria` — SKUs em listings sem entrada em pricing_products, com dismiss/undismiss
- [ ] Tab `concorrencia` — busca de preços ML
- [ ] Botão "Push" por linha/coluna/seleção, modal de confirmação, progress, telegram report
- [ ] UI de "Produtos sem SKU" (B6)

**Aceite:** edito margem em 10 contas, vejo preços recalculados em real-time, faço push em 5 produtos x 3 contas, recebo report no Telegram, push duplicado por idempotency key não duplica.

### Fase 10 — Auditoria por planilha (2 dias)

Cobre `Audit.tsx`.

**Backend (router `audit`)**
- [ ] `POST /api/audit/uploads` (multipart, salva em volume local `/data/uploads/{user_id}/{uuid}.xlsx` via `LocalStorage`, retorna sheets)
- [ ] `POST /api/audit/parse` (parse com `openpyxl`, devolve account map)
- [ ] `POST /api/jobs/audit` (cria job tipo `audit`, processa SKU × conta)
- [ ] `POST /api/audit/fix-price` e `/fix-prices`

**Service `AuditRunner`**
- [ ] Compara estoque/preço esperado (Bling + pricing) vs planilha
- [ ] Classifica: `ok`, `price_mismatch`, `missing`, `paused`

**Frontend `pages/audit.vue`**
- [ ] Upload arquivo, escolher aba, preview
- [ ] Disparar audit, polling progresso
- [ ] Tabela de resultados com filtros, botão "Corrigir preço" (individual e em massa)

**Aceite:** subo planilha de 5000 produtos, audit termina em < 10min, posso corrigir preços em massa.

### Fase 11 — Discrepâncias + Store Info (0.5 dia)

**Backend**
- [ ] `GET /api/discrepancies`
- [ ] `store_info` CRUD + `setDepartment` (cria `pricing_accounts` automaticamente)

**Frontend**
- [ ] Página `/discrepancies` simples
- [ ] Página `/store-info` (CRUD)

### Fase 12 — Onboarding + Dashboard (1 dia)

**Frontend**
- [ ] `pages/onboarding.vue` com 5 steps (Bling → import produtos → import listings → auto-link → ativar sync)
- [ ] `pages/index.vue` Dashboard com cards (produtos ativos, integrações conectadas, alertas) + gráfico Recharts (substituto Vue: [`vue-chartjs`](https://vue-chartjs.org/) ou [`unovis`](https://unovis.dev/))
- [ ] Middleware redireciona para onboarding se nenhuma integration ainda

**Aceite:** novo usuário consegue percorrer onboarding até ter 1 produto sincronizando.

### Fase 13 — Hardening, testes, observabilidade (2 dias)

- [ ] Testes unitários services críticos (`SyncOrchestrator`, `MercadoLivreClient.update_stock` — cobre B1)
- [ ] Testes de integração contra fixtures dos marketplaces (responses gravados)
- [ ] Testes E2E Playwright para fluxos chave (login, importar produto, sync, push de preço)
- [ ] Métricas: contagem de syncs, latência por marketplace, erros por código
- [ ] Sentry/equivalente para erros não tratados
- [ ] Documentação OpenAPI publicada

### Fase 14 — Cutover (0.5 dia)

- [ ] Script de migração de dados MySQL → Postgres (export por tabela, import com `COPY`)
- [ ] Validar contagens (produtos, links, alerts) batem
- [ ] Apontar webhooks Bling para nova URL
- [ ] Reconfigurar redirect URI Mercado Livre (depende de domínio novo)
- [ ] Manter app antigo em modo read-only por 30 dias

---

## 12. Riscos e mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| ML/Shopee mudam API durante migração | Média | Alto | Wrappers isolados; testes contra fixtures |
| Migração de dados perde links | Baixa | Alto | Validação contagem antes/depois; manter app antigo paralelo |
| OAuth redirect URI bloqueia novo domínio | Alta | Médio | Solicitar novo URI ao ML/Shopee no início da Fase 1 |
| Performance pior em queries que usavam Drizzle joins | Média | Médio | EXPLAIN das queries pesadas; índices §4.1 |
| Equipe nova com Vue 3 | Depende | Médio | Stack escolhido por proximidade com React (composables ≈ hooks, shadcn-vue ≈ shadcn-react) |

---

## 13. Variáveis de ambiente

Em produção, vivem em `/opt/davinci/.env` na VPS (chmod 600). Em dev local Mac, em `.env` na raiz do repo (gitignored). `.env.example` versionado.

```
# App
ENV=production
APP_URL=https://app.hadken.com
API_URL=https://api.hadken.com
PORT=8000

# Database (Postgres 18 no compose, hostname interno do compose network)
DATABASE_URL=postgresql+asyncpg://davinci:<pwd>@postgres:5432/davinci
DATABASE_SCHEMA=davinci
POSTGRES_PASSWORD=<pwd>     # consumido pelo container postgres

# Redis (compose, hostname interno)
REDIS_URL=redis://redis:6379/0
ARQ_REDIS_URL=redis://redis:6379/1   # banco separado para fila Arq

# Storage local (audit uploads)
UPLOADS_DIR=/data/uploads    # bind-mount de ./data/uploads do host

# Traefik (prod)
TRAEFIK_ACME_EMAIL=spectrum77@tuta.com
DOMAIN_APP=app.hadken.com
DOMAIN_API=api.hadken.com

# Auth — Login OTP + JWT
JWT_SECRET=<random 32 bytes base64>            # obrigatório
JWT_TTL_SECONDS=604800                         # 7d
CREDENTIALS_KEY=<random 32 bytes base64>       # AES-GCM (credenciais OAuth marketplaces)
COOKIE_NAME=davinci_session
COOKIE_DOMAIN=.hadken.com
OWNER_OPEN_ID=email:spectrum77@tuta.com # bootstrap admin no primeiro login

# OTP
OTP_CODE_TTL_MS=600000                         # 10 min
OTP_MAX_ATTEMPTS=5
OTP_RATE_PER_IP=3                              # /hora
OTP_RATE_PER_EMAIL=3                           # /hora
OTP_PREFIX_LEN=4                               # chars do prefixo anti-phishing
OTP_CODE_LEN=8

# Cloudflare Turnstile (opcional; se vazio, validação é pulada)
TURNSTILE_SECRET_KEY=
TURNSTILE_SITE_KEY=                            # vai para o frontend via runtimeConfig

# E-mail (Mailjet)
EMAIL_FROM="DaVinci <no-reply@hadken.com>"
EMAIL_FROM_NAME=DaVinci
MAILJET_API_KEY=
MAILJET_SECRET_KEY=

# Bling
BLING_CLIENT_ID=
BLING_CLIENT_SECRET=
BLING_REDIRECT_URI=https://api.hadken.com/api/oauth/bling/callback
BLING_WEBHOOK_SECRET=

# Shopee
SHOPEE_PARTNER_ID=
SHOPEE_PARTNER_KEY=
SHOPEE_USE_SANDBOX=false

# Mercado Livre
ML_CLIENT_ID=
ML_CLIENT_SECRET=
ML_REDIRECT_URI=https://api.hadken.com/api/oauth/mercadolivre/callback

# Amazon
AMAZON_CLIENT_ID=
AMAZON_CLIENT_SECRET=
AMAZON_REFRESH_TOKEN=
AMAZON_MARKETPLACE_ID=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Observabilidade
LOG_LEVEL=info
SENTRY_DSN=
```

---

## 14. Critérios de aceite globais

A migração está **pronta para cutover** quando:

1. Todas as 15 fases (incl. 1.5 e 1.6) concluídas com seus aceites individuais.
2. Bugs B1, B2, B3, B5, B6, B8, B11, B13, B15, B16 verificados resolvidos com teste regressivo.
3. Sync diário roda 7 dias sem erro fatal.
4. Push de preço processa 100 itens em < 5min.
5. Auditoria de planilha 5k linhas < 10min.
6. Migração de dados validada (contagens batem, amostragem manual de 20 produtos).
7. Webhooks Bling recebidos e processados em < 1s.
8. Documentação OpenAPI completa, navegável em `/api/docs`.
9. Testes E2E críticos verdes.
10. Rollback plan documentado (voltar DNS para app antigo se preciso).

---

## 15. Estimativa total

Soma das fases (incluindo Fase 1.5 de usuários e Fase 1.6 de empresas/lojas/cadastros): **~27 dias úteis** de uma pessoa sênior. Realisticamente **6-7 semanas** com testes, ajustes e revisão.

Sugestão de paralelização (2 devs): 1 backend + 1 frontend a partir da Fase 1.5, encurta para **4-5 semanas**.
