<script setup lang="ts">
// Cancelamento de uma nota emitida (diálogo central), montado UMA vez pela
// página e aberto por tela.cancelar(e). Motivo 1/2/9 em cartões, sugestões de
// justificativa, justificativa de 15 a 255 letras e — se o cancelamento for
// recusado — o motivo aqui mesmo, com "Tentar de novo". Se a conexão cair no
// meio, não se tenta de novo às cegas: o próprio diálogo oferece "Atualizar
// cancelamento agora".
//
// 29/09/2026 (motor NFE.io): sem senha (a NFE.io assina). O motivo e a
// justificativa ficam guardados no DaVinci (a NFE.io não recebe motivo). A
// prefeitura pode demorar para confirmar: a nota fica "Cancelando" e o diálogo
// pergunta à NFE.io por alguns segundos antes de fechar.
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { Ban, Loader2, RefreshCw } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  erroApi, falhaDeRede, fmtBrl, fmtMes, fmtPct, mesParaData, MOTIVOS_CANCELAMENTO, plural, problemasApi,
  useNfseTela, type CancelarApi, type Emissao, type EventoNota, type Msg,
  useNfseApi,
} from '~/lib/nfse'

type Motivo = 1 | 2 | 9
// O contrato devolve a nota (EmissaoOut); a versão antiga devolvia { evento, emissao }.
type Resposta = Emissao | { evento?: { status: string; erros: Msg[] | null } | null; emissao: Emissao }

// Depois do pedido, pergunta à NFE.io a cada 4 s por até 20 s se a prefeitura já confirmou.
const ESPERA_MS = 4000
const ESPERA_VEZES = 5

// Igual ao backend (schemas/nfse.py CancelarIn e services/nfse/emissao.py).
const MIN = 15
const MAX = 255

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

const aberto = ref(false)
const nota = ref<Emissao | null>(null)
const motivo = ref<Motivo>(1)
const justificativa = ref('')
const tentou = ref(false)
const enviando = ref(false)
const conferindo = ref(false)
const esperando = ref(false) // pedido aceito, esperando a prefeitura confirmar
const errosGov = ref<Msg[] | null>(null)
const erroGeral = ref<string | null>(null)
const problemas = ref<string[]>([])
// Última versão da nota que o servidor devolveu (depois de uma recusa ou de
// uma queda de conexão). Fechar devolve ela, para a tela se atualizar.
const ultima = ref<Emissao | null>(null)
const campo = ref<HTMLTextAreaElement | null>(null)

// Nota de percentual (29/09): "0,5% de R$ 200.000,00" junto do valor.
const deOnde = computed(() => {
  const x = nota.value
  if (!x) return ''
  const p = x.percentual ?? x.snapshot?.servico?.percentual ?? null
  const base = x.base_calculo ?? x.snapshot?.servico?.base_calculo ?? null
  return p != null && base != null ? `${fmtPct(p)} de ${fmtBrl(base)}` : ''
})

let resolver: ((v: Emissao | null) => void) | null = null

function cancelar(e: Emissao): Promise<Emissao | null> {
  // Chamado de novo com um aberto: o anterior vale como desistência.
  resolver?.(null)
  nota.value = e
  motivo.value = 1
  justificativa.value = ''
  tentou.value = false
  enviando.value = false
  esperando.value = false
  errosGov.value = null
  erroGeral.value = null
  problemas.value = []
  ultima.value = null
  aberto.value = true
  return new Promise<Emissao | null>((res) => {
    resolver = res
  })
}

function terminar(v: Emissao | null) {
  aberto.value = false
  const r = resolver
  resolver = null
  r?.(v)
}

function aoMudar(v: boolean) {
  if (!v && !enviando.value && !conferindo.value) {
    esperando.value = false
    terminar(ultima.value)
  }
}

const exposto: CancelarApi = {
  cancelar,
  // Pedido a caminho ou esperando a prefeitura: a senha da página que vencer
  // espera acabar (trancar desmontaria a janela e o resultado se perderia).
  ocupado: () => aberto.value && (enviando.value || conferindo.value || esperando.value),
}
defineExpose(exposto)

// Desmontada (a página trancou ou saiu): para de perguntar à prefeitura.
onBeforeUnmount(() => {
  aberto.value = false
})

// --- Formulário -------------------------------------------------------------------

const opcoes = ([1, 2, 9] as const).map((v) => ({
  valor: v,
  titulo: MOTIVOS_CANCELAMENTO[v].titulo,
  descricao: MOTIVOS_CANCELAMENTO[v].descricao,
}))

const sugestoes = computed(() => MOTIVOS_CANCELAMENTO[motivo.value].sugestoes)

function escolherMotivo(v: string | number | null) {
  if (v !== 1 && v !== 2 && v !== 9) return
  // A justificativa era uma sugestão do motivo anterior: não serve mais.
  const antes = MOTIVOS_CANCELAMENTO[motivo.value].sugestoes
  motivo.value = v
  if (antes.includes(justificativa.value.trim())) justificativa.value = ''
}

async function usarSugestao(s: string) {
  justificativa.value = s
  await nextTick()
  const el = campo.value
  if (el) {
    el.focus()
    el.setSelectionRange(s.length, s.length)
  }
}

const tamanho = computed(() => justificativa.value.trim().length)
const faltam = computed(() => Math.max(0, MIN - tamanho.value))
const valida = computed(() => tamanho.value >= MIN && tamanho.value <= MAX)
const contador = computed(() =>
  faltam.value > 0 ? `faltam ${plural(faltam.value, 'letra', 'letras')}` : `${tamanho.value} de ${MAX}`,
)
const erroJustificativa = computed(() => {
  if (!tentou.value || valida.value) return null
  return faltam.value > 0
    ? `Escreva pelo menos ${MIN} letras explicando o motivo.`
    : `A justificativa pode ter no máximo ${MAX} letras.`
})

// Depois de uma queda de conexão, a nota pode ter ido para "cancelando": aí
// não se tenta de novo às cegas — atualiza-se a nota (aqui mesmo).
const travado = computed(() => !!ultima.value && ultima.value.status !== 'emitida')
const ocupado = computed(() => enviando.value || conferindo.value || esperando.value)

async function conferirAgora() {
  const u = ultima.value
  if (!u || ocupado.value) return
  conferindo.value = true
  try {
    // tela.conferir pergunta à NFE.io, avisa o resultado (toast) e recarrega a tela.
    const nova = await tela.conferir(u)
    if (!nova) return
    ultima.value = nova
    if (nova.status === 'cancelada') {
      terminar(nova)
      return
    }
    if (nova.status === 'emitida') {
      // O pedido não pegou: a nota segue emitida e dá para tentar de novo.
      erroGeral.value = null
      errosGov.value = null
      problemas.value = []
    }
  } finally {
    conferindo.value = false
  }
}

const textoBotao = computed(() => {
  if (enviando.value || esperando.value) return 'Cancelando…'
  return errosGov.value || erroGeral.value ? 'Tentar de novo' : 'Cancelar a nota'
})

const titulo = computed(() => (nota.value?.n_nfse ? `Cancelar a nota nº ${nota.value.n_nfse}` : 'Cancelar a nota'))
const tituloErro = computed(() =>
  travado.value ? 'O cancelamento ainda não foi confirmado' : 'Não deu para cancelar',
)

// --- Envio --------------------------------------------------------------------------

async function releNota(n: Emissao): Promise<Emissao | null> {
  try {
    const q = new URLSearchParams({
      competencia: mesParaData(n.competencia),
      company_id: n.company_id,
    })
    const lista = await api<Emissao[]>(`/api/nfse/emissoes?${q}`)
    return lista.find((x) => x.id === n.id) ?? null
  } catch {
    return null
  }
}

// O motivo da recusa: o que veio junto, senão o último pedido de cancelamento
// guardado (linha do tempo), senão a última resposta da NFE.io.
async function motivoRecusa(e: Emissao, evento?: { erros: Msg[] | null } | null): Promise<Msg[]> {
  if (evento?.erros?.length) return evento.erros
  try {
    const evs = await api<EventoNota[]>(`/api/nfse/emissoes/${e.id}/eventos`)
    const ultimo = [...evs].reverse().find((v) => v.status === 'rejeitado' && v.erros?.length)
    if (ultimo?.erros?.length) return ultimo.erros
  } catch {
    // fica com o texto abaixo
  }
  if (e.erros?.length) return e.erros
  return [{ descricao: e.flow_message || 'O cancelamento foi recusado sem dizer o motivo.' }]
}

function esperar(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms))
}

// A prefeitura ainda não confirmou: pergunta à NFE.io por alguns segundos.
async function esperarPrefeitura(e: Emissao): Promise<Emissao> {
  esperando.value = true
  try {
    let atual = e
    for (let i = 0; i < ESPERA_VEZES && aberto.value && atual.status === 'cancelando'; i++) {
      await esperar(ESPERA_MS)
      if (!aberto.value) break
      const r = await tela.atualizarEmissoes([e.id])
      const nova = r?.find((x) => x.id === e.id)
      if (nova) atual = nova
    }
    return atual
  } finally {
    esperando.value = false
  }
}

async function confirmar() {
  const n = nota.value
  if (!n || ocupado.value || travado.value) return
  tentou.value = true
  if (!valida.value) {
    campo.value?.focus()
    return
  }
  const rotulo = n.n_nfse ? `Nota nº ${n.n_nfse}` : 'Nota'
  enviando.value = true
  erroGeral.value = null
  errosGov.value = null
  problemas.value = []
  try {
    const r = await api<Resposta>(`/api/nfse/emissoes/${n.id}/cancelar`, {
      method: 'POST',
      body: { c_motivo: motivo.value, x_motivo: justificativa.value.trim() },
    })
    let e = 'emissao' in r && r.emissao ? r.emissao : (r as Emissao)
    const evento = 'emissao' in r ? r.evento ?? null : null
    enviando.value = false
    if (e.status === 'cancelando') e = await esperarPrefeitura(e)
    ultima.value = e
    if (e.status === 'cancelada') {
      toasts.success(`${rotulo} cancelada`)
      terminar(e)
      return
    }
    if (e.status === 'cancelando') {
      toasts.info(
        'Cancelamento pedido',
        'A prefeitura ainda não confirmou. O DaVinci confere sozinho; veja depois em Notas emitidas.',
      )
      terminar(e)
      return
    }
    // Continua emitida: o cancelamento foi recusado. Fica aberto, com o motivo
    // (e avisa também fora da janela, caso ela feche antes de a pessoa ler).
    errosGov.value = await motivoRecusa(e, evento)
    toasts.warning(`${rotulo}: o cancelamento foi recusado`, errosGov.value[0]?.descricao || 'Veja o motivo na janela de cancelamento.')
  } catch (err) {
    errosGov.value = null
    if (falhaDeRede(err)) {
      const nova = await releNota(n)
      if (nova) ultima.value = nova
      erroGeral.value =
        nova && nova.status === 'emitida'
          ? 'A conexão caiu antes de o pedido sair: a nota continua emitida. Pode tentar de novo.'
          : 'A conexão caiu no meio do pedido e ele pode ter chegado à NFE.io. Atualize o cancelamento antes de tentar de novo.'
    } else {
      erroGeral.value = erroApi(err)
      problemas.value = problemasApi(err)
    }
  } finally {
    enviando.value = false
  }
}

function atalho(ev: KeyboardEvent) {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
    ev.preventDefault()
    confirmar()
  }
}
</script>

<template>
  <NfseDialog
    :open="aberto"
    :titulo="titulo"
    descricao="A NFE.io pede o cancelamento à prefeitura e a nota deixa de valer."
    tamanho="md"
    tom="perigo"
    :icone="Ban"
    :fechavel="!ocupado"
    foco-inicial="#nfse-cancelar-justificativa"
    @update:open="aoMudar"
  >
    <template v-if="nota">
      <!-- A nota -->
      <div class="rounded-md bg-muted/40 p-3 text-sm">
        <div class="font-medium">{{ nota.prestador_nome || 'Empresa' }} → {{ nota.tomador_nome || 'tomador' }}</div>
        <div class="text-xs tabular-nums text-muted-foreground">
          {{ fmtBrl(nota.valor_servico) }}<template v-if="deOnde"> ({{ deOnde }})</template> · {{ fmtMes(nota.competencia) }}
          <template v-if="nota.teste"> · nota de teste</template>
        </div>
      </div>

      <NfseAviso tom="perigo">
        Cancelar não tem volta. A nota fica cancelada na prefeitura e deixa de valer.
        Alguns municípios têm prazo para cancelar.
      </NfseAviso>

      <NfseCampo rotulo="Motivo">
        <NfseOpcoes
          :model-value="motivo"
          :opcoes="opcoes"
          tom="perigo"
          :disabled="ocupado"
          @update:model-value="escolherMotivo"
        />
      </NfseCampo>

      <NfseCampo rotulo="Justificativa" para="nfse-cancelar-justificativa" :erro="erroJustificativa">
        <div v-if="sugestoes.length" class="flex flex-wrap items-center gap-1.5">
          <span class="text-xs text-muted-foreground">Sugestões:</span>
          <button
            v-for="s in sugestoes"
            :key="s"
            type="button"
            class="pill-muted transition-colors hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            :class="justificativa.trim() === s && 'border-primary/40 text-foreground'"
            :disabled="ocupado"
            @click="usarSugestao(s)"
          >
            {{ s }}
          </button>
        </div>
        <textarea
          id="nfse-cancelar-justificativa"
          ref="campo"
          v-model="justificativa"
          rows="3"
          :maxlength="MAX"
          :disabled="ocupado"
          placeholder="Ex.: valor emitido errado; a nota certa será emitida em seguida."
          class="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :class="erroJustificativa && 'border-red-500 dark:border-red-400'"
          :aria-invalid="!!erroJustificativa"
          @keydown="atalho"
        />
        <div
          class="text-right text-xs tabular-nums"
          :class="erroJustificativa ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'"
          aria-live="polite"
        >
          {{ contador }}
        </div>
      </NfseCampo>

      <NfseAviso v-if="esperando" tom="info" :icone="Loader2">
        Pedido feito. Esperando a prefeitura confirmar o cancelamento…
      </NfseAviso>

      <!-- Cancelamento recusado -->
      <NfseProblemas v-if="errosGov?.length" :erros="errosGov" :acoes="false" />

      <!-- Erro do nosso lado (conexão, empresa sem NFE.io…) -->
      <NfseAviso v-if="erroGeral" :tom="travado ? 'atencao' : 'perigo'" :titulo="tituloErro">
        {{ erroGeral }}
        <p v-if="travado && !tela.canEdit.value" class="text-xs">
          Peça a alguém com permissão de edição para atualizar a nota em Notas emitidas.
        </p>
        <template v-if="travado && tela.canEdit.value" #acoes>
          <Button type="button" size="sm" variant="outline" :disabled="ocupado" @click="conferirAgora">
            <Loader2 v-if="conferindo" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
            <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
            Atualizar cancelamento agora
          </Button>
        </template>
      </NfseAviso>
      <NfseProblemas v-if="problemas.length" :problemas="problemas" :acoes="false" />
    </template>

    <template #rodape>
      <Button type="button" variant="outline" size="sm" :disabled="ocupado" @click="terminar(ultima)">
        {{ travado ? 'Fechar' : 'Manter a nota' }}
      </Button>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        :disabled="ocupado || travado"
        @click="confirmar"
      >
        <Loader2 v-if="enviando || esperando" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
        <Ban v-else class="mr-1.5 size-4" aria-hidden="true" />
        {{ textoBotao }}
      </Button>
    </template>
  </NfseDialog>
</template>
