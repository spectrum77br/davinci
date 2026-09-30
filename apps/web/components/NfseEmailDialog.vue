<script setup lang="ts">
// Envio da nota por e-mail (diálogo central), montado UMA vez pela página e
// aberto por tela.enviarEmail(e) — no menu ⋯ da nota e no detalhe da nota.
//
// 30/09/2026 (Eduardo: o e-mail da nota não pode sair sozinho): o DaVinci não
// manda mais o e-mail do tomador à NFE.io (com ele, a NFE.io avisava o tomador
// sozinha na emissão e no cancelamento). O envio é só este, MANUAL: o próprio
// DaVinci manda o PDF e o XML para o endereço confirmado aqui. O "Para" vem com
// o e-mail do cadastro do tomador e dá para trocar (até 5 endereços). Tomador
// sem e-mail: o campo vem vazio e a caixa "guardar no cadastro" já vem
// marcada; digitou um e-mail diferente do cadastrado, a caixa aparece
// desmarcada (trocar o cadastro é escolha da pessoa).
//
// Falhou no meio de vários endereços: quem já recebeu sai do "Para" (clicar de
// novo não manda em dobro) e o erro de endereço que o servidor recusou aparece
// embaixo do campo, não na caixa geral.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { FlaskConical, Loader2, Mail } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  campoDoErro, codigoErro, EMAIL_VALIDO, erroApi, fmtBrl, fmtMes, listaDeEmails, plural, useNfseApi, useNfseTela,
  type EmailApi, type Emissao,
} from '~/lib/nfse'

// Igual ao backend (schemas/nfse.py EmailNotaIn: 1 a 5 endereços).
const MAX_ENDERECOS = 5

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

const aberto = ref(false)
const nota = ref<Emissao | null>(null)
const texto = ref('')
const guardar = ref(false)
const tentou = ref(false)
const enviando = ref(false)
const erroGeral = ref<string | null>(null)
// Endereço recusado pelo servidor (422 no "para"): some quando a pessoa mexe no campo.
const erroDoServidor = ref<string | null>(null)

let resolver: ((v: boolean) => void) | null = null

// --- Abrir / fechar ----------------------------------------------------------------

const tomador = computed(() => {
  const id = nota.value?.tomador_id
  return id ? tela.tomadores.value.find((t) => t.id === id) ?? null : null
})
const cadastrados = computed(() => listaDeEmails(tomador.value?.email))
const semEmailNoCadastro = computed(() => !cadastrados.value.length)

function enviar(e: Emissao): Promise<boolean> {
  // Chamado de novo com uma aberta: a anterior vale como desistência.
  resolver?.(false)
  nota.value = e
  texto.value = cadastrados.value.join(', ')
  guardar.value = semEmailNoCadastro.value
  tentou.value = false
  enviando.value = false
  erroGeral.value = null
  erroDoServidor.value = null
  aberto.value = true
  return new Promise<boolean>((res) => {
    resolver = res
  })
}

function terminar(v: boolean) {
  aberto.value = false
  const r = resolver
  resolver = null
  r?.(v)
}

function aoMudar(v: boolean) {
  if (!v && !enviando.value) terminar(false)
}

const exposto: EmailApi = {
  enviar,
  // E-mail saindo: a senha da página que vencer espera acabar.
  ocupado: () => aberto.value && enviando.value,
}
defineExpose(exposto)

// Desmontada (a página trancou ou saiu): quem abriu não fica esperando.
onBeforeUnmount(() => terminar(false))

// --- Formulário --------------------------------------------------------------------

const digitados = computed(() => listaDeEmails(texto.value))
const invalidos = computed(() => digitados.value.filter((x) => !EMAIL_VALIDO.test(x)))

watch(texto, () => {
  erroDoServidor.value = null
})

const erroPara = computed(() => {
  if (!tentou.value) return null
  if (!digitados.value.length) return 'Digite o e-mail de quem vai receber a nota.'
  if (invalidos.value.length) {
    return `Confira ${invalidos.value.length === 1 ? 'o e-mail' : 'os e-mails'} ${invalidos.value.join(', ')}: o certo é como financeiro@empresa.com.br.`
  }
  if (digitados.value.length > MAX_ENDERECOS) return `No máximo ${MAX_ENDERECOS} endereços por envio.`
  return erroDoServidor.value
})
const valido = computed(
  () => digitados.value.length > 0 && digitados.value.length <= MAX_ENDERECOS && !invalidos.value.length,
)

// Mesmo conjunto de endereços (sem olhar ordem nem maiúsculas).
function chave(lista: string[]): string {
  return lista.map((x) => x.toLowerCase()).sort().join(',')
}
const diferenteDoCadastro = computed(() => chave(digitados.value) !== chave(cadastrados.value))

// A caixa "guardar no cadastro" só aparece para nota com tomador cadastrado, para
// quem pode editar, e quando há o que guardar (tomador sem e-mail, ou digitado
// diferente do cadastrado).
const mostrarGuardar = computed(
  () => !!tomador.value && tela.canEdit.value && (semEmailNoCadastro.value || diferenteDoCadastro.value),
)

const titulo = computed(() =>
  nota.value?.n_nfse ? `Enviar a nota nº ${nota.value.n_nfse} por e-mail` : 'Enviar a nota por e-mail',
)
const descricao = computed(
  () =>
    `O DaVinci manda o PDF e o XML da nota${nota.value?.n_nfse ? ` nº ${nota.value.n_nfse}` : ''} para o endereço abaixo. Nada é enviado automaticamente na emissão.`,
)

// --- Envio -------------------------------------------------------------------------

async function confirmar() {
  const n = nota.value
  if (!n || enviando.value) return
  tentou.value = true
  if (!valido.value) {
    document.getElementById('nfse-email-para')?.focus()
    return
  }
  const salvar = mostrarGuardar.value && guardar.value
  enviando.value = true
  erroGeral.value = null
  erroDoServidor.value = null
  try {
    const r = await api<{ ok: boolean; para?: string[] }>(`/api/nfse/emissoes/${n.id}/enviar-email`, {
      method: 'POST',
      body: { para: digitados.value, salvar_no_tomador: salvar },
    })
    const para = r?.para?.length ? r.para : digitados.value
    toasts.success(
      `E-mail enviado para ${para.join(', ')}`,
      salvar ? 'O e-mail ficou guardado no cadastro do tomador.' : undefined,
    )
    // O cadastro do tomador mudou: a tela relê (o próximo envio já vem com ele).
    if (salvar) void tela.recarregar()
    terminar(true)
  } catch (err) {
    // Endereço que o servidor recusou (ex.: "x@empresa.local"): embaixo do campo.
    if (campoDoErro(err) === 'para') {
      erroDoServidor.value = erroApi(err)
      document.getElementById('nfse-email-para')?.focus()
      return
    }
    let msg = erroApi(err)
    // Parou no meio (email_falhou): o servidor diz para quem já foi. Esses saem
    // do "Para" — clicar de novo manda só para os que faltam, sem repetir.
    const enviados = (err as { data?: { detail?: { enviados?: unknown } } })?.data?.detail?.enviados
    const ja: unknown[] = codigoErro(err) === 'email_falhou' && Array.isArray(enviados) ? enviados : []
    const foram = new Set(ja.filter((x): x is string => typeof x === 'string').map((x) => x.toLowerCase()))
    if (foram.size) {
      texto.value = digitados.value.filter((x) => !foram.has(x.toLowerCase())).join(', ')
      msg += ' Quem já recebeu saiu do campo "Para": é só enviar de novo para os que faltam.'
      // Guardar agora gravaria só os que faltam: o cadastro fica como está.
      if (guardar.value) msg += ' O cadastro do tomador não mudou.'
      guardar.value = false
    }
    erroGeral.value = msg
  } finally {
    enviando.value = false
  }
}
</script>

<template>
  <NfseDialog
    :open="aberto"
    :titulo="titulo"
    :descricao="descricao"
    tamanho="md"
    :icone="Mail"
    :fechavel="!enviando"
    foco-inicial="#nfse-email-para"
    @update:open="aoMudar"
  >
    <form v-if="nota" id="nfse-email" class="space-y-4" @submit.prevent="confirmar">
      <!-- A nota -->
      <div class="rounded-md bg-muted/40 p-3 text-sm">
        <div class="font-medium">{{ nota.prestador_nome || 'Empresa' }} → {{ nota.tomador_nome || 'tomador' }}</div>
        <div class="text-xs tabular-nums text-muted-foreground">
          {{ fmtBrl(nota.valor_servico) }} · {{ fmtMes(nota.competencia) }}
          <template v-if="nota.teste"> · nota de teste</template>
        </div>
      </div>

      <NfseAviso v-if="nota.teste" tom="atencao" :icone="FlaskConical" compacto>
        Nota de teste: o e-mail sai marcado como TESTE, sem valor fiscal.
      </NfseAviso>

      <NfseAviso v-if="!tomador" tom="atencao" compacto>
        Esta nota não tem tomador cadastrado: digite para quem mandar.
      </NfseAviso>
      <NfseAviso v-else-if="semEmailNoCadastro" tom="atencao" compacto>
        O tomador não tem e-mail no cadastro: digite para quem mandar.
      </NfseAviso>

      <NfseCampo
        rotulo="Para"
        para="nfse-email-para"
        obrigatorio
        :erro="erroPara"
        :dica="`Até ${plural(MAX_ENDERECOS, 'endereço', 'endereços')}, separados por vírgula ou ponto e vírgula.`"
      >
        <input
          id="nfse-email-para"
          v-model="texto"
          type="text"
          inputmode="email"
          autocomplete="off"
          spellcheck="false"
          maxlength="1000"
          placeholder="financeiro@empresa.com.br"
          :disabled="enviando"
          class="h-9 w-full rounded-md border bg-background px-3 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :class="erroPara && 'border-red-500 dark:border-red-400'"
          :aria-invalid="erroPara ? 'true' : undefined"
        />
      </NfseCampo>

      <label v-if="mostrarGuardar" class="flex cursor-pointer items-start gap-2 text-sm">
        <input
          v-model="guardar"
          type="checkbox"
          class="mt-0.5 size-4 shrink-0 rounded accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
          :disabled="enviando"
        />
        <span>
          Guardar no cadastro do tomador
          <span v-if="!semEmailNoCadastro" class="block text-xs text-muted-foreground">
            troca o e-mail do cadastro ({{ cadastrados.join(', ') }}) por este
          </span>
        </span>
      </label>

      <NfseAviso v-if="erroGeral" tom="perigo" titulo="Não deu para enviar o e-mail">{{ erroGeral }}</NfseAviso>
    </form>

    <template #rodape>
      <Button type="button" variant="outline" size="sm" :disabled="enviando" @click="terminar(false)">Voltar</Button>
      <Button type="submit" form="nfse-email" size="sm" :disabled="enviando">
        <Loader2 v-if="enviando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <Mail v-else class="mr-1.5 size-4" aria-hidden="true" />
        {{ enviando ? 'Enviando…' : 'Enviar e-mail' }}
      </Button>
    </template>
  </NfseDialog>
</template>
