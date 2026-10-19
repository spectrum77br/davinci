# Notificações e leitura do comunicador

## Contrato esperado

O número da loja deve representar mensagens não lidas. Durante a migração,
o objetivo é corresponder às mesmas mensagens exibidas no Duoke: cinco lá
devem ser as mesmas cinco aqui, respeitado o atraso de sincronização. Após
a substituição, o DaVinci precisa manter esse estado sem depender do Duoke.

Abrir uma conversa visível registra até a mensagem efetivamente exibida.
Uma mensagem que chega depois volta a contar. Ler não responde ao comprador
e não elimina a fila da IA, o rascunho nem a Caixa Humano. As mesmas regras
valem nos modos Observar e de envio; apenas o envio de recibo à plataforma
continua condicionado às permissões e ao modo da loja.

## Resultado da auditoria de 19/10/2026

O campo legado `nao_lidas_duoke` não é importado do Duoke. Ele reconstrói o
histórico local, descontando respostas humanas e leituras no DaVinci. O
cálculo ainda usa o corte de 14/10/2026 e uma janela móvel de sete dias.
Isso não é um controle de leitura completo para a operação independente.

Uma consulta somente de leitura em produção encontrou 345 notificações em
293 conversas Shopee. Dessas mensagens, 340 pertenciam a conversas cujo
`unread_count` da Shopee era zero e 344 não tinham leitura local. A soma do
SQL foi conferida com a função Python em todas as conversas positivas e com
o `/resumo`. O frontend exibe esse resultado sem duplicar lojas ou canais.

O Duoke mantém seu próprio estado de leitura. O envio de menus e campanhas
pode zerar o `unread_count` da Shopee, mas manter mensagens não lidas no Duoke.
O `last_read_message_id` do GET da Shopee também pode apontar para uma
mensagem automática. O endpoint não devolve horário de leitura que permita
distinguir esses estados. Portanto esses campos, isoladamente, não podem
substituir o contador do Duoke.

Não foi encontrada uma integração existente, documentação de API ou
exportação acessível nessa conta que forneça os recibos de leitura do
Duoke. O usuário também não dispõe de uma fonte conhecida. Enquanto a
equipe continuar lendo nos dois sistemas, a igualdade contínua depende de
receber esses eventos. Ajustar janela, copiar apenas totais ou zerar o
histórico não resolve essa ausência de informação.

## Correção entregue pela auditoria

A resposta de um POST `/visto` ou `/lida` referente a um snapshot antigo é
ignorada quando o detalhe já exibe outro `visto_ate`. Isso impede uma
resposta atrasada de regredir o contador, o visto e o estado atual da
conversa. O servidor já mantém o visto monotônico com `GREATEST`.

Os textos da tela distinguem mensagens não lidas de conversas que faltam
responder. Os testes cobrem abertura em ambos os modos, aba escondida,
mensagem posterior ao visto, preservação da fila/IA e respostas fora de
ordem. Essa correção não modifica a régua do backend nem resolve, por si,
a diferença entre os totais dos dois sistemas.

## Condições para concluir a migração

Uma importação inicial precisa de um snapshot completo e consistente, por
plataforma, loja e conversa, incluindo quais mensagens estavam não lidas
e até qual mensagem a fonte foi observada. Ausência em uma página parcial
não significa zero. O vínculo precisa usar identificadores verificáveis;
nomes de loja e totais agregados não são suficientes.

Depois desse corte, leituras no DaVinci passam a ser a autoridade local.
O histórico importado não pode reaparecer como notificação e mensagens
novas não podem ser consumidas por um snapshot anterior. Só então os cortes
históricos fixo e móvel podem ser retirados, com testes de equivalência
SQL/Python e de sincronização atrasada.

Para operar nos dois sistemas em paralelo com os mesmos números, também é
necessário um fluxo contínuo e autorizado de recibos do Duoke. Não há esse
fluxo implementado nesta entrega. Nenhum histórico foi zerado para forçar
igualdade e nenhuma configuração de IA ou modo de loja foi alterada.
