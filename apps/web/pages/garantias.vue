<script setup lang="ts">
// Pós-venda › Garantias (07/10/2026) — Painel de Garantia Uranyx.
// Documento "Painel de Garantia — Uranyx (DaVinci)", tela do §4.1:
// [+ Nova garantia], busca (Nome / CPF / NF / Pedido), Status, Período, os 4
// indicadores (Ativas · Só software · HW vence em 30d · Atendimentos no mês —
// clicar filtra) e a tabela Nome | CPF (mascarado) | NF | Entrega | Fim HW |
// Fim SW | Status | Atend. Clicar na linha abre o detalhe (§4.2).
// Permissões (§6): a página pede "Consultar" (garantias.view); "Cadastrar"
// (garantias.edit) mostra o + Nova garantia e o Corrigir; o CPF completo só no
// detalhe, para quem tem "Ver o CPF completo". As regras (prazos, status,
// pontos do §8) vêm do servidor (GET /regras) e aparecem em "Como funciona".
// Links: ?garantia=<id> abre o detalhe; ?novo=<pedido> abre o cadastro já
// buscando o pedido (é o que o /atendimento usa).
// A lista é POST /api/garantias/lista com os filtros no CORPO: a busca leva
// nome e CPF, que não podem ir para o log de acesso (ele grava a query).
import { AlertCircle, Info, Loader2, Plus, RefreshCw, Search, ShieldCheck } from 'lucide-vue-next'
import {
  CARTOES,
  FILTROS_PADRAO,
  FILTROS_STATUS,
  LIMITE_PAGINA,
  REGRAS_PADRAO,
  cartaoAtivo,
  dataBR,
  dataCurta,
  errosDaApi,
  filtroDoCartao,
  nomePlataforma,
  corpoDaLista,
  textoNf,
  type Cartao,
  type FiltrosLista,
  type GarantiaDetalhe,
  type GarantiaLinha,
  type GarantiaLista,
  type Indicadores,
  type Regras,
} from '~/lib/garantias'

definePageMeta({ middleware: ['permission'], permission: { resource: 'garantias', action: 'view' } })

const { api } = useApi()
const route = useRoute()
const router = useRouter()
const toasts = useToasts()
const acesso = useGarantiaAcesso()

// ─── regras (prazos e pontos do §8) ─────────────────────────────────────────
const regras = ref<Regras>(REGRAS_PADRAO)
const regrasAbertas = ref(false)
async function carregarRegras() {
  try {
    regras.value = { ...REGRAS_PADRAO, ...(await api<Regras>('/api/garantias/regras')) }
  } catch {
    // fica o padrão (o mesmo do servidor hoje)
  }
}

// ─── lista ──────────────────────────────────────────────────────────────────
const filtros = reactive<FiltrosLista>({ ...FILTROS_PADRAO })
const itens = ref<GarantiaLinha[]>([])
const total = ref(0)
const indicadores = ref<Indicadores | null>(null)
const carregando = ref(false)
const carregandoMais = ref(false)
const carregado = ref(false)
const erro = ref('')
let geracao = 0

async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpoDaLista(filtros, 0) })
    if (g !== geracao) return
    itens.value = r.itens
    total.value = r.total
    indicadores.value = r.indicadores
    carregado.value = true
  } catch (e) {
    if (g !== geracao) return
    erro.value = errosDaApi(e).geral || 'Não consegui carregar as garantias.'
  } finally {
    if (g === geracao) carregando.value = false
  }
}

async function carregarMais() {
  if (carregandoMais.value || itens.value.length >= total.value) return
  const g = geracao
  carregandoMais.value = true
  try {
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpoDaLista(filtros, itens.value.length) })
    if (g !== geracao) return
    const vistos = new Set(itens.value.map((x) => x.id))
    itens.value = [...itens.value, ...r.itens.filter((x) => !vistos.has(x.id))]
    total.value = r.total
  } catch (e) {
    toasts.error('Não consegui carregar mais', errosDaApi(e).geral)
  } finally {
    carregandoMais.value = false
  }
}

let atraso: ReturnType<typeof setTimeout> | null = null
watch(() => filtros.busca, () => {
  if (atraso) clearTimeout(atraso)
  atraso = setTimeout(carregar, 300)
})
watch(() => [filtros.status, filtros.periodo, filtros.de, filtros.ate, filtros.com_atendimento_no_mes], () => { void carregar() })

const ativo = computed(() => cartaoAtivo(filtros))
function clicarCartao(c: Cartao) {
  Object.assign(filtros, filtroDoCartao(filtros, c))
}
const temFiltro = computed(() => !!(filtros.busca.trim() || filtros.status !== 'todos' || filtros.de || filtros.ate || filtros.com_atendimento_no_mes))
function limparFiltros() {
  Object.assign(filtros, { ...FILTROS_PADRAO })
}
const buscaParcialDeCpf = computed(() => {
  const d = filtros.busca.replace(/\D/g, '')
  return /^[\d.\-\s]+$/.test(filtros.busca.trim()) && d.length > 9 && d.length < 11
})

// ─── detalhe e cadastro ─────────────────────────────────────────────────────
const abertaId = ref<number | null>(null)
const avisosDaAberta = ref<string[]>([])
const versaoDetalhe = ref(0)
const formAberto = ref(false)
const corrigindo = ref<GarantiaDetalhe | null>(null)
const pedidoInicial = ref<string | null>(null)

function abrir(id: number, avisos: string[] = []) {
  avisosDaAberta.value = avisos
  abertaId.value = id
}
function fecharDetalhe() {
  abertaId.value = null
  avisosDaAberta.value = []
}
function novaGarantia(pedido: string | null = null) {
  corrigindo.value = null
  pedidoInicial.value = pedido
  formAberto.value = true
}
function corrigir(g: GarantiaDetalhe) {
  corrigindo.value = g
  pedidoInicial.value = null
  formAberto.value = true
}
function aoSalvar(g: GarantiaDetalhe) {
  const era = corrigindo.value
  formAberto.value = false
  corrigindo.value = null
  toasts.success(era ? `Garantia #${g.id} corrigida` : `Garantia #${g.id} cadastrada`, g.data_inicio
    ? `Hardware até ${dataBR(g.fim_hardware)} · software até ${dataBR(g.fim_software)}.`
    : g.entregue_sem_data
      ? 'O pedido já consta como entregue no Bling, mas o DaVinci não tem a data de entrega: a garantia fica sem prazos.'
      : 'Aguardando a data de entrega do pedido.')
  if (era && abertaId.value === g.id) {
    avisosDaAberta.value = g.avisos || []
    versaoDetalhe.value++
  } else {
    abrir(g.id, g.avisos || [])
  }
  void carregar()
}
function abrirDoForm(id: number) {
  formAberto.value = false
  corrigindo.value = null
  abrir(id)
}

// A URL acompanha a garantia aberta (dá para mandar o link).
watch(abertaId, (id) => {
  const query = { ...route.query }
  if (id) query.garantia = String(id)
  else delete query.garantia
  delete query.novo
  void router.replace({ query })
})

onMounted(() => {
  void carregarRegras()
  void carregar()
  const id = Number(route.query.garantia)
  if (Number.isInteger(id) && id > 0) abrir(id)
  const novo = typeof route.query.novo === 'string' ? route.query.novo.trim() : ''
  if (novo) {
    if (acesso.value.cadastra) novaGarantia(novo)
    const query = { ...route.query }
    delete query.novo
    void router.replace({ query })
  }
})

const STATUS_CARTAO: Record<Cartao, keyof Indicadores> = {
  ativas: 'ativas',
  somente_software: 'somente_software',
  hw_vence_30d: 'hw_vence_30d',
  atendimentos_no_mes: 'atendimentos_no_mes',
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Painel de Garantia — Uranyx" description="Garantias dos produtos Uranyx: prazos de hardware e software a partir da entrega e os atendimentos vinculados no Atendimento.">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" variant="outline" :aria-expanded="regrasAbertas" @click="regrasAbertas = !regrasAbertas">
          <Info class="mr-1 size-4" /> Como funciona
        </Button>
        <Button v-if="acesso.cadastra" size="sm" data-nova-garantia @click="novaGarantia()"><Plus class="mr-1 size-4" /> Nova garantia</Button>
      </template>
    </PageHeader>

    <!-- regras (§2 e os pontos do §8, como o servidor diz) -->
    <section v-if="regrasAbertas" class="space-y-1.5 rounded-lg border bg-muted/30 p-4 text-sm" data-regras>
      <h2 class="font-semibold">Como a garantia é calculada</h2>
      <ul class="list-disc space-y-1 pl-5 text-muted-foreground">
        <li>A <strong class="text-foreground">data inicial</strong> é a data de entrega do pedido (não se edita). Hardware: +{{ regras.meses_hardware }} meses; software: +{{ regras.meses_software }} meses. Se o dia não existe no mês final, vale o último dia (30/11 → 28/02).</li>
        <li>{{ regras.ultimo_dia_coberto ? 'A garantia vale até a data final, inclusive.' : 'A garantia termina no dia anterior à data final.' }}</li>
        <li>{{ regras.cadastro_antes_da_entrega ? 'Pode cadastrar antes da entrega: fica 🟡 Aguardando entrega e os prazos entram sozinhos quando a data aparecer.' : 'Só dá para cadastrar depois da entrega.' }}</li>
        <li>{{ regras.garantia_por === 'nf' ? 'Uma garantia por NF (o mesmo CPF não se repete na mesma NF); os itens da NF aparecem no detalhe.' : 'Uma garantia por produto.' }}</li>
        <li>{{ regras.vinculo_automatico ? 'O Atendimento vincula sozinho pelo CPF/telefone.' : 'O vínculo com o Atendimento é manual: o atendente clica em "Vincular à garantia" e busca por CPF, NF ou pedido (a busca já vem com o pedido da conversa).' }}</li>
        <li>{{ regras.recalcular_quando_entrega_mudar ? 'Se a data de entrega for corrigida, a garantia é recalculada sozinha (fica no log).' : 'A data de entrega corrigida depois não muda a garantia.' }}</li>
        <li>Status: 🟢 Ativa (hardware e software) · 🔵 Somente software · 🔴 Expirada · 🟡 Aguardando entrega — ou 🟡 "Entregue — sem data no DaVinci", quando o Bling já dá o pedido como entregue mas nenhuma fonte tem a data (a Logística guarda a data só desde 15/07/2026). Na lista o CPF aparece mascarado.</li>
        <li>Sem escolher mensagens, a data do atendimento é a 1ª mensagem do cliente no trecho atual da conversa (uma pausa de até {{ regras.dias_pausa_atendimento }} dias não abre trecho novo).</li>
      </ul>
    </section>

    <!-- busca, status e período -->
    <div class="flex flex-wrap items-end gap-2">
      <div class="w-full sm:w-80">
        <label for="gar-busca" class="mb-1 block text-xs font-medium text-muted-foreground">Buscar</label>
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input id="gar-busca" v-model="filtros.busca" class="h-9 pl-8" placeholder="Nome / CPF / NF / Pedido" autocomplete="off" />
        </div>
      </div>
      <div class="w-[calc(50%-0.25rem)] sm:w-48">
        <label for="gar-status" class="mb-1 block text-xs font-medium text-muted-foreground">Status</label>
        <select id="gar-status" v-model="filtros.status" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
          <option v-for="s in FILTROS_STATUS" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
      </div>
      <div class="w-[calc(50%-0.25rem)] sm:w-44">
        <label for="gar-periodo" class="mb-1 block text-xs font-medium text-muted-foreground">Período por</label>
        <select id="gar-periodo" v-model="filtros.periodo" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
          <option value="cadastro">data de cadastro</option>
          <option value="entrega">data de entrega</option>
        </select>
      </div>
      <div class="flex w-full items-end gap-2 sm:w-auto">
        <div class="flex-1 sm:w-36 sm:flex-none">
          <label for="gar-de" class="mb-1 block text-xs font-medium text-muted-foreground">De</label>
          <Input id="gar-de" v-model="filtros.de" type="date" class="h-9" :max="filtros.ate || undefined" />
        </div>
        <div class="flex-1 sm:w-36 sm:flex-none">
          <label for="gar-ate" class="mb-1 block text-xs font-medium text-muted-foreground">Até</label>
          <Input id="gar-ate" v-model="filtros.ate" type="date" class="h-9" :min="filtros.de || undefined" />
        </div>
      </div>
      <Button v-if="temFiltro" size="sm" variant="ghost" class="h-9" @click="limparFiltros">limpar filtros</Button>
    </div>
    <p v-if="buscaParcialDeCpf" class="-mt-2 text-xs text-muted-foreground">Para buscar por CPF, digite os 11 dígitos (pedaço de CPF não busca).</p>

    <!-- indicadores (clicar filtra) -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4" data-indicadores>
      <button
        v-for="c in CARTOES"
        :key="c.chave"
        type="button"
        class="rounded-xl border bg-card p-3 text-left transition-colors hover:bg-muted/50 sm:p-4"
        :class="ativo === c.chave ? 'border-primary ring-1 ring-primary' : ''"
        :aria-pressed="ativo === c.chave"
        :title="`${c.dica} — clique para ${ativo === c.chave ? 'tirar o filtro' : 'filtrar'}`"
        :data-cartao="c.chave"
        @click="clicarCartao(c.chave)"
      >
        <div class="truncate text-xs font-medium uppercase tracking-wider text-muted-foreground">{{ c.rotulo }}</div>
        <div class="mt-1 text-2xl font-semibold tabular-nums" :class="c.chave === 'hw_vence_30d' && (indicadores?.hw_vence_30d || 0) > 0 ? 'text-amber-600 dark:text-amber-400' : ''">
          {{ indicadores ? indicadores[STATUS_CARTAO[c.chave]] : '—' }}
        </div>
      </button>
    </div>
    <p v-if="indicadores" class="-mt-1 text-xs text-muted-foreground">
      {{ indicadores.total }} {{ indicadores.total === 1 ? 'garantia' : 'garantias' }} ·
      <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'aguardando_entrega'; filtros.com_atendimento_no_mes = false">🟡 {{ indicadores.aguardando_entrega }} sem data de entrega</button>
      <template v-if="indicadores.entregue_sem_data">
        (<button type="button" class="underline-offset-2 hover:underline" data-entregue-sem-data @click="filtros.status = 'entregue_sem_data'; filtros.com_atendimento_no_mes = false">{{ indicadores.entregue_sem_data }} já {{ indicadores.entregue_sem_data === 1 ? 'entregue' : 'entregues' }} no Bling</button>)
      </template> ·
      <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'expirada'; filtros.com_atendimento_no_mes = false">🔴 {{ indicadores.expiradas }} expiradas</button>
    </p>

    <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
      <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar">tentar de novo</button>
    </p>

    <!-- carregando / vazio -->
    <div v-if="!carregado && !erro" class="flex items-center justify-center gap-2 rounded-lg border py-14 text-sm text-muted-foreground" data-carregando>
      <Loader2 class="size-4 animate-spin" /> carregando garantias…
    </div>
    <EmptyState
      v-else-if="carregado && !itens.length"
      :icon="ShieldCheck"
      :title="temFiltro ? 'Nenhuma garantia com esses filtros' : 'Nenhuma garantia cadastrada ainda'"
      :description="temFiltro ? 'Mude a busca, o status ou o período.' : 'Cadastre a primeira pelo + Nova garantia: informe o pedido e a data de entrega vira o início da garantia.'"
      data-vazio
    >
      <Button v-if="temFiltro" size="sm" variant="outline" @click="limparFiltros">limpar filtros</Button>
      <Button v-else-if="acesso.cadastra" size="sm" @click="novaGarantia()"><Plus class="mr-1 size-4" /> Nova garantia</Button>
    </EmptyState>

    <template v-else-if="carregado">
      <!-- tabela (tela larga) -->
      <div class="hidden overflow-x-auto rounded-lg border md:block">
        <table class="w-full text-sm" data-tabela-garantias>
          <thead class="bg-muted">
            <tr class="text-left">
              <th class="px-3 py-2.5 font-medium">Nome</th>
              <th class="whitespace-nowrap px-3 py-2.5 font-medium">CPF</th>
              <th class="px-3 py-2.5 font-medium">NF</th>
              <th class="px-3 py-2.5 font-medium">Entrega</th>
              <th class="whitespace-nowrap px-3 py-2.5 font-medium">Fim HW</th>
              <th class="whitespace-nowrap px-3 py-2.5 font-medium">Fim SW</th>
              <th class="px-3 py-2.5 font-medium">Status</th>
              <th class="px-3 py-2.5 text-right font-medium">Atend.</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="g in itens"
              :key="g.id"
              tabindex="0"
              class="cursor-pointer border-t hover:bg-muted/40 focus:outline-none focus-visible:bg-muted/60"
              :class="abertaId === g.id ? 'bg-muted/50' : ''"
              :aria-label="`abrir a garantia de ${g.cliente_nome}`"
              @click="abrir(g.id)"
              @keydown.enter="abrir(g.id)"
            >
              <td class="px-3 py-2.5">
                <div class="font-medium">{{ g.cliente_nome }}</div>
                <div class="text-xs text-muted-foreground">pedido {{ g.pedido_bling }}<template v-if="g.plataforma"> · {{ nomePlataforma(g.plataforma) }}</template></div>
              </td>
              <td class="whitespace-nowrap px-3 py-2.5 font-mono text-xs">{{ g.cpf_mascarado || '—' }}</td>
              <td class="whitespace-nowrap px-3 py-2.5 font-mono">{{ textoNf(g.nf_numero, g.nf_serie) }}</td>
              <td class="whitespace-nowrap px-3 py-2.5 tabular-nums">{{ dataCurta(g.data_inicio) }}</td>
              <td class="whitespace-nowrap px-3 py-2.5 tabular-nums">{{ dataCurta(g.fim_hardware) }}</td>
              <td class="whitespace-nowrap px-3 py-2.5 tabular-nums">{{ dataCurta(g.fim_software) }}</td>
              <td class="px-3 py-2.5"><GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" /></td>
              <td class="px-3 py-2.5 text-right tabular-nums">{{ g.atendimentos }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- cartões (celular) -->
      <ul class="space-y-2 md:hidden" data-cartoes-garantias>
        <li v-for="g in itens" :key="g.id">
          <button type="button" class="w-full space-y-1.5 rounded-lg border bg-card p-3 text-left hover:bg-muted/40" @click="abrir(g.id)">
            <div class="flex items-start gap-2">
              <div class="min-w-0 flex-1">
                <div class="truncate font-medium">{{ g.cliente_nome }}</div>
                <div class="font-mono text-xs text-muted-foreground">{{ g.cpf_mascarado || '—' }} · NF {{ textoNf(g.nf_numero, g.nf_serie) }}</div>
              </div>
              <GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" />
            </div>
            <div class="grid grid-cols-3 gap-1 text-xs">
              <div><div class="text-muted-foreground">Entrega</div><div class="tabular-nums">{{ dataCurta(g.data_inicio) }}</div></div>
              <div><div class="text-muted-foreground">Fim HW</div><div class="tabular-nums">{{ dataCurta(g.fim_hardware) }}</div></div>
              <div><div class="text-muted-foreground">Fim SW</div><div class="tabular-nums">{{ dataCurta(g.fim_software) }}</div></div>
            </div>
            <div class="text-xs text-muted-foreground">pedido {{ g.pedido_bling }} · {{ g.atendimentos }} {{ g.atendimentos === 1 ? 'atendimento' : 'atendimentos' }}</div>
          </button>
        </li>
      </ul>

      <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{{ itens.length }} de {{ total }}</span>
        <Button v-if="itens.length < total" size="sm" variant="outline" :disabled="carregandoMais" @click="carregarMais">
          <Loader2 v-if="carregandoMais" class="mr-1 size-4 animate-spin" /> carregar mais {{ Math.min(LIMITE_PAGINA, total - itens.length) }}
        </Button>
      </div>
    </template>

    <GarantiaDetalhe
      v-if="abertaId !== null && !formAberto"
      :garantia-id="abertaId"
      :regras="regras"
      :avisos="avisosDaAberta"
      :versao="versaoDetalhe"
      @fechar="fecharDetalhe"
      @corrigir="corrigir"
      @mudou="carregar"
    />
    <GarantiaForm
      v-if="formAberto"
      :garantia="corrigindo"
      :pedido-inicial="pedidoInicial"
      :regras="regras"
      @fechar="formAberto = false; corrigindo = null"
      @salva="aoSalvar"
      @abrir="abrirDoForm"
    />
  </div>
</template>
