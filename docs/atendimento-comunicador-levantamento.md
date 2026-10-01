# Comunicador × /atendimento: o que já existe e o que falta

Este levantamento compara o `PROJETO-COMUNICADOR.md` (esboço v7, de 01/10/2026) com o DaVinci que está em produção (branch `main`, commit `0e5b9064`, 01/10/2026).
A base foi a leitura do código e contagens com `SELECT` no banco de produção. Nenhum código foi alterado e nada foi gravado em produção.

**Esforço:** P = até 1 dia · M = 2 a 5 dias · G = mais de 1 semana.
**Status:** ✅ feito · 🟡 parcial · ❌ falta · ✏️ retificar (o spec está desatualizado ou errado frente ao que já existe ou ao que é possível).
Quando o spec precisa ser corrigido **e** ainda há trabalho, a linha traz os dois: ✏️❌ = corrigir o spec, nada feito; ✏️🟡 = corrigir o spec, parte pronta; ✏️✅ = só o spec está errado.
A conferência de completude (01/10, à tarde) está no §6.

---

## 1. Resumo

- O "Comunicador" do spec **já existe**: é o **/atendimento** (menu Pós-venda), em produção desde 30/09–01/10 em **modo observação**. Ele lê tudo e a IA sugere respostas, mas nada é enviado (`ATENDIMENTO_ENVIO_ATIVO=false`). O acesso está restrito a thorfinn e heisenberg.
- **Volume lido hoje:** 80 canais em 55 contas, todos em "observar". São cerca de 4.970 conversas (Shopee 3.455, ML 1.002, TikTok 511, Amazon, Magalu e AliExpress 6) e 41.220 mensagens. Nenhuma mensagem saiu pelo DaVinci.
- **A leitura cobre todas as plataformas de marketplace:**
  - ML: perguntas e pós-venda, 23 contas.
  - Shopee: chat, 14 lojas.
  - TikTok: 6 de 8 lojas.
  - Amazon: pelo Gmail, 4 contas.
  - Magalu: pela API (perguntas, chat e SAC).
  - Temu (4) e AliExpress (1): pelo robô do AdsPower no Mac mini. O **Temu ainda tem 0 conversas gravadas** (3 canais com leitura ok hoje e 1 com sessão caída): falta conferir se é falta de mensagem ou falha na captura.
  - Instagram Direct: só leitura.

  Com isso, as Fases 1, 8 e 9 do spec estão cobertas na **leitura de perguntas e mensagens**. O que falta nelas é **responder**, trazer **reclamações e devoluções** e desligar o Duoke. A Fase 1 também pede estoque no painel e o botão do AdsPower, que ainda não existem.
- **Já está pronto:**
  - tela de 4 colunas como a do Duoke;
  - painel do pedido com Bling, Logística, NF, chamados e devoluções;
  - anexos, falha de envio visível e bloqueio da plataforma;
  - 48 respostas prontas;
  - manual da IA (42 assuntos, 64 regras), validador e modos observar/humano/copiloto/auto;
  - filtros Pré-venda, Pós-venda e prazo.
- **Os 5 maiores buracos:**
  1. **Reclamações das plataformas não entram.** Nenhum job lista as reclamações do ML, e as devoluções de Shopee e TikTok, que a Logística já lê, não chegam ao atendimento. É a lacuna do pedido 297840, que continua aberta.
  2. **Não existe "etiqueta = status".** Não há coluna, histórico, destaque nem prioridade. Os dados para calcular a maior parte dela já estão no banco.
  3. **Ag. cancelamento e troca de produto (RF3 e RF4) não existem no atendimento.** Quase todas as peças do Bling já rodam em outros robôs.
  4. **E-mail (Tuta) e sites (RF5 e RF6) não existem.** Só a Amazon entra por e-mail.
  5. **Falta tirar do modo observação:** piloto de envio, acesso da equipe, botão do AdsPower, saldo de estoque no painel e avisos de leitura parada.
- Mídia, Avaliações, Carrinho e Zap (RF7 a RF10) dependem em boa parte de terceiros: Meta e WhatsApp, os próprios sites e o custo do X. Pelo volume real medido, são as últimas da fila, exceto as avaliações da Shopee e o Direct.

---

## 2. O que muda no spec (retificações)

### 2.1 O módulo, os nomes e onde as coisas ficam

| O spec diz | O que é hoje |
|---|---|
| Criar o módulo "Comunicador" e copiar o spec para `docs/comunicador/` (seção 3) | O módulo é o **/atendimento**. Arquivos principais: `apps/web/pages/atendimento.vue` e `components/Atendimento*.vue`; `apps/api/app/routers/atendimento.py` e `atendimento_robo.py`; `apps/api/app/services/atendimento/*`; `apps/api/app/models/atendimento.py`. Documentação: `docs/atendimento-unificado.md`, `-magalu`, `-aliexpress-temu`, `-manual-base` e `deploy-atendimento.md`. O spec deve ser tratado como **evolução do /atendimento**, sem módulo novo. O "prompt de descoberta" da Fase 0 já foi feito. |
| Usar o módulo de DMs do Instagram como base do modelo de dados (seção 4) | Foi decidido **não** reaproveitar `dm_conversas` e `dm_mensagens`, porque o robô de DM responde sozinho tudo o que estiver lá. O atendimento tem 10 tabelas próprias `atendimento_*`. O Direct entra por um adaptador, só leitura (`services/atendimento/instagram.py`). |
| "Nenhuma mensagem vai ao comprador sem clique humano" | Existe o modo **auto** por assunto (a IA envia sozinha), hoje desligado. O **robô de DM do Instagram** (`dm_auto=true` em 3 contas) enviou 20 respostas sozinho entre 17 e 23/09. O spec precisa dizer se os dois ficam proibidos até o fim do piloto. |
| Imagens em `esboco/` e `esboco/mockup.html` | Só o `.md` chegou. As imagens e o mockup precisam ser pedidos antes de desenhar as telas novas. |
| (nosso) `docs/atendimento-unificado.md` | Ainda diz "Local, sem commit, sem deploy" (linhas 3 e 252), o que está desatualizado. Atualizar. |

### 2.2 Plataformas (seções 6 e 9, pergunta 10)

- **Shopee:** o chat **não precisa de liberação**. Já funciona com o app atual em 13 de 14 lojas, sem nunca marcar como lido.
- **TikTok:** o atendimento ao cliente já é lido em 6 de 8 lojas. Poofy e Inova continuam no app antigo, sem o escopo `seller.customer_service`.
- **Amazon:** **não usa a SP-API Messaging**, que dá 403 por falta do papel Buyer Communication e não lê mensagens. A leitura é pelo **Gmail (IMAP)** e a resposta por **SMTP** para o relay `@marketplace.amazon.com.br` (`services/atendimento/amazon_email.py`). Envio testado de ponta a ponta em 28/09. Para A-to-Z não há API pública conhecida [confirmar].
- **Temu, Magalu e AliExpress não são "só e-mail":**
  - Magalu é lida pela API (perguntas, chat e SAC).
  - Temu (4 lojas) e AliExpress (1) são lidos pelo robô do AdsPower no Mac mini. A resposta continua sendo dada no Seller Center.
- **Contas a cobrir:** são 43 lojas ativas marcadas como Duoke no store-info (ML 22, Shopee 15, TikTok 6), não "~30". A lista do robô (Temu e AliExpress) é um JSON paralelo, `apps/api/scripts/atendimento_robo_lojas.json`, o que contraria a regra "sem cadastro paralelo".
- **Fases:** a leitura foi feita para todas as plataformas ao mesmo tempo. A ordem da pergunta 9 ("ML primeiro, depois Shopee → TikTok → Amazon") passa a valer só para **ligar o envio**.

| Fase do spec | Situação real |
|---|---|
| 0 Descoberta | Em grande parte feita (sondagem de 25/09 em `docs/atendimento-unificado.md` §1). Faltam: pedido de teste no Bling, escolha do caminho do Tuta, modelo de e-mail dos sites e plataforma da 7buyers e da Locagil. |
| 1 ML leitura | Ultrapassada em abrangência (todas as plataformas). Faltam reclamações do ML, estoque no painel, botão AdsPower e acesso da equipe. |
| 2 ML resposta | Envio no código e desligado. Modelos e erro de envio visível estão feitos. Faltam resposta a reclamação, nota interna e o piloto. |
| 3 Ag. cancelamento · 4 Troca | Não começaram no atendimento. As peças do Bling existem em outros módulos. |
| 5 Tuta · 6 Sites | Não começaram. Do lado dos sites, Uranyx e Charlots já geram protocolo (pacote ainda não publicado). |
| 7 Mídia · 7b Avaliações | Parcial: Direct do Instagram (só leitura) e índice de avaliações da Shopee. |
| 7c Zap · 7d Carrinho | Nada. |
| 8 Shopee/TikTok · 9 Amazon e demais | Leitura feita. Faltam envio, devoluções e disputas na caixa e desligar o Duoke. |

### 2.3 Bling, Ag. cancelamento e troca (RF3 e RF4)

- **83955 não é só cancelamento.** Nos últimos 30 dias:
  - o robô da **Margem** pôs **264** pedidos nessa situação, como trava interna (182 voltaram para Em aberto);
  - o sweep de NF pôs **79** por **falta de estoque** (76 Shopee; 57 terminaram cancelados) e **47** por **restrição de envio**;
  - há também os movimentos manuais.

  Abrir conversa ou avisar o comprador em toda entrada em 83955 mandaria mensagem a quem só está com o pedido em análise de margem. **O gatilho tem de filtrar pela origem.**
- O nome no Bling é **"Aguardando Cancelamento"**. O catálogo `situacao_bling` já é sincronizado, e o padrão do DaVinci é uma constante numa fonte única (`services/bling_situacoes.py`). O id 83955 está fixo hoje em `routers/nf.py`, `margem_auto_hold.py`, `aprovar_margem.py`, `estoque.py` e `margens.py`.
- **O webhook de pedidos do Bling já existe e está em uso** (`pedido.alteracao.situacao` → `upsert_order`, que já conhece a situação antiga e a nova). Não é "[confirmar]". A rede de segurança de 10 minutos não cobre 83955; basta incluir.
- **O motivo não deve vir principalmente da observação:**
  - o robô de falta de estoque **não escreve nada** nas Observações; o motivo e os SKUs ficam em `nf_faturamento.erro_faturamento`;
  - a Margem e a restrição gravam recados próprios.

  O motivo vem do dado do DaVinci, e a 1ª linha das Observações só serve para os casos manuais.
- **Pergunta 4, respondida para as automações:** o DaVinci lê e grava sempre o campo `observacoes`, no formato `dd/mm - texto` no topo (`compose_observacoes`), nunca `observacoesInternas`. **Atenção:** `bling_orders.observacao` é um campo **local** da aba Margem, não espelho das Observações do Bling (`routers/margens.py:1069-1078`). As Observações do Bling só vêm pelo GET do pedido.
- **"Uma conversa por pedido":** 9 dos 12 pedidos hoje em 83955 já têm conversa no /atendimento (8 Shopee, 1 ML). O cartão de Ag. cancelamento vai na conversa existente, e só se cria conversa interna quando não houver nenhuma.
- **Saldo "por depósito":** o DaVinci usa só o depósito padrão. O local real do estoque é o **lote no sufixo do SKU** (`.ci/.sp/.pi/.ra/.sa/.us/.cd`), com soma por família.
- **Kits:** o Bling dá ao kit o saldo do menor componente, e a composição está em `bling_kit_components` (1.745 kits). Editar um item de kit faz o Bling baixar a composição antiga, então a troca deve **substituir** o item, sem id.
- **Categoria do Bling é larga demais:** são só 12 (Celular, Celular Kit, Mala…). O segmento do DaVinci está nas linhas da Tabela de Preços (`pricing_products`), não no produto (`products.segment_id` está preenchido em 1 produto ativo).
- **Custo:** a fonte certa é `products.bling_cost_price` do SKU exato, preenchido em 100% e conferido todo dia; o kit tem custo próprio. `pricing_products.bling_cost_price` só existe em 68 de 112 linhas.
- **Trocar para o mesmo produto em outro lote já é automático:** o robô de prioridade fez 2.014 trocas de SKU em 7 dias. "Sugerir troca" é para **outro produto**.
- **"Ag cancelamento → Em aberto" são dois degraus:** 83955 → 9 (Atendido) → 6, sempre **depois** do PUT de itens e observação. Depois da troca, o pedido **não volta sozinho para a NF**, porque `status_faturamento='sem_estoque'` trava o sweep.
- **A Margem pode segurar de novo um pedido trocado** com custo até +5%: é preciso pré-checar a margem ou gravar uma decisão auditada.
- **"O Bling pode não deixar editar pedido de marketplace":** o robô de prioridade já edita itens de pedidos Shopee, ML, TikTok e Amazon todo dia. O pedido sem estoque é barrado antes da NF, então no caso típico não há NF-e.
- **Foto do modal:** `products.image_url` está vazio em todos os ativos. A foto tem de vir do anúncio (`product_links`) ou da pasta MEGA (`pricing_products.fotos_url`).

### 2.4 E-mail, Tuta e sites (RF5 e RF6)

- **Já existe um leitor de tela do Tuta** (`apps/executor/src/tuta.ts` + `services/tuta_devolucoes.py`, com cron às 07:00). Ele só lê o texto da caixa de entrada, e **nunca rodou de verdade**: os 15 comandos de 17/09 a 01/10 falharam com "ação não suportada pelo executor".
- **E-mail da loja:** o campo existe (`store_info.email`), preenchido em 80 de 81 lojas ativas, mas **só 5 guardam o endereço completo** (domínios makisa.com.br e uranyx.com.br). 64 guardam só a parte antes do @ (a JLAS2 está como `16tr`), e 11 guardam `@marca` sem o resto do domínio (`…@uranyx`, `…@poofy`, `…@locagil`). Nenhum está como `@tuta.com`. Além disso, `store_info.integration_id` só está preenchido em 13 lojas do ML. Antes de mapear e-mails é preciso normalizar os endereços e ligar cada loja à integração.
- **Escopo do RF5:** as pastas "vendas" e "mensagens" de ML, Shopee, TikTok e Magalu repetem o que já é lido por API. O ganho real está em problema e reclamação e nas plataformas sem API de aviso. Se "mensagens X" gerar pendência, a fila fica duplicada com o chat.
- **Sites:**
  - Uranyx e Charlots são PHP próprio na Hostinger e já geram protocolo atômico (`Protocolo.php`). O pacote de 01/10 ainda não foi publicado: `/sac` dá 404.
  - A **7buyers é Shopify**, sem protocolo.
  - **locagil.com.br não respondeu** em 01/10.
  - Nenhum dos 4 está no store-info; eles estão em Cadastros › Marcas. A plataforma "Site" foi liberada no store-info em 01/10, mas há 0 linhas.
- **Não existem as caixas atacado@ e duvidas@:** os 3 tipos vão para **um único e-mail de SAC**, e o tipo sai só do prefixo do protocolo. O SAC da Charlots é `sac@poofy.com.br`, uma caixa da Hostinger que encaminha para o Tuta.
- **Os sites não devem entrar lendo o e-mail no Tuta.** O site já guarda o chamado estruturado e já conversa com o DaVinci de servidor para servidor, com token (`sites_estoque`). O caminho recomendado é o site **mandar o chamado em JSON** para o DaVinci, e o e-mail fica só como cópia.
- **SPF/DKIM:** o DMARC (`adkim=s`) passa com o **DKIM alinhado**; incluir o serviço no SPF é recomendável, mas não é o que decide. A receita já está aplicada no `hadken.com` (MX no Tuta + SPF e DKIM do Mailjet).

### 2.5 Mídia, avaliações, carrinho e Zap (RF7 a RF10)

- **✈ / conexão oficial:**
  - Não é só a Uranyx. Há token de publicação para **7buyers IG**, **Charlots IG/FB/YT** e **Uranyx IG/FB/YT** (7 contas), e Direct conectado em 7buyers, Charlots e Uranyx.
  - O ✈ da Uranyx também vale para o TikTok, que publica pelo AdsPower, sem token.
  - A Locagil não tem nenhuma conexão.
- **Os tokens servem só para publicar.** Faltam os escopos de leitura de comentários e insights, e o YouTube precisa do escopo `youtube.force-ssl` para responder.
- **A conexão da Meta não é por OAuth:** cola-se um token de System User. O token do **Direct vence em 16/11/2026** e não se renova sozinho.
- **TikTok de conteúdo:** o app foi recusado em duas auditorias, então só daria por robô no AdsPower.
- **Volume real de mídia é baixo:** as 69 publicações automáticas somam cerca de 1 comentário. O Direct teve 9 conversas desde 17/09, a última em 23/09; vale conferir se o webhook da Meta continua assinado.
- **Avaliações:**
  - Na Shopee, 92,5% das 3.741 já têm resposta da loja feita por fora. Nos últimos 30 dias foram 1.720 avaliações, 55 com nota de 1 a 3 e só 3 dessas sem resposta. A regra "todas pendentes" criaria cerca de 57 pendências por dia já respondidas.
  - No ML, o que se prende ao pedido é a avaliação da **venda** (positivo/neutro/negativo), não estrelas.
- **Carrinho:**
  - Charlots e Uranyx são **atacado**: o carrinho é do lojista logado e termina "pelo WhatsApp", sem checkout, frete, cupom, etapa nem link de recuperação. Quando o lojista confirma o envio, o site **apaga** o carrinho sem histórico.
  - A 7buyers (Shopify) tem API de checkouts abandonados.
- **Zap:**
  - Não há nenhuma integração de WhatsApp. O "Fone/WhatsApp" é `marcas.sac_fone`, preenchido em 4 de 9 marcas.
  - A sugestão "telefone igual ao do cliente no Bling" vai acertar pouco, porque o espelho do Bling não guarda telefone e os marketplaces não informam o telefone real do comprador.

### 2.6 Modelo de dados (seção 7)

| Do spec | No DaVinci |
|---|---|
| `conversa` | `atendimento_conversas` já existe. Faltam `etiqueta_atual`/`etiqueta_desde`, além de `pedido_bling_id` e `reclamacao_id` como colunas. **Ficar só com `etiqueta_atual` + histórico** (o `tipos[]` do spec é redundante). |
| `mensagem` | `atendimento_mensagens` já existe, com status recebida/enviando/enviada/falhou/revisar e erro. Faltam o autor "mediador", o tipo "interno" (nota interna) e o canal por mensagem. |
| `modelo_resposta` · `sincronizacao` | Já existem: `atendimento_modelos` (48) e `atendimento_canais` (cursor, `ultimo_ok_em`, erro). |
| `conta_rede` | **Não criar.** Já existem `redes_sociais` + `redes_sociais_tokens` (+ `dm_contas`). |
| `publicacao` | Existe em parte: `marketing_postagens` + `marketing_postagem_metricas` + criativo → produto. Só cobre o que o DaVinci publicou. |
| `avaliacao` | Existe em parte: `atendimento_avaliacoes_loja`, só Shopee, sem mídia nem dados de resposta. Não confundir com `atendimento_avaliacoes`, que é a nota da pessoa sobre a sugestão da IA. |
| Faltam | `historico_etiqueta`, tabela de reclamações, `ag_cancelamento`, `troca_produto`, `email_recebido`, `regra_pasta_email`, `chamado_site`, `agrupamento`, `comentario`, `carrinho`, `zap_numero`, `zap_vinculo`. |
| O spec não prevê | A camada de IA: `atendimento_rascunhos`, `atendimento_regras` (64), `atendimento_categorias` (42), validador e modos. Acrescentar ao spec, junto com a regra de que a IA nunca envia troca, cancelamento ou reclamação. |

### 2.7 Conflitos com decisões já tomadas

1. **Bolinha da loja.** Hoje é sempre vermelha para conversa esperando resposta de verdade (decisão de 01/10, "como o Duoke"). O spec quer vermelho só com reclamação ou Ag. cancelamento.
2. **Ordenação.** Hoje a lista abre em "Todas", com a mais recente primeiro (commit `0e5b9064`). O spec quer o prazo mais curto primeiro. Proposta: prazo primeiro **só na aba "Falta responder"**.

### 2.8 Onde os conferentes divergiram (e o que vale)

| Ponto | Divergência | O que vale |
|---|---|---|
| Observação do Bling no painel | Um conferente disse que `bling_orders.observacao` é espelho do Bling. | É campo **local** da aba Margem (conferido em `routers/margens.py:1069-1078`). As Observações do Bling exigem GET ao vivo. |
| Fonte do saldo de estoque | Um conferente indicou `products.stock` (webhook); outro, o saldo virtual ao vivo do Bling com cache (`saldo_virtual_total` só em 559 simples ativos). | Painel: `products.stock`, mostrando a hora. Modal e confirmação da troca: consulta ao vivo no Bling, com cache curto. |
| Leitor do Tuta | Um conferente disse "o executor já lê o Tuta". | Existe no código, mas **nunca rodou** em produção (15 de 15 comandos falharam, conferido). |
| Contas | "55 canais" contra "80 canais". | 80 canais em 55 contas (conferido). |
| Lojas do Duoke | 44 contra 43. | 43 ativas (44 contando uma arquivada, conferido). |
| E-mail da loja | "100% preenchido" contra "64 só com a parte antes do @". | Preenchido em 80 de 81 ativas, mas só 5 com o endereço completo (conferido). |
| Pergunta 6 (custo) | "Não decidido" contra "respondida". | Recomendação técnica: `products.bling_cost_price` do SKU exato. Falta o dono confirmar. |
| Esforço da lacuna 297840 | M contra G. | M para o job de reclamações do ML + ligar ao pedido e ao painel. G se incluir o canal de reclamação com as mensagens do mediador e as ações. |

---

## 3. Tabela por requisito

### 3.0 Regras para o programador (seção 3 do spec)

| Regra | Status | Onde está / o que falta |
|---|---|---|
| Copiar o spec para `docs/comunicador/` e citar no `CLAUDE.md` | ✏️ | O módulo é o /atendimento. Se o spec for guardado no repositório, o nome deve seguir o padrão (`docs/atendimento-comunicador-spec.md`), e a regra no `CLAUDE.md` deve apontar para ele e para este levantamento. |
| Reaproveitar o que existe, sem cadastro paralelo | 🟡 | Contas, pedidos, produtos, estoque e chamados são os do DaVinci. A exceção é a lista do robô Temu/Ali em `apps/api/scripts/atendimento_robo_lojas.json` (§2.2). |
| Nenhuma mensagem ao comprador sem clique humano | 🟡 | Hoje nada sai (observação). Mas o modo **auto** existe no código (desligado), e o **robô de DM do Instagram** (`dm_auto` em 3 contas) responde sozinho (§2.1). |
| Nenhuma alteração no Bling sem confirmação explícita, com auditoria | ❌ | O atendimento não altera o Bling hoje; a troca do RF4 ainda não existe. Os robôs de outros módulos (Margem, NF, prioridade de estoque) alteram sozinhos, fora do escopo do Comunicador. |
| Testar no Bling primeiro em pedido de teste | ❌ | O pedido de teste ainda não foi separado (Onda 0.8). |
| Segredos só em variável de ambiente; senha do Tuta fora do DaVinci; redes pelo login oficial | ✏️🟡 | Tokens das redes cifrados no banco (`token_enc`) e segredos de app no `.env`; nenhuma senha do Tuta. A Meta não é por OAuth: é um token de System User colado (§2.5). |
| Texto de comprador é dado, nunca instrução | ✅ | Bloco de dado no prompt e detector de injeção (`ia.py:209` e `ia.py:892-933`). |
| E-mail: mostrar o remetente real, não abrir links nem carregar imagens externas | 🟡 | Amazon: o HTML vira texto e o remetente é filtrado (`amazon_email.py`). Faltam o selo de remetente suspeito e o "mostrar imagens" (RF5). |

### 3.1 RF1 ① Contas

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Contas agrupadas por plataforma, com logo e nome (incluindo os grupos Site e Mídia) | 🟡 | `AtendimentoLojas.vue:41`: ordem Shopee, TikTok, ML, Amazon, Magalu, Temu, AliExpress, com o nome do cadastro (`services/atendimento/lojas.py`). Grupo "Instagram › Direct" separado. | Grupo Site (RF6); grupo "Mídia" por marca (RF7); listar também as contas do store-info sem integração | P | RF6/RF7 para ter conteúdo |
| Contador de pendentes, vermelho só com reclamação ou Ag. cancelamento | 🟡 | Bolinha = conversas esperando resposta de verdade, sempre vermelha (decisão de 01/10) | Contar por etiqueta no `/resumo` e pintar de vermelho só com RECLAMAÇÃO ou AG. CANCELAMENTO | P | Etiqueta (RF1-Et); decisão do dono (conflito §2.7) |
| "Todas as contas" no topo; clicar no grupo filtra | ✅ | `AtendimentoLojas.vue` (`escolherTodas`, `escolherPlataforma`) | — | — | — |
| Aviso de conta desconectada ou com token expirado | 🟡 | Loja apagada com cadeado, alerta ou tomada solta para sem_escopo, erro, parado e sessao_caiu, com o motivo no tooltip (`AtendimentoLojas.vue:255-262`). Hoje: 2 TikTok sem escopo, ML Poofy 403, ML com token que não renovou, 1 Shopee com erro, 1 Temu com sessão caída. | Mostrar as contas do cadastro sem integração como "não conectada"; motivo visível sem depender do tooltip | P | — |

### 3.2 RF1 ② Lista de conversas

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Linha com comprador, conta, logo, hora e prévia | ✅ | `AtendimentoLista.vue` | — | — | — |
| Etiqueta do tipo na linha | ❌ | Os selos atuais são operacionais: a conferir, IA sugeriu, atribuída, pausada, bloqueada, fechada | Mostrar `etiqueta_atual` + indicador pequeno da 2ª mais urgente | P | RF1-Et1 |
| Ícone ✉ com a quantidade de e-mails | ❌ | Não há vínculo entre e-mail e conversa | — | P | RF5 |
| 🔴 Reclamação com contagem regressiva do prazo | ❌ | Só o prazo genérico por SLA e os filtros Vencendo/Vencidas. Já existe o mecanismo de prazo da plataforma (`CHAVE_PRAZO_PLATAFORMA`, usado no SAC da Magalu) e o prazo TikTok em `devolucao_rastreio.prazo_acao_auto`. | Faixa e fundo por etiqueta; prazo real (claim do ML, `due_date` da Shopee, `seller_next_action` do TikTok) | M | Reclamações do ML (§4 lacuna) |
| 🟣 Devolução | ❌ | Os dados existem fora do atendimento: `devolucao_rastreio`, Bling 83957 (83 conversas ligadas hoje), `Logistica.meli_status.return_status` (113 conversas ligadas a pedido com devolução ou reclamação) | Etiqueta DEVOLUÇÃO a partir desses dados + destaque | P | RF1-Et1 |
| 🔵 Pré-venda; pós-venda comum sem destaque | 🟡 | Filtro "Pré-venda" (canal pergunta ou sem pedido), `routers/atendimento.py:1068-1081` | Etiqueta visível + destaque azul | P | RF1-Et1 |
| 🟠 Ag. cancelamento | ❌ | 9 conversas abertas hoje com pedido em 83955, sem marca | Etiqueta pela situação do Bling, filtrando a origem | P | RF3 |
| SAC/Atacado/Dúvidas, 💗 Mídia, ⭐ Avaliação, 🛒 Carrinho, ícone do Zap | ❌ | Nenhuma dessas fontes ligada à lista | Só o selo na lista | P | RF6 a RF10 |
| Filtros por tipo em chips com contagem | 🟡 | Abas Todas e Falta responder + menu Filtrar com 11 filtros (`routers/atendimento.py:198`). Contagem só em alguns. | Filtro por etiqueta com contagem no `/resumo` | P | RF1-Et1 |
| Filtro "E-mail sem vínculo" | ❌ | Só a fila "Amazon (conta não identificada)" | — | P | RF5 |
| Busca por comprador, nº do pedido, SKU e protocolo | 🟡 | Busca em comprador, pedido no marketplace, anúncio, conta, id externo e resumo (`routers/atendimento.py:1083-1099`) | SKU (`bling_orders.item_codigo`), nº do Bling (`bling_orders.numero`), protocolo | P | Protocolo depende de RF6 |
| Ordenação pelo prazo mais curto | ❌ | Ordena pela última mensagem (decisão de 01/10) | Prazo primeiro na aba "Falta responder", com paginação por (prazo, id) | P | Decisão do dono (§2.7) |

### 3.3 RF1 Etiqueta = status atual

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Uma etiqueta por conversa, que muda sozinha | ❌ | `atendimento_conversas` só tem `situacao` aberta/respondida/fechada/bloqueada (`models/atendimento.py:228`) | Migration (`etiqueta_atual`, `etiqueta_desde`, `etiqueta_manual` + tabela de histórico). Serviço `etiquetas.recalcular` chamado no gravar/sync e num cron que cruza Bling, Logística, `devolucao_rastreio`, reclamações e avaliações. | G | — |
| Mudança na linha do tempo e no histórico; filtros e contadores pela etiqueta | ❌ | Linha do tempo do cartão Cliente e `/resumo` por loja já existem | Evento "etiqueta mudou de X para Y"; `/resumo` e filtros por etiqueta | M | Et1 |
| Pré-venda → Pós-venda quando o pedido aparece | 🟡 | A regra já existe no filtro. Pedido ligado em 2.360 de 3.451 conversas Shopee, 244 de 510 TikTok e 145 de 145 ML pós-venda. | Gravar como etiqueta e registrar a passagem | P | Et1 |
| Reclamação aberta → RECLAMAÇÃO; encerrada → PÓS-VENDA | 🟡 | ML: `claim_ids` lidos do pack (`atendimento/ml.py:1103-1107`); 10 conversas hoje. **Correção (revisão, 01/10/2026):** o ML NÃO zera `claim_ids` quando a reclamação acaba — dos 10 packs, 6 `active` e 2 `blocked_by_cancelled_order` eram de reclamação encerrada ou de cancelamento; só o chat bloqueado (`blocked_by_claim`/`blocked_by_mediation`) indica reclamação aberta. Shopee e TikTok: na Logística, sem ligação. | Ligar as fontes ao motor; tratar o encerramento | M | Lacuna §4 |
| Devolução iniciada ou encerrada | 🟡 | `devolucao_rastreio` (status e tipo automáticos), `meli_status.return_*`, Bling 83957 | Regra de "vivo × encerrado" (como `logistica_tiktok._melhor_devolucao_por_pedido`) | P | Et1 |
| Bling em Ag. cancelamento ou saída dela | ❌ | `bling_orders.situacao` mantido pelo webhook | Regra no motor (o RF3 cria o cartão) | P | Et1, RF3 |
| Avaliação, site, mídia, carrinho | ❌ | Só a avaliação Shopee (`resposta_loja` indica se foi respondida) | Regras quando as fontes existirem; a avaliação Shopee pode entrar já | M | RF6 a RF9 |
| Prioridade Reclamação > Ag. canc. > Devolução > Avaliação > Carrinho > Pré > Pós | ❌ | — | Ordem fixa + campo das etiquetas secundárias | P | Et1 |
| Canais nunca são etiqueta | ❌ | Canal mostrado como rótulo à parte (Pergunta, Pós-venda, SAC, E-mail) | Garantir por construção: o vocabulário de etiquetas não inclui canais | P | Et1 |
| Troca manual registrada; o próximo acontecimento volta a valer | ❌ | `PATCH /api/atendimento/conversas/{id}` já existe (`routers/atendimento.py:1838`) | Campo etiqueta no PATCH + histórico "por usuário" | P | Et1 |

### 3.4 RF1 ③ Conversa

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Cabeçalho com comprador, plataforma + conta, nº do pedido e botão AdsPower | 🟡 | Avatar, loja, canal, nº do pedido com copiar, selos, modo e prazo (`AtendimentoConversa.vue:1370-1415`) | Botão AdsPower (RF11), protocolo (RF6), nº do Bling | P | RF11 |
| Autores: comprador, equipe, mediador, sistema | 🟡 | `autor` cliente/loja/sistema + `origem` (Equipe/IA/Fora do DaVinci). As mensagens do mediador do ML só são lidas por `chamados_devolucao_sync.py:1062`. | Autor "mediador" e leitura das mensagens do claim | M | Lacuna §4 |
| Anexos com visualização | ✅ | 5.892 mensagens com anexo; miniatura que abre grande | Opcional: guardar cópia, porque a URL da CDN expira | — | — |
| Falha de envio com motivo e "tentar de novo" | 🟡 | Status falhou com motivo legível (`erroEnvioLegivel`, `AtendimentoConversa.vue:1681`); "revisar" pergunta "Saiu/Não saiu" | Botão "tentar de novo" no balão; validar no piloto | P | Ligar o envio numa loja piloto |
| Caixa com modelos, anexo e nota interna | 🟡 | 48 respostas prontas no menu da caixa. `ResponderIn` só aceita texto (`schemas/atendimento.py:307`). | Nota interna (nunca enviada, fora da pendência e fora do prompt da IA); anexo no envio por plataforma | M | Confirmar a API de upload de cada plataforma |
| Bloqueio da plataforma mostrado no lugar da caixa | ✅ | Faixa "A plataforma não deixa mais responder" (`AtendimentoConversa.vue:1587`); 17 conversas ML bloqueadas hoje | Traduzir os códigos `blocked_by_*` | P | — |

### 3.5 RF1 ④ Detalhes do pedido

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Situação na plataforma e no Bling + tags (ex.: Reputação afetada) | 🟡 | Retrato Shopee/ML (`enriquecer.py`); situação do Bling em "No DaVinci" (`contexto.py`) | Tags do pedido (ML `order.tags`, claim que afeta reputação); retrato TikTok/Magalu | M | — |
| Nº no marketplace e no Bling com copiar, data | ✅ | `AtendimentoPedido.vue:494-525` | — | — | — |
| Produto: foto, nome, variação, SKU, quantidade, preço | 🟡 | Completo no retrato Shopee/ML; nas demais, só os itens do Bling | Retrato TikTok/Magalu/Amazon ou catálogo | M | — |
| **Saldo em estoque do SKU comprado** | ❌ | Os dados existem (`products.stock`, `saldo_fisico`, `saldo_virtual_total`); `contexto.py` não consulta | Saldo por item com hora; verde ou vermelho contra a quantidade; lotes irmãos; kit explodido | P–M | — |
| Reclamação (ID, motivo, status, prazo) e devolução | 🟡 | Mostra chamados e devoluções **internos** (`contexto.py:222-330`) | Bloco com o claim ou return da plataforma | M | Lacuna §4 |
| Valores (total, pagamento, margem) | 🟡 | Valor, método e data no retrato Shopee/ML | Margem reaproveitando `_MARGEM_SQL` (`claude_tarefas.py`); valores das outras plataformas | P | Decidir quem vê a margem quando abrir à equipe |
| Logística (envio, rastreio, última posição) | ✅ | Retrato + "Entrega (Logística)" (`contexto._logistica`) | — | — | — |
| E-mails da venda | ❌ | — | — | P | RF5 |
| Observações do Bling (só leitura) e observação interna | ❌ | Nada no painel. `bling_orders.observacao` é campo local da Margem (ver §2.8). | Ler as Observações pelo GET do pedido, com cache (texto de terceiros: só na tela, nunca no prompt); campo de observação interna com autor e data | P | Rate limit do Bling |
| Botões "Abrir no Bling" e "Abrir na plataforma" | 🟡 | "Abrir na plataforma" já existe, na conversa ③, para Amazon (caso no Seller Central), Magalu (portal) e Temu/Ali (Seller Center) (`AtendimentoConversa.vue:1527-1563`). No painel ④ só há links internos (Logística, Chamados, Devoluções). `bling_orders.bling_id` está disponível. | "Abrir no Bling" em todas; "Abrir na plataforma" para ML, Shopee e TikTok; levar os botões para o painel ④ | P | Confirmar as URLs atuais |

### 3.6 RF2 Reclamação: conversas embaixo

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Cartão da reclamação com ações (aceitar devolução, oferecer solução, pedir mediação) | ❌ | O cliente ML já tem `get_claim`, `get_claim_messages`, `open_claim_dispute`, `send_claim_message` e `return_review_fail` (`services/marketplaces/ml.py:257-398`). A disputa Shopee e a recusa TikTok estão em `chamados_devolucao.py`. "Aceitar devolução" e "oferecer solução" não existem. | Cartão + botões com confirmação e auditoria; ação só com clique humano | G | Confirmar os endpoints atuais das ações |
| Abas Pré · Pós · Reclamação · Mediador com as conversas do mesmo comprador ou pedido | ❌ | Uma conversa por (loja × canal × id externo). O vínculo aparece só na linha do tempo do cartão Cliente e em "outras perguntas". 155 compradores têm 2 ou mais conversas na mesma loja. | Endpoint de conversas relacionadas + abas com contagem | G | Lacuna §4 para Reclamação e Mediador |
| Abas E-mail · Zap · Avaliação em todas as conversas | ❌ | Só a Amazon tem canal e-mail; avaliações só no cartão Cliente | Estrutura de abas fixa | M | RF5, RF8, RF10 |
| Contagem por aba; a resposta sai no canal da aba | ❌ | Envio por conversa (`POST /conversas/{id}/responder`) | Contagem; roteamento + validador para mediador, e-mail, Zap e avaliação | M | Abas |
| ML: pré = perguntas antes da compra; pós = pack; reclamação/mediador = claim | 🟡 | Uma conversa por pack. O cartão Cliente já conta as perguntas feitas **antes de alguma compra** e as põe na linha do tempo, junto com a reclamação do pack (`cliente.py:732`). `_outras_perguntas` (`contexto.py:357`) só aparece quando a conversa aberta é uma pergunta: no pack, as perguntas não aparecem. | Aba Pré-venda no pack com as mensagens das perguntas, reaproveitando o corte do cartão; canal de reclamação | M | Lacuna §4 |
| Shopee/TikTok: chat único, antes ou depois do pedido; reclamação = disputa ou devolução | 🟡 | Chat único (feito); pré × pós decidido pela conversa inteira | Separar mensagens pela criação do pedido; anexar a devolução ou disputa como aba | M | §4-b |

### 3.7 RF3 Ag. cancelamento

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Gatilho (id da situação não fixo) | ✏️❌ | `situacao_bling` sincronizado; 83955 fixo em 5 módulos; 12 pedidos em 83955 na manhã de 01/10 (4 sem estoque, 4 da Margem, 3 manuais, 1 antigo) e 14 na reconferência da tarde | Constante única em `bling_situacoes.py`; gancho em `upsert_order` (`services/bling_orders.py:617`) e chamada direta nos robôs (`nf._marcar_aguardando_cancelamento`, `margem_auto_hold._hold_one`); **filtro de origem** | P | Decisão: quais origens geram cartão |
| Detecção (webhook ou consulta a cada 5 min) | 🟡 | Webhook em uso (`routers/webhooks.py:303/418`); safety net de 10 min sem 83955 | Incluir 83955 na safety net | P | — |
| Uma conversa por pedido + cartão de sistema | ✏️❌ | 9 dos 12 pedidos já têm conversa | Tabela `atendimento_ag_cancelamento`; mensagem de sistema na conversa existente; conversa interna só quando não houver nenhuma; reabrir se o pedido voltar a 83955 | M | Decisão: conversa interna ou tarefa |
| Motivo = observação do Bling | ✏️❌ | Falta de estoque grava em `nf_faturamento.erro_faturamento`; Margem e restrição gravam recado próprio | Motivo pelo dado estruturado; 1ª linha das Observações só para os manuais | P | Prefixo padrão dos manuais |
| Data e hora de entrada | ❌ | `bling_orders` não tem `situacao_desde` | `detectado_em` na tabela nova | P | — |
| Classificação do motivo | ✏️❌ | Motivos reais: margem (segurado ou reprovado), falta de estoque, restrição de envio, manual | Classificador em 2 camadas: origem + palavras-chave editáveis para os manuais | P | Lista de motivos |
| Rascunho por motivo, revisado | 🟡 | 3 respostas prontas do tema; a IA recebe "Aguardando Cancelamento" **sem o motivo** (`ia.py:2025`) e corre o risco de falar em cancelamento num hold de margem | Modelo do aviso de falta de estoque e de restrição; rascunho de sistema sem IA; passar o motivo à IA com a regra de nunca revelar hold de margem | P | Textos aprovados |
| Encerramento com resultado | ❌ | `upsert_order` compara a situação antiga e a nova. Desfecho dos 79 sem estoque: 57 cancelados, 11 entregues, 5 em andamento… | Gancho de saída + mensagem de sistema | P | — |
| Regras por plataforma para iniciar a conversa | 🟡 | ML: limite de 350 caracteres e bloqueio tratados, mas só **responde** em pack existente. Amazon: só responde e-mail recebido. | Matriz "pode iniciar?" por canal; selo "interna — avisar pelo Seller Center"; depois o action guide do ML | M | Docs ML/Amazon/TikTok; envio desligado |

### 3.8 RF4 Estoque + sugestão de troca

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Saldo no painel (por lote, com hora, verde/vermelho) | ❌ | Painel sem estoque (`AtendimentoPedido.vue:497-525`); saldo ao vivo já consultado pelos robôs (`routers/nf.py:1058`) | Saldo por item, lotes irmãos, hora da leitura | M | Rate limit do Bling |
| Kits: saldo dos componentes | ✏️❌ | `bling_kit_components` (1.745 kits); regra do menor componente (`estoque_familia.py`) | Explodir o kit e destacar o componente que limita | P | — |
| Botão "Sugerir troca" | ❌ | 79 pedidos sem estoque em 30 dias; cerca de 71 teriam algum candidato | Botão no cartão do RF3 | P | RF3 |
| Regra de categoria | ✏️❌ | 12 categorias Bling; segmento em `pricing_products` | Categoria Bling + linha ou segmento da Tabela de Preços; mapa manual opcional | M | Pergunta 5 |
| Custo ≤ +5% | 🟡 | `products.bling_cost_price` em 100% dos produtos; `bling_orders.preco_custo` | Comparar no SKU exato; definir o piso | P | Perguntas 6 e 7 |
| Estoque do candidato ≥ quantidade | 🟡 | Lógica pronta em `prioridade_estoque.py:109-130, 257-345` | Consulta ao vivo ao abrir o modal e de novo ao executar | P | Rate limit |
| Unidade com unidade, kit com kit, voltagem | 🟡 | Kit = "+" no SKU ou formato E; 13 SKUs com sufixo de voltagem | Mesmo formato + mesmo sufixo ou nome de voltagem | P | — |
| Exclusões (o próprio SKU, inativos) | ✏️❌ | `products.situacao`; troca de lote automática pelo robô | Excluir; mostrar "mesmo produto em outro lote" separado | P | Decisão: lote precisa de aceite? |
| Ordem (menor diferença, depois maior estoque) | ❌ | — | Ordenar no endpoint | P | — |
| Colunas do modal (foto, custo, %, estoque, margem); acima de +5% esmaecidos e não selecionáveis | ❌ | `image_url` vazio; margem calculada em `verificar_margem.py` | Endpoint de candidatos com foto do anúncio e simulação de margem; devolver também os que ficaram de fora, com o motivo (custo, estoque, tipo), para aparecerem esmaecidos | M | — |
| Enviar opções ao comprador | ❌ | Envio desligado | Texto com as opções (em observação: "Copiar" para o Duoke) | P | Ligar o envio |
| "Comprador aceitou" vinculando a mensagem | ❌ | Mensagens imutáveis com id | `mensagem_aceite_id` obrigatório | P | — |
| Tela de confirmação do que muda | ❌ | Padrão de confirmação na Logística e na Margem | Dry-run: antes e depois, valor, observação, 83955 → 9 → 6, aviso de margem | P | — |
| Ler o pedido e trocar o item mantendo o valor | 🟡 | `build_observacoes_put_body` (`logistica_bling.py:91-131`) + `aplicar_trocas_nos_itens` (`prioridade_estoque.py:517-549`) | Serviço de troca manual de 1 item, sempre no modo substituir | M | Pedido de teste |
| Observação da troca | 🟡 | `compose_observacoes` no mesmo PUT | Texto `dd/mm - TROCA antigo -> novo, aceita em … - Atendimento/<usuário>` | P | — |
| Ag. cancelamento → Em aberto | ✏️🟡 | Dois degraus já usados pela Margem (`bling.py:498-518`) | Reusar; **re-enfileirar a NF** depois da troca | P | — |
| Recalcular a margem e fechar o alerta | 🟡 | Ingest recalcula a margem; a Margem pode segurar de novo | Pré-checar a margem; decisão auditada; fechar o cartão com "troca" | P | Decisão: abaixo do mínimo pode? |
| Falha sem deixar o pedido pela metade | 🟡 | PUT único e atômico; PATCH depois | Máquina de estados + "retomar" na tela | M | — |
| NF-e, integração, PUT e prova | ✏️🟡 | PUT seguro resolvido; integração não impede | Travar se houver NF emitida; guardar o aceite | P | Alinhamento fiscal |
| Auditoria e permissão | 🟡 | `margem_audit` (`mudado_por`); Histórico; só admin hoje | Tabela `troca_produto`; origem `atendimento_troca`; permissão própria | P | — |

### 3.9 RF5 E-mails do Tuta

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Como ler e enviar pelo Tuta (opções A, B, C) | 🟡 | Leitor de tela (`apps/executor/src/tuta.ts`) que **nunca rodou**; robô do AdsPower no padrão Temu/Ali; a Amazon resolveu com IMAP | Escolher e registrar: navegador invisível no servidor, AdsPower no Mac mini ou caixa com IMAP. Abrir no Tuta marca como lido, então o robô precisa desmarcar. | G | Decisão do dono; número de contas Tuta (48 cadastros tuta); 2FA |
| Só pastas com nome de plataforma; tabela configurável | ❌ | Nada; o store-info usa `ml` e `mercadolivre` | `regra_pasta_email` + tela + leitor de pastas | M | Leitor |
| Loja pelo destinatário (Para/Delivered-To) + pasta; a pasta desempata; fila "E-mail sem loja" | 🟡 | `store_info.email` com só 5 endereços completos (§2.4); `integration_id` em 13 lojas; a Amazon já descobre a loja pelo destinatário | Normalizar o domínio; ligar a loja à integração; ler o Delivered-To; desempatar pela pasta quando o mesmo e-mail estiver em plataformas diferentes; fila "E-mail sem loja" | M | Dono confirmar os domínios |
| Prender ao cliente ou pedido; fila "sem vínculo" | 🟡 | Só Amazon: `RE_PEDIDO`, fio por In-Reply-To, adoção manual da conta | Padrão de pedido por plataforma (hoje só existe o da Amazon, `amazon_email.py:154`); 2º passo por nome + produto + data dentro da loja; `email_recebido` com `conversa_id` opcional; "Vincular" auditado | M | Formato do nº em cada plataforma |
| Aba E-mail, ícone na lista, "E-mails da venda" no painel | ❌ | Canal e-mail só para a Amazon | Juntar à conversa do pedido + tela | G | RF2 abas; leitor |
| Prefixo da pasta define o efeito (vendas sem pendência) | 🟡 | `sem_resposta_necessaria`, autor sistema | Regra por prefixo. Retificar: "mensagens X" de plataforma com API duplica a fila. | P | Decisão do dono; leitor |
| Exibição segura (HTML limpo, imagens bloqueadas, remetente suspeito) | 🟡 | HTML → texto e filtro de remetente da Amazon (`amazon_email.py:338-400`) | HTML sanitizado + "mostrar imagens"; anexos baixáveis; domínios oficiais com selo | M | — |
| Sem duplicar e guardar o original | 🟡 | Message-ID como `externo_id` + UNIQUE | Guardar o `.eml`; separar duplicatas Amazon Tuta × Gmail | P | Leitor |
| Responder do mesmo endereço, no mesmo fio, com aviso de "não responder" | 🟡 | Amazon: Re:, In-Reply-To e SMTP, mas de um remetente único | Envio pelo Tuta escolhendo o endereço; aviso de "não responder"; "Abrir no Tuta" | G | Leitor; liberar envio |
| Alerta de leitor parado mais de 30 min | 🟡 | Status parado e sessao_caiu dos robôs | Canal "tuta" com pulso na barra | P | Leitor |
| Generalizar o e-mail da Amazon | 🟡 | Amazon em produção (4 canais ok) | Módulo de caixa genérico (N caixas no banco) | M | — |

### 3.10 RF6 Sites

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Lojas do tipo site no cadastro, grupo "Site" (Locagil só com SAC e Dúvidas) | ✏️❌ | As 4 marcas em `marcas` (`models/marca.py`); "Site" liberado no store-info (0 linhas); 7buyers = Shopify; Locagil fora do ar | Grupo Site a partir de marcas ou store-info | P | Decisão sobre 7buyers e Locagil |
| E-mail de formulário vira chamado; tipo pelo prefixo + caixa | ✏️❌ | Uranyx e Charlots gravam o chamado em `adm_atendimentos` e mandam e-mail `[US-26-0001]` para **um** SAC | **POST do site → DaVinci** (JSON + Bearer por site, como `SITES_ESTOQUE_TOKENS`); o tipo só pelo protocolo | M | Publicar o pacote SAC; alterar o site |
| Cartão com os campos do formulário | ❌ | O site já tem tudo estruturado (comprador, compra, produto, defeito, anexos com link assinado) | Receber o JSON + cartão no painel | M | POST do site |
| Protocolo e conferências (formato, repetido, sem protocolo) | 🟡 | Site gera atômico por prefixo e ano (`Protocolo.php`) | UNIQUE + alertas no DaVinci | P | POST do site |
| Responder pelo e-mail da marca; a resposta do cliente volta | 🟡 | Mailjet com From e Reply-To por marca (`services/email.py:87`, `email_marca.py`) | Envio por chamado com `[protocolo]`; Reply-To num subdomínio com inbound (Mailjet Parse ou Brevo) → webhook | M | DNS; decisão sobre atacado@ e duvidas@ |
| SPF/DKIM/DMARC | ✏️❌ | 4 domínios com MX no Tuta, SPF Tuta e DMARC `p=quarantine; adkim=s`; receita pronta no hadken.com | Autenticar cada domínio no Mailjet (DKIM + SPF) e testar no Gmail e no Outlook | P | Acesso ao DNS (registro.br) |
| Detalhes do chamado (protocolo com a legenda, tipo, status Aberto/Aguardando cliente/Resolvido, origem, e-mail de resposta; cliente com CPF/CNPJ; chamados desta cliente; pedido citado com estoque) | ❌ | O q-admin do site tem situação e nota; o estoque dos sites está em `sites_estoque`; o espelho do Bling tem nome e CPF/CNPJ do destinatário (`bling_orders.documento_destinatario`), mas não tem e-mail nem telefone | Painel ④ do site; sincronizar o status; lista "chamados desta cliente" na mesma marca; achar o pedido citado pelo nº ou pelo CPF | M | Confirmar se os pedidos dos sites entram no Bling |
| Mudar o tipo à mão: fica registrado, o protocolo não muda e as próximas respostas saem do e-mail do novo tipo; conversa agrupada com tipos diferentes responde pelo e-mail do chamado principal, com escolha | ❌ | — | Tipo editável no PATCH + histórico; escolha do remetente na caixa de resposta | P | Decisão sobre atacado@ e duvidas@ (§2.4) |
| Agrupar e desagrupar na mesma marca | ❌ | — | Tabela `agrupamento`, aviso, ação, desfazer, auditoria | M | — |
| Chips de tipo e busca por protocolo | 🟡 | A busca já procura em `externo_id` | Chips + busca nos protocolos agrupados | P | POST do site |

### 3.11 RF7 Mídia

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Contas por marca e rede | ✅ | `redes_sociais` (20 linhas, os @ batem com o spec) | Não criar `conta_rede` | — | — |
| ✈ / conexão oficial existente | ✏️🟡 | 7 tokens de publicação; ✈ = `postagem_auto` + token | Regerar os tokens com escopos de leitura | P | App Review da Meta |
| Conexão por OAuth | ✏️🟡 | Meta = token de System User colado; YouTube = OAuth; Direct = 3º token que **vence em 16/11/2026** | Renovação automática do token do Direct; tela "Conectar" | M | App id/secret da Meta no servidor; prazo 16/11 |
| Todo comentário vira conversa (uma por pessoa por publicação, incluindo anúncios, Q14) | ❌ | O webhook só trata `object=instagram` e `messaging` (`webhooks.py:836-845`); cerca de 1 comentário em 69 publicações | Campos de webhook comments/feed, permissões, tabelas, leitura inicial, 2 segredos de assinatura | G | App Review; volume quase nulo |
| Toda menção vira conversa | ❌ | `story_mention` gravado como anexo genérico | Tratar menção + baixar a mídia (some em 24 h); campo `mentions` | M | Permissões |
| Direct, Messenger e outras mensagens privadas | 🟡 | Direct no /atendimento só leitura (9 conversas); robô de DM com `dm_auto` | Direct da Locagil; Messenger; conferir a assinatura do webhook; decidir sobre o `dm_auto` | M | Decisão do dono |
| Pendente até responder; ocultar e resolver | 🟡 | Direct fica "aguardando" até a resposta | Ocultar comentário + resolver auditado | P | Comentários |
| Perguntas no topo | ❌ | — | `eh_pergunta` + lista configurável | P | Comentários |
| Resposta da marca não abre conversa | 🟡 | Eco do Direct = autor loja | Mesma regra nos comentários | P | Comentários |
| Lista: etiqueta MÍDIA, origem, 24 h, filtros | 🟡 | Logo IG, janela de 24 h com contagem; uma linha "Direct" sem separar por marca; só para quem vê todas as equipes | Etiqueta, texto de origem, grupo por marca, filtros, escopo restrito | M | Etiqueta (RF1) |
| Conversa ③ da mídia: cabeçalho (@ da pessoa, rede + marca), cartão "comentário público" com Ver publicação, Ocultar e Resolver, aviso de interação anterior, abas Comentário · Direct, modelos rápidos | ❌ | Só o Direct aparece, como conversa comum só leitura | Cartão, abas e ações; modelos curtos (agradecer elogio) em `atendimento_modelos` | M | Comentários; envio |
| Responder em público ou no Direct pelo /atendimento | 🟡 | Direct recusa com `somente_leitura` (`routers/atendimento.py:1492`); a fila de envio do robô já existe (`instagram_dm.py`, `responder_dm`) | Ligar a caixa do Direct à fila existente (P/M); resposta pública e privada a comentário | M | Envio desligado; permissões |
| Detalhes da publicação (foto ou vídeo com player, legenda, métricas, comentários com o da conversa em destaque, produto com preço e estoque por cor, miniatura própria, imagem do story) | 🟡 | `marketing_postagens` + métricas + criativo → produto (só o que o DaVinci publicou) | Posts externos, lista de comentários, painel ④, preço e estoque do produto, guardar a miniatura e a imagem do story | M | Comentários |
| Instagram (API) | 🟡 | Webhook de mensagens, publicação, métricas | Comentários, menções, ocultar, resposta privada | G | App Review |
| Facebook (Página) | ❌ | Só publicação de Reels (Charlots, Uranyx) | Permissões de página, webhook `object=page`, Messenger, conectar 7buyers e Locagil | G | App Review |
| YouTube | 🟡 | 2 canais conectados com `youtube.readonly`, escopo que já permite ler `commentThreads`. Nenhum código lê comentários hoje (`marketing/metricas.py` só lê estatísticas) | Job de leitura; `force-ssl` para responder; conectar 7buyers e Locagil | M | Reautorização por canal |
| TikTok (conteúdo) | ✏️❌ | App recusado 2×; postagem pelo AdsPower | Só por robô no AdsPower | G | Risco de bloqueio da conta |
| X | ❌ | Nada | Levantar o custo, depois OAuth + leitura e envio | G | Custo da API |

### 3.12 RF8 Avaliações

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Avaliação presa ao cliente, aba ★ | 🟡 | `atendimento_avaliacoes_loja` (Shopee, 3.741, de hora em hora); ML ao vivo no cartão Cliente | Virar item ou conversa + aba; gravar a do ML | M | — |
| Estrelas, texto, fotos, data, produto, respondida | 🟡 | Estrelas, texto (500 caracteres), pedido, `resposta_loja`; a mídia não é salva | Colunas de mídia, `pode_responder`, prazo, `respondida_por`, `conversa_id` | P | — |
| Vínculo pelo pedido; fila "sem vínculo" | 🟡 | Shopee 100% com pedido; ML por pedido | Fila só se entrarem fontes sem pedido | P | — |
| Responder pela tela (resposta pública, com aviso na caixa; onde não pode, o motivo) | ❌ | Sem chamada de resposta; 92,5% já respondidas por fora | Shopee `reply_comment`; ML [confirmar]; aviso de resposta pública; descobrir quem responde hoje | M | Envio desligado; permissão por loja |
| Etiqueta AVALIAÇÃO na lista | ❌ | Só o selo "avaliou mal" | Etiqueta + destaque | P | RF1-Et |
| Painel: nota, fotos, prazo, histórico | 🟡 | Cartão Cliente com a mais recente e a pior | Fotos, prazo, "respondida" | P | — |
| ML (estrelas do produto) | ✏️🟡 | O que liga ao pedido é a avaliação da venda (positivo, neutro, negativo) | Corrigir o spec; gravar no índice | M | Docs ML |
| Shopee | 🟡 | Leitura nas 14 lojas | Resposta e conversa | M | Permissão de produto |
| TikTok e Amazon | ❌ | Nada | [confirmar] APIs; Amazon só por relatório | G | Docs e permissões |
| Sites (e Magalu, Temu, Ali) | ✏️ | Charlots e Uranyx só têm depoimentos; 7buyers por app Shopify | Tirar do RF8 os sites próprios; decidir sobre Magalu, Temu e Ali | — | Decisão do dono |

### 3.13 RF9 Carrinho abandonado

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Origem: API da plataforma de loja virtual (marketplaces não mostram o carrinho) | ✏️❌ | Charlots e Uranyx em PHP próprio (`adm_carrinho`, lojista logado); 7buyers Shopify; Locagil fora do ar | O site manda os carrinhos ao DaVinci (como `/api/sites/estoque`); 7buyers pela Admin API | M | Deploy nos sites; token Shopify |
| Quando abre (1 h); uma conversa por carrinho; entra na conversa aberta do mesmo cliente na loja | ❌ | Todo carrinho persistido é de lojista identificado | Tabela + regra de tempo (sugestão para atacado: 24 h); juntar à conversa aberta do cliente | M | Decisão do dono |
| Detalhes (itens com estoque, frete, cupom, etapa, link, cliente, compras e carrinhos anteriores) | ✏️❌ | Estoque viável (o DaVinci alimenta o site); frete, cupom e etapa não existem | Itens, estoque, preço de atacado e cliente para os PHP | M | — |
| Item sem estoque → sugerir parecido | ❌ | `avisoEstoque` no site | Reaproveitar o RF4 | P | RF4 |
| Ações: lembrete por Zap ou e-mail, copiar o link, marcar como resolvido | ❌ | — | Lembrete pelo modelo aprovado (Zap) ou pelo e-mail da marca; resolver com registro | M | RF10, RF6 |
| Recuperado ou não recuperado; taxa | ✏️❌ | O site apaga o carrinho sem histórico ao confirmar o envio | O site registrar o evento; definir "recuperado" | M | Mudança no site; decisão |
| Opt-in (LGPD) | 🟡 | `privacidade_em`, newsletter | Opt-in de contato por WhatsApp | P | Jurídico ou dono |

### 3.14 RF10 Zap

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Cadastro dos números | 🟡 | `marcas.sac_fone` (4 de 9), com `fone` próprio por conta em `redes_sociais` (0 de 20); `marcas.whatsapp_verificacao_status` já existe (9 de 9 "não solicitado"); `store_info.phone` (não diz se é WhatsApp) | Tabela e tela `zap_numero`; importar a lista do Duoke | M | Lista real do Duoke |
| Conversas, aba 🟢 Zap, mídia | ❌ | Nada; o webhook da Meta recusa outros objetos; o validador **bloqueia** "WhatsApp/zap" (`validador.py:187`) | Webhook `whatsapp_business_account`, canal "zap", mídia, exceção no validador | G | WABA + permissões Meta |
| Zap sem venda → PRÉ-VENDA | ❌ | — | Filtro + regra de etiqueta | P | Canal Zap, RF1-Et |
| Transferir para uma venda (busca por nº, nome, CPF ou telefone; desfazer; histórico) | ❌ | O espelho do Bling tem nome e CPF/CNPJ do destinatário, mas **não guarda telefone**. Extrator de nº de pedido citado no texto só existe para o formato Amazon (`amazon_email.py:154`) | `zap_vinculo` com desfazer; a sugestão por telefone acerta pouco nos marketplaces | M | Canal Zap |
| Janela de 24 h, modelos aprovados, Cloud API | ❌ | Lógica de 24 h do Direct reaproveitável | Envio, templates, assinatura com o segredo do app | M | Verificação da empresa; custo por template |
| Migração número a número do Duoke | ❌ | — | Plano por número (PIN, WABA) | M | Duoke liberar |

### 3.15 RF11 Botão do AdsPower

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Qual perfil (campo Servidor) | 🟡 | `store_info.server` numérico em 80 de 80 lojas de marketplace, todos no espelho `adspower` (142 perfis); `integration_id` só em 13, e por plataforma + nome casam 48 de 50 integrações; Temu e Ali em `robo_perfil_id` | Função conversa → perfil, exposta no detalhe e no `/resumo`; ligar à mão as 2 Shopee | P | Cadastro |
| Abrir pela API local | ❌ | Só os robôs usam (`atendimento_robo_adspower.py:95`) | Botão com fetch a `http://127.0.0.1:50325` (literal, não `local.adspower.net`, por causa do conteúdo misto); consultar `browser/active` antes; trava de cerca de 3 s | M | [confirmar] CORS e `serial_number`; AdsPower em cada máquina (`users.adspower` em 11 de 22) |
| Botão também na coluna ① | ❌ | — | Hover na loja | P | Os dois itens acima |
| Abrir já na página do pedido | ❌ | Os robôs fazem por CDP | Ajudante local (padrão `apps/executor`) | G | Instalar em cada computador |
| Mensagens de erro | ❌ | O executor-leitura distingue "inacessível" e retry por limite | 3 mensagens + **"perfil em uso por robô"** (Temu/Ali no Mac mini, ML/Shopee no Mac Santiago) | P | [confirmar] perfil aberto em dois computadores |
| Rede local, CORS, chave, limite de 1 req/s | 🟡 | Limite medido; chave só no computador | Testar no Chrome do atendente; plano B com ajudante local | M | Versão do AdsPower e do Chrome |
| Registro e permissão | ❌ | O Histórico grava todo POST | `POST /api/atendimento/adspower/aberto` | P | — |

### 3.16 Seção 4: ponto de partida

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Contas das lojas + e-mail da loja | ✏️ | `store_info.email` existe (ver §2.4); a coluna ① vem das integrações + JSON do robô, não do store-info | Tirar o "[confirmar]"; normalizar os e-mails; cadastrar os sites | P | Cadastro |
| Pedidos do Bling | ✅ | Espelho + Logística + chamados + devoluções + NF, já no painel (`contexto.py`) | Margem no painel; gatilho do RF3 | — | — |
| Produtos e custos | ✅ | `pricing_products`, `products`, `segments`, `product_categories` | — | — | — |
| Estoque (onde fica o saldo) | ✏️ | `products.stock` (webhook), `reserved_stock`, `saldo_fisico`, `saldo_virtual_total` | Tirar o "[confirmar]"; mostrar no painel | P | — |
| Mensagens (DM como base) | ✏️ | 10 tabelas `atendimento_*` | Atualizar o spec | — | — |
| Perfis do AdsPower | ✅ | Espelho `adspower`, `adspower_agent.py:135-200` | — | — | — |
| Executor local para o Tuta | ✏️❌ | Fila "tuta" existe, mas os 15 comandos falharam (§2.8) | Corrigir o executor ou escolher outro caminho | — | RF5.0 |
| Postagem automática (✈) | ✏️ | Ver §2.5 | Atualizar o spec | — | — |
| Números de Zap | ✏️ | `marcas.sac_fone` | Atualizar o spec | — | — |
| Redes sociais | ✅ | `redes_sociais` | — | — | — |
| **Lacuna 297840** (reclamação ML 5582543195 invisível) | ❌ | Em produção: Bling em 83953, Logística com `ship_status delivered` sem `claim_*`, 0 chamados, 0 devoluções, 0 conversas. Causas: (1) o atendimento lê só perguntas e packs, e pack sem mensagem não vira conversa (`atendimento/ml.py:1074`); (2) a varredura do ML não vê claims (`logistica_meli.py:833`; só 55 de 2.237 linhas ML de 45 dias têm `claim_status`); (3) `devolucao_rastreio_sync` só olha 83957; (4) o painel e o `consultar_pedido` olham só chamados internos (`claude_tarefas.py:753/796`). Não há webhook do ML. | Job por conta ML que lista reclamações abertas e recém-fechadas; tabela de reclamações (`claim_id`, conta, pack, pedido Bling, tipo, stage, status, motivo, prazo, return); gravar `claim_*` em `meli_status`; ligar ou abrir conversa "reclamação" com as mensagens; mostrar no painel e no `consultar_pedido` | M–G | Confirmar o endpoint de busca de claims e a cota; URL de notificação se usar o tópico claims |
| Devoluções e disputas Shopee/TikTok no atendimento | 🟡 | A Logística já varre a loja inteira (`logistica_shopee.sweep_pos_venda`, `logistica_tiktok.sweep_pos_venda`); prazo em `devolucao_rastreio` | `contexto` e `consultar_pedido` lerem `meli_status.return_*`/`claim_*` e `devolucao_rastreio` | P | — |

### 3.17 Seção 6: integrações

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| ML perguntas | ✅ | 21 ok, 1 erro (token), 1 sem escopo (Poofy 403); mediana 1,6 min | Reconectar as 2 contas; ligar o envio | P | Login (Eduardo); decisão do envio |
| ML pós-venda | ✅ | 21 ok; mediana 5 min; nunca marca como lido | Mesmas contas | P | Idem |
| ML reclamações e devoluções | 🟡 | Só `claim_ids` no atendimento; clientes de claim já existem | Canal de reclamação (lacuna §4) | M | — |
| ML notificações | ❌ | Só webhooks `/bling` e `/meta/instagram`; consulta a cada 2 min | Endpoint que só agenda a leitura | M | URL e tópicos no app do ML |
| Shopee chat | ✏️✅ | Funciona sem liberação: 13 ok + 1 erro; mediana 1,1 min | Conectar as lojas que faltam (22 ativas no store-info × 14 integradas); ver a loja com erro | P | Login das lojas |
| Shopee devolução e disputa | 🟡 | `chamados_devolucao.py`, Logística | Ligar ao atendimento | M | — |
| TikTok atendimento | ✅ | 6 de 8; mediana 1,5 min | Reautorizar Poofy e Inova no app novo | P | Login da loja |
| TikTok devolução e reembolso | 🟡 | `chamados_devolucao.py`, vigia de reembolso | Ligar ao atendimento | M | — |
| Amazon | ✏️✅ | Gmail IMAP + SMTP (4 ok) | Atualizar o spec; A-to-Z [confirmar] se chega ao Gmail | M | — |
| Temu, Magalu, AliExpress | ✏️🟡 | Magalu API; Temu e Ali pelo robô (1 Temu com sessão caída). Conversas gravadas até agora: Magalu 2, AliExpress 2, **Temu 0** | Reentrar na sessão Temu; conferir por que o Temu não gravou nenhuma conversa; envio pelo robô fica para depois | — | Login manual |
| Tuta | ❌ | Ver RF5 | RF5 | G | Decisão |
| Sites | ❌ | Ver RF6 | RF6 | G | Site + DNS |
| Instagram e Facebook | 🟡 | Direct só leitura | RF7; token do Direct antes de 16/11 | G | App Review |
| YouTube, TikTok, X | ❌ | 2 tokens YouTube | RF7 | G | TikTok restrito; X pago |
| Zap | ❌ | — | RF10 | G | WABA |
| Carrinhos | ❌ | — | RF9 | G | Site |
| AdsPower local | 🟡 | Só robôs | RF11 | M | — |
| Avaliações | 🟡 | Índice Shopee; ML ao vivo | RF8 | M | — |
| Bling | ✅ | GET, PUT completo e PATCH de situação (`bling.py:340, 501-567`) | Usar no RF4 | — | Pedido de teste |
| Contas a cobrir / lista oficial | 🟡 | 43 lojas Duoke ativas; 55 contas no atendimento; JSON paralelo do robô | Gerar a lista do robô a partir do store-info; conectar as faltantes (ativas: Shopee 22 × 14, ML 30 × 23, Amazon 8 × 4, TikTok 10 × 8, Magalu 3 × 1, Shein 2 × 0, a conferir duplicadas) | P | Logins |

### 3.18 Seção 8: requisitos não funcionais

| Item | Status | O que já existe (onde) | O que falta | Esforço | Depende de |
|---|---|---|---|---|---|
| Tempo real (~1 min) | 🟡 | Consulta a cada 2 min; medianas de 1,1 a 5 min; **p90 de 95–106 min** no ML pós-venda e no TikTok (teto de conversas por rodada); robô e Instagram quase em tempo real | Webhooks ML e Shopee como gatilho; investigar o p90 (`ATENDIMENTO_SYNC_MAX_CONVERSAS`) | M | URLs de notificação |
| Escala (o spec fala em ~33 contas) | ✏️✅ | 55 contas e 80 canais sem falha de rodada | Worker próprio se o ingest atrasar (divide 10 vagas) | P | — |
| Entregabilidade | ❌ | Mailjet para e-mails de marca | Ver RF6.5 | P | DNS |
| Usuários e permissões | 🟡 | `SO_ADMIN=True` + `ATENDIMENTO_USUARIOS`; `atendimento:view/edit/delete` e `team_scope` já nas rotas | Abrir à equipe (`routers/atendimento.py:170-176`); permissão separada para a troca | P | Decisão do dono |
| Auditoria | 🟡 | O Histórico grava todo POST/PUT/PATCH/DELETE com ator; antes e depois em canais, regras, modelos e categorias; `autor_user_id` nas mensagens; 0 eventos até agora | Registrar troca, vínculo, etiqueta e agrupamento quando existirem; importação do manual com ator | P | — |
| Monitoramento | 🟡 | Na tela; alerta do Telegram de prazo **desligado** (`ATENDIMENTO_ALERTA_TELEGRAM=false`). Já existe o **`vigia_credenciais`** (prova de vida de hora em hora por conta ML, Shopee, TikTok, Amazon e Magalu, com ocorrência na Ouvidoria), mas ele está **desligado em produção** desde 22/09. Agora: 1 Temu caída, 1 Shopee com erro, 2 ML com erro ou sem escopo, sem aviso a ninguém. | Ligar o `vigia_credenciais`; vigia do atendimento para o que ele não cobre (canal sem ok há mais de 30 min, robô Temu/Ali parado, sessão caída, escopo de atendimento como `seller.customer_service`), no padrão `vigia_robo_leitura.py` | P | Decisão de ligar (Ouvidoria) |
| Convivência com o Duoke | ✅ | Observação; nunca marca como lido; resposta automática e campanhas do Duoke não contam | Plano de desligar loja por loja; reproduzir as automações do Duoke | — | Decisão do dono |

### 3.19 Seção 10: critérios de aceite

| Critério | Status | Onde está / o que falta | Esforço |
|---|---|---|---|
| **Tela:** todas as contas na coluna ①, grupo Site, conta sem conexão sinalizada | 🟡 | Integradas e sinalizadas; faltam as sem integração e o grupo Site | P |
| **Tela:** reclamação, devolução, pré-venda e Ag. cancelamento destacadas, com filtro e contagem | 🟡 | Só o filtro Pré-venda; etiqueta + destaques | G |
| **Tela:** reclamação mostra o prazo e sobe quando perto de vencer | ❌ | RF1 ②d + ②l | M |
| **Tela:** abas Pré/Pós/Reclamação/Mediador/E-mail na reclamação | ❌ | RF2 | G |
| **Tela:** saldo em estoque do SKU no painel | ❌ | RF4.1 | P–M |
| **Tela:** falha de envio com o motivo | ✅ | Feito; validar no piloto e acrescentar "tentar de novo" | P |
| **Bling:** Ag. cancelamento gera uma conversa com o motivo em até 5 min | ✏️❌ | Reescrever: "um cartão por pedido (na conversa existente ou interna), com o motivo do DaVinci ou da observação, em até 5 min, e nada para hold de margem" | M |
| **Bling:** "falta de estoque" mostra Sugerir troca; candidatos por categoria, estoque e ≤ +5% | ❌ | RF4.3 a RF4.10 | M |
| **Bling:** a troca altera o item, mantém o valor, grava a observação e volta para Em aberto | 🟡 | Peças prontas em outros fluxos; falta o serviço manual + re-enfileirar a NF + teste | M |
| **Bling:** falha não deixa o pedido pela metade | 🟡 | Máquina de estados + "retomar" | M |
| **E-mail:** 16tr@tuta.com em "vendas ml" aparece na JLAS2 | ❌ | JLAS2 = `16tr` sem domínio e sem integração; RF5.0–5.4 | G |
| **E-mail:** pastas sem plataforma não entram | ❌ | RF5.1 | P |
| **E-mail:** "vendas" não aumenta Pendentes | 🟡 | Mecanismo existe; falta a regra | P |
| **E-mail:** sem vínculo + vincular à mão | 🟡 | Só o equivalente Amazon (conta) | M |
| **E-mail:** não entra duas vezes; alerta de remetente | 🟡 | Amazon dedupe; falta o selo | P |
| **E-mail:** responder do endereço que recebeu, no fio, com aviso | 🟡 | Só Amazon, remetente único | G |
| **Sites:** chamado com o protocolo que veio | 🟡 | Uranyx e Charlots geram (não publicado); 7DS e LS não existem | M |
| **Sites:** alertas de sem protocolo, formato errado ou repetido | ❌ | RF6.3 | P |
| **Sites:** resposta do e-mail do tipo, fora do spam, e a resposta volta | ✏️❌ | Não há caixas por tipo; DKIM do Mailjet só no hadken.com | M |
| **Sites:** tipos na lista, filtro e busca por protocolo | ❌ | RF6.8 | P |
| **Sites:** agrupar só na mesma marca | ❌ | RF6.7 | M |
| **Mídia:** todo comentário vira conversa MÍDIA pendente | ❌ | RF7 comentários | G |
| **Mídia:** toda menção e todo Direct | 🟡 | Direct IG com 24 h em 3 marcas; faltam menções, Locagil e Messenger | M |
| **Mídia:** todas as redes conectadas | ✏️🟡 | 7 de 19 contas com token; reescrever: TikTok só por robô, X depende do custo | M |
| **Mídia:** detalhes com foto, vídeo, métricas e comentários | ❌ | Dados só das publicações do DaVinci | M |
| **Mídia:** resposta pública, Direct no prazo, ocultar e resolver | ❌ | Direct só leitura no /atendimento | M |
| **Avaliações:** aparece na aba ★ com estrelas, texto e fotos | 🟡 | Só no cartão Cliente, sem fotos | M |
| **Avaliações:** responder pela tela; onde não pode, explicar | ❌ | Sem chamada de resposta | M |
| **Etiqueta:** reclamação muda sozinha para RECLAMAÇÃO e volta a PÓS-VENDA | ❌ | RF1-Et + lacuna §4 | G |
| **Etiqueta:** E-mail e Zap nunca como etiqueta | ❌ | Por construção no motor | P |
| **Carrinho:** carrinho de teste vira conversa com itens, estoque, total, etapa e link | ❌ | Trocar "etapa e link" por campos que existem no atacado | M |
| **Carrinho:** compra finalizada → PÓS-VENDA e "recuperado" | ✏️❌ | Redefinir: evento "enviou pelo WhatsApp" ou pedido no Bling | P |
| **Zap:** números cadastrados com status de conexão | ❌ | RF10 | M |
| **Zap:** mensagem na aba 🟢; fora de 24 h só modelo | ❌ | RF10 | G |
| **Zap:** transferir para a venda e desfazer | ❌ | RF10 | M |
| **AdsPower:** abre o perfil certo no computador de quem clicou | ❌ | RF11.1–11.2 | M |
| **AdsPower:** mensagens certas e abertura registrada | ❌ | RF11.5 + 11.7 | P |
| **Geral:** toda ação no histórico | 🟡 | Middleware do Histórico; faltam as ações novas | P |

### 3.20 Seção 11: decisões e perguntas

| Item | Status | Situação |
|---|---|---|
| D1 Tuta: caminho a critério do programador | ❌ | Ainda não foi escolhido nem registrado. O leitor de tela do executor nunca rodou (§2.4). |
| D2 E-mails da pasta "vendas" só no histórico | 🟡 | O mecanismo existe (`sem_resposta_necessaria`, autor sistema); faltam o leitor e a regra por prefixo. |
| D3 Protocolo por prefixo e ano, gerado pelo site | 🟡 | Uranyx e Charlots geram (`Protocolo.php`, pacote ainda não publicado); 7buyers e Locagil, não. |
| D4 Agrupar só na mesma marca | ❌ | O agrupamento não existe. |
| D5 Resposta por sac@, atacado@ e duvidas@ de cada marca | ✏️❌ | As caixas atacado@ e duvidas@ não existem, e o SAC da Charlots é `sac@poofy.com.br` (§2.4). |
| D6 Tipo pelo prefixo, conferido com a caixa | ✏️❌ | Com uma caixa só, o tipo sai só do protocolo. |
| D7 X e TikTok da Charlots = @poofy_brasil | ✅ | O cadastro `redes_sociais` já está assim. |
| D8 ✈ = postagem automática | ✅ | `redes_sociais.postagem_auto` (4 contas) + token. Vale para mais marcas do que o spec diz (§2.5). |
| D9 Conectar todas as redes | ✏️🟡 | 7 de 19 contas com token, só de publicação; TikTok de conteúdo só por robô; X depende do custo. |
| D10 Todo comentário vira conversa | ❌ | Nenhum comentário é lido (§3.11). |
| D11 Toda menção vira conversa | ❌ | A menção em story chega como anexo do Direct, sem tratamento próprio. |
| D12 E-mail com resposta | 🟡 | Só a Amazon (SMTP para o relay); Tuta e sites, não. |
| D13 Avaliações presas ao cliente, aba ★, com resposta | 🟡 | Shopee indexada (3.741) e ML ao vivo no cartão Cliente; sem aba e sem resposta. |
| D14 Etiqueta = status atual | ❌ | §3.3. |
| D15 Carrinho abandonado vira conversa CARRINHO | ✏️❌ | Nada feito; o carrinho de atacado dos sites não tem etapa nem link (§2.5). |
| D16 Zap: cadastro, transferir para venda, aba depois do E-mail, não é etiqueta | ❌ | §3.14. |
| D17 Botão do AdsPower no cabeçalho | ❌ | §3.15. |
| Q1 abas ou painéis | ❌ | Seguir a sugestão (abas com contagem) ao fazer o RF2 |
| Q2 Ag. cancelamento automática ou revisada | ✅ | Na prática, revisada: o envio está desligado, e cancelamento e troca são "só pessoa" mesmo no modo auto (`CATEGORIAS_SO_HUMANO`, `constantes.py:272`) |
| Q3 aceite obrigatório | ❌ | Seguir a sugestão (sim) → `mensagem_aceite_id` |
| Q4 observações ou internas | 🟡 | Automações = `observacoes`; falta confirmar o uso manual com a expedição |
| Q5 categoria semelhante | ❌ | Recomendação: categoria Bling + linha ou segmento da Tabela de Preços |
| Q6 custo de referência | 🟡 | Recomendação: `products.bling_cost_price` do SKU exato |
| Q7 limite para baixo | ❌ | Recomendação: sem limite, abaixo de −20% esmaecido |
| Q8 quem executa a troca | 🟡 | Hoje só admin entra na tela; falta a permissão própria |
| Q9 ordem das plataformas | ✅ | Ultrapassada na leitura; vale para ligar o envio |
| Q10 Temu, Magalu, Ali "só e-mail" | ✏️ | Magalu por API; Temu e Ali pelo robô |
| Q11 letra da Locagil | ❌ | Seguir a sugestão (`L`) quando a Locagil tiver site (hoje fora do ar) |
| Q12 protocolo ao mudar o tipo | ❌ | Seguir a sugestão (não muda; registra a mudança) ao fazer o RF6 (linha "Mudar o tipo à mão" no §3.10) |
| Q13 em qual conta do Tuta chegam os SAC dos sites | 🟡 | Uranyx no Tuta; Charlots em `sac@poofy.com.br` (Hostinger → Tuta); levantar as outras |
| Q14 comentários em anúncios | ❌ | Seguir a sugestão no RF7 |
| Q15 avaliações pendentes | ✏️ | Os dados pedem "pendente = sem resposta da loja" (§2.5) |
| Q16 ordem das abas | ❌ | Seguir a sugestão (E-mail · Zap · Avaliação) |
| Q17 tempo do carrinho | ✏️ | 1 h é curto para atacado (sugestão: 24 h) |
| Q18 plataforma dos sites | 🟡 | Charlots e Uranyx PHP próprio; 7buyers Shopify; Locagil sem resposta |
| Q19 migração do Zap | ❌ | Seguir a sugestão (número a número, começando pelo de menor movimento) quando houver o RF10; depende de o Duoke liberar cada número |
| Q20 Zap sem venda | ❌ | Seguir a sugestão (PRÉ-VENDA até transferir) quando houver o motor de etiqueta e o RF10 |
| Q21 AdsPower já no pedido | ❌ | Seguir a sugestão (fase 2); exige ajudante local |

---

## 4. Backlog priorizado (ondas)

A ordem segue o que destrava mais com menos dependência externa. Os pedidos a terceiros da Onda 0 devem sair **já**, porque demoram.

### Onda 0: destravar (pouco ou nenhum código, começa já)

| # | Item | Esforço | Quem ou o quê |
|---|---|---|---|
| 0.1 | Decisões do dono da seção 5 (bolinha, ordenação, origens do 83955, piloto e envio, `dm_auto`, Tuta…) | — | Eduardo |
| 0.2 | Reconectar contas: ML com token que não renova, ML Poofy 403, Shopee com erro, sessão Temu caída; reautorizar TikTok Poofy e Inova no app novo | P | Login das lojas (Eduardo) |
| 0.3 | Conectar as lojas do store-info que ainda não têm integração (Shopee, ML, Amazon, Magalu, TikTok, Shein) | P por loja | OAuth pelo dono |
| 0.4 | **Token do Direct antes de 16/11/2026:** renovação automática + app id/secret da Meta no servidor | P | Eduardo (segredo) |
| 0.5 | Pedir já o **App Review da Meta** (comentários, menções, Messenger, WhatsApp) e reautorizar o YouTube com `force-ssl` | — | Meta e Google (demora) |
| 0.6 | DNS: autenticar os 4 domínios no Mailjet (DKIM + SPF) | P | registro.br (Eduardo) |
| 0.7 | Publicar o pacote SAC de Uranyx e Charlots na Hostinger | P | Eduardo |
| 0.8 | Separar um **pedido de teste no Bling** para o RF3 e o RF4 | — | Expedição |
| 0.9 | Pedir ao autor do spec as imagens de `esboco/` e o `mockup.html` | — | Autor do spec |
| 0.10 | Atualizar `docs/atendimento-unificado.md` (status "local, sem commit" está desatualizado) | P | — |
| 0.11 | **Ligar o `vigia_credenciais`** (já existe, desligado desde 22/09) e criar o **vigia do atendimento** na Ouvidoria/Telegram para o resto: canal sem ok há mais de 30 min, robô parado, sessão caída, sem escopo | P | Eduardo (ligar na Ouvidoria) |
| 0.12 | Conferir por que o **Temu** não gravou nenhuma conversa (3 canais com leitura ok) | P | — |

### Onda 1: reclamações e etiqueta (o maior buraco, sem dependência externa)

| # | Item | Esforço | Depende de |
|---|---|---|---|
| 1.1 | **Reclamações do ML:** job por conta (busca de claims abertas e recém-fechadas), tabela de reclamações, `claim_*` em `meli_status`, painel e `consultar_pedido`. Resolve o 297840. | M | Confirmar endpoint e cota |
| 1.2 | Ligar as devoluções e disputas Shopee e TikTok (Logística, `devolucao_rastreio`) ao painel e ao `consultar_pedido` | P | — |
| 1.3 | **Motor de etiqueta:** migration, histórico, recalcular no sync + cron, prioridade, troca manual | G | — |
| 1.4 | Etiqueta na lista: destaques, chips com contagem no `/resumo`, bolinha por etiqueta | M | 1.3; decisão da bolinha |
| 1.5 | Prazo real da plataforma (claim, return, TikTok) + contagem regressiva + ordenação por prazo em "Falta responder" | M | 1.1; decisão da ordenação |
| 1.6 | Canal "reclamação" no atendimento com as mensagens do claim e o autor "mediador" (base das abas do RF2) | M | 1.1 |

### Onda 2: painel e caixa (itens rápidos)

| # | Item | Esforço |
|---|---|---|
| 2.1 | Saldo de estoque por item (lote comprado + lotes irmãos + kit explodido, com hora) | P–M |
| 2.2 | Margem no painel (`_MARGEM_SQL`) | P |
| 2.3 | Observações do Bling (GET com cache, só na tela) + observação interna | P |
| 2.4 | Botões "Abrir no Bling" e "Abrir na plataforma"; nº do Bling no cabeçalho | P |
| 2.5 | Busca por SKU e por nº do Bling | P |
| 2.6 | Traduzir `blocked_by_*`; botão "tentar de novo" | P |
| 2.7 | Nota interna (fora da pendência e da IA) | M |
| 2.8 | Retrato TikTok e Magalu (status, produto, valores) | M |
| 2.9 | **Botão AdsPower** (perfil por conversa, fetch local, erros incluindo "perfil em uso por robô", registro) | M |

### Onda 3: Ag. cancelamento e troca (RF3 → RF4)

| # | Item | Esforço | Depende de |
|---|---|---|---|
| 3.1 | **RF3:** constante única, gancho em `upsert_order` + robôs, safety net com 83955, `atendimento_ag_cancelamento`, cartão na conversa existente ou interna, classificador por origem, rascunhos sem IA, motivo para a IA, encerramento, filtro. Cerca de 3 a 4 dias. | M | Decisões de origem e conversa interna; textos |
| 3.2 | **RF4:** candidatos (categoria/linha, custo, estoque ao vivo, tipo, voltagem, lote separado), modal com foto e margem, aceite vinculado, dry-run, serviço de troca com máquina de estados, re-enfileirar a NF, decisão de margem auditada, `troca_produto`, permissão. Cerca de 1,5 a 2 semanas. | G | Perguntas 5 a 8; pedido de teste; aval fiscal |

### Onda 4: sair do modo observação (substituir o Duoke)

| # | Item | Esforço | Depende de |
|---|---|---|---|
| 4.1 | Piloto: lojas em humano ou copiloto, `ATENDIMENTO_ENVIO_ATIVO=true` nelas, validar falha e "tentar de novo" | M | Decisão do dono |
| 4.2 | Abrir à equipe: `SO_ADMIN=False`, recurso em `useCan.ts`, menu e middleware; permissão separada para a troca | P | Decisão do dono |
| 4.3 | Reproduzir as automações do Duoke (confirmação de pedido, convite para seguir) | M | Textos |
| 4.4 | **RF2:** conversas relacionadas em abas com contagem; cartão da reclamação com ações confirmadas | G | Onda 1 |
| 4.5 | Webhooks ML e Shopee como gatilho de leitura; corrigir o p90 do ML pós-venda e do TikTok | M | URLs nos apps |
| 4.6 | Anexo no envio por plataforma | M | APIs de upload |
| 4.7 | Desligar o Duoke loja por loja | — | Aceite |

### Onda 5: avaliações e Direct (o volume real de "mídia")

| # | Item | Esforço | Depende de |
|---|---|---|---|
| 5.1 | **RF8 Shopee:** avaliação como item ou aba ★, fotos, "pendente = sem resposta", `reply_comment`; gravar a avaliação da venda do ML | M | Saber quem responde hoje; envio |
| 5.2 | Responder o **Direct** pelo /atendimento reaproveitando a fila do robô de DM | P–M | Decisão do `dm_auto`; envio |

### Onda 6: sites e e-mail

| # | Item | Esforço | Depende de |
|---|---|---|---|
| 6.1 | **RF6:** endpoint `POST` do site (Bearer por site), grupo Site, cartão, protocolo com alertas, chips e busca | M | Pacote publicado; alteração no site |
| 6.2 | RF6: resposta por Mailjet com `[protocolo]` + inbound (subdomínio de resposta → webhook); agrupar e desagrupar | M + M | DNS (0.6) |
| 6.3 | Normalizar o e-mail das lojas (domínio completo) e ligar o store-info à integração | P–M | Dono confirmar os domínios |
| 6.4 | **RF5:** escolher o leitor do Tuta; generalizar o módulo de caixa da Amazon; pastas e regras; vínculo e fila "sem vínculo"; aba E-mail; envio pelo Tuta | G | Decisão do Tuta; abas do RF2 |

### Onda 7: dependentes de terceiros (por último)

| # | Item | Esforço | Depende de |
|---|---|---|---|
| 7.1 | **RF10 Zap:** WABA, webhook, canal, modelos, cadastro, transferir para venda, migração número a número | G | Meta (verificação e templates), Duoke liberar os números, custo |
| 7.2 | **RF9 Carrinho:** o site envia os carrinhos e o evento "enviou pelo WhatsApp"; 7buyers pela Admin API do Shopify | M | Desenvolvimento no site; token Shopify; decisões |
| 7.3 | **RF7:** comentários e menções IG/FB, Messenger, YouTube, painel da publicação | G | App Review; volume hoje quase nulo |
| 7.4 | TikTok de conteúdo por robô; X | G | Risco de conta; custo do X |
| 7.5 | AdsPower abrindo já no pedido (ajudante local por CDP) | G | Instalar em cada máquina |

---

## 5. Perguntas que continuam em aberto (para o dono)

1. **Bolinha da loja:** continua sempre vermelha (decisão de 01/10) ou fica vermelha só com reclamação ou Ag. cancelamento?
2. **Ordenação:** prazo mais curto primeiro só na aba "Falta responder", mantendo "Todas" pela mais recente?
3. **Ag. cancelamento: quais origens avisam o comprador?** Sugestão: falta de estoque, restrição e manual, sim; Margem "Pendente", nunca. E a Margem "Reprovado", que vira cancelamento?
4. Pedido em 83955 **sem conversa**: vira conversa interna (sugestão) ou só tarefa?
5. **Prefixo padrão** da observação quando a expedição move à mão para 83955. Confirmar que ela usa "observações", não "observações internas".
6. **Troca:** a categoria semelhante é categoria Bling + linha da Tabela de Preços? O custo é `products.bling_cost_price` do SKU exato? Há piso de custo? Troca de **lote** do mesmo produto precisa de aceite (hoje o robô troca sem)? Pode trocar se a margem ficar abaixo do mínimo?
7. **Fiscal:** NF com produto diferente do anunciado. Alinhar com o contador antes do RF4.
8. **Piloto:** quais lojas, quando ligar o envio, quem da equipe entra e quem vê a margem? O modo **auto** fica proibido até o fim do piloto?
9. **Robô de DM do Instagram** (`dm_auto`, envia sem clique humano): continua?
10. **Tuta:** qual caminho (navegador invisível no servidor, AdsPower no Mac mini ou caixa com IMAP)? Quantas contas Tuta existem? E-mails de "mensagens X" de plataforma com API geram pendência ou ficam só no histórico?
11. **E-mails das lojas:** qual o domínio de cada endereço guardado só com a parte antes do @ (64 lojas) ou com `@marca` sem o resto do domínio (11 lojas): `tuta.com`, `tutamail.com`, `uranyx.com.br`…?
12. **Sites:** 7buyers (Shopify, sem protocolo) e Locagil (sem site no ar) entram no RF6? As caixas atacado@ e duvidas@ vão existir, ou fica um SAC por marca?
13. **Avaliações:** pendente = sem resposta da loja, com destaque para 1 a 3 estrelas? Qual a data de corte na importação inicial? Quem responde hoje as da Shopee (resposta automática ou Duoke)?
14. **Carrinho no atacado:** o que conta como abandonado (prazo de 24 h?) e o que conta como recuperado? Vai haver opt-in de contato por WhatsApp?
15. **Redes:** aceitar o custo da API do X? TikTok de conteúdo por robô no AdsPower (risco de bloqueio)?
16. **Zap:** qual número migra primeiro, e quando? Quem faz a verificação da empresa na Meta?
17. **AdsPower:** o atendente pode abrir um perfil que o robô está usando (Temu e Ali no Mac mini, ML e Shopee no Mac Santiago), ou o botão bloqueia?

---

## 6. Conferência de completude (01/10, à tarde)

Conferido contra o spec inteiro: todos os bullets de RF1 a RF11, os 38 critérios de aceite da seção 10, as 17 decisões e as 21 perguntas da seção 11 e as regras da seção 3. Os casos duvidosos foram conferidos no código e no banco de produção (só `SELECT`).

**O que faltava e entrou:**
- Regras da seção 3 do spec (§3.0).
- As 17 decisões da seção 11, uma por linha (estavam juntas numa linha só), e as perguntas Q11, Q12, Q19 e Q20 separadas.
- RF4: produtos acima de +5% esmaecidos e não selecionáveis.
- RF5: Delivered-To, desempate pela pasta e o 2º passo do vínculo (nome + produto + data).
- RF6: mudar o tipo à mão (linha nova) e os campos que faltavam nos detalhes do chamado (legenda, status, e-mail de resposta, CPF/CNPJ, chamados desta cliente); Locagil sem atacado.
- RF7: a conversa ③ da mídia (linha nova); uma conversa por pessoa por publicação; produto com preço e estoque, miniatura e imagem do story nos detalhes.
- RF8: aviso de resposta pública.
- RF9: marketplaces sem carrinho, carrinho na conversa aberta do cliente, copiar link e resolver, histórico do cliente.
- RF10: busca por nome, CPF e telefone na transferência.

**Status ou fatos corrigidos:**
- "Abrir na plataforma": de ❌ para 🟡. Já existe na conversa para Amazon, Magalu e Temu/Ali; faltam ML, Shopee, TikTok e "Abrir no Bling".
- Pré-venda no ML (RF2): o cartão Cliente já separa as perguntas feitas antes da compra; o que falta é a aba.
- Monitoramento: o `vigia_credenciais` já existe, mas está desligado em produção desde 22/09. Ligá-lo virou o item 0.11.
- Temu: 4 canais lidos, mas 0 conversas gravadas. Entrou como item 0.12.
- E-mail das lojas: são 16 com @, mas só 5 completos; 11 têm `@marca` sem o resto do domínio, e nenhum é `@tuta.com`.
- YouTube: o escopo `youtube.readonly` permite ler comentários, mas nenhum código lê (antes dizia "já lê").
- Extrator de nº de pedido: `enriquecer.py:259` é o filtro de dado pessoal. Só existe o extrator do formato Amazon (`amazon_email.py:154`).
- Caminho do JSON do robô: `apps/api/scripts/atendimento_robo_lojas.json`.
- "Generalizar o e-mail da Amazon": de "✅ / M" para 🟡.
- Linhas ✏️ que escondiam "nada feito" agora mostram também o status da implementação (✏️❌, ✏️🟡 ou ✏️✅). Valeu para RF3, RF4, RF6, RF7, RF8, RF9, integrações, critérios de aceite e decisões.
- Pedidos em 83955: eram 12 de manhã e 14 na reconferência da tarde.
