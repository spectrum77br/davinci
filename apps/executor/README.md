# DaVinci Executor — o braço local (Shopee via AdsPower)

Peça que **realmente clica na Shopee**. Roda no **Mac** (do lado do AdsPower) e
é dirigida 100% pelo DaVinci: **não decide nada**, só executa. O cérebro
(agenda BRT, decisão, estado, UI) vive no DaVinci; aqui é só o músculo.

Substitui o antigo projeto standalone `~/marionete` — a lógica de automação
(`shopee.ts`, `adspower.ts`) foi transplantada; o que sumiu foi o cérebro dele
(SQLite, cron, servidor web), que agora é responsabilidade do DaVinci.

```
DaVinci (nuvem)                          Este app (seu Mac)
  agenda BRT + outbox  ──/agent/lease──►  puxa comando pendente
                                          abre o perfil no AdsPower
                                          pausa/retoma via shopee.ts
  applied_state ◄──/agent/.../result───   reporta done/failed
  badge ONLINE  ◄──/agent/heartbeat────   sinal de vida a cada 60s
```

## Por que roda no Mac (e não na nuvem)

A API oficial de Ads da Shopee está bloqueada pela cota de partner → só dá para
controlar por **navegador real**, e o **AdsPower** (com as sessões logadas das
lojas, fingerprint e IP certos) roda no seu Mac. O servidor do DaVinci não tem
nada disso. Por isso o *código* mora dentro do DaVinci (`apps/executor`), mas o
*processo* roda aqui. O modelo é **poll** (o Mac puxa do servidor): atravessa o
NAT sem abrir porta nenhuma e reconecta sozinho se a internet cair.

## Pré-requisitos

- **Node 18+** (`node -v`).
- **AdsPower aberto** com a **Local API ligada** (`http://local.adspower.net:50325`).
- Perfis do AdsPower das lojas já logados na Shopee (os `adspower_user_id`
  ficam cadastrados nas contas do DaVinci — ver `seed_marketing_shopee_accounts.py`).
- No DaVinci: `MARKETING_AGENT_TOKEN` preenchido (o mesmo valor vai no `.env` aqui).

## Setup

```bash
cd apps/executor
npm install
cp .env.example .env
# edite o .env: DAVINCI_API_URL, MARKETING_AGENT_TOKEN (igual ao do DaVinci)
```

Rodar em primeiro plano (para testar):

```bash
npm start          # tsx src/index.ts
```

Você deve ver no log: heartbeat enviado, e a cada ~15s um `lease`. Enquanto não
houver comando pendente, ele fica quieto. No dashboard do DaVinci o badge
**"Executor local"** deve ficar **ONLINE**.

## Trava de segurança (`SELECTORS_CALIBRATED`)

Enquanto `SELECTORS_CALIBRATED` **não** for `true` no `.env`, o `applyState()`
**lança erro de propósito**: o executor conecta e loga, mas **não altera nenhum
anúncio** (os comandos voltam como `failed`). Isso evita que o Mac aja sozinho
antes de você decidir. Vire para `true` quando estiver pronto.

## Rodar como serviço (liga no login, reinicia em crash)

Tem que ser **LaunchAgent** (sessão gráfica) para enxergar o AdsPower.

```bash
# 1) edite com.davinci.executor.plist: troque __DIR__ e __USER__
#    __DIR__  = caminho absoluto deste app (pwd)
#    __USER__ = seu usuário do Mac (whoami)
cp com.davinci.executor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.davinci.executor.plist
# logs:
tail -f /tmp/davinci-executor.out.log /tmp/davinci-executor.err.log
```

Parar / recarregar:

```bash
launchctl unload ~/Library/LaunchAgents/com.davinci.executor.plist
```

## Aposentar o `~/marionete` antigo

Para não terem **dois** processos mexendo nos mesmos perfis, desligue o marionete
antigo antes de ligar este:

```bash
launchctl unload ~/Library/LaunchAgents/com.marionete.plist 2>/dev/null || true
# confira que nada mais aponta pro AdsPower:
launchctl list | grep -i marionete   # deve sair vazio
```

O diretório `~/marionete` pode ficar como backup; ele não é mais usado.

## Configuração (`.env`)

| Variável | Default | Papel |
|---|---|---|
| `DAVINCI_API_URL` | `http://localhost:8000` | base da API do DaVinci (sem barra no fim) |
| `MARKETING_AGENT_TOKEN` | — | token M2M; **igual** ao do DaVinci (vazio → 401) |
| `AGENT_NAME` | `marionete` | nome no badge do dashboard |
| `LEASE_LIMIT` | `10` | comandos puxados por ciclo |
| `POLL_INTERVAL_MS` | `15000` | frequência do poll |
| `HEARTBEAT_INTERVAL_MS` | `60000` | frequência do sinal de vida |
| `PROFILE_GAP_MS` | `2000` | intervalo entre perfis (rate-limit AdsPower) |
| `EXECUTOR_DEFAULT_MODE` | `manual` | `manual` (por anúncio) ou `gmvmax` (loja inteira) |
| `EXECUTOR_DEFAULT_SCOPE` | `all` | escopo do modo manual: `all`/`ids`/`names` |
| `SELECTORS_CALIBRATED` | `false` | trava — `true` libera a ação real |
| `ADSPOWER_API_BASE` | `http://local.adspower.net:50325` | Local API do AdsPower |
| `SHOPEE_SELLER_ADS_URL` | (padrão BR) | página de Ads do Seller Center |

## Arquivos

| Arquivo | Papel |
|---|---|
| `src/index.ts` | loop: heartbeat + lease + executar + reportar |
| `src/davinci.ts` | client HTTP do control plane (`/agent/*`) |
| `src/config.ts` | leitura do `.env` |
| `src/log.ts` | logger mínimo |
| `src/adspower.ts` | Local API do AdsPower (transplantado do marionete) |
| `src/shopee.ts` | **núcleo** da automação calibrada (transplantado do marionete) |

## Retry / robustez

Sem estado próprio: um comando `failed` **não** mexe no `applied_state` da conta,
então o reconciler do DaVinci reenfileira no próximo minuto se o desired ainda
divergir. Se este processo cair e voltar, reconverge no ciclo seguinte.
