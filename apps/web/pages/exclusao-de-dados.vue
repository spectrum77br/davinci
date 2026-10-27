<script setup lang="ts">
// Página pública exigida pela Meta (URL de "instruções de exclusão de dados" do app do WhatsApp).
definePageMeta({ layout: false })

useHead({
  title: 'Exclusão de dados — DaVinci',
})
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <div class="max-w-3xl mx-auto px-6 py-10">
      <NuxtLink to="/privacidade" class="text-sm text-muted-foreground hover:text-foreground">
        ← Política de Privacidade
      </NuxtLink>

      <h1 class="mt-6 text-3xl font-semibold tracking-tight">
        Como pedir a exclusão dos seus dados
      </h1>
      <p class="mt-2 text-sm text-muted-foreground">
        Última atualização: 27 de outubro de 2026
      </p>

      <div class="mt-8 space-y-8 text-sm leading-relaxed">
        <section>
          <h2 class="text-lg font-semibold">1. Como pedir</h2>
          <p class="mt-2">
            Envie um e-mail para
            <a href="mailto:stuart18@tuta.com" class="underline">stuart18@tuta.com</a>
            com o assunto <strong>"Exclusão de dados"</strong>, informando:
          </p>
          <ul class="mt-2 list-disc pl-5 space-y-2">
            <li>
              o número de telefone com que você falou com a loja pelo WhatsApp (com DDD), e/ou
            </li>
            <li>o número do pedido no marketplace, se a conversa for sobre uma compra.</li>
          </ul>
          <p class="mt-2">
            Por segurança, podemos pedir que você confirme o pedido pelo mesmo WhatsApp com que
            falou com a loja. Nunca pedimos senha, número de cartão ou dados bancários.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">2. O que é apagado</h2>
          <ul class="mt-2 list-disc pl-5 space-y-2">
            <li>o seu contato do WhatsApp (número de telefone e nome do perfil);</li>
            <li>o texto das conversas de atendimento;</li>
            <li>as fotos, áudios, vídeos e documentos que você enviou.</li>
          </ul>
        </section>

        <section>
          <h2 class="text-lg font-semibold">3. O que pode ficar guardado</h2>
          <p class="mt-2">
            Dados que a lei obriga a guardar — por exemplo, os dados da nota fiscal e do pedido de
            uma compra — ficam pelo prazo legal e são usados só para essa finalidade.
          </p>
        </section>

        <section>
          <h2 class="text-lg font-semibold">4. Prazo</h2>
          <p class="mt-2">
            Confirmamos o recebimento do pedido e concluímos a exclusão no prazo previsto na Lei
            Geral de Proteção de Dados (LGPD). Independentemente de pedido, as fotos, áudios,
            vídeos e documentos recebidos pelo WhatsApp são apagados automaticamente depois de
            180 dias.
          </p>
        </section>
      </div>
    </div>
  </div>
</template>
