<script lang="ts">
// Filtros da lista — a página guarda e manda para a API (`GET /conversas`).
export type FiltrosLista = {
  plataforma: string
  integration_id: string
  canal: string
  filtro: string
  q: string
}
export const FILTROS_RAPIDOS: { value: string; label: string; hint: string }[] = [
  { value: 'aguardando', label: 'Aguardando', hint: 'o cliente falou por último e ninguém respondeu ainda' },
  { value: 'vencendo', label: 'Vencendo', hint: 'o prazo de resposta acaba em menos de 2 h' },
  { value: 'vencidas', label: 'Vencidas', hint: 'passou do prazo sem resposta' },
  { value: 'com_rascunho', label: 'Com sugestão', hint: 'a IA deixou uma resposta pronta para conferir' },
  // Resposta nossa que a plataforma não confirmou: pode ter chegado ou não.
  // Alguém precisa olhar na plataforma e marcar — o DaVinci não reenvia.
  { value: 'a_conferir', label: 'A conferir', hint: 'resposta enviada pelo DaVinci que a plataforma não confirmou — confira se chegou ao comprador' },
  { value: 'minhas', label: 'Minhas', hint: 'conversas atribuídas a você' },
  { value: 'todas', label: 'Todas', hint: 'abertas e respondidas' },
  { value: 'fechadas', label: 'Fechadas', hint: 'fechadas por alguém da equipe' },
]
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
import { Bot, Inbox, Loader2, Lock, PauseCircle, RotateCcw, Search, Sparkles, TriangleAlert, UserRound, X } from 'lucide-vue-next'
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
const contagem = computed((): Record<string, number | null> => {
  const r = props.resumo
  if (!r) return {}
  const f = filtros.value
  // "A conferir": o total vem no topo do resumo; por loja/plataforma só se o
  // backend separar — sem o número certo, melhor não mostrar número nenhum.
  if (f.integration_id) {
    const l = r.lojas.find((x) => x.integration_id === f.integration_id)
    return { aguardando: l?.aguardando ?? 0, vencidas: l?.vencidas ?? 0, vencendo: null, a_conferir: l?.a_conferir ?? null }
  }
  const ps = f.plataforma ? r.plataformas.filter((p) => p.plataforma === f.plataforma) : r.plataformas
  const porPlataforma = ps.length > 0 && ps.every((p) => typeof p.a_conferir === 'number')
  return {
    aguardando: ps.reduce((s, p) => s + (p.aguardando || 0), 0),
    vencendo: ps.reduce((s, p) => s + (p.vencendo || 0), 0),
    vencidas: ps.reduce((s, p) => s + (p.vencidas || 0), 0),
    a_conferir: porPlataforma ? ps.reduce((s, p) => s + (p.a_conferir || 0), 0) : (f.plataforma ? null : (r.a_conferir ?? null)),
  }
})
function contadorCls(value: string, n: number | null | undefined) {
  if (!n) return 'bg-muted text-muted-foreground'
  if (value === 'vencidas') return 'bg-red-500 text-white'
  if (value === 'vencendo' || value === 'a_conferir') return 'bg-amber-500 text-white'
  return 'bg-primary/15 text-primary'
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
function temSelos(c: ConversaResumo) {
  return !!(c.envio_a_conferir || c.tem_rascunho || c.atribuido_a || c.ia_pausada || c.somente_leitura || c.sem_resposta_necessaria || c.situacao === 'bloqueada' || c.situacao === 'fechada')
}
// Na linha escolhida (fundo azul) os selos coloridos viram translúcidos —
// âmbar/violeta em cima do azul não se lê.
function selo(sel: boolean, cls: string) {
  return sel ? 'bg-white/20 text-current' : cls
}

const VAZIO: Record<string, string> = {
  aguardando: 'Nada esperando resposta agora.',
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
          placeholder="buscar comprador, pedido, anúncio…"
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
      <!-- filtros rápidos -->
      <div class="flex flex-wrap gap-1">
        <button
          v-for="f in FILTROS_RAPIDOS"
          :key="f.value"
          type="button"
          class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors"
          :class="filtros.filtro === f.value ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted'"
          :title="f.hint"
          :aria-pressed="filtros.filtro === f.value"
          @click="mudar('filtro', f.value)"
        >
          {{ f.label }}
          <span
            v-if="contagem[f.value] !== undefined && contagem[f.value] !== null"
            class="min-w-[18px] rounded-full px-1 text-center text-[10px] font-semibold tabular-nums"
            :class="filtros.filtro === f.value ? 'bg-primary-foreground/25 text-primary-foreground' : contadorCls(f.value, contagem[f.value])"
          >{{ contagem[f.value] }}</span>
        </button>
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
            class="flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition-colors"
            :class="[
              c.id === selecionada ? 'bg-primary text-primary-foreground shadow-sm' : 'hover:bg-muted/70',
              c.situacao === 'fechada' && c.id !== selecionada ? 'opacity-60' : '',
            ]"
            @click="emit('selecionar', c.id)"
          >
            <AtendimentoAvatar
              class="mt-0.5"
              :nome="c.comprador_nome || c.pedido_marketplace"
              :foto="c.comprador_avatar"
              :plataforma="c.plataforma"
              :nao-lidas="c.nao_lidas"
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
