# Assinaturas de e-mail

Em `/email-padroes`, o operador cadastra uma assinatura por marca e canal.
Texto, inclusão de logo/dados e situação ativa são independentes entre canais.
Logo, empresa e site vêm de Marcas. WhatsApp e e-mail SAC vêm de Redes Sociais.
Os modelos antigos de mensagem foram preservados, mas não são editados nesta tela.

## Uso manual no Tuta

1. Abra a marca/canal, ajuste os campos e atualize a prévia.
2. Clique em **Copiar assinatura**. A cópia leva HTML formatado, imagens incorporadas
   e links clicáveis, além de texto puro para aplicativos sem formatação.
3. No Tuta pelo navegador, vá a **Configurações → E-mail → Assinatura de e-mail →
   lápis → Personalizada**. Substitua a assinatura anterior pela cópia com Ctrl+V/Cmd+V.
4. Salve no Tuta e confira a apresentação ao compor uma mensagem e uma resposta.

A cópia usa a prévia atual e fica indisponível enquanto ela estiver desatualizada.
Ela não salva o cadastro nem envia e-mails. Para persistir o rascunho no DaVinci,
use **Salvar assinatura**. Depois de alterações, copie e salve novamente no Tuta.

O Tuta aceita imagens em base64 na assinatura, sem depender de URLs locais.
O cadastro de assinatura do Tuta é associado ao usuário: endereços adicionais e
caixas compartilhadas acessadas pelo mesmo usuário podem usar a mesma assinatura.
Não presuma seleção automática de marca/canal pelo endereço remetente.

Referências: [guia do Tuta](https://tuta.com/blog/how-to-email-signature),
[editor de assinatura](https://github.com/tutao/tutanota/blob/master/src/applications/mail-app/settings/EditSignatureDialog.ts),
[seleção da assinatura do usuário](https://github.com/tutao/tutanota/blob/master/src/applications/mail-app/mail/signature/Signature.ts).

## Integração com o robô remetente

Depois de montar a mensagem, consulte `GET /api/email-assinaturas/{marca_id}/{contexto}/render`
com uma sessão autenticada com permissão `email_padroes:view`. Os canais são os mesmos
de `GET /api/email-assinaturas/grid` (por exemplo, `sac`, `ml` e `shopee`).

- Se houver assinatura ativa, acrescente `html` dentro do corpo HTML existente,
  antes de `</body>`, e `text` ao final da versão em texto puro, uma única vez.
- Adicione cada item de `inline_images` como anexo inline: decodifique `base64`,
  use `mime` como tipo e `content_id` como Content-ID. O HTML já referencia esses IDs.
- `ativo: false` retorna rodapé vazio. `404 assinatura_not_found` significa que
  a combinação ainda não foi cadastrada. O robô deve definir como tratar esse caso.
- Assunto, destinatário, envio e tentativas continuam sendo responsabilidade do robô.

O endpoint monta o rodapé e não envia e-mails. Nenhum robô externo foi conectado.
