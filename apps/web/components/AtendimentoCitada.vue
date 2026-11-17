<script lang="ts">
// A mensagem CITADA em cima do balão (17/11/2026). O dono: "a equipe mandou as
// malas e a pessoa respondeu na mala que queria, mas no nosso comunicador não
// mostra em qual mensagem ele respondeu; no Duoke isso aparecia". Quando o
// comprador usa o "responder" da plataforma, o servidor manda `mensagem.citada`
// (services/atendimento/citacao.py: Shopee `quoted_msg`, Zap `context`); aqui o
// bloco compacto — a barra fina, a miniatura da foto ou o texto em até 2 linhas,
// e de quem é. Com a citada gravada nesta conversa (`mensagem_id`) ele é botão:
// leva até ela e a destaca, como o "responder" do WhatsApp e do Duoke.
import { urlSegura, type Citada } from '~/components/AtendimentoPlataforma.vue'

const TIPOS: readonly Citada['tipo'][] = ['texto', 'imagem', 'video', 'produto', 'pedido', 'outro']
// Sem texto, o bloco diz o que é.
const ROTULO_DO_TIPO: Record<Citada['tipo'], string> = {
  texto: 'Mensagem',
  imagem: 'Foto',
  video: 'Vídeo',
  produto: 'Produto',
  pedido: 'Pedido',
  outro: 'Mensagem',
}
export const NAO_ESTA_AQUI = 'Mensagem que não está no DaVinci'
export const ROTULO_IR = 'Ir para a mensagem citada'

function texto(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

// O campo como veio da API, conferido: o servidor antigo não manda (undefined) e o
// torto vira null; a miniatura passa pela régua das fotos da tela (`urlSegura`).
export function citadaDe(v: unknown): Citada | null {
  if (!v || typeof v !== 'object') return null
  const o = v as Record<string, unknown>
  const externo = texto(o.externo_id)
  if (!externo) return null
  const tipo = TIPOS.find((t) => t === o.tipo) ?? 'outro'
  return {
    externo_id: externo,
    mensagem_id: texto(o.mensagem_id),
    autor: o.autor === 'loja' || o.autor === 'cliente' ? o.autor : null,
    tipo,
    texto: texto(o.texto),
    imagem_url: urlSegura(o.imagem_url),
  }
}

// De quem é a citada: "Loja" ou "Cliente" (sem saber, só "Mensagem citada").
export function autorDaCitada(c: Pick<Citada, 'autor'>): string {
  if (c.autor === 'loja') return 'Loja'
  if (c.autor === 'cliente') return 'Cliente'
  return 'Mensagem citada'
}

// A linha do bloco: o texto; sem ele, o que é ("Foto", "Produto"); a citada que
// não está gravada aqui e não diz nada (a mandada pelo celular, no Zap), isso.
export function resumoDaCitada(c: Citada): string {
  if (c.texto) return c.texto
  if (!c.mensagem_id && !c.imagem_url && c.tipo === 'outro') return NAO_ESTA_AQUI
  return ROTULO_DO_TIPO[c.tipo] ?? 'Mensagem'
}

// A barra e o nome com a cor de quem escreveu (loja em azul, como o balão dela).
export function coresDaCitada(c: Pick<Citada, 'autor'>): { barra: string; nome: string } {
  if (c.autor === 'loja') return { barra: 'border-sky-500 dark:border-sky-400', nome: 'text-sky-700 dark:text-sky-300' }
  if (c.autor === 'cliente') return { barra: 'border-emerald-500 dark:border-emerald-400', nome: 'text-emerald-700 dark:text-emerald-300' }
  return { barra: 'border-muted-foreground/50', nome: 'text-muted-foreground' }
}
</script>

<script setup lang="ts">
const props = defineProps<{
  citada: Citada
  // O id da mensagem que cita (o id do texto que o leitor de tela lê com o botão).
  de?: string | null
}>()
const emit = defineEmits<{ (e: 'ir', id: string): void }>()

const c = computed(() => citadaDe(props.citada))
// A miniatura que não carregou (link expirado da plataforma): fica só o texto.
const fotoFalhou = ref(false)
watch(() => c.value?.imagem_url, () => { fotoFalhou.value = false })
const miniatura = computed(() => (fotoFalhou.value ? null : c.value?.imagem_url ?? null))
const clicavel = computed(() => !!c.value?.mensagem_id)
const cores = computed(() => coresDaCitada(c.value ?? { autor: null }))
const idDoResumo = computed(() => `citada-${props.de || c.value?.externo_id || ''}`)

function ir() {
  const id = c.value?.mensagem_id
  if (id) emit('ir', id)
}
</script>

<template>
  <component
    :is="clicavel ? 'button' : 'div'"
    v-if="c"
    :type="clicavel ? 'button' : undefined"
    :data-citada-ir="clicavel ? '' : undefined"
    class="mb-1.5 flex w-full min-w-0 items-center gap-2 rounded-md border-l-[3px] bg-foreground/5 py-1 pl-2 pr-1.5 text-left"
    :class="[cores.barra, clicavel ? 'cursor-pointer transition-colors hover:bg-foreground/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring' : '']"
    :title="clicavel ? 'ir para a mensagem citada' : 'a mensagem citada não está nesta conversa do DaVinci'"
    data-citada
    :data-citada-tipo="c.tipo"
    @click="ir"
  >
    <img
      v-if="miniatura"
      :src="miniatura"
      alt=""
      loading="lazy"
      referrerpolicy="no-referrer"
      class="size-11 shrink-0 rounded border border-border/60 bg-background object-cover"
      data-citada-miniatura
      @error="fotoFalhou = true"
    />
    <span :id="idDoResumo" class="min-w-0 flex-1 leading-tight">
      <!-- O nome do botão é o próprio conteúdo citado, com o prefixo só para o leitor de tela. -->
      <span v-if="clicavel" class="sr-only">{{ ROTULO_IR }}: </span>
      <span class="block truncate text-[11px] font-semibold" :class="cores.nome">{{ autorDaCitada(c) }}</span>
      <span class="line-clamp-2 break-words text-xs text-muted-foreground" data-citada-resumo>{{ resumoDaCitada(c) }}</span>
    </span>
  </component>
</template>
