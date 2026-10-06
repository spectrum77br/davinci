<script setup lang="ts">
// App Uranyx › Receitas (06/10/2026): a aba Descobrir do app mostra as
// receitas publicadas a quem tem o eletrodoméstico (os comprados primeiro).
// Foto por upload, vídeo pelo YouTube.
import { ChefHat, ImageOff, Plus, RefreshCw, Search } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  DIFICULDADE_LABEL, erroAppUranyx, TABS_APP_URANYX, type ProdutoCatalogo, type Receita,
} from '~/lib/appUranyx'

definePageMeta({ middleware: ['admin', 'app-uranyx'] })

const { chamar, indisponivel } = useAppUranyx()

const receitas = ref<Receita[]>([])
const eletros = ref<ProdutoCatalogo[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const busca = ref('')
const situacao = ref<'' | 'publicadas' | 'rascunhos'>('')

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const [r, e] = await Promise.all([
      chamar<Receita[]>('receitas'),
      chamar<ProdutoCatalogo[]>('catalogo', { query: { categoria: 'eletrodomestico' } }),
    ])
    if (meu !== seq) return
    receitas.value = r
    eletros.value = e
  } catch (e: any) {
    if (meu !== seq) return
    erro.value = erroAppUranyx(e, 'Não deu para carregar as receitas').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
onMounted(carregar)

function semAcento(s: string) {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

const filtradas = computed(() => {
  const q = semAcento(busca.value.trim())
  return receitas.value.filter((r) => {
    if (situacao.value === 'publicadas' && !r.publicada) return false
    if (situacao.value === 'rascunhos' && r.publicada) return false
    if (!q) return true
    return semAcento(r.titulo).includes(q)
      || r.tags.some((t) => semAcento(t).includes(q))
      || r.ingredientes.some((i) => semAcento(i).includes(q))
  })
})

const gavetaAberta = ref(false)
const selecionada = ref<Receita | null>(null)

function nova() {
  selecionada.value = null
  gavetaAberta.value = true
}

function abrir(r: Receita) {
  selecionada.value = r
  gavetaAberta.value = true
}

function aoSalvar(r: Receita) {
  const i = receitas.value.findIndex((x) => x.id === r.id)
  if (i >= 0) receitas.value.splice(i, 1, r)
  else receitas.value = [...receitas.value, r].sort((a, b) => a.titulo.localeCompare(b.titulo, 'pt-BR'))
}

function aoApagar(id: string) {
  receitas.value = receitas.value.filter((r) => r.id !== id)
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_APP_URANYX" />
    <PageHeader
      title="Receitas"
      description="Receitas da aba Descobrir do app, para quem tem o eletrodoméstico. Só as publicadas aparecem."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" :disabled="!!indisponivel" @click="nova">
          <Plus class="mr-1 size-4" /> Nova receita
        </Button>
      </template>
    </PageHeader>

    <AppUranyxIndisponivel v-if="indisponivel" :mensagem="indisponivel" :carregando="carregando" @tentar="carregar" />

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input v-model="busca" class="h-9 w-64 max-w-full rounded-md border bg-background pl-8 pr-3 text-sm" placeholder="Título, ingrediente ou tag…" />
        </div>
        <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Situação">
          <option value="">Todas</option>
          <option value="publicadas">Publicadas</option>
          <option value="rascunhos">Rascunhos</option>
        </select>
        <span class="ml-auto text-sm text-muted-foreground">{{ filtradas.length }} de {{ receitas.length }}</span>
      </div>

      <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

      <div class="table-card overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr>
              <th class="w-16">Foto</th>
              <th>Receita</th>
              <th>Eletrodomésticos</th>
              <th class="whitespace-nowrap">Tempo</th>
              <th>Dificuldade</th>
              <th>No app</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && !receitas.length">
              <td colspan="6" class="py-10 text-center text-muted-foreground">Carregando…</td>
            </tr>
            <tr v-else-if="!filtradas.length">
              <td colspan="6" class="py-10 text-center text-muted-foreground">
                <ChefHat class="mx-auto mb-2 size-6 opacity-50" />
                {{ receitas.length ? 'Nada com esses filtros.' : 'Nenhuma receita ainda. Use "Nova receita".' }}
              </td>
            </tr>
            <tr v-for="r in filtradas" :key="r.id" class="cursor-pointer" @click="abrir(r)">
              <td>
                <div class="grid h-10 w-14 place-items-center overflow-hidden rounded-md border bg-muted/30">
                  <img v-if="r.foto_url" :src="r.foto_url" :alt="r.titulo" class="size-full object-cover" loading="lazy" />
                  <ImageOff v-else class="size-4 text-muted-foreground" aria-hidden="true" />
                </div>
              </td>
              <td>
                <div class="font-medium">{{ r.titulo }}</div>
                <div v-if="r.tags.length" class="mt-0.5 flex flex-wrap gap-1">
                  <span v-for="t in r.tags" :key="t" class="pill-muted">{{ t }}</span>
                </div>
              </td>
              <td class="text-muted-foreground">{{ r.eletrodomesticos.map((e) => e.nome).join(', ') || '—' }}</td>
              <td class="whitespace-nowrap">{{ r.tempo_min }} min · {{ r.rendimento }}</td>
              <td>{{ DIFICULDADE_LABEL[r.dificuldade] ?? r.dificuldade }}</td>
              <td>
                <span v-if="r.publicada" class="pill-success">publicada</span>
                <span v-else class="pill-muted">rascunho</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <AppUranyxReceitaGaveta
      v-model:open="gavetaAberta"
      :receita="selecionada"
      :eletros="eletros"
      @salva="aoSalvar"
      @apagada="aoApagar"
    />
  </div>
</template>
