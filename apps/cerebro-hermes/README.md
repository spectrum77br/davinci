# IA de Chamado (Hermes no Mac Santiago)

Vinicius, 24/09/2026: o cérebro dos chamados sai do computador do Eduardo e
passa a ser o **Hermes Agent** que roda no Mac Santiago (`ssh mac-robo`). Na aba
**Chamados › IA de Chamado** ele se chama "IA de Chamado": lá a pessoa liga/
desliga, escreve o **manual** ("quando acontecer isso → faça isso") e vê o que a
IA decidiu. Sem modo teste — ligada, ela decide de verdade.

```
DaVinci (nuvem)                                   Hermes (Mac Santiago)
  aba IA de Chamado ── liga/desliga + manual
  /api/chamados/agent/cerebro  ◄── precheck ───  agendador, a cada 1 min
  /api/chamados/agent/analisar ◄──┘              desligada ou sem caso novo
                                                  → a IA nem acorda (custo zero)
  /api/chamados/agent/analise  ◄── decidir ────  a IA decide: instrução > manual
  /api/chamados/agent/caso     ◄── caso ───────  > bom senso (skill ia-de-chamado)
```

## Peças

| Onde (no Santiago) | O quê |
|---|---|
| `~/.hermes/scripts/davinci_chamados.py` | cópia de `scripts/davinci_chamados.py` — a única coisa que fala com o DaVinci; sem argumento = pré-rodada do agendador |
| `~/.hermes/scripts/adspower.py` | cópia de `scripts/adspower.py` — acha o perfil do AdsPower da loja ("Loja - Plataforma"), abre e fecha pela Local API |
| `~/.hermes/scripts/tela.py` | cópia de `scripts/tela.py` — a IA NA TELA: chamado → perfil → abre → `hermes -z -t browser` com `BROWSER_CDP_URL` do perfil → fecha. Padrão só leitura; `--pode-agir` libera |
| `~/.hermes/scripts/evidencia.py` + `shopee_evidencia.mjs` | cópias de `scripts/` — o "Upload Evidence" da Shopee (2ª disputa): baixa as fotos do chamado, a IA escolhe até 3 (visão), o `.mjs` (puppeteer do `~/DaVinci/executor-leitura-chamado`) anexa, clica Enviar, confirma "Contestar Shopee" e confere a linha; grava análise + 2 prints e apaga as fotos (25/09) |
| `~/.hermes/skills/ia-de-chamado/SKILL.md` | cópia de `skills/ia-de-chamado/SKILL.md` — as instruções fixas (o manual do Vinicius vem do DaVinci a cada passada) |
| `~/DaVinci/cerebro/.env` (chmod 600) | `DAVINCI_CEREBRO_TOKEN` e os filtros (`DAVINCI_CEREBRO_PLATAFORMA`, `_CANAIS`, `_LIMITE`) |
| `~/DaVinci/cerebro/decisoes.jsonl` | toda decisão, com a resposta do DaVinci |
| launchd `ai.hermes.gateway` | o agendador do Hermes (volta sozinho) |
| cron `ia-de-chamado-forte` (`ia_chamado_forte_direto.py`, sem agente) | a cada 1 min, **gpt-6-sol** esforço extra: instrução de pessoa (inclui a correção do ✗) e envio travado |
| cron `ia-de-chamado-simples` (`ia_chamado_simples.py`) | a cada 1 min, **gpt-6-luna** esforço extra: a plataforma respondeu, sem instrução |

**Modelo (25/09, tarde):** saiu a API da Anthropic (crédito acabando) e entrou a
**assinatura do ChatGPT** do Vinicius — provider `openai-codex` do Hermes, login por
código de dispositivo (`hermes auth add openai-codex --no-browser`; a conta precisa
de "Ativar login com código de dispositivo para Codex…" em ChatGPT › Configurações ›
Segurança e login). Sem custo por uso, mas com limite de uso do plano (Plus).
`config.yaml`: `model.default: gpt-6-sol`, `provider: openai-codex` e **sem**
`base_url` (o da Anthropic sobrando dava 404 em tudo). `tela.py`, `evidencia.py` e
`ia_chamado_forte_direto.py` fixam `gpt-6-sol`; os jobs do cron, `hermes cron edit
<id> --model … --provider openai-codex`. Backups `*.bak-20260925-chatgpt`.

A IA nunca vê o token: o script lê o `.env` sozinho. O token é da IA (tabela
`chamados_cerebros`, migração 0319 — só o sha256 no banco) e só abre as rotas do
cérebro; as mãos do Eduardo (lease, resultado, recebida) seguem com o token
antigo.

## Atualizar

```bash
scp apps/cerebro-hermes/scripts/{davinci_chamados,tela,evidencia,adspower}.py \
    apps/cerebro-hermes/scripts/shopee_evidencia.mjs mac-robo:.hermes/scripts/
scp apps/cerebro-hermes/skills/ia-de-chamado/SKILL.md mac-robo:.hermes/skills/ia-de-chamado/
```

## Cérebro antigo

Travado desde 24/09 (`exclusivo`): o token antigo recebe lista vazia no
`/agent/analisar` e 409 no `/agent/analise`. `davinci_chamados.py guarda liberar`
desfaz. Ver §7 de `docs/robo-chamados-v2.md`.

## Remendo local no Hermes (25/09)

`~/.hermes/hermes-agent/tools/browser_tool.py` (`_capture_vision_screenshot`):
tirado o `--full` da captura do `browser_vision`. A lista de Retornos da Shopee
dava 7616×37218 px — o Pillow recusa reduzir (decompression bomb) e a Anthropic
recusa a imagem (>10 MB), derrubando a tarefa na tela. Agora a captura é só a
parte visível; a IA rola a página. Original em `browser_tool.py.orig-20260925`.
**Um `hermes update` desfaz** — reaplicar depois de atualizar.
