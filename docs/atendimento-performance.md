# Carregamento do Atendimento

## Diagnóstico de 19/10/2026

A Caixa e o E-mail já usam paginação por cursor, com 50 itens por página.
A abertura das caixas de e-mail não transfere todos os corpos: eles são
buscados quando uma mensagem é aberta. Acrescentar outra paginação não
resolveria os gargalos encontrados.

Na entrada, o `setup` da página esperava resumo, lista, modelos de resposta
e contagens do e-mail antes de liberar a navegação. O cliente também
restaurava os filtros depois da primeira lista, gerando uma segunda busca.
O JavaScript da página incluía antecipadamente todas as abas secundárias.

O `/resumo` fazia duas agregações sobre as mesmas conversas e buscava os
nomes de cada loja individualmente. Em uma amostra de produção, foram 113
consultas e 3,12 segundos. A resolução em lote e o reaproveitamento dos
totais por loja reduziram essa amostra para 14 consultas e 1,38 segundo.
A lista Shopee de 50 conversas passou de 29 para 7 consultas.

No E-mail, a autorização das conversas vinculadas convertia a chave UUID
da mensagem original para texto. Isso impedia o uso do índice da chave
primária e repetia varreduras da tabela. Converter a referência JSON
canônica para UUID, dentro de um `CASE` protegido, permite usar o índice.
Uma comparação de 32 conversas, com o JIT padrão do banco, passou de
881–1.188 para 41–87 milissegundos em duas execuções; referências inválidas
continuam sem corresponder a um original.

Esses tempos são amostras, sujeitos à carga do servidor e ao cache do
PostgreSQL. As comparações antigas/novas usaram a mesma transação
`REPEATABLE READ READ ONLY`, conferindo o conteúdo completo para usuários
administradores e restritos. Não houve importação, alteração de mensagens
ou mudança das regras de acesso para produzir esses resultados.

## Comportamento da página

A estrutura da tela aparece sem esperar as consultas. A lista chega de
forma independente dos contadores. Os filtros salvos são restaurados
antes da primeira consulta; Caixa e Caixa Humano só são consultadas quando
essas abas estão abertas. Os modelos de resposta são buscados quando uma
conversa precisa deles.

As abas secundárias usam componentes carregados sob demanda. No E-mail,
as consultas e a atualização periódica das Caixas param quando a pessoa
está em Filas ou Administração.

No build de produção local, o arquivo principal da página passou de
880.804 para 527.139 bytes, uma redução de 40,2%. As seis abas secundárias
ficaram em importações dinâmicas; esse número não inclui os arquivos que
serão baixados quando cada uma dessas abas for aberta.

Respostas antigas de resumo e contagens não sobrescrevem pedidos mais
novos. A conciliação entre lista e detalhe preserva uma leitura que tenha
sido registrada enquanto a primeira lista ainda estava em trânsito. Os
testes cobrem esse caso, filtros salvos, entrada direta no E-mail e
carregamento independente dos contadores.

Os nomes de lojas continuam usando cache restrito à sessão do banco. Não
há cache global de autorização ou de contadores que atrase as leituras.

## Ajuste complementar de 19/10/2026 — publicação aprovada

A verificação posterior encontrou nove execuções simultâneas da conferência
automática de frete, com o PostgreSQL consumindo aproximadamente 3,7 dos
quatro núcleos do servidor. Nos registros de acesso da mesma janela, a
contagem de e-mails teve mediana de 3,2 segundos e um máximo de 173 segundos.
Isso indica disputa por capacidade; não demonstra que seja a única causa
de lentidão da tela.

O hook de frete usava a view completa de 20 dias, filtrando o número apenas
na saída. A migration `0418_refunds_freight_for_pedido` gera uma função a
partir dessa mesma definição. O pedido é restringido antes do cálculo de
preços, mantendo os dois limites internos de 20 dias. As chaves de preço
preservam todos os registros recentes com os IDs do pedido, inclusive
quando dados legados têm números ou lojas diferentes para o mesmo ID.
Não foi usada a função antiga sem janela: filtrar só sua saída poderia
alterar a multiplicidade dos itens.

A fórmula permanece igual: soma somente diferenças positivas por item,
usa a regra específica da Shopee e cria Logística a partir de R$ 5. A
conferência diária de 90 dias continua igual; registros existentes não são
atualizados. Um semáforo permite uma consulta do hook por processo e libera
o permit ao terminar, falhar ou cancelar. As APIs continuam concorrentes.
Não há bloqueio global de transação: os lotes de Shopee e TikTok mantêm
transações entre pedidos, tornando esse bloqueio perigoso. Um savepoint
preserva a transação financeira diante de falha SQL da conferência.

O controle de repetição existente (`WHERE NOT EXISTS` e bloqueio por pedido
no worker financeiro) foi preservado. O semáforo não é uma garantia de
unicidade entre processos ou entre o hook e o backfill; esta alteração não
introduz uma nova restrição de unicidade nos registros manuais.

Na interface, a página fornece suas contagens a E-mail → Filas. A montagem,
o intervalo de 30 segundos e a volta de uma conversa deixam de emitir uma
segunda consulta idêntica. Atualização manual e ações continuam buscando
dados atuais. Permissões, escolhas de seção e chegada de e-mails a cada
cinco segundos permanecem iguais; não há cache compartilhado entre usuários.

### Verificação local

- PostgreSQL 18.4 local, banco de teste próprio e schemas isolados. Nenhum
  pedido, mensagem ou reembolso de produção foi alterado pelos testes.
- 123 testes de frete, persistência financeira, reembolsos, margem e
  conciliações Shopee/TikTok/Amazon passaram. Incluem R$ 4,99/5/5,01, soma
  de itens, valores ausentes, fronteiras de 20/90 dias, registros manuais,
  repetição, cancelamento e falha SQL sem perda do financeiro. Duas
  transações reais do worker para o mesmo ID foram executadas: a segunda
  aguardou o bloqueio já existente, ambas concluíram e ficou um único
  reembolso com os valores e eventos corretos. Dois testes
  antigos de condições especiais receberam `situacao=all` para testar suas
  datas fixas sem depender da janela móvel de triagem. O código original
  apresentava as mesmas falhas de envelhecimento das fixtures. A rodada
  final foi repetida com Python 3.12, sobre `f5939749`, incluindo a alteração
  concorrente de conciliação recebida antes da publicação.
- Aplicação, reaplicação e reversão da migration testadas; estrutura
  inesperada da view faz a migration falhar, sem gerar SQL silenciosamente
  diferente. A árvore de migrations tem uma única ponta.
- 33 suítes de interface do Atendimento passaram; build de produção passou.
  A verificação geral de tipos ainda apresenta os mesmos nove diagnósticos
  de uma cópia limpa de `366c61c9`, nenhum nos arquivos alterados. Não se
  considera o typecheck geral aprovado.
- Uma segunda comparação usou o SQL integral vigente da view, obtido por
  leitura de metadados, em tabelas locais com dados fictícios. `EXCEPT ALL`
  nos dois sentidos encontrou zero diferenças, inclusive com IDs/números
  compartilhados, loja antiga, datas limítrofes, aspas e acentos.

No benchmark local, acrescentaram-se 4.000 pedidos fictícios não relacionados
e 200 produtos de preço. Cada consulta teve limite de 30 segundos. As três
execuções anteriores levaram 969,491/973,332/1.053,874 ms; as três novas,
8,533/8,191/8,092 ms. As medianas foram **973,332 → 8,191 ms**. As buscas
internas nos produtos de preço caíram de 4.007 para três. As linhas e o
prejuízo de R$ 32 do cenário fictício permaneceram idênticos. Esse ganho é
local, não uma previsão do tempo da página em produção.

Os logs e planos desta validação estão em
`/tmp/davinci-financial-equivalence-20261019/`,
`/tmp/davinci-atendimento-backend-regression-20261019.log` e
`/tmp/davinci-atendimento-web-build-20261019.log`.

### Publicação e reversão

Publicação autorizada pelo usuário em 19/10/2026.

Sincronizar com `origin/main` antes do commit, preservando trabalhos de
outras pessoas. A migration é aditiva e deve ser aplicada com a imagem nova
antes de ativar os processos que usam a função. Ela não recalcula nem
altera reembolsos existentes. Atualizar também o worker financeiro
(`worker_financials`), além de API, web e worker padrão: atualizar somente
o trio deixaria o principal consumidor ainda executando a consulta antiga.
Manter os dois arquivos de Compose explícitos no procedimento já adotado.

Após a publicação, comparar tempos reais e carga do banco e conferir a
continuidade das filas e dos registros de frete, sem gerar mensagens ou
reembolsos artificiais. Para reverter, restaurar primeiro o código anterior
em todos os processos afetados; a função adicional pode permanecer ou ser
removida posteriormente pela reversão da migration. Não remover a função
enquanto houver processo executando a versão nova.
