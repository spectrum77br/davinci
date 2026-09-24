# IA de Chamado (Hermes no Mac Santiago)

Vinicius, 24/09/2026: o cérebro dos chamados sai do computador do Eduardo e
passa a ser o **Hermes Agent** que roda no Mac Santiago (`ssh mac-robo`). Na aba
**Chamados › IA de Chamado** ele se chama "IA de Chamado": lá a pessoa liga/
desliga, escreve o **manual** ("quando acontecer isso → faça isso") e vê o que a
IA decidiu. Sem modo teste — ligada, ela decide de verdade.

```
DaVinci (nuvem)                                   Hermes (Mac Santiago)
  aba IA de Chamado ── liga/desliga + manual
  /api/chamados/agent/cerebro  ◄── precheck ───  agendador, a cada 5 min
  /api/chamados/agent/analisar ◄──┘              desligada ou sem caso novo
                                                  → a IA nem acorda (custo zero)
  /api/chamados/agent/analise  ◄── decidir ────  a IA decide: instrução > manual
  /api/chamados/agent/caso     ◄── caso ───────  > bom senso (skill ia-de-chamado)
```

## Peças

| Onde (no Santiago) | O quê |
|---|---|
| `~/.hermes/scripts/davinci_chamados.py` | cópia de `scripts/davinci_chamados.py` — a única coisa que fala com o DaVinci; sem argumento = pré-rodada do agendador |
| `~/.hermes/skills/ia-de-chamado/SKILL.md` | cópia de `skills/ia-de-chamado/SKILL.md` — as instruções fixas (o manual do Vinicius vem do DaVinci a cada passada) |
| `~/DaVinci/cerebro/.env` (chmod 600) | `DAVINCI_CEREBRO_TOKEN` e os filtros (`DAVINCI_CEREBRO_PLATAFORMA`, `_CANAIS`, `_LIMITE`) |
| `~/DaVinci/cerebro/decisoes.jsonl` | toda decisão, com a resposta do DaVinci |
| launchd `ai.hermes.gateway` | o agendador do Hermes (volta sozinho) |
| cron do Hermes `ia-de-chamado` | a passada a cada 5 min |

A IA nunca vê o token: o script lê o `.env` sozinho. O token é da IA (tabela
`chamados_cerebros`, migração 0319 — só o sha256 no banco) e só abre as rotas do
cérebro; as mãos do Eduardo (lease, resultado, recebida) seguem com o token
antigo.

## Atualizar

```bash
scp apps/cerebro-hermes/scripts/davinci_chamados.py mac-robo:.hermes/scripts/
scp apps/cerebro-hermes/skills/ia-de-chamado/SKILL.md mac-robo:.hermes/skills/ia-de-chamado/
```

## Cérebro antigo

Travado desde 24/09 (`exclusivo`): o token antigo recebe lista vazia no
`/agent/analisar` e 409 no `/agent/analise`. `davinci_chamados.py guarda liberar`
desfaz. Ver §7 de `docs/robo-chamados-v2.md`.
