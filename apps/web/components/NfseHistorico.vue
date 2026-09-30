<script setup lang="ts">
// Aba "Notas enviadas": todas as notas que já foram para a NFE.io, com o
// resumo que filtra (emitidas, para resolver, canceladas), busca, mês,
// empresa e ambiente (teste × produção). Cada linha tem UMA ação principal
// (PDF, atualizar, reenviar) + menu ⋯ (NfseAcoesNota) e o clique na linha abre
// o detalhe da nota (NfseHistoricoNota, montado pela página).
//
// 28/09/2026 (Eduardo: "simples e bagunçado"): reescrita sem props — tudo vem
// de useNfseTela(). Atualizar, reenviar e cancelar passam pela tela (uma
// janela só para cada coisa, sem modais caseiros).
//
// 29/09/2026 (motor NFE.io): o ambiente é de cada nota (a empresa estava em
// Teste ou em Produção na NFE.io quando ela saiu). A lista traz as duas e o
// filtro Teste × Produção é daqui; nota de teste ganha o selo "teste".
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import {
  Ban, CheckCircle2, ChevronDown, FileText, FlaskConical, HelpCircle, Loader2, Search, SearchX, Send, ShieldAlert, X,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  erroApi, fmtBrl, fmtCompetencia, fmtDataHora, fmtPctOrigem, mesParaData, origemDaEmissao, plural, prestadorPorId,
  STATUS_PARA_RESOLVER, tomadorDaEmissao, useNfseTela, type Emissao,
} from '~/lib/nfse'

type Grupo = 'emitidas' | 'resolver' | 'canceladas'
type Resolver = (typeof STATUS_PARA_RESOLVER)[number]

const GRUPOS: readonly Grupo[] = ['emitidas', 'resolver', 'canceladas']
const PARA_RESOLVER = new Set<string>(STATUS_PARA_RESOLVER)
const AUTORIZADAS = new Set<string>(['emitida', 'cancelando', 'cancelada'])
const ROTULO_RESOLVER: Record<Resolver, string> = {
  rejeitada: 'Recusadas',
  processando: 'Na prefeitura',
  incerta: 'Sem resposta',
  enviando: 'Enviando',
  cancelando: 'Cancelando',
}
// O servidor devolve no máximo as 500 mais recentes (routers/nfse.py).
const LIMITE_SERVIDOR = 500

const tela = useNfseTela()
const route = useRoute()
const { api } = useApi()

function grupoDaQuery(v: unknown): Grupo | null {
  return typeof v === 'string' && (GRUPOS as readonly string[]).includes(v) ? (v as Grupo) : null
}

// --- Filtros -------------------------------------------------------------------

// Do servidor: mês ('' = todos) e empresa ('' = todas).
const mes = ref('')
const empresa = ref('')
// Locais: ambiente (as duas, só teste, só produção), busca, grupo (resumo
// clicável ou ?grupo=) e, dentro de "Para resolver", a situação exata
// (recusadas, na prefeitura, sem resposta…).
const amb = ref<'todas' | 'teste' | 'producao'>('todas')
const busca = ref('')
const grupo = ref<Grupo | null>(grupoDaQuery(route.query.grupo))
const situacaoResolver = ref('todas')

const ambModel = computed({
  get: () => amb.value as string,
  set: (v: string) => {
    amb.value = v === 'teste' || v === 'producao' ? v : 'todas'
  },
})

const opcoesAmb = [
  { id: 'todas', rotulo: 'Todas' },
  { id: 'teste', rotulo: 'Teste', icone: FlaskConical },
  { id: 'producao', rotulo: 'Produção', icone: ShieldAlert },
]

// ?grupo=resolver (ex.: contador da aba) liga o filtro. Sair da aba tira o
// parâmetro da URL, mas o filtro continua aqui (KeepAlive); ao voltar, a URL
// volta a mostrá-lo.
watch(
  () => route.query.grupo,
  (v) => {
    const g = grupoDaQuery(v)
    if (g && g !== grupo.value) {
      grupo.value = g
      situacaoResolver.value = 'todas'
    }
  },
)

onActivated(() => {
  if (grupo.value && route.query.grupo !== grupo.value) tela.irPara('notas', { grupo: grupo.value })
})

function filtrarGrupo(g: Grupo | null) {
  grupo.value = g && grupo.value !== g ? g : null
  situacaoResolver.value = 'todas'
  tela.irPara('notas', { grupo: grupo.value ?? undefined })
}

const temFiltro = computed(
  () => !!(mes.value || empresa.value || busca.value.trim() || grupo.value || amb.value !== 'todas'),
)

function limparFiltros() {
  mes.value = ''
  empresa.value = ''
  busca.value = ''
  amb.value = 'todas'
  const tinhaGrupo = !!grupo.value
  grupo.value = null
  situacaoResolver.value = 'todas'
  if (tinhaGrupo) tela.irPara('notas', { grupo: undefined })
}

// --- Dados ---------------------------------------------------------------------

const linhas = ref<Emissao[]>([])
const carregando = ref(false)
const carregou = ref(false)
const erro = ref<string | null>(null)
// Empresas que já apareceram em alguma lista (a lista filtrada por empresa
// só traz uma — o seletor não pode encolher junto).
const vistas = ref(new Map<string, string>())
let seq = 0

// Espera a casca terminar a 1ª carga (ou falhar) para buscar as notas.
const pronto = computed(
  () => tela.carregado.value || tela.status.value != null || tela.erroStatus.value != null,
)

async function carregar() {
  const meu = ++seq
  carregando.value = true
  try {
    const q = new URLSearchParams()
    if (mes.value) q.set('competencia', mesParaData(mes.value))
    if (empresa.value) q.set('company_id', empresa.value)
    const qs = q.toString()
    const r = await api<Emissao[]>(`/api/nfse/emissoes${qs ? `?${qs}` : ''}`)
    if (meu !== seq) return
    linhas.value = r
    erro.value = null
    carregou.value = true
    const m = new Map(vistas.value)
    for (const l of r) if (!m.has(l.company_id)) m.set(l.company_id, l.prestador_nome || '')
    vistas.value = m
  } catch (e) {
    if (meu !== seq) return
    erro.value = erroApi(e)
  } finally {
    if (meu === seq) carregando.value = false
  }
}

watch([mes, empresa, tela.versao, pronto], () => {
  if (pronto.value) carregar()
})
onMounted(() => {
  if (pronto.value) carregar()
})

// Ação feita na linha (atualizar, reenviar, cancelar): troca no lugar. A tela
// também recarrega e a lista vem de novo do servidor logo depois.
function trocar(nova: Emissao) {
  const i = linhas.value.findIndex((l) => l.id === nova.id)
  if (i >= 0) linhas.value.splice(i, 1, nova)
}

function abrir(l: Emissao) {
  // Selecionar texto da linha (copiar o nº, por exemplo) não abre o detalhe.
  if (typeof window !== 'undefined' && window.getSelection()?.toString()) return
  tela.abrirNota(l)
}

// --- Listas derivadas ----------------------------------------------------------

function normalizar(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()
}

const doAmbiente = computed(() => {
  if (amb.value === 'teste') return linhas.value.filter((l) => l.teste)
  if (amb.value === 'producao') return linhas.value.filter((l) => !l.teste)
  return linhas.value
})

const buscadas = computed(() => {
  const t = normalizar(busca.value)
  if (!t) return doAmbiente.value
  const digitos = t.replace(/\D/g, '')
  return doAmbiente.value.filter((l) => {
    const texto = normalizar(
      [l.n_nfse ?? '', l.prestador_nome ?? '', l.tomador_nome ?? '', l.check_code ?? ''].join(' '),
    )
    if (texto.includes(t)) return true
    // CNPJ/CPF do tomador, digitado com ou sem pontuação.
    const doc = String(l.snapshot?.tomador?.documento ?? '').replace(/\D/g, '')
    return digitos.length >= 4 && doc.includes(digitos)
  })
})

function doGrupo(l: Emissao, g: Grupo): boolean {
  if (g === 'emitidas') return l.status === 'emitida'
  if (g === 'canceladas') return l.status === 'cancelada'
  return PARA_RESOLVER.has(l.status)
}

const resumo = computed(() => {
  let emitidas = 0
  let resolver = 0
  let canceladas = 0
  let somaEmitidas = 0
  const porSituacao: Record<string, number> = {}
  for (const l of buscadas.value) {
    if (l.status === 'emitida') {
      emitidas++
      somaEmitidas += Number(l.valor_servico) || 0
    } else if (l.status === 'cancelada') {
      canceladas++
    } else if (PARA_RESOLVER.has(l.status)) {
      resolver++
      porSituacao[l.status] = (porSituacao[l.status] ?? 0) + 1
    }
  }
  return { total: buscadas.value.length, emitidas, resolver, canceladas, somaEmitidas, porSituacao }
})

// Dentro de "Para resolver": escolher a situação exata (só com 2 ou mais tipos).
const opcoesResolver = computed(() => {
  const n = resumo.value.porSituacao
  const tipos = STATUS_PARA_RESOLVER.filter((s) => n[s])
  if (tipos.length < 2) return []
  return [
    { id: 'todas', rotulo: 'Todas', contador: resumo.value.resolver, tomContador: 'neutro' as const },
    ...tipos.map((s) => ({ id: s, rotulo: ROTULO_RESOLVER[s], contador: n[s] ?? 0, tomContador: 'neutro' as const })),
  ]
})

const situacaoEfetiva = computed(() =>
  opcoesResolver.value.some((o) => o.id === situacaoResolver.value) ? situacaoResolver.value : 'todas',
)

const visiveis = computed(() => {
  const g = grupo.value
  let r = buscadas.value
  if (g) r = r.filter((l) => doGrupo(l, g))
  if (g === 'resolver' && situacaoEfetiva.value !== 'todas') r = r.filter((l) => l.status === situacaoEfetiva.value)
  return r
})

const somaVisiveis = computed(() =>
  visiveis.value.filter((l) => l.status === 'emitida').reduce((s, l) => s + (Number(l.valor_servico) || 0), 0),
)

type Cartao = {
  id: Grupo | null
  rotulo: string
  valor: string | number
  icone: typeof FileText
  tom?: 'success' | 'warning'
  dica?: string
}

const cartoes = computed<Cartao[]>(() => {
  const r = resumo.value
  const v = (n: number) => (carregou.value ? n : '—')
  return [
    {
      id: null,
      rotulo: 'Notas',
      valor: v(r.total),
      icone: FileText,
      dica: carregou.value ? `${fmtBrl(r.somaEmitidas)} emitidos` : undefined,
    },
    { id: 'emitidas', rotulo: 'Emitidas', valor: v(r.emitidas), icone: CheckCircle2, tom: 'success' },
    {
      id: 'resolver',
      rotulo: 'Para resolver',
      valor: v(r.resolver),
      icone: HelpCircle,
      tom: 'warning',
      dica: 'recusadas, na prefeitura ou sem resposta',
    },
    { id: 'canceladas', rotulo: 'Canceladas', valor: v(r.canceladas), icone: Ban },
  ]
})

const opcoesEmpresa = computed(() => {
  const ids = new Set<string>([...vistas.value.keys(), ...linhas.value.map((l) => l.company_id)])
  if (empresa.value) ids.add(empresa.value)
  return [...ids]
    .map((id) => ({
      id,
      nome: prestadorPorId(tela.prestadores.value, id)?.apelido || vistas.value.get(id) || 'Empresa',
    }))
    .sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'))
})

// Selo "teste" na linha só quando a lista mistura os dois ambientes; filtrando
// Teste, o aviso vai uma vez só, no rodapé da tabela.
const misturaAmbientes = computed(() => amb.value === 'todas')

// --- Linha -----------------------------------------------------------------------

function marca(l: Emissao): string {
  if (l.status === 'rejeitada') return 'border-l-2 border-l-red-500 dark:border-l-red-400'
  if (l.status === 'incerta' || l.status === 'enviando' || l.status === 'cancelando' || l.status === 'processando') {
    return 'border-l-2 border-l-amber-500 dark:border-l-amber-400'
  }
  return ''
}

// Nota de percentual (29/09): "0,5% de R$ 200.000,00" embaixo do valor.
function deOnde(l: Emissao): string {
  const pct = l.percentual ?? l.snapshot?.servico?.percentual ?? null
  const base = l.base_calculo ?? l.snapshot?.servico?.base_calculo ?? null
  return pct != null && base != null ? `${fmtPctOrigem(pct, origemDaEmissao(l))} de ${fmtBrl(base)}` : ''
}

function motivoRecusa(l: Emissao): string {
  const m = l.erros?.[0]
  return m?.o_que_fazer || m?.descricao || l.flow_message || 'veja o motivo no detalhe'
}
</script>

<template>
  <section class="space-y-4">
    <!-- Resumo: clicar filtra; clicar de novo tira o filtro -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <button
        v-for="c in cartoes"
        :key="c.rotulo"
        type="button"
        class="rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="c.id && grupo === c.id && 'ring-2 ring-primary'"
        :title="c.id ? (grupo === c.id ? 'tirar o filtro' : `filtrar: ${c.rotulo}`) : 'mostrar todas'"
        :aria-pressed="c.id ? grupo === c.id : undefined"
        @click="filtrarGrupo(c.id)"
      >
        <StatCard class="h-full" :label="c.rotulo" :value="c.valor" :icon="c.icone" :tone="c.tom" :hint="c.dica" compact />
      </button>
    </div>

    <!-- Filtros -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative w-full sm:w-72">
        <Search
          class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          v-model="busca"
          type="search"
          autocomplete="off"
          placeholder="buscar nº, empresa, tomador…"
          aria-label="buscar nota por número, empresa ou tomador"
          class="h-9 w-full rounded-md border bg-background pl-8 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-search-cancel-button]:hidden"
          :class="busca ? 'pr-8' : 'pr-3'"
        />
        <button
          v-if="busca"
          type="button"
          class="absolute right-1.5 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label="limpar busca"
          @click="busca = ''"
        >
          <X class="size-3.5" aria-hidden="true" />
        </button>
      </div>
      <NfseMesPicker v-model="mes" permitir-todos />
      <div class="relative">
        <select
          v-model="empresa"
          aria-label="empresa que emitiu"
          class="h-9 max-w-[220px] appearance-none rounded-md border bg-background pl-3 pr-8 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:[color-scheme:dark]"
        >
          <option value="">todas as empresas</option>
          <option v-for="p in opcoesEmpresa" :key="p.id" :value="p.id">{{ p.nome }}</option>
        </select>
        <ChevronDown
          class="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
      </div>
      <NfseSegmentado v-model="ambModel" tamanho="sm" :opcoes="opcoesAmb" />
      <Button v-if="temFiltro" size="sm" variant="ghost" @click="limparFiltros">
        <X class="mr-1.5 size-4" aria-hidden="true" /> limpar filtros
      </Button>
      <span class="ml-auto inline-flex items-center gap-1.5 text-xs tabular-nums text-muted-foreground">
        <Loader2
          v-if="carregando && carregou"
          class="size-3.5 animate-spin motion-reduce:animate-none"
          aria-label="atualizando"
        />
        <template v-if="carregou">{{ plural(visiveis.length, 'nota', 'notas') }}</template>
      </span>
    </div>

    <!-- Dentro de "Para resolver": como resolver + a situação exata -->
    <template v-if="grupo === 'resolver' && carregou && resumo.resolver">
      <NfseAviso tom="info" titulo="Como resolver">
        <p>
          <span class="font-medium">Na prefeitura:</span> a NFE.io está esperando a prefeitura autorizar. O DaVinci
          confere sozinho a cada 2 minutos; para ver agora, clique em <span class="font-medium">atualizar</span>.
        </p>
        <p>
          <span class="font-medium">Sem resposta:</span> clique em <span class="font-medium">atualizar</span>
          antes de qualquer coisa. Não reenvie: a nota pode já ter virado nota.
        </p>
        <p>
          <span class="font-medium">Recusada:</span> corrija o que foi apontado e clique em
          <span class="font-medium">reenviar</span>. Vai a mesma nota de novo, sem risco de nota em dobro.
        </p>
      </NfseAviso>
      <div v-if="opcoesResolver.length" class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-muted-foreground">Mostrar:</span>
        <NfseSegmentado v-model="situacaoResolver" tamanho="sm" :opcoes="opcoesResolver" />
      </div>
    </template>

    <NfseAviso v-if="erro" tom="perigo" titulo="Não carregou as notas">
      {{ erro }}
      <template #acoes>
        <Button size="sm" variant="outline" :disabled="carregando" @click="carregar">tentar de novo</Button>
      </template>
    </NfseAviso>

    <!-- 1ª carga -->
    <NfseSkeletonTabela v-if="!carregou && !erro" :linhas="8" :colunas="6" />

    <!-- Nada enviado (com os filtros padrão) -->
    <EmptyState
      v-else-if="carregou && !linhas.length && !temFiltro"
      :icon="FileText"
      title="Nenhuma nota enviada ainda"
      description="Quando você emitir, as notas aparecem aqui com PDF, XML e a resposta da prefeitura."
    >
      <div class="flex flex-wrap justify-center gap-2">
        <Button size="sm" @click="tela.irPara('emitir')">
          <Send class="mr-1.5 size-4" aria-hidden="true" /> ir para Emitir do mês
        </Button>
      </div>
    </EmptyState>

    <!-- Nada com os filtros -->
    <EmptyState
      v-else-if="carregou && !visiveis.length"
      :icon="SearchX"
      title="Nada com esses filtros"
      description="Nenhuma nota bate com a busca, o mês, a empresa ou o ambiente escolhidos."
    >
      <Button size="sm" variant="outline" @click="limparFiltros">
        <X class="mr-1.5 size-4" aria-hidden="true" /> limpar filtros
      </Button>
    </EmptyState>

    <!-- Lista -->
    <div v-else-if="carregou" class="table-card tabela-nfse">
      <div class="relative overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th class="whitespace-nowrap">Nº</th>
              <th>Mês</th>
              <th>Empresa → tomador</th>
              <th class="!text-right">Valor</th>
              <th class="hidden whitespace-nowrap lg:table-cell">Emitida em</th>
              <th>Situação</th>
              <th class="col-acoes w-px"><span class="sr-only">Ações</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="l in visiveis"
              :key="l.id"
              class="cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              tabindex="0"
              :aria-label="`abrir a nota ${l.n_nfse ? 'nº ' + l.n_nfse : 'ainda sem número'}`"
              @click="abrir(l)"
              @keydown.enter.self="abrir(l)"
            >
              <td class="whitespace-nowrap" :class="marca(l)">
                <div class="flex items-center gap-1.5">
                  <span v-if="l.n_nfse" class="font-medium tabular-nums">{{ l.n_nfse }}</span>
                  <span v-else class="text-muted-foreground">—</span>
                  <span v-if="!l.modelo_id" class="pill-muted">avulsa</span>
                </div>
              </td>
              <td class="whitespace-nowrap tabular-nums text-muted-foreground">
                {{ fmtCompetencia(l.competencia) }}
              </td>
              <td class="min-w-[180px]">
                <div class="font-medium" :class="l.status === 'cancelada' && 'text-muted-foreground'">
                  {{ l.prestador_nome || '—' }}
                </div>
                <div class="text-xs text-muted-foreground">para {{ tomadorDaEmissao(l) }}</div>
              </td>
              <td class="whitespace-nowrap text-right tabular-nums">
                <div
                  :class="l.status === 'cancelada' && 'text-muted-foreground line-through decoration-muted-foreground/60'"
                >
                  {{ fmtBrl(l.valor_servico) }}
                </div>
                <div v-if="deOnde(l)" class="text-xs text-muted-foreground" title="percentual sobre a base de cálculo">
                  {{ deOnde(l) }}
                </div>
              </td>
              <td class="hidden whitespace-nowrap tabular-nums text-muted-foreground lg:table-cell">
                {{ AUTORIZADAS.has(l.status) && l.dh_emi ? fmtDataHora(l.dh_emi) : '—' }}
              </td>
              <td>
                <NfseSituacao :estado="l.status" :teste="misturaAmbientes && l.teste" />
                <div
                  v-if="l.status === 'rejeitada'"
                  class="mt-0.5 max-w-[260px] truncate text-xs text-red-600 dark:text-red-400"
                  :title="motivoRecusa(l)"
                >
                  {{ motivoRecusa(l) }}
                </div>
                <div
                  v-else-if="l.status === 'incerta'"
                  class="mt-0.5 text-xs text-amber-600 dark:text-amber-400"
                >
                  não reenvie: atualize
                </div>
                <div v-else-if="l.status === 'processando'" class="mt-0.5 text-xs text-muted-foreground">
                  esperando a prefeitura
                </div>
              </td>
              <td class="col-acoes w-px whitespace-nowrap text-right">
                <NfseAcoesNota :emissao="l" @abrir="tela.abrirNota(l)" @atualizada="trocar" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-2 text-xs text-muted-foreground">
        <span>
          {{ plural(visiveis.length, 'nota', 'notas') }} · emitidas somam
          <span class="font-medium tabular-nums text-foreground">{{ fmtBrl(somaVisiveis) }}</span>
        </span>
        <span v-if="linhas.length >= LIMITE_SERVIDOR">
          · mostrando as {{ LIMITE_SERVIDOR }} mais recentes: escolha um mês para ver as mais antigas
        </span>
        <span
          v-if="amb === 'teste'"
          class="ml-auto inline-flex items-center gap-1 text-amber-700 dark:text-amber-400"
        >
          <FlaskConical class="size-3.5" aria-hidden="true" /> notas de teste: simuladas, sem valor fiscal
        </span>
      </div>
    </div>
  </section>
</template>
