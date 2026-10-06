<script setup lang="ts">
// App Uranyx › Contas › "Não fui eu" (spec 4.7): alguém comprovou a compra
// de um CPF que já tem conta e diz que não foi ele quem criou. A equipe
// decide até o prazo:
//  - aceitar: a conta atual é EXCLUÍDA e o CPF fica livre para quem pediu;
//  - recusar: a conta atual continua (e sai do congelamento, se era o último pedido).
// Os dois lados recebem e-mail; a resposta diz se cada aviso saiu.
import { RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  AVISO_EMAIL_LABEL, camposRequerente, dataHora, erroAppUranyx, ESTADO_CONTA_LABEL, ESTADO_RECLAMACAO_LABEL,
  linhasDoErro, rotulo, type Reclamacao,
} from '~/lib/appUranyx'

const emit = defineEmits<{ (e: 'ver-conta', id: string): void }>()

const { chamar } = useAppUranyx()
const toasts = useToasts()

const estado = ref<'aberta' | 'aceita' | 'recusada' | 'todas'>('aberta')
const lista = ref<Reclamacao[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const notas = reactive<Record<string, string>>({})
const decidindo = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const r = await chamar<Reclamacao[]>('reclamacoes', { query: { estado: estado.value } })
    if (meu === seq) lista.value = r
  } catch (e: any) {
    if (meu === seq) erro.value = erroAppUranyx(e, 'Não deu para carregar os pedidos de "Não fui eu"').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
// Só no navegador, como as outras telas do módulo: no servidor do Nuxt o GET
// do repasse seria jogado fora e repetido aqui.
onMounted(carregar)
watch(estado, carregar)
defineExpose({ carregar })

// "Quem pediu" com os campos reais do requerente (lib: camposRequerente); a
// lista dos pedidos comprovados aparece item por item.
const requerentes = computed(() => {
  const out: Record<string, ReturnType<typeof camposRequerente>> = {}
  for (const r of lista.value) out[r.id] = camposRequerente(r.requerente)
  return out
})

function verConta(r: Reclamacao) {
  if (r.conta_atual) emit('ver-conta', r.conta_atual.id)
}

async function decidir(r: Reclamacao, decisao: 'aceita' | 'recusada') {
  const quem = r.conta_atual ? (r.conta_atual.nome || r.conta_atual.documento_mascarado) : 'a conta atual'
  const pergunta = decisao === 'aceita'
    ? `ACEITAR o pedido? A conta de ${quem} será EXCLUÍDA (dados apagados) e o CPF fica livre para quem pediu. Não dá para desfazer.`
    : `RECUSAR o pedido? A conta de ${quem} continua com quem a criou.`
  if (!window.confirm(pergunta)) return
  decidindo.value = r.id
  try {
    const nota = (notas[r.id] || '').trim()
    const resp = await chamar<Reclamacao>(`reclamacoes/${r.id}/decidir`, {
      method: 'POST',
      body: { decisao, nota: nota || null },
    })
    const avisos = Object.entries(resp.emails ?? {}).map(([lado, st]) =>
      `${lado === 'conta_atual' ? 'Conta atual' : 'Quem pediu'}: ${rotulo(AVISO_EMAIL_LABEL, st)}`)
    toasts.success(decisao === 'aceita' ? 'Pedido aceito' : 'Pedido recusado', avisos)
    if (estado.value === 'aberta') lista.value = lista.value.filter((x) => x.id !== r.id)
    else lista.value = lista.value.map((x) => (x.id === r.id ? resp : x))
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para registrar a decisão')
    toasts.error('Não deu para registrar a decisão', [er.texto, ...linhasDoErro(er)])
  } finally {
    decidindo.value = null
  }
}
</script>

<template>
  <section class="space-y-3">
    <div class="flex flex-wrap items-center gap-2">
      <ShieldAlert class="size-4 text-muted-foreground" aria-hidden="true" />
      <h2 class="text-sm font-semibold">"Não fui eu"</h2>
      <span class="text-xs text-muted-foreground">quem comprovou a compra de um CPF que já tem conta</span>
      <select v-model="estado" class="ml-auto h-9 rounded-md border bg-background px-2 text-sm" aria-label="Situação dos pedidos">
        <option value="aberta">Abertos</option>
        <option value="aceita">Aceitos</option>
        <option value="recusada">Recusados</option>
        <option value="todas">Todos</option>
      </select>
      <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
        <RefreshCw class="size-4" :class="{ 'animate-spin': carregando }" />
      </Button>
    </div>

    <p v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</p>
    <p v-else-if="carregando && !lista.length" class="text-sm text-muted-foreground">Carregando…</p>
    <p v-else-if="!lista.length" class="rounded-md border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
      {{ estado === 'aberta' ? 'Nenhum pedido aberto.' : 'Nada por aqui.' }}
    </p>

    <article v-for="r in lista" :key="r.id" class="space-y-3 rounded-xl border bg-card p-4 text-sm">
      <div class="flex flex-wrap items-center gap-2">
        <span :class="r.estado === 'aberta' ? 'pill-warning' : r.estado === 'aceita' ? 'pill-danger' : 'pill-muted'">
          {{ rotulo(ESTADO_RECLAMACAO_LABEL, r.estado) }}
        </span>
        <span class="text-muted-foreground">pedido em {{ dataHora(r.criado_em) }}</span>
        <span v-if="r.estado === 'aberta'" :class="r.prazo_vencido ? 'pill-danger' : 'pill-muted'">
          prazo {{ dataHora(r.prazo) }}{{ r.prazo_vencido ? ' (vencido)' : '' }}
        </span>
        <span v-else class="text-muted-foreground">decidido em {{ dataHora(r.decidido_em) }}</span>
      </div>

      <div class="grid gap-3 md:grid-cols-2">
        <div class="space-y-1 rounded-lg border p-3">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Conta atual</h3>
          <template v-if="r.conta_atual">
            <div class="font-medium">{{ r.conta_atual.nome || 'Sem nome' }} · {{ r.conta_atual.documento_mascarado }}</div>
            <div class="text-muted-foreground">{{ r.conta_atual.email || 'sem e-mail' }} · {{ r.conta_atual.celular || 'sem celular' }}</div>
            <div class="text-muted-foreground">
              {{ rotulo(ESTADO_CONTA_LABEL, r.conta_atual.estado) }} · criada em {{ dataHora(r.conta_atual.criado_em) }}
            </div>
            <button class="text-xs text-primary underline-offset-2 hover:underline" @click="verConta(r)">ver conta</button>
          </template>
          <p v-else class="text-muted-foreground">A conta não existe mais.</p>
        </div>
        <div class="space-y-1 rounded-lg border p-3">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Quem pediu</h3>
          <p v-if="r.requerente?.erro === 'nao_decifrado'" class="text-amber-700 dark:text-amber-400">Não deu para ler os dados de quem pediu.</p>
          <dl v-else-if="requerentes[r.id]?.length" class="space-y-0.5">
            <div v-for="c in requerentes[r.id]" :key="c.chave" class="flex gap-2">
              <dt class="shrink-0 text-muted-foreground">{{ c.label }}:</dt>
              <dd v-if="Array.isArray(c.valor)" class="min-w-0">
                <span v-if="!c.valor.length" class="text-amber-700 dark:text-amber-400">nenhum</span>
                <span v-else class="flex flex-wrap gap-1">
                  <code v-for="(v, i) in c.valor" :key="i" class="pill-muted break-all font-mono">{{ v }}</code>
                </span>
              </dd>
              <dd v-else class="min-w-0 break-all" :class="{ 'text-amber-700 dark:text-amber-400': c.alerta }">{{ c.valor }}</dd>
            </div>
          </dl>
          <p v-else class="text-amber-700 dark:text-amber-400">Sem dados de quem pediu.</p>
        </div>
      </div>

      <p v-if="r.decisao_nota" class="rounded-md bg-muted/40 px-3 py-2 text-muted-foreground">Nota: {{ r.decisao_nota }}</p>

      <div v-if="r.estado === 'aberta'" class="flex flex-wrap items-end gap-2">
        <label class="min-w-[16rem] flex-1 space-y-1 text-xs">
          <span class="text-muted-foreground">Nota da decisão (fica registrada; opcional)</span>
          <input v-model="notas[r.id]" maxlength="1000" class="h-9 w-full rounded-md border bg-background px-2 text-sm" placeholder="ex.: conferido o pedido no Bling" />
        </label>
        <Button size="sm" variant="outline" :disabled="decidindo === r.id" @click="decidir(r, 'recusada')">Recusar (conta continua)</Button>
        <Button size="sm" variant="destructive" :disabled="decidindo === r.id" @click="decidir(r, 'aceita')">Aceitar (exclui a conta atual)</Button>
      </div>
    </article>
  </section>
</template>
