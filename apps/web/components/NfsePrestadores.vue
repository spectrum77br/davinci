<script setup lang="ts">
// Aba "Empresas" da Emissão de Serviço: o que cada empresa do grupo precisa
// para emitir NFS-e. A gaveta NfsePrestadoresSheet (montada pela página, aberta
// por tela.abrirEmpresa) tem o cartão da NFE.io e o serviço prestado.
//
// 28/09/2026 (Eduardo: "to achando simples e bagunçado"): reescrita sem
// props — resumo clicável em cima, busca + Em uso/Todas, tabela enxuta com
// "O que falta" num popover que leva direto ao conserto.
//
// 29/09 (Eduardo: "a porcentagem de cada empresa que temos"): a % padrão das
// notas de percentual aparece embaixo do nome (só leitura; muda em Cadastros ›
// Empresas — a gaveta tem o link).
//
// 29/09/2026 (motor NFE.io): município, regime, certificado e inscrição
// municipal agora vêm do cadastro da empresa na NFE.io (só leitura aqui). A
// tabela mostra o ambiente de cada uma (Teste × Produção) e o admin tem o
// "Sincronizar com a NFE.io", que liga pelo CNPJ e mostra o que sobrou dos dois
// lados. Nada é emitido nem mudado na NFE.io: só leitura lá.
//
// 01/10/2026 (Eduardo: "algumas empresas nossas não estão integradas no nfe.io,
// precisa integrar ... com filtro para ver só os pendentes"): 3º escopo
// "Pendentes" (não integradas ou com a integração incompleta), "Em uso" passa a
// contar a empresa com loja cadastrada (em operação) e cada linha pendente ganha
// o botão "Integrar na NFE.io" / "Completar integração" (diálogo
// NfseIntegrarDialog, montado pela página). O "Sem NFE.io" agora conta de verdade.
import { computed, onMounted, ref } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import {
  AlertCircle, AlertTriangle, Building2, CheckCircle2, ExternalLink, KeyRound, Loader2, PlugZap, RefreshCw, Search,
  SearchX, Settings2, Unplug, X,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  erroApi, fmtDoc, fmtHora, fmtPct, pctPositivo, pendenciaTexto, plural, regimeTexto, situacaoCertificado,
  situacaoFiscalTexto, soDigitos, textoCertificadoGuardado, TOM_TEXTO, useNfseTela,
  type AmbienteNfeio, type ChecklistItem, type Prestador, type Sincronizacao,
  useNfseApi,
} from '~/lib/nfse'

const tela = useNfseTela()
const { prestadores, modelos, carregado, carregando, canEdit, isAdmin, podeAbrirCadastroEmpresa } = tela
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

// Esqueleto na 1ª carga — inclusive no HTML do servidor, antes de a página
// começar a buscar (a carga só começa no navegador). Recarregar mantém a lista.
const montado = ref(false)
onMounted(() => {
  montado.value = true
})
const esqueleto = computed(() => !carregado.value && (carregando.value || !montado.value))

// --- Filtros -------------------------------------------------------------------

type Cartao = 'prontas' | 'pendencia' | 'certificado' | 'nao_ligadas'

const busca = ref('')
type Escopo = 'uso' | 'pendentes' | 'todas'
const escopo = ref<Escopo>('uso')
const cartao = ref<Cartao | null>(null)

const escopoModel = computed({
  get: () => escopo.value as string,
  set: (v: string) => {
    escopo.value = v === 'todas' || v === 'pendentes' ? v : 'uso'
  },
})

// Ainda não integrada na NFE.io, ou ligada mas sem certificado/inscrição lá.
function pendenteIntegracao(p: Prestador): boolean {
  return p.integracao !== 'ok'
}
// Conta em TODAS as empresas (não só nas em uso): é o que falta integrar.
const nPendentes = computed(() => prestadores.value.filter(pendenteIntegracao).length)

const OPCOES_ESCOPO = computed(() => [
  { id: 'uso', rotulo: 'Em uso', dica: 'Integradas na NFE.io, com loja cadastrada, já configuradas ou usadas em nota fixa' },
  {
    id: 'pendentes',
    rotulo: 'Pendentes',
    contador: nPendentes.value,
    tomContador: 'atencao' as const,
    dica: 'Ainda não integradas na NFE.io (ou com a integração incompleta)',
  },
  { id: 'todas', rotulo: 'Todas' },
])

// Notas fixas ativas por empresa (quem emite).
const notasFixas = computed(() => {
  const m = new Map<string, number>()
  for (const x of modelos.value) {
    if (!x.ativo) continue
    m.set(x.company_id, (m.get(x.company_id) ?? 0) + 1)
  }
  return m
})
function usoDe(p: Prestador): number {
  return notasFixas.value.get(p.company_id) ?? 0
}

// Empresa usada em nota fixa ativa entra sempre, mesmo sem NFE.io: é
// justamente a que trava o Emitir do mês (e o selo da aba). 01/10: e a que tem
// loja cadastrada (em operação), mesmo sem NFE.io — é a que falta integrar.
function emUso(p: Prestador): boolean {
  return !!p.nfeio || !!p.fiscal || usoDe(p) > 0 || p.em_operacao
}
// Certificado na NFE.io vencido ou vencendo (em Teste ele é opcional).
function certificadoAtencao(p: Prestador): boolean {
  if (!p.nfeio || p.nfeio.teste) return false
  const s = situacaoCertificado(p)
  return s.vencido || s.tom === 'atencao'
}

const CRITERIO: Record<Cartao, (p: Prestador) => boolean> = {
  prontas: (p) => p.pronto,
  pendencia: (p) => !p.pronto,
  certificado: certificadoAtencao,
  nao_ligadas: pendenteIntegracao,
}

// Base do resumo: o escopo escolhido (padrão "Em uso"), sem busca nem cartão.
const base = computed(() => {
  if (escopo.value === 'uso') return prestadores.value.filter(emUso)
  if (escopo.value === 'pendentes') return prestadores.value.filter(pendenteIntegracao)
  return prestadores.value
})

const resumo = computed(() => {
  const b = base.value
  const pend = b.filter(CRITERIO.pendencia)
  return {
    prontas: b.filter(CRITERIO.prontas).length,
    pendencia: pend.length,
    pendenciaUsadas: pend.filter((p) => usoDe(p) > 0).length,
    certificado: b.filter(CRITERIO.certificado).length,
    naoLigadas: b.filter(CRITERIO.nao_ligadas).length,
  }
})

const cartoes = computed(() => [
  { id: 'prontas' as const, rotulo: 'Prontas para emitir', icone: CheckCircle2, tom: 'success' as const, valor: resumo.value.prontas, hint: undefined },
  {
    id: 'pendencia' as const,
    rotulo: 'Com pendência',
    icone: AlertCircle,
    tom: 'danger' as const,
    valor: resumo.value.pendencia,
    hint: `usadas em notas fixas: ${resumo.value.pendenciaUsadas}`,
  },
  {
    id: 'certificado' as const,
    rotulo: 'Certificado vencendo',
    icone: KeyRound,
    tom: 'warning' as const,
    valor: resumo.value.certificado,
    hint: 'vencido ou nos próximos 30 dias',
  },
  {
    id: 'nao_ligadas' as const,
    rotulo: 'Sem NFE.io',
    icone: Unplug,
    tom: 'warning' as const,
    valor: resumo.value.naoLigadas,
    hint: 'não integradas ou incompletas',
  },
])

function alternarCartao(c: Cartao) {
  cartao.value = cartao.value === c ? null : c
}

function ordem(p: Prestador): number {
  const usada = usoDe(p) > 0
  if (usada && !p.pronto) return 0
  if (usada) return 1
  if (p.nfeio) return 2
  return 3
}

const lista = computed(() => {
  const q = busca.value.trim().toLowerCase()
  const qd = soDigitos(q)
  const filtro = cartao.value ? CRITERIO[cartao.value] : null
  return base.value
    .filter((p) => {
      if (filtro && !filtro(p)) return false
      if (!q) return true
      if (`${p.apelido} ${p.razao_social}`.toLowerCase().includes(q)) return true
      return qd.length >= 2 && soDigitos(p.cnpj).includes(qd)
    })
    .sort(
      (a, b) =>
        ordem(a) - ordem(b) ||
        Number(b.em_operacao) - Number(a.em_operacao) ||
        a.apelido.localeCompare(b.apelido, 'pt-BR', { sensitivity: 'base' }),
    )
})

const temFiltro = computed(() => !!busca.value.trim() || !!cartao.value)

function mostrarTodas() {
  escopo.value = 'todas'
}

function mostrarEmUso() {
  escopo.value = 'uso'
}

// "Integrar na NFE.io" / "Completar integração": o diálogo da página cuida do
// aviso (toast) e de recarregar a lista.
async function integrar(p: Prestador) {
  popoverDe.value = null
  await tela.integrarEmpresa(p)
}

function limparFiltros() {
  busca.value = ''
  cartao.value = null
}

// --- Linha ---------------------------------------------------------------------

function municipio(p: Prestador): string {
  const n = p.nfeio
  if (!n?.municipio) return '—'
  return n.uf ? `${n.municipio}/${n.uf}` : n.municipio
}

function linhaAtencao(p: Prestador): boolean {
  return !p.pronto && usoDe(p) > 0
}

// "O que falta" (popover da coluna Situação): um por linha, controlado aqui
// para fechar ao escolher um item.
const popoverDe = ref<string | null>(null)

function faltando(p: Prestador): ChecklistItem[] {
  return p.pendencias.map((txt) => {
    const info = pendenciaTexto(txt)
    let acao: string | undefined
    if (info.alvo === 'empresa') acao = canEdit.value ? 'corrigir' : 'ver'
    else if (podeAbrirCadastroEmpresa.value) acao = 'abrir cadastro'
    return { chave: txt, titulo: info.texto, ok: false, acao }
  })
}

// CNPJ se resolve em Cadastros › Empresas (outra tela).
function faltaNoCadastro(p: Prestador): boolean {
  return p.pendencias.some((t) => pendenciaTexto(t).alvo === 'cadastro')
}

function mudarPopover(p: Prestador, v: boolean) {
  popoverDe.value = v ? p.company_id : null
}

function resolver(p: Prestador, chave: string) {
  popoverDe.value = null
  const info = pendenciaTexto(chave)
  if (info.alvo === 'empresa') {
    void tela.abrirEmpresa(p.company_id, info.foco)
    return
  }
  if (podeAbrirCadastroEmpresa.value) {
    window.open(`/companies/${p.company_id}`, '_blank', 'noopener')
    return
  }
  // Sem acesso ao cadastro: a gaveta mostra as pendências e a quem pedir.
  void tela.abrirEmpresa(p.company_id)
}

function abrir(p: Prestador) {
  popoverDe.value = null
  void tela.abrirEmpresa(p.company_id)
}

// --- Sincronizar com a NFE.io (admin) --------------------------------------------

const sincronizando = ref(false)
const sincronizacao = ref<Sincronizacao | null>(null)
const sincronizadoEm = ref<Date | null>(null)

function nomeAmbiente(a: AmbienteNfeio | string | null | undefined): string {
  if (a === 'Production') return 'Produção'
  if (a === 'Development' || a === 'Staging') return 'Teste'
  return 'ambiente ?'
}

async function sincronizar() {
  if (!isAdmin.value || sincronizando.value) return
  const ok = await tela.confirmar({
    titulo: 'Sincronizar com a NFE.io?',
    texto:
      'Liga cada empresa do DaVinci à empresa da NFE.io com o mesmo CNPJ e atualiza ambiente, certificado, regime e situação. Só lê a NFE.io: nada é emitido nem mudado lá.',
    botao: 'Sincronizar',
  })
  if (!ok) return
  sincronizando.value = true
  try {
    sincronizacao.value = await api<Sincronizacao>('/api/nfse/nfeio/sincronizar', { method: 'POST' })
    sincronizadoEm.value = new Date()
    toasts.success('Empresas sincronizadas com a NFE.io')
    await tela.recarregar()
  } catch (e) {
    toasts.error('Não deu para sincronizar com a NFE.io', erroApi(e))
  } finally {
    sincronizando.value = false
  }
}
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-start gap-x-4 gap-y-2">
      <p class="min-w-0 flex-1 text-sm text-muted-foreground">
        Para emitir, a empresa precisa estar integrada na NFE.io (lá ficam o certificado, o regime e a inscrição
        municipal) e ter o serviço prestado. Empresa ainda não integrada: use Integrar na NFE.io (o DaVinci manda os
        dados da Receita e o certificado guardado). O CNPJ se cadastra em
        <NuxtLink
          v-if="podeAbrirCadastroEmpresa"
          to="/companies"
          class="inline-flex items-center gap-0.5 text-primary hover:underline"
        >Cadastros › Empresas<ExternalLink class="size-3" aria-hidden="true" /></NuxtLink>
        <span v-else class="text-foreground">Cadastros › Empresas</span>.
      </p>
      <NfseDica
        v-if="isAdmin"
        texto="Liga as empresas pelo CNPJ e atualiza ambiente, certificado e situação. Só lê a NFE.io."
      >
        <Button size="sm" variant="outline" :disabled="sincronizando" @click="sincronizar">
          <Loader2 v-if="sincronizando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
          Sincronizar com a NFE.io
        </Button>
      </NfseDica>
    </div>

    <!-- Resultado da sincronização (admin) -->
    <div v-if="sincronizacao" class="space-y-2 rounded-lg border bg-muted/20 p-3 text-sm" role="status">
      <div class="flex items-center justify-between gap-2">
        <p class="font-medium">
          Sincronizado com a NFE.io<template v-if="sincronizadoEm"> às {{ fmtHora(sincronizadoEm) }}</template>
        </p>
        <Button variant="ghost" size="icon" class="size-7" aria-label="fechar o resultado" @click="sincronizacao = null">
          <X class="size-4" aria-hidden="true" />
        </Button>
      </div>
      <details class="group">
        <summary class="cursor-pointer select-none" :class="TOM_TEXTO.sucesso">
          <CheckCircle2 class="mr-1 inline size-4 align-text-bottom" aria-hidden="true" />
          {{ plural(sincronizacao.ligadas.length, 'empresa ligada', 'empresas ligadas') }}
        </summary>
        <ul class="mt-1 space-y-0.5 pl-6 text-xs text-muted-foreground">
          <li v-for="x in sincronizacao.ligadas" :key="x.company_id">
            <span class="text-foreground">{{ x.apelido }}</span> → {{ x.nfeio_nome }} · {{ nomeAmbiente(x.ambiente) }}
          </li>
        </ul>
      </details>
      <details v-if="sincronizacao.so_na_nfeio.length" class="group" open>
        <summary class="cursor-pointer select-none" :class="TOM_TEXTO.atencao">
          <AlertTriangle class="mr-1 inline size-4 align-text-bottom" aria-hidden="true" />
          {{ plural(sincronizacao.so_na_nfeio.length, 'empresa só na NFE.io', 'empresas só na NFE.io') }}
        </summary>
        <p class="mt-1 pl-6 text-xs text-muted-foreground">
          Estão na NFE.io, mas não no DaVinci. Para emitir por aqui, cadastre em Cadastros › Empresas com o mesmo CNPJ
          e sincronize de novo.
        </p>
        <ul class="mt-1 space-y-0.5 pl-6 text-xs text-muted-foreground">
          <li v-for="x in sincronizacao.so_na_nfeio" :key="x.nfeio_id">
            <span class="text-foreground">{{ x.nome }}</span>
            <template v-if="x.cnpj"> · <span class="tabular-nums">{{ fmtDoc(x.cnpj) }}</span></template>
            · {{ nomeAmbiente(x.ambiente) }}
          </li>
        </ul>
      </details>
      <details v-if="sincronizacao.so_no_davinci.length" class="group" open>
        <summary class="cursor-pointer select-none" :class="TOM_TEXTO.atencao">
          <Unplug class="mr-1 inline size-4 align-text-bottom" aria-hidden="true" />
          {{ plural(sincronizacao.so_no_davinci.length, 'empresa só no DaVinci', 'empresas só no DaVinci') }}
        </summary>
        <p class="mt-1 pl-6 text-xs text-muted-foreground">
          Não achamos na NFE.io pelo CNPJ. Se ela existir lá com outro cadastro, abra a empresa e cole o link da NFE.io.
        </p>
        <ul class="mt-1 space-y-0.5 pl-6 text-xs text-muted-foreground">
          <li v-for="x in sincronizacao.so_no_davinci" :key="x.company_id">
            <button
              type="button"
              class="text-foreground underline-offset-2 hover:underline"
              @click="tela.abrirEmpresa(x.company_id, 'nfeio')"
            >
              {{ x.apelido }}
            </button>
            <template v-if="x.cnpj"> · <span class="tabular-nums">{{ fmtDoc(x.cnpj) }}</span></template>
          </li>
        </ul>
      </details>
    </div>

    <!-- Resumo: clicar filtra a tabela; clicar de novo tira o filtro. -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <button
        v-for="c in cartoes"
        :key="c.id"
        type="button"
        class="rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="cartao === c.id && 'ring-2 ring-primary'"
        :aria-pressed="cartao === c.id"
        :title="cartao === c.id ? 'tirar o filtro' : `filtrar: ${c.rotulo}`"
        @click="alternarCartao(c.id)"
      >
        <StatCard
          class="h-full"
          :label="c.rotulo"
          :value="carregado ? c.valor : '—'"
          :icon="c.icone"
          :tone="carregado ? c.tom : 'default'"
          :hint="c.hint"
          compact
        />
      </button>
    </div>

    <!-- Busca + escopo -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative w-full sm:w-64">
        <Search
          class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          v-model="busca"
          type="search"
          autocomplete="off"
          placeholder="buscar empresa ou CNPJ…"
          aria-label="buscar empresa ou CNPJ"
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
      <NfseSegmentado v-model="escopoModel" tamanho="sm" :opcoes="OPCOES_ESCOPO" />
      <Button v-if="temFiltro" size="sm" variant="ghost" @click="limparFiltros">
        <X class="mr-1.5 size-4" aria-hidden="true" /> limpar filtros
      </Button>
      <span class="ml-auto text-xs text-muted-foreground tabular-nums">
        {{ plural(lista.length, 'empresa', 'empresas') }}
      </span>
    </div>

    <!-- 1ª carga -->
    <NfseSkeletonTabela v-if="esqueleto" :linhas="6" :colunas="6" />

    <!-- Nenhuma empresa em uso ainda -->
    <EmptyState
      v-else-if="carregado && escopo === 'uso' && !base.length"
      :icon="Building2"
      title="Nenhuma empresa ligada à NFE.io ainda"
      description="Ligue cada empresa à NFE.io (pela gaveta da empresa ou, se você for administrador, em Sincronizar com a NFE.io)."
    >
      <div class="flex flex-wrap justify-center gap-2">
        <Button v-if="isAdmin" size="sm" :disabled="sincronizando" @click="sincronizar">
          <RefreshCw class="mr-1.5 size-4" aria-hidden="true" /> Sincronizar com a NFE.io
        </Button>
        <Button size="sm" variant="outline" @click="mostrarTodas">mostrar todas</Button>
      </div>
    </EmptyState>

    <!-- Nenhuma pendente de integração -->
    <EmptyState
      v-else-if="carregado && escopo === 'pendentes' && !base.length"
      :icon="CheckCircle2"
      title="Todas as empresas estão integradas na NFE.io"
      description="Nenhuma empresa pendente de integração."
    >
      <Button size="sm" variant="outline" @click="mostrarEmUso">ver em uso</Button>
    </EmptyState>

    <!-- Filtro sem resultado -->
    <EmptyState
      v-else-if="carregado && !lista.length"
      :icon="SearchX"
      title="Nada com esses filtros"
      description="Nenhuma empresa bate com a busca ou com o resumo escolhido."
    >
      <Button size="sm" variant="outline" @click="limparFiltros">
        <X class="mr-1.5 size-4" aria-hidden="true" /> limpar filtros
      </Button>
    </EmptyState>

    <div v-else-if="lista.length" class="table-card tabela-nfse relative overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th>Empresa</th>
            <th>NFE.io</th>
            <th class="hidden lg:table-cell">Regime</th>
            <th class="hidden lg:table-cell">Certificado</th>
            <th>Situação</th>
            <th class="col-acoes w-px"><span class="sr-only">Ação</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in lista"
            :key="p.company_id"
            class="cursor-pointer"
            @click="abrir(p)"
          >
            <!-- Empresa -->
            <td :class="linhaAtencao(p) && 'border-l-2 border-l-amber-500'">
              <div class="font-medium">{{ p.apelido }}</div>
              <div class="text-xs text-muted-foreground">
                <span v-if="p.razao_social && p.razao_social !== p.apelido">{{ p.razao_social }} · </span>
                <span class="tabular-nums">{{ p.cnpj ? fmtDoc(p.cnpj) : 'sem CNPJ' }}</span>
              </div>
              <div v-if="usoDe(p) || pctPositivo(p.percentual_servico)" class="mt-1 flex flex-wrap gap-1">
                <span v-if="usoDe(p)" class="pill-muted">{{ plural(usoDe(p), 'nota fixa', 'notas fixas') }}</span>
                <NfseDica
                  v-if="pctPositivo(p.percentual_servico)"
                  :texto="`% padrão das notas de percentual da ${p.apelido}: vale nas notas fixas sem % própria. Muda em Cadastros › Empresas.`"
                >
                  <span class="pill-info cursor-help tabular-nums">{{ fmtPct(p.percentual_servico) }} padrão</span>
                </NfseDica>
              </div>
            </td>

            <!-- NFE.io: ambiente + situação na prefeitura -->
            <td>
              <div class="flex flex-wrap items-center gap-1.5">
                <NfseAmbienteBadge tamanho="sm" :ambiente="p.nfeio?.ambiente" :ligada="!!p.nfeio" />
                <span v-if="p.integracao === 'incompleta'" class="pill-warning whitespace-nowrap">integração incompleta</span>
              </div>
              <div v-if="!p.nfeio" class="mt-1">
                <span class="text-xs text-muted-foreground">{{ p.em_operacao ? 'com loja cadastrada' : 'sem loja cadastrada' }}</span>
              </div>
              <div v-if="p.nfeio" class="mt-1 text-xs">
                <span :class="TOM_TEXTO[situacaoFiscalTexto(p.nfeio.status_fiscal).tom]">
                  {{ situacaoFiscalTexto(p.nfeio.status_fiscal).texto }}
                </span>
                <span v-if="p.nfeio.municipio" class="text-muted-foreground"> · {{ municipio(p) }}</span>
              </div>
            </td>

            <!-- Regime (da NFE.io) -->
            <td class="hidden lg:table-cell">
              <span :class="!p.nfeio?.regime && 'text-muted-foreground'">{{ p.nfeio ? regimeTexto(p.nfeio.regime) : '—' }}</span>
              <div v-if="p.nfeio?.retem_ir" class="mt-1">
                <span class="pill-info whitespace-nowrap">retém IR</span>
              </div>
            </td>

            <!-- Certificado (na NFE.io) -->
            <td class="hidden whitespace-nowrap lg:table-cell">
              <NfseNfeioChip v-if="p.nfeio" :prestador="p" />
              <span v-else class="text-xs" :class="TOM_TEXTO[textoCertificadoGuardado(p).tom]">
                {{ textoCertificadoGuardado(p).rotulo }}
              </span>
            </td>

            <!-- Situação -->
            <td class="whitespace-nowrap">
              <span v-if="p.pronto" class="inline-flex items-center gap-1.5">
                <span class="pill-success">
                  <CheckCircle2 class="size-3" aria-hidden="true" />
                  Pronta
                </span>
                <NfseDica v-if="p.avisos?.length" :texto="p.avisos.join(' · ')">
                  <span class="pill-warning cursor-help">
                    <AlertTriangle class="size-3" aria-hidden="true" />
                    {{ plural(p.avisos.length, 'aviso', 'avisos') }}
                  </span>
                </NfseDica>
              </span>
              <PopoverRoot
                v-else
                :open="popoverDe === p.company_id"
                @update:open="(v: boolean) => mudarPopover(p, v)"
              >
                <PopoverTrigger as-child>
                  <button
                    type="button"
                    class="pill-warning hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    :aria-label="`ver o que falta na ${p.apelido}`"
                    @click.stop
                  >
                    <AlertTriangle class="size-3" aria-hidden="true" />
                    {{ plural(p.pendencias.length, 'pendência', 'pendências') }}
                  </button>
                </PopoverTrigger>
                <PopoverPortal>
                  <PopoverContent
                    side="bottom"
                    align="start"
                    :side-offset="6"
                    :collision-padding="8"
                    class="z-[80] w-72 space-y-2 rounded-md border bg-background p-3 shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
                  >
                    <div>
                      <p class="text-sm font-semibold">O que falta</p>
                      <p class="text-xs text-muted-foreground">{{ p.apelido }} só emite depois disso. Clique para resolver.</p>
                    </div>
                    <NfseChecklist
                      :itens="faltando(p)"
                      @ir="(k: string) => resolver(p, k)"
                      @acao="(k: string) => resolver(p, k)"
                    />
                    <p v-if="!podeAbrirCadastroEmpresa && faltaNoCadastro(p)" class="text-xs text-muted-foreground">
                      O CNPJ se cadastra em Cadastros › Empresas: peça para quem tem acesso.
                    </p>
                    <ul v-if="p.avisos?.length" class="space-y-0.5 border-t pt-2 text-xs" :class="TOM_TEXTO.atencao">
                      <li v-for="a in p.avisos" :key="a">{{ a }}</li>
                    </ul>
                    <div class="flex justify-end border-t pt-2">
                      <Button size="sm" variant="outline" class="h-8 px-2.5" @click="abrir(p)">
                        <Settings2 class="mr-1.5 size-4" aria-hidden="true" />
                        abrir empresa
                      </Button>
                    </div>
                  </PopoverContent>
                </PopoverPortal>
              </PopoverRoot>
            </td>

            <!-- Ação -->
            <td class="col-acoes w-px whitespace-nowrap text-right">
              <Button
                v-if="canEdit && p.integracao !== 'ok'"
                size="sm"
                :variant="p.integracao === 'nao_integrada' ? 'default' : 'outline'"
                class="h-8 px-2.5"
                @click.stop="integrar(p)"
              >
                <PlugZap class="mr-1.5 size-4" aria-hidden="true" />
                {{ p.integracao === 'nao_integrada' ? 'Integrar na NFE.io' : 'Completar integração' }}
              </Button>
              <Button v-else size="sm" variant="outline" class="h-8 px-2.5" @click.stop="abrir(p)">
                <Settings2 class="mr-1.5 size-4" aria-hidden="true" />
                {{ canEdit ? 'configurar' : 'ver' }}
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
