<script setup lang="ts">
// Cadastro da garantia (§3) e correção do que foi INFORMADO.
// - Nº do pedido → GET /api/garantias/pedido: a data de entrega vira a data
//   inicial (RN01, travada — não é campo), com o fim do hardware (+3 meses) e
//   do software (+12 meses) e o status já calculados; o pedido também sugere a
//   NF de produto, o nome e (para quem vê CPF) o CPF — a pessoa confere.
// - NF, nome e CPF são obrigatórios; o CPF tem máscara e passa pelo dígito
//   verificador antes de sair (RN06; o backend confere de novo).
// - Data de cadastro e "cadastrado por" são do servidor (RN05).
// - Correção (`garantia`): manda só o que mudou (cada campo vira linha no
//   log); CPF vazio mantém o atual.
import { onKeyStroke } from '@vueuse/core'
import { AlertCircle, Loader2, Lock, Search, TriangleAlert, X } from 'lucide-vue-next'
import {
  corpoDaCorrecao,
  corpoDoCadastro,
  dataBR,
  errosDaApi,
  formatarCpf,
  nomePlataforma,
  rotuloNota,
  textoAviso,
  validarFormulario,
  type FormGarantia,
  type GarantiaDetalhe,
  type NotaDoPedido,
  type PedidoParaCadastro,
  type Regras,
} from '~/lib/garantias'

const props = defineProps<{
  // Correção de uma garantia existente; sem ela, cadastro novo.
  garantia?: GarantiaDetalhe | null
  // Abre já buscando este pedido (ex.: link do /atendimento).
  pedidoInicial?: string | null
  regras: Regras
}>()
const emit = defineEmits<{
  (e: 'fechar'): void
  (e: 'salva', g: GarantiaDetalhe): void
  // Abrir uma garantia que já existe (a duplicada, a do mesmo pedido).
  (e: 'abrir', id: number): void
}>()

const { api } = useApi()
const edicao = computed(() => !!props.garantia)

const form = reactive<FormGarantia>({
  pedido: props.garantia?.pedido_bling || props.pedidoInicial || '',
  nf_numero: props.garantia?.nf_numero || '',
  nf_serie: props.garantia?.nf_serie || '',
  cliente_nome: props.garantia?.cliente_nome || '',
  cpf: props.garantia?.cpf_completo ? formatarCpf(props.garantia.cpf) : '',
})
// Máscara 000.000.000-00 enquanto digita (colar formatado ou não).
watch(() => form.cpf, (v) => {
  const f = formatarCpf(v)
  if (f !== v) form.cpf = f
})

const errosCampo = ref<Record<string, string>>({})
const erroGeral = ref('')
const duplicadaId = ref<number | null>(null)
const salvando = ref(false)

// ─── pedido → entrega, prazos e sugestões ──────────────────────────────────
const consulta = ref<PedidoParaCadastro | null>(null)
const consultado = ref('')
const buscando = ref(false)
let geracao = 0

async function buscarPedido(): Promise<boolean> {
  const numero = form.pedido.trim()
  if (!numero) {
    errosCampo.value = { ...errosCampo.value, pedido: 'Informe o nº do pedido.' }
    return false
  }
  if (consulta.value && consultado.value === numero) return true
  const g = ++geracao
  buscando.value = true
  const { pedido: _p, ...resto } = errosCampo.value
  errosCampo.value = resto
  try {
    const r = await api<PedidoParaCadastro>('/api/garantias/pedido', { query: { numero } })
    if (g !== geracao) return false
    consulta.value = r
    consultado.value = numero
    aplicarSugestoes(r)
    return true
  } catch (e) {
    if (g !== geracao) return false
    consulta.value = null
    consultado.value = ''
    const er = errosDaApi(e)
    errosCampo.value = { ...errosCampo.value, pedido: er.campos.pedido || er.geral }
    return false
  } finally {
    if (g === geracao) buscando.value = false
  }
}

// As sugestões só entram em campo VAZIO: o que a pessoa já digitou fica.
function aplicarSugestoes(r: PedidoParaCadastro) {
  if (!form.nf_numero.trim() && r.nf_sugerida) escolherNota(r.nf_sugerida)
  if (!form.cliente_nome.trim() && r.nome_sugerido) form.cliente_nome = r.nome_sugerido
  if (!form.cpf.trim() && r.cpf_sugerido) form.cpf = formatarCpf(r.cpf_sugerido)
}

function escolherNota(n: NotaDoPedido) {
  form.nf_numero = n.numero
  form.nf_serie = n.serie || ''
  const { nf_numero: _a, nf_serie: _b, ...resto } = errosCampo.value
  errosCampo.value = resto
}
const notaEscolhida = (n: NotaDoPedido) => form.nf_numero.replace(/^0+/, '') === n.numero && (form.nf_serie || '') === (n.serie || '')

// Mudou o nº depois de buscar: a prévia deixa de valer até buscar de novo.
watch(() => form.pedido, (v) => {
  if (consulta.value && v.trim() !== consultado.value) {
    consulta.value = null
    consultado.value = ''
  }
})
onMounted(() => {
  if (!edicao.value && form.pedido.trim()) void buscarPedido()
})

// O que a tela mostra como início/fins: a prévia do pedido buscado ou, na
// correção sem trocar de pedido, o que a garantia já tem.
const prazos = computed(() => {
  if (consulta.value) return consulta.value.prazos
  const g = props.garantia
  if (g) return { data_inicio: g.data_inicio, fim_hardware: g.fim_hardware, fim_software: g.fim_software, status: g.status, status_rotulo: g.status_rotulo, entregue_sem_data: g.entregue_sem_data }
  return null
})
const entrega = computed(() => consulta.value?.entrega ?? props.garantia?.entrega ?? null)
const outrasGarantias = computed(() => (consulta.value?.garantias_existentes || []).filter((x) => x.id !== props.garantia?.id))
const avisos = computed(() => (consulta.value?.avisos || []).filter((a) => !(a === 'pedido_ja_tem_garantia' && !outrasGarantias.value.length)))
const cpfDoPedidoMascarado = computed(() => (!consulta.value?.cpf_sugerido && consulta.value?.cpf_sugerido_mascarado) || null)

// ─── salvar ─────────────────────────────────────────────────────────────────
async function salvar() {
  if (salvando.value) return
  erroGeral.value = ''
  duplicadaId.value = null
  errosCampo.value = validarFormulario(form, edicao.value)
  if (Object.keys(errosCampo.value).length) return
  salvando.value = true
  try {
    // Cadastro: sem o pedido buscado não há data de entrega para mostrar.
    if (!edicao.value && !(await buscarPedido())) return
    let g: GarantiaDetalhe
    if (props.garantia) {
      const corpo = corpoDaCorrecao(props.garantia, form)
      if (!Object.keys(corpo).length) {
        emit('fechar')
        return
      }
      g = await api<GarantiaDetalhe>(`/api/garantias/${props.garantia.id}`, { method: 'PUT', body: corpo })
    } else {
      g = await api<GarantiaDetalhe>('/api/garantias', { method: 'POST', body: corpoDoCadastro(form) })
    }
    emit('salva', g)
  } catch (e) {
    const er = errosDaApi(e)
    errosCampo.value = er.campos
    erroGeral.value = er.geral
    duplicadaId.value = er.garantiaId
  } finally {
    salvando.value = false
  }
}

onKeyStroke('Escape', () => { if (!salvando.value) emit('fechar') })
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4" @click.self="!salvando && emit('fechar')">
    <form
      class="flex max-h-[100dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-xl border bg-background shadow-xl sm:max-h-[92dvh] sm:rounded-xl"
      role="dialog"
      aria-modal="true"
      aria-labelledby="garantia-form-titulo"
      novalidate
      data-garantia-form
      @submit.prevent="salvar"
    >
      <div class="flex shrink-0 items-center gap-2 border-b px-4 py-3">
        <h2 id="garantia-form-titulo" class="text-base font-semibold">{{ edicao ? `Corrigir garantia #${garantia?.id}` : 'Nova garantia' }}</h2>
        <Button type="button" class="ml-auto" size="sm" variant="ghost" aria-label="Fechar" :disabled="salvando" @click="emit('fechar')"><X class="size-4" /></Button>
      </div>

      <div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <!-- Nº do pedido: busca a entrega e os prazos -->
        <div>
          <Label for="gar-pedido">Nº do pedido *</Label>
          <div class="mt-1 flex gap-2">
            <Input
              id="gar-pedido"
              v-model="form.pedido"
              maxlength="64"
              autocomplete="off"
              spellcheck="false"
              class="font-mono"
              placeholder="nº do Bling ou da plataforma"
              @keydown.enter.prevent="buscarPedido"
              @blur="form.pedido.trim() && buscarPedido()"
            />
            <Button type="button" variant="outline" :disabled="buscando || !form.pedido.trim()" @click="buscarPedido">
              <Loader2 v-if="buscando" class="mr-1 size-4 animate-spin" /><Search v-else class="mr-1 size-4" /> Buscar
            </Button>
          </div>
          <p v-if="errosCampo.pedido" class="mt-1 text-xs text-destructive" data-erro-campo="pedido">{{ errosCampo.pedido }}</p>
          <p v-else-if="!consulta && !edicao" class="mt-1 text-xs text-muted-foreground">A data de entrega do pedido vira a data inicial da garantia.</p>
        </div>

        <!-- o pedido encontrado -->
        <section v-if="consulta" class="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm" data-pedido-encontrado>
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span><span class="text-muted-foreground">Bling</span> <span class="font-mono">{{ consulta.pedido_bling }}</span></span>
            <span v-if="consulta.pedido_marketplace"><span class="text-muted-foreground">{{ nomePlataforma(consulta.plataforma) || 'Plataforma' }}</span> <span class="font-mono">{{ consulta.pedido_marketplace }}</span></span>
            <span v-if="consulta.conta" class="text-muted-foreground">{{ consulta.conta }}</span>
            <span v-if="consulta.situacao" class="rounded bg-muted px-1.5 py-px text-xs">{{ consulta.situacao }}</span>
          </div>
          <ul v-if="consulta.itens.length" class="space-y-0.5 text-xs">
            <li v-for="(it, i) in consulta.itens" :key="i" class="flex items-baseline gap-1.5">
              <span class="tabular-nums text-muted-foreground">{{ it.quantidade ?? 1 }}×</span>
              <span class="min-w-0 flex-1 truncate" :title="it.descricao || ''">{{ it.descricao || '—' }}</span>
              <span v-if="it.sku" class="font-mono text-muted-foreground">{{ it.sku }}</span>
              <span v-if="it.uranyx" class="rounded bg-primary/10 px-1 text-[10px] font-medium text-primary">Uranyx</span>
            </li>
          </ul>
        </section>

        <!-- avisos do pedido (não bloqueiam) -->
        <ul v-if="avisos.length" class="space-y-1" data-avisos-pedido>
          <li v-for="a in avisos" :key="a" class="flex items-start gap-1.5 rounded-md border border-amber-400/50 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
            <TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" /><span>{{ textoAviso(a) }}</span>
          </li>
        </ul>
        <div v-if="outrasGarantias.length" class="space-y-1 text-xs">
          <div class="text-muted-foreground">Garantias deste pedido:</div>
          <button
            v-for="o in outrasGarantias"
            :key="o.id"
            type="button"
            class="flex w-full flex-wrap items-center gap-2 rounded-md border px-2.5 py-1.5 text-left hover:bg-muted"
            @click="emit('abrir', o.id)"
          >
            <GarantiaStatus :status="o.status" :entregue-sem-data="o.entregue_sem_data" />
            <span class="font-medium">#{{ o.id }} · {{ o.cliente_nome }}</span>
            <span class="text-muted-foreground">NF {{ o.nf_numero }} · {{ o.cpf_mascarado || '—' }}</span>
          </button>
        </div>

        <!-- prazos: calculados, nunca editáveis (RN01–RN04) -->
        <section class="grid grid-cols-2 gap-3 sm:grid-cols-4" data-prazos-calculados>
          <div class="col-span-2 sm:col-span-1">
            <div class="flex items-center gap-1 text-xs font-medium text-muted-foreground"><Lock class="size-3" aria-hidden="true" /> Data inicial</div>
            <div
              class="mt-1 flex h-10 items-center rounded-md border bg-muted/50 px-3 tabular-nums"
              :class="prazos?.data_inicio ? 'text-sm' : 'text-xs text-muted-foreground'"
              aria-readonly="true"
              data-data-inicial
            >
              {{ prazos?.data_inicio ? dataBR(prazos.data_inicio) : prazos?.entregue_sem_data ? 'sem data de entrega' : prazos ? 'aguardando entrega' : 'informe o pedido' }}
            </div>
          </div>
          <div>
            <div class="flex items-center gap-1 text-xs font-medium text-muted-foreground"><Lock class="size-3" aria-hidden="true" /> Fim hardware</div>
            <div class="mt-1 flex h-10 items-center rounded-md border bg-muted/50 px-3 text-sm tabular-nums">{{ prazos?.fim_hardware ? dataBR(prazos.fim_hardware) : '—' }}</div>
          </div>
          <div>
            <div class="flex items-center gap-1 text-xs font-medium text-muted-foreground"><Lock class="size-3" aria-hidden="true" /> Fim software</div>
            <div class="mt-1 flex h-10 items-center rounded-md border bg-muted/50 px-3 text-sm tabular-nums">{{ prazos?.fim_software ? dataBR(prazos.fim_software) : '—' }}</div>
          </div>
          <div class="col-span-2 sm:col-span-1">
            <div class="text-xs font-medium text-muted-foreground">Status</div>
            <div class="mt-1 flex h-10 items-center">
              <GarantiaStatus v-if="prazos" :status="prazos.status" :entregue-sem-data="prazos.entregue_sem_data" />
              <span v-else class="text-sm text-muted-foreground">—</span>
            </div>
          </div>
          <p class="col-span-2 text-xs text-muted-foreground sm:col-span-4">
            <template v-if="entrega?.data">Entrega em {{ dataBR(entrega.data) }}<template v-if="entrega.origem_rotulo"> — {{ entrega.origem_rotulo }}</template>. </template>
            Início = data de entrega (não editável) · hardware +{{ regras.meses_hardware }} meses · software +{{ regras.meses_software }} meses<template v-if="regras.ultimo_dia_coberto"> · vale até o último dia, inclusive</template>.
          </p>
        </section>

        <!-- NF -->
        <div class="space-y-2">
          <div v-if="consulta?.notas.length" class="flex flex-wrap gap-1.5" data-notas-do-pedido>
            <span class="w-full text-xs text-muted-foreground">Notas do pedido (clique para usar):</span>
            <button
              v-for="n in consulta.notas"
              :key="n.chave || `${n.numero}-${n.serie}`"
              type="button"
              class="rounded-md border px-2 py-1 text-xs"
              :class="notaEscolhida(n) ? 'border-primary bg-primary/10 text-primary' : 'hover:bg-muted'"
              :aria-pressed="notaEscolhida(n)"
              @click="escolherNota(n)"
            >{{ rotuloNota(n) }}</button>
          </div>
          <div class="grid grid-cols-[1fr_6rem] gap-2">
            <div>
              <Label for="gar-nf">Nota fiscal (NF) *</Label>
              <Input id="gar-nf" v-model="form.nf_numero" class="mt-1 font-mono" maxlength="20" inputmode="numeric" autocomplete="off" placeholder="só números" />
              <p v-if="errosCampo.nf_numero" class="mt-1 text-xs text-destructive" data-erro-campo="nf_numero">{{ errosCampo.nf_numero }}</p>
            </div>
            <div>
              <Label for="gar-serie">Série</Label>
              <Input id="gar-serie" v-model="form.nf_serie" class="mt-1 font-mono" maxlength="5" inputmode="numeric" autocomplete="off" placeholder="opcional" />
              <p v-if="errosCampo.nf_serie" class="mt-1 text-xs text-destructive" data-erro-campo="nf_serie">{{ errosCampo.nf_serie }}</p>
            </div>
          </div>
          <p v-if="duplicadaId" class="text-xs">
            <button type="button" class="text-primary underline" @click="emit('abrir', duplicadaId)">abrir a garantia #{{ duplicadaId }}</button>
          </p>
        </div>

        <!-- Nome -->
        <div>
          <Label for="gar-nome">Nome do cliente *</Label>
          <Input id="gar-nome" v-model="form.cliente_nome" class="mt-1" maxlength="200" autocomplete="off" placeholder="Nome completo" />
          <p v-if="errosCampo.cliente_nome" class="mt-1 text-xs text-destructive" data-erro-campo="cliente_nome">{{ errosCampo.cliente_nome }}</p>
          <p v-else-if="consulta?.nome_sugerido && form.cliente_nome === consulta.nome_sugerido" class="mt-1 text-xs text-muted-foreground">
            Veio {{ consulta.nome_origem === 'nf' ? 'da NF do produto' : 'do destinatário da entrega' }} — confira.
          </p>
        </div>

        <!-- CPF -->
        <div>
          <Label for="gar-cpf">CPF {{ edicao ? '' : '*' }}</Label>
          <Input
            id="gar-cpf"
            v-model="form.cpf"
            class="mt-1 font-mono"
            maxlength="14"
            inputmode="numeric"
            autocomplete="off"
            :placeholder="edicao && !garantia?.cpf_completo ? `vazio = mantém o atual (${garantia?.cpf_mascarado || '—'})` : '000.000.000-00'"
          />
          <p v-if="errosCampo.cpf" class="mt-1 text-xs text-destructive" data-erro-campo="cpf">{{ errosCampo.cpf }}</p>
          <p v-else-if="cpfDoPedidoMascarado" class="mt-1 text-xs text-muted-foreground">O pedido tem o CPF {{ cpfDoPedidoMascarado }} — digite o CPF do cliente para conferir.</p>
        </div>

        <p v-if="!edicao" class="text-xs text-muted-foreground">Data de cadastro e "cadastrado por" são gravados sozinhos ao salvar.</p>
      </div>

      <div class="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t px-4 py-3">
        <p v-if="erroGeral" role="alert" class="mr-auto flex min-w-0 items-center gap-1.5 text-sm text-destructive" data-erro-geral><AlertCircle class="size-4 shrink-0" /> {{ erroGeral }}</p>
        <p v-else-if="Object.keys(errosCampo).length" class="mr-auto text-xs text-destructive">Confira os campos marcados.</p>
        <Button type="button" variant="ghost" :disabled="salvando" @click="emit('fechar')">Cancelar</Button>
        <Button type="submit" :disabled="salvando || buscando"><Loader2 v-if="salvando" class="mr-1 size-4 animate-spin" /> {{ edicao ? 'Salvar correção' : 'Cadastrar garantia' }}</Button>
      </div>
    </form>
  </div>
</template>
