<script lang="ts">
// Rótulo + quadradinho de cor por plataforma, usado nas duas abas de
// Ouvidoria › Robôs. Fica aqui (e não em lib/) porque só a Ouvidoria usa o
// código curto ("ML") e a cor — as outras telas mostram o nome que vem do
// banco. Exportado pro `<select>` de plataforma da página reusar os nomes.
export type PlataformaInfo = { nome: string; curto: string; cor: string }

export const PLATAFORMAS: Record<string, PlataformaInfo> = {
  ml: { nome: 'Mercado Livre', curto: 'ML', cor: 'bg-yellow-400 ring-1 ring-yellow-500/70' },
  shopee: { nome: 'Shopee', curto: 'Shopee', cor: 'bg-orange-600' },
  tiktok: { nome: 'TikTok', curto: 'TikTok', cor: 'bg-black dark:bg-white' },
  amazon: { nome: 'Amazon', curto: 'Amazon', cor: 'bg-amber-500' },
  magalu: { nome: 'Magalu', curto: 'Magalu', cor: 'bg-sky-500' },
  bling: { nome: 'Bling', curto: 'Bling', cor: 'bg-emerald-500' },
  interno: { nome: 'interno', curto: 'interno', cor: 'bg-gray-400 dark:bg-gray-500' },
}

// Plataforma desconhecida (código novo no backend antes da tela saber dele):
// mostra o código cru com um quadradinho cinza em vez de quebrar.
export function plataformaInfo(codigo: string | null | undefined): PlataformaInfo {
  const cod = (codigo || '').trim().toLowerCase()
  return PLATAFORMAS[cod] ?? { nome: cod || '—', curto: cod || '—', cor: 'bg-gray-400 dark:bg-gray-500' }
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  codigo: string | null | undefined
  // "ML" em vez de "Mercado Livre" — pra coluna Plataformas da aba Robôs,
  // onde cabem 4 numa célula.
  curto?: boolean
  // Com borda (chip), como na aba Robôs; sem borda é texto corrido (aba
  // Ocorrências).
  caixa?: boolean
}>(), { curto: false, caixa: false })

const info = computed(() => plataformaInfo(props.codigo))
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 whitespace-nowrap"
    :class="caixa ? 'rounded border px-1.5 py-px text-[11px]' : ''"
    :title="info.nome"
  >
    <i class="inline-block size-2 shrink-0 rounded-sm" :class="info.cor" />
    {{ curto ? info.curto : info.nome }}
  </span>
</template>
