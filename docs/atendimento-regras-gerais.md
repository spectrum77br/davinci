# Padrões de atendimento — 19/10/2026

Os exemplos revisados na Kia definem o comportamento geral de todas as lojas
ativadas, nos canais compatíveis da Shopee, Mercado Livre e TikTok Shop. Ativar
uma loja continua sendo uma escolha na tela: esta alteração não muda modos,
permissões nem categorias autorizadas. Observar e copiloto mantêm sugestões;
somente automático pode enviar, com a categoria permitida e as travas usuais.

| Consulta | Padrão aprovado |
| --- | --- |
| Tamanho da pulseira | Usar a ficha própria ou aprovação do mesmo produto com vínculo vivo no catálogo; sem identificação, pedir modelo/anúncio. |
| Como é o modelo, pedido genérico de foto | Orientar as fotos do próprio anúncio e perguntar qual detalhe deseja ver; não prometer foto nova. |
| Previsão de entrega/envio | Com pedido próprio pago e em preparação, seguir o prazo da plataforma, sem inventar data. |
| Origem do envio | Informar apenas origem comprovada para a loja e pedido/anúncio; equivalência de produto não comprova origem. |
| Apenas cartão de produto/pedido, já acolhido pelo robô | Marcar como processado no DaVinci, sem repetir a saudação, quando o adaptador reconhece o cartão estruturado. |
| Novo ou recondicionado | Confirmar a condição só com título próprio atualizado; caso contrário, pedir modelo/anúncio. |
| Opção de nota fiscal do menu, sem pedido | Informar que acompanha NF; seleção ambígua de dois e cinco também pede detalhar a dúvida, sem inventar solicitação de arquivo ou cobertura de garantia. |

O formato acompanha a plataforma: pergunta pública do Mercado Livre não pede
card nem expõe dados de pedido; pós-venda usa o próprio pedido. Menus só são
interpretados quando o histórico contém o menu real. Cartões de Shopee e TikTok
são reconhecidos pelos respectivos formatos; foto, áudio e arquivo não viram
cartões por inferência. Validação de tamanho e conteúdo permanece por canal.

A aprovação da pulseira padrão dos produtos Apple Watch auditados pode acompanhar
o mesmo produto em outra loja, por `product_links` da própria integração,
plataforma e anúncio/variação. Título parecido não comprova identidade. Vínculo
morto, variação desconhecida, ficha divergente ou outro produto não transfere o
fato. A aprovação de origem São Paulo do anúncio Kia `22299473628` permanece
restrita àquela loja/anúncio e ao pedido próprio em preparação.

Continuam válidas a Caixa Humano, atribuição, pausa manual, os trinta minutos
após resposta humana e as categorias só para pessoa. Urgência, reclamação,
garantia concreta, devolução, cancelamento e pedido de material novo seguem suas
regras. O menu para falar com atendente conserva a dúvida original; uma escolha
sensível de cancelamento/endereço/garantia não é apagada por regra de produto.

As respostas literais exigem o conteúdo exato aprovado da regra geral. Edição,
desativação ou conflito no Manual revoga o atalho. A regra de saudação também autoriza o processamento
sem demanda, sem acrescentar instrução geral a todos os assuntos. A auditoria registra a regra, loja e anúncio
usados. Nova mensagem do cliente reabre o atendimento normalmente.

Sugestões antigas não avaliadas só são recuperadas quando toda a dúvida atual
corresponde a uma resposta literal aprovada. A reavaliação consulta a ficha
própria como a geração normal. Sugestões pedidas pela tela ou avaliadas por
pessoa não são retomadas automaticamente. A manutenção também recalcula
conversas já respondidas por pessoa, sem reenviar saudação.

Fonte versionada: `apps/api/scripts/atendimento_manual_base.json`. A atualização
do banco altera apenas as cinco regras gerais indicadas
e desativa as cinco especializações antigas da Kia, sob a trava do Manual e com
verificação dos textos anteriores. Não reimporta o restante das regras da equipe.
