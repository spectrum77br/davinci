<script setup lang="ts">
// Consórcio — planilha editável inline com auto-save (debounce 500ms).
// Toda célula vira input ao clicar; campos calculados (IMPOSTO 7%,
// DEVEDOR, FINALIZADO, CRÉDITO líquido) ficam read-only. O IMPOSTO 1% é
// calculado, mas aceita um valor digitado (0 zera).
import { computed, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro_consorcio', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.financeiro_consorcio
  return Boolean(p?.edit || p?.delete)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.financeiro_consorcio?.delete)
})

type Row = {
  id: string
  credito: number | null
  emp: string | null
  grupo: number | null
  cota: number | null
  alienacao: string | null
  nf: string | null
  parc_a_pagar: number | null
  lance: number | null
  valor_parc: number | null
  atualizado: string | null  // YYYY-MM-DD
  fundo_reserva: string | null
  obs: string | null
  imposto1_manual: number | null  // null = automático (1% do crédito)
}

const rows = ref<Row[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
// id -> timer pendente do auto-save. Permite cancelar quando o user
// digita rápido na mesma célula.
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})
// id -> campos editados que ainda não foram salvos. O PATCH leva todos: antes
// só ia o último, e editar o Crédito e logo em seguida o Lance perdia o Crédito.
const camposPendentes: Record<string, Set<keyof Row>> = {}

const ALIENACAO_OPTIONS = ['', 'pago', 'ag. repasse', 'a contemplar']

async function load() {
  loading.value = true
  errorText.value = null
  try {
    const r = await api<Row[]>('/api/financeiro/consorcio')
    // Coerce alienacao null → '' so the <select v-model> matches the
    // empty-string option ("—") explicitly. Without this, the select
    // would render the first option visually but row.alienacao would
    // stay null — and the SSR-rendered HTML for `<select :value=>` had
    // no way to encode the selected option, so on F5 the dropdown
    // appeared empty until the user re-clicked it.
    rows.value = r.map((row) => ({ ...row, alienacao: row.alienacao ?? '' }))
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
    rows.value = []
  } finally {
    aplicarFiltros()
    loading.value = false
  }
}

function scheduleSave(row: Row, field: keyof Row, value: any) {
  ;(row as any)[field] = value
  ;(camposPendentes[row.id] ??= new Set()).add(field)
  if (saveTimers[row.id]) clearTimeout(saveTimers[row.id])
  saveTimers[row.id] = setTimeout(() => {
    void persist(row)
  }, 500)
}

// Salva os campos pendentes da linha (e `field`, quando vem do select da
// alienação, que salva na hora) num PATCH só.
async function persist(row: Row, field?: keyof Row) {
  if (saveTimers[row.id]) clearTimeout(saveTimers[row.id])
  delete saveTimers[row.id]
  const campos = camposPendentes[row.id] ?? new Set<keyof Row>()
  delete camposPendentes[row.id]
  if (field) campos.add(field)
  if (campos.size === 0) return
  try {
    const body: Record<string, any> = Object.fromEntries([...campos].map((c) => [c, row[c]]))
    await api(`/api/financeiro/consorcio/${row.id}`, { method: 'PATCH', body })
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${[...campos].join(', ')}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function addRow() {
  try {
    const r = await api<Row>('/api/financeiro/consorcio', {
      method: 'POST',
      body: { emp: '', alienacao: '' },
    })
    rows.value = [r, ...rows.value]
    // A linha nova aparece mesmo com filtro ligado (ainda está em branco).
    idsVisiveis.value.add(r.id)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  }
}

async function removeRow(row: Row) {
  if (!confirm('Excluir esta linha?')) return
  try {
    await api(`/api/financeiro/consorcio/${row.id}`, { method: 'DELETE' })
    clearTimeout(saveTimers[row.id])
    delete saveTimers[row.id]
    delete camposPendentes[row.id]
    rows.value = rows.value.filter((r) => r.id !== row.id)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_delete'
  }
}

// ── Calculadas ────────────────────────────────────────────────────────
function numOrZero(v: number | null | undefined): number {
  return v == null || isNaN(Number(v)) ? 0 : Number(v)
}

function imposto7(row: Row): number | null {
  if (!row.lance || numOrZero(row.lance) <= 0) return null
  return numOrZero(row.credito) * 0.07
}
// 1% do crédito, nas mesmas cotas do 7% (com lance) — Eduardo, 03/11/2026.
function imposto1Automatico(row: Row): number | null {
  if (!row.lance || numOrZero(row.lance) <= 0) return null
  return numOrZero(row.credito) * 0.01
}
// O valor digitado (0 inclusive) vale no lugar do automático.
function imposto1(row: Row): number | null {
  if (row.imposto1_manual != null && String(row.imposto1_manual).trim() !== '') {
    return numOrZero(row.imposto1_manual)
  }
  return imposto1Automatico(row)
}
// Texto de um <input type="number">, ou undefined enquanto o número está
// incompleto ("1141." no meio da digitação: o navegador dá valor vazio). Salvar
// ali apagava a célula, o Vue reescrevia o campo sem o ponto e "500.00" virava
// 5000 (achado na revisão de 03/11; valia para todas as células de número).
function valorNumerico(e: Event): string | undefined {
  const input = e.target as HTMLInputElement
  return input.validity?.badInput ? undefined : input.value
}
// Célula do Imposto 1%: vazia = volta ao automático (null); número ≥ 0 = digitado;
// negativo ou inválido = não salva (undefined).
function valorDigitadoImposto1(texto: string): number | null | undefined {
  if (texto.trim() === '') return null
  const n = Number(texto)
  return Number.isFinite(n) && n >= 0 ? n : undefined
}
function devedor(row: Row): number {
  return numOrZero(row.parc_a_pagar) * numOrZero(row.valor_parc)
}
function finalizado(row: Row): string | null {
  if (!row.atualizado || !row.parc_a_pagar) return null
  const [y, m, d] = row.atualizado.split('-').map(Number)
  if (!y || !m || !d) return null
  const dt = new Date(y, m - 1, d)
  dt.setDate(dt.getDate() + row.parc_a_pagar * 30)
  return dt.toLocaleDateString('pt-BR')
}
function creditoLiquido(row: Row): number | null {
  if (!row.lance || numOrZero(row.lance) <= 0) return null
  return numOrZero(row.credito) - (numOrZero(row.lance) + (imposto7(row) ?? 0) + (imposto1(row) ?? 0))
}

function fmtMoney(n: number | null): string {
  if (n == null) return ''
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

const totalRows = computed(() => rows.value.length)

// ── Filtros ───────────────────────────────────────────────────────────
// "" = todas; SEM_VALOR = só as linhas com a célula vazia.
const SEM_VALOR = '__sem__'

// "Kia", "KIA" e "Kia " são a mesma empresa.
function empresaDaLinha(emp: string | null | undefined): string {
  return (emp ?? '').trim().toUpperCase()
}

type Filtros = { grupoCota: string, alienacao: string, empresa: string }

// Grupo ou cota: um número procura nos dois ("1654" acha o grupo 1654 e a
// cota 1654); "1654/566" é o grupo 1654 com a cota começando por 566.
function passaNosFiltros(row: Row, f: Filtros): boolean {
  const numeros = f.grupoCota.match(/\d+/g) ?? []
  if (numeros.length === 1) {
    const [n] = numeros
    if (![row.grupo, row.cota].some((v) => v != null && String(v).includes(n!))) return false
  } else if (numeros.length > 1) {
    if (String(row.grupo ?? '') !== numeros[0] || !String(row.cota ?? '').startsWith(numeros[1]!)) return false
  }
  if (f.alienacao) {
    const a = row.alienacao ?? ''
    if (f.alienacao === SEM_VALOR ? a !== '' : a !== f.alienacao) return false
  }
  if (f.empresa) {
    const e = empresaDaLinha(row.emp)
    if (f.empresa === SEM_VALOR ? e !== '' : e !== f.empresa) return false
  }
  return true
}

const filtroGrupoCota = ref('')
const filtroAlienacao = ref('')
const filtroEmpresa = ref('')
const filtrando = computed(() =>
  Boolean(filtroGrupoCota.value.trim() || filtroAlienacao.value || filtroEmpresa.value))

// Quem aparece é decidido quando o filtro muda (ou ao recarregar), não a cada
// tecla: trocar a EMP ou a alienação de uma linha não a faz sumir da tela.
const idsVisiveis = ref(new Set<string>())
function aplicarFiltros() {
  const f = { grupoCota: filtroGrupoCota.value, alienacao: filtroAlienacao.value, empresa: filtroEmpresa.value }
  idsVisiveis.value = new Set(rows.value.filter((r) => passaNosFiltros(r, f)).map((r) => r.id))
}
watch([filtroGrupoCota, filtroAlienacao, filtroEmpresa], aplicarFiltros)
const linhasVisiveis = computed(() => rows.value.filter((r) => idsVisiveis.value.has(r.id)))

function limparFiltros() {
  filtroGrupoCota.value = ''
  filtroAlienacao.value = ''
  filtroEmpresa.value = ''
}

// Opções vêm das linhas; a escolhida continua na lista mesmo sem linha.
const opcoesEmpresa = computed(() => {
  const nomes = new Set(rows.value.map((r) => empresaDaLinha(r.emp)))
  if (filtroEmpresa.value && filtroEmpresa.value !== SEM_VALOR) nomes.add(filtroEmpresa.value)
  return {
    nomes: [...nomes].filter(Boolean).sort((a, b) => a.localeCompare(b, 'pt-BR')),
    semEmpresa: nomes.has('') || filtroEmpresa.value === SEM_VALOR,
  }
})
const opcoesAlienacao = computed(() => {
  const valores = new Set(ALIENACAO_OPTIONS.filter(Boolean))
  for (const r of rows.value) if (r.alienacao) valores.add(r.alienacao)
  if (filtroAlienacao.value && filtroAlienacao.value !== SEM_VALOR) valores.add(filtroAlienacao.value)
  return [...valores]
})

// Depois dos filtros: o load() já aplica o filtro nas linhas que chegam.
await load()
</script>

<template>
  <div class="space-y-3 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <h1 class="text-xl font-semibold">Consórcio</h1>
        <span class="text-xs text-muted-foreground" data-contagem>
          <template v-if="filtrando">{{ linhasVisiveis.length }} de </template>{{ totalRows }} {{ totalRows === 1 ? 'linha' : 'linhas' }}
        </span>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="load"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
        Recarregar
      </button>
      <button
        v-if="canEdit"
        class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90"
        @click="addRow"
      >
        <Plus class="size-3.5" /> Nova linha
      </button>
    </div>

    <div class="flex flex-wrap items-end gap-3 text-xs">
      <label class="flex flex-col gap-1">
        <span class="text-muted-foreground">Grupo ou cota</span>
        <input
          v-model="filtroGrupoCota"
          data-filtro-grupo-cota
          inputmode="numeric"
          placeholder="ex.: 1654 ou 1654/566"
          class="border rounded px-2 py-1 text-sm bg-background h-8 w-44"
        />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-muted-foreground">Alienação</span>
        <select v-model="filtroAlienacao" data-filtro-alienacao class="border rounded px-2 py-1 text-sm bg-background h-8">
          <option value="">Todas</option>
          <option v-for="a in opcoesAlienacao" :key="a" :value="a">{{ a }}</option>
          <option :value="SEM_VALOR">Sem alienação</option>
        </select>
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-muted-foreground">Empresa</span>
        <select v-model="filtroEmpresa" data-filtro-empresa class="border rounded px-2 py-1 text-sm bg-background h-8">
          <option value="">Todas</option>
          <option v-for="e in opcoesEmpresa.nomes" :key="e" :value="e">{{ e }}</option>
          <option v-if="opcoesEmpresa.semEmpresa" :value="SEM_VALOR">Sem empresa</option>
        </select>
      </label>
      <button
        v-if="filtrando"
        data-limpar-filtros
        class="inline-flex items-center gap-1 rounded-md border px-2 h-8 text-sm hover:bg-muted"
        @click="limparFiltros"
      >
        <X class="size-3.5" /> Limpar filtros
      </button>
      <span class="ml-auto self-end text-muted-foreground" data-dica-imposto1>
        Imposto 1%: em branco calcula sozinho (1% do crédito, em cinza). Digite 0 para zerar.
      </span>
    </div>

    <div v-if="errorText" class="text-sm text-destructive">erro: {{ errorText }}</div>

    <div class="border overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <colgroup>
          <!-- Larguras explícitas: EMP/Grupo/Cota são identificadores
               curtos (até 4 chars) então recebem o mínimo confortável;
               Alienação cabe textos como "ag. Repasse" e "a contemplar"
               sem quebrar linha. As demais colunas seguem auto-fit. -->
          <col />              <!-- Crédito -->
          <col style="width: 48px" />  <!-- EMP -->
          <col style="width: 60px" />  <!-- Grupo -->
          <col style="width: 60px" />  <!-- Cota -->
          <col style="width: 140px" /> <!-- Alienação -->
        </colgroup>
        <thead>
          <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
            <th class="text-right">Crédito</th>
            <th class="text-left">EMP</th>
            <th class="text-right">Grupo</th>
            <th class="text-right">Cota</th>
            <th class="text-left">Alienação</th>
            <th class="text-left">NF</th>
            <th class="text-right">Parc a pagar</th>
            <th class="text-right">Lance</th>
            <th class="text-right">Imposto 7%</th>
            <th class="text-right" data-coluna-imposto1>Imposto 1%</th>
            <th class="text-right">Valor parc.</th>
            <th class="text-right">Devedor</th>
            <th class="text-left">Atualizado</th>
            <th class="text-left">Finalizado</th>
            <th class="text-right">Crédito líq.</th>
            <th class="text-left">Fundo reserva</th>
            <th class="text-left">Obs</th>
            <th v-if="canDelete" class="w-8"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 18 : 17" class="py-6 text-center text-muted-foreground">
              Nenhuma linha. Clique em "Nova linha" para começar.
            </td>
          </tr>
          <tr v-else-if="!loading && linhasVisiveis.length === 0">
            <td :colspan="canDelete ? 18 : 17" class="py-6 text-center text-muted-foreground">
              Nenhuma linha com esses filtros.
            </td>
          </tr>
          <tr v-for="row in linhasVisiveis" :key="row.id" class="even:bg-muted/10 hover:bg-amber-50/40 dark:hover:bg-amber-900/10">
            <td>
              <input type="number" step="0.01" class="cell-input text-right" style="min-width: 5.5rem"
                :value="row.credito ?? ''" :disabled="!canEdit"
                @input="(e) => { const v = valorNumerico(e); if (v !== undefined) scheduleSave(row, 'credito', Number(v) || null) }" />
            </td>
            <td>
              <input class="cell-input" :value="row.emp ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'emp', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" class="cell-input text-right"
                :value="row.grupo ?? ''" :disabled="!canEdit"
                @input="(e) => { const v = valorNumerico(e); if (v !== undefined) scheduleSave(row, 'grupo', Number(v) || null) }" />
            </td>
            <td>
              <input type="number" class="cell-input text-right"
                :value="row.cota ?? ''" :disabled="!canEdit"
                @input="(e) => { const v = valorNumerico(e); if (v !== undefined) scheduleSave(row, 'cota', Number(v) || null) }" />
            </td>
            <td>
              <!-- v-model (instead of :value+@change) so SSR renders
                   <option selected> correctly. The previous :value
                   binding only set the DOM property after hydration,
                   making the dropdown appear empty until the user
                   interacted with it. -->
              <select class="cell-input" v-model="row.alienacao" :disabled="!canEdit"
                @change="() => persist(row, 'alienacao')">
                <option v-for="o in ALIENACAO_OPTIONS" :key="o" :value="o">{{ o || '—' }}</option>
              </select>
            </td>
            <td>
              <input class="cell-input" style="min-width: 4.5rem" :value="row.nf ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'nf', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" class="cell-input text-right"
                :value="row.parc_a_pagar ?? ''" :disabled="!canEdit"
                @input="(e) => { const v = valorNumerico(e); if (v !== undefined) scheduleSave(row, 'parc_a_pagar', Number(v) || null) }" />
            </td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right" style="min-width: 5.5rem"
                :value="row.lance ?? ''" :disabled="!canEdit"
                @input="(e) => { const v = valorNumerico(e); if (v !== undefined) scheduleSave(row, 'lance', Number(v) || null) }" />
            </td>
            <td class="calc text-right">{{ fmtMoney(imposto7(row)) }}</td>
            <td>
              <!-- Em branco = automático (o 1% aparece em cinza); 0 ou outro valor = digitado. -->
              <input type="number" step="0.01" min="0" class="cell-input text-right" data-imposto1
                :value="row.imposto1_manual ?? ''" :placeholder="fmtMoney(imposto1Automatico(row))"
                :disabled="!canEdit" title="Em branco: 1% do crédito, automático. Digite 0 para zerar."
                @input="(e) => {
                  const texto = valorNumerico(e)
                  const v = texto === undefined ? undefined : valorDigitadoImposto1(texto)
                  if (v !== undefined) scheduleSave(row, 'imposto1_manual', v)
                }" />
            </td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.valor_parc ?? ''" :disabled="!canEdit"
                @input="(e) => { const v = valorNumerico(e); if (v !== undefined) scheduleSave(row, 'valor_parc', Number(v) || null) }" />
            </td>
            <td class="calc text-right">{{ fmtMoney(devedor(row)) }}</td>
            <td>
              <input type="date" class="cell-input" :value="row.atualizado ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'atualizado', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td class="calc">{{ finalizado(row) ?? '—' }}</td>
            <td class="calc text-right">{{ fmtMoney(creditoLiquido(row)) }}</td>
            <td>
              <input class="cell-input" :value="row.fundo_reserva ?? ''" :disabled="!canEdit"
                placeholder="ex: 2.5%"
                @input="(e) => scheduleSave(row, 'fundo_reserva', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <!-- Largura mínima (aqui, na NF, no Crédito e no Lance): com a coluna
                   Imposto 1% as células editáveis encolhiam e cortavam o texto
                   ("1% = 1141,80 pago 27/10", "NF87 254"). Tela estreita rola para o lado. -->
              <input class="cell-input" style="min-width: 10rem" :value="row.obs ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'obs', (e.target as HTMLInputElement).value)" />
            </td>
            <td v-if="canDelete" class="text-center">
              <button class="text-muted-foreground hover:text-destructive" :title="'Excluir linha'" @click="removeRow(row)">
                <Trash2 class="size-3.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* Visual de planilha — header verde escuro, zebra leve, células
   compactas. As "calc" são as 4 colunas amarela-cinza que o usuário
   não pode editar (Imposto 7%, Devedor, Finalizado, Crédito líq.). */
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 5px;
  white-space: nowrap;
}
.grid-table thead th {
  border-color: rgba(255, 255, 255, 0.15);
  font-weight: 600;
}
.grid-table td.calc {
  background-color: hsl(var(--muted) / 0.5);
  color: hsl(var(--muted-foreground));
}
/* Inputs editáveis em fundo amarelo claro (estilo Excel). */
.cell-input {
  width: 100%;
  border: 0;
  background: rgb(254 252 232 / 0.6); /* yellow-50/60 */
  padding: 2px 4px;
  font-size: 11px;
  color: inherit;
}
:global(.dark) .cell-input {
  background: rgb(120 53 15 / 0.15); /* amber-900 leve */
}
.cell-input:focus {
  outline: 1px solid hsl(var(--primary));
  background: hsl(var(--background));
}
.cell-input:disabled {
  cursor: not-allowed;
  opacity: 0.7;
  background: transparent;
}
</style>
