<script setup lang="ts">
// Controle de Estoque › "Transferir estoque" (Eduardo, 05/11/2026) — só admin.
// Troca a loja interna "Centro de Distribuição" do Bling: o DaVinci lança direto
// no estoque do Bling, primeiro a SAÍDA na origem e depois a ENTRADA no destino
// (services/estoque_transferencia.py). Fluxo da tela: preencher as linhas →
// "Conferir" (saldo disponível ao vivo, sem lançar) → "Transferir". A chave
// `idemKey` nasce ao abrir e só troca depois de uma transferência feita: clique
// duplo ou reenvio do navegador não lançam duas vezes.
import { AlertTriangle, ArrowRight, CheckCircle2, Loader2, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

type LinhaForm = { origem: string; destino: string; quantidade: number | null }
type Conferida = {
  ordem: number
  origem: string
  destino: string
  quantidade: number
  origem_nome: string | null
  destino_nome: string | null
  saldo_fisico: number | null
  saldo_virtual: number | null
  troca_produto: boolean
  custo: number | null
  erros: string[]
}
type LinhaOut = {
  id: string
  ordem: number
  origem: string
  destino: string
  quantidade: number
  custo: number | null
  estado: 'ok' | 'andamento' | 'falhou' | 'atencao'
  texto: string
  pode_resolver: boolean
  precisa_conferir: boolean
  pode_ja_lancou: boolean
  tentativa: string | null
}
type TransferenciaOut = {
  id: string
  numero: number
  criado_em: string
  criado_por: string | null
  observacao: string | null
  estado: 'ok' | 'andamento' | 'falhou' | 'atencao'
  linhas: LinhaOut[]
}
type Produto = { sku: string; nome: string; virtual: number; fisico: number }

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'transferido'): void }>()

const { api } = useApi()

const linhas = ref<LinhaForm[]>([])
const observacao = ref('')
const idemKey = ref('')
const conferidas = ref<Conferida[] | null>(null)
// O pedido EXATO que foi conferido: o Transferir manda este, nunca o que está
// digitado (mexer numa linha durante o "Conferir" não passa sem conferir).
const conferidoJson = ref<string | null>(null)
// Troca a cada abertura: resposta atrasada de uma abertura anterior não cai
// no formulário novo.
let sessao = 0
const conferindo = ref(false)
const enviando = ref(false)
const erro = ref<string | null>(null)
const feita = ref<TransferenciaOut | null>(null)
const historico = ref<TransferenciaOut[]>([])
const carregandoHistorico = ref(false)
const resolvendo = ref<string | null>(null)
const sugestoes = ref<Produto[]>([])

function novaChave() {
  const c = globalThis.crypto as Crypto | undefined
  return c?.randomUUID ? c.randomUUID() : `t-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function limpar() {
  sessao += 1
  linhas.value = [{ origem: '', destino: '', quantidade: null }]
  observacao.value = ''
  conferidas.value = null
  conferidoJson.value = null
  erro.value = null
  feita.value = null
  idemKey.value = novaChave()
}

async function carregarHistorico() {
  carregandoHistorico.value = true
  try {
    historico.value = await api<TransferenciaOut[]>('/api/estoque/transferencias?limite=20')
    // O painel da transferência feita acompanha a lista (linha que estava
    // "entrando…" vira "Transferido" sem fechar a janela).
    const atual = feita.value && historico.value.find((h) => h.id === feita.value?.id)
    if (atual) feita.value = atual
  } catch (e: any) {
    erro.value = apiError(e)
  } finally {
    carregandoHistorico.value = false
  }
}

// Enquanto houver linha em andamento (entrada sendo lançada, worker tentando
// de novo), a lista se atualiza sozinha a cada 10 s.
let poll: ReturnType<typeof setInterval> | null = null
function pararPoll() {
  if (poll) clearInterval(poll)
  poll = null
}
const emAndamento = computed(() =>
  [feita.value, ...historico.value].some((t) => t?.linhas.some((l) => l.estado === 'andamento')),
)
watch([() => props.open, emAndamento], ([open, andando]) => {
  pararPoll()
  if (open && andando) poll = setInterval(() => { void carregarHistorico() }, 10_000)
})
onBeforeUnmount(pararPoll)

watch(() => props.open, (open) => {
  if (!open) return
  limpar()
  void carregarHistorico()
})

// Qualquer mudança nas linhas invalida a conferência (o Transferir volta a
// pedir "Conferir").
watch(linhas, () => {
  conferidas.value = null
  conferidoJson.value = null
}, { deep: true })

let buscaTimer: ReturnType<typeof setTimeout> | null = null
function buscar(q: string) {
  if (buscaTimer) clearTimeout(buscaTimer)
  const termo = (q || '').trim()
  if (termo.length < 2) return
  buscaTimer = setTimeout(async () => {
    try {
      sugestoes.value = await api<Produto[]>(`/api/estoque/transferencias/produtos?q=${encodeURIComponent(termo)}`)
    } catch {
      sugestoes.value = []
    }
  }, 250)
}

function adicionarLinha() {
  const ultima = linhas.value[linhas.value.length - 1]
  linhas.value.push({ origem: ultima?.origem || '', destino: '', quantidade: null })
}
function removerLinha(i: number) {
  linhas.value.splice(i, 1)
  if (!linhas.value.length) linhas.value.push({ origem: '', destino: '', quantidade: null })
}

const payloadLinhas = computed(() => linhas.value
  .filter((l) => l.origem.trim() || l.destino.trim() || l.quantidade)
  .map((l) => ({
    origem: l.origem.trim().toLowerCase(),
    destino: l.destino.trim().toLowerCase(),
    quantidade: Number(l.quantidade || 0),
  })))

const temErros = computed(() => !!conferidas.value?.some((c) => c.erros.length))
const podeTransferir = computed(() =>
  !!conferidas.value && !temErros.value && !enviando.value && !feita.value
  && conferidoJson.value === JSON.stringify(payloadLinhas.value),
)
// Por que o Transferir está desligado (no celular a coluna Conferência fica
// fora da tela).
const motivoDesligado = computed(() => {
  if (feita.value || enviando.value) return ''
  if (temErros.value) return 'Corrija as linhas em vermelho e confira de novo.'
  if (!conferidas.value) return 'Clique em "Conferir" antes de transferir.'
  return ''
})

async function conferir() {
  erro.value = null
  // Linha em branco no meio sai antes: a conferência casa com a linha pela
  // posição.
  const cheias = linhas.value.filter((l) => l.origem.trim() || l.destino.trim() || l.quantidade)
  if (!cheias.length) {
    erro.value = 'Preencha ao menos uma linha.'
    return
  }
  if (cheias.length !== linhas.value.length) linhas.value = cheias
  if (cheias.some((l) => !Number.isInteger(Number(l.quantidade)) || Number(l.quantidade) < 1)) {
    erro.value = 'Quantidade precisa ser um número inteiro maior que zero.'
    return
  }
  const enviado = JSON.stringify(payloadLinhas.value)
  conferindo.value = true
  try {
    const r = await api<{ linhas: Conferida[] }>('/api/estoque/transferencias/conferir', {
      method: 'POST',
      body: { linhas: JSON.parse(enviado) },
    })
    if (enviado !== JSON.stringify(payloadLinhas.value)) return // mexeram durante o Conferir
    conferidas.value = r.linhas
    conferidoJson.value = enviado
  } catch (e: any) {
    erro.value = apiError(e)
  } finally {
    conferindo.value = false
  }
}

async function transferir() {
  if (!podeTransferir.value || !conferidas.value || !conferidoJson.value) return
  const resumo = conferidas.value
    .map((c) => `• ${c.quantidade} × ${c.origem} → ${c.destino}${c.troca_produto ? ' (troca de produto)' : ''}`)
    .join('\n')
  if (!confirm(`Transferir no estoque do Bling?\n\n${resumo}\n\nSai da origem e entra no destino agora.`)) return
  enviando.value = true
  erro.value = null
  const minha = sessao
  try {
    const t = await api<TransferenciaOut>('/api/estoque/transferencias', {
      method: 'POST',
      body: { linhas: JSON.parse(conferidoJson.value), observacao: observacao.value.trim() || null },
      headers: { 'Idempotency-Key': idemKey.value },
    })
    emit('transferido')
    if (minha !== sessao) return // a janela foi fechada e aberta de novo
    feita.value = t
    void carregarHistorico()
  } catch (e: any) {
    if (minha !== sessao) return
    const det = e?.data?.detail
    if (det?.code === 'transferencia_invalida' && Array.isArray(det.linhas)) {
      // Saldo mudou entre o Conferir e o Transferir: mostra o motivo na linha.
      conferidas.value = det.linhas
    } else {
      erro.value = apiError(e)
    }
  } finally {
    enviando.value = false
  }
}

async function resolver(linha: LinhaOut, acao: 'ja_lancou' | 'tentar_de_novo') {
  const msg = acao === 'ja_lancou'
    ? (linha.precisa_conferir
        ? 'Você conferiu no extrato do Bling e o movimento ESTÁ lá? A linha fica como lançada, sem lançar de novo.'
        : 'Você mesmo lançou essa entrada à mão no Bling? A linha fica como lançada, sem lançar de novo.')
    : linha.precisa_conferir
      ? 'Você conferiu no extrato do Bling e o movimento NÃO está lá? O DaVinci lança de novo.'
      : 'Lançar de novo no Bling o que ficou parado nesta linha? (a saída confere o saldo de novo)'
  if (!confirm(msg)) return
  resolvendo.value = linha.id
  erro.value = null
  try {
    const t = await api<TransferenciaOut>(`/api/estoque/transferencias/linhas/${linha.id}/resolver`, {
      method: 'POST',
      body: {
        acao,
        conferi_extrato: linha.precisa_conferir || acao === 'ja_lancou',
        tentativa: linha.tentativa,
      },
    })
    historico.value = historico.value.map((h) => (h.id === t.id ? t : h))
    if (feita.value?.id === t.id) feita.value = t
    emit('transferido')
  } catch (e: any) {
    erro.value = apiError(e)
  } finally {
    resolvendo.value = null
  }
}

function novaTransferencia() {
  limpar()
}

const ESTADO_COR: Record<string, string> = {
  ok: 'text-emerald-700 dark:text-emerald-400',
  andamento: 'text-amber-700 dark:text-amber-400',
  falhou: 'text-red-600 dark:text-red-400',
  atencao: 'text-red-600 dark:text-red-400',
}

function quando(iso: string) {
  try {
    return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return iso
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 bg-black/60 flex items-start justify-center z-50 p-4 overflow-y-auto"
    @click.self="emit('close')"
  >
    <div class="bg-background border rounded-lg w-full max-w-4xl p-5 space-y-4 my-6" data-transferir-modal>
      <div class="flex items-start gap-3">
        <div>
          <h2 class="text-lg font-semibold">Transferir estoque</h2>
          <p class="text-sm text-muted-foreground">
            Passa estoque de um SKU para outro direto no Bling, sem pedido: sai da origem e entra no destino.
            Serve para lote (dg010.ra → dg010.ci) e para troca de produto (uaf001m1.220 → uaf001m1.110).
            Substitui a loja "Centro de Distribuição".
          </p>
        </div>
        <Button class="ml-auto shrink-0" size="sm" variant="ghost" aria-label="Fechar" @click="emit('close')">
          <X class="size-4" />
        </Button>
      </div>

      <!-- Resultado da transferência feita -->
      <div v-if="feita" class="rounded-md border p-3 space-y-2" data-transferencia-feita>
        <div class="text-sm font-medium">Transferência #{{ feita.numero }}</div>
        <ul class="space-y-1 text-sm">
          <li v-for="l in feita.linhas" :key="l.id" class="flex items-start gap-2">
            <CheckCircle2 v-if="l.estado === 'ok'" class="size-4 mt-0.5 text-emerald-600 shrink-0" />
            <Loader2 v-else-if="l.estado === 'andamento'" class="size-4 mt-0.5 text-amber-600 shrink-0" />
            <AlertTriangle v-else class="size-4 mt-0.5 text-red-600 shrink-0" />
            <span :class="ESTADO_COR[l.estado]">{{ l.texto }}</span>
          </li>
        </ul>
        <div class="flex justify-end">
          <Button size="sm" variant="outline" @click="novaTransferencia">Nova transferência</Button>
        </div>
      </div>

      <!-- Formulário -->
      <template v-else>
        <datalist id="transferir-skus">
          <option v-for="p in sugestoes" :key="p.sku" :value="p.sku">{{ p.nome }} · disp. {{ p.virtual }}</option>
        </datalist>
        <div class="overflow-x-auto">
          <table class="w-full text-sm border-collapse">
            <thead>
              <tr class="text-left text-xs text-muted-foreground">
                <th class="py-1 pr-2 font-medium">Sai de (origem)</th>
                <th />
                <th class="py-1 pr-2 font-medium">Entra em (destino)</th>
                <th class="py-1 pr-2 font-medium w-28">Quantidade</th>
                <th class="py-1 font-medium">Conferência</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="(l, i) in linhas" :key="i" class="align-top border-t" data-linha-transferir>
                <td class="py-1.5 pr-2">
                  <input
                    v-model="l.origem"
                    list="transferir-skus"
                    placeholder="ex.: dg010.ra"
                    class="h-8 w-40 rounded-md border bg-background px-2 font-mono text-xs"
                    @input="buscar(l.origem)"
                  />
                </td>
                <td class="py-1.5 pr-2 text-muted-foreground"><ArrowRight class="size-4 mt-2" /></td>
                <td class="py-1.5 pr-2">
                  <input
                    v-model="l.destino"
                    list="transferir-skus"
                    placeholder="ex.: dg010.ci"
                    class="h-8 w-40 rounded-md border bg-background px-2 font-mono text-xs"
                    @input="buscar(l.destino)"
                  />
                </td>
                <td class="py-1.5 pr-2">
                  <input
                    v-model.number="l.quantidade"
                    type="number"
                    min="1"
                    step="1"
                    class="h-8 w-24 rounded-md border bg-background px-2 text-right tabular-nums"
                  />
                </td>
                <td class="py-1.5 text-xs min-w-[220px]">
                  <template v-if="conferidas && conferidas[i]">
                    <div v-if="conferidas[i].erros.length" class="space-y-0.5 text-red-600 dark:text-red-400">
                      <div v-for="(m, k) in conferidas[i].erros" :key="k">{{ m }}</div>
                    </div>
                    <div v-else class="space-y-0.5">
                      <div class="text-emerald-700 dark:text-emerald-400">
                        OK · {{ conferidas[i].origem }} tem {{ conferidas[i].saldo_virtual }} disponível
                      </div>
                      <div v-if="conferidas[i].troca_produto" class="text-amber-700 dark:text-amber-400">
                        Troca de produto: {{ conferidas[i].origem_nome }} → {{ conferidas[i].destino_nome }}
                      </div>
                      <div v-if="conferidas[i].custo == null" class="text-amber-700 dark:text-amber-400">
                        {{ conferidas[i].destino }} sem custo no cadastro: entra com custo 0 no Bling.
                      </div>
                    </div>
                  </template>
                  <span v-else class="text-muted-foreground">—</span>
                </td>
                <td class="py-1.5 pl-1">
                  <Button size="sm" variant="ghost" aria-label="Remover linha" @click="removerLinha(i)">
                    <Trash2 class="size-4" />
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" @click="adicionarLinha">
            <Plus class="size-4 mr-1" /> Adicionar linha
          </Button>
          <input
            v-model="observacao"
            maxlength="500"
            placeholder="Motivo (vai para o extrato do Bling) — ex.: distribuição do CD"
            class="h-8 flex-1 min-w-[240px] rounded-md border bg-background px-2 text-sm"
          />
        </div>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <span v-if="motivoDesligado" class="mr-auto text-xs text-muted-foreground">{{ motivoDesligado }}</span>
          <Button variant="ghost" @click="emit('close')">Fechar</Button>
          <Button variant="outline" :disabled="conferindo || enviando" @click="conferir">
            <Loader2 v-if="conferindo" class="size-4 mr-1 animate-spin" />
            Conferir
          </Button>
          <Button :disabled="!podeTransferir" data-botao-transferir @click="transferir">
            <Loader2 v-if="enviando" class="size-4 mr-1 animate-spin" />
            Transferir
          </Button>
        </div>
      </template>

      <p v-if="erro" class="text-sm text-red-600" data-erro-transferir>{{ erro }}</p>

      <!-- Últimas transferências -->
      <div class="border-t pt-3 space-y-2">
        <div class="flex items-center gap-2">
          <h3 class="text-sm font-semibold">Últimas transferências</h3>
          <Button size="sm" variant="ghost" :disabled="carregandoHistorico" aria-label="Atualizar" @click="carregarHistorico">
            <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregandoHistorico }" />
          </Button>
        </div>
        <p v-if="!historico.length && !carregandoHistorico" class="text-xs text-muted-foreground">Nenhuma ainda.</p>
        <div v-for="t in historico" :key="t.id" class="rounded-md border px-3 py-2 text-xs space-y-1" data-transferencia-historico>
          <div class="flex flex-wrap items-center gap-x-2 text-muted-foreground">
            <span class="font-medium text-foreground">#{{ t.numero }}</span>
            <span>{{ quando(t.criado_em) }}</span>
            <span v-if="t.criado_por">· {{ t.criado_por }}</span>
            <span v-if="t.observacao">· {{ t.observacao }}</span>
          </div>
          <div v-for="l in t.linhas" :key="l.id" class="flex flex-wrap items-center gap-2">
            <span class="font-mono">{{ l.quantidade }} × {{ l.origem }} → {{ l.destino }}</span>
            <span :class="ESTADO_COR[l.estado]">{{ l.texto }}</span>
            <template v-if="l.pode_resolver">
              <Button v-if="l.pode_ja_lancou" size="sm" variant="outline" class="h-6 px-2 text-xs" :disabled="resolvendo === l.id" @click="resolver(l, 'ja_lancou')">
                {{ l.precisa_conferir ? 'Está no extrato' : 'Já lancei no Bling' }}
              </Button>
              <Button size="sm" variant="outline" class="h-6 px-2 text-xs" :disabled="resolvendo === l.id" @click="resolver(l, 'tentar_de_novo')">
                {{ l.precisa_conferir ? 'Não está — lançar de novo' : 'Tentar de novo' }}
              </Button>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
