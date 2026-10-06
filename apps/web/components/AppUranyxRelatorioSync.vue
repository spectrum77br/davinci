<script setup lang="ts">
// App Uranyx: o relatório da última cópia do catálogo do site
// (GET /admin/catalogo/sincronizacao, também devolvido pelo "Sincronizar").
// Números da cópia + o que precisa de alguém: avisos da cópia, conflitos,
// linhas do site que o app não conhece, produtos do site com formato
// inválido (ficaram de fora), SKUs do site que o DaVinci nunca viu e a
// conferência no DaVinci que não rodou (aí a lista de SKUs não diz nada).
import { AlertTriangle, CheckCircle2 } from 'lucide-vue-next'
import { dataHora, pendenciasRelatorio, seloRelatorio, type RelatorioSync } from '~/lib/appUranyx'

const props = withDefaults(defineProps<{ relatorio: RelatorioSync | null; carregando?: boolean; soPendencias?: boolean }>(), {
  carregando: false,
  soPendencias: false,
})

// Cópia que nunca rodou: selo neutro, sem números nem a caixa vermelha.
const selo = computed(() => seloRelatorio(props.relatorio))
const rodou = computed(() => selo.value !== 'nunca_rodou')

const numeros = computed(() => {
  const r = props.relatorio
  if (!r) return []
  return [
    { label: 'Produtos no site', valor: r.produtos_site },
    { label: 'Criados', valor: r.criados },
    { label: 'Atualizados', valor: r.atualizados },
    { label: 'Fora do site', valor: r.fora_do_site },
    { label: 'SKUs ligados', valor: r.skus_ligados },
  ]
})

const pendencias = computed(() => pendenciasRelatorio(props.relatorio))
</script>

<template>
  <section class="rounded-xl border bg-card p-4 space-y-3">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-sm font-semibold">Última cópia do site</h2>
      <template v-if="relatorio && rodou">
        <span v-if="selo === 'ok'" class="pill-success"><CheckCircle2 class="size-3" /> deu certo</span>
        <span v-else class="pill-danger"><AlertTriangle class="size-3" /> falhou</span>
        <span class="text-xs text-muted-foreground">{{ dataHora(relatorio.quando) }}</span>
      </template>
      <span v-else-if="carregando && !relatorio" class="text-xs text-muted-foreground">carregando…</span>
      <template v-else>
        <span class="pill-muted">ainda não rodou</span>
        <span class="text-xs text-muted-foreground">{{ relatorio?.erro || 'ou a cópia está desligada na API do app' }}</span>
      </template>
    </div>

    <template v-if="relatorio && rodou">
      <p v-if="relatorio.erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
        {{ relatorio.erro }}
      </p>

      <dl v-if="!soPendencias" class="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <div v-for="n in numeros" :key="n.label" class="rounded-lg border px-3 py-2">
          <dt class="text-[10px] uppercase tracking-wider text-muted-foreground">{{ n.label }}</dt>
          <dd class="text-lg font-semibold tabular-nums">{{ n.valor }}</dd>
        </div>
      </dl>

      <p v-if="relatorio.ok && pendencias === 0" class="text-sm text-muted-foreground">Nada para conferir nesta cópia.</p>

      <ul v-if="relatorio.avisos.length" class="space-y-0.5 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
        <li v-for="(a, i) in relatorio.avisos" :key="i" class="flex gap-1.5">
          <AlertTriangle class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> <span>{{ a }}</span>
        </li>
      </ul>

      <div v-if="relatorio.conflitos.length" class="space-y-1">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
          Conflitos ({{ relatorio.conflitos.length }})
        </h3>
        <p class="text-xs text-muted-foreground">
          O site põe o código num produto, mas ele já está ligado (à mão) a outro. A cópia não mudou nada: decida no produto.
        </p>
        <ul class="divide-y rounded-md border text-sm">
          <li v-for="c in relatorio.conflitos" :key="c.codigo_base" class="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-1.5">
            <code class="font-mono text-xs">{{ c.codigo_base }}</code>
            <span class="text-muted-foreground">no site:</span> <span>{{ c.produto_site }}</span>
            <span class="text-muted-foreground">· ligado a:</span> <span class="font-medium">{{ c.ligado_a }}</span>
          </li>
        </ul>
      </div>

      <div v-if="relatorio.linhas_desconhecidas.length" class="space-y-1">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
          Linhas do site que o app não conhece ({{ relatorio.linhas_desconhecidas.length }})
        </h3>
        <p class="text-xs text-muted-foreground">
          Os produtos dessas linhas não foram criados no app (só Celulares, Acessórios e Eletrodomésticos viram produto).
        </p>
        <div class="flex flex-wrap gap-1.5">
          <span v-for="l in relatorio.linhas_desconhecidas" :key="l" class="pill-warning">{{ l }}</span>
        </div>
      </div>

      <div v-if="relatorio.produtos_invalidos.length" class="space-y-1">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
          Produtos do site com formato inválido ({{ relatorio.produtos_invalidos.length }})
        </h3>
        <p class="text-xs text-muted-foreground">
          Ficaram de fora desta cópia. Corrija no site: id do produto no site, ou a posição na lista quando nem o id deu
          para ler.
        </p>
        <div class="flex flex-wrap gap-1.5">
          <code v-for="(p, i) in relatorio.produtos_invalidos" :key="i" class="pill-warning font-mono">{{ p }}</code>
        </div>
      </div>

      <div v-if="!relatorio.conferiu_davinci" class="space-y-1">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
          SKUs do site × DaVinci: não conferido
        </h3>
        <p class="text-xs text-muted-foreground">
          A conferência no DaVinci não rodou nesta cópia: não dá para saber se há SKU do site que o DaVinci não conhece.
          Rode a cópia de novo mais tarde.
        </p>
      </div>

      <div v-if="relatorio.skus_site_sem_davinci.length" class="space-y-1">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          SKUs do site que o DaVinci não conhece ({{ relatorio.skus_site_sem_davinci.length }})
        </h3>
        <p class="text-xs text-muted-foreground">
          Não estão nos produtos nem nos pedidos do DaVinci. Pode ser erro de digitação no site ou produto que ainda não vendeu.
        </p>
        <div class="flex flex-wrap gap-1.5">
          <code v-for="s in relatorio.skus_site_sem_davinci" :key="s" class="pill-muted font-mono">{{ s }}</code>
        </div>
      </div>
    </template>
  </section>
</template>
