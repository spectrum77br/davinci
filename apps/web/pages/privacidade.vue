<script setup lang="ts">
definePageMeta({ layout: false })

useHead({
  title: 'Política de Privacidade — DaVinci',
})
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <div class="max-w-3xl mx-auto px-6 py-10">
      <NuxtLink to="/login" class="text-sm text-muted-foreground hover:text-foreground">
        ← Voltar ao login
      </NuxtLink>

      <h1 class="mt-6 text-3xl font-semibold tracking-tight">
        Política de Privacidade — DaVinci
      </h1>
      <p class="mt-2 text-sm text-muted-foreground">
        Última atualização: 27 de outubro de 2026
      </p>

      <div class="mt-8 space-y-8 text-sm leading-relaxed">
        <section>
          <h2 class="text-lg font-semibold">1. Quem somos</h2>
          <p class="mt-2">
            O DaVinci é o sistema interno de gestão (ERP) e de atendimento usado pelas lojas que
            vendem em marketplaces e atendem os seus clientes, acessível em app.hadken.com. Ele
            centraliza pedidos, estoque, anúncios, financeiro e as conversas de atendimento de
            múltiplos canais de venda. Cada loja é a responsável (controladora) pelos dados dos
            seus próprios clientes; o DaVinci é a ferramenta com que ela os trata.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">2. Dados que coletamos e acessamos</h2>
          <ul class="mt-2 list-disc pl-5 space-y-3">
            <li>
              <strong>Dados da sua conta no DaVinci:</strong> nome, e-mail, senha (armazenada de
              forma cifrada/hash) e permissões de acesso.
            </li>
            <li>
              <strong>Dados acessados via integrações autorizadas (OAuth):</strong> ao conectar
              um marketplace ou o Bling (Mercado Livre, Shopee, Amazon, TikTok Shop, Temu,
              Magalu, Bling), acessamos via APIs oficiais: pedidos, produtos/anúncios, níveis de
              estoque, preços, dados de envio e dados financeiros (taxas, repasses, faturamento).
              Não coletamos nem armazenamos suas senhas desses marketplaces — usamos tokens de
              acesso (OAuth) concedidos por você.
            </li>
            <li>
              <strong>Dados de clientes finais presentes nos pedidos:</strong> nome, endereço de
              entrega e contato, tratados exclusivamente para a operação logística e fiscal do
              pedido.
            </li>
            <li>
              <strong>Conversas de atendimento pelo WhatsApp:</strong> quando você fala com uma
              das lojas pelo WhatsApp, recebemos pela plataforma oficial da Meta (WhatsApp
              Business Platform) o seu número de telefone, o nome do seu perfil, as mensagens e os
              arquivos que você enviar (fotos, áudios, vídeos e documentos) e os avisos de entrega
              e leitura das respostas.
            </li>
          </ul>
        </section>

        <section>
          <h2 class="text-lg font-semibold">2.1 Atendimento pelo WhatsApp</h2>
          <ul class="mt-2 list-disc pl-5 space-y-3">
            <li>
              As mensagens passam pelos servidores da Meta, que as entrega ao DaVinci; o
              tratamento feito pela Meta segue a política de privacidade do WhatsApp.
            </li>
            <li>
              Usamos as conversas só para responder ao seu atendimento (dúvidas de compra,
              entrega, troca e devolução). Nunca pedimos pelo WhatsApp senha, número de cartão,
              dados bancários, CPF ou RG.
            </li>
            <li>
              As fotos, áudios, vídeos e documentos recebidos ficam guardados por até
              <strong>180 dias</strong> e depois são apagados; o texto das conversas fica pelo
              tempo necessário ao atendimento e às obrigações legais da loja. Os avisos técnicos
              que a Meta manda junto com cada mensagem ficam até <strong>30 dias</strong>.
            </li>
            <li>
              Depois de um pedido de exclusão, os dados somem do DaVinci na hora; as cópias de
              segurança (backup) que ainda os tenham são apagadas no ciclo normal do backup,
              sem nunca voltarem ao sistema.
            </li>
            <li>
              Você pode pedir a exclusão da sua conversa e do seu contato a qualquer momento —
              veja
              <NuxtLink to="/exclusao-de-dados" class="underline">exclusão de dados</NuxtLink>.
            </li>
          </ul>
        </section>

        <section>
          <h2 class="text-lg font-semibold">3. Como usamos os dados</h2>
          <p class="mt-2">
            Exclusivamente para operar o ERP — centralizar e exibir pedidos, estoque, faturamento
            e relatórios; sincronizar estoque/preços; e dar suporte à operação do vendedor. Não
            usamos esses dados para publicidade nem os vendemos.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">4. Base legal (LGPD)</h2>
          <p class="mt-2">
            Tratamos os dados para execução de contrato com o vendedor, cumprimento de obrigações
            legais/fiscais e legítimo interesse na operação do sistema.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">5. Compartilhamento</h2>
          <p class="mt-2">
            Não vendemos nem cedemos dados a terceiros para fins de marketing. Compartilhamos
            apenas: (a) com provedores de infraestrutura que hospedam o sistema, sob obrigação de
            confidencialidade; (b) com as próprias APIs dos marketplaces e do Bling, na medida
            necessária à operação que você autorizou; e (c) quando exigido por lei ou ordem
            judicial.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">6. Tokens e segurança</h2>
          <p class="mt-2">
            Os tokens de acesso OAuth são armazenados de forma segura; a comunicação ocorre por
            HTTPS; o acesso interno é controlado por papéis e permissões.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">7. Retenção</h2>
          <p class="mt-2">
            Mantemos os dados enquanto a integração e a conta estiverem ativas e pelo prazo
            exigido por obrigações legais/fiscais. Após isso, são eliminados ou anonimizados.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">8. Revogação de acesso e exclusão de dados</h2>
          <p class="mt-2">
            Você pode revogar a autorização OAuth a qualquer momento no painel do respectivo
            marketplace e/ou solicitar a exclusão dos seus dados enviando e-mail para
            <a href="mailto:stuart18@tuta.com" class="underline">stuart18@tuta.com</a>
            (passo a passo em
            <NuxtLink to="/exclusao-de-dados" class="underline">exclusão de dados</NuxtLink>).
            Atenderemos no prazo legal.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">9. Direitos do titular (LGPD)</h2>
          <p class="mt-2">
            Você pode solicitar acesso, correção, portabilidade, exclusão e revogação de
            consentimento dos seus dados pelo e-mail de contato.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">10. Cookies</h2>
          <p class="mt-2">
            Usamos apenas cookie de sessão essencial para autenticação; não usamos cookies de
            rastreamento/publicidade.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">11. Alterações</h2>
          <p class="mt-2">
            Esta política pode ser atualizada; a data de "Última atualização" no topo indica a
            versão vigente.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">12. Contato / Encarregado (DPO)</h2>
          <p class="mt-2">
            Para qualquer assunto de privacidade e dados pessoais, escreva para
            <a href="mailto:stuart18@tuta.com" class="underline">stuart18@tuta.com</a>.
          </p>
        </section>
      </div>
    </div>
  </div>
</template>
