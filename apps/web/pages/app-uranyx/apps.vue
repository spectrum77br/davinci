<script setup lang="ts">
// App Uranyx › Apps recomendados (06/10/2026): as categorias e os apps da
// aba Descobrir do app (spec 10). Categoria com app não apaga (a API
// responde 409): desative ou mova os apps antes.
import { AppWindow, ExternalLink, ImageOff, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  erroAppUranyx, inteiroOuNull, linhasDoErro, linkPlayStore, TABS_APP_URANYX,
  type AppRecomendado, type CategoriaApps,
} from '~/lib/appUranyx'

definePageMeta({ middleware: ['admin', 'app-uranyx'] })

const { chamar, indisponivel } = useAppUranyx()
const toasts = useToasts()

const categorias = ref<CategoriaApps[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const r = await chamar<CategoriaApps[]>('apps/categorias')
    if (meu !== seq) return
    categorias.value = r
  } catch (e: any) {
    if (meu !== seq) return
    erro.value = erroAppUranyx(e, 'Não deu para carregar os apps').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
onMounted(carregar)

// ─── categoria ──────────────────────────────────────────────────────────────
const catAberta = ref(false)
const catEditando = ref<CategoriaApps | null>(null)
const catForm = reactive({ nome: '', ordem: 0 as string | number, ativo: true })
const catSalvando = ref(false)
const catErros = ref<string[]>([])

function abrirCategoria(c: CategoriaApps | null) {
  catEditando.value = c
  catForm.nome = c?.nome ?? ''
  catForm.ordem = c?.ordem ?? (categorias.value.length ? Math.max(...categorias.value.map((x) => x.ordem)) + 1 : 0)
  catForm.ativo = c?.ativo ?? true
  catErros.value = []
  catAberta.value = true
}

async function salvarCategoria() {
  if (catSalvando.value) return
  const nome = catForm.nome.trim()
  const ordem = inteiroOuNull(catForm.ordem, 0, 10000)
  const problemas: string[] = []
  if (nome.length < 2) problemas.push('Nome: use pelo menos 2 letras.')
  if (ordem === null) problemas.push('Ordem: um número de 0 a 10000.')
  catErros.value = problemas
  if (problemas.length) return
  catSalvando.value = true
  try {
    const corpo = { nome, ordem, ativo: catForm.ativo }
    const c = catEditando.value
    if (c) await chamar(`apps/categorias/${c.id}`, { method: 'PATCH', body: corpo })
    else await chamar('apps/categorias', { method: 'POST', body: corpo })
    toasts.success(c ? 'Categoria salva' : 'Categoria criada', nome)
    catAberta.value = false
    await carregar()
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para salvar a categoria')
    catErros.value = [er.texto, ...linhasDoErro(er)]
  } finally {
    catSalvando.value = false
  }
}

async function apagarCategoria(c: CategoriaApps) {
  if (c.apps.length) {
    toasts.warning('A categoria tem apps', 'Apague ou mova os apps antes, ou só desative a categoria.')
    return
  }
  if (!window.confirm(`Apagar a categoria "${c.nome}"?`)) return
  try {
    await chamar(`apps/categorias/${c.id}`, { method: 'DELETE' })
    categorias.value = categorias.value.filter((x) => x.id !== c.id)
    toasts.success('Categoria apagada', c.nome)
  } catch (e: any) {
    toasts.error('Não deu para apagar', erroAppUranyx(e, 'Não deu para apagar').texto)
  }
}

// ─── app ────────────────────────────────────────────────────────────────────
const appAberto = ref(false)
const appEditando = ref<AppRecomendado | null>(null)
const categoriaDoNovo = ref<string | null>(null)

function abrirApp(a: AppRecomendado | null, categoriaId: string | null = null) {
  appEditando.value = a
  categoriaDoNovo.value = categoriaId
  appAberto.value = true
}

// Salvar pode mudar a categoria e a ordem: relê a lista inteira (é pequena).
function aoMudarApp() {
  void carregar()
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_APP_URANYX" />
    <PageHeader
      title="Apps recomendados"
      description="Categorias e apps da aba Descobrir do app. O botão abre a Play Store pelo pacote; só apps oficiais."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" variant="outline" :disabled="!!indisponivel" @click="abrirCategoria(null)">
          <Plus class="mr-1 size-4" /> Nova categoria
        </Button>
        <Button size="sm" :disabled="!!indisponivel || !categorias.length" @click="abrirApp(null)">
          <Plus class="mr-1 size-4" /> Novo app
        </Button>
      </template>
    </PageHeader>

    <AppUranyxIndisponivel v-if="indisponivel" :mensagem="indisponivel" :carregando="carregando" @tentar="carregar" />

    <template v-else>
      <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

      <p v-if="carregando && !categorias.length" class="text-sm text-muted-foreground">Carregando…</p>
      <EmptyState
        v-else-if="!categorias.length && !erro"
        :icon="AppWindow"
        title="Nenhuma categoria"
        description="Crie uma categoria (ex.: Bancos, Transporte) e depois os apps dela."
      >
        <Button size="sm" @click="abrirCategoria(null)"><Plus class="mr-1 size-4" /> Nova categoria</Button>
      </EmptyState>

      <section v-for="c in categorias" :key="c.id" class="table-card">
        <div class="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
          <h2 class="text-sm font-semibold">{{ c.nome }}</h2>
          <span class="text-xs text-muted-foreground">ordem {{ c.ordem }}</span>
          <span v-if="!c.ativo" class="pill-muted">inativa</span>
          <span class="text-xs text-muted-foreground">· {{ c.apps.length }} {{ c.apps.length === 1 ? 'app' : 'apps' }}</span>
          <div class="ml-auto flex items-center gap-1">
            <button class="btn btn-sm" @click="abrirApp(null, c.id)"><Plus class="mr-1 size-3.5" /> app</button>
            <button class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="Editar categoria" aria-label="Editar categoria" @click="abrirCategoria(c)">
              <Pencil class="size-4" />
            </button>
            <button class="rounded p-1 text-muted-foreground hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40" title="Apagar categoria" aria-label="Apagar categoria" @click="apagarCategoria(c)">
              <Trash2 class="size-4" />
            </button>
          </div>
        </div>
        <table class="w-full text-sm">
          <tbody>
            <tr v-if="!c.apps.length">
              <td class="py-4 text-center text-muted-foreground">Nenhum app nesta categoria.</td>
            </tr>
            <tr v-for="a in c.apps" :key="a.id" class="cursor-pointer" :class="{ 'opacity-60': !a.ativo }" @click="abrirApp(a)">
              <td class="w-12">
                <div class="grid size-9 place-items-center overflow-hidden rounded-lg border bg-muted/30">
                  <img v-if="a.icone_url" :src="a.icone_url" :alt="a.nome" class="size-full object-cover" loading="lazy" />
                  <ImageOff v-else class="size-4 text-muted-foreground" aria-hidden="true" />
                </div>
              </td>
              <td>
                <div class="font-medium">{{ a.nome }}</div>
                <div class="max-w-[28rem] truncate text-xs text-muted-foreground" :title="a.descricao">{{ a.descricao || '—' }}</div>
              </td>
              <td class="whitespace-nowrap">
                <a :href="linkPlayStore(a.pacote_android)" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline" @click.stop>
                  {{ a.pacote_android }} <ExternalLink class="size-3" />
                </a>
                <a v-if="a.link_app_store" :href="a.link_app_store" target="_blank" rel="noopener noreferrer" class="ml-2 inline-flex items-center gap-1 text-xs text-primary hover:underline" @click.stop>
                  App Store <ExternalLink class="size-3" />
                </a>
              </td>
              <td class="whitespace-nowrap text-xs text-muted-foreground">ordem {{ a.ordem }}</td>
              <td class="whitespace-nowrap">
                <span v-if="a.gratis" class="pill-muted">grátis</span>
                <span v-if="a.ativo" class="pill-success ml-1">ativo</span>
                <span v-else class="pill-muted ml-1">inativo</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <AppUranyxGaveta
      v-model:open="catAberta"
      largura="md"
      :titulo="catEditando ? `Categoria: ${catEditando.nome}` : 'Nova categoria'"
      subtitulo="As categorias aparecem no app pela ordem (menor primeiro)"
    >
      <div v-if="catErros.length" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
        <p v-for="(l, i) in catErros" :key="i">{{ l }}</p>
      </div>
      <form class="space-y-3" @submit.prevent="salvarCategoria">
        <label class="block space-y-1 text-sm">
          <span class="text-muted-foreground">Nome</span>
          <input v-model="catForm.nome" maxlength="60" class="h-9 w-full rounded-md border bg-background px-2 text-sm" placeholder="ex.: Bancos" />
        </label>
        <label class="block space-y-1 text-sm">
          <span class="text-muted-foreground">Ordem</span>
          <input v-model="catForm.ordem" type="number" min="0" max="10000" step="1" class="h-9 w-32 rounded-md border bg-background px-2 text-sm" />
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="catForm.ativo" type="checkbox" class="size-4" /> Ativa (inativa some do app com os apps dela)
        </label>
      </form>
      <template #rodape>
        <Button variant="ghost" class="ml-auto" @click="catAberta = false">Cancelar</Button>
        <Button :disabled="catSalvando" @click="salvarCategoria">{{ catSalvando ? 'Salvando…' : 'Salvar' }}</Button>
      </template>
    </AppUranyxGaveta>

    <AppUranyxAppGaveta
      v-model:open="appAberto"
      :app="appEditando"
      :categorias="categorias"
      :categoria-inicial="categoriaDoNovo"
      @salvo="aoMudarApp"
      @apagado="aoMudarApp"
    />
  </div>
</template>
