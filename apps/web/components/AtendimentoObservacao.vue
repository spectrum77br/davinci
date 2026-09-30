<script setup lang="ts">
// Painel "O que a IA responderia" (Atendimento, 28/09/2026) — no lugar da
// caixa de envio quando a conversa está em MODO OBSERVAÇÃO (loja em Observar
// ou envio desligado no servidor). É o primeiro teste em produção: quem
// responde continua sendo o Duoke; o DaVinci só lê e mostra a sugestão da IA,
// com "Copiar" (para colar no Duoke) e 👍/👎 com correção. Nada aqui envia.
// Sugestão BLOQUEADA também aparece (com o porquê): no teste, saber o que o
// validador barrou é tão útil quanto o que passou.
// Compacto de propósito: em notebook (720 px de altura) o painel alto deixava
// dois balões de conversa à mostra. O texto rola dentro de uma altura curta, e
// dá para recolher o painel a uma linha (lembrado neste navegador). Recolher
// só esconde (v-show): a correção do 👎 em curso continua montada.
// Temu e AliExpress (30/09/2026): o DaVinci lê pelo robô do Mac mini e nunca
// envia — a conversa cai sempre aqui, a faixa diz que a resposta é no Seller
// Center da plataforma e traz o link da tela de chat de lá.
// Magalu (30/09/2026): o Duoke não a cobre — em Observar, quem responde é o
// Portal do Seller; a faixa traz o link da caixa de lá (pergunta, chat, SAC).
import { ChevronDown, ChevronUp, Copy, ExternalLink, Eye, Loader2, Sparkles, TriangleAlert } from 'lucide-vue-next'
import {
  categoriaLabel,
  copiar,
  haQuanto,
  portalMagaluDe,
  sellerCenterDe,
  type AvaliacaoIa,
  type SugestaoIa,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  sugestao: SugestaoIa | null
  canEdit: boolean
  // Dá para pedir uma sugestão agora (IA ligada, não pausada, conversa aberta).
  podeSugerir: boolean
  gerando: boolean
  // Por que não há sugestão, quando a tela sabe (IA desligada, pausada…).
  semSugestaoMotivo?: string | null
  // A sugestão do painel está presa pela correção aberta e já há outra mais
  // nova esperando (AtendimentoConversa).
  temMaisNova?: boolean
  agora: number
  // A Amazon não passa pelo Duoke: responde-se no Seller Central. A Temu e o
  // AliExpress, no Seller Center de cada uma; a Magalu, no portal.
  plataforma?: string
  // A caixa da conversa: na Magalu, o link do portal é o da caixa.
  canal?: string
}>()
const emit = defineEmits<{
  (e: 'sugerir'): void
  (e: 'avaliada', id: string, a: AvaliacaoIa): void
}>()
const toasts = useToasts()

const sellerCenter = computed(() => sellerCenterDe(props.plataforma))
const portal = computed(() => portalMagaluDe(props.plataforma, props.canal))
const ondeResponde = computed(() => {
  if (props.plataforma === 'amazon') return 'o Seller Central'
  if (sellerCenter.value) return `o ${sellerCenter.value.nome}`
  if (portal.value) return `o ${portal.value.nome}`
  return 'o Duoke'
})

const bloqueada = computed(() => props.sugestao?.status === 'bloqueado')
// Já responderam por fora enquanto a pessoa corrigia: a sugestão fica aqui até
// ela salvar ou fechar a correção (depois desce para "A IA teria respondido").
const jaRespondida = computed(() => !!props.sugestao?.resposta_real || props.sugestao?.status === 'substituido')

const RECOLHIDO_KEY = 'davinci.atendimento.sugestaoRecolhida'
const recolhido = ref(false)
onMounted(() => {
  try {
    recolhido.value = localStorage.getItem(RECOLHIDO_KEY) === '1'
  } catch {
    // sem localStorage — começa aberto
  }
})
function alternar() {
  recolhido.value = !recolhido.value
  try {
    localStorage.setItem(RECOLHIDO_KEY, recolhido.value ? '1' : '0')
  } catch {
    // só não lembra
  }
}

async function copiarTexto() {
  const t = props.sugestao?.texto
  if (!t) return
  if (await copiar(t)) toasts.success('Sugestão copiada', `Cole n${ondeResponde.value} para responder.`)
  else window.prompt('Sugestão da IA', t)
}
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex items-start gap-1.5 rounded-md border border-sky-300/60 bg-sky-50 px-2 py-1 text-[11px] leading-4 text-sky-900 dark:border-sky-800/60 dark:bg-sky-900/20 dark:text-sky-200">
      <Eye class="mt-px size-3.5 shrink-0" />
      <span class="min-w-0 flex-1">
        <span class="font-semibold">Modo observação:</span> quem responde é {{ ondeResponde }};
        <template v-if="sellerCenter">o DaVinci só lê (pelo robô do Mac mini) e mostra a sugestão da IA — nada sai por aqui.</template>
        <template v-else>o DaVinci só lê e mostra a sugestão da IA.</template>
      </span>
      <!-- A tela de chat de lá (a lista de conversas: não há link por conversa). -->
      <a
        v-if="sellerCenter"
        :href="sellerCenter.url"
        target="_blank"
        rel="noopener noreferrer"
        class="inline-flex shrink-0 items-center gap-0.5 font-medium underline hover:text-sky-700 dark:hover:text-sky-100"
        :title="`abrir a tela de chat do ${sellerCenter.nome} (outra aba; se pedir login, abra pelo perfil da loja no AdsPower)`"
      >Abrir no {{ sellerCenter.curto }}<ExternalLink class="size-3" /></a>
      <!-- Magalu: a caixa desta conversa no portal (lá também há moderação). -->
      <a
        v-else-if="portal"
        :href="portal.url"
        target="_blank"
        rel="noopener noreferrer"
        class="inline-flex shrink-0 items-center gap-0.5 font-medium underline hover:text-sky-700 dark:hover:text-sky-100"
        :title="`abrir esta caixa no ${portal.nome} (outra aba) — a resposta passa pela moderação da Magalu`"
      >Abrir no {{ portal.curto }}<ExternalLink class="size-3" /></a>
    </div>

    <div class="rounded-md border border-violet-300/60 bg-violet-50/60 px-2.5 py-1.5 text-xs dark:border-violet-800/60 dark:bg-violet-900/15">
      <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded text-[13px] font-semibold text-violet-800 hover:bg-violet-100/70 dark:text-violet-300 dark:hover:bg-violet-900/30"
          :aria-expanded="!recolhido"
          :title="recolhido ? 'mostrar a sugestão' : 'recolher (a conversa ganha altura)'"
          @click="alternar"
        >
          <Sparkles class="size-4" /> O que a IA responderia
          <ChevronUp v-if="recolhido" class="size-3.5" />
          <ChevronDown v-else class="size-3.5" />
        </button>
        <template v-if="sugestao">
          <span v-if="sugestao.categoria" class="text-muted-foreground">· {{ categoriaLabel(sugestao.categoria) }}</span>
          <span v-if="sugestao.confianca !== null && sugestao.confianca !== undefined" class="text-muted-foreground">· {{ Math.round(sugestao.confianca * 100) }}% de confiança</span>
          <span
            v-if="sugestao.precisa_humano"
            class="rounded bg-amber-500/20 px-1.5 text-amber-800 dark:text-amber-300"
            title="a IA marcou que este assunto precisa de uma pessoa (dinheiro, troca, reclamação, pedido sem dado…)"
          >precisa de pessoa</span>
          <span v-if="sugestao.created_at" class="text-muted-foreground" :title="new Date(sugestao.created_at).toLocaleString('pt-BR')">· {{ haQuanto(sugestao.created_at, agora) }}</span>
        </template>
        <span class="ml-auto flex items-center gap-1">
          <button
            v-if="sugestao?.texto && !bloqueada"
            type="button"
            class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5 font-medium hover:bg-muted"
            :title="`copiar o texto para colar n${ondeResponde}`"
            @click="copiarTexto"
          >
            <Copy class="size-3.5" /> Copiar
          </button>
          <button
            v-if="podeSugerir"
            type="button"
            class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5 hover:bg-muted disabled:opacity-60"
            :disabled="gerando"
            title="pedir uma sugestão à IA agora (não envia nada)"
            @click="emit('sugerir')"
          >
            <Loader2 v-if="gerando" class="size-3.5 animate-spin" /><Sparkles v-else class="size-3.5" /> {{ sugestao ? 'Sugerir de novo' : 'Sugerir agora' }}
          </button>
        </span>
      </div>

      <!-- recolhido: uma linha com o começo da sugestão -->
      <button
        v-if="recolhido"
        type="button"
        class="mt-1 block w-full truncate text-left text-muted-foreground hover:text-foreground"
        title="mostrar a sugestão"
        @click="alternar"
      >{{ sugestao ? (sugestao.texto || 'A IA não chegou a uma resposta que pudesse sair.') : (semSugestaoMotivo || 'A IA ainda não sugeriu nada para a última mensagem do comprador.') }}</button>

      <div v-show="!recolhido">
        <template v-if="sugestao">
          <div v-if="jaRespondida || temMaisNova" class="mt-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-900 dark:text-amber-200">
            <template v-if="jaRespondida">A conversa já foi respondida por fora — esta sugestão fica aqui até você salvar ou fechar a correção; depois, ela aparece em "A IA teria respondido", na conversa.</template>
            <template v-else>Chegou uma sugestão mais nova — ela aparece aqui quando você salvar ou fechar esta correção.</template>
          </div>
          <div
            v-if="sugestao.texto"
            class="mt-1 max-h-28 overflow-y-auto whitespace-pre-wrap break-words rounded-md border bg-background px-2.5 py-1.5 text-[13px] leading-relaxed"
            :class="bloqueada ? 'border-amber-400/60 text-muted-foreground' : 'border-violet-200/70 dark:border-violet-800/50'"
          >{{ sugestao.texto }}</div>
          <div v-else class="mt-1 italic text-muted-foreground">A IA não chegou a uma resposta que pudesse sair.</div>
          <div v-if="bloqueada || sugestao.validador_erros?.length" class="mt-1 flex items-start gap-1.5 text-amber-800 dark:text-amber-300">
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0" />
            <div>
              <div v-if="bloqueada" class="font-medium">O validador barrou esta resposta — ela não sairia assim.</div>
              <ul v-if="sugestao.validador_erros?.length" class="list-inside list-disc">
                <li v-for="(v, i) in sugestao.validador_erros" :key="i">{{ v }}</li>
              </ul>
            </div>
          </div>
          <div class="mt-1.5">
            <AtendimentoAvaliarIa
              :sugestao-id="sugestao.id"
              :avaliacao="sugestao.avaliacao"
              :can-edit="canEdit"
              @avaliada="(a: AvaliacaoIa) => emit('avaliada', sugestao!.id, a)"
            />
          </div>
        </template>
        <div v-else class="mt-1 text-muted-foreground">
          {{ semSugestaoMotivo || 'A IA ainda não sugeriu nada para a última mensagem do comprador.' }}
        </div>
      </div>
    </div>
  </div>
</template>
