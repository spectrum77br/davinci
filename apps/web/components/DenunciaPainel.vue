<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba "Anúncios e denúncias" (01/10/2026) ──────────────────────────
// Vinicius: as abas Anúncios e Denúncias "são quase as mesmas informações" — virou uma só.
// Cada loja (e cada anúncio) com o que a loja fez com a nossa denúncia ("Na loja") e onde
// está na Anatel (07/10: a situação na própria Anatel — na fila, enviada, em análise, respondida, pede
// complemento; "respondida" abre o texto da Anatel), no formato de etiqueta que ele escolheu ("9 removidos · 21 recusadas").
// Sub-abas: Por loja (padrão, sempre na frente) · Por anúncio · Denúncias enviadas (o
// histórico). Clicar num anúncio — ou numa denúncia enviada — abre a ficha com tudo junto.
// Cópia só leitura do sistema do Mac mini; os status vêm de services/denuncia_painel (API).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ChevronDown, ChevronLeft, ChevronRight, ExternalLink, Loader2 } from 'lucide-vue-next'
import {
  type DenunciaEnviada, type FalhaCaso, type PainelAnuncio as Anuncio, type PainelLoja as Loja, ETIQ_ANATEL_FASE, ETIQ_LOJA,
  FASE_COM_RESPOSTA,
  ativoSimNao, dataBr, etiquetas, nomeGrupo, numero, pillAtivo, pillGrupo, pillTom,
} from '~/lib/denuncia'

type Numeros = {
  lojas: number
  anuncios: number
  no_ar: number
  fora_do_ar: number
  processos: number
  na_loja: Record<string, number>
  na_anatel: Record<string, number>
  anatel_fases: Record<string, number>
}
type Opcoes = {
  marketplaces: string[]
  grupos: string[]
  na_loja: { chave: string; rotulo: string }[]
  na_anatel: { chave: string; rotulo: string }[]
}
type Resposta<T> = { total: number; itens: T[]; numeros: Numeros; opcoes: Opcoes; falhas_caso?: FalhaCaso[] }

const { api } = useApi()
const LIMITE = 100

const visao = ref<'loja' | 'anuncio' | 'enviadas' | 'anatel'>('loja')
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
// 01/10 (Vinicius: "tem que clicar na loja e depois no anúncio"): clicar na loja abre a ficha dela
const lojaFicha = ref<Loja | null>(null)
// coluna Caso: o número do caso abre o caso numa NOVA aba do navegador (Vinicius, 01/10: "não trocar a aba atual")
function irCaso(id: number) {
  window.open(`/denuncia?aba=casos&caso=${id}`, '_blank', 'noopener')
}
const foco = ref<number | null>(null)
const enviadas = ref<{ carregar: () => Promise<void> } | null>(null)
const respostasAnatel = ref<{ carregar: () => Promise<void> } | null>(null)
// 07/10: o clique em "respondida" (anúncio ou loja) abre a sub-aba Respostas da Anatel já buscando aquilo
const filtroRespostas = ref('')
function verResposta(q: string | null | undefined) {
  filtroRespostas.value = q || ''
  visao.value = 'anatel'
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
  if (visao.value === 'anatel') {
    await respostasAnatel.value?.carregar()
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
      falhas.value = r.falhas_caso || []
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
      falhas.value = r.falhas_caso || []
    }
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
    vigiarPendentes()
  }
}

// ── 01/10 (Vinicius): "selecionar e enviar para caso, igual a lista de compra" e um "criar" na
// coluna Caso — um caso por loja, com todos os anúncios dela. Quem abre é o sistema do Mac mini
// (alguns segundos): enquanto isso a loja mostra "criando…" e a tela se atualiza sozinha.
const podeEditar = useCan('denuncia', 'edit')
const falhas = ref<FalhaCaso[]>([])
const marcadas = ref<Set<string>>(new Set())
const criando = ref(false)
function podeCriar(x: { shop_id: string | null; casos: unknown[]; caso_pendente?: boolean }): boolean {
  return podeEditar.value && !!x.shop_id && !x.casos.length && !x.caso_pendente
}
const marcaveis = computed(() => lojas.value.filter(podeCriar))
const todasMarcadas = computed(() => marcaveis.value.length > 0 && marcaveis.value.every((l) => marcadas.value.has(chaveLoja(l))))
function marcar(l: Loja) {
  const s = new Set(marcadas.value)
  const k = chaveLoja(l)
  if (s.has(k)) s.delete(k)
  else s.add(k)
  marcadas.value = s
}
function marcarTodas() {
  marcadas.value = todasMarcadas.value ? new Set() : new Set(marcaveis.value.map(chaveLoja))
}
async function criarCasos(alvos: { marketplace: string | null; shop_id: string | null; loja: string | null; anuncios?: number }[]) {
  const validos = alvos.filter((x) => x.shop_id)
  if (!validos.length || criando.value) return
  const nomes = validos.map((x) => `• ${x.loja || x.shop_id}${x.anuncios ? ` (${x.anuncios} anúncio${x.anuncios > 1 ? 's' : ''})` : ''}`)
  const texto = validos.length === 1
    ? `Criar o caso da loja ${validos[0].loja || validos[0].shop_id}?\nO caso leva todos os anúncios da loja.`
    : `Criar ${validos.length} casos — um por loja, com todos os anúncios de cada uma?\n\n${nomes.slice(0, 15).join('\n')}${nomes.length > 15 ? `\n… e mais ${nomes.length - 15}` : ''}`
  if (!window.confirm(texto)) return
  criando.value = true
  try {
    const r = await api<{ criados: { loja: string }[]; pulados: { loja: string; motivo: string }[] }>(
      '/api/denuncia/casos/criar',
      { method: 'POST', body: { lojas: validos.map((x) => ({ marketplace: x.marketplace, shop_id: x.shop_id })) } },
    )
    const t = useToasts()
    if (r.criados.length) {
      t.push({
        kind: 'success',
        title: r.criados.length === 1 ? 'Caso pedido ao robô' : `${r.criados.length} casos pedidos ao robô`,
        lines: ['Aparece em alguns segundos (a tela atualiza sozinha).', ...r.pulados.map((p) => `${p.loja}: ${p.motivo}`)],
      })
    } else {
      t.push({ kind: 'error', title: 'Nenhum caso criado', lines: r.pulados.map((p) => `${p.loja}: ${p.motivo}`) })
    }
    marcadas.value = new Set()
    await carregar()
  } catch (e: any) {
    useToasts().push({ kind: 'error', title: 'Não deu para pedir o caso', lines: e?.data?.detail?.code || e?.message || 'erro' })
  } finally {
    criando.value = false
  }
}
function criarDoAnuncio(a: Anuncio) {
  void criarCasos([{ marketplace: a.marketplace, shop_id: a.shop_id, loja: a.loja }])
}
// enquanto houver "criando…" na tela, recarrega de 8 em 8 s (até 3 min)
let vigia: ReturnType<typeof setTimeout> | null = null
let vigiaDesde = 0
function vigiarPendentes() {
  if (vigia) clearTimeout(vigia)
  vigia = null
  const pendente = visao.value === 'loja' ? lojas.value.some((l) => l.caso_pendente) : itens.value.some((a) => a.caso_pendente)
  if (!pendente) {
    vigiaDesde = 0
    return
  }
  vigiaDesde = vigiaDesde || Date.now()
  if (Date.now() - vigiaDesde > 180_000) return
  vigia = setTimeout(() => void carregar(), 8000)
}
onBeforeUnmount(() => {
  if (vigia) clearTimeout(vigia)
})

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

function trocarVisao(v: 'loja' | 'anuncio' | 'enviadas' | 'anatel') {
  if (visao.value === v) return
  visao.value = v
  offset.value = 0
  if (v !== 'enviadas' && v !== 'anatel') void carregar()
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
  if (visao.value === 'enviadas' || visao.value === 'anatel') visao.value = 'loja'
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
        <StatCard compact label="Ativos" :value="numero(n.no_ar)" tone="warning" hint="ainda vendendo" />
      </button>
      <button type="button" class="text-left rounded-lg" :class="situacao === 'fora do ar' ? 'ring-2 ring-primary' : ''" title="filtrar os que saíram do ar" @click="porCartao('situacao', 'fora do ar')">
        <StatCard compact label="Não ativos" :value="numero(n.fora_do_ar)" tone="success" hint="saíram do ar depois de vistos" />
      </button>
      <button type="button" class="text-left rounded-lg" :class="naLoja === 'recusou' ? 'ring-2 ring-primary' : ''" title="filtrar os que a loja recusou" @click="porCartao('naLoja', 'recusou')">
        <StatCard
          compact label="Loja recusou" :value="numero(n.na_loja.recusou)" tone="danger"
          :hint="`${numero(n.na_loja.conferindo)} conferindo · ${numero(n.na_loja.aguardando)} aguardando · ${numero(n.na_loja.removido)} removidos`"
        />
      </button>
      <button type="button" class="text-left rounded-lg" :class="naAnatel === 'processo' ? 'ring-2 ring-primary' : ''" title="filtrar os que já têm processo na Anatel" @click="porCartao('naAnatel', 'processo')">
        <StatCard compact label="Processos na Anatel" :value="numero(n.processos)" :hint="`${numero(n.na_anatel.processo)} anúncios`" />
      </button>
      <button type="button" class="text-left rounded-lg" :class="naAnatel === 'fila' ? 'ring-2 ring-primary' : ''" title="filtrar os que estão na fila da Anatel" @click="porCartao('naAnatel', 'fila')">
        <StatCard
          compact label="Na fila da Anatel" :value="numero(n.na_anatel.fila)" tone="warning"
          :hint="`${numero((n.anatel_fases.respondida || 0) + (n.anatel_fases.complemento || 0))} com resposta da Anatel`"
        />
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="flex items-center gap-1 border-b border-border">
      <button
        v-for="v in [{ k: 'loja', t: 'Por loja' }, { k: 'anuncio', t: 'Por anúncio' }, { k: 'enviadas', t: 'Denúncias enviadas' }, { k: 'anatel', t: 'Respostas da Anatel' }]"
        :key="v.k"
        type="button"
        class="-mb-px inline-flex h-9 items-center border-b-2 px-3 text-sm font-medium transition-colors"
        :class="visao === v.k ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="trocarVisao(v.k as 'loja' | 'anuncio' | 'enviadas' | 'anatel')"
      >
        {{ v.t }}
      </button>
    </div>

    <DenunciaEnviadas v-if="visao === 'enviadas'" ref="enviadas" @abrir="abrirEnviada" />
    <!-- 07/10/2026: o que a Anatel respondeu (texto) e o que fizemos depois -->
    <DenunciaRespostasAnatel v-else-if="visao === 'anatel'" ref="respostasAnatel" v-model:filtro="filtroRespostas" @abrir="(a, d) => abrirAnuncio(a, d)" />

    <template v-else>
      <div v-if="falhas.length" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
        <div class="font-medium">O robô do Mac mini não conseguiu:</div>
        <div v-for="f in falhas" :key="f.id" class="text-xs">• {{ f.texto }} — {{ f.resultado || 'sem resposta' }}</div>
      </div>
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
          <option value="">ativo: todos</option>
          <option value="ativo">ativo: sim</option>
          <option value="fora do ar">ativo: não</option>
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
        <div v-if="visao === 'loja' && podeEditar" class="ml-auto flex items-center gap-2">
          <Button v-if="marcadas.size" size="sm" variant="ghost" class="h-8 text-xs" @click="marcadas = new Set()">limpar</Button>
          <Button
            size="sm"
            variant="outline"
            class="h-8 text-xs"
            :disabled="!marcadas.size || criando"
            title="marque as lojas na tabela — um caso por loja, com todos os anúncios dela"
            @click="criarCasos(lojas.filter((l) => marcadas.has(chaveLoja(l))))"
          >
            <Loader2 v-if="criando" class="mr-1 size-3.5 animate-spin" />
            enviar para caso{{ marcadas.size ? ` (${marcadas.size})` : '' }}
          </Button>
        </div>
      </div>

      <!-- ══ Por loja ══ — 01/10 (Vinicius: "tudo desalinhado"): colunas de largura fixa e os
           anúncios da loja aberta como linhas da MESMA tabela (antes era uma tabela dentro da outra) -->
      <div v-if="visao === 'loja'" class="table-card overflow-x-auto">
        <table class="w-full min-w-[1120px] table-fixed">
          <colgroup>
            <col class="w-[40px]">
            <col class="w-[108px]">
            <col>
            <col class="w-[140px]">
            <col class="w-[96px]">
            <col class="w-[96px]">
            <col class="w-[210px]">
            <col class="w-[220px]">
            <col class="w-[92px]">
            <col class="w-[110px]">
          </colgroup>
          <thead>
            <tr class="[&>th]:whitespace-nowrap">
              <th class="!px-0 !text-center">
                <input v-if="podeEditar && marcaveis.length" type="checkbox" class="size-4 align-middle" :checked="todasMarcadas" title="marcar todas as lojas sem caso" @change="marcarTodas">
              </th>
              <th title="Quando o robô achou o anúncio mais novo desta loja">Último achado</th>
              <th>Loja</th>
              <th>Certificado</th>
              <th class="text-center">Anúncios</th>
              <th class="text-center">Vendas</th>
              <th>Na loja</th>
              <th>Na Anatel</th>
              <th title="Ainda vendendo? (anúncios no ar / total)">Ativo</th>
              <th>Caso</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && lojas.length === 0">
              <td colspan="10" class="text-center text-muted-foreground py-6">carregando…</td>
            </tr>
            <tr v-else-if="lojas.length === 0">
              <td colspan="10" class="text-center text-muted-foreground py-6">nenhuma loja neste filtro</td>
            </tr>
            <template v-for="l in lojas" :key="chaveLoja(l)">
              <tr
                class="cursor-pointer [&>td]:align-middle"
                :class="marcadas.has(chaveLoja(l)) ? 'bg-primary/5' : lojaAberta === chaveLoja(l) ? 'bg-muted/40' : ''"
                @click="lojaFicha = l"
              >
                <td class="!px-0 text-center" @click.stop>
                  <input v-if="podeCriar(l)" type="checkbox" class="size-4 align-middle" :checked="marcadas.has(chaveLoja(l))" title="marcar para enviar para caso" @change="marcar(l)">
                </td>
                <td class="text-xs tabular-nums whitespace-nowrap" :title="l.ultimo_achado || ''">
                  <div>{{ dataBr(l.ultimo_achado, false) }}</div>
                  <div class="text-[11px] text-muted-foreground">{{ l.ultimo_achado && l.ultimo_achado.length >= 16 ? l.ultimo_achado.slice(11, 16) : '' }}</div>
                </td>
                <td>
                  <div class="flex items-center gap-1 min-w-0">
                    <button
                      type="button"
                      class="-ml-1 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                      :title="lojaAberta === chaveLoja(l) ? 'fechar os anúncios' : 'ver os anúncios aqui'"
                      @click.stop="abrirLoja(l)"
                    >
                      <ChevronDown v-if="lojaAberta === chaveLoja(l)" class="size-3.5" />
                      <ChevronRight v-else class="size-3.5" />
                    </button>
                    <span class="truncate text-sm font-medium" :title="l.loja || ''">{{ l.loja || l.shop_id || 'sem loja' }}</span>
                  </div>
                  <div class="pl-[18px] text-[11px] text-muted-foreground truncate">
                    {{ l.marketplace || '—' }}<span v-if="l.shop_id" class="font-mono"> · {{ l.shop_id }}</span>
                  </div>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-if="l.nosso" class="pill-danger">Nosso {{ l.nosso }}</span>
                    <span v-if="l.diversos" class="pill-warning">Diversos {{ l.diversos }}</span>
                    <span v-if="l.outros" class="pill-muted">outros {{ l.outros }}</span>
                  </div>
                </td>
                <td class="text-center text-sm tabular-nums">{{ numero(l.anuncios) }}</td>
                <td class="text-center text-sm font-medium tabular-nums">{{ numero(l.vendas) }}</td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="e in etiquetas(l.na_loja, ETIQ_LOJA)" :key="e.k" :class="e.cls">{{ e.texto }}</span>
                  </div>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1" :title="l.processos.join('\n')">
                    <template v-for="e in etiquetas(l.anatel_fases || {}, ETIQ_ANATEL_FASE)" :key="e.k">
                      <button
                        v-if="FASE_COM_RESPOSTA.includes(e.k)"
                        type="button"
                        :class="[e.cls, 'hover:underline']"
                        title="ver a resposta da Anatel"
                        @click.stop="verResposta(l.shop_id || l.loja)"
                      >{{ e.texto }}</button>
                      <span v-else :class="e.cls">{{ e.texto }}</span>
                    </template>
                    <span v-if="!etiquetas(l.anatel_fases || {}, ETIQ_ANATEL_FASE).length" class="text-xs text-muted-foreground">—</span>
                  </div>
                </td>
                <td>
                  <span :class="l.no_ar ? 'pill-warning' : 'pill-success'">{{ l.no_ar ? 'Sim' : 'Não' }}</span>
                  <div class="text-[11px] text-muted-foreground mt-0.5 tabular-nums whitespace-nowrap">{{ numero(l.no_ar) }} de {{ numero(l.anuncios) }}</div>
                </td>
                <td @click.stop>
                  <div v-if="l.casos.length" class="flex flex-wrap gap-1">
                    <button v-for="c in l.casos" :key="c.id" type="button" class="pill-info hover:underline" :title="`abrir o ${c.codigo} (${c.status})`" @click.stop="irCaso(c.id)">{{ c.codigo }}</button>
                  </div>
                  <span v-else-if="l.caso_pendente" class="pill-muted whitespace-nowrap" title="pedido ao robô do Mac mini — aparece em alguns segundos"><Loader2 class="size-3 animate-spin" /> criando…</span>
                  <Button v-else-if="podeCriar(l)" size="sm" variant="outline" class="h-7 px-2.5 text-xs" :disabled="criando" title="criar o caso desta loja (todos os anúncios dela)" @click="criarCasos([l])">criar</Button>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </td>
              </tr>
              <template v-if="lojaAberta === chaveLoja(l)">
                <tr v-if="carregandoLoja">
                  <td colspan="10" class="bg-muted/20 text-xs text-muted-foreground">carregando os anúncios da loja…</td>
                </tr>
                <tr v-else-if="!anunciosDaLoja.length">
                  <td colspan="10" class="bg-muted/20 text-xs text-muted-foreground">nenhum anúncio desta loja neste filtro</td>
                </tr>
                <tr
                  v-for="a in anunciosDaLoja"
                  :key="a.id"
                  class="cursor-pointer bg-muted/20 [&>td]:align-middle"
                  @click="abrirAnuncio(a.id)"
                >
                  <td />
                  <td class="text-xs tabular-nums whitespace-nowrap text-muted-foreground">{{ dataBr(a.visto_primeiro, false) }}</td>
                  <td>
                    <div class="pl-[18px] border-l-2 border-border ml-1.5">
                      <div class="flex items-center gap-1 min-w-0">
                        <span class="truncate text-xs" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                        <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                          <ExternalLink class="size-3.5" />
                        </a>
                      </div>
                      <div class="font-mono text-[11px] text-muted-foreground truncate">{{ a.id }} · {{ a.hom || (a.inmetro ? `Inmetro ${a.inmetro}` : 'sem nº') }}</div>
                    </div>
                  </td>
                  <td><span v-if="a.grupo" :class="pillGrupo(a.grupo)">{{ nomeGrupo(a.grupo) }}</span></td>
                  <td />
                  <td class="text-center text-xs tabular-nums">{{ numero(a.vendas) }}</td>
                  <td>
                    <span v-if="a.loja_st.chave === 'vazio'" class="text-xs text-muted-foreground">—</span>
                    <span v-else :class="pillTom(a.loja_st.tom)">{{ a.loja_st.rotulo }}</span>
                    <div v-if="a.loja_st.data && a.loja_st.chave !== 'vazio'" class="text-[11px] text-muted-foreground mt-0.5 whitespace-nowrap">{{ dataBr(a.loja_st.data, false) }}<template v-if="(a.loja_st.tentativas || 0) > 1"> · {{ a.loja_st.tentativas }} tentativas</template></div>
                  </td>
                  <td>
                    <button
                  v-if="FASE_COM_RESPOSTA.includes(a.anatel_st.fase || '')"
                  type="button"
                  :class="[pillTom(a.anatel_st.tom), 'hover:underline']"
                  title="ver a resposta da Anatel"
                  @click.stop="verResposta(a.anatel_st.resposta_protocolo || a.id)"
                >{{ a.anatel_st.rotulo }}</button>
                <span v-else :class="pillTom(a.anatel_st.tom)">{{ a.anatel_st.rotulo }}</span>
                    <div v-if="a.anatel_st.protocolo" class="font-mono text-[11px] text-muted-foreground mt-0.5 truncate">{{ a.anatel_st.protocolo }}</div>
                  </td>
                  <td><span :class="pillAtivo(a.situacao)">{{ ativoSimNao(a.situacao) }}</span></td>
                  <td>
                    <div v-if="a.casos.length" class="flex flex-wrap gap-1">
                      <button v-for="c in a.casos" :key="c.id" type="button" class="pill-info hover:underline" :title="`abrir o ${c.codigo} (${c.status})`" @click.stop="irCaso(c.id)">{{ c.codigo }}</button>
                    </div>
                    <span v-else-if="a.caso_pendente || l.caso_pendente" class="text-xs text-muted-foreground">criando…</span>
                    <span v-else class="text-xs text-muted-foreground">—</span>
                  </td>
                </tr>
              </template>
            </template>
          </tbody>
        </table>
      </div>

      <!-- ══ Por anúncio ══ -->
      <div v-else class="table-card overflow-x-auto">
        <table class="w-full min-w-[1080px] table-fixed">
          <colgroup>
            <col class="w-[108px]">
            <col>
            <col class="w-[180px]">
            <col class="w-[110px]">
            <col class="w-[96px]">
            <col class="w-[200px]">
            <col class="w-[220px]">
            <col class="w-[80px]">
            <col class="w-[110px]">
          </colgroup>
          <thead>
            <tr class="[&>th]:whitespace-nowrap">
              <th title="Quando o robô viu o anúncio pela primeira vez">Achado em</th>
              <th>Anúncio</th>
              <th>Loja</th>
              <th>Certificado</th>
              <th class="text-center">Vendas</th>
              <th>Na loja</th>
              <th>Na Anatel</th>
              <th title="Ainda vendendo?">Ativo</th>
              <th>Caso</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && itens.length === 0">
              <td colspan="9" class="text-center text-muted-foreground py-6">carregando…</td>
            </tr>
            <tr v-else-if="itens.length === 0">
              <td colspan="9" class="text-center text-muted-foreground py-6">nenhum anúncio neste filtro</td>
            </tr>
            <tr v-for="a in itens" :key="a.id" class="cursor-pointer [&>td]:align-middle" @click="abrirAnuncio(a.id)">
              <td class="text-xs tabular-nums whitespace-nowrap">
                <div>{{ dataBr(a.visto_primeiro, false) }}</div>
                <div class="text-[11px] text-muted-foreground">{{ a.visto_primeiro && a.visto_primeiro.length >= 16 ? a.visto_primeiro.slice(11, 16) : '' }}</div>
              </td>
              <td>
                <div class="flex items-center gap-1 min-w-0">
                  <span class="truncate text-xs" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                  <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                    <ExternalLink class="size-3.5" />
                  </a>
                </div>
                <div class="font-mono text-[11px] text-muted-foreground truncate">{{ a.id }} · {{ a.hom || (a.inmetro ? `Inmetro ${a.inmetro}` : 'sem nº') }}</div>
              </td>
              <td>
                <div class="truncate text-xs" :title="a.loja || ''">{{ a.loja || '—' }}</div>
                <div class="text-[11px] text-muted-foreground truncate">{{ a.marketplace || '' }}</div>
              </td>
              <td><span v-if="a.grupo" :class="pillGrupo(a.grupo)">{{ nomeGrupo(a.grupo) }}</span></td>
              <td class="text-center text-xs tabular-nums">{{ numero(a.vendas) }}</td>
              <td>
                <span v-if="a.loja_st.chave === 'vazio'" class="text-xs text-muted-foreground">—</span>
                    <span v-else :class="pillTom(a.loja_st.tom)">{{ a.loja_st.rotulo }}</span>
                <div v-if="a.loja_st.data && a.loja_st.chave !== 'vazio'" class="text-[11px] text-muted-foreground mt-0.5 whitespace-nowrap">{{ dataBr(a.loja_st.data, false) }}<template v-if="(a.loja_st.tentativas || 0) > 1"> · {{ a.loja_st.tentativas }} tentativas</template></div>
              </td>
              <td>
                <button
                  v-if="FASE_COM_RESPOSTA.includes(a.anatel_st.fase || '')"
                  type="button"
                  :class="[pillTom(a.anatel_st.tom), 'hover:underline']"
                  title="ver a resposta da Anatel"
                  @click.stop="verResposta(a.anatel_st.resposta_protocolo || a.id)"
                >{{ a.anatel_st.rotulo }}</button>
                <span v-else :class="pillTom(a.anatel_st.tom)">{{ a.anatel_st.rotulo }}</span>
                <div v-if="a.anatel_st.protocolo" class="font-mono text-[11px] text-muted-foreground mt-0.5 truncate">{{ a.anatel_st.protocolo }}</div>
              </td>
              <td><span :class="pillAtivo(a.situacao)">{{ ativoSimNao(a.situacao) }}</span></td>
              <td @click.stop>
                <div v-if="a.casos.length" class="flex flex-wrap gap-1">
                  <button v-for="c in a.casos" :key="c.id" type="button" class="pill-info hover:underline" :title="`abrir o ${c.codigo} (${c.status})`" @click.stop="irCaso(c.id)">{{ c.codigo }}</button>
                </div>
                <span v-else-if="a.caso_pendente" class="pill-muted whitespace-nowrap" title="pedido ao robô do Mac mini — aparece em alguns segundos"><Loader2 class="size-3 animate-spin" /> criando…</span>
                <Button v-else-if="podeCriar(a)" size="sm" variant="outline" class="h-7 px-2.5 text-xs" :disabled="criando" :title="`criar o caso da loja ${a.loja || ''} (todos os anúncios dela)`" @click="criarDoAnuncio(a)">criar</Button>
                <span v-else class="text-xs text-muted-foreground">—</span>
              </td>
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

    <DenunciaLojaGaveta :loja="lojaFicha" :propria="propria" @fechar="lojaFicha = null" @caso="irCaso" />
    <DenunciaAnuncioGaveta :anuncio-id="aberto" :foco-denuncia="foco" @fechar="aberto = null; foco = null" @caso="irCaso" />
  </div>
</template>
