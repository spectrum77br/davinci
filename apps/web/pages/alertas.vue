<script setup lang="ts">
// Alertas (M-19, 23/11/2026): a lista de cartões virou TABELA no padrão da
// Tabela de Preços, com a barra Buscar | Equipe ▾ | Plataforma ▾ | Contas ▾ |
// Layout ▾. Quem vê o quê é do SERVIDOR (ADM só administrador; equipe só as
// lojas dela + o pessoal); a tela só mostra o que a API devolve. Filtros,
// busca e paginação valem para todos os alertas (não só os mais recentes).
import { TABS_CADASTROS, TABS_INTEGRACOES } from '~/lib/navGroups'
import { Bell, Check, ChevronDown, Search } from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import type { AlertItem, FiltrosAlertas, LeituraAlertas, OpcoesAlertas } from '~/composables/useAlerts'

definePageMeta({ middleware: ['permission'], permission: { resource: 'alertas', action: 'view' } })

const { items, total, naoLidos, encontrados, loading, refresh, opcoes, markRead, markAllRead } = useAlerts()

const POR_PAGINA = 100

// As caixas da barra vêm do servidor (só o que a pessoa pode ver).
const op = ref<OpcoesAlertas>({ equipes: [], plataformas: [], contas: [] })

// Cada filtro guarda o que está DESMARCADO: vazio = tudo marcado (o começo).
const busca = ref('')
const equipesDesmarcadas = ref<string[]>([])
const plataformasDesmarcadas = ref<string[]>([])
const contasDesmarcadas = ref<string[]>([])
const buscaConta = ref('')
const mostrarNaoLidos = ref(true)
const mostrarLidos = ref(true)
const pagina = ref(1)
const erro = ref('')

// Equipe: só o número (101, 102, 103); ADM e Pessoal por extenso.
function rotuloEquipeFiltro(valor: string): string {
  if (valor === 'adm') return 'ADM'
  if (valor === 'pessoal') return 'Pessoal'
  return valor
}

const PLATAFORMAS: Record<string, string> = {
  ml: 'ML',
  shopee: 'Shopee',
  tiktok: 'TikTok',
  amazon: 'Amazon',
  magalu: 'Magalu',
  temu: 'Temu',
  shein: 'Shein',
  carrefour: 'Carrefour',
  aliexpress: 'AliExpress',
  bling: 'Bling',
}

function rotuloPlataforma(p: string | null | undefined): string {
  if (!p) return '—'
  return PLATAFORMAS[p] ?? p
}

// A coluna Equipe da tabela: ADM, Pessoal ou o número da equipe da loja.
function rotuloEquipe(a: Pick<AlertItem, 'escopo' | 'equipe'>): string {
  if (a.escopo === 'adm') return 'ADM'
  if (a.escopo === 'pessoal') return 'Pessoal'
  return a.equipe != null ? String(a.equipe) : '—'
}

// ADM e Pessoal mostram "—" em Plataforma e Conta.
function plataformaDaLinha(a: Pick<AlertItem, 'escopo' | 'plataforma'>): string {
  return a.escopo === 'equipe' ? rotuloPlataforma(a.plataforma) : '—'
}

function contaDaLinha(a: Pick<AlertItem, 'escopo' | 'conta'>): string {
  return a.escopo === 'equipe' && a.conta ? a.conta : '—'
}

// dd/mm hh:mm no horário de Brasília.
function quando(iso: string): string {
  const partes = new Intl.DateTimeFormat('pt-BR', {
    timeZone: 'America/Sao_Paulo',
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(iso))
  const p = (t: string) => partes.find((x) => x.type === t)?.value ?? ''
  return `${p('day')}/${p('month')} ${p('hour')}:${p('minute')}`
}

// Bolinha da gravidade: vermelho erro, laranja aviso, azul informativo.
function corGravidade(s: string): string {
  if (s === 'error') return 'bg-red-500'
  if (s === 'warning') return 'bg-orange-500'
  if (s === 'success') return 'bg-emerald-500'
  return 'bg-blue-500'
}

function nomeGravidade(s: string): string {
  if (s === 'error') return 'Erro'
  if (s === 'warning') return 'Aviso'
  if (s === 'success') return 'Concluído'
  return 'Informativo'
}

// O que está marcado: null = tudo (o filtro não vai para o servidor).
function marcadas(todas: string[], desmarcadas: string[]): string[] | null {
  if (!desmarcadas.length) return null
  return todas.filter((v) => !desmarcadas.includes(v))
}

function leituraAtual(naoLidosOn: boolean, lidosOn: boolean): LeituraAlertas | null {
  if (naoLidosOn && lidosOn) return 'todos'
  if (naoLidosOn) return 'nao_lidos'
  if (lidosOn) return 'lidos'
  return null
}

const filtros = computed<FiltrosAlertas>(() => ({
  q: busca.value,
  equipes: marcadas(op.value.equipes, equipesDesmarcadas.value),
  plataformas: marcadas(op.value.plataformas, plataformasDesmarcadas.value),
  contas: marcadas(op.value.contas.map((c) => c.id), contasDesmarcadas.value),
}))

function rotuloFiltro(nome: string, todas: string[], desmarcadas: string[]): string {
  const n = todas.filter((v) => !desmarcadas.includes(v)).length
  return n < todas.length ? `${nome} (${n})` : nome
}

const rotuloEquipeBotao = computed(() => rotuloFiltro('Equipe', op.value.equipes, equipesDesmarcadas.value))
const rotuloPlataformaBotao = computed(() => rotuloFiltro('Plataforma', op.value.plataformas, plataformasDesmarcadas.value))
const rotuloContasBotao = computed(() =>
  rotuloFiltro('Contas', op.value.contas.map((c) => c.id), contasDesmarcadas.value),
)

const contasBuscadas = computed(() => {
  const q = buscaConta.value.trim().toLowerCase()
  if (!q) return op.value.contas
  return op.value.contas.filter((c) => c.nome.toLowerCase().includes(q))
})

function alternar(lista: string[], valor: string, marcar: boolean): string[] {
  return marcar ? lista.filter((v) => v !== valor) : lista.includes(valor) ? lista : [...lista, valor]
}

function marcarEquipes(todas: boolean) {
  equipesDesmarcadas.value = todas ? [] : [...op.value.equipes]
}
function marcarPlataformas(todas: boolean) {
  plataformasDesmarcadas.value = todas ? [] : [...op.value.plataformas]
}
// "Marcar todas" / "Limpar" valem para as contas que a busca mostra.
function marcarContas(todas: boolean) {
  const vistas = contasBuscadas.value.map((c) => c.id)
  contasDesmarcadas.value = todas
    ? contasDesmarcadas.value.filter((id) => !vistas.includes(id))
    : Array.from(new Set([...contasDesmarcadas.value, ...vistas]))
}

const layoutTodos = computed({
  get: () => mostrarNaoLidos.value && mostrarLidos.value,
  set: (v: boolean) => {
    mostrarNaoLidos.value = v
    mostrarLidos.value = v
  },
})

const totalPaginas = computed(() => Math.max(1, Math.ceil(encontrados.value / POR_PAGINA)))

async function carregar() {
  erro.value = ''
  const leitura = leituraAtual(mostrarNaoLidos.value, mostrarLidos.value)
  try {
    await refresh({
      ...filtros.value,
      leitura: leitura ?? 'todos',
      limit: POR_PAGINA,
      offset: (pagina.value - 1) * POR_PAGINA,
    })
    if (!leitura) {
      // Nenhuma caixa do Layout: a lista fica vazia (os números continuam).
      items.value = []
      encontrados.value = 0
    }
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para carregar os alertas. Tente de novo.'
  }
}

function irParaPagina(n: number) {
  pagina.value = Math.min(Math.max(1, n), totalPaginas.value)
  void carregar()
}

// Filtro mudou: volta para a página 1. A busca espera a pessoa parar de digitar.
let esperaBusca: ReturnType<typeof setTimeout> | null = null
watch(busca, () => {
  if (esperaBusca) clearTimeout(esperaBusca)
  esperaBusca = setTimeout(() => {
    pagina.value = 1
    void carregar()
  }, 300)
})
watch([equipesDesmarcadas, plataformasDesmarcadas, contasDesmarcadas, mostrarNaoLidos, mostrarLidos], () => {
  pagina.value = 1
  void carregar()
})
onScopeDispose(() => {
  if (esperaBusca) clearTimeout(esperaBusca)
})

async function onMarkRead(id: string) {
  await markRead(id)
}

async function onMarkAllRead() {
  await markAllRead(filtros.value)
  await carregar()
}

try {
  op.value = await opcoes()
} catch {
  // Sem as opções a barra fica sem caixas; a lista carrega igual.
}
await carregar()
</script>

<template>
  <div class="space-y-5">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <RouteTabs :tabs="TABS_INTEGRACOES" sub />
    <PageHeader title="Alertas" description="Eventos que pedem atenção — anúncio com falha, banido ou em revisão, avisos do sistema e tarefas.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="naoLidos === 0 || loading" @click="onMarkAllRead">
          marcar todos lidos
        </Button>
      </template>
    </PageHeader>

    <!-- Barra (M-19): no padrão da Tabela de Preços — campo e botões com 28px
         (h-7), popovers com caixas e "Marcar todas · Limpar". -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative flex-1 min-w-[200px] max-w-sm">
        <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <input
          v-model="busca"
          placeholder="Buscar por SKU, conta ou aviso…"
          class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"
          data-campo="alertas-busca"
        />
      </div>

      <!-- Equipe ▾: ADM, 101, 102, 103, Pessoal — só o que a pessoa pode ver. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="equipe" title="Escolher as equipes que a lista mostra">
            {{ rotuloEquipeBotao }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent side="bottom" align="start" :side-offset="4" :collision-padding="8" class="z-[70] w-56 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg">
            <div class="flex items-center gap-2 px-1 pb-1 text-xs">
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarEquipes(true)">Marcar todas</button>
              <span class="text-muted-foreground">·</span>
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarEquipes(false)">Limpar</button>
            </div>
            <div class="max-h-72 overflow-y-auto border-t pt-1">
              <label v-for="v in op.equipes" :key="v" class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="!equipesDesmarcadas.includes(v)"
                  :data-equipe="v"
                  @change="equipesDesmarcadas = alternar(equipesDesmarcadas, v, ($event.target as HTMLInputElement).checked)"
                />
                {{ rotuloEquipeFiltro(v) }}
              </label>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>

      <!-- Plataforma ▾ -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="plataforma" title="Escolher as plataformas que a lista mostra">
            {{ rotuloPlataformaBotao }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent side="bottom" align="start" :side-offset="4" :collision-padding="8" class="z-[70] w-56 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg">
            <div class="flex items-center gap-2 px-1 pb-1 text-xs">
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarPlataformas(true)">Marcar todas</button>
              <span class="text-muted-foreground">·</span>
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarPlataformas(false)">Limpar</button>
            </div>
            <div class="max-h-72 overflow-y-auto border-t pt-1">
              <label v-for="p in op.plataformas" :key="p" class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="!plataformasDesmarcadas.includes(p)"
                  :data-plataforma="p"
                  @change="plataformasDesmarcadas = alternar(plataformasDesmarcadas, p, ($event.target as HTMLInputElement).checked)"
                />
                {{ rotuloPlataforma(p) }}
              </label>
              <p v-if="!op.plataformas.length" class="px-1 py-1 text-[11px] text-muted-foreground">Nenhum alerta de anúncio.</p>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>

      <!-- Contas ▾: quem é de equipe só vê as contas da equipe (servidor). -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="contas" title="Escolher as contas que a lista mostra">
            {{ rotuloContasBotao }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent side="bottom" align="start" :side-offset="4" :collision-padding="8" class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg">
            <div class="relative px-1 pb-1">
              <Search class="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <input v-model="buscaConta" placeholder="Buscar conta…" class="h-7 w-full border rounded pl-7 pr-2 text-sm bg-background" data-campo="contas-busca" />
            </div>
            <div class="flex items-center gap-2 px-1 pb-1 text-xs">
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarContas(true)">Marcar todas</button>
              <span class="text-muted-foreground">·</span>
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarContas(false)">Limpar</button>
            </div>
            <div class="max-h-72 overflow-y-auto border-t pt-1">
              <label v-for="c in contasBuscadas" :key="c.id" class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="!contasDesmarcadas.includes(c.id)"
                  :data-conta="c.id"
                  @change="contasDesmarcadas = alternar(contasDesmarcadas, c.id, ($event.target as HTMLInputElement).checked)"
                />
                <span class="truncate">{{ c.nome }}</span>
                <span class="ml-auto shrink-0 text-[11px] text-muted-foreground">{{ rotuloPlataforma(c.plataforma) }}</span>
              </label>
              <p v-if="!contasBuscadas.length" class="px-1 py-1 text-[11px] text-muted-foreground">
                {{ buscaConta.trim() ? 'Nenhuma conta encontrada.' : 'Nenhuma conta com alerta.' }}
              </p>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>

      <!-- Layout ▾: Todos, Não lidos, Lidos. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-acao="layout" title="Mostrar os não lidos, os lidos ou todos">
            Layout
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent side="bottom" align="start" :side-offset="4" :collision-padding="8" class="z-[70] w-48 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg">
            <label class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
              <input v-model="layoutTodos" type="checkbox" class="size-4" data-campo="layout-todos" />
              Todos
            </label>
            <label class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
              <input v-model="mostrarNaoLidos" type="checkbox" class="size-4" data-campo="layout-nao-lidos" />
              Não lidos
            </label>
            <label class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
              <input v-model="mostrarLidos" type="checkbox" class="size-4" data-campo="layout-lidos" />
              Lidos
            </label>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>

      <span class="ml-auto text-xs text-muted-foreground" data-contagem>
        {{ naoLidos.toLocaleString('pt-BR') }} não lidos · {{ total.toLocaleString('pt-BR') }} total
      </span>
    </div>

    <p v-if="erro" class="text-sm text-red-600">{{ erro }}</p>

    <!-- Tabela (M-19): no padrão da Tabela de Preços — cabeçalho 11px
         semibold com 24px, linhas de 34px, moldura com rolagem. -->
    <div class="overflow-auto rounded border max-h-[70vh]">
      <table class="w-full text-xs border-collapse">
        <thead class="bg-background sticky top-0 z-10">
          <tr class="text-[11px] font-semibold leading-[14px]">
            <th class="h-6 w-6 px-2 py-0" aria-label="Gravidade" />
            <th class="h-6 px-2 py-0 text-left whitespace-nowrap">Quando</th>
            <th class="h-6 px-2 py-0 text-center">Equipe</th>
            <th class="h-6 px-2 py-0 text-center">Plataforma</th>
            <th class="h-6 px-2 py-0 text-left">Conta</th>
            <th class="h-6 px-2 py-0 text-left">Tipo</th>
            <th class="h-6 px-2 py-0 text-left">Aviso</th>
            <th class="h-6 px-2 py-0 text-left">Detalhe</th>
            <th class="h-6 w-8 px-2 py-0" aria-label="Marcar lido" />
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in items" :key="a.id" class="h-[34px] border-t hover:bg-muted/20" data-linha-alerta>
            <td class="px-2 text-center">
              <span class="inline-block size-2 rounded-full" :class="corGravidade(a.severity)" :title="nomeGravidade(a.severity)" />
            </td>
            <td class="px-2 whitespace-nowrap tabular-nums text-muted-foreground">{{ quando(a.created_at) }}</td>
            <td class="px-2 text-center whitespace-nowrap">{{ rotuloEquipe(a) }}</td>
            <td class="px-2 text-center whitespace-nowrap">{{ plataformaDaLinha(a) }}</td>
            <td class="px-2 whitespace-nowrap max-w-[160px] truncate" :title="contaDaLinha(a)">{{ contaDaLinha(a) }}</td>
            <td class="px-2 whitespace-nowrap">{{ a.tipo || '—' }}</td>
            <td class="px-2 max-w-[320px] truncate" :class="!a.read_at && 'font-semibold'" :title="a.title">{{ a.title }}</td>
            <td class="px-2 max-w-[420px] truncate text-muted-foreground" :title="a.message || ''">{{ a.message || '—' }}</td>
            <td class="px-2 text-center">
              <button
                v-if="!a.read_at"
                type="button"
                class="inline-grid size-6 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                title="Marcar lido"
                aria-label="Marcar lido"
                @click="onMarkRead(a.id)"
              >
                <Check class="size-3.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <EmptyState
        v-if="!loading && items.length === 0"
        :icon="Bell"
        title="Nada por aqui"
        description="Nenhum alerta corresponde aos filtros."
      />
    </div>

    <div v-if="encontrados > POR_PAGINA" class="flex flex-wrap sm:flex-nowrap justify-between items-center gap-y-2 text-sm">
      <div class="flex flex-wrap sm:flex-nowrap gap-1 items-center">
        <Button size="sm" variant="outline" :disabled="pagina <= 1" title="Primeira página" @click="irParaPagina(1)">«</Button>
        <Button size="sm" variant="outline" :disabled="pagina <= 1" title="Página anterior" @click="irParaPagina(pagina - 1)">‹</Button>
        <span class="px-2 py-1">página {{ pagina }} de {{ totalPaginas }}</span>
        <Button size="sm" variant="outline" :disabled="pagina >= totalPaginas" title="Próxima página" @click="irParaPagina(pagina + 1)">›</Button>
        <Button size="sm" variant="outline" :disabled="pagina >= totalPaginas" title="Última página" @click="irParaPagina(totalPaginas)">»</Button>
      </div>
      <span class="text-xs text-muted-foreground">
        mostrando {{ (pagina - 1) * POR_PAGINA + 1 }}-{{ Math.min(pagina * POR_PAGINA, encontrados) }} de {{ encontrados.toLocaleString('pt-BR') }} alertas
      </span>
    </div>
  </div>
</template>
