# IA de Chamado (Claude Code)

A partir de 29/09/2026 a IA de Chamado do DaVinci é o **Claude Code**, rodando no
**Mac Santiago** (o Mac do robô). Ela substitui o Hermes com a mesma identidade:
na aba Chamados › IA de Chamado tudo continua igual (liga/desliga, manual, ✓/✗).

## Como funciona

A cada 10 minutos o despertador do Mac roda o `rodada.sh`:

1. Um programinha pergunta ao DaVinci se tem trabalho (sem IA, não gasta nada).
2. Não tem → volta a dormir.
3. Tem → acorda o Claude com o manual e os chamados. Ele decide cada um,
   faz o que precisa (pela API ou pela tela da loja no AdsPower) e registra no
   próprio chamado.
   - Coisa que precisa pensar (instrução de pessoa, fala presa, tela): **Opus 5.5,
     esforço alto**.
   - Coisa simples (a plataforma só respondeu): **Sonnet 5.5**.

Captcha, login ou código: a IA **para**, deixa a tela aberta e chama uma pessoa.

## Onde ver o que ela fez

- **No DaVinci**, no histórico de cada chamado (assinado "IA de Chamado").
- No Mac Santiago, em `registros/` — um arquivo por dia com cada rodada.

## A pasta no Mac Santiago (`~/DaVinci/ia-de-chamado`)

| O quê | Pra quê |
|---|---|
| `CLAUDE.md` | as instruções da IA (como decidir, caminhos da Shopee, segurança) |
| `rodada.sh` | o que o despertador roda a cada 10 min |
| `ferramentas/` | falar com o DaVinci, achar/abrir o perfil do AdsPower, conduzir a tela, mandar evidência |
| `.claude/settings.json` | a lista fechada do que a IA pode fazer sozinha |
| `.env` | acesso da IA ao DaVinci (só o dono do Mac lê) |
| `.env.claude` | login do Claude, vale 1 ano (só o dono do Mac lê) |
| `claude/` | dados internos do Claude (conversas das rodadas, 30 dias) |
| `estado/` | controle interno (casos já vistos, trava da rodada, lista de perfis) |
| `registros/` | o diário das rodadas |
| `historico-hermes/` | o que ficou do Hermes (decisões e tarefas de tela até 29/09) |
| `tmp/` | fotos e prints da rodada — apagado no fim de cada rodada |

No projeto (`apps/ia-de-chamado/`) ficam só as partes que não são segredo nem
diário: `CLAUDE.md`, `rodada.sh`, `ferramentas/`, `permissoes.json` (no Mac vira
`.claude/settings.json`), o despertador (`com.davinci.ia-de-chamado.plist`) e
este LEIAME.

## Ligar e desligar

- **Pela aba IA de Chamado** (liga/desliga): desligada, a rodada não acorda ninguém.
- **O despertador** (no Mac Santiago):
  - ligar: `launchctl load ~/Library/LaunchAgents/com.davinci.ia-de-chamado.plist`
  - desligar: `launchctl unload ~/Library/LaunchAgents/com.davinci.ia-de-chamado.plist`

## Mudar o jeito dela trabalhar

Regra de negócio ("QUANDO → FAÇA") vai no **manual**, na aba IA de Chamado. Mudança
nas instruções (`CLAUDE.md`) ou nas ferramentas é feita no projeto, junto com o
Vinicius no Claude Code, e copiada pro Mac Santiago. A IA não muda as próprias
instruções sozinha — quando aprende algo, deixa uma "sugestão de regra" no chamado.
