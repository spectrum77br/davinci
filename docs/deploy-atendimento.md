# Deploy do atendimento unificado (/atendimento) — roteiro

Preparado em 30/09/2026. Nada disto foi commitado nem publicado. Cada passo que
mexe em produção (commit, deploy, migration, `.env`, carga no banco) só com o
Eduardo confirmando.

## 0. O que sobe e em que estado

- **O que sobe:**
  - a caixa `/atendimento`: Shopee, ML, TikTok, Amazon por e-mail, Magalu e Temu/AliExpress pelo robô;
  - a IA que sugere a resposta;
  - o manual da IA;
  - o robô do AdsPower.
- **Acesso só admin**, travado em três lugares:
  - menu `adminOnly`;
  - página com middleware `admin`;
  - API com `SO_ADMIN = True`, que devolve 403 `admin_only` para quem não é admin.
  - O recurso saiu da tela de Permissões. As rotas `/api/atendimento/robo/*` usam só o token do robô.
- **Modo observação:**
  - o DaVinci lê as conversas e mostra o que a IA responderia;
  - nada é enviado ao comprador (`ATENDIMENTO_ENVIO_ATIVO=false`);
  - nenhuma conversa é marcada como lida;
  - o Duoke e os Seller Centers continuam respondendo como hoje.
- **Sem as linhas `ATENDIMENTO_*` no `.env`, nada roda.** As flags nascem desligadas. Isso foi testado: os 4 crons voltam sem conectar, e a rota do robô dá 404 sem token.
- **Migrations:** `0346_atendimento` cria 10 tabelas novas. `0347_atendimento_robo` só mexe nessas tabelas novas. Produção hoje está em `0345_denuncia_acesso_cairo`.
- **Dependência nova:** `anthropic` 1.9.0 (mais as dependências dela; o `idna` sobe para 3.20). Por isso é rebuild de api e de todos os workers, que o comando padrão já faz.

### Verificação feita em 30/09 (Python 3.12, o mesmo de produção)

- **Suíte inteira da API:**
  - Local: 5.964 testes passaram. As 89 falhas e 60 erros são a **mesma lista** do origin/main limpo, que já falhava antes. Nenhum teste que passa no origin falha aqui.
  - Os cerca de 1.260 testes do atendimento passam todos.
- **Web:**
  - `nuxt build` termina com EXIT 0.
  - O typecheck dá os mesmos 7 erros antigos do origin, nenhum nos arquivos do atendimento.
  - Os 4 testes `atendimento-*.cjs` passam.
- **Rotas e worker:** nenhuma rota sumiu ou mudou, só entraram as de `/api/atendimento`. O worker ganhou só 5 funções e 4 crons do atendimento.
- **Imagem:** reproduzi o Dockerfile (uv 0.5.31, `--frozen --no-dev`, Python 3.12). O `anthropic` instala, e o `app.main` e o `app.worker` importam.
- **Próxima vez:** validar no **3.12** antes de subir. O `.venv` do Mac é 3.14 e pode esconder erro.

## 1. Antes de começar

- [ ] **Horário calmo.** O rebuild e o recreate da api apagam os helpers `/app/_*.py` dos robôs (etiquetas, NF, chamados). Isso vai acontecer **duas vezes**: no passo 3 e no passo 7. Recopiar **uma vez só, no fim** (passo 11).
- [ ] **Decidir a IA:**
  - **Claude:** `ATENDIMENTO_LLM_MODEL` + `ATENDIMENTO_LLM_API_KEY`;
  - **Groq:** sem nada. Usa o `LLM_API_KEY` que já está no `.env` de produção, o mesmo do DM do Instagram.
- [ ] **Decidir** se a importação do histórico antigo (passo 12, 2 a 4 h) entra agora ou depois.
- [ ] **Não rodar** `git stash -u` nem `git checkout` no `~/davinci` antes do commit. O robô do Mac roda direto dessa pasta.
- [ ] **Container avulso.** Existe um `davinci-api-run-…` rodando em produção. O deploy não mexe nele, e ele fica com o código velho.

## 2. Commit (no Mac, em `~/davinci`)

```bash
cd ~/davinci
git fetch origin
git log --oneline HEAD..origin/main -- apps/api/alembic/versions
```

- **Se aparecer migration nova no origin:** renumerar `0346_atendimento` e `0347_atendimento_robo` para head+1 e head+2. É preciso mudar o arquivo, `revision` e `down_revision`. A primeira passa a apontar para o novo head. Depois ajustar `tests/test_atendimento_migration.py` e rodar esse teste de novo.
- **Hoje (30/09, 14:50):** o origin está 1 commit à frente (`737322df`, Magalu auto_link). Não tem migration nem arquivo em comum com os nossos.

```bash
git pull --ff-only origin main
```

Adicionar **só** estes arquivos (nunca `-A` nem `.`; o `CLAUDE.local.md` fica de fora):

```bash
git add \
  apps/api/app/config.py apps/api/app/main.py apps/api/app/worker.py \
  apps/api/app/historico/nomes.py apps/api/app/historico/sql.py \
  apps/api/app/models/__init__.py apps/api/app/models/atendimento.py \
  apps/api/app/schemas/permissions.py apps/api/app/schemas/atendimento.py apps/api/app/schemas/atendimento_robo.py \
  apps/api/app/routers/atendimento.py apps/api/app/routers/atendimento_robo.py \
  apps/api/app/services/atendimento/ \
  apps/api/app/services/marketplaces/magalu.py apps/api/app/services/marketplaces/ml.py \
  apps/api/app/services/marketplaces/shopee.py apps/api/app/services/marketplaces/tiktok.py \
  apps/api/alembic/versions/0346_atendimento.py apps/api/alembic/versions/0347_atendimento_robo.py \
  apps/api/pyproject.toml apps/api/uv.lock \
  apps/api/scripts/atendimento_manual.py apps/api/scripts/atendimento_manual_base.json \
  apps/api/scripts/atendimento_importar_historico.py apps/api/scripts/atendimento_local.py \
  apps/api/scripts/atendimento_robo_adspower.py apps/api/scripts/atendimento_robo_lojas.json \
  apps/api/scripts/atendimento_robo_adspower.plist.exemplo \
  apps/api/tests/conftest.py apps/api/tests/test_historico_regras.py \
  apps/api/tests/test_atendimento_*.py \
  apps/web/components/AppSidebar.vue apps/web/composables/useCan.ts \
  apps/web/components/Atendimento*.vue apps/web/pages/atendimento.vue \
  apps/web/public/atendimento-demo/ \
  apps/web/tests/atendimento-*.cjs \
  docs/atendimento-unificado.md docs/atendimento-magalu.md docs/atendimento-aliexpress-temu.md \
  docs/atendimento-manual-base.md docs/deploy-atendimento.md
git status --short   # conferir: só sobram CLAUDE.local.md, apps/api/_*.py e apps/web/middleware/xml py/
```

- **`atendimento_manual_base.json` é obrigatório.** Sem ele, o passo 6 falha em produção.
- **`atendimento_robo_lojas.json`** tem só o id do perfil do AdsPower, a plataforma, a loja e a URL do Seller Center. Nenhum segredo.

```bash
git commit -m "feat(atendimento): caixa unificada de mensagens com IA (só admin, modo observação)

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin main
```

## 3. Deploy (flags ainda desligadas; `.env` sem mudança)

```bash
ssh davinci-prod "cd /opt/davinci && git fetch origin && git reset --hard origin/main && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build api web worker worker_financials worker_marketing_agent worker_marketplace worker_sync worker_ui"
ssh davinci-prod "docker ps --format '{{.Names}} {{.Status}}' | grep davinci"
```

- Tudo tem que aparecer `Up`, a api `(healthy)` e nenhum `Restarting`.
- **Até o passo 4,** `/api/atendimento/*` responde 500 porque as tabelas ainda não existem. Só admin vê essa tela.
- **Espaço em disco:** 85% usado, 11 GB livres. Cabe o rebuild.

## 4. Migration

```bash
ssh davinci-prod "docker exec davinci-api-1 uv run alembic upgrade head"
```

- Aplica a 0346 e depois a 0347, numa transação só.
- Nas tabelas que já existem entra só a chave estrangeira para `integrations` e `users`, com `lock_timeout` de 3 s. Se der `lock timeout`, é só rodar de novo.

Conferir (psql só leitura, começando com `set search_path=davinci;`):

```sql
select version_num from alembic_version;                       -- 0347_atendimento_robo
select count(*) from information_schema.tables
 where table_schema='davinci' and table_name like 'atendimento\_%';   -- 10
```

## 5. Gatilho do Histórico nas tabelas novas

Os workers subiram antes da migration. Sem este passo, o Histórico das tabelas `atendimento_canais`, `atendimento_regras`, `atendimento_modelos` e `atendimento_categorias` só começa às 03:40 do dia seguinte.

```bash
ssh davinci-prod "docker exec davinci-api-1 uv run python -m app.historico.instalar"
```

- Deve responder `gatilho posto em 4 tabela(s)`. Pode rodar de novo sem problema.
- Conferir: a consulta abaixo deve listar essas 4 tabelas.

```sql
select c.relname from pg_trigger t join pg_class c on c.oid=t.tgrelid
 join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='davinci' and t.tgname='historico_captura' and c.relname like 'atendimento%';
```

## 6. Manual da IA (o contexto)

Importar **só** o arquivo do repositório. **Nunca** exportar do banco local: ele tem sobras da semente de teste (o assunto `duvida_produto` e 5 respostas inventadas).

```bash
ssh davinci-prod "docker exec davinci-api-1 uv run python -m scripts.atendimento_manual importar scripts/atendimento_manual_base.json --seco"
ssh davinci-prod "docker exec davinci-api-1 uv run python -m scripts.atendimento_manual importar scripts/atendimento_manual_base.json"
```

- **Primeiro comando (`--seco`):** tem que dar 42 assuntos, 64 regras e 48 respostas prontas novas, e código de saída 0.
- **Segundo comando:** deve responder `Gravado.`.
- **Segurança:** o comando é idempotente, nunca apaga nada, e se achar erro não grava nada.

```sql
select (select count(*) from atendimento_categorias where ativa),
       (select count(*) from atendimento_regras where ativa),
       (select count(*) from atendimento_modelos where ativo),
       (select count(*) from atendimento_categorias where so_humano);   -- 42|64|48|15
select tipo, count(*) from atendimento_regras where ativa group by 1 order by 1;
   -- categoria 44 | estilo 4 | seguranca 16
select count(*) from atendimento_categorias where id='duvida_produto';   -- 0
```

## 7. `.env` de produção e recriar api + worker

**Como foi feito em 30/09:** o bloco pronto, com o token do robô e a senha de app do Gmail já preenchidos, fica no Mac em `~/.davinci/env_atendimento_prod.txt` (chmod 600).

- O Eduardo cola esse bloco no fim do `/opt/davinci/.env` **antes do deploy**, com `ATENDIMENTO_LEITURA_ATIVA=false` e `ATENDIMENTO_IA_ATIVA=false`.
- Assim, o passo 3 já sobe com o token do robô, a Amazon e a IA configurados, mas sem ler nada.
- Neste passo só viram `true` as duas flags:

```bash
ssh davinci-prod "cd /opt/davinci && cp -p .env /root/env-antes-atendimento-ligar && sed -i 's/^ATENDIMENTO_LEITURA_ATIVA=false$/ATENDIMENTO_LEITURA_ATIVA=true/; s/^ATENDIMENTO_IA_ATIVA=false$/ATENDIMENTO_IA_ATIVA=true/' .env && grep -c '^ATENDIMENTO_\(LEITURA\|IA\)_ATIVA=true' .env"
```

O último comando deve responder `2`. Referência do que o bloco tem, e do que acontece sem cada linha:

| Variável | Valor para a observação | Segredo? | Sem ela |
|---|---|---|---|
| `ATENDIMENTO_LEITURA_ATIVA` | `true` | não | não lê nada |
| `ATENDIMENTO_IA_ATIVA` | `true` | não | sem sugestão da IA |
| `ATENDIMENTO_IA_MODOS` | `observar,copiloto` | não | **o padrão é `copiloto,auto`: a IA fica calada nas lojas em observar** |
| `ATENDIMENTO_ENVIO_ATIVO` | `false` | não | (padrão false) nada sai para o comprador |
| `ATENDIMENTO_AUTO_ATIVO` | `false` | não | (padrão false) |
| `ATENDIMENTO_ALERTA_TELEGRAM` | `false` | não | (padrão false) |
| `ATENDIMENTO_SYNC_CONCORRENCIA` | `2` no primeiro dia (padrão 4) | não | 4 canais ao mesmo tempo |
| `ATENDIMENTO_SYNC_MAX_CONVERSAS` | `15` no primeiro dia (padrão 40) | não | até 40 conversas por canal por rodada |
| `ATENDIMENTO_IA_TETO_DIARIO` | `600` na primeira semana (padrão 1000) | não | teto de 1000 chamadas por dia |
| `ATENDIMENTO_ROBO_TOKEN` | o mesmo valor do `~/.davinci/robo_atendimento.token` do Mac mini | **sim** | a rota do robô dá 404 e o robô não entrega |
| `ATENDIMENTO_LLM_BASE_URL` | `https://api.groq.com/openai/v1` (decisão 30/09: Groq por enquanto) | não | herda o `LLM_BASE_URL` do DM; com Claude é ignorado |
| `ATENDIMENTO_LLM_MODEL` | `openai/gpt-oss-120b` (Groq). Para trocar para Claude: `claude-opus-5-5` ou `claude-sonnet-5-5` | não | herda o `LLM_MODEL` do DM |
| `ATENDIMENTO_LLM_API_KEY` | chave do Groq (`gsk_…`). Para Claude: `sk-ant-…` | **sim** | usa o `LLM_API_KEY` do DM, que já está no `.env` |
| `ATENDIMENTO_AMAZON_IMAP_HOST` | `imap.gmail.com` | não | a Amazon fica "desligado" |
| `ATENDIMENTO_AMAZON_IMAP_USUARIO` | o Gmail do atendimento | não | idem |
| `ATENDIMENTO_AMAZON_IMAP_SENHA` | senha de app do Gmail | **sim** | idem |
| `ATENDIMENTO_AMAZON_SMTP_HOST` | `smtp.gmail.com` | não | só serve para enviar (desligado agora) |
| `ATENDIMENTO_AMAZON_SMTP_USUARIO` | o Gmail do atendimento | não | idem |
| `ATENDIMENTO_AMAZON_SMTP_SENHA` | senha de app do Gmail | **sim** | idem |
| `ATENDIMENTO_AMAZON_REMETENTE` | o Gmail do atendimento | não | idem |

- **As 7 linhas da Amazon** são as mesmas do `apps/api/.env` do Mac, que foram usadas no teste da KFA. O SMTP entra já para evitar outro recreate quando o envio for ligado.
- **Nunca** pôr `ATENDIMENTO_SIMULADOR=true` em produção.
- **Para trocar do Groq para a Claude:** mudar o `ATENDIMENTO_LLM_MODEL` e o `ATENDIMENTO_LLM_API_KEY`, e recriar a api e o worker. Com Claude o endereço é fixo, e o `ATENDIMENTO_LLM_BASE_URL` é ignorado.
- **`ATENDIMENTO_SIMULADOR_EXCETO`** é só para o teste local. Não pôr.
- **Opcionais que já têm padrão:** `ATENDIMENTO_AMAZON_IMAP_PORT=993`, `ATENDIMENTO_AMAZON_IMAP_PASTA=INBOX`, `ATENDIMENTO_AMAZON_SMTP_PORT=587`, `ATENDIMENTO_ROBO_PARADO_MIN=5`.
- **Se quiser ir mais devagar:** subir primeiro só com `ATENDIMENTO_IA_ATIVA=false`, olhar a carga, e ligar depois. Mas cada troca de flag é mais um recreate da api, e os helpers somem de novo.

```bash
ssh davinci-prod "cd /opt/davinci && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate api worker"
ssh davinci-prod "docker ps --format '{{.Names}} {{.Status}}' | grep davinci"
```

- Só a api e o worker padrão leem essas flags.
- O `--force-recreate` não reconstrói a imagem. Só recarrega o `.env`.

## 8. Canais (sem comando)

- **Canais das lojas:** o `garantir_canais` cria sozinho, na primeira rodada da leitura (minutos ímpares), já em `observar`.
- **Esperado:** 75 canais de API: Shopee 14, ML 23 × 2, TikTok 8, Amazon 4 e Magalu 1 × 3.
- **Canais do robô:** os 5 nascem no primeiro pulso dele (passo 9).

```sql
select plataforma, canal, modo, count(*) from atendimento_canais group by 1,2,3 order by 1,2,3;
```

## 9. Robô do Mac mini apontando para produção

Hoje o robô manda para a API local (`http://127.0.0.1:8011`).

```bash
sed -i '' 's#http://127.0.0.1:8011#https://app.hadken.com#' ~/Library/LaunchAgents/com.davinci.robo-atendimento.plist
launchctl unload ~/Library/LaunchAgents/com.davinci.robo-atendimento.plist
launchctl load ~/Library/LaunchAgents/com.davinci.robo-atendimento.plist
tail -f ~/Library/Logs/davinci-robo-atendimento.log   # "lendo" nas 5 lojas, sem 401/404
```

- **Token:** continua lendo o `~/.davinci/robo_atendimento.token`. Por isso o `ATENDIMENTO_ROBO_TOKEN` de produção tem que ser esse mesmo valor.
- **Ordem:** só fazer depois do passo 7. Antes disso a produção responde 404, e o robô registra erro e tenta de novo.
- **Corpo dos envios:** os lotes do robô têm até 6 MB. O Caddy de produção não limita o tamanho, e não tem Cloudflare na frente.

## 10. Conferência depois do deploy

- [ ] **Containers:** `docker ps` com tudo `Up`, api `(healthy)`, nenhum `Restarting`.
- [ ] **Worker:** `docker logs davinci-worker-1 --since 15m 2>&1 | grep -E 'atendimento_sincronizar|Traceback' | tail`. A rodada tem que terminar bem antes de 2 minutos.
- [ ] **Renovação de tokens:** `docker logs davinci-worker-1 --since 1h 2>&1 | grep -E 'token_refresh_(ok|failed|skipped)' | sort | uniq -c`.
  - Duas falhas do ML com `invalid client_id or client_secret` já existiam antes. Não são deste deploy.
  - `token_refresh_skipped_locked` só aparece com a leitura ligada, e é normal.
- [ ] **Cota das plataformas:** `docker logs davinci-worker-1 --since 1h 2>&1 | grep -cE 'ml_retry|magalu_retry|429'`. Comparar com o número de antes.
- [ ] **Pedidos:** o `ingest_bling_order_run` continua no mesmo ritmo. O atendimento divide as 10 vagas do worker padrão com ele.
- [ ] **Como admin:** o menu Atendimento aparece, as conversas chegam e a caixa "O que a IA responderia" aparece.
- [ ] **Como não-admin:** o menu não aparece, `/atendimento` vai para `/403` e a API dá 403 `admin_only`.
- [ ] **Nada enviado:**

  ```sql
  select count(*) from atendimento_mensagens where origem in ('davinci_humano','davinci_ia');   -- 0
  ```

  As respostas dadas pelo Duoke ou pelo Seller Center aparecem como `origem='externo'`, e isso é normal.
- [ ] **Nenhuma conversa marcada como lida:** o não-lido no Duoke e nos Seller Centers continua igual.
- [ ] **Gasto da IA:** se for Claude, acompanhar no console.anthropic.com e no log com `grep atendimento_ia`.

## 11. Helpers

Avisar o Eduardo para **recopiar os `/app/_*.py`** no `davinci-api-1`. Foram apagados no passo 3 e de novo no passo 7.

## 12. (Opcional, só com aprovação) Histórico antigo e exemplos da IA

- **Para que serve:** é a única fonte do passado do cartão Cliente da Shopee.
- **Como é:** retomável e idempotente. O `--seco` leva de 30 a 50 min, e a importação completa de 2 a 4 h.
- **Quando e onde:** fora do horário de pico, pelo `docker exec`, e não enfileirado no worker, para não ocupar uma vaga por horas.

```bash
ssh davinci-prod "docker exec davinci-api-1 uv run python -m scripts.atendimento_importar_historico --seco"
```

Depois: uma loja, conferir, e em seguida todas.

## 13. Voltar atrás (rollback)

Do mais leve para o mais pesado:

1. **Parar a leitura e a IA sem mexer no código.** No `.env`: `ATENDIMENTO_LEITURA_ATIVA=false` e `ATENDIMENTO_IA_ATIVA=false`. Depois:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate api worker
   ```

   Os crons param na hora. As tabelas e os dados ficam.
2. **Parar o robô.**

   ```bash
   launchctl unload ~/Library/LaunchAgents/com.davinci.robo-atendimento.plist
   ```

   Ou apagar o `ATENDIMENTO_ROBO_TOKEN`: a rota passa a dar 404.
3. **Tirar o código.** `git revert <commit>`, push e o deploy do passo 3.
   - As tabelas `atendimento_*` ficam no banco, e isso é inofensivo: nada fora do atendimento lê essas tabelas.
   - O `alembic_version` fica em 0347, sem os arquivos. Um deploy futuro com migration nova vai reclamar. Por isso, **antes** do revert, rodar o downgrade do item 4.
4. **Apagar as tabelas** (só se decidir abandonar; perde tudo o que o atendimento gravou):

   ```bash
   ssh davinci-prod "docker exec davinci-api-1 uv run alembic downgrade 0345_denuncia_acesso_cairo"
   ```

   Rodar com o código do atendimento ainda no ar, porque o downgrade precisa dos arquivos 0346 e 0347.

## 14. Riscos conhecidos e mitigação

| Risco | Gravidade | Mitigação |
|---|---|---|
| **Leitura em escala real nunca rodou** (75 canais de 2 em 2 min). O ML sozinho faz cerca de 2,8 mil chamadas por hora, só para verificar se há novidade, na mesma cota de pedidos e estoque (estimativa). | média | Concorrência 2 e 15 conversas no primeiro dia. Acompanhar 429 e retries. Se precisar, desligar a leitura (item 1 do rollback). |
| **O atendimento divide o worker padrão** (10 vagas) com o ingest de pedidos. | média | Olhar o tempo da rodada e do ingest. Se atrasar, a solução é código novo, ainda não feito: um worker próprio para o atendimento, como o `worker_financials`. |
| **Custo da IA com Claude.** | média | Teto diário de 600 chamadas. Acompanhar o console. O Sonnet 5.5 é mais barato. |
| **A renovação de token de Shopee, ML e TikTok ganhou uma trava no Redis**, e isso vale para todas as lojas, mesmo com o atendimento desligado. | baixa | Está testada. Sem Redis, renova como antes. Conferir o `token_refresh` no log. |
| **Magalu:** os escopos de perguntas, chat e SAC podem ficar pendentes, e as chamadas passam pelo proxy do Mac. | baixa | Os canais ficam "sem permissão" e tentam de hora em hora. O proxy já libera `services.magalu.com`. |
| **O TikTok da Poofy e da Inova** ainda está no app antigo, sem Customer Service. | baixa | Esses canais ficam "sem permissão" até trocar o app. As outras 6 lojas já leem. |
| **O ML do "lucas mei"** está com o token vencido, e o da Poofy dá 403 nas mensagens. | baixa | Reconectar em Integrações. Os outros canais não são afetados. |
| **Log do worker cresce** uns 2,3 mil linhas por hora. | baixa | O Docker guarda 10 MB × 3 por container. Só diminui o histórico de log disponível. |
