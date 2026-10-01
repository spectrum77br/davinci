<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Anúncios (30/09/2026) ───────────────────────────────────────
// Os anúncios que o robô da fiscalização (Mac mini da Makisa) achou nos
// marketplaces usando a nossa homologação/marca. Cópia só leitura do sistema
// de lá. Mesmo recorte padrão do sistema do mini: sem os descartados/fora de
// escopo (só aparecem filtrando o Grupo) e sem os anúncios das lojas próprias.
import { onMounted, ref } from 'vue'
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-vue-next'
import { dataBr, nomeGrupo, numero, pillGrupo, pillSituacaoAnuncio } from '~/lib/denuncia'


type Item = {
  id: string
  marketplace: string | null
  shop_id: string | null
  loja: string | null
  titulo: string | null
  url: string | null
  hom: string | null
  inmetro: string | null
  certificacao: string | null
  grupo: string | null
  escopo: string | null
  vendas: number
  situacao: string | null
  propria: number
  marca_uranyx: number | null
  a_conferir: number | null
  visto_primeiro: string | null
  visto_ultimo: string | null
  saiu_em: string | null
  nden: number
  nprov: number
  caso: string | null
}
type Resposta = {
  total: number
  itens: Item[]
  numeros: { total: number; ativos: number; fora_do_ar: number; com_denuncia: number }
  opcoes: { marketplaces: string[]; grupos: string[] }
}

const { api } = useApi()
const LIMITE = 100

const itens = ref<Item[]>([])
const total = ref(0)
const numeros = ref<Resposta['numeros'] | null>(null)
const opcoes = ref<Resposta['opcoes']>({ marketplaces: [], grupos: [] })
const offset = ref(0)
const carregando = ref(true)
const erro = ref<string | null>(null)

const q = ref('')
const marketplace = ref('')
const grupo = ref('')
const situacao = ref('')
const den = ref('')
const propria = ref('0')
const ordem = ref('vendas')

const aberto = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const qs = new URLSearchParams({ limite: String(LIMITE), offset: String(offset.value), propria: propria.value, ordem: ordem.value })
    if (q.value.trim()) qs.set('q', q.value.trim())
    if (marketplace.value) qs.set('marketplace', marketplace.value)
    if (grupo.value) qs.set('grupo', grupo.value)
    if (situacao.value) qs.set('situacao', situacao.value)
    if (den.value) qs.set('den', den.value)
    const r = await api<Resposta>(`/api/denuncia/anuncios?${qs}`)
    itens.value = r.itens
    total.value = r.total
    numeros.value = r.numeros
    opcoes.value = r.opcoes
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}

function filtrar() {
  offset.value = 0
  void carregar()
}
function pagina(delta: number) {
  offset.value = Math.max(0, offset.value + delta * LIMITE)
  void carregar()
}

onMounted(carregar)
// o botão "recarregar" do topo do painel chama isto na aba aberta
defineExpose({ carregar })
</script>

<template>
  <div class="space-y-5">

    <div v-if="numeros" class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <StatCard compact label="Anúncios" :value="numero(numeros.total)" hint="no filtro atual" />
      <StatCard compact label="No ar" :value="numero(numeros.ativos)" tone="warning" hint="ainda vendendo" />
      <StatCard compact label="Fora do ar" :value="numero(numeros.fora_do_ar)" tone="success" hint="saíram depois de vistos" />
      <StatCard compact label="Com denúncia" :value="numero(numeros.com_denuncia)" hint="ao menos uma denúncia feita" />
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="flex flex-wrap gap-2 items-center">
      <Input v-model="q" placeholder="anúncio, loja, título, nº Anatel…" class="w-64" @keyup.enter="filtrar" />
      <select v-model="marketplace" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="">todos marketplaces</option>
        <option v-for="m in opcoes.marketplaces" :key="m" :value="m">{{ m }}</option>
      </select>
      <select v-model="grupo" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="">certificado: todos (sem descartados)</option>
        <option v-for="g in opcoes.grupos" :key="g" :value="g">certificado: {{ nomeGrupo(g).toLowerCase() }}</option>
      </select>
      <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="">no ar e fora do ar</option>
        <option value="ativo">no ar</option>
        <option value="fora do ar">fora do ar</option>
        <option value="desconhecido">desconhecido</option>
      </select>
      <select v-model="den" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="">com e sem denúncia</option>
        <option value="com">com denúncia</option>
        <option value="sem">sem denúncia</option>
      </select>
      <select v-model="propria" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="0">concorrentes</option>
        <option value="1">lojas próprias</option>
        <option value="todas">todas as lojas</option>
      </select>
      <select v-model="ordem" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="vendas">mais vendas</option>
        <option value="novos">achados mais recentes</option>
        <option value="loja">por loja</option>
        <option value="grupo">por certificado</option>
      </select>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th title="Quando o robô viu o anúncio pela primeira vez">Achado em</th>
            <th>Anúncio</th>
            <th>Loja</th>
            <th>Título</th>
            <th>Nº declarado</th>
            <th>Certificado</th>
            <th class="text-right">Vendas</th>
            <th>Situação</th>
            <th class="text-right">Den.</th>
            <th class="text-right">Provas</th>
            <th>Caso</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && itens.length === 0">
            <td colspan="11" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="itens.length === 0">
            <td colspan="11" class="text-center text-muted-foreground py-6">nenhum anúncio neste filtro</td>
          </tr>
          <tr v-for="a in itens" :key="a.id" class="cursor-pointer" @click="aberto = a.id">
            <!-- visto_primeiro: quando o robô achou o anúncio (hora do Mac mini, Brasília). -->
            <td class="text-xs whitespace-nowrap tabular-nums" :title="a.visto_primeiro || ''">
              <div>{{ dataBr(a.visto_primeiro, false) }}</div>
              <div v-if="a.visto_primeiro && a.visto_primeiro.length >= 16" class="text-[11px] text-muted-foreground">{{ a.visto_primeiro.slice(11, 16) }}</div>
            </td>
            <td class="whitespace-nowrap">
              <div class="font-mono text-xs">{{ a.id }}</div>
              <div class="text-[11px] text-muted-foreground">{{ a.marketplace }}</div>
            </td>
            <td class="text-xs max-w-[160px] truncate" :title="a.loja || ''">{{ a.loja || a.shop_id || '—' }}</td>
            <td class="text-xs max-w-[320px]">
              <div class="flex items-center gap-1 min-w-0">
                <span class="truncate" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
              <div v-if="a.escopo" class="text-[11px] text-muted-foreground truncate">{{ a.escopo }}</div>
            </td>
            <td class="font-mono text-xs whitespace-nowrap">{{ a.hom || (a.inmetro ? `Inmetro ${a.inmetro}` : '—') }}</td>
            <td><span v-if="a.grupo" :class="pillGrupo(a.grupo)" :title="a.grupo">{{ nomeGrupo(a.grupo) }}</span></td>
            <td class="text-right text-xs tabular-nums">{{ numero(a.vendas) }}</td>
            <td class="whitespace-nowrap">
              <span :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao || '—' }}</span>
              <div v-if="a.saiu_em" class="text-[11px] text-muted-foreground">saiu {{ dataBr(a.saiu_em, false) }}</div>
            </td>
            <td class="text-right text-xs tabular-nums">{{ a.nden || '—' }}</td>
            <td class="text-right text-xs tabular-nums">{{ a.nprov || '—' }}</td>
            <td class="text-xs whitespace-nowrap">{{ a.caso || '' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <div>{{ numero(total) }} anúncios</div>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="ghost" :disabled="offset === 0" @click="pagina(-1)">
          <ChevronLeft class="size-4" />
        </Button>
        <span class="tabular-nums">{{ total === 0 ? 0 : offset + 1 }}–{{ Math.min(offset + LIMITE, total) }}</span>
        <Button size="sm" variant="ghost" :disabled="offset + LIMITE >= total" @click="pagina(1)">
          <ChevronRight class="size-4" />
        </Button>
      </div>
    </div>

    <DenunciaAnuncioGaveta :anuncio-id="aberto" @fechar="aberto = null" />
  </div>
</template>
