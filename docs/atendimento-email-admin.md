# Administração dos e-mails do Atendimento

A aba **E-mail → Administração** fica disponível para administradores que
podem operar o Atendimento. Cadastrar a funcionalidade não altera os acessos
existentes: uma regra só entra em vigor quando o administrador a salva.

## Acesso das pessoas

Escolha uma caixa e uma pessoa. Administradores continuam podendo administrar
todas as caixas e não recebem restrições individuais de leitura.

- **Manter o acesso atual:** mantém as permissões de caixa e de equipe que a
  pessoa já possui, aplicando as restrições escolhidas abaixo. Não concede
  acesso novo à caixa inteira.
- **Somente selecionadas:** concede leitura dos e-mails das lojas selecionadas
  na Central. Uma seleção vazia não libera nenhuma mensagem. Não concede
  permissão para responder nem amplia a equipe comercial no Atendimento.
- **Bloquear:** impede a leitura dos e-mails dessa caixa pela pessoa.

Uma loja bloqueada prevalece sobre uma loja permitida. O administrador pode
limitar as categorias **Informativo**, **Chamado** e **Venda**. Uma lista de
pedidos não vazia limita ainda mais a seleção: cada pedido precisa corresponder
à sua loja, na sua plataforma. Número de pedido ou identidade do comprador,
isoladamente, não concede acesso a outro e-mail.

As regras valem também para e-mails antigos, listas e contagens, leitura direta,
anexos e o botão **E-mails desta venda**. E-mails sem informação suficiente para
comprovar uma seleção restrita não são liberados por aproximação.

Uma conversa pode guardar e-mails de vendas diferentes. Se parte dela estiver
bloqueada ou na lixeira, a conversa completa fica oculta para evitar expor o
histórico ou a prévia do item indisponível. Os e-mails individualmente permitidos
continuam acessíveis na Central e na respectiva venda.

O formulário mostra quem alterou a regra e quando. Se outro administrador
salvar a mesma regra durante a edição, a gravação é recusada e o formulário
preserva a edição local para conferência.

## Pastas e exibição

A seção reúne as regras de pastas já existentes e o atalho para configurar a
caixa, seus endereços, a ponte para o Atendimento e os leitores da caixa inteira.
Uma regra de pasta controla o que entra no Atendimento; uma regra de acesso
controla o que cada pessoa consegue ler no DaVinci.

## Lixeira do DaVinci

No detalhe da mensagem em **Caixas**, um administrador pode escolher
**Excluir do DaVinci**, conferir a mensagem selecionada e informar um motivo.
Essa exclusão é reversível em **Administração → Lixeira do DaVinci**.

O original e as pastas do Tuta não são alterados. A sincronização e o
reprocessamento não desfazem uma exclusão local. O conteúdo cifrado permanece
preservado para a restauração, que continua respeitando as regras de acesso.
Uma resposta pendente ou incerta precisa ser conferida antes da exclusão.

As alterações de acesso, exclusões e restaurações registram autor, data e
identificadores para auditoria, sem copiar assunto ou corpo para os registros.
