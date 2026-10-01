<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba "Anúncios e denúncias" (01/10/2026) ──────────────────────────
// Vinicius: as abas Anúncios e Denúncias "são quase as mesmas informações" — virou uma só.
// Cada loja (e cada anúncio) com o que a loja fez com a nossa denúncia ("Na loja") e onde
// está no caminho da Anatel ("Na Anatel": processo aberto, na fila, falta o print, esperando
// a loja recusar…), no formato de etiqueta que ele escolheu ("9 removidos · 21 recusadas").
// Sub-abas: Por loja (padrão, sempre na frente) · Por anúncio · Denúncias enviadas (o
// histórico). Clicar num anúncio — ou numa denúncia enviada — abre a ficha com tudo junto.
// Cópia só leitura do sistema do Mac mini; os status vêm de services/denuncia_painel (API).
import { computed, onMounted, ref } from 'vue'
import { ChevronDown, ChevronLeft, ChevronRight, ExternalLink } from 'lucide-vue-next'
import {
  type DenunciaEnviada, dataBr, nomeGrupo, numero, pillGrupo, pillSituacaoAnuncio,
} from '~/lib/denuncia'

type Status = {
  chave: string
  rotulo: string
  tom: string
  data?: string | null
  tentativas?: number
  protocolo?: string | null
  consumidor?: string | null
}
type Anuncio = {
  id: string
  marketplace: string | null
  shop_id: string | null
  loja: string | null
  titulo: string | null
  url: string | null
  hom: string | null
  inmetro: string | null
  grupo: string | null
  vendas: number
  situacao: string | null
  visto_primeiro: string | null
  loja_st: Status
  anatel_st: Status
  nden: number
}
type Loja = {
  marketplace: string | null
  shop_id: string | null
  chave: string
  loja: string | null
  anuncios: number
  no_ar: number
  fora_do_ar: number
  vendas: number
  nosso: number
  diversos: number
  outros: number
  na_loja: Record<string, number>
  na_anatel: Record<string, number>
  processos: string[]
  ultimo_achado: string | null
}
type Numeros = {
  lojas: number
  anuncios: number
  no_ar: number
  fora_do_ar: number
  processos: number
  na_loja: Record<string, number>
  na_anatel: Record<string, number>
}
type Opcoes = {
  marketplaces: string[]
  grupos: string[]
  na_loja: { chave: string; rotulo: string }[]
  na_anatel: { chave: string; rotulo: string }[]
}
type Resposta<T> = { total: number; itens: T[]; numeros: Numeros; opcoes: Opcoes }

const { api } = useApi()
const LIMITE = 100

const visao = ref<'loja' | 'anuncio' | 'enviadas'>('loja')
const lojas = ref<Loja[]>([])
const itens = ref<Anuncio[]>([])
const total = ref(0)
const numeros = ref<Numeros | null>(null)
const opcoes = ref<Opcoes>({ marketplaces: [], grupos: [], na_loja: [], na_anatel: [] })
const offset = ref(0)
const carregando = ref(true)
const erro = ref<string | null>(null)

const q = ref('')
const marketplace = ref('')
const grupo = ref('')
const situacao = ref('')
const naLoja = ref('')
const naAnatel = ref('')
const propria = ref('0')
const ordemLoja = ref('vendas')
const ordem = ref('vendas')

const lojaAberta = ref<string | null>(null)
const anunciosDaLoja = ref<Anuncio[]>([])
const carregandoLoja = ref(false)

// ficha do anúncio (tudo junto); foco = a denúncia clicada em "Denúncias enviadas"
const aberto = ref<string | null>(null)
const foco = ref<number | null>(null)
const enviadas = ref<{ carregar: () => Promise<void> } | null>(null)

// "9 removidos · 21 recusadas · 36 aguardando" — a ordem e o texto de cada etiqueta
const ETIQ_LOJA: [string, string, string, string][] = [
  ['removido', 'pill-success', 'removido', 'removidos'],
  ['recusou', 'pill-danger', 'recusada', 'recusadas'],
  ['aguardando', 'pill-muted', 'aguardando', 'aguardando'],
  ['nao', 'pill-muted', 'sem denúncia', 'sem denúncia'],
]
const ETIQ_ANATEL: [string, string, string, string][] = [
  ['processo', 'pill-info', 'com processo', 'com processo'],
  ['fila', 'pill-warning', 'na fila', 'na fila'],
  ['falta_print', 'pill-warning', 'falta o print', 'falta o print'],
  ['esperando_recusa', 'pill-muted', 'esperando recusa', 'esperando recusa'],
  ['falta_loja', 'pill-muted', 'falta denunciar na loja', 'falta denunciar na loja'],
]
function etiquetas(cont: Record<string, number>, tabela: [string, string, string, string][]) {
  return tabela
    .filter(([k]) => cont[k])
    .map(([k, cls, um, varios]) => ({ k, cls, texto: `${cont[k]} ${cont[k] > 1 ? varios : um}` }))
}
const TOM: Record<string, string> = {
  success: 'pill-success', danger: 'pill-danger', warning: 'pill-warning', info: 'pill-info', muted: 'pill-muted',
}
function pillTom(t: string | undefined): string {
  return TOM[t || 'muted'] || 'pill-muted'
}

function chaveLoja(l: Loja): string {
  return `${l.marketplace || ''}|${l.chave}`
}

function filtros(): URLSearchParams {
  const qs = new URLSearchParams({ propria: propria.value })
  if (q.value.trim()) qs.set('q', q.value.trim())
  if (marketplace.value) qs.set('marketplace', marketplace.value)
  if (grupo.value) qs.set('grupo', grupo.value)
  if (situacao.value) qs.set('situacao', situacao.value)
  if (naLoja.value) qs.set('na_loja', naLoja.value)
  if (naAnatel.value) qs.set('na_anatel', naAnatel.value)
  return qs
}

async function carregar() {
  if (visao.value === 'enviadas') {
    await enviadas.value?.carregar()
    return
  }
  carregando.value = true
  erro.value = null
  try {
    const qs = filtros()
    if (visao.value === 'loja') {
      qs.set('visao', 'lojas')
      qs.set('ordem', ordemLoja.value)
      const r = await api<Resposta<Loja>>(`/api/denuncia/painel?${qs}`)
      lojas.value = r.itens
      total.value = r.total
      numeros.value = r.numeros
      opcoes.value = r.opcoes
      if (lojaAberta.value && !r.itens.some((l) => chaveLoja(l) === lojaAberta.value)) lojaAberta.value = null
    } else {
      qs.set('visao', 'anuncios')
      qs.set('ordem', ordem.value)
      qs.set('limite', String(LIMITE))
      qs.set('offset', String(offset.value))
      const r = await api<Resposta<Anuncio>>(`/api/denuncia/painel?${qs}`)
      itens.value = r.itens
      total.value = r.total
      numeros.value = r.numeros
      opcoes.value = r.opcoes
    }
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}

async function abrirLoja(l: Loja) {
  const k = chaveLoja(l)
  if (lojaAberta.value === k) {
    lojaAberta.value = null
    return
  }
  lojaAberta.value = k
  anunciosDaLoja.value = []
  carregandoLoja.value = true
  try {
    const qs = filtros()
    qs.set('visao', 'anuncios')
    qs.set('loja', l.chave)
    if (l.marketplace) qs.set('marketplace', l.marketplace)
    qs.set('ordem', 'vendas')
    const r = await api<Resposta<Anuncio>>(`/api/denuncia/painel?${qs}`)
    if (lojaAberta.value === k) anunciosDaLoja.value = r.itens
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregandoLoja.value = false
  }
}

function trocarVisao(v: 'loja' | 'anuncio' | 'enviadas') {
  if (visao.value === v) return
  visao.value = v
  offset.value = 0
  if (v !== 'enviadas') void carregar()
}

function filtrar() {
  offset.value = 0
  lojaAberta.value = null
  void carregar()
}

// os cartões do topo também filtram (clicar de novo tira o filtro)
function porCartao(campo: 'situacao' | 'naLoja' | 'naAnatel', valor: string) {
  const alvo = { situacao, naLoja, naAnatel }[campo]
  alvo.value = alvo.value === valor ? '' : valor
  if (visao.value === 'enviadas') visao.value = 'loja'
  filtrar()
}

function pagina(delta: number) {
  offset.value = Math.max(0, offset.value + delta * LIMITE)
  void carregar()
}

function abrirAnuncio(id: string, denuncia: number | null = null) {
  foco.value = denuncia
  aberto.value = id
}
function abrirEnviada(d: DenunciaEnviada) {
  const id = d.anuncios.find((x) => x.id)?.id
  if (id) abrirAnuncio(id, d.id)
}

const n = computed(() => numeros.value)

onMounted(carregar)
// o botão "recarregar" do topo do painel chama isto
defineExpose({ carregar })
</script>

<template>
  <div class="space-y-5">
    <!-- números do recorte; clicar filtra -->
    <div v-if="n" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
      <StatCard compact label="Lojas" :value="numero(n.lojas)" :hint="`${numero(n.anuncios)} anúncios`" />
      <button type="button" class="text-left rounded-lg" :class="situacao === 'ativo' ? 'ring-2 ring-primary' : ''" title="filtrar os que estão no ar" @click="porCartao('situacao', 'ativo')">
        <StatCard compact label="No ar" :value="numero(n.no_ar)" tone="warning" hint="ainda vendendo" />
      </button>
      <button type="button" class="text-left rounded-lg" :class="situacao === 'fora do ar' ? 'ring-2 ring-primary' : ''" title="filtrar os que saíram do ar" @click="porCartao('situacao', 'fora do ar')">
        <StatCard compact label="Fora do ar" :value="numero(n.fora_do_ar)" tone="success" hint="saíram depois de vistos" />
      </button>
      <button type="button" class="text-left rounded-lg" :class="naLoja === 'recusou' ? 'ring-2 ring-primary' : ''" title="filtrar os que a loja recusou" @click="porCartao('naLoja', 'recusou')">
        <StatCard
          compact label="Loja recusou" :value="numero(n.na_loja.recusou)" tone="danger"
          :hint="`${numero(n.na_loja.aguardando)} aguardando · ${numero(n.na_loja.removido)} removidos`"
        />
      </button>
      <button type="button" class="text-left rounded-lg" :class="naAnatel === 'processo' ? 'ring-2 ring-primary' : ''" title="filtrar os que já têm processo na Anatel" @click="porCartao('naAnatel', 'processo')">
        <StatCard compact label="Processos na Anatel" :value="numero(n.processos)" :hint="`${numero(n.na_anatel.processo)} anúncios`" />
      </button>
      <button type="button" class="text-left rounded-lg" :class="naAnatel === 'fila' ? 'ring-2 ring-primary' : ''" title="filtrar os que estão na fila da Anatel" @click="porCartao('naAnatel', 'fila')">
        <StatCard
          compact label="Na fila da Anatel" :value="numero(n.na_anatel.fila)" tone="warning"
          :hint="`${numero(n.na_anatel.falta_print)} falta o print · ${numero(n.na_anatel.esperando_recusa)} esperando recusa`"
        />
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="flex items-center gap-1 border-b border-border">
      <button
        v-for="v in [{ k: 'loja', t: 'Por loja' }, { k: 'anuncio', t: 'Por anúncio' }, { k: 'enviadas', t: 'Denúncias enviadas' }]"
        :key="v.k"
        type="button"
        class="-mb-px inline-flex h-9 items-center border-b-2 px-3 text-sm font-medium transition-colors"
        :class="visao === v.k ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="trocarVisao(v.k as 'loja' | 'anuncio' | 'enviadas')"
      >
        {{ v.t }}
      </button>
    </div>

    <DenunciaEnviadas v-if="visao === 'enviadas'" ref="enviadas" @abrir="abrirEnviada" />

    <template v-else>
      <div class="flex flex-wrap gap-2 items-center">
        <Input v-model="q" placeholder="anúncio, loja, título, nº Anatel…" class="w-60" @keyup.enter="filtrar" />
        <select v-model="marketplace" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="">todos marketplaces</option>
          <option v-for="m in opcoes.marketplaces" :key="m" :value="m">{{ m }}</option>
        </select>
        <select v-model="grupo" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="">certificado: todos</option>
          <option v-for="g in opcoes.grupos" :key="g" :value="g">certificado: {{ nomeGrupo(g).toLowerCase() }}</option>
        </select>
        <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="">no ar e fora do ar</option>
          <option value="ativo">no ar</option>
          <option value="fora do ar">fora do ar</option>
          <option value="desconhecido">desconhecido</option>
        </select>
        <select v-model="naLoja" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="">na loja: todos</option>
          <option v-for="o in opcoes.na_loja" :key="o.chave" :value="o.chave">na loja: {{ o.rotulo }}</option>
        </select>
        <select v-model="naAnatel" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="">na Anatel: todos</option>
          <option v-for="o in opcoes.na_anatel" :key="o.chave" :value="o.chave">na Anatel: {{ o.rotulo }}</option>
        </select>
        <select v-model="propria" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="0">concorrentes</option>
          <option value="1">lojas próprias</option>
          <option value="todas">todas as lojas</option>
        </select>
        <select v-if="visao === 'loja'" v-model="ordemLoja" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="vendas">mais vendas</option>
          <option value="anuncios">mais anúncios</option>
          <option value="recusadas">mais recusadas</option>
          <option value="fila">mais na fila da Anatel</option>
          <option value="recentes">achados mais recentes</option>
          <option value="nome">nome da loja</option>
        </select>
        <select v-else v-model="ordem" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
          <option value="vendas">mais vendas</option>
          <option value="recentes">achados mais recentes</option>
          <option value="loja">por loja</option>
        </select>
      </div>

      <!-- ══ Por loja ══ -->
      <div v-if="visao === 'loja'" class="table-card overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th class="w-24" title="Quando o robô achou o anúncio mais novo desta loja">Último achado</th>
              <th>Loja</th>
              <th>Certificado</th>
              <th class="text-right">Anúncios</th>
              <th class="text-right">Vendas</th>
              <th>Na loja</th>
              <th>Na Anatel</th>
              <th>Resultado</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && lojas.length === 0">
              <td colspan="8" class="text-center text-muted-foreground py-6">carregando…</td>
            </tr>
            <tr v-else-if="lojas.length === 0">
              <td colspan="8" class="text-center text-muted-foreground py-6">nenhuma loja neste filtro</td>
            </tr>
            <template v-for="l in lojas" :key="chaveLoja(l)">
              <tr class="cursor-pointer" @click="abrirLoja(l)">
                <td class="text-xs whitespace-nowrap tabular-nums align-top" :title="l.ultimo_achado || ''">
                  <div>{{ dataBr(l.ultimo_achado, false) }}</div>
                  <div v-if="l.ultimo_achado && l.ultimo_achado.length >= 16" class="text-[11px] text-muted-foreground">{{ l.ultimo_achado.slice(11, 16) }}</div>
                </td>
                <td class="max-w-[260px] align-top">
                  <div class="flex items-center gap-1 min-w-0">
                    <ChevronDown v-if="lojaAberta === chaveLoja(l)" class="size-3.5 shrink-0 text-muted-foreground" />
                    <ChevronRight v-else class="size-3.5 shrink-0 text-muted-foreground" />
                    <span class="truncate text-sm font-medium" :title="l.loja || ''">{{ l.loja || l.shop_id || 'sem loja' }}</span>
                  </div>
                  <div class="pl-[18px] text-[11px] text-muted-foreground truncate">
                    {{ l.marketplace || '—' }}<span v-if="l.shop_id" class="font-mono"> · {{ l.shop_id }}</span>
                  </div>
                </td>
                <td class="align-top">
                  <div class="flex flex-wrap gap-1">
                    <span v-if="l.nosso" class="pill-danger">Nosso {{ l.nosso }}</span>
                    <span v-if="l.diversos" class="pill-warning">Diversos {{ l.diversos }}</span>
                    <span v-if="l.outros" class="pill-muted">outros {{ l.outros }}</span>
                  </div>
                </td>
                <td class="text-right tabular-nums whitespace-nowrap align-top">
                  <div class="text-sm">{{ numero(l.anuncios) }}</div>
                  <div class="text-[11px] text-muted-foreground">{{ numero(l.no_ar) }} no ar</div>
                </td>
                <td class="text-right text-sm font-medium tabular-nums whitespace-nowrap align-top">{{ numero(l.vendas) }}</td>
                <td class="align-top">
                  <div class="flex flex-wrap gap-1">
                    <span v-for="e in etiquetas(l.na_loja, ETIQ_LOJA)" :key="e.k" :class="e.cls">{{ e.texto }}</span>
                  </div>
                </td>
                <td class="align-top">
                  <div class="flex flex-wrap gap-1" :title="l.processos.join('\n')">
                    <span v-for="e in etiquetas(l.na_anatel, ETIQ_ANATEL)" :key="e.k" :class="e.cls">{{ e.texto }}</span>
                    <span v-if="!etiquetas(l.na_anatel, ETIQ_ANATEL).length" class="text-xs text-muted-foreground">—</span>
                  </div>
                </td>
                <td class="align-top whitespace-nowrap">
                  <span v-if="l.fora_do_ar" class="pill-success">{{ l.fora_do_ar }} fora do ar</span>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </td>
              </tr>
              <tr v-if="lojaAberta === chaveLoja(l)">
                <td colspan="8" class="bg-muted/30 p-0">
                  <div v-if="carregandoLoja" class="px-4 py-3 text-xs text-muted-foreground">carregando os anúncios da loja…</div>
                  <table v-else class="w-full">
                    <tbody>
                      <tr v-for="a in anunciosDaLoja" :key="a.id" class="cursor-pointer" @click="abrirAnuncio(a.id)">
                        <td class="pl-8 w-28 text-xs whitespace-nowrap tabular-nums">{{ dataBr(a.visto_primeiro, false) }}</td>
                        <td class="text-xs max-w-[340px]">
                          <div class="flex items-center gap-1 min-w-0">
                            <span class="truncate" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                            <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                              <ExternalLink class="size-3.5" />
                            </a>
                          </div>
                          <div class="font-mono text-[11px] text-muted-foreground">{{ a.id }} · {{ a.hom || (a.inmetro ? `Inmetro ${a.inmetro}` : 'sem nº') }}</div>
                        </td>
                        <td><span v-if="a.grupo" :class="pillGrupo(a.grupo)">{{ nomeGrupo(a.grupo) }}</span></td>
                        <td class="text-right text-xs tabular-nums">{{ numero(a.vendas) }}</td>
                        <td class="whitespace-nowrap">
                          <span :class="pillTom(a.loja_st.tom)">{{ a.loja_st.rotulo }}</span>
                          <div v-if="a.loja_st.data" class="text-[11px] text-muted-foreground mt-0.5">{{ dataBr(a.loja_st.data, false) }}<template v-if="(a.loja_st.tentativas || 0) > 1"> · {{ a.loja_st.tentativas }} tentativas</template></div>
                        </td>
                        <td class="whitespace-nowrap">
                          <span :class="pillTom(a.anatel_st.tom)">{{ a.anatel_st.rotulo }}</span>
                          <div v-if="a.anatel_st.protocolo" class="font-mono text-[11px] text-muted-foreground mt-0.5">{{ a.anatel_st.protocolo }}</div>
                        </td>
                        <td class="whitespace-nowrap"><span :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao || '—' }}</span></td>
                      </tr>
                      <tr v-if="!anunciosDaLoja.length">
                        <td class="px-4 py-3 text-xs text-muted-foreground">nenhum anúncio desta loja neste filtro</td>
                      </tr>
                    </tbody>
                  </table>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <!-- ══ Por anúncio ══ -->
      <div v-else class="table-card overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th class="w-24" title="Quando o robô viu o anúncio pela primeira vez">Achado em</th>
              <th>Anúncio</th>
              <th>Loja</th>
              <th>Certificado</th>
              <th class="text-right">Vendas</th>
              <th>Na loja</th>
              <th>Na Anatel</th>
              <th>Situação</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && itens.length === 0">
              <td colspan="8" class="text-center text-muted-foreground py-6">carregando…</td>
            </tr>
            <tr v-else-if="itens.length === 0">
              <td colspan="8" class="text-center text-muted-foreground py-6">nenhum anúncio neste filtro</td>
            </tr>
            <tr v-for="a in itens" :key="a.id" class="cursor-pointer" @click="abrirAnuncio(a.id)">
              <td class="text-xs whitespace-nowrap tabular-nums align-top">
                <div>{{ dataBr(a.visto_primeiro, false) }}</div>
                <div v-if="a.visto_primeiro && a.visto_primeiro.length >= 16" class="text-[11px] text-muted-foreground">{{ a.visto_primeiro.slice(11, 16) }}</div>
              </td>
              <td class="text-xs max-w-[320px] align-top">
                <div class="flex items-center gap-1 min-w-0">
                  <span class="truncate" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                  <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                    <ExternalLink class="size-3.5" />
                  </a>
                </div>
                <div class="font-mono text-[11px] text-muted-foreground">{{ a.id }} · {{ a.hom || (a.inmetro ? `Inmetro ${a.inmetro}` : 'sem nº') }}</div>
              </td>
              <td class="text-xs max-w-[180px] align-top">
                <div class="truncate" :title="a.loja || ''">{{ a.loja || '—' }}</div>
                <div class="text-[11px] text-muted-foreground">{{ a.marketplace || '' }}</div>
              </td>
              <td class="align-top"><span v-if="a.grupo" :class="pillGrupo(a.grupo)">{{ nomeGrupo(a.grupo) }}</span></td>
              <td class="text-right text-xs tabular-nums align-top">{{ numero(a.vendas) }}</td>
              <td class="whitespace-nowrap align-top">
                <span :class="pillTom(a.loja_st.tom)">{{ a.loja_st.rotulo }}</span>
                <div v-if="a.loja_st.data" class="text-[11px] text-muted-foreground mt-0.5">{{ dataBr(a.loja_st.data, false) }}<template v-if="(a.loja_st.tentativas || 0) > 1"> · {{ a.loja_st.tentativas }} tentativas</template></div>
              </td>
              <td class="whitespace-nowrap align-top">
                <span :class="pillTom(a.anatel_st.tom)">{{ a.anatel_st.rotulo }}</span>
                <div v-if="a.anatel_st.protocolo" class="font-mono text-[11px] text-muted-foreground mt-0.5">{{ a.anatel_st.protocolo }}</div>
              </td>
              <td class="whitespace-nowrap align-top"><span :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao || '—' }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex items-center justify-between text-sm text-muted-foreground">
        <div v-if="visao === 'loja'">{{ numero(total) }} lojas<span v-if="total > lojas.length"> (mostrando as {{ numero(lojas.length) }} primeiras)</span></div>
        <template v-else>
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
        </template>
      </div>
    </template>

    <DenunciaAnuncioGaveta :anuncio-id="aberto" :foco-denuncia="foco" @fechar="aberto = null; foco = null" />
  </div>
</template>
