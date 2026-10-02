<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Casos (30/09/2026) ──────────────────────────────────────────
// Casos jurídicos (CASO-00N): o anúncio, a compra de prova (pedido, NF-e,
// entrega), as provas obrigatórias e o envio ao advogado. Cópia só leitura
// do sistema de Fiscalização do Mac mini da Makisa.
import { computed, onMounted, ref, watch } from 'vue'
import {
  type Prova, ativoSimNao, dataBr, dinheiro, numero, pillAtivo, pillResultado, pillSituacaoDenuncia,
  pillStatusCaso, pillStatusCompra,
} from '~/lib/denuncia'


type Caso = {
  id: number
  codigo: string | null
  titulo: string | null
  status: string | null
  anuncio_id: string | null
  loja: string | null
  titulo_anuncio: string | null
  marketplace: string | null
  aberto_em: string | null
  juridico: string | null
  juridico_enviado_em: string | null
  ncompras: number
  nprovas: number
  url: string | null
  hom: string | null
  vendas: number | null
  shop_id: string | null
  compra: { pedido: string | null; status: string | null; valor_pago: number | null; data: string | null } | null
}
type Resposta = { total: number; itens: Caso[]; por_status: Record<string, number> }
type Detalhe = {
  caso: Record<string, any>
  anuncio: Record<string, any> | null
  compras: Record<string, any>[]
  provas: Prova[]
  denuncias: Record<string, any>[]
}

const { api } = useApi()
const itens = ref<Caso[]>([])
const porStatus = ref<Record<string, number>>({})
const carregando = ref(true)
const erro = ref<string | null>(null)
const status = ref('')

const aberto = ref<number | null>(null)
const detalhe = ref<Detalhe | null>(null)
const anuncioAberto = ref<string | null>(null)

const visiveis = computed(() => (status.value ? itens.value.filter((c) => (c.status || '—') === status.value) : itens.value))

// ── 01/10 (Vinicius: "um botão para selecionar os casos e gerar lista de compra — loja, anúncio,
// valor, produto"): marca os casos e monta a lista para quem vai comprar (copiar ou baixar CSV).
// O robô não guarda o preço do anúncio: o valor aparece quando a compra já foi registrada.
const selecionados = ref<Set<number>>(new Set())
function marcar(id: number) {
  const s = new Set(selecionados.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  selecionados.value = s
}
const todosMarcados = computed(() => visiveis.value.length > 0 && visiveis.value.every((c) => selecionados.value.has(c.id)))
function marcarTodos() {
  selecionados.value = todosMarcados.value ? new Set() : new Set(visiveis.value.map((c) => c.id))
}
const listaAberta = ref(false)
const lista = computed(() =>
  itens.value
    .filter((c) => selecionados.value.has(c.id))
    .map((c) => ({
      caso: c.codigo || `#${c.id}`,
      loja: c.loja || '—',
      marketplace: c.marketplace || '',
      anuncio: c.anuncio_id || '',
      produto: c.titulo_anuncio || '',
      url: c.url || '',
      valor: c.compra?.valor_pago ?? null,
      comprado: c.compra ? `${c.compra.status || 'comprado'}${c.compra.pedido ? ` · pedido ${c.compra.pedido}` : ''}` : 'não',
    })),
)
const COLUNAS: [keyof (typeof lista.value)[number], string][] = [
  ['caso', 'Caso'], ['loja', 'Loja'], ['marketplace', 'Marketplace'], ['anuncio', 'Anúncio'],
  ['produto', 'Produto'], ['valor', 'Valor'], ['comprado', 'Comprado?'], ['url', 'Link'],
]
function textoLista(sep: string): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? '' : typeof v === 'number' ? v.toFixed(2).replace('.', ',') : String(v)
    return sep === ';' && /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [COLUNAS.map(([, n]) => n).join(sep), ...lista.value.map((l) => COLUNAS.map(([k]) => esc(l[k])).join(sep))].join('\n')
}
const copiado = ref(false)
async function copiarLista() {
  try {
    await navigator.clipboard.writeText(textoLista('\t'))
    copiado.value = true
    setTimeout(() => (copiado.value = false), 2000)
  } catch {
    erro.value = 'não consegui copiar — use "baixar planilha"'
  }
}
function baixarLista() {
  const blob = new Blob(['\ufeff' + textoLista(';')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `lista-de-compra-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const r = await api<Resposta>('/api/denuncia/casos')
    itens.value = r.itens
    porStatus.value = r.por_status
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}


type AbaCaso = 'resumo' | 'juridico' | 'compra' | 'provas' | 'denuncias'
const abaCaso = ref<AbaCaso>('resumo')

async function abrir(c: Caso, focoJuridico = false) {
  aberto.value = c.id
  detalhe.value = null
  abaCaso.value = focoJuridico ? 'juridico' : 'resumo'
  try {
    detalhe.value = await api<Detalhe>(`/api/denuncia/casos/${c.id}`)
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const gavetaAberta = computed({
  get: () => aberto.value !== null,
  set: (v: boolean) => {
    if (!v) aberto.value = null
  },
})
const k = computed(() => detalhe.value?.caso || {})

// 01/10 (Vinicius: "quais são os itens obrigatórios para enviar ao jurídico?") — as mesmas listas do
// sistema do mini: para ABRIR o caso (db.DOCS_CASO) e o "pronto para o advogado" (modelos.CHECKLIST_ADVOGADO).
// O certificado (PDF do nº declarado) o robô confere na hora de montar o pacote.
const checklist = computed(() => {
  const d = detalhe.value
  if (!d) return []
  const tem = (...tipos: string[]) => d.provas.filter((p) => p.tipo && tipos.includes(p.tipo)).length
  const devolucao = d.compras.some((c) => c.devolucao_pedida_em || c.status === 'Devolvido')
    || d.provas.some((p) => /devolu/i.test(`${p.tipo} ${p.nome} ${p.obs || ''}`))
  return [
    { grupo: 'para abrir o caso', nome: 'Tela do anúncio (print)', n: tem('Captura no ato', 'Print', 'PDF do anúncio') },
    { grupo: 'para abrir o caso', nome: 'Tela do pedido (compra de prova)', n: tem('Tela do pedido') },
    { grupo: 'para abrir o caso', nome: 'Comprovante na fatura do cartão', n: tem('Fatura do cartão') },
    { grupo: 'quando o produto chega', nome: 'Vídeo da embalagem sendo aberta', n: tem('Vídeo') },
    { grupo: 'quando o produto chega', nome: 'Fotos do produto (com o selo Anatel)', n: tem('Foto') },
    { grupo: 'quando o produto chega', nome: 'NF-e da compra (PDF)', n: tem('NF-e') },
    { grupo: 'quando o produto chega', nome: 'Devolução pedida na loja', n: devolucao ? 1 : 0 },
    { grupo: 'denúncias', nome: 'Comprovantes das denúncias', n: tem('Captura no ato', 'Registro da denúncia', 'Protocolo') || d.denuncias.length },
  ]
})
const faltam = computed(() => checklist.value.filter((x) => !x.n))
const abasCaso = computed(() => [
  { k: 'resumo' as AbaCaso, t: 'Resumo' },
  { k: 'juridico' as AbaCaso, t: faltam.value.length ? `Jurídico (falta ${faltam.value.length})` : 'Jurídico' },
  { k: 'compra' as AbaCaso, t: `Compra de prova (${detalhe.value?.compras.length || 0})` },
  { k: 'provas' as AbaCaso, t: `Provas (${detalhe.value?.provas.length || 0})` },
  { k: 'denuncias' as AbaCaso, t: `Denúncias (${detalhe.value?.denuncias.length || 0})` },
])
const an = computed(() => detalhe.value?.anuncio || null)

function verAnuncio(id: string | null | undefined) {
  if (!id) return
  aberto.value = null
  anuncioAberto.value = id
}

// 01/10: a coluna "Caso" da aba Anúncios e denúncias chega aqui com o caso a abrir
const props = defineProps<{ abrirCaso?: number | null }>()
const emit = defineEmits<{ (e: 'aberto'): void }>()
function abrirPedido() {
  if (!props.abrirCaso) return
  void abrir({ id: props.abrirCaso } as Caso)
  emit('aberto')
}
watch(() => props.abrirCaso, abrirPedido)
onMounted(async () => {
  await carregar()
  abrirPedido()
})
// o botão "recarregar" do topo do painel chama isto na aba aberta
defineExpose({ carregar })
</script>

<template>
  <div class="space-y-5">

    <div v-if="Object.keys(porStatus).length" class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <button
        v-for="(n, s) in porStatus"
        :key="s"
        type="button"
        class="text-left rounded-lg"
        :class="status === s ? 'ring-2 ring-primary' : ''"
        @click="status = status === s ? '' : String(s)"
      >
        <StatCard compact :label="String(s)" :value="numero(n)" />
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="flex items-center gap-3">
      <span class="text-sm text-muted-foreground">{{ selecionados.size ? `${selecionados.size} caso${selecionados.size > 1 ? 's' : ''} selecionado${selecionados.size > 1 ? 's' : ''}` : 'marque os casos para montar a lista de compra' }}</span>
      <Button size="sm" :disabled="!selecionados.size" @click="listaAberta = true">Gerar lista de compra</Button>
      <Button v-if="selecionados.size" size="sm" variant="ghost" @click="selecionados = new Set()">limpar</Button>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th class="w-10"><input type="checkbox" class="size-4 align-middle" :checked="todosMarcados" title="marcar todos" @change="marcarTodos"></th>
            <th>Caso</th>
            <th>Anúncio</th>
            <th class="text-center">Status</th>
            <th class="text-center">Aberto em</th>
            <th class="text-center">Jurídico</th>
            <th class="text-center">Compras</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && itens.length === 0">
            <td colspan="7" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="visiveis.length === 0">
            <td colspan="7" class="text-center text-muted-foreground py-6">nenhum caso</td>
          </tr>
          <tr v-for="c in visiveis" :key="c.id" class="cursor-pointer" :class="selecionados.has(c.id) ? 'bg-primary/5' : ''" @click="abrir(c)">
            <td @click.stop><input type="checkbox" class="size-4 align-middle" :checked="selecionados.has(c.id)" @change="marcar(c.id)"></td>
            <td>
              <div class="font-medium text-sm whitespace-nowrap">{{ c.codigo }}</div>
              <div class="text-[11px] text-muted-foreground max-w-[220px] truncate" :title="c.titulo || ''">{{ c.titulo }}</div>
            </td>
            <td class="text-xs max-w-[280px]">
              <div class="truncate" :title="c.titulo_anuncio || ''">{{ c.loja || '—' }} — {{ c.titulo_anuncio || c.anuncio_id }}</div>
              <div class="text-[11px] text-muted-foreground font-mono">{{ c.marketplace }} · {{ c.anuncio_id }}</div>
            </td>
            <td class="text-center"><span :class="pillStatusCaso(c.status)">{{ c.status || '—' }}</span></td>
            <td class="text-center text-xs tabular-nums whitespace-nowrap">{{ dataBr(c.aberto_em, false) }}</td>
            <td class="text-center text-xs tabular-nums whitespace-nowrap">
              <!-- 01/10 (Vinicius): enviado = só a data; sem envio = botão "enviar" (abre o que falta) -->
              <template v-if="c.juridico_enviado_em">{{ dataBr(c.juridico_enviado_em, false) }}</template>
              <Button v-else size="sm" variant="outline" class="h-7 px-2.5 text-xs" @click.stop="abrir(c, true)">enviar</Button>
            </td>
            <td class="text-center text-xs tabular-nums">{{ c.ncompras || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <DenunciaGaveta v-model:open="listaAberta" titulo="Lista de compra" :subtitulo="`${lista.length} caso${lista.length > 1 ? 's' : ''} selecionado${lista.length > 1 ? 's' : ''}`">
      <div class="flex flex-wrap items-center gap-2">
        <Button size="sm" @click="copiarLista">{{ copiado ? 'copiado ✓' : 'copiar (cola no WhatsApp ou na planilha)' }}</Button>
        <Button size="sm" variant="outline" @click="baixarLista">baixar planilha</Button>
      </div>
      <p class="text-xs text-muted-foreground">O robô não guarda o preço do anúncio: o valor aparece quando a compra já foi registrada no caso.</p>
      <div class="space-y-2">
        <div v-for="l in lista" :key="l.caso" class="rounded-lg border px-3 py-2 space-y-1">
          <div class="flex items-center gap-2 text-sm">
            <span class="font-medium">{{ l.loja }}</span>
            <span class="text-xs text-muted-foreground">{{ l.marketplace }}</span>
            <span class="flex-1" />
            <span class="text-xs text-muted-foreground">{{ l.caso }}</span>
          </div>
          <div class="text-sm truncate" :title="l.produto">{{ l.produto }}</div>
          <div class="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
            <span class="font-mono">{{ l.anuncio }}</span>
            <span>valor: {{ l.valor !== null ? dinheiro(l.valor) : '—' }}</span>
            <span>comprado: {{ l.comprado }}</span>
            <a v-if="l.url" :href="l.url" target="_blank" rel="noopener noreferrer" class="text-primary hover:underline">abrir o anúncio</a>
          </div>
        </div>
      </div>
    </DenunciaGaveta>

    <!-- 01/10 (Vinicius: "deixa padrãozinho igual fizemos na aba Anúncios e denúncias"): a ficha do caso
         com 4 quadrinhos em cima (Status · Compra de prova · Jurídico · Anúncio ativo) e abas embaixo -->
    <DenunciaGaveta
      v-model:open="gavetaAberta"
      :titulo="[k.codigo, k.titulo].filter(Boolean).join(' — ') || 'Caso'"
      :subtitulo="k.aberto_em ? `aberto em ${dataBr(k.aberto_em, false)}` : undefined"
    >
      <div v-if="!detalhe" class="text-sm text-muted-foreground">carregando…</div>
      <div v-else class="space-y-5">
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
            <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Status</div>
            <span :class="pillStatusCaso(k.status)">{{ k.status || '—' }}</span>
          </div>
          <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
            <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Compra de prova</div>
            <template v-if="detalhe.compras.length">
              <span :class="pillStatusCompra(detalhe.compras[0].status)">{{ detalhe.compras[0].status }}</span>
              <div class="text-[11px] text-muted-foreground truncate">pedido {{ detalhe.compras[0].pedido || `#${detalhe.compras[0].id}` }}</div>
            </template>
            <span v-else class="text-sm text-muted-foreground">—</span>
          </div>
          <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
            <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Jurídico</div>
            <template v-if="k.juridico_enviado_em">
              <span class="pill-success">enviado</span>
              <div class="text-[11px] text-muted-foreground tabular-nums">{{ dataBr(k.juridico_enviado_em, false) }}</div>
            </template>
            <template v-else>
              <span :class="faltam.length ? 'pill-warning' : 'pill-success'">{{ faltam.length ? `falta${faltam.length > 1 ? 'm' : ''} ${faltam.length}` : 'pronto' }}</span>
              <div class="text-[11px] text-muted-foreground">não enviado</div>
            </template>
          </div>
          <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
            <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Anúncio ativo</div>
            <span :class="pillAtivo(an?.situacao)">{{ ativoSimNao(an?.situacao) }}</span>
          </div>
        </div>

        <div class="flex items-center gap-1 border-b border-border overflow-x-auto">
          <button
            v-for="x in abasCaso"
            :key="x.k"
            type="button"
            class="-mb-px inline-flex h-8 items-center whitespace-nowrap border-b-2 px-2.5 text-xs font-medium transition-colors"
            :class="abaCaso === x.k ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="abaCaso = x.k"
          >
            {{ x.t }}
          </button>
        </div>

        <!-- Resumo -->
        <section v-if="abaCaso === 'resumo'" class="space-y-4">
          <button v-if="an" type="button" class="w-full text-left rounded-lg border px-3 py-2 text-sm hover:border-primary/50 hover:bg-muted/30" @click="verAnuncio(an.id)">
            <div class="flex items-center gap-2 min-w-0">
              <span class="truncate flex-1">{{ an.titulo }}</span>
              <span class="shrink-0" :class="pillAtivo(an.situacao)">ativo: {{ ativoSimNao(an.situacao) }}</span>
            </div>
            <div class="font-mono text-[11px] text-muted-foreground mt-0.5">{{ an.marketplace }} · {{ an.loja }} · {{ an.id }}</div>
          </button>
          <p v-if="k.resumo" class="text-sm whitespace-pre-wrap">{{ k.resumo }}</p>
          <p v-if="k.ciencia_autoria" class="text-xs text-muted-foreground">Ciência da autoria: {{ k.ciencia_autoria }}</p>
          <p v-if="k.obs" class="text-xs text-muted-foreground whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2">{{ k.obs }}</p>
        </section>

        <!-- Jurídico -->
        <section v-else-if="abaCaso === 'juridico'" class="space-y-3">
          <div class="rounded-lg border px-3 py-2.5 space-y-2">
            <div class="flex items-center gap-2 text-sm">
              <span class="font-medium">Pronto para enviar ao advogado?</span>
              <span v-if="!faltam.length" class="pill-success">sim, está tudo aqui</span>
              <span v-else class="pill-warning">falta{{ faltam.length > 1 ? 'm' : '' }} {{ faltam.length }}</span>
            </div>
            <template v-for="g in ['para abrir o caso', 'quando o produto chega', 'denúncias']" :key="g">
              <div class="text-[10px] uppercase tracking-wider text-muted-foreground pt-1">{{ g }}</div>
              <ul class="grid gap-x-4 gap-y-1 sm:grid-cols-2 text-xs">
                <li v-for="x in checklist.filter((c) => c.grupo === g)" :key="x.nome" class="flex items-center gap-1.5">
                  <span :class="x.n ? 'text-emerald-600' : 'text-amber-600'">{{ x.n ? '✓' : '○' }}</span>
                  <span :class="x.n ? '' : 'text-muted-foreground'">{{ x.nome }}</span>
                  <span v-if="x.n > 1" class="text-muted-foreground">({{ x.n }})</span>
                </li>
              </ul>
            </template>
          </div>
          <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <div><dt class="text-[10px] uppercase tracking-wider text-muted-foreground">Advogado</dt><dd>{{ k.juridico || '—' }}</dd></div>
            <div><dt class="text-[10px] uppercase tracking-wider text-muted-foreground">Enviado em</dt><dd>{{ dataBr(k.juridico_enviado_em) }}</dd></div>
            <div v-if="k.juridico_email" class="col-span-2"><dt class="text-[10px] uppercase tracking-wider text-muted-foreground">E-mail</dt><dd class="truncate">{{ k.juridico_email }}</dd></div>
          </dl>
        </section>

        <!-- Compra de prova -->
        <section v-else-if="abaCaso === 'compra'" class="space-y-2">
          <div v-if="detalhe.compras.length === 0" class="text-sm text-muted-foreground">Nenhuma compra registrada.</div>
          <div v-for="c in detalhe.compras" :key="c.id" class="rounded-lg border px-3 py-2 space-y-2">
            <div class="flex flex-wrap items-center gap-2 text-sm">
              <span class="font-medium">Pedido {{ c.pedido || `#${c.id}` }}</span>
              <span class="text-xs text-muted-foreground">comprado {{ dataBr(c.data, false) }}<template v-if="c.entregue_em"> · entregue {{ dataBr(c.entregue_em, false) }}</template></span>
              <span class="flex-1" />
              <span :class="pillStatusCompra(c.status)">{{ c.status }}</span>
            </div>
            <dl class="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs">
              <div><dt class="text-muted-foreground">Valor pago</dt><dd>{{ dinheiro(c.valor_pago) }}</dd></div>
              <div v-if="c.valor_frete !== null && c.valor_frete !== undefined"><dt class="text-muted-foreground">Frete</dt><dd>{{ dinheiro(c.valor_frete) }}</dd></div>
              <div v-if="c.comprador"><dt class="text-muted-foreground">Comprador</dt><dd class="truncate">{{ c.comprador }}</dd></div>
              <div v-if="c.nfe_numero"><dt class="text-muted-foreground">NF-e</dt><dd>{{ c.nfe_numero }}<span v-if="c.nfe_serie">/{{ c.nfe_serie }}</span> · {{ dinheiro(c.nfe_valor) }}</dd></div>
              <div v-if="c.nfe_emitente"><dt class="text-muted-foreground">Emitente</dt><dd class="truncate" :title="c.nfe_emitente">{{ c.nfe_emitente }}</dd></div>
              <div v-if="c.selo_anatel"><dt class="text-muted-foreground">Selo Anatel</dt><dd>{{ c.selo_anatel }}</dd></div>
              <div v-if="c.devolucao_pedida_em"><dt class="text-muted-foreground">Devolução pedida</dt><dd>{{ dataBr(c.devolucao_pedida_em, false) }}</dd></div>
            </dl>
            <p v-if="c.nfe_chave" class="font-mono text-[10px] text-muted-foreground break-all">{{ c.nfe_chave }}</p>
          </div>
        </section>

        <!-- Provas -->
        <section v-else-if="abaCaso === 'provas'">
          <div v-if="!detalhe.provas.length" class="text-sm text-muted-foreground">Nenhuma prova guardada.</div>
          <DenunciaProvas v-else :provas="detalhe.provas" />
        </section>

        <!-- Denúncias do anúncio -->
        <section v-else class="space-y-1.5">
          <div v-if="!detalhe.denuncias.length" class="text-sm text-muted-foreground">Nenhuma denúncia.</div>
          <div v-for="d in detalhe.denuncias" :key="d.id" class="rounded-lg border px-3 py-2 flex items-center gap-2 text-xs">
            <span class="tabular-nums text-muted-foreground whitespace-nowrap">{{ dataBr(d.data, false) }}</span>
            <span class="font-medium whitespace-nowrap">{{ d.canal }}</span>
            <span class="font-mono truncate">{{ d.protocolo || '' }}</span>
            <span class="flex-1" />
            <span v-if="d.resultado && d.resultado !== 'Aguardando'" :class="pillResultado(d.resultado)">{{ d.resultado }}</span>
            <span v-else :class="pillSituacaoDenuncia(d.situacao)">{{ d.situacao }}</span>
          </div>
        </section>
      </div>
    </DenunciaGaveta>

    <DenunciaAnuncioGaveta :anuncio-id="anuncioAberto" @fechar="anuncioAberto = null" />
  </div>
</template>
