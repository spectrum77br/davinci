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

## Uma máquina, uma fila (`EXECUTOR_FILAS`)

O mesmo código roda em mais de um Mac; cada um liga só o que faz:

| Máquina | `EXECUTOR_FILAS` | O que faz |
|---|---|---|
| executor do Eduardo | `shopee,tuta` (default) | anúncios/Oferta Relâmpago da Shopee + caixa do Tuta |
| Mac Santiago (desde 24/09/2026) | `melhorenvio,tiktok` | "Suspender entrega" da Logística + pedido de senha no chat da TikTok (29/09) |
| quem for coletar a Conferência | `…,conferencia` | Conferência Shopee (06/10/2026): lê afiliados, Ads, vendas e saldo de cada loja — ver abaixo |

O servidor só entrega a suspensão (`melhorenvio_suspender`) pra quem a declara
no lease — executor sem `acoes` (versão antiga) não pega mais. Ligue
`melhorenvio` em **uma** máquina só. O sinal de vida também é separado: o de
quem faz `shopee` acende o badge "Executor local" do Marketing; o de quem faz
`melhorenvio` vai pra Ouvidoria › Robôs ("Vigia Robô Melhor Envio"), que avisa
quando o robô some, o AdsPower fecha ou uma suspensão fica parada/falha.

## Senha do comprador na TikTok (`tiktok`, desde 29/09/2026)

Devolução lançada como **Bloqueado** numa loja TikTok: o DaVinci cria a tarefa
`tiktok_senha` (um minuto depois do lançamento, pra as fotos subirem) e este
executor pede a senha ao comprador **no chat da loja**, porque a API da TikTok
não deixa (falta o escopo de atendimento). O texto é o mesmo da Shopee.

1. Acha o perfil da loja pelo nome: conta "TikTok Barbosa" → perfil
   "Barbosa - Tiktok" (grupo das lojas). O que não casar vai no
   `TIKTOK_PERFIS_EXTRA` (ex.: `{"Loja 206081932": "k1dkfg0l"}` — JLAS).
2. Perfil **aberto** = alguém usando: não mexe, devolve "perfil em uso" e o
   DaVinci tenta de novo no :25 (não gasta tentativa).
3. Página do pedido → link do chat daquele comprador (`mGetContactBuyerLinkByOrder`)
   → recusa os cookies opcionais → confere a conversa (cabeçalho = comprador e
   painel com `#pedido`) → se o pedido de senha **já está no chat**, só avisa
   → escreve pelo `value` da caixa (nunca teclado) → confere de novo → Enviar
   → anexa a foto (janela "Enviar fotos" › Enviar) → fecha o perfil e apaga a
   foto do disco.
4. `TIKTOK_CALIBRATED` diferente de `true` = modo seco: faz tudo até antes do
   Enviar e devolve o que faria (não gasta tentativa).

O resultado volta pra linha da aba Devoluções ("Senha pedida ao cliente …" ou
"Senha: na fila — <motivo>") e, quando sai, vira evento no chamado.

## Conferência Shopee (`conferencia`, desde 06/10/2026)

Relatório de terça e quinta (documentação completa em `docs/conferencia-shopee.md`).
O DaVinci cria uma **coleta por loja**; a máquina com `conferencia` no
`EXECUTOR_FILAS` pede **uma loja por ciclo** (`POST
/api/marketing/conferencia-shopee/agent/lease`), depois das outras filas, e
devolve o resultado (`POST …/agent/coletas/{id}/resultado`). Só **lê**: nada
muda na Shopee. Ligue em **uma** máquina só (a que tem os perfis das lojas).

Para cada loja (`src/conferencia.ts`):

1. Passou do corte (17:30, ou 3 h depois de uma execução manual à noite) →
   `erro` sem abrir nada.
2. Perfil **aberto** = alguém usando: espera 7 s × 3; continua aberto →
   `perfil_em_uso` e **não mexe nele** (o DaVinci tenta de novo em 10 min, até
   3 vezes — só as voltas de perfil em uso contam; as de afiliados, não). Só
   fecha perfil que ele mesmo abriu.
3. `caffeinate -i -w <pid>` enquanto coleta a loja (o Mac não dorme no meio).
4. Abre o perfil, **aba nova** (não usa as abas que já estavam lá), Central do
   Vendedor, confere o login (`/api/v2/login/` — só username, shopid e nome da
   loja saem da página, mais o código/mensagem de erro; os tokens nunca vão
   pro log nem pro DaVinci). 403/429/captcha já nessa chamada → `bloqueada`
   na hora, sem recarregar. Sem resposta (rede) duas vezes → `erro`.
   Deslogada: recarrega uma vez; continua deslogada e o job permite
   (`login_auto`) → se a tela de login é da **própria Shopee**
   (accounts/seller/shopee.com.br, https) e já tem **usuário e senha
   preenchidos pelo perfil**, dá **um** clique em "Entrar" (nunca digita
   nada). Tela de outro site, pediu código, OTP ou captcha, ou não tem os
   campos preenchidos → `deslogada`.
5. Afiliados do último dia (domingo na terça, ontem na quinta) ainda não
   publicados e antes das 15:00 → `aguardando_afiliados` (fecha o perfil; o
   DaVinci devolve pra fila em 10 min). Depois das 15:00 coleta assim mesmo,
   com o aviso "afiliados só até dd/mm". Semana sem **nenhum** dia na lista
   (loja sem venda de afiliado) não espera: `afiliados_ultimo_dia` vai null.
6. 4 semanas × (afiliados, itens de afiliados, Ads, anúncios de Ads, vendas
   por dia) + saldo de Ads: uma chamada por vez, de dentro da página logada,
   com 1,2–1,8 s de pausa — cerca de 50 a 90 chamadas, 2 a 4 minutos por loja.
7. Fecha a aba, desconecta e fecha o perfil (`adspower.stop`), e espera o
   AdsPower largar o perfil (até 20 s).

| Status | Quando |
|---|---|
| `ok` | todas as partes vieram |
| `parcial` | alguma chamada falhou (não bloqueio): só aquela parte fica sem dados, motivo em `avisos` |
| `deslogada` | a loja caiu no login (ou pediu código/captcha) |
| `perfil_em_uso` | perfil aberto por outra pessoa (volta pra fila) |
| `aguardando_afiliados` | afiliados do último dia ainda não saíram (volta pra fila) |
| `sem_automacao` | perfil que o puppeteer não controla (núcleo Firefox) |
| `bloqueada` | a Shopee respondeu 403/429 ou pediu captcha/verificação no meio — para na hora |
| `interrompida` | o relógio pulou mais de 2 min entre dois passos (o Mac dormiu) — a loja é descartada |
| `erro` | qualquer outra coisa (AdsPower não abriu, corte, nenhuma parte veio) |

Com o AdsPower fora do ar o executor **nem pede loja** (senão a execução
inteira virava `erro` em segundos). Se a entrega do resultado falhar por rede
ou 5xx, tenta mais 2 vezes (10 s e 20 s); se mesmo assim não for, o DaVinci
devolve a loja pra fila sozinho depois de 20 min. Se o DaVinci recusar os
números (422: formato que ele não reconhece), manda só o status `erro` com o
motivo — a loja fecha em vez de abrir o perfil de novo.

**Sinal de vida:** máquina só com `conferencia` não manda heartbeat (o do
Marketing acenderia o badge "Executor local" com a Shopee parada). O
acompanhamento é a própria tela da Conferência, pelo andamento das coletas.

**Uma loja ocupa o ciclo inteiro** (2–4 min): na máquina que também faz
`shopee`, um pause/resume que chegar nesse meio espera a loja acabar. As
coletas param às 17:30, antes do robô de horários dos anúncios (18h).

Teste manual de uma loja, **sem o DaVinci** (nada é enviado):

```bash
npm start -- --teste-conferencia k1dkeaxv            # semana fechada
npm start -- --teste-conferencia k1dkeaxv --parcial  # seg até ontem
#   --login-auto       deixa clicar em "Entrar" se a loja estiver deslogada
#   --json saida.json  grava o resultado completo
```

O teste não espera os afiliados (só avisa) e respeita as mesmas regras de
perfil aberto.

Teste automático das partes puras e da coleta com uma Shopee falsa (sem
AdsPower): `npm test`.

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

Tem que ser **LaunchAgent** (sessão gráfica) para enxergar o AdsPower. O plist
roda o executor dentro do `caffeinate -i`, que impede o repouso por
inatividade enquanto ele estiver de pé (tampa fechada dorme mesmo assim).

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
| `EXECUTOR_FILAS` | `shopee,tuta` | o que esta máquina faz: `shopee`, `melhorenvio`, `tuta`, `tiktok`, `conferencia` |
| `DAVINCI_API_URL` | `http://localhost:8000` | base da API do DaVinci (sem barra no fim) |
| `MARKETING_AGENT_TOKEN` | — | token M2M; **igual** ao do DaVinci (vazio → 401) |
| `AGENT_NAME` | `marionete` | nome no badge do dashboard (e o `agente` gravado nas coletas da Conferência) |
| `LEASE_LIMIT` | `10` | comandos puxados por ciclo |
| `POLL_INTERVAL_MS` | `15000` | frequência do poll |
| `HEARTBEAT_INTERVAL_MS` | `60000` | frequência do sinal de vida |
| `PROFILE_GAP_MS` | `2000` | intervalo entre perfis (rate-limit AdsPower) |
| `EXECUTOR_DEFAULT_MODE` | `manual` | `manual` (por anúncio) ou `gmvmax` (loja inteira) |
| `EXECUTOR_DEFAULT_SCOPE` | `all` | escopo do modo manual: `all`/`ids`/`names` |
| `SELECTORS_CALIBRATED` | `false` | trava — `true` libera a ação real |
| `ADSPOWER_API_BASE` | `http://local.adspower.net:50325` | Local API do AdsPower |
| `SHOPEE_SELLER_ADS_URL` | (padrão BR) | página de Ads do Seller Center |
| `CONFERENCIA_LOGIN_AUTO` | `true` | `false` = nesta máquina a Conferência nunca clica em "Entrar", mesmo que o DaVinci permita (`login_auto`) |

## Arquivos

| Arquivo | Papel |
|---|---|
| `src/index.ts` | loop: heartbeat + lease + executar + reportar |
| `src/davinci.ts` | client HTTP do control plane (`/agent/*`) |
| `src/config.ts` | leitura do `.env` |
| `src/log.ts` | logger mínimo |
| `src/adspower.ts` | Local API do AdsPower (transplantado do marionete) |
| `src/shopee.ts` | **núcleo** da automação calibrada (transplantado do marionete) |
| `src/conferencia.ts` | Conferência Shopee: abre o perfil, confere o login, coleta uma loja |
| `src/conferencia_util.ts` | Conferência: datas, dinheiro, chamadas e payload (puro, testado) |
| `test/*.test.ts` | `npm test` — Conferência sem AdsPower (respostas sintéticas) |

## Retry / robustez

Sem estado próprio: um comando `failed` **não** mexe no `applied_state` da conta,
então o reconciler do DaVinci reenfileira no próximo minuto se o desired ainda
divergir. Se este processo cair e voltar, reconverge no ciclo seguinte.
