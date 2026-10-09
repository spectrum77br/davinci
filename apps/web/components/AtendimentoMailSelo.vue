<script lang="ts">
// O SELO DA LOJA de um e-mail na aba E-mail › Caixas (09/10/2026).
//
// O dono pediu: "visualmente na aba de e-mail poderia trazer o nome da loja
// aqui também". O selo vem pronto da rota (routers/mail_caixa.py): o tipo,
// o rótulo ("JLAS2 · ML", "Site Uranyx", "sem loja", "segurança",
// "privado") e a plataforma — o desenho é o MESMO ícone de plataforma do
// /atendimento (AtendimentoIconePlataforma). O selo nunca mostra nada do
// conteúdo do e-mail. `provavel` = a loja que o índice calculou (o e-mail
// não passou pela ponte): o mesmo desenho, só a dica diz.
// Funções puras aqui em cima (tests/atendimento-mail-caixa.cjs).

export type SeloDaCaixa = {
  tipo: string
  rotulo: string
  plataforma: string | null
  loja?: string | null
  chave?: string
  provavel?: boolean
}

// Os tipos do selo (indice.SELO_*).
export const TIPOS_DE_SELO = ['loja', 'site', 'sem_loja', 'seguranca', 'privado'] as const

// As cores do selo — legíveis no claro e no escuro (contraste AA ≥ 4,5:1 para
// texto de 11 px, também na linha selecionada, que tem o fundo `bg-muted`: o
// cinza `text-muted-foreground` dava 4,4:1 no claro — revisão de 09/10).
const ESTILO: Record<string, string> = {
  loja: 'border-border bg-background text-foreground',
  site: 'border-border bg-background text-foreground',
  sem_loja: 'border-dashed border-muted-foreground/50 bg-transparent text-foreground/75',
  seguranca: 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:border-amber-400/40 dark:bg-amber-400/10 dark:text-amber-200',
  privado: 'border-transparent bg-muted text-foreground/75',
}

// O "(loja provável)" para o leitor de tela: só quando o selo É uma loja.
export function lojaProvavel(selo: Pick<SeloDaCaixa, 'tipo' | 'provavel'> | null | undefined): boolean {
  return !!selo?.provavel && (selo.tipo === 'loja' || selo.tipo === 'site')
}

export function estiloDoSelo(tipo: string | null | undefined): string {
  return ESTILO[tipo || ''] || ESTILO.sem_loja
}

// O desenho ao lado do rótulo: o ícone da plataforma (loja e site com
// plataforma) ou um ícone do tipo.
export function iconeDoSelo(selo: Pick<SeloDaCaixa, 'tipo' | 'plataforma'> | null | undefined): 'plataforma' | 'seguranca' | 'privado' | 'loja' | 'sem_loja' {
  const tipo = selo?.tipo || ''
  if ((tipo === 'loja' || tipo === 'site') && selo?.plataforma) return 'plataforma'
  if (tipo === 'seguranca' || tipo === 'privado' || tipo === 'loja') return tipo
  if (tipo === 'site') return 'loja'
  return 'sem_loja'
}

// A dica (title) do selo: o que ele quer dizer, em português simples.
export function dicaDoSelo(selo: SeloDaCaixa | null | undefined): string {
  if (!selo) return ''
  switch (selo.tipo) {
    case 'seguranca':
      return 'E-mail de acesso, código ou senha: o assunto não aparece na lista.'
    case 'privado':
      return 'Privado: não chegou num endereço de loja desta caixa.'
    case 'sem_loja':
      return selo.provavel
        ? 'Sem loja: nem o endereço que recebeu nem o remetente dizem a loja.'
        : 'Sem loja: a ponte não achou a loja deste e-mail.'
    default:
      return selo.provavel
        ? `${selo.rotulo} — loja provável, pelo endereço que recebeu e pela pasta (este e-mail não passou pela ponte).`
        : `${selo.rotulo} — loja decidida pela ponte.`
  }
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import { Lock, ShieldAlert, Store, CircleDashed } from 'lucide-vue-next'

const props = defineProps<{ selo: SeloDaCaixa }>()
const icone = computed(() => iconeDoSelo(props.selo))
</script>

<template>
  <span
    class="inline-flex min-w-0 max-w-full shrink items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-[11px] font-medium leading-4"
    :class="estiloDoSelo(selo.tipo)"
    :title="dicaDoSelo(selo)"
    data-mail-selo
    :data-selo-tipo="selo.tipo"
  >
    <AtendimentoIconePlataforma v-if="icone === 'plataforma'" :plataforma="selo.plataforma" :tamanho="12" decorativo />
    <ShieldAlert v-else-if="icone === 'seguranca'" class="h-3 w-3 shrink-0" aria-hidden="true" />
    <Lock v-else-if="icone === 'privado'" class="h-3 w-3 shrink-0" aria-hidden="true" />
    <Store v-else-if="icone === 'loja'" class="h-3 w-3 shrink-0" aria-hidden="true" />
    <CircleDashed v-else class="h-3 w-3 shrink-0" aria-hidden="true" />
    <span class="truncate">{{ selo.rotulo }}</span>
    <span v-if="lojaProvavel(selo)" class="sr-only"> (loja provável)</span>
  </span>
</template>
