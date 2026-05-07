# Cutover Runbook — DaVinci (Fase 14)

Migração final do app antigo (Drizzle/MySQL hospedado na Manus AI, mas com schema
em formato Postgres `stocksync` — ver `~/Downloads/sql_completo.sql`) para o
novo app DaVinci (Postgres `davinci`, FastAPI + Nuxt 3 na Hetzner).

Janela alvo: **0,5 dia**, com app antigo permanecendo como **read-only por 30
dias** após a virada.

---

## 0. Pré-requisitos

- [ ] App novo já em produção em `https://app.hadken.com` / `https://api.hadken.com`
- [ ] Alembic do novo app rodado (`./scripts/migrate.sh`) → schema `davinci` populado vazio
- [ ] Variáveis em `/opt/davinci/.env` na VPS conferidas, principalmente:
  - `DATABASE_URL` (alvo)
  - `CREDENTIALS_KEY` (estável — se mudar depois, integrações precisam ser re-OAuth)
  - `OWNER_OPEN_ID=email:spectrum77@tuta.com`
  - `BLING_REDIRECT_URI=https://app.hadken.com/api/oauth/bling/callback`
  - `ML_REDIRECT_URI=https://app.hadken.com/api/oauth/ml/callback`
- [ ] Backup recente do app antigo dump pronto localmente (`legacy-stocksync.sql.gz`)
- [ ] App antigo avisado dos usuários: **manutenção começando às HH:MM**

---

## 1. Carregar o schema antigo na VPS como `stocksync_legacy`

O dump da Manus AI vira um schema lateral no mesmo Postgres do DaVinci. Não
toca em `davinci`.

```bash
./scripts/cutover-load-legacy.sh ~/Downloads/legacy-stocksync.sql.gz
```

O script:
1. faz `scp` do dump para a VPS,
2. roda `psql` dentro do container `postgres`,
3. renomeia `stocksync` → `stocksync_legacy`,
4. imprime `loaded N tables`.

> Suporta `.sql.gz`, `.sql` ou `.dump`/`.custom` (pg_restore).

Se o dump original for de **MySQL** e não Postgres:

```bash
# converter antes de rodar o script acima
pgloader mysql://user:pass@manus-host/davinci postgresql://localhost/tmp_legacy
pg_dump -Fp -n public tmp_legacy | sed 's/SCHEMA public/SCHEMA stocksync/g' > legacy-stocksync.sql
gzip legacy-stocksync.sql
```

(O resto do runbook segue igual.)

---

## 2. Rodar a migração de dados

```bash
./scripts/cutover-run.sh --reset
```

Equivale a, dentro do container `api`:

```bash
python -m app.cutover.cli migrate --reset
python -m app.cutover.cli validate
```

Comportamento:

- `--reset` faz `TRUNCATE` em todas as tabelas alvo do `davinci` (com
  `RESTART IDENTITY CASCADE`). Use **apenas** se o schema novo só tem dados de
  bootstrap (admin owner) — perde tudo que estiver dentro.
- Tradução de IDs: cada linha do legacy ganha um `uuid` novo; um mapa em
  memória resolve as FKs entre tabelas.
- **Credenciais**: por default, todas as `integrations.credentials` são
  zeradas (re-cifradas com payload `{"_cleared_at_cutover": true}`) e o status
  vira `disconnected`. Depois do cutover, **cada usuário precisa reconectar
  Bling/ML/Shopee/Amazon via OAuth**. Para tentar reaproveitar (não
  recomendado se a `CREDENTIALS_KEY` mudou), passar `--keep-credentials`.
- **Senhas em `pricing_accounts.password` / `store_info.password`**: cifradas
  com `CredentialsCipher` (AES-GCM) no campo `password_enc`.
- **Sync logs e sync_queue**: não migrados — ruído histórico, o app novo
  reconstrói. Idem `freight_recon`, `devolucoes`, `auth_codes`,
  `password_reset_tokens` (auth e reconciliação reformulados).

---

## 3. Validar contagens

`./scripts/cutover-run.sh --validate` imprime tabela markdown:

```
| table              | legacy | new | diff | ok |
| users              |     12 |  12 |    0 | ✓  |
| integrations       |     34 |  34 |    0 | ✓  |
| products           |   8421 | 8421 |   0 | ✓  |
| product_links      |  20133 | 19987 | 146 | ✗  |  ← investigar
...
```

Diferenças aceitáveis (a `validate.py` marca como `✗` mesmo assim — confirmar
no relatório de `migrate`):

| Tabela | Razão |
|---|---|
| `product_links` | linhas com `platform ∈ {tiktok, temu, aliexpress}` são descartadas (enums não suportados no novo app) |
| `listings` / `listing_requests` | idem |
| `integrations` | idem (qualquer integração não-bling/ml/shopee/amazon é descartada) |

O resumo do `migrate` (impresso no console) dá o motivo exato por linha
descartada (`reasons={"platform_tiktok_dropped": 146}`). **Anexe esse output
ao changelog do cutover.**

---

## 4. Apontar webhooks Bling para a nova URL

URL nova: `https://api.hadken.com/api/webhooks/bling`

Não há API pública do Bling para reconfigurar webhooks programaticamente — é
manual no painel:

1. Logar em https://www.bling.com.br/ com a conta do dono
2. **Preferências → Integrações → API → Webhooks**
3. Para cada webhook ativo apontando para o domínio antigo, atualizar URL
   para `https://api.hadken.com/api/webhooks/bling`
4. Manter o campo *Secret* idêntico ao `BLING_WEBHOOK_SECRET` na VPS (HMAC
   SHA-256 sobre o body) — verificável em
   `GET https://api.hadken.com/api/settings/webhook-url`
5. Se algum webhook estiver desabilitado, deletá-lo
6. Disparar um evento de teste via Bling (alterar estoque de um produto) →
   verificar nos logs do DaVinci:

```bash
ssh davinci 'docker compose logs api --tail=50 | grep bling_webhook'
```

> Eventos cobertos pelo handler: `estoque.alteracao`, `produto.alteracao`,
> `produto.exclusao` (ver `app/routers/webhooks.py`).

---

## 5. Reconfigurar redirect URI Mercado Livre

ML não permite mais de uma redirect URI por aplicação (só uma "principal"). Faz
manual no painel:

1. https://developers.mercadolivre.com.br/devcenter → app DaVinci → **Editar**
2. Atualizar **Redirect URI** para `https://app.hadken.com/api/oauth/ml/callback`
3. Salvar
4. Testar fluxo: `/companies/<id>` → "Conectar OAuth" no card ML → completar
   o fluxo → `integrations.status` deve ficar `active` no DaVinci

> Os tokens antigos do ML do app antigo **não são reaproveitáveis**: a Manus
> AI não compartilha a `CREDENTIALS_KEY` e o ML invalida refresh tokens em
> mudança de redirect URI. Cada conta precisa reconectar.

Mesmo procedimento para Shopee (Open Platform → app → Domains/Callbacks) e
Amazon SP-API (Developer Profile → app → URLs autorizadas).

---

## 6. Avisar usuários para reconectar marketplaces

Após validações de contagem OK:

1. Marcar `users.status='pending'` para todo mundo? **NÃO** — interrompe
   acesso. Eles continuam `active`, só veem banner.
2. Adicionar banner global ou alerta persistente ("Reconecte suas
   integrações") — feito automaticamente: a cada `integration` em status
   `disconnected`, o handler de alertas dispara `token_expiring` (ver
   `app/services/alerts.py`). Após o cutover, todas estarão `disconnected` →
   alerta automático na sidebar.
3. Mensagem no Threema/email: "DaVinci v2 no ar em https://app.hadken.com.
   Logue com seu e-mail (OTP) e reconecte Bling/ML/Shopee/Amazon na página
   Empresas."

---

## 7. Manter app antigo em modo read-only por 30 dias

O app antigo continua hospedado na Manus AI por 30 dias para janela de
auditoria/rollback. Para impedir gravações:

```sql
-- conectar no Postgres do app antigo (Manus AI)
SET search_path TO stocksync;

-- revoga DML do role da aplicação
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
  ON ALL TABLES IN SCHEMA stocksync FROM stocksync_app;

-- revoga uso de sequences (impede inserts mesmo com SELECT)
REVOKE USAGE ON ALL SEQUENCES IN SCHEMA stocksync FROM stocksync_app;
```

Plus banner amarelo no front antigo:

> "Este app está em modo somente-leitura desde 2026-XX-XX. A versão atual está
> em https://app.hadken.com. Os dados aqui são apenas para auditoria. Em
> 2026-YY-YY este endereço será desligado."

Calendário sugerido:

| Dia | Ação |
|---|---|
| D | Cutover, app antigo vira read-only |
| D+7 | E-mail de lembrete da migração |
| D+14 | Banner mais agressivo no app antigo |
| D+25 | E-mail "será desligado em 5 dias" |
| D+30 | Desligar containers do app antigo, manter só backups |

---

## 8. Rollback (se algo der muito errado nas primeiras 24h)

1. Voltar DNS de `app.hadken.com` / `api.hadken.com` para o app antigo
2. Reabrir DML no Postgres antigo (`GRANT INSERT, UPDATE, DELETE`)
3. **Não** mexer no `davinci` schema — fica disponível para retomar
4. Comunicar usuários

Como o app antigo só foi colocado em read-only (não desligado) e nenhum write
do app novo afeta a Manus AI, rollback é direto — sem perda de dados nas
primeiras 24h.

---

## 9. Checklist final

- [ ] Counts batem (com diffs explicados)
- [ ] OTP login funciona com `OWNER_OPEN_ID`
- [ ] `/companies` mostra empresas + reconexão OAuth completa
- [ ] Webhook Bling testado (alteração de estoque chega no DaVinci)
- [ ] Sync manual roda fim-a-fim (`/sync` button → `last_sync_status='ok'`)
- [ ] Pricing push para uma conta funciona (após reconectar marketplace)
- [ ] App antigo banner ativo + DML revogado
- [ ] Telegram/email de aviso enviado para os usuários
