<script setup lang="ts">
// "Vincular à garantia" (§5.1) — o modal da conversa no /atendimento.
// 1. Busca a garantia por CPF, NF ou nº do pedido — a busca já vem com o
//    pedido da conversa (ponto 4: vínculo MANUAL; o DaVinci nunca vincula
//    sozinho) e as garantias do CPF/pedido da conversa já aparecem. Só vem
//    MARCADA a garantia do pedido desta conversa (quando é uma só): a do
//    mesmo CPF em outro pedido é outro aparelho, aparece com "outro pedido"
//    e a pessoa escolhe — atendimento vinculado não se apaga. A busca é POST
//    (o termo, que pode ser CPF, vai no corpo, fora do log de acesso).
// 2. Tipo do problema (Hardware / Software), resumo (vazio = as últimas falas
//    do cliente), as mensagens a copiar (nenhuma marcada = as 100 mais
//    recentes) e os anexos que vão junto, e a solução/encaminhamento.
// 3. Antes de salvar mostra se fica Coberto ou Fora da garantia (a data do
//    atendimento contra o fim do tipo escolhido); quem grava é o servidor
//    (POST /api/garantias/{id}/atendimentos), que copia tudo da conversa.
// Atendimento só se ADICIONA: não há editar nem apagar depois (§5.3).
// Quem pode: "Registrar atendimento" (garantias_atendimento.edit) — vale
// mesmo para quem só LÊ a caixa do Atendimento (fase de observação).
import { onKeyStroke } from '@vueuse/core'
import { AlertCircle, CheckSquare, FileText, Loader2, Search, ShieldAlert, Square, X } from 'lucide-vue-next'
import { origemLabel, type ConversaDetalhe, type Mensagem } from '~/components/AtendimentoPlataforma.vue'
import {
  REGRAS_PADRAO,
  TIPOS_PROBLEMA,
  anexosDasMensagens,
  dataBR,
  dataCurta,
  dataDoAtendimento,
  dataHoraBR,
  ehImagem,
  errosDaApi,
  mensagensDoVinculo,
  previaCobertura,
  resumir,
  textoNf,
  type AtendimentoGarantia,
  type GarantiaLinha,
  type Regras,
  type SituacaoConversa,
  type TipoProblema,
} from '~/lib/garantias'

const props = defineProps<{
  conversa: ConversaDetalhe
  mensagens: Mensagem[]
  situacao: SituacaoConversa | null
}>()
const emit = defineEmits<{
  (e: 'fechar'): void
  (e: 'vinculado', a: AtendimentoGarantia): void
}>()

const { api } = useApi()
const acesso = useGarantiaAcesso()

const regras = ref<Regras>(REGRAS_PADRAO)
onMounted(async () => {
  try {
    regras.value = { ...REGRAS_PADRAO, ...(await api<Regras>('/api/garantias/regras')) }
  } catch {
    // fica o padrão
  }
})

// ─── 1. a garantia ──────────────────────────────────────────────────────────
const busca = ref(props.situacao?.busca_sugerida || props.conversa.pedido_marketplace || '')
const resultados = ref<GarantiaLinha[]>([...(props.situacao?.garantias || [])])
const buscando = ref(false)
const buscou = ref(false)
const erroBusca = ref('')
const pedidoDaConversa = computed(() => props.situacao?.pedido_bling || null)
function outroPedido(r: GarantiaLinha): boolean {
  return !!pedidoDaConversa.value && r.pedido_bling !== pedidoDaConversa.value
}
function sugerida(lista: GarantiaLinha[]): GarantiaLinha | null {
  const doPedido = lista.filter((r) => !!pedidoDaConversa.value && r.pedido_bling === pedidoDaConversa.value)
  return doPedido.length === 1 ? doPedido[0] : null
}
const escolhida = ref<GarantiaLinha | null>(sugerida(resultados.value))
let geracao = 0

async function buscar() {
  const q = busca.value.trim()
  if (!q) return
  const g = ++geracao
  buscando.value = true
  erroBusca.value = ''
  try {
    const r = await api<GarantiaLinha[]>('/api/garantias/busca', { method: 'POST', body: { q } })
    if (g !== geracao) return
    resultados.value = r
    buscou.value = true
    if (escolhida.value && !r.some((x) => x.id === escolhida.value?.id)) escolhida.value = null
    if (!escolhida.value) escolhida.value = sugerida(r)
  } catch (e) {
    if (g !== geracao) return
    erroBusca.value = errosDaApi(e).geral
  } finally {
    if (g === geracao) buscando.value = false
  }
}
let atraso: ReturnType<typeof setTimeout> | null = null
watch(busca, (v) => {
  if (atraso) clearTimeout(atraso)
  if (v.trim().length >= 3) atraso = setTimeout(buscar, 400)
})
// Abriu sem garantia conhecida da conversa: já busca pelo que veio preenchido.
onMounted(() => {
  if (!resultados.value.length && busca.value.trim()) void buscar()
})

// ─── 2. o atendimento ───────────────────────────────────────────────────────
const tipo = ref<TipoProblema | null>(null)
const resumo = ref('')
const solucao = ref('')
// As mensagens que dá para escolher: tudo menos as de sistema (cartões de
// status), a mais nova em cima — o mesmo recorte da cópia padrão do servidor
// (a fala do mediador do ML, autor 'mediador', entra nas duas).
function quandoMs(m: Mensagem): number {
  const t = m.enviada_em ? new Date(m.enviada_em).getTime() : Number.NaN
  return Number.isNaN(t) ? 0 : t
}
const mensagensEscolhiveis = computed(() =>
  [...props.mensagens].filter((m) => m.autor !== 'sistema')
    .sort((a, b) => quandoMs(b) - quandoMs(a)),
)
const escolhidas = ref<string[]>([])
function alternar(id: string) {
  escolhidas.value = escolhidas.value.includes(id) ? escolhidas.value.filter((x) => x !== id) : [...escolhidas.value, id]
}
function soDoCliente() {
  escolhidas.value = mensagensEscolhiveis.value.filter((m) => m.autor === 'cliente').map((m) => m.id)
}
const copiadas = computed(() => mensagensDoVinculo(props.mensagens, escolhidas.value))
const anexos = computed(() => anexosDasMensagens(copiadas.value))
const quando = computed(() => dataDoAtendimento(props.mensagens, escolhidas.value, props.conversa.ultima_mensagem_em, Date.now(), regras.value.dias_pausa_atendimento))
const previa = computed(() => (escolhida.value && tipo.value ? previaCobertura(escolhida.value, tipo.value, quando.value, regras.value.ultimo_dia_coberto) : null))

// ─── 3. salvar ──────────────────────────────────────────────────────────────
const salvando = ref(false)
const erros = ref<Record<string, string>>({})
const erroGeral = ref('')

async function vincular() {
  if (salvando.value) return
  const e: Record<string, string> = {}
  if (!escolhida.value) e.garantia = 'Escolha a garantia na lista.'
  if (!tipo.value) e.tipo_problema = 'Escolha Hardware ou Software.'
  const sol = solucao.value.trim()
  if (sol.length < 3) e.solucao = sol ? 'Mínimo de 3 caracteres.' : 'Informe a solução ou o encaminhamento.'
  else if (sol.length > 4000) e.solucao = 'Máximo de 4000 caracteres.'
  if (resumo.value.trim().length > 4000) e.resumo = 'Máximo de 4000 caracteres.'
  erros.value = e
  // O erro de cima (a garantia) pode estar fora da tela: a frase também vai
  // para perto do botão.
  erroGeral.value = Object.values(e)[0] || ''
  if (Object.keys(e).length || !escolhida.value || !tipo.value) return
  salvando.value = true
  try {
    const corpo: Record<string, unknown> = { conversa_id: props.conversa.id, tipo_problema: tipo.value, solucao: sol }
    if (resumo.value.trim()) corpo.resumo = resumo.value.trim()
    if (escolhidas.value.length) corpo.mensagem_ids = escolhidas.value
    const at = await api<AtendimentoGarantia>(`/api/garantias/${escolhida.value.id}/atendimentos`, { method: 'POST', body: corpo })
    emit('vinculado', at)
  } catch (err) {
    const er = errosDaApi(err)
    erros.value = er.campos
    erroGeral.value = er.geral || Object.values(er.campos)[0] || ''
  } finally {
    salvando.value = false
  }
}

onKeyStroke('Escape', () => { if (!salvando.value) emit('fechar') })
</script>

<template>
  <div class="fixed inset-0 z-[75] flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4" @click.self="!salvando && emit('fechar')">
    <form
      class="flex max-h-[100dvh] w-full max-w-2xl flex-col overflow-hidden rounded-t-xl border bg-background text-sm shadow-xl sm:max-h-[92dvh] sm:rounded-xl"
      role="dialog"
      aria-modal="true"
      aria-labelledby="vincular-garantia-titulo"
      novalidate
      data-vincular-garantia
      @submit.prevent="vincular"
    >
      <div class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
        <div class="min-w-0 flex-1">
          <h2 id="vincular-garantia-titulo" class="text-base font-semibold">Vincular à garantia</h2>
          <p class="truncate text-xs text-muted-foreground">
            Conversa {{ conversa.conta || conversa.plataforma }}<template v-if="conversa.pedido_marketplace"> · pedido {{ conversa.pedido_marketplace }}</template>
          </p>
        </div>
        <Button type="button" size="sm" variant="ghost" aria-label="Fechar" :disabled="salvando" @click="emit('fechar')"><X class="size-4" /></Button>
      </div>

      <div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        <!-- §5.3: CPF sem garantia -->
        <div
          v-if="situacao?.alerta_cpf_sem_garantia"
          class="flex items-start gap-2 rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
          role="alert"
          data-alerta-cpf-sem-garantia
        >
          <ShieldAlert class="mt-px size-4 shrink-0" aria-hidden="true" />
          <div>
            <div class="font-medium">O CPF deste pedido não tem garantia cadastrada.</div>
            <div>O pedido tem produto Uranyx. Confira com o cliente<template v-if="acesso.cadastra && situacao.pedido_bling"> ou <NuxtLink :to="`/garantias?novo=${encodeURIComponent(situacao.pedido_bling)}`" target="_blank" class="font-medium underline">cadastre a garantia do pedido {{ situacao.pedido_bling }}</NuxtLink></template>.</div>
          </div>
        </div>

        <!-- 1. garantia -->
        <section class="space-y-2">
          <label for="vinc-busca" class="block text-xs font-medium text-muted-foreground">1. Garantia — busque por CPF, NF ou nº do pedido</label>
          <div class="flex gap-2">
            <div class="relative flex-1">
              <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="vinc-busca" v-model="busca" class="h-9 pl-8" autocomplete="off" placeholder="CPF (11 dígitos), NF, pedido ou nome" @keydown.enter.prevent="buscar" />
            </div>
            <Button type="button" size="sm" variant="outline" class="h-9" :disabled="buscando || !busca.trim()" @click="buscar">
              <Loader2 v-if="buscando" class="size-4 animate-spin" /><span v-else>Buscar</span>
            </Button>
          </div>
          <p v-if="erroBusca" class="text-xs text-destructive">{{ erroBusca }}</p>
          <p v-else-if="buscou && !resultados.length" class="text-xs text-muted-foreground">Nenhuma garantia encontrada.<template v-if="acesso.cadastra && situacao?.pedido_bling">{{ ' ' }}<NuxtLink :to="`/garantias?novo=${encodeURIComponent(situacao.pedido_bling)}`" target="_blank" class="underline">Cadastrar a do pedido {{ situacao.pedido_bling }}</NuxtLink></template></p>
          <p v-else-if="!buscou && resultados.length" class="text-xs text-muted-foreground">Garantias do pedido/CPF desta conversa:</p>
          <p v-if="resultados.length && resultados.every(outroPedido)" class="text-xs text-amber-800 dark:text-amber-300" data-so-outros-pedidos>
            Nenhuma é do pedido desta conversa ({{ pedidoDaConversa }}): vincule só se for o mesmo aparelho.
          </p>
          <ul v-if="resultados.length" class="max-h-56 space-y-1 overflow-y-auto" role="radiogroup" aria-label="garantias encontradas" data-resultados>
            <li v-for="r in resultados" :key="r.id">
              <button
                type="button"
                role="radio"
                :aria-checked="escolhida?.id === r.id"
                class="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border px-2.5 py-2 text-left"
                :class="escolhida?.id === r.id ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'hover:bg-muted'"
                @click="escolhida = r"
              >
                <GarantiaStatus :status="r.status" :entregue-sem-data="r.entregue_sem_data" />
                <span class="font-medium">{{ r.cliente_nome }}</span>
                <span class="font-mono text-xs text-muted-foreground">{{ r.cpf_mascarado || '—' }}</span>
                <span
                  v-if="outroPedido(r)"
                  class="rounded border border-amber-400/60 bg-amber-50 px-1.5 text-[10px] font-medium text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300"
                  title="garantia de outro pedido do mesmo cliente — confira se é o aparelho desta conversa"
                  data-outro-pedido
                >outro pedido</span>
                <span class="w-full text-xs text-muted-foreground">
                  #{{ r.id }} · NF {{ textoNf(r.nf_numero, r.nf_serie) }} · pedido {{ r.pedido_bling }} ·
                  HW até {{ dataCurta(r.fim_hardware) }} · SW até {{ dataCurta(r.fim_software) }}
                </span>
              </button>
            </li>
          </ul>
          <p v-if="erros.garantia" class="text-xs text-destructive">{{ erros.garantia }}</p>
        </section>

        <!-- 2. tipo -->
        <section class="space-y-2">
          <div class="text-xs font-medium text-muted-foreground">2. Tipo do problema</div>
          <div class="grid grid-cols-2 gap-2" role="radiogroup" aria-label="tipo do problema">
            <button
              v-for="t in TIPOS_PROBLEMA"
              :key="t.value"
              type="button"
              role="radio"
              :aria-checked="tipo === t.value"
              class="rounded-md border px-3 py-2 text-left"
              :class="tipo === t.value ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'hover:bg-muted'"
              :data-tipo="t.value"
              @click="tipo = t.value"
            >
              <div class="font-medium">{{ t.label }}</div>
              <div class="text-[11px] text-muted-foreground">{{ t.dica }}</div>
            </button>
          </div>
          <p v-if="erros.tipo_problema" class="text-xs text-destructive">{{ erros.tipo_problema }}</p>
        </section>

        <!-- cobertura antes de salvar -->
        <GarantiaCobertura v-if="previa" :cobertura="previa.cobertura" bloco data-previa-cobertura>
          <span class="text-xs">
            <template v-if="previa.cobertura === 'sem_data_de_entrega'">a garantia ainda espera a data de entrega do pedido.</template>
            <template v-else>atendimento em {{ dataBR(quando) }} · {{ tipo === 'hardware' ? 'hardware' : 'software' }} até {{ dataBR(previa.fim) }}.</template>
          </span>
        </GarantiaCobertura>
        <p v-else-if="escolhida" class="text-xs text-muted-foreground">Escolha o tipo para ver se fica Coberto ou Fora da garantia.</p>

        <!-- mensagens -->
        <section class="space-y-2">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span class="text-xs font-medium text-muted-foreground">Mensagens que vão para a garantia</span>
            <span class="text-[11px] text-muted-foreground">{{ escolhidas.length ? `${escolhidas.length} escolhida${escolhidas.length === 1 ? '' : 's'}` : 'nenhuma marcada = as 100 mais recentes' }}</span>
            <span class="ml-auto flex gap-2 text-[11px]">
              <button type="button" class="underline" @click="soDoCliente">só as do cliente</button>
              <button v-if="escolhidas.length" type="button" class="underline" @click="escolhidas = []">limpar</button>
            </span>
          </div>
          <p v-if="!mensagensEscolhiveis.length" class="text-xs text-muted-foreground">Sem mensagens carregadas nesta conversa.</p>
          <ul v-else class="max-h-48 space-y-1 overflow-y-auto rounded-md border p-1" data-mensagens-escolha>
            <li v-for="m in mensagensEscolhiveis" :key="m.id">
              <button
                type="button"
                role="checkbox"
                :aria-checked="escolhidas.includes(m.id)"
                class="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-muted"
                @click="alternar(m.id)"
              >
                <CheckSquare v-if="escolhidas.includes(m.id)" class="mt-0.5 size-4 shrink-0 text-primary" />
                <Square v-else class="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <span class="min-w-0 flex-1">
                  <span class="block text-[11px] text-muted-foreground">{{ origemLabel(m, conversa.plataforma) }} · {{ dataHoraBR(m.enviada_em) }}<template v-if="m.anexos?.length"> · {{ m.anexos.length }} anexo{{ m.anexos.length === 1 ? '' : 's' }}</template></span>
                  <span class="block break-words text-xs">{{ resumir(m.texto, 160) || (m.anexos?.length ? '[anexo]' : '—') }}</span>
                </span>
              </button>
            </li>
          </ul>
          <p class="text-[11px] text-muted-foreground" data-data-atendimento>Data do atendimento: {{ dataHoraBR(quando) }} ({{ escolhidas.length ? '1ª mensagem do cliente entre as escolhidas' : `1ª mensagem do cliente no trecho atual da conversa — pausa de até ${regras.dias_pausa_atendimento} dias não abre trecho novo` }}).</p>
        </section>

        <!-- anexos -->
        <section v-if="anexos.length" class="space-y-1.5" data-anexos-vinculo>
          <div class="text-xs font-medium text-muted-foreground">Anexos que vão junto ({{ anexos.length }})</div>
          <div class="flex flex-wrap gap-2">
            <template v-for="(a, i) in anexos" :key="i">
              <img v-if="a.url && ehImagem(a)" :src="a.url" :alt="a.nome || 'foto da conversa'" referrerpolicy="no-referrer" class="size-14 rounded-md border object-cover" loading="lazy" />
              <span v-else class="inline-flex max-w-[12rem] items-center gap-1 rounded-md border px-2 py-1 text-xs"><FileText class="size-3.5 shrink-0" /><span class="truncate">{{ a.nome || a.tipo }}</span></span>
            </template>
          </div>
          <p class="text-[11px] text-muted-foreground">O DaVinci guarda uma cópia na hora (até 10 arquivos, 8 MB cada). Do Mercado Livre fica só o nome do arquivo.</p>
        </section>

        <!-- resumo e solução -->
        <section class="space-y-3">
          <div>
            <label for="vinc-resumo" class="block text-xs font-medium text-muted-foreground">Resumo do problema</label>
            <textarea id="vinc-resumo" v-model="resumo" rows="2" maxlength="4000" class="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm" placeholder="vazio = as últimas falas do cliente" />
            <p v-if="erros.resumo" class="text-xs text-destructive">{{ erros.resumo }}</p>
          </div>
          <div>
            <label for="vinc-solucao" class="block text-xs font-medium text-muted-foreground">Solução / encaminhamento *</label>
            <textarea id="vinc-solucao" v-model="solucao" rows="3" maxlength="4000" class="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm" placeholder="Ex.: orientado a reiniciar; troca pela assistência; enviado para análise…" />
            <p v-if="erros.solucao" class="text-xs text-destructive" data-erro-campo="solucao">{{ erros.solucao }}</p>
          </div>
        </section>

        <p class="text-[11px] text-muted-foreground">O atendimento fica gravado na garantia (data, você como atendente, o link da conversa, mensagens e anexos) e não pode ser editado nem apagado depois.</p>
      </div>

      <div class="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t px-4 py-3">
        <!-- No rodapé: o erro de um campo lá em cima pode estar fora da tela. -->
        <p v-if="erroGeral" role="alert" class="mr-auto flex min-w-0 items-center gap-1.5 text-sm text-destructive" data-erro-geral><AlertCircle class="size-4 shrink-0" /> {{ erroGeral }}</p>
        <Button type="button" variant="ghost" :disabled="salvando" @click="emit('fechar')">Cancelar</Button>
        <Button type="submit" :disabled="salvando" data-confirmar-vinculo><Loader2 v-if="salvando" class="mr-1 size-4 animate-spin" /> Vincular</Button>
      </div>
    </form>
  </div>
</template>
