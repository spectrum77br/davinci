<script lang="ts">
// Nota interna do Atendimento (01/10/2026, item 3 do Comunicador): recado da
// equipe para a equipe, em AMARELO, "só a equipe vê". Nunca é enviada ao
// comprador, não conta como resposta (a conversa não sai da fila), não entra
// na pendência e a IA não lê. No backend: `tipo='nota'`, `origem=
// 'davinci_nota'`, autor `equipe` (services/atendimento/painel.criar_nota).
//
// Um componente, dois usos:
//   <AtendimentoNota :mensagem="m" />                    o balão na linha do tempo
//   <AtendimentoNota caixa :conversa-id @criada="..." /> a caixa para escrever
import type { Mensagem } from '~/components/AtendimentoPlataforma.vue'

// O mesmo teto do backend (painel.NOTA_MAX_CARACTERES).
export const NOTA_MAX = 4000

// A nota sendo escrita, por conversa, enquanto a página está aberta: trocar
// de conversa (ou voltar para "Responder") no meio da nota não perde o texto.
// Fora do componente porque a caixa é desmontada ao trocar de aba.
const rascunhosNota = new Map<string, string>()

// A mensagem é nota interna? Pelo tipo OU pela origem (como `constantes.e_nota`).
export function eNota(m: Pick<Mensagem, 'tipo' | 'origem'> | null | undefined): boolean {
  return !!m && (m.tipo === 'nota' || m.origem === 'davinci_nota')
}
</script>

<script setup lang="ts">
import { Loader2, Lock, StickyNote } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, fmtDiaHora } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  // Balão: a nota já gravada.
  mensagem?: Mensagem | null
  // Caixa: escrever uma nota nova nesta conversa.
  caixa?: boolean
  conversaId?: string | null
  canEdit?: boolean
}>(), { mensagem: null, caixa: false, conversaId: null, canEdit: false })
const emit = defineEmits<{ (e: 'criada', m: Mensagem): void }>()

const { api } = useApi()
const toasts = useToasts()

const texto = ref((props.conversaId && rascunhosNota.get(props.conversaId)) || '')
const salvando = ref(false)
watch(() => props.conversaId, (novo) => {
  texto.value = (novo && rascunhosNota.get(novo)) || ''
})
watch(texto, (t) => {
  const id = props.conversaId
  if (!id) return
  if (t.trim()) rascunhosNota.set(id, t)
  else rascunhosNota.delete(id)
})

const podeSalvar = computed(() => props.canEdit && !!props.conversaId && !!texto.value.trim() && texto.value.length <= NOTA_MAX && !salvando.value)

async function salvar() {
  const id = props.conversaId
  if (!id || !podeSalvar.value) return
  salvando.value = true
  try {
    const r = await api<{ mensagem: Mensagem }>(`/api/atendimento/conversas/${encodeURIComponent(id)}/notas`, {
      method: 'POST',
      body: { texto: texto.value.trim() },
    })
    if (props.conversaId === id) texto.value = ''
    rascunhosNota.delete(id)
    if (r?.mensagem) emit('criada', r.mensagem)
    toasts.success('Nota interna salva', 'Só a equipe vê — não foi enviada ao comprador.')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui salvar a nota')
    toasts.error(er.texto, er.motivos)
  } finally {
    salvando.value = false
  }
}
function aoTeclar(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    void salvar()
  }
}
</script>

<template>
  <!-- balão da nota na linha do tempo -->
  <div v-if="!caixa && mensagem" class="flex justify-center">
    <div
      class="w-full max-w-[85%] rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-amber-950 shadow-sm md:max-w-[75%] dark:border-amber-700/60 dark:bg-amber-900/25 dark:text-amber-100"
      :title="`nota interna · ${fmtDataHora(mensagem.enviada_em)}`"
    >
      <div class="mb-0.5 flex flex-wrap items-center gap-1 text-[11px] font-medium text-amber-800 dark:text-amber-300">
        <StickyNote class="size-3.5 shrink-0" aria-hidden="true" />
        <span>Nota interna<template v-if="mensagem.autor_nome"> · {{ mensagem.autor_nome }}</template></span>
        <span class="inline-flex items-center gap-0.5 rounded bg-amber-200/70 px-1 py-px text-[10px] dark:bg-amber-800/50"><Lock class="size-2.5" aria-hidden="true" /> só a equipe vê</span>
        <span class="ml-auto font-normal text-amber-700/80 dark:text-amber-300/80">{{ fmtDiaHora(mensagem.enviada_em) }}</span>
      </div>
      <div class="whitespace-pre-wrap break-words text-sm leading-relaxed">{{ mensagem.texto }}</div>
    </div>
  </div>

  <!-- caixa para escrever a nota -->
  <div v-else-if="caixa" class="space-y-1.5 rounded-md border border-amber-300 bg-amber-50/80 p-2 dark:border-amber-700/60 dark:bg-amber-900/20">
    <div class="flex items-center gap-1.5 text-[11px] leading-4 text-amber-900 dark:text-amber-200">
      <Lock class="size-3.5 shrink-0" aria-hidden="true" />
      <span><span class="font-semibold">Nota interna — só a equipe vê.</span> Não vai para o comprador, não conta como resposta e a IA não lê.</span>
    </div>
    <textarea
      v-model="texto"
      rows="3"
      :maxlength="NOTA_MAX"
      :disabled="!canEdit || salvando"
      class="block max-h-[40vh] min-h-[68px] w-full resize-y rounded-md border border-amber-300 bg-background px-2.5 py-2 text-sm [field-sizing:content] focus:outline-none focus:ring-1 focus:ring-amber-500 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-700/60"
      :placeholder="canEdit ? 'Escreva a nota para a equipe… (Ctrl+Enter salva)' : 'Falta a permissão de editar o Atendimento para escrever notas.'"
      aria-label="nota interna (só a equipe vê)"
      @keydown="aoTeclar"
    />
    <div class="flex items-center gap-2">
      <span class="text-[11px] tabular-nums" :class="texto.length > NOTA_MAX * 0.9 ? 'text-amber-800 dark:text-amber-300' : 'text-muted-foreground'">{{ texto.length }}/{{ NOTA_MAX }}</span>
      <Button size="sm" class="ml-auto h-8 bg-amber-600 text-white hover:bg-amber-700" :disabled="!podeSalvar" title="salvar a nota (Ctrl+Enter) — não envia nada ao comprador" @click="salvar">
        <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin" />
        <StickyNote v-else class="mr-1.5 size-4" />
        Salvar nota
      </Button>
    </div>
  </div>
</template>
