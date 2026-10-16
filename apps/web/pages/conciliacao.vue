<script setup lang="ts">
// Conciliação — pedido × financeiro das lojas (passo 2b, 14/10/2026).
// Desenho e decisões em docs/conciliacao-avaliacao.md; maquetes em docs/img/.
// Esta é a aba Pedidos: uma linha por pedido do Bling com as três versões do
// dinheiro (venda, fatura, extrato — o Bling saiu em 15/10: "nunca bate"), o
// resultado e a situação; o Raio-X
// do pedido selecionado fica à direita. Extrato do dia e Resumo vêm depois.
import { Download, KeyRound, Loader2, RefreshCw, Search, Upload, Wand2 } from 'lucide-vue-next'
import { ETAPAS, SITUACOES, etapaCls, fmtDia, fmtDiaHora, fmtMoney, fmtPct, sinal, sitCls, tipoMov } from '~/lib/conciliacao'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'conciliacao', action: 'view' },
})

type Loja = {
  conta: string
  integration_id: string
  platform: string
  inicio: string
  ultimo_extrato_em: string | null
  ultimo_fatura_em: string | null
  ultimo_recalculo_em: string | null
  ultimo_erro: string | null

  modo_extrato?: string
}
type Pedido = {
  id: string
  pedido_bling: string
  numeroloja: string | null
  pack_id: string | null
  conta: string | null
  data_pedido: string | null
  situacao_bling: string | null
  bling_total: number | null
  venda_valor: number | null
  venda_tarifa: number | null
  venda_frete: number | null
  venda_cupom: number | null
  venda_afiliados: number | null
  venda_frete_pago: number | null
  venda_estorno: number | null
  venda_liquido: number | null
  venda_extras: number | null
  venda_status: string | null
  previsao_liberacao: string | null
  fatura_tarifa: number | null
  fatura_parcelamento: number | null
  fatura_frete: number | null
  fatura_bonificacao: number | null
  liberado_em: string | null
  liberado_valor: number | null
  saidas_depois: number | null
  recebido: number | null
  custo: number | null
  reembolso: number | null
  sobrou: number | null
  margem: number | null
  dif_bling_venda: number | null
  dif_previsto_recebido: number | null
  situacao: string
  situacao_label: string
  motivo: string | null
  etapa: string
  etapa_label: string
  etapa_em: string | null
  etapa_por: string | null
  etapa_motivo: string | null
  conferido: boolean
  conferido_por: string | null
  conferido_em: string | null
  observacao: string | null
}
type Resumo = {
  pedidos: number
  vendido: number
  tarifas_frete: number
  previsto: number
  recebido: number
  diferenca: number
  custo: number
  reembolso: number
  sobrou: number
  margem: number | null
  liberados: number
  por_situacao: Record<string, number>
  por_etapa: Record<string, number>
}
type Movimento = {
  id: string
  origem: string
  data: string | null
  tipo: string
  descricao: string | null
  sub_tipo: string | null
  credito: number | null
  debito: number | null
  bruto: number | null
  tarifa: number | null
  parcelamento: number | null
  frete: number | null
  descontado_da_venda: boolean | null
  original: Record<string, unknown> | null
  pagamento_id: string | null
}
type Versao = Record<string, number | string | null | undefined>
type LinhaComparativo = { chave: string; rotulo: string; previsto: number | null; real: number | null; dif: number | null; estilo: string }
type Comparativo = { linhas: LinhaComparativo[]; real_rotulo: string; rodape: string | null; comparar: boolean; nota: string | null; frete_liquido_loja: number | null }
type FretePago = { fonte: string; servico: string | null; status: string | null; rastreio: string | null; preco: number | null; desconto: number | null; conferencia: number | null; frete_pago: number | null; pago_em: string | null; postado_em: string | null; cancelado_em: string | null }
type RaioX = { pedido: Pedido; versoes: Record<'bling' | 'venda' | 'fatura' | 'extrato', Versao>; comparativo: Comparativo; movimentos: Movimento[]; frete_pago?: FretePago[] }

const { api } = useApi()
const canEdit = useCan('conciliacao', 'edit')
const toasts = useToasts()

const MARKETPLACES = [
  { key: 'ml', label: 'Mercado Livre', ativo: true },
  { key: 'shopee', label: 'Shopee', ativo: true },
  { key: 'amazon', label: 'Amazon', ativo: true },
  { key: 'tiktok', label: 'TikTok', ativo: true },
]
type Visao = 'resumo' | 'pedidos' | 'extrato' | 'fechamento'
const VISOES: Array<{ key: Visao; label: string; ativo: boolean }> = [
  { key: 'resumo', label: 'Resumo', ativo: true },
  { key: 'pedidos', label: 'Pedidos', ativo: true },
  { key: 'extrato', label: 'Extrato do dia', ativo: true },
  // 15/10: Fechamento do mês escondido por enquanto (pedido do Vinicius); o componente fica.
]
const tab = ref('ml')
// A tela de entrada é o Resumo (passo 2d): de lá se vai pro extrato do dia de uma
// loja ou pros pedidos dela já filtrados.
const visao = ref<Visao>('resumo')
const extratoDia = ref('')
const lojas = ref<Loja[]>([])
const loja = ref('')
const de = ref('')
const ate = ref('')
const dataFiltro = ref<'pedido' | 'liberacao'>('pedido')
const situacao = ref('')
const etapa = ref('')
const q = ref('')
const page = ref(1)
const SIZE = 100
const itens = ref<Pedido[]>([])
const total = ref(0)
const resumo = ref<Resumo | null>(null)
const loading = ref(false)
const erro = ref('')
const selecionado = ref<Pedido | null>(null)
const raiox = ref<RaioX | null>(null)
const raioxLoading = ref(false)
const sincronizando = ref(false)
const exportando = ref(false)
const salvando = ref(false)
const obsDraft = ref('')
const originalAberto = ref<string | null>(null) // movimento com a linha original do relatório aberta
// Reservas por reclamação vêm em pares (reserva −X, devolução +X) e somam zero:
// ficam escondidas por padrão no Raio-X, com um clique pra ver (15/10).
const mostrarReservas = ref(false)
const movimentosVisiveis = computed(() => {
  const todos = raiox.value?.movimentos || []
  return mostrarReservas.value ? todos : todos.filter((m) => !m.tipo.startsWith('reserva_'))
})
const reservasEscondidas = computed(() => (raiox.value?.movimentos || []).filter((m) => m.tipo.startsWith('reserva_')).length)
const pagamentosDoPedido = computed(() => new Set((raiox.value?.movimentos || []).map((m) => m.pagamento_id).filter(Boolean)).size)
const motivoDraft = ref('')

// Token do Melhor Envio (15/10): a operação cola aqui; fica cifrado no servidor e nunca volta pra tela.
type TokenMe = { tem_token: boolean; origem: 'painel' | 'servidor' | null; atualizado_em: string | null; atualizado_por: string | null; conta: string | null; testado_em: string | null; aviso: string | null }
const tokenMe = ref<{ aberto: boolean; status: TokenMe | null; draft: string; msg: string; erro: boolean; ocupado: boolean }>({ aberto: false, status: null, draft: '', msg: '', erro: false, ocupado: false })
async function abrirTokenMe() {
  tokenMe.value = { aberto: true, status: null, draft: '', msg: '', erro: false, ocupado: true }
  try {
    tokenMe.value.status = await api<TokenMe>('/api/conciliacao/melhor-envio/token')
  } catch (e: any) {
    tokenMe.value.msg = e?.data?.detail?.code || 'não consegui ler a situação do token'
    tokenMe.value.erro = true
  } finally {
    tokenMe.value.ocupado = false
  }
}
async function salvarTokenMe() {
  if (!tokenMe.value.draft.trim()) return
  tokenMe.value.ocupado = true
  tokenMe.value.msg = ''
  try {
    tokenMe.value.status = await api<TokenMe>('/api/conciliacao/melhor-envio/token', { method: 'PUT', body: { token: tokenMe.value.draft.trim() } })
    tokenMe.value.draft = ''
    tokenMe.value.msg = `token aceito pelo Melhor Envio · conta ${tokenMe.value.status?.conta || '—'}`
    tokenMe.value.erro = false
  } catch (e: any) {
    tokenMe.value.msg = e?.data?.detail?.mensagem || e?.data?.detail?.code || 'não consegui salvar'
    tokenMe.value.erro = true
  } finally {
    tokenMe.value.ocupado = false
  }
}
async function testarTokenMe() {
  tokenMe.value.ocupado = true
  tokenMe.value.msg = ''
  try {
    const r = await api<{ ok: boolean; conta?: string; envios_total?: number | null; envios_na_pagina?: number; mensagem?: string; http?: number }>('/api/conciliacao/melhor-envio/token/testar', { method: 'POST' })
    tokenMe.value.msg = r.ok ? `ok · conta ${r.conta || '—'} · ${r.envios_total ?? r.envios_na_pagina ?? '?'} envios na lista` : `falhou: ${r.mensagem || r.http || '?'}`
    tokenMe.value.erro = !r.ok
  } catch (e: any) {
    tokenMe.value.msg = e?.data?.detail?.code || 'não consegui testar'
    tokenMe.value.erro = true
  } finally {
    tokenMe.value.ocupado = false
  }
}
async function removerTokenMe() {
  if (!confirm('Remover o token do Melhor Envio do painel?')) return
  tokenMe.value.ocupado = true
  try {
    tokenMe.value.status = await api<TokenMe>('/api/conciliacao/melhor-envio/token', { method: 'DELETE' })
    tokenMe.value.msg = 'token removido'
    tokenMe.value.erro = false
  } catch (e: any) {
    tokenMe.value.msg = e?.data?.detail?.code || 'não consegui remover'
    tokenMe.value.erro = true
  } finally {
    tokenMe.value.ocupado = false
  }
}

const lojaAtual = computed(() => lojas.value.find((l) => l.conta === loja.value) || null)
// Cada aba mostra só as lojas da plataforma dela (15/10: Shopee entrou).
const lojasDaAba = computed(() => lojas.value.filter((l) => l.platform === tab.value))
// Loja lida por arquivo (16/10, KFA: a Amazon nega a API): alguém envia o CSV de
// Pagamentos › Visualizar transações › Fazer download.
const lojasUpload = computed(() => lojasDaAba.value.filter((l) => l.modo_extrato === 'upload'))
type ResultadoUpload = { loja: string; arquivo?: string; linhas: number; movimentos: number; inseridos: number; atualizados: number; ja_existiam: number; de: string | null; ate: string | null; por_tipo: Record<string, number> }
const upload = ref<{ aberto: boolean; loja: string; arquivo: File | null; msg: string; erro: boolean; ocupado: boolean; resultado: ResultadoUpload | null }>({ aberto: false, loja: '', arquivo: null, msg: '', erro: false, ocupado: false, resultado: null })
function abrirUpload() {
  upload.value = { aberto: true, loja: lojasUpload.value[0]?.conta || '', arquivo: null, msg: '', erro: false, ocupado: false, resultado: null }
  chaveScript.value.nova = ''
  lerChaveScript()
}
function escolherArquivo(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0] || null
  upload.value.arquivo = f
  upload.value.msg = ''
}
// Chave do script do Mac ("KFA Conciliação.command"): gerada aqui, aparece uma vez.
const chaveScript = ref<{ tem_chave: boolean; gerada_em?: string | null; por?: string | null; nova: string; ocupado: boolean; msg: string }>({ tem_chave: false, nova: '', ocupado: false, msg: '' })
async function lerChaveScript() {
  try {
    const r = await api<{ tem_chave: boolean; gerada_em?: string | null; por?: string | null }>('/api/conciliacao/amazon/upload/chave')
    chaveScript.value = { ...chaveScript.value, ...r }
  } catch {}
}
async function gerarChaveScript() {
  if (chaveScript.value.tem_chave && !confirm('Gerar uma chave nova? A anterior deixa de valer e o script precisa da nova.')) return
  chaveScript.value.ocupado = true
  chaveScript.value.msg = ''
  try {
    const r = await api<{ chave: string }>('/api/conciliacao/amazon/upload/chave', { method: 'POST' })
    chaveScript.value.nova = r.chave
    await lerChaveScript()
  } catch (e: any) {
    chaveScript.value.msg = e?.data?.detail?.code || 'não consegui gerar'
  } finally {
    chaveScript.value.ocupado = false
  }
}
async function enviarUpload() {
  if (!upload.value.arquivo || !upload.value.loja) return
  upload.value.ocupado = true
  upload.value.msg = ''
  try {
    const fd = new FormData()
    fd.append('loja', upload.value.loja)
    fd.append('arquivo', upload.value.arquivo)
    upload.value.resultado = await api<ResultadoUpload>('/api/conciliacao/amazon/upload', { method: 'POST', body: fd })
    upload.value.msg = `arquivo lido: ${upload.value.resultado.inseridos} linha(s) nova(s), ${upload.value.resultado.atualizados} atualizada(s), ${upload.value.resultado.ja_existiam} já estavam`
    upload.value.erro = false
    upload.value.arquivo = null
    await carregarLojas()
    await carregar()
  } catch (e: any) {
    upload.value.msg = e?.data?.detail?.mensagem || e?.data?.detail?.code || 'não consegui ler o arquivo'
    upload.value.erro = true
  } finally {
    upload.value.ocupado = false
  }
}
const nomeVenda = computed(() => (tab.value === 'shopee' ? 'Venda Shopee' : tab.value === 'tiktok' ? 'Venda TikTok' : tab.value === 'amazon' ? 'Venda Amazon' : 'Venda ML'))
function escolherLojaDaAba() {
  const primeira = lojasDaAba.value[0]
  if (!primeira) {
    loja.value = ''
    return
  }
  if (!lojasDaAba.value.some((l) => l.conta === loja.value)) {
    loja.value = primeira.conta
    de.value = primeira.inicio
    ate.value = ''
    page.value = 1
  }
}
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / SIZE)))
const pctBateu = computed(() => {
  const r = resumo.value
  if (!r || !r.pedidos) return 0
  const ok = (r.por_situacao?.bateu || 0) + (r.por_situacao?.cancelado || 0)
  return Math.round((ok / r.pedidos) * 100)
})

function params() {
  const p: Record<string, string | number> = { loja: loja.value, data: dataFiltro.value, page: page.value, size: SIZE }
  if (de.value) p.de = de.value
  if (ate.value) p.ate = ate.value
  if (situacao.value) p.situacao = situacao.value
  if (etapa.value) p.etapa = etapa.value
  if (q.value.trim()) p.q = q.value.trim()
  return p
}

async function carregarLojas() {
  const r = await api<{ lojas: Loja[] }>('/api/conciliacao/lojas')
  lojas.value = r.lojas
  if (!loja.value && lojasDaAba.value.length) {
    loja.value = lojasDaAba.value[0].conta
    if (!de.value) de.value = lojasDaAba.value[0].inicio
  }
}

watch(tab, () => {
  escolherLojaDaAba()
  if (visao.value === 'fechamento') visao.value = 'pedidos'
})

async function carregar() {
  if (!loja.value) return
  loading.value = true
  erro.value = ''
  try {
    const r = await api<{ itens: Pedido[]; total: number; resumo: Resumo }>('/api/conciliacao/pedidos', { query: params() })
    itens.value = r.itens
    total.value = r.total
    resumo.value = r.resumo
    if (selecionado.value && !itens.value.some((i) => i.pedido_bling === selecionado.value?.pedido_bling)) {
      selecionado.value = null
      raiox.value = null
    }
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro ao carregar'
  } finally {
    loading.value = false
  }
}

async function abrir(p: Pedido) {
  selecionado.value = p
  raioxLoading.value = true
  try {
    raiox.value = await api<RaioX>(`/api/conciliacao/pedidos/${encodeURIComponent(p.pedido_bling)}`, { query: { loja: loja.value } })
    obsDraft.value = raiox.value.pedido.observacao || ''
    motivoDraft.value = ''
  } catch (e: any) {
    toasts.error?.(e?.data?.detail?.code || 'não consegui abrir o pedido')
    raiox.value = null
  } finally {
    raioxLoading.value = false
  }
}

async function marcar(patch: Record<string, unknown>) {
  if (!selecionado.value || !canEdit.value) return
  salvando.value = true
  try {
    const r = await api<{ pedido: Pedido }>(`/api/conciliacao/pedidos/${encodeURIComponent(selecionado.value.pedido_bling)}`, {
      method: 'PATCH',
      body: { loja: loja.value, ...patch },
    })
    const i = itens.value.findIndex((x) => x.pedido_bling === r.pedido.pedido_bling)
    if (i >= 0) itens.value[i] = r.pedido
    selecionado.value = r.pedido
    if (raiox.value) raiox.value.pedido = r.pedido
    toasts.success?.('salvo')
  } catch (e: any) {
    const d = e?.data?.detail
    toasts.error?.(d?.mensagem || d?.code || 'não consegui salvar')
  } finally {
    salvando.value = false
  }
}

async function finalizar() {
  const p = selecionado.value
  if (!p) return
  const precisaMotivo = !['bateu', 'cancelado'].includes(p.situacao)
  if (precisaMotivo && !motivoDraft.value.trim()) {
    toasts.error?.('pra finalizar com pendência, escreva o motivo')
    return
  }
  await marcar({ etapa: 'finalizado', etapa_motivo: motivoDraft.value.trim() || undefined })
}

async function sincronizar() {
  if (!loja.value) return
  sincronizando.value = true
  try {
    const r = await api<{ enfileirado: boolean }>(`/api/conciliacao/lojas/${encodeURIComponent(loja.value)}/sincronizar`, { method: 'POST' })
    toasts.success?.(r.enfileirado ? 'rodada enfileirada: fatura, extrato e recálculo (leva alguns minutos)' : 'já tem uma rodada na fila')
  } catch (e: any) {
    toasts.error?.(e?.data?.detail?.code || 'não consegui enfileirar')
  } finally {
    sincronizando.value = false
  }
}

async function exportar() {
  exportando.value = true
  try {
    const { page: _p, size: _s, ...rest } = params()
    const blob = await api<Blob>('/api/conciliacao/export.xlsx', { method: 'POST', query: rest, responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `conciliacao_${loja.value}_${new Date().toISOString().slice(0, 10)}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    toasts.error?.('não consegui gerar o Excel')
  } finally {
    exportando.value = false
  }
}

function verPedido(pedido: string) {
  visao.value = 'pedidos'
  q.value = pedido
  situacao.value = ''
  etapa.value = ''
  de.value = lojaAtual.value?.inicio || ''
  ate.value = ''
  page.value = 1
  carregar()
}

function abrirExtrato(conta: string, dia: string) {
  loja.value = conta
  extratoDia.value = dia
  visao.value = 'extrato'
}

function verPedidos(conta: string, filtro: Record<string, string>) {
  loja.value = conta
  situacao.value = filtro.situacao || ''
  etapa.value = filtro.etapa || ''
  q.value = ''
  dataFiltro.value = 'pedido'
  de.value = lojaAtual.value?.inicio || ''
  ate.value = ''
  page.value = 1
  visao.value = 'pedidos'
  carregar()
}

function sincronizarLoja(conta: string) {
  loja.value = conta
  sincronizar()
}

function extrasTitulo(p: Pedido) {
  if (p.venda_extras == null) return ''
  const cupom = p.venda_cupom || 0
  const afiliados = p.venda_afiliados || 0
  const outros = Math.round((p.venda_extras - cupom - afiliados) * 100) / 100
  if (p.venda_extras < -0.005) return `a plataforma acrescentou ${fmtMoney(-p.venda_extras)} (${tab.value === 'tiktok' ? 'subsídio da TikTok no produto' : tab.value === 'amazon' ? 'devolução de tarifa / ajuste' : 'subsídio de frete, cashback'})`
  const partes: string[] = []
  if (Math.abs(cupom) > 0.005 || !afiliados) partes.push(`cupom da loja ${fmtMoney(cupom)}`)
  if (Math.abs(afiliados) > 0.005) partes.push(`afiliados ${fmtMoney(afiliados)}`)
  if (Math.abs(outros) > 0.005) partes.push(`parcelamento / outros ${fmtMoney(outros)}`)
  return partes.join(' · ')
}

function limpar() {
  situacao.value = ''
  etapa.value = ''
  q.value = ''
  dataFiltro.value = 'pedido'
  de.value = lojaAtual.value?.inicio || ''
  ate.value = ''
  page.value = 1
  carregar()
}

watch([loja, dataFiltro, situacao, etapa], () => {
  page.value = 1
  carregar()
})
watch(page, () => carregar())

await carregarLojas()
// Link do Vigia de Conciliação (Threema/Ouvidoria): /conciliacao?loja=&visao=extrato&dia= ou
// ?loja=&visao=pedidos&situacao=|etapa= — abre direto no lugar certo (15/10).
const q0 = useRoute().query
if (typeof q0.loja === 'string') {
  const alvo = lojas.value.find((l) => l.conta === q0.loja)
  if (alvo) {
    tab.value = alvo.platform
    loja.value = alvo.conta
    de.value = alvo.inicio
  }
}
if (typeof q0.situacao === 'string') situacao.value = q0.situacao
if (typeof q0.etapa === 'string') etapa.value = q0.etapa
if (q0.visao === 'extrato') {
  extratoDia.value = typeof q0.dia === 'string' ? q0.dia : ''
  visao.value = 'extrato'
} else if (q0.visao === 'pedidos') {
  visao.value = 'pedidos'
}
await carregar()
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-card px-3 py-2">
      <div class="flex flex-wrap items-center gap-3">
        <h1 class="text-base font-bold">Conciliação</h1>
        <div class="flex gap-0.5 border-b">
          <button
            v-for="m in MARKETPLACES"
            :key="m.key"
            type="button"
            class="px-3 py-1.5 text-xs font-semibold border-b-2 -mb-px"
            :class="[
              tab === m.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground',
              !m.ativo && 'opacity-50 cursor-not-allowed',
            ]"
            :disabled="!m.ativo"
            :title="m.ativo ? '' : 'entra depois do Mercado Livre'"
            @click="tab = m.key"
          >
            {{ m.label }}
          </button>
        </div>
        <div class="inline-flex overflow-hidden rounded-md border text-xs">
          <button
            v-for="v in VISOES"
            :key="v.key"
            type="button"
            class="px-3 py-1 font-semibold"
            :class="[visao === v.key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground', !v.ativo && 'opacity-50 cursor-not-allowed']"
            :disabled="!v.ativo"
            :title="v.ativo ? '' : 'próxima fase'"
            @click="visao = v.key"
          >
            {{ v.label }}
          </button>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-1.5 text-xs">
        <button v-if="visao === 'pedidos'" type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="loading" @click="carregar()">
          <RefreshCw class="size-3.5" :class="loading && 'animate-spin'" /> Recarregar
        </button>
        <button v-if="visao === 'pedidos'" type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="exportando" @click="exportar()">
          <Download class="size-3.5" /> Excel
        </button>
        <span v-if="visao !== 'resumo' && lojaAtual" class="text-muted-foreground">loja <b>{{ loja }}</b></span>
        <button v-if="canEdit && tab === 'amazon'" type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" title="Token do Melhor Envio (frete pago nos envios próprios)" @click="abrirTokenMe()">
          <KeyRound class="size-3.5" /> Melhor Envio
        </button>
        <button v-if="canEdit && tab === 'amazon' && lojasUpload.length" type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" title="Enviar o CSV de transações da loja que a Amazon não deixa ler pela API" @click="abrirUpload()">
          <Upload class="size-3.5" /> Enviar transações
        </button>
        <button
          v-if="canEdit && visao !== 'resumo'"
          type="button"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 font-semibold text-primary-foreground"
          :disabled="sincronizando"
          title="Lê a fatura do ML e o extrato do Mercado Pago agora e recalcula os pedidos desta loja"
          @click="sincronizar()"
        >
          <Loader2 v-if="sincronizando" class="size-3.5 animate-spin" /><Wand2 v-else class="size-3.5" /> Sincronizar agora
        </button>
      </div>
    </div>

    <ConciliacaoResumo v-if="visao === 'resumo'" :can-edit="canEdit" @abrir-extrato="abrirExtrato" @ver-pedidos="verPedidos" @sincronizar="sincronizarLoja" />

    <ConciliacaoExtrato v-if="visao === 'extrato'" :loja="loja" :plataforma="tab" :dia="extratoDia" :can-edit="canEdit" @ver-pedido="verPedido" />

    <ConciliacaoFechamento v-if="visao === 'fechamento'" v-model:loja="loja" :lojas="lojas" @abrir-extrato="abrirExtrato" />

    <template v-if="visao === 'pedidos'">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Loja</span>
        <select v-model="loja" class="bg-transparent outline-none">
          <option v-for="l in lojasDaAba" :key="l.conta" :value="l.conta">{{ l.conta }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">De</span>
        <input v-model="de" type="date" class="bg-transparent outline-none" @change="page = 1; carregar()" />
        <span class="font-semibold text-muted-foreground">até</span>
        <input v-model="ate" type="date" class="bg-transparent outline-none" @change="page = 1; carregar()" />
      </label>
      <div class="inline-flex items-center gap-2 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Data</span>
        <label class="inline-flex items-center gap-1"><input v-model="dataFiltro" type="radio" value="pedido" /> do pedido</label>
        <label class="inline-flex items-center gap-1"><input v-model="dataFiltro" type="radio" value="liberacao" /> da liberação</label>
      </div>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Situação</span>
        <select v-model="situacao" class="bg-transparent outline-none">
          <option value="">Todas</option>
          <option v-for="s in SITUACOES" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Etapa</span>
        <select v-model="etapa" class="bg-transparent outline-none">
          <option value="">Todas</option>
          <option v-for="e in ETAPAS" :key="e.key" :value="e.key">{{ e.label }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="q" placeholder="pedido Bling ou nº na plataforma" class="w-44 bg-transparent outline-none" @keyup.enter="page = 1; carregar()" />
      </label>
      <button type="button" class="text-muted-foreground underline" @click="limpar()">limpar</button>
      <span class="text-muted-foreground">{{ itens.length }} de {{ total }} pedidos</span>
      <span v-if="lojaAtual" class="ml-auto text-muted-foreground" :title="lojaAtual.ultimo_erro || ''">
        extrato lido {{ fmtDiaHora(lojaAtual.ultimo_extrato_em) }} · fatura {{ fmtDiaHora(lojaAtual.ultimo_fatura_em) }} · recálculo {{ fmtDiaHora(lojaAtual.ultimo_recalculo_em) }}
        <span v-if="lojaAtual.ultimo_erro" class="text-red-600">· erro: {{ lojaAtual.ultimo_erro }}</span>
      </span>
    </div>

    <div v-if="resumo" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-2 text-xs">
      <span class="text-sm font-bold">{{ loja }} · {{ resumo.pedidos }} pedidos</span>
      <span class="inline-flex items-center gap-2">
        <span class="inline-block h-2.5 w-48 overflow-hidden rounded-full bg-muted"><span class="block h-full bg-emerald-600" :style="{ width: pctBateu + '%' }" /></span>
        <b>{{ (resumo.por_situacao.bateu || 0) + (resumo.por_situacao.cancelado || 0) }} de {{ resumo.pedidos }}</b> fechados
      </span>
      <template v-for="s in SITUACOES" :key="s.key">
        <button v-if="resumo.por_situacao[s.key]" type="button" class="rounded px-1.5 py-0.5 font-bold" :class="s.cls" @click="situacao = situacao === s.key ? '' : s.key">
          {{ resumo.por_situacao[s.key] }} {{ s.label.toLowerCase() }}
        </button>
      </template>
      <span class="ml-auto inline-flex gap-2 text-muted-foreground">
        <template v-for="e in ETAPAS" :key="e.key">
          <button v-if="resumo.por_etapa[e.key]" type="button" class="rounded px-1.5 py-0.5 font-bold" :class="e.cls" @click="etapa = etapa === e.key ? '' : e.key">
            {{ resumo.por_etapa[e.key] }} {{ e.label }}
          </button>
        </template>
      </span>
    </div>

    <div class="grid gap-3 xl:grid-cols-[1fr_460px]">
      <div class="flex min-h-0 min-w-0 flex-col rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Pedidos — uma linha por pedido do Bling</span>
          <span class="text-[11px] font-normal text-slate-300">o que a loja deveria receber × o que entrou de fato × quanto sobrou</span>
        </div>
        <div class="max-h-[64vh] overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="sticky top-0 z-10 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr class="text-center normal-case">
                <th colspan="4" class="border-b px-1.5 py-1 text-left">Identificação</th>
                <th colspan="5" class="border-b border-l-2 px-1.5 py-1">{{ nomeVenda }}</th>
                <th colspan="2" class="border-b border-l-2 px-1.5 py-1">Extrato (entrou de fato)</th>
                <th class="border-b border-l-2 px-1.5 py-1">Conc.</th>
                <th colspan="4" class="border-b border-l-2 px-1.5 py-1">Resultado de verdade</th>
                <th colspan="4" class="border-b border-l-2 px-1.5 py-1">Situação</th>
              </tr>
              <tr>
                <th class="border-b px-1.5 py-1 text-left">Data</th>
                <th class="border-b px-1.5 py-1 text-left">Pedido</th>
                <th class="border-b px-1.5 py-1 text-left">Nº na plataforma</th>
                <th class="border-b px-1.5 py-1 text-left">Loja</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Valor</th>
                <th class="border-b px-1.5 py-1 text-right">Tarifa</th>
                <th class="border-b px-1.5 py-1 text-right">Frete</th>
                <th class="border-b px-1.5 py-1 text-right" title="cupom da loja, parcelamento e qualquer outro desconto antes de liberar: valor − tarifa − frete − líquido">Extras</th>
                <th class="border-b px-1.5 py-1 text-right">Líq. previsto</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-left">Liberado em</th>
                <th class="border-b px-1.5 py-1 text-right">Recebido</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Dif.</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Custo</th>
                <th class="border-b px-1.5 py-1 text-right">Reembolso</th>
                <th class="border-b px-1.5 py-1 text-right">Sobrou</th>
                <th class="border-b px-1.5 py-1 text-right">Margem</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-left">Conciliação</th>
                <th class="border-b px-1.5 py-1 text-left">Motivo</th>
                <th class="border-b px-1.5 py-1 text-left" title="situação do pedido no Bling">Status Bling</th>
                <th class="border-b px-1.5 py-1 text-left">Etapa</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loading && !itens.length">
                <td colspan="19" class="py-8 text-center text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</td>
              </tr>
              <tr v-else-if="erro">
                <td colspan="19" class="py-8 text-center text-red-600">{{ erro }}</td>
              </tr>
              <tr v-else-if="!itens.length">
                <td colspan="19" class="py-8 text-center text-muted-foreground">nenhum pedido com esse filtro</td>
              </tr>
              <tr
                v-for="p in itens"
                :key="p.id"
                class="cursor-pointer border-b hover:bg-muted/60"
                :class="[
                  selecionado?.pedido_bling === p.pedido_bling && 'outline outline-2 -outline-offset-2 outline-primary',
                  ['recebido_a_menos', 'vencido', 'estornado_depois'].includes(p.situacao) && 'bg-red-50 dark:bg-red-950/20',
                  ['aguardando', 'recebido_a_mais'].includes(p.situacao) && 'bg-amber-50/60 dark:bg-amber-950/10',
                ]"
                @click="abrir(p)"
              >
                <td class="whitespace-nowrap px-1.5 py-1">{{ fmtDia(p.data_pedido) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 font-bold">{{ p.pedido_bling }}</td>
                <td class="whitespace-nowrap px-1.5 py-1">{{ p.numeroloja }}<span v-if="p.pack_id" class="ml-1 text-muted-foreground">pack</span></td>
                <td class="whitespace-nowrap px-1.5 py-1">{{ p.conta }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.venda_valor) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.venda_tarifa) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">
                  <template v-if="(p.venda_frete || 0) > 0 || p.venda_frete_pago == null">{{ fmtMoney(p.venda_frete) }}</template>
                  <span v-else title="frete pago no Melhor Envio (fora da plataforma): entra no Sobrou, não no Líquido">{{ fmtMoney(p.venda_frete_pago) }} <span class="text-[9px] font-semibold text-muted-foreground">ME</span></span>
                </td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums" :title="extrasTitulo(p)">{{ fmtMoney(p.venda_extras) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums">{{ fmtMoney(p.venda_liquido) }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1" :class="!p.liberado_em && 'text-muted-foreground'">
                  <template v-if="p.liberado_em">{{ fmtDia(p.liberado_em) }}</template>
                  <template v-else-if="p.situacao === 'vencido'"><span class="text-red-600">venceu {{ fmtDia(p.previsao_liberacao) }}</span></template>
                  <template v-else-if="p.situacao === 'cancelado'">—</template>
                  <template v-else-if="p.previsao_liberacao">prev. {{ fmtDia(p.previsao_liberacao) }}</template>
                  <template v-else>—</template>
                </td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums" :class="!p.liberado_em && 'font-normal text-muted-foreground'">
                  {{ fmtMoney(p.recebido) }}<span v-if="p.saidas_depois" class="ml-1 cursor-help font-bold text-red-600" :title="`entrou ${fmtMoney(p.liberado_valor)} · saiu depois ${fmtMoney(p.saidas_depois)} (estorno) · ficou ${fmtMoney(p.recebido)}`">↩</span>
                </td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right font-bold tabular-nums" :class="sinal(p.dif_previsto_recebido, { pos: 'text-emerald-700', neg: 'text-red-600' })">
                  <template v-if="p.dif_previsto_recebido !== null">{{ p.dif_previsto_recebido > 0 ? '+ ' : '' }}{{ fmtMoney(p.dif_previsto_recebido) }}</template>
                  <template v-else>—</template>
                </td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.custo) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums" :class="p.reembolso && p.reembolso > 0 ? 'text-red-600' : ''">{{ fmtMoney(p.reembolso) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums" :class="[sinal(p.sobrou, { pos: 'text-emerald-700', neg: 'text-red-600' }), !p.liberado_em && 'font-normal text-muted-foreground']">
{{ fmtMoney(p.sobrou) }}
                </td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums" >{{ fmtPct(p.margem) }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1"><span class="rounded px-1.5 py-0.5 text-[10px] font-bold" :class="sitCls(p.situacao)">● {{ p.situacao_label }}</span></td>
                <td class="max-w-[220px] truncate px-1.5 py-1 text-muted-foreground" :title="p.motivo || ''">{{ p.motivo }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-muted-foreground">{{ p.situacao_bling || '—' }}</td>
                <td class="whitespace-nowrap px-1.5 py-1">
                  <span class="rounded px-1.5 py-0.5 text-[10px] font-bold" :class="etapaCls(p.etapa)">{{ p.etapa === 'finalizado' ? '■' : p.etapa === 'reaberto' ? '↺' : '●' }} {{ p.etapa_label }}</span>
                  <span v-if="p.conferido" class="ml-1 text-emerald-700" :title="`conferido por ${p.conferido_por} em ${fmtDiaHora(p.conferido_em)}`">✓</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="flex items-center justify-between border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          <span>Sobrou = Recebido (ou previsto, em cinza, enquanto não libera) − custo dos produtos − reembolsos do painel Reembolso − frete pago fora da plataforma (Melhor Envio, marcado ME). Margem = Sobrou ÷ custo.</span>
          <span class="inline-flex items-center gap-2">
            <button type="button" class="rounded border px-2 py-0.5 disabled:opacity-40" :disabled="page <= 1" @click="page--">‹</button>
            página {{ page }} de {{ totalPages }}
            <button type="button" class="rounded border px-2 py-0.5 disabled:opacity-40" :disabled="page >= totalPages" @click="page++">›</button>
          </span>
        </div>
      </div>

      <div class="flex min-h-0 min-w-0 flex-col rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Raio-X {{ selecionado ? 'do pedido ' + selecionado.pedido_bling : '' }}</span>
          <span class="text-[11px] font-normal text-slate-300">o que a venda prometeu × o que entrou de fato</span>
        </div>
        <div v-if="!selecionado" class="p-6 text-center text-xs text-muted-foreground">clique num pedido pra ver as quatro versões do dinheiro e cada movimento dele</div>
        <div v-else-if="raioxLoading" class="p-6 text-center text-xs text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</div>
        <div v-else-if="raiox" class="space-y-2 p-2.5 text-[11px]">
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 rounded border bg-muted/40 px-2 py-1.5">
            <span>Pedido Bling <b>{{ raiox.pedido.pedido_bling }}</b></span>
            <span>{{ nomeVenda }} <b>{{ raiox.pedido.numeroloja }}</b><span v-if="raiox.pedido.pack_id"> · pack {{ raiox.pedido.pack_id }}</span></span>
            <span>Loja <b>{{ raiox.pedido.conta }}</b> · vendido {{ fmtDia(raiox.pedido.data_pedido) }}</span>
            <span>Liberado {{ fmtDia(raiox.pedido.liberado_em) }}<span v-if="raiox.pedido.previsao_liberacao && !raiox.pedido.liberado_em && raiox.pedido.situacao !== 'cancelado'"> (prev. {{ fmtDia(raiox.pedido.previsao_liberacao) }})</span></span>
            <span>Bling: {{ raiox.pedido.situacao_bling || '—' }}</span>
            <span><span class="rounded px-1.5 py-0.5 font-bold" :class="sitCls(raiox.pedido.situacao)">● {{ raiox.pedido.situacao_label }}</span> <span class="rounded px-1.5 py-0.5 font-bold" :class="etapaCls(raiox.pedido.etapa)">{{ raiox.pedido.etapa_label }}</span></span>
          </div>
          <table class="w-full border-collapse">
            <thead class="bg-muted text-[10px] uppercase text-muted-foreground">
              <tr><th class="px-1.5 py-1 text-left"></th><th class="px-1.5 py-1 text-right">Previsto<br><span class="normal-case font-normal">({{ nomeVenda.toLowerCase() }})</span></th><th class="px-1.5 py-1 text-right">Real<br><span class="normal-case font-normal">({{ raiox.comparativo.real_rotulo }})</span></th><th class="px-1.5 py-1 text-right">Dif.</th></tr>
            </thead>
            <tbody class="tabular-nums">
              <tr v-for="l in raiox.comparativo.linhas" :key="l.chave" class="border-b" :class="[l.estilo === 'bold' && 'bg-muted/40 font-bold', l.estilo === 'muted' && 'text-muted-foreground', raiox.comparativo.comparar && l.dif != null && Math.abs(l.dif) > 0.05 && 'bg-red-50 dark:bg-red-950/20']">
                <td class="px-1.5 py-1">{{ l.rotulo }}</td>
                <td class="px-1.5 py-1 text-right" :class="l.previsto == null && 'text-muted-foreground'">{{ fmtMoney(l.previsto) }}</td>
                <td class="px-1.5 py-1 text-right" :class="l.real == null && 'text-muted-foreground'">{{ fmtMoney(l.real) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right" :class="[l.dif == null && 'text-muted-foreground', raiox.comparativo.comparar && l.dif != null && (Math.abs(l.dif) > 0.05 ? 'font-bold text-red-600' : 'text-emerald-700')]">
                  <template v-if="l.dif == null || !raiox.comparativo.comparar">—</template>
                  <template v-else>{{ fmtMoney(l.dif) }} {{ Math.abs(l.dif) > 0.05 ? '✖' : '✔' }}</template>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="pt-0.5 text-[10px] text-muted-foreground">
            <span v-if="raiox.comparativo.nota">{{ raiox.comparativo.nota }}</span>
            <span v-else-if="raiox.comparativo.rodape">{{ raiox.comparativo.rodape }}</span>
          </div>
          <template v-if="raiox.frete_pago?.length || raiox.pedido.situacao === 'falta_frete'">
            <div class="pt-1 font-bold text-[#1f3a5f] dark:text-slate-200">Frete pago fora da Amazon · Melhor Envio</div>
            <div class="rounded border border-indigo-200 bg-indigo-50/60 px-2 py-1.5 text-[11px] dark:border-indigo-900 dark:bg-indigo-950/30">
              <template v-if="raiox.frete_pago?.length">
                <div v-for="f in raiox.frete_pago" :key="f.rastreio || f.servico || ''" class="grid grid-cols-[1fr_auto] gap-x-3 tabular-nums">
                  <span>Etiqueta {{ f.servico || '—' }}<span v-if="f.rastreio" class="text-muted-foreground"> · {{ f.rastreio }}</span><span v-if="f.cancelado_em" class="text-red-600"> · cancelada</span></span><span>{{ fmtMoney(f.preco) }}</span>
                  <template v-if="(f.desconto || 0) > 0"><span class="text-muted-foreground">− desconto</span><span>{{ fmtMoney(f.desconto) }}</span></template>
                  <template v-if="(f.conferencia || 0) > 0"><span class="text-muted-foreground">+ conferência de peso (cobrado a mais)</span><span>{{ fmtMoney(f.conferencia) }}</span></template>
                </div>
                <div class="mt-1 grid grid-cols-[1fr_auto] gap-x-3 border-t border-indigo-200 pt-1 font-bold tabular-nums"><span>= Frete pago</span><span>{{ fmtMoney(raiox.pedido.venda_frete_pago) }}</span></div>
                <div class="text-[10px] text-muted-foreground">não entra no Líquido (a Amazon não desconta): entra no Sobrou</div>
              </template>
              <div v-else class="text-amber-700">o Melhor Envio ainda não trouxe o envio deste pedido — por isso ele não está "bateu"</div>
            </div>
          </template>
          <div class="pt-1 font-bold text-[#1f3a5f] dark:text-slate-200">Movimentos deste pedido</div>
          <div class="max-h-56 overflow-auto rounded border">
            <table class="w-full border-collapse">
              <thead class="bg-muted text-[10px] uppercase text-muted-foreground"><tr><th class="px-1.5 py-1 text-left">Data</th><th class="px-1.5 py-1 text-left">Origem</th><th class="px-1.5 py-1 text-left">O que foi</th><th class="px-1.5 py-1 text-right">Valor</th><th class="px-1 py-1"></th></tr></thead>
              <tbody class="tabular-nums">
                <tr v-if="!raiox.movimentos.length"><td colspan="5" class="px-1.5 py-2 text-center text-muted-foreground">nenhum movimento ainda</td></tr>
                <tr v-if="reservasEscondidas" class="border-b bg-muted/30">
                  <td colspan="5" class="px-1.5 py-1 text-muted-foreground">
                    {{ reservasEscondidas }} linha(s) de reserva por reclamação {{ mostrarReservas ? 'à mostra' : 'escondida(s)' }} (vão e voltam, somam zero)
                    <button type="button" class="ml-1 underline" @click="mostrarReservas = !mostrarReservas">{{ mostrarReservas ? 'esconder' : 'mostrar' }}</button>
                    <span v-if="pagamentosDoPedido > 1" class="ml-2">· {{ pagamentosDoPedido }} pagamentos nesta venda</span>
                  </td>
                </tr>
                <template v-for="m in movimentosVisiveis" :key="m.id">
                  <tr class="border-b" :class="m.tipo.startsWith('reserva_') && 'text-muted-foreground'">
                    <td class="whitespace-nowrap px-1.5 py-1 text-muted-foreground">{{ fmtDiaHora(m.data) }}</td>
                    <td class="whitespace-nowrap px-1.5 py-1">{{ m.origem === 'ml_fatura' ? 'fatura' : m.origem === 'shopee_carteira' ? 'carteira' : 'extrato' }}<span v-if="pagamentosDoPedido > 1 && m.pagamento_id" class="ml-1 text-[10px] text-muted-foreground" :title="'pagamento ' + m.pagamento_id">pag. …{{ String(m.pagamento_id).slice(-4) }}</span></td>
                    <td class="px-1.5 py-1">{{ tipoMov(m.tipo) }}<span v-if="m.descontado_da_venda === false && !(m.tipo === 'fatura_frete' && ((raiox.versoes.extrato.frete as number) || 0) > 0)" class="ml-1 text-muted-foreground">(cobrada à parte)</span></td>
                    <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold" :class="(m.credito || 0) > 0 ? 'text-emerald-700' : 'text-red-600'">{{ (m.credito || 0) > 0 ? '+ ' + fmtMoney(m.credito) : '− ' + fmtMoney(m.debito) }}</td>
                    <td class="px-1 py-1 text-right">
                      <button type="button" class="rounded border px-1 text-[10px] text-muted-foreground hover:bg-muted" :title="m.origem === 'ml_fatura' ? 'o detalhe original da fatura do ML' : 'a linha original como veio da plataforma'" @click="originalAberto = originalAberto === m.id ? null : m.id">{{ originalAberto === m.id ? 'fechar' : 'original' }}</button>
                    </td>
                  </tr>
                  <tr v-if="originalAberto === m.id" class="border-b bg-muted/40">
                    <td colspan="5" class="px-2 py-1.5">
                      <div class="mb-1 text-[10px] font-semibold uppercase text-muted-foreground">{{ m.origem === 'ml_fatura' ? 'Detalhe original da fatura (Mercado Livre)' : m.origem === 'shopee_carteira' ? 'Lançamento original da carteira (Shopee)' : m.origem === 'tiktok_extrato' ? 'Transação original do extrato diário (TikTok)' : m.origem === 'amazon_transacoes' ? 'Transação original (Amazon, Pagamentos › Transações)' : 'Linha original do relatório de liberações (Mercado Pago)' }}</div>
                      <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
                        <template v-for="(v, k) in m.original || {}" :key="String(k)">
                          <span class="font-mono text-muted-foreground">{{ k }}</span><span class="break-all">{{ typeof v === 'object' ? JSON.stringify(v) : String(v) }}</span>
                        </template>
                        <span v-if="!m.original || !Object.keys(m.original).length" class="col-span-2 text-muted-foreground">sem linha original guardada</span>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <div class="rounded border px-2 py-1.5" :class="['recebido_a_menos', 'vencido', 'estornado_depois'].includes(raiox.pedido.situacao) ? 'border-red-300 bg-red-50 dark:bg-red-950/20' : 'border-emerald-300 bg-emerald-50 dark:bg-emerald-950/20'">
            <b>Previsto {{ fmtMoney(raiox.pedido.venda_liquido) }} · Recebido {{ fmtMoney(raiox.pedido.recebido) }} · Diferença {{ fmtMoney(raiox.pedido.dif_previsto_recebido) }}</b>
            <span v-if="raiox.pedido.motivo"> — {{ raiox.pedido.motivo }}</span><br />
            <b v-if="raiox.pedido.recebido != null">Sobrou: {{ fmtMoney(raiox.pedido.recebido) }} − custo {{ fmtMoney(raiox.pedido.custo) }} − reembolso {{ fmtMoney(raiox.pedido.reembolso) }}<template v-if="raiox.pedido.venda_frete_pago != null"> − frete pago (Melhor Envio) {{ fmtMoney(raiox.pedido.venda_frete_pago) }}</template> = {{ fmtMoney(raiox.pedido.sobrou) }} · margem {{ fmtPct(raiox.pedido.margem) }}</b><b v-else class="font-normal text-muted-foreground">Resultado de verdade: só depois que o dinheiro entrar</b>
            <div v-if="raiox.pedido.etapa_por" class="mt-1 text-muted-foreground">etapa {{ raiox.pedido.etapa_label }} por {{ raiox.pedido.etapa_por }} em {{ fmtDiaHora(raiox.pedido.etapa_em) }}<span v-if="raiox.pedido.etapa_motivo">: {{ raiox.pedido.etapa_motivo }}</span></div>
          </div>
          <div v-if="canEdit" class="space-y-1.5">
            <textarea v-model="obsDraft" rows="2" placeholder="observação" class="w-full rounded border bg-background px-2 py-1 text-[11px]" />
            <input v-if="!['bateu', 'cancelado'].includes(raiox.pedido.situacao)" v-model="motivoDraft" placeholder="motivo pra finalizar (obrigatório quando tem pendência)" class="w-full rounded border bg-background px-2 py-1 text-[11px]" />
            <div class="flex flex-wrap gap-1.5">
              <button type="button" class="rounded border px-2 py-1 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ observacao: obsDraft })">Salvar observação</button>
              <button type="button" class="rounded border px-2 py-1 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ conferido: !raiox.pedido.conferido })">{{ raiox.pedido.conferido ? 'Desmarcar conferido' : 'Marcar conferido' }}</button>
              <button v-if="raiox.pedido.etapa !== 'finalizado'" type="button" class="rounded bg-primary px-2 py-1 font-semibold text-primary-foreground" :disabled="salvando" @click="finalizar()">Finalizar</button>
              <button v-else type="button" class="rounded border px-2 py-1 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ etapa: 'reaberto', etapa_motivo: motivoDraft || 'reaberto à mão' })">Reabrir</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    </template>

    <div v-if="upload.aberto" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="upload.aberto = false">
      <div class="w-full max-w-lg rounded-md border bg-card shadow-lg">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-2 text-xs font-semibold text-white">
          <span>Enviar transações da Amazon (arquivo)</span>
          <button type="button" class="text-white/80 hover:text-white" @click="upload.aberto = false">fechar</button>
        </div>
        <div class="space-y-3 p-3 text-xs">
          <div class="rounded border bg-muted/40 px-2.5 py-2 text-[11px] text-muted-foreground">
            No Seller Central da loja: <b>Pagamentos › Visualizar transações</b> › Grupo de Pagamento <b>Todos os tipos de conta</b> › período (até 600 transações por arquivo; se passar, divide por mês) › <b>Atualizar</b> › <b>Fazer download</b>. Pode enviar o mesmo período de novo: linha repetida não duplica e "Diferido" que virou "Liberado" é atualizado. O arquivo não traz os repasses pro banco.
          </div>
          <div class="grid gap-2 sm:grid-cols-2">
            <label class="space-y-1"><span class="text-[11px] font-semibold">Loja</span>
              <select v-model="upload.loja" class="w-full rounded border bg-background px-2 py-1.5"><option v-for="l in lojasUpload" :key="l.conta" :value="l.conta">{{ l.conta }}</option></select>
            </label>
            <label class="space-y-1"><span class="text-[11px] font-semibold">Arquivo CSV</span>
              <input type="file" accept=".csv,text/csv" class="w-full rounded border bg-background px-2 py-1 text-[11px]" @change="escolherArquivo" />
            </label>
          </div>
          <div v-if="upload.msg" class="rounded px-2.5 py-1.5" :class="upload.erro ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'">{{ upload.msg }}</div>
          <div v-if="upload.resultado && !upload.erro" class="text-[11px] text-muted-foreground">
            {{ upload.resultado.linhas }} linhas no arquivo · {{ upload.resultado.de }} a {{ upload.resultado.ate }} · <span v-for="(n, t) in upload.resultado.por_tipo" :key="t" class="mr-2">{{ t }}: {{ n }}</span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button" class="rounded-md bg-primary px-2.5 py-1.5 font-semibold text-primary-foreground disabled:opacity-50" :disabled="upload.ocupado || !upload.arquivo || !upload.loja" @click="enviarUpload()">Enviar</button>
            <Loader2 v-if="upload.ocupado" class="size-3.5 animate-spin text-muted-foreground" />
          </div>
          <div class="rounded border px-2.5 py-2">
            <div class="text-[11px] font-semibold">Script no Mac (baixa e envia sozinho)</div>
            <div class="text-[11px] text-muted-foreground">O "KFA Conciliação.command" abre o perfil da Amazon no AdsPower, baixa o CSV dos últimos dias e envia aqui. Ele se identifica por uma chave: gere, copie e cole no arquivo <span class="font-mono">~/.davinci/kfa.env</span> do Mac (linha <span class="font-mono">DAVINCI_UPLOAD_CHAVE=…</span>). A chave aparece uma vez só.</div>
            <div class="mt-1 flex flex-wrap items-center gap-2">
              <span v-if="chaveScript.tem_chave && !chaveScript.nova" class="text-[11px] text-muted-foreground">chave gerada em {{ chaveScript.gerada_em ? new Date(chaveScript.gerada_em).toLocaleString('pt-BR') : '—' }} por {{ chaveScript.por || '—' }}</span>
              <button type="button" class="rounded-md border px-2.5 py-1 font-semibold hover:bg-muted disabled:opacity-50" :disabled="chaveScript.ocupado" @click="gerarChaveScript()">{{ chaveScript.tem_chave ? 'Gerar chave nova' : 'Gerar chave pro script' }}</button>
              <span v-if="chaveScript.msg" class="text-[11px] text-red-700">{{ chaveScript.msg }}</span>
            </div>
            <div v-if="chaveScript.nova" class="mt-1 rounded bg-emerald-50 px-2 py-1.5 font-mono text-[11px] text-emerald-900 break-all">DAVINCI_UPLOAD_CHAVE={{ chaveScript.nova }}</div>
          </div>
        </div>
      </div>
    </div>
    <div v-if="tokenMe.aberto" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="tokenMe.aberto = false">
      <div class="w-full max-w-lg rounded-md border bg-card shadow-lg">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-2 text-xs font-semibold text-white">
          <span>Token do Melhor Envio</span>
          <button type="button" class="text-white/80 hover:text-white" @click="tokenMe.aberto = false">fechar</button>
        </div>
        <div class="space-y-3 p-3 text-xs">
          <div class="rounded border bg-muted/40 px-2.5 py-2">
            <template v-if="tokenMe.status">
              <div v-if="tokenMe.status.origem === 'painel'">Token guardado no painel · conta <b>{{ tokenMe.status.conta || '—' }}</b> · colado em {{ tokenMe.status.atualizado_em ? new Date(tokenMe.status.atualizado_em).toLocaleString('pt-BR') : '—' }} por {{ tokenMe.status.atualizado_por || '—' }}</div>
              <div v-else class="text-amber-700">{{ tokenMe.status.aviso }}</div>
            </template>
            <div v-else class="text-muted-foreground">lendo…</div>
          </div>
          <div class="space-y-1">
            <div class="text-[11px] text-muted-foreground">Cole o token gerado em Melhor Envio › Configurações › Tokens (só leitura: envios, compras, rastreio, transações, cotação). Ele é conferido no Melhor Envio e guardado criptografado; não aparece de novo nesta tela.</div>
            <input v-model="tokenMe.draft" type="password" autocomplete="off" spellcheck="false" placeholder="cole o token aqui (começa com eyJ)" class="w-full rounded border bg-background px-2 py-1.5 font-mono text-[11px]" />
          </div>
          <div v-if="tokenMe.msg" class="rounded px-2.5 py-1.5" :class="tokenMe.erro ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'">{{ tokenMe.msg }}</div>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button" class="rounded-md bg-primary px-2.5 py-1.5 font-semibold text-primary-foreground disabled:opacity-50" :disabled="tokenMe.ocupado || !tokenMe.draft.trim()" @click="salvarTokenMe()">Salvar token</button>
            <button type="button" class="rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted disabled:opacity-50" :disabled="tokenMe.ocupado || !tokenMe.status?.tem_token" @click="testarTokenMe()">Testar</button>
            <button v-if="tokenMe.status?.origem === 'painel'" type="button" class="rounded-md border px-2.5 py-1.5 font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50" :disabled="tokenMe.ocupado" @click="removerTokenMe()">Remover</button>
            <Loader2 v-if="tokenMe.ocupado" class="size-3.5 animate-spin text-muted-foreground" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
