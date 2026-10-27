# Robô vigia de mensagens sem resposta — avaliação

*21/09/2026. Nada foi implementado nem alterado no repositório. Todos os números de contas e de API abaixo vêm de sondagem só-leitura em produção feita hoje.*

---

## 1. Resumo em 5 linhas

1. **Dá pra fazer agora, sem pedir nada a ninguém, para Shopee (14/14 lojas) e Mercado Livre (21/24 lojas)**: as credenciais atuais já leem o chat pela API (confirmado em produção hoje, HTTP 200 em todas). Em ~7 dias de trabalho o aviso está funcionando para essas duas — pelo Threema (canal que seus outros vigias já usam) ou por um chat novo do Telegram, você escolhe.
2. **TikTok tem API completa, mas está trancada**: as 8 lojas devolvem 401 (código 105005) porque nenhum dos 8 apps tem o escopo `seller.customer_service`. Pedir hoje; a aprovação não tem prazo e pode exigir uma "janela de chat" no produto (é o que a doc de escopo e um checklist de terceiro sugerem; não há regra oficial explícita pra app próprio de vendedor).
3. **Amazon, Temu, Shein e AliExpress não têm API de leitura de chat, ponto.** Amazon só entrega a mensagem por e-mail (caixa de atendimento cadastrada); Temu/Shein/AliExpress só pela tela do Seller Center. Cobrir essas quatro exige leitor de e-mail e/ou robô de navegador no Mac (perfis AdsPower já cadastrados) — projeto separado, depois que o vigia provar valor.
4. **O Duoke não tem API nem webhook nem exportação**; o DaVinci não lê nada dele. O que ele tem é um alerta interno de atraso (Target Resolution Time) que fica preso dentro do Duoke — vale ligar hoje como primeira linha, custo zero.
5. **O maior risco não é técnico, é alarme falso**: 39 dos 41 "não lidos" do ML já tinham sido respondidos; mensagens automáticas (voucher, auto-resposta do Duoke) mascaram cliente esperando; e na Amazon a resposta dada pelo Seller Central provavelmente não aparece no e-mail (a confirmar na caixa real). O desenho abaixo trata isso como requisito, não como detalhe.

---

## 2. Por plataforma

| Plataforma | De onde vem a mensagem | Dá pra saber "sem resposta há N h"? | O que precisa antes | Prazo sugerido (aviso / último aviso) | Esforço | O que a sondagem confirmou hoje |
|---|---|---|---|---|---|---|
| **Mercado Livre** (24 integrações) | **API**, credenciais atuais: índice de conversas com mensagem não lida, última mensagem de cada conversa (sem marcar como lida) e perguntas pré-venda sem resposta. Existe notificação push do ML, mas o DaVinci não tem receptor — fica pra depois. | **Sim.** Chat: quem mandou a última mensagem (loja ou não) e a hora que a plataforma registrou. Pergunta: o próprio status "sem resposta" já é a resposta. | Poofy: ligar "Comunicação pré e pós-venda" no DevCenter e reautorizar (hoje 403). lucas mei: conferir o token — a coluna de vencimento diz vencido há 17 dias, mas o último teste passou; pode ser só a coluna desatualizada; reautorizar se preciso. nexus: arquivada, fora. | Pergunta pré-venda **1 h / 3 h** (o próprio ML marca "afeta seu tempo de resposta" após 1 h). Chat pós-venda **4 h / 12 h** (calibração interna; não há métrica oficial de reputação por hora no chat, só as 48 h úteis do Agente). | ~3 dias | 21 contas → 200 nas 3 leituras. 111 conversas "não lidas" acumuladas; em 39 de 41 amostradas a última mensagem era **nossa** (Duoke responde sem o ML marcar lida). **2 clientes da counhago esperando há 13 e 30 dias.** 0 perguntas pré-venda pendentes. |
| **Shopee** (14 integrações) | **API**, credenciais atuais: lista de conversas da loja (mais recentes primeiro, página por página) e histórico da conversa quando houver dúvida. Existe notificação push (Code 10), mas ligar é escrita no app — fica pra depois. | **Sim.** A lista de conversas diz quem falou por último e a hora exata da última mensagem. | Nada de acesso. Falta o Vinicius informar o **texto da Resposta Automática do Duoke** por loja (ela sai como mensagem da loja e a Shopee conta como resposta). | **3 h / 8 h** — a Shopee derruba a Taxa de Resposta ao passar de 12 h, fim de semana e feriado contam, resposta automática não conta. | ~3 dias | 14 contas → 200, sem erro de permissão (o módulo Chat já está liberado nos apps, ao contrário do que a doc pública sugere). Amostra de hoje: **20 conversas com cliente esperando** (7 há <1 h, 3 há 1-3 h, 10 há 3-6 h); 3 delas na vortan **já lidas e sem resposta** — "lido" não é "respondido". Página de 25 cobre só 3-6 h nas lojas grandes (ATV, Jlas, Inova, Barbosa). |
| **TikTok Shop** (8 integrações, todas BR) | **API** existe e é completa (lista de conversas com a última mensagem e o papel de quem mandou; notificação de mensagem nova). | Sim, quando o escopo vier: papel do remetente da última mensagem + hora. | Pedir escopo `seller.customer_service` **em cada um dos 8 apps** (um app por loja) no Partner Center, passar pela aprovação especial e reautorizar as 8 lojas. Duoke cobre 6 das 8 (injox e jlas estão fora). | **3 h / 12 h** quando entrar (24-Hour Response Rate ≥ 90 % na política US; BR não confirmado; conversa fecha sozinha em 6 h sem interação do comprador). | ~2 dias de código **depois** do escopo | 8 contas → **401 / 105005** (token e shop_cipher OK; falta escopo). As credenciais guardadas nem registram quais escopos o token tem. |
| **Amazon** (4 integrações SP-API; 6 lojas ativas na tela Lojas) | **E-mail** (única fonte oficial): cada mensagem do comprador chega na caixa "e-mail do atendimento ao cliente" da conta, vinda de `xxxx@marketplace.amazon.com.br`, com texto e, quando parte do pedido, o número do pedido. A SP-API Messaging só **envia** modelos; não há notificação de mensagem recebida (lista oficial de tipos conferida hoje; Amazon confirmou no GitHub em 2023). | **Parcial.** O e-mail diz "chegou". Se a resposta dada pelo Seller Central gera cópia na caixa, **ninguém confirmou ainda** (a doc não diz; presumo que não). Se gerar, o e-mail sozinho basta; se não, só a tela (filtro "Resposta necessária") reflete a resposta. | Vinicius informar qual e-mail está cadastrado por loja (kfa, kia, nexus, poofy + makisa e vortan, que só existem como login na tela Lojas), o provedor (tem IMAP? Tuta não tem), como a equipe responde hoje e se a resposta pelo Seller Central chega na caixa. | **12 h / 20 h** — a Amazon espera 24 h corridas (política US; a versão BR não traz o número) e em 48 h, nos pedidos enviados pela loja (não FBA), o cliente pode abrir reivindicação A a Z. | ~4 dias (IMAP) + robô de tela se precisar | Não sondado (não há endpoint a sondar). Tela Lojas: 8 linhas Amazon (6 ativas: kfa, kia, nexus, poofy, makisa, vortan), 7 perfis AdsPower dedicados, **nenhuma no Duoke**. |
| **Temu** (0 integrações) | **Só tela** (Seller Center). API oficial conferida nas 3 regiões hoje: nenhum endpoint nem evento de chat; não há escopo a pedir. | Só por robô de navegador lendo a lista de conversas (remetente + hora). | Confirmar quem responde hoje e se as 4 lojas vendem. Não existe nenhuma conta Temu no DaVinci (settings vazias, 0 integrações). | Sem regra oficial pública. Interna: **4 h / 12 h**. | Fora desta entrega (robô) | 0 integrações. 4 logins na tela Lojas (atv, barbosa, eron, jlas) com perfil AdsPower dedicado — insumo pronto pro robô. |
| **Shein** (0 integrações) | **Só tela.** Catálogo oficial (~230 endpoints + 24 webhooks) sem nada de mensagem. | Só por robô. | Perfis AdsPower dedicados cadastrados pra kfa e kia (login a confirmar); corrigir cadastro (stores tem Shein "Jlas", tela Lojas tem kfa/kia). | Sem regra oficial. Interna: **4 h / 12 h**. | Fora (robô) | 0 integrações; enum aceita mas o app devolve 501. 2 logins (kfa, kia). |
| **AliExpress** (0 integrações) | **Só tela.** FAQ oficial 792 diz que o IM não é acessível por API e as APIs de mensagem interna foram desativadas em 2019. | Só por robô. Disputas (reembolso/devolução), sim, dá pra vigiar por API hoje. | Perfil AdsPower dedicado cadastrado pra vita (login a confirmar); AliExpress nem está no enum de integrações. Perguntar ao suporte da Open Platform se o push tipo 21 ("Instant Messaging Notification", só na tabela em chinês, sem payload) está ativo. | Métrica cobrada (fonte secundária): "IM 24h reply rate". Interna: **4 h / 12 h**. | Fora (robô) | 0 integrações; 1 login (vita); 1 pedido nas views. |

---

## 3. Como o relógio conta e como o aviso chega

### O relógio

- **Começa na hora que a plataforma registrou a mensagem do cliente**, nunca na hora em que o robô viu (cada plataforma traz essa hora no payload; na Amazon é a data do e-mail).
- **Uma pendência por conversa.** A âncora é a hora da mensagem do cliente vista no primeiro tick em que a conversa entrou pendente. Cliente que manda 3 mensagens seguidas **não ganha prazo novo** — a prévia atualiza, o relógio não. (Prometer "primeira mensagem sem resposta" exigiria ler o histórico inteiro de cada conversa a cada rodada; fica pra quando houver webhook.)
- **Conta 24 h por dia, 7 dias por semana**, porque é assim que Shopee (12 h, fim de semana conta), Amazon (24 h corridas) e ML (1 h em perguntas) contam. O que respeita horário é o **envio**, não o prazo.
- **Só resposta humana da loja zera o relógio.** Mensagem automática (tipos automáticos da Shopee, auto-resposta do Duoke, Agente de mensageria do ML) não conta.
- **Dois níveis**: aviso e "ÚLTIMO AVISO". Cada pendência cruza cada nível uma vez só. Resolveu (resposta humana apareceu, pergunta sumiu da lista)? Some em silêncio, sem mensagem.
- **Janela de silêncio 23h-7h (BRT)**: mensagem que estoura o prazo de madrugada entra no digest das 7h com "há 8 h". Configurável; pode ser desligada.

### O aviso

- **Canal — você escolhe (pergunta 1):**
  - **Threema**: é o canal que todos os vigias de operação do DaVinci já usam (importação, prazo TikTok, devoluções, Amazon, logística parada). Seu ID já está no cadastro de Usuários; basta uma lista nova no .env, no mesmo molde do `VIGIA_IMPORTACAO_THREEMA_RECIPIENTS`. Vazio = vigia desligado, como nos outros.
  - **Telegram**: chat novo (você sozinho ou um grupo com o bot); precisa do chat_id. **Não** vai pro chat global — o código chama esse chat de "o celular do Eduardo" e ele é o canal técnico/marketing.
  - Sino do painel e roteamento por loja/atendente ("Informar" por contexto) ficam pra fase seguinte — hoje não existe cadastro de quem atende cada loja.
- **Formato:** um digest por plataforma por rodada, nunca uma mensagem por conversa. Exemplo:

```
⏰ Shopee — 6 clientes sem resposta
• Inova: 4 (há 3h, 4h, 5h, 5h) — 3 não lidas
   › "o pedido veio sem o…" (há 5h, ÚLTIMO AVISO)
• Jlas: 2 (há 3h, 6h)
• vortan: 3 lidas e sem resposta (há 4-6h)

Não consegui ler: —
```
```
⏰ Mercado Livre — 2 clientes sem resposta
• counhago: chat, há 13d e 30d (backlog)
• Perguntas pré-venda: 0
```

  Cada linha: loja, tipo (chat/pergunta/e-mail), há quanto tempo, número do pedido quando a plataforma dá, prévia curta **mascarada** (CPF/telefone/e-mail viram [CPF] etc.) só no último aviso. Link "abrir" só quando a URL for confirmada. Rodapé fixo com as contas que **não conseguiu ler** (403, token vencido) — falha nunca vira silêncio.
- **Sem repetição:** cada pendência é avisada uma vez por nível. O robô só anota "avisado" depois que o canal confirmou a entrega; se falhou, tenta de novo na rodada seguinte.
- **Teto:** no máximo 1 mensagem por plataforma por rodada (≤ 4 por rodada, 15 min). Digest cortado em 25 linhas + "e mais N" (o Threema aceita ~3.500 bytes por mensagem, o Telegram 4.096 caracteres). Contas com falha entram no rodapé uma vez por dia.
- **Trava de enxurrada:** se uma rodada trouxer mais de 30 pendências **novas** de uma vez (backlog ou bug de classificação), o robô **não avisa** — marca "suspeito" no log e manda uma linha só pro admin.
- **Backlog do primeiro dia:** pendências mais velhas que 7 dias (os 2 da counhago, as 111 conversas não lidas) entram numa única mensagem "backlog encontrado" e não repetem.

---

## 4. Falsos alarmes que o robô precisa ignorar

| Situação | Como o robô trata |
|---|---|
| ML: conversa "não lida" com última mensagem do próprio vendedor (39 de 41 amostradas) | Nunca decide pelo contador de não lidos; decide por quem mandou a última mensagem — se não foi a loja, é o cliente. |
| ML: remetente é o Agente de mensageria (id 3037675074, desde 02/2026) | A regra é "não foi a loja", nunca "foi o comprador": o Agente pode aparecer como remetente no lugar do comprador e conta como cliente. (Não apareceu na sondagem de hoje, mas a regra cobre.) |
| ML: conversa travada por reclamação/reembolso (visto na kfa2) | Ignorada — o canal ali é o chamado, já vigiado pelo Chamados. |
| ML: backlog velho (counhago 13/30 dias, 111 conversas não lidas) inundando o canal no 1º tick | Corte de 7 dias + mensagem única de backlog. |
| Shopee: mensagem automática da loja como "última" (cupom, sugestão de produto, primeira mensagem automática, prompt da FAQ, cartão formatado — todos vistos hoje; lista completa no apêndice) | Lista de tipos automáticos em configuração; não contam como resposta; nesses casos o robô olha o histórico da conversa pra achar a última mensagem humana. **Exceção:** o pedido de atendente vindo da FAQ (comprador clicou "falar com o vendedor") é **pendência do comprador**, com relógio a partir da hora do prompt, mesmo sem texto depois — a Shopee exige resposta nesse caso (artigo 2713). Entra na semana de modo seco. **Sticker fica "indefinido"** até a semana de modo seco mostrar de quem vem (na Minas, 9 stickers entre as últimas mensagens da loja — provavelmente atendente humano). |
| Shopee/ML/TikTok: Resposta Automática do Duoke (Reply Rate Protection) | A plataforma conta como resposta; o robô desconta por texto cadastrado. **Sem o texto, o robô fica cego justamente nas lojas do Duoke** — por isso é pergunta de dia 0. Como ela aparece no payload ninguém mediu ainda; a semana de modo seco serve pra isso. |
| Shopee: "lida" ≠ "respondida" (vortan: 3 lidas sem resposta há 3-6 h) | "Lida" é só sinal secundário no texto ("não lida"/"lida sem resposta"); a decisão é por quem falou por último. |
| Shopee: página 1 cobre só 3-6 h nas lojas grandes | Lê página por página até cobrir a janela, com teto de páginas. |
| Amazon: respondida pelo Seller Central, possivelmente sem cópia no e-mail (**a confirmar na caixa real**) | Enquanto não se confirmar, o e-mail **só abre** a pendência e libera o nível 1 rotulado "PROVÁVEL"; o último aviso só sai quando o robô de tela confirmar que o item continua em "Resposta necessária" (ou quando houver e-mail enviado ao mesmo cliente). Se a cópia chegar na caixa, o leitor de e-mail fecha a pendência sozinho e o robô de tela vira opcional. Sem uma coisa nem outra, Amazon **não entra** — não vale entregar um vigia que dá alarme falso por desenho. |
| Todas: conta com token vencido/403 (Poofy, magalu; lucas mei a conferir) | Não conta como "zero pendências"; entra no rodapé "não consegui ler". |
| Todas: dois workers varrendo juntos (deploy recria o worker ~25×/dia) | Trava no banco por rodada: quem não pega, sai. |
| Todas: cliente que só disse "obrigado" | Não dá pra saber pela API. Custo aceito na fase 1; a fase 2 traz "Marcar tratada" na tela. |
| Todas: o robô **nunca marca como lida** em nenhuma plataforma (detalhe por plataforma no apêndice) | Senão apagaria o sinal que a equipe usa no Duoke/Seller Center. Precisa de teste que falhe se isso mudar. |
| Madrugada: mensagem às 23h dispararia aviso às 2h | Janela de silêncio 23-07; relógio continua, envio espera. |

---

## 5. O que o Duoke já resolve (ou não) e como o robô convive com ele

**O que o Duoke tem** (central de ajuda em 4 idiomas varrida): cobre Shopee, TikTok, ML, Lazada, Daraz, Facebook, WhatsApp. Tem "Chat Settings → Target Resolution Time for Conversation Items" (em minutos, escala ao Supervisor), pop-up de mensagem nova, painel de "pendentes" por atendente e app mobile. **Não tem** API, webhook, exportação de conversas nem Zapier. Amazon, Temu, Shein e AliExpress não aparecem em nenhum artigo.

**O que fazer hoje, sem código (camada 0):** ligar o Target Resolution Time (ex.: 120 min) e dar papel Supervisor pra você. Cobre Shopee/TikTok/ML dentro do Duoke. Limite: o aviso fica dentro do Duoke; não chega no Threema nem no Telegram. Confirmar no seu Duoke por qual canal a escalação ao supervisor de fato aparece (a documentação só mostra um print da tela).

**Como o robô convive:** o DaVinci não lê nada do Duoke — lê direto as plataformas. O Duoke continua sendo onde a equipe responde. Duas interfaces com ele: (a) a auto-resposta do Duoke precisa ser descontada (item 4); (b) o flag `duoker` da tela Lojas já diz quais lojas estão no Duoke, então o aviso pode dizer "responda pelo Duoke" ou "responda no Seller Center".

**Lojas com API mas fora do Duoke** (ninguém vigia hoje): ML counhago (em modo férias, com 2 clientes esperando há 13 e 30 dias), Shopee aguiar, TikTok injox e jlas. E 6 lojas Shopee da tela Lojas sem integração nenhuma (agil, injox, luno, makisa brand, makisa venda, oliveira) — essas nem o robô alcança.

---

## 6. Fases de entrega e pré-requisitos fora do código

| Fase | Entrega | Aviso funcionando para |
|---|---|---|
| **0 — hoje, sem código** | Você responde as perguntas do item 7. Ligar Target Resolution Time + Supervisor no Duoke. Poofy: ligar "Comunicação pré e pós-venda" no DevCenter e reautorizar. lucas mei: conferir se o token está mesmo vencido (a coluna diz que sim, o teste diz que não) e reautorizar se preciso. Pedir escopo Customer Service nos 8 apps TikTok (começa agora, fora do cronograma). Responder os 2 clientes da counhago. | — |
| **1 — dias 1-7** | Tabela de estado + cron a cada 15 min + regra do relógio + digest (Threema ou Telegram) + modo seco. ML (3 leituras novas) e Shopee (2 leituras novas, paginação, tipos automáticos, pedido de atendente via FAQ, texto do Duoke). Roda 3-5 dias em produção **só lendo e logando**; você confere se os 2 da counhago aparecem e os 39 "do vendedor" não. **Dia 7: liga.** | **Shopee (14) + ML (21)** |
| **2 — dias 8-14** | Endurecer: vigia-do-vigia (avisa se o cron parar), rodapé "não consegui ler", trava de enxurrada, ajuste de prazos com os números reais, memória do projeto. Opcional: "lembrete de rodada" fixo 09h/14h/19h pra Temu/Shein/AliExpress (é lembrete, não detecção — só se alguém for dono da rodada). | idem |
| **3 — semanas 3-4 (decidir depois da fase 1)** | Amazon: leitor IMAP (se a caixa tiver IMAP) abrindo pendência "provável" + robô de tela no Seller Central lendo "Resposta necessária" pra confirmar/fechar — o robô só é obrigatório se a resposta pelo Seller Central não chegar na caixa (pergunta 4). Tela mínima no painel: lista + "Marcar tratada" + "Silenciar até" + saúde por conta. | + Amazon (até 6 lojas) |
| **4 — quando o escopo sair** | TikTok entra no mesmo serviço (é só mais um coletor, ~2 dias). | + TikTok (8) |
| **5 — decisão separada** | Temu/Shein/AliExpress por robô de navegador no executor (perfis AdsPower dedicados já casados; login nas sessões a confirmar). Fazer uma prova de conceito num único Seller Center antes de estimar prazo — não há precedente no repo pra estimar "2-3 dias por plataforma". Alternativa: turno humano fixo pra Shein/AliExpress (3 contas, volume baixo). AliExpress: perguntar ao suporte da Open Platform se o push tipo 21 (IM) está ativo; disputas dá pra vigiar por API já. Webhooks ML/Shopee (fecham a lacuna da conversa "lida sem resposta"). | + Temu/Shein/Ali |

**Pré-requisitos fora do código, em resumo:** canal de aviso (lista de IDs Threema no .env ou chat_id do Telegram); texto da auto-resposta do Duoke por loja; e-mail de atendimento das 6 lojas Amazon (e provedor); acesso ao DevCenter do app da Poofy; conferência/reautorização de lucas mei; pedido de escopo nos 8 apps TikTok (a revisão pode pedir prints/gravação de uma janela de chat funcionando — um vigia só-leitura pode não passar).

---

## 7. Perguntas que só você responde

1. **Canal do aviso:** Threema (o canal que seus outros vigias já usam, com seu ID cadastrado; basta uma lista nova no .env, como o `VIGIA_IMPORTACAO_THREEMA_RECIPIENTS`) ou Telegram (chat novo com o bot; precisa do chat_id)? Se Telegram, quem entra no chat (só você, ou Eduardo/atendentes)? Confirma que **não** vai pro chat global?
2. **Prazos:** valem 1 h/3 h (pergunta ML), 4 h/12 h (chat ML), 3 h/8 h (Shopee), 12 h/20 h (Amazon)? Silêncio 23h-7h ou aviso a qualquer hora? Fim de semana conta igual?
3. **Duoke:** qual é o texto exato da Resposta Automática em cada loja Shopee, ML e TikTok, e em quais está ligada? Quem tem papel Supervisor?
4. **Amazon:** qual e-mail está cadastrado como atendimento ao cliente em kfa, kia, nexus, poofy, makisa e vortan? Gmail/Outlook (tem IMAP) ou Tuta (não tem)? A equipe responde pelo Seller Central ou pelo e-mail? E quando alguém responde pelo Seller Central, chega cópia na caixa de e-mail? (Se chegar, o leitor de e-mail sozinho fecha a pendência e o robô de tela vira opcional.)
5. **Lojas fora do Duoke com API** (counhago em férias, Shopee aguiar, TikTok injox/jlas): vigiar igual? counhago em férias deve avisar?
6. **Poofy e lucas mei:** quem tem acesso ao DevCenter/à conta pra ligar a permissão e reautorizar? Pode ser esta semana?
7. **TikTok:** autoriza preparar o pedido do escopo nos 8 apps agora, sabendo que a aprovação pode exigir uma tela de chat no DaVinci (segundo a doc de escopo e relato de terceiros)?
8. **Temu/Shein/AliExpress:** quem responde o chat hoje e com que frequência? Essas lojas estão vendendo? Quer o "lembrete de rodada" fixo enquanto não há robô, ou silêncio até ter detecção de verdade?
9. **Prévia da mensagem no aviso** (mascarada): pode, ou prefere só loja + pedido + tempo?
10. **Perguntas pré-venda do ML** (hoje 0 pendentes): entram no vigia ou só o chat?

---

## 8. Apêndice técnico (pra quem for implementar)

### Componentes da fase 1

| Componente | O que é | Onde |
|---|---|---|
| Tabela `mensagens_vigia` (migration + model) | Uma linha por conversa vigiada: `plataforma`, `integration_id` (FK SET NULL) + `conta` (snapshot), `tipo` (chat/pergunta/email), `conversa_ref` (pack_id / question_id / conversation_id / alias Amazon), `ultima_msg_cliente_em` (hora **da plataforma**), `ultima_msg_cliente_id`, `ultima_resposta_em`, `previa` (≤120 chars, mascarada), `lida`, `pedido`, `ultima_verificacao`, `avisado_em`, `aviso_nivel` (0/1/2), `resolvido_em`, `url`. UNIQUE (plataforma, integration_id, conversa_ref); índice parcial `resolvido_em IS NULL`. | criar `apps/api/alembic/versions/<próximo número livre>_mensagens_vigia.py` — **não fixar número agora**: o head provisório no checkout é `0300_ouvidoria.py` (untracked, trabalho do Ouvidoria em andamento), então hoje seria 0301; conferir com `ls apps/api/alembic/versions` na hora. Model em `apps/api/app/models/mensagens_vigia.py` — molde `apps/api/app/models/vigia_importacao.py:11-57` |
| Leituras no cliente ML | `get_unread_packs()` → `GET /messages/unread?role=seller&tag=post_sale`; `get_pack_last_message(pack_id, seller_id)` → `GET /messages/packs/{pack}/sellers/{seller}?tag=post_sale&mark_as_read=false&limit=1&offset=0` (mensagens vêm da mais nova pra mais antiga, confirmado hoje; `mark_as_read=false` fixo, não parametrizável); `search_unanswered_questions(seller_id)` → `GET /questions/search?seller_id=X&status=UNANSWERED&api_version=4&sort_fields=date_created&sort_types=ASC`. Só GET; usa o `_request` existente (refresh em 401, backoff em 429). Regra do remetente: `from.user_id != seller_id` (cobre o Agente 3037675074); ignorar `conversation_status.status == blocked` (`blocked_by_claim` visto na kfa2). | tocar `apps/api/app/services/marketplaces/ml.py` (ao lado de `get_claim_messages`, l.277-290); cliente por conta via `apps/api/app/services/logistica_meli.py:477-495` (`_build_ml_client`, padrão `decrypt_json` + `on_token_refresh=_persist` — refresh_token do ML é de uso único, nunca chamar sem o callback) |
| Leituras no cliente Shopee | `get_conversation_list(direction='older', next_cursor=None, page_size=25, type='all')` paginando por `page_result.next_cursor` até `last_message_timestamp` (ns, 19 dígitos) ficar mais velho que a janela (teto 50 páginas); `get_message(conversation_id, page_size=10)` só quando a última for automática (**não sondado** — campos assumidos do SDK de 2022; validar no 1º dia em modo seco). Comprador foi o último = `latest_message_from_id == to_id`; `unread_count` só como sinal secundário. Tipos automáticos vistos hoje: `voucher`, `product_new`, `first_msg_item_rcmd`, `bot_voucher_product`, `faq_liveagent_prompt`, `extended_markdown`; `sticker` indefinido. `faq_liveagent_prompt` = pendência do comprador com âncora na hora do prompt (artigo 2713). Via `_sign`/`_call`. **Incluir backoff em 429** no `_call` — hoje não existe. Nunca `read_conversation` nem `send_message`. | tocar `apps/api/app/services/marketplaces/shopee.py` (paginação em 398-429, `_call` em 431-451) |
| Módulo puro `mensagens_regras.py` | Classificação testável sem banco: remetente (cliente/loja/automático) por plataforma, tipos automáticos Shopee, caso `faq_liveagent_prompt`, prefixos do Duoke, `aviso_devido(pend, prazos, agora) → 0/1/2/None`, âncora do relógio, corte de backlog. | criar `apps/api/app/services/mensagens_regras.py` |
| Service `mensagens_vigia.py` | `run(session, dry_run=False)` → resumo por plataforma. `pg_try_advisory_xact_lock(SYNC_NAMESPACE, chave própria)`; loop por `Integration` ativa (`archived_at IS NULL`), falha isolada por conta (`contas_falha += 1; continue`); coletores `_coletar_ml`, `_coletar_shopee` devolvendo lista normalizada; upsert; regra do relógio; trava de enxurrada (>30 novas → não avisa); digest por plataforma com `dm_ia.mascarar`; envio pelo canal configurado — Threema: `ThreemaClient().send_to_all(texto, parse_recipients(settings.mensagens_vigia_threema_recipients))` (devolve `{sent, failed}`, texto puro, teto 3500 bytes, `threema.py:129-151`); Telegram: `html.escape` + `TelegramClient().safe_send(texto, chat_id=settings.mensagens_vigia_telegram_chat_id)` (assinatura conferida em `telegram.py:72-78`, devolve bool); carimba `avisado_em`/`aviso_nivel` **só se enviou** (dedup por linha: avisado_em + nível + `ultima_msg_cliente_id`). Cache por pack no ML: guardar id da última mensagem vista + count do unread, só reler o pack quando o count subir. | criar `apps/api/app/services/mensagens_vigia.py` — moldes `vigia_importacao.py:176-236, 309-351` (lock, Threema, carimbo-só-se-enviou), `chamados_tiktok_reembolso.py:361-379` (aviso_devido) e `421-533` (dry_run), `dm_ia.py:109-117` (mascarar). **Não** reutilizar `dm_alerta.avisar` — o teto de 15/hora dele é global e disputaria com os avisos de DM. |
| Cron `mensagens_vigia_tick` | `async with session_scope() as s: summary = await mensagens_vigia.run(s)`; `logger.exception` sem re-raise. Registrar em `WorkerSettings.functions` + `cron(..., minute={7, 22, 37, 52}, run_at_startup=False, timeout=600)`. **Atenção aos minutos:** conferido hoje, **não existe minuto vazio** no worker default (há um cron leve em todo minuto par e outro em todo ímpar). A escolha é evitar os pesados: :00/:05 (ingests), :10/:40, :15/:45, :20/:50, os conjuntos {3,13,…,53}, {4,14,…,54}, {9,39}, {13,43}, {16,46} e os únicos :06/:19/:25/:29/:31/:35/:49. Os desenhos que afirmaram {3,18,33,48} ou {4,19,34,49} "livres" estavam errados. Carimbo `_marcar_ok('mensagens_vigia')` + entrada em `_LOGISTICA_VIGIA_LIMITES_MIN` (45 min) pro vigia-do-vigia. Se a fila default atrasar, o caminho já existe: `WorkerSettingsMarketplace` (como o shipment check). | tocar `apps/api/app/worker.py` (tick ao lado de 2299-2314; functions 2952-3013; cron_jobs 3014-3315; `_marcar_ok` 1571-1578; limites 1649-1656) |
| Settings | Canal: `mensagens_vigia_threema_recipients: str = ''` (molde `vigia_importacao_threema_recipients`, config.py:390-394) **ou** `mensagens_vigia_telegram_chat_id: str = ''` — ambos vazios = desligado; `mensagens_vigia_dry_run: bool = True`; `mensagens_vigia_prazos: 'ml_pergunta=1/3,ml_chat=4/12,shopee=3/8,amazon=12/20'`; `mensagens_vigia_silencio_brt: '23-07'`; `mensagens_vigia_backlog_dias: 7`; `mensagens_vigia_teto_novas: 30`; `shopee_msgs_automaticas` (lista); `duoke_autoresposta_prefixos` (por loja). | tocar `apps/api/app/config.py:376-394` |
| Testes | `test_mensagens_regras.py` (puro) e `test_mensagens_vigia.py` (cron com `ctx={}` contra o banco de teste): níveis 0→1→2, não re-avisa a mesma `ultima_msg_cliente_id`, mensagem nova do cliente não re-arma, resposta humana resolve, automática não resolve, `faq_liveagent_prompt` abre pendência, `from != seller` incl. Agente, pack blocked ignorado, ns→datetime, carimbo só se enviou (Threema `sent` / Telegram True), `mark_as_read=false` presente (teste que falha se sumir), trava de enxurrada, lock com 2 ticks. | criar `apps/api/tests/test_mensagens_regras.py`, `test_mensagens_vigia.py` — moldes `tests/test_cron_jobs.py:1-30`, `tests/test_dm_alerta.py:56-160` |

### Nunca marcar como lida (por plataforma)

ML: `mark_as_read=false` fixo no código (o GET sem ele marca como lida). Shopee: nunca chamar `read_conversation`. TikTok: nunca chamar Read Message. IMAP (Amazon): nunca setar `\Seen`.

### Fases seguintes (não codar agora)

- **Amazon:** `mensagens_amazon_email.py` (imaplib em `asyncio.to_thread`; não há IMAP em lugar nenhum do repo hoje — `services/email.py` é só envio Mailjet), credenciais por caixa cifradas com `security/cipher.py:37-54`; regra "provável" (nível 1) vs confirmado (robô viu em "Resposta necessária", ou cópia da resposta na caixa — se a pergunta 4 confirmar que ela existe). Robô de tela: ação `mensagens_ler_caixa` no lease de logística (`apps/executor/src/index.ts:184-243`, molde `tuta.ts:72-125`), perfil vindo de `vw_perfis` (dedicado=true), comando em `LogisticaRoboComando` (`models/logistica.py:225-247`, `logistica_id` nullable). Lojas: as 6 ativas da tela Lojas (kfa, kia, nexus, poofy, makisa, vortan), não só as 4 com SP-API.
- **TikTok:** `get_conversations()` em `tiktok.py` (ao lado de `_get`, l.353-411) quando o escopo vier; ignorar `is_visible=false`, roles SYSTEM/ROBOT, tipos NOTIFICATION/ALLOCATED_SERVICE/BUYER_ENTER_FROM_*; nunca Read Message. Guardar lista de escopos na credencial na reautorização.
- **Webhooks (fase 5):** ML `topic=messages` (validar `application_id` + IPs, 200 em 500 ms, `missed_feeds` 2 dias); Shopee Code 10 via `set_app_push_config` (HMAC `url|body` com partner_key; os partner_ids mascarados sugerem ~5 apps distintos — 9 contas no 20315xx — mas isso é inferido, contar no Console antes); TikTok NEW_MESSAGE (HMAC app_key+body cru, dedupe `tts_notification_id`). Molde do receptor: `routers/webhooks.py:678-699, 807-875`. Aí sim entra um log `mensagens_eventos` com `msg_id_externo` UNIQUE (molde `models/instagram_dm.py:207-265`) e a âncora "primeira mensagem sem resposta".
- **AliExpress:** disputas por `aliexpress.issue.issuelist.get` / `issue.detail.get` (existe hoje, OAuth normal); push tipo 21 (IM) só se o suporte da Open Platform confirmar payload e ativação.
- **Tela `/mensagens`:** router novo + `pages/mensagens.vue` (molde `chamados.vue`), recurso `mensagens` em `useCan.ts:3-41` e `schemas/permissions.py:5-76`, item na seção Pós-venda do `AppSidebar.vue:135-143`, relógio vivo extraído pra `lib/prazo.ts` (hoje local em `devolucoes.vue:698-711` e `controle-estoque.vue:886-919`).

### Endpoints e documentação

- ML mensagens: https://developers.mercadolivre.com.br/pt_br/mensagens-post-venda · pendentes: https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes · perguntas: https://developers.mercadolivre.com.br/pt_br/gerenciamento-perguntas-respostas · notificações: https://developers.mercadolivre.com.br/pt_br/produto-receba-notificacoes · permissões: https://developers.mercadolivre.com.br/pt_br/permissoes-funcionais · regra de 1 h: https://vendedores.mercadolivre.com.br/nota/responda-a-todas-as-perguntas-e-conclua-a-transacao-de-forma-mais-rapida
- Shopee push Code 10: https://open.shopee.com/push-mechanism/10 · tipos de app: https://open.shopee.com/developer-guide/14 · política BR da Chat API: https://open.shopee.com/announcements/1430 · regra das 12 h (inclui o caso "conversar com o vendedor" na FAQ): https://seller.shopee.com.br/edu/article/2713 (doc dos endpoints `sellerchat.*` é fechada por login; campos conferidos hoje pela sondagem)
- TikTok: https://partner.tiktokshop.com/docv2/page/get-conversations-202309 · https://partner.tiktokshop.com/docv2/page/14-new-message · escopos: https://partner.tiktokshop.com/docv2/page/access-scope · política 24 h (US): https://seller-us.tiktok.com/university/essay?knowledge_id=7454760317110062&lang=en · checklist de aprovação (terceiro): https://www.unifyport.ai/blog/tiktok-shop-customer-service-api-approval-checklist/
- Amazon: Messaging só envia — https://developer-docs.amazon.com/sp-api/docs/messaging-api · tipos de notificação (sem mensagem): https://developer-docs.amazon.com/sp-api/docs/notification-type-values · e-mail do comprador: https://sellercentral.amazon.com.br/help/hub/reference/external/G200383320?locale=pt-BR · política 24 h/48 h (US, A a Z só em pedidos com logística pelo vendedor): https://sellercentral.amazon.com/help/hub/reference/external/G1701 · confirmação da Amazon: https://github.com/amzn/selling-partner-api-models/discussions/3487
- Temu: https://partner.temu.com/documentation?menu_code=0cc16b077a3343d08e87bc3c0a7593a8 (eventos de webhook, sem chat)
- Shein: https://open.sheincorp.com/api/api/apiDoc/queryAllApiDocCategoryList?categoryType=1 (catálogo, sem mensagens)
- AliExpress FAQ 792: https://openservice.aliexpress.com/doc/doc.htm#/?docId=792 · tabela de push em chinês com o tipo 21: https://openservice.aliexpress.com/doc/doc.htm#/?docId=832
- Duoke Target Resolution Time: https://ai.duoke.com/en/help-doc-2-1082 · Reply Rate Protection: https://ai.duoke.com/en/help-doc-2-1322

### O que a sondagem em produção retornou (21/09/2026, só-leitura)

- **ML (24 integrações):** 21 → 200 em `/messages/unread`, pack com `mark_as_read=false` e `/questions/search`. 16 contas com packs não lidos (111 packs, 379 msgs); 0 perguntas pendentes. Amostra de 41 packs: 39 última do vendedor, 2 do comprador (counhago, 13 e 30 dias, `read=null`), 1 `blocked_by_claim`, 0 com Agente. **Poofy → 403** (token sem `urn:ml:mktp:comunication`). **lucas mei** não sondado: `expires_at` da credencial aponta vencimento há ~413 h, mas `last_test_ok=True` — hipótese: refresh sem atualizar a coluna; o diagnóstico não foi confirmado. **nexus** arquivada.
- **Shopee (14):** 14 → 200 em `get_unread_conversation_count` e `get_conversation_list` (sem `error_permission`). Página 1 de 25 cobre 3,4 h (ATV) a 676 h (aguiar). 20 conversas com cliente esperando; contadores de não lidas (ATV 7, Jlas 6, Inova 5, Barbosa 3) maiores que a página 1. Tipos vistos: text, sticker, image, voucher, product_new, first_msg_item_rcmd, bot_voucher_product, faq_liveagent_prompt, extended_markdown. `direction=latest` sem cursor devolve as mais **antigas**; usar `older`. Minas: 9 stickers entre as últimas mensagens da loja.
- **TikTok (8):** 8 → 401 código 105005 em `/customer_service/202309/conversations`; tokens válidos por 84-126 h; 8 app_keys distintos.
- **Amazon (4 integrações SP-API):** não sondado (sem endpoint). Tela Lojas: 6 ativas (kfa, kia, nexus, poofy, makisa, vortan). Temu: 0 integrações, settings vazias. Shein/AliExpress: 0 integrações; só login + perfil AdsPower dedicado em `store_info`/`vw_perfis` (sessão logada não verificada).
- **Cadastro:** `store_info.duoker` — ML 22, Shopee 15, TikTok 6 no Duoke; Amazon/Temu/Shein/AliExpress/Magalu zero. Nomes com espaço à esquerda em `integrations.name` (' barbosa', ' Kia' etc.) — normalizar como o `conta_key` do `adspower_conta`.