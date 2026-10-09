<script lang="ts">
// As FILAS do e-mail das lojas (RF5/RF6, 08/10/2026), na aba E-mail do
// /atendimento (a seção "Filas" do AtendimentoMail, a Central do outro dev).
// Só quem MEXE no /atendimento: as filas podem ter e-mail de qualquer loja e
// de endereço interno (a rota recusa os outros com `atendimento_so_quem_mexe`).
//
//   Sem loja      — o endereço/pasta não diz de qual loja é (alias sem
//                   cadastro, de duas lojas, marca ambígua…): escolher a loja
//                   ou a marca (as sugestões são 1 clique; a ficha SEM
//                   integração também vale — o e-mail entra na conversa dela).
//                   "Reprocessar" depois de corrigir o cadastro (Cadastros › Lojas).
//   Sem vínculo   — na conversa da loja, sem pedido: vincular ao pedido (o
//                   citado que existe é 1 clique).
//   Suspeitos     — remetente pode ser falso: ler, conferir no Tuta, nunca responder.
//   Resumos       — resumo diário/lista de vários pedidos: só histórico.
//   Com erro      — a ponte não conseguiu ler: reprocessar ou tirar da fila.
//   Revisar envio — a resposta que PODE ter saído (o Mac não teve certeza,
//                   ou pegou e sumiu há mais de 15 min): conferir nos Enviados
//                   do Tuta e marcar Saiu / Não saiu — nunca reenvia (a regra
//                   da Central).
//
// O texto (protegido: links de acesso e códigos fora, nunca HTML) só abre no
// "ver o e-mail" (GET …/emails/{id}). Rotas: routers/atendimento_email.py.
// Funções puras aqui em cima (tests/atendimento-mail-atendimento.cjs).

import type { ResumoLoja } from '~/components/AtendimentoPlataforma.vue'

export type Fila = 'sem_loja' | 'sem_vinculo' | 'suspeito' | 'resumo' | 'erro' | 'revisar_envio'
// A ordem e os nomes da tela (as chaves = FILAS de mail_atendimento/constantes.py + o "revisar envio").
export const FILAS: { value: Fila; label: string; dica: string }[] = [
  { value: 'sem_loja', label: 'Sem loja', dica: 'O endereço que recebeu (e a pasta) não diz de qual loja é: escolha a loja.' },
  { value: 'sem_vinculo', label: 'Sem vínculo', dica: 'Já está na conversa da loja, mas sem pedido: vincule ao pedido.' },
  { value: 'suspeito', label: 'Suspeitos', dica: 'O remetente pode ser falso: confira no Tuta. Não responda nem clique em links.' },
  { value: 'resumo', label: 'Resumos', dica: 'Resumo diário ou lista com vários pedidos: fica só como histórico.' },
  { value: 'erro', label: 'Com erro', dica: 'A ponte não conseguiu ler este e-mail: reprocesse ou tire da fila.' },
  { value: 'revisar_envio', label: 'Revisar envio', dica: 'A resposta pode ter saído (o Mac não confirmou): confira nos Enviados do Tuta e marque.' },
]

export type ItemFila = {
  id: string
  estado: string
  recebido_em: string | null
  pasta: string | null
  plataforma: string | null
  finalidade: string | null
  destaque: boolean
  alias: string | null
  loja: string | null
  integration_id: string | null
  motivo: string | null
  motivo_texto: string | null
  sugestoes: Sugestao[]
  suspeito: boolean
  suspeito_motivos: string[]
  conversa_id: string | null
  pedido: string | null
  pedidos_citados: { pedido: string; existe: boolean; plataforma: string | null }[]
  protocolo: string | null
  alertas: { codigo: string; texto: string }[]
  abrir_no_tuta: string | null
  sem_integracao?: boolean
  assunto?: string | null
  de?: string | null
}
// A loja do cadastro (a ficha, com ou sem integração) OU — na marca ambígua
// (o mesmo domínio em duas marcas) — a marca do site.
export type Sugestao = {
  store_info_id: string | null
  marca_id?: string | null
  marca_slug?: string | null
  nome: string | null
  plataforma: string
  integration_id: string | null
  sugestao: boolean
}
export type EnvioARevisar = {
  id: string
  status: string
  codigo: string | null
  de: string | null
  para: string | null
  conversa_id: string | null
  mensagem_id: string | null
  criado_em: string | null
  concluido_em: string | null
  resolucao: string | null
  resolvido_em: string | null
  revisar: boolean
  // O Mac pegou e sumiu (lease de mais de 15 min, sem recibo).
  lease_vencido?: boolean
  recibo_tardio?: string | null
}

// As sugestões que dá para escolher com 1 clique: a ficha (com ou sem
// integração: a sem integração entra na conversa da ficha) ou a marca.
export function sugestoesUteis(e: Pick<ItemFila, 'sugestoes'>): Sugestao[] {
  return (e.sugestoes || []).filter((s) => !!s.store_info_id || !!s.marca_id)
}

// O corpo do POST …/loja para a sugestão (a marca, ou a ficha).
export function corpoDaSugestao(s: Sugestao): { store_info_id: string } | { marca_id: string } {
  return s.marca_id ? { marca_id: s.marca_id } : { store_info_id: s.store_info_id || '' }
}

export function rotuloDaSugestao(s: Sugestao, curto: (p: string) => string): string {
  if (s.marca_id) return `site ${s.nome || s.marca_slug || 'marca'}${s.sugestao ? ' (sugestão)' : ''}`
  const sem = s.integration_id ? '' : ' (sem integração)'
  return `${curto(s.plataforma)} ${s.nome || 'loja'}${sem}${s.sugestao ? ' (sugestão)' : ''}`
}

// As lojas conectadas para "outra loja…" (as da plataforma da pasta primeiro).
export function lojasParaEscolher(lojas: ResumoLoja[], plataforma: string | null | undefined): ResumoLoja[] {
  const conectadas = lojas.filter((l) => !!l.integration_id)
  const p = (plataforma || '').trim()
  if (!p) return conectadas
  return [...conectadas.filter((l) => l.plataforma === p), ...conectadas.filter((l) => l.plataforma !== p)]
}

// Os pedidos citados que EXISTEM na loja: vincular com 1 clique.
export function pedidosQueExistem(e: Pick<ItemFila, 'pedidos_citados'>): string[] {
  return [...new Set((e.pedidos_citados || []).filter((p) => p.existe && p.pedido).map((p) => p.pedido))]
}

// O que cada fila deixa fazer (a rota confere de novo).
export function acoesDaFila(fila: Fila, isAdmin: boolean) {
  return {
    escolherLoja: fila === 'sem_loja',
    vincular: fila === 'sem_vinculo',
    ignorar: fila === 'sem_loja' || fila === 'resumo' || fila === 'erro',
    reprocessar: fila === 'sem_loja' || fila === 'erro',
    reprocessarFila: fila === 'sem_loja' && isAdmin,
  }
}

export function urlDaFila(fila: Fila, antesDe?: string | null): string {
  if (fila === 'revisar_envio') return '/api/atendimento/email/envios?estado=revisar'
  const q = new URLSearchParams({ fila })
  if (antesDe) q.set('antes_de', antesDe)
  return `/api/atendimento/email/emails?${q.toString()}`
}

// A pergunta antes de marcar (o "não saiu" libera responder de novo: o cliente pode receber duas vezes).
export function perguntaResolver(saiu: boolean): string {
  return saiu
    ? 'Confirmar que esta resposta aparece nos Enviados do Tuta?'
    : 'Marcar que esta resposta NÃO saiu?\n\nConfira nos Enviados do Tuta antes: se ela saiu e alguém responder de novo, o cliente recebe duas vezes.'
}

export const ESTADO_DA_CAIXA: Record<string, string> = {
  online: 'Mac conectado',
  offline: 'Mac desconectado',
  login_required: 'faça login no Mac',
  error: 'conexão precisa de atenção',
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronUp, Download, ExternalLink, Loader2, MailWarning, RefreshCw, RotateCcw, ShieldAlert, TriangleAlert } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'
import { avisosDeProtecao, destinoDaLoja, remetenteLegivel, tamanhoLegivel, urlDoAnexo, type CartaoEmail } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{
  isAdmin: boolean
  lojas: ResumoLoja[]
}>()
const emit = defineEmits<{ (e: 'abrirConversa', id: string): void }>()

const { api, url } = useApi()
const toasts = useToasts()

const fila = ref<Fila>('sem_loja')
const contagens = ref<Record<string, number>>({})
const itens = ref<ItemFila[]>([])
const proximo = ref<string | null>(null)
const envios = ref<EnvioARevisar[]>([])
const saude = ref<any | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null)
const aberto = ref<string | null>(null)
const detalhe = ref<CartaoEmail | null>(null)
const pedidoDigitado = reactive<Record<string, string>>({})
const lojaEscolhida = reactive<Record<string, string>>({})
let geracao = 0

const acoes = computed(() => acoesDaFila(fila.value, props.isAdmin))
const filaInfo = computed(() => FILAS.find((f) => f.value === fila.value) || FILAS[0])

async function carregarContagens() {
  try {
    contagens.value = await api<Record<string, number>>('/api/atendimento/email/filas')
  } catch {
    // As contagens são ajuda: a fila aberta mostra o erro dela.
  }
}
async function carregarSaude() {
  try {
    saude.value = await api('/api/atendimento/email/saude')
  } catch {
    saude.value = null
  }
}
async function carregar(mais = false) {
  const g = ++geracao
  const f = fila.value
  carregando.value = true
  erro.value = null
  try {
    if (f === 'revisar_envio') {
      const r = await api<{ itens: EnvioARevisar[] }>(urlDaFila(f))
      if (g !== geracao) return
      envios.value = r.itens || []
    } else {
      const r = await api<{ itens: ItemFila[]; proximo: string | null }>(urlDaFila(f, mais ? proximo.value : null))
      if (g !== geracao) return
      itens.value = mais ? [...itens.value, ...(r.itens || []).filter((x) => !itens.value.some((y) => y.id === x.id))] : r.itens || []
      for (const i of itens.value) lojaEscolhida[i.id] ??= ''
      proximo.value = r.proximo || null
    }
  } catch (e: any) {
    if (g === geracao) erro.value = erroDaApi(e, 'Não consegui carregar a fila de e-mail').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
async function atualizar() {
  await Promise.all([carregar(), carregarContagens(), carregarSaude()])
}
watch(fila, () => {
  aberto.value = null
  detalhe.value = null
  itens.value = []
  envios.value = []
  proximo.value = null
  void carregar()
})
onMounted(() => { void atualizar() })

async function abrir(item: ItemFila) {
  if (aberto.value === item.id) {
    aberto.value = null
    detalhe.value = null
    return
  }
  aberto.value = item.id
  detalhe.value = null
  try {
    const d = await api<CartaoEmail>(`/api/atendimento/email/emails/${encodeURIComponent(item.id)}`)
    if (aberto.value === item.id) detalhe.value = d
  } catch (e: any) {
    if (aberto.value === item.id) {
      aberto.value = null
      const er = erroDaApi(e, 'Não consegui abrir o e-mail')
      toasts.error(er.texto, er.motivos)
    }
  }
}

// Toda ação: uma por vez, com o aviso e a fila relida (o e-mail sai dela).
async function acao(chave: string, fn: () => Promise<unknown>, ok: string) {
  if (ocupado.value) return
  ocupado.value = chave
  try {
    await fn()
    toasts.success(ok)
    await Promise.all([carregar(), carregarContagens()])
  } catch (e: any) {
    const er = erroDaApi(e, 'Não deu certo')
    toasts.error(er.texto, er.motivos)
  } finally {
    ocupado.value = null
  }
}

function escolherLoja(e: ItemFila, corpo: { store_info_id?: string; integration_id?: string; marca_id?: string }) {
  return acao(`loja:${e.id}`, () => api(`/api/atendimento/email/emails/${encodeURIComponent(e.id)}/loja`, { method: 'POST', body: corpo }), 'E-mail ligado à loja')
}
function vincular(e: ItemFila, pedido: string | undefined) {
  const p = (pedido || '').trim()
  if (!p) return
  return acao(`vinc:${e.id}`, () => api(`/api/atendimento/email/emails/${encodeURIComponent(e.id)}/vincular`, { method: 'POST', body: { pedido: p } }), `E-mail vinculado ao pedido ${p}`)
}
function ignorar(e: ItemFila) {
  if (!confirm('Tirar este e-mail da fila? Ele continua na caixa inteira (e fica registrado quem tirou).')) return
  return acao(`ign:${e.id}`, () => api(`/api/atendimento/email/emails/${encodeURIComponent(e.id)}/ignorar`, { method: 'POST' }), 'Tirado da fila')
}
function reprocessar(e: ItemFila) {
  return acao(`rep:${e.id}`, () => api(`/api/atendimento/email/emails/${encodeURIComponent(e.id)}/reprocessar`, { method: 'POST' }), 'Passou de novo pela ponte')
}
function reprocessarFila() {
  if (!confirm('Passar de novo pela ponte toda a fila "sem loja" (e os com erro)? Use depois de corrigir o cadastro das lojas.')) return
  return acao('reprocessar', async () => {
    const r = await api<{ total: number; resolvidos: number }>('/api/atendimento/email/reprocessar', { method: 'POST' })
    toasts.info(`${r?.resolvidos ?? 0} de ${r?.total ?? 0} acharam a loja`)
  }, 'Fila reprocessada')
}
function resolver(v: EnvioARevisar, saiu: boolean) {
  if (!confirm(perguntaResolver(saiu))) return
  return acao(`env:${v.id}`, () => api(`/api/atendimento/email/envios/${encodeURIComponent(v.id)}/resolver`, { method: 'POST', body: { saiu } }), saiu ? 'Marcada: a resposta saiu' : 'Marcada: a resposta não saiu (dá para responder de novo)')
}

const caixasDaSaude = computed<any[]>(() => saude.value?.caixas || [])
const lojasSemCaixa = computed<any[]>(() => saude.value?.lojas_sem_caixa_lida || [])
const verLojasSemCaixa = ref(false)
const lojasSemIntegracao = computed<any[]>(() => saude.value?.lojas_sem_integracao || [])
const verLojasSemIntegracao = ref(false)
const curto = (p: string) => plataformaInfo(p).curto
</script>

<template>
  <div class="space-y-3" data-mail-filas>
    <!-- as caixas que a ponte lê (a saúde) -->
    <div v-if="caixasDaSaude.length || lojasSemCaixa.length || lojasSemIntegracao.length" class="space-y-1 rounded-lg border p-3 text-xs" data-mail-saude>
      <div v-for="c in caixasDaSaude" :key="c.mailbox_id" class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span class="font-medium">{{ c.nome }}</span>
        <span class="text-muted-foreground">{{ c.visibilidade === 'empresa' ? 'da empresa' : 'privada' }}</span>
        <span :class="c.estado === 'online' ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-800 dark:text-amber-300'">{{ ESTADO_DA_CAIXA[c.estado] || c.estado }}</span>
        <span :class="c.ponte_ligada ? '' : 'text-muted-foreground'">{{ c.ponte_ligada ? `ponte ligada desde ${fmtDataHora(c.ponte_desde)}` : 'ponte desligada (nada vai ao Atendimento)' }}</span>
        <span class="text-muted-foreground">· envio {{ c.envio?.ligado ? (c.envio?.modo === 'teste' ? 'em teste' : 'ligado') : 'desligado' }}</span>
        <span v-if="c.envios_a_conferir" class="text-amber-800 dark:text-amber-300">· {{ c.envios_a_conferir }} a conferir</span>
        <span v-if="c.pastas_sem_revisar" class="text-amber-800 dark:text-amber-300">· {{ c.pastas_sem_revisar }} pasta(s) nova(s)</span>
      </div>
      <div v-if="lojasSemCaixa.length" class="text-amber-800 dark:text-amber-300">
        {{ lojasSemCaixa.length }} loja(s) com e-mail no cadastro que nenhuma caixa lida recebe.
        <button type="button" class="underline" @click="verLojasSemCaixa = !verLojasSemCaixa">{{ verLojasSemCaixa ? 'esconder' : 'ver' }}</button>
        <ul v-if="verLojasSemCaixa" class="mt-0.5 list-inside list-disc text-muted-foreground">
          <li v-for="l in lojasSemCaixa" :key="l.store_info_id">{{ plataformaInfo(l.plataforma).curto }} {{ l.nome }}<template v-if="l.endereco"> · {{ l.endereco }}</template></li>
        </ul>
      </div>
      <div v-if="lojasSemIntegracao.length" class="text-muted-foreground" data-lojas-sem-integracao>
        {{ lojasSemIntegracao.length }} loja(s) do cadastro sem integração (nem ligada nem com o mesmo nome): o e-mail delas entra na conversa da loja, sem a API. Ligue em Cadastros › Lojas.
        <button type="button" class="underline" @click="verLojasSemIntegracao = !verLojasSemIntegracao">{{ verLojasSemIntegracao ? 'esconder' : 'ver' }}</button>
        <ul v-if="verLojasSemIntegracao" class="mt-0.5 list-inside list-disc">
          <li v-for="l in lojasSemIntegracao" :key="l.store_info_id">{{ plataformaInfo(l.plataforma).curto }} {{ l.nome }}<template v-if="l.endereco"> · {{ l.endereco }}</template></li>
        </ul>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-1 border-b pb-1" role="tablist" aria-label="Filas de e-mail">
      <button
        v-for="f in FILAS"
        :key="f.value"
        type="button"
        role="tab"
        :aria-selected="fila === f.value"
        class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs"
        :class="fila === f.value ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted/60'"
        :title="f.dica"
        :data-fila="f.value"
        @click="fila = f.value"
      >
        {{ f.label }}
        <span v-if="contagens[f.value]" class="rounded-full bg-background px-1.5 text-[10px] tabular-nums ring-1 ring-border">{{ contagens[f.value] }}</span>
      </button>
      <button type="button" class="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" :disabled="carregando" @click="atualizar">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregando }" /> atualizar
      </button>
    </div>

    <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>{{ filaInfo.dica }}</span>
      <button v-if="acoes.reprocessarFila" type="button" class="ml-auto inline-flex items-center gap-1 rounded border px-2 py-0.5 text-foreground hover:bg-muted" :disabled="!!ocupado" title="depois de corrigir o cadastro (Cadastros › Lojas), passa a fila de novo pela ponte" @click="reprocessarFila">
        <RotateCcw class="size-3.5" /> reprocessar a fila
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300">{{ erro }}</div>

    <!-- revisar envio -->
    <template v-if="fila === 'revisar_envio'">
      <div v-if="carregando && !envios.length" class="flex justify-center py-8"><Loader2 class="size-5 animate-spin" /></div>
      <div v-else-if="!envios.length" class="py-8 text-center text-xs text-muted-foreground">Nenhuma resposta esperando conferência.</div>
      <ul v-else class="space-y-2">
        <li v-for="v in envios" :key="v.id" class="space-y-1 rounded-lg border border-amber-500/40 p-2.5 text-xs" :data-envio="v.id">
          <div class="font-medium">{{ v.lease_vencido ? 'O Mac pegou esta resposta e sumiu (há mais de 15 min): pode ter saído — confira nos Enviados do Tuta e marque.' : 'Pode ter saído — confira nos Enviados do Tuta e marque.' }}</div>
          <div class="break-all">{{ v.de || '?' }} → {{ v.para || '?' }}</div>
          <div class="text-muted-foreground">{{ fmtDataHora(v.criado_em) }}<template v-if="v.codigo"> · {{ v.codigo }}</template></div>
          <div class="flex flex-wrap items-center gap-1.5 pt-0.5">
            <button v-if="v.conversa_id" type="button" class="rounded border px-2 py-0.5 hover:bg-muted" @click="emit('abrirConversa', v.conversa_id)">abrir a conversa</button>
            <button type="button" class="rounded border px-2 py-0.5 text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300" :disabled="!!ocupado" data-resolver-saiu @click="resolver(v, true)">Saiu</button>
            <button type="button" class="rounded border px-2 py-0.5 text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300" :disabled="!!ocupado" data-resolver-nao-saiu @click="resolver(v, false)">Não saiu</button>
            <Loader2 v-if="ocupado === `env:${v.id}`" class="size-3.5 animate-spin" />
          </div>
        </li>
      </ul>
    </template>

    <!-- as filas de e-mail -->
    <template v-else>
      <div v-if="carregando && !itens.length" class="flex justify-center py-8"><Loader2 class="size-5 animate-spin" /></div>
      <div v-else-if="!itens.length" class="py-8 text-center text-xs text-muted-foreground">Nada nesta fila.</div>
      <ul v-else class="space-y-2">
        <li v-for="e in itens" :key="e.id" class="space-y-1 rounded-lg border p-2.5 text-xs" :data-email-fila="e.id">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="rounded px-1.5 py-px text-[11px]" :class="e.destaque ? 'bg-red-500/15 text-red-700 dark:text-red-300' : 'bg-muted text-muted-foreground'">{{ e.pasta || 'sem pasta' }}</span>
            <span class="text-muted-foreground">{{ fmtDataHora(e.recebido_em) }}</span>
            <span v-if="e.loja" class="text-muted-foreground">· {{ plataformaInfo(e.plataforma).curto }} {{ e.loja }}</span>
            <span v-if="e.protocolo" class="rounded bg-teal-500/15 px-1.5 py-px text-[11px] text-teal-800 dark:text-teal-300">{{ e.protocolo }}</span>
            <ShieldAlert v-if="e.suspeito" class="size-3.5 text-red-600" aria-label="remetente pode ser falso" />
            <a v-if="e.abrir_no_tuta" :href="e.abrir_no_tuta" target="_blank" rel="noopener noreferrer" class="ml-auto inline-flex items-center gap-0.5 underline" title="abre no Tuta (lá fica lido)"><ExternalLink class="size-3" />Abrir no Tuta</a>
          </div>
          <div class="break-words font-medium">{{ e.assunto || '(sem assunto)' }}</div>
          <div class="break-all text-muted-foreground">De {{ e.de || '—' }} · para {{ destinoDaLoja(e.alias, e.loja) }}</div>
          <div v-if="e.motivo_texto" class="flex items-start gap-1 text-amber-800 dark:text-amber-300"><MailWarning class="mt-px size-3.5 shrink-0" />{{ e.motivo_texto }}</div>
          <div v-for="a in e.alertas" :key="a.codigo" class="flex items-start gap-1 text-amber-800 dark:text-amber-300"><TriangleAlert class="mt-px size-3 shrink-0" />{{ a.texto }}</div>
          <div v-if="e.suspeito && e.suspeito_motivos?.length" class="text-red-700 dark:text-red-300">Pode ser golpe: {{ e.suspeito_motivos.join('; ') }}.</div>

          <!-- sem loja: escolher a loja -->
          <div v-if="acoes.escolherLoja" class="flex flex-wrap items-center gap-1.5 pt-1">
            <button
              v-for="s in sugestoesUteis(e)"
              :key="s.store_info_id || s.marca_id || ''"
              type="button"
              class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50"
              :class="s.sugestao ? 'border-dashed' : ''"
              :title="s.marca_id ? 'o domínio está em mais de uma marca: confirme a marca' : s.sugestao ? 'sugestão pelo remetente — confirme que é esta' : 'a loja do endereço que recebeu'"
              :disabled="!!ocupado"
              data-sugestao-loja
              @click="escolherLoja(e, corpoDaSugestao(s))"
            >{{ rotuloDaSugestao(s, curto) }}</button>
            <select v-model="lojaEscolhida[e.id]" class="h-7 rounded border bg-background px-1.5" aria-label="escolher outra loja">
              <option value="">outra loja…</option>
              <option v-for="l in lojasParaEscolher(lojas, e.plataforma)" :key="l.integration_id || ''" :value="l.integration_id || ''">{{ plataformaInfo(l.plataforma).curto }} · {{ l.conta }}</option>
            </select>
            <button type="button" class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="!lojaEscolhida[e.id] || !!ocupado" @click="escolherLoja(e, { integration_id: lojaEscolhida[e.id] })">ligar</button>
          </div>

          <!-- sem vínculo: vincular ao pedido -->
          <div v-if="acoes.vincular && e.conversa_id" class="flex flex-wrap items-center gap-1.5 pt-1">
            <button
              v-for="p in pedidosQueExistem(e)"
              :key="p"
              type="button"
              class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50"
              title="o pedido citado no e-mail, que existe nesta loja"
              :disabled="!!ocupado"
              @click="vincular(e, p)"
            >pedido {{ p }}</button>
            <input v-model="pedidoDigitado[e.id]" class="h-7 w-48 rounded border bg-background px-1.5" placeholder="nº do pedido na plataforma" aria-label="número do pedido" />
            <button type="button" class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="!pedidoDigitado[e.id] || !!ocupado" @click="vincular(e, pedidoDigitado[e.id])">vincular ao pedido</button>
          </div>

          <div class="flex flex-wrap items-center gap-2 pt-1">
            <button type="button" class="inline-flex items-center gap-0.5 underline" :aria-expanded="aberto === e.id" @click="abrir(e)">
              <component :is="aberto === e.id ? ChevronUp : ChevronDown" class="size-3" />{{ aberto === e.id ? 'fechar o e-mail' : 'ver o e-mail' }}
            </button>
            <button v-if="e.conversa_id" type="button" class="underline" @click="emit('abrirConversa', e.conversa_id)">abrir a conversa</button>
            <button v-if="acoes.reprocessar" type="button" class="underline disabled:opacity-50" :disabled="!!ocupado" title="de novo pela ponte (depois de corrigir o cadastro)" @click="reprocessar(e)">reprocessar</button>
            <button v-if="acoes.ignorar" type="button" class="ml-auto text-muted-foreground underline disabled:opacity-50" :disabled="!!ocupado" @click="ignorar(e)">{{ fila === 'sem_loja' ? 'não é de loja nenhuma' : 'tirar da fila' }}</button>
          </div>

          <div v-if="aberto === e.id" class="space-y-1.5 rounded-md border bg-background p-2" data-email-fila-aberto>
            <div v-if="!detalhe" class="flex justify-center py-3"><Loader2 class="size-4 animate-spin" /></div>
            <template v-else>
              <div class="break-all text-[11px]"><span class="text-muted-foreground">De</span> {{ remetenteLegivel(detalhe) }}</div>
              <div v-if="detalhe.reply_to?.length" class="text-amber-800 dark:text-amber-300">Pede a resposta para outro endereço: {{ detalhe.reply_to.join(', ') }}</div>
              <div v-for="(p, i) in avisosDeProtecao(detalhe)" :key="i" class="text-[11px] text-muted-foreground">{{ p }}</div>
              <pre class="max-h-[320px] overflow-y-auto whitespace-pre-wrap break-words font-sans text-xs leading-relaxed">{{ detalhe.texto || '(e-mail sem texto)' }}</pre>
              <div v-if="detalhe.anexos?.length" class="space-y-0.5">
                <a v-for="an in detalhe.anexos" :key="an.id" :href="url(urlDoAnexo(an.id))" class="flex max-w-full items-center gap-1 underline" rel="noopener noreferrer">
                  <Download class="size-3 shrink-0" aria-hidden="true" /><span class="truncate">{{ an.filename || 'anexo' }}</span><span class="shrink-0 text-muted-foreground">{{ tamanhoLegivel(an.tamanho) }}</span>
                </a>
              </div>
            </template>
          </div>
        </li>
      </ul>
      <div v-if="proximo" class="flex justify-center">
        <button type="button" class="rounded border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50" :disabled="carregando" @click="carregar(true)">carregar mais</button>
      </div>
    </template>
  </div>
</template>
