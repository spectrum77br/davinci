<script setup lang="ts">
// 👍 Boa / 👎 Errou numa sugestão da IA (Atendimento, 28/09/2026) — o gesto do
// teste em OBSERVAÇÃO: o Duoke responde, o DaVinci mostra o que a IA teria
// dito, e a equipe diz se estava boa. O 👎 pede o certo (a correção é o que
// ensina a IA). Uma avaliação por sugestão: avaliar de novo troca a anterior
// (`POST /rascunhos/{id}/avaliacao` aceita pendente, substituída e bloqueada).
import { Check, Loader2, Pencil, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { CHAVE_CORRECOES, erroDaApi, type AvaliacaoIa } from '~/components/AtendimentoPlataforma.vue'

// Fase de observação (07/10/2026): quem só lê (o /me sem `atendimento_mexe`)
// também dá 👍/👎, mas não troca a nota que OUTRA pessoa deu — a API recusa
// (409 `avaliacao_de_outra_pessoa`). Essa nota vem marcada (`de_outra_pessoa`)
// e aqui fica só o selo, sem os botões.

const props = withDefaults(defineProps<{
  sugestaoId: string
  avaliacao?: AvaliacaoIa | null
  canEdit: boolean
  // Texto do botão 👍 ("Boa" no painel; "Boa" também no cartão comparativo).
  rotuloBoa?: string
}>(), { avaliacao: null, rotuloBoa: 'Boa' })
const emit = defineEmits<{ (e: 'avaliada', a: AvaliacaoIa): void }>()

const { api } = useApi()
const toasts = useToasts()
const auth = useAuthStore()

// Mesmo limite do backend (AvaliacaoIn.correcao).
const MAX_CORRECAO = 4000

// A correção em curso mora na conversa, por id de sugestão (CHAVE_CORRECOES):
// quando o Duoke responde, a sugestão sai do painel e volta no cartão "A IA
// teria respondido" — outro componente —, e o texto digitado não pode sumir.
// Fora da conversa (sem provide) fica só aqui, como antes.
const caderno = inject(CHAVE_CORRECOES, null)

// O que está salvo (vem da API; depois de salvar, o que a pessoa mandou —
// o próximo tique da conversa traz o mesmo do servidor).
const salva = ref<AvaliacaoIa | null>(props.avaliacao ?? null)
const emCurso = caderno?.get(props.sugestaoId)
const abrindo = ref(emCurso?.aberto ?? false)
const correcao = ref(emCurso ? emCurso.texto : props.avaliacao?.correcao || '')
const salvando = ref(false)
// A API recusou: outra pessoa avaliou enquanto esta tela estava aberta (a
// próxima atualização da conversa traz a nota dela).
const recusadaPorOutra = ref(false)
const soLe = computed(() => auth.user?.atendimento_mexe !== true)
const deOutraPessoa = computed(() => soLe.value && (recusadaPorOutra.value || (!!salva.value?.nota && salva.value?.de_outra_pessoa === true)))
const podeAvaliar = computed(() => props.canEdit && !deOutraPessoa.value)

// Guarda a cada tecla (e ao abrir/fechar a caixa). Sem nada a guardar — caixa
// fechada e texto igual ao salvo —, apaga: senão a sugestão já avaliada
// voltaria com a caixa aberta.
function lembrar() {
  if (!caderno) return
  const t = correcao.value
  if (abrindo.value || (t.trim() && t !== (salva.value?.correcao || ''))) {
    caderno.set(props.sugestaoId, { texto: t, aberto: abrindo.value })
  } else {
    caderno.delete(props.sugestaoId)
  }
}
watch([correcao, abrindo], lembrar)
// Outra sugestão no mesmo lugar (o painel troca a da vez): a correção da
// anterior já está no caderno; esta começa com a dela, se houver.
watch(() => props.sugestaoId, (id) => {
  salva.value = props.avaliacao ?? null
  recusadaPorOutra.value = false
  const g = caderno?.get(id)
  abrindo.value = g?.aberto ?? false
  correcao.value = g ? g.texto : props.avaliacao?.correcao || ''
})
// A avaliação salva mudou no servidor: acompanha, sem passar por cima do que
// a pessoa está escrevendo.
watch(() => [props.avaliacao?.nota, props.avaliacao?.correcao, props.avaliacao?.de_outra_pessoa] as const, () => {
  if (salvando.value) return
  salva.value = props.avaliacao ?? null
  if (salva.value?.nota) recusadaPorOutra.value = false
  if (!abrindo.value && !caderno?.has(props.sugestaoId)) correcao.value = props.avaliacao?.correcao || ''
})

async function avaliar(nota: 'ok' | 'erro') {
  if (!podeAvaliar.value || salvando.value) return
  if (nota === 'erro' && !correcao.value.trim()) {
    abrindo.value = true
    return
  }
  const id = props.sugestaoId
  const texto = correcao.value.trim()
  salvando.value = true
  try {
    const body = nota === 'ok' ? { nota } : { nota, correcao: texto }
    await api(`/api/atendimento/rascunhos/${encodeURIComponent(id)}/avaliacao`, { method: 'POST', body })
    // Salvou: a correção em curso daquela sugestão acabou, mesmo que a tela
    // já mostre outra.
    caderno?.delete(id)
    if (props.sugestaoId !== id) return
    const a: AvaliacaoIa = { nota, correcao: nota === 'erro' ? texto : null }
    salva.value = a
    correcao.value = a.correcao || ''
    abrindo.value = false
    emit('avaliada', a)
    toasts.success(nota === 'ok' ? 'Anotado: a sugestão estava boa' : 'Correção guardada', 'A IA aprende com isso.')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui salvar a avaliação')
    if (e?.data?.detail?.code === 'avaliacao_de_outra_pessoa' && props.sugestaoId === id) {
      // Não é erro de quem clicou: a nota da outra pessoa fica. O texto da
      // correção continua no caderno (não se perde).
      recusadaPorOutra.value = true
      abrindo.value = false
      toasts.info(er.texto)
    } else {
      toasts.error(er.texto, er.motivos)
    }
  } finally {
    salvando.value = false
  }
}
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex flex-wrap items-center gap-1.5 text-xs">
      <template v-if="salva?.nota === 'ok'">
        <span class="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 font-medium text-emerald-700 dark:text-emerald-300"><ThumbsUp class="size-3.5" /> avaliada: boa</span>
      </template>
      <template v-else-if="salva?.nota === 'erro'">
        <span class="inline-flex items-center gap-1 rounded-full bg-red-500/15 px-2 py-0.5 font-medium text-red-700 dark:text-red-300"><ThumbsDown class="size-3.5" /> avaliada: errou</span>
      </template>
      <span v-if="canEdit && deOutraPessoa" class="text-muted-foreground" data-avaliada-por-outra>{{ salva?.nota ? 'por outra pessoa' : 'já avaliada por outra pessoa' }}</span>
      <template v-if="podeAvaliar">
        <button
          v-if="salva?.nota !== 'ok'"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5 text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300"
          title="a sugestão estava boa — dava para mandar"
          :disabled="salvando"
          @click="avaliar('ok')"
        >
          <Loader2 v-if="salvando && !abrindo" class="size-3.5 animate-spin" /><ThumbsUp v-else class="size-3.5" /> {{ rotuloBoa }}
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5 text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
          :title="salva?.nota === 'erro' ? 'mudar a correção' : 'a sugestão errou — dizer o que era o certo'"
          :disabled="salvando"
          @click="abrindo = !abrindo"
        >
          <Pencil v-if="salva?.nota === 'erro'" class="size-3.5" /><ThumbsDown v-else class="size-3.5" /> {{ salva?.nota === 'erro' ? 'Mudar correção' : 'Errou' }}
        </button>
      </template>
    </div>
    <div v-if="salva?.nota === 'erro' && salva.correcao && !abrindo" class="whitespace-pre-wrap break-words rounded-md border border-red-300/50 bg-red-50/60 px-2 py-1 text-xs dark:border-red-800/50 dark:bg-red-900/15">
      <span class="font-medium">Correção:</span> {{ salva.correcao }}
    </div>
    <div v-if="abrindo && podeAvaliar" class="flex flex-wrap items-end gap-1.5">
      <textarea
        v-model="correcao"
        rows="2"
        :maxlength="MAX_CORRECAO"
        class="min-w-[220px] flex-1 rounded-md border bg-background px-2 py-1 text-xs"
        placeholder="o que estava errado — e como seria o certo?"
        aria-label="o que a sugestão errou e como seria o certo"
      />
      <Button size="sm" class="h-7 text-xs" :disabled="!correcao.trim() || salvando" @click="avaliar('erro')">
        <Loader2 v-if="salvando" class="mr-1 size-3.5 animate-spin" /><Check v-else class="mr-1 size-3.5" /> salvar correção
      </Button>
    </div>
  </div>
</template>
