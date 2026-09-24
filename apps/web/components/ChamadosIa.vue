<script setup lang="ts">
import { Bot, BookOpen, Check, Clock, ExternalLink, Loader2, Pencil, Plus, Power, RotateCcw, ThumbsDown, ThumbsUp, Trash2, Undo2, X } from 'lucide-vue-next'

// Aba Chamados › IA de Chamado (Vinicius 24/09: "essa aba é onde eu vou ensinar
// ele, e onde vai criando um manual — quando acontecer isso e isso você faz
// isso"). A IA (hoje o Hermes no Mac Santiago) lê o manual inteiro a cada
// passada. Aqui: liga/desliga (sem modo teste — ligada decide de verdade), o
// manual, e o que ela decidiu nos chamados — com ✓ acertou / ✗ errou (o ✗ leva a
// correção: ela refaz o chamado e aprende).

const props = defineProps<{ canEdit: boolean }>()
const { api } = useApi()

type Regra = {
  id: string
  quando: string
  faca: string
  plataforma: string | null
  ativa: boolean
  autor: string | null
  created_at: string
  updated_at: string
}
type Avaliacao = { certo: boolean; correcao: string | null; autor: string | null; quando: string }
type Decisao = {
  mensagem_id: string
  chamado_id: string
  pedido_bling: string | null
  plataforma: string | null
  conta: string | null
  quando: string
  texto: string
  avaliacao: Avaliacao | null
}
type Estado = {
  nome: string
  ligada: boolean
  exclusivo: boolean
  ultima_passada: string | null
  esperando: number
  regras: Regra[]
  decisoes: Decisao[]
}

const PLATAFORMAS = [
  { value: '', label: 'todas as plataformas' },
  { value: 'ml', label: 'Mercado Livre' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'amazon', label: 'Amazon' },
]
const nomePlataforma = (v: string | null) => PLATAFORMAS.find(p => p.value === (v || ''))?.label || v || ''

const estado = ref<Estado | null>(null)
const loading = ref(false)
const erro = ref<string | null>(null)
const salvando = ref(false)

function mensagemDeErro(e: any, padrao: string) {
  const d = e?.data?.detail
  if (typeof d === 'string') return d
  if (d?.code) return `${padrao} (${d.code})`
  return padrao
}

async function carregar() {
  loading.value = true
  erro.value = null
  try {
    estado.value = await api<Estado>('/api/chamados/ia')
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui carregar a IA de Chamado')
  } finally {
    loading.value = false
  }
}
onMounted(carregar)

// ─── liga / desliga ─────────────────────────────────────────────────────────
async function alternar() {
  if (!estado.value || !props.canEdit) return
  const ligar = !estado.value.ligada
  const aviso = ligar
    ? 'Ligar a IA de Chamado?\n\nA partir de agora ela lê o manual e decide de verdade nos chamados que chegarem.'
    : 'Desligar a IA de Chamado?\n\nEla para de receber e de decidir chamados até alguém religar. O manual fica guardado.'
  if (!confirm(aviso)) return
  salvando.value = true
  try {
    estado.value = await api<Estado>('/api/chamados/ia', { method: 'PATCH', body: { ligada: ligar } })
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui mudar')
  } finally {
    salvando.value = false
  }
}

// ─── manual ─────────────────────────────────────────────────────────────────
const novaAberta = ref(false)
const nova = reactive({ quando: '', faca: '', plataforma: '' })
function fecharNova() {
  novaAberta.value = false
  nova.quando = ''
  nova.faca = ''
  nova.plataforma = ''
}
async function criar() {
  if (!nova.quando.trim() || !nova.faca.trim()) return
  salvando.value = true
  try {
    const r = await api<Regra>('/api/chamados/ia/regras', {
      method: 'POST',
      body: { quando: nova.quando, faca: nova.faca, plataforma: nova.plataforma || null },
    })
    estado.value?.regras.push(r)
    fecharNova()
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui salvar a regra')
  } finally {
    salvando.value = false
  }
}

const editando = ref<string | null>(null)
const rascunho = reactive({ quando: '', faca: '', plataforma: '' })
function editar(r: Regra) {
  editando.value = r.id
  rascunho.quando = r.quando
  rascunho.faca = r.faca
  rascunho.plataforma = r.plataforma || ''
}
function trocar(r: Regra) {
  if (!estado.value) return
  const i = estado.value.regras.findIndex(x => x.id === r.id)
  if (i >= 0) estado.value.regras[i] = r
}
async function salvarEdicao(r: Regra) {
  if (!rascunho.quando.trim() || !rascunho.faca.trim()) return
  salvando.value = true
  try {
    trocar(await api<Regra>(`/api/chamados/ia/regras/${r.id}`, {
      method: 'PATCH',
      body: { quando: rascunho.quando, faca: rascunho.faca, plataforma: rascunho.plataforma || null },
    }))
    editando.value = null
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui salvar a regra')
  } finally {
    salvando.value = false
  }
}
async function alternarRegra(r: Regra) {
  salvando.value = true
  try {
    trocar(await api<Regra>(`/api/chamados/ia/regras/${r.id}`, { method: 'PATCH', body: { ativa: !r.ativa } }))
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui mudar a regra')
  } finally {
    salvando.value = false
  }
}
async function apagar(r: Regra) {
  if (!confirm(`Apagar a regra?\n\nQuando: ${r.quando}\nFaça: ${r.faca}\n\nSe for só por um tempo, use "desativar" — ela fica guardada.`)) return
  salvando.value = true
  try {
    await api(`/api/chamados/ia/regras/${r.id}`, { method: 'DELETE' })
    if (estado.value) estado.value.regras = estado.value.regras.filter(x => x.id !== r.id)
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui apagar a regra')
  } finally {
    salvando.value = false
  }
}

// ─── ✓ acertou / ✗ errou ────────────────────────────────────────────────────
// 24/09 (Vinicius: "como eu faço pra dizer: nesse você errou, nesse acertou").
const corrigindo = ref<string | null>(null)
const correcao = ref('')
const avaliando = ref<string | null>(null)
function trocarDecisao(d: Decisao) {
  if (!estado.value) return
  const i = estado.value.decisoes.findIndex(x => x.mensagem_id === d.mensagem_id)
  if (i >= 0) estado.value.decisoes[i] = d
}
async function avaliar(d: Decisao, certo: boolean) {
  if (!certo && !correcao.value.trim()) return
  avaliando.value = d.mensagem_id
  try {
    trocarDecisao(await api<Decisao>(`/api/chamados/ia/decisoes/${d.mensagem_id}/avaliacao`, {
      method: 'PUT',
      body: certo ? { certo: true } : { certo: false, correcao: correcao.value },
    }))
    corrigindo.value = null
    correcao.value = ''
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui salvar a avaliação')
  } finally {
    avaliando.value = null
  }
}
function abrirCorrecao(d: Decisao) {
  corrigindo.value = d.mensagem_id
  correcao.value = d.avaliacao?.certo === false ? (d.avaliacao.correcao || '') : ''
}
async function desfazerAvaliacao(d: Decisao) {
  avaliando.value = d.mensagem_id
  try {
    trocarDecisao(await api<Decisao>(`/api/chamados/ia/decisoes/${d.mensagem_id}/avaliacao`, { method: 'DELETE' }))
  } catch (e: any) {
    erro.value = mensagemDeErro(e, 'Não consegui desfazer')
  } finally {
    avaliando.value = null
  }
}
// "virar regra": abre a nova regra já preenchida com a situação e a correção.
const manualRef = ref<HTMLElement | null>(null)
function plataformaDaRegra(p: string | null) {
  const v = (p || '').toLowerCase()
  if (['ml', 'mercado livre', 'mercadolivre', 'meli'].includes(v)) return 'ml'
  return PLATAFORMAS.some(x => x.value === v) ? v : ''
}
function virarRegra(d: Decisao) {
  const situacao = resumoDe(d.texto)
  nova.quando = situacao.length > 400 ? `${situacao.slice(0, 400)}…` : situacao
  nova.faca = d.avaliacao?.correcao || ''
  nova.plataforma = plataformaDaRegra(d.plataforma)
  novaAberta.value = true
  nextTick(() => manualRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}
function linkDoPedido(pedido: string) {
  return `/chamados?search=${encodeURIComponent(pedido)}`
}

// ─── resumo e decisões ──────────────────────────────────────────────────────
const regrasAtivas = computed(() => estado.value?.regras.filter(r => r.ativa).length ?? 0)

function haQuanto(iso: string | null) {
  if (!iso) return 'nunca'
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'agora'
  if (min < 60) return `há ${min} min`
  const h = Math.round(min / 60)
  if (h < 48) return `há ${h} h`
  return `há ${Math.round(h / 24)} dias`
}
// Ligada e sem passar há mais de 20 min: a máquina dela pode ter parado.
const parada = computed(() => {
  const e = estado.value
  if (!e?.ligada) return false
  if (!e.ultima_passada) return true
  return Date.now() - new Date(e.ultima_passada).getTime() > 20 * 60000
})

function fmtQuando(v: string) {
  return new Date(v).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// O texto da análise é "Análise da IA de Chamado [classe]: resumo → ação".
const ACOES: { fim: string; label: string; cls: string }[] = [
  { fim: 'precisa de humano', label: 'chamou humano', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  { fim: 'réplica enfileirada pro robô', label: 'respondeu', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
  { fim: 'aguardar a plataforma', label: 'esperar', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' },
  { fim: 'robô sugere fechar', label: 'sugeriu fechar', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300' },
]
function acaoDe(texto: string) {
  return ACOES.find(a => texto.trim().endsWith(a.fim)) || null
}
function resumoDe(texto: string) {
  let t = texto.replace(/^Análise d[aoe] [^[]*\[[^\]]*\]:\s*/, '')
  const a = acaoDe(t)
  if (a) t = t.slice(0, t.lastIndexOf('→')).trim()
  return t
}
</script>

<template>
  <div class="space-y-5">
    <div v-if="erro" class="flex items-center justify-between gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-500">
      <span>{{ erro }}</span>
      <button type="button" class="text-xs underline" @click="erro = null">fechar</button>
    </div>

    <div v-if="loading && !estado" class="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 class="size-4 animate-spin" /> carregando…
    </div>

    <template v-if="estado">
      <!-- situação -->
      <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <div class="rounded-lg border bg-card px-3 py-2 flex items-center justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              <Bot class="size-3.5" /> {{ estado.nome }}
            </div>
            <div class="mt-0.5 flex items-center gap-1.5 text-lg font-semibold leading-6">
              <span class="size-2.5 rounded-full" :class="estado.ligada ? 'bg-emerald-500' : 'bg-gray-400'" />
              {{ estado.ligada ? 'Ligada' : 'Desligada' }}
            </div>
          </div>
          <Button size="sm" :variant="estado.ligada ? 'outline' : 'default'" :disabled="!canEdit || salvando" @click="alternar">
            <Loader2 v-if="salvando" class="size-4 mr-1.5 animate-spin" />
            <Power v-else class="size-4 mr-1.5" />
            {{ estado.ligada ? 'desligar' : 'ligar' }}
          </Button>
        </div>
        <StatCard label="Esperando a IA" :value="estado.esperando" :icon="Clock" :tone="estado.ligada ? 'default' : 'warning'" hint="resposta nova da plataforma, instrução sua ou envio travado" compact />
        <StatCard
          label="Última passada"
          :value="haQuanto(estado.ultima_passada)"
          :icon="RotateCcw"
          :tone="parada ? 'danger' : 'default'"
          :hint="parada ? 'ligada, mas não passa há mais de 20 min — a máquina dela pode ter parado' : 'ela passa sozinha de tempos em tempos'"
          compact
        />
        <StatCard label="Regras no manual" :value="regrasAtivas" :icon="BookOpen" :hint="`${estado.regras.length - regrasAtivas} desativada(s)`" compact />
      </div>

      <!-- manual -->
      <section ref="manualRef" class="rounded-lg border bg-card scroll-mt-4">
        <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2">
          <BookOpen class="size-4 text-muted-foreground" />
          <h2 class="text-sm font-semibold">Manual</h2>
          <span class="text-xs text-muted-foreground">
            A IA lê o manual inteiro a cada passada. Escreva como falaria com uma pessoa: <b>quando</b> acontecer isso, <b>faça</b> isso.
            Pra um caso só, use a instrução dentro do histórico do chamado.
          </span>
          <Button v-if="canEdit && !novaAberta" size="sm" class="ml-auto" @click="novaAberta = true">
            <Plus class="size-4 mr-1.5" /> nova regra
          </Button>
        </div>

        <div v-if="novaAberta" class="grid gap-2 border-b bg-muted/30 px-3 py-3 lg:grid-cols-[1fr_1fr_180px_auto]">
          <label class="space-y-1">
            <span class="text-[11px] font-medium text-muted-foreground">Quando acontecer…</span>
            <textarea v-model="nova.quando" rows="3" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="ex.: a Shopee pedir prova (vídeo ou foto) numa devolução" />
          </label>
          <label class="space-y-1">
            <span class="text-[11px] font-medium text-muted-foreground">Faça…</span>
            <textarea v-model="nova.faca" rows="3" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="ex.: responder com o vídeo da embalagem; sem vídeo, chamar humano" />
          </label>
          <label class="space-y-1">
            <span class="text-[11px] font-medium text-muted-foreground">Vale pra</span>
            <select v-model="nova.plataforma" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
              <option v-for="p in PLATAFORMAS" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </label>
          <div class="flex items-end gap-1.5">
            <Button size="sm" :disabled="salvando || !nova.quando.trim() || !nova.faca.trim()" @click="criar">
              <Check class="size-4 mr-1.5" /> salvar
            </Button>
            <Button size="sm" variant="ghost" @click="fecharNova"><X class="size-4" /></Button>
          </div>
        </div>

        <div v-if="!estado.regras.length && !novaAberta" class="px-3 py-6 text-center text-sm text-muted-foreground">
          Nenhuma regra ainda. Comece pela situação que mais aparece nos chamados.
        </div>

        <ol class="divide-y">
          <li v-for="(r, i) in estado.regras" :key="r.id" class="px-3 py-2.5" :class="r.ativa ? '' : 'opacity-60'">
            <div v-if="editando === r.id" class="grid gap-2 lg:grid-cols-[1fr_1fr_180px_auto]">
              <textarea v-model="rascunho.quando" rows="3" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
              <textarea v-model="rascunho.faca" rows="3" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
              <select v-model="rascunho.plataforma" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
                <option v-for="p in PLATAFORMAS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
              <div class="flex items-start gap-1.5">
                <Button size="sm" :disabled="salvando || !rascunho.quando.trim() || !rascunho.faca.trim()" @click="salvarEdicao(r)">
                  <Check class="size-4 mr-1.5" /> salvar
                </Button>
                <Button size="sm" variant="ghost" @click="editando = null"><X class="size-4" /></Button>
              </div>
            </div>
            <div v-else class="flex items-start gap-3">
              <span class="mt-0.5 w-5 shrink-0 text-right text-xs font-semibold tabular-nums text-muted-foreground">{{ i + 1 }}</span>
              <div class="min-w-0 flex-1 grid gap-1 lg:grid-cols-2 lg:gap-4">
                <div class="text-sm whitespace-pre-line"><span class="text-[11px] font-semibold uppercase text-muted-foreground">Quando </span>{{ r.quando }}</div>
                <div class="text-sm whitespace-pre-line"><span class="text-[11px] font-semibold uppercase text-muted-foreground">Faça </span>{{ r.faca }}</div>
              </div>
              <div class="flex shrink-0 flex-col items-end gap-1">
                <div class="flex items-center gap-1">
                  <span class="rounded-full bg-muted px-2 py-0.5 text-[11px]">{{ r.plataforma ? nomePlataforma(r.plataforma) : 'todas' }}</span>
                  <span v-if="!r.ativa" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground">desativada</span>
                </div>
                <div v-if="canEdit" class="flex items-center gap-0.5">
                  <Button size="sm" variant="ghost" class="h-7 px-2 text-xs" :disabled="salvando" @click="alternarRegra(r)">{{ r.ativa ? 'desativar' : 'ativar' }}</Button>
                  <Button size="sm" variant="ghost" class="h-7 px-2" title="editar" @click="editar(r)"><Pencil class="size-3.5" /></Button>
                  <Button size="sm" variant="ghost" class="h-7 px-2 text-red-600" title="apagar" :disabled="salvando" @click="apagar(r)"><Trash2 class="size-3.5" /></Button>
                </div>
                <span v-if="r.autor" class="text-[10px] text-muted-foreground">{{ r.autor }} · {{ fmtQuando(r.updated_at) }}</span>
              </div>
            </div>
          </li>
        </ol>
      </section>

      <!-- o que ela decidiu -->
      <section class="rounded-lg border bg-card">
        <div class="flex items-center gap-2 border-b px-3 py-2">
          <Bot class="size-4 text-muted-foreground" />
          <h2 class="text-sm font-semibold">O que ela decidiu</h2>
          <span class="text-xs text-muted-foreground">marque <b>acertou</b> ou <b>errou</b> — no errou, diga o que era o certo: ela refaz o chamado e aprende</span>
          <Button size="sm" variant="outline" class="ml-auto" :disabled="loading" @click="carregar">
            <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" /> atualizar
          </Button>
        </div>
        <div v-if="!estado.decisoes.length" class="px-3 py-6 text-center text-sm text-muted-foreground">
          Ela ainda não decidiu nada.
        </div>
        <table v-else class="w-full text-xs">
          <tbody class="divide-y">
            <template v-for="d in estado.decisoes" :key="d.mensagem_id">
            <tr class="align-top" :class="d.avaliacao ? (d.avaliacao.certo ? 'bg-emerald-500/[0.04]' : 'bg-red-500/[0.04]') : ''">
              <td class="whitespace-nowrap px-3 py-2 text-muted-foreground tabular-nums">{{ fmtQuando(d.quando) }}</td>
              <td class="whitespace-nowrap px-2 py-2">
                <!-- 24/09 (Vinicius): abre em OUTRA aba — a tela da IA fica onde está -->
                <a v-if="d.pedido_bling" :href="linkDoPedido(d.pedido_bling)" target="_blank" rel="noopener" class="inline-flex items-center gap-1 font-mono underline decoration-dotted hover:text-primary" title="abrir o chamado em outra aba">
                  {{ d.pedido_bling }} <ExternalLink class="size-3 opacity-60" />
                </a>
                <div class="text-[11px] text-muted-foreground">{{ [nomePlataforma(d.plataforma), d.conta].filter(Boolean).join(' · ') }}</div>
              </td>
              <td class="whitespace-nowrap px-2 py-2">
                <span v-if="acaoDe(d.texto)" class="rounded-full px-2 py-0.5 text-[11px] font-medium" :class="acaoDe(d.texto)!.cls">{{ acaoDe(d.texto)!.label }}</span>
              </td>
              <td class="px-2 py-2 text-sm">
                {{ resumoDe(d.texto) }}
                <div v-if="d.avaliacao && !d.avaliacao.certo" class="mt-1.5 rounded border border-red-500/30 bg-red-500/5 px-2 py-1 text-xs">
                  <span class="font-semibold text-red-700 dark:text-red-300">O certo era:</span> {{ d.avaliacao.correcao }}
                  <span class="text-muted-foreground"> — {{ d.avaliacao.autor }}</span>
                </div>
              </td>
              <td class="whitespace-nowrap px-3 py-2 text-right">
                <Loader2 v-if="avaliando === d.mensagem_id" class="ml-auto size-4 animate-spin text-muted-foreground" />
                <template v-else-if="d.avaliacao">
                  <span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium" :class="d.avaliacao.certo ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-red-500/15 text-red-700 dark:text-red-300'">
                    <component :is="d.avaliacao.certo ? ThumbsUp : ThumbsDown" class="size-3" />
                    {{ d.avaliacao.certo ? 'acertou' : 'errou' }}
                  </span>
                  <div v-if="canEdit" class="mt-1 flex justify-end gap-0.5">
                    <Button v-if="!d.avaliacao.certo" size="sm" variant="ghost" class="h-6 px-1.5 text-[11px]" title="criar uma regra no manual a partir desta correção" @click="virarRegra(d)">virar regra</Button>
                    <Button size="sm" variant="ghost" class="h-6 px-1.5" title="desfazer a avaliação" @click="desfazerAvaliacao(d)"><Undo2 class="size-3" /></Button>
                  </div>
                </template>
                <div v-else-if="canEdit" class="flex justify-end gap-1">
                  <Button size="sm" variant="outline" class="h-7 px-2 text-xs text-emerald-700 dark:text-emerald-300" title="ela acertou" @click="avaliar(d, true)">
                    <ThumbsUp class="size-3.5 mr-1" /> acertou
                  </Button>
                  <Button size="sm" variant="outline" class="h-7 px-2 text-xs text-red-700 dark:text-red-300" title="ela errou — dizer o que era o certo" @click="abrirCorrecao(d)">
                    <ThumbsDown class="size-3.5 mr-1" /> errou
                  </Button>
                </div>
              </td>
            </tr>
            <tr v-if="corrigindo === d.mensagem_id">
              <td colspan="5" class="bg-red-500/[0.04] px-3 pb-3 pt-1">
                <div class="flex flex-wrap items-end gap-2">
                  <label class="min-w-[320px] flex-1 space-y-1">
                    <span class="text-[11px] font-medium text-muted-foreground">O que era o certo? — ela refaz este chamado na próxima passada (até 5 min) e guarda como aprendizado</span>
                    <textarea v-model="correcao" rows="3" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="ex.: não era pra esperar — responder pedindo a devolução do valor, citando o rastreio entregue" />
                  </label>
                  <Button size="sm" :disabled="!correcao.trim() || avaliando === d.mensagem_id" @click="avaliar(d, false)">
                    <Check class="size-4 mr-1.5" /> salvar correção
                  </Button>
                  <Button size="sm" variant="ghost" @click="corrigindo = null"><X class="size-4" /></Button>
                </div>
              </td>
            </tr>
            </template>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>
