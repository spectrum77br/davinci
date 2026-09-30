<script setup lang="ts">
// Aba "Notas fixas e tomadores" da Emissão de Serviço: o que se cadastra uma
// vez e sai todo mês. Dois cartões, um embaixo do outro. Os formulários são
// gavetas montadas pela página (tela.abrirModelo / tela.abrirTomador).
</script>

<template>
  <section class="space-y-5">
    <p class="text-sm text-muted-foreground">
      Notas fixas são as notas que saem todo mês. Cada uma liga a empresa que emite a quem recebe (o tomador).
    </p>
    <NfseModelos />
    <NfseTomadores />
  </section>
</template>
