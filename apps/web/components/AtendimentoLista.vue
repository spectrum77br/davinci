<script lang="ts">
// Filtros da lista — a página guarda e manda para a API (`GET /conversas`).
export type FiltrosLista = {
  plataforma: string
  integration_id: string
  canal: string
  filtro: string
  q: string
}
type OpcaoFiltro = { value: string; label: string; hint: string }
// Como o Duoke (01/10/2026): duas abas em cima — "Todas" (o All, onde a lista
// abre) e "Falta responder" (a Fila) — e o resto no menu "Filtrar" ao lado.
export const ABAS_LISTA: OpcaoFiltro[] = [
  { value: 'todas', label: 'Todas', hint: 'todas as conversas abertas, da mais recente para a mais antiga' },
  { value: 'aguardando', label: 'Falta responder', hint: 'o comprador falou por último e ninguém respondeu de verdade (resposta automática não conta)' },
]
export const FILTROS_MENU: OpcaoFiltro[] = [
  { value: 'vencendo', label: 'Vencendo', hint: 'o prazo de resposta acaba em menos de 2 h' },
  { value: 'vencidas', label: 'Vencidas', hint: 'passou do prazo sem resposta' },
  { value: 'automatica', label: 'Só resposta automática', hint: 'o robô do Duoke (ou uma campanha) respondeu e nenhuma pessoa ainda' },
  { value: 'com_rascunho', label: 'Com sugestão da IA', hint: 'a IA deixou uma resposta pronta para conferir' },
  // Resposta nossa que a plataforma não confirmou: pode ter chegado ou não.
  // Alguém precisa olhar na plataforma e marcar — o DaVinci não reenvia.
  { value: 'a_conferir', label: 'A conferir', hint: 'resposta enviada pelo DaVinci que a plataforma não confirmou — confira se chegou ao comprador' },
  { value: 'minhas', label: 'Minhas', hint: 'conversas atribuídas a você' },
  { value: 'fechadas', label: 'Fechadas', hint: 'fechadas por alguém da equipe' },
  // Pela ETIQUETA (status atual, 01/10/2026), da mais urgente para a menos —
  // os mesmos códigos da API (`filtro=reclamacao`…). Pré-venda e Pós-venda
  // passaram a ser a etiqueta: a conversa com reclamação aberta está em
  // Reclamação, não em Pós-venda.
  { value: 'reclamacao', label: 'Reclamação', hint: 'reclamação ou mediação aberta na plataforma' },
  { value: 'ag_cancelamento', label: 'Ag. cancelamento', hint: 'pedido em Aguardando Cancelamento no Bling (fora a trava do robô da Margem)' },
  { value: 'devolucao', label: 'Devolução', hint: 'devolução aberta na plataforma ou pedido em Aguardando Devolução no Bling' },
  { value: 'pre_venda', label: 'Pré-venda', hint: 'perguntas e conversas sem pedido ligado' },
  { value: 'pos_venda', label: 'Pós-venda', hint: 'conversa de um pedido sem nada aberto (sem reclamação, devolução nem Ag. cancelamento)' },
]
// Os filtros do menu que são ETIQUETA (contam pelo /resumo `etiquetas`).
export const FILTROS_ETIQUETA = new Set(['reclamacao', 'ag_cancelamento', 'devolucao', 'pre_venda', 'pos_venda'])
export const FILTROS_RAPIDOS: OpcaoFiltro[] = [...ABAS_LISTA, ...FILTROS_MENU]
</script>

<script setup lang="ts">
// Coluna da lista na Caixa (Atendimento, 25/09/2026; cara do Duoke em
// 28/09/2026): a fila de conversas de todas as lojas. Cada linha como no
// Duoke — avatar redondo (foto ou iniciais) com o mini-ícone da plataforma e a
// bolinha vermelha de não lidas; "comprador | loja" e a hora (HH:MM hoje,
// DD/MM antes); a prévia numa linha ("[Pedido]", "[Imagem]" quando não é
// texto); a conversa aberta em azul. O que é nosso e o Duoke não tem fica
// discreto: selo de prazo, "IA sugeriu", "a conferir", atribuída.
// Os contadores dos filtros rápidos vêm do /resumo (a lista é paginada,
// contar os itens carregados mentiria). Setas ↑/↓ andam pela lista sem mouse.
// A plataforma e a loja se escolhem na barra de lojas (AtendimentoLojas); em
// tela estreita, onde a barra some, voltam os dois seletores aqui.
import { onClickOutside } from '@vueuse/core'
import { Bot, Check, Inbox, ListFilter, Loader2, Lock, PauseCircle, RotateCcw, Search, Sparkles, TriangleAlert, UserRound, X } from 'lucide-vue-next'
import { ETIQUETAS_INFO, faixaDaEtiqueta, secundariasDe } from '~/components/AtendimentoEtiqueta.vue'
import {
  PLATAFORMAS_ATENDIMENTO,
  canaisDa,
  canalLabel,
  horaLista,
  plataformaInfo,
  prazoDe,
  variasCaixas,
  viaRobo,
  type ConversaResumo,
  type Resumo,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  itens: ConversaResumo[]
  carregando: boolean
  carregandoMais: boolean
  erro: string | null
  temMais: boolean
  selecionada: string | null
  resumo: Resumo | null
  agora: number
  meuId: string | null
}>()
const filtros = defineModel<FiltrosLista>('filtros', { required: true })
const emit = defineEmits<{
  (e: 'selecionar', id: string): void
  (e: 'carregarMais'): void
  (e: 'recarregar'): void
}>()

function mudar<K extends keyof FiltrosLista>(k: K, v: FiltrosLista[K]) {
  const novo = { ...filtros.value, [k]: v }
  // Trocou a plataforma: a loja e o canal de antes não valem mais.
  if (k === 'plataforma') {
    novo.integration_id = ''
    novo.canal = ''
  }
  filtros.value = novo
}

// Busca com espera de 300 ms (mesmo ritmo de Chamados) — digitar "12345" não
// dispara cinco consultas.
const busca = ref(filtros.value.q)
let buscaTimer: ReturnType<typeof setTimeout> | null = null
watch(busca, (v) => {
  if (buscaTimer) clearTimeout(buscaTimer)
  buscaTimer = setTimeout(() => mudar('q', v.trim()), 300)
})
watch(() => filtros.value.q, (q) => { if (q !== busca.value.trim()) busca.value = q })
onBeforeUnmount(() => { if (buscaTimer) clearTimeout(buscaTimer) })

// ─── contadores (do /resumo) ────────────────────────────────────────────────
// Loja escolhida → números da loja (o resumo por loja não separa "vencendo");
// plataforma → da plataforma; nada → soma de tudo.
// Etiquetas (Reclamação, Devolução…): conversas abertas com aquela etiqueta,
// do `etiquetas` do /resumo no mesmo nível. A API antiga não manda: sem número.
function porEtiqueta(contagens: (Record<string, number> | undefined)[]): Record<string, number | null> {
  const out: Record<string, number | null> = {}
  const tem = contagens.length > 0 && contagens.every((c) => !!c && typeof c === 'object')
  for (const e of FILTROS_ETIQUETA) out[e] = tem ? contagens.reduce((s, c) => s + (Number(c?.[e]) || 0), 0) : null
  return out
}
const contagem = computed((): Record<string, number | null> => {
  const r = props.resumo
  if (!r) return {}
  const f = filtros.value
  // "A conferir": o total vem no topo do resumo; por loja/plataforma só se o
  // backend separar — sem o número certo, melhor não mostrar número nenhum.
  if (f.integration_id) {
    const l = r.lojas.find((x) => x.integration_id === f.integration_id)
    return {
      aguardando: l?.aguardando ?? 0,
      vencidas: l?.vencidas ?? 0,
      vencendo: null,
      a_conferir: l?.a_conferir ?? null,
      ...porEtiqueta(l ? [l.etiquetas] : []),
    }
  }
  const ps = f.plataforma ? r.plataformas.filter((p) => p.plataforma === f.plataforma) : r.plataformas
  const porPlataforma = ps.length > 0 && ps.every((p) => typeof p.a_conferir === 'number')
  return {
    aguardando: ps.reduce((s, p) => s + (p.aguardando || 0), 0),
    vencendo: ps.reduce((s, p) => s + (p.vencendo || 0), 0),
    vencidas: ps.reduce((s, p) => s + (p.vencidas || 0), 0),
    a_conferir: porPlataforma ? ps.reduce((s, p) => s + (p.a_conferir || 0), 0) : (f.plataforma ? null : (r.a_conferir ?? null)),
    // Sem plataforma escolhida, o total do topo; com ela, o da plataforma
    // (a que não tem conversa não manda `etiquetas`: conta zero).
    ...(f.plataforma
      ? porEtiqueta(ps.length ? ps.map((p) => p.etiquetas || {}) : [])
      : porEtiqueta([r.etiquetas])),
  }
})
function contadorCls(value: string, n: number | null | undefined) {
  if (!n) return 'bg-muted text-muted-foreground'
  if (value === 'vencidas') return 'bg-red-500 text-white'
  if (value === 'vencendo' || value === 'a_conferir') return 'bg-amber-500 text-white'
  // Etiqueta: a cor dela (Reclamação vermelho, Devolução roxo…).
  if (FILTROS_ETIQUETA.has(value)) return ETIQUETAS_INFO[value]?.cls || 'bg-primary/15 text-primary'
  return 'bg-primary/15 text-primary'
}

// ─── menu "Filtrar" ─────────────────────────────────────────────────────────
const menuAberto = ref(false)
const menuRef = ref<HTMLElement | null>(null)
onClickOutside(menuRef, () => { menuAberto.value = false })
const filtroDoMenu = computed(() => FILTROS_MENU.find((f) => f.value === filtros.value.filtro) ?? null)
function escolherDoMenu(value: string) {
  menuAberto.value = false
  // Clicar de novo no filtro escolhido tira o filtro (volta para Todas).
  mudar('filtro', filtros.value.filtro === value ? 'todas' : value)
}

function aguardandoDaPlataforma(p: string): number | null {
  const x = props.resumo?.plataformas.find((y) => y.plataforma === p)
  return x ? x.aguardando : null
}
// O Instagram só entra na escolha quando o /resumo o traz (quem vê todas as
// equipes, havendo DM) — como na barra de lojas: para os outros a opção só
// levava a uma lista vazia. Se o filtro já está nele, fica, para aparecer.
// Temu e AliExpress (robô do Mac mini), do mesmo jeito: só quando o /resumo
// tem a plataforma ou uma loja dela — antes do robô existir, a opção só
// levava a uma lista vazia (ou recusada pela API). A Magalu (30/09/2026)
// também: o backend que ainda não lê a Magalu recusa o filtro
// (`plataforma_invalida`); o que já lê manda a plataforma no /resumo.
const SO_COM_RESUMO = new Set(['instagram', 'magalu'])
const plataformasDoFiltro = computed(() =>
  PLATAFORMAS_ATENDIMENTO.filter((p) =>
    (!SO_COM_RESUMO.has(p.value) && !viaRobo(p.value))
    || filtros.value.plataforma === p.value
    || !!props.resumo?.plataformas.some((y) => y.plataforma === p.value)
    || ((viaRobo(p.value) || SO_COM_RESUMO.has(p.value)) && !!props.resumo?.lojas.some((l) => l.plataforma === p.value))),
)

const lojas = computed(() => {
  const ls = props.resumo?.lojas || []
  const f = filtros.value.plataforma
  // Conversa de loja que saiu do DaVinci fica sem integration_id: não dá
  // para filtrar por ela (aparece em "todas lojas").
  return ls
    .filter((l): l is typeof l & { integration_id: string } => !!l.integration_id)
    .filter((l) => !f || l.plataforma === f)
    .slice()
    .sort((a, b) => a.plataforma.localeCompare(b.plataforma) || (a.conta || '').localeCompare(b.conta || '', 'pt-BR'))
})
const canais = computed(() => canaisDa(filtros.value.plataforma))

// ─── itens ──────────────────────────────────────────────────────────────────
function titulo(c: ConversaResumo) {
  return c.comprador_nome || (c.pedido_marketplace ? `Pedido ${c.pedido_marketplace}` : 'Comprador')
}
function loja(c: ConversaResumo) {
  const nome = c.conta || plataformaInfo(c.plataforma).nome
  // Só onde a loja tem mais de uma caixa o canal ajuda (ML: Pergunta ×
  // Pós-venda; Magalu: Pergunta × Chat × SAC); nos outros é sempre o mesmo.
  return variasCaixas(c.plataforma) ? `${nome} · ${canalLabel(c.canal)}` : nome
}
// Prévia como no Duoke: o que não é texto vira "[Pedido]", "[Produto]",
// "[Imagem]" — o cartão do pedido não tem texto, e a linha ficava vazia.
const PREVIA_TIPO: Record<string, string> = {
  imagem: '[Imagem]',
  produto: '[Produto]',
  pedido: '[Pedido]',
  video: '[Vídeo]',
  arquivo: '[Arquivo]',
  outro: '[Mensagem]',
}
function previa(c: ConversaResumo) {
  let t = (c.ultima_mensagem_resumo || '').trim()
  const tipo = (c.ultima_mensagem_tipo || '').toLowerCase()
  // Mensagem sem texto: o backend guarda o tipo cru no resumo ("[pedido]",
  // gravar.recalcular) — vira o rótulo do Duoke ("[Pedido]").
  const cru = /^\[([a-z_]+)\]$/.exec(t)
  if (cru) t = PREVIA_TIPO[cru[1]] || PREVIA_TIPO[tipo] || PREVIA_TIPO.outro
  const marca = tipo && tipo !== 'texto' && !t.startsWith('[') ? PREVIA_TIPO[tipo] || '' : ''
  const corpo = [marca, t].filter(Boolean).join(' ')
  if (!corpo) return c.anuncio_titulo || ''
  return c.ultima_autor === 'loja' ? `Loja: ${corpo}` : corpo
}
// A etiqueta na linha: selo só das que pedem atenção (Pós-venda sem
// destaque), mais o indicador das secundárias e a mão da troca manual.
function temEtiqueta(c: ConversaResumo) {
  return !!(faixaDaEtiqueta(c.etiqueta) || secundariasDe(c.etiqueta, c.etiquetas_secundarias).length)
}
function temSelos(c: ConversaResumo) {
  return !!(temEtiqueta(c) || c.envio_a_conferir || c.tem_rascunho || c.atribuido_a || c.ia_pausada || c.somente_leitura || c.sem_resposta_necessaria || c.situacao === 'bloqueada' || c.situacao === 'fechada')
}
// Na linha escolhida (fundo azul) os selos coloridos viram translúcidos —
// âmbar/violeta em cima do azul não se lê.
function selo(sel: boolean, cls: string) {
  return sel ? 'bg-white/20 text-current' : cls
}

const VAZIO: Record<string, string> = {
  aguardando: 'Nada esperando resposta agora.',
  automatica: 'Nenhuma conversa só com a resposta automática.',
  pre_venda: 'Nenhuma conversa de pré-venda com esses filtros.',
  pos_venda: 'Nenhuma conversa de pós-venda com esses filtros.',
  reclamacao: 'Nenhuma reclamação aberta com esses filtros.',
  devolucao: 'Nenhuma devolução aberta com esses filtros.',
  ag_cancelamento: 'Nenhum pedido em Aguardando Cancelamento com esses filtros.',
  vencendo: 'Nenhuma conversa perto de vencer.',
  vencidas: 'Nenhuma conversa vencida.',
  com_rascunho: 'Nenhuma sugestão da IA esperando conferência.',
  a_conferir: 'Nenhuma resposta esperando conferência — tudo que saiu pelo DaVinci foi confirmado.',
  minhas: 'Nenhuma conversa atribuída a você.',
  fechadas: 'Nenhuma conversa fechada com esses filtros.',
  todas: 'Nenhuma conversa com esses filtros.',
}
const textoVazio = computed(() => (filtros.value.q ? 'Nada encontrado para essa busca.' : VAZIO[filtros.value.filtro] || VAZIO.todas))

// ↑/↓ com o foco na lista: anda pelas conversas (a equipe responde em
// sequência — tirar a mão do teclado a cada conversa cansa).
const listaRef = ref<HTMLElement | null>(null)
// Acessibilidade: o foco fica no listbox (o <ul>, uma parada de Tab só) e o
// leitor de tela anuncia a conversa escolhida pelo `aria-activedescendant`.
// Por isso os itens não entram no Tab (tabindex -1) e o <li> vira
// `presentation` — um listitem entre o listbox e a option quebra a relação.
// Erro, vazio e "carregar mais" ficam fora do listbox (não são opções).
function opcaoId(id: string) {
  return `atd-conversa-${id}`
}
const ativa = computed(() => (props.selecionada && props.itens.some((c) => c.id === props.selecionada) ? opcaoId(props.selecionada) : undefined))
function mover(delta: number) {
  if (!props.itens.length) return
  const i = props.itens.findIndex((c) => c.id === props.selecionada)
  const j = i < 0 ? 0 : Math.min(props.itens.length - 1, Math.max(0, i + delta))
  const alvo = props.itens[j]
  if (!alvo) return
  emit('selecionar', alvo.id)
  nextTick(() => listaRef.value?.querySelector<HTMLElement>(`[data-conversa="${CSS.escape(alvo.id)}"]`)?.scrollIntoView({ block: 'nearest' }))
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <!-- filtros -->
    <div class="shrink-0 space-y-2 border-b p-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          v-model="busca"
          class="h-8 w-full rounded-md border bg-background pl-8 pr-7 text-sm"
          placeholder="buscar comprador, pedido, nº do Bling, SKU…"
          aria-label="buscar conversas"
        />
        <button v-if="busca" type="button" class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted" title="limpar busca" @click="busca = ''">
          <X class="size-3.5" />
        </button>
      </div>
      <!-- Plataforma e loja: na barra de lojas em tela larga; aqui só quando ela
           some (tela estreita). A caixa do ML (Pergunta/Pós-venda) fica sempre. -->
      <div class="gap-1.5" :class="canais.length > 1 ? 'flex' : 'flex lg:hidden'">
        <select
          :value="filtros.plataforma"
          class="h-8 min-w-0 flex-1 rounded-md border bg-background px-1.5 text-xs lg:hidden"
          aria-label="plataforma"
          @change="mudar('plataforma', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">todas plataformas</option>
          <option v-for="p in plataformasDoFiltro" :key="p.value" :value="p.value">
            {{ p.nome }}<template v-if="aguardandoDaPlataforma(p.value)"> ({{ aguardandoDaPlataforma(p.value) }})</template>
          </option>
        </select>
        <select
          :value="filtros.integration_id"
          class="h-8 min-w-0 flex-1 rounded-md border bg-background px-1.5 text-xs lg:hidden"
          aria-label="loja"
          :disabled="filtros.plataforma === 'instagram'"
          @change="mudar('integration_id', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">todas lojas</option>
          <option v-for="l in lojas" :key="l.integration_id" :value="l.integration_id">
            {{ filtros.plataforma ? '' : `${plataformaInfo(l.plataforma).curto} · ` }}{{ l.conta || 'sem nome' }}<template v-if="l.aguardando"> ({{ l.aguardando }})</template>
          </option>
        </select>
        <select
          v-if="canais.length > 1"
          :value="filtros.canal"
          class="h-8 min-w-[92px] shrink-0 rounded-md border bg-background px-1.5 text-xs lg:flex-1"
          aria-label="canal"
          @change="mudar('canal', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">{{ plataformaInfo(filtros.plataforma).curto }}: todas as caixas</option>
          <option v-for="c in canais" :key="c.value" :value="c.value">{{ c.label }}</option>
        </select>
      </div>
      <!-- abas (Todas / Falta responder) + menu Filtrar, como o Duoke -->
      <div class="flex items-end gap-2 border-b">
        <div class="flex min-w-0 flex-1 items-end gap-3" role="tablist" aria-label="conversas">
          <button
            v-for="a in ABAS_LISTA"
            :key="a.value"
            type="button"
            role="tab"
            class="-mb-px inline-flex items-center gap-1 border-b-2 px-0.5 pb-1.5 text-xs font-medium transition-colors"
            :class="filtros.filtro === a.value ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
            :title="a.hint"
            :aria-selected="filtros.filtro === a.value"
            @click="mudar('filtro', a.value)"
          >
            {{ a.label }}
            <span
              v-if="a.value === 'aguardando' && contagem.aguardando"
              class="min-w-[18px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold tabular-nums text-white"
            >{{ contagem.aguardando > 99 ? '99+' : contagem.aguardando }}</span>
          </button>
        </div>
        <div ref="menuRef" class="relative mb-1 shrink-0" @keydown.esc="menuAberto = false">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-md border px-2 text-[11px] transition-colors"
            :class="filtroDoMenu ? 'border-primary bg-primary/10 text-primary' : 'hover:bg-muted'"
            aria-haspopup="menu"
            :aria-expanded="menuAberto"
            @click="menuAberto = !menuAberto"
          >
            <ListFilter class="size-3.5" /> Filtrar
          </button>
          <div
            v-if="menuAberto"
            role="menu"
            aria-label="filtrar conversas"
            class="absolute right-0 z-30 mt-1 w-60 rounded-md border bg-background p-1 shadow-lg"
          >
            <template v-for="(f, i) in FILTROS_MENU" :key="f.value">
              <div
                v-if="FILTROS_ETIQUETA.has(f.value) && (i === 0 || !FILTROS_ETIQUETA.has(FILTROS_MENU[i - 1].value))"
                role="separator"
                class="mx-2 mt-1 border-t pt-1 text-[10px] uppercase tracking-wide text-muted-foreground"
              >Etiqueta</div>
              <button
                type="button"
                role="menuitemradio"
                :aria-checked="filtros.filtro === f.value"
                class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
                :class="filtros.filtro === f.value ? 'font-medium text-primary' : ''"
                :title="f.hint"
                @click="escolherDoMenu(f.value)"
              >
                <span class="min-w-0 flex-1 truncate">{{ f.label }}</span>
                <span
                  v-if="contagem[f.value] !== undefined && contagem[f.value] !== null"
                  class="min-w-[18px] rounded-full px-1 text-center text-[10px] font-semibold tabular-nums"
                  :class="contadorCls(f.value, contagem[f.value])"
                >{{ contagem[f.value] }}</span>
                <Check v-if="filtros.filtro === f.value" class="size-3.5 shrink-0" />
              </button>
            </template>
          </div>
        </div>
      </div>
      <div v-if="filtroDoMenu" class="flex items-center gap-1 text-[11px]">
        <span class="text-muted-foreground">Filtro:</span>
        <span class="inline-flex items-center gap-1 rounded-full border border-primary bg-primary/10 px-2 py-0.5 text-primary" :title="filtroDoMenu.hint">
          {{ filtroDoMenu.label }}
          <button type="button" class="rounded-full hover:bg-primary/20" aria-label="tirar o filtro" @click="mudar('filtro', 'todas')">
            <X class="size-3" />
          </button>
        </span>
      </div>
    </div>

    <!-- itens -->
    <div ref="listaRef" class="min-h-0 flex-1 overflow-y-auto">
      <div v-if="erro" class="m-2 space-y-1 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
        <div>{{ erro }}</div>
        <button type="button" class="inline-flex items-center gap-1 underline" @click="emit('recarregar')"><RotateCcw class="size-3" /> tentar de novo</button>
      </div>

      <div v-if="carregando && !itens.length" class="space-y-2 p-3" aria-busy="true">
        <div v-for="i in 6" :key="i" class="space-y-1.5 rounded-md border p-2">
          <div class="h-3 w-1/3 animate-pulse rounded bg-muted" />
          <div class="h-3 w-2/3 animate-pulse rounded bg-muted" />
          <div class="h-3 w-full animate-pulse rounded bg-muted" />
        </div>
      </div>

      <div v-else-if="!itens.length && !erro" class="px-4 py-10 text-center text-sm text-muted-foreground">
        <Inbox class="mx-auto mb-2 size-6 opacity-60" />
        {{ textoVazio }}
        <div v-if="filtros.filtro !== 'todas'" class="mt-2">
          <button type="button" class="text-xs underline" @click="mudar('filtro', 'todas')">ver todas</button>
        </div>
      </div>

      <ul
        v-else
        class="space-y-0.5 px-1.5 py-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary"
        tabindex="0"
        role="listbox"
        aria-label="conversas"
        :aria-activedescendant="ativa"
        :aria-busy="carregando || undefined"
        @keydown.down.prevent="mover(1)"
        @keydown.up.prevent="mover(-1)"
      >
        <li v-for="c in itens" :key="c.id" role="presentation">
          <button
            :id="opcaoId(c.id)"
            type="button"
            role="option"
            tabindex="-1"
            :aria-selected="c.id === selecionada"
            :data-conversa="c.id"
            class="relative flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition-colors"
            :class="[
              c.id === selecionada ? 'bg-primary text-primary-foreground shadow-sm' : 'hover:bg-muted/70',
              c.situacao === 'fechada' && c.id !== selecionada ? 'opacity-60' : '',
            ]"
            :data-etiqueta="c.etiqueta || undefined"
            @click="emit('selecionar', c.id)"
          >
            <!-- Faixa da etiqueta (Reclamação vermelho, Ag. cancelamento laranja,
                 Devolução roxo, Pré-venda azul; Pós-venda sem destaque). -->
            <span
              v-if="faixaDaEtiqueta(c.etiqueta)"
              class="absolute inset-y-1.5 left-0 w-1 rounded-full"
              :class="faixaDaEtiqueta(c.etiqueta)"
              aria-hidden="true"
              data-faixa
            />
            <AtendimentoAvatar
              class="mt-0.5"
              :nome="c.comprador_nome || c.pedido_marketplace"
              :foto="c.comprador_avatar"
              :plataforma="c.plataforma"
              :nao-lidas="c.pendentes ?? c.nao_lidas"
              :tamanho="40"
            />
            <span class="min-w-0 flex-1">
              <span class="flex items-center gap-1 text-[13px] leading-5">
                <span class="min-w-0 truncate" :class="c.aguardando_resposta ? 'font-semibold' : 'font-medium'" :title="titulo(c)">{{ titulo(c) }}</span>
                <span class="shrink-0 opacity-40" aria-hidden="true">|</span>
                <span class="min-w-0 max-w-[48%] shrink-[2] truncate" :class="c.id === selecionada ? '' : 'text-foreground/80'" :title="loja(c)">{{ loja(c) }}</span>
                <span
                  class="ml-auto shrink-0 pl-1 text-[11px] tabular-nums"
                  :class="c.id === selecionada ? 'text-primary-foreground/80' : 'text-muted-foreground'"
                  :title="c.ultima_mensagem_em ? new Date(c.ultima_mensagem_em).toLocaleString('pt-BR') : ''"
                >{{ horaLista(c.ultima_mensagem_em, agora) }}</span>
              </span>
              <span class="mt-0.5 flex items-center gap-1.5">
                <span
                  class="min-w-0 flex-1 truncate text-xs"
                  :class="c.id === selecionada ? 'text-primary-foreground/85' : 'text-muted-foreground'"
                  :title="previa(c)"
                >{{ previa(c) || '—' }}</span>
                <span
                  v-if="prazoDe(c, agora) && prazoDe(c, agora)!.nivel !== 'ok'"
                  class="shrink-0 rounded px-1 py-px text-[10px] font-medium"
                  :class="selo(c.id === selecionada, prazoDe(c, agora)!.cls)"
                  :title="prazoDe(c, agora)!.titulo"
                >{{ prazoDe(c, agora)!.texto }}</span>
                <span
                  v-else-if="prazoDe(c, agora)"
                  class="shrink-0 text-[10px] tabular-nums"
                  :class="c.id === selecionada ? 'text-primary-foreground/70' : 'text-muted-foreground'"
                  :title="prazoDe(c, agora)!.titulo"
                >{{ prazoDe(c, agora)!.texto }}</span>
              </span>
              <span v-if="temSelos(c)" class="mt-1 flex flex-wrap items-center gap-1 text-[10px]">
                <AtendimentoEtiqueta
                  v-if="temEtiqueta(c)"
                  :etiqueta="c.etiqueta"
                  :secundarias="c.etiquetas_secundarias"
                  :manual="c.etiqueta_manual"
                  :desde="c.etiqueta_desde"
                  :selecionada="c.id === selecionada"
                  esconder-pos-venda
                />
                <span
                  v-if="c.envio_a_conferir"
                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-px font-medium"
                  :class="selo(c.id === selecionada, 'bg-amber-500/20 text-amber-800 dark:text-amber-300')"
                  title="a plataforma não confirmou uma resposta nossa — abra e marque se ela chegou ao comprador"
                >
                  <TriangleAlert class="size-3" /> a conferir
                </span>
                <span v-if="c.tem_rascunho" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px font-medium" :class="selo(c.id === selecionada, 'bg-violet-500/15 text-violet-700 dark:text-violet-300')">
                  <Sparkles class="size-3" /> IA sugeriu
                </span>
                <span v-if="c.atribuido_a" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-sky-500/15 text-sky-700 dark:text-sky-300')" :title="`atribuída a ${c.atribuido_a_nome || 'alguém'}`">
                  <UserRound class="size-3" /> {{ c.atribuido_a === meuId ? 'você' : (c.atribuido_a_nome || 'atribuída') }}
                </span>
                <span v-if="c.ia_pausada" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')" title="a IA não sugere nesta conversa">
                  <PauseCircle class="size-3" /><Bot class="size-3" /> pausada
                </span>
                <span v-if="c.sem_resposta_necessaria" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')">não precisa de resposta</span>
                <span v-if="c.situacao === 'bloqueada'" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-red-500/15 text-red-700 dark:text-red-300')">bloqueada</span>
                <span v-if="c.situacao === 'fechada'" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')">fechada</span>
                <span v-if="c.somente_leitura" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')" title="só leitura no Atendimento">
                  <Lock class="size-3" /> só leitura
                </span>
              </span>
            </span>
          </button>
        </li>
      </ul>

      <div v-if="temMais && itens.length" class="p-2">
        <button
          type="button"
          class="flex w-full items-center justify-center gap-1.5 rounded-md border py-1.5 text-xs hover:bg-muted disabled:opacity-60"
          :disabled="carregandoMais"
          @click="emit('carregarMais')"
        >
          <Loader2 v-if="carregandoMais" class="size-3.5 animate-spin" />
          carregar mais
        </button>
      </div>
    </div>
  </div>
</template>
