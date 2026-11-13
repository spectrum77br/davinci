<script lang="ts">
import type { TipoRegra } from '~/components/AtendimentoPlataforma.vue'

// O formulário da regra do Manual do agente (nova e edição usam o mesmo). Desde 13/11/2026 a
// tela só mostra as regras POR ASSUNTO — as que o agente lê (`consultar_manual`): o tipo fica
// fixo em `categoria` e não aparece. `categoria` vazio = sem assunto (o agente não lê); a regra
// nova pede o assunto.
export type FormRegra = {
  quando: string
  faca: string
  plataforma: string
  canal: string
  tipo: TipoRegra
  categoria: string
  prioridade: number
}
// A recusa 409 `regra_conflitante`, já traduzida: a frase e a regra que já
// existe (`numero` = o nº dela na lista, para a pessoa achar).
export type ConflitoForm = {
  texto: string
  regra: { id: string; quando: string; faca: string } | null
  numero: number | null
}
</script>

<script setup lang="ts">
// Formulário do Manual do agente (antes "Manual da IA", 28/09/2026): o QUANDO → FAÇA, o
// ASSUNTO (a lista oficial do GET /categorias), onde vale (plataforma/caixa) e a PRIORIDADE
// (menor = o agente lê antes, dentro do assunto). Duas regras ativas para o mesmo
// assunto/plataforma/caixa se contradizem — a API recusa a segunda (409 `regra_conflitante`)
// e o formulário mostra qual é a outra, com o atalho para ela, em vez de um erro solto.
import { Check, Loader2, TriangleAlert, X } from 'lucide-vue-next'
import {
  PLATAFORMAS_COM_CANAL,
  canaisDa,
  categoriaLabel,
  type Categoria,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  categorias: Categoria[]
  salvando: boolean
  conflito: ConflitoForm | null
  // Edição mostra "salvar" com a regra já existente; nova, com o placeholder.
  edicao?: boolean
}>()
const emit = defineEmits<{
  (e: 'salvar'): void
  (e: 'cancelar'): void
  (e: 'verRegra', id: string): void
  (e: 'editarRegra', id: string): void
}>()
const form = defineModel<FormRegra>({ required: true })

// A regra nova precisa do assunto (sem ele, o agente não a lê); na edição, a que já não tinha
// continua salvável (corrigir o texto não pode exigir escolher o assunto).
const ok = computed(() => !!form.value.quando.trim() && !!form.value.faca.trim() && (!!form.value.categoria || !!props.edicao))

function aoMudarPlataforma() {
  if (!canaisDa(form.value.plataforma).some((c) => c.value === form.value.canal)) form.value.canal = ''
}
// Assunto que não está mais na lista ativa (a regra antiga guardou um que
// saiu do manual): continua como opção, senão o select mostraria "sem assunto" e
// salvar trocaria o assunto sem a pessoa ver. Sem mexer nele, o salvar nem
// manda o assunto (o PATCH da edição leva só o que mudou — AtendimentoManual),
// e a correção de texto passa mesmo com o assunto fora da lista.
const opcoesCategoria = computed(() => {
  const lista = props.categorias.filter((c) => c.ativa !== false)
  const atual = form.value.categoria
  if (atual && !lista.some((c) => c.id === atual)) return [...lista, { id: atual, nome: `${categoriaLabel(atual)} (fora da lista)`, so_humano: false }]
  return lista
})
function verOutra() {
  const id = props.conflito?.regra?.id
  if (id) emit('verRegra', id)
}
function editarOutra() {
  const id = props.conflito?.regra?.id
  if (id) emit('editarRegra', id)
}
function aoMudarPrioridade(e: Event) {
  const n = Number.parseInt((e.target as HTMLInputElement).value, 10)
  form.value.prioridade = Number.isFinite(n) ? Math.min(9999, Math.max(0, n)) : 100
}
</script>

<template>
  <div class="space-y-2">
    <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-[minmax(220px,1fr)_minmax(200px,1fr)_110px]">
      <label class="block space-y-1">
        <span class="block text-[11px] font-medium text-muted-foreground">Assunto</span>
        <select v-model="form.categoria" class="h-9 w-full rounded-md border bg-background px-2 text-sm" aria-label="assunto da regra" data-assunto-da-regra>
          <option value="" :disabled="!edicao">{{ edicao ? 'sem assunto (o agente não lê)' : 'escolha o assunto…' }}</option>
          <option v-for="c in opcoesCategoria" :key="c.id" :value="c.id">{{ c.nome }}</option>
        </select>
      </label>
      <div class="space-y-1">
        <span class="block text-[11px] font-medium text-muted-foreground">Vale para</span>
        <div class="flex gap-1.5">
          <select v-model="form.plataforma" class="h-9 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm" aria-label="vale para qual plataforma" @change="aoMudarPlataforma">
            <option value="">todas as lojas</option>
            <option v-for="p in PLATAFORMAS_COM_CANAL" :key="p.value" :value="p.value">{{ p.nome }}</option>
          </select>
          <select v-if="canaisDa(form.plataforma).length > 1" v-model="form.canal" class="h-9 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm" aria-label="caixa">
            <option value="">todas as caixas</option>
            <option v-for="c in canaisDa(form.plataforma)" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
        </div>
      </div>
      <label class="block space-y-1">
        <span class="block text-[11px] font-medium text-muted-foreground" title="dentro do assunto, o agente lê primeiro a de menor número">Prioridade</span>
        <input
          type="number"
          min="0"
          max="9999"
          step="1"
          inputmode="numeric"
          class="h-9 w-full rounded-md border bg-background px-2 text-sm tabular-nums"
          :value="form.prioridade"
          title="menor número = o agente lê antes, dentro do assunto (padrão 100)"
          @change="aoMudarPrioridade"
        />
      </label>
    </div>

    <div class="grid gap-2 lg:grid-cols-2">
      <label class="block space-y-1">
        <span class="block text-[11px] font-medium text-muted-foreground">Quando acontecer…</span>
        <textarea
          v-model="form.quando"
          rows="3"
          maxlength="2000"
          class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          :placeholder="edicao ? '' : 'ex.: o comprador perguntar se o produto é original'"
        />
      </label>
      <label class="block space-y-1">
        <span class="block text-[11px] font-medium text-muted-foreground">Faça…</span>
        <textarea
          v-model="form.faca"
          rows="3"
          maxlength="4000"
          class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          :placeholder="edicao ? '' : 'ex.: dizer que todos os produtos são originais, com nota fiscal, e agradecer a pergunta'"
        />
      </label>
    </div>
    <p class="text-[11px] text-muted-foreground">
      Escreva como falaria com uma pessoa. <code class="rounded bg-muted px-1">{numero_pedido}</code>, <code class="rounded bg-muted px-1">{rastreio}</code> e as outras
      lacunas o agente preenche com o dado do pedido.
    </p>

    <!-- 409 regra_conflitante: qual é a outra regra, e o atalho para ela -->
    <div v-if="conflito" class="space-y-1 rounded-md border border-red-500/50 bg-red-500/10 px-2.5 py-2 text-xs text-red-700 dark:text-red-300" role="alert">
      <div class="flex items-start gap-1.5 font-medium">
        <TriangleAlert class="mt-px size-3.5 shrink-0" />
        <span>{{ conflito.texto }}</span>
      </div>
      <div class="pl-5">
        Duas regras ativas para o mesmo assunto, plataforma e caixa se contradizem (o agente não sabe qual seguir). Mude o assunto, a plataforma ou a caixa desta — ou desative a outra antes.
      </div>
      <div v-if="conflito.regra" class="rounded border border-red-500/30 bg-background/70 px-2 py-1 text-foreground">
        <span v-if="conflito.numero" class="mr-1 font-semibold tabular-nums text-red-700 dark:text-red-300">#{{ conflito.numero }}</span>
        <span class="text-[10px] font-semibold uppercase text-muted-foreground">Quando </span>{{ conflito.regra.quando }}
        <span class="text-[10px] font-semibold uppercase text-muted-foreground"> → Faça </span>{{ conflito.regra.faca }}
      </div>
      <div v-if="conflito.regra" class="flex flex-wrap gap-2">
        <button type="button" class="underline" @click="verOutra">ver a outra regra</button>
        <button type="button" class="underline" @click="editarOutra">editar a outra regra</button>
      </div>
    </div>

    <div class="flex items-center justify-end gap-1.5">
      <Button size="sm" variant="ghost" @click="emit('cancelar')"><X class="mr-1 size-4" /> cancelar</Button>
      <Button size="sm" :disabled="salvando || !ok" @click="emit('salvar')">
        <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin" /><Check v-else class="mr-1.5 size-4" /> salvar
      </Button>
    </div>
  </div>
</template>
