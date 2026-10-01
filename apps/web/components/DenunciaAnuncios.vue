<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Anúncios (30/09/2026) ───────────────────────────────────────
// Os anúncios que o robô da fiscalização (Mac mini da Makisa) achou nos
// marketplaces usando a nossa homologação/marca. Cópia só leitura do sistema
// de lá. Mesmo recorte padrão do sistema do mini: sem os descartados/fora de
// escopo (só aparecem filtrando o Grupo) e sem os anúncios das lojas próprias.
// 01/10 (Vinicius: "por loja — essa loja quantos anúncios tem, a soma das
// vendas… por loja sempre na frente"): duas visões com os mesmos filtros —
// "Por loja" (padrão; clicar na loja abre os anúncios dela) e "Por anúncio".
import { onMounted, ref } from 'vue'
import { ChevronDown, ChevronLeft, ChevronRight, ExternalLink } from 'lucide-vue-next'
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
  denuncias: number
  com_denuncia: number
  ultimo_achado: string | null
}
type Numeros = { total: number; ativos: number; fora_do_ar: number; com_denuncia: number }
type Opcoes = { marketplaces: string[]; grupos: string[] }
type Resposta = { total: number; itens: Item[]; numeros: Numeros; opcoes: Opcoes }
type RespostaLojas = { total: number; itens: Loja[]; numeros: Numeros; opcoes: Opcoes }

const { api } = useApi()
const LIMITE = 100

const visao = ref<'loja' | 'anuncio'>('loja')
const itens = ref<Item[]>([])
const lojas = ref<Loja[]>([])
const total = ref(0)
const numeros = ref<Numeros | null>(null)
const opcoes = ref<Opcoes>({ marketplaces: [], grupos: [] })
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
const ordemLoja = ref('vendas')

const aberto = ref<string | null>(null)
// loja aberta na visão "por loja": os anúncios dela (mesmos filtros)
const lojaAberta = ref<string | null>(null)
const anunciosDaLoja = ref<Item[]>([])
const carregandoLoja = ref(false)

function chaveLoja(l: Loja): string {
  return `${l.marketplace || ''}|${l.chave}`
}

function filtros(): URLSearchParams {
  const qs = new URLSearchParams({ propria: propria.value })
  if (q.value.trim()) qs.set('q', q.value.trim())
  if (marketplace.value) qs.set('marketplace', marketplace.value)
  if (grupo.value) qs.set('grupo', grupo.value)
  if (situacao.value) qs.set('situacao', situacao.value)
  if (den.value) qs.set('den', den.value)
  return qs
}

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const qs = filtros()
    if (visao.value === 'loja') {
      qs.set('ordem', ordemLoja.value)
      const r = await api<RespostaLojas>(`/api/denuncia/anuncios/lojas?${qs}`)
      lojas.value = r.itens
      total.value = r.total
      numeros.value = r.numeros
      opcoes.value = r.opcoes
      if (lojaAberta.value && !r.itens.some((l) => chaveLoja(l) === lojaAberta.value)) lojaAberta.value = null
    } else {
      qs.set('limite', String(LIMITE))
      qs.set('offset', String(offset.value))
      qs.set('ordem', ordem.value)
      const r = await api<Resposta>(`/api/denuncia/anuncios?${qs}`)
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
    qs.set('loja', l.chave)
    if (l.marketplace) qs.set('marketplace', l.marketplace)
    qs.set('limite', '500')
    qs.set('ordem', 'vendas')
    const r = await api<Resposta>(`/api/denuncia/anuncios?${qs}`)
    if (lojaAberta.value === k) anunciosDaLoja.value = r.itens
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregandoLoja.value = false
  }
}

function trocarVisao(v: 'loja' | 'anuncio') {
  if (visao.value === v) return
  visao.value = v
  offset.value = 0
  void carregar()
}

function filtrar() {
  offset.value = 0
  lojaAberta.value = null
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
      <StatCard compact label="Anúncios" :value="numero(numeros.total)" :hint="visao === 'loja' ? `em ${numero(total)} lojas` : 'no filtro atual'" />
      <StatCard compact label="No ar" :value="numero(numeros.ativos)" tone="warning" hint="ainda vendendo" />
      <StatCard compact label="Fora do ar" :value="numero(numeros.fora_do_ar)" tone="success" hint="saíram depois de vistos" />
      <StatCard compact label="Com denúncia" :value="numero(numeros.com_denuncia)" hint="ao menos uma denúncia feita" />
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="flex items-center gap-1 border-b border-border">
      <button
        v-for="v in [{ k: 'loja', t: 'Por loja' }, { k: 'anuncio', t: 'Por anúncio' }]"
        :key="v.k"
        type="button"
        class="-mb-px inline-flex h-9 items-center border-b-2 px-3 text-sm font-medium transition-colors"
        :class="visao === v.k ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="trocarVisao(v.k as 'loja' | 'anuncio')"
      >
        {{ v.t }}
      </button>
    </div>

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
      <select v-if="visao === 'loja'" v-model="ordemLoja" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="vendas">mais vendas</option>
        <option value="anuncios">mais anúncios</option>
        <option value="denuncias">mais denúncias</option>
        <option value="nome">nome da loja</option>
      </select>
      <select v-else v-model="ordem" class="h-9 rounded-md border bg-background px-2 text-sm" @change="filtrar">
        <option value="vendas">mais vendas</option>
        <option value="novos">achados mais recentes</option>
        <option value="loja">por loja</option>
        <option value="grupo">por certificado</option>
      </select>
    </div>

    <!-- ══ Por loja ══ -->
    <div v-if="visao === 'loja'" class="table-card overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th title="Quando o robô achou o anúncio mais novo desta loja">Último achado</th>
            <th>Loja</th>
            <th>Marketplace</th>
            <th>Certificado</th>
            <th class="text-right">Anúncios</th>
            <th class="text-right">Vendas</th>
            <th class="text-right">Denúncias</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && lojas.length === 0">
            <td colspan="7" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="lojas.length === 0">
            <td colspan="7" class="text-center text-muted-foreground py-6">nenhuma loja neste filtro</td>
          </tr>
          <template v-for="l in lojas" :key="chaveLoja(l)">
            <tr class="cursor-pointer" @click="abrirLoja(l)">
              <td class="text-xs whitespace-nowrap tabular-nums" :title="l.ultimo_achado || ''">
                <div>{{ dataBr(l.ultimo_achado, false) }}</div>
                <div v-if="l.ultimo_achado && l.ultimo_achado.length >= 16" class="text-[11px] text-muted-foreground">{{ l.ultimo_achado.slice(11, 16) }}</div>
              </td>
              <td class="max-w-[240px]">
                <div class="flex items-center gap-1 min-w-0">
                  <ChevronDown v-if="lojaAberta === chaveLoja(l)" class="size-3.5 shrink-0 text-muted-foreground" />
                  <ChevronRight v-else class="size-3.5 shrink-0 text-muted-foreground" />
                  <span class="truncate text-sm font-medium" :title="l.loja || ''">{{ l.loja || l.shop_id || 'sem loja' }}</span>
                </div>
                <div v-if="l.shop_id" class="pl-[18px] font-mono text-[11px] text-muted-foreground">{{ l.shop_id }}</div>
              </td>
              <td class="text-xs whitespace-nowrap">{{ l.marketplace || '—' }}</td>
              <td class="whitespace-nowrap">
                <span v-if="l.nosso" class="pill-danger mr-1">Nosso {{ l.nosso }}</span>
                <span v-if="l.diversos" class="pill-warning mr-1">Diversos {{ l.diversos }}</span>
                <span v-if="l.outros" class="pill-muted">outros {{ l.outros }}</span>
              </td>
              <td class="text-right text-xs tabular-nums whitespace-nowrap">
                {{ numero(l.anuncios) }}
                <div class="text-[11px] text-muted-foreground">{{ numero(l.no_ar) }} no ar</div>
              </td>
              <td class="text-right text-sm font-medium tabular-nums">{{ numero(l.vendas) }}</td>
              <td class="text-right text-xs tabular-nums whitespace-nowrap">
                {{ l.denuncias ? numero(l.denuncias) : '—' }}
                <div v-if="l.denuncias" class="text-[11px] text-muted-foreground">em {{ numero(l.com_denuncia) }} anúncio{{ l.com_denuncia > 1 ? 's' : '' }}</div>
              </td>
            </tr>
            <tr v-if="lojaAberta === chaveLoja(l)">
              <td colspan="7" class="bg-muted/30 p-0">
                <div v-if="carregandoLoja" class="px-4 py-3 text-xs text-muted-foreground">carregando os anúncios da loja…</div>
                <table v-else class="w-full">
                  <tbody>
                    <tr v-for="a in anunciosDaLoja" :key="a.id" class="cursor-pointer" @click="aberto = a.id">
                      <td class="pl-8 text-xs whitespace-nowrap tabular-nums">
                        {{ dataBr(a.visto_primeiro, false) }}
                        <span v-if="a.visto_primeiro && a.visto_primeiro.length >= 16" class="text-[11px] text-muted-foreground">{{ a.visto_primeiro.slice(11, 16) }}</span>
                      </td>
                      <td class="font-mono text-xs whitespace-nowrap">{{ a.id }}</td>
                      <td class="text-xs max-w-[340px]">
                        <div class="flex items-center gap-1 min-w-0">
                          <span class="truncate" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                          <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                            <ExternalLink class="size-3.5" />
                          </a>
                        </div>
                      </td>
                      <td class="font-mono text-xs whitespace-nowrap">{{ a.hom || (a.inmetro ? `Inmetro ${a.inmetro}` : '—') }}</td>
                      <td><span v-if="a.grupo" :class="pillGrupo(a.grupo)" :title="a.grupo">{{ nomeGrupo(a.grupo) }}</span></td>
                      <td class="text-right text-xs tabular-nums">{{ numero(a.vendas) }}</td>
                      <td class="whitespace-nowrap"><span :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao || '—' }}</span></td>
                      <td class="text-right text-xs tabular-nums">{{ a.nden ? `${a.nden} den.` : '' }}</td>
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

    <DenunciaAnuncioGaveta :anuncio-id="aberto" @fechar="aberto = null" />
  </div>
</template>
