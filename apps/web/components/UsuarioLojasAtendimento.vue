<script lang="ts">
// Usuários › "LOJAS NO ATENDIMENTO" (30/10/2026) — o Eduardo: "será separado as
// lojas no atendimento por pessoas então x usuario vai poder ver x lojas e
// outros não isso também se aplica pra caixa de e-mail das lojas delas" (e a
// Garantia: "eles só vão poder ver das lojas deles"). A planilha "Separação de
// contas": Vendedor 1 = londres, 2 = israel, 3 = marrocos.
//
//   GET /api/users/{id}/lojas-atendimento — as lojas para marcar (uma por
//       plataforma + conta, com as chaves que andam juntas: a ficha, a
//       integração do chat, o canal do robô), as chaves da pessoa e quem mais
//       tem cada loja;
//   PUT /api/users/{id}/lojas-atendimento {chaves} — troca as lojas.
//
// Admin vê tudo sem marcar nada; quem não é admin e não tem loja não vê nenhuma.
// Só admin abre a tela (middleware `admin` da página) e a API confere de novo.
// Funções puras aqui em cima (tests/usuario-lojas-atendimento.cjs).

export type LojaParaVincular = {
  chave: string
  plataforma: string
  nome: string
  nome_no_chat: string | null
  chat: boolean
  chaves: string[]
}
export type LojasDaPessoa = {
  user_id: string
  admin: boolean
  chaves: string[]
  lojas: LojaParaVincular[]
  outros: Record<string, string[]>
}

export const PLATAFORMAS: Record<string, string> = {
  shopee: 'Shopee',
  ml: 'Mercado Livre',
  tiktok: 'TikTok',
  amazon: 'Amazon',
  magalu: 'Magalu',
  shein: 'Shein',
  temu: 'Temu',
  aliexpress: 'AliExpress',
  carrefour: 'Carrefour',
  netshoes: 'Netshoes',
}
export const nomeDaPlataforma = (p: string) => PLATAFORMAS[p] || (p ? p[0].toUpperCase() + p.slice(1) : '—')

// A loja está marcada quando TODAS as chaves dela estão na seleção.
export function marcada(loja: LojaParaVincular, selecao: Set<string>): boolean {
  return loja.chaves.length > 0 && loja.chaves.every((c) => selecao.has(c))
}

// Marca/desmarca a loja inteira (as chaves andam juntas).
export function alternarLoja(selecao: Set<string>, loja: LojaParaVincular, marcar: boolean): Set<string> {
  const nova = new Set(selecao)
  for (const c of loja.chaves) {
    if (marcar) nova.add(c)
    else nova.delete(c)
  }
  return nova
}

// As lojas por plataforma, na ordem da lista (a API já ordena), com o filtro do texto.
export function porPlataforma(lojas: LojaParaVincular[], busca = ''): { plataforma: string; lojas: LojaParaVincular[] }[] {
  const q = busca.trim().toLowerCase()
  const grupos = new Map<string, LojaParaVincular[]>()
  for (const l of lojas) {
    const texto = `${nomeDaPlataforma(l.plataforma)} ${l.nome} ${l.nome_no_chat || ''}`.toLowerCase()
    if (q && !texto.includes(q)) continue
    if (!grupos.has(l.plataforma)) grupos.set(l.plataforma, [])
    grupos.get(l.plataforma)!.push(l)
  }
  return [...grupos.entries()].map(([plataforma, ls]) => ({ plataforma, lojas: ls }))
}

// O que vai no PUT: as chaves das lojas marcadas + as gravadas que não são de
// loja nenhuma da lista (a loja arquivada continua com a pessoa até alguém tirar).
export function chavesParaSalvar(lojas: LojaParaVincular[], selecao: Set<string>, gravadas: string[]): string[] {
  const daLista = new Set(lojas.flatMap((l) => l.chaves))
  const saida = new Set<string>()
  for (const l of lojas) if (marcada(l, selecao)) for (const c of l.chaves) saida.add(c)
  for (const c of gravadas) if (!daLista.has(c)) saida.add(c)
  return [...saida].sort()
}

export function mesmasChaves(a: string[], b: string[]): boolean {
  const sa = new Set(a)
  return sa.size === new Set(b).size && b.every((x) => sa.has(x))
}

// Quem mais tem a loja (pela primeira chave dela: a da ficha, senão a do chat).
export function outrosDaLoja(loja: LojaParaVincular, outros: Record<string, string[]>): string[] {
  const nomes = new Set<string>()
  for (const c of loja.chaves) for (const n of outros[c] || []) nomes.add(n)
  return [...nomes].sort((a, b) => a.localeCompare(b, 'pt-BR'))
}

export const ERROS_LOJAS: Record<string, string> = {
  admin_only: 'Só admin muda as lojas de uma pessoa.',
  usuario_nao_encontrado: 'Usuário não encontrado. Atualize a página.',
  chave_invalida: 'Alguma loja veio num formato inválido. Atualize a página e marque de novo.',
  loja_desconhecida: 'Alguma loja não existe mais. Atualize a página e marque de novo.',
}
</script>

<script setup lang="ts">
import { Loader2, Save } from 'lucide-vue-next'

const props = defineProps<{ userId: string; isAdminUser: boolean }>()
const { api } = useApi()

const dados = ref<LojasDaPessoa | null>(null)
const selecao = ref<Set<string>>(new Set())
const busca = ref('')
const carregando = ref(false)
const salvando = ref(false)
const erro = ref<string | null>(null)
const mensagem = ref<string | null>(null)

const rota = computed(() => `/api/users/${encodeURIComponent(props.userId)}/lojas-atendimento`)
const grupos = computed(() => (dados.value ? porPlataforma(dados.value.lojas, busca.value) : []))
const paraSalvar = computed(() => (dados.value ? chavesParaSalvar(dados.value.lojas, selecao.value, dados.value.chaves) : []))
const mudou = computed(() => !!dados.value && !mesmasChaves(paraSalvar.value, dados.value.chaves))
const quantas = computed(() => (dados.value ? dados.value.lojas.filter((l) => marcada(l, selecao.value)).length : 0))

function textoDoErro(e: any, padrao: string): string {
  const code = e?.data?.detail?.code
  if (code && ERROS_LOJAS[code]) return ERROS_LOJAS[code]
  return e?.data?.detail?.detail || e?.message || padrao
}
function aplicar(d: LojasDaPessoa) {
  dados.value = d
  selecao.value = new Set(d.chaves)
}
async function carregar() {
  if (carregando.value) return
  carregando.value = true
  erro.value = null
  try { aplicar(await api<LojasDaPessoa>(rota.value)) }
  catch (e: any) { erro.value = textoDoErro(e, 'Não consegui carregar as lojas.') }
  finally { carregando.value = false }
}
function alternar(loja: LojaParaVincular, marcar: boolean) {
  mensagem.value = null
  selecao.value = alternarLoja(selecao.value, loja, marcar)
}
function alternarPlataforma(lojas: LojaParaVincular[], marcar: boolean) {
  mensagem.value = null
  let s = selecao.value
  for (const l of lojas) s = alternarLoja(s, l, marcar)
  selecao.value = s
}
function desfazer() {
  if (dados.value) selecao.value = new Set(dados.value.chaves)
  mensagem.value = null
}
async function salvar() {
  if (!dados.value || salvando.value || !mudou.value) return
  salvando.value = true
  erro.value = null
  mensagem.value = null
  try {
    aplicar(await api<LojasDaPessoa>(rota.value, { method: 'PUT', body: { chaves: paraSalvar.value } }))
    mensagem.value = 'Lojas no atendimento salvas.'
  } catch (e: any) {
    erro.value = textoDoErro(e, 'Não consegui salvar as lojas.')
  } finally { salvando.value = false }
}
function aoAbrir(event: Event) {
  if ((event.target as HTMLDetailsElement).open && !dados.value) void carregar()
}
</script>

<template>
  <details class="rounded-lg border bg-card text-card-foreground" data-lojas-atendimento @toggle="aoAbrir">
    <summary class="cursor-pointer px-6 py-4 font-semibold">Lojas no atendimento</summary>
    <div class="px-6 pb-6 space-y-3">
      <p class="text-sm text-muted-foreground">
        As lojas (plataforma + conta) que esta pessoa vê no Atendimento: as conversas, os números e
        bolinhas, as caixas de e-mail das lojas na barra da Caixa e a Garantia. Responder e mexer na
        conversa também só nessas lojas.
      </p>
      <p class="text-sm font-medium">
        Sem nenhuma loja marcada, a pessoa não vê nenhuma loja no Atendimento.
      </p>
      <p v-if="isAdminUser" class="text-sm text-muted-foreground" data-aviso-admin>
        Administradores veem todas as lojas, a aba E-mail e todos os e-mails, independentemente desta seleção.
      </p>
      <p v-if="carregando" class="text-sm text-muted-foreground">Carregando lojas… <Loader2 class="inline size-4 animate-spin" /></p>
      <p v-if="erro" class="text-sm text-destructive" role="alert">{{ erro }}</p>
      <Button v-if="!dados && !carregando" type="button" variant="outline" size="sm" data-carregar-lojas @click="carregar">
        Carregar lojas
      </Button>
      <template v-if="dados">
        <input
          v-model="busca"
          type="search"
          placeholder="Filtrar loja ou plataforma"
          class="h-9 w-full max-w-sm rounded-md border bg-background px-3 text-sm"
          aria-label="Filtrar lojas"
        />
        <fieldset :disabled="salvando" class="space-y-4 disabled:opacity-60">
          <legend class="sr-only">Lojas no atendimento</legend>
          <section v-for="g in grupos" :key="g.plataforma" class="space-y-1" :data-plataforma="g.plataforma">
            <div class="flex items-center gap-3">
              <h3 class="text-sm font-semibold">{{ nomeDaPlataforma(g.plataforma) }}</h3>
              <button type="button" class="text-xs underline text-muted-foreground" @click="alternarPlataforma(g.lojas, true)">marcar todas</button>
              <button type="button" class="text-xs underline text-muted-foreground" @click="alternarPlataforma(g.lojas, false)">desmarcar</button>
            </div>
            <div class="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
              <label
                v-for="l in g.lojas"
                :key="l.chave"
                class="flex items-start gap-2 rounded border px-3 py-2 text-sm cursor-pointer hover:bg-muted/50"
                :data-loja="l.chave"
              >
                <input
                  type="checkbox"
                  class="mt-0.5"
                  :checked="marcada(l, selecao)"
                  @change="alternar(l, ($event.target as HTMLInputElement).checked)"
                />
                <span class="min-w-0">
                  <span class="font-medium">{{ l.nome }}</span>
                  <span v-if="l.nome_no_chat && l.nome_no_chat.toLowerCase() !== l.nome.toLowerCase()" class="text-muted-foreground"> · no chat: {{ l.nome_no_chat }}</span>
                  <span v-if="!l.chat" class="block text-xs text-muted-foreground">sem chat no DaVinci: e-mails, pedidos e garantia</span>
                  <span v-if="outrosDaLoja(l, dados.outros).length" class="block text-xs text-amber-600">também com: {{ outrosDaLoja(l, dados.outros).join(', ') }}</span>
                </span>
              </label>
            </div>
          </section>
          <p v-if="!grupos.length" class="text-sm text-muted-foreground">Nenhuma loja com esse filtro.</p>
        </fieldset>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <p class="text-sm" aria-live="polite">
            {{ mensagem || (quantas ? `${quantas} loja(s) marcada(s).` : 'Nenhuma loja marcada.') }}
          </p>
          <div class="flex items-center gap-2">
            <Button type="button" variant="outline" :disabled="!mudou || salvando" @click="desfazer">Desfazer</Button>
            <Button type="button" :disabled="!mudou || salvando" data-salvar-lojas @click="salvar">
              <Loader2 v-if="salvando" class="size-4 mr-1 animate-spin" /><Save v-else class="size-4 mr-1" />
              {{ salvando ? 'Salvando…' : 'Salvar lojas no atendimento' }}
            </Button>
          </div>
        </div>
      </template>
    </div>
  </details>
</template>
