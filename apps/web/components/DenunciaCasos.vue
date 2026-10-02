<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Casos (30/09/2026) ──────────────────────────────────────────
// Casos jurídicos (CASO-00N): o anúncio, a compra de prova (pedido, NF-e,
// entrega), as provas obrigatórias e o envio ao advogado. Cópia só leitura
// do sistema de Fiscalização do Mac mini da Makisa.
import { computed, onMounted, ref, watch } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
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
  status_mini: string | null
  url: string | null
  hom: string | null
  vendas: number | null
  shop_id: string | null
  compra: {
    pedido: string | null; status: string | null; valor_pago: number | null; data: string | null
    entregue_em: string | null; comprador: string | null
  } | null
  extra: CasoExtra
}
// 01/10 (Vinicius): o que a gente acompanha do caso no DaVinci (o robô não tem): onde comprou,
// pedido e previsão; o processo (nº, link do Jusbrasil) e a última movimentação
type CasoExtra = {
  compra_data: string | null
  compra_loja: string | null
  compra_pedido: string | null
  compra_previsao: string | null
  processo_numero: string | null
  processo_link: string | null
  mov_data: string | null
  mov_texto: string | null
  mov_status: string | null
  atualizado_por: string | null
}
type Resposta = { total: number; itens: Caso[]; por_status: Record<string, number> }
type Anexo = {
  id: number
  tipo: string
  tipo_nome: string
  nome: string | null
  link: string | null
  obs: string | null
  enviado_por: string | null
  enviado_em: string | null
  entregue_em: string | null
  ok: boolean | null
  resultado: string | null
}
type Detalhe = {
  extra?: CasoExtra
  status_tela?: string
  anexos?: Anexo[]
  tipos_anexo?: { chave: string; nome: string }[]
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
      comprado: c.compra || c.extra?.compra_pedido
        ? `${c.compra?.status || 'comprado'}${(c.extra?.compra_pedido || c.compra?.pedido) ? ` · pedido ${c.extra?.compra_pedido || c.compra?.pedido}` : ''}`
        : 'não',
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
// ── 01/10 (Vinicius: "chegou o produto, onde eu vou colocar as provas?"): anexa aqui; o robô do mini
// busca (a cada 30 s), guarda no sistema de lá e no MEGA, e a prova volta na cópia em até 5 min.
const podeAnexar = useCan('denuncia', 'edit')
const anexoTipo = ref('foto')
const anexoLink = ref('')
const anexoObs = ref('')
const anexoArquivo = ref<File | null>(null)
const anexando = ref(false)
const anexoErro = ref<string | null>(null)
const ERROS_ANEXO: Record<string, string> = {
  denuncia_anexo_video_so_link_mega: 'Vídeo só como link do MEGA, com a chave (…#…).',
  denuncia_anexo_sem_arquivo: 'Escolha o arquivo.',
  denuncia_anexo_grande_demais: 'Arquivo grande demais (até 30 MB). Vídeo vai como link do MEGA.',
}
const podeEnviarAnexo = computed(() =>
  anexoTipo.value === 'video' ? /^https:\/\/mega\.nz\/\S+#\S+$/.test(anexoLink.value.trim()) : !!anexoArquivo.value,
)
async function anexar() {
  if (!aberto.value || !podeEnviarAnexo.value) return
  anexando.value = true
  anexoErro.value = null
  try {
    const fd = new FormData()
    fd.append('tipo', anexoTipo.value)
    fd.append('obs', anexoObs.value)
    if (anexoTipo.value === 'video') fd.append('link', anexoLink.value.trim())
    else if (anexoArquivo.value) fd.append('arquivo', anexoArquivo.value)
    const r = await api<{ anexos: Anexo[] }>(`/api/denuncia/casos/${aberto.value}/anexos`, { method: 'POST', body: fd })
    if (detalhe.value) detalhe.value.anexos = r.anexos
    anexoLink.value = ''
    anexoObs.value = ''
    anexoArquivo.value = null
    const campo = document.getElementById('anexo-arquivo') as HTMLInputElement | null
    if (campo) campo.value = ''
  } catch (e: any) {
    const code = e?.data?.detail?.code
    anexoErro.value = ERROS_ANEXO[code] || code || e?.message || 'erro'
  } finally {
    anexando.value = false
  }
}
// enviado_em vem em UTC (é do DaVinci, não do mini) → hora de Brasília
function quando(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString('pt-BR', {
    timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}
function situacaoAnexo(x: Anexo): { texto: string; cls: string } {
  if (!x.entregue_em) return { texto: 'indo para o robô', cls: 'pill-muted' }
  if (x.ok) return { texto: 'guardado no sistema', cls: 'pill-success' }
  return { texto: 'erro', cls: 'pill-danger' }
}

const VAZIO_EXTRA: CasoExtra = {
  compra_data: null, compra_loja: null, compra_pedido: null, compra_previsao: null, processo_numero: null, processo_link: null,
  mov_data: null, mov_texto: null, mov_status: null, atualizado_por: null,
}
const extraForm = ref<Record<keyof CasoExtra, string>>(Object.fromEntries(Object.keys(VAZIO_EXTRA).map((k) => [k, ''])) as any)
watch(detalhe, (d) => {
  const e = d?.extra || VAZIO_EXTRA
  extraForm.value = Object.fromEntries(Object.keys(VAZIO_EXTRA).map((k) => [k, (e as any)[k] || ''])) as any
  extraSalvo.value = false
  extraErro.value = null
})
const salvandoExtra = ref(false)
const extraSalvo = ref(false)
const extraErro = ref<string | null>(null)
const ERROS_EXTRA: Record<string, string> = {
  denuncia_link_invalido: 'O link precisa começar com https://',
  denuncia_data_invalida: 'Data inválida.',
}
async function salvarExtra() {
  if (!aberto.value) return
  salvandoExtra.value = true
  extraErro.value = null
  try {
    const { atualizado_por: _x, ...corpo } = extraForm.value
    const r = await api<{ extra: CasoExtra }>(`/api/denuncia/casos/${aberto.value}/extra`, { method: 'PUT', body: corpo })
    if (detalhe.value) detalhe.value.extra = r.extra
    const item = itens.value.find((c) => c.id === aberto.value)
    if (item) item.extra = r.extra
    extraSalvo.value = true
  } catch (e: any) {
    const code = e?.data?.detail?.code
    extraErro.value = ERROS_EXTRA[code] || code || e?.message || 'erro'
  } finally {
    salvandoExtra.value = false
  }
}

// células da tabela (mesmo jeito do painel Devoluções): salva ao sair do campo, só se mudou
const celula = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-center text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
// 02/10 (Vinicius: "coloca balãozinho igual fez nos outros painéis"): os campos de texto abrem um
// balão; o que se digita fica no rascunho da célula até o balão fechar (aí salva, se mudou)
type CampoTexto = 'compra_loja' | 'compra_pedido' | 'processo_numero' | 'mov_status' | 'processo_link'
const rascunhos = ref<Record<string, string>>({})
function valorCampo(c: Caso, campo: CampoTexto): string {
  const r = rascunhos.value[`${c.id}:${campo}`]
  if (r !== undefined) return r
  if (campo === 'compra_pedido') return c.extra.compra_pedido || c.compra?.pedido || ''
  return c.extra[campo] || ''
}
function rascunhar(c: Caso, campo: CampoTexto, v: string) {
  rascunhos.value = { ...rascunhos.value, [`${c.id}:${campo}`]: v }
}
async function salvarRascunho(c: Caso, campo: CampoTexto) {
  const chave = `${c.id}:${campo}`
  const v = rascunhos.value[chave]
  if (v === undefined) return
  await salvarCampo(c, campo, campo === 'mov_status' ? v : v.replace(/\s*\n\s*/g, ' '))
  const { [chave]: _x, ...resto } = rascunhos.value
  rascunhos.value = resto
}
async function salvarCampo(c: Caso, campo: keyof CasoExtra, valor: string) {
  const v = (valor || '').trim()
  const atual = (c.extra[campo] || '') as string
  if (v === atual) return
  try {
    const r = await api<{ extra: CasoExtra }>(`/api/denuncia/casos/${c.id}/extra`, { method: 'PUT', body: { [campo]: v } })
    c.extra = r.extra
    if (detalhe.value && aberto.value === c.id) detalhe.value.extra = r.extra
  } catch (e: any) {
    const code = e?.data?.detail?.code
    erro.value = `${c.codigo}: ${ERROS_EXTRA[code] || code || e?.message || 'não salvou'}`
  }
}

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

    <!-- resumo no topo: o total e cada status (clicar filtra a tabela) -->
    <div v-if="Object.keys(porStatus).length" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
      <button type="button" class="text-left rounded-lg" :class="status === '' ? 'ring-2 ring-primary' : ''" @click="status = ''">
        <StatCard compact label="Todos os casos" :value="numero(itens.length)" />
      </button>
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

    <div class="flex items-center justify-between gap-2">
      <div class="text-xs text-muted-foreground">compra e jurídico: clique no campo para escrever — salva ao sair</div>
      <div class="flex items-center gap-2">
        <Button v-if="selecionados.size" size="sm" variant="ghost" class="h-8 text-xs" @click="selecionados = new Set()">limpar</Button>
        <Button size="sm" variant="outline" class="h-8 text-xs" :disabled="!selecionados.size" title="marque os casos na tabela" @click="listaAberta = true">
          lista de compra{{ selecionados.size ? ` (${selecionados.size})` : '' }}
        </Button>
      </div>
    </div>

    <!-- 01/10 (Vinicius: "assim como no painel Devoluções"): grupos Compra e Jurídico com as colunas
         separadas; os campos que o robô não tem a pessoa preenche aqui mesmo. 02/10 ("ficou tudo
         apertado"): largura fixa por coluna (rola para o lado), Compra e Jurídico centralizados e
         os campos de texto no balão (ObservacaoPopover) — a linha não muda de altura. -->
    <div class="table-card overflow-x-auto">
      <table class="w-full min-w-[1906px] table-fixed text-xs">
        <!-- larguras fixas; só a coluna Caso (o produto) estica quando a tela é maior -->
        <colgroup>
          <col class="w-[40px]">
          <col class="w-[88px]">
          <col>
          <col class="w-[170px]">
          <col class="w-[156px]">
          <col class="w-[132px]">
          <col class="w-[140px]">
          <col class="w-[150px]">
          <col class="w-[132px]">
          <col class="w-[96px]">
          <col class="w-[200px]">
          <col class="w-[132px]">
          <col class="w-[160px]">
          <col class="w-[130px]">
        </colgroup>
        <thead>
          <tr>
            <th class="!py-1.5 text-[11px] font-semibold" colspan="5">Caso</th>
            <th class="!py-1.5 !text-center text-[11px] font-semibold border-l-[3px] border-l-gray-400 dark:border-l-gray-600 !bg-amber-50 dark:!bg-amber-900/20" colspan="4">Compra</th>
            <th class="!py-1.5 !text-center text-[11px] font-semibold border-l-[3px] border-l-gray-400 dark:border-l-gray-600 !bg-emerald-50 dark:!bg-emerald-900/20" colspan="5">Jurídico</th>
          </tr>
          <tr class="[&>th]:whitespace-nowrap">
            <th class="!px-0 !text-center"><input type="checkbox" class="size-4 align-middle" :checked="todosMarcados" title="marcar todos" @change="marcarTodos"></th>
            <th>Aberto em</th>
            <th>Caso</th>
            <th>Loja</th>
            <th class="!text-center">Status</th>
            <th class="!text-center !bg-amber-50 dark:!bg-amber-900/20 border-l-[3px] border-l-gray-400 dark:border-l-gray-600">Data</th>
            <th class="!text-center !bg-amber-50 dark:!bg-amber-900/20">Loja</th>
            <th class="!text-center !bg-amber-50 dark:!bg-amber-900/20">Pedido</th>
            <th class="!text-center !bg-amber-50 dark:!bg-amber-900/20">Previsão entrega</th>
            <th class="!text-center !bg-emerald-50 dark:!bg-emerald-900/20 border-l-[3px] border-l-gray-400 dark:border-l-gray-600" title="Quando o caso foi enviado ao advogado">Data</th>
            <th class="!text-center !bg-emerald-50 dark:!bg-emerald-900/20" title="Nº do processo">Protocolo</th>
            <th class="!text-center !bg-emerald-50 dark:!bg-emerald-900/20">Últ. movimentação</th>
            <th class="!text-center !bg-emerald-50 dark:!bg-emerald-900/20">Último status</th>
            <th class="!text-center !bg-emerald-50 dark:!bg-emerald-900/20" title="Link de consulta do processo no Jusbrasil">Jusbrasil</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && itens.length === 0">
            <td colspan="14" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="visiveis.length === 0">
            <td colspan="14" class="text-center text-muted-foreground py-6">nenhum caso</td>
          </tr>
          <tr v-for="c in visiveis" :key="c.id" class="cursor-pointer [&>td]:align-middle" :class="selecionados.has(c.id) ? 'bg-primary/5' : ''" @click="abrir(c)">
            <td class="!px-0 text-center" @click.stop><input type="checkbox" class="size-4 align-middle" :checked="selecionados.has(c.id)" @change="marcar(c.id)"></td>
            <td class="tabular-nums whitespace-nowrap">{{ dataBr(c.aberto_em, false) }}</td>
            <td>
              <div class="font-medium text-sm whitespace-nowrap">{{ c.codigo }}</div>
              <div class="text-[11px] text-muted-foreground truncate" :title="c.titulo_anuncio || ''">{{ c.titulo_anuncio || c.anuncio_id }}</div>
            </td>
            <td>
              <div class="truncate text-sm" :title="c.loja || ''">{{ c.loja || '—' }}</div>
              <div class="text-[11px] text-muted-foreground truncate">{{ c.marketplace || '—' }}<span v-if="c.shop_id" class="font-mono"> · {{ c.shop_id }}</span></div>
            </td>
            <td class="!px-2 text-center">
              <span class="whitespace-nowrap" :class="pillStatusCaso(c.status)" :title="c.status_mini && c.status_mini !== c.status ? `no sistema do mini: ${c.status_mini}` : ''">{{ c.status || '—' }}</span>
            </td>
            <!-- Compra -->
            <td class="!px-2 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-l-gray-400 dark:border-l-gray-600" @click.stop>
              <input type="date" :value="c.extra.compra_data || c.compra?.data || ''" :disabled="!podeAnexar" :class="celula" @change="(e) => salvarCampo(c, 'compra_data', (e.target as HTMLInputElement).value)">
            </td>
            <td class="!px-2 bg-amber-50/40 dark:bg-amber-900/10" @click.stop>
              <ObservacaoPopover
                :model-value="valorCampo(c, 'compra_loja')"
                :disabled="!podeAnexar"
                :titulo="`Onde comprou · ${c.codigo}`"
                placeholder="onde comprou"
                centro
                @update:model-value="(v) => rascunhar(c, 'compra_loja', v)"
                @save="salvarRascunho(c, 'compra_loja')"
              />
            </td>
            <td class="!px-2 bg-amber-50/40 dark:bg-amber-900/10" @click.stop>
              <ObservacaoPopover
                :model-value="valorCampo(c, 'compra_pedido')"
                :disabled="!podeAnexar"
                :titulo="`Nº do pedido · ${c.codigo}`"
                placeholder="nº do pedido"
                centro
                mono
                @update:model-value="(v) => rascunhar(c, 'compra_pedido', v)"
                @save="salvarRascunho(c, 'compra_pedido')"
              />
            </td>
            <td class="!px-2 bg-amber-50/40 dark:bg-amber-900/10 text-center" @click.stop>
              <span v-if="c.compra?.entregue_em" class="text-emerald-700 dark:text-emerald-400 whitespace-nowrap">entregue {{ dataBr(c.compra.entregue_em, false) }}</span>
              <input v-else type="date" :value="c.extra.compra_previsao || ''" :disabled="!podeAnexar" :class="celula" @change="(e) => salvarCampo(c, 'compra_previsao', (e.target as HTMLInputElement).value)">
            </td>
            <!-- Jurídico -->
            <td class="!px-2 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-l-gray-400 dark:border-l-gray-600 text-center whitespace-nowrap tabular-nums" @click.stop>
              <span v-if="c.juridico_enviado_em">{{ dataBr(c.juridico_enviado_em, false) }}</span>
              <Button v-else size="sm" variant="outline" class="h-7 px-3 text-xs" @click="abrir(c, true)">enviar</Button>
            </td>
            <td class="!px-2 bg-emerald-50/40 dark:bg-emerald-900/10" @click.stop>
              <ObservacaoPopover
                :model-value="valorCampo(c, 'processo_numero')"
                :disabled="!podeAnexar"
                :titulo="`Nº do processo · ${c.codigo}`"
                placeholder="nº do processo"
                centro
                mono
                @update:model-value="(v) => rascunhar(c, 'processo_numero', v)"
                @save="salvarRascunho(c, 'processo_numero')"
              />
            </td>
            <td class="!px-2 bg-emerald-50/40 dark:bg-emerald-900/10" @click.stop>
              <input type="date" :value="c.extra.mov_data || ''" :disabled="!podeAnexar" :class="celula" @change="(e) => salvarCampo(c, 'mov_data', (e.target as HTMLInputElement).value)">
            </td>
            <td class="!px-2 bg-emerald-50/40 dark:bg-emerald-900/10" @click.stop>
              <ObservacaoPopover
                :model-value="valorCampo(c, 'mov_status')"
                :disabled="!podeAnexar"
                :titulo="`Último status · ${c.codigo}`"
                :dica="c.extra.mov_texto || ''"
                placeholder="ex.: em andamento"
                centro
                @update:model-value="(v) => rascunhar(c, 'mov_status', v)"
                @save="salvarRascunho(c, 'mov_status')"
              />
            </td>
            <td class="!px-2 bg-emerald-50/40 dark:bg-emerald-900/10" @click.stop>
              <div class="flex items-center gap-1 min-w-0">
                <div class="min-w-0 flex-1">
                  <ObservacaoPopover
                    :model-value="valorCampo(c, 'processo_link')"
                    :disabled="!podeAnexar"
                    :titulo="`Link do Jusbrasil · ${c.codigo}`"
                    placeholder="colar o link"
                    centro
                    @update:model-value="(v) => rascunhar(c, 'processo_link', v)"
                    @save="salvarRascunho(c, 'processo_link')"
                  />
                </div>
                <a v-if="c.extra.processo_link" :href="c.extra.processo_link" target="_blank" rel="noopener noreferrer" class="shrink-0 text-primary" title="abrir no Jusbrasil">
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
            </td>
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
            <span :class="pillStatusCaso(detalhe.status_tela || k.status)">{{ detalhe.status_tela || k.status || '—' }}</span>
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
              <span class="pill-success">enviado {{ dataBr(k.juridico_enviado_em, false) }}</span>
              <div v-if="detalhe.extra?.processo_numero" class="font-mono text-[11px] text-muted-foreground truncate" :title="detalhe.extra.processo_numero">{{ detalhe.extra.processo_numero }}</div>
              <div v-else class="text-[11px] text-muted-foreground">sem nº de processo</div>
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
          <!-- 01/10 (Vinicius): "nº do processo, última movimentação, status, link de consulta — vamos logar no Jusbrasil" -->
          <div class="rounded-lg border px-3 py-2.5 space-y-2">
            <div class="flex items-center gap-2 text-sm">
              <span class="font-medium">Processo</span>
              <span v-if="k.juridico_enviado_em" class="text-xs text-muted-foreground">enviado ao advogado em {{ dataBr(k.juridico_enviado_em, false) }}</span>
              <span class="flex-1" />
              <a v-if="extraForm.processo_link" :href="extraForm.processo_link" target="_blank" rel="noopener noreferrer" class="text-xs text-primary hover:underline">consultar no Jusbrasil</a>
            </div>
            <div class="grid gap-2 sm:grid-cols-2">
              <label class="text-[11px] text-muted-foreground space-y-0.5">Nº do processo
                <Input v-model="extraForm.processo_numero" :disabled="!podeAnexar" placeholder="0000000-00.0000.0.00.0000" class="font-mono" />
              </label>
              <label class="text-[11px] text-muted-foreground space-y-0.5">Link de consulta (Jusbrasil)
                <Input v-model="extraForm.processo_link" :disabled="!podeAnexar" placeholder="https://www.jusbrasil.com.br/…" />
              </label>
              <label class="text-[11px] text-muted-foreground space-y-0.5">Última movimentação (data)
                <Input v-model="extraForm.mov_data" :disabled="!podeAnexar" type="date" />
              </label>
              <label class="text-[11px] text-muted-foreground space-y-0.5">Status da última movimentação
                <Input v-model="extraForm.mov_status" :disabled="!podeAnexar" placeholder="ex.: em andamento, audiência marcada" />
              </label>
              <label class="text-[11px] text-muted-foreground space-y-0.5 sm:col-span-2">O que aconteceu
                <Input v-model="extraForm.mov_texto" :disabled="!podeAnexar" placeholder="ex.: petição inicial distribuída" />
              </label>
            </div>
            <div v-if="podeAnexar" class="flex items-center gap-2">
              <Button size="sm" :disabled="salvandoExtra" @click="salvarExtra">{{ salvandoExtra ? 'salvando…' : 'salvar' }}</Button>
              <span v-if="extraSalvo" class="text-xs text-emerald-600">salvo ✓</span>
              <span v-if="extraErro" class="text-xs text-red-600">{{ extraErro }}</span>
            </div>
          </div>
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
          <div v-if="podeAnexar" class="rounded-lg border px-3 py-2.5 space-y-2">
            <div class="text-sm font-medium">Anexar prova</div>
            <div class="flex flex-wrap items-center gap-2">
              <select v-model="anexoTipo" class="h-9 rounded-md border bg-background px-2 text-sm">
                <option v-for="tp in detalhe.tipos_anexo || []" :key="tp.chave" :value="tp.chave">{{ tp.nome }}</option>
              </select>
              <Input v-if="anexoTipo === 'video'" v-model="anexoLink" placeholder="link do MEGA (com a chave #…)" class="w-72" />
              <input
                v-else
                id="anexo-arquivo"
                type="file"
                class="text-xs file:mr-2 file:rounded-md file:border file:bg-background file:px-2 file:py-1 file:text-xs"
                @change="(e) => (anexoArquivo = (e.target as HTMLInputElement).files?.[0] || null)"
              >
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <Input v-model="anexoObs" placeholder="observação (opcional)" class="flex-1 min-w-[200px]" />
              <Button size="sm" :disabled="anexando || !podeEnviarAnexo" @click="anexar">{{ anexando ? 'enviando…' : 'anexar' }}</Button>
            </div>
            <p v-if="anexoErro" class="text-xs text-red-600">{{ anexoErro }}</p>
            <p class="text-[11px] text-muted-foreground">O robô do mini guarda no sistema e no MEGA; a prova aparece em "Provas" em até 5 min. Vídeo: só o link do MEGA.</p>
            <ul v-if="detalhe.anexos?.length" class="space-y-1 pt-1">
              <li v-for="x in detalhe.anexos" :key="x.id" class="flex items-center gap-2 text-xs min-w-0">
                <span class="font-medium whitespace-nowrap">{{ x.tipo_nome }}</span>
                <span class="truncate text-muted-foreground" :title="x.nome || x.link || ''">{{ x.nome || x.link }}</span>
                <span class="text-muted-foreground whitespace-nowrap tabular-nums">{{ quando(x.enviado_em) }}</span>
                <span class="flex-1" />
                <span :class="situacaoAnexo(x).cls" :title="x.resultado || ''">{{ situacaoAnexo(x).texto }}</span>
              </li>
            </ul>
          </div>
          <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <div><dt class="text-[10px] uppercase tracking-wider text-muted-foreground">Advogado</dt><dd>{{ k.juridico || '—' }}</dd></div>
            <div><dt class="text-[10px] uppercase tracking-wider text-muted-foreground">Enviado em</dt><dd>{{ dataBr(k.juridico_enviado_em) }}</dd></div>
            <div v-if="k.juridico_email" class="col-span-2"><dt class="text-[10px] uppercase tracking-wider text-muted-foreground">E-mail</dt><dd class="truncate">{{ k.juridico_email }}</dd></div>
          </dl>
        </section>

        <!-- Compra de prova -->
        <section v-else-if="abaCaso === 'compra'" class="space-y-2">
          <!-- 01/10 (Vinicius): "por onde compramos, qual loja, número do pedido, previsão de entrega" -->
          <div class="rounded-lg border px-3 py-2.5 space-y-2">
            <div class="text-sm font-medium">Compra de prova</div>
            <div class="grid gap-2 sm:grid-cols-3">
              <label class="text-[11px] text-muted-foreground space-y-0.5">Comprado em (loja)
                <Input v-model="extraForm.compra_loja" :disabled="!podeAnexar" :placeholder="an?.loja ? `${an.loja} (${an.marketplace})` : 'loja / marketplace'" />
              </label>
              <label class="text-[11px] text-muted-foreground space-y-0.5">Nº do pedido
                <Input v-model="extraForm.compra_pedido" :disabled="!podeAnexar" :placeholder="detalhe.compras.at(-1)?.pedido || ''" class="font-mono" />
              </label>
              <label class="text-[11px] text-muted-foreground space-y-0.5">Previsão de entrega
                <Input v-model="extraForm.compra_previsao" :disabled="!podeAnexar" type="date" />
              </label>
            </div>
            <div v-if="podeAnexar" class="flex items-center gap-2">
              <Button size="sm" :disabled="salvandoExtra" @click="salvarExtra">{{ salvandoExtra ? 'salvando…' : 'salvar' }}</Button>
              <span v-if="extraSalvo" class="text-xs text-emerald-600">salvo ✓</span>
              <span v-if="extraErro" class="text-xs text-red-600">{{ extraErro }}</span>
            </div>
          </div>
          <div v-if="detalhe.compras.length === 0" class="text-sm text-muted-foreground">O robô ainda não registrou a compra deste caso.</div>
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
