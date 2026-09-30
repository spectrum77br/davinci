# Magalu na caixa /atendimento (pesquisa de 30/09/2026)

Visto no ID Magalu da Poofy (Consentimentos): o app DavinciERP tem só "APIs de Marketplace - Portfólio" e "Pedidos"; a UpSeller ERP já tem "Plataforma Seller - Customer Services" e "Perguntas e Respostas". O DavinciERP é gerenciado pelo CLI oficial `idm` (não há tela web).

Eduardo, abaixo o que foi conferido em 30/09. O que não está confirmado vem marcado **[incerto]**.

**0) Integrações ativas em produção hoje**
Fiz só leitura na tabela `integrations`:
- **Mercado Livre:** 23 contas. A nexus está arquivada.
- **Shopee:** 14.
- **TikTok:** 8.
- **Amazon:** 4 (kfa, kia, nexus, poofy).
- **Magalu:** 1 (poofy), com teste OK hoje.
- **Bling:** 1 (Bling Geral). Não é canal de chat.
- **Temu:** o conector existe no código (só estoque, preço e anúncio), mas não há nenhuma integração cadastrada.

Das contas acima, a única que ainda não está prevista no /atendimento é a Magalu.

O Duoke tem mais contas que o DaVinci: 21 Shopee e 25 ML, contra 14 e 23. **[incerto]** Não conferi quais são nem se o /atendimento pegaria essas contas sem cadastro no DaVinci.

**1) Magalu no /atendimento: dá, pré e pós-venda**
A Magalu tem 3 APIs oficiais para o vendedor. Nenhuma está nos 11 escopos atuais do app DavinciERP.
- **Perguntas (pré-venda):**
  - Servidor `services.magalu.com`.
  - `GET /v0/questions?status=WAITING_RESPONSE` e `POST /v0/questions/{id}/answer`.
  - Escopos `services:questions-seller:read` e `services:questions-seller:write`.
  - A resposta passa por moderação antes de publicar.
- **Chat com cliente:**
  - O comprador abre a conversa pelo produto, e ela continua aberta depois da compra.
  - Servidor `services.magalu.com`: `GET /v0/conversations`, `GET .../messages` e `POST .../messages` (até 2200 caracteres).
  - Escopos `services:conversations-seller:read` e `services:conversations-seller:write`.
  - O vendedor só responde, não abre conversa.
- **SAC/protocolos (pós-venda):**
  - Servidor `api.magalu.com`.
  - `GET /seller/v0/tickets?status=waiting_seller`. O ticket traz `due_date`, que é o prazo de resposta.
  - `GET` e `POST .../tickets/{id}/messages` (até 3000 caracteres, anexo de até 25MB).
  - Escopos `open:tickets-seller:read`, `open:ticket-messages-seller:read` e `open:ticket-messages-seller:write`.

Os três têm webhook. O limite é de 200 leituras por minuto por vendedor.

**2) O que você precisa fazer**
- **a) Incluir os escopos no app DavinciERP (ID Magalu):**
  - Não achei tela web para isso. O caminho documentado é o CLI oficial `idm`, logado na conta dona do app: `./idm client add-scope --client-uuid <uuid> --scopes '<escopos acima>' --reason '...'`.
  - Pode cair em análise de conformidade. O status aparece como PENDING em `./idm client list`.
- **b) Conferir o audience do app:**
  - Ele precisa ter `https://services.magalu.com`. **[incerto]** Não sei se já tem.
  - Se não tiver: `./idm client update --audience "https://api.magalu.com https://services.magalu.com"`. Passe os dois, porque o update substitui a lista inteira.
- **c) Reautorizar a loja poofy:**
  - Só depois que o código com os escopos novos estiver em produção: Integrações > poofy > **"Autorizar no Magalu"**, e aceitar a tela de consentimento.
  - Reautorizar antes não adianta, porque hoje o DaVinci pede só os 11 escopos.

**3) O que o DaVinci constrói**
- Em `marketplaces/magalu.py`: os escopos novos em `MAGALU_SCOPES` e um segundo endereço base para `services.magalu.com`. Hoje só existe o `api.magalu.com`.
- Um adaptador novo, `atendimento/magalu.py`, no molde do `ml.py`: perguntas como as do ML, e chat e SAC como mensagens pós-venda. Primeiro por consulta periódica, webhook depois.
- `magalu` entra em `PLATAFORMAS` (`constantes.py`) e no router. Hoje não está lá; só aparece como apelido de nome de loja.
- O DaVinci nunca chama `PATCH /v0/conversations/{id}/read_by`, que é o que marca como lido.

Riscos:
- O /atendimento ainda não está em produção. Está tudo local, sem commit desde 25/09, e a Magalu entraria em cima disso.
- **[incerto]** A documentação não diz com todas as letras que o GET não marca como lido. Precisa de um teste na primeira conversa, olhando `unread_to_count` antes e depois.
- Os escopos podem ficar PENDING na Magalu, sem prazo conhecido.
- Pelo FAQ oficial do SAC, mensagens privadas ou barradas pela moderação não aparecem na API. Parte do protocolo mediado pode continuar visível só no portal.
- A moderação bloqueia CPF, PIX, e-mail etc. nas respostas.
- **[incerto]** Há um aviso oficial "Alteração Header API de SAC" (14/01/2025) em PDF que não abri. Pode exigir um header no SAC.
- O SLA oficial é retorno ao cliente em até 2 dias úteis, com 98% dentro do prazo. Se o vendedor não resolver no prazo, a Magalu atende o cliente e cobra os custos do vendedor. A meta de 8h úteis só aparece em blog de terceiro **[incerto]**.

**4) Duoke**
- Cobre Shopee, Mercado Livre, TikTok Shop, Lazada, Daraz, Facebook Messenger, WhatsApp e LiveChat. Isso bate entre o site oficial ("8 plataformas") e os 127 artigos da central de ajuda.
- No Brasil, na prática: Shopee, ML e TikTok, mais WhatsApp e Facebook.
- Não cobre Magalu, Amazon, Shein, Temu, AliExpress nem Americanas.
- Instagram só aparece num post do blog, sem guia de conexão **[incerto]**.
- As mensagens da Magalu poofy não estão no Duoke. Se alguém lê hoje, é pelo portal do seller Magalu.
- O DaVinci seria o único lugar que junta Magalu e Amazon com os outros canais.
- O Duoke não tem API pública, então o DaVinci não consegue ler de lá.

**Fontes**
- Perguntas: developers.magalu.com/docs/apis/questions/ref/perguntas
- Chat: developers.magalu.com/docs/apis/conversations/ref/conversas
- SAC: developers.magalu.com/docs/apis/sac/overview e /docs/apis/faq/sac
- Webhooks: developers.magalu.com/docs/development-guide/webhooks
- Limite de requisições: developers.magalu.com/docs/development-guide/rate-limit
- CLI e escopos: developers.magalu.com/docs/first-steps/create-an-application/create-application
- Consentimento: developers.magalu.com/docs/first-steps/create-an-application/authentication-authorization
- Datas de lançamento: developers.magalu.com/releases.html
- SLA: universo.magalu.com/blog/artigo/acordo-de-nivel e /anexo-de-pos-venda
- Duoke: duoke.com/pt/index.html; ai.duoke.com/pt/help-doc-article-1285 (plataformas), -965 (ML) e -953
- Código:
  - /Users/thorfinn/davinci/apps/api/app/services/marketplaces/magalu.py
  - /Users/thorfinn/davinci/apps/api/app/services/atendimento/constantes.py
  - /Users/thorfinn/davinci/apps/api/app/services/atendimento/lojas.py:54
  - /Users/thorfinn/davinci/apps/web/pages/integrations.vue:198 (botão "Autorizar no Magalu")
- Banco de produção: consulta só leitura em `davinci.integrations` (plataforma, status e nome; sem credenciais).
