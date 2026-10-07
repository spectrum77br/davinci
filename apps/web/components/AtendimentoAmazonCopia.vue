<script lang="ts">
// Amazon: a cópia de uma resposta dada no Seller Central que EMPATOU entre
// esta e outra conversa do mesmo comprador (08/10/2026, caso KIA do dia 07:
// "respondi pela conta e não apareceu no DaVinci"). O leitor da caixa só liga
// sozinho quando só UMA conversa casa (pelo "ID do pedido" do modelo da
// cópia ou, sem ele, pelo nome); com duas ou mais, nunca escolhe: guarda a
// cópia nas candidatas e nenhuma sai da fila. Aqui o aviso vira a escolha de 1 clique:
// "Esta foi a respondida" (a cópia entra como resposta da loja e a conversa
// sai da fila) ou "Não foi esta" (só tira o aviso daqui) — para quem mexe na
// caixa. Quem só lê vê o aviso. A marca de antes de 08/10 (sem o texto) só
// avisa, até a leitura seguinte reavaliá-la.
// Funções puras aqui em cima (testadas em tests/atendimento-amazon-copia.cjs).

export type CopiaAmazon = {
  message_id: string
  em: string
  texto?: string | null
  pedido?: string | null
  outras?: number
  pode_escolher?: boolean
}
export type ConversaComCopias = {
  id: string
  plataforma: string
  amazon_copia_a_conferir_em?: string | null
  amazon_copias_a_conferir?: CopiaAmazon[] | null
}

// As cópias a mostrar: a lista da API nova; da antiga (só a hora), um aviso
// sem escolha. Sem nada → nenhuma.
export function copiasDaConversa(c: ConversaComCopias | null | undefined): CopiaAmazon[] {
  if (!c || c.plataforma !== 'amazon') return []
  if (Array.isArray(c.amazon_copias_a_conferir)) {
    return c.amazon_copias_a_conferir.filter((x) => !!x && typeof x.message_id === 'string' && typeof x.em === 'string')
  }
  return c.amazon_copia_a_conferir_em ? [{ message_id: '', em: c.amazon_copia_a_conferir_em, pode_escolher: false }] : []
}

export function fraseDaCopia(copia: CopiaAmazon, quando: string): string {
  const inicio = `O Seller Central respondeu alguém com este nome em ${quando}`
  // A marca de antes de 08/10 não diz quantas eram: a frase de sempre.
  if (!copia.pode_escolher) return `${inicio}, mas há outra conversa com o mesmo nome e o DaVinci não sabe qual foi.`
  const outras = Number(copia.outras) || 0
  // A(s) outra(s) já disse(ram) "não foi esta": não há mais com quem empatar.
  if (outras === 0) return `${inicio}, e o DaVinci não ligou sozinho a resposta a esta conversa.`
  const onde = outras === 1 ? 'outra conversa' : `outras ${outras} conversas`
  return `${inicio}, e há ${onde} com o mesmo nome: o DaVinci não sabe qual foi.`
}

export const AVISO_FOI_ESTA = 'A cópia entra como resposta da loja nesta conversa (e ela sai da fila, se for a resposta mais nova).'
export const AVISO_NAO_FOI = 'O aviso sai desta conversa; a outra continua com a escolha.'
</script>

<script setup lang="ts">
import { Check, ExternalLink, Loader2, TriangleAlert, X } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, statusDoErro } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  conversa: ConversaComCopias
  // Quem mexe na caixa (`acesso.pode_mexer` + atendimento.edit): só ele escolhe.
  podeMexer: boolean
  linkCaso?: string | null
}>()
const emit = defineEmits<{ (e: 'recarregar'): void }>()

const { api } = useApi()
const toasts = useToasts()

// Escolhida aqui: some na hora, antes de a conversa ser relida.
const resolvidas = ref(new Set<string>())
watch(() => props.conversa.id, () => { resolvidas.value = new Set() })
const copias = computed(() => copiasDaConversa(props.conversa).filter((c) => !c.message_id || !resolvidas.value.has(c.message_id)))
const enviando = ref<string | null>(null)

async function escolher(copia: CopiaAmazon, foiEsta: boolean) {
  if (enviando.value || !copia.message_id) return
  const id = props.conversa.id
  enviando.value = `${copia.message_id}:${foiEsta ? 'sim' : 'nao'}`
  try {
    await api(`/api/atendimento/conversas/${encodeURIComponent(id)}/amazon-copia`, {
      method: 'POST',
      body: { message_id: copia.message_id, foi_esta: foiEsta },
    })
    resolvidas.value = new Set([...resolvidas.value, copia.message_id])
    toasts.success(foiEsta ? 'Marcada: esta foi a respondida no Seller Central' : 'Ok: não foi esta conversa')
  } catch (e: any) {
    if (statusDoErro(e) === 404) {
      // Outra pessoa (ou a leitura da caixa) já resolveu: relê e pronto.
      resolvidas.value = new Set([...resolvidas.value, copia.message_id])
      toasts.success('Esta cópia já tinha sido resolvida')
    } else {
      const er = erroDaApi(e, 'Não consegui marcar')
      toasts.error(er.texto, er.motivos)
    }
  } finally {
    enviando.value = null
    emit('recarregar')
  }
}
</script>

<template>
  <div
    v-if="copias.length"
    class="shrink-0 space-y-1.5 border-b border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-900 dark:text-amber-200"
    data-amazon-copia
  >
    <div v-for="c in copias" :key="c.message_id || c.em" class="space-y-1">
      <div>
        <TriangleAlert class="mr-1 inline size-3.5" />{{ fraseDaCopia(c, fmtDataHora(c.em)) }}
        <template v-if="c.pode_escolher">Esta resposta foi para esta conversa?</template>
        <template v-else>
          Confira <template v-if="linkCaso"><a :href="linkCaso" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 underline hover:text-foreground">no Seller Central<ExternalLink class="size-3" /></a></template><template v-else>no Seller Central</template>: se esta já foi respondida, marque "não precisa de resposta".
        </template>
      </div>
      <blockquote
        v-if="c.texto"
        class="line-clamp-3 whitespace-pre-line border-l-2 border-amber-500/50 pl-2 italic text-foreground/80"
        :title="c.texto"
        data-copia-texto
      >{{ c.texto }}</blockquote>
      <div v-if="c.pode_escolher" class="flex flex-wrap items-center gap-1.5">
        <template v-if="podeMexer">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-md bg-amber-600 px-2.5 font-medium text-white hover:bg-amber-700 disabled:opacity-60"
            :disabled="!!enviando"
            :title="AVISO_FOI_ESTA"
            data-copia-foi-esta
            @click="escolher(c, true)"
          >
            <Loader2 v-if="enviando === `${c.message_id}:sim`" class="size-3.5 animate-spin" />
            <Check v-else class="size-3.5" />
            Esta foi a respondida
          </button>
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-md border border-amber-600/50 bg-background px-2.5 hover:bg-muted disabled:opacity-60"
            :disabled="!!enviando"
            :title="AVISO_NAO_FOI"
            data-copia-nao-foi
            @click="escolher(c, false)"
          >
            <Loader2 v-if="enviando === `${c.message_id}:nao`" class="size-3.5 animate-spin" />
            <X v-else class="size-3.5" />
            Não foi esta
          </button>
        </template>
        <span v-else class="text-muted-foreground">(quem cuida do Atendimento escolhe qual foi)</span>
        <a
          v-if="linkCaso"
          :href="linkCaso"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-0.5 underline hover:text-foreground"
        >conferir no Seller Central<ExternalLink class="size-3" /></a>
      </div>
    </div>
  </div>
</template>
