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
