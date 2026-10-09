<script lang="ts">
// A caixa de resposta da ABA (RF2, 02/10/2026): quando a aba ativa não é a da
// conversa aberta e quem responde nela é OUTRA conversa (a pergunta no
// anúncio na Pré-venda, o pack na Pós-venda, a avaliação, o e-mail, o Zap),
// a resposta sai POR ESSA conversa — `POST /conversas/{id}/responder`, o
// caminho único de saída, com as travas dela (envio desligado, conversa
// bloqueada, loja em observar…). Sem conversa que responda (Mediador): só
// leitura, com o porquê.
//
// Pequena de propósito: sem sugestão da IA, foto ou respostas prontas — para
// isso, "abrir a conversa" leva à conversa de origem, com a caixa completa.
// Na avaliação a resposta é PÚBLICA: a mesma confirmação do cartão antes de
// qualquer envio.
// Na aba E-mail com a conversa de e-mail da PONTE (08/10/2026), a caixa ganha
// a prévia (AtendimentoEmailResposta: De, Para, Assunto e as travas do e-mail
// escolhido) e o corpo leva `mail_message_id` e `confirmar_nao_responde`.
import type { Aba } from '~/components/AtendimentoAbas.vue'
import { AVISO_SO_LEITURA, ERROS, motivoLegivel } from '~/components/AtendimentoPlataforma.vue'

// O texto em escrita, por conversa de origem, enquanto a página está aberta:
// a caixa some ao voltar para a aba da conversa (fora do componente).
const rascunhosAba = new Map<string, string>()

// Por que não dá para responder nesta aba, em linguagem simples ('' = dá).
export function bloqueioDaAba(aba: Pick<Aba, 'responde' | 'chave'> | null | undefined, canEdit: boolean): string {
  const r = aba?.responde
  if (!r || !r.conversa_id) return r?.motivo || 'Esta aba é só de leitura.'
  // Fase de observação (07/10/2026): quem só lê não responde.
  if (!canEdit) return AVISO_SO_LEITURA
  if (r.pode_enviar) return ''
  const codigo = r.codigo || ''
  if (codigo === 'conversa_bloqueada') {
    // O ML manda o código cru ("blocked_by_…"): fica a frase da tela.
    const m = (r.motivo || '').trim()
    return m && !/^blocked/i.test(m) ? `${ERROS.conversa_bloqueada.replace(/\.$/, '')}: ${m}` : ERROS.conversa_bloqueada
  }
  if (codigo && ERROS[codigo]) return ERROS[codigo]
  return motivoLegivel(r.motivo) || 'O envio não está liberado para esta conversa.'
}
</script>

<script setup lang="ts">
import { ExternalLink, Globe, Loader2, Lock, Send, X } from 'lucide-vue-next'
import { AVISO_RESPOSTA_PUBLICA, perguntaRespostaPublica } from '~/components/AtendimentoAvaliacao.vue'
import { CODIGOS_CONFIRMAVEIS, LIMITE_RESPOSTA_EMAIL, TRAVAS_GERAIS_DO_EMAIL, corpoDoEmail } from '~/components/AtendimentoEmailResposta.vue'
import { erroDaApi, statusDoErro, tamanhoDoEnvio, type Mensagem } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  aba: Aba
  canEdit: boolean
  // A plataforma da loja (a mesma da conversa aberta): conta como ela conta.
  plataforma: string | null
  // O aviso de resposta pública (o do backend das avaliações), quando houver.
  avisoPublica?: string | null
  // E-mail: o escolhido no cartão ("responder este e-mail"); null = o mais novo.
  mailMessageId?: string | null
}>()
const emit = defineEmits<{
  (e: 'enviada', m: Mensagem | null): void
  (e: 'abrirConversa', id: string): void
  (e: 'responderMaisNovo'): void
}>()

const { api } = useApi()
const toasts = useToasts()

const responde = computed(() => props.aba.responde)
const conversaId = computed(() => responde.value?.conversa_id ?? null)
const origem = computed(() => props.aba.conversas.find((c) => c.id === conversaId.value) ?? null)
// A conversa que responde é de e-mail da ponte: canal `email` E as mensagens
// dela trazem o resumo do e-mail. A conversa de chat/pós-venda que recebeu o
// AVISO por e-mail da plataforma (a ponte grava `email` nela) NÃO é e-mail: a
// resposta sai pelo chat, com o limite e o aviso do canal.
const ehEmail = computed(() => !!conversaId.value && responde.value?.canal === 'email' && props.aba.mensagens.some((m) => m.conversa_id === conversaId.value && !!m.email?.tipo))
const confirmouNaoResponde = ref(false)
const travaEmail = ref<string | null>(null)
// No e-mail, as travas do e-mail escolhido vêm da prévia (como na caixa da conversa).
const bloqueio = computed(() => {
  const r = responde.value
  if (!ehEmail.value || !props.canEdit || !r?.conversa_id) return bloqueioDaAba(props.aba, props.canEdit)
  if (!r.pode_enviar && TRAVAS_GERAIS_DO_EMAIL.has(r.codigo || '')) return bloqueioDaAba(props.aba, props.canEdit)
  if (travaEmail.value !== null) return travaEmail.value
  if (r.pode_enviar || CODIGOS_CONFIRMAVEIS.has(r.codigo || '')) return ''
  return bloqueioDaAba(props.aba, props.canEdit)
})

const texto = ref((conversaId.value && rascunhosAba.get(conversaId.value)) || '')
watch(conversaId, (novo) => {
  texto.value = (novo && rascunhosAba.get(novo)) || ''
  erro.value = null
})
watch(texto, (t) => {
  const id = conversaId.value
  if (!id) return
  if (t.trim()) rascunhosAba.set(id, t)
  else rascunhosAba.delete(id)
})

const limite = computed(() => (ehEmail.value ? LIMITE_RESPOSTA_EMAIL : 0) || responde.value?.limite_caracteres || 0)
// No e-mail, a trava que veio da prévia já aparece nela (a faixa não repete).
const faixa = computed(() => (ehEmail.value && travaEmail.value && bloqueio.value === travaEmail.value ? '' : bloqueio.value))
const tamanho = computed(() => tamanhoDoEnvio(texto.value, props.plataforma))
const acimaDoLimite = computed(() => !!limite.value && tamanho.value > limite.value)
const enviando = ref(false)
type Erro = { texto: string; motivos: string[]; confirmar?: boolean; naoResponde?: boolean }
const erro = ref<Erro | null>(null)
const podeEnviar = computed(() => !bloqueio.value && !!tamanho.value && !acimaDoLimite.value && !enviando.value)

async function enviar(opcoes?: { confirmar?: boolean; confirmarNaoResponde?: boolean }) {
  const id = conversaId.value
  const r = responde.value
  const t = texto.value.trim()
  if (!id || !r || !podeEnviar.value) return
  // Avaliação: a resposta aparece no anúncio, para qualquer comprador.
  if (r.publica && !confirm(perguntaRespostaPublica(props.avisoPublica, t))) return
  const body: { texto: string; ultima_vista_id?: string; confirmar?: boolean; mail_message_id?: string; confirmar_nao_responde?: boolean } = { texto: t }
  if (r.ultima_vista_id) body.ultima_vista_id = r.ultima_vista_id
  if (opcoes?.confirmar === true) body.confirmar = true
  if (ehEmail.value) Object.assign(body, corpoDoEmail(props.mailMessageId, confirmouNaoResponde.value || opcoes?.confirmarNaoResponde === true))
  enviando.value = true
  erro.value = null
  try {
    const res = await api<{ mensagem: Mensagem }>(`/api/atendimento/conversas/${encodeURIComponent(id)}/responder`, { method: 'POST', body })
    const m = res?.mensagem ?? null
    if (m?.status === 'falhou') {
      erro.value = { texto: 'A plataforma recusou — a resposta NÃO chegou ao comprador. O texto continua na caixa.', motivos: [] }
    } else {
      rascunhosAba.delete(id)
      if (conversaId.value === id) texto.value = ''
      confirmouNaoResponde.value = false
      if (ehEmail.value) toasts.success(`Resposta na fila do e-mail (${r.canal_rotulo || 'outra conversa'})`)
      else toasts.success(`Resposta enviada (${r.canal_rotulo || 'outra conversa'})`)
    }
    emit('enviada', m)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const st = statusDoErro(e)
    if (!code && (!st || st >= 500)) {
      // Sem resposta que diga o que houve: pode ter saído — não manda de novo.
      erro.value = { texto: 'Não deu para confirmar o envio — pode ter saído.', motivos: ['Abra a conversa de origem e confira antes de mandar de novo.'] }
      emit('enviada', null)
    } else {
      erro.value = { ...erroDaApi(e, 'Não consegui enviar'), confirmar: code === 'conversa_mudou' || CODIGOS_CONFIRMAVEIS.has(code), naoResponde: CODIGOS_CONFIRMAVEIS.has(code) }
    }
  } finally {
    enviando.value = false
  }
}
function aoTeclar(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    void enviar()
  }
}
</script>

<template>
  <div class="space-y-1.5" data-caixa-da-aba>
    <!-- por onde sai (sem conversa — o Mediador —, o aviso abaixo já diz) -->
    <div v-if="conversaId" class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-muted-foreground">
      <span>Responde em <span class="font-medium text-foreground">{{ responde.canal_rotulo || 'outra conversa' }}</span><template v-if="origem?.titulo"> · {{ origem.titulo }}</template></span>
      <button type="button" class="inline-flex items-center gap-0.5 underline hover:text-foreground" title="abrir a conversa de origem (com a caixa completa: IA, respostas prontas, foto)" @click="emit('abrirConversa', conversaId)">
        abrir a conversa <ExternalLink class="size-3" />
      </button>
    </div>

    <div v-if="faixa" class="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-900 dark:text-amber-200" data-bloqueio-aba>
      <Lock class="mt-0.5 size-3.5 shrink-0" />
      <span class="flex-1">{{ faixa }}</span>
    </div>

    <template v-if="conversaId">
      <!-- E-mail da ponte: como a resposta vai sair e as travas do e-mail escolhido -->
      <AtendimentoEmailResposta
        v-if="ehEmail && canEdit"
        v-model:confirmou-nao-responde="confirmouNaoResponde"
        v-model:trava="travaEmail"
        :conversa-id="conversaId"
        :mail-message-id="mailMessageId ?? null"
        :versao="aba.total"
        @responder-mais-novo="emit('responderMaisNovo')"
      />
      <div v-if="responde.publica && !bloqueio" class="flex items-start gap-1.5 px-0.5 text-[11px] leading-4 text-amber-800 dark:text-amber-300">
        <Globe class="mt-px size-3.5 shrink-0" aria-hidden="true" />
        <span>{{ avisoPublica || AVISO_RESPOSTA_PUBLICA }}</span>
      </div>
      <textarea
        v-model="texto"
        rows="2"
        :disabled="!!bloqueio || enviando"
        class="block max-h-[30vh] min-h-[56px] w-full resize-y rounded-md border bg-background px-2.5 py-2 text-sm [field-sizing:content] focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
        :placeholder="bloqueio ? 'Resposta desabilitada nesta aba' : `Escreva a resposta (${responde.canal_rotulo || 'outra conversa'})… (Ctrl+Enter envia)`"
        :aria-label="`resposta pela aba ${aba.rotulo}`"
        @keydown="aoTeclar"
      />
      <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-700 dark:text-red-300">
        <div class="flex items-start gap-2">
          <span class="flex-1 font-medium">{{ erro.texto }}<template v-if="erro.motivos.length">:</template></span>
          <button type="button" class="shrink-0 opacity-70 hover:opacity-100" title="fechar" @click="erro = null"><X class="size-3.5" /></button>
        </div>
        <ul v-if="erro.motivos.length" class="mt-0.5 list-inside list-disc">
          <li v-for="(mo, i) in erro.motivos" :key="i">{{ mo }}</li>
        </ul>
        <button
          v-if="erro.confirmar && podeEnviar"
          type="button"
          class="mt-1 rounded border border-red-500/40 bg-background px-1.5 py-0.5 font-medium hover:bg-red-500/10"
          @click="enviar(erro.naoResponde ? { confirmarNaoResponde: true } : { confirmar: true })"
        >Conferi — enviar mesmo assim</button>
      </div>
      <div class="flex items-center gap-1.5">
        <span
          v-if="limite && !bloqueio"
          class="text-[11px] tabular-nums"
          :class="acimaDoLimite ? 'font-semibold text-red-600 dark:text-red-400' : 'text-muted-foreground'"
        >{{ tamanho }}/{{ limite }}</span>
        <Button size="sm" class="ml-auto h-8" :disabled="!podeEnviar" :title="bloqueio || 'enviar por esta conversa (Ctrl+Enter)'" @click="enviar()">
          <Loader2 v-if="enviando" class="mr-1.5 size-4 animate-spin" />
          <Send v-else class="mr-1.5 size-4" />
          Enviar
        </Button>
      </div>
    </template>
  </div>
</template>
