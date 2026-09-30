# Executor de leitura de chamado

Robô do Mac Santiago que **lê** o "Histórico da Solicitação" das devoluções da
Shopee no Seller Center — e, desde 25/09, as **consultas do Portal de
Atendimento ao Vendedor** — e devolve o texto pro chamado do DaVinci. Nasceu no
chamado 2609200FUTKM4JD (pedido 296012, 24/09/2026): a Shopee recusou a disputa
às 15:59 com uma explicação escrita, a API dela seguia dizendo "aguardando
análise", e o chamado ficou mudo.

**Só lê.** Não responde, não contesta, não avalia. A senha dele (tabela
`chamados_leitores` no DaVinci) só abre `/api/chamados/agent/leitor/fila` e
`/leitor/resultado`.

## Como funciona

1. Pergunta ao DaVinci quais devoluções reler — só das lojas com perfil no
   AdsPower deste Mac. A cadência é do DaVinci: cada caso volta de 3 em 3 h
   (24 h se ninguém fala nele há mais de 15 dias).
2. Por loja: abre o perfil **só se estiver fechado** (aberto = alguém usando;
   pula e tenta na próxima passada), lê cada caso e fecha o perfil.
3. Em cada caso: *Retornos e Pedidos cancelados* › busca pelo **nº do pedido** ›
   *Aplicar* › abre a devolução › *Histórico do chat* › *Ver detalhes*.
4. `real`: as falas do **Agente da Shopee** entram no chamado com a hora da
   tela, e a página inteira vira o "histórico" (só contexto). `seco`: grava em
   `logs/seco/` e não manda nada.

**Portal de Atendimento** (`tipo: portal`, 25/09 — 292592): chamado que o robô
abriu NA TELA pelo Portal (`chamado` = ID da consulta). Abre direto
`seller-service.cs.shopee.com.br/detail/<ID>` no perfil da loja, clica "Ver N
mais conversas" (só expande a lista) e lê cada mensagem — o texto do agente
fica num `<shadow-html data-html>` que o innerText não enxerga. "Caso
concluído" não fecha o chamado. `LEITURA_PORTAL=0` no `.env` desliga.

**v1.2 (25/09 — 294571 / 296012):**
- *Consulta ligada* (`tipo: ambos` ou `portal`): devolução acompanhada pela API
  em que a pessoa abriu À MÃO uma consulta no Portal (campo "Consulta no Portal"
  do chamado). Lê as duas e junta no mesmo chamado; se só o Portal falhar, a
  devolução vai assim mesmo e o motivo fica no histórico.
- *Pendências*: na devolução, procura o que a tela PEDE com prazo ("Evidência
  Solicitada — Envie evidências até 26-09-2026 … Upload Evidence") e manda como
  `pendencias` — vira aviso no chamado, uma vez por texto.
- O Portal só mostra a consulta pro LOGIN que abriu: aberta com outro login da
  loja, a página vem vazia e o erro diz isso ("não aparece no login …").

**v1.3 (30/09 — 298394): consulta do formulário de ajuda do Mercado Livre**
(`tipo: ml_consulta`). A parte de chamados do ML saiu do computador do Eduardo,
onde a resposta vinha pelo e-mail do Tuta. Abre direto
`mercadolivre.com.br/cases/detail/<N>` no perfil `<Loja> - Mercado Livre` e lê
cada `[data-testid="message-card"]` (autor, dia, texto). A tela só mostra o DIA
("24 de setembro"): a fala vai com `so_dia` e o DaVinci reconhece a repetida por
(texto, dia). "Finalizou" é o ML fechando o formulário, não a decisão — não
encerra nada. Fila e modo próprios: `LEITURA_ML=seco|real` no `.env` (sem nada
= desligado), independente do `LEITURA_MODO` da Shopee.

Loja → perfil: casa pelo nome do perfil (`Vortan - Shopee` → `Shopee Vortan`;
`Forpaper - Mercado Livre` → `forpaper`, aceitando "ML Forpaper" no chamado).
O que não casar na Shopee vai no `PERFIS_EXTRA` do `.env`.

## Rodar

```bash
npm start -- --teste-pedido 260910MATESNVN --conta "Shopee Vortan"   # só a tela, sem DaVinci
npm start -- --teste-ml 484465159 --conta forpaper                   # idem, consulta do ML
npm start -- --uma-vez                                               # uma passada (modo do .env)
LEITURA_MODO=real npm start -- --uma-vez --so 296012                  # um caso, de verdade
```

No Mac Santiago a pasta é `~/DaVinci/executor-leitura-chamado` (atalho na
Mesa: `DaVinci`). Atualizar = copiar `apps/executor-leitura-chamado/` sem
`node_modules`, `.env`, `logs` e `debug`. Ligar sozinho com o Mac: o `.plist`
desta pasta (instruções dentro dele).
