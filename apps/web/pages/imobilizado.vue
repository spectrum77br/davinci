<script setup lang="ts">
// Cadastros › Imobilizado (06/10/2026): o que a empresa tem, quanto vale e
// quem responde por cada item. Projeto "Cadastro de Imobilizado" v0.1.
// Sem middleware de permissão de propósito: quem não tem `imobilizado:view`
// entra e vê só os itens de que é responsável (a API já filtra).
// edit = cadastrar/editar; delete = dar baixa. Item nunca é excluído.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { isoToday } from '~/lib/date'
import { TABS_CADASTROS } from '~/lib/navGroups'
import {
  CAMPO_LABELS, STATUS_LABELS, errosDaApi, filtrarPessoas, formatBRL, lerValor, textoHistorico, valorParaCampo,
  type ImobilizadoHistorico, type ImobilizadoItem, type ImobilizadoLista, type Pessoa,
} from '~/lib/imobilizado'
import { AlertCircle, Archive, Check, Loader2, Pencil, Plus, RefreshCw, Search, X } from 'lucide-vue-next'

const { api } = useApi()
const veTudo = useCan('imobilizado', 'view')
const podeEditar = useCan('imobilizado', 'edit')
const podeBaixar = useCan('imobilizado', 'delete')

// ── Listagem ─────────────────────────────────────────────────────────────
const filtros = reactive({ busca: '', responsavel_id: '', status: 'ativo' as 'ativo' | 'baixado' | 'todos' })
const lista = ref<ImobilizadoLista>({ itens: [], quantidade: 0, soma: '0.00' })
const carregando = ref(false)
const carregado = ref(false)
const erro = ref('')
const aviso = ref('')
const pessoas = ref<Pessoa[]>([])
const pessoasAtivas = computed(() => pessoas.value.filter(p => p.ativo !== false))

async function carregar() {
  carregando.value = true
  erro.value = ''
  try {
    const query: Record<string, string> = { status: filtros.status }
    if (filtros.busca.trim()) query.busca = filtros.busca.trim()
    if (veTudo.value && filtros.responsavel_id) query.responsavel_id = filtros.responsavel_id
    lista.value = await api<ImobilizadoLista>('/api/imobilizado', { query })
    carregado.value = true
  } catch (e) { erro.value = errosDaApi(e).geral || 'Não foi possível carregar.' }
  finally { carregando.value = false }
}

async function carregarPessoas() {
  if (!veTudo.value) return
  try { pessoas.value = await api<Pessoa[]>('/api/imobilizado/responsaveis') } catch { pessoas.value = [] }
}

let atraso: ReturnType<typeof setTimeout> | null = null
watch(() => filtros.busca, () => {
  if (atraso) clearTimeout(atraso)
  atraso = setTimeout(carregar, 300)
})
watch(() => [filtros.status, filtros.responsavel_id], () => { void carregar() })
onMounted(() => { void carregar(); void carregarPessoas() })

// ── Formulário (novo e edição) ───────────────────────────────────────────
const form = reactive({
  aberto: false,
  id: null as number | null,
  numero: '',
  descricao: '',
  valor: '',
  responsavel_id: '',
  responsavelBusca: '',
  listaAberta: false,
})
const errosCampo = ref<Record<string, string>>({})
const erroForm = ref('')
const salvando = ref(false)
const sugestoes = computed(() => filtrarPessoas(pessoasAtivas.value, form.responsavelBusca).slice(0, 8))

async function novo() {
  Object.assign(form, { aberto: true, id: null, numero: '', descricao: '', valor: '', responsavel_id: '', responsavelBusca: '', listaAberta: false })
  errosCampo.value = {}
  erroForm.value = ''
  try { form.numero = (await api<{ numero: string }>('/api/imobilizado/proximo-numero')).numero } catch { /* a pessoa digita */ }
}

function editar(item: ImobilizadoItem) {
  Object.assign(form, {
    aberto: true, id: item.id, numero: item.numero, descricao: item.descricao,
    valor: valorParaCampo(item.valor), responsavel_id: item.responsavel.id,
    responsavelBusca: item.responsavel.nome, listaAberta: false,
  })
  errosCampo.value = {}
  erroForm.value = ''
}

// "6890,5" vira "6.890,50" ao sair do campo; texto inválido fica como está.
function formatarValor() {
  const v = lerValor(form.valor)
  if ('valor' in v) form.valor = valorParaCampo(v.valor)
}

function escolherResponsavel(p: Pessoa) {
  form.responsavel_id = p.id
  form.responsavelBusca = p.nome
  form.listaAberta = false
  delete errosCampo.value.responsavel_id
}

function digitouResponsavel() {
  // Texto mudou: a escolha anterior deixa de valer até clicar num nome.
  const atual = pessoasAtivas.value.find(p => p.id === form.responsavel_id)
  if (!atual || atual.nome !== form.responsavelBusca) form.responsavel_id = ''
  form.listaAberta = true
}

function validar(): { numero: string; descricao: string; valor: string; responsavel_id: string } | null {
  const e: Record<string, string> = {}
  const numero = form.numero.trim().toUpperCase()
  const descricao = form.descricao.trim()
  if (!form.id) {
    if (!numero) e.numero = 'Informe o número.'
    else if (numero.length > 20) e.numero = 'Máximo de 20 caracteres.'
  }
  if (descricao.length < 3) e.descricao = descricao ? 'Mínimo de 3 caracteres.' : 'Informe a descrição.'
  else if (descricao.length > 200) e.descricao = 'Máximo de 200 caracteres.'
  const v = lerValor(form.valor)
  if ('erro' in v) e.valor = v.erro
  if (!form.responsavel_id) e.responsavel_id = 'Selecione o responsável na lista.'
  errosCampo.value = e
  if (Object.keys(e).length || 'erro' in v) return null
  return { numero, descricao, valor: v.valor, responsavel_id: form.responsavel_id }
}

async function salvar() {
  if (salvando.value) return
  const dados = validar()
  if (!dados) return
  salvando.value = true
  erroForm.value = ''
  try {
    let item: ImobilizadoItem
    if (form.id) {
      const { numero: _fixo, ...body } = dados // RN02: número não vai na edição
      item = await api<ImobilizadoItem>(`/api/imobilizado/${form.id}`, { method: 'PUT', body })
      aviso.value = `Item ${item.numero} atualizado.`
    } else {
      item = await api<ImobilizadoItem>('/api/imobilizado', { method: 'POST', body: dados })
      aviso.value = `Item ${item.numero} cadastrado.`
    }
    form.aberto = false
    if (detalhe.item?.id === item.id) { detalhe.item = item; await carregarHistorico() }
    await carregar()
  } catch (e) {
    const r = errosDaApi(e)
    errosCampo.value = r.campos
    erroForm.value = r.geral
  } finally { salvando.value = false }
}

// ── Detalhe, histórico e baixa ───────────────────────────────────────────
const detalhe = reactive({
  item: null as ImobilizadoItem | null,
  aba: 'dados' as 'dados' | 'historico',
  historico: [] as ImobilizadoHistorico[],
  carregandoHistorico: false,
  baixaAberta: false,
  baixaData: '',
  baixaMotivo: '',
  baixando: false,
  erro: '',
  errosBaixa: {} as Record<string, string>,
})

async function abrir(item: ImobilizadoItem) {
  Object.assign(detalhe, { item, aba: 'dados', historico: [], baixaAberta: false, erro: '', errosBaixa: {} })
  await carregarHistorico()
}

async function carregarHistorico() {
  if (!detalhe.item) return
  detalhe.carregandoHistorico = true
  try {
    detalhe.historico = await api<ImobilizadoHistorico[]>(`/api/imobilizado/${detalhe.item.id}/historico`)
  } catch (e) { detalhe.erro = errosDaApi(e).geral }
  finally { detalhe.carregandoHistorico = false }
}

function abrirBaixa() {
  Object.assign(detalhe, { baixaAberta: true, baixaData: isoToday(), baixaMotivo: '', errosBaixa: {}, erro: '' })
}

async function confirmarBaixa() {
  const item = detalhe.item
  if (!item || detalhe.baixando) return
  const e: Record<string, string> = {}
  const motivo = detalhe.baixaMotivo.trim()
  if (!detalhe.baixaData) e.baixa_data = 'Informe a data da baixa.'
  else if (detalhe.baixaData > isoToday()) e.baixa_data = 'A data da baixa não pode ser depois de hoje.'
  if (motivo.length < 3) e.baixa_motivo = motivo ? 'Mínimo de 3 caracteres.' : 'Informe o motivo.'
  else if (motivo.length > 200) e.baixa_motivo = 'Máximo de 200 caracteres.'
  detalhe.errosBaixa = e
  if (Object.keys(e).length) return
  detalhe.baixando = true
  detalhe.erro = ''
  try {
    detalhe.item = await api<ImobilizadoItem>(`/api/imobilizado/${item.id}/baixa`, {
      method: 'POST', body: { baixa_data: detalhe.baixaData, baixa_motivo: motivo },
    })
    detalhe.baixaAberta = false
    aviso.value = `Baixa do item ${item.numero} registrada.`
    await Promise.all([carregarHistorico(), carregar()])
  } catch (err) {
    const r = errosDaApi(err)
    detalhe.errosBaixa = r.campos
    detalhe.erro = r.geral
  } finally { detalhe.baixando = false }
}

function dataBR(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [a, m, d] = iso.slice(0, 10).split('-')
  return `${d}/${m}/${a}`
}
function dataHoraBR(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', dateStyle: 'short', timeStyle: 'short' })
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader
      :title="veTudo ? 'Imobilizado' : 'Meus itens do imobilizado'"
      :description="veTudo ? 'Bens da empresa: o que temos, quanto vale e quem responde por cada item.' : 'Bens da empresa que estão sob a sua responsabilidade.'"
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': carregando }" /> recarregar
        </Button>
        <Button v-if="podeEditar" size="sm" @click="novo"><Plus class="size-4 mr-1" /> Novo item</Button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-end gap-2">
      <div class="relative w-full sm:w-72">
        <Search class="size-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="filtros.busca" class="pl-8 h-9" placeholder="Buscar por número ou descrição" aria-label="Buscar por número ou descrição" />
      </div>
      <select v-if="veTudo" v-model="filtros.responsavel_id" aria-label="Responsável" class="h-9 rounded-md border bg-background px-2 text-sm w-full sm:w-56">
        <option value="">Todos os responsáveis</option>
        <option v-for="p in pessoas" :key="p.id" :value="p.id">{{ p.nome }}{{ p.ativo === false ? ' (inativo)' : '' }}</option>
      </select>
      <select v-model="filtros.status" aria-label="Status" class="h-9 rounded-md border bg-background px-2 text-sm w-full sm:w-40">
        <option value="ativo">Ativos</option>
        <option value="baixado">Baixados</option>
        <option value="todos">Todos</option>
      </select>
    </div>

    <p v-if="erro" role="alert" class="text-sm text-destructive flex items-center gap-2"><AlertCircle class="size-4" /> {{ erro }}</p>
    <p v-if="aviso" role="status" class="text-sm text-emerald-600 flex items-center gap-2"><Check class="size-4" /> {{ aviso }}</p>

    <div class="rounded-lg border overflow-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted"><tr>
          <th class="px-3 py-3 text-left whitespace-nowrap">Número</th>
          <th class="px-3 py-3 text-left min-w-[220px]">Descrição</th>
          <th class="px-3 py-3 text-right whitespace-nowrap">Valor</th>
          <th class="px-3 py-3 text-left min-w-[160px]">Responsável</th>
          <th class="px-3 py-3 text-left">Status</th>
        </tr></thead>
        <tbody>
          <tr v-if="!carregado"><td colspan="5" class="p-8 text-center text-muted-foreground">{{ erro ? 'Não foi possível carregar.' : 'Carregando itens…' }}</td></tr>
          <tr v-else-if="!lista.itens.length"><td colspan="5" class="p-8 text-center text-muted-foreground">Nenhum item encontrado.</td></tr>
          <tr
            v-for="item in lista.itens" :key="item.id" tabindex="0" role="button"
            class="border-t hover:bg-muted/40 cursor-pointer focus:outline-none focus-visible:bg-muted/40"
            @click="abrir(item)" @keydown.enter="abrir(item)"
          >
            <td class="px-3 py-2.5 font-mono whitespace-nowrap">{{ item.numero }}</td>
            <td class="px-3 py-2.5">{{ item.descricao }}</td>
            <td class="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">{{ formatBRL(item.valor) }}</td>
            <td class="px-3 py-2.5">{{ item.responsavel.nome }}</td>
            <td class="px-3 py-2.5">
              <span class="text-xs px-2 py-0.5 rounded border" :class="item.status === 'ativo' ? 'border-emerald-500 text-emerald-600' : 'border-muted-foreground/40 text-muted-foreground'">{{ STATUS_LABELS[item.status] }}</span>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="carregado" class="bg-muted/50 border-t font-medium">
          <tr>
            <td colspan="2" class="px-3 py-2.5">{{ lista.quantidade }} {{ lista.quantidade === 1 ? 'item' : 'itens' }}</td>
            <td class="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">{{ formatBRL(lista.soma) }}</td>
            <td colspan="2" />
          </tr>
        </tfoot>
      </table>
    </div>

    <!-- Formulário: novo item / edição -->
    <div v-if="form.aberto" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="form.aberto = false">
      <form class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4" novalidate @submit.prevent="salvar">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ form.id ? 'Editar item do imobilizado' : 'Novo item do imobilizado' }}</h2>
          <Button type="button" class="ml-auto" size="sm" variant="ghost" aria-label="Fechar" @click="form.aberto = false"><X class="size-4" /></Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label for="imob-numero">Número *</Label>
            <Input id="imob-numero" v-model="form.numero" maxlength="20" autocomplete="off" spellcheck="false" :disabled="!!form.id" class="font-mono" placeholder="IMB-000001" />
            <p v-if="form.id" class="text-xs text-muted-foreground mt-1">O número não pode ser alterado depois de salvo.</p>
            <p v-if="errosCampo.numero" class="text-xs text-destructive mt-1">{{ errosCampo.numero }}</p>
          </div>
          <div>
            <Label for="imob-descricao">Descrição *</Label>
            <Input id="imob-descricao" v-model="form.descricao" maxlength="200" placeholder="Notebook Dell Latitude 5440" />
            <p v-if="errosCampo.descricao" class="text-xs text-destructive mt-1">{{ errosCampo.descricao }}</p>
          </div>
          <div>
            <Label for="imob-valor">Valor (R$) *</Label>
            <Input id="imob-valor" v-model="form.valor" inputmode="decimal" autocomplete="off" placeholder="6.890,00" @blur="formatarValor" />
            <p v-if="errosCampo.valor" class="text-xs text-destructive mt-1">{{ errosCampo.valor }}</p>
          </div>
          <div class="relative">
            <Label for="imob-responsavel">Responsável *</Label>
            <Input
              id="imob-responsavel" v-model="form.responsavelBusca" autocomplete="off" placeholder="Digite o nome para buscar"
              role="combobox" :aria-expanded="form.listaAberta" aria-controls="imob-responsavel-lista"
              @focus="form.listaAberta = true" @input="digitouResponsavel" @keydown.esc="form.listaAberta = false"
              @keydown.enter.prevent="sugestoes.length && escolherResponsavel(sugestoes[0])"
            />
            <ul
              v-if="form.listaAberta" id="imob-responsavel-lista" role="listbox"
              class="absolute z-10 mt-1 w-full max-h-56 overflow-auto rounded-md border bg-background shadow-md text-sm"
            >
              <li v-if="!sugestoes.length" class="px-3 py-2 text-muted-foreground">Nenhum usuário ativo com esse nome.</li>
              <li
                v-for="p in sugestoes" :key="p.id" role="option" :aria-selected="p.id === form.responsavel_id"
                class="px-3 py-2 cursor-pointer hover:bg-accent" :class="{ 'bg-accent': p.id === form.responsavel_id }"
                @mousedown.prevent="escolherResponsavel(p)"
              >{{ p.nome }}</li>
            </ul>
            <p v-if="errosCampo.responsavel_id" class="text-xs text-destructive mt-1">{{ errosCampo.responsavel_id }}</p>
          </div>
        </div>
        <p v-if="erroForm" role="alert" class="text-sm text-destructive">{{ erroForm }}</p>
        <div class="flex justify-end gap-2">
          <Button type="button" variant="ghost" :disabled="salvando" @click="form.aberto = false">Cancelar</Button>
          <Button type="submit" :disabled="salvando"><Loader2 v-if="salvando" class="size-4 mr-1 animate-spin" /> Salvar</Button>
        </div>
      </form>
    </div>

    <!-- Detalhe do item -->
    <div v-if="detalhe.item && !form.aberto" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="detalhe.item = null">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-start gap-2">
          <div class="min-w-0">
            <h2 class="text-lg font-semibold font-mono">{{ detalhe.item.numero }}</h2>
            <p class="text-sm text-muted-foreground break-words">{{ detalhe.item.descricao }}</p>
          </div>
          <span class="text-xs px-2 py-0.5 rounded border mt-1 shrink-0" :class="detalhe.item.status === 'ativo' ? 'border-emerald-500 text-emerald-600' : 'border-muted-foreground/40 text-muted-foreground'">{{ STATUS_LABELS[detalhe.item.status] }}</span>
          <Button class="ml-auto shrink-0" size="sm" variant="ghost" aria-label="Fechar" @click="detalhe.item = null"><X class="size-4" /></Button>
        </div>

        <div class="flex gap-1 border-b" role="tablist">
          <button
            v-for="aba in (['dados', 'historico'] as const)" :key="aba" type="button" role="tab" :aria-selected="detalhe.aba === aba"
            class="px-3 py-2 text-sm -mb-px border-b-2" :class="detalhe.aba === aba ? 'border-primary font-medium' : 'border-transparent text-muted-foreground'"
            @click="detalhe.aba = aba"
          >{{ aba === 'dados' ? 'Dados' : `Histórico (${detalhe.historico.length})` }}</button>
        </div>

        <dl v-if="detalhe.aba === 'dados'" class="grid grid-cols-[auto,1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-muted-foreground">Valor</dt><dd class="tabular-nums">{{ formatBRL(detalhe.item.valor) }}</dd>
          <dt class="text-muted-foreground">Responsável</dt><dd>{{ detalhe.item.responsavel.nome }}</dd>
          <template v-if="detalhe.item.status === 'baixado'">
            <dt class="text-muted-foreground">Data da baixa</dt><dd>{{ dataBR(detalhe.item.baixa_data) }}</dd>
            <dt class="text-muted-foreground">Motivo da baixa</dt><dd class="break-words">{{ detalhe.item.baixa_motivo }}</dd>
          </template>
          <dt class="text-muted-foreground">Cadastrado</dt><dd>{{ dataHoraBR(detalhe.item.criado_em) }} · {{ detalhe.item.criado_por?.nome || '—' }}</dd>
          <template v-if="detalhe.item.atualizado_em">
            <dt class="text-muted-foreground">Última alteração</dt><dd>{{ dataHoraBR(detalhe.item.atualizado_em) }} · {{ detalhe.item.atualizado_por?.nome || '—' }}</dd>
          </template>
        </dl>

        <div v-else class="text-sm">
          <p v-if="detalhe.carregandoHistorico" class="text-muted-foreground">Carregando histórico…</p>
          <p v-else-if="!detalhe.historico.length" class="text-muted-foreground">Nenhuma alteração desde o cadastro.</p>
          <ol v-else class="space-y-2">
            <li v-for="h in detalhe.historico" :key="h.id" class="rounded-md border px-3 py-2">
              <div class="text-xs text-muted-foreground">{{ dataHoraBR(h.alterado_em) }} · {{ h.alterado_por?.nome || '—' }}</div>
              <div><span class="font-medium">{{ CAMPO_LABELS[h.campo] || h.campo }}:</span> {{ textoHistorico(h.campo, h.valor_anterior) }} → {{ textoHistorico(h.campo, h.valor_novo) }}</div>
            </li>
          </ol>
        </div>

        <div v-if="detalhe.baixaAberta" class="rounded-md border p-3 space-y-3">
          <p class="text-sm font-medium">Dar baixa no item</p>
          <div>
            <Label for="imob-baixa-data">Data da baixa *</Label>
            <Input id="imob-baixa-data" v-model="detalhe.baixaData" type="date" :max="isoToday()" />
            <p v-if="detalhe.errosBaixa.baixa_data" class="text-xs text-destructive mt-1">{{ detalhe.errosBaixa.baixa_data }}</p>
          </div>
          <div>
            <Label for="imob-baixa-motivo">Motivo *</Label>
            <Input id="imob-baixa-motivo" v-model="detalhe.baixaMotivo" maxlength="200" placeholder="Equipamento danificado sem conserto" />
            <p v-if="detalhe.errosBaixa.baixa_motivo" class="text-xs text-destructive mt-1">{{ detalhe.errosBaixa.baixa_motivo }}</p>
          </div>
          <p class="text-xs text-muted-foreground">Depois da baixa o item não pode mais ser editado nem trocar de responsável.</p>
          <div class="flex justify-end gap-2">
            <Button size="sm" variant="ghost" :disabled="detalhe.baixando" @click="detalhe.baixaAberta = false">Cancelar</Button>
            <Button size="sm" variant="destructive" :disabled="detalhe.baixando" @click="confirmarBaixa">
              <Loader2 v-if="detalhe.baixando" class="size-4 mr-1 animate-spin" /> Confirmar baixa
            </Button>
          </div>
        </div>

        <p v-if="detalhe.erro" role="alert" class="text-sm text-destructive">{{ detalhe.erro }}</p>

        <div v-if="detalhe.item.status === 'ativo' && !detalhe.baixaAberta && (podeEditar || podeBaixar)" class="flex justify-end gap-2">
          <Button v-if="podeBaixar" size="sm" variant="outline" @click="abrirBaixa"><Archive class="size-4 mr-1" /> Dar baixa</Button>
          <Button v-if="podeEditar" size="sm" @click="editar(detalhe.item)"><Pencil class="size-4 mr-1" /> Editar</Button>
        </div>
      </div>
    </div>
  </div>
</template>
