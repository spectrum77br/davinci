<script setup lang="ts">
// O diálogo "Lançar reembolso ao cliente" (Novos procedimentos 3/6, 17/11/2026).
// Abre pelo botão do bloco AtendimentoReembolsoCliente e mora no painel do pedido,
// fora dos ramos com e sem retrato, como o da troca: o retrato que chega no meio
// não fecha o que a pessoa está digitando.
//  - Campos do dono: valor (obrigatório, > 0, nunca acima do valor pago), motivo
//    (a lista) + detalhe (obrigatório no "Outro"), forma (Pix | Cupom) e os dados
//    do Pix (obrigatórios no Pix). Pedido, loja, plataforma, data e quem lançou o
//    servidor tira da conversa. Do pedido, o corpo só leva o nº do Bling que o
//    diálogo mostrou, para o servidor conferir: se a conversa estiver em outro, a
//    resposta é 409 `pedido_mudou`, o bloco relê e o diálogo não lança mais.
//  - Ao abrir, relê o GET da conversa, porque o bloco só relê a cada 2 min. Assim
//    o aviso dos anteriores sai atualizado. O "Lançar" espera essa leitura.
//  - Avisa antes, sem bloquear: vai precisar do ADM (> R$ 100, > 25% do pago ou o
//    valor inteiro), o pedido já tem outro reembolso, a plataforma já devolveu.
//  - Os dados do Pix ficam num campo protegido: sem autocompletar, sem corretor,
//    limpos ao fechar, e nunca vão para toast, evento ou log.
//  - Sem resposta (a rede caiu no meio), o reembolso pode ter entrado. O "Lançar"
//    fica desligado até fechar, e ao fechar o bloco relê a lista.
//  - Fecha pelo X, pelo Cancelar, pelo Esc (a caixa tem tabindex, então o Esc vale
//    mesmo depois de um clique num texto dela) e pelo fundo escuro. O fundo só
//    fecha com o clique inteiro nele, e pergunta antes se algo foi preenchido.
import { HandCoins, Loader2, Lock, TriangleAlert, X } from 'lucide-vue-next'
import { erroDaApi, statusDoErro } from '~/components/AtendimentoPlataforma.vue'
import {
  DETALHE_MAX,
  ERROS_REEMBOLSO,
  FORMAS,
  LIMITE_PADRAO,
  MOTIVOS,
  PIX_MAX,
  RELE_NA_RECUSA,
  avisoDeAnteriores,
  corpoDoReembolso,
  erroDoReembolso,
  formPreenchido,
  lerValor,
  mesmoPedido,
  moeda,
  porquesDoAdm,
  problemaDoForm,
  respostaDe,
  semPix,
  urlReembolsos,
  valorNoCampo,
  type ErroReembolso,
  type FormReembolso,
  type FormaReembolso,
  type ItemReembolso,
  type MotivoReembolso,
  type ReembolsosResposta,
} from '~/components/AtendimentoReembolsoCliente.vue'

const props = defineProps<{
  conversaId: string
  // O que o bloco leu: o pedido, o valor pago, o limite e os que já há. O diálogo
  // começa com isto e relê ao abrir.
  dados: ReembolsosResposta
}>()
const aberto = defineModel<boolean>('aberto', { required: true })
const emit = defineEmits<{
  // Lançou: o painel relê o bloco.
  (e: 'lancado'): void
  // O bloco relê quando a recusa mudou o que vale (sem permissão, a plataforma já
  // reembolsou, o pedido mudou), quando a releitura achou outro pedido e quando o
  // diálogo fecha sem saber se o reembolso entrou.
  (e: 'recarregar'): void
}>()
const { api } = useApi()
const toasts = useToasts()

const valorTexto = ref('')
const motivo = ref<MotivoReembolso | ''>('')
const detalhe = ref('')
const forma = ref<FormaReembolso | ''>('')
const dadosPix = ref('')
const enviando = ref(false)
const erro = ref<ErroReembolso | null>(null)
// O que o diálogo mostra: o que o bloco leu, até a releitura chegar (17/11/2026).
const atual = ref<ReembolsosResposta>(props.dados)
const relendo = ref(false)
const falhaAoReler = ref(false)
// O "Lançar" desligado até fechar (17/11/2026): a resposta não chegou ('incerto')
// ou a conversa já está em outro pedido ('pedido_mudou').
const travado = ref<'incerto' | 'pedido_mudou' | null>(null)

const pedido = computed(() => atual.value.pedido)
const valorPago = computed(() => (typeof pedido.value?.valor_pago === 'number' ? pedido.value.valor_pago : null))
const limite = computed(() => atual.value.limite ?? LIMITE_PADRAO)
const lido = computed(() => lerValor(valorTexto.value))
const valor = computed(() => (lido.value && !('erro' in lido.value) ? lido.value.valor : null))
const porquesAdm = computed(() => (valor.value === null ? [] : porquesDoAdm(valor.value, valorPago.value, limite.value)))
const anteriores = computed(() => avisoDeAnteriores(atual.value.itens))
const form = computed<FormReembolso>(() => ({
  valorTexto: valorTexto.value,
  motivo: motivo.value,
  detalhe: detalhe.value,
  forma: forma.value,
  dadosPix: dadosPix.value,
}))
const problema = computed(() => problemaDoForm(form.value, valorPago.value))
// Por que o "Lançar" está desligado (null = pode lançar), por ordem: as travas, a
// releitura, quem não lança (o que a releitura disse) e o formulário (17/11/2026).
const bloqueio = computed<string | null>(() => {
  if (travado.value === 'incerto') return 'Feche e confira a lista do pedido: o reembolso pode ter entrado.'
  if (travado.value === 'pedido_mudou') return 'A conversa está em outro pedido: feche e abra o lançamento de novo.'
  if (relendo.value) return 'Conferindo os reembolsos que o pedido já tem…'
  if (!atual.value.pode_lancar) return atual.value.motivo_bloqueio || ERROS_REEMBOLSO.sem_permissao
  return problema.value
})
// "Pedido 2510… · Bling 123456 · Shopee ATV": de qual pedido é (o da conversa).
const qualPedido = computed(() => {
  const p = pedido.value
  if (!p) return ''
  return [
    p.marketplace ? `Pedido ${p.marketplace}` : '',
    p.bling ? `Bling ${p.bling}` : '',
    p.conta || '',
  ].filter(Boolean).join(' · ')
})

// Ao sair do campo, o valor fica escrito do jeito certo ("1.234,50").
function formatarValor() {
  if (valor.value !== null) valorTexto.value = valorNoCampo(valor.value)
}

// A releitura ao abrir (17/11/2026). Se falhar, fica o que o bloco leu: o servidor
// confere tudo de novo no POST. O Pix dos anteriores nem entra no diálogo.
let geracao = 0
async function reler() {
  const g = ++geracao
  relendo.value = true
  falhaAoReler.value = false
  try {
    const r = semPix(respostaDe(await api<ReembolsosResposta>(urlReembolsos(props.conversaId))))
    if (g !== geracao) return
    // A conversa já estava em outro pedido: o diálogo mostra o de agora e o bloco relê.
    if (!mesmoPedido(r.pedido, atual.value.pedido)) emit('recarregar')
    atual.value = r
  } catch {
    if (g === geracao) falhaAoReler.value = true
  } finally {
    if (g === geracao) relendo.value = false
  }
}

async function lancar() {
  // O nº do Bling que o diálogo mostrou vai junto: o servidor confere com o da conversa.
  const corpo = corpoDoReembolso(form.value, pedido.value?.bling ?? null)
  if (bloqueio.value || !corpo || enviando.value) return
  enviando.value = true
  erro.value = null
  try {
    const r = await api<{ item?: ItemReembolso }>(urlReembolsos(props.conversaId), { method: 'POST', body: corpo })
    const aguarda = r?.item?.situacao === 'aguardando_adm'
    toasts.success(
      'Reembolso ao cliente lançado',
      aguarda ? 'Aguardando a aprovação de um ADM antes de pagar.' : 'Em aberto: quem paga marca Finalizado na tela Reembolso.',
    )
    dadosPix.value = ''
    aberto.value = false
    emit('lancado')
  } catch (e: any) {
    const st = statusDoErro(e)
    if (!st || st >= 500) {
      // A resposta não chegou: o reembolso pode ter entrado. O "Lançar" fica
      // desligado até fechar, e ao fechar o bloco relê a lista (17/11/2026).
      travado.value = 'incerto'
      erro.value = {
        code: null,
        texto: 'Não sei se o reembolso entrou: a resposta do servidor não chegou.',
        motivos: ['Feche e confira a lista do pedido antes de lançar de novo.'],
      }
      return
    }
    const er = erroDoReembolso(e, erroDaApi(e, 'Não consegui lançar o reembolso agora'))
    erro.value = er
    // A conversa está em outro pedido: este diálogo não lança mais (17/11/2026).
    if (er.code === 'pedido_mudou') travado.value = 'pedido_mudou'
    if (er.code && RELE_NA_RECUSA.has(er.code)) emit('recarregar')
  } finally {
    enviando.value = false
  }
}

// Aberto: tudo em branco, relê e põe o cursor no valor.
const campoValor = ref<HTMLInputElement | null>(null)
watch(aberto, (v) => {
  if (!v) {
    dadosPix.value = ''
    return
  }
  valorTexto.value = ''
  motivo.value = ''
  detalhe.value = ''
  forma.value = ''
  dadosPix.value = ''
  erro.value = null
  travado.value = null
  atual.value = props.dados
  void reler()
  void nextTick(() => campoValor.value?.focus())
}, { immediate: true })

function fechar() {
  // No meio do lançamento não fecha: a resposta é o que diz se entrou.
  if (enviando.value) return
  geracao += 1 // a releitura que chegar depois não vale mais
  dadosPix.value = ''
  // Fecha sem saber se entrou: o bloco relê a lista (17/11/2026).
  if (travado.value === 'incerto') emit('recarregar')
  aberto.value = false
}

// O fundo escuro (17/11/2026): só o clique que começa e termina nele fecha. O
// arrasto que sai de um campo e solta fora da caixa não fecha. Se algo foi
// preenchido, pergunta antes de descartar (o Pix digitado).
let apertouNoFundo = false
function apertarFundo(e: MouseEvent) {
  apertouNoFundo = e.target === e.currentTarget
  // O foco fica na caixa, para o Esc seguir fechando se a pessoa desistir.
  if (apertouNoFundo) e.preventDefault()
}
function clicarFundo(e: MouseEvent) {
  const noFundo = apertouNoFundo && e.target === e.currentTarget
  apertouNoFundo = false
  if (!noFundo || enviando.value) return
  // Com o "Lançar" travado, o que foi digitado não serve mais: fecha sem perguntar.
  if (!travado.value && formPreenchido(form.value) && !confirm('Fechar sem lançar? O que você preencheu neste reembolso se perde.')) return
  fechar()
}
</script>

<template>
  <div
    v-if="aberto"
    class="fixed inset-0 z-[80] flex items-stretch justify-center bg-black/50 sm:items-center sm:p-4"
    data-dialogo-reembolso
    @mousedown="apertarFundo"
    @click="clicarFundo"
    @keydown.esc.stop="fechar"
  >
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Lançar reembolso ao cliente"
      tabindex="-1"
      class="flex max-h-full w-full flex-col bg-background text-xs shadow-2xl outline-none sm:max-h-[88vh] sm:max-w-md sm:rounded-lg sm:border"
    >
      <header class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
        <HandCoins class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <h2 class="text-sm font-semibold">Lançar reembolso ao cliente</h2>
          <p v-if="qualPedido" class="break-words text-[11px] text-muted-foreground" data-reembolso-pedido>{{ qualPedido }}</p>
        </div>
        <button type="button" class="rounded p-1 hover:bg-muted disabled:opacity-50" aria-label="fechar" :disabled="enviando" @click="fechar">
          <X class="size-4" />
        </button>
      </header>

      <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <!-- A releitura ao abrir falhou: os avisos são do que o bloco tinha lido. -->
        <p v-if="falhaAoReler" class="text-[11px] text-amber-800 dark:text-amber-300" data-aviso-releitura>
          Não consegui reler os reembolsos do pedido agora: os avisos abaixo são do que o bloco tinha lido.
        </p>
        <!-- O pedido já tem reembolso nosso (qualquer tipo): avisa, não bloqueia. -->
        <div
          v-if="anteriores"
          class="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-900 dark:text-amber-200"
          role="alert"
          data-aviso-anteriores
        >
          <p class="font-medium">{{ anteriores.titulo }}</p>
          <ul class="mt-0.5 space-y-0.5">
            <li v-for="(l, k) in anteriores.linhas" :key="k" class="tabular-nums">{{ l }}</li>
          </ul>
          <p class="mt-1 text-[11px]">Confira antes de lançar outro: o pedido pode ter mais de um reembolso, mas não o mesmo duas vezes.</p>
        </div>
        <p
          v-if="atual.plataforma_reembolsou"
          class="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-900 dark:text-amber-200"
          data-aviso-plataforma
        >
          A plataforma já devolveu dinheiro deste pedido ao comprador<template v-if="atual.plataforma_detalhe"> ({{ atual.plataforma_detalhe }})</template>: confira para não pagar duas vezes.
        </p>

        <!-- valor -->
        <div class="space-y-1">
          <label for="reembolso-valor" class="font-medium">Valor <span class="font-normal text-muted-foreground">(obrigatório)</span></label>
          <div class="flex flex-wrap items-center gap-2">
            <div class="relative">
              <span class="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true">R$</span>
              <input
                id="reembolso-valor"
                ref="campoValor"
                v-model="valorTexto"
                type="text"
                inputmode="decimal"
                autocomplete="off"
                placeholder="0,00"
                class="h-8 w-32 rounded-md border bg-background pl-8 pr-2 text-right tabular-nums"
                data-campo-valor
                @blur="formatarValor"
              >
            </div>
            <span v-if="valorPago !== null" class="text-muted-foreground" data-valor-pago>de {{ moeda(valorPago) }} pagos</span>
            <span v-else class="text-muted-foreground">o valor pago não veio: o servidor confere o teto</span>
          </div>
        </div>
        <!-- Vai precisar do ADM (o ADM que lança já sai aprovado). -->
        <div
          v-if="porquesAdm.length"
          class="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-amber-900 dark:text-amber-200"
          data-aviso-adm
        >
          <TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
          <p v-if="atual.pode_aprovar">Passa do limite ({{ porquesAdm.join('; ') }}), mas você é ADM: o reembolso já sai aprovado no seu nome.</p>
          <p v-else>Vai precisar da aprovação de um ADM ({{ porquesAdm.join('; ') }}). Até lá fica "Aguardando ADM" e não pode ser pago.</p>
        </div>

        <!-- motivo e detalhe -->
        <div class="space-y-1">
          <label for="reembolso-motivo" class="font-medium">Motivo</label>
          <select id="reembolso-motivo" v-model="motivo" class="h-8 w-full rounded-md border bg-background px-2" data-campo-motivo>
            <option value="" disabled>escolha o motivo…</option>
            <option v-for="m in MOTIVOS" :key="m.v" :value="m.v">{{ m.l }}</option>
          </select>
          <textarea
            v-model="detalhe"
            rows="2"
            :maxlength="DETALHE_MAX"
            class="block w-full resize-y rounded-md border bg-background px-2 py-1"
            :placeholder="motivo === 'outro' ? 'Escreva o motivo (obrigatório no Outro)' : 'Detalhe (opcional)'"
            aria-label="detalhe do motivo"
            data-campo-detalhe
          />
        </div>

        <!-- forma -->
        <fieldset class="space-y-1">
          <legend class="font-medium">Forma</legend>
          <div class="flex items-center gap-4">
            <label v-for="f in FORMAS" :key="f.v" class="inline-flex items-center gap-1.5">
              <input v-model="forma" type="radio" name="reembolso-forma" :value="f.v" :data-forma="f.v">
              {{ f.l }}
            </label>
          </div>
        </fieldset>

        <!-- Os dados do Pix: campo protegido, só o ADM vê depois. -->
        <div v-if="forma === 'pix'" class="space-y-1 rounded-md border px-3 py-2" data-campo-pix>
          <label for="reembolso-pix" class="flex items-center gap-1 font-medium">
            <Lock class="size-3.5" aria-hidden="true" /> Dados do Pix
          </label>
          <textarea
            id="reembolso-pix"
            v-model="dadosPix"
            rows="2"
            :maxlength="PIX_MAX"
            autocomplete="off"
            spellcheck="false"
            data-lpignore="true"
            data-1p-ignore="true"
            class="block w-full resize-y rounded-md border bg-background px-2 py-1"
            placeholder="Chave Pix, banco e nome de quem recebe"
          />
          <p class="text-[11px] text-muted-foreground" data-aviso-pix-protegido>Campo protegido: só o ADM vê estes dados (na conversa e na tela Reembolso). Eles não saem no Excel.</p>
        </div>

        <div
          v-if="erro"
          class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300"
          role="alert"
          data-erro-reembolso
          :data-code="erro.code"
        >
          <p>{{ erro.texto }}</p>
          <p v-for="(m, k) in erro.motivos" :key="k">{{ m }}</p>
        </div>
      </div>

      <footer class="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t px-4 py-3">
        <p v-if="bloqueio" class="mr-auto min-w-0 flex-1 text-[11px] text-muted-foreground" data-motivo-sem-lancar>{{ bloqueio }}</p>
        <button type="button" class="rounded-md border px-2.5 py-1.5 hover:bg-muted disabled:opacity-60" :disabled="enviando" @click="fechar">Cancelar</button>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!!bloqueio || enviando"
          :title="bloqueio || ''"
          :data-trava="travado"
          data-botao-lancar
          @click="lancar"
        >
          <Loader2 v-if="enviando || relendo" class="size-3.5 animate-spin" />
          <HandCoins v-else class="size-3.5" aria-hidden="true" /> Lançar reembolso
        </button>
      </footer>
    </div>
  </div>
</template>
