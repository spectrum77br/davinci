<script setup lang="ts">
// O balão da fila de envio (06/11/2026, lib/atendimentoFilaEnvio.ts): a mensagem
// que a pessoa já mandou e ainda não virou linha no servidor, no fim da linha do
// tempo. É uma pseudo-mensagem (id "fila:<n>"): sem os botões da mensagem real
// (apagar, marcar, copiar link). Quem decide e fala com a API é a conversa; daqui
// saem só os pedidos da mensagem que não saiu e da que espera numa fila parada.
import { Clock, Loader2, Pencil, RotateCcw, Send, TriangleAlert, X } from 'lucide-vue-next'
import { fmtDataHora, fmtDiaHora, origemLabel } from '~/components/AtendimentoPlataforma.vue'
import { seloDoItem, type ItemFila } from '~/lib/atendimentoFilaEnvio'

const props = defineProps<{
  item: ItemFila
  // A fila desta conversa está parada (recusa, incerto, resposta a conferir, anexo).
  parada?: boolean
  // O que segura a fila, quando não é a mensagem de cima que diz (só no primeiro balão).
  motivoParada?: string
  // O anexo de antes não saiu: a pessoa pode mandar esta mesmo assim (só no primeiro balão).
  podeSoltar?: boolean
}>()
const emit = defineEmits<{
  (e: 'tentar'): void
  (e: 'editar'): void
  (e: 'descartar'): void
  (e: 'soltar'): void
}>()

const selo = computed(() => seloDoItem(props.item, props.parada === true))
const autor = origemLabel({ autor: 'loja', origem: 'davinci_humano', autor_nome: null, do_afiliado: false })
</script>

<template>
  <div class="flex flex-col items-end" :data-msg-id="item.id" data-fila-balao :data-fila-estado="item.estado">
    <div class="mb-0.5 flex max-w-[85%] items-center gap-1 px-1 text-[11px] text-emerald-700 dark:text-emerald-300 md:max-w-[75%]">
      <span class="truncate">{{ autor }}</span>
      <!-- reclamação do ML: a quem a mensagem vai -->
      <span v-if="item.corpo.para" class="shrink-0 rounded px-1" :class="item.corpo.para === 'mediador' ? 'bg-violet-500/15 text-violet-700 dark:text-violet-300' : 'bg-muted text-muted-foreground'" data-fila-para>→ {{ item.corpo.para === 'mediador' ? 'Mercado Livre (mediador)' : 'comprador' }}</span>
    </div>
    <div class="max-w-[85%] rounded-lg rounded-tr-sm bg-sky-100 px-3 py-2 dark:bg-sky-900/45 md:max-w-[75%]" :class="item.estado === 'falhou' ? 'ring-1 ring-red-400/80' : ''">
      <div class="mb-1">
        <span class="inline-flex items-center gap-1 rounded px-1.5 py-px text-[11px]" :class="selo.cls" :title="selo.hint" data-fila-selo>
          <Loader2 v-if="item.estado === 'enviando'" class="size-3 animate-spin" />
          <TriangleAlert v-else-if="item.estado === 'falhou' || item.estado === 'incerto'" class="size-3" />
          <Pencil v-else-if="item.estado === 'editando'" class="size-3" />
          <Clock v-else class="size-3" />
          {{ selo.label }}
        </span>
      </div>
      <div class="whitespace-pre-wrap break-words text-sm leading-relaxed">{{ item.texto }}</div>
      <!-- Não saiu: nada chegou ao comprador. A fila desta conversa espera a pessoa resolver. -->
      <div v-if="item.estado === 'falhou'" class="mt-1 space-y-1 rounded bg-red-500/10 px-1.5 py-1 text-[11px] text-red-700 dark:text-red-300" data-fila-erro>
        <div class="font-medium">{{ item.erro?.texto || 'A mensagem não saiu.' }}</div>
        <ul v-if="item.erro?.motivos.length" class="list-inside list-disc">
          <li v-for="(mo, i) in item.erro.motivos" :key="i">{{ mo }}</li>
        </ul>
        <div class="flex flex-wrap gap-1 pt-0.5">
          <button
            type="button"
            class="inline-flex items-center gap-0.5 rounded border border-red-500/40 bg-background px-1.5 py-px font-medium hover:bg-red-500/10"
            :title="item.permiteConfirmar ? 'você viu a resposta que já saiu e quer mandar a sua também' : 'mandar o mesmo texto de novo'"
            data-fila-tentar
            @click="emit('tentar')"
          ><RotateCcw class="size-3" /> Tentar de novo</button>
          <button
            type="button"
            class="inline-flex items-center gap-0.5 rounded border border-red-500/40 bg-background px-1.5 py-px font-medium hover:bg-red-500/10"
            title="põe o texto na caixa para corrigir (o que já estiver escrito fica). As seguintes esperam você enviar de novo"
            data-fila-editar
            @click="emit('editar')"
          ><Pencil class="size-3" /> Editar</button>
          <button
            type="button"
            class="inline-flex items-center gap-0.5 rounded border border-red-500/40 bg-background px-1.5 py-px font-medium hover:bg-red-500/10"
            title="tira da fila: esta mensagem não sai e as seguintes saem sozinhas (para segurar alguma, descarte ela antes)"
            data-fila-descartar
            @click="emit('descartar')"
          ><X class="size-3" /> Descartar</button>
        </div>
      </div>
      <!-- Sem resposta do servidor: pode ter saído. Nunca reenvia sozinho. -->
      <div v-else-if="item.estado === 'incerto'" class="mt-1 space-y-1 rounded bg-amber-500/10 px-1.5 py-1 text-[11px] text-amber-900 dark:text-amber-200" data-fila-incerto>
        <div class="font-medium">Não deu para confirmar o envio: pode ter saído.</div>
        <div>Não envie de novo ainda: assim que a conversa atualizar, a tela mostra se a resposta saiu.</div>
      </div>
      <!-- "Editar" com mensagem esperando atrás: o texto está na caixa e este é o lugar dele. -->
      <div v-else-if="item.estado === 'editando'" class="mt-1 space-y-1 rounded bg-amber-500/10 px-1.5 py-1 text-[11px] text-amber-900 dark:text-amber-200" data-fila-editando>
        <div class="font-medium">O texto está na caixa de resposta. As seguintes esperam você enviar de novo.</div>
        <div class="flex flex-wrap gap-1 pt-0.5">
          <button
            type="button"
            class="inline-flex items-center gap-0.5 rounded border border-amber-500/40 bg-background px-1.5 py-px font-medium hover:bg-amber-500/10"
            title="desiste desta mensagem e as seguintes saem sozinhas (o que está na caixa fica lá)"
            data-fila-descartar
            @click="emit('descartar')"
          ><X class="size-3" /> Descartar</button>
        </div>
      </div>
      <!-- Esperando numa fila parada: dá para tirar antes de ela voltar a andar. -->
      <div v-else-if="item.estado === 'na_fila' && parada" class="mt-1 space-y-1 rounded bg-amber-500/10 px-1.5 py-1 text-[11px] text-amber-900 dark:text-amber-200" data-fila-parada>
        <div v-if="motivoParada">{{ motivoParada }}</div>
        <div class="flex flex-wrap gap-1 pt-0.5">
          <button
            v-if="podeSoltar"
            type="button"
            class="inline-flex items-center gap-0.5 rounded border border-amber-500/40 bg-background px-1.5 py-px font-medium hover:bg-amber-500/10"
            title="o anexo de antes não saiu: mandar esta mensagem assim mesmo"
            data-fila-soltar
            @click="emit('soltar')"
          ><Send class="size-3" /> Mandar mesmo assim</button>
          <button
            type="button"
            class="inline-flex items-center gap-0.5 rounded border border-amber-500/40 bg-background px-1.5 py-px font-medium hover:bg-amber-500/10"
            title="tira da fila: esta mensagem não sai"
            data-fila-descartar
            @click="emit('descartar')"
          ><X class="size-3" /> Descartar</button>
        </div>
      </div>
    </div>
    <div class="mt-0.5 px-1 text-[10px] text-muted-foreground" :title="fmtDataHora(item.criadoEm)">{{ fmtDiaHora(item.criadoEm) }}</div>
  </div>
</template>
