<script setup lang="ts">
// Cadastros › Envios Threema (tarefa 16). Cada aviso que o DaVinci (e o Mac
// do escritório) manda pelo Threema, num lugar só: o que é, quando sai, quem
// recebe, do que depende, se está ligado e como foi o último envio.
//
// O código descreve cada envio (o catálogo); o banco vai decidir quem recebe.
// Enquanto um envio não foi migrado (todos, nesta fase), quem recebe é a
// configuração antiga de sempre — a faixa âmbar diz qual e onde ela é editada
// hoje. Nada desta tela muda quem recebe um aviso do servidor ainda não
// migrado. Os IDs do Threema nunca aparecem inteiros.
//
// Endereço: ?envio=<chave> abre a gaveta do envio (&regra=<id> marca a regra
// de status do item 1); ?pessoa=<id> mostra só os envios daquela pessoa;
// ?aba=grupos|contatos abre Grupos ou Contatos externos.
//
// Edição (C7, só admin): na gaveta, para o envio que lê o Cadastro (o do
// servidor migrado e o do Mac); Grupos e Contatos externos nas abas; e o
// botão de emergência, que volta TODOS os envios para a configuração antiga.
import { computed, onMounted, ref, watch } from 'vue'
import { MessageSquare, RefreshCw, Search, ShieldAlert, X } from 'lucide-vue-next'
import { TABS_CADASTROS } from '~/lib/navGroups'
import {
  FREQUENCIA, ONDE_RODA, SITUACAO, classeFaixa, porRegraTexto, quemRecebeTexto, ultimoEnvioTexto,
  type Envio, type Lista,
} from '~/lib/enviosThreema'

definePageMeta({ middleware: ['admin'] })

const { api } = useApi()
const route = useRoute()
const router = useRouter()

const lista = ref<Lista | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)

const busca = ref('')
const painel = ref('')
const ondeRoda = ref('')
const situacao = ref('')
const pessoa = computed(() => (typeof route.query.pessoa === 'string' ? route.query.pessoa : ''))

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const query: Record<string, string> = {}
    if (painel.value) query.painel = painel.value
    if (ondeRoda.value) query.onde_roda = ondeRoda.value
    if (situacao.value) query.situacao = situacao.value
    if (busca.value.trim()) query.busca = busca.value.trim()
    if (pessoa.value) query.pessoa = pessoa.value
    const r = await api<Lista>('/api/threema/envios', { query })
    if (meu !== seq) return
    lista.value = r
  } catch {
    if (meu !== seq) return
    erro.value = 'Não deu para carregar os envios do Threema. Tente atualizar.'
  } finally {
    if (meu === seq) carregando.value = false
  }
}

let espera: ReturnType<typeof setTimeout> | null = null
watch(busca, () => {
  if (espera) clearTimeout(espera)
  espera = setTimeout(carregar, 300)
})
watch([painel, ondeRoda, situacao, pessoa], () => carregar())
onMounted(carregar)

const temFiltro = computed(
  () => !!(busca.value.trim() || painel.value || ondeRoda.value || situacao.value || pessoa.value),
)
function limparFiltros() {
  busca.value = ''
  painel.value = ''
  ondeRoda.value = ''
  situacao.value = ''
  if (pessoa.value) tirarDoEndereco('pessoa')
}

function tirarDoEndereco(...chaves: string[]) {
  const query = { ...route.query }
  for (const k of chaves) delete query[k]
  router.replace({ query })
}

// Os envios por painel, na ordem do catálogo.
const porPainel = computed(() => {
  const grupos: { painel: string; envios: Envio[] }[] = []
  for (const e of lista.value?.envios ?? []) {
    let g = grupos.find((x) => x.painel === e.painel)
    if (!g) {
      g = { painel: e.painel, envios: [] }
      grupos.push(g)
    }
    g.envios.push(e)
  }
  return grupos
})

const migrados = computed(() => (lista.value?.envios ?? []).filter((e) => e.migrado).length)
const descricao = computed(() => {
  const base = 'Cada aviso que sai pelo Threema: o que é, quando sai, quem recebe e se chegou.'
  if (!lista.value) return base
  return `${base} ${lista.value.total} envios · ${migrados.value} lendo daqui · `
    + 'os demais usam a configuração antiga (faixa âmbar).'
})

// Abas da página: Envios | Grupos | Contatos externos (no endereço, ?aba=).
const ABAS = [['envios', 'Envios'], ['grupos', 'Grupos'], ['contatos', 'Contatos externos']] as const
type Aba = (typeof ABAS)[number][0]
const aba = computed<Aba>(() =>
  route.query.aba === 'grupos' || route.query.aba === 'contatos' ? route.query.aba : 'envios',
)
function trocarAba(a: Aba) {
  const query = { ...route.query }
  if (a === 'envios') delete query.aba
  else query.aba = a
  delete query.envio
  delete query.regra
  router.replace({ query })
}

// Emergência: um clique volta todos os envios para a configuração antiga
// (vale no próximo envio, sem recriar container).
const mudandoEmergencia = ref(false)
async function alternarEmergencia() {
  const atual = lista.value?.emergencia
  if (!atual || mudandoEmergencia.value) return
  const ligar = !atual.usar_legado
  const pergunta = ligar
    ? 'Ligar a emergência? TODOS os envios voltam para a configuração antiga no próximo envio '
      + '(o Cadastro deixa de valer até desligar).'
    : 'Desligar a emergência? Os envios migrados voltam a ler a lista daqui.'
  if (!confirm(pergunta)) return
  mudandoEmergencia.value = true
  erro.value = null
  try {
    await api('/api/threema/emergencia', { method: 'POST', body: { usar_legado: ligar } })
    await carregar()
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para mudar a emergência.'
  } finally {
    mudandoEmergencia.value = false
  }
}
const contagem = computed(() => lista.value?.contagem ?? ({} as Record<string, number>))

// Descrição longa: "ver mais" por linha.
const abertas = ref<Set<string>>(new Set())
function alternarDescricao(chave: string) {
  const s = new Set(abertas.value)
  if (s.has(chave)) s.delete(chave)
  else s.add(chave)
  abertas.value = s
}

// Gaveta do envio: pelo endereço (?envio=), para o link direto abrir nela.
const envioAberto = computed(() => (typeof route.query.envio === 'string' ? route.query.envio : null))
const regraMarcada = computed(() => (typeof route.query.regra === 'string' ? route.query.regra : null))
function abrir(e: Envio) {
  router.replace({ query: { ...route.query, envio: e.chave } })
}
function fechar() {
  tirarDoEndereco('envio', 'regra')
}
</script>

<template>
  <div>
    <!-- A gaveta fica FORA do space-y-4: senão ganha o margin-top de 16 px e o
         topo da tela fica sem escurecer. -->
    <div class="space-y-4">
      <RouteTabs :tabs="TABS_CADASTROS" />

      <PageHeader title="Envios Threema" :description="descricao">
        <template v-if="lista" #actions>
          <div
            class="rounded-md border px-3 py-2 text-sm max-w-md"
            :class="lista.emergencia.usar_legado || lista.emergencia.env_desligado
              ? 'border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300'
              : 'bg-muted/30'"
          >
            <div class="flex items-center gap-2 font-medium">
              <ShieldAlert class="size-4" />
              Emergência: voltar todos para a configuração antiga
            </div>
            <p class="mt-0.5 text-xs">
              <template v-if="lista.emergencia.usar_legado">
                LIGADA<span v-if="lista.emergencia.alterado_por"> por {{ lista.emergencia.alterado_por }}</span>:
                todos os envios usam a configuração antiga.
              </template>
              <template v-else-if="lista.emergencia.env_desligado">
                <span title="THREEMA_CADASTRO_ATIVO=false no servidor">Ligada pelo servidor (só o técnico desliga)</span>:
                todos usam a configuração antiga.
              </template>
              <template v-else>Desligada. Os envios migrados leem a lista daqui.</template>
            </p>
            <button
              type="button"
              class="mt-1 h-7 rounded-md border bg-background px-2 text-xs hover:bg-muted disabled:opacity-50"
              :disabled="mudandoEmergencia"
              data-acao="emergencia"
              @click="alternarEmergencia"
            >{{ lista.emergencia.usar_legado ? 'Desligar a emergência' : 'Ligar a emergência' }}</button>
          </div>
        </template>
      </PageHeader>

      <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">{{ erro }}</div>

      <nav class="flex gap-4 border-b" aria-label="Seções de Envios Threema">
        <button
          v-for="[k, rotulo] in ABAS"
          :key="k"
          type="button"
          class="-mb-px border-b-2 px-1 py-2 text-sm"
          :class="aba === k ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'"
          :aria-current="aba === k ? 'page' : undefined"
          @click="trocarAba(k)"
        >{{ rotulo }}</button>
      </nav>

      <EnviosThreemaGrupos v-if="aba === 'grupos'" @mudou="carregar" />
      <EnviosThreemaContatos v-else-if="aba === 'contatos'" @mudou="carregar" />
      <template v-else>
        <div class="flex flex-wrap items-center gap-2">
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="busca"
              class="h-9 w-72 max-w-full rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="envio, pessoa, remetente…"
            />
          </div>
          <select v-model="painel" class="h-9 rounded-md border bg-background px-2 text-sm" title="Painel">
            <option value="">Painel: todos</option>
            <option v-for="p in lista?.paineis ?? []" :key="p" :value="p">{{ p }}</option>
          </select>
          <select v-model="ondeRoda" class="h-9 rounded-md border bg-background px-2 text-sm" title="Onde roda">
            <option value="">Onde roda: todos</option>
            <option v-for="(rotulo, k) in ONDE_RODA" :key="k" :value="k">{{ rotulo }}</option>
          </select>
          <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" title="Situação">
            <option value="">Situação: todas</option>
            <option v-for="(s, k) in SITUACAO" :key="k" :value="k">
              {{ s.rotulo }} ({{ contagem[k] ?? 0 }})
            </option>
          </select>
          <span v-if="pessoa" class="inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs" data-filtro-pessoa>
            Só os envios de {{ lista?.pessoa?.nome || 'uma pessoa' }}
            <span class="text-muted-foreground">(fora os do Mac, que ainda usam a lista de lá)</span>
            <button type="button" aria-label="tirar o filtro da pessoa" @click="tirarDoEndereco('pessoa')">
              <X class="size-3" />
            </button>
          </span>
          <button v-if="temFiltro" type="button" class="text-sm text-muted-foreground underline" @click="limparFiltros">
            limpar filtros
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-3 text-sm hover:bg-muted"
            :disabled="carregando"
            @click="carregar"
          >
            <RefreshCw class="size-4" :class="{ 'animate-spin': carregando }" /> atualizar
          </button>
        </div>


        <p v-if="carregando && !lista" class="rounded-md border py-10 text-center text-sm text-muted-foreground" data-carregando>
          Carregando envios…
        </p>

        <div v-if="lista && !lista.envios.length" class="rounded-md border py-10 text-center text-sm text-muted-foreground">
          <MessageSquare class="mx-auto mb-2 size-6 opacity-50" />
          Nenhum envio com esses filtros.
        </div>

        <section v-for="g in porPainel" :key="g.painel" class="space-y-2">
          <h2 class="text-sm font-semibold text-muted-foreground">{{ g.painel }}</h2>
          <div class="table-card overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr>
                  <th class="text-left min-w-[18rem]">Envio</th>
                  <th class="text-left">Frequência</th>
                  <th class="text-left min-w-[12rem]">Quem recebe</th>
                  <th class="text-left">Depende de</th>
                  <th class="text-left">Ativo</th>
                  <th class="text-left whitespace-nowrap">Último envio</th>
                  <th class="coluna-acoes text-right">Ações</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="e in g.envios"
                  :key="e.chave"
                  :data-envio="e.chave"
                  class="align-top"
                  :class="{ 'text-muted-foreground bg-muted/20': !e.gerenciado }"
                >
                  <td>
                    <button type="button" class="text-left font-medium hover:underline" data-abrir-nome @click="abrir(e)">
                      {{ e.nome }}<span v-if="e.aba" class="font-normal text-muted-foreground"> · {{ e.aba }}</span>
                    </button>
                    <div class="text-xs text-muted-foreground">
                      <span :class="abertas.has(e.chave) ? '' : 'line-clamp-2'">{{ e.descricao }}</span>
                      <button
                        v-if="e.descricao.length > 140"
                        type="button"
                        class="underline"
                        @click="alternarDescricao(e.chave)"
                      >{{ abertas.has(e.chave) ? 'ver menos' : 'ver mais' }}</button>
                    </div>
                    <div v-if="e.ultimo?.titulo" class="mt-0.5 text-xs text-muted-foreground">
                      última mensagem: {{ e.ultimo.titulo }}
                    </div>
                    <ul v-if="e.faixas.length" class="mt-1 space-y-1">
                      <li
                        v-for="(f, i) in e.faixas"
                        :key="i"
                        class="rounded border px-2 py-1 text-xs"
                        :class="classeFaixa(f.tipo)"
                        :data-faixa="f.situacao"
                      >{{ f.texto }}</li>
                    </ul>
                  </td>
                  <td class="whitespace-nowrap">
                    <span class="pill-muted">{{ FREQUENCIA[e.frequencia_tipo] }}</span>
                    <div class="mt-0.5 text-xs text-muted-foreground whitespace-normal max-w-[12rem]">{{ e.frequencia_texto }}</div>
                    <div class="text-xs text-muted-foreground">{{ ONDE_RODA[e.onde_roda] }}</div>
                  </td>
                  <td>
                    <span
                      v-if="e.gerenciado && !e.quem_recebe.nomes.length"
                      class="text-xs"
                      :class="e.onde_roda === 'mac' ? 'text-muted-foreground' : 'text-red-700 font-medium dark:text-red-400'"
                    >{{ e.onde_roda === 'mac' ? 'a lista do próprio Mac' : 'ninguém' }}</span>
                    <span v-else>{{ e.quem_recebe.por_regra?.regras ? `padrão: ${quemRecebeTexto(e)}` : quemRecebeTexto(e) }}</span>
                    <div v-if="porRegraTexto(e)" class="text-xs text-muted-foreground" data-por-regra>{{ porRegraTexto(e) }}</div>
                  </td>
                  <td class="text-xs">
                    <ul v-if="e.depende_de.length" class="space-y-0.5">
                      <li v-for="d in e.depende_de" :key="d.alvo" :class="d.ok ? 'text-muted-foreground' : 'text-amber-700 dark:text-amber-400'">
                        <template v-if="d.ok">✓ {{ d.texto }}</template>
                        <template v-else>! Precisa: {{ d.texto }} — hoje: {{ d.estado }}</template>
                      </li>
                    </ul>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="text-xs">
                    <span :class="e.ativo ? '' : 'text-muted-foreground'">{{ e.ativo_texto }}</span>
                  </td>
                  <td class="text-xs min-w-[7rem] max-w-[12rem]">
                    <span
                      :class="e.ultimo && (e.ultimo.falhas || !e.ultimo.total) ? 'text-red-700 dark:text-red-400' : 'text-muted-foreground'"
                    >{{ ultimoEnvioTexto(e.ultimo, undefined, e) }}</span>
                  </td>
                  <td class="coluna-acoes whitespace-nowrap text-right">
                    <button
                      type="button"
                      class="h-8 rounded-md border px-2 text-xs hover:bg-muted"
                      @click="abrir(e)"
                    >{{ e.editavel ? 'Editar' : 'Detalhes' }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

      </template>

    </div>

    <EnviosThreemaGaveta
      :chave="envioAberto"
      :regra="regraMarcada"
      @fechar="fechar"
      @mudou="carregar"
    />
  </div>
</template>

<style scoped>
/* A coluna Ações fica presa à direita: na tabela larga (e no celular) o
   botão continua à vista sem arrastar o cartão para o lado. O fundo é
   imagem para cobrir o que rola por baixo (o hover da linha só pinta cor). */
.coluna-acoes {
  position: sticky;
  right: 0;
  z-index: 1;
  background-image: linear-gradient(hsl(var(--card)), hsl(var(--card)));
  box-shadow: inset 1px 0 0 hsl(var(--border));
}
thead .coluna-acoes {
  background-image:
    linear-gradient(hsl(var(--muted) / 0.4), hsl(var(--muted) / 0.4)),
    linear-gradient(hsl(var(--card)), hsl(var(--card)));
}
</style>
