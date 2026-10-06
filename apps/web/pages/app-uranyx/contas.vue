<script setup lang="ts">
// App Uranyx › Contas (06/10/2026): busca por CPF/CNPJ exato, a conta
// (congelar/descongelar, acessos) e os pedidos de "Não fui eu".
// O documento vai no CORPO do pedido, nunca na URL (não pode parar em log de
// acesso nem no histórico do navegador). `?conta=<id>` abre direto uma conta
// (é o link da Fila do SAC) e acompanha a conta na tela: fechar tira, e
// conta que não existe mais avisa e tira.
import { RefreshCw, Search, UserRound } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  dataHora, documentoLimpo, erroAppUranyx, ESTADO_CONTA_LABEL, linhasDoErro, rotulo, TABS_APP_URANYX,
  type Conta,
} from '~/lib/appUranyx'

definePageMeta({ middleware: ['admin', 'app-uranyx'] })

const { chamar, indisponivel } = useAppUranyx()
const route = useRoute()
const router = useRouter()

const documento = ref('')
const buscando = ref(false)
const buscou = ref(false)
const erroBusca = ref<string | null>(null)
const resultados = ref<Conta[]>([])
const conta = ref<Conta | null>(null)
const abrindo = ref(false)

const docLimpo = computed(() => documentoLimpo(documento.value))
const docValido = computed(() => docLimpo.value.length === 11 || docLimpo.value.length === 14)

async function buscar() {
  if (!docValido.value || buscando.value) return
  buscando.value = true
  erroBusca.value = null
  try {
    const r = await chamar<Conta[]>('contas/busca', { method: 'POST', body: { documento: docLimpo.value } })
    resultados.value = r
    buscou.value = true
    // Uma conta só: já abre.
    if (r.length === 1) mostrar(r[0])
    else fechar()
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para buscar')
    erroBusca.value = [er.texto, ...linhasDoErro(er)].join(' ')
  } finally {
    buscando.value = false
  }
}

function mostrar(c: Conta) {
  conta.value = c
  if (route.query.conta !== c.id) void router.replace({ query: { ...route.query, conta: c.id } })
}

// Tira o `?conta=` da URL (o watch abaixo limpa a tela).
function semContaNaUrl() {
  if (!('conta' in route.query)) return
  const query = { ...route.query }
  delete query.conta
  void router.replace({ query })
}

// `seqConta` descarta a resposta de uma conta que já saiu da tela.
let seqConta = 0
function largarConta() {
  seqConta++
  conta.value = null
  abrindo.value = false
}

function fechar() {
  largarConta()
  semContaNaUrl()
}

async function abrirPorId(id: string) {
  if (conta.value?.id === id) return
  const meu = ++seqConta
  abrindo.value = true
  erroBusca.value = null
  try {
    const c = await chamar<Conta>(`contas/${encodeURIComponent(id)}`)
    if (meu === seqConta) conta.value = c
  } catch (e: any) {
    if (meu !== seqConta) return
    // Não fica outra conta na tela com a URL apontando para esta.
    conta.value = null
    const er = erroAppUranyx(e, 'Não deu para abrir a conta')
    if (er.codigo === 'nao_encontrado') {
      erroBusca.value = 'A conta deste link não existe mais no app.'
      semContaNaUrl()
    } else erroBusca.value = er.texto
  } finally {
    if (meu === seqConta) abrindo.value = false
  }
}

function verConta(id: string) {
  // Mesmo id já na URL (abrir falhou antes): a URL não muda, abre direto.
  if (route.query.conta === id) void abrirPorId(id)
  else void router.replace({ query: { ...route.query, conta: id } })
}

function seguirUrl(id: unknown) {
  if (typeof id === 'string' && id) void abrirPorId(id)
  else largarConta() // saiu da URL: Fechar ou o voltar do navegador
}
// O `?conta=` do link abre no navegador (onMounted), como as outras telas do
// módulo: no servidor do Nuxt o GET do repasse seria jogado fora e repetido.
watch(() => route.query.conta, seguirUrl)
onMounted(() => seguirUrl(route.query.conta))

function aoMudar(c: Conta) {
  conta.value = c
  resultados.value = resultados.value.map((x) => (x.id === c.id ? c : x))
}

// Com a API fora, a lista do "Não fui eu" nem está na tela: um pedido leve
// diz se ela voltou (e a tela volta sozinha, a lista carrega ao aparecer).
async function tentarDeNovo() {
  abrindo.value = true
  try {
    await chamar('reclamacoes', { query: { estado: 'aberta' } })
  } catch {
    // continua o aviso de fora do ar (ou o erro aparece nas próximas ações)
  } finally {
    abrindo.value = false
  }
  const id = route.query.conta
  if (!indisponivel.value && typeof id === 'string' && id) {
    conta.value = null
    void abrirPorId(id)
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_APP_URANYX" />
    <PageHeader
      title="Contas do app"
      description="Busque pelo CPF ou CNPJ completo. Dá para congelar uma conta (derruba as sessões) e ver os acessos. Abaixo, os pedidos de &quot;Não fui eu&quot;."
    />

    <AppUranyxIndisponivel v-if="indisponivel" :mensagem="indisponivel" :carregando="buscando || abrindo" @tentar="tentarDeNovo" />

    <template v-else>
      <form class="flex flex-wrap items-end gap-2" autocomplete="off" @submit.prevent="buscar">
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">CPF ou CNPJ</span>
          <span class="relative block">
            <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              v-model="documento"
              inputmode="text"
              maxlength="24"
              class="h-9 w-64 max-w-full rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="000.000.000-00"
            />
          </span>
        </label>
        <Button type="submit" size="sm" :disabled="!docValido || buscando">
          <RefreshCw v-if="buscando" class="mr-1 size-4 animate-spin" />
          Buscar
        </Button>
        <span v-if="documento && !docValido" class="text-xs text-muted-foreground">CPF tem 11 dígitos; CNPJ, 14.</span>
      </form>

      <div v-if="erroBusca" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erroBusca }}</div>

      <p v-if="buscou && !resultados.length && !erroBusca" class="text-sm text-muted-foreground">Nenhuma conta com esse documento.</p>
      <ul v-if="resultados.length > 1" class="divide-y rounded-xl border bg-card">
        <li v-for="c in resultados" :key="c.id">
          <button class="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/40" :class="{ 'bg-muted/40': conta?.id === c.id }" @click="mostrar(c)">
            <UserRound class="size-4 text-muted-foreground" aria-hidden="true" />
            <span class="font-medium">{{ c.nome || 'Sem nome' }}</span>
            <span class="text-muted-foreground">{{ c.documento_mascarado }}</span>
            <span class="pill-muted">{{ rotulo(ESTADO_CONTA_LABEL, c.estado) }}</span>
            <span class="ml-auto text-xs text-muted-foreground">criada em {{ dataHora(c.criado_em) }}</span>
          </button>
        </li>
      </ul>

      <p v-if="abrindo && !conta" class="text-sm text-muted-foreground">Abrindo a conta…</p>
      <AppUranyxConta v-if="conta" :conta="conta" @mudou="aoMudar" @fechar="fechar" />

      <AppUranyxReclamacoes @ver-conta="verConta" />
    </template>
  </div>
</template>
