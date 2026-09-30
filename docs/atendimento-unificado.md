# Atendimento unificado no DaVinci (o "Duoke nosso") + IA que aprende

Plano de 25/09/2026, parte 2 em 28/09/2026. **Local, sem commit, sem deploy** —
nada vai para produção antes de o Eduardo aprovar.

Atalhos: [status](#status-da-implementação-local) ·
[ligar em produção — modo observação](#como-ligar-em-produção--modo-observação) ·
[importar o histórico](#importar-o-histórico-uma-vez) ·
[manual base](#manual-base-da-ia) · [pendências](#pendências-e-de-quem) ·
[testar localmente](#como-testar-localmente).

Base: levantamento do código do DaVinci, pesquisa das APIs de cada loja e uma
**sondagem em produção, só leitura** feita em 25/09 com os tokens que o DaVinci
já tem (nada foi enviado, nada foi marcado como lido, nenhum token foi renovado).

---

## 1. Quais conexões dão certo

| Loja | No Duoke hoje | No DaVinci hoje | Ler mensagens | Responder | Veredito |
|---|---|---|---|---|---|
| **Shopee** | 21 lojas | 14 lojas | ✅ funciona em 12 de 12 testadas (2 puladas só por token na hora de renovar) — 30 conversas não lidas no momento | ✅ o DaVinci **já envia** por essa API (senha de devolução) | **Dá certo.** Falta conectar ~7 lojas |
| **Mercado Livre** | 25 contas | 23 contas ativas | ✅ pós-venda funciona em 21 de 23 (119 conversas não lidas no momento); perguntas pré-venda funcionam (0 sem resposta no momento) | ✅ mesma API (resposta de pergunta até 2.000 caracteres; pós-venda até 350) | **Dá certo.** Falta 1–2 contas e a Poofy |
| **TikTok Shop** | 8 lojas | 8 lojas | ❌ 8 de 8 dão `401 · 105005 Access denied` — o app não tem a permissão `seller.customer_service` | ❌ idem | **Dá, mas depende de aprovação da TikTok** |
| **Amazon** | 0 (o Duoke não tem Amazon) | 4 contas | ⚠️ não existe API de leitura em nenhum integrador — só **por e-mail** | ✅ por e-mail, respondendo da caixa autorizada | **Dá certo pelo e-mail.** Falta criar a caixa e trocar 2 campos no Seller Central |
| **Instagram** | — | 3 contas | ✅ já existe (webhook da Meta) | ✅ já existe | **Já funciona.** Token vence 16/11/2026 |

### Por que o Duoke tem TikTok e nós não
O Duoke é um app grande, parceiro da TikTok, que já recebeu a permissão
`seller.customer_service`. O app do DaVinci é da categoria "TikTok Shop Seller"
(uso da própria empresa) e **a permissão não vem por padrão**: é pedida no Partner
Center e aprovada **na mão** pela TikTok. A regra deles pede 1.000 vendedores ou
1 milhão de chamadas/dia — a exceção é justamente app de vendedor próprio, com
vídeo mostrando a caixa de atendimento funcionando. **Sem prazo e sem garantia.**
Enquanto não sai, a TikTok continua respondida no Duoke (ou no Seller Center).

### Amazon "de alguma forma" — o caminho
A Amazon **não tem API para ler mensagem de comprador** (nem para o Duoke, nem para
o eDesk, nem para ninguém). Todo integrador de Amazon faz assim:

1. A Amazon manda cada mensagem de comprador para o **e-mail cadastrado** no
   Seller Central, vinda de um endereço de retransmissão `@marketplace.amazon.com.br`.
2. O sistema **lê essa caixa** (IMAP ou Gmail API).
3. A resposta é um **e-mail de volta para o endereço de retransmissão**, saindo
   de um e-mail **autorizado** no Seller Central. A Amazon repassa ao comprador e a
   resposta aparece normalmente no "Mensagens" do Seller Central.

O que falta é só a caixa (o Tuta não tem IMAP — por isso o robô de devoluções lê
o Tuta pela tela, e isso **não** serve para atendimento). Passos, por conta
(kfa, kia, nexus, poofy):

- **Criar uma caixa com IMAP** (Google Workspace/Gmail, Zoho, Migadu…), por exemplo
  `atendimento-amazon@<domínio>`. Uma caixa só para as 4 contas basta: o DaVinci
  separa a conta pelo destinatário/assunto. *Criar conta é com você — eu não crio
  conta nem digito senha.*
- **Seller Central → Configurações → Preferências de notificação → Mensagens →
  Mensagens do comprador → Editar** → colocar esse e-mail.
- **Seller Central → Mensagens → E-mails autorizados** (Approved senders) → incluir
  o mesmo e-mail.
- Guardar a caixa no `.env` do servidor (você, não eu — a senha é senha de app).
  Os nomes são EXATAMENTE estes (o DaVinci ignora em silêncio variável com outro
  nome, e o canal Amazon fica `desligado` sem erro nenhum):

  | Variável | O que é |
  |---|---|
  | `ATENDIMENTO_AMAZON_IMAP_HOST` | servidor IMAP (ex.: `imap.gmail.com`). Vazio = Amazon desligada |
  | `ATENDIMENTO_AMAZON_IMAP_PORT` | padrão `993` |
  | `ATENDIMENTO_AMAZON_IMAP_USUARIO` | o e-mail da caixa |
  | `ATENDIMENTO_AMAZON_IMAP_SENHA` | a senha de app |
  | `ATENDIMENTO_AMAZON_IMAP_PASTA` | padrão `INBOX` |
  | `ATENDIMENTO_AMAZON_SMTP_HOST` | servidor SMTP (ex.: `smtp.gmail.com`) |
  | `ATENDIMENTO_AMAZON_SMTP_PORT` | padrão `587` |
  | `ATENDIMENTO_AMAZON_SMTP_USUARIO` / `ATENDIMENTO_AMAZON_SMTP_SENHA` | vazios = os mesmos do IMAP (o caso comum: uma caixa só) |
  | `ATENDIMENTO_AMAZON_REMETENTE` | o e-mail AUTORIZADO em Seller Central → Mensagens → E-mails autorizados. Obrigatório para responder: resposta de outro endereço a Amazon descarta sem avisar |

Regras da Amazon que o validador aplica: sem link externo, sem e-mail, sem
telefone, sem emoji, sem pedir avaliação, sempre com o ID do pedido
(17 dígitos), resposta em até 24 h.

### O tempo de resposta da Amazon (80 h hoje)
A Amazon mede quantas mensagens de comprador foram respondidas em até **24 h
corridas** — sábado, domingo e feriado contam — e quer **90% ou mais**. Abaixo
disso a conta ganha alerta de desempenho. Os 80 h de hoje vêm de a Amazon não
estar no Duoke: a mensagem cai num e-mail que ninguém olha no dia a dia.

O que o plano muda:
- a mensagem entra no DaVinci em 1–2 min e fica na mesma fila de Shopee/ML, com o
  relógio de 24 h e o filtro "vencendo";
- aviso no Telegram quando faltar pouco para vencer sem resposta;
- a IA deixa a resposta pronta; a pessoa confere e envia;
- a resposta pelo e-mail autorizado **conta como resposta** (passa pelo sistema
  de mensagens da Amazon e aparece no Seller Central).

O que ele **não** resolve sozinho:
- **fim de semana**: alguém precisa olhar a fila uma vez por dia, ou, depois de
  provado, liga-se o envio automático só para "cadê meu pedido" com o rastreio do
  sistema;
- **mensagem que não pede resposta** ("obrigado", aviso automático) precisa do
  botão "Não é necessária resposta" no Seller Central, senão conta como atrasada.
  Por e-mail não existe esse botão: na tela há o botão "Não precisa de resposta"
  (tira da fila e das métricas, sem fingir que alguém respondeu) — quem clica é a
  pessoa; o DaVinci ainda NÃO detecta essas mensagens sozinho (pendência abaixo);
- o número **cai aos poucos**, porque a métrica olha uma janela de dias para trás.

Por isso a Amazon subiu na ordem: a leitura e a resposta por e-mail entram na
primeira leva de código, prontas para ligar assim que a caixa existir.

**Dica para a caixa**: use um endereço com "+" por conta (`atendimento+kfa@…`,
`atendimento+kia@…` etc., o Gmail aceita). O DaVinci descobre a conta pelo "+"
sem depender do texto do e-mail.

Extra opcional: a **API de Mensagens** da SP-API (mensagens prontas como "problema
inesperado", "confirmar entrega", nota fiscal) hoje dá **403** em kfa e kia — o app
não tem o papel "Buyer Communication". Não é necessária para o atendimento; só
vale se quisermos mensagem proativa pelo DaVinci.

### Resultado da sondagem (25/09/2026, produção, só leitura)

**Shopee** — `get_unread_conversation_count`

| Loja | Resultado |
|---|---|
| aguiar | OK · 0 não lidas |
| ATV | OK · 4 |
| Barbosa | pulada (token no minuto de renovar; o cron renova) |
| Inova | OK · 4 |
| Jlas | OK · 5 |
| kfa | OK · 2 |
| Kia | OK · 2 |
| mega | OK · 4 |
| Minas | OK · 1 |
| mini | OK · 1 |
| Poofy | OK · 1 |
| Victor Mei | pulada (token no minuto de renovar) |
| Vita | OK · 1 |
| vortan | OK · 5 |

**Mercado Livre** — `/my/received_questions/search?status=UNANSWERED` e `/messages/unread?role=seller&tag=post_sale`

| Conta | Perguntas sem resposta | Pós-venda não lidas |
|---|---|---|
| aguiar | 0 | 12 |
| aguiar2 | 0 | 0 |
| barbosa | 0 | 3 |
| counhago | 0 | 4 |
| dream2 | 0 | 0 |
| eron | 0 | 0 |
| forpaper | 0 | 7 |
| injox | 0 | 8 |
| inova | 0 | 9 |
| jlas | 0 | 0 |
| jlas2 | 0 | 21 |
| kfa | 0 | 12 |
| kfa2 | 0 | 5 |
| kia | 0 | 2 |
| lucas mei | pulada (token no minuto de renovar) | — |
| marquezini | 0 | 23 |
| mega | 0 | 0 |
| mini | 0 | 3 |
| **Poofy** | **403 `PA_UNAUTHORIZED_RESULT_FROM_POLICIES`** | **403** |
| velasco | 0 | 1 |
| victor mei | 0 | 3 |
| vita | 0 | 1 |
| Zorvex | 0 | 5 |

**TikTok** — `/customer_service/202309/conversations`: atv, Barbosa, eron, injox,
inova, jlas, Mini, Poofy → todas `401 · 105005 · app sem escopo`.

**Amazon** — `/messaging/v1/orders/{id}`: kfa e kia `403 Unauthorized` (papel
Buyer Communication ausente); nexus e poofy sem pedido nos últimos 6 dias.

### O que falta, e como conseguir

| # | Falta | Quem faz | Como |
|---|---|---|---|
| 1 | **~7 lojas Shopee** que estão no Duoke e não no DaVinci. Pelo cadastro de lojas: **atlas, eron, fiore, lucas mei**; as outras 3 só dá para ver na lista do Duoke | Você (login da loja) | DaVinci → Integrações → Nova → Shopee → autorizar com o login de cada loja. Mesmo fluxo das 14 atuais |
| 2 | **ML nexus** (e mais 1 que o Duoke mostra) | Você | DaVinci → Integrações → Nova → Mercado Livre → autorizar |
| 3 | **ML Poofy** bloqueada por política | Você | Ver no ML se a conta tem restrição; se não, reautorizar a integração. Se continuar 403, é bloqueio da conta no ML |
| 4 | **TikTok `seller.customer_service`** | Quem tem login do Partner Center | Partner Center → App → Gerenciar escopos/API → pedir "Customer Service". Anexar prints e vídeo de 30–90 s da tela `/atendimento` com Shopee/ML funcionando (por isso vem **depois** da tela pronta). Aprovado: reautorizar as 8 lojas |
| 5 | **Caixa de e-mail da Amazon** com IMAP | Você | Seção "Amazon" acima |
| 6 | **Aviso automático (webhook) do ML e da Shopee** — opcional, só para ficar instantâneo | Você, com o login de dev de cada app | ML: "URL de notificações" + tópicos `questions` e `messages` em cada app. Shopee: Push Mechanism no console. Sem isso funciona por consulta a cada 1–2 min |
| 7 | **Token do DM do Instagram** vence 16/11/2026 | Código (E0) | Renovação automática no worker |

**Boa notícia da sondagem:** no ML, a permissão de mensagens já está ligada em
21 das 23 contas. Não é preciso mexer app por app como eu tinha estimado.

---

## 2. Como funciona (resumo da arquitetura)

- **Entrada por consulta periódica** (1–2 min por loja): é a fonte confiável. O aviso
  automático de cada loja entra depois, só para acelerar.
- **Enquanto o Duoke estiver ligado, o DaVinci nunca marca nada como lido**
  (Shopee: nunca `read_conversation`; ML: sempre `mark_as_read=false`).
- **Uma tela só — `/atendimento`** (menu Pós-venda): conversas de todas as lojas à
  esquerda, a conversa no meio com o rascunho da IA pronto na caixa de resposta,
  e o pedido à direita (Bling, rastreio, NF, chamado aberto).
- **Trava de "quem responde" por loja**: `observar` (só lê), `humano` (equipe
  responde pelo DaVinci), `copiloto` (IA sugere, pessoa envia) e, só depois de
  provado, `auto` por categoria. Começa tudo em `observar`.
- **O agente é o cérebro do DaVinci** (decisão de 25/09): uma chamada de modelo sem
  ferramentas. O código escolhe o pedido e preenche rastreio/prazo/NF; o modelo só
  escreve o texto; um validador em código confere as regras de cada loja; o que
  reprova vai para humano. Hermes/OpenClaw fora do caminho do comprador.
- **Aprendizado com trava**: tudo que a IA sugere fica guardado junto com o que a
  pessoa fez (enviou igual, editou, descartou com motivo). Respostas aprovadas por
  pessoa viram exemplos; o manual "QUANDO → FAÇA" é versionado e só muda com
  aprovação humana. A IA nunca escreve as próprias regras.

## 3. Etapas

| Etapa | O que entrega | Depende de |
|---|---|---|
| **E0** Arrumar a casa | Filtro `instagram` nos robôs de DM (senão o robô do Instagram pega conversa de Shopee), renovação do token do Instagram, trava de renovação de token Shopee/ML | — |
| **E1** Shopee + ML + Amazon lendo | Espelho das conversas das 14 Shopee e 23 ML (pós-venda + perguntas), sem marcar lido, ligadas ao pedido; Amazon pela caixa de e-mail assim que ela existir; tela `/atendimento` com saúde de cada loja e relógio de prazo | Amazon: caixa criada (item 5) |
| **E2** Responder pela tela | Enviar, macros, trava por loja, contador de caracteres e janela de envio | Escolher 1–2 lojas piloto |
| **E2b** Pedido à TikTok | Vídeo + prints da tela funcionando | Login do Partner Center |
| **E3** IA copiloto | Rascunho em cada mensagem, validador por loja, registro do que a pessoa fez | Teto de gasto do modelo |
| **E4** Aprendizado | Exemplos aprovados, manual versionado, revisão semanal, liberação `auto` por categoria | Aprovar categorias |
| **E5** TikTok | Cliente `customer_service/202309` | Escopo aprovado (item 4) |

**Desligar o Duoke:** loja por loja, quando a loja estiver em `humano` ou além.

## 4. O que a IA nunca envia sozinha

Preço, desconto, frete, prazo ou data que não veio do sistema; estorno, reembolso,
cancelamento, troca, defeito, garantia (e nunca negar os 7 dias do art. 49 do CDC);
pedir/repetir CPF, telefone, e-mail, endereço; qualquer contato para fora da loja
(WhatsApp, link, @, PIX); pedir avaliação ou desestimular reclamação; Procon,
Reclame Aqui, advogado, xingamento; conversa com reclamação/mediação/chamado
aberto; mensagem só com foto/áudio; pedido não encontrado; comprador pedindo
atendente ou texto com cara de instrução para a IA; conversa em que humano já
respondeu (pelo DaVinci ou por fora).

Limites por loja no validador: ML 350 caracteres e só ISO-8859-1; TikTok 2.000 sem
link; Shopee sem contato externo; Amazon sem link/e-mail/telefone/emoji e com ID do
pedido.

## 5. Decisões

Já tomadas (25/09): agente = cérebro do DaVinci; OpenClaw/Tuta fica como está;
implementar tudo **local, sem commit**, para revisão na segunda.

Ainda suas:
1. Quais 1–2 lojas Shopee pilotam a resposta pelo DaVinci (o Duoke só observa nelas).
2. Quem conecta as lojas que faltam (itens 1–3) e quem pede o escopo da TikTok (item 4).
3. Qual caixa de e-mail recebe as mensagens da Amazon (item 5).
4. Quais categorias podem, um dia, sair sem revisão. Proposta: "cadê meu pedido"
   com rastreio do sistema, "NF foi emitida?" e agradecimento.
5. Teto de gasto do modelo (estimativa: ~US$ 75–190/mês para 200–500 mensagens/dia).

## Status da implementação local

Tudo abaixo está NESTE Mac, sem commit e sem deploy: parte 1 em 25/09, parte 2
(correções e o que faltou) em 28/09. Nada liga sozinho — cada interruptor é um
setting desligado por padrão.

O que MUDA no deploy mesmo com todos os interruptores desligados (fora do
`/atendimento`):

- `worker._refresh_tokens_for` (crons `shopee_token_refresh`,
  `ml_token_refresh`, `tiktok_token_refresh`): passa pela trava de renovação
  por loja (Redis) e relê as credenciais do banco antes de renovar; sem Redis,
  renova como antes. Bling fica como estava.
- Histórico: o gatilho deixa de gravar `atendimento_mensagens`,
  `_rascunhos`, `_avaliacoes` e `_conversas`, e o corpo dos pedidos em
  `/api/atendimento/conversas|rascunhos|mensagens/...` não é guardado
  (`historico/nomes.py`, `SEM_CORPO`).
- As migrations 0346 e 0347 (só tabelas novas e o canal do robô; nada muda
  nas existentes) e o item "Atendimento" no menu, SÓ PARA ADMIN por enquanto
  (Eduardo, 30/09/2026): menu (`adminOnly`), página (middleware `admin`) e
  API (`SO_ADMIN` em `routers/atendimento.py`, 403 `admin_only` mesmo para
  quem tiver o recurso `atendimento`, que saiu da tela de Permissões). O
  `/api/atendimento/robo/*` segue no token do robô. Para abrir para a equipe,
  ver o comentário do `SO_ADMIN`.
  A parte 2 entrou na MESMA 0346 (ela ainda não foi para produção):
  `atendimento_categorias`,
  `atendimento_pedidos_comprador`, `atendimento_avaliacoes_loja` e as colunas
  `categoria`/`tipo`/`prioridade` em `atendimento_regras`.
- Crons novos registrados no worker padrão, que não fazem nada com a leitura
  desligada: a leitura (minutos ímpares), as sugestões da IA, o alerta de prazo
  e, na parte 2, `atendimento_indexar_pedidos` (a cada 1 h).

### Parte 1 — pronto (com teste)

- **Leitura** por consulta (minutos ímpares) de Shopee, ML (pergunta — uma
  conversa por pergunta — e pós-venda), TikTok (quando sair o escopo) e Amazon
  por e-mail; nunca marca como lido. Instagram aparece só leitura.
- **ML pós-venda com o Agente de Mensageria**: desde 02/02/2026 o ML migra as
  conversas para um agente (ID 3037675074 no Brasil) e, nelas, a resposta tem
  de ir para o agente. O DaVinci decide por conversa (`dados.via_agente`).
  Medido em 25/09 (16 conversas lidas de 5 contas): nenhuma passava pelo agente
  ainda — todas vão direto ao comprador, como hoje o Duoke faz.
- **Tela** `/atendimento` com a cara do Duoke: barra de lojas com a bolinha de
  não lidas, fila com filtros (inclusive "A conferir"), conversa com os cartões
  de pedido e produto, pedido ao lado (retrato do marketplace + Bling, rastreio,
  NF, chamados, devoluções; na pergunta do ML, as outras perguntas do mesmo
  comprador no anúncio), lojas e modo, manual da IA, respostas prontas e
  métricas. No modo observação, "O que a IA responderia" com 👍/👎 no lugar da
  caixa de envio.
- **Envio** pelo caminho único, com as travas: modo da loja, validador (contato
  fora, avaliação, reclamação, limites do ML/Amazon), uma resposta em voo por
  conversa, resposta repetida em 2 min, "alguém respondeu enquanto você
  escrevia", envio ambíguo em `revisar` visível em "A conferir" até a pessoa
  conferir, simulador que recusa em produção.
- **IA** (copiloto e automático por categoria), com o validador e as travas em
  código; exemplos aprovados por pessoa; teto diário de chamadas; só gera nos
  canais dos modos configurados.
  O automático nunca responde sozinho: conversa do ML com reclamação/mediação
  aberta (`claim_ids`) e a Amazon inteira (a resposta dada no Seller Central
  não chega à caixa, então "aguardando" pode ser mentira — na Amazon, só a
  resposta dada PELO DaVinci, ou o botão "não precisa de resposta", tira a
  conversa da fila).
- **Rodada do sync em transação curta**: os adaptadores commitam a cada
  conversa (pack/pergunta no ML), e o canal só é gravado no fim — a rodada não
  segura a conversa nem a loja enquanto fala com a API. Se ainda assim a tela
  encontrar a linha ocupada, espera no máximo 5 s e diz "tente de novo".
- **Trava de renovação de token** compartilhada entre o atendimento e os crons
  `shopee_token_refresh`/`ml_token_refresh`/`tiktok_token_refresh`: quem não
  pega a trava pula a loja no ciclo; quem pega relê as credenciais do banco.
- **Alerta de prazo** no Telegram (vencendo < 2 h e vencidas recentes).
- **Histórico**: registra modo da loja, manual e respostas prontas; NÃO copia
  texto de comprador (mensagens, conversas, sugestões e avaliações ficam fora).

### Parte 2 — o que mudou em 28/09

| # | O quê | Por quê |
|---|---|---|
| P1 | **Shopee lida no sentido certo.** A leitura caminha do topo com `direction=older` (mais novas → mais antigas) até o cursor da rodada anterior, 25 por página, com o teto e a retomada de antes. O teste reproduz a API como ela é e falha se alguém voltar a usar `latest` no topo | Medido em produção em 28/09 (só leitura, loja kfa): `latest` sem cursor devolve as conversas MAIS ANTIGAS (2023) — a caixa leria o passado e nunca a conversa de hoje. `page_size` 60 devolveu lista vazia em algumas lojas |
| P2 | **Nome da loja, não da integração.** Barra de lojas, lista, cabeçalho, `/resumo` e alertas mostram o nome do cadastro (`stores.apelido_override`, senão `companies.apelido`) sem o prefixo da plataforma: a integração "mega" aparece como **Marquezini**. Sem loja ligada, fica o nome da integração (`services/atendimento/lojas.py`) | É o nome que a equipe conhece e o que o Duoke mostra; com o nome de sistema, a pessoa acha que está numa loja e está em outra |
| P3 | **A IA aprende no modo observação.** 👍 numa sugestão que não saiu vira exemplo aprovado; 👎 com correção vai para o prompt no bloco "Correções da equipe (não repita estes erros)" (as 15 últimas da plataforma, a mesma categoria primeiro — nunca como texto de resposta); a resposta real que a equipe deu POR FORA vira exemplo "como a equipe responde", abaixo dos aprovados. Ficam fora: mensagem automática do Duoke (texto idêntico em 5 ou mais conversas da loja — "Confirmação de pedido", "Convite para seguir"), saudação curta (menos de 25 caracteres) e o que o validador reprovaria. Dado pessoal mascarado antes de ir ao modelo | No teste em produção nada sai pelo DaVinci: sem isso a IA não aprenderia nada no período em que mais precisa |
| P4 | **Painel Pedido: "Hora de envio" e "Tempo concluído"**, como no Duoke (Shopee: coleta e conclusão do pedido; ML: `date_shipped`/`date_delivered`). O botão "atualizar" some quando a leitura está desligada (antes mostrava erro 409) | O que a equipe olha primeiro para responder "cadê meu pedido" |
| P5 | **Cartão "Cliente"** no topo do painel da direita: cliente desde, compras e total gasto, última compra, devoluções, cancelamentos, avaliações ★, perguntas antes de comprar e a linha do tempo (recolhível). Selos no cabeçalho da conversa ("avaliou mal", "reclamação aberta", "já pediu devolução", "recorrente", "primeira compra"). A IA recebe os sinais (só com a leitura ligada: o cartão do ML consulta a loja ao vivo), e "avaliou mal" ou "reclamação aberta" mandam a conversa para pessoa. Shopee pelos índices próprios (pedidos por comprador e avaliações da loja, alimentados pelo enriquecimento e pelo job de 1 h); ML ao vivo (`/orders/search?buyer`, cache de 2 h, só com a leitura ligada — desligada, o cartão fica com o que o banco sabe; avaliação dos 5 pedidos mais recentes); TikTok/Amazon, só o pedido da conversa | A Shopee não filtra pedido nem avaliação por comprador — sem índice próprio não dá para saber que é a terceira compra de quem está escrevendo |
| P6 | **Importação do histórico**, uma vez, à mão (ver abaixo) | A IA aprende com as respostas reais da equipe e o cartão Cliente nasce com passado |
| P7 | **Manual da IA sem regra batendo com regra.** Cada regra tem tipo — `seguranca` (vale sempre e vem primeiro no prompt), `categoria` (só quando a mensagem é daquela categoria) e `estilo` (tom e assinatura, por último) — e prioridade. Duas regras ativas de `categoria` com a mesma categoria, plataforma e canal são conflito: a API recusa a segunda (409 `regra_conflitante`) e a tela mostra em vermelho as que já existirem. As categorias moram no banco (`atendimento_categorias`, com descrição, exemplos, "só pessoa" e lacunas) e a IA classifica pela descrição; tabela vazia = as categorias fixas do código. Importador do manual base (ver abaixo) | Regra geral e regra específica dizendo coisas diferentes para a mesma mensagem = a IA escolhe uma no chute |

### Pendências (e de quem)

| Pendência | Quem | Observação |
|---|---|---|
| Aprovar a parte 2 e o deploy | Eduardo | Nada foi commitado nem subiu |
| Escopo `seller.customer_service` da **TikTok** | Quem tem login do Partner Center | Seção 1, item 4. Enquanto não sai, as 8 lojas TikTok ficam "sem permissão" na caixa e seguem no Duoke |
| **Amazon na caixa**: a caixa de e-mail com IMAP e os dois campos no Seller Central | Eduardo | Seção 1, "Amazon". Sem a caixa o canal Amazon fica `desligado` — e a Amazon não está no Duoke: os 80 h continuam até a caixa existir |
| **Automações de confirmação de pedido** (a mensagem automática "Recebemos seu pedido…", o cartão "Confirmação de pedido", "Convite para seguir" e as outras do Duoke) | Código + Eduardo (textos) | O DaVinci não manda mensagem sozinho: enquanto o Duoke estiver ligado, elas seguem saindo de lá. Antes de desligar o Duoke numa loja, é preciso reproduzir no DaVinci as que valem a pena. Na leitura, elas já são reconhecidas (P3) e não ensinam a IA |
| Manual base (taxonomia de categorias + regras + respostas prontas) | Eduardo (conteúdo), Claude (importar) | As 12 categorias do teste local são exemplo. Importação abaixo |
| Conferir em produção, SÓ LEITURA, o que ainda falta medir para o ENVIO: id devolvido pelo POST do ML, status de moderação, e-mail da Amazon Brasil, conversas fixadas da Shopee | Claude, com ok do Eduardo | A ordem e a paginação da lista da Shopee e a unidade dos horários foram medidas em 28/09 (P1). Não impede o modo observação |
| Renovação automática do token do DM do Instagram (vence 16/11/2026) | Código (E0) | O `meta_token_refresh` só avisa; não renova `DmConta` |
| Detectar sozinho "não precisa de resposta" (o "obrigado" da Amazon) | Código (E4) | Hoje é o botão da tela |
| Revisão semanal do manual e versão do manual | Código (E4) | O rascunho guarda o `manual_hash` (qual manual gerou cada sugestão); não há tela de revisão |
| Lojas que faltam conectar (Shopee atlas, eron, fiore, lucas mei…; ML nexus; ML Poofy com 403) | Eduardo | Seção 1, "O que falta", itens 1–3 |

### Como ligar em produção — modo observação

O primeiro passo em produção é **só observar**: o DaVinci lê todas as lojas,
mostra a conversa com o pedido e o cliente, e a IA escreve o que TERIA
respondido — mas **nada sai pelo DaVinci**. Quem responde continua sendo o
Duoke. Nenhuma conversa é marcada como lida (a bolinha do Duoke não muda).

**Antes** (tudo com o ok do Eduardo, pelo processo do `CLAUDE.local.md`):

1. Deploy: commit só dos arquivos do atendimento e subida com os DOIS `-f`
   (api, web e todos os workers). Lembrar que o rebuild apaga os helpers
   `/app/_*.py` dos robôs — avisar para recopiar.
2. Migration: `ssh davinci-prod "docker exec davinci-api-1 uv run alembic upgrade head"`
   (a 0346 cria as tabelas do atendimento, as da parte 2 inclusive; a 0347
   deixa o canal aceitar as lojas do robô Temu/AliExpress, sem integração).
3. Com tudo desligado, conferir que nada mudou: `docker ps` sem `Restarting`,
   o menu "Atendimento" aparece SÓ para admin (para os outros o item some, a
   página manda para /403 e a API responde 403) e a faixa da tela diz
   "leitura desligada".

**As variáveis** — no `.env` do servidor (alteração de `.env` em produção só
com o Eduardo confirmando antes):

```bash
ATENDIMENTO_LEITURA_ATIVA=true
ATENDIMENTO_IA_ATIVA=true
ATENDIMENTO_IA_MODOS=observar,copiloto
ATENDIMENTO_ENVIO_ATIVO=false
ATENDIMENTO_AUTO_ATIVO=false
ATENDIMENTO_ALERTA_TELEGRAM=true
```

(A última linha é opcional. Sem comentário na mesma linha do valor: no
`.env`, `true   # opcional` pode chegar inteiro ao app e não ser lido como
"ligado".)

| Variável | No modo observação |
|---|---|
| `ATENDIMENTO_LEITURA_ATIVA=true` | Lê as caixas a cada 2 min (minutos ímpares) e roda a indexação de pedidos e avaliações a cada 1 h. Toda loja nasce em `observar`. Nunca marca como lido |
| `ATENDIMENTO_IA_ATIVA=true` | Liga as sugestões. Usa `ATENDIMENTO_LLM_BASE_URL`/`_MODEL`/`_API_KEY`; vazias = os `LLM_*` da DM (o Groq; com a Claude, ver "A IA com a Claude" abaixo). No máximo `ATENDIMENTO_IA_TETO_DIARIO` chamadas por dia (padrão 1000; 0 = sem teto) — gasta token |
| `ATENDIMENTO_IA_MODOS=observar,copiloto` | Em quais modos de canal a IA escreve. O padrão do código (`copiloto,auto`) NÃO inclui `observar`: sem esta linha a IA fica calada em todas as lojas, porque todas estão em `observar` |
| `ATENDIMENTO_ENVIO_ATIVO=false` | Nada sai pelo DaVinci — nem pela IA, nem por pessoa na tela. É o que garante que o Duoke é o único que responde |
| `ATENDIMENTO_AUTO_ATIVO=false` | Envio automático desligado (só faria sentido com o envio ligado e a loja em `auto`) |
| `ATENDIMENTO_ALERTA_TELEGRAM=true` | Opcional: aviso no Telegram de conversa perto de vencer o prazo sem resposta. Útil para medir; pode incomodar enquanto a equipe responde pelo Duoke |

Para valer, os containers precisam ser recriados com o `.env` novo: o mesmo
`up -d` do passo 2 do deploy (com os DOIS `-f`), que recria o que mudou.

**A IA com a Claude** (opcional — sem estas duas linhas a IA segue no Groq,
exatamente como hoje). No mesmo `.env` do servidor, com o mesmo ok do Eduardo:

```bash
ATENDIMENTO_LLM_MODEL=claude-opus-5
ATENDIMENTO_LLM_API_KEY=<a chave criada em console.anthropic.com>
```

- **Modelo**: `claude-opus-5` é o mais capaz; `claude-sonnet-5` é o mais
  barato — US$ 2 de entrada e US$ 10 de saída por milhão de tokens, contra
  US$ 5 / US$ 25 do Opus. Trocar é só mudar o nome na primeira linha.
- **Chave**: só a `ATENDIMENTO_LLM_API_KEY`, a da Anthropic (começa com
  `sk-ant-`), colada pura depois do `=` — sem aspas, sem os `< >` do exemplo
  e sem "Bearer". A `LLM_API_KEY` do DM do Instagram é a do Groq e nunca é
  usada para a Claude — e a chave da Claude nunca vai para o Groq. Com o
  modelo da Claude e sem essa chave (ou com ela colada de outro jeito), a IA
  não gera ("sem chave" na tela) e nada é chamado.
- `ATENDIMENTO_LLM_BASE_URL` não precisa: a Claude vai sempre para
  `https://api.anthropic.com`, pela API nativa (SDK `anthropic`) — não pelo
  modo "compatível com OpenAI" da Anthropic, que é só para teste e recusa a
  `temperature` que o Groq usa. Um endereço de outro lugar nessa linha é
  ignorado.
- **O que muda na chamada**: sem `temperature`; o prompt de sistema vai com
  cache. O cache é por começo de texto: na resposta, o que vale para o canal
  (regras e segurança) e o manual do assunto vão primeiro, iguais para todas
  as lojas do mesmo canal; o nome da loja, os exemplos e as correções vão no
  fim, fora do cache. A classificação é igual em todas as lojas e vai inteira
  no cache. A leitura do cache custa um décimo da entrada e a gravação 1,25×:
  compensa quando outra conversa do mesmo canal (e assunto) chega em até
  5 min. Raciocínio com esforço baixo, e por isso `max_tokens` de 2.000 na
  classificação e 4.000 na resposta (o raciocínio conta nele). Recusa por
  política da Anthropic vira sugestão bloqueada com o motivo ("a Claude
  recusou por política…"); no Opus, antes disso, o servidor da Anthropic
  tenta o mesmo pedido noutro modelo (fallback). Se esse modelo reserva
  estava no limite ou sobrecarregado, a recusa não bloqueia: a conversa fica
  sem sugestão e a próxima rodada tenta de novo. Limite por minuto (429),
  falha de rede e fora do ar: igual ao Groq.
- O teto `ATENDIMENTO_IA_TETO_DIARIO` continua valendo; o gasto vai para a
  conta da Anthropic (acompanhar em console.anthropic.com).
- **Conferir**: `docker logs davinci-worker-1 --since 10m 2>&1 | grep atendimento_ia_claude`
  (o cron; o "Sugerir" da tela aparece no `davinci-api-1`) — uma linha por
  chamada com `modelo`, `entrada`, `cache_lido`, `saida`, sem texto de
  cliente. `cache_lido` acima de zero = o cache está pegando.
- **Voltar para o Groq**: apagar as duas linhas e recriar os containers.

**Conferir no primeiro dia**:

- Aba **Lojas e modo**: cada canal `ok` (TikTok "sem permissão" é esperado;
  Amazon `desligado` até existir a caixa de e-mail).
- Log do worker: `docker logs davinci-worker-1 --since 10m 2>&1 | grep atendimento_`
  — um `atendimento_sincronizar_tick` a cada 2 min, sem `_falhou`.
- No Duoke, as conversas continuam com a bolinha de não lida como antes.
- Na caixa, as conversas de hoje no topo (P1), com o nome da loja (P2), o pedido
  com "Hora de envio" (P4) e o cartão Cliente (P5 — nas lojas Shopee ele
  enche aos poucos: a indexação pega as últimas 2 h por rodada; o passado vem
  da importação abaixo).
- As sugestões aparecendo em "O que a IA responderia"; a equipe dá 👍/👎 (com a
  correção no 👎). É isso que ensina a IA nesta fase (P3).

**Desligar** a qualquer momento: `ATENDIMENTO_LEITURA_ATIVA=false` e
`ATENDIMENTO_IA_ATIVA=false` no `.env` e recriar os containers. Os dados ficam.

**Depois de provado** (fora do modo observação, um passo de cada vez):

1. `ATENDIMENTO_ENVIO_ATIVO=true` — permite responder pela tela, SÓ nas lojas que
   a pessoa puser em `humano`/`copiloto` na aba Lojas e modo (as outras seguem
   com o Duoke). Antes: as medições de envio da tabela de pendências.
2. Só depois de provado o copiloto: `ATENDIMENTO_AUTO_ATIVO=true` e
   `ATENDIMENTO_IA_MODOS=observar,copiloto,auto`, e na tela (admin) a loja em
   `auto` com as categorias liberadas.

`ATENDIMENTO_SIMULADOR` é só para o teste local: em produção ele recusa todo envio.
Amazon: as variáveis da seção 1.

### Importar o histórico (uma vez)

Traz para a caixa as conversas dos últimos dias que já foram respondidas pelo
Duoke — é delas que a IA tira os exemplos de "como a equipe responde" (P3), e
é o passado do cartão Cliente. Roda **uma vez, à mão**, depois que a leitura
estiver ligada e saudável (não é cron).

```bash
# 1) só conta, não grava (lê as listas; o resumo sai em JSON, só contagens por loja)
ssh davinci-prod "docker exec davinci-api-1 uv run python -m scripts.atendimento_importar_historico --dias 90 --seco"
# 2) uma loja primeiro, para conferir na tela
ssh davinci-prod "docker exec davinci-api-1 uv run python -m scripts.atendimento_importar_historico --dias 90 --loja marquezini"
# 3) todas
ssh davinci-prod "docker exec davinci-api-1 uv run python -m scripts.atendimento_importar_historico --dias 90"
```

O que ele faz e o que não faz:

- Lê a Shopee (lista `older` + mensagens paginadas), as perguntas do ML já
  respondidas e o pós-venda do ML (packs dos pedidos, `mark_as_read=false`), e
  grava pelo mesmo caminho da leitura: o que a loja escreveu entra como
  "fora do DaVinci" (`externo`), o do comprador como `cliente`.
- **Não** gera sugestão da IA e **não** dispara alerta; conversa antiga entra
  como respondida/fechada conforme o estado. **Nunca marca como lido.**
- `--loja` aceita um pedaço do nome da loja ("marquezini") ou da integração
  ("mega"), ou o id da integração — um pedaço que exista em duas lojas pega as
  duas (ex.: "mega" pega a Shopee Marquezini e o ML MEGA).
- Retomável: guarda o cursor por loja; parar no meio e rodar de novo continua
  de onde parou. Tem teto de chamadas por minuto (`--por-minuto`, padrão 60),
  para não disputar cota com a leitura nem com os robôs.
- No fim de cada loja Shopee, grava a marca de "histórico completo" do índice:
  é a partir dela que o cartão Cliente afirma "primeira compra".
- Recusa rodar com `ATENDIMENTO_LEITURA_ATIVA` desligada, a menos que se passe
  `--forcar` (para não encher a caixa de uma leitura que ninguém acompanha).
- Existe também como função do worker, não agendada — só roda chamada à mão.

### Manual base da IA

O manual base (a taxonomia de assuntos, as regras QUANDO → FAÇA e as respostas
prontas) entra por arquivo, validado antes de gravar:

```bash
cd apps/api
uv run python -m scripts.atendimento_manual exportar manual_atual.json      # cópia do que está no banco
uv run python -m scripts.atendimento_manual importar manual_base.json --seco # mostra o que faria
uv run python -m scripts.atendimento_manual importar manual_base.json
```

Formato:

```json
{
  "categorias": [
    {"id": "rastreio", "nome": "Rastreio", "descricao": "Onde está o pedido…",
     "exemplos": ["Meu pedido já foi enviado?"], "so_humano": false,
     "lacunas": ["rastreio", "transportadora"]}
  ],
  "regras": [
    {"tipo": "categoria", "categoria": "rastreio", "plataforma": null, "canal": null,
     "prioridade": 100, "quando": "O cliente pergunta onde está o pedido",
     "faca": "Informe {rastreio} e {transportadora}; não prometa data."}
  ],
  "respostas_prontas": [
    {"titulo": "Rastreio disponível", "texto": "Olá! Seu pedido já foi enviado…",
     "plataforma": null, "canal": null, "categoria": "rastreio"}
  ]
}
```

- `tipo`: `seguranca` (vale para toda mensagem, primeiro no prompt),
  `categoria` (só na categoria da mensagem) ou `estilo` (tom, assinatura; por
  último). `plataforma`/`canal` `null` = todas.
- `lacunas`: os fatos do sistema que a resposta daquela categoria pode usar
  (`numero_pedido`, `rastreio`, `transportadora`, `previsao_entrega`,
  `data_envio`, `nf_numero`) — o código preenche; a IA nunca escreve o valor.
- `so_humano: true` = a IA sugere, mas quem envia é sempre pessoa.
- Qualquer erro ou conflito (duas regras `categoria` ativas com a mesma
  categoria, plataforma e canal — no arquivo ou com as que já estão no banco)
  recusa a importação inteira e diz quais são. Reimportar o mesmo arquivo não
  duplica nada; nada é apagado nem desativado (tirar regra é na tela).
- Para escrever o manual base, comece pelo `exportar`: com a tabela de
  categorias vazia, ele sai com as categorias fixas do código, no formato certo.
- Com a tabela de categorias preenchida, a IA passa a classificar por ela
  (pela descrição); vazia, usa as categorias fixas do código.
- Quando há regra de assunto valendo para o canal, cada sugestão faz **2
  chamadas** ao modelo: uma curta, só para classificar o assunto, e a
  resposta com as regras daquele assunto. O teto diário
  (`ATENDIMENTO_IA_TETO_DIARIO`) conta as duas. Sem regra de assunto, é uma
  chamada só. A versão do prompt passou para `v2` (fica gravada em cada
  sugestão, junto com o `manual_hash`).
- As categorias liberáveis do envio automático (aba Lojas e modo, só admin)
  seguem a mesma lista: assunto que só existe no manual base pode ser
  liberado, e o "só pessoa" do banco soma ao do código — nunca tira (troca,
  reembolso, defeito continuam só com pessoa).

Em produção o arquivo precisa estar dentro do container (`docker cp` para
`davinci-api-1`) e o comando roda com `docker exec` — com o ok do Eduardo,
como toda alteração de banco fora do deploy.

## Fontes

- Shopee Open Platform — sellerchat (`get_conversation_list`, `get_message`, `get_unread_conversation_count`, `send_message`).
- Mercado Livre Developers — [Perguntas e respostas](https://developers.mercadolivre.com.br/pt_br/gerenciar-perguntas-e-respostas), [Mensagens pós-venda](https://developers.mercadolivre.com.br/pt_br/mensagens-pos-venda).
- TikTok Shop Partner Center — Customer Service API `202309` e escopo `seller.customer_service`.
- Amazon — [O Serviço de comunicação entre cliente e vendedor](https://www.amazon.com.br/gp/help/customer/display.html?nodeId=G3JQ9V9LQ8FFMR7W), [Buyer-Seller Messaging Permissions](https://sellercentral.amazon.com/help/hub/reference/external/G201054220?locale=en-US), [Perguntas frequentes sobre mensagens](https://sellercentral.amazon.com/help/hub/reference/external/G200383320?locale=pt-BR); SP-API Messaging `v1`.

---

## Como testar localmente

Tudo neste Mac, sem produção: um schema próprio no Postgres local
(`davinci_local_atendimento`, no banco `davinci` da porta 5433) e o cenário do
**primeiro teste em produção — só observar**: as lojas como estão no Duoke,
todos os canais em `observar` e o **envio desligado**. O DaVinci lê e mostra o
que a IA responderia; quem responde é o Duoke. Nada sai para comprador e a API
local **barra qualquer chamada** para Shopee/ML/TikTok/Amazon/Bling (e
Telegram) antes de sair do Mac. O schema `davinci` local (o de
desenvolvimento) não é tocado. O script é `apps/api/scripts/atendimento_local.py`.

Antes: Postgres local (5433) e Redis local (6379) ligados, como no dev de
sempre. Se o `nuxt dev` de sempre estiver aberto na porta 3000, feche-o antes.
O navegador precisa de internet: as fotos dos produtos vêm do CDN público do
Mercado Livre (como no Duoke).

### 1. Preparar o banco

Cria o schema do zero (tipos, tabelas e o gatilho do Histórico). Recusa rodar
no schema `davinci` e em banco que não seja o deste Mac. **Rode de novo depois
da parte 2 (28/09)**: há três tabelas novas e colunas novas no manual. Se
esquecer, a `semente` e a `api` recusam o schema defasado e dizem o que falta.
Com a `api` do passo 3 no ar, reinicie-a depois do `preparar` (ela guarda os
tipos do schema apagado).

```bash
cd ~/davinci/apps/api
uv run python -m scripts.atendimento_local preparar --schema davinci_local_atendimento
```

### 2. Gravar os dados de exemplo (no dia do teste)

As lojas do Duoke, com os nomes de verdade: Shopee **Inova, Marquezini, KFA,
KIA, Poofy**; TikTok **Mini, Barbosa, ATV** (sem permissão de leitura — como
em produção hoje); Mercado Livre **DREAM2, MEGA, POOFY, AGUIAR2, VELASCO**
(o pós-venda da VELASCO com erro de leitura); Amazon **KFA**. As integrações
têm o nome de sistema ("mega", "kfa", "dream2"…) e ficam ligadas às lojas do
cadastro pelas duas formas que existem em produção — a caixa mostra o nome da
LOJA (a integração "mega" aparece como **Marquezini**; a VELASCO, sem loja
ligada, mostra o nome da integração). Todos os canais em `observar`. 27 conversas com compradores inventados, cada uma com o número
de não lidas da plataforma (a bolinha vermelha), o pedido e o produto
preenchidos como a API das lojas entrega (**foto, título, SKU e preço de
anúncios reais do catálogo**; status, pagamento, transportadora e rastreio do
pedido), cartões "Confirmação de pedido" e de produto nas mensagens, fotos de
comprador em algumas conversas da Shopee (PNGs genéricos em
`apps/web/public/atendimento-demo/`; ML e Amazon não têm foto: a tela mostra as
iniciais), e as sugestões da IA no modo observação: pendentes, substituídas
pela resposta que a equipe deu por fora (uma parecida, outras diferentes), uma
avaliada 👍 e outra 👎 com correção. Mais o espelho do Bling (pedido, NF,
chamado, devolução — o "No DaVinci" do painel), "Hora de envio" e "Tempo
concluído" nos pedidos, o histórico de compra e as avaliações de cinco
compradores da Shopee (o cartão **Cliente**), as 12 categorias do manual (um
EXEMPLO até chegar o manual base), 8 regras por tipo (2 de segurança, 5 de
categoria, 1 de estilo — nenhuma em conflito) e 5 respostas prontas. Os prazos contam a partir de AGORA (e o número do pedido da
Shopee começa pela data da compra) — rode de novo no dia do teste e sempre que
quiser voltar ao estado inicial.

```bash
cd ~/davinci/apps/api
uv run python -m scripts.atendimento_local semente --schema davinci_local_atendimento
```

A última linha resume: `14 lojas (13 ligadas ao cadastro), 19 canais (todos
em observar), 27 conversas, … 18 sugestões (1 bloqueado, 1 descartado,
12 pendente, 4 substituido), 3 avaliações, … índice do cliente: 21 pedidos e
3 avaliações da loja, 12 categorias, 8 regras (2 segurança, 5 categoria,
1 estilo) …`. Linha começando com `aviso:` quer dizer que um dado de exemplo
não bate com o validador, com o gravar, com o nome da loja ou com o manual
(categoria que não existe, regra em conflito) — avise.

### 3. Subir a API (terminal 1, deixe aberto)

Porta 8011, **envio desligado**, leitura/automático/Telegram desligados à força
e chamadas às lojas barradas. Não use `uvicorn app.main:app` com
`DATABASE_SCHEMA`: o usuário do Postgres local cai no schema `davinci` para os
tipos e o SQL cru, e o app quebra ("operador não existe: …user_role =
user_role"). O script fixa o schema em cada conexão.

```bash
cd ~/davinci/apps/api
uv run python -m scripts.atendimento_local api --schema davinci_local_atendimento --porta 8011
```

A primeira linha tem que dizer `simulador=True envio=False leitura=False … lojas=barradas`.

### 4. Subir a tela (terminal 2, deixe aberto)

```bash
cd ~/davinci/apps/web
API_URL_INTERNAL=http://127.0.0.1:8011 npx nuxt dev --port 3000
```

(Um aviso `Failed to resolve import "#app-manifest"` aparece no terminal da
web e não atrapalha.)

### 5. Entrar

Abra no navegador — com `localhost`, não `127.0.0.1` (o login volta para
`localhost:3000` e o cookie é por endereço):

```text
http://localhost:3000/api/dev/mock-login?next=/atendimento
```

Entra como "Local Admin" e cai direto em **Pós-venda › Atendimento**. A faixa
no topo confirma: leitura desligada, envio desligado, IA desligada.

### 6. Onde clicar

Aba **Caixa**. Na barra de lojas da esquerda, cada loja com o ícone da
plataforma e a bolinha vermelha de não lidas (Marquezini 13, Inova 8, KFA 5,
DREAM2 3, KIA 2, MEGA 2, VELASCO 2); Poofy, POOFY e AGUIAR2 aparecem mesmo sem
conversa aberta; as três TikTok, apagadas com cadeado e o motivo. A VELASCO
fica acesa (as perguntas dela estão sendo lidas), com o número e um alerta
pequeno: passe o mouse para ver que o pós-venda dela está com erro de leitura.
Clique numa loja para filtrar; busque pelo nome do comprador. O nome é o da
LOJA do cadastro: a integração "mega" da Shopee aparece como **Marquezini**, e
a Amazon "kfa" como **KFA** (P2).

O cartão **Cliente** fica no topo do painel da direita (P5) — os cinco casos
do roteiro estão na tabela abaixo marcados com **Cliente**. Os compradores do
ML só têm o cartão com o que está no banco (as perguntas antes de comprar): a
lista de pedidos do comprador no ML vem ao vivo da API, que a API local barra
(`TECH.MARIANA`: 2 perguntas antes de comprar; `HELENA_COSTA10`: a compra da
conversa).

| Conversa | O que ver |
|---|---|
| `carla.nunes.84` (Shopee Inova, com foto) | Cartão "Confirmação de pedido" (foto, ID, status, Montante Total) e a mensagem automática, os dois "Fora do DaVinci". Painel **Pedido**: Enviado, itens com foto/variação/SKU, valor pago, cartão de crédito, Shopee Xpress, rastreio, "Coletado", **Hora de envio** (a coleta, há 20 h). Embaixo, **No DaVinci**: pedido 48211 no Bling. Em vez da caixa de envio, **O que a IA responderia**: 👍/👎 e **Copiar**. **Cliente**: primeira compra |
| `patricia.lemos` (Shopee Inova) | IA × equipe: a resposta real (por fora) e, logo abaixo, "A IA teria respondido" — parecida, já avaliada 👍 pela Paula |
| `marcos.vinicius22` (Shopee Marquezini) | IA × equipe com a IA errada: a logística diz "Falha na entrega" e a IA não viu; avaliada 👎 com a correção |
| `gui.mendes_` (Shopee Inova) | Cartão de produto que o comprador mandou (foto, título, preço "de/por") e a pergunta de pré-venda |
| `julianarocha.rj` (Shopee Marquezini, 8 não lidas) | Várias mensagens seguidas e dois cartões de produto (malas); a prévia da lista mostra "[Produto]" |
| `ana.beatriz.s` (Shopee Marquezini) | O comprador mandou o cartão do pedido (à esquerda). Pedido de 2 itens, "Pronto para enviar", sem rastreio nem hora de envio (antes da coleta a Shopee não tem). **Cliente** recorrente: cliente desde mar/2026, 3 compras, avaliou 5★ a primeira |
| `rodrigo_alves.m` (Shopee Inova, vencida) | Foto do comprador em miniatura; sugestão "precisa de pessoa" (defeito); **No DaVinci** mostra a devolução. Pedido concluído: **Hora de envio** e **Tempo concluído**. **Cliente**: primeira compra, avaliou 2★ ("tela riscada", sem resposta da loja) e já pediu devolução — selos "avaliou mal" e "já pediu devolução" no cabeçalho |
| `fe.cardoso` (Shopee Marquezini) | A sugestão da IA foi barrada pelo validador (prometia prazo e frete grátis) |
| `leo.batista` (Shopee KFA) | Mensagem do sistema ("solicitou o cancelamento"), pedido "Cancelando", sugestão só para pessoa. **Cliente**: nenhuma compra que valeu e 2 cancelamentos (um há 20 dias e o da conversa) |
| `joao_p.freitas` (Shopee KFA) | Só cartão + foto: sem sugestão; pedido "Em devolução". **Cliente**: 2 compras (recorrente), a da conversa em devolução — selo "já pediu devolução" |
| `sandra.m.oliveira` (Shopee KFA) | Agradecimento já respondido por fora. **Cliente**: 2 compras (recorrente), avaliou 5★ o pedido da conversa (com a resposta da loja). Pedido concluído com **Hora de envio** e **Tempo concluído** |
| `vitor.hugo.sp` (Shopee KIA) | Filtro **Minhas** (atribuída ao Local Admin); a sugestão foi descartada com o motivo |
| `renato.alm` (Shopee KIA) | Conversa longa (pré-venda, pedido, pós-venda), IA pausada, "obrigado" parado na fila |
| `TECH.MARIANA` (ML DREAM2, pergunta, vence em < 1 h) | Aba **Produto**: o anúncio perguntado com foto e preço "de/por". **Outras perguntas** traz a de ontem da mesma compradora no mesmo anúncio |
| `LU.TAVARES` (ML DREAM2, pergunta) | Respondida por fora, ainda SEM avaliação: é aqui que se testa o 👍/👎 do "A IA teria respondido" |
| `SERGIO.VIAGENS` (ML AGUIAR2, pergunta) | IA × equipe com textos diferentes (a equipe deu as medidas; a IA mandou ver a ficha) |
| `PAULO_R2019` (ML DREAM2, pergunta vencida) | Pedido de desconto: sugestão só para pessoa |
| `HELENA_COSTA10` (ML DREAM2, pós-venda) | Painel **Pedido** do ML: Pago, "A caminho", Mercado Envios; a NF aparece no **No DaVinci** (o pedido do ML não traz NF) |
| `CAMILA_ROCHA_SP` (ML MEGA, vence em < 2 h) | A resposta por fora foi reprovada na moderação do ML (em vermelho): o cliente continua esperando e a sugestão continua valendo. **No DaVinci**: chamado aberto e atraso |
| `RAFA.SOUZA88` (ML MEGA) | Bloqueada pelo ML (pedido cancelado) |
| `DUDA_ALMEIDA` (ML VELASCO) | Pergunta sem sugestão ainda |
| `Beatriz Lacerda` (Amazon KFA) | Sem retrato do pedido (a Amazon ainda não tem API aqui): o painel mostra o que o DaVinci sabe — Bling e rastreio da Amazon |
| `Otávio Nogueira` (Amazon, conta não identificada) | A faixa pede para escolher de qual conta Amazon é a conversa |
| `Sofia Brandão` (Amazon) | "Não precisa de resposta": fora da fila, só no filtro **Todas** |
| `bruna_s.lima` (Shopee KIA) | Filtro **Fechadas** |

O botão **atualizar** do painel Pedido não aparece nesta API local: a leitura
está desligada (P4 — com a leitura desligada, o botão some em vez de dar erro).
O painel mostra o retrato da semente. Com a leitura ligada, em produção, o
segundo clique dentro de 1 minuto nem sai ("Pedido atualizado há pouco").

Aba **Lojas e modo**: todas em `observar`; TikTok "sem permissão" e "ML
VELASCO · Pós-venda" com erro de leitura (é o dado de exemplo). **Automático**
só admin. Aba **Manual da IA** (P7): as regras agrupadas por tipo — 2 de
segurança, 5 de categoria (rastreio, prazo de envio, nota fiscal, dúvida de
produto no ML, defeito na Shopee), 1 de estilo — com categoria, tipo e
prioridade no formulário. Para ver o conflito, crie outra regra de
**categoria** "Nota fiscal" para todas as plataformas: a tela recusa e mostra
a que já existe. Abas **Manual da IA** e **Respostas prontas**: criar, editar,
desativar e apagar. **Métricas**: o que a equipe fez com as sugestões (7/15/30
dias).

Para testar a caixa de ENVIO (vai para o **simulador**: nada sai para
comprador), suba a API assim no passo 3 e, na aba **Lojas e modo**, passe a
loja da conversa para **Humano** ou **Copiloto**:

```bash
cd ~/davinci/apps/api
ATENDIMENTO_ENVIO_ATIVO=true uv run python -m scripts.atendimento_local api --schema davinci_local_atendimento --porta 8011
```

A primeira linha passa a dizer `envio=True`.

Com a IA desligada o botão **Sugerir resposta** não aparece (as sugestões da
tela são as da semente). Gerar sugestão de verdade precisa da chave do modelo
(`ATENDIMENTO_IA_ATIVA` + `ATENDIMENTO_LLM_*` no `.env`) e gasta token — fica
para quando o Eduardo decidir.

**Testar a IA com a Claude aqui no Mac** (gasta token da conta da Anthropic;
localmente só o botão **Sugerir resposta** chama o modelo — o cron não roda):
no `~/davinci/.env`, as duas linhas da Claude (seção "Como ligar em
produção") mais a IA ligada. As linhas `ATENDIMENTO_IA_ATIVA` e
`ATENDIMENTO_LLM_API_KEY` já existem nesse `.env`: troque o VALOR delas (guarde
a chave que estava lá, para voltar), não acrescente outra linha igual.

```bash
ATENDIMENTO_IA_ATIVA=true
ATENDIMENTO_LLM_MODEL=claude-sonnet-5
ATENDIMENTO_LLM_API_KEY=<a chave sk-ant- do console.anthropic.com>
```

Religue a API local (`Ctrl+C` no terminal 1 e o comando do passo 3 de novo):
a primeira linha passa a dizer `ia=True`. Abra uma conversa e aperte
**Sugerir resposta**; no terminal da API aparece `atendimento_ia_claude` com o
modelo e os tokens. Para voltar ao Groq, apague a linha do modelo e devolva a
chave antiga à `ATENDIMENTO_LLM_API_KEY`.

### 7. Desligar e apagar

`Ctrl+C` nos dois terminais. Para apagar o schema de teste:

```bash
cd ~/davinci/apps/api
uv run python -m scripts.atendimento_local limpar --schema davinci_local_atendimento
```

O login de teste deixa o navegador com a sessão do "Local Admin" em
`localhost:3000`; para voltar ao dev de sempre, saia e entre de novo.
