<script lang="ts">
// AS PASTAS E AS LOJAS da caixa, na aba E-mail › Caixas, como no Tuta
// (09/10/2026). O dono: "deixar separado e visualmente igual ao Tuta".
//
//   modo "coluna"  — a coluna da esquerda (tela larga): "Todas" no topo, as
//                    pastas de sistema na ordem do Tuta (Entrada, Rascunhos,
//                    Enviados, Lixeira, Arquivo, Spam), depois "Suas pastas"
//                    (na ordem do Tuta) e, embaixo, as LOJAS (como as
//                    etiquetas do Tuta), cada uma com a quantidade de e-mails.
//                    Pastas e lojas rolam SEPARADAS: com as ~40 pastas da
//                    conta, as lojas continuam à vista no pé da coluna (o
//                    filtro "separado" que o dono pediu — revisão de 09/10).
//   modo "seletor" — tela estreita (celular): os dois seletores no topo da
//                    lista (pasta e loja).
//
// A ordem e os nomes vêm prontos da rota (GET /api/mail/mailboxes/{id}/pastas,
// routers/mail_caixa.py); aqui só se agrupa e se mostra. Pasta e loja se
// combinam: com uma loja escolhida, a quantidade de cada pasta é só a daquela
// loja (e vice-versa). Nada de e-mail aparece aqui — só nomes de pasta e de
// loja. Funções puras aqui em cima (tests/atendimento-mail-caixa.cjs).

import type { SeloDaCaixa } from '~/components/AtendimentoMailSelo.vue'

export type PastaDaCaixa = {
  id: string | null
  nome: string
  caminho: string | null
  tipo: string
  quantidade: number
  total: number
}
export type LojaDaCaixa = SeloDaCaixa & { chave: string; quantidade: number; total: number }

// O ícone de cada pasta de sistema, pela chave da rota ("s" + o tipo do Tuta).
const ICONE_DO_SISTEMA: Record<string, string> = {
  s1: 'entrada',
  s6: 'rascunhos',
  s2: 'enviados',
  s3: 'lixeira',
  s4: 'arquivo',
  s5: 'spam',
  s10: 'agendados',
  s7: 'todas',
}

export function iconeDaPasta(pasta: Pick<PastaDaCaixa, 'id' | 'tipo'>): string {
  if (pasta.tipo === 'todas' || pasta.id === null) return 'todas'
  if (pasta.tipo === 'sistema') return ICONE_DO_SISTEMA[pasta.id] || 'pasta'
  if (pasta.tipo === 'ilegivel') return 'ilegivel'
  return 'pasta'
}

// Os grupos da coluna, na ordem da rota: "Todas", sistema, "Suas pastas", o resto.
export function gruposDasPastas(pastas: PastaDaCaixa[]): { todas: PastaDaCaixa | null; sistema: PastaDaCaixa[]; pessoais: PastaDaCaixa[]; outras: PastaDaCaixa[] } {
  return {
    todas: pastas.find((p) => p.tipo === 'todas') || null,
    sistema: pastas.filter((p) => p.tipo === 'sistema'),
    pessoais: pastas.filter((p) => p.tipo === 'pessoal'),
    outras: pastas.filter((p) => p.tipo !== 'todas' && p.tipo !== 'sistema' && p.tipo !== 'pessoal'),
  }
}

// 1234 → "1.234".
export function quantidadeLegivel(n: number | null | undefined): string {
  return Math.max(0, Math.trunc(Number(n) || 0)).toLocaleString('pt-BR')
}

export function rotuloComQuantidade(nome: string, n: number | null | undefined): string {
  return `${nome} (${quantidadeLegivel(n)})`
}

// O valor do <select> (string) ↔ o filtro (null = todas).
export function valorDoSeletor(id: string | null | undefined): string {
  return id || ''
}
export function filtroDoSeletor(valor: string | null | undefined): string | null {
  return valor ? valor : null
}

// Quantos e-mails na pasta escolhida, somando as lojas (cada e-mail tem um selo só).
export function totalDasLojasNaPasta(lojas: LojaDaCaixa[]): number {
  return lojas.reduce((n, l) => n + Math.max(0, Number(l.quantidade) || 0), 0)
}

// A pasta e a loja escolhidas, para o título da lista.
export function pastaEscolhida(pastas: PastaDaCaixa[], id: string | null): PastaDaCaixa | null {
  return pastas.find((p) => p.id === id) || null
}
export function lojaEscolhida(lojas: LojaDaCaixa[], chave: string | null): LojaDaCaixa | null {
  return chave ? lojas.find((l) => l.chave === chave) || null : null
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import {
  Archive, CircleDashed, Clock, FileWarning, Folder, Inbox, Lock, Mails, OctagonAlert, Pencil, Send, ShieldAlert, Store, Trash2,
} from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modo: 'coluna' | 'seletor'
  pastas: PastaDaCaixa[]
  lojas: LojaDaCaixa[]
  pasta: string | null
  loja: string | null
  desligado?: boolean
}>(), { desligado: false })
const emit = defineEmits<{ (e: 'pasta', id: string | null): void; (e: 'loja', chave: string | null): void }>()

const ICONES: Record<string, unknown> = {
  todas: Mails, entrada: Inbox, rascunhos: Pencil, enviados: Send, lixeira: Trash2, arquivo: Archive,
  spam: OctagonAlert, agendados: Clock, pasta: Folder, ilegivel: FileWarning,
}
const ICONES_DE_LOJA: Record<string, unknown> = { seguranca: ShieldAlert, privado: Lock, sem_loja: CircleDashed, loja: Store, site: Store }
const grupos = computed(() => gruposDasPastas(props.pastas))
// "Todas" e as de sistema, no topo da coluna.
const deCima = computed(() => [...(grupos.value.todas ? [grupos.value.todas] : []), ...grupos.value.sistema])
const pessoais = computed(() => [...grupos.value.pessoais, ...grupos.value.outras])
// "Todas as lojas" = os e-mails da pasta escolhida (cada e-mail tem um selo só).
const totalDasLojas = computed(() => totalDasLojasNaPasta(props.lojas))

function escolherPasta(id: string | null) {
  if (!props.desligado && id !== props.pasta) emit('pasta', id)
}
function escolherLoja(chave: string | null) {
  if (!props.desligado && chave !== props.loja) emit('loja', chave)
}
</script>

<template>
  <!-- Tela larga: a coluna, como a do Tuta -->
  <nav v-if="modo === 'coluna'" class="min-h-0 flex-col bg-muted/30 text-sm" aria-label="Pastas e lojas da caixa" data-mail-pastas-coluna>
    <div class="min-h-0 flex-1 overflow-y-auto px-2 py-3" data-mail-pastas-rolagem>
      <ul class="space-y-0.5">
        <li v-for="p in deCima" :key="p.id || 'todas'">
          <button
            type="button"
            class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
            :class="p.id === pasta ? 'bg-primary/10 font-medium text-primary' : 'text-foreground hover:bg-muted'"
            :aria-current="p.id === pasta ? 'true' : undefined"
            :disabled="desligado"
            :data-pasta="p.id || 'todas'"
            @click="escolherPasta(p.id)"
          >
            <component :is="ICONES[iconeDaPasta(p)]" class="h-4 w-4 shrink-0" aria-hidden="true" />
            <span class="min-w-0 flex-1 truncate">{{ p.nome }}</span>
            <span class="shrink-0 text-xs tabular-nums" :class="p.quantidade ? 'text-muted-foreground' : 'text-muted-foreground/50'">{{ quantidadeLegivel(p.quantidade) }}</span>
          </button>
        </li>
      </ul>

      <template v-if="pessoais.length">
        <p class="mt-4 px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Suas pastas</p>
        <ul class="space-y-0.5">
          <li v-for="p in pessoais" :key="p.id || p.nome">
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
              :class="p.id === pasta ? 'bg-primary/10 font-medium text-primary' : 'text-foreground hover:bg-muted'"
              :aria-current="p.id === pasta ? 'true' : undefined"
              :title="p.caminho && p.caminho !== p.nome ? p.caminho : undefined"
              :disabled="desligado"
              :data-pasta="p.id"
              @click="escolherPasta(p.id)"
            >
              <component :is="ICONES[iconeDaPasta(p)]" class="h-4 w-4 shrink-0" aria-hidden="true" />
              <span class="min-w-0 flex-1 truncate">{{ p.nome }}</span>
              <span class="shrink-0 text-xs tabular-nums" :class="p.quantidade ? 'text-muted-foreground' : 'text-muted-foreground/50'">{{ quantidadeLegivel(p.quantidade) }}</span>
            </button>
          </li>
        </ul>
      </template>
    </div>

    <!-- As lojas no pé da coluna, com rolagem própria: sempre à vista. -->
    <div v-if="lojas.length" class="flex max-h-[360px] min-h-0 shrink-0 flex-col border-t" data-mail-lojas-bloco>
      <p class="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Lojas</p>
      <ul class="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3" data-mail-lojas-coluna>
        <li>
          <button
            type="button"
            class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
            :class="loja === null ? 'bg-primary/10 font-medium text-primary' : 'text-foreground hover:bg-muted'"
            :aria-current="loja === null ? 'true' : undefined"
            :disabled="desligado"
            data-loja="todas"
            @click="escolherLoja(null)"
          >
            <Store class="h-4 w-4 shrink-0" aria-hidden="true" />
            <span class="min-w-0 flex-1 truncate">Todas as lojas</span>
            <span class="shrink-0 text-xs tabular-nums text-muted-foreground">{{ quantidadeLegivel(totalDasLojas) }}</span>
          </button>
        </li>
        <li v-for="l in lojas" :key="l.chave">
          <button
            type="button"
            class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors"
            :class="l.chave === loja ? 'bg-primary/10 font-medium text-primary' : 'text-foreground hover:bg-muted'"
            :aria-current="l.chave === loja ? 'true' : undefined"
            :disabled="desligado"
            :data-loja="l.chave"
            @click="escolherLoja(l.chave)"
          >
            <span class="flex h-4 w-4 shrink-0 items-center justify-center">
              <AtendimentoIconePlataforma v-if="(l.tipo === 'loja' || l.tipo === 'site') && l.plataforma" :plataforma="l.plataforma" :tamanho="16" decorativo />
              <component :is="ICONES_DE_LOJA[l.tipo] || CircleDashed" v-else class="h-4 w-4" aria-hidden="true" />
            </span>
            <span class="min-w-0 flex-1 truncate">{{ l.rotulo }}</span>
            <span class="shrink-0 text-xs tabular-nums" :class="l.quantidade ? 'text-muted-foreground' : 'text-muted-foreground/50'">{{ quantidadeLegivel(l.quantidade) }}</span>
          </button>
        </li>
      </ul>
    </div>
  </nav>

  <!-- Tela estreita: os dois seletores no topo da lista -->
  <div v-else class="grid grid-cols-2 gap-2" data-mail-pastas-seletor>
    <label class="min-w-0 text-xs text-muted-foreground">
      <span class="sr-only">Pasta</span>
      <select
        class="w-full min-w-0 rounded border bg-background p-2 text-sm text-foreground"
        aria-label="Pasta"
        :value="valorDoSeletor(pasta)"
        :disabled="desligado"
        @change="escolherPasta(filtroDoSeletor(($event.target as HTMLSelectElement).value))"
      >
        <option v-if="grupos.todas" value="">{{ rotuloComQuantidade(grupos.todas.nome, grupos.todas.quantidade) }}</option>
        <optgroup v-if="grupos.sistema.length" label="Pastas">
          <option v-for="p in grupos.sistema" :key="p.id || ''" :value="valorDoSeletor(p.id)">{{ rotuloComQuantidade(p.nome, p.quantidade) }}</option>
        </optgroup>
        <optgroup v-if="pessoais.length" label="Suas pastas">
          <option v-for="p in pessoais" :key="p.id || ''" :value="valorDoSeletor(p.id)">{{ rotuloComQuantidade(p.nome, p.quantidade) }}</option>
        </optgroup>
      </select>
    </label>
    <label class="min-w-0 text-xs text-muted-foreground">
      <span class="sr-only">Loja</span>
      <select
        class="w-full min-w-0 rounded border bg-background p-2 text-sm text-foreground"
        aria-label="Loja"
        :value="valorDoSeletor(loja)"
        :disabled="desligado || !lojas.length"
        @change="escolherLoja(filtroDoSeletor(($event.target as HTMLSelectElement).value))"
      >
        <option value="">{{ rotuloComQuantidade('Todas as lojas', totalDasLojas) }}</option>
        <option v-for="l in lojas" :key="l.chave" :value="l.chave">{{ rotuloComQuantidade(l.rotulo, l.quantidade) }}</option>
      </select>
    </label>
  </div>
</template>
