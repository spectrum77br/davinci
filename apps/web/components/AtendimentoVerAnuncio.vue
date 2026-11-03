<script lang="ts">
// Botão "Ver anúncio" do cabeçalho da conversa (03/11/2026). O Eduardo:
// "um botão pra abrir ele não só no AdsPower mas no navegador normal, só o
// anúncio mesmo, como uma pessoa vê ele". Abre a página PÚBLICA do anúncio
// (a que o comprador vê) numa aba nova deste navegador — nem o painel do
// vendedor, nem o perfil do AdsPower, nem login.
//
// Qual anúncio e o link vêm prontos do GET /conversas/{id}/painel
// (`anuncios`, services/atendimento/anuncio_publico.py): o do pedido (vários
// itens = menu), senão o último cartão de produto do comprador, senão o da
// pergunta do ML. Sem anúncio, o botão fica desligado com o porquê.
export type AnuncioPublico = {
  item_id: string
  titulo: string | null
  variacao: string | null
  link: string
  // pedido | chat | conversa
  origem: string
}
export type AnunciosPainel = {
  itens: AnuncioPublico[]
  motivo: string | null
  codigo: string | null
}

const ORIGEM: Record<string, string> = { pedido: 'do pedido', chat: 'que o comprador mandou', conversa: 'da conversa' }

// Cinto da tela: só https (quem confere o domínio da plataforma é o backend).
export function linkPublico(v: unknown): string | null {
  try {
    const u = new URL(String(v || ''))
    return u.protocol === 'https:' && !u.username && !u.password ? u.toString() : null
  } catch {
    return null
  }
}

// Os itens que dá para abrir (link https), na ordem do painel.
export function anunciosAbriveis(bloco: AnunciosPainel | null | undefined): AnuncioPublico[] {
  return (bloco?.itens || []).filter((a) => !!a && !!linkPublico(a.link))
}

export function rotuloAnuncio(a: Pick<AnuncioPublico, 'item_id' | 'titulo' | 'variacao'>): string {
  const titulo = (a.titulo || '').trim() || `Anúncio ${a.item_id}`
  const variacao = (a.variacao || '').trim()
  return variacao ? `${titulo} (${variacao})` : titulo
}

// A dica do botão/da linha: o que abre e o título do anúncio.
export function dicaAnuncio(a: AnuncioPublico): string {
  const de = ORIGEM[a.origem] ? ` ${ORIGEM[a.origem]}` : ''
  return `ver o anúncio${de} como o comprador vê (aba nova): ${rotuloAnuncio(a)}`
}

export type EstadoVerAnuncio =
  | { modo: 'carregando' }
  | { modo: 'desligado'; motivo: string }
  | { modo: 'um'; anuncio: AnuncioPublico }
  | { modo: 'menu'; anuncios: AnuncioPublico[] }

export function estadoVerAnuncio(bloco: AnunciosPainel | null | undefined, carregando = false): EstadoVerAnuncio {
  if (!bloco) {
    return carregando ? { modo: 'carregando' } : { modo: 'desligado', motivo: 'Não consegui ler o anúncio agora.' }
  }
  const itens = anunciosAbriveis(bloco)
  if (itens.length === 1) return { modo: 'um', anuncio: itens[0] }
  if (itens.length > 1) return { modo: 'menu', anuncios: itens }
  return { modo: 'desligado', motivo: bloco.motivo || 'Sem anúncio identificado nesta conversa.' }
}
</script>

<script setup lang="ts">
import { ChevronDown, ExternalLink, Loader2 } from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'

const props = withDefaults(defineProps<{
  // O `anuncios` do painel; null enquanto carrega (ou se o painel falhou).
  anuncios: AnunciosPainel | null
  carregando?: boolean
}>(), { carregando: false })

const estado = computed(() => estadoVerAnuncio(props.anuncios, props.carregando))
const menu = ref(false)
</script>

<template>
  <Button
    v-if="estado.modo === 'um'"
    as="a"
    size="sm"
    variant="outline"
    class="h-7 px-2 text-xs"
    :href="estado.anuncio.link"
    target="_blank"
    rel="noopener noreferrer"
    :title="dicaAnuncio(estado.anuncio)"
    aria-label="Ver anúncio"
    data-ver-anuncio
  >
    <ExternalLink class="size-3.5" />
    <span class="ml-1">Ver anúncio</span>
  </Button>
  <PopoverRoot v-else-if="estado.modo === 'menu'" v-model:open="menu">
    <PopoverTrigger as-child>
      <Button
        size="sm"
        variant="outline"
        class="h-7 px-2 text-xs"
        :title="`o pedido tem ${estado.anuncios.length} anúncios — escolha qual abrir`"
        aria-label="Ver anúncio"
        data-ver-anuncio
      >
        <ExternalLink class="size-3.5" />
        <span class="ml-1">Ver anúncio</span>
        <ChevronDown class="ml-0.5 size-3.5" />
      </Button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="end"
        :side-offset="4"
        :collision-padding="8"
        class="z-[70] w-[340px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-1 text-xs shadow-lg"
      >
        <p class="px-2 py-1 text-muted-foreground">Abrir o anúncio como o comprador vê (aba nova):</p>
        <a
          v-for="a in estado.anuncios"
          :key="a.item_id"
          :href="a.link"
          target="_blank"
          rel="noopener noreferrer"
          :title="dicaAnuncio(a)"
          class="flex items-start gap-1.5 rounded px-2 py-1.5 text-[13px] leading-5 hover:bg-muted"
          data-ver-anuncio-item
          @click="menu = false"
        >
          <ExternalLink class="mt-1 size-3.5 shrink-0 text-muted-foreground" />
          <span class="line-clamp-2 min-w-0 break-words">{{ rotuloAnuncio(a) }}</span>
        </a>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
  <Button
    v-else
    size="sm"
    variant="outline"
    class="h-7 px-2 text-xs"
    disabled
    :title="estado.modo === 'desligado' ? estado.motivo : 'lendo o anúncio…'"
    aria-label="Ver anúncio"
    data-ver-anuncio
  >
    <Loader2 v-if="estado.modo === 'carregando'" class="size-3.5 animate-spin" />
    <ExternalLink v-else class="size-3.5" />
    <span class="ml-1">Ver anúncio</span>
  </Button>
</template>
