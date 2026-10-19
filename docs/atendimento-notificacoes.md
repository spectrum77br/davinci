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

O campo legado `nao_lidas_duoke` não é importado do Duoke. Para conversas
ainda não migradas, ele reconstrói o histórico local, descontando respostas
humanas e leituras no DaVinci, com o corte de 14/10/2026 e uma janela móvel
de sete dias. A implementação nativa descrita abaixo substitui essa régua
somente após uma importação conferida.

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
Duoke. O relatório `EQUIPE 2 Chat Details`, exportado em 19/10 e fornecido
pelo usuário, contém 175 sessões de 18/10, com comprador, número da sessão,
contagens de mensagens e métricas de resposta. Suas 40 colunas não incluem
estado de leitura, IDs das mensagens não lidas nem o limite observado da
conversa. Ele não constitui um snapshot de leitura e não pode ser usado
para zerar as conversas ausentes ou inferir suas notificações. Enquanto a
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

## Migração e contador próprio

A migration `0416_notificacoes_nativas` acrescenta um perfil dedicado no
canal e três campos dedicados na conversa: corte, mensagens iniciais e
visto nativo. Eles não ficam em `dados` nem no cursor do adaptador, que são
atualizados durante o sync. Sem perfil/estado válido, a régua anterior
continua funcionando.

O estado nativo usa IDs explícitos das mensagens não lidas e o limite
conferido da conversa. Mensagens posteriores continuam contando até serem
vistas ou respondidas; não expiram após sete dias. Importação atrasada de
histórico anterior não recria notificações. IDs opacos nunca são ordenados
numericamente. Uma mensagem desconhecida no mesmo timestamp do limite
permanece contada; empate sem ordem comprovada também não permite uma
resposta externa apagar a mensagem silenciosamente.

O visto nativo é separado de `visto_em`, preservando o controle anterior.
A interface recebe a identificação da base importada e a devolve no POST
de leitura. Um pedido antigo sem essa identificação não consome os avisos
da base nova. Se a importação ocorrer com a conversa aberta, o detalhe
renova o gatilho de leitura, respeitando a visibilidade e o atraso de
abertura. Clientes anteriores a essa implementação precisam recarregar a
página.

Comprador e afiliado têm perfis separados. Novas conversas herdam o perfil
vigente, inclusive quando chegam por importação de histórico. A criação e
a aplicação usam travas compartilhada/exclusiva por canal para impedir
que uma conversa escape durante a troca. O perfil não altera modo da
loja, envio automático, regras da IA, rascunhos ou Caixa Humano.

### Ferramenta administrativa

O comando padrão só faz a prévia, em transação `READ ONLY`:

```sh
python -m scripts.atendimento_notificacoes_migrar snapshot.json --relatorio previa.json
```

Para gravar é necessário informar explicitamente o hash emitido pela
prévia:

```sh
python -m scripts.atendimento_notificacoes_migrar snapshot.json --aplicar --hash-previsto HASH_DA_PREVIA
```

O manifesto versionado precisa identificar fonte/evidência com SHA256,
conta externa, canal, escopo de comprador/afiliado, instante da captura e
limite da plataforma. Todas as conversas já conhecidas no escopo precisam
ser declaradas, inclusive as lidas, com a mensagem que ancora seu limite
e os IDs observados no mesmo timestamp. Conversa sem mensagens só pode ser
declarada vazia se o banco confirmar que não há mensagens. A soma dos IDs
não lidos precisa corresponder ao total declarado na fonte. A captura
também precisa declarar uma janela com operação pausada para ser consistente.

Conta incorreta, IDs ausentes, conversa omitida, âncora divergente e prova
incompleta recusam a importação. Uma leitura ou nova conversa entre prévia
e aplicação exige nova prévia. Repetir a mesma importação é idempotente e
preserva as leituras realizadas depois; outra geração não substitui
silenciosamente uma já aplicada.

O comando não extrai dados do Duoke. Na conta examinada, a lista visual
expõe comprador, loja, prévia e quantidade, mas não os IDs das conversas e
mensagens necessários para esse manifesto. Essa captura serve para
auditoria e não autoriza fabricar um estado inicial. A implementação pode
ser instalada sem migrar nenhuma loja; isso não significa que os números
já estejam equiparados.

A ferramenta aceita chat de Shopee/TikTok e pós-venda do ML com canal
cadastrado. As reclamações do ML, atualmente importadas com `canal_id`
nulo, continuam na régua anterior; não estão cobertas por esse importador.
Uma migração parcial não deve ser rotulada como alinhamento da plataforma
inteira.

## Condições para concluir a migração

Uma importação inicial precisa de um snapshot completo e consistente, por
plataforma, loja e conversa, incluindo quais mensagens estavam não lidas
e até qual mensagem a fonte foi observada. Ausência em uma página parcial
não significa zero. O vínculo precisa usar identificadores verificáveis;
nomes de loja e totais agregados não são suficientes.

Depois da aplicação de um corte conferido, leituras no DaVinci passam a ser a autoridade local.
O histórico importado não pode reaparecer como notificação e mensagens
novas não podem ser consumidas por um snapshot anterior. Só então os cortes
históricos fixo e móvel podem ser retirados, com testes de equivalência
SQL/Python e de sincronização atrasada.

Para operar nos dois sistemas em paralelo com os mesmos números, também é
necessário um fluxo contínuo e autorizado de recibos do Duoke. Não há esse
fluxo contínuo implementado. Nenhum histórico foi zerado para forçar
igualdade e nenhuma configuração de IA ou modo de loja foi alterada.
