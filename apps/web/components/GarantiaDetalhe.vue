<script setup lang="ts">
// Detalhe da garantia (§4.2), aberto ao clicar na linha do painel.
// - Dados: nome, CPF (completo só para quem tem "Ver o CPF completo"; os
//   outros veem mascarado), o e-mail e o WhatsApp (ou o telefone fixo) do
//   comprador (09/11/2026: do formulário do SAC ou do cadastro do contato no
//   Bling; mascarados como o CPF), NF, pedido, data de cadastro, cadastrado por.
// - Prazo: início (a entrega, com a origem) e o fim ÚNICO da garantia (desde
//   06/11/2026: entrega + o prazo do produto na Tabela de Preços › Produtos),
//   com a barra de dias restantes — ou "Prazo não cadastrado", ou "Sem
//   garantia" (o prazo 0: o produto vendido sem garantia, 06/11/2026). Sem
//   hardware nem software no painel.
// - Atendimentos: linha do tempo dos atendimentos vindos do /atendimento
//   ("Vincular à garantia"), cada um Coberto ou Fora da garantia, com o
//   resumo, a solução, as mensagens e os anexos copiados. Só se adiciona —
//   não há editar nem apagar (§5.3).
// - Log (admin): quem consultou e quem alterou (§6).
// Abrir o detalhe grava no log quem consultou (o GET faz isso no servidor).
// Só o GET traz o CPF completo: a resposta do "Conferir entrega agora" vem
// mascarada, e a tela mantém o CPF que o GET já mostrou.
import { onKeyStroke } from '@vueuse/core'
import { AlertCircle, ExternalLink, FileText, Loader2, Lock, MessageCircle, MessagesSquare, Pencil, RefreshCw, TriangleAlert, X } from 'lucide-vue-next'
import {
  ACOES_LOG,
  CAMPOS_LOG,
  FONTE_DO_CONTATO,
  MOTIVOS_ANEXO,
  dataBR,
  dataHoraBR,
  ehImagem,
  errosDaApi,
  hojeSP,
  nomeDaLinha,
  nomePlataforma,
  porqueSemContato,
  prazoUnico,
  rotuloDoPedido,
  tamanhoLegivel,
  textoAviso,
  textoNf,
  vendidoSemGarantia,
  type GarantiaDetalhe,
  type LogLinha,
  type Regras,
} from '~/lib/garantias'

const props = defineProps<{
  garantiaId: number
  regras: Regras
  // Avisos do cadastro/correção que acabou de salvar (não bloquearam).
  avisos?: string[]
  // Recarregar (ex.: depois de corrigir) sem fechar.
  versao?: number
}>()
const emit = defineEmits<{
  (e: 'fechar'): void
  (e: 'corrigir', g: GarantiaDetalhe): void
  // Algo mudou (conferiu a entrega, marcou/tirou o pedido de garantia): a
  // lista relê; com a garantia salva, a linha aberta acompanha.
  (e: 'mudou', g?: GarantiaDetalhe): void
}>()

const { api } = useApi()
const toasts = useToasts()

type Aba = 'dados' | 'atendimentos' | 'log'
const aba = ref<Aba>('dados')
const g = ref<GarantiaDetalhe | null>(null)
const carregando = ref(false)
const erro = ref('')
const avisosVisiveis = ref<string[]>([...(props.avisos || [])])
let geracao = 0

async function carregar() {
  const n = ++geracao
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<GarantiaDetalhe>(`/api/garantias/${props.garantiaId}`)
    if (n !== geracao) return
    g.value = r
  } catch (e) {
    if (n !== geracao) return
    erro.value = errosDaApi(e).geral || 'Não consegui abrir a garantia.'
  } finally {
    if (n === geracao) carregando.value = false
  }
}

// ─── conferir a entrega agora (o que o robô faz de hora em hora) ───────────
const conferindo = ref(false)
async function conferirEntrega() {
  if (!g.value || conferindo.value) return
  conferindo.value = true
  const antes = g.value.data_inicio
  try {
    const r = await api<GarantiaDetalhe>(`/api/garantias/${g.value.id}/recalcular`, { method: 'POST' })
    g.value = { ...r, cpf: g.value.cpf, cpf_completo: g.value.cpf_completo, contato: g.value.contato }
    log.value = null
    if (r.data_inicio !== antes) {
      toasts.success('Entrega atualizada', r.data_inicio ? `Início ${dataBR(r.data_inicio)} — prazos recalculados.` : 'O pedido ficou sem data de entrega.')
      emit('mudou')
    } else {
      toasts.info('Entrega conferida', r.data_inicio ? 'A data de entrega continua a mesma.' : 'O pedido ainda não tem data de entrega.')
    }
  } catch (e) {
    toasts.error('Não consegui conferir a entrega', errosDaApi(e).geral)
  } finally {
    conferindo.value = false
  }
}

// ─── pedido de garantia (28/10/2026): a pessoa corrige o robô ──────────────
const marcando = ref(false)
async function marcarPedido(pedido: boolean) {
  if (!g.value || marcando.value) return
  marcando.value = true
  try {
    const r = await api<GarantiaDetalhe>(`/api/garantias/${g.value.id}/pedido`, { method: 'PUT', body: { pedido } })
    g.value = { ...r, cpf: g.value.cpf, cpf_completo: g.value.cpf_completo, contato: g.value.contato }
    log.value = null
    toasts.success(pedido ? 'Marcada como pedido de garantia' : 'Não é pedido de garantia', pedido ? 'A garantia entra no Painel.' : 'Sai do Painel (continua na busca) e o robô não marca de novo.')
    emit('mudou', r)
  } catch (e) {
    toasts.error('Não consegui mudar o pedido de garantia', errosDaApi(e).geral)
  } finally {
    marcando.value = false
  }
}

// ─── log (admin) ────────────────────────────────────────────────────────────
const log = ref<LogLinha[] | null>(null)
const carregandoLog = ref(false)
const erroLog = ref('')
async function carregarLog() {
  if (!g.value) return
  carregandoLog.value = true
  erroLog.value = ''
  try {
    log.value = await api<LogLinha[]>(`/api/garantias/${g.value.id}/log`)
  } catch (e) {
    erroLog.value = errosDaApi(e).geral
  } finally {
    carregandoLog.value = false
  }
}
watch(aba, (a) => { if (a === 'log' && log.value === null) void carregarLog() })

// Abriu (ou trocou de garantia, ou corrigiu): relê; o log volta a ser pedido.
watch(() => [props.garantiaId, props.versao], () => {
  log.value = null
  void carregar()
}, { immediate: true })
watch(() => props.avisos, (a) => { avisosVisiveis.value = [...(a || [])] })

const abas = computed<{ v: Aba; l: string }[]>(() => {
  const d = g.value
  if (!d) return []
  const out: { v: Aba; l: string }[] = [
    { v: 'dados', l: 'Dados' },
    { v: 'atendimentos', l: `Atendimentos (${d.atendimentos_lista.length})` },
  ]
  if (d.permissoes.ver_log) out.push({ v: 'log', l: 'Log' })
  return out
})

// A barra do prazo único (a mesma conta do servidor; o dia em São Paulo).
const prazo = computed(() => (g.value ? prazoUnico(g.value, hojeSP(), props.regras.ultimo_dia_coberto) : null))
const AUTOR_ROTULO: Record<string, string> = { cliente: 'Cliente', loja: 'Loja', mediador: 'Mediador', sistema: 'Sistema', equipe: 'Equipe' }

onKeyStroke('Escape', () => emit('fechar'))
</script>

<template>
  <div class="fixed inset-0 z-50 flex justify-end bg-black/50" @click.self="emit('fechar')">
    <aside
      class="flex h-full w-full flex-col border-l bg-background shadow-2xl sm:w-[600px] sm:max-w-[94vw]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="garantia-detalhe-titulo"
      data-garantia-detalhe
    >
      <!-- cabeçalho -->
      <div class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
        <div class="min-w-0 flex-1">
          <div class="text-xs text-muted-foreground">Garantia #{{ garantiaId }}</div>
          <h2 id="garantia-detalhe-titulo" class="break-words text-base font-semibold">{{ g ? nomeDaLinha(g) : (carregando ? 'carregando…' : 'Garantia') }}</h2>
          <div v-if="g" class="mt-1 flex flex-wrap items-center gap-1.5">
            <GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" />
            <span class="text-xs text-muted-foreground">NF {{ textoNf(g.nf_numero, g.nf_serie) }} · pedido {{ g.pedido_bling }}</span>
          </div>
        </div>
        <Button size="sm" variant="ghost" aria-label="Fechar" @click="emit('fechar')"><X class="size-4" /></Button>
      </div>

      <div v-if="carregando && !g" class="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 class="size-4 animate-spin" /> abrindo a garantia…
      </div>
      <div v-else-if="erro && !g" class="m-4 space-y-2 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-300" role="alert">
        <div class="flex items-center gap-1.5"><AlertCircle class="size-4" /> {{ erro }}</div>
        <button type="button" class="text-xs underline" @click="carregar">tentar de novo</button>
      </div>

      <template v-else-if="g">
        <!-- avisos do que acabou de salvar -->
        <ul v-if="avisosVisiveis.length" class="shrink-0 space-y-1 border-b px-4 py-2" data-avisos-salvos>
          <li v-for="a in avisosVisiveis" :key="a" class="flex items-start gap-1.5 text-xs text-amber-900 dark:text-amber-200">
            <TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" /><span>{{ textoAviso(a) }}</span>
          </li>
        </ul>

        <!-- abas -->
        <div class="flex shrink-0 gap-1 border-b px-2" role="tablist" aria-label="detalhe da garantia">
          <button
            v-for="a in abas"
            :key="a.v"
            type="button"
            role="tab"
            :aria-selected="aba === a.v"
            class="-mb-px border-b-2 px-3 py-2 text-sm"
            :class="aba === a.v ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="aba = a.v"
          >{{ a.l }}</button>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4">
          <!-- ═══ DADOS ═══ -->
          <div v-if="aba === 'dados'" class="space-y-5">
            <dl class="grid grid-cols-[auto,1fr] gap-x-4 gap-y-2 text-sm" data-dados-garantia>
              <dt class="text-muted-foreground">Nome</dt><dd class="break-words">{{ g.cliente_nome }}</dd>
              <dt class="text-muted-foreground">CPF</dt>
              <dd class="font-mono">
                {{ g.cpf || '—' }}
                <span v-if="!g.cpf_completo" class="ml-1 inline-flex items-center gap-0.5 font-sans text-[11px] text-muted-foreground" title="o CPF completo só aparece para quem tem a permissão 'Ver o CPF completo'"><Lock class="size-3" /> mascarado</span>
              </dd>
              <!-- O contato do comprador (09/11/2026): do SAC do site ou do Bling -->
              <template v-if="g.contato && (g.contato.email || g.contato.telefone)">
                <dt class="text-muted-foreground">E-mail</dt>
                <dd class="break-all" data-contato-email>
                  <template v-if="g.contato.email">
                    <a v-if="g.contato.completo" :href="`mailto:${g.contato.email}`" class="underline-offset-2 hover:underline">{{ g.contato.email }}</a>
                    <span v-else>{{ g.contato.email }}</span>
                    <span class="ml-1 text-xs text-muted-foreground">({{ FONTE_DO_CONTATO[g.contato.email_fonte || ''] }})</span>
                    <span v-if="!g.contato.completo" class="ml-1 inline-flex items-center gap-0.5 text-[11px] text-muted-foreground" title="o contato completo só aparece para quem tem a permissão 'Ver o CPF completo'"><Lock class="size-3" /> mascarado</span>
                    <div v-if="g.contato.email_repassador" class="break-normal text-xs text-muted-foreground">e-mail da {{ nomePlataforma(g.plataforma) || 'plataforma' }}: só entrega a mensagem pela {{ nomePlataforma(g.plataforma) || 'plataforma' }}</div>
                  </template>
                  <span v-else class="text-muted-foreground">—</span>
                </dd>
                <dt class="text-muted-foreground">{{ g.contato.telefone && !g.contato.celular ? 'Telefone' : 'WhatsApp' }}</dt>
                <dd data-contato-telefone>
                  <template v-if="g.contato.telefone">
                    <a v-if="g.contato.whatsapp_link" :href="g.contato.whatsapp_link" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 rounded border border-emerald-400/70 px-1.5 py-0.5 text-emerald-800 hover:bg-emerald-50 dark:text-emerald-300 dark:hover:bg-emerald-900/30" title="abrir a conversa no WhatsApp"><MessageCircle class="size-3.5" aria-hidden="true" />{{ g.contato.telefone }}</a>
                    <span v-else class="font-mono">{{ g.contato.telefone }}</span>
                    <span class="ml-1 text-xs text-muted-foreground">({{ FONTE_DO_CONTATO[g.contato.telefone_fonte || ''] }})</span>
                    <span v-if="!g.contato.completo" class="ml-1 inline-flex items-center gap-0.5 text-[11px] text-muted-foreground" title="o contato completo só aparece para quem tem a permissão 'Ver o CPF completo'"><Lock class="size-3" /> mascarado</span>
                    <div v-else-if="!g.contato.celular" class="text-xs text-muted-foreground">telefone fixo: sem WhatsApp</div>
                  </template>
                  <span v-else class="text-muted-foreground">—</span>
                </dd>
              </template>
              <template v-else-if="g.contato">
                <dt class="text-muted-foreground">Contato</dt>
                <dd class="text-muted-foreground" data-contato-vazio>{{ porqueSemContato(g.contato, g.plataforma) }}</dd>
              </template>
              <dt class="text-muted-foreground">NF</dt>
              <dd>
                <span class="font-mono">{{ textoNf(g.nf_numero, g.nf_serie) }}</span>
                <div v-if="g.nf_chave" class="break-all font-mono text-[11px] text-muted-foreground" title="chave de acesso da NF-e">{{ g.nf_chave }}</div>
              </dd>
              <dt class="text-muted-foreground">Pedido</dt>
              <dd>
                <span class="font-mono">{{ g.pedido_bling }}</span> <span class="text-xs text-muted-foreground">(Bling)</span>
                <div v-if="g.pedido_marketplace" class="text-xs"><span class="font-mono">{{ g.pedido_marketplace }}</span> <span class="text-muted-foreground">{{ nomePlataforma(g.plataforma) }}<template v-if="g.conta"> · {{ g.conta }}</template></span></div>
              </dd>
              <dt class="text-muted-foreground">Produto</dt>
              <dd data-produto>
                {{ g.produto_nome || '—' }}
                <span v-if="g.produto_nome && g.produto_na_tabela === false" class="ml-1 text-xs text-amber-700 dark:text-amber-300" title="o SKU não está na Tabela de Preços › Produtos: o nome é o da nota fiscal">(fora da Tabela de Preços)</span>
              </dd>
              <template v-if="g.status_pedido">
                <dt class="text-muted-foreground">Status do pedido</dt><dd data-status-pedido>{{ g.status_pedido }} <span class="text-xs text-muted-foreground">(Bling)</span></dd>
              </template>
              <template v-if="g.sac">
                <dt class="text-muted-foreground">SAC do site</dt>
                <dd data-sac-detalhe>
                  {{ g.sac.protocolo }} · {{ dataBR(g.sac.recebido_em) }}
                  <span class="text-xs text-muted-foreground">— {{ g.sac.encerrado_em ? `encerrado em ${dataBR(g.sac.encerrado_em)}` : g.sac.confirmado_em ? 'garantia acionada' : 'aguardando confirmar' }}</span>
                </dd>
              </template>
              <dt class="text-muted-foreground">Pedido de garantia</dt>
              <dd data-pedido-de-garantia>
                <span :class="g.pedido_em && !g.nao_e_pedido ? '' : 'text-muted-foreground'">{{ rotuloDoPedido(g) }}</span>
                <span v-if="g.pedido_em && !g.nao_e_pedido" class="ml-1 text-xs text-muted-foreground">({{ dataHoraBR(g.pedido_em) }})</span>
              </dd>
              <dt class="text-muted-foreground">Cadastro</dt><dd>{{ dataHoraBR(g.criado_em) }}</dd>
              <dt class="text-muted-foreground">Cadastrado por</dt><dd>{{ g.criado_por?.nome || '—' }}</dd>
              <template v-if="g.atualizado_em">
                <dt class="text-muted-foreground">Última correção</dt><dd>{{ dataHoraBR(g.atualizado_em) }} · {{ g.atualizado_por?.nome || '—' }}</dd>
              </template>
            </dl>

            <section class="space-y-3 rounded-lg border p-3" data-prazos>
              <div class="flex flex-wrap items-baseline justify-between gap-2">
                <h3 class="text-sm font-semibold">Prazo da garantia</h3>
                <span class="text-xs text-muted-foreground">
                  início <strong class="text-foreground">{{ g.data_inicio ? dataBR(g.data_inicio) : g.entregue_sem_data ? 'sem data de entrega' : 'aguardando entrega' }}</strong>
                </span>
              </div>
              <p class="text-xs text-muted-foreground">
                <template v-if="g.entrega.data">Entrega {{ dataBR(g.entrega.data) }}<template v-if="g.entrega.origem_rotulo"> — {{ g.entrega.origem_rotulo }}</template>.</template>
                <template v-else-if="g.entregue_sem_data"><span data-entregue-sem-data-texto>O Bling já dá o pedido como entregue, mas nenhuma fonte do DaVinci tem a data de entrega (a Logística guarda a data só desde 15/07/2026). Sem ela não há prazo — a data inicial não se digita; o DaVinci confere de hora em hora.</span></template>
                <template v-else-if="vendidoSemGarantia(g)">O pedido ainda não tem data de entrega.</template>
                <template v-else>O pedido ainda não tem data de entrega: o prazo entra sozinho quando ela aparecer (o DaVinci confere de hora em hora).</template>
                <template v-if="g.entrega.verificada_em"> Conferida em {{ dataHoraBR(g.entrega.verificada_em) }}.</template>
              </p>
              <div v-if="vendidoSemGarantia(g)" class="flex items-start gap-1.5 rounded-md border bg-muted/40 px-2.5 py-2 text-xs text-muted-foreground" data-sem-garantia>
                <span><strong class="text-foreground">Sem garantia</strong> — o produto foi vendido sem garantia (prazo 0 na Tabela de Preços › Produtos, como o avulso).</span>
              </div>
              <GarantiaPrazoBarra v-else-if="g.garantia_meses" titulo="Garantia" :meses="g.garantia_meses" :prazo="prazo" />
              <div v-else class="flex items-start gap-1.5 rounded-md border border-amber-300/70 bg-amber-50 px-2.5 py-2 text-xs text-amber-900 dark:border-amber-800/60 dark:bg-amber-900/25 dark:text-amber-200" data-sem-prazo>
                <TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
                <span><strong>Prazo não cadastrado</strong> — o produto não tem o prazo da garantia na Tabela de Preços › Produtos (coluna Garantia, em meses). Sem ele a garantia fica sem data final.</span>
              </div>
            </section>

            <section v-if="g.itens.length" class="space-y-1.5">
              <h3 class="text-sm font-semibold">Itens da NF / pedido</h3>
              <ul class="space-y-1 text-sm">
                <li v-for="(it, i) in g.itens" :key="i" class="flex items-baseline gap-2 rounded-md border px-2.5 py-1.5">
                  <span class="tabular-nums text-muted-foreground">{{ it.quantidade ?? 1 }}×</span>
                  <span class="min-w-0 flex-1 break-words">{{ it.descricao || '—' }}</span>
                  <span v-if="it.sku" class="font-mono text-xs text-muted-foreground">{{ it.sku }}</span>
                  <span v-if="it.uranyx" class="rounded bg-primary/10 px-1 text-[10px] font-medium text-primary">Uranyx</span>
                </li>
              </ul>
              <p class="text-xs text-muted-foreground">Uma garantia por NF: os itens acima ficam cobertos por ela.</p>
            </section>

            <div v-if="g.permissoes.cadastrar" class="flex flex-wrap justify-end gap-2 border-t pt-3">
              <Button v-if="g.pedido_em && !g.nao_e_pedido" size="sm" variant="outline" :disabled="marcando" title="o cliente não pediu garantia: sai do Painel (continua na busca) e o robô não marca de novo" data-tirar-pedido @click="marcarPedido(false)">
                <Loader2 v-if="marcando" class="mr-1 size-4 animate-spin" /> Não é pedido de garantia
              </Button>
              <Button v-else size="sm" variant="outline" :disabled="marcando" title="o cliente pediu garantia (o robô não achou): entra no Painel" data-marcar-pedido @click="marcarPedido(true)">
                <Loader2 v-if="marcando" class="mr-1 size-4 animate-spin" /> Marcar como pedido de garantia
              </Button>
              <Button size="sm" variant="outline" :disabled="conferindo" title="relê a data de entrega na Logística agora (o robô faz isso de hora em hora)" @click="conferirEntrega">
                <Loader2 v-if="conferindo" class="mr-1 size-4 animate-spin" /><RefreshCw v-else class="mr-1 size-4" /> Conferir entrega agora
              </Button>
              <Button size="sm" @click="emit('corrigir', g)"><Pencil class="mr-1 size-4" /> Corrigir dados</Button>
            </div>
          </div>

          <!-- ═══ ATENDIMENTOS ═══ -->
          <div v-else-if="aba === 'atendimentos'" class="space-y-3">
            <p class="text-xs text-muted-foreground">
              Os atendimentos chegam pelo Atendimento (botão "Vincular à garantia" na conversa) e só se adicionam — não dá para editar nem apagar.
            </p>
            <EmptyState
              v-if="!g.atendimentos_lista.length"
              :icon="MessagesSquare"
              title="Nenhum atendimento vinculado"
              description="Quando alguém vincular uma conversa a esta garantia, ela aparece aqui, marcada como Coberto ou Fora da garantia."
            />
            <ol v-else class="relative space-y-4 border-l pl-4" data-linha-do-tempo>
              <li v-for="a in g.atendimentos_lista" :key="a.id" class="relative" :data-atendimento="a.id">
                <span class="absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-background" :class="a.cobertura === 'coberto' ? 'bg-emerald-500' : a.cobertura === 'fora_da_garantia' ? 'bg-red-500' : 'bg-amber-500'" aria-hidden="true" />
                <div class="space-y-2 rounded-lg border p-3">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <span class="text-sm font-medium tabular-nums">{{ dataHoraBR(a.data_atendimento) }}</span>
                    <GarantiaCobertura :cobertura="a.cobertura" :rotulo="a.cobertura_rotulo" />
                  </div>
                  <div class="text-xs text-muted-foreground">
                    {{ a.atendente.nome }} · <NuxtLink :to="a.conversa_link" class="inline-flex items-center gap-0.5 text-primary hover:underline">conversa {{ nomePlataforma(a.conversa_plataforma) }}<template v-if="a.conversa_conta"> · {{ a.conversa_conta }}</template><ExternalLink class="size-3" /></NuxtLink>
                    <template v-if="a.conversa_pedido"> · pedido {{ a.conversa_pedido }}</template>
                  </div>
                  <p v-if="a.fim_considerado" class="text-xs text-muted-foreground">
                    Fim considerado na hora do vínculo: {{ dataBR(a.fim_considerado) }}.
                  </p>
                  <p v-if="a.cobertura_hoje !== a.cobertura" class="text-xs text-amber-800 dark:text-amber-300" data-cobertura-hoje>
                    Com os prazos de hoje (entrega corrigida depois): {{ a.cobertura_hoje_rotulo }}.
                  </p>
                  <div class="text-sm">
                    <div class="text-xs font-medium text-muted-foreground">Resumo</div>
                    <p class="whitespace-pre-wrap break-words">{{ a.resumo }}</p>
                  </div>
                  <div class="text-sm">
                    <div class="text-xs font-medium text-muted-foreground">Solução / encaminhamento</div>
                    <p class="whitespace-pre-wrap break-words">{{ a.solucao }}</p>
                  </div>
                  <!-- anexos copiados -->
                  <div v-if="a.anexos.length" class="space-y-1.5" data-anexos-atendimento>
                    <div class="text-xs font-medium text-muted-foreground">Anexos ({{ a.anexos.length }})</div>
                    <div class="flex flex-wrap gap-2">
                      <template v-for="(ax, i) in a.anexos" :key="i">
                        <a
                          v-if="ax.baixado && ax.arquivo_url && ehImagem(ax)"
                          :href="ax.arquivo_url"
                          target="_blank"
                          rel="noopener"
                          class="block size-20 overflow-hidden rounded-md border bg-muted"
                          :title="ax.nome || 'foto'"
                        ><img :src="ax.arquivo_url" :alt="ax.nome || 'foto do atendimento'" class="size-full object-cover" loading="lazy" /></a>
                        <a
                          v-else-if="ax.baixado && ax.arquivo_url"
                          :href="ax.arquivo_url"
                          target="_blank"
                          rel="noopener"
                          class="inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted"
                        ><FileText class="size-3.5 shrink-0" /><span class="truncate">{{ ax.nome || ax.tipo }}</span><span v-if="ax.tamanho" class="text-muted-foreground">{{ tamanhoLegivel(ax.tamanho) }}</span></a>
                        <span v-else class="inline-flex max-w-full flex-col rounded-md border border-dashed px-2 py-1 text-xs text-muted-foreground" data-anexo-nao-guardado>
                          <span class="truncate">{{ ax.nome || ax.tipo }}</span>
                          <span class="text-[11px]">{{ MOTIVOS_ANEXO[ax.motivo || ''] || 'não guardado' }}</span>
                          <a v-if="ax.url_original" :href="ax.url_original" target="_blank" rel="noopener noreferrer" class="text-[11px] text-primary underline">link original (pode ter expirado)</a>
                        </span>
                      </template>
                    </div>
                  </div>
                  <!-- mensagens copiadas -->
                  <details v-if="a.mensagens.length" class="text-sm">
                    <summary class="cursor-pointer text-xs text-muted-foreground hover:text-foreground">mensagens copiadas da conversa ({{ a.mensagens.length }})</summary>
                    <ul class="mt-2 space-y-1.5">
                      <li v-for="m in a.mensagens" :key="m.id" class="rounded-md px-2 py-1" :class="m.autor === 'cliente' ? 'bg-muted' : 'bg-primary/5'">
                        <div class="text-[11px] text-muted-foreground">{{ AUTOR_ROTULO[m.autor] || m.autor }} · {{ dataHoraBR(m.enviada_em) }}</div>
                        <div class="whitespace-pre-wrap break-words">{{ m.texto || (m.anexos?.length ? '[anexo]' : '—') }}</div>
                      </li>
                    </ul>
                  </details>
                </div>
              </li>
            </ol>
          </div>

          <!-- ═══ LOG (admin) ═══ -->
          <div v-else-if="aba === 'log'" class="space-y-2 text-sm" data-log-garantia>
            <p class="text-xs text-muted-foreground">Quem consultou e quem alterou esta garantia (o CPF aparece sempre mascarado aqui).</p>
            <p v-if="carregandoLog" class="flex items-center gap-1.5 text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</p>
            <p v-else-if="erroLog" class="text-destructive" role="alert">{{ erroLog }} <button type="button" class="underline" @click="carregarLog">tentar de novo</button></p>
            <p v-else-if="log && !log.length" class="text-muted-foreground">Nada registrado ainda.</p>
            <ol v-else-if="log" class="space-y-1.5">
              <li v-for="l in log" :key="l.id" class="rounded-md border px-3 py-2">
                <div class="text-xs text-muted-foreground">{{ dataHoraBR(l.em) }} · {{ l.pessoa.nome }}</div>
                <div>
                  <span class="font-medium">{{ ACOES_LOG[l.acao] || l.acao }}</span>
                  <template v-if="l.campo"> — {{ CAMPOS_LOG[l.campo] || l.campo }}: {{ l.valor_anterior ?? '—' }} → {{ l.valor_novo ?? '—' }}</template>
                </div>
                <div v-if="l.detalhe" class="text-xs text-muted-foreground">{{ l.detalhe }}</div>
              </li>
            </ol>
          </div>
        </div>
      </template>
    </aside>
  </div>
</template>
