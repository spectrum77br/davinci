<script lang="ts">
// O CHAMADO DO SITE (RF6, 08/10/2026) no topo da conversa de e-mail: o
// formulário do site (Uranyx, Charlots, 7Buyers, Locagil) vira chamado com o
// PROTOCOLO lido do e-mail (US-26-0001…) e o TIPO (SAC, Atacado, Dúvidas e
// sugestões). A resposta sai do e-mail do tipo na marca (a prévia mostra).
//
// A faixa mostra TODOS os protocolos do chamado (o principal primeiro e os
// agrupados nele), o status (Aberto · Aguardando cliente · Resolvido), os
// alertas do chamado (sem protocolo, formato errado, protocolo repetido) e o
// pedido que a cliente escreveu no formulário — só como SUGESTÃO (nunca liga
// sozinho: pode ser o pedido de outra pessoa).
//
// Quando a mesma cliente (o mesmo e-mail, telefone ou nº de pedido — estes dois
// só com nomes que não são de pessoas diferentes) tem OUTRO chamado aberto na
// mesma marca, a faixa sugere AGRUPAR — nunca sozinho (dois protocolos podem
// ser dois assuntos): "Ver chamado" abre o outro; "Agrupar"
// (quem mexe, confirma) junta os dois e FICA O MAIS ANTIGO (POST
// /api/atendimento/email/conversas/{id}/agrupar); "Não agrupar" (quem mexe)
// some com a sugestão e fica lembrado nos dois (…/nao-agrupar). Nada se apaga.

import type { Chamado } from '~/components/AtendimentoEmailCartao.vue'

export function rotuloDoChamado(c: Pick<Chamado, 'protocolo' | 'tipo_rotulo' | 'marca_nome'> | null | undefined): string {
  if (!c) return ''
  const partes = [c.protocolo || 'sem protocolo']
  if (c.tipo_rotulo) partes.push(c.tipo_rotulo)
  if (c.marca_nome) partes.push(c.marca_nome)
  return partes.join(' · ')
}

// Os protocolos que a faixa mostra: o principal e os agrupados (sem repetir).
export function protocolosDoChamado(c: Pick<Chamado, 'protocolo' | 'protocolos'> | null | undefined): string[] {
  if (!c) return []
  return [...new Set([c.protocolo, ...(c.protocolos || [])].filter((p): p is string => !!p))]
}

// O chamado com o nome da cliente: "US-26-0001 (Maria Souza)".
function comCliente(c: Pick<Chamado, 'protocolo' | 'cliente_nome'>): string {
  const p = c.protocolo || '(sem protocolo)'
  return c.cliente_nome ? `${p} (${c.cliente_nome})` : p
}

// `outro.fica_este` (do servidor): é o outro que fica (o mais antigo). Sem a
// informação, vale "este vai para o outro" (o jeito de antes). Os dois nomes
// aparecem; pelo telefone ou pelo pedido (e-mails diferentes), a pergunta
// pede para conferir que é a mesma pessoa (depois de agrupar, a resposta
// padrão pode ir para o e-mail do outro chamado).
export function perguntaAgrupar(
  este: Pick<Chamado, 'protocolo' | 'cliente_nome'>,
  outro: Pick<Chamado, 'protocolo' | 'cliente_nome' | 'fica_este' | 'motivo' | 'motivo_rotulo'>,
): string {
  const [vai, fica] = outro.fica_este === false ? [outro, este] : [este, outro]
  const conferir = outro.motivo && outro.motivo !== 'email'
    ? `ATENÇÃO: o que liga os dois é só o ${outro.motivo_rotulo || outro.motivo}. Confira que é a MESMA pessoa antes.\n\n`
    : ''
  return `Agrupar o chamado ${comCliente(vai)} no ${comCliente(fica)}?\n\n`
    + conferir
    + 'Fica o mais antigo (o protocolo principal): as mensagens e os e-mails do outro vão para ele e os dois protocolos continuam achando a conversa; '
    + 'o outro fica fechado (nada se apaga). Agrupe só se for o MESMO assunto da mesma cliente.'
}

// A frase em cima das sugestões: pelo e-mail é a mesma cliente; pelo telefone
// ou pelo pedido (texto livre do formulário), PODE ser — a pessoa confere.
export function fraseDasSugestoes(sugestoes: Pick<Chamado, 'motivo'>[]): string {
  if (!sugestoes.length) return ''
  const n = sugestoes.length === 1 ? 'outro chamado aberto' : `${sugestoes.length} outros chamados abertos`
  const soEmail = sugestoes.every((s) => !s.motivo || s.motivo === 'email')
  return soEmail
    ? `Esta cliente tem ${n} nesta marca. Se for o mesmo assunto, agrupe (nunca é automático; fica o mais antigo).`
    : `Pode ser a mesma cliente: há ${n} nesta marca com dados iguais (confira o nome). Se for a mesma pessoa e o mesmo assunto, agrupe (nunca é automático; fica o mais antigo).`
}

export function perguntaNaoAgrupar(outro: Pick<Chamado, 'protocolo'>): string {
  return `Não agrupar com ${outro.protocolo || 'o outro chamado'}?\n\nA sugestão some dos dois chamados (fica registrado quem decidiu). Dá para agrupar depois, se mudar de ideia.`
}

// O pedido do formulário no Bling: a frase da sugestão (nunca o nome de ninguém).
export function frasePedidoSugerido(c: Pick<Chamado, 'pedido_citado' | 'pedido_sugerido'>): string {
  const s = c.pedido_sugerido
  if (!c.pedido_citado) return ''
  if (!s || !s.achado) return `Pedido citado no formulário: ${c.pedido_citado} (não achado no Bling)`
  const partes = s.pedidos.map((p) => {
    const nums = [p.numero_bling && `Bling ${p.numero_bling}`, p.numero_loja && p.numero_loja !== p.numero_bling && `loja ${p.numero_loja}`].filter(Boolean).join(' / ')
    const conf = p.confere === true ? 'o nome confere' : p.confere === false ? 'OUTRO nome: confira antes de ligar' : 'sem nome para conferir'
    return `${nums} (${conf})`
  })
  return `Pedido citado no formulário: ${c.pedido_citado} — no Bling: ${partes.join('; ')}. Só sugestão: nada foi ligado.`
}
</script>

<script setup lang="ts">
import { Loader2, Ticket, TriangleAlert } from 'lucide-vue-next'
import { erroDaApi, fmtDiaHora } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  chamado: Chamado
  outros: Chamado[]
  // Agrupar e "Não agrupar": só quem mexe no /atendimento (a rota recusa os outros).
  podeMexer: boolean
}>()
const emit = defineEmits<{
  (e: 'agrupado', destinoId: string): void
  (e: 'abrir', conversaId: string): void
  (e: 'recusado', outroId: string): void
}>()

const { api } = useApi()
const toasts = useToasts()
const agrupando = ref<string | null>(null)
const recusando = ref<string | null>(null)
// O "Não agrupar" some com a sugestão já (a lista nova vem na próxima leitura).
const recusados = ref<string[]>([])
const sugestoes = computed(() => props.outros.filter((o) => !recusados.value.includes(o.conversa_id)))
const protocolos = computed(() => protocolosDoChamado(props.chamado))
const pedidoSugerido = computed(() => frasePedidoSugerido(props.chamado))

async function agrupar(outro: Chamado) {
  if (agrupando.value || !props.podeMexer) return
  if (!confirm(perguntaAgrupar(props.chamado, outro))) return
  agrupando.value = outro.conversa_id
  try {
    const r = await api<{ conversa_id: string; protocolo?: string | null }>(`/api/atendimento/email/conversas/${encodeURIComponent(props.chamado.conversa_id)}/agrupar`, {
      method: 'POST',
      body: { conversa_id: outro.conversa_id },
    })
    const ficou = r?.conversa_id || outro.conversa_id
    const protocolo = ficou === props.chamado.conversa_id ? props.chamado.protocolo : (r?.protocolo || outro.protocolo)
    toasts.success(`Chamados agrupados em ${protocolo || 'um chamado só'}`)
    emit('agrupado', ficou)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui agrupar')
    toasts.error(er.texto, er.motivos)
  } finally {
    agrupando.value = null
  }
}

async function naoAgrupar(outro: Chamado) {
  if (recusando.value || !props.podeMexer) return
  if (!confirm(perguntaNaoAgrupar(outro))) return
  recusando.value = outro.conversa_id
  try {
    await api(`/api/atendimento/email/conversas/${encodeURIComponent(props.chamado.conversa_id)}/nao-agrupar`, {
      method: 'POST',
      body: { conversa_id: outro.conversa_id },
    })
    recusados.value = [...recusados.value, outro.conversa_id]
    toasts.success(`Sugestão de agrupar com ${outro.protocolo || 'o outro chamado'} descartada`)
    emit('recusado', outro.conversa_id)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui registrar o "não agrupar"')
    toasts.error(er.texto, er.motivos)
  } finally {
    recusando.value = null
  }
}
</script>

<template>
  <div class="space-y-1 border-b bg-teal-50/60 px-3 py-1.5 text-xs dark:bg-teal-900/15" data-email-chamado>
    <div class="flex flex-wrap items-center gap-1.5">
      <Ticket class="size-3.5 shrink-0 text-teal-700 dark:text-teal-300" aria-hidden="true" />
      <span class="font-medium">Chamado do site</span>
      <template v-if="protocolos.length">
        <span
          v-for="(p, i) in protocolos"
          :key="p"
          class="rounded px-1.5 py-px font-medium"
          :class="i === 0 ? 'bg-teal-500/15 text-teal-800 dark:text-teal-300' : 'bg-muted text-muted-foreground'"
          :title="i === 0 ? 'protocolo principal (o mais antigo)' : 'protocolo agrupado neste chamado'"
          data-email-protocolo
        >{{ p }}</span>
      </template>
      <span v-else class="rounded bg-amber-500/15 px-1.5 py-px font-medium text-amber-800 dark:text-amber-300">sem protocolo</span>
      <span v-if="chamado.tipo_rotulo" class="text-muted-foreground">{{ chamado.tipo_rotulo }}</span>
      <span v-if="chamado.marca_nome" class="text-muted-foreground">· {{ chamado.marca_nome }}</span>
      <span
        v-if="chamado.status_rotulo"
        class="rounded-full border px-1.5 py-px text-[10px]"
        :class="chamado.status === 'resolvido' ? 'text-muted-foreground' : chamado.status === 'aguardando_cliente' ? 'border-sky-500/40 text-sky-800 dark:text-sky-300' : 'border-teal-500/40 text-teal-800 dark:text-teal-300'"
        data-email-chamado-status
      >{{ chamado.status_rotulo }}</span>
    </div>
    <div v-if="chamado.cliente_nome || chamado.telefone" class="text-muted-foreground" data-email-chamado-cliente>
      Cliente: {{ [chamado.cliente_nome, chamado.telefone].filter(Boolean).join(' · ') }}
    </div>
    <div v-if="pedidoSugerido" class="text-muted-foreground" data-email-pedido-sugerido>{{ pedidoSugerido }}</div>
    <div v-for="a in chamado.alertas || []" :key="a.codigo" class="flex items-start gap-1 text-amber-800 dark:text-amber-300" data-email-chamado-alerta>
      <TriangleAlert class="mt-px size-3 shrink-0" aria-hidden="true" /><span>{{ a.texto }}</span>
    </div>
    <div v-if="sugestoes.length" class="space-y-0.5" data-email-outros-chamados>
      <div class="text-sky-800 dark:text-sky-300">{{ fraseDasSugestoes(sugestoes) }}</div>
      <div v-for="o in sugestoes" :key="o.conversa_id" class="flex flex-wrap items-center gap-1.5">
        <span class="font-medium">{{ rotuloDoChamado(o) }}</span>
        <span v-if="o.cliente_nome" class="text-muted-foreground" data-email-outro-cliente>· {{ o.cliente_nome }}</span>
        <span v-if="o.motivo_rotulo" class="text-muted-foreground">· {{ o.motivo_rotulo }}</span>
        <span v-if="o.ultima_mensagem_em" class="text-muted-foreground">· {{ fmtDiaHora(o.ultima_mensagem_em) }}</span>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 hover:bg-muted"
          title="abrir o outro chamado"
          data-email-ver-chamado
          @click="emit('abrir', o.conversa_id)"
        >Ver chamado</button>
        <button
          v-if="podeMexer"
          type="button"
          class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 hover:bg-muted disabled:opacity-50"
          :disabled="!!agrupando || !!recusando"
          title="junta os dois chamados; fica o mais antigo (pede confirmação)"
          data-email-agrupar
          @click="agrupar(o)"
        >
          <Loader2 v-if="agrupando === o.conversa_id" class="size-3 animate-spin" />Agrupar conversas
        </button>
        <button
          v-if="podeMexer"
          type="button"
          class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 hover:bg-muted disabled:opacity-50"
          :disabled="!!agrupando || !!recusando"
          title="a sugestão some dos dois chamados (fica registrado)"
          data-email-nao-agrupar
          @click="naoAgrupar(o)"
        >
          <Loader2 v-if="recusando === o.conversa_id" class="size-3 animate-spin" />Não agrupar
        </button>
      </div>
    </div>
  </div>
</template>
