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
        Última atualização: 24 de junho de 2026
      </p>

      <div class="mt-8 space-y-8 text-sm leading-relaxed">
        <section>
          <h2 class="text-lg font-semibold">1. Quem somos</h2>
          <p class="mt-2">
            O DaVinci é um sistema interno de gestão (ERP) para vendedores em marketplaces,
            operado por
            <!-- PREENCHER --><strong>[RAZÃO SOCIAL]</strong>,
            CNPJ
            <!-- PREENCHER --><strong>[CNPJ]</strong>,
            acessível em app.hadken.com. Este sistema centraliza pedidos, estoque, anúncios e
            financeiro de múltiplos canais de venda.
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
            <!-- PREENCHER --><strong>[E-MAIL DE CONTATO]</strong>.
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
            <!-- PREENCHER --><strong>[NOME/E-MAIL DO ENCARREGADO]</strong>,
            e-mail
            <!-- PREENCHER --><strong>[E-MAIL DE CONTATO]</strong>.
          </p>
        </section>
      </div>
    </div>
  </div>
</template>
