<script lang="ts">
// As PASTAS INFORMATIVAS (21/10/2026, decisão 4 do dono), no chip
// "Informativos" da aba E-mail › Filas: o e-mail de uma pasta com a categoria
// Informativo (Pastas e regras) não abre conversa. Cada pasta é UM item, com
// quantos e-mails, o último e as lojas; abrir o item mostra uma linha por
// e-mail (data · loja · remetente · assunto, o mais novo em cima) e o "ver o
// e-mail" abre o texto PROTEGIDO (links de acesso e códigos fora, nunca HTML),
// como nas filas.
//
// Só quem MEXE no /atendimento (as rotas recusam os outros com
// `atendimento_so_quem_mexe`); cada linha passa pelo filtro de acesso da
// pessoa (as lojas e as categorias permitidas). Rotas (routers/atendimento_email.py):
//   GET …/email/informativos                       — os itens (sem texto);
//   GET …/email/emails?fila=informativo&pasta=<id> — as linhas de um item
//                                                    (com a paginação `antes_de`);
//   GET …/email/emails/{id}                        — o e-mail (ver o e-mail).
// Funções puras aqui em cima (tests/atendimento-mail-atendimento.cjs).
import type { ItemFila } from '~/components/AtendimentoMailFilas.vue'

export type PastaInformativa = {
  pasta_id: string
  nome: string
  mailbox_id: string
  caixa: string | null
  total: number
  ultimo_em: string | null
  lojas: string[]
}

export const URL_INFORMATIVOS = '/api/atendimento/email/informativos'

// As linhas (e-mails) de UM item, as mais novas primeiro; `antesDe` = a próxima página.
export function urlDasLinhas(pastaId: string, antesDe?: string | null): string {
  const q = new URLSearchParams({ fila: 'informativo', pasta: pastaId })
  if (antesDe) q.set('antes_de', antesDe)
  return `/api/atendimento/email/emails?${q.toString()}`
}

// "Loja A, Loja B e mais 2" (todas no título).
export function rotuloDasLojas(lojas: string[] | null | undefined, max = 3): string {
  const l = (lojas || []).filter(Boolean)
  if (l.length <= max) return l.join(', ')
  return `${l.slice(0, max).join(', ')} e mais ${l.length - max}`
}

// "12 e-mails" / "1 e-mail".
export function rotuloDoTotal(n: number | null | undefined): string {
  const t = Math.max(0, Math.trunc(Number(n) || 0))
  return `${t.toLocaleString('pt-BR')} e-mail${t === 1 ? '' : 's'}`
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronUp, Download, ExternalLink, Loader2, RefreshCw } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'
import { avisosDeProtecao, remetenteLegivel, tamanhoLegivel, urlDoAnexo, type CartaoEmail } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{
  // Sobe quando as Filas se atualizam ("atualizar", a volta da conversa): relê.
  sinal?: number
}>()

const { api, url } = useApi()
const toasts = useToasts()

const itens = ref<PastaInformativa[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
// O item aberto e as linhas dele.
const aberta = ref<string | null>(null)
const linhas = ref<ItemFila[]>([])
const proximo = ref<string | null>(null)
const carregandoLinhas = ref(false)
const erroLinhas = ref<string | null>(null)
// O e-mail aberto ("ver o e-mail").
const emailAberto = ref<string | null>(null)
const detalhe = ref<CartaoEmail | null>(null)
let geracao = 0
let geracaoLinhas = 0

async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = null
  try {
    const r = await api<{ itens: PastaInformativa[] }>(URL_INFORMATIVOS)
    if (g !== geracao) return
    itens.value = r?.itens || []
    // O item aberto sumiu (a pasta deixou de ser informativa): fecha.
    if (aberta.value && !itens.value.some((i) => i.pasta_id === aberta.value)) fecharItem()
  } catch (e: any) {
    if (g === geracao) erro.value = erroDaApi(e, 'Não consegui carregar as pastas informativas').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}

async function carregarLinhas(mais = false) {
  const pasta = aberta.value
  if (!pasta) return
  const g = ++geracaoLinhas
  carregandoLinhas.value = true
  erroLinhas.value = null
  try {
    const r = await api<{ itens: ItemFila[]; proximo: string | null }>(urlDasLinhas(pasta, mais ? proximo.value : null))
    if (g !== geracaoLinhas || aberta.value !== pasta) return
    const novas = r?.itens || []
    linhas.value = mais ? [...linhas.value, ...novas.filter((x) => !linhas.value.some((y) => y.id === x.id))] : novas
    proximo.value = r?.proximo || null
  } catch (e: any) {
    if (g === geracaoLinhas) erroLinhas.value = erroDaApi(e, 'Não consegui carregar os e-mails desta pasta').texto
  } finally {
    if (g === geracaoLinhas) carregandoLinhas.value = false
  }
}

function fecharItem() {
  aberta.value = null
  linhas.value = []
  proximo.value = null
  erroLinhas.value = null
  emailAberto.value = null
  detalhe.value = null
  ++geracaoLinhas
  carregandoLinhas.value = false
}

// Abrir um item mostra as linhas dele (o outro fecha); de novo, fecha.
function abrirItem(it: PastaInformativa) {
  if (aberta.value === it.pasta_id) {
    fecharItem()
    return
  }
  fecharItem()
  aberta.value = it.pasta_id
  void carregarLinhas()
}

async function verEmail(e: ItemFila) {
  if (emailAberto.value === e.id) {
    emailAberto.value = null
    detalhe.value = null
    return
  }
  emailAberto.value = e.id
  detalhe.value = null
  try {
    const d = await api<CartaoEmail>(`/api/atendimento/email/emails/${encodeURIComponent(e.id)}`)
    if (emailAberto.value === e.id) detalhe.value = d
  } catch (err: any) {
    if (emailAberto.value === e.id) {
      emailAberto.value = null
      const er = erroDaApi(err, 'Não consegui abrir o e-mail')
      toasts.error(er.texto, er.motivos)
    }
  }
}

function atualizar() {
  void carregar()
  if (aberta.value) {
    emailAberto.value = null
    detalhe.value = null
    void carregarLinhas()
  }
}
onMounted(() => { void carregar() })
watch(() => props.sinal, () => { atualizar() })
</script>

<template>
  <section class="space-y-1.5 rounded-lg border p-2.5 text-xs" aria-label="Pastas informativas" data-pastas-informativas>
    <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <h3 class="text-[13px] font-semibold text-foreground">Pastas informativas</h3>
      <span class="min-w-0 flex-1 text-muted-foreground">uma por pasta com a categoria Informativo (Pastas e regras); cada e-mail é uma linha, sem conversa</span>
      <button type="button" class="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground" :disabled="carregando" @click="atualizar">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregando }" /> atualizar
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300">{{ erro }}</div>
    <div v-else-if="carregando && !itens.length" class="flex justify-center py-4"><Loader2 class="size-4 animate-spin" /></div>
    <div v-else-if="!itens.length" class="py-2 text-muted-foreground" data-sem-pastas-informativas>Nenhuma pasta informativa ainda: escolha a categoria Informativo numa pasta em Pastas e regras.</div>
    <ul v-else class="divide-y">
      <li v-for="it in itens" :key="it.pasta_id" class="py-0.5" :data-pasta-informativa="it.pasta_id">
        <button
          type="button"
          class="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 rounded px-1 py-1 text-left hover:bg-muted/60"
          :aria-expanded="aberta === it.pasta_id"
          data-abrir-pasta-informativa
          @click="abrirItem(it)"
        >
          <component :is="aberta === it.pasta_id ? ChevronUp : ChevronDown" class="size-3 shrink-0" />
          <span class="font-medium text-foreground">{{ it.nome }}</span>
          <span class="rounded-full bg-background px-1.5 text-[10px] tabular-nums ring-1 ring-border" :title="rotuloDoTotal(it.total)">{{ it.total }}</span>
          <span v-if="it.caixa" class="text-muted-foreground">{{ it.caixa }}</span>
          <span v-if="it.lojas?.length" class="min-w-0 text-muted-foreground" :title="it.lojas.join(', ')">· {{ rotuloDasLojas(it.lojas) }}</span>
          <span class="ml-auto shrink-0 text-muted-foreground">{{ it.ultimo_em ? `último ${fmtDataHora(it.ultimo_em)}` : 'nenhum e-mail ainda' }}</span>
        </button>

        <!-- as linhas do item: uma por e-mail, a mais nova em cima -->
        <div v-if="aberta === it.pasta_id" class="space-y-1 pb-1 pl-5" data-linhas-informativas>
          <div v-if="carregandoLinhas && !linhas.length" class="flex justify-center py-3"><Loader2 class="size-4 animate-spin" /></div>
          <div v-else-if="erroLinhas" class="text-red-700 dark:text-red-300">{{ erroLinhas }}</div>
          <div v-else-if="!linhas.length" class="py-1 text-muted-foreground">Nenhum e-mail nesta pasta ainda.</div>
          <ul v-else class="divide-y rounded-md border">
            <li v-for="e in linhas" :key="e.id" class="space-y-1 px-2 py-1.5" :data-linha-informativa="e.id">
              <div class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                <span class="shrink-0 text-muted-foreground">{{ fmtDataHora(e.recebido_em) }}</span>
                <span class="text-muted-foreground">· {{ e.loja ? `${plataformaInfo(e.plataforma).curto} ${e.loja}` : 'sem loja' }}</span>
                <span class="min-w-0 break-all text-muted-foreground">· {{ e.de || '—' }}</span>
                <span class="min-w-0 flex-1 break-words font-medium">{{ e.assunto || '(sem assunto)' }}</span>
                <button type="button" class="inline-flex items-center gap-0.5 underline" :aria-expanded="emailAberto === e.id" data-ver-email-informativo @click="verEmail(e)">
                  <component :is="emailAberto === e.id ? ChevronUp : ChevronDown" class="size-3" />{{ emailAberto === e.id ? 'fechar o e-mail' : 'ver o e-mail' }}
                </button>
                <a v-if="e.abrir_no_tuta" :href="e.abrir_no_tuta" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 underline" title="abre no Tuta (lá fica lido)"><ExternalLink class="size-3" />Abrir no Tuta</a>
              </div>
              <div v-if="emailAberto === e.id" class="space-y-1.5 rounded-md border bg-background p-2" data-email-informativo-aberto>
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
            <button type="button" class="rounded border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50" :disabled="carregandoLinhas" data-mais-linhas-informativas @click="carregarLinhas(true)">carregar mais</button>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
