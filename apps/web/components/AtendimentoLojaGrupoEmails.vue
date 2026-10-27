<script lang="ts">
// A CAIXA de uma pasta embaixo de uma loja, ABERTA (27/10/2026) — a lista
// curta dos e-mails dela na barra de lojas da Caixa (AtendimentoLojas). O
// Eduardo, 27/10: as pastas Problemas, Envios, Devolução e Informativo embaixo
// de cada loja, "opção de encolher ou abrir para cada pasta", só com os e-mails
// daquela loja. Montada SÓ com a caixa aberta (a lista é carregada aqui, na
// hora: a barra abre rápido com os números de uma leitura só). Os últimos
// `LIMITE_CURTO` e-mails (GET /api/atendimento/email/lojas/emails — todo
// e-mail da pasta, também o que a ponte segurou como segurança); o clique num
// e-mail abre a lista da loja na Caixa com ele (ou a conversa dele); "ver
// todos" abre a lista inteira. Vazia: o PORQUÊ (`motivos`, da barra).
import { fmtDataHora } from '~/components/AtendimentoPlataforma.vue'
import type { EmailDaCaixa, EscolhaEmail } from '~/components/AtendimentoLojas.vue'

export const LIMITE_CURTO = 5

// O site (27/10/2026): `site` no lugar da loja, e o grupo é a caixa (sac | admin).
export function urlDosEmailsDaCaixa(e: Pick<EscolhaEmail, 'integration_id' | 'store_info_id' | 'grupo' | 'site'>, o: { antesDe?: string | null; limite?: number } = {}): string | null {
  const q = new URLSearchParams({ grupo: e.grupo })
  if (e.site) q.set('site', e.site)
  else if (e.integration_id) q.set('integration_id', e.integration_id)
  else if (e.store_info_id) q.set('store_info_id', e.store_info_id)
  else return null
  if (o.limite) q.set('limite', String(o.limite))
  if (o.antesDe) q.set('antes_de', o.antesDe)
  return `/api/atendimento/email/lojas/emails?${q.toString()}`
}

// O que a linha do e-mail diz além do assunto (curto: a coluna tem 196 px).
export function marcasDoEmail(e: Pick<EmailDaCaixa, 'seguranca' | 'conversa_id' | 'aguardando_resposta' | 'estado'>): string[] {
  const marcas: string[] = []
  if (e.seguranca) marcas.push('código')
  if (e.conversa_id && e.aguardando_resposta) marcas.push('falta responder')
  else if (e.conversa_id) marcas.push('conversa')
  return marcas
}
</script>

<script setup lang="ts">
import { Loader2 } from 'lucide-vue-next'

const props = defineProps<{
  escolha: EscolhaEmail
  // O número da caixa (os e-mails dos últimos dias): "ver todos" quando há mais.
  total?: number
  // O porquê da caixa vazia (a barra calcula: `motivosDaCaixaVazia`).
  motivos?: string[]
  // O polling da página: relê a lista curta.
  tique?: number
}>()
const emit = defineEmits<{
  (e: 'abrir', email: EmailDaCaixa): void
  (e: 'verTodos'): void
}>()

const { api } = useApi()
const itens = ref<EmailDaCaixa[]>([])
const carregando = ref(false)
const lido = ref(false)
const erro = ref(false)
let geracao = 0

async function carregar() {
  const endereco = urlDosEmailsDaCaixa(props.escolha, { limite: LIMITE_CURTO })
  if (!endereco) {
    lido.value = true
    return
  }
  const g = ++geracao
  carregando.value = true
  try {
    const r = await api<{ itens: EmailDaCaixa[] }>(endereco)
    if (g !== geracao) return
    itens.value = Array.isArray(r?.itens) ? r.itens.slice(0, LIMITE_CURTO) : []
    erro.value = false
  } catch {
    if (g === geracao && !itens.value.length) erro.value = true
  } finally {
    if (g === geracao) {
      carregando.value = false
      lido.value = true
    }
  }
}
onMounted(() => { void carregar() })
watch(() => props.tique, () => { if (!carregando.value) void carregar() })
watch(() => [props.escolha.grupo, props.escolha.integration_id, props.escolha.store_info_id, props.escolha.site || ''].join('|'), () => { void carregar() })

const mais = computed(() => Math.max(Number(props.total) || 0, itens.value.length) > itens.value.length)
</script>

<template>
  <div class="mb-0.5 ml-3 space-y-px border-l border-border/60 pl-1" :data-caixa-emails="escolha.grupo">
    <div v-if="!lido && carregando" class="flex justify-center py-1.5"><Loader2 class="size-3 animate-spin text-muted-foreground" /></div>
    <p v-else-if="erro" class="px-1.5 py-1 text-[11px] text-red-700 dark:text-red-300" role="alert">Não consegui carregar os e-mails.</p>
    <div v-else-if="!itens.length" class="space-y-0.5 px-1.5 py-1 text-[11px] leading-snug text-muted-foreground" data-caixa-vazia>
      <p v-for="(m, i) in motivos || []" :key="i">{{ m }}</p>
    </div>
    <template v-else>
      <button
        v-for="e in itens"
        :key="e.id"
        type="button"
        class="block w-full rounded px-1.5 py-0.5 text-left text-[11px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :title="`${e.assunto || '(sem assunto)'}\n${e.de || ''}\n${fmtDataHora(e.recebido_em)}`"
        :data-email-da-caixa="e.id"
        @click="emit('abrir', e)"
      >
        <span class="block truncate text-foreground">{{ e.assunto || '(sem assunto)' }}</span>
        <span class="flex items-center gap-1 text-muted-foreground">
          <span class="min-w-0 flex-1 truncate">{{ fmtDataHora(e.recebido_em) }}</span>
          <span v-for="m in marcasDoEmail(e)" :key="m" class="shrink-0 rounded px-1 text-[10px]" :class="m === 'falta responder' ? 'bg-red-500/15 text-red-700 dark:text-red-300' : 'bg-muted'">{{ m }}</span>
        </span>
      </button>
      <button
        v-if="mais"
        type="button"
        class="w-full rounded px-1.5 py-0.5 text-left text-[11px] text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-ver-todos
        @click="emit('verTodos')"
      >ver todos ({{ total }})</button>
    </template>
  </div>
</template>
