<script setup lang="ts">
// "A IA teria respondido" (Atendimento, 28/09/2026): no teste em observação a
// equipe responde pelo Duoke e o DaVinci guarda o que a IA teria dito. Logo
// abaixo da resposta REAL, este cartão recolhível mostra a da IA — a
// comparação lado a lado — com 👍/👎 para a equipe dizer se dava para mandar.
// Recolhido por padrão (a conversa continua lendo como conversa); já avaliado
// mostra o selo no título. Abre sozinho quando há correção do 👎 em curso
// para esta sugestão (começada no painel antes de o Duoke responder): a
// pessoa encontra o texto dela aqui, não uma caixa vazia.
// Sugestão DESCARTADA (sem resposta real depois) também vem para cá, no fim
// da conversa, com o título dizendo que foi descartada — senão ela sumia e o
// painel dizia que a IA não tinha sugerido nada.
import { ChevronDown, ChevronRight, Copy, Sparkles, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { CHAVE_CORRECOES, categoriaLabel, copiar, type AvaliacaoIa, type SugestaoIa } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  sugestao: SugestaoIa
  canEdit: boolean
}>()
const emit = defineEmits<{ (e: 'avaliada', a: AvaliacaoIa): void }>()
const toasts = useToasts()

const caderno = inject(CHAVE_CORRECOES, null)
const aberto = ref(!!caderno?.get(props.sugestao.id))
const titulo = computed(() => {
  if (props.sugestao.resposta_real) return 'A IA teria respondido:'
  if (props.sugestao.status === 'descartado') return 'Sugestão da IA descartada:'
  if (props.sugestao.status === 'bloqueado') return 'Sugestão da IA barrada:'
  return 'A IA sugeriu:'
})
const nota = computed(() => props.sugestao.avaliacao?.nota || null)
const STATUS: Record<string, string> = {
  pendente: 'não enviada',
  substituido: 'a equipe respondeu por fora',
  bloqueado: 'o validador barrou',
  descartado: 'descartada',
}

async function copiarTexto() {
  const t = props.sugestao.texto
  if (!t) return
  if (await copiar(t)) toasts.success('Sugestão da IA copiada')
  else window.prompt('Sugestão da IA', t)
}
</script>

<template>
  <div class="w-full max-w-[85%] rounded-md border border-dashed border-violet-300/80 bg-violet-50/70 text-xs dark:border-violet-800/70 dark:bg-violet-900/15 md:max-w-[75%]">
    <button
      type="button"
      class="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left"
      :aria-expanded="aberto"
      @click="aberto = !aberto"
    >
      <ChevronDown v-if="aberto" class="size-3.5 shrink-0 text-violet-600 dark:text-violet-400" />
      <ChevronRight v-else class="size-3.5 shrink-0 text-violet-600 dark:text-violet-400" />
      <Sparkles class="size-3.5 shrink-0 text-violet-600 dark:text-violet-400" />
      <span class="shrink-0 font-medium text-violet-800 dark:text-violet-300">{{ titulo }}</span>
      <span v-if="!aberto" class="min-w-0 flex-1 truncate text-muted-foreground">{{ sugestao.texto || '(sem texto que pudesse sair)' }}</span>
      <span v-else class="flex-1" />
      <ThumbsUp v-if="nota === 'ok'" class="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-label="avaliada: boa" />
      <ThumbsDown v-else-if="nota === 'erro'" class="size-3.5 shrink-0 text-red-600 dark:text-red-400" aria-label="avaliada: errou" />
    </button>
    <div v-if="aberto" class="space-y-2 border-t border-dashed border-violet-300/70 px-2.5 py-2 dark:border-violet-800/60">
      <div class="flex flex-wrap items-center gap-x-1.5 text-[11px] text-muted-foreground">
        <span v-if="sugestao.categoria">{{ categoriaLabel(sugestao.categoria) }}</span>
        <span v-if="sugestao.confianca !== null && sugestao.confianca !== undefined">· {{ Math.round(sugestao.confianca * 100) }}% de confiança</span>
        <span v-if="STATUS[sugestao.status]">· {{ STATUS[sugestao.status] }}</span>
        <span v-if="sugestao.precisa_humano" class="rounded bg-amber-500/20 px-1.5 text-amber-800 dark:text-amber-300">precisa de pessoa</span>
        <!-- Barrada pelo validador não se copia (como no painel): colada no Duoke, sairia assim. -->
        <button v-if="sugestao.texto && sugestao.status !== 'bloqueado'" type="button" class="ml-auto inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-muted" title="copiar a sugestão da IA" @click="copiarTexto">
          <Copy class="size-3" /> copiar
        </button>
      </div>
      <div v-if="sugestao.texto" class="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-foreground">{{ sugestao.texto }}</div>
      <div v-else class="italic text-muted-foreground">A IA não chegou a uma resposta que pudesse sair.</div>
      <ul v-if="sugestao.validador_erros?.length" class="list-inside list-disc text-amber-800 dark:text-amber-300">
        <li v-for="(v, i) in sugestao.validador_erros" :key="i">{{ v }}</li>
      </ul>
      <AtendimentoAvaliarIa :sugestao-id="sugestao.id" :avaliacao="sugestao.avaliacao" :can-edit="canEdit" @avaliada="(a: AvaliacaoIa) => emit('avaliada', a)" />
    </div>
  </div>
</template>
