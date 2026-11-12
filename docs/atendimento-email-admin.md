# Administração dos e-mails do Atendimento

A caixa completa em **Atendimento → E-mail** fica disponível aos administradores.
Uma exceção explícita, `atendimento_email_leitura` com **ver**, permite consultar
mensagens, pastas e anexos somente das caixas já concedidas à pessoa. Não libera
envio, rascunhos, exclusão ou configurações. Ser responsável pela caixa ou ter
apenas permissões antigas de leitor não abre a Central. Revogar a nova permissão
fecha também os links diretos. O conector do Tuta continua sincronizando.

Desde 05/11/2026, `atendimento_email_operacao` com **ver + editar** permite
responder, compor, encaminhar, anexar arquivos e gerir os próprios rascunhos e
envios nas caixas já autorizadas. **Excluir** também libera excluir e restaurar
mensagens pela **Lixeira desta caixa**. Essa concessão não altera o papel da
pessoa, as lojas, as caixas ou o acesso às configurações e credenciais. A
permissão de leitura e as permissões antigas, sozinhas, continuam sem enviar.
O conector revalida a concessão antes de despachar cada mensagem.

## Administração: a lista de usuários (12/11/2026)

A aba **E-mail → Administração** é uma lista de usuários, no modelo das
subcontas de um ERP. Só administradores que operam o Atendimento a veem e
mexem nela. Cada linha mostra o e-mail (login), o nome, a **permissão de
caixas** (Todas as caixas, Caixas específicas ou Nenhuma caixa), o **estado**
(ligado/desligado), a data de criação, o último acesso e **Editar**.

- **Estado** liga/desliga a permissão `atendimento_email_leitura` do usuário
  (a mesma da tela de permissões), que abre as **Caixas** na aba E-mail.
  Desligar também tira a operação (`atendimento_email_operacao`). Ligar não
  concede responder nem as Filas das lojas.
- **Editar** mostra cada conta de e-mail com as pastas dela em checkbox
  (Entrada, informativo, SAC dos sites, as pastas das lojas…). Marcar a **conta
  inteira** libera todas as pastas, também as criadas depois; marcar pastas
  soltas libera só elas (`mail_leitor_pastas`). Marcar qualquer coisa de uma
  conta coloca o usuário na lista de leitores dela (a mesma do "Quem mais vê").
  "Todas as caixas" marca as contas existentes inteiras; uma conta cadastrada
  depois precisa ser marcada.
- Administrador vê todas as caixas e não é editado aqui. O dono de uma caixa
  já a vê.
- Dentro de uma caixa, quem não é administrador nem dono vê apenas os e-mails
  das lojas (loja, site, sem loja). O e-mail particular do dono (caixa "só
  aliases de loja") e os de segurança (código, senha, acesso) nunca aparecem
  para ele, nem os nomes de pastas vazias da conta. Um e-mail ainda não
  indexado fica oculto até a indexação (a cada minuto).
- O Atendimento e as lojas por pessoa não mudam por esta tela.

A antiga regra por pessoa × caixa × loja × venda (tabela `mail_acessos`) e a
lixeira geral da Administração saíram; a migration 0476 apaga a tabela, que
estava vazia, e cria `mail_leitor_pastas`. Cada inclusão ou retirada de leitor
e cada troca de pastas fica registrada em `mail_admin_eventos`.

## Lixeira do DaVinci

No detalhe da mensagem em **Caixas**, um administrador ou operador com
a permissão de exclusão pode escolher
**Excluir do DaVinci**, conferir a mensagem selecionada e informar um motivo.
Essa exclusão é reversível em **Lixeira desta caixa**.

O original e as pastas do Tuta não são alterados. A sincronização e o
reprocessamento não desfazem uma exclusão local. O conteúdo cifrado permanece
preservado para a restauração, que continua respeitando as regras de acesso.
Uma resposta pendente ou incerta precisa ser conferida antes da exclusão.

As alterações de acesso, exclusões e restaurações registram autor, data e
identificadores para auditoria, sem copiar assunto ou corpo para os registros.

## Escrever e responder

Em **Caixas**, o compositor permite escrever uma nova mensagem, responder,
responder a todos ou encaminhar. Confira o remetente autorizado, os campos
**Para**, **CC** e **CCO**, o assunto e os arquivos antes de confirmar o envio.
O destinatário em CCO não aparece nos cabeçalhos recebidos pelos outros.

É possível adicionar até **10 arquivos**, com **10 MB por arquivo** e **15 MB
no total**. Ao encaminhar, os anexos originais podem ser mantidos ou removidos;
eles também contam nesse limite. O DaVinci verifica novamente a permissão de
leitura do original antes de utilizar seus anexos.

Os **rascunhos do DaVinci** são salvos cifrados e pertencem à pessoa que os
escreveu. Não são rascunhos compartilhados nem sincronizados com a pasta de
rascunhos do Tuta. Salvar um rascunho não envia a mensagem. O envio efetivo é
feito pelo conector do Tuta, que registra a mensagem nos enviados da conta.

O histórico de envios diferencia mensagem aguardando envio, enviada, falha e
resultado incerto. Um envio incerto nunca é repetido automaticamente: primeiro
confira os enviados no Tuta. As permissões, remetentes autorizados, pausa,
modo de teste e limites da caixa continuam valendo, inclusive para CC e CCO.
O compositor livre exige acesso de operação à caixa completa; permissões
restritas a mensagens não liberam acesso aos demais remetentes ou rascunhos.
