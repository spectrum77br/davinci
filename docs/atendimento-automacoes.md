# Mensagens automáticas no DaVinci: motor, modo seco e troca do Duoke

Eduardo, 05/10/2026: "queria também ... fazer uma pesquisa ali no Duoke pra saber
as mensagens automáticas que são usadas lá hoje ... pra aplicarmos aí, daí tal dia
manda tal mensagem" e "e sobre as respostas automáticas já fez também?".

Combinado com ele: o DaVinci recria as mensagens automáticas que o Duoke manda
hoje, **começando em modo seco**. O DaVinci registra o que mandaria, para quem e
quando, e **não envia nada**. Um comparador confere com o que o Duoke mandou de
verdade. Quando bater, a troca é feita loja por loja e automação por automação:
desliga no Duoke e liga no DaVinci na mesma hora.

Este documento é o **desenho e o que foi implementado** (backend, 05/10/2026).
A base é o levantamento das automações de 05/10 (SELECTs só de leitura em
produção, semana de 28/09 a 04/10), as conferências desta página, também só com
SELECT, e a crítica do desenho (as lacunas de gravidade alta e média estão
resolvidas no texto e no código; as baixas, quase todas). A **§13** diz o que
está no código, onde, e o que a **simulação contra 7 dias de produção** mediu.

Regras desta entrega:

- **Nada sai para comprador.** O modo "enviar" existe no código, mas só sai com
  a chave nova do `.env` (`ATENDIMENTO_AUTOMACOES_ENVIO`, desligada por padrão),
  o envio geral (`ATENDIMENTO_ENVIO_ATIVO`, o freio único) **e** a regra da loja
  em `enviar`. A tela recusa pôr regra em `enviar` com a chave desligada. Os
  testes provam que simular nunca chama o adaptador, o cliente nem a rede.
- O deploy não liga nada. O motor só roda com `ATENDIMENTO_AUTOMACOES_ATIVA=true`
  (e a leitura ligada), e quem muda o `.env` de produção é o Eduardo, ou alguém
  com o OK dele. A migration 0366 semeia as regras em `simular` onde o Duoke
  manda hoje: com o motor desligado, nada acontece.
- O registro e a tela nunca guardam nem mostram texto de comprador. Só loja,
  pedido ou conversa, automação, horário e estado.

---

## 1. Resumo

| O quê | Como fica |
|---|---|
| Modelo | 2 tabelas novas (migration `0366_atendimento_automacoes`). **Regras**: automação × loja, com modo `desligado`/`simular`/`enviar`, partes da mensagem com `{placeholders}`, atraso, horário, condições, o disjuntor e `enviar_desde`. **Registro**: alvo × automação, uma linha por pedido, conversa, comprador ou mensagem, conforme a automação. Estado `agendado` → `simulado`/`enviado`/`pulado`(+motivo)/`falhou`/`revisar`, `so_duoke`, e a comparação com o Duoke (com `divergencia` e `alerta`). A migration **semeia** as regras: `simular` nas lojas onde o Duoke manda hoje, `desligado` no resto. |
| Motor | Cron `atendimento_automacoes` nos minutos pares, logo depois da leitura das caixas (minutos ímpares). Uma rodada por vez (trava no Redis). Em cada rodada: **descobrir** gatilhos (mensagem do comprador, pedido pago no Bling, entregue/concluído na varredura da Logística) → **decidir** os que venceram → **comparar** com o Duoke. |
| Automações desta entrega | **Shopee:** menu + respostas 1, 2, 3, 5 e 6 **na hora**; "aguarde" da ATV; pedido recebido; entregue (celular/mala, em horário comercial); convite para seguir; "ficou alguma dúvida" 2 h e 26 h; pós-conclusão 4 h. **TikTok:** "aguarde", convite e "ficou alguma dúvida" 2 h e 24 h. **ML:** menu do pós-venda e respostas das opções, com até 350 caracteres. São cerca de 7.300 textos por semana hoje no Duoke. |
| Fora desta entrega | Carrinho com cupom, resposta de avaliação e "pedido recebido" do TikTok (§5). |
| Modo seco | O motor roda de verdade e grava "mandaria X às HH:MM" (`simulado`). O comparador procura a mesma automação do Duoke na mesma conversa ou pedido, pela assinatura do texto do modelo ou pelo payload, dentro de uma janela. O estado do robô (sessão do menu, intervalo do "aguarde", ciclo da dúvida) é medido **só com o que o DaVinci mandaria** (§6.1). A tela mostra, por automação × loja, a % que bateu (a menor entre precisão e cobertura) e o **critério da troca de cada loja** em 7 dias — é ele que diz quando dá para trocar. |
| Envio | Só pelo `enviar.py` (função nova `enviar_automatica`), com origem própria `davinci_auto` e a marca `payload.automacao`. A régua `e_mensagem_automatica`, a fila, a métrica e a IA reconhecem as duas (e o cartão do pedido da campanha do Duoke). Teto por loja e família por dia, 3 mensagens normais por comprador sem resposta dele, chave única por alvo, linha em voo, nunca retenta o ambíguo; na troca, espera a leitura e não manda se o Duoke mandou depois do gatilho; o disjuntor volta a regra para `simular`. Entra junto a correção do `ia._humano_respondeu_recente`. |
| Tela | Aba **"Automáticas"** no /atendimento, para quem já vê o /atendimento. API (§8.1) e tela (§8.2) implementadas. Mostra automação × loja: modo, texto editável, atraso e horário, simulados em 24 h e 7 dias, precisão, cobertura e % que bateu com o Duoke, o atraso real do DaVinci e o critério da troca **da loja** (o da automação só soma as lojas). Embaixo, o registro recente, sem texto de comprador. |

---

## 2. O que o DaVinci já vê: as fontes de gatilho, conferidas em produção (05/10)

Toda automação nasce de uma destas fontes. Nenhuma fonte nova chama a
plataforma no modo seco: o motor só lê o banco.

### 2.1 Mensagem do comprador (leitura das caixas, minutos ímpares)

- `atendimento_mensagens` com `autor = 'cliente'`. `enviada_em` é o relógio da
  plataforma e `created_at` é a hora em que o DaVinci gravou.
- Atraso da leitura (`created_at − enviada_em`, 2 dias):

  | Plataforma | p10 | p50 | p90 |
  |---|---|---|---|
  | Shopee | 11 s | 59 s | 110 s |
  | TikTok | 11 s | 62 s | 107 s |
  | ML pós-venda | 26 s | 81 s | 116 s |

  Com o motor nos minutos pares, o menu sai de 1 a 4 min depois da mensagem. O
  do Duoke sai em cerca de 1 min.
- O cartão que o comprador manda sem escrever é reconhecido pelo payload
  (`constantes.e_cartao_do_comprador`). Shopee: `item`, `variation_card`,
  `order`. TikTok: `PRODUCT_CARD` (25 por semana) e `ORDER_CARD` (24).
- Dígito sozinho do comprador em 7 dias, que é a escolha do menu. Shopee:
  6 = 301, 1 = 73, 2 = 39, 5 = 24, 3 = 10, 4 = 1. ML: 6 = 19, 1 = 7, 5 = 4, 2 = 1,
  4 = 1.

### 2.2 Pedido pago (Bling)

- `bling_orders.created_at` é a hora em que o **DaVinci** gravou o pedido. Uma
  linha por item, então o motor usa `DISTINCT numeroloja`. O pedido da Shopee é
  `numeroloja ~ '^[0-9]{6}[0-9A-Z]{8}$'`. O Bling só recebe pedido pago.
- Loja → integração: `bling_orders.store_id → stores.integration_id`. Casou 1.064
  dos 1.065 pedidos Shopee da semana. **Não** usar `integrations.store_id`, que
  só está preenchido em 3 lojas Shopee.
- Atraso do Bling desde a criação na Shopee (`atendimento_pedidos_comprador.criado_em`):
  p10 0,2 min, p50 1,0 min, p90 6,8 min.
- **O "pedido recebido" do Duoke sai 5 min depois do Bling.** O cartão do pedido
  do Duoke chega de 4,7 a 5,0 min (p10 a p90) depois do `bling_orders.created_at`
  do mesmo `order_sn` (1.201 cartões casados). O gatilho do DaVinci é exatamente
  esse: `created_at + 5 min`.
- Comprador do pedido (o `to_id` da Shopee): está no índice
  `atendimento_pedidos_comprador` em 1.054 de 1.067. Mas o índice roda de hora em
  hora (no :22, com janela de 2 h), então aos 5 min quase nunca está lá. O modo
  seco não precisa dele. No envio, `ShopeeClient.get_order_buyer(order_sn)`, que
  já existe.
- Só 120 desses 1.067 compradores tinham conversa com mensagem no DaVinci antes
  de pagar (11%). **O envio por pedido precisa funcionar sem conversa.** A Shopee
  manda pelo `to_id` e devolve o `conversation_id` (§7.4).

### 2.3 Entregue e concluído (varredura da Logística, Shopee no :09)

- `logistica.meli_status.order_status` e `status_datas.order_status.em`, que é o
  `update_time` da Shopee para o status atual. Do lado físico,
  `meli_status.logistics_status` (`LOGISTICS_DELIVERY_DONE`). A varredura
  carimba `logistica.status_lido_em` em TODA leitura (`logistica_shopee.enrich_row`),
  não só quando o status muda (só o `sweep_pos_venda` carimba na mudança): a
  busca dos "últimos 3 dias" pega linhas relidas, e o `evento_em` delas pode ser
  antigo. Quem segura é a chave única, o filtro do evento depois de a regra
  ligar e a validade.
- Retrato de 30 dias: `COMPLETED` 2.304, `SHIPPED` 901, `TO_CONFIRM_RECEIVE`
  (entregue e não concluído) 446, `TO_RETURN` 80, `CANCELLED` 19. Todos têm a data
  em `status_datas`.
- Quanto tempo depois do horário da Shopee o DaVinci vê (7 dias):

  | Status | p10 | p50 | p90 |
  |---|---|---|---|
  | `TO_CONFIRM_RECEIVE` | 12 min | 39 min | 98 min |
  | `COMPLETED` | 9 min | 33 min | 62 min |

- O `status_datas` só guarda o status **atual**: o entregue some quando o pedido
  é concluído. Por isso **o próprio registro é o evento**. Quando o motor vê o
  pedido em `TO_CONFIRM_RECEIVE`, grava a linha `shopee_entregue` com
  `evento_em` (o horário da Shopee) e `visto_em` (agora), e ela fica gravada
  mesmo que o status mude depois. O motor não muda nada na varredura da
  Logística. Ele lê a `logistica` a cada 10 min, procurando o
  `status_lido_em` dos últimos 3 dias.
- **Fora da Logística:** os pedidos Shopee na situação 545902 "Resolvido" do
  Bling não estão na tabela `logistica` (de 10 a 30 dias atrás, 122 de ~2.860,
  4%; 51 já concluídos no índice). O DaVinci não vê o entregue nem o concluído
  deles. Nesta entrega fica aceito e medido: o "só Duoke" desses pedidos vira a
  diferença combinada `sem_logistica`, fora da conta. Depois da troca, esses
  compradores ficam sem o entregue e o pós (pendência: ler o status pelo índice
  ou por `get_order_status_map` numa rotina própria).
- Hoje, 442 dos 447 pedidos entregues já têm conversa no DaVinci, porque o
  "pedido recebido" do Duoke abriu a conversa. Isso deixa de valer quando o
  Duoke parar.

### 2.4 Avaliação, reclamação/devolução e histórico de compra

- Avaliação do pedido: `atendimento_avaliacoes_loja (integration_id, pedido)`,
  lida a cada 30 min (:12/:42). Na Shopee, 100% das avaliações vêm com o pedido.
- Reclamação ou devolução aberta: `atendimento_reclamacoes` com
  `encerrada_em IS NULL`, casando por `pedido_marketplace` ou `conversa_id`.
  Também o `order_status = 'TO_RETURN'` da Logística e a etiqueta da conversa
  (`reclamacao`, `devolucao`, `ag_cancelamento`).
- "Já comprou na loja": `atendimento_pedidos_comprador (integration_id, comprador_id)`.
  **O índice só está completo desde 28/09**: 1.904 pedidos naquela semana, contra
  419 a 849 nas anteriores e quase nada antes de setembro. Até importar o
  histórico (`importar.py`, 90 dias, que precisa do OK do Eduardo), "nunca
  comprou" quer dizer "não comprou desde que o índice cobre a loja"
  (`indice.cobertura`). O comparador vai mostrar o efeito disso: o Duoke deixa
  de mandar para quem comprou antes, e o DaVinci manda.

### 2.5 Como as automações do Duoke chegam na caixa (base do comparador)

Shopee (7 dias). O que separa as duas famílias é o `payload.status` da Shopee:

| Modelo | autor / origem | payload.status | Por semana |
|---|---|---|---|
| menu, opções 1/2/3/5/6, "aguarde" (ATV) | loja / externo | `normal` (conta como resposta na Shopee) | 1.662 + 212 + 336 |
| pedido recebido, convite, dúvida 2 h/26 h, entrega celular/mala, pós-conclusão | sistema / sistema | `auto_reply` (não conta) | 1.024 + 1.102 + 832 + 798 + 374 + 258 + 323 |
| cartão do pedido (`message_type=order`, `content.order_sn`) | loja / externo | `normal` | 1.932 |
| figurinha (`sticker_id` 0007, pacote `br_shoppito`) | loja / externo | `normal` | 971 |

TikTok: tudo do Duoke chega como loja / externo, papel `CUSTOMER_SERVICE`.
"Aguarde" 170, pedido recebido 157 + 158 cartões, convite 126, dúvida 2 h 22 e
24 h 25. As mensagens da própria TikTok chegam como sistema (`ROBOT`).

ML pós-venda: loja / externo. Menu 48, opção 6 25, opção 1 10.

### 2.6 A IA calada pelo robô (o defeito do item 4)

Agora há 104 conversas esperando resposta com mensagem do comprador nas últimas
24 h: 96 Shopee, 7 TikTok e 1 ML. Nas 104, o `ia._humano_respondeu_recente` diz
"alguém da equipe falou nas últimas 24 h". Em **89** delas, quem "falou" foi só
mensagem automática: menu, "aguarde", campanha do TikTok ou a senha da
devolução. Hoje não faz diferença, porque a IA está em copiloto. Precisa estar
corrigido antes de qualquer automático (§7.7).

---

## 3. Modelo de dados: migration `0366_atendimento_automacoes`

O maior número em `origin/main` em 05/10 (depois do `git fetch origin`) é o 0365
(`0365_marca_emails`, depois da `0364_denuncia_relatorios`). A nova é a 0366 e
revisa a 0365 (nasceu 0364 e foi renumerada quando as duas chegaram ao origin).
Antes de commitar, conferir de novo com `git fetch origin`.
A migration só acrescenta coisas: duas tabelas, sem mexer em nenhuma que já
existe. Ela entra em `tests/test_atendimento_migration.py`, que roda a migration
num schema descartável e compara com o model. Usa `lock_timeout` de 3 s, como as
outras do atendimento.

### 3.1 `atendimento_automacao_regras`: automação × loja

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | uuid PK | |
| `automacao` | varchar(48) NOT NULL | código do catálogo (§4), por exemplo `shopee_menu` |
| `integration_id` | uuid NOT NULL, FK `integrations` ON DELETE CASCADE | a loja |
| `plataforma` | varchar(16) NOT NULL | retrato (shopee, tiktok ou ml) |
| `modo` | varchar(16) NOT NULL default `'desligado'` | CHECK em (`desligado`, `simular`, `enviar`) |
| `ligada_desde` | timestamptz NULL | quando saiu de `desligado`. O motor só descobre eventos a partir daqui. Passar de `simular` para `enviar` **não** muda esse valor (§6.6). |
| `partes` | jsonb NOT NULL default `'[]'` | em ordem: `{"tipo":"texto","texto":"Oi, {comprador}! …"}`, `{"tipo":"cartao_pedido"}`, `{"tipo":"figurinha","figurinha":"0007","pacote":"br_shoppito"}` |
| `atraso_min` | integer NOT NULL | minutos depois do gatilho |
| `janela_inicio`, `janela_fim` | time NULL | horário de São Paulo. Os dois NULL = 24 h. |
| `condicoes` | jsonb NOT NULL default `'{}'` | os parâmetros da automação, com chaves do catálogo: `intervalo_h`, `nao_se_pessoa_respondeu`, `ciclo_dias`… |
| `teto_dia` | integer NULL | teto próprio. Além dele vale o da loja (§7.6). |
| `versao` | integer NOT NULL default 1 | sobe a cada mudança de partes ou condições. O registro guarda qual versão valeu. |
| `atualizado_por` | uuid NULL, FK `users` SET NULL | |
| `enviar_desde` | timestamptz NULL | quando foi para `enviar` (o disjuntor só olha o Duoke DEPOIS disso) |
| `disjuntor_em`, `disjuntor_motivo` | timestamptz / varchar(48) NULL | o motor voltou a regra sozinho para `simular` ("Duoke ainda ligado?") |
| `created_at`, `updated_at` | | `TimestampMixin` |

`UNIQUE (automacao, integration_id)`. `CHECK modo IN (desligado, simular, enviar)`.

Loja **sem linha** = automação desligada, com os padrões do catálogo, como no
`LogisticaMensagemTemplate`. **A migration semeia**: cada loja Shopee, TikTok e
ML ativa ganha uma linha por automação da plataforma, com os textos padrão
(§4), em `simular` nas lojas onde o Duoke manda hoje e em `desligado` no resto
(a opção 4 sempre `desligado`). A loja casa pelo nome da integração (`strip +
lower`, como a Logística casa a conta); a lista está no catálogo e o teste da
migration confere. Em outro ambiente, a loja de outro nome nasce `desligado` (o
erro possível é não simular, nunca enviar). O botão "Simular nas lojas do
Duoke" (§8) cria a ausente e liga a desligada, e nunca mexe em regra em
`simular` ou `enviar`.

**Histórico:** a tabela de regras fica COM o gatilho (quem mudou o modo, o
texto, o horário). O registro fica FORA (`historico/sql.EXCLUIDAS`): é escrito
pela máquina (~1.500 linhas por dia, cada uma inserida, decidida e comparada) e
guarda o id do comprador e o pedido.

### 3.2 `atendimento_automacao_registros`: alvo × automação

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | uuid PK | |
| `automacao` | varchar(48) NOT NULL | |
| `regra_id` | uuid NULL, FK regras SET NULL | |
| `regra_versao` | integer NULL | |
| `integration_id` | uuid NOT NULL, FK `integrations` CASCADE | |
| `plataforma` | varchar(16) NOT NULL | |
| `alvo` | varchar(16) NOT NULL | `pedido`, `conversa`, `comprador`, `mensagem` ou `duoke`. É o que a tela mostra. |
| `chave` | varchar(191) NOT NULL | a unicidade, conforme a automação (§4): `pedido:<sn>`, `conversa:<id>:msg:<id>`, `comprador:<id>`, `duoke:<mensagem_id>` |
| `conversa_id` | uuid NULL, FK `atendimento_conversas` SET NULL | |
| `gatilho_mensagem_id` | uuid NULL, FK `atendimento_mensagens` SET NULL | a mensagem do comprador que disparou |
| `pedido` | varchar(64) NULL | |
| `comprador_id` | varchar(128) NULL | id da plataforma, não é dado pessoal |
| `evento_em` | timestamptz NULL | relógio do gatilho: mensagem, pago no Bling, entregue ou concluído na Shopee |
| `visto_em` | timestamptz NOT NULL default now() | quando o motor viu o gatilho |
| `devido_em` | timestamptz NOT NULL | quando sairia: atraso, ajustado à janela de horário (também na decisão: fora do horário, anda para a próxima abertura) |
| `decidido_em` | timestamptz NULL | |
| `estado` | varchar(16) NOT NULL default `'agendado'` | CHECK em (`agendado`, `simulado`, `enviando`, `enviado`, `pulado`, `falhou`, `revisar`, `so_duoke`) |
| `modo` | varchar(16) NULL | o modo que valeu na decisão |
| `motivo` | varchar(48) NULL | código estável (§3.4) |
| `erro` | text NULL | código da plataforma, nunca texto |
| `mensagem_ids` | jsonb NOT NULL default `'[]'` | as linhas de `atendimento_mensagens` criadas no envio, uma por parte |
| `tentativas` | smallint NOT NULL default 0 | |
| `duoke` | varchar(16) NULL | `pendente`, `mandou`, `nao_mandou` ou `nao_se_aplica` |
| `duoke_mensagem_id` | uuid NULL, FK `atendimento_mensagens` SET NULL | a mensagem do Duoke que casou |
| `duoke_em` | timestamptz NULL | |
| `duoke_diferenca_s` | integer NULL | `duoke_em −` a hora em que a nossa saiu (a 1ª parte gravada) ou sairia (a decisão, no modo seco); na linha pulada, `− devido_em` |
| `divergencia` | varchar(32) NULL | diferença combinada de propósito (§6.3). Sai da conta da %. |
| `alerta` | varchar(32) NULL | o erro que trava a troca: "só DaVinci" para quem devolveu, cancelou ou reclamou (§6.4) |
| `comparado_em` | timestamptz NULL | |
| `created_at`, `updated_at` | | |

Índices:

- `UNIQUE (automacao, integration_id, chave)`: a trava contra duplicar. A
  descoberta grava com `INSERT … ON CONFLICT DO NOTHING`.
- `ix_…_agendados (devido_em) WHERE estado = 'agendado'`: a fila do decidir.
- `ix_…_conversa (conversa_id, automacao, devido_em)`: responde "já mandou nas
  últimas 12 h".
- `ix_…_pedido (integration_id, pedido) WHERE pedido IS NOT NULL`: o comparador do pedido.
- `ix_…_tela (integration_id, automacao, devido_em)`: as contagens da tela (pela `devido_em`).
- `UNIQUE (duoke_mensagem_id) WHERE duoke_mensagem_id IS NOT NULL`: uma mensagem
  do Duoke casa com uma linha só.
- `ix_…_comparar (devido_em) WHERE duoke = 'pendente'`.

Volume esperado: cerca de 1.500 linhas por dia, contando as puladas, ou cerca de
45 mil por mês. Esta entrega não tem limpeza.

Models: `AtendimentoAutomacaoRegra` e `AtendimentoAutomacaoRegistro`, em
`app/models/atendimento.py`, com docstring no estilo das outras.

### 3.3 Origem `davinci_auto` (sem migration)

`atendimento_mensagens.origem` é varchar(24) sem CHECK. A constante nova é
`constantes.ORIGEM_AUTO = "davinci_auto"`, e ela entra em `ORIGENS_DAVINCI`.
Isso dá três coisas: a leitura adota a nossa linha quando a mensagem volta
(`gravar._nossa_para_adotar`), a conversa entra em "A conferir" se o envio ficar
ambíguo, e a Shopee relê a conversa logo depois do envio
(`shopee._reconferir_enviadas`) para pegar a mensagem barrada.

### 3.4 Motivos (códigos estáveis; a tela traduz)

`regra_desligada`, `envio_desligado` (a regra está em enviar, mas a chave do
`.env` não; a linha vira `simulado` com este motivo), `atrasado` (passou da
validade, §4.0), `pessoa_respondeu`, `ja_mandado` (intervalo), `ja_recebeu` (uma
vez por comprador), `nao_e_primeira` (convite do TikTok), `ja_comprou`,
`avaliou`, `reclamacao_aberta`, `devolucao` (aberta, ou qualquer uma no entregue
e no pós), `pedido_cancelado` (Bling 12 ou `excluido`, índice `CANCELLED`/`IN_CANCEL`),
`ja_concluido`, `status_mudou`, `opcao_ja_respondida`, `sem_texto`,
`sem_cartao_produto` (TikTok 2 h), `fora_da_janela_shopee`, `conversa_bloqueada`,
`via_agente` (ML), `ia_no_automatico`, `loja_sem_acesso` (canal `sem_escopo` ou
`desligado`; o `erro` passageiro não pula: no modo enviar espera a próxima
rodada), `disputa_com_pessoa` (menu), `teto_dia`, `teto_comprador`,
`texto_invalido`, `sem_conversa`, `duoke_mandou` (modo enviar: o Duoke mandou
depois do gatilho), `campanha_sem_auto_reply`, `envio_recusado` e `parte_N` (a
parte N de uma mensagem com várias partes falhou, e as anteriores já tinham
saído). A lista com o texto da tela está em `automacoes_catalogo.MOTIVOS`.

---

## 4. As automações desta entrega

O catálogo fica no código, em `services/atendimento/automacoes_catalogo.py`,
como funções puras. Cada automação tem: código, plataforma, canal, alvo e
chave, gatilho, partes e textos padrão, atraso, janela e validade, condições,
lojas do Duoke (nomes normalizados com `strip().lower()`), assinatura do Duoke
e janela de comparação. As decisões são **funções puras** sobre um dicionário
de fatos lido do banco, `decidir(automacao, fatos) -> motivo | None`, para o
teste não precisar de banco.

### 4.0 Regras comuns

- **Validade.** Linha vencida há mais que a validade vira `pulado: atrasado`.
  Depois de um deploy ou de 3 h de motor parado, não saem 200 menus velhos.
  Validades: menu, opções, "aguarde" e convite 30 min; dúvida 3 h; pedido
  recebido 6 h; entregue até o fim da janela do dia seguinte; pós-conclusão 24 h.
- **"Pessoa respondeu"** é mensagem da loja depois do gatilho que **não** é
  automática pela régua: `autor = 'loja'`, `status != 'falhou'`, origem `externo`,
  `davinci_humano` ou `davinci_ia`, e `NOT gravar.mensagem_automatica_sql(texto, payload)`.
  É a mesma régua da fila "Falta responder" — que agora reconhece também o
  **cartão do pedido da campanha** do Duoke (Shopee `message_type=order` com
  `source=openapi`; TikTok `ORDER_CARD` com papel `CUSTOMER_SERVICE`): medido em
  7 dias, 1.934 dos 1.935 cartões da Shopee e 157 de 157 do TikTok vieram a até
  15 s de um texto de campanha. Contando como pessoa, ele calava a IA por 24 h
  depois de cada pedido recebido ou entregue e fazia o "aguarde" pular.
- **"Já mandado"** é a união de: as linhas do registro desta automação
  (`agendado`, `simulado`, `enviando`, `enviado` ou `revisar`), as mensagens
  nossas que voltaram (`davinci_auto`) e as mensagens do Duoke com a assinatura
  desta automação **anteriores ao gatilho**. Assim o modo seco não se cala por
  causa do menu que o Duoke acabou de mandar para a mesma mensagem, e também não
  convida de novo quem o Duoke convidou há dois meses.
  **No modo enviar** (depois da troca), conta também a mensagem do Duoke com a
  assinatura **DEPOIS** do gatilho — na conversa e, nas automações do **pedido**
  (pedido recebido, entregue, pós-conclusão), pela mesma régua do comparador
  (`automacoes_comparar.duoke_dos_pedidos`: o cartão do pedido que o Duoke manda
  junto, ou a conversa do pedido). Na **transição** (os 3 primeiros dias depois
  de `enviar_desde`, `automacoes.TRANSICAO`) a decisão espera `devido_em` + a
  espera do Duoke da automação (menu, "aguarde" e convite 2 min; pedido recebido
  3 min; dúvida e pós-conclusão 5 min) **e** a leitura da loja passar disso
  (`atendimento_canais.ultimo_ok_em`); depois da transição não espera mais (só
  atrasaria o menu, o "aguarde" e as opções). Se o Duoke mandou, a linha vira
  `pulado: duoke_mandou` e, se ele mandou depois de a regra ir para `enviar`, o
  **disjuntor** volta a regra para `simular` **na hora** — o resto do lote da
  rodada já decide em `simular` (§6.6). É o que impede a duplicidade na troca: a
  linha agendada que atravessa a troca (o entregue empurrado para as 9h, com o
  lote do Duoke de madrugada), o rearme de algo que o Duoke já mandou e a leitura
  ainda não trouxe, e o que o Duoke tinha agendado antes de ser desligado e
  mandou mesmo assim. (Na 1ª versão, as do pedido não conferiam o Duoke: um lote
  de entregues saía em dobro antes de o disjuntor do comparador desligar a regra
  — a revisão de 05/10 provou com sondas; corrigido e testado.)
- **Exclusões das campanhas** (pedido recebido, entregue, pós-conclusão, convite
  e dúvida): reclamação ou devolução aberta do pedido (linha do pedido) ou da
  conversa (linha da conversa), etiqueta `reclamacao`/`devolucao`/`ag_cancelamento`,
  conversa `bloqueada` e canal da loja `sem_escopo`/`desligado`. **No entregue e
  no pós-conclusão, QUALQUER devolução ou reembolso do pedido, aberta ou
  encerrada** (`atendimento_reclamacoes` de qualquer status pelo pedido, mais
  `logistica.meli_status.return_status` presente ou `TO_RETURN`): medido em 45
  dias, 46 pedidos concluídos com devolução aceita, 43 com a devolução encerrada
  antes do devido — o Duoke mandou o pós para 0, e o DaVinci mandaria
  "está tudo certo? ... avaliar" para ~7 a 11 compradores por semana que acabaram
  de devolver. O menu, as opções e o "aguarde" **não** pulam por reclamação:
  respondem ao comprador, como o Duoke — menos o **menu em disputa com pessoa**
  (etiqueta `reclamacao`/`devolucao` e uma pessoa respondeu nas últimas 24 h:
  `disputa_com_pessoa`, condição `pular_em_disputa_com_pessoa`, ligada; é o
  DaVinci falando no chat da disputa e contando como resposta da loja; decisão
  do Eduardo, §12). Nenhuma automação vale com a IA em `auto` no canal
  (`ia_no_automatico`): no automático, quem fala é a IA.
- **Teto por comprador** (só o que é mensagem normal, não campanha: menu, opções,
  "aguarde"): no máximo 3 do DaVinci depois da última mensagem do comprador
  (`teto_comprador`). A Shopee tem `reach_5_message_limit` e o bloqueio de
  mensagem repetida em 24 h: a resposta da equipe (NF, rastreio) não pode ficar
  travada por mensagem automática.
- **Janela da Shopee** (medida na senha da devolução, 29 recusas
  `user_is_forbidden`). Só dá para mandar se o comprador falou nos últimos 7 dias
  (`conversa.ultima_do_cliente_em`), ou se o pedido tem até 30 dias, ou se há
  devolução aberta. Fora disso, `pulado: fora_da_janela_shopee`. Todas as
  automações abaixo cabem na janela em mais de 99% dos casos (levantamento).
- **Horário.** Com `janela_inicio`/`janela_fim`, o `devido_em` que cair fora da
  janela anda para a próxima abertura — na descoberta **e na decisão**: a linha
  que só é decidida fora do horário (esperou a leitura, o motor parou num
  deploy, o PATCH rearmou às 22h) fica `agendado` com o `devido_em` na próxima
  abertura, se ela ainda couber na validade (senão, `atrasado`). Vale também
  para a linha seguinte (o 26 h nasce no horário da regra dela). Fora da janela
  não é motivo para pular. (Na 1ª versão o horário só valia na descoberta.)
- **`{comprador}`.** Na Shopee é o usuário (`conversa.comprador_nome`; no envio
  sem conversa, o `buyer_username` do `get_order_buyer`). No TikTok é o apelido.
  Sem nome, a parte é renderizada sem ele: `"Oi, {comprador}! …"` vira
  `"Oi! …"`, e `"{comprador} já segue…"` vira `"Já segue…"`. Lacuna que sobrar
  vira `texto_invalido`, porque o validador já barra.
- **Validador.** No modo seco também: o texto renderizado passa por
  `validador.validar(…, origem="davinci_auto")`. Valem as regras de todos, não
  as da IA, porque o texto fixo é da loja, como o de uma pessoa. Reprovou =
  `pulado: texto_invalido`. Todos os textos padrão abaixo passaram em 05/10;
  o único que não passou foi a opção 2 do ML (364 > 350), já encurtada aqui.

### 4.1 Tabela

| Código | Gatilho (fonte) | Atraso | Alvo / chave | Partes | Lojas (Duoke) | Duoke/semana |
|---|---|---|---|---|---|---|
| `shopee_menu` | mensagem do comprador (qualquer tipo), sem robô (menu ou opção) valendo nas últimas 12 h — nem agendado por mensagem anterior | 1 min, 24 h | sessão: `conversa:<id>:msg:<id>` | texto (status `normal`) | barbosa, inova, jlas, kfa, kia, mega, minas, mini, poofy, victor mei, vita, vortan (12) | 1.580 |
| `shopee_opcao_1/2/3/5/6` | o comprador manda só o dígito N com o menu valendo (12 h) | 1 min (**na hora**) | `conversa:<id>:msg:<id do dígito>` | texto (`normal`) | as mesmas 12 | 212 |
| `shopee_opcao_4` | sem texto: fica `desligado` até o painel (§10) | – | – | – | – | 0 |
| `shopee_aguarde` | 1ª mensagem do comprador depois da última pessoa **e** do último "aguarde" | 10 min, 24 h; intervalo 4 h | `conversa:<id>:msg:<id>` | texto (`normal`) | atv | 331 |
| `shopee_convite` | mensagem do comprador que nunca recebeu convite | 1 min, 24 h | comprador: `comprador:<id>` | texto (`auto_reply` se der, §7.3) | as 12 + atv (13) | 1.198 |
| `shopee_duvida_2h` | 1ª mensagem do comprador do ciclo de pré-venda (7 dias) | 2 h, 24 h | `conversa:<id>:msg:<id>` | texto | 13 | 826 |
| `shopee_duvida_26h` | o 2 h saiu (simulado ou enviado) | +24 h | a mesma chave do 2 h | texto + figurinha 0007 | 13 | 804 |
| `shopee_pedido_recebido` | pedido pago visto no Bling | 5 min, 24 h | `pedido:<sn>` | cartão do pedido + texto | 13 | 1.025 |
| `shopee_entregue` | `TO_CONFIRM_RECEIVE` visto pela varredura | 0, janela 09:00–20:00 | `pedido:<sn>` | cartão + texto (celular ou mala) | celular: atv, barbosa, jlas, kia, mega, mini, victor mei, vita, vortan; mala: inova, kfa, minas, poofy | 595 |
| `shopee_pos_conclusao` | `COMPLETED` visto pela varredura | 4 h, 24 h | `pedido:<sn>` | texto | 13 | 323 |
| `tiktok_aguarde` | 1ª mensagem do comprador depois da última pessoa **e** do último "aguarde" | 10 min, 24 h; intervalo 8 h | `conversa:<id>:msg:<id>` | texto | mini, barbosa, atv, eron | 155 |
| `tiktok_convite` | 1ª mensagem do comprador na conversa | 1 min, 24 h | `conversa:<id>` | texto | atv, barbosa, mini, injox, jlas, eron | 126 |
| `tiktok_duvida_2h` | ÚLTIMA mensagem do comprador do ciclo (7 dias) numa conversa em que ele mandou `PRODUCT_CARD` | 2 h, 24 h | `conversa:<id>:ciclo:<1ª msg>` (a linha agendada anda com a última mensagem) | texto | atv, barbosa, mini, eron | 25 |
| `tiktok_duvida_24h` | o 2 h saiu | +24 h | a chave do 2 h | texto | as mesmas | 24 |
| `ml_menu` | mensagem do comprador no pack sem robô nas últimas 12 h | 1 min, 24 h | `conversa:<id>:msg:<id>` | texto (≤ 350, ISO-8859-1) | aguiar, barbosa, counhago, forpaper, injox, inova, jlas2, kfa, kfa2, kia, marquezini, mini, velasco, victor mei, zorvex (15) | 49 |
| `ml_opcao_1/2/3/5/6` (e `ml_opcao_4` travada) | dígito N com o menu valendo | 1 min (**na hora**) | `conversa:<id>:msg:<id>` | texto | as 15 | 38 |

O pedido só citava o menu do ML. As respostas das opções entram porque são o
mesmo fluxo, com texto fixo e quase sem custo. Menu sem resposta da opção
deixaria o comprador esperando a pessoa.

Fora do alcance, porque o DaVinci não tem acesso: TikTok Inova e Poofy (sem
escopo de atendimento), ML Poofy (403) e ML lucas mei (token). Ali a automação
continua no Duoke.

### 4.2 Shopee: menu e respostas das opções

**Menu.** Na descoberta, para cada conversa Shopee com mensagem nova do
comprador (`created_at` nos últimos 40 min), o motor anda pelas mensagens do
comprador em ordem. Uma mensagem abre sessão (`devido_em = enviada_em + 1 min`)
se não houver **robô valendo**: menu ou resposta de opção (do Duoke ou nossa)
nas 12 h ANTES dela, ou um menu nosso já agendado/simulado por uma mensagem
ANTERIOR — mesmo que ainda não tenha saído. Assim a rajada de mensagens no
mesmo minuto dá um menu só (como o Duoke); a 1ª versão olhava só o que já
tinha saído, e a simulação contra produção mostrou 3 a 4 menus por rajada. A
resposta de opção reinicia a sessão de 12 h ("sem robô nas últimas 12 h"). Na
decisão, confere de novo.

Texto padrão: o do Duoke, igual em todas as lojas.

> Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!
>
> 1 - Previsão de entrega / envio
> 2 - Nota fiscal
> 3 - Encerramento da compra
> 4 - Troca de endereço
> 5 - Garantia
> 6 - Falar com Atendente

**Opções.** O comprador escreveu só o número:
`^\s*(?:op[cç][aã]o\s*)?([1-6])\s*[.)\-]?\s*$`. Com menu "já mandado" nas
12 h anteriores, o motor grava `shopee_opcao_N`. Sem menu valendo, o dígito
**não** dispara nada: um "2" solto pode ser "quero 2". O Duoke responde até fora
de sessão. Condições na decisão: ninguém da equipe respondeu depois do dígito
(`pessoa_respondeu`), e a mesma opção ainda não foi respondida nesta sessão
(`opcao_ja_respondida`). **Diferença de propósito:** o Duoke responde 12 h
depois do menu, só se ninguém respondeu, e por isso de madrugada. O DaVinci
responde na hora (§6.3).

Textos padrão (os do Duoke):

- 1: "A entrega é feita pela Shopee, não temos acesso ao transporte, pode seguir o prazo informado na hora da compra por favor!\n\nTodas informações de envio e transporte estão em > Informações do Envio dentro do pedido!"
- 2: "Todos nossos produtos são enviados com nota fiscal que vai anexada a caixa do produto, caso não chegue ou queira antecipadamente, podemos enviar por aqui.\n\nAs vezes pode ocorrer da nota inserida no pedido contenha algum campo errado, pois ela é inserida pelo sistema automaticamente.\n\nConforme sua solicitação, enviaremos a nota em ate 1 dia util.\n\nAtenciosamente."
- 3: "O pedido pode ser encerrado a qualquer momento antes do envio pelo comprador.\n\nCaso o pedido esteja em trânsito, deve-se recusar o recebimento do produto no ato da entrega."
- 5: "Por favor, descreva qual é o defeito do produto e envie-nos uma foto.\n\nAssim que tivermos um atendente disponível, já responderemos com a solicitação.\n\nO prazo de garantia é de 90 dias; a garantia não cobre mau uso!\n\nAtenciosamente"
- 6: "Descreva sua dúvida que assim que um atendente estiver disponível ele irá te responder!"
- 4: sem texto. A regra fica travada em `desligado`, e a tela não deixa ligar sem texto.

Tipo: mensagem normal (`send_message`), que conta como resposta na Shopee, igual
ao Duoke hoje. Na caixa do DaVinci é automática: não fecha a vez do comprador
(§7.5). As opções saem quando o comprador falou agora, então a janela da Shopee
está sempre aberta.

### 4.3 "Mensagem recebida, aguarde" (Shopee ATV; TikTok Mini, Barbosa, ATV e Eron)

M é a 1ª mensagem do comprador depois de **max(última resposta de pessoa, último
"aguarde")** — o do Duoke (o comprador viu), o nosso que voltou, ou a linha
nossa agendada/simulada. A chave é `conversa:<id>:msg:<M>`. O motor grava
`devido_em = max(M + 10 min, último "aguarde" + intervalo)`, com intervalo de
4 h na Shopee e 8 h no TikTok. Quem escreve de novo dentro do intervalo recebe
o próximo quando ele fecha, como o Duoke (medido: em 14 dias, o 2º "aguarde"
sem pessoa no meio saiu em 88 conversas Shopee e 48 TikTok, 13% a 15%); a 1ª
versão usava M = 1ª mensagem depois da última pessoa, e a chave repetida
descartava o 2º. Na decisão: `pessoa_respondeu` desde M → pula.

Texto: o do Duoke está em português de Portugal ("equipa"). A proposta, que
depende do OK do Eduardo, é:

> Olá! Recebemos sua mensagem. Estamos com muitos atendimentos neste momento, mas já vamos te responder por aqui. Obrigado por aguardar!

Tipo: texto normal. Na ATV, que não tem menu automático, é o que segura a taxa
de resposta: 51% dos "aguarde" não tiveram resposta da equipe em 12 h. No
TikTok, 64%.

### 4.4 Shopee: convite para seguir

O comprador sem convite "já mandado" (registro por `comprador:<id>` + histórico
do Duoke antes do gatilho, em qualquer época) manda mensagem → `devido_em =
enviada_em + 1 min`. Conta também a **pergunta pronta do chat da Shopee** (o
comprador tocou na pergunta e o robô da Shopee respondeu: `bundle_message` do
`server`, gravada como `sistema`): medido em 7 dias, 31 dos 43 convites do Duoke
sem mensagem do comprador nos 10 min anteriores vieram depois dela.
Vai uma vez por comprador. O Duoke repete em 6% dos casos depois de 7 dias; a
regra exata é pendência do painel (§10). Texto padrão, o do Duoke, que já traz o
nome:

> {comprador} já segue nossa loja aqui na Shopee? Seguindo você recebe ofertas exclusivas, cupons e novidades em primeira mão 🚀

Exclusões: as das campanhas (§4.0). Tipo: hoje sai como `auto_reply`, que não
conta como resposta. Ver §7.3. A Shopee barrou 8 por lista negra
(`censored_blacklist`): a leitura marca a mensagem e o registro mostra o
`erro`.

### 4.5 Shopee: "ficou alguma dúvida" 2 h e 26 h

- Ciclo: M é a 1ª mensagem do comprador sem `shopee_duvida_2h` "já mandado"
  na conversa nos 7 dias anteriores (`condicoes.ciclo_dias = 7`). O 2 h conta da
  **primeira** mensagem do ciclo; o levantamento corrigido mostrou 120 min desde
  a 1ª, mesmo com várias. O motor grava `devido_em = M + 2 h`.
- Condições do 2 h: nunca comprou na loja. Isso quer dizer: nenhum pedido no
  índice para (loja, comprador) que não esteja `UNPAID`, a conversa sem
  `pedido_marketplace` e **sem cartão de pedido** na conversa (o do comprador ou
  o da campanha "pedido recebido" — o índice roda de hora em hora, e quem compra
  na 2ª hora pode não estar nele ainda). Mais as exclusões das campanhas. Até
  importar os 90 dias de pedidos, "nunca comprou" é fraco: o "só DaVinci" que
  isso dá **conta como erro** na comparação (não é diferença combinada). **Igual ao Duoke**, sai mesmo que a equipe já tenha respondido
  (`condicoes.nao_se_pessoa_respondeu = false`; o Duoke manda em 96% desses
  casos). Mudar isso é decisão do Eduardo (§12).
- 26 h: o motor grava a linha quando o 2 h é decidido como `simulado` ou
  `enviado`, com `devido_em = devido do 2 h + 24 h` e as mesmas condições.
  Partes: texto + figurinha 0007 (`br_shoppito`), como o Duoke. Se o adaptador
  da figurinha não estiver pronto na hora de enviar, sai só o texto.
- Textos padrão (os do Duoke): "Ficou alguma dúvida sobre o produto? Estou aqui
  pra te ajudar!" e "Tudo bem? Caso ainda esteja em dúvida, posso te explicar
  melhor sobre o produto".
- Janela da Shopee: o comprador falou há menos de 7 dias.

### 4.6 Shopee: pedido recebido

- Descoberta a cada rodada: `bling_orders` com `created_at` maior ou igual a
  `max(agora − 2 h, ligada_desde)`, pedido Shopee, loja por
  `stores.integration_id`. Grava `pedido:<sn>` com `evento_em = created_at`
  e `devido_em = created_at + 5 min`.
- Condições: pedido não cancelado (Bling situação 12 ou `excluido` — o
  soft-delete do `mark_order_excluido` —, ou índice `CANCELLED`/`IN_CANCEL`) e
  as exclusões das campanhas.
- Partes: **cartão do pedido** + texto. O cartão vai antes, como o Duoke faz
  (1 s antes). Texto padrão: "Oi! Recebemos seu pedido e já estamos preparando
  pra envio. Em breve você receberá o código de rastreio. Obrigado pela
  compra!".
- O que falta no adaptador: `message_type=order` (§7.3). Sem conversa (89% dos
  casos), o envio vai pelo `to_id` (§7.4).
- Janela da Shopee: o pedido tem menos de 30 dias.

### 4.7 Shopee: entregue (celular ou mala)

- Descoberta a cada 10 min: `logistica` Shopee com `order_status =
  'TO_CONFIRM_RECEIVE'` (e `logistics_status = 'LOGISTICS_DELIVERY_DONE'`) e
  `status_lido_em` nos últimos 3 dias, com o evento depois de `ligada_desde`.
  Loja pelo `_shopee_integration_for_conta` da Logística, o mesmo nome da conta.
  Grava `pedido:<sn>` com `evento_em = status_datas.order_status.em`. É aqui que
  "o evento fica gravado na hora em que o motor vê" (§2.3).
- `devido_em = evento_em + atraso (0)`, empurrado para dentro da janela
  **09:00–20:00**. O Duoke manda num lote de madrugada (mala por volta de 01 h,
  celular por volta de 05 h), em média 17 h depois da entrega. A mudança é de
  propósito (§6.3).
- Condições na decisão: o pedido **ainda** está em `TO_CONFIRM_RECEIVE`
  (senão `ja_concluido`; o Duoke também só manda para quem não concluiu: 260 de
  274), sem `TO_RETURN`, sem devolução ou reclamação aberta, e janela da Shopee
  (pedido com menos de 30 dias ou o comprador falou há menos de 7).
- Partes: cartão do pedido + texto com o nome **dentro**. O Duoke manda o nome
  sozinho numa bolha separada, e essa diferença também é de propósito. O texto
  depende do tipo da loja, e cada loja tem a sua regra com o texto padrão
  certo:
  - celular (9 lojas): "Oi, {comprador}! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se tratar de um produto de valor, recomendo abrir a embalagem gravando um vídeo contínuo, mostrando a caixa lacrada até a retirada do produto. Para a segurança dos nossos clientes, todos os pedidos são filmados e pesados antes do envio, garantindo que tudo saia daqui em perfeito estado. Qualquer coisa, tô por aqui pra ajudar!"
  - mala (inova, kfa, minas, poofy): "Oi, {comprador}! 🧳✨ Que alegria saber que sua mala já chegou! Espero que tenha gostado e que ela te acompanhe em muitas viagens incríveis. Qualquer dúvida sobre o produto, é só me chamar por aqui 😊"

### 4.8 Shopee: pós-conclusão 4 h

- Descoberta: `logistica` com `order_status = 'COMPLETED'` e `status_lido_em`
  nos últimos 3 dias. `evento_em = status_datas.order_status.em` e
  `devido_em = evento_em + 4 h`. Vai a qualquer hora, como o Duoke.
- Condições: sem avaliação do pedido em `atendimento_avaliacoes_loja`
  (`avaliou`; o Duoke manda para 284 de 353 sem avaliação e para 5 de 374 já
  avaliados). O pedido ainda está `COMPLETED`, **sem devolução ou reembolso
  nenhum** (aberto ou encerrado, §4.0), cabe na janela da Shopee e **tem conversa
  com a loja** (`so_com_conversa`: a simulação mostrou o Duoke sem pós para 5 de
  5 pedidos sem conversa; conferir no painel, §10). A conversa é achada pelo
  pedido ligado, pelo cartão do pedido da campanha (o "pedido recebido" que abriu
  a conversa) ou pelo comprador do índice.
- Texto com o nome dentro: "Oi, {comprador}! Só passando para saber se está
  tudo certo com o seu produto. Se sim e puder avaliar, agradeço muito! Se
  tiver qualquer problema, me avisa que eu resolvo." Passa no validador: não
  pede nota nem condiciona. A figurinha que o Duoke manda em 40% dos casos fica
  de fora.

### 4.9 TikTok: convite e "ficou alguma dúvida"

- `tiktok_convite`: 1ª mensagem do comprador na conversa (`conversa:<id>`, uma
  vez por conversa) → +1 min. Texto do Duoke: "{comprador} Já segue nossa loja
  aqui no Tiktok? Seguindo você recebe ofertas exclusivas, cupons e novidades em
  primeira mão 🚀". Em Injox e JLAS não se sabe quem manda; a simulação vai
  mostrar se é o mesmo motor.
- `tiktok_duvida_2h`: 2 h depois da **última** mensagem do comprador, numa
  conversa em que ele mandou `PRODUCT_CARD`. Sai mesmo que a loja tenha
  respondido (44 de 50 no Duoke). O ciclo é de 7 dias por conversa: UMA linha
  por ciclo (`conversa:<id>:ciclo:<1ª mensagem>`), e cada mensagem nova do
  comprador regrava o `devido_em` e o gatilho da linha ainda `agendado`
  (`INSERT … ON CONFLICT DO UPDATE … WHERE estado = 'agendado'`). Sem cartão de
  produto: `sem_cartao_produto`; com cartão de pedido na conversa: `ja_comprou`.
  `tiktok_duvida_24h`: +24 h depois do de 2 h. Textos do Duoke.
- Tipo: só texto, numa conversa que já existe (`tiktok.enviar_texto`, que já
  existe no código). A TikTok aceita até 2.000 caracteres e não aceita link. Com
  a conversa fechada, o adaptador devolve `bloqueio` e a conversa fica
  `bloqueada`.

### 4.10 Mercado Livre: menu do pós-venda

- `ml_menu`: mensagem do comprador no pack (`canal = 'pos_venda'`) sem
  mensagem do robô "já mandada" (menu ou opção) nas últimas 12 h → +1 min. As
  opções funcionam como na Shopee, na hora.
- Condições: sem reclamação ou mediação no pack pela regra ESTRITA da etiqueta
  (`claim_ids` com o chat bloqueado pela reclamação, `substatus_ml` em
  `blocked_by_claim`/`blocked_by_mediation`: o ML não esvazia `claim_ids` quando
  a reclamação acaba, e a regra solta pularia para sempre o pack com reclamação
  antiga), pack sem `dados.via_agente` (o menu **nunca** vai para o Agente do
  ML), e conversa não bloqueada.
- Limites: 350 caracteres, só ISO-8859-1. O validador já barra os dois e tira
  os emojis. O menu tem 218 caracteres e a opção 1 do ML tem 345. A opção 2, com
  364, é a única que não cabe: o texto padrão do ML perde o "Atenciosamente." do
  fim e fica com 347. A opção 1 do ML tem texto próprio ("A entrega é feita pelo
  Mercado Livre… --> minhas compras --> detalhes do envio… palavra-chave…").
- Risco: o ML modera mensagem automática (motivo `AUTOMATIC_MESSAGE`). O Duoke
  manda cerca de 87 por semana sem bloqueio visível, mas é preciso acompanhar o
  `bloqueio` no registro.

---

## 5. Fora desta entrega, e por quê

| Automação | Por que fica para depois |
|---|---|
| **Carrinho com cupom** (Shopee, 330/semana) | O cupom e as faixas (R$5/10 mala; R$20/25/30 celular) só existem no painel: não se sabe se é cupom da loja nem o código. O gatilho (pedido **não pago** + 30 min) não chega a tempo: o índice roda de hora em hora com janela de 2 h, e precisaria de um `get_order_list UNPAID` a cada 10 ou 15 min. A Shopee já manda sozinha um cupom 24 h depois (337/semana), e o comprador não pode receber dois. |
| **Resposta de avaliação** (Shopee, 465 públicas + 419 no chat) | É resposta **pública**, no anúncio. Hoje o `enviar.py` proíbe resposta automática em avaliação (`_destino_avaliacao`: "só pessoa responde"), e mudar isso tem que ser uma decisão de propósito. Também mexe na carência de `avaliacoes.py`, que se baseia num `resposta_em` 59 min inflado. |
| **"Pedido recebido" do TikTok** (157/semana) | Precisa abrir conversa (Create Conversation), mandar `ORDER_CARD` e saber o comprador de cada pedido do TikTok, e o DaVinci não tem nenhuma das três coisas. A própria TikTok já manda "Agradecemos pelo seu pedido!" na mesma hora. |
| Nativas da Shopee e da TikTok (cupom de 24 h, "avalie para ganhar moedas", cartões de rastreio, robô `ROBOT` da TikTok) | Não são do Duoke (`source` `server`/`crm`, papel `ROBOT`) e continuam sem ele. |
| Campanhas por evento no ML; qualquer coisa na Amazon/Magalu | O ML não deixa o vendedor puxar conversa livre. Na Amazon, só o que o DaVinci já manda por e-mail. Na Magalu, a loja só responde. |

---

## 6. Modo seco e comparação com o Duoke

### 6.1 O que o modo seco faz e o que não faz

- Faz **tudo** do motor: descobre, decide com as mesmas condições, renderiza o
  texto, passa pelo validador, aplica o teto e grava a linha: `simulado` com
  `devido_em` ("mandaria às HH:MM") ou `pulado` com o motivo.
- **Não** cria linha em `atendimento_mensagens`, **não** importa
  `enviar`/adaptador/cliente e **não** chama a plataforma, nem para ler: nada
  de `get_order_buyer`. A fila, a métrica e a IA não mudam em nada.
- Uma regra em `enviar` com a chave do `.env` desligada vira `simulado` com
  `motivo = envio_desligado`, e a tela mostra isso em vermelho (§8).
- **Mede o DaVinci sozinho** (correção da revisão de 05/10). O estado do robô —
  a sessão de 12 h do menu, o intervalo do "aguarde", o ciclo do "ficou alguma
  dúvida", a opção já respondida — não conta a mensagem do Duoke de uma
  automação que o DaVinci está **simulando** a partir do corte
  (`automacoes_catalogo.cortes_do_modo_seco`: o mais tarde entre `ligada_desde`
  e o começo do motor); no lugar dela vale a linha simulada do DaVinci (o
  registro). Antes do corte vale o histórico do Duoke (o DaVinci não tinha
  linha); em `enviar` e em `desligado`, a mensagem do Duoke fica (é a que o
  comprador recebeu). O comparador continua comparando com o Duoke. O convite e
  as campanhas do pedido não entram (o convite é uma vez por comprador e sai no
  mesmo minuto nos dois). Por quê: depois da troca o estado vem só do DaVinci, e
  os dois não andam juntos — a resposta da opção do Duoke sai 12 h depois do
  menu (medido em produção: 150 de 174 respostas de opção da Shopee em menu +
  12 h ± 5 min), a do DaVinci 1 min depois do dígito. Contando o Duoke, a
  resposta tardia dele segurava a sessão, o DaVinci nem criava a linha do menu,
  e o comparador nunca via a diferença: a contraprova da revisão (o motor sem
  ver as automáticas do Duoke a partir do começo da simulação) levou o menu da
  Shopee de 99,1% para 93,3% de precisão. Os números da §13 já são os medidos
  assim.

### 6.2 O comparador (`automacoes_comparar.py`, na mesma rodada)

Para cada linha decidida com `duoke = 'pendente'` (até 3.000 por rodada, por
conjunto — uma consulta por tipo, não por linha), o comparador procura a
mensagem do Duoke dessa automação: da loja **ou** do sistema (as campanhas da
Shopee chegam como `sistema`), **só de fora** — origem `externo`/`sistema` e
**sem** a marca `payload.automacao` (depois da troca, a nossa mensagem nunca casa
consigo mesma) —, com a **assinatura** e dentro da **janela**. O texto é
comparado com `constantes.normalizar_inicio` (o SQL só pré-filtra). Achou →
`mandou`, com `duoke_mensagem_id`, `duoke_em` e `duoke_diferenca_s` = Duoke −
**a hora em que a nossa saiu ou sairia**: no modo enviar, a 1ª parte gravada; no
modo seco, a decisão (a rodada dos minutos pares, de 0 a 2 min depois do
`devido_em`); na linha pulada, o `devido_em`. (Na 1ª versão era contra o
`devido_em`, e o menu aparecia "na mesma hora" que o Duoke quando a nossa sairia
1 a 2 min depois.) As consultas levam o `enviada_em` indexado na frente (o
`coalesce(enviada_em, created_at)` sozinho varria a tabela inteira a cada 2 min;
medido em produção: nenhuma mensagem de 30 dias sem `enviada_em`). A janela
fechou sem achar → `nao_mandou`, **só se a leitura da loja já passou do fim da
janela** (`atendimento_canais.ultimo_ok_em`); com a leitura parada (deploy,
token), continua `pendente` — senão viraria "só DaVinci" falso, e depois "só
Duoke" falso quando a leitura voltasse.

| Automação | Assinatura do Duoke (texto normalizado) | Onde procura | Janela (Duoke em relação a…) |
|---|---|---|---|
| menu (Shopee/ML) | começa com `ola, por favor selecione sua duvida` | a mesma conversa | devido −5 min a +20 min |
| opção N | começa com o texto do Duoke da opção (Shopee: `a entrega e feita pela shopee`, `todos nossos produtos sao enviados com nota`, `o pedido pode ser encerrado`, `por favor, descreva qual e o defeito`, `descreva sua duvida que assim`; ML: `a entrega e feita pelo mercado livre`…) | a mesma conversa | do dígito até menu + 13 h |
| aguarde | começa com `ola, a sua mensagem foi recebida` | a mesma conversa | devido −5 a +20 min |
| convite | contém `ja segue nossa loja aqui na shopee` ou `aqui no tiktok` | a mesma conversa | devido −5 a +20 min |
| dúvida 2 h / 26 h / 24 h | `ficou alguma duvida sobre o produto` / `tudo bem? caso ainda esteja em duvida` | a mesma conversa | devido −30 a +45 min |
| pedido recebido | `oi! recebemos seu pedido e ja estamos preparando` | conversa da loja com `pedido_marketplace = sn` **ou** com cartão `message_type=order` e `content.order_sn = sn` a até 10 s do texto | evento até evento + 1 h |
| entregue | contém `confirmamos a entrega do seu pedido` ou `que alegria saber que sua mala ja chegou` | o mesmo cartão `order_sn` | evento até evento + 36 h (lote da madrugada seguinte) |
| pós-conclusão | `oi! so passando para saber se esta tudo certo` | conversa da loja com o comprador do pedido (índice) ou `pedido_marketplace = sn` | evento + 3 h a + 8 h |

As assinaturas ficam no catálogo, junto com as constantes de
`RESPOSTAS_AUTOMATICAS` (§7.5), e o teste confere uma contra a outra.

Nas automações do pedido, a mensagem do Duoke casa com o pedido pelo **cartão do
pedido** que ele manda junto (`content.order_sn` a até 10 s do texto); sem cartão
na conversa, pelo `pedido_marketplace` da conversa ou pelos pedidos do comprador
no índice. A conversa pode nem existir no gatilho (89% no pedido recebido): o
cartão do Duoke é que a abre.

**Só o Duoke mandou.** O comparador procura, nas lojas com a regra fora de
`desligado`, mensagens do Duoke com a assinatura que nenhuma linha usou (o
índice único de `duoke_mensagem_id` garante isso), **das últimas 48 h** (pelo
índice de `enviada_em`) e com o motor e a regra ligados há mais que o maior
atraso do Duoke daquela automação. Espera 1 h (o entregue, 14 h: a nossa linha
das 9h só é decidida depois do lote de madrugada do Duoke) antes de dar a
mensagem como sem par. Para cada uma, grava `estado = 'so_duoke'` com
`chave = duoke:<mensagem_id>`, conversa e pedido (pelo cartão). É o falso
negativo do DaVinci.

### 6.3 Diferenças combinadas (não contam como erro)

| `divergencia` | Quando | Efeito |
|---|---|---|
| `menu_fim_de_sessao` | "só Duoke" do menu sem mensagem do comprador nos 3 min anteriores: o "re-menu" de fim de sessão de 12 h do Duoke (em 7 dias, 213 dos 1.658 menus vieram 30 min a 12 h depois do comprador, 84 com mais de 12 h e 40 sem mensagem nenhuma) | fora da conta; o painel (§10, item 2) diz o que é, e se o DaVinci copia |
| `concluiu_entre_horarios` | entregue: o pedido foi concluído entre o horário do DaVinci (9h–20h) e o lote do Duoke (madrugada) — "só DaVinci" quando concluiu depois do nosso; "só Duoke" quando concluiu depois do dele | fora da conta |
| `sem_logistica` | "só Duoke" do entregue/pós de pedido fora da tabela `logistica` (§2.3) | fora da conta, listado |
| `disputa_com_pessoa` | menu pulado em reclamação/devolução com pessoa atendendo (§4.0) | fora da conta |
| `exclusao_disputa` | campanha (convite, dúvida, pedido recebido, entregue, pós) pulada por reclamação ou devolução: o DaVinci não manda de propósito, o Duoke manda (a simulação mostrou o convite, a dúvida e o pedido recebido indo para essas conversas) | fora da conta |
| `opcao_repetida` | "só Duoke" da resposta de opção que o Duoke REPETE a cada 12 h enquanto o comprador escreve (o ML faz isso; a mesma opção nas 36 h antes) — o DaVinci responde uma vez | fora da conta |
| `motor_atrasado` | `pulado: atrasado` (motor parado ou atrasado, passou da validade) | fora da conta (é operação, não regra) |
| `teto_dia`, `teto_comprador` | só no modo seco: passou do teto | fora da conta |
| `regra_desligada` | a regra foi desligada depois do gatilho | fora da conta |

Sem diferença combinada (contam como erro, de propósito): o **"nunca comprou"
com o índice curto** (até importar 90 dias, o "só DaVinci" do 2 h é erro: medido
38 em ~718 por semana) e as **opções**: o Duoke responde 12 h depois e só se
ninguém respondeu, então o "só DaVinci" delas é esperado — para as opções a
precisão não se mede pelo Duoke (abaixo). O horário do entregue (o Duoke de
madrugada, nós das 9h às 20h) não é divergência: continua `mandou`, e a
diferença de horário aparece na mediana.

Diferença só de texto (aguarde em português do Brasil, nome dentro do texto,
opção 2 do ML mais curta) não afeta a comparação: a assinatura é a do texto do
**Duoke**.

### 6.4 A conta que a tela mostra

Por automação × loja, em 24 h e no período (pela `devido_em`;
`automacoes_comparar.estatisticas`):

- **bateu, mandou**: `simulado`/`enviado` + Duoke `mandou`;
- **bateu, não mandou**: `pulado` + Duoke `nao_mandou`;
- **só DaVinci**: `simulado`/`enviado` + Duoke `nao_mandou`;
- **só Duoke**: `pulado` + Duoke `mandou`, mais as linhas `so_duoke`;
- **combinada**: com `divergencia`, que fica fora de tudo;
- **precisão** = bateu, mandou ÷ (bateu, mandou + só DaVinci) — o DaVinci não
  manda o que o Duoke não manda;
- **cobertura** = bateu, mandou ÷ (bateu, mandou + só Duoke) — o DaVinci manda
  o que o Duoke manda;
- **concordância** = (bateu, mandou + bateu, não mandou) ÷ o total;
- **alertas** (tolerância zero): "só DaVinci" para pedido cancelado, com
  devolução/reembolso (aberto ou encerrado) ou com reclamação — a coluna
  `alerta`. Um alerta trava a troca;
- mais a diferença de horário mediana (`duoke_diferenca_s`: Duoke − a hora em
  que a nossa sai ou sairia), o **atraso real do DaVinci** (`atraso_mediana_s`:
  do gatilho até a hora em que a nossa sai — no modo seco, a decisão; no modo
  enviar, a decisão termina depois da plataforma) e os motivos de `pulado` mais
  comuns.

Uma % agregada ≥ 95% deixaria passar um erro sistemático de 2% a 5% justo nos
piores destinatários (o pós para quem devolveu dava 2% a 3%): por isso as duas
(precisão e cobertura) separadas, e os alertas. **A % que a tela pinta** (verde
a partir de 95%) é a **menor entre precisão e cobertura**; nas opções, a
cobertura. A concordância fica no detalhe: ela soma "nenhum dos dois mandou"
(as linhas puladas pelo próprio motor) e pintava de verde, por exemplo, a dúvida
2 h da Barbosa com 95,7% de concordância e 94,4% de cobertura.

### 6.5 Critério para trocar (proposta, decisão do Eduardo)

**A troca é loja por loja, e o critério também**: cada loja tem o dela, sempre
nos últimos 7 dias (`pode_trocar`/`por_que_nao_trocar` da loja na API, o selo
na linha da loja na tela). O total da automação é só a soma das lojas — passar
na soma não diz nada de uma loja (na simulação, o pedido recebido passava na
soma e só 4 das 13 lojas passavam). O `PATCH` para `enviar` exige o critério
daquela loja ou a marcação explícita "sei que o critério não passou nesta loja
e quero trocar mesmo assim" (`troca_sem_criterio`; sem ela, 422
`criterio_nao_passou` com os motivos).

Uma automação numa loja pode ir para `enviar` quando, em 7 dias seguidos
(`resumir`, o `pode_trocar` da loja):

- precisão **e** cobertura **e** concordância ≥ 95%, com pelo menos 30 casos;
- nenhum alerta (tolerância zero), nenhum `texto_invalido`, e nenhum "só
  DaVinci" nos últimos 2 dias;
- nas **opções**, o critério é próprio: cobertura ≥ 95% (o Duoke respondeu e nós
  também), o dígito detectado com o menu valendo (é a regra da descoberta) e o
  texto passando no validador — a precisão não se mede pelo Duoke. **A sobra é
  grande e é de propósito**: o DaVinci responde na hora a quem o Duoke só
  responderia 12 h depois (e não responde se alguém já respondeu) — na
  simulação, nas lojas "prontas" da opção 6, de 46% a 64% a mais (Jlas 20 de
  43, mega 33 de 58, vortan 28 de 44). A tela mostra essa sobra no title da % e
  no quadro do Enviar, para o Eduardo dar o OK sabendo disso;
- **trava fora do número** (o painel do Duoke, §10): o remetente confirmado como
  o Duoke (não o UpSeller nem recurso nativo — desligar no Duoke não para outro
  remetente; em Injox e JLAS no TikTok não se sabe quem manda o "já segue");
  e a troca **em pares** quando o Duoke não separa: menu + opções, 2 h + 26 h
  (24 h no TikTok).

**A ordem tem dependência**: as respostas de opção do Duoke saem no fim da
sessão do MENU do Duoke (medido: +12h00 do menu, e quase nunca quando uma
pessoa responde). Trocar o menu antes das opções deixa o comprador que digitou
sem resposta nenhuma (sem sessão do Duoke, sem opção nossa). E trocar as opções
antes do menu só funciona se o painel do Duoke desligar as respostas das opções
sem desligar o menu (§10, item 2) — senão o Duoke responde de novo 12 h depois
e o disjuntor volta a regra. Com o modo seco medindo o DaVinci sozinho (§6.1),
o número do menu já é o de **menu e opções do DaVinci juntos** (a sessão conta
a resposta da opção do DaVinci, 1 min depois do dígito): a troca dos dois
juntos é medida como vai ficar. A revisão de 05/10 sugeriu trocar as opções
primeiro e medir o menu 7 dias depois; isso vale se o painel separar os dois.
Ordem: opções e menu (juntos, ou opções primeiro se o Duoke separar) →
"aguarde" → pedido recebido e entregue → campanhas (estas só depois do envio
por resposta automática, §7.3).

### 6.6 A troca, loja por loja

1. Na mesma hora: o Eduardo desliga a automação daquela loja no **Duoke** e
   muda a regra para `enviar` na tela (`PATCH`). A API recusa sem a chave do
   `.env` (409 `envio_desligado`), sem o envio geral (`envio_geral_desligado`),
   na campanha da Shopee sem a resposta automática — a chave confirmada **e** o
   envio por ela no adaptador (`campanha_sem_auto_reply`, §7.3) —, na loja sem
   acesso, na regra sem texto; sem a marcação "Desliguei esta automação desta
   loja no Duoke" (422 `confirmar_duoke`); e, com o critério da loja não
   passando em 7 dias, sem a marcação "sei disso" (422 `criterio_nao_passou`).
   Carimba `enviar_desde`.
2. A mudança `simular → enviar` não mexe em `ligada_desde`, então o gatilho que
   aconteceu antes da troca e foi visto depois ainda sai. O PATCH também
   **rearma** (`simulado` → `agendado`) as linhas dessa regra e loja que o Duoke
   **não** mandou (`pendente` ou `nao_mandou`) e que ainda saem dentro da
   validade **no horário da regra** (rearmado às 22h, o entregue sai às 9h; o
   menu de 30 min, não rearma). O caso típico é o entregue que o Duoke só
   mandaria na madrugada. O rearmado não sai se o Duoke já mandou: a decisão no
   modo enviar confere o Duoke depois do gatilho — na conversa e, nas do pedido,
   pelo cartão ou pela conversa do pedido — e, na transição, espera a leitura
   da loja (§4.0).
3. **Disjuntor:** um `mandou` do Duoke numa regra em `enviar`, com a mensagem
   dele DEPOIS de `enviar_desde` (no decidir, ou no comparador depois que o
   nosso saiu), volta a regra sozinha para `simular`, com `disjuntor_em` e
   `disjuntor_motivo = duoke_ainda_ligado`, e loga `warning`: é o "Duoke ainda
   ligado?". No decidir, na hora: o resto do lote daquela rodada já decide em
   `simular`. O que escapa: a mensagem do Duoke que chega DEPOIS da nossa (o pós
   que o Duoke manda às 6 h quando o nosso saiu às 4 h05; o entregue entregue às
   10 h, o nosso às 10 h, o lote do Duoke às 2 h) — o comparador pega e o
   disjuntor desliga, mas aquele comprador recebeu duas. Some se o Duoke parar
   de verdade ao ser desligado (§10, item 13).
4. Pendente do painel (§10): **desligar a regra no Duoke cancela o que ele já
   agendou** (o 26 h, o lote da madrugada, o pós de 4 h)? Se não cancelar, o
   disjuntor pega, mas depois de uma mensagem em dobro.

---

## 7. Envio (código atrás das chaves desligadas)

### 7.1 As chaves

`config.py` (com comentário, no padrão das outras):

- `atendimento_automacoes_ativa: bool = False`: o motor roda (descobre, decide,
  registra e compara). Também exige `atendimento_leitura_ativa`. **Nada sai.**
- `atendimento_automacoes_envio: bool = False`: sem ela, nenhuma regra envia.
  Uma regra em `enviar` vira simulação com `motivo = envio_desligado`.
- `atendimento_automacoes_teto_dia: int = 400`: teto por loja **e por família**
  (as que respondem conversa × as do pedido) em 24 h corridas. O envio conta só
  `enviando`/`enviado`/`revisar` (o simulado não come o teto de quem envia); o
  modo seco conta o `simulado` à parte e, passando, vira a diferença combinada
  `teto_dia` (fora da %). Dimensionado pelo pico: o 9.9 teve 4× a mediana na ATV
  e 5× na Barbosa; o pico medido do Duoke foi 224 textos/dia (mega).
- `atendimento_automacoes_shopee_auto_reply: bool = False`: o teste de permissão
  do `send_autoreply_message` passou. **Sozinha não libera nada**: a campanha da
  Shopee só vai para `enviar` com ela **e** com o envio por resposta automática
  no adaptador (`enviar_auto_reply` em `services/atendimento/shopee.py`, que
  hoje não existe — `enviar.auto_reply_no_adaptador`). Sem os dois, a tela
  recusa, o motor pula `campanha_sem_auto_reply` e o `enviar_automatica`
  recusa; e quando puder sair, a campanha sai pelo `enviar_auto_reply`, nunca
  pelo `enviar_texto` (mensagem normal). Na 1ª versão a chave sozinha
  liberava, e a campanha sairia como mensagem normal — o que a trava existe
  para evitar (correção da revisão de 05/10). A API mostra as duas
  (`chaves.shopee_auto_reply` e `chaves.shopee_auto_reply_adaptador`).

Uma linha só sai para a plataforma se tudo isto valer: **o freio único**
`ATENDIMENTO_ENVIO_ATIVO` (desligado, nada sai pelo DaVinci — pessoa, IA ou
automação), a chave nova `ATENDIMENTO_AUTOMACOES_ENVIO`, `regra.modo == 'enviar'`
relido do banco na hora, o simulador **não** ligado em produção e, na Shopee,
`SHOPEE_MENSAGENS_COMPRADOR` (o freio de mão da mensagem proativa, que já
existia para a senha da devolução). O modo `observar` do canal **não** entra, de
propósito: a equipe continua respondendo pelo Duoke, com as caixas em observar,
enquanto as automações já saem pelo DaVinci. A faixa da tela avisa isso. (A 1ª
versão deixava o `ATENDIMENTO_ENVIO_ATIVO` de fora; a crítica mostrou que o
interruptor geral passaria a significar outra coisa: agora ele para tudo, e
como as caixas continuam em `observar`, ligá-lo não deixa a pessoa enviar.)

### 7.2 `enviar.enviar_automatica`: o mesmo caminho, outras travas

Função nova no `enviar.py`. É o mesmo caminho do texto e da foto: trava a
conversa, grava a linha em voo e a commita **antes** da plataforma, depois aplica
a régua de resultado (`enviada`/`revisar`/`falhou`) e a recusa vira
`EnvioRecusado`.

- **Travas que continuam:** `canal_sem_envio`, `somente_leitura` (só shopee,
  tiktok e ml), `simulador_em_producao`, `conversa_bloqueada` e janela,
  `sem_integracao`, `texto_invalido` (validador com `origem = davinci_auto`),
  `envio_repetido` (o mesmo texto há menos de 2 min) e `envio_em_andamento`
  (o índice `uq_atendimento_envio_em_voo`).
- **Próprias:** `envio_desligado` (o freio único), `automacoes_envio_desligado`
  (a chave nova), `shopee_mensagens_desligadas`, `campanha_sem_auto_reply` e
  `regra_nao_envia` (a regra relida não está em `enviar`) no lugar do
  `canal_em_observacao`. As travas da IA não entram: `auto_desligado`,
  `nao_aguarda`, `conversa_mudou`.
- A linha nasce com `autor = 'loja'`, `origem = 'davinci_auto'` e
  `payload = {"automacao": {"codigo", "registro_id", "regra_versao"}}`.
  `_gravar_resultado(…, avaliar=False)`: mensagem automática não é avaliação de
  rascunho.
- Cada parte é uma mensagem, em ordem: cartão, texto, figurinha. Se uma parte
  falha, o registro vira `falhou` com `motivo = parte_N`, e as partes que já
  saíram **não** são reenviadas. **Nesta entrega o envio é só das partes de
  TEXTO numa conversa que já existe**: o cartão e a figurinha ficam de fora (o
  adaptador ainda não manda), e a linha sem conversa vira `pulado: sem_conversa`.
  A recusa temporária (`conversa_ocupada`, `envio_em_andamento`) volta a
  `agendado` e tenta na rodada seguinte, até a validade; outra recusa vira
  `pulado: envio_recusado` com o código em `erro`; a mensagem `falhou` vira
  `falhou`, a `revisar` vira `revisar` — nunca retentadas.

O motor marca o registro como `enviando` e commita **antes** de chamar
`enviar_automatica`. Com a `chave` única, essa é a segunda trava contra
duplicar.

### 7.3 Adaptadores: o que existe e o que falta

| Parte | Shopee | TikTok | ML |
|---|---|---|---|
| texto | existe (`chat_send_message(text=…)`) | existe (`cs_send_text`) | existe (pack) |
| cartão do pedido | **falta**: `chat_send_message(order_sn=…)` → `{"message_type": "order", "content": {"order_sn": …}}` [conferir o formato na doc da Shopee] | fora (§5) | – |
| figurinha | **falta**: `{"message_type": "sticker", "content": {"sticker_id": "0007", "sticker_package_id": "br_shoppito"}}` (o formato que o Duoke manda, visto no payload) | – | – |
| como "resposta automática" (`auto_reply`) | **não se sabe**: `v2.sellerchat.send_autoreply_message` aparece na lista de operações, mas não foi testado. Conferir a permissão com corpo vazio (`param_error` = tem acesso; `error_permission`/`api_suspended` = não tem), como foi feito em 22/09 com o `send_message`. Só com o OK do Eduardo. | – | – |

Sem `auto_reply`, as campanhas (pedido recebido, entregue, pós-conclusão,
convite e dúvida) sairiam como mensagem **normal**: contariam como resposta na
Shopee e — o risco maior — entrariam no limite de mensagens por comprador da
Shopee (`reach_5_message_limit`, bloqueio de mensagem repetida em 24 h): um
comprador calado receberia em poucos dias o pedido recebido (cartão + texto), o
entregue (cartão + texto), o pós, o convite e as duas dúvidas (texto +
figurinha) — mais de 5 mensagens normais, e a mensagem da equipe (NF, rastreio)
poderia ficar travada para ele. E o `config.py` já anota que a FAQ do Chat API
proíbe "proactive order updates". Por isso, **sem o `auto_reply` confirmado, as
campanhas não vão para `enviar`** (§7.1) — e confirmado quer dizer a chave E o
envio por `auto_reply` no adaptador: hoje o adaptador só manda texto normal, e
a chave sozinha não basta. **Antes de liberar as campanhas** (pendência da
revisão): a volta da nossa resposta automática pela leitura chega como
`autor/origem = sistema` (`status = auto_reply`) e sem a marca; o comparador a
aceitaria como "o Duoke mandou" (`_de_fora` aceita `sistema` sem a marca) — um
acerto falso, e o disjuntor dispararia com "Duoke ainda ligado". A adoção
dessa volta (pelo id da plataforma ou pelo texto, pondo a marca) tem que vir
junto com o `enviar_auto_reply`, com um teste do comparador. Se der `auto_reply`, a mensagem volta
na leitura como `autor = 'sistema'`, e a adoção precisa aceitar isso (§7.4).

### 7.4 Sem conversa no DaVinci (pedido recebido, entregue, pós-conclusão) — pendente

Nesta entrega, a linha sem conversa no modo enviar vira `pulado: sem_conversa`
(e a decisão já acha a conversa pelo pedido ligado, pelo cartão do pedido dos
últimos 45 dias ou pelo comprador do índice). O desenho abaixo é a próxima etapa:

1. Procura a conversa Shopee da loja com `comprador_id` = o comprador do pedido
   (pelo índice ou pelo `get_order_buyer`, que só roda no modo enviar).
2. Achou → `enviar_automatica(conversa, …)`.
3. Não achou → o adaptador manda pelo `to_id` (`shopee.enviar_ao_comprador`), a
   Shopee devolve `conversation_id` e `message_id`, e o motor faz
   `gravar.upsert_conversa(externo_id=conversation_id, comprador_id, pedido_marketplace=sn)`
   e grava as mensagens com `externo_id = message_id`, `origem = davinci_auto` e
   `status = enviada`. A leitura seguinte acha o `externo_id` e não duplica.
   Nesse caminho não há linha em voo da conversa, porque a conversa ainda não
   existe; quem trava é o registro `enviando` com a chave única.
4. A adoção pela leitura passa a aceitar `autor = 'sistema'` quando a linha
   esperando é `davinci_auto`, para o caso do `auto_reply`. O primeiro critério
   continua sendo o `externo_id`.

### 7.5 A régua reconhece a mensagem do DaVinci

- `constantes.e_automatica_pelo_payload`: também vale `isinstance(payload.get("automacao"), dict)`
  e o **cartão do pedido da campanha** (Shopee `order`/`openapi`; TikTok
  `ORDER_CARD` com papel `CUSTOMER_SERVICE`). `gravar.automatica_pelo_payload_sql`:
  `jsonb_typeof(payload -> 'automacao') = 'object'` (o `->`, não o subscrito: sobre
  um valor com cast o subscrito vira sintaxe errada). Como quase todo
  mundo já passa o payload, isso cobre de uma vez a fila (`recalcular`,
  `recalcular_conversa`), o `_aposentar_rascunho`, a métrica de tempo de
  resposta (`routers/atendimento.py`, `~mensagem_automatica_sql(texto, payload)`)
  e os exemplos da IA (`ia.py`, `anterior.payload`). O `ia.py` que passa só o
  texto (`candidatas.c.texto`) já filtra antes pela origem (pessoa ou externo),
  então `davinci_auto` não entra.
- Turno só de cartão (`_fechava_a_vez`): hoje a figurinha 0007 da dúvida 26 h, o
  cartão do pedido da campanha e o "já segue" do TikTok fecham esse turno. Para a
  fila ficar **igual** depois da troca, `_fechava_a_vez` (e o SQL do
  `recalcular_conversa`) vale para `davinci_auto` só nas partes equivalentes:
  `constantes.AUTOMACOES_QUE_FECHAM_A_VEZ` = (`tiktok_convite`, texto),
  (`shopee_duvida_26h`, figurinha), (`shopee_pedido_recebido`, cartão),
  (`shopee_entregue`, cartão). As outras continuam como o robô do Duoke, que
  nunca fechou a vez.
- Os textos padrão que MUDAM em relação ao Duoke (o "aguarde" em pt-BR, o nome
  dentro do entregue e do pós, o convite sem o nome) entram em
  `MENSAGENS_DAVINCI_FORA`: é a rede para quando a leitura não adota a nossa
  linha e a mensagem volta como `externo` sem a marca.
- `RESPOSTAS_AUTOMATICAS`, para o Duoke enquanto ele roda e para as assinaturas.
  Acrescenta as opções 1, 2, 3 e 5 da Shopee e as 1, 3 e 5 do ML, que hoje
  contam como "a loja respondeu" (medido de novo em 05/10, 14 dias: na Shopee
  125 das 152 saíram exatamente 12 h depois do menu, nenhuma entre 5 min e 12 h),
  e "oi! 🧳✨ que alegria saber que sua mala". **Tira** `bom dia! ficou alguma
  duvida em que eu possa te ajudar`: a medição mostra que é pessoa (nenhuma a
  12 h do menu, 7 h às 13 h, espaçamento humano), e hoje ela esconde uma
  resposta real da equipe. O teste `test_atendimento_resposta_automatica.py`
  confere o Python contra o SQL.
- **O efeito na parte 1 (fila, métrica, IA) já vale em produção** (a régua
  subiu junto, no commit `32d45b6b`, de título sobre a reclamação do ML).
  Medido pela revisão (05/10, SELECT só de contagem, 7 dias de mensagens da
  loja): na Shopee, 2.018 passam a automáticas (~1.943 cartões de pedido
  `openapi` e ~75 respostas das opções) e 76 passam a pessoa (o "bom dia!
  ficou alguma dúvida"), em 1.870 conversas; no TikTok, 157 (os `ORDER_CARD` de
  `CUSTOMER_SERVICE`); no ML, 11. A fila "Falta responder" **cresce** (a
  conversa cuja última fala da loja era só o cartão volta a esperar a pessoa) e
  a **mediana de 1ª resposta muda**. O `recalcular_conversa` só roda quando
  chega mensagem nova: as conversas paradas ficam com o carimbo da régua velha,
  e a fila mistura as duas. Para alinhar de uma vez, **com o OK do Eduardo**:
  `uv run python -m scripts.atendimento_fila_recalcular` (seco: só conta
  quantas mudam, entram e saem da fila) e, aprovado, `--gravar` (lotes de 200,
  conversa travada; nada sai para comprador). Se ele não aprovar a régua nova,
  o caminho é reverter só a parte da régua (o cartão, as opções e o "bom dia")
  sem mexer no motor.

### 7.6 Teto e travas contra duplicar

1. `UNIQUE (automacao, integration_id, chave)` + `ON CONFLICT DO NOTHING`.
2. Uma rodada por vez: trava `atendimento:automacoes:rodada` no Redis, TTL 110 s
   com o cron a cada 2 min.
3. A condição "já mandado" é conferida de novo na decisão, não só na descoberta.
4. O registro `enviando` é commitado antes da plataforma. Um `enviando` com mais
   de 10 min vira `revisar`, como o `aposentar_envios_presos`, e **nunca** se
   retenta `revisar`.
5. A linha em voo por conversa e o `envio_repetido` do `enviar.py`.
6. O teto por loja e família (chave do `.env`) e o da regra (`teto_dia`) →
   `pulado: teto_dia`, com log `warning`; o teto por comprador (3 mensagens
   normais sem resposta dele) → `teto_comprador`. Também há no máximo 200
   decisões por rodada, **no máximo 50 da mesma loja × automação** (a linha que
   espera a leitura da loja volta igual e seria escolhida de novo: sem o limite
   por regra, a fila parada de uma loja segurava o menu das outras até vencer);
   o resto fica para a próxima.
7. Recusa temporária (`conversa_ocupada`, `envio_em_andamento`): continua
   `agendado` e tenta na rodada seguinte, até a validade. Recusa com código da
   plataforma → `falhou`, sem retentar.
8. Operação: o INSERT de muitas linhas vai em lotes de 500 (a Logística
   reinsere ~600 pedidos de 3 dias a cada 10 min; o asyncpg recusa mais de
   32.767 parâmetros, ~1.700 linhas — a rodada inteira cairia); o registro
   guarda 90 dias (a limpeza roda às 3h30 de Brasília, 5.000 por vez, nunca
   `agendado`/`enviando`).

### 7.7 A correção do `ia._humano_respondeu_recente`

Hoje ele conta `origem in (externo, davinci_humano)`, ou seja, o menu, o
"aguarde", a campanha do TikTok e a senha da devolução contam como pessoa, e a
IA fica calada por 24 h (89 de 104 conversas, §2.6). Com a régua:

```python
async def _humano_respondeu_recente(session: AsyncSession, conversa: AtendimentoConversa) -> bool:
    """Alguém da equipe (pelo DaVinci ou por fora) falou nas últimas 24 h?

    Mensagem automática (`gravar.mensagem_automatica_sql`: robô e campanhas do
    Duoke, a senha da devolução, a figurinha 0007, os cartões da Shopee e o
    que o próprio motor de automações manda, `davinci_auto`) não é pessoa.
    """
    desde = datetime.now(UTC) - JANELA_HUMANO
    n = await session.scalar(
        select(func.count())
        .select_from(AtendimentoMensagem)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_LOJA,
            AtendimentoMensagem.status != MSG_FALHOU,
            AtendimentoMensagem.origem.in_((ORIGEM_EXTERNO, ORIGEM_HUMANO)),
            ~gravar.mensagem_automatica_sql(AtendimentoMensagem.texto, AtendimentoMensagem.payload),
            _momento_col() >= desde,
        )
        # (implementado assim em `ia.py`; a régua agora pega também o cartão do
        # pedido da campanha, que calava a IA por 24 h depois de cada pedido)
    )
    return bool(n)
```

Teste (`test_humano_respondeu_recente_nao_conta_mensagem_automatica`): uma
conversa só com menu do Duoke, "aguarde", o cartão do pedido da campanha, a
mensagem do motor e uma resposta que falhou → `False`. Com uma resposta
digitada → `True`.

---

## 8. Tela: aba "Automáticas" no /atendimento

### 8.1 API (`routers/atendimento_automacoes.py`) — implementada

O mesmo `dependencies=[Depends(_so_admin)]` dos outros routers do atendimento
(só os admins de `ATENDIMENTO_USUARIOS`; o `test_atendimento_so_admin` cobre as
rotas novas sozinho), leitura com `_view`, mudança com `_edit`, escopo por
equipe. Registrado no `main.py`. Nenhuma rota devolve texto de comprador; o
texto que aparece é o da REGRA (o modelo da loja) e o da prévia com o nome de
exemplo `maria.silva`.

**`GET /api/atendimento/automacoes?plataforma=shopee|tiktok|ml&dias=7`**

```jsonc
{
  "chaves": {"leitura_ativa", "motor_ativo", "envio_automacoes", "envio_geral",
             "shopee_auto_reply", "shopee_auto_reply_adaptador",
             "shopee_mensagens_comprador", "teto_dia"},
  "faixa": {"nivel": "info|aviso|erro", "texto": "Modo seco: ..."},
  "dias": 7,
  "motivos": {"codigo": "texto da tela"}, "divergencias": {...}, "alertas": {...},
  "automacoes": [{
    "codigo", "plataforma", "canal", "nome", "descricao", "tipo", "gatilho", "alvo",
    "familia", "campanha", "travada", "diferenca_combinada", "atraso_min",
    "validade_min", "janela_inicio", "janela_fim", "condicoes_padrao",
    "placeholders": {"comprador": "..."}, "seguinte",
    "total_24h": CONTA | null, "total_periodo": CONTA | null,
    "lojas": [{
      "integration_id", "loja", "integracao", "canal_status", "canal_modo",
      "sem_acesso", "duoke_hoje",
      "regra": {"id"|null, "modo", "padrao", "partes", "atraso_min", "janela_inicio",
                "janela_fim", "condicoes", "teto_dia", "versao", "ligada_desde",
                "enviar_desde", "disjuntor_em", "disjuntor_motivo", "atualizado_em"},
      "h24": CONTA | null, "periodo": CONTA | null,
      "ultimo": {"devido_em", "estado", "motivo"} | null,
      "pode_enviar": bool, "por_que_nao_enviar": ["envio_desligado", ...],
      "pode_trocar": bool, "por_que_nao_trocar": ["poucos casos (3 de 30)", ...]
    }]
  }]
}
```

`CONTA` = `{total, simulado, enviado, pulado, falhou, agendado, pendente,
bateu_mandou, bateu_nao_mandou, so_davinci, so_davinci_2d, so_duoke, combinada,
alertas, texto_invalido, envio_desligado, diferenca_mediana_s, atraso_mediana_s,
motivos{codigo:n}, casos, precisao, cobertura, concordancia, pode_trocar,
por_que_nao[]}` (precisão/cobertura/concordância de 0 a 1, `null` sem caso; nas
opções a precisão e a concordância são `null`, §6.5). O `pode_trocar` da LOJA
(`lojas[].pode_trocar`) é sempre o de 7 dias, qualquer que seja o `dias`; o
`pode_trocar` do `total_periodo` é só a soma das lojas (informação).

**`GET /api/atendimento/automacoes/registro?automacao=&integration_id=&estado=&duoke=&so=so_davinci|so_duoke|bateu|alerta|combinada&limite=200&antes=<iso>`**

`{"linhas": [{id, automacao, automacao_nome, plataforma, integration_id, loja,
alvo, pedido, conversa_id, evento_em, visto_em, devido_em, decidido_em, estado,
modo, motivo, motivo_texto, erro, duoke, duoke_em, duoke_diferenca_s,
divergencia, divergencia_texto, alerta, alerta_texto, tentativas,
regra_versao}], "proximo": <devido_em da última> | null}` — as mais novas
primeiro; `antes` pagina. Até 500 por página.

**`GET /api/atendimento/automacoes/estatisticas?dias=7&automacao=&integration_id=`**

`{"desde", "ate", "linhas": [{automacao, integration_id, loja, ...CONTA}],
"por_automacao": [{automacao, ...CONTA}]}`.

**`PATCH /api/atendimento/automacoes/{automacao}/{integration_id}`**

Corpo (tudo opcional): `{modo, partes: [{tipo: texto|cartao_pedido|figurinha,
texto?, figurinha?, pacote?}], atraso_min, janela_inicio: "HH:MM",
janela_fim: "HH:MM", sem_janela, condicoes: {chave do catálogo: valor},
teto_dia, sem_teto, desliguei_no_duoke, troca_sem_criterio}`. Cria a regra se
não houver.
Respostas de erro (`detail.code`):

- 409 ao pôr em `enviar`: `envio_desligado` (a chave `ATENDIMENTO_AUTOMACOES_ENVIO`),
  `envio_geral_desligado`, `campanha_sem_auto_reply`, `shopee_mensagens_desligadas`,
  `loja_sem_acesso`, `sem_texto` (com `motivos` = a lista toda);
- 422: `confirmar_duoke` (falta "desliguei no Duoke"), `criterio_nao_passou`
  (o critério desta loja em 7 dias não passou e falta `troca_sem_criterio`; com
  `motivos` = o `por_que_nao` da loja), `texto_invalido` (com
  `motivos` do validador, renderizado com o nome de exemplo e sem ele),
  `sem_texto` (opção 4), `janela_invalida`, `condicao_desconhecida`,
  `condicao_invalida`;
- 404: `automacao_nao_encontrada`, `loja_nao_encontrada` (fora do escopo ou de
  outra plataforma).

Sobe a `versao` quando muda partes ou condições; carimba `ligada_desde` ao sair
de `desligado` e `enviar_desde` ao ir para `enviar` (limpa o disjuntor); na troca
`simular → enviar`, rearma (§6.6). Devolve `{integration_id, automacao, regra,
rearmadas, pode_enviar, por_que_nao_enviar}`.

**`POST /api/atendimento/automacoes/{automacao}/simular-nas-lojas-do-duoke`**

Cria em `simular` a regra ausente e liga a `desligado`, só nas lojas onde o Duoke
manda hoje. Nunca mexe em `simular` nem em `enviar` (numa loja já trocada,
rebaixar deixaria o comprador sem a mensagem). Devolve `{criadas, ligadas,
mantidas}`.

**`POST /api/atendimento/automacoes/previa`** `{automacao, integration_id?, partes?}`

Renderiza com o nome de exemplo e devolve `{partes, sem_nome, motivos,
comprador_exemplo}`. Não envia nada.

### 8.2 Tela (`components/AtendimentoAutomaticas.vue`; aba `automaticas` em `pages/atendimento.vue`) — implementada

- **Aba** "Automáticas" (ícone `Bot`) entre "Respostas prontas" e "Métricas";
  `?tab=automaticas` abre direto nela. A mesma trava da página (admin +
  `ATENDIMENTO_USUARIOS`); quem não tem `atendimento.edit` vê tudo, sem mudar.
- **Faixa** no topo: o texto é o da API (`faixa`: azul no modo seco, âmbar com
  o motor desligado, vermelho com regra em ENVIAR e a chave desligada) e, em
  chips, as chaves: motor, envio das automáticas, envio geral, resposta
  automática da Shopee (só na Shopee; com a chave ligada e sem o envio por ela
  no DaVinci, em âmbar: "falta o envio por ela") e o teto do dia, cada uma com a variável
  do `.env` no title. Embaixo, uma linha: "Enviar está travado em todas as
  lojas: …" enquanto a chave estiver desligada.
- **Uma plataforma por vez** (Shopee | TikTok | Mercado Livre, como o
  `?plataforma=` da API) e o período (7, 15 ou 30 dias); os dois ficam
  lembrados neste navegador (só conveniência; sem armazenamento, o padrão).
  Busca de loja e o filtro "todas / só ligadas / só onde o Duoke manda hoje".
- **Uma seção por automação**, fechada por padrão (são ~14 × ~20 lojas na
  Shopee; buscar ou filtrar abre todas; "abrir todas"). O cabeçalho tem o nome,
  o gatilho, o atraso, o horário e os selos (campanha, diferença combinada com
  a explicação, "sem texto" na opção 4); embaixo, os totais: quantas mandaria
  em 24 h e no período, a **% que bateu** (a menor entre precisão e
  cobertura; nas opções, a cobertura), "só DaVinci", "só Duoke" e os alertas
  (clicáveis: filtram o registro), **"N de M lojas prontas para trocar"** (a
  troca é por loja; a soma das lojas é só informação, no title), quantas lojas
  em cada modo e o botão **"simular nas lojas do Duoke"** (pede
  confirmação; desabilitado quando não há loja do Duoke desligada; nunca mexe
  em Simular/Enviar — a API garante).
- **Uma linha por loja**: o nome e os selos (Duoke hoje, sem acesso, padrão =
  sem regra salva, "ENVIAR sem envio" em vermelho, disjuntor e, em Simular, o
  selo da troca DA LOJA — "pronta para trocar" / "troca: ainda não", o porquê
  dos últimos 7 dias no title), o **modo** (Desligado / Simular / Enviar),
  quantas mandaria em 24 h e no período, a % que bateu (a menor entre precisão
  e cobertura; verde a partir de 95%, âmbar de 85%, vermelho abaixo; o title
  tem a conta inteira: precisão, cobertura, concordância, bateu, só DaVinci de
  2 dias, só Duoke, combinadas, pendentes, alertas, a mediana do horário em
  relação à nossa, o atraso real do DaVinci, a sobra das opções, os motivos e o
  porquê da troca), "só DaVinci" e "só Duoke" (filtram o registro) e o último.
  A % é cortada para baixo: 94,96% aparece 94,9% (nunca um "95%" verde falso).
- **Enviar, três travas**: a opção vem desabilitada, com o motivo em
  português (`por_que_nao_enviar` da API), enquanto a API disser que não pode;
  se mesmo assim chegar, a tela não abre nada; liberada, abre na linha o quadro
  "Desliguei esta automação desta loja no Duoke" e só manda o PATCH com a
  marcação (`desliguei_no_duoke`). Se o critério da troca da loja não passou,
  o quadro lista os motivos e pede também "Sei que o critério não passou nesta
  loja e quero trocar mesmo assim" (`troca_sem_criterio`); se a API recusar
  pelo critério (a lista estava velha), o quadro passa a pedir. Nas opções, o
  quadro diz quantas vezes o DaVinci responderia a mais que o Duoke no período.
  A API recusa de novo (409/422) e a tela mostra o porquê. Sair de Enviar pede confirmação (o comprador pode ficar sem).
  Desligado ↔ Simular é direto (modo seco). Depois de cada PATCH, a lista é
  relida (uma atualização automática que já tinha saído não traz o modo antigo).
- **Editor da regra** (o lápis da linha): o texto de cada parte (o cartão do
  pedido e a figurinha aparecem como "vai junto"), as **{lacunas}** com a
  explicação da API (clicar insere no cursor; "Oi, {comprador}! …" sem nome sai
  "Oi! …"), o contador como sai (com o nome de exemplo; o limite do canal: ML
  350), a **prévia** pelo backend (`POST /previa`: com o nome de exemplo, sem o
  nome quando muda, e os motivos do validador — espera a pessoa parar de
  digitar), o atraso (min), o horário de Brasília (ou o dia todo), o teto da
  loja (vazio = o geral) e as condições do catálogo com nome em português. O
  que segura o salvar antes da API: texto vazio, lacuna que não existe, passar
  do limite, atraso fora de 0–10080, horário invertido, teto fora de 0–6000,
  condição numérica ≤ 0. O PATCH leva **só o que mudou**. O 422 do validador
  aparece no editor, com os motivos.
- **Registro recente** embaixo (200 por página, "carregar mais"), com filtros
  de automação, loja, estado e comparação (bateu, só DaVinci, só Duoke, com
  alerta, diferença combinada, ainda conferindo). Sem filtro, mostra todas as
  plataformas (o chip diz qual). Cada linha: quando, automação e loja (com a
  versão da regra), para quem (conversa, comprador ou pedido nº), o estado (e o
  motivo), o que o Duoke fez (e quanto antes ou depois), a comparação, o
  alerta e a diferença combinada — **nenhum texto**, nem do comprador nem o
  renderizado. A conversa abre na Caixa.
- Carregando, erro ("tentar de novo"; erro da atualização automática não apaga
  a tela) e vazio (sem loja da plataforma; registro vazio diz se o motor está
  desligado). A conta se atualiza sozinha a cada 1 min com a aba visível; o
  registro, no "atualizar" e nos filtros.
- Tema escuro (cores com a versão `dark:`; os controles nativos com
  `color-scheme: dark`) e tela estreita: no celular, as linhas da loja e do
  registro empilham, com o rótulo de cada número; a grade de colunas só a
  partir de `md`.
- **Na conversa**, a origem `davinci_auto` tem nome ("Automática · DaVinci"),
  cor e explicação; o balão "a conferir" (Saiu / Não saiu) já vale para ela (não
  filtra a origem; a API aceita a nova); o "tentar de novo" à mão continua só
  para pessoa/IA — a automática é da regra, não de quem está na conversa.
- **Teste** `apps/web/tests/atendimento-automaticas.cjs`: o contrato campo a
  campo com o backend (as 6 rotas, cada saída, o corpo do PATCH, os códigos de
  erro e de "por que não enviar", estados, Duoke, gatilhos, condições, filtros
  do registro), as regras puras, a tela com a API falsa (o que ela chama e que
  nunca chama nada de envio, o modo e as três travas do Enviar, o editor com a
  prévia e o PATCH só com o que mudou, o registro e os filtros, preferências
  com e sem armazenamento) e renderizada (carregando, erro, vazio, faixa,
  Enviar travado com o motivo, nada do comprador na tela, tema escuro, tela
  estreita), a página e a conversa. Conferido com 32 mutações (todas pegas;
  11 delas das correções de 05/10: o selo e o "sei disso" por loja, a % pela
  menor, o chip do auto_reply, a sobra das opções, o atraso real).
  Typecheck e build numa cópia isolada: só os 7 erros antigos.

---

## 9. O motor no código (implementado)

- `alembic/versions/0366_atendimento_automacoes.py`: as duas tabelas e a semente.
- `app/models/atendimento.py`: `AtendimentoAutomacaoRegra` e `AtendimentoAutomacaoRegistro`.
- `services/atendimento/automacoes_catalogo.py` (PURO): catálogo, textos padrão,
  lojas do Duoke, assinaturas, `classificar` (a mensagem vira sinais, sem
  texto), `gatilhos_da_conversa`, `fatos_da_conversa`, `decidir`, horário,
  validade, `renderizar` + validador, semente.
- `services/atendimento/automacoes.py`: `rodada(agora, motor_desde)` —
  `descobrir_mensagens` (40 min pelo `created_at`, com o `enviada_em` indexado),
  `descobrir_pedidos_pagos` (Bling pelo `data` indexado + `created_at`),
  `descobrir_logistica` (a cada 10 min), `aposentar_enviando_presos`,
  `decidir_vencidas` (até 200; fatos em lote: reclamações, Bling, Logística,
  índice, avaliações, linha do tempo de 8 dias) — e o worker
  `atendimento_automacoes` (trava no Redis por schema, 110 s; com o Redis fora,
  não roda). O log da rodada leva as contagens e a duração de cada fase (`ms`).
  "Desde quando o motor roda" fica no Redis (`motor_desde`); desligado, apaga.
- `services/atendimento/automacoes_comparar.py`: `comparar` (pendentes por
  conjunto, só Duoke, disjuntor), `estatisticas`, `resumir`, `somar`.
- `services/atendimento/enviar.py`: `enviar_automatica` (§7.2).
- `constantes.py` / `gravar.py`: `ORIGEM_AUTO`, a régua (marca, cartão da
  campanha, opções, textos novos, sem o "bom dia"), `_fechava_a_vez`.
- `ia.py`: `_humano_respondeu_recente` (§7.7).
- `config.py`: as quatro chaves (§7.1). `historico/sql.py`: o registro fora.
- `worker.py`: `atendimento_automacoes` em `functions` e no cron dos minutos
  pares, `timeout=110`.
- `routers/atendimento_automacoes.py` + `main.py` (§8.1).

### 9.1 Testes (feitos)

- **Correções da revisão de 05/10 (§14)**, nos dois arquivos: o modo seco
  medindo o DaVinci sozinho (os cortes, a contraprova da opção tardia do Duoke,
  a opção já respondida); no modo enviar, pedido recebido, entregue e pós
  conferindo o Duoke pelo cartão ou pela conversa do pedido (um lote de 3:
  nenhum sai, o 1º dispara o disjuntor e os outros decidem em simular); o
  horário na decisão e no rearme (o entregue rearmado às 22h sai às 9h, pela
  resposta automática); a campanha que não sai com a chave sem o envio por
  auto_reply, e que, com ele, sai por ele e nunca pelo `enviar_texto`; a
  transição (passados os 3 dias, não espera a leitura); a diferença pela hora
  em que a nossa sairia e o atraso real; o critério por loja (lista e PATCH);
  a fila de uma regra que não segura as outras; o INSERT em lotes; a limpeza
  do registro; a seguinte no horário dela; e o script da fila
  (`scripts/atendimento_fila_recalcular.py`: o seco só conta, o gravar grava).
  Conferido com 16 mutações nas correções (todas pegas: tirar a conferência do
  Duoke no pedido, o horário na decisão, a trava do adaptador, o modo seco
  sozinho, o critério da loja, a diferença, o limite por regra, a transição, o
  rearme, a seguinte, a limpeza, o disjuntor na hora, o atraso, o lote).
- `tests/test_atendimento_automacoes_catalogo.py` (puro, 176 + 5): todo texto padrão
  no validador (com nome, sem nome, nome que parece telefone), ML ≤ 350 e
  ISO-8859-1, assinatura × modelo do levantamento e × pessoa, a régua reconhece
  tudo o que o motor manda, dígito, classificação, cada gatilho (menu e sessão,
  rajada e empate no mesmo segundo, idempotência com o registro, opção com e sem
  sessão, "aguarde" com o 2º no mesmo turno e o do TikTok pela última fala,
  convite uma vez e pela pergunta pronta, ciclo da dúvida, TikTok pela última
  mensagem), cada condição e exclusão de `decidir` (parametrizado), horário,
  validade, janela de comparação, semente.
- `tests/test_atendimento_automacoes_motor.py` (com banco, 32 + 16): cada fonte
  (mensagem, Bling, Logística com o horário e o concluído), uma vez só,
  `atrasado`, teto; **simular nunca chama a plataforma** (5 combinações de
  chave × modo; adaptador, cliente, `enviar_automatica`, `enviar_resposta` e o
  `httpx.AsyncClient.send` levantam; `atendimento_mensagens` não ganha linha);
  enviar uma vez com a marca e nunca de novo, esperar a leitura, Duoke mandou →
  pula e dispara o disjuntor, `enviando` preso → `revisar` sem retentar, campanha
  sem `auto_reply`, regra relida e o freio geral no `enviar_automatica`;
  comparador (mandou com a diferença, não mandou, leitura parada = pendente, a
  nossa não casa, só Duoke e fim de sessão, cartão do pedido, alerta de
  devolução, disjuntor), `resumir`, `_humano_respondeu_recente`, fila com
  `davinci_auto`, Histórico, e as rotas (lista, registro e estatísticas sem
  texto, PATCH com cada recusa, versão e rearme, simular nas lojas do Duoke sem
  mexer em `enviar`, prévia).
- `test_atendimento_migration.py` (0366 × model, a semente e o downgrade),
  `test_atendimento_resposta_automatica.py` (opções, "bom dia", textos novos,
  cartões e marca — Python × SQL).

## 10. O que só o painel do Duoke fecha (pendente)

Conferir com o Eduardo logado. Só olhar e tirar print, sem salvar nem ligar ou
desligar nada:

1. **Opção 4 (troca de endereço)**: o texto. A regra fica `desligado` até lá.
2. **Robô de menu**: dispara em toda mensagem ou só na 1ª? A sessão é de 12 h? A
   opção 6 transfere para um atendente? Tem horário? Por que a ATV está fora?
   O ML usa o mesmo robô?
3. **"Aguarde"**: os 10 min e os intervalos de 4 h (ATV Shopee) e 8 h (TikTok).
   Para quando a equipe responde? Em que lojas está ligado?
4. **Convite**: uma vez por comprador para sempre, ou por período (6% voltam
   depois de 7 dias)? Quem manda o "Já segue" em TikTok Injox e JLAS?
5. **"Ficou alguma dúvida"**: a condição exata de "nunca comprou". Conta da 1ª
   mensagem (Shopee) ou da última (TikTok)? Para quando alguém responde? Quando
   o ciclo recomeça (aqui ficou 7 dias)?
6. **Pedido recebido**: conta do pagamento ou do "a enviar"?
7. **Entregue**: a condição "não concluído" e o horário do lote.
8. **Pós-conclusão**: a condição "sem avaliação". A figurinha é sorteada?
9. **Lojas**: quais estão conectadas em cada regra. A Aguiar foi conectada em
   04/10 (1 convite às 22h52)?
10. **Regras desligadas ainda cadastradas** (pagamento pendente, pedido enviado,
    o robô antigo "sou a Inteligência Artificial", o "Passando rapidinho"),
    para ninguém religar sem querer.
11. **Limites e proteções**: teto por dia, lista negra, não mandar a quem tem
    reclamação, "não perturbe".
12. **Duoke ou UpSeller**: os dados não separam. Também conferir em "Apps
    autorizados" do Seller Center quem tem permissão de chat. É trava da troca
    (§6.5): desligar no Duoke não para outro remetente.
13. **Desligar a regra no Duoke cancela o que ele já agendou** (o 26 h, o lote
    de madrugada do entregue, o pós de 4 h)? Se não, o disjuntor pega, mas
    depois de uma mensagem em dobro.
14. **O "re-menu" de fim de sessão** (menu 12 h depois do anterior sem mensagem
    nova do comprador, §6.3): é regra do Duoke? O DaVinci copia?
15. **O pós-conclusão só vai a quem tem conversa** (a simulação mostrou 5 de 5
    sem conversa e sem pós do Duoke): conferir a regra.
16. **O "aguarde" do TikTok** só com o comprador falando por último (o "já
    segue" conta como fala da loja): conferir a regra no painel.
17. **ATV na Shopee: o texto do menu sem o robô** (revisão de 05/10): 79
    mensagens que começam com o menu do Duoke em 6 dias, nenhuma até 3 min
    depois de uma mensagem do comprador, de origem `externo` pela openapi, uma
    a uma, das 07h50 às 12h e das 23h às 00h30, em conversas sem resposta de
    pessoa — parece resposta pronta de atendente. A ATV não está no menu do
    Duoke (§4.1), então a regra dela fica `desligado` e o comparador não as vê.
    Se for pessoa usando o modelo, a régua conta essas respostas como
    automáticas (a fila e a IA erram nelas); se for automação, entra no
    catálogo.

Também pendente, mas fora do painel: o teste do `send_autoreply_message` (§7.3)
e a importação de 90 dias de pedidos da Shopee (§2.4). As duas mexem em
produção e só com o OK do Eduardo.

---

## 11. Ordem de implementação sugerida

1. **Feito (05/10):** migration com a semente, models, catálogo, motor com
   descobrir/decidir/comparar, envio atrás das chaves, régua, correção da IA,
   API, tela e testes; as correções da revisão (§14). O backend da 1ª versão
   já está em produção com o motor ligado em modo seco (§14.1); falta subir as
   correções e a tela (§14.3).
2. Uma semana de comparação. Ajustar condições e janelas pelo que só o Duoke ou
   só o DaVinci mandou, e fechar o §10 no painel.
3. Adaptadores (cartão, figurinha, envio sem conversa, `enviar_auto_reply` se
   der — com a adoção da volta como `sistema`, §7.3) e `enviar_automatica`,
   ainda com `ATENDIMENTO_AUTOMACOES_ENVIO=false`.
4. A troca, uma automação e uma loja por vez (§6.6). Começar pelas que só
   respondem conversa: opções na hora, depois menu e "aguarde" da ATV. Depois o
   pedido recebido, o entregue e as campanhas.

---

## 12. Decisões do Eduardo

1. **Textos que mudam em relação ao Duoke**:
   - o "aguarde" em português do Brasil (§4.3), ou dar à ATV o mesmo menu das
     outras lojas;
   - o nome dentro do texto no entregue e no pós-conclusão, em vez da bolha só
     com o nome;
   - a opção 2 do ML sem o "Atenciosamente.", para caber em 350 caracteres;
   - o horário do entregue: 09h–20h todos os dias, em vez da madrugada.
2. **Campanhas como mensagem normal**: se a Shopee não deixar o DaVinci mandar
   como resposta automática (`auto_reply`), o pedido recebido, o entregue, o
   pós-conclusão, o convite e a dúvida podem sair como mensagem normal? **Risco**
   (a crítica): a FAQ do Chat API proíbe "proactive order updates" (o
   `config.py` já anota, no `shopee_mensagens_comprador`), e um comprador calado
   receberia mais de 5 mensagens normais em poucos dias — o
   `reach_5_message_limit` e o bloqueio de mensagem repetida em 24 h podem
   travar a mensagem da equipe (NF, rastreio) para ele. Hoje o Duoke manda como
   `auto_reply`, provavelmente com permissão de parceiro. **O código não deixa
   ir para `enviar` sem o `auto_reply` confirmado**; a recomendação passa a ser:
   testar a permissão primeiro e só então trocar as campanhas.
3. **"Ficou alguma dúvida" depois de resposta da equipe**: continua indo, como
   no Duoke (96% dos casos), ou para de ir? O padrão é igual ao Duoke, para a
   comparação ficar limpa.
4. **Critério e ordem da troca** (o mesmo da §6.5): **por loja**, em 7 dias,
   precisão **e** cobertura **e** concordância ≥ 95% (a % que a tela pinta é a
   menor entre precisão e cobertura), com pelo menos 30 casos, sem alerta, sem
   "só DaVinci" sem explicação nos últimos 2 dias e sem `texto_invalido`; nas
   opções, a cobertura (e o OK dele para a sobra: o DaVinci responde na hora a
   quem o Duoke não responde). A soma das lojas não decide nada. Trocar sem o
   critério da loja é possível, mas só com a marcação explícita na tela. A
   ordem é: opções e menu (juntos, ou opções primeiro se o painel do Duoke
   separar, §6.5) → "aguarde" → pedido recebido e entregue → campanhas (estas,
   só com o envio por resposta automática). Quem desliga no Duoke é ele, na
   mesma hora em que liga no DaVinci. Decisão dele: o "nenhum só DaVinci nos
   últimos 2 dias" vale assim (é o mais duro)?
5. **Menu em reclamação/devolução**: igual ao Duoke (32 dos 1.658 menus da
   semana foram para conversa com reclamação ou devolução aberta), menos quando
   uma pessoa já está atendendo a disputa (últimas 24 h) — o padrão do código
   (`pular_em_disputa_com_pessoa`). Manter, ou pular sempre na disputa?
6. **O freio único**: `ATENDIMENTO_ENVIO_ATIVO` passa a segurar também as
   automáticas (a 1ª versão não segurava). As caixas continuam em `observar`,
   então ligá-lo não deixa a pessoa enviar.
7. **Autorizações em produção** (regra 5 do CLAUDE.local.md), cada uma na sua
   hora:
   - `ATENDIMENTO_AUTOMACOES_ATIVA=true` depois do deploy (modo seco);
   - a importação de 90 dias de pedidos da Shopee, para o "nunca comprou";
   - o teste de permissão do `send_autoreply_message` com corpo vazio (e, se
     passar, `ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY=true`);
   - mais tarde, `ATENDIMENTO_AUTOMACOES_ENVIO=true` **e** `ATENDIMENTO_ENVIO_ATIVO=true`
     (o freio único).
   - (05/10, revisão) o motor já está ligado em produção (§14.1): confirmar
     que o `ATENDIMENTO_AUTOMACOES_ATIVA=true` foi decisão dele; a
     `ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY` sozinha não libera mais as
     campanhas (falta o envio por `auto_reply` no adaptador); e rodar o
     `scripts.atendimento_fila_recalcular --gravar` (a fila com a régua nova,
     §7.5) só com o OK dele.
8. **A sessão do menu depois da opção** (§13.3): quando o comprador volta entre
   12 h e 24 h depois do menu, o Duoke ainda está na sessão dele (a resposta da
   opção dele sai 12 h depois do menu) e não manda menu; o DaVinci (que
   responde a opção na hora) manda. Copiar a sessão do Duoke (e o "re-menu" de
   fim de sessão) ou manter o menu de novo? Hoje: menu de novo — é o que tira o
   menu da Shopee de 99% para 93% de precisão.

---

## 13. O que foi implementado e a simulação contra 7 dias de produção (05/10)

### 13.1 Implementado (backend; a 1ª versão já subiu sem esta etapa — §14.1)

Tudo da §9: migration 0366 com a semente, models, catálogo puro, motor (cron
nos minutos pares, trava no Redis, duração por fase no log), gatilhos das três
fontes, registro, modo seco, comparador (com o "só Duoke", as diferenças
combinadas, os alertas e o disjuntor), o envio atrás das chaves (`enviar_automatica`,
só texto em conversa existente), a régua (marca `davinci_auto`, cartão da
campanha, opções, textos novos, sem o "bom dia"), a correção do
`ia._humano_respondeu_recente`, o Histórico (registro fora, regras dentro) e a
API da aba (§8.1). Os testes da §9.1 passam no Python 3.14 e no 3.12. A bateria
inteira (3.14: 7.150 passam) não tem falha nova: as 150 que falham são as mesmas
do HEAD, medido rodando o HEAD numa cópia (no 3.12, mais a
`test_cliente_do_sdk_sem_nova_tentativa_e_sem_ler_o_ambiente`, que também falha
no HEAD com o 3.12).

Mudanças em relação ao desenho, vindas da crítica e da simulação:

- **Freio único**: `ATENDIMENTO_ENVIO_ATIVO` segura também as automáticas;
  `SHOPEE_MENSAGENS_COMPRADOR` também; campanha da Shopee só vai para `enviar`
  com o `auto_reply` confirmado (chave nova `ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY`).
- **A troca não duplica**: no modo enviar, "já mandado" conta o Duoke depois do
  gatilho, com a leitura da loja em dia; rearme só do que o Duoke não mandou;
  disjuntor automático.
- **A semente está na migration** (não no botão).
- **Menu**: a rajada e o cartão + texto no mesmo segundo dão UM menu (a 1ª
  versão dava 3 a 4); quem já conhece o menu e digita a opção recebe a resposta
  na hora, sem menu novo (o "robô liberado" do Duoke, medido).
- **"Aguarde"**: o 2º no mesmo turno quando fecha o intervalo; no TikTok, só com
  o comprador falando por último (o "já segue" conta como fala da loja).
- **Convite da Shopee**: também na pergunta pronta do chat (`bundle_message`).
- **Entregue e pós**: nada para quem devolveu (aberta ou encerrada); o pós só
  com conversa; a conversa achada também pelo cartão do pedido da campanha.
- **Comparador**: precisão e cobertura separadas, alertas de tolerância zero,
  `nao_mandou` só com a leitura em dia, "só Duoke" de 48 h com espera por
  automação, as diferenças combinadas da §6.3.

### 13.2 Como a simulação foi feita

- **Dados**: 7 dias de produção (28/09 16h a 05/10 16h UTC), exportados por
  SELECT em transação só de leitura (`ssh davinci-prod … pg18`), **sem texto de
  comprador**: das mensagens, só autor, origem, horários, o pedaço do payload que
  a régua usa, o dígito da opção (quando a mensagem é só o número), a
  assinatura do modelo do Duoke (calculada no SQL) e o resultado da régua; o
  texto da loja virou o texto do MODELO correspondente ou um marcador
  ("[resposta da equipe]"). Também as conversas (sem o nome do comprador), os
  canais, as integrações, o Bling (pedidos Shopee da semana), a Logística
  Shopee, as reclamações, o índice do cartão "Cliente" e as avaliações (sem
  texto). 42.572 mensagens de 5.774 conversas, desde 20/09 (o histórico de 8
  dias para as sessões e os ciclos), mais os 415 convites anteriores.
- **Banco local** (schema `davinci_sim_auto`), com a semente da 0366. O relógio
  anda de 2 em 2 minutos (5.041 rodadas): a cada passo entram as linhas que o
  DaVinci de produção já tinha gravado (mensagem pelo `created_at` — a leitura
  começou em 30/09; antes disso, o que foi importado entra no `enviada_em` + 60 s,
  o p50 medido da leitura —, Bling pelo `created_at`, Logística no entregue/
  concluído + 39/33 min, índice no :22 seguinte, avaliação no :12/:42,
  reclamação na abertura e no encerramento). O motor roda com as funções de
  produção (descobrir, decidir; o comparador a cada 30 min, o que não muda o
  resultado). A conta é da `estatisticas` de produção, depois de 1 dia de
  aquecimento e só com a janela de comparação já fechada.
- **Limites**: o cancelamento do Bling entra com o estado de HOJE (os 23 "só
  Duoke" do pedido recebido por `pedido_cancelado` são isso: o pedido foi
  cancelado depois; em produção, aos 5 min, quase nunca está); a etiqueta e a
  situação da conversa também são as de hoje; o `pedido_marketplace` da conversa
  ficou vazio (para não vazar o futuro no "nunca comprou"); o índice só cobre
  desde 28/09. A simulação roda num Postgres local, não no de produção.
- **Rodada de novo depois da revisão** (05/10, tarde), com o motor corrigido
  (§14): o modo seco medindo o DaVinci sozinho (o corte no começo da
  simulação), a diferença contra a hora da decisão, o atraso real e a conta
  por loja. Os mesmos dados, 5.041 rodadas, 9,5 min.

### 13.3 Resultado por automação

A conta da §6.4 (as combinadas, fora). "% da tela" é a menor entre precisão e
cobertura (nas opções, a cobertura). A diferença é Duoke − a hora em que a
nossa sairia (negativa = o Duoke foi antes); o atraso é do gatilho até a nossa
sair. "Lojas prontas" é o critério da §6.5 **por loja** (as lojas com a regra
ligada na semente).

| Automação | Casos | Bateu (mandou / não) | Só DaVinci | Só Duoke | Combinadas | Precisão | Cobertura | Concordância | % da tela | Diferença mediana | Atraso do DaVinci | Alertas | Lojas prontas |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Shopee menu | 1175 | 1093 / 1 | 78 | 3 | 221 | 93,3% | 99,7% | 93,1% | 93,3% | −70 s | 2 min | 0 | 1 de 12 |
| Shopee opção 1 | 50 | 22 / 2 | 21 | 5 | 0 | – | 81,5% | – | 81,5% | 11,8 h | 2 min | 0 | 0 de 9 |
| Shopee opção 2 | 34 | 18 / 3 | 11 | 2 | 0 | – | 90,0% | – | 90,0% | 11,5 h | 2 min | 0 | 0 de 8 |
| Shopee opção 3 | 7 | 5 / 0 | 2 | 0 | 0 | – | 100,0% | – | 100,0% | 10,2 h | 2 min | 0 | 0 de 5 |
| Shopee opção 5 | 17 | 7 / 0 | 9 | 1 | 0 | – | 87,5% | – | 87,5% | 11,9 h | 2 min | 0 | 0 de 6 |
| Shopee opção 6 | 240 | 106 / 2 | 127 | 5 | 0 | – | 95,5% | – | 95,5% | 11,9 h | 2 min | 0 | 3 de 12 |
| Shopee aguarde (ATV) | 339 | 273 / 49 | 4 | 13 | 0 | 98,6% | 95,5% | 95,0% | 95,5% | −56 s | 11 min | 0 | 0 de 1 |
| Shopee convite | 1065 | 966 / 71 | 2 | 26 | 26 | 99,8% | 97,4% | 97,4% | 97,4% | −72 s | 2 min | 0 | 6 de 13 |
| Shopee dúvida 2 h | 1065 | 682 / 326 | 19 | 38 | 30 | 97,3% | 94,7% | 94,7% | 94,7% | −59 s | 2,0 h | 0 | 1 de 13 |
| Shopee dúvida 26 h | 736 | 667 / 8 | 30 | 31 | 0 | 95,7% | 95,6% | 91,7% | 95,6% | −61 s | 26,0 h | 0 | 0 de 13 |
| Shopee pedido recebido | 904 | 837 / 22 | 11 | 34 | 12 | 98,7% | 96,1% | 95,0% | 96,1% | −63 s | 6 min | 0 | 4 de 13 |
| Shopee entregue | 467 | 438 / 5 | 18 | 6 | 191 | 96,0% | 98,7% | 94,9% | 96,0% | 13,2 h | 44 min | 2 | 2 de 13 |
| Shopee pós-conclusão | 607 | 265 / 316 | 19 | 7 | 33 | 93,3% | 97,4% | 95,7% | 93,3% | −77 s | 4,0 h | 0 | 3 de 13 |
| TikTok aguarde | 240 | 123 / 96 | 5 | 16 | 0 | 96,1% | 88,5% | 91,2% | 88,5% | −51 s | 11 min | 0 | 0 de 4 |
| TikTok convite | 104 | 98 / 1 | 2 | 3 | 6 | 98,0% | 97,0% | 95,2% | 97,0% | −56 s | 2 min | 0 | 1 de 6 |
| TikTok dúvida 2 h | 114 | 17 / 91 | 4 | 2 | 7 | 81,0% | 89,5% | 94,7% | 81,0% | −72 s | 2,0 h | 0 | 0 de 4 |
| TikTok dúvida 24 h | 23 | 13 / 0 | 8 | 2 | 0 | 61,9% | 86,7% | 56,5% | 61,9% | −75 s | 26,0 h | 0 | 0 de 4 |
| ML menu | 49 | 25 / 4 | 15 | 5 | 15 | 62,5% | 83,3% | 59,2% | 62,5% | −57 s | 2 min | 0 | 0 de 11 |
| ML opção 1 | 5 | 4 / 0 | 0 | 1 | 3 | – | 80,0% | – | 80,0% | 11,7 h | 3 min | 0 | 0 de 5 |
| ML opção 5 | 2 | 1 / 0 | 1 | 0 | 0 | – | 100,0% | – | 100,0% | 11,1 h | 2 min | 0 | 0 de 2 |
| ML opção 6 | 17 | 5 / 2 | 4 | 6 | 5 | – | 45,5% | – | 45,5% | 10,6 h | 2 min | 0 | 0 de 8 |

**As lojas prontas pelo critério, uma a uma** (o que a tela mostra no selo da
loja; as que não estão aqui ainda não, com o porquê no title):

| Automação | Prontas | Perto (o que falta) |
|---|---|---|
| Shopee menu | Kia | vortan (só DaVinci nos 2 dias), mega e Inova (precisão 94,9%) |
| Shopee opção 6 | Jlas, mega, vortan | Barbosa (24 casos), Kia (18) |
| Shopee convite | Barbosa, Kia, ATV, Jlas, mega, vortan | Minas (só DaVinci nos 2 dias), Inova e Victor Mei (poucos casos) |
| Shopee dúvida 2 h | Jlas | mega (só DaVinci nos 2 dias), Barbosa (cobertura 94,4%), vortan (concordância 95,0%) |
| Shopee pedido recebido | Barbosa, Inova, Minas, vortan | ATV (cobertura 94,9%), mega (94,2%), kfa (94,6%), Vita (93,9%), Jlas (90,0%) |
| Shopee entregue | Barbosa, kfa | Inova e ATV (1 alerta cada), mega (só DaVinci nos 2 dias), vortan e Minas (precisão) |
| Shopee pós-conclusão | Jlas, kfa, mega | Barbosa e Inova (só DaVinci nos 2 dias), vortan (cobertura 91,3%) |
| TikTok convite | Barbosa | atv (28 casos), Mini (87,5% de concordância) |
| as outras | nenhuma | opções 1, 2, 3 e 5 (poucos casos por loja), "aguarde" (Shopee ATV: concordância 95,0% e só DaVinci; TikTok: cobertura), dúvida 26 h, TikTok dúvida, ML |

Leitura:

- **O modo seco medindo o DaVinci sozinho mudou o menu**: de 99,1% para
  **93,3% de precisão** (78 "só DaVinci" contra 10), e só **1 de 12 lojas**
  passa (Kia). A soma das lojas escondia isso duas vezes: a sessão do menu
  contava a resposta da opção do Duoke 12 h depois do menu, e o selo da
  automação somava as lojas. É o número da troca de menu e opções juntos
  (§6.5). Dos 78 "só DaVinci" do menu, 46 têm a resposta da opção do Duoke
  nas 12 h antes (o comprador voltou depois que a sessão do DaVinci acabou, 12 h
  depois da resposta dele à opção, e a do Duoke ainda valia), 22 têm um menu do
  Duoke nas 12 h antes sem ter um do DaVinci (o "re-menu" de fim de sessão, que
  o DaVinci não copia, §10 item 14) e 10 são os de antes. Decisão do Eduardo:
  o DaVinci copia a sessão do Duoke (12 h a partir da resposta da opção, e o
  re-menu) ou manda o menu de novo quando o comprador volta?
  Também caíram o "aguarde" do TikTok (cobertura 89,9% → 88,5%) e o menu do ML
  (73,5% → 62,5%). As automações do pedido não mudam.
- **A % da tela** (a menor entre precisão e cobertura) deixa de pintar de verde
  a dúvida 2 h (concordância 94,7%, cobertura 94,7%) e mostra o pós-conclusão
  pela precisão (93,3%), não pela concordância (95,7%, que soma 316 "nenhum dos
  dois mandou").
- **A diferença real**: o Duoke sai ~1 min antes do DaVinci no menu, no convite,
  no "aguarde" e nas campanhas (−51 s a −77 s): o DaVinci decide na rodada dos
  minutos pares depois do `devido_em`. Antes, medida do `devido_em`, aparecia
  "0 s". O atraso real do menu e do convite é de 2 min do gatilho (o do Duoke,
  ~1 min); no modo enviar, na transição, ainda há a espera da leitura (até
  ~4 a 6 min), que some depois de 3 dias.
- **Entregue**: 96,0% / 98,7%, 2 lojas prontas (Barbosa, kfa); os **2 alertas**
  (mandaria para quem devolveu, Inova e ATV) continuam — a devolução chegou
  depois da nossa decisão — e travam a troca nessas lojas. 191 combinadas (o
  horário e o concluído entre o nosso horário e o lote do Duoke).
- **Pedido recebido**: passa na soma (96,1%), mas só em **4 de 13 lojas**
  (Barbosa, Inova, Minas, vortan); ATV, mega, kfa e Vita ficam entre 93,9% e
  94,9% de cobertura. Ainda depende do envio por resposta automática (§7.3).
- **Opções**: a cobertura da 6 é 95,5% na soma e passa em 3 lojas (Jlas, mega,
  vortan) — com **a sobra** que é de propósito: nessas lojas o DaVinci
  responderia na hora 20 de 43, 33 de 58 e 28 de 44 vezes em que o Duoke não
  respondeu (ele responde 12 h depois, e não se alguém respondeu). As opções 1,
  2, 3 e 5 não têm 30 casos em loja nenhuma.
- **A §13.3 da 1ª simulação dizia** "pelo número já dá para trocar: opção 2,
  opção 6, pedido recebido e o convite do TikTok" — pela soma das lojas e com o
  modo seco contando o Duoke. Pelo critério por loja, a opção 2 não tem loja
  com 30 casos; o pedido recebido passa em 4 de 13; o convite do TikTok, só na
  Barbosa; a opção 6, em 3 de 12.
- **TikTok "aguarde"**, **dúvida** do TikTok e **ML** continuam abaixo (poucos
  casos e regras que o painel precisa fechar, §10).

**Custo**: na simulação corrigida, uma rodada (descobrir mensagens, Bling e
Logística, decidir) custou em média ~80 ms no Postgres local (mensagens 15 ms,
Bling 20 ms, Logística 26 ms a cada 10 min, decidir 22 ms); a passada do
comparador, ~12 ms por rodada (a cada 30 min, na simulação). Em produção o log da rodada (`atendimento_automacoes_tick`) traz o `ms`
de cada fase.


---

## 14. A revisão de 05/10 (tarde): correções e o estado em produção

### 14.1 O que já está em produção (achado da revisão)

A entrega do backend **já foi publicada e está rodando**, sem passar pelo OK
desta etapa: os commits `f4574e72` (os 8 arquivos novos) e `32d45b6b` (models,
enviar, config, worker, main, constantes, gravar, ia, historico, conftest e 2
testes — junto com uma correção da reclamação do ML, no mesmo commit e com o
título dela) entraram no `origin/main` às 14h54, e o servidor foi reconstruído
duas vezes (~17h56 e ~18h17 UTC; **os rebuilds apagaram os helpers `/app/_*.py`
dos robôs — regra 9: recopiar**). Conferido por SELECT só de leitura (05/10,
~18h35 UTC): `alembic_version` = `0369_denuncia_robo_agenda_tempo_parado` (a
`0367_flex` do Marketing tem a `0366_atendimento_automacoes` como
`down_revision`: **a 0366 não pode ser renumerada nem descida**); 389 regras
(shopee 151 em simular / 45 desligado, ml 90/71, tiktok 18/14), **nenhuma em
`enviar`** e nenhuma mudada por pessoa; o registro com 40 linhas entre 18h00 e
18h32 UTC (28 `simulado`, 10 `agendado`, 2 `pulado`; 24 já casadas com o
Duoke), nenhuma `enviando`/`enviado`;
**nenhuma mensagem `davinci_auto`**. No worker: leitura e motor ligados
(`ATENDIMENTO_AUTOMACOES_ATIVA=true` — confirmar com o Eduardo se foi decisão
dele; se não, desligar só faz parar de registrar), envio das automáticas,
envio geral e `auto_reply` desligados. **Nada saiu para comprador.** Às
~19h15 UTC houve mais uma subida (a Denúncia, `d8eb4d23`, migration
`0370_denuncia_robo_agenda_replica_19h`, todos os containers reconstruídos —
os helpers de novo): o motor seguiu em modo seco (66 `simulado`, 26
`agendado`, 4 `pulado`, nenhuma regra em `enviar`, nenhuma `davinci_auto`).

Os defeitos 1 a 3 da revisão (abaixo) estavam em produção, adormecidos atrás
das chaves de envio — corrigidos aqui, e precisam subir **antes** de qualquer
chave de envio. A tela (aba "Automáticas") não foi publicada: produção tem a
API sem a aba. A mudança da régua da parte 1 (§7.5) já vale em produção.

### 14.2 As correções

| # | Gravidade | O que a revisão achou | O que mudou |
|---|---|---|---|
| 1 | alta | No modo enviar, pedido recebido, entregue e pós não conferiam o Duoke antes de mandar (o `duoke_depois` só existia na conversa): o entregue saía às 9h02 com o do Duoke às 2h; um lote de 3 saía em dobro antes de o disjuntor do comparador desligar a regra. | `automacoes_comparar.duoke_dos_pedidos` (a mesma régua do comparador: cartão do pedido a até 10 s, ou a conversa do pedido) alimenta a decisão das linhas do pedido; achou → `pulado: duoke_mandou` e o disjuntor **na hora** (o resto do lote decide em `simular`). Pós com espera do Duoke de 5 min. Testes: os 3 casos × lote de 3. |
| 2 | média | O horário (9h–20h) só valia na descoberta: a linha decidida fora da janela (leitura parada, motor parado, rearme às 22h) saía de noite. | A decisão segura a linha fora do horário e anda o `devido_em` para a próxima abertura (se couber na validade; senão `atrasado`); o rearme só pega o que sai no horário; a seguinte (26 h) nasce no horário da regra dela. Testes: entregue rearmado às 22h sai às 9h02; menu de 21h58 não rearma; 26 h às 9h. |
| 3 | média | A chave `ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY` liberava as campanhas, que sairiam como mensagem NORMAL (não existe envio por `auto_reply`). | A campanha só vai para `enviar` com a chave **e** o envio por resposta automática no adaptador (`enviar_auto_reply`, que não existe hoje): `enviar.campanha_por_auto_reply`, nas três travas (tela/API, motor, `enviar_automatica`); quando existir, ela sai por ele, nunca pelo `enviar_texto`. A API mostra `chaves.shopee_auto_reply_adaptador`; a tela, o chip "falta o envio por ela". |
| 4 | alta | O modo seco usava os envios reais do Duoke para decidir o estado do menu: a paridade saía inflada (menu da Shopee de 99,1% para 93,3% de precisão na contraprova). | O estado do robô mede o DaVinci sozinho (§6.1): `cortes_do_modo_seco` + `sem_o_duoke_substituido`, na descoberta e na decisão. Os números da §13 são os novos. A ordem da troca na §6.5 foi revista. |
| 5 | média | O selo "pronta para trocar" e a §13.3 usavam a soma das lojas; a troca é por loja. | `pode_trocar`/`por_que_nao_trocar` por loja (sempre 7 dias) na API e o selo na linha da loja; a automação mostra "N de M lojas prontas"; o `PATCH` para `enviar` exige o critério da loja ou `troca_sem_criterio` (422 `criterio_nao_passou`); a §13.3 lista as lojas. |
| 6 | média | O atraso em relação ao Duoke era medido do `devido_em` (o menu "na mesma hora"); no modo enviar a espera atrasava o menu 4 a 6 min para sempre. | `duoke_diferenca_s` contra a hora em que a nossa sai (a mensagem gravada) ou sairia (a decisão); `atraso_mediana_s` (o atraso real) na conta e na tela; a espera da leitura e do Duoke só na transição (3 dias depois de `enviar_desde`). |
| 7 | alta | A entrega já estava no origin e em produção, com o motor ligado. | §14.1; nada desta etapa foi publicado (o que falta subir está na §14.3). |
| 8 | média | A régua da parte 1 mudou e já vale em produção (fila e métrica misturando as duas réguas). | Os números na §7.5 e o script `scripts/atendimento_fila_recalcular.py` (seco por padrão) para alinhar a fila, com o OK do Eduardo. |
| b | baixa | Fila presa de uma loja segurava as outras; INSERT sem lote estourava o limite do asyncpg; o comparador varria a tabela inteira; o registro sem limpeza. | No máximo 50 por loja × automação na rodada; INSERT em lotes de 500; `enviada_em` indexado nas consultas do comparador (EXPLAIN ANALYZE em produção, só leitura: as mensagens do Duoke de 4 dias do comparador do pedido, de ~600 ms em Seq Scan para ~110 ms pelo índice); limpeza diária (90 dias). Pendentes: o índice do registro (`decidido_em` e `enviando`) vai na próxima migration (acima da 0369); a semente da 0366 congelada como lista literal quando o catálogo mudar (hoje não mudou); a ATV (§10, item 17); a volta do `auto_reply` como `sistema` (§7.3). |

### 14.3 Para subir (com o OK do Eduardo, fora do horário de operação)

- **Código**: as correções acima + a tela (que nunca subiu). No `origin/main`
  os arquivos do backend estão na versão de antes das correções; o
  `git pull --ff-only` no Mac precisa de cuidado (os arquivos novos da entrega
  estão não versionados aqui e já existem no origin — guardar só eles, com
  pathspec, nunca `git stash -u` sem caminho, que levaria os `apps/api/_*.py` e
  a pasta `middleware/xml py`). **Sem migration nova** (a 0366 já está
  aplicada; nada nesta etapa mexe no banco).
- **`.env` de produção**: nada muda para subir. Hoje (lido no worker, 05/10,
  só os liga/desliga): `ATENDIMENTO_LEITURA_ATIVA=true`,
  `ATENDIMENTO_AUTOMACOES_ATIVA=true` (modo seco; confirmar que foi o Eduardo),
  `ATENDIMENTO_AUTOMACOES_ENVIO=false`, `ATENDIMENTO_ENVIO_ATIVO=false`,
  `ATENDIMENTO_AUTOMACOES_SHOPEE_AUTO_REPLY=false`,
  `ATENDIMENTO_AUTOMACOES_TETO_DIA=400`, `SHOPEE_MENSAGENS_COMPRADOR=true`,
  `ATENDIMENTO_SIMULADOR=false`. Ligar qualquer chave de envio só depois de as
  correções estarem no ar e, a campanha, só depois do `enviar_auto_reply`
  existir.

### 14.4 Rodado de novo (05/10, tarde)

- **API, Python 3.14** (a bateria inteira, schema `davinci_test_auto`): 7.170
  passam; 93 falhas + 75 erros = 151 testes, os mesmos 150 do HEAD mais o
  `test_atendimento_robo::test_status_efetivo_sem_sinal_e_com_evento_recente`,
  que depende do relógio (o `AGORA` é lido na importação e o limite é 5 min: a
  bateria levou 18,6 min rodando junto com outras) — passa sozinho aqui e no
  HEAD; o `robo.py` não foi tocado.
- **API, Python 3.12** (`data/venv312`, schema `davinci_test_auto312`): 7.169
  passam; 94 falhas + 75 erros = 152 testes: os 150 do HEAD, a
  `test_atendimento_ia_claude::test_cliente_do_sdk_sem_nova_tentativa_e_sem_ler_o_ambiente`
  (falha no HEAD com o 3.12) e a mesma do relógio acima (passa sozinha).
- **Em cima do `origin/main`** (`d8eb4d23`, com a 0367 a 0370): as correções
  aplicadas numa cópia (o `scratchpad/auto-fix/publicar.patch` aplica limpo) e
  os testes das automações, da migration, do envio, da régua, do `so_admin` e
  do Histórico: 337 passam; na web, os `atendimento-*.cjs` passam menos o
  `atendimento-so-admin.cjs`, que já falha no `origin/main` puro
  (`withDefaults is not defined`, de um commit da web para celular), sem estas
  mudanças.
- **Mutações**: 16 no backend das correções e 32 na tela, todas pegas.
- **Web**: os 16 `atendimento-*.cjs` passam; a bateria web inteira, 35 de 38
  (as 3 que falham falham igual no HEAD); typecheck numa cópia isolada só com
  os 7 erros antigos; `nuxi build` termina com exit 0.
- **Simulação de 7 dias** de novo com o motor corrigido (§13).
