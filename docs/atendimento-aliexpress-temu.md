# AliExpress e Temu na /atendimento: plano (29/09/2026)

**Resumo**
- **Nenhuma das duas tem API oficial para ler ou responder o chat do comprador.** No AliExpress a certeza é alta, porque a própria plataforma diz isso por escrito. Na Temu a certeza também é alta, mas só para a documentação pública: não sei se existe uma API privada.
- **O que a API permite trazer para a /atendimento:**
  - AliExpress: pedidos, disputas e avaliações.
  - Temu: pedidos, cancelamentos e devoluções com o comentário do comprador.
  - Isso entra como "casos" (disputa, cancelamento, devolução), não como chat.
- **O chat continua no Seller Center de cada plataforma.** O Duoke também não cobre nenhuma das duas. Então não existe o problema de "marcar como lido" com elas.

Como ler as marcas: **[oficial]** é documentação da plataforma, **[terceiro]** é blog, ERP ou site de terceiros, **[dedução]** é conclusão minha.

---

## AliExpress

### 1) Veredito: chat não dá. Certeza alta.
- **[oficial]** A FAQ 792 (25/04/2022) diz três coisas:
  - desde mar/2019 as mensagens antigas viraram o chat novo (IM);
  - o IM não pode ser lido por interface;
  - as interfaces antigas de mensagem estão "todas desativadas".
  - Por isso não adianta pedir as APIs antigas `aliexpress.message.*`.
- **[oficial]** O catálogo tem 43 categorias e 438 APIs (conferido em 29/09/2026). Nenhuma lista, lê, responde ou marca conversa como lida. As APIs antigas de mensagem não têm página.
- **[terceiro]** Uma FAQ do AliExpress republicada no cifnews diz que não há plano de abrir API do IM. O Dianxiaomi (ERP chinês) diz que nenhum ERP consegue sincronizar o chat novo.
- **Única ponta solta [oficial]:** a doc chinesa 832 (07/2025) lista um aviso por push do tipo 21, "Instant Messaging Notification".
  - Não tem exemplo de conteúdo.
  - A mesma doc diz que hoje só os avisos de pedido estão no ar.
  - A versão em inglês nem lista esse tipo.
  - Mesmo que funcione, seria só um aviso: não existe API para ler nem responder.
- **O que existe por API [oficial]:**
  - pedido completo;
  - disputas, que são a única troca de mensagens com o comprador acessível por API;
  - avaliações, com resposta.

### 2) O que você precisa fazer (só se vendem no AliExpress)
1. Criar a conta de desenvolvedor em https://openservice.aliexpress.com.
   - Fluxo: "Overseas Developers".
   - Tipo: **Seller-inhouse Developer** (sistema próprio do vendedor).
   - O tipo não pode ser trocado depois [oficial, docs 1361 e 1868].
2. Pedir os grupos de permissão de pedido (Order&Transaction), disputa (Return&Refund) e avaliação (Evaluate). A revisão é manual [oficial].
3. Autorizar a loja: abrir `https://api-sg.aliexpress.com/oauth/authorize?response_type=code&force_auth=true&redirect_uri=<callback>&client_id=<appkey>` e entrar com a conta de vendedor.
   - O login é seu; o DaVinci só recebe o código.
   - O código vale 30 minutos [oficial, doc 1364].
4. Abrir um ticket no App Console (Ticket) com só estas perguntas:
   - O push tipo 21 vale para app Seller-inhouse de vendedor brasileiro?
   - Ele traz o texto da mensagem ou só o aviso?
   - Existe alguma API de IM para vendedor?

**Prazo e aprovação**
- A regra chinesa diz de 2 a 5 dias úteis [oficial, doc 1006]. Para o fluxo overseas não achei prazo.
- **A aprovação com CNPJ não está garantida (não verificável).**
  - As regras detalhadas publicadas só falam de empresa da China continental ou de Hong Kong.
  - O fluxo overseas não restringe país.
  - Não achei nenhum exemplo de vendedor brasileiro aprovado.
  - Hoje as lojas brasileiras usam a API por meio de integradores (Bling, Olist, UpSeller) [terceiro].

**Validade do token**
- App self-dev: 365 dias, refresh de 730 dias, até 50 lojas da mesma empresa [oficial, doc 639 em chinês].
- A FAQ overseas fala em 30/60 dias, mas está na seção de dropship. Confirmar na prática.

### 3) O que o DaVinci constrói
- **Cliente novo `services/marketplaces/aliexpress.py`** (hoje não existe):
  - assinatura HMAC-SHA256 no padrão do SDK oficial (IOP);
  - endpoint `https://api-sg.aliexpress.com/sync`;
  - renovar o token 30 minutos antes de vencer.
- **Adaptador `services/atendimento/aliexpress.py`**, no molde do `ml.py` e do `shopee.py`:
  - **Disputa:** `aliexpress.issue.issuelist.get` e `aliexpress.issue.detail.get` trazem o histórico (texto, anexos, quem enviou). As ações são aceitar (`solution.agree`) ou contrapropor e recusar (`solution.save`; recusar é uma proposta de valor 0). **Não é texto livre de chat** [oficial, doc 708].
  - **Avaliação:** `aliexpress.evaluation.listorderevaluation.get` lê e `aliexpress.evaluation.evaluation.reply` responde em texto. A IA pode sugerir a resposta.
- **Painel lateral:** `aliexpress.trade.seller.orderlist.get` e `aliexpress.trade.new.redefining.findorderbyid` trazem itens, valores, pagamento, rastreio, disputa e observação do comprador.
  - As datas vêm no horário do Pacífico: converter.
  - Para saber se o pedido terminou, ler o `endReason` do subpedido [oficial, docs 9805 e findorderbyid].
- **Push (opcional):** se não usar, fica com polling. Para usar:
  - HTTPS com certificado **OV/EV** (DV e Let's Encrypt não servem);
  - responder HTTP 200 em até 500 ms;
  - tratar avisos repetidos (idempotência);
  - validar a assinatura HMAC [oficial, doc 1478].
- **Marcar como lido:** não se aplica, porque não há API de chat e o Duoke não cobre o AliExpress.

**Riscos**
- A aprovação do app é incerta para CNPJ brasileiro.
- O AliExpress desliga APIs pouco usadas: houve rodadas em dez/2024 e mar/2025 [oficial, docs 1737 e 1799].
- `issue.image.upload` tem informação contraditória: a FAQ diz que não tem mais suporte, o catálogo ainda lista. Testar.
- Não há limite de chamadas publicado para as APIs de pedido.
- O campo `order_msg_list` está marcado como "deprecated" e provavelmente vem vazio.

### 4) Alternativas para o chat
- **E-mail, como na Amazon:** não achei nenhuma fonte dizendo que o AliExpress manda o texto da mensagem por e-mail. O teste é simples: se você receber um e-mail de "nova mensagem", mande o print.
- **Extensão de navegador:** existem, por exemplo 速卖通客服助手 (v8.6, 22/04/2026), que rodam dentro do seller center já logado.
  - Em teoria, uma extensão nossa copiaria as mensagens para o DaVinci.
  - Contras: depende de login e do layout da tela (quebra quando mudam), abrir a conversa provavelmente marca como lida, e não achei cláusula nos termos sobre isso (risco não medido).
  - **Não recomendo agora.**
- **Robô que faz login:** exige senha e arrisca a conta. **Não.**
- **Recomendação:** o chat do AliExpress fica no seller center. O DaVinci mostra disputas, avaliações e pedido, com um botão "abrir no AliExpress". Não achei link documentado que abra direto a conversa.
  - Um guia de terceiro fala em resposta em até ~24h no chat, sem regra oficial achada. Alguém precisa continuar olhando o chat lá.

---

## Temu

### 1) Veredito: chat não dá pela API oficial. Certeza alta (doc pública).
- **[oficial]** Conferi as documentações Global (que inclui o Brasil), dos EUA e da Europa:
  - nenhum método de chat, mensagem ou atendimento;
  - 175 métodos nos pacotes de permissão e nenhum pacote de "Customer Service";
  - o webhook tem só 4 eventos: pedido, endereço, pós-venda e cancelamento.
- **[terceiro]** O eDesk diz que a integração com a Temu sai "em breve" (página atualizada em 10/09/2026). O Responso lia a Temu por e-mail encaminhado (UE) e está fora do ar. Nenhum hub brasileiro (UpSeller, Magis5, Bling, ANYMARKET) nem o Duoke tem chat da Temu.
- **[oficial] O Brasil já está na Open API:**
  - grupo "Global", painel br.seller.temu.com, endpoint `openapi-b-global.temu.com`;
  - o `temu.py` do DaVinci já usa esse endpoint como padrão (`TEMU_REGION_URLS["global"]`).
- **A loja local brasileira tem chat? Não confirmado por fonte oficial.**
  - A favor:
    - a política do vendedor (21/05/2026, página dos EUA) fala em histórico de conversa entre vendedor, clientes e Temu;
    - um curso da Temu para vendedor chinês (chwang, 31/03/2025) diz que toda loja pode contatar o comprador pelo pedido. A primeira mensagem tem motivo pré-definido e depois são só 2 livres. O atendimento pelo próprio lojista é ligado quando a lei local exige;
    - a Nuvemshop (08/09/2026) fala em metas de tempo de resposta.
  - Contra: não há API nem integrador.
  - Só você, olhando o Seller Center, resolve isso.
- **O que dá por API [oficial]:**
  - pedidos;
  - cancelamentos pedidos pelo comprador (`bg.aftersales.cancel.list.get` e `bg.aftersales.cancel.agree`). O vendedor tem 24h; depois a aprovação é automática;
  - pós-venda com o comentário e o motivo do comprador (`temu.aftersales.parentaftersales.detail.get`: `buyerComment`, `afterSalesReasonDesc`);
  - reembolso (`temu.aftersales.refund.issue`).
  - O `buyerComment` é uma observação única, não uma conversa.

### 2) O que você precisa fazer
1. Em https://partner.temu.com, criar a conta e escolher **"Self-developed applications seller"**.
   - O tipo não muda depois.
   - Cada conta do Seller Center pode ter só 1 app próprio [oficial, 14/05/2026].
2. Vincular a **conta principal** do Seller Center do Brasil, como vendedor local.
3. Preencher o app:
   - prints e descrição das APIs: Order Management, Aftersales Management e Basic Management (para o webhook);
   - questionário de compliance e segurança;
   - IP do servidor Hetzner.
4. Depois de aprovado, autorizar a loja no Seller Center, em open-platform/client-manage, clicando em "Authorize a new app".
   - Marcar as permissões e copiar o access_token (ou usar o fluxo por callback).
   - A doc só mostra os endereços dos EUA e da Europa. O do Brasil (`br.seller.temu.com/open-platform/client-manage`) é **[dedução]**.
5. Mandar e-mail para **partner@temu.com**, e para o gerente de conta da Temu Brasil se houver, pedindo API de atendimento/chat para vendedor local no Brasil. É o único canal oficial, sem compromisso de resposta.

**Prazo de aprovação:** não achei.

### 3) O que o DaVinci constrói
- **Estender o `temu.py`**, que hoje só faz estoque, preço e listagem e nunca rodou em produção: pedidos, endereço (`bg.order.shippinginfo.v2.get`), pós-venda e cancelamento.
- **Adaptador `services/atendimento/temu.py`:**
  - casos de cancelamento e devolução, com o comentário e o motivo do comprador;
  - ações de aprovar cancelamento e reembolsar. **Mexem em dinheiro: sempre clique humano com confirmação, nunca a IA.**
- **Webhook** `bg_aftersales_status_change` e `bg_cancel_order_status_change`, mais polling de segurança.
- **Painel lateral:** pedido, itens, status e rastreio.
  - Nos pedidos do Brasil a API traz o telefone real e o CPF (`taxCode`) [oficial].
  - A política de segurança da Temu manda apagar dados pessoais até 30 dias depois do serviço. Não guardar isso na base de clientes do atendimento além desse prazo.
- **Botão "abrir no Seller Center":** não há link documentado para a conversa de um pedido. Abre a lista de pedidos.
- **Marcar como lido:** não se aplica, porque o Duoke não cobre a Temu.

**Riscos**
- Aprovação sem prazo conhecido.
- Ações de dinheiro dentro do DaVinci.
- Dados pessoais (telefone e CPF).
- Um ERP chinês (datacaciques) diz que sincroniza mensagens da Temu por "API oficial". É texto de marketing sem detalhe técnico: não confio.

### 4) Alternativas para o chat
- **E-mail (melhor aposta, precisa testar):**
  - O Responso lia a Temu por e-mail na Europa, então a Temu mandava avisos por e-mail lá **[dedução, baixa]**.
  - Se a conta brasileira receber e-mail com o texto do comprador, o DaVinci lê por IMAP, igual ao `amazon_email.py`.
  - **Responder por e-mail ninguém confirmou.** Só com teste controlado.
- **E-mail do pedido:** o campo `mail` é um "Virtual Email" (`@g.shipping.temuemail.com`). A doc não diz se repassa ao comprador. Não usar sem confirmar.
- **Telefone real e WhatsApp:** a API traz o telefone, mas os termos limitam o uso ao negócio dentro da Temu. Não achei regra brasileira sobre contato fora da plataforma. **Não recomendo.**
- **Extensão ou robô:** mesmos problemas do AliExpress. **Não.**
- **Recomendação:**
  - Fase 1: app self-dev, casos de pós-venda e cancelamento na /atendimento, e-mail para partner@temu.com, e teste dos e-mails de aviso.
  - Até lá, o chat fica no Seller Center.

---

## Perguntas que só você responde
1. Vocês vendem hoje no AliExpress e na Temu? Quantas lojas em cada? Nenhuma está conectada no DaVinci hoje.
2. Como vendedor local (CNPJ, estoque no Brasil) ou cross-border?
3. Quantas mensagens de comprador chegam por semana em cada uma? Vale o trabalho?
4. Na Temu Brasil: o Seller Center tem uma tela de conversas ou "consultas de consumidores"? Quem responde hoje, vocês ou a Temu? Mande print.
5. Chega e-mail quando o comprador escreve, no AliExpress e na Temu? Vem com o texto? Dá para responder o e-mail? Mande print de um.
6. Quem tem a conta principal do Seller Center de cada uma? Você topa abrir o cadastro de desenvolvedor em nome da empresa?
7. Bling, UpSeller ou Olist já estão conectados a essas lojas? Não trazem chat, mas mostram que já há app autorizado.
8. O DaVinci pode aprovar cancelamento e reembolso na Temu e agir em disputa do AliExpress, ou deve só mostrar?

**Ordem sugerida:**
1. Você responde as perguntas 1 a 5.
2. Se vendem na Temu, começar por ela: o Brasil já está documentado, o `temu.py` já tem a base e o cadastro não restringe país.
3. AliExpress depois, com o ticket aberto antes.

---

## Fontes principais
**AliExpress**
- Catálogo de APIs: https://openservice.aliexpress.com/doc/api.htm (consultado em 29/09/2026)
- FAQ 792, "IM sem interface; mensagens antigas desativadas" (25/04/2022): https://openservice.aliexpress.com/handler/share/doc/getDocDetail.json?docId=792
- Push, tipo 21 (07/2025): https://openservice.aliexpress.com/doc/doc.htm?nodeId=27493&docId=118729#/?docId=832
- Regras do push (30/08/2024): https://openservice.aliexpress.com/doc/doc.htm?nodeId=27493&docId=118729#/?docId=1478
- Tipos de app overseas (29/03/2024): …#/?docId=1361 e …#/?docId=1362
- Regras de entrada (13/11/2025): …#/?docId=1006
- Autorização e tokens (20/05/2025): …#/?docId=639
- OAuth (29/03/2024): …#/?docId=1364
- Disputas (26/03/2025): …#/?docId=708
- Guia de pedidos (17/09/2026): …#/?docId=9805
- Desligamento de APIs (19/11/2024): https://openservice.aliexpress.com/handler/share/doc/getDocDetail.json?docId=1737
- FAQ do IM republicada (sem data): https://m.cifnews.com/guide/aliexpress/2xmu7saq
- Dianxiaomi (sem data): https://help.dianxiaomi.com/article/customerService/1004

**Temu**
- Referência da API: https://partner.temu.com/documentation?menu_code=fb16b05f7a904765aac4af3a24b87d4a
- Eventos de webhook (31/07/2025): https://partner.temu.com/documentation?menu_code=8f85ca80c375428eb08559e7fd9b6a62&sub_menu_code=0cc16b077a3343d08e87bc3c0a7593a8
- App self-developed (14/05/2026): https://partner.temu.com/documentation?menu_code=52ef88bdef1d4527b15f6d303b173e48&sub_menu_code=6f0f07a3ef3d4158973e9e9aa3ac816c
- Primeiro app e partner@temu.com (26/01/2025): …menu_code=52ef88bdef1d4527b15f6d303b173e48&sub_menu_code=5d3fe3f2759447b38a4931da31a8e8e4
- Autorização do vendedor (07/01/2026): https://partner.temu.com/documentation?menu_code=38e79b35d2cb463d85619c1c786dd303&sub_menu_code=1e91f4383a8340c2b98780acc34fa6ec
- Site Brasil (17/08/2025): …menu_code=38e79b35d2cb463d85619c1c786dd303&sub_menu_code=0613960f489243358637212facf31549
- Pós-venda (30/10/2025): …menu_code=fb16b05f7a904765aac4af3a24b87d4a&sub_menu_code=96269b0c4bf145e5b8c89c3f00511a80
- Política do vendedor (21/05/2026): https://seller.temu.com/policy-page.html?type=5
- Curso de atendimento Temu (31/03/2025): https://www.chwang.com/guide/186886769696
- eDesk (10/09/2026): https://www.edesk.com/blog/temu-customer-service-integration/
- Responso: https://docs.responso.com/responso-doc/integracje/inne-integracje/temu
- Duoke, canais atendidos (consultado em 29/09/2026): https://www.duoke.com/pt/index.html

**Código lido:** `/Users/thorfinn/davinci/apps/api/app/services/marketplaces/temu.py`, linhas 33 a 37 (`global` = `openapi-b-global`, o padrão). Nenhum arquivo do projeto foi alterado.

---

# Atualização 29/09 — Temu: o chat existe no Seller Center (visto na loja Barbosa)

A pesquisa acima dizia "não confirmado". Olhando o Seller Center da Barbosa: existe "Mensagens de Compradores" (br.seller.temu.com/chat.html), o comprador fala com a loja pelo botão "Bate-papo" da página da loja (sem precisar de pedido), e em Preferências de notificação há "Lembretes de mensagens de clientes" por e-mail (vai para o e-mail de contato da loja, hoje no Tuta). O desenho abaixo veio da leitura do código da tela de chat (só arquivos locais).

# Temu no atendimento do DaVinci — o que dá pra fazer

## Em uma linha
A Temu não tem API de chat. O Seller Center (`br.seller.temu.com/chat.html`) recebe mensagem nova por um WebSocket próprio ("Titan") e busca o resto por chamadas internas (`/api/plateau/...`). Toda chamada dessas carrega um token antirrobô (**Anti-Content**) que **só o navegador real gera**. Então o caminho realista passa por dentro da aba já logada no AdsPower — não por servidor. A loja que você abriu não tinha conversas, então os formatos abaixo vieram da leitura do código do próprio chat; faltam respostas reais (é o que o teste no fim resolve).

## As 4 opções

### A) Espelho passivo — só escutar
Script roda na aba do chat aberta no AdsPower e **só LÊ** o que a página já recebe sozinha: os empurrões da Titan e as respostas das chamadas que a própria página dispara a cada 60s (`getInitState`, `/sync/message`, `getConvList`, `needReplyCount`, `unReadConvList`). Repassa pro DaVinci. Não faz chamada nova, não marca lido, não abre conversa.
- **Funciona?** Sim, para VER. É o único caminho que não muda **nada** no lado da Temu — confirmado no código: a aba aberta já faz tudo isso sozinha; o script só aproveita.
- **Riscos:** a aba tem que ficar aberta e logada (sessão caiu → reabrir no AdsPower). Se a Temu mudar o bundle do chat, o leitor quebra (manutenção recorrente). **Cuidado:** não pode ter conversa selecionada na aba, nem URL com `?posn=<pedido>` — os dois marcam lido sozinhos. No passivo você pega a última mensagem de cada conversa e o que entrar novo, **não** o histórico antigo.
- **Esforço:** baixo–médio.

### B) Script chama as funções da própria página (listar/histórico, sem markRead)
Mesmo script, mas puxa lista e histórico sob demanda (`getConvList`, `getBizConvList`, `getHistoryMsg`, `needReplyCount`). **Nunca** `markRead` nem `enterConv`.
- **Funciona?** Provavelmente sim, e cobre o buraco do histórico do A. Como roda dentro da página, o Anti-Content é gerado pela própria Temu — não precisamos forjar nada.
- **Riscos:** são chamadas EXTRAS fora do ritmo normal da página; padrão estranho pode cair no captcha (erro 54001) e, no limite, sinalizar risco na conta. `getHistoryMsg`/`getConvList` **não** marcam lido (confirmado no código), mas `enterConv`/`markRead` marcam e não podem ser tocados. Mais frágil que A.
- **Esforço:** médio.

### C) Servidor chamando a API interna direto
- **Funciona?** Não de forma sustentável. Fora do navegador não há Anti-Content válido; o servidor é recusado (54001 → captcha) a menos que a gente **forje** o token antirrobô — que é exatamente o que a Temu está tentando barrar, o que mais arrisca a conta e vai contra os termos deles.
- **Recomendo NÃO seguir.** Alto esforço, quebra a cada mudança do token, conta em risco.

### D) Resposta (enviar)
- **D1** — DaVinci enfileira e o script envia pela página (`sendMessage`).
- **D2** — DaVinci só sugere; a pessoa cola e envia no Seller Center.
- **Funciona?** D2 sim, risco zero (é a pessoa enviando). D1 tecnicamente funciona (Anti-Content sai da própria Temu), mas **enviar marca a conversa como lida**, enviar numa conversa com robô da Temu **desliga o robô**, e envio automático é o que mais chama o antifraude. Limite de 2000 caracteres.
- **Esforço:** D2 baixo; D1 médio–alto e arriscado.

## Recomendação
Começar com **A (espelho passivo) + D2 (sugestão que a pessoa envia)**. É o único combo que não altera nada na Temu, não marca lido, não forja token e não arrisca a conta — e já entrega a caixa unificada com o chat da Temu visível dentro do DaVinci. Se faltar histórico, evoluir para **B** só para puxar histórico sob demanda, nunca tocando `markRead`/`enterConv`. **Nunca C. D1 só se você decidir depois.**

## Teste mínimo (numa loja que TENHA conversas)
Objetivo: provar que dá pra VER tudo **sem marcar nada**. Aba do chat aberta no AdsPower, **nenhuma conversa selecionada**, URL **sem** `?posn=`.

1. **Só observar** (aba Rede/WebSocket, sem clicar em nada):
   - o WebSocket `wss://br.seller.temu.com?...` — a mensagem nova aparece aqui sozinha;
   - as respostas que a página já dispara a cada 60s: `sync/getInitState`, `sync/message`, `conv/getConvList`, `conv/needReplyCount`, `conv/unReadConvList`;
   - conferir se os campos batem: nome do comprador, última mensagem, contador de não lidas, grupo `unReply`.
2. **Confirmar o que é seguro:** ver na rede real se `unReadConvList`/`needReplyCount` saem com header `Anti-Content` (no código não põem; se aparecer, há plugin global — bom saber) e qual o prefixo real das rotas `/latitude/*`.
3. **Nunca, nesse teste:** clicar numa conversa, rolar o painel de mensagens, abrir link com `?posn=`, disparar `markRead`/`enterConv`/`markNoNeedReplyMsg`/`markConversation`. Qualquer um marca lido pra equipe inteira.

Se a mensagem nova chegar pelo WebSocket e a lista/contadores vierem das chamadas que a página já faz, o **A está provado** e dá pra ligar o repasse pro DaVinci.

## Incertezas (marcadas)
- Se o servidor recusa chamada **sem** Anti-Content: só dá pra saber testando — e não vamos forçar.
- O que `enterConv` faz no servidor: desconhecido → não tocar.
- Autenticação da Titan: presumidamente pelos cookies da sessão do AdsPower (inferência, não está no código).
- Prefixo real de `/latitude/*`: confirmar na rede.
- Formatos das respostas: reconstruídos do código porque a loja observada não tinha conversas — o teste acima é o que valida.

---

# Atualização 29/09 — AliExpress: a tela de chat do vendedor (visto na loja Vita)

"Centro de mensagens" abre o AliExpress Chat (gsp.aliexpress.com/m_apps/im-chat). Em Configurações > Notificações há "Email Lembrete" (desligado; no máximo 1 e-mail a cada 6 h por comprador). O desenho abaixo veio da leitura do código da tela (só arquivos locais). A pergunta "existe API de mensagem para parceiro/ISV?" está numa pesquisa à parte.

# AliExpress no DaVinci — como mostrar e responder o chat

A AliExpress **não tem API oficial de chat**. Tudo passa pela tela "AliExpress Chat" da conta Seller (a que está aberta no AdsPower da Vita). Então qualquer integração é "por cima" dessa tela — e o risco maior não é técnico, é **queimar a conta** (a Alibaba tem camada anti-robô pesada rodando nessa página) e **marcar conversa como lida sem querer** (a própria tela dispara isso sozinha em alguns casos).

Regra de ouro que atravessa tudo: **ler o que a tela já recebeu é seguro; abrir/renderizar uma sessão com mensagem do comprador faz a tela marcar como lida sozinha.** Nunca deixar o DaVinci (ou um robô) dirigir essa tela.

## As 5 opções

### A) Espelho passivo — script na aba do AdsPower só escuta
- **Funciona?** Sim, para RECEBER. A página já baixa tudo (websocket ACCS + sync) e guarda no banco local do navegador (IndexedDB). Um script na mesma aba lê o que já está lá e manda pro DaVinci. **Não faz nenhuma chamada nova à AliExpress** → não adiciona nada pro anti-robô ver.
- **O que pode dar errado:** a aba precisa ficar sempre aberta e logada (se a sessão cai, para de chegar); se a AliExpress muda o app (hoje 1.42.0), muda o formato do banco e o script quebra; **marcar lido** só acontece se o script (ou alguém) *abrir/renderizar* uma sessão — se ele só lê o banco e nunca mexe na tela, não marca. Detecção: um AdsPower que a equipe já usa de verdade é o cenário normal; um script que só lê não gera requisição.
- **Exemplo:** o script lê a sessão nova no IndexedDB do IM e envia pro DaVinci `{sessão, comprador, última msg, não-lidas}` — sem tocar na tela.
- **Esforço:** médio.

### B) Script chama as funções/mtop da própria página (listar/histórico)
- **Funciona?** Sim, tecnicamente. Dentro da página logada, `querySessionList`, `queryBySessionId`, `sync` e `unreadcount` **não marcam lido**, e a própria página já resolve a assinatura.
- **O que pode dar errado:** agora o script **gera chamadas próprias** — padrão anômalo aumenta o risco de cair no anti-brush (verificação/slider) se tiver volume. Tem que **nunca** chamar `putOffset` e nunca abrir a sessão de um jeito que a tela dispare o `putOffset`. Muda o build, quebra.
- **Exemplo:** puxar histórico com `queryBySessionId` (só leitura) em vez de esperar o banco local encher.
- **Esforço:** médio-alto. Superfície de detecção maior que A, sem ganho grande.

### C) Servidor chamando o mtop direto com cookies
- **Funciona?** Tecnicamente dá, mas **risco alto de queimar a conta**. Fora do navegador faltam os sinais anti-robô que a página injeta; tráfego repetitivo "sem navegador" é exatamente o que o motor de risco pega; o cookie de assinatura expira e só é renovado dentro do navegador. Pode cair em verificação/punição e marcar a conta.
- **Esforço:** alto **+ risco alto**. **Descartar.**

### D) Email Lembrete como gatilho
- **Funciona? (parcial)** É recurso oficial do painel: manda e-mail quando o comprador escreve, **no máx. 1 a cada 6 h por comprador**, e **não toca em "lido"**. O DaVinci já lê e-mail (Amazon/Gmail), então vira um sinal "chegou mensagem na AliExpress" quase de graça.
- **O que pode dar errado:** 6 h por comprador é grosseiro (não vê cada mensagem); **[INCERTO]** não dá pra saber pelo código se o e-mail traz o texto da mensagem ou só um aviso — precisa testar. Não dá histórico nem conteúdo confiável sozinho.
- **Exemplo:** liga `emailnotice.save` no painel com um e-mail que cai numa caixa que o DaVinci lê.
- **Esforço:** baixo.

### E) Resposta
- **E1 — DaVinci enfileira, script envia pela página:** funciona (`sendImMessage` dentro da página logada). Risco moderado: gera envio, o servidor modera (pode voltar "Seller_Abuse"), e volume automatizado cai no anti-brush. Enviar em si não marca lido, mas responder implica estar na sessão.
- **E2 — pessoa responde lá com a sugestão copiada:** funciona, **risco mínimo**, a AliExpress trata como uso humano normal.
- **Exemplo:** DaVinci mostra "sugestão de resposta" ao lado; a pessoa cola e envia na própria tela (E2).

## Recomendação

**Fase 1 (agora): D + A + E2.**
1. **Ligar o Email Lembrete** como sinal barato de "chegou mensagem" na caixa unificada (e no teste descobrir o que o e-mail traz).
2. **Espelho passivo (A):** script na aba do AdsPower que lê só o que a página já recebeu e manda pro DaVinci **só para exibir** — nunca dirige a tela, nunca abre sessão, nunca chama `putOffset`.
3. **Resposta por E2:** DaVinci sugere, a pessoa responde na própria tela. **Zero risco de conta.**

**Fase 2 (só se A provar estável):** E1 (enviar de dentro do DaVinci) **com trava de volume e sempre com uma pessoa confirmando o envio**. **Nunca C.**

Por quê: A + D dão a visão (ver a conversa na caixa única) com o menor risco possível, e E2 tira o risco de envio. É o mesmo padrão do resto do atendimento (mostrar e sugerir; não marcar lido sozinho).

## Teste mínimo (numa loja com conversas)
- **Onde:** a própria aba AliExpress Chat no perfil AdsPower da Vita, já logada e usada pela equipe.
- **Capturar (só leitura):** o formato do banco local do IM (nomes das stores, 1 sessão, 1 mensagem) e um item de `sync` que já chega; e, à parte, ligar o Email Lembrete com um e-mail de teste, mandar uma mensagem de comprador de teste e ver **exatamente o que o e-mail traz e em quanto tempo**.
- **Conferir que nada marcou lido:** comparar o contador de não-lidas (`nonReadNumber`) antes e depois de rodar o script, e conferir com a tela da equipe (não pode sumir badge).
- **NUNCA, no teste:** clicar numa sessão com badge; abrir por link/deeplink; chamar `putRangeRead`/`putOffset`; abrir uma segunda conexão que possa derrubar a da equipe **[INCERTO se derruba]**; servidor batendo mtop.

**Marcar como incerto (confirmar no teste ou deixar claro pro Eduardo):** o conteúdo do e-mail do Lembrete; se o servidor marca lido como efeito colateral de só ler o histórico/abrir sessão; se abrir 2ª conexão ACCS derruba a da equipe; os campos exatos do Email Lembrete.

---

# Atualização 29/09 — AliExpress: existe API de mensagem?

**AliExpress: existe API de mensagem?**

**1) Veredito: não existe API pública. Existe um canal fechado, só para parceiro (ISV).**

- **API pública: não tem.** Conferi hoje o catálogo inteiro do Open Platform (43 categorias), a busca da própria documentação e a lista "Global APIs Overview" (atualizada em 11/09/2026). Não há nada de chat. As APIs antigas de mensagem (`aliexpress.message.*`) foram desativadas (FAQ 792). Um guia do vendedor da AliExpress, republicado em dez/2024, responde "暂时未计划开放api接口" ("por enquanto não há plano de abrir API").
- **Canal fechado: tem, e nisso você está certo.** O código da tela de chat do vendedor (im-ae 1.42.0) tem um programa de "plugins de IM" para provedores do Serviço Marketplace (服务市场):
  - Mensagens enviadas pelo canal `im_open_api` aparecem no chat como "机器人回复内容" (conteúdo de resposta de robô).
  - Os plugins cobrem três cenas: recepção por robô (智能接待), tarefas automáticas (自动任务: cobrar pagamento, recuperar carrinho etc.) e ferramentas no painel (客服面板).
  - Eles também marcam conversas como "importante" ou "precisa responder".
  - O vendedor ativa em "服务市场 → 软件授权页 → 使用服务". A tela diz que há "vários provedores para você escolher".
- **Sem documentação pública:** o programa não tem nome oficial nem doc. O nome da API que o provedor usa no servidor também não aparece em lugar nenhum.
- **Leitura: incerto.** A documentação chinesa de notificações (doc 832, 07/07/2025) lista um "tipo 21: Instant Messaging Notification", mas sem exemplo de conteúdo. A mesma página diz que só as notificações de pedido estão no ar. Não dá para afirmar que o provedor recebe as mensagens por aí.
- **Origem provável da impressão:** a Lazada (mesmo grupo) tem API de chat pública com 7 endpoints. A aliexpress.ru também tem chat por API (integração com o Jivo). As duas são outras plataformas.

**2) Serve para o DaVinci ler e responder? Hoje, não.**

- **Virar ISV não dá.** As regras de entrada (docs 1992/1007, de 13/11/2025) exigem, para desenvolvedor comercial, empresa da China continental com 1 ano de registro e capital de 1 milhão de RMB. Desenvolvedor próprio só pode ser empresa da China continental ou de Hong Kong. Empresa brasileira não entra pelas regras publicadas.
- **Usar um provedor que já tem o plugin:** não achei nenhum que anuncie isso em público. Duoke, BigSeller, eDesk, ChannelReply, UpSeller, Olist e Magis5 não têm chat da AliExpress. Custo: desconhecido.
- **Mesmo com provedor:** o plugin roda dentro da página da AliExpress e as mensagens ficam no servidor do provedor. O DaVinci só receberia algo se o provedor oferecesse webhook ou API para nós, e nenhum oferece.
- **Vale para loja local do Brasil? Não confirmado.** A tela não exclui loja local. A aba de plugin aparece conforme um sinal do servidor da AliExpress.
- **O que você pode checar em 1 minuto:** abra o chat da Vita logado e veja se aparece a aba "Plugin"/"插件" ou a tela "场景设置 / 我订购的插件" com "前往订购". Se aparecer, me mande o print com a lista de provedores. Esse é o caminho concreto.

**3) Caminho alternativo (sem API)**

- **Leitor na aba do AdsPower:** um perfil do AdsPower fica com o chat da Vita aberto (gsp.aliexpress.com/m_apps/im-chat). O leitor copia as conversas novas para o /atendimento do DaVinci, sem marcar como lida.
- **Resposta:** o DaVinci manda o texto e o leitor envia na própria aba (ou você responde lá). Não é oficial, é o que o 东风CRM e as extensões chinesas fazem. Quebra se a AliExpress mudar a tela e tem risco para a conta.
- **E-mail de aviso:** não confirmei se a AliExpress manda e-mail a cada mensagem nova. Se mandar, serve só como alerta, não para responder.

**4) Chamado para a AliExpress (Open Platform ou gerente de conta Brasil)**

1. O push tipo 21 "Instant Messaging Notification" e o envio pelo canal `im_open_api` podem ser liberados para o app próprio de um vendedor local do Brasil? Por qual grupo de permissão?
2. O programa de plugins de IM (智能接待/自动任务/客服面板) está disponível para lojas locais do Brasil? Quais são os requisitos para empresa não chinesa?
3. Qual é o conteúdo da notificação tipo 21? Traz o texto da mensagem, o comprador e o pedido?
4. Quais provedores de plugin de IM atendem o Brasil hoje?

**Fontes**
- FAQ 792 (APIs de mensagem desativadas, 2022): https://openservice.aliexpress.com/doc/doc.htm#/?docId=792
- Notificações, tipos 21 e 24 (07/07/2025): https://openservice.aliexpress.com/doc/doc.htm#/?docId=832
- Global APIs Overview (11/09/2026): https://openservice.aliexpress.com/doc/doc.htm#/?docId=8257
- Catálogo de APIs: https://openservice.aliexpress.com/doc/api.htm
- Regras de ISV (13/11/2025): https://openservice.aliexpress.com/doc/doc.htm#/?docId=1992 e docId=1007
- Guia "sem plano de API" (republicação): https://www.chwang.com/guide/485552107642
- Dianxiaomi ("nenhum ERP sincroniza o IM"): https://help.dianxiaomi.com/pre/getContent.htm?id=1004
- API de chat da Lazada: https://open.lazada.com/apps/doc/api?path=%2Fim%2Fsession%2Flist
- Código da tela de chat (baixado hoje): /private/tmp/claude-501/-Users-thorfinn-davinci/f7964a28-f09b-47f7-b501-195305e94997/scratchpad/ads/ali_js/
  - e0b684948e.js: `im_open_api` e a aba de plugin
  - bb8457286a.js: textos do programa de plugins
  - 23d80504fc.js: contêiner do plugin

Observação: no chat da Vita (29/09) as configurações mostram Frases Rápidas, Resposta automática, Contas do serviço do cliente, Código do cupom secreto, Palavras-chave de resposta automática, Notificações, Etiquetas estrela e Gestão do Grupo — nenhuma aba de plugin.
