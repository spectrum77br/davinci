# Cérebro dos chamados no Hermes (Mac Santiago)

Vinicius, 24/09/2026: o cérebro dos chamados sai do computador do Eduardo e
passa a ser o **Hermes Agent** que roda no Mac Santiago (`ssh mac-robo`). O
Hermes decide o que fazer em cada chamado e, depois do piloto, também executa na
tela (AdsPower → Seller Center / central de ajuda do ML).

```
DaVinci (nuvem)                                Hermes (Mac Santiago)
  /api/chamados/agent/analisar ◄── precheck ──  agendador, a cada ~10 min
  (casos com resposta nova,                     sem caso novo → a IA nem acorda
   instrução ou bloqueio)
  /api/chamados/agent/analise  ◄── decidir ───  a IA decide pelo manual (skill)
  /api/chamados/agent/caso     ◄── caso ──────  "no chamado do pedido X, …"
```

## Peças

| Onde (no Santiago) | O quê |
|---|---|
| `~/.hermes/scripts/davinci_chamados.py` | cópia de `scripts/davinci_chamados.py` — a única coisa que fala com o DaVinci |
| `~/DaVinci/cerebro/.env` (chmod 600) | `DAVINCI_CEREBRO_TOKEN`, `DAVINCI_CEREBRO_MODO` (`seco`/`real`), filtros |
| `~/DaVinci/cerebro/decisoes.jsonl` | toda decisão, nos dois modos — é o que a gente revisa |

A IA nunca vê o token: o script lê o `.env` sozinho. O token é do cérebro Hermes
(tabela `chamados_cerebros`, migração 0319 — só o sha256 no banco) e só abre as
rotas do cérebro; as mãos do Eduardo continuam com o token antigo.

## Modo seco × real

`seco` (padrão): o Hermes decide e anota em `decisoes.jsonl`, nada vai pro
DaVinci. `real`: a decisão vira análise no histórico ("Análise do robô Hermes …").

## Troca de guarda

`python3 ~/.hermes/scripts/davinci_chamados.py guarda assumir` liga o
`exclusivo`: o cérebro do Eduardo passa a receber lista vazia e não consegue mais
decidir (as mãos dele seguem). `guarda liberar` desfaz; `guarda` só mostra o
estado. Ver §7 de `docs/robo-chamados-v2.md`.
