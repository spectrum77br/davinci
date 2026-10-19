# Ajustes de atendimento Kia — 19/10/2026

Os exemplos aprovados pelo responsável pela caixa viraram especializações do
Manual para o chat da Shopee. As regras gerais permanecem válidas. O código
limita os fatos à integração da Kia; a identidade da loja não depende do nome
escrito pelo cliente.

| Consulta | Resposta aprovada | Escopo |
| --- | --- | --- |
| Pulseira P/M ou M/G, ou tamanho da pulseira | A pulseira deste modelo tem tamanho padrão. | Apple Watch Series 11, anúncio `22299473628` |
| Como é o modelo, pedido genérico de foto | As fotos do anúncio mostram o modelo que será recebido; conferir as imagens do anúncio. | Uranyx Óculos Inteligentes AI, anúncio `58269229175` |
| Previsão de entrega/envio e origem | Seguir o prazo informado no próprio pedido da Shopee; se perguntado, envio de São Paulo. | Pedido pago, identificado e ainda em preparação, do anúncio `22299473628` |
| Apenas cartão de produto/pedido, já acolhido pelo robô | Marcar como processado no DaVinci, sem enviar outra mensagem. | Kia; histórico completo, sem pergunta pendente, reclamação, atribuição ou pausa |
| Pergunta genérica se é novo ou recondicionado | Confirmar novo apenas com título próprio atualizado; caso contrário, pedir modelo/card. | Kia, antes da compra; nenhuma afirmação de condição sem prova |
| Opção de nota fiscal do menu, sem pedido | Informar que acompanha NF; seleção ambígua de dois e cinco também pede detalhar a dúvida. | Kia; menu confirmado, sem pedido de arquivo nem outra pergunta concreta |

Essas aprovações não autorizam medidas inventadas, compatibilidade de pulseira,
autenticidade de produto, foto ou vídeo adicional, promessa de postagem em certa
data ou alteração da situação do pedido. Informação de pulseira divergente na
ficha ou na variação devolve a decisão ao fluxo normal.

O envio usa o retrato do mesmo pedido da conversa, pago, lido no último dia e sem
postagem ou conclusão registrada. Etiqueta e rastreio não comprovam postagem.
Urgência, prazo vencido, devolução, chamado ou outra demanda continuam sujeitos às
regras existentes. Uma compra verificada pode encerrar uma consulta de estoque
anterior, mas não apaga reclamações ou solicitações sensíveis.

O número do menu para falar com atendente conserva a dúvida original ainda sem
resposta. O menu não transforma uma pergunta sobre pulseira em garantia. Cartão
sem dúvida e bot já presente não deve criar uma nova saudação repetida.

As respostas de política exigem o conteúdo exato aprovado da regra geral e
da especialização ativa. Edição, desativação ou conflito no Manual desliga o
atalho. A regra de processamento sem demanda também exige autorização exata.
A auditoria registra a regra usada, a loja e o anúncio. Novas mensagens do
cliente voltam a abrir o atendimento normalmente.

Sugestões antigas e não avaliadas só são recuperadas quando a dúvida atual inteira
corresponde a uma resposta literal aprovada. Sugestões pedidas pela tela não são
enviadas automaticamente. A manutenção da fila também recalcula conversas já
respondidas por pessoa, evitando outra saudação sobre a mesma mensagem.

As escolhas do menu não são, por si só, um pedido de cópia da NF ou uma afirmação
de cobertura de garantia. Pedidos de documento, correção, nota não recebida ou
uma dúvida concreta sobre garantia continuam no fluxo de pessoa.

Fonte versionada: `apps/api/scripts/atendimento_manual_base.json`. Para atualizar
um banco existente, importar apenas as cinco especializações após simulação;
não reimportar todas as regras gerais, que podem ter edições posteriores da equipe.
As conversas usadas como exemplo devem ser conferidas no estado atual antes de
qualquer resposta ou processamento retroativo.
