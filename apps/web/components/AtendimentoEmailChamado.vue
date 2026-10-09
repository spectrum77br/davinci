<script lang="ts">
// O CHAMADO DO SITE (RF6, 08/10/2026) no topo da conversa de e-mail: o
// formulário do site (Uranyx, Charlots, 7Buyers, Locagil) vira chamado com o
// PROTOCOLO lido do e-mail (US-26-0001…) e o TIPO (SAC, Atacado, Dúvidas e
// sugestões). A resposta sai do e-mail do tipo na marca (a prévia mostra).
//
// Quando a mesma cliente tem OUTRO chamado aberto na mesma marca, a faixa
// sugere AGRUPAR — nunca sozinho (dois protocolos podem ser dois assuntos):
// quem mexe clica "agrupar neste", confirma, e as mensagens deste chamado vão
// para o escolhido (POST /api/atendimento/email/conversas/{id}/agrupar); nada
// se apaga, este fica fechado com a marca de para onde foi.

import type { Chamado } from '~/components/AtendimentoEmailCartao.vue'

export function rotuloDoChamado(c: Pick<Chamado, 'protocolo' | 'tipo_rotulo' | 'marca_nome'> | null | undefined): string {
  if (!c) return ''
  const partes = [c.protocolo || 'sem protocolo']
  if (c.tipo_rotulo) partes.push(c.tipo_rotulo)
  if (c.marca_nome) partes.push(c.marca_nome)
  return partes.join(' · ')
}

export function perguntaAgrupar(origem: Pick<Chamado, 'protocolo'>, destino: Pick<Chamado, 'protocolo'>): string {
  return `Agrupar o chamado ${origem.protocolo || '(sem protocolo)'} no ${destino.protocolo || '(sem protocolo)'}?\n\n`
    + 'As mensagens e os e-mails deste chamado vão para o outro, que continua aberto; este fica fechado (nada se apaga). '
    + 'Agrupe só se for o MESMO assunto da mesma cliente.'
}
</script>

<script setup lang="ts">
import { Loader2, Ticket } from 'lucide-vue-next'
import { erroDaApi, fmtDiaHora } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  chamado: Chamado
  outros: Chamado[]
  // Agrupar: só quem mexe no /atendimento (a rota recusa os outros).
  podeMexer: boolean
}>()
const emit = defineEmits<{ (e: 'agrupado', destinoId: string): void }>()

const { api } = useApi()
const toasts = useToasts()
const agrupando = ref<string | null>(null)

async function agrupar(destino: Chamado) {
  if (agrupando.value || !props.podeMexer) return
  if (!confirm(perguntaAgrupar(props.chamado, destino))) return
  agrupando.value = destino.conversa_id
  try {
    const r = await api<{ conversa_id: string }>(`/api/atendimento/email/conversas/${encodeURIComponent(props.chamado.conversa_id)}/agrupar`, {
      method: 'POST',
      body: { conversa_id: destino.conversa_id },
    })
    toasts.success(`Chamado agrupado em ${destino.protocolo || 'outro chamado'}`)
    emit('agrupado', r?.conversa_id || destino.conversa_id)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui agrupar')
    toasts.error(er.texto, er.motivos)
  } finally {
    agrupando.value = null
  }
}
</script>

<template>
  <div class="space-y-1 border-b bg-teal-50/60 px-3 py-1.5 text-xs dark:bg-teal-900/15" data-email-chamado>
    <div class="flex flex-wrap items-center gap-1.5">
      <Ticket class="size-3.5 shrink-0 text-teal-700 dark:text-teal-300" aria-hidden="true" />
      <span class="font-medium">Chamado do site</span>
      <span class="rounded bg-teal-500/15 px-1.5 py-px font-medium text-teal-800 dark:text-teal-300">{{ chamado.protocolo || 'sem protocolo' }}</span>
      <span v-if="chamado.tipo_rotulo" class="text-muted-foreground">{{ chamado.tipo_rotulo }}</span>
      <span v-if="chamado.marca_nome" class="text-muted-foreground">· {{ chamado.marca_nome }}</span>
    </div>
    <div v-if="outros.length" class="space-y-0.5" data-email-outros-chamados>
      <div class="text-sky-800 dark:text-sky-300">
        Esta cliente tem {{ outros.length === 1 ? 'outro chamado aberto' : `${outros.length} outros chamados abertos` }} nesta marca. Se for o mesmo assunto, agrupe (nunca é automático).
      </div>
      <div v-for="o in outros" :key="o.conversa_id" class="flex flex-wrap items-center gap-1.5">
        <span class="font-medium">{{ rotuloDoChamado(o) }}</span>
        <span v-if="o.ultima_mensagem_em" class="text-muted-foreground">· {{ fmtDiaHora(o.ultima_mensagem_em) }}</span>
        <button
          v-if="podeMexer"
          type="button"
          class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 hover:bg-muted disabled:opacity-50"
          :disabled="!!agrupando"
          title="as mensagens deste chamado vão para aquele (pede confirmação)"
          data-email-agrupar
          @click="agrupar(o)"
        >
          <Loader2 v-if="agrupando === o.conversa_id" class="size-3 animate-spin" />agrupar neste
        </button>
      </div>
    </div>
  </div>
</template>
