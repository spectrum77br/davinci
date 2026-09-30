<script setup lang="ts">
// Esqueleto de tabela para a 1ª carga (recarregar mantém a lista na tela).
withDefaults(defineProps<{ linhas?: number; colunas?: number }>(), { linhas: 5, colunas: 4 })

const LARGURAS = ['w-3/4', 'w-1/2', 'w-2/3', 'w-1/3']
</script>

<template>
  <div class="table-card" aria-busy="true" aria-label="carregando">
    <table class="w-full">
      <thead>
        <tr>
          <th v-for="c in colunas" :key="c"><div class="h-3 w-20 rounded bg-muted" /></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="l in linhas" :key="l">
          <td v-for="c in colunas" :key="c">
            <div class="h-4 animate-pulse rounded bg-muted motion-reduce:animate-none" :class="LARGURAS[(l + c) % LARGURAS.length]" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
