<script lang="ts">
// Aba "Banco de respostas" do Atendimento (13/11/2026, o Eduardo) — SÓ ADMIN: a página só
// mostra a aba para admin (`abasDe`, pages/atendimento.vue) e a API recusa os outros com 403
// `so_admin` (apps/api/app/routers/atendimento_banco.py). Duas partes:
// - "Revisão do dia": o que a IA mandou SOZINHA no dia — todos os SENSÍVEIS (reembolso,
//   cancelamento, troca/devolução e reclamação forte: a régua de 85%) e uma amostra de até 20
//   dos outros, sempre a mesma no mesmo dia —, com a pergunta do cliente (o turno dele logo
//   antes do envio) e o que saiu. 👍 "certo" conta acerto na régua do assunto; 👎 "errou" pede
//   "como seria o certo" (vem com o texto da IA para ajustar) e a correção ENTRA NO BANCO como
//   resposta ajustada por pessoa — a tela diz se entrou e, se não, por quê. Revisar de novo
//   troca a nota; o 👍 depois do 👎 tira a correção do banco. Quem não mexe na caixa (a fase de
//   observação) só vê.
// - "Banco": a busca nas respostas da equipe que o agente consulta (texto, assunto, loja).
// A % da régua por assunto fica na aba Métricas (por equipe), não aqui.

export const ROTAS = {
  revisao: '/api/atendimento/banco/revisao',
  revisar: (rascunhoId: string) => `/api/atendimento/banco/revisao/${encodeURIComponent(rascunhoId)}`,
  respostas: '/api/atendimento/banco/respostas',
}
// = MAX_CHARS_CORRECAO de apps/api/app/schemas/atendimento_banco.py (o teste confere).
export const MAX_CORRECAO = 4000
export const LIMITE_BANCO = 50
export const LIMITE_BANCO_MAX = 200

export type NotaRevisao = { nota: string; correcao: string | null; por: string | null; em: string | null; no_banco: boolean | null }
export type ItemRevisao = {
  rascunho_id: string
  conversa_id: string
  integration_id: string | null
  loja: string | null
  plataforma: string
  canal: string
  assunto: string | null
  assunto_nome: string
  sensivel: boolean
  pergunta: string | null
  resposta: string | null
  com_anexo: boolean
  enviada_em: string | null
  avaliacao: NotaRevisao | null
}
export type ContagensRevisao = { total: number; sensiveis: number; revisados: number; erros: number; mostrados: number }
export type Revisao = { dia: string; hoje: string; amostra: number; contagens: ContagensRevisao; itens: ItemRevisao[] }
export type BancoDaCorrecao = { entrou: boolean; motivo: string | null; frase: string | null; retirada: boolean }
export type RevisaoSalva = { item: ItemRevisao; banco: BancoDaCorrecao }
export type RespostaBanco = {
  id: number
  tipo: string
  tipo_nome: string
  integration_id: string | null
  loja: string | null
  plataforma: string
  canal: string
  modelos: string[]
  pergunta: string
  resposta: string
  fonte: string
  da_revisao: boolean
  respondido_em: string
  conversa_id: string | null
}
export type RespostasBanco = {
  total: number
  itens: RespostaBanco[]
  tipos: { tipo: string; nome: string; n: number }[]
  lojas: { integration_id: string; plataforma: string; loja: string; n: number }[]
}

// De onde veio a resposta do banco (`banco_respostas.FONTE_*`).
export const FONTES: Record<string, string> = {
  equipe_fora: 'equipe, por fora (Duoke/Seller Center)',
  equipe_escreveu: 'equipe, no DaVinci',
  equipe_ajustou: 'equipe ajustou o rascunho da IA',
  equipe_aprovou: 'equipe mandou o rascunho da IA sem mudar',
}
export function fonteLabel(r: Pick<RespostaBanco, 'fonte' | 'da_revisao'>): string {
  if (r.da_revisao) return 'correção da revisão diária'
  return FONTES[r.fonte] || r.fonte
}

// "AAAA-MM-DD" + n dias, sem fuso (o dia é o de São Paulo, que o servidor decide).
export function somarDias(dia: string, n: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dia || '')
  if (!m) return dia
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + n)).toISOString().slice(0, 10)
}
export function fmtDia(dia: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dia || '')
  return m ? `${m[3]}/${m[2]}/${m[1]}` : dia
}

export type FiltroRevisao = 'todos' | 'faltam' | 'sensiveis' | 'erros'
export const FILTROS_REVISAO: { value: FiltroRevisao; label: string }[] = [
  { value: 'todos', label: 'Todos' },
  { value: 'faltam', label: 'Falta revisar' },
  { value: 'sensiveis', label: 'Sensíveis' },
  { value: 'erros', label: 'Errou' },
]
export function filtrarRevisao(itens: ItemRevisao[], filtro: FiltroRevisao): ItemRevisao[] {
  if (filtro === 'faltam') return itens.filter((i) => !i.avaliacao)
  if (filtro === 'sensiveis') return itens.filter((i) => i.sensivel)
  if (filtro === 'erros') return itens.filter((i) => i.avaliacao?.nota === 'erro')
  return itens
}

// As contagens do dia depois de uma revisão (a nota de antes sai, a nova entra).
export function contagensDepois(c: ContagensRevisao, antes: NotaRevisao | null, depois: NotaRevisao | null): ContagensRevisao {
  const revisado = (n: NotaRevisao | null) => (n ? 1 : 0)
  const errou = (n: NotaRevisao | null) => (n?.nota === 'erro' ? 1 : 0)
  return {
    ...c,
    revisados: c.revisados - revisado(antes) + revisado(depois),
    erros: c.erros - errou(antes) + errou(depois),
  }
}

// O aviso depois de salvar: o que a revisão fez no banco.
export function avisoDoBanco(nota: 'ok' | 'erro', b: BancoDaCorrecao | null | undefined): { tipo: 'success' | 'warning'; titulo: string; linhas: string[] } {
  if (nota === 'ok') return { tipo: 'success', titulo: 'Marcado como certo', linhas: b?.retirada ? ['A correção que estava no banco saiu.'] : [] }
  if (b?.entrou) return { tipo: 'success', titulo: 'Correção salva — entrou no banco de respostas', linhas: ['O agente passa a consultá-la nas perguntas parecidas.'] }
  return { tipo: 'warning', titulo: 'Correção salva, mas não entrou no banco', linhas: [b?.frase || 'Ela não passou nas regras do banco.'] }
}

// O 👎 só salva com a correção escrita (até 4.000) e diferente do que a IA mandou.
export function correcaoPronta(texto: string | null | undefined, enviado: string | null | undefined): boolean {
  const t = (texto || '').trim()
  if (!t || t.length > MAX_CORRECAO) return false
  return t.split(/\s+/).join(' ') !== (enviado || '').trim().split(/\s+/).join(' ')
}
</script>

<script setup lang="ts">
import { Bot, ChevronLeft, ChevronRight, ClipboardCheck, ExternalLink, Library, Loader2, Paperclip, Pencil, RotateCcw, Search, ShieldAlert, ThumbsDown, ThumbsUp, X } from 'lucide-vue-next'
import { canalLabel, erroDaApi, fmtDataHora, fmtHora } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ podeRevisar: boolean }>()
const { api } = useApi()
const toasts = useToasts()

type Parte = 'revisao' | 'banco'
const PARTES: { value: Parte; label: string; icon: any }[] = [
  { value: 'revisao', label: 'Revisão do dia', icon: ClipboardCheck },
  { value: 'banco', label: 'Banco', icon: Library },
]
const parte = ref<Parte>('revisao')

// ─── revisão do dia ─────────────────────────────────────────────────────────
// `dia` vazio = hoje (o dia de São Paulo, que o servidor decide e devolve em `hoje`).
const dia = ref('')
const revisao = ref<Revisao | null>(null)
const carregandoRevisao = ref(false)
const erroRevisao = ref<string | null>(null)
const filtro = ref<FiltroRevisao>('todos')
// rascunho_id → o texto da correção aberta (o 👎 em andamento).
const corrigindo = reactive<Record<string, string>>({})
// Os já revisados que a pessoa reabriu ("mudar").
const mudando = reactive<Record<string, boolean>>({})
const salvando = ref<string | null>(null)

let geracaoRevisao = 0
async function carregarRevisao() {
  const g = ++geracaoRevisao
  carregandoRevisao.value = true
  erroRevisao.value = null
  try {
    const r = await api<Revisao>(ROTAS.revisao, { query: dia.value ? { dia: dia.value } : {} })
    if (g !== geracaoRevisao) return
    revisao.value = r
  } catch (e: any) {
    if (g !== geracaoRevisao) return
    erroRevisao.value = erroDaApi(e, 'Não consegui carregar a revisão do dia').texto
  } finally {
    if (g === geracaoRevisao) carregandoRevisao.value = false
  }
}
const diaMostrado = computed(() => dia.value || revisao.value?.dia || '')
const ehHoje = computed(() => !!revisao.value && diaMostrado.value === revisao.value.hoje)
function irPara(novo: string) {
  const hoje = revisao.value?.hoje || ''
  if (!/^\d{4}-\d{2}-\d{2}$/.test(novo) || (hoje && novo > hoje)) return
  dia.value = novo === hoje ? '' : novo
}
function limparEdicoes() {
  for (const k of Object.keys(corrigindo)) delete corrigindo[k]
  for (const k of Object.keys(mudando)) delete mudando[k]
}
watch(dia, () => {
  limparEdicoes()
  void carregarRevisao()
})
onMounted(() => void carregarRevisao())

const itens = computed(() => filtrarRevisao(revisao.value?.itens || [], filtro.value))
const contagens = computed(() => revisao.value?.contagens || null)

function abrirCorrecao(i: ItemRevisao) {
  corrigindo[i.rascunho_id] = i.avaliacao?.nota === 'erro' && i.avaliacao.correcao ? i.avaliacao.correcao : i.resposta || ''
}
function fecharCorrecao(i: ItemRevisao) {
  delete corrigindo[i.rascunho_id]
}
function mudar(i: ItemRevisao) {
  mudando[i.rascunho_id] = true
}
function desistir(i: ItemRevisao) {
  delete mudando[i.rascunho_id]
  delete corrigindo[i.rascunho_id]
}

async function revisar(i: ItemRevisao, nota: 'ok' | 'erro') {
  if (!props.podeRevisar || salvando.value) return
  const correcao = (corrigindo[i.rascunho_id] || '').trim()
  if (nota === 'erro' && !correcaoPronta(correcao, i.resposta)) return
  salvando.value = i.rascunho_id
  try {
    const r = await api<RevisaoSalva>(ROTAS.revisar(i.rascunho_id), {
      method: 'POST',
      body: nota === 'erro' ? { nota, correcao } : { nota },
    })
    const atual = revisao.value
    if (atual) {
      const antes = atual.itens.find((x) => x.rascunho_id === i.rascunho_id)?.avaliacao ?? null
      atual.itens = atual.itens.map((x) => (x.rascunho_id === i.rascunho_id ? r.item : x))
      atual.contagens = contagensDepois(atual.contagens, antes, r.item.avaliacao)
    }
    desistir(i)
    const aviso = avisoDoBanco(nota, r.banco)
    toasts[aviso.tipo](aviso.titulo, aviso.linhas)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui salvar a revisão')
    toasts.error(er.texto, er.motivos)
  } finally {
    salvando.value = null
  }
}

// ─── banco ──────────────────────────────────────────────────────────────────
const busca = reactive({ q: '', tipo: '', integration_id: '' })
const limite = ref(LIMITE_BANCO)
const respostas = ref<RespostasBanco | null>(null)
const carregandoBanco = ref(false)
const erroBanco = ref<string | null>(null)
let geracaoBanco = 0
async function carregarBanco() {
  const g = ++geracaoBanco
  carregandoBanco.value = true
  erroBanco.value = null
  try {
    const query: Record<string, string | number> = { limite: limite.value }
    if (busca.q.trim()) query.q = busca.q.trim()
    if (busca.tipo) query.tipo = busca.tipo
    if (busca.integration_id) query.integration_id = busca.integration_id
    const r = await api<RespostasBanco>(ROTAS.respostas, { query })
    if (g !== geracaoBanco) return
    respostas.value = r
  } catch (e: any) {
    if (g !== geracaoBanco) return
    erroBanco.value = erroDaApi(e, 'Não consegui carregar o banco de respostas').texto
  } finally {
    if (g === geracaoBanco) carregandoBanco.value = false
  }
}
// O banco abre na primeira vez que a pessoa vai até ele.
watch(parte, (p) => {
  if (p === 'banco' && !respostas.value && !carregandoBanco.value) void carregarBanco()
})
// A busca espera a pessoa parar de digitar; os seletores e o "mostrar mais" recarregam na hora.
let esperaBusca: ReturnType<typeof setTimeout> | null = null
watch(() => busca.q, () => {
  if (esperaBusca) clearTimeout(esperaBusca)
  esperaBusca = setTimeout(() => {
    limite.value = LIMITE_BANCO
    void carregarBanco()
  }, 400)
})
watch(() => [busca.tipo, busca.integration_id], () => {
  limite.value = LIMITE_BANCO
  void carregarBanco()
})
function mostrarMais() {
  limite.value = Math.min(LIMITE_BANCO_MAX, limite.value + LIMITE_BANCO)
  void carregarBanco()
}
onBeforeUnmount(() => {
  if (esperaBusca) clearTimeout(esperaBusca)
})
const podeMostrarMais = computed(() => !!respostas.value && respostas.value.itens.length < respostas.value.total && limite.value < LIMITE_BANCO_MAX)
</script>

<template>
  <section class="space-y-3" data-banco-respostas>
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex gap-1 rounded-md bg-muted/40 p-1" role="tablist" aria-label="partes do banco de respostas">
        <button
          v-for="p in PARTES"
          :key="p.value"
          type="button"
          role="tab"
          :aria-selected="parte === p.value"
          class="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors"
          :class="parte === p.value ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground hover:text-foreground'"
          @click="parte = p.value"
        >
          <component :is="p.icon" class="size-3.5" aria-hidden="true" />
          {{ p.label }}
        </button>
      </div>
      <span v-if="!podeRevisar" class="text-xs text-muted-foreground">Só quem cuida do Atendimento revisa — aqui você só vê.</span>
    </div>

    <!-- ─── revisão do dia ─── -->
    <div v-if="parte === 'revisao'" class="space-y-3" data-parte="revisao">
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex items-center gap-1">
          <Button size="sm" variant="outline" class="h-8 px-2" title="o dia anterior" aria-label="o dia anterior" :disabled="!diaMostrado || carregandoRevisao" @click="irPara(somarDias(diaMostrado, -1))">
            <ChevronLeft class="size-4" />
          </Button>
          <input
            type="date"
            class="h-8 rounded-md border bg-background px-2 text-xs"
            :value="diaMostrado"
            :max="revisao?.hoje || undefined"
            aria-label="o dia da revisão"
            @change="irPara(($event.target as HTMLInputElement).value)"
          />
          <Button size="sm" variant="outline" class="h-8 px-2" title="o dia seguinte" aria-label="o dia seguinte" :disabled="!diaMostrado || ehHoje || carregandoRevisao" @click="irPara(somarDias(diaMostrado, 1))">
            <ChevronRight class="size-4" />
          </Button>
          <Button v-if="revisao && !ehHoje" size="sm" variant="ghost" class="h-8 text-xs" @click="irPara(revisao.hoje)">hoje</Button>
        </div>
        <div class="flex gap-1 rounded-md bg-muted/40 p-1" role="tablist" aria-label="filtrar a revisão">
          <button
            v-for="f in FILTROS_REVISAO"
            :key="f.value"
            type="button"
            role="tab"
            :aria-selected="filtro === f.value"
            class="rounded px-2.5 py-1 text-xs transition-colors"
            :class="filtro === f.value ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'"
            @click="filtro = f.value"
          >{{ f.label }}</button>
        </div>
        <Button size="sm" variant="outline" class="ml-auto h-8" :disabled="carregandoRevisao" @click="carregarRevisao()">
          <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregandoRevisao }" /> atualizar
        </Button>
      </div>
      <p class="text-xs text-muted-foreground">
        O que a IA mandou <b>sozinha</b> no dia: todos os <b>sensíveis</b> (reembolso, cancelamento, troca e reclamação — a régua de 85%) e uma amostra de
        até {{ revisao?.amostra ?? 20 }} dos outros, sempre a mesma no mesmo dia. 👍 conta como acerto na régua do assunto; 👎 conta como erro, e a correção
        entra no banco de respostas como resposta ajustada por pessoa.
      </p>

      <div v-if="contagens" class="grid grid-cols-2 gap-2 md:grid-cols-4" data-contagens>
        <StatCard label="Mandou sozinha" :value="contagens.total" :icon="Bot" :hint="revisao ? `em ${fmtDia(revisao.dia)}` : ''" compact />
        <StatCard label="Sensíveis" :value="contagens.sensiveis" :icon="ShieldAlert" tone="warning" hint="todos na lista" compact />
        <StatCard label="Revisadas" :value="contagens.revisados" :icon="ClipboardCheck" :hint="`de ${contagens.mostrados} na lista`" compact />
        <StatCard label="Errou" :value="contagens.erros" :icon="ThumbsDown" tone="danger" hint="das revisadas" compact />
      </div>

      <div v-if="erroRevisao" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
        {{ erroRevisao }} <button type="button" class="ml-2 text-xs underline" @click="carregarRevisao()">tentar de novo</button>
      </div>
      <div v-if="carregandoRevisao && !revisao" class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</div>
      <div v-else-if="revisao && !revisao.itens.length" class="rounded-lg border bg-card px-3 py-8 text-center text-sm text-muted-foreground">
        A IA não mandou nada sozinha {{ ehHoje ? 'hoje' : `em ${fmtDia(revisao.dia)}` }}.
      </div>
      <div v-else-if="revisao && !itens.length" class="rounded-lg border bg-card px-3 py-6 text-center text-sm text-muted-foreground">
        Nada com esse filtro.
      </div>

      <ol v-if="itens.length" class="space-y-2">
        <li
          v-for="i in itens"
          :key="i.rascunho_id"
          class="rounded-lg border bg-card"
          :class="i.sensivel ? 'border-l-2 border-l-amber-500' : ''"
          :data-revisao="i.rascunho_id"
        >
          <div class="flex flex-wrap items-center gap-1.5 border-b px-3 py-1.5 text-xs">
            <span class="rounded-full bg-muted px-2 py-0.5 font-medium">{{ i.assunto_nome }}</span>
            <span v-if="i.sensivel" class="rounded-full bg-amber-500/20 px-2 py-0.5 text-amber-800 dark:text-amber-300" title="reembolso, cancelamento, troca ou reclamação: a régua é de 85%" data-sensivel>sensível</span>
            <AtendimentoPlataforma :codigo="i.plataforma" />
            <span class="font-medium">{{ i.loja || '—' }}</span>
            <span class="text-muted-foreground">· {{ canalLabel(i.canal) }}</span>
            <span v-if="i.com_anexo" class="inline-flex items-center gap-0.5 text-muted-foreground" title="a IA mandou um anexo (a nota fiscal)"><Paperclip class="size-3" /> anexo</span>
            <span class="ml-auto tabular-nums text-muted-foreground" :title="fmtDataHora(i.enviada_em)">{{ fmtHora(i.enviada_em) }}</span>
            <a
              :href="`/atendimento?conversa=${encodeURIComponent(i.conversa_id)}`"
              target="_blank"
              rel="noopener"
              class="inline-flex items-center gap-0.5 text-muted-foreground underline hover:text-foreground"
              title="abrir a conversa (outra aba)"
            >conversa <ExternalLink class="size-3" /></a>
          </div>
          <div class="grid gap-3 px-3 py-2 lg:grid-cols-2">
            <div class="min-w-0">
              <div class="text-[11px] font-semibold uppercase text-muted-foreground">Cliente</div>
              <div class="max-h-40 overflow-y-auto whitespace-pre-line break-words text-sm">{{ i.pergunta || '—' }}</div>
            </div>
            <div class="min-w-0">
              <div class="text-[11px] font-semibold uppercase text-muted-foreground">A IA mandou</div>
              <div class="max-h-40 overflow-y-auto whitespace-pre-line break-words text-sm">{{ i.resposta || '—' }}</div>
            </div>
          </div>

          <!-- a revisão: o que já foi dito, ou os botões -->
          <div class="border-t px-3 py-2">
            <div v-if="i.avaliacao && !mudando[i.rascunho_id]" class="flex flex-wrap items-start gap-x-2 gap-y-1 text-xs" data-revisada>
              <span
                class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium"
                :class="i.avaliacao.nota === 'erro' ? 'bg-red-500/15 text-red-700 dark:text-red-300' : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'"
              >
                <ThumbsDown v-if="i.avaliacao.nota === 'erro'" class="size-3.5" /><ThumbsUp v-else class="size-3.5" />
                {{ i.avaliacao.nota === 'erro' ? 'errou' : 'certo' }}
              </span>
              <span
                v-if="i.avaliacao.nota === 'erro'"
                class="rounded-full px-2 py-0.5"
                :class="i.avaliacao.no_banco ? 'bg-sky-500/15 text-sky-800 dark:text-sky-300' : 'bg-gray-500/15 text-muted-foreground'"
                :title="i.avaliacao.no_banco ? 'a correção está no banco de respostas' : 'a correção não passou nas regras do banco (dado pessoal, sem conteúdo, curta…)'"
              >{{ i.avaliacao.no_banco ? 'no banco' : 'fora do banco' }}</span>
              <span class="text-muted-foreground">{{ [i.avaliacao.por, fmtDataHora(i.avaliacao.em)].filter((x) => x && x !== '—').join(' · ') }}</span>
              <Button v-if="podeRevisar" size="sm" variant="ghost" class="ml-auto h-7 px-2 text-xs" :disabled="!!salvando" @click="mudar(i)">
                <Pencil class="mr-1 size-3.5" /> mudar
              </Button>
              <div v-if="i.avaliacao.nota === 'erro' && i.avaliacao.correcao" class="basis-full whitespace-pre-line rounded-md bg-muted/40 px-2 py-1.5 text-sm">
                <span class="text-[11px] font-semibold uppercase text-muted-foreground">O certo seria </span>{{ i.avaliacao.correcao }}
              </div>
            </div>

            <template v-else-if="podeRevisar">
              <div v-if="corrigindo[i.rascunho_id] !== undefined" class="space-y-1.5" data-correcao>
                <label class="block space-y-1">
                  <span class="block text-[11px] font-medium text-muted-foreground">Como seria o certo? (vai para o banco de respostas, sem nome nem contato do cliente)</span>
                  <textarea
                    v-model="corrigindo[i.rascunho_id]"
                    rows="4"
                    :maxlength="MAX_CORRECAO"
                    class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
                    :aria-label="`como seria o certo (${i.assunto_nome})`"
                  />
                </label>
                <div class="flex flex-wrap items-center justify-end gap-1.5">
                  <span class="mr-auto text-[11px] tabular-nums text-muted-foreground">{{ (corrigindo[i.rascunho_id] || '').trim().length }}/{{ MAX_CORRECAO }}</span>
                  <Button size="sm" variant="ghost" class="h-8" :disabled="salvando === i.rascunho_id" @click="i.avaliacao ? desistir(i) : fecharCorrecao(i)"><X class="mr-1 size-4" /> cancelar</Button>
                  <Button size="sm" class="h-8" :disabled="salvando !== null || !correcaoPronta(corrigindo[i.rascunho_id], i.resposta)" @click="revisar(i, 'erro')">
                    <Loader2 v-if="salvando === i.rascunho_id" class="mr-1.5 size-4 animate-spin" /><ThumbsDown v-else class="mr-1.5 size-4" /> salvar correção
                  </Button>
                </div>
              </div>
              <div v-else class="flex flex-wrap items-center gap-1.5" data-votar>
                <Button size="sm" variant="outline" class="h-8 text-emerald-700 dark:text-emerald-300" :disabled="salvando !== null" @click="revisar(i, 'ok')">
                  <Loader2 v-if="salvando === i.rascunho_id" class="mr-1.5 size-4 animate-spin" /><ThumbsUp v-else class="mr-1.5 size-4" /> certo
                </Button>
                <Button size="sm" variant="outline" class="h-8 text-red-600 dark:text-red-400" :disabled="salvando !== null" @click="abrirCorrecao(i)">
                  <ThumbsDown class="mr-1.5 size-4" /> errou
                </Button>
                <Button v-if="i.avaliacao" size="sm" variant="ghost" class="h-8 text-xs" @click="desistir(i)">deixar como estava</Button>
              </div>
            </template>
            <span v-else class="text-xs text-muted-foreground">Ainda não revisado.</span>
          </div>
        </li>
      </ol>
    </div>

    <!-- ─── banco ─── -->
    <div v-else class="space-y-3" data-parte="banco">
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative w-full sm:w-72">
          <Search class="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input v-model="busca.q" class="h-8 w-full rounded-md border bg-background pl-7 pr-2 text-xs" placeholder="buscar nas perguntas e respostas…" aria-label="buscar no banco de respostas" maxlength="200" />
        </div>
        <select v-model="busca.tipo" class="h-8 max-w-56 rounded-md border bg-background px-2 text-xs" aria-label="filtrar pelo assunto">
          <option value="">todo assunto</option>
          <option v-for="t in respostas?.tipos || []" :key="t.tipo" :value="t.tipo">{{ t.nome }} ({{ t.n }})</option>
        </select>
        <select v-model="busca.integration_id" class="h-8 max-w-56 rounded-md border bg-background px-2 text-xs" aria-label="filtrar pela loja">
          <option value="">toda loja</option>
          <option v-for="l in respostas?.lojas || []" :key="l.integration_id" :value="l.integration_id">{{ l.loja }} · {{ l.plataforma }} ({{ l.n }})</option>
        </select>
        <Button size="sm" variant="outline" class="ml-auto h-8" :disabled="carregandoBanco" @click="carregarBanco()">
          <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregandoBanco }" /> atualizar
        </Button>
      </div>
      <p class="text-xs text-muted-foreground">
        As respostas que a equipe escreveu ou ajustou (no DaVinci, no Duoke ou no Seller Center) e as correções da revisão — o que o agente consulta. Só a
        versão generalizada: sem nome, pedido ou contato do cliente. A busca acha as que têm todas as palavras.
      </p>

      <div v-if="erroBanco" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
        {{ erroBanco }} <button type="button" class="ml-2 text-xs underline" @click="carregarBanco()">tentar de novo</button>
      </div>
      <div v-if="carregandoBanco && !respostas" class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</div>
      <template v-if="respostas">
        <div class="text-xs text-muted-foreground" data-total>
          {{ respostas.total }} resposta{{ respostas.total === 1 ? '' : 's' }}{{ respostas.itens.length < respostas.total ? ` — mostrando ${respostas.itens.length}` : '' }}
        </div>
        <div v-if="!respostas.itens.length" class="rounded-lg border bg-card px-3 py-8 text-center text-sm text-muted-foreground">
          {{ busca.q.trim() || busca.tipo || busca.integration_id ? 'Nenhuma resposta com esse filtro.' : 'O banco ainda está vazio.' }}
        </div>
        <ol v-else class="divide-y rounded-lg border bg-card">
          <li v-for="r in respostas.itens" :key="r.id" class="space-y-1.5 px-3 py-2.5" :data-resposta="r.id">
            <div class="grid gap-3 lg:grid-cols-2">
              <div class="min-w-0 whitespace-pre-line break-words text-sm"><span class="text-[11px] font-semibold uppercase text-muted-foreground">Cliente </span>{{ r.pergunta }}</div>
              <div class="min-w-0 whitespace-pre-line break-words text-sm"><span class="text-[11px] font-semibold uppercase text-muted-foreground">Equipe </span>{{ r.resposta }}</div>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
              <span class="rounded-full bg-muted px-2 py-0.5 font-medium text-foreground">{{ r.tipo_nome }}</span>
              <AtendimentoPlataforma :codigo="r.plataforma" />
              <span>{{ r.loja || '—' }}</span>
              <span v-for="m in r.modelos" :key="m" class="rounded border px-1.5 py-px font-mono">{{ m }}</span>
              <span :class="r.da_revisao ? 'rounded-full bg-sky-500/15 px-2 py-0.5 text-sky-800 dark:text-sky-300' : ''">· {{ fonteLabel(r) }}</span>
              <span>· {{ fmtDataHora(r.respondido_em) }}</span>
              <a
                v-if="r.conversa_id"
                :href="`/atendimento?conversa=${encodeURIComponent(r.conversa_id)}`"
                target="_blank"
                rel="noopener"
                class="ml-auto inline-flex items-center gap-0.5 underline hover:text-foreground"
                title="abrir a conversa (outra aba)"
              >conversa <ExternalLink class="size-3" /></a>
            </div>
          </li>
        </ol>
        <div v-if="podeMostrarMais" class="text-center">
          <Button size="sm" variant="outline" :disabled="carregandoBanco" @click="mostrarMais()">
            <Loader2 v-if="carregandoBanco" class="mr-1.5 size-4 animate-spin" /> mostrar mais
          </Button>
        </div>
      </template>
    </div>
  </section>
</template>
