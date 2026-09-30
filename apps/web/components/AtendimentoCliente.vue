<script lang="ts">
// A escolha "linha do tempo aberta/fechada" fica fora do componente: o painel
// é remontado a cada troca de conversa, e quem abriu a linha do tempo numa
// conversa quer vê-la aberta na próxima (também lembrado no navegador).
let linhaAbertaMemoria: boolean | null = null
const LINHA_KEY = 'davinci.atendimento.linhaDoTempo'
</script>

<script setup lang="ts">
// Cartão "Cliente" no topo do painel da direita (Atendimento, parte 2 — P5,
// 28/09/2026): quem é este comprador PARA A LOJA, antes de responder. Bem
// objetivo, em duas linhas, porque quem atende passa o olho e segue:
//   1ª: "Cliente desde mar/2026 · 3 compras (R$ 1.840,00) · última 24/09"
//   2ª: devoluções, cancelamentos, a nota da avaliação (★) e as perguntas
//       nos anúncios ("perguntou antes de comprar", quando dá para afirmar).
// Embaixo, recolhível, a linha do tempo (pergunta → compra → envio →
// entrega → avaliação/reclamação), a mais recente em cima; clicar leva ao
// pedido ou à outra conversa. Os selos de alerta (avaliou mal, reclamação
// aberta…) ficam no cabeçalho da conversa — aqui não se repetem.
// Os números vêm do backend (ML ao vivo pelo comprador; Shopee pelo índice
// próprio de pedidos e avaliações; TikTok/Amazon só o pedido da conversa):
// a tela não inventa nada — sem dado, o cartão nem aparece — e não afirma
// mais do que o backend afirma: com `historico_completo` falso, "Cliente
// desde" e "N compras" viram "visto desde" e "pelo menos N compras".
import {
  ChevronDown,
  ChevronRight,
  MessageCircleQuestion,
  MessageSquare,
  Package,
  PackageCheck,
  ShoppingBag,
  Star,
  TriangleAlert,
  Truck,
  UserRound,
} from 'lucide-vue-next'
import {
  fmtDataPlataforma,
  fmtDiaCurto,
  fmtDinheiro,
  fmtMesAno,
  type AvaliacaoCliente,
  type Cliente,
  type EventoCliente,
  type TipoEventoCliente,
} from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  cliente: Cliente
  // A conversa que está na tela: a reclamação DELA leva ao fim da conversa,
  // a de outra conversa abre a outra (a dica do clique tem de dizer qual).
  conversaId?: string | null
}>(), { conversaId: null })
const emit = defineEmits<{
  (e: 'irPara', ev: EventoCliente): void
}>()

const c = computed(() => props.cliente)

function plural(n: number, um: string, varios: string) {
  return `${n} ${n === 1 ? um : varios}`
}
function quando(iso: string | null | undefined) {
  const t = iso ? new Date(iso).getTime() : Number.NaN
  return Number.isNaN(t) ? 0 : t
}
// Para comparar textos fixos do backend sem depender de acento/maiúscula.
function chave(t: string | null | undefined) {
  return (t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()
}

// ─── 1ª linha: desde quando, quantas compras, quanto, a última ──────────────
// `desde` é a primeira compra OU a primeira mensagem, a que vier antes: sem
// compra nenhuma, é só "em contato desde". Com o histórico incompleto, a
// primeira compra de verdade pode ser mais antiga: "visto desde".
const completo = computed(() => c.value.historico_completo)
const linha1 = computed(() => {
  const x = c.value
  const partes: string[] = []
  if (x.desde) {
    const rotulo = x.compras === 0 ? 'Em contato desde' : completo.value ? 'Cliente desde' : 'Visto desde'
    partes.push(`${rotulo} ${fmtMesAno(x.desde)}`)
  }
  if (x.compras > 0) {
    const total = x.total_gasto && x.total_gasto > 0 ? ` (${fmtDinheiro(x.total_gasto)})` : ''
    const qtd = plural(x.compras, 'compra', 'compras')
    partes.push(`${completo.value ? qtd : `pelo menos ${qtd}`}${total}`)
  } else {
    // "registrada": com o histórico incompleto, pode haver compra que o
    // DaVinci ainda não viu.
    partes.push(completo.value ? 'nenhuma compra' : 'nenhuma compra registrada')
  }
  if (x.ultima_compra) partes.push(`última ${fmtDiaCurto(x.ultima_compra)}`)
  return partes
})
// O aviso do histórico parcial é TEXTO visível (o title não abre em tela de
// toque): com a data de cobertura, "histórico desde 30/06"; sem, "parcial".
const avisoParcial = computed(() => {
  if (completo.value) return null
  const desde = c.value.historico_desde
  return desde
    ? {
        texto: `histórico desde ${fmtDiaCurto(desde)}`,
        dica: `A loja só tem as compras deste comprador a partir de ${fmtDiaCurto(desde)}: pode haver compras mais antigas.`,
      }
    : {
        texto: 'histórico parcial',
        dica: 'Nem todas as compras deste comprador puderam ser lidas (a plataforma não respondeu a tempo, ou só se conhece o pedido das conversas): pode haver mais compras.',
      }
})
const dicaLinha1 = computed(() =>
  completo.value && c.value.historico_desde ? `histórico de compras desta loja a partir de ${fmtDiaCurto(c.value.historico_desde)}` : undefined,
)

// ─── 2ª linha: o que muda o tom da resposta ─────────────────────────────────
// Mesma nota do backend (`NOTA_RUIM` em cliente.py): ≤ 2 liga "avaliou mal".
const NOTA_RUIM = 2
// A avaliação mais recente é a que aparece; as outras entram no title.
const avaliacoes = computed(() => c.value.avaliacoes.slice().sort((a, b) => quando(b.criado_em) - quando(a.criado_em)))
const avaliacao = computed<AvaliacaoCliente | null>(() => avaliacoes.value.find((a) => a.estrelas !== null) ?? null)
// A nota ruim mais recente. O selo "avaliou mal" do cabeçalho liga com
// QUALQUER nota ≤ 2 recente: se a última for boa, a ruim também aparece
// aqui, em vermelho e com a data — senão o cartão mostraria ★★★★★ verde ao
// lado do selo vermelho, e a nota ruim só no title.
const pior = computed<AvaliacaoCliente | null>(() => avaliacoes.value.find((a) => a.estrelas !== null && a.estrelas <= NOTA_RUIM) ?? null)
const piorSeparada = computed(() => (pior.value && pior.value !== avaliacao.value ? pior.value : null))
// As que não aparecem na linha (ficam no title).
const outrasAvaliacoes = computed(() => avaliacoes.value.length - (avaliacao.value ? 1 : 0) - (piorSeparada.value ? 1 : 0))
function estrelas(n: number) {
  return '★'.repeat(n) + '☆'.repeat(Math.max(0, 5 - n))
}
function corNota(n: number) {
  if (n <= NOTA_RUIM) return 'text-red-600 dark:text-red-400'
  if (n === 3) return 'text-amber-600 dark:text-amber-400'
  return 'text-emerald-600 dark:text-emerald-400'
}
const tituloAvaliacoes = computed(() =>
  avaliacoes.value
    .map((a) => {
      const nota = a.estrelas !== null ? `${a.estrelas}/5` : 'sem nota'
      const dia = a.criado_em ? ` em ${fmtDiaCurto(a.criado_em)}` : ''
      const ped = a.pedido ? ` (pedido ${a.pedido})` : ''
      const resp = a.respondida ? ' — respondida pela loja' : ' — sem resposta da loja'
      return `${nota}${dia}${ped}${resp}${a.texto ? `\n“${a.texto}”` : ''}`
    })
    .join('\n\n'),
)

// ─── perguntas nos anúncios ─────────────────────────────────────────────────
// O `texto` do evento "pergunta" é o TÍTULO DO ANÚNCIO (ou o fixo "Pergunta
// no anúncio" quando não há título) — não o que o comprador perguntou. Por
// isso a tela nunca o põe entre aspas: "sobre Mala de Bordo ABS".
const PERGUNTA_GENERICA = chave('Pergunta no anúncio')
function anuncioDe(e: EventoCliente): string | null {
  const t = (e.texto || '').trim()
  return t && chave(t) !== PERGUNTA_GENERICA ? t : null
}
const perguntasLinha = computed(() =>
  c.value.linha_do_tempo.filter((e) => e.tipo === 'pergunta').sort((a, b) => quando(b.em) - quando(a.em)),
)
// As OUTRAS perguntas do comprador (a aberta é a que está na tela: nem o
// `perguntas_pre_venda` do backend nem a linha do tempo a contam). O
// backend só conta as feitas antes de uma compra; a linha do tempo tem
// todas, mas pode ter cortado as mais antigas — vale o maior dos dois (um
// piso: a tela nunca afirma mais perguntas do que alguém viu).
const nPerguntas = computed(() => Math.max(perguntasLinha.value.length, c.value.perguntas_pre_venda))
// "Antes de comprar" só quando dá para afirmar: histórico completo, TODAS as
// compras (inclusive canceladas) e TODAS as perguntas na linha do tempo (ela
// guarda só as mais recentes — a primeira compra podia ter ficado de fora).
// Pergunta feita depois da primeira compra não é "antes de comprar".
const perguntasAntes = computed<EventoCliente[]>(() => {
  const x = c.value
  if (x.compras === 0 || !completo.value || perguntasLinha.value.length < nPerguntas.value) return []
  const datasCompra = x.linha_do_tempo.filter((e) => e.tipo === 'compra' && !!e.em).map((e) => quando(e.em))
  if (!datasCompra.length || datasCompra.length < x.compras + x.cancelamentos) return []
  const primeira = Math.min(...datasCompra)
  return perguntasLinha.value.filter((e) => !!e.em && quando(e.em) < primeira)
})
const perguntas = computed(() => {
  const n = nPerguntas.value
  if (n <= 0) return null
  const antes = perguntasAntes.value.length
  let texto: string
  if (antes > 0 && antes === n) texto = n > 1 ? `perguntou antes de comprar (${n}×)` : 'perguntou antes de comprar'
  else if (antes > 0) texto = `${n} perguntas em anúncios, ${antes} antes de comprar`
  else texto = plural(n, 'pergunta em anúncio', 'perguntas em anúncios')
  // O anúncio da última pergunta (das "antes de comprar", quando é disso
  // que a frase fala).
  const base = antes === n ? perguntasAntes.value : perguntasLinha.value
  const anuncio = base.map(anuncioDe).find((t) => !!t) ?? null
  return {
    texto: anuncio ? `${texto} ${n > 1 ? 'a última ' : ''}sobre ${anuncio}` : texto,
    dica: anuncio ? `anúncio da última pergunta: ${anuncio}` : undefined,
  }
})
const temLinha2 = computed(() => c.value.devolucoes > 0 || c.value.cancelamentos > 0 || !!avaliacao.value || !!piorSeparada.value || !!perguntas.value)

// ─── linha do tempo ─────────────────────────────────────────────────────────
const TIPO: Record<TipoEventoCliente, { label: string; icone: any; ponto: string; cor: string }> = {
  pergunta: { label: 'Pergunta', icone: MessageCircleQuestion, ponto: 'bg-sky-500', cor: 'text-sky-700 dark:text-sky-300' },
  compra: { label: 'Compra', icone: ShoppingBag, ponto: 'bg-emerald-500', cor: 'text-emerald-700 dark:text-emerald-300' },
  envio: { label: 'Envio', icone: Truck, ponto: 'bg-sky-500', cor: 'text-sky-700 dark:text-sky-300' },
  entrega: { label: 'Entrega', icone: PackageCheck, ponto: 'bg-emerald-500', cor: 'text-emerald-700 dark:text-emerald-300' },
  avaliacao: { label: 'Avaliação', icone: Star, ponto: 'bg-amber-500', cor: 'text-amber-700 dark:text-amber-300' },
  mensagem: { label: 'Mensagem', icone: MessageSquare, ponto: 'bg-muted-foreground/60', cor: 'text-muted-foreground' },
  reclamacao: { label: 'Reclamação', icone: TriangleAlert, ponto: 'bg-red-500', cor: 'text-red-700 dark:text-red-300' },
}
// Nos eventos genéricos o backend já põe o tipo no texto ("Reclamação no
// Mercado Livre", "Mensagem pós-venda", "Pedido enviado"): aí o texto É o
// rótulo — "Reclamação: Reclamação no Mercado Livre" repetia. A pergunta
// traz o título do anúncio: "Pergunta sobre <anúncio>".
const TEXTO_E_ROTULO = new Set<TipoEventoCliente>(['mensagem', 'reclamacao', 'envio', 'entrega'])
function partes(e: EventoCliente): { rotulo: string; sep: string; detalhe: string | null } {
  const label = TIPO[e.tipo].label
  const t = (e.texto || '').trim()
  if (!t || chave(t) === chave(label)) return { rotulo: label, sep: '', detalhe: null }
  if (e.tipo === 'pergunta') {
    const anuncio = anuncioDe(e)
    return anuncio ? { rotulo: 'Pergunta', sep: ' sobre ', detalhe: anuncio } : { rotulo: t, sep: '', detalhe: null }
  }
  if (TEXTO_E_ROTULO.has(e.tipo)) return { rotulo: t, sep: '', detalhe: null }
  return { rotulo: label, sep: ': ', detalhe: t }
}
// O que o clique faz (o `ref` da pergunta, da mensagem e da reclamação é o
// id de uma CONVERSA): outra conversa abre; a desta rola até o fim.
function onde(e: EventoCliente): string | null {
  if (e.tipo === 'pergunta') return 'abrir a pergunta'
  if (e.tipo === 'mensagem' || e.tipo === 'reclamacao') {
    return props.conversaId && e.ref === props.conversaId ? 'ir até o fim desta conversa' : 'abrir a conversa'
  }
  return null
}
function dica(e: EventoCliente) {
  const quandoTxt = e.em ? fmtDataPlataforma(e.em) : 'sem data'
  const p = partes(e)
  const oque = `${p.rotulo}${p.detalhe ? `${p.sep}${p.detalhe}` : ''}`
  if (!e.ref) return `${oque} · ${quandoTxt}`
  return `${oque} · ${quandoTxt} — clique para ${onde(e) || 'ver o pedido'}`
}
const eventos = computed(() =>
  c.value.linha_do_tempo
    .map((e, i) => ({ e, p: partes(e), chave: `${e.tipo}-${e.em || ''}-${e.ref || ''}-${i}` }))
    .sort((a, b) => quando(b.e.em) - quando(a.e.em)),
)
const aberta = ref(false)
onMounted(() => {
  if (linhaAbertaMemoria !== null) {
    aberta.value = linhaAbertaMemoria
    return
  }
  try {
    aberta.value = localStorage.getItem(LINHA_KEY) === '1'
  } catch {
    // sem localStorage — começa fechada
  }
})
function alternar() {
  aberta.value = !aberta.value
  linhaAbertaMemoria = aberta.value
  try {
    localStorage.setItem(LINHA_KEY, aberta.value ? '1' : '0')
  } catch {
    // só não lembra
  }
}
</script>

<template>
  <section class="space-y-1 px-3 py-2 text-xs" aria-label="cliente">
    <!-- 1ª linha -->
    <div class="flex items-start gap-1.5">
      <UserRound class="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <p class="min-w-0 flex-1 leading-5" :title="dicaLinha1">
        <template v-for="(t, i) in linha1" :key="i">
          <span v-if="i" class="text-muted-foreground" aria-hidden="true"> · </span>
          <span :class="i === 0 ? 'font-medium' : ''">{{ t }}</span>
        </template>
        <template v-if="avisoParcial">
          <span class="text-muted-foreground" aria-hidden="true"> · </span>
          <span class="whitespace-nowrap rounded bg-amber-500/15 px-1 text-[10px] text-amber-800 dark:text-amber-300" :title="avisoParcial.dica">{{ avisoParcial.texto }}</span>
        </template>
      </p>
    </div>

    <!-- 2ª linha -->
    <div v-if="temLinha2" class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 pl-5 leading-5 text-muted-foreground">
      <span v-if="c.devolucoes > 0" class="font-medium text-amber-700 dark:text-amber-300">{{ plural(c.devolucoes, 'devolução', 'devoluções') }}</span>
      <template v-if="c.cancelamentos > 0">
        <span v-if="c.devolucoes > 0" aria-hidden="true">·</span>
        <span>{{ plural(c.cancelamentos, 'cancelamento', 'cancelamentos') }}</span>
      </template>
      <template v-if="avaliacao && avaliacao.estrelas !== null">
        <span v-if="c.devolucoes > 0 || c.cancelamentos > 0" aria-hidden="true">·</span>
        <span class="inline-flex items-center gap-1" :title="tituloAvaliacoes" :aria-label="`última avaliação: nota ${avaliacao.estrelas} de 5`">
          <span v-if="piorSeparada">última</span>
          <span class="tracking-tight" :class="corNota(avaliacao.estrelas)">{{ estrelas(avaliacao.estrelas) }}</span>
          <span v-if="avaliacao.estrelas <= NOTA_RUIM && avaliacao.criado_em" :class="corNota(avaliacao.estrelas)">em {{ fmtDiaCurto(avaliacao.criado_em) }}</span>
          <span v-if="!avaliacao.respondida" class="rounded bg-muted px-1 text-[10px]">sem resposta</span>
        </span>
      </template>
      <!-- a nota ruim que não é a última: visível, não só no title -->
      <template v-if="piorSeparada && piorSeparada.estrelas !== null">
        <span aria-hidden="true">·</span>
        <span class="inline-flex items-center gap-1 text-red-600 dark:text-red-400" :title="tituloAvaliacoes" :aria-label="`já deu nota ${piorSeparada.estrelas} de 5${piorSeparada.criado_em ? ` em ${fmtDiaCurto(piorSeparada.criado_em)}` : ''}`">
          já deu <span class="tracking-tight">{{ estrelas(piorSeparada.estrelas) }}</span><template v-if="piorSeparada.criado_em"> em {{ fmtDiaCurto(piorSeparada.criado_em) }}</template>
          <span v-if="!piorSeparada.respondida" class="rounded bg-muted px-1 text-[10px] text-muted-foreground">sem resposta</span>
        </span>
      </template>
      <span v-if="outrasAvaliacoes > 0 && avaliacao" :title="tituloAvaliacoes">(+{{ outrasAvaliacoes }})</span>
      <template v-if="perguntas">
        <span v-if="c.devolucoes > 0 || c.cancelamentos > 0 || avaliacao || piorSeparada" aria-hidden="true">·</span>
        <span class="min-w-0 max-w-full truncate" :title="perguntas.dica">{{ perguntas.texto }}</span>
      </template>
    </div>

    <!-- linha do tempo, recolhível -->
    <div v-if="eventos.length" class="pl-5">
      <button
        type="button"
        class="-ml-1 inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
        :aria-expanded="aberta"
        @click="alternar"
      >
        <ChevronDown v-if="aberta" class="size-3.5" />
        <ChevronRight v-else class="size-3.5" />
        Linha do tempo ({{ eventos.length }})
      </button>
      <ol v-if="aberta" class="mt-1 max-h-56 space-y-0.5 overflow-y-auto overscroll-contain border-l border-border pl-3 pr-1">
        <li v-for="{ e, p, chave: k } in eventos" :key="k" class="relative">
          <!-- a bolinha na linha, na cor do tipo -->
          <span class="absolute -left-[17px] top-[7px] size-2 rounded-full ring-2 ring-card" :class="TIPO[e.tipo].ponto" aria-hidden="true" />
          <component
            :is="e.ref ? 'button' : 'div'"
            :type="e.ref ? 'button' : undefined"
            class="flex w-full items-start gap-1.5 rounded px-1 py-0.5 text-left"
            :class="e.ref ? 'hover:bg-muted' : ''"
            :title="dica(e)"
            @click="e.ref && emit('irPara', e)"
          >
            <span class="w-[62px] shrink-0 tabular-nums text-muted-foreground">{{ e.em ? fmtDiaCurto(e.em) : '—' }}</span>
            <component :is="TIPO[e.tipo].icone" class="mt-0.5 size-3 shrink-0" :class="TIPO[e.tipo].cor" aria-hidden="true" />
            <span class="min-w-0 flex-1 line-clamp-2">
              <span class="font-medium" :class="TIPO[e.tipo].cor">{{ p.rotulo }}</span><template v-if="p.detalhe">{{ p.sep }}{{ p.detalhe }}</template>
            </span>
            <Package v-if="e.ref && !onde(e)" class="mt-0.5 size-3 shrink-0 text-muted-foreground/70" aria-hidden="true" />
          </component>
        </li>
      </ol>
    </div>
  </section>
</template>
