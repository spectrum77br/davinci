<script setup lang="ts">
// App Uranyx › Catálogo (06/10/2026). Os produtos que o app mostra a quem
// comprou: vêm do site (nome, foto, linha, SKUs; cópia de hora em hora) e o
// painel cuida dos prazos de garantia, voltagem, data da logo, ativo, foto
// com trava e manuais. "Sincronizar com o site" roda a cópia na hora e
// mostra o relatório. Contrato: app-uranyx/docs/integracao/conteudo-e-catalogo-v1.md.
import { ImageOff, Package, RefreshCw, Search } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  CATEGORIA_LABEL, erroAppUranyx, garantiaTexto, linhasDoErro, normalizarRelatorio, pendenciasRelatorio,
  TABS_APP_URANYX, type Categoria, type ProdutoCatalogo, type RelatorioSync, type RelatorioSyncApi,
} from '~/lib/appUranyx'

// Só admin, e entre os admins só quem está em APP_URANYX_USUARIOS (mesma
// trava do menu e da API: app_uranyx_restrito).
definePageMeta({ middleware: ['admin', 'app-uranyx'] })

const { chamar, ultimoRelatorio, indisponivel } = useAppUranyx()
const toasts = useToasts()

const produtos = ref<ProdutoCatalogo[]>([])
const relatorio = ref<RelatorioSync | null>(null)
const carregando = ref(false)
const carregandoRelatorio = ref(false)
const erro = ref<string | null>(null)
const sincronizando = ref(false)

const busca = ref('')
const categoria = ref<'' | Categoria>('')
const situacao = ref<'' | 'ativos' | 'inativos' | 'fora_do_site' | 'sem_sku'>('')

async function carregarRelatorio() {
  carregandoRelatorio.value = true
  try {
    relatorio.value = await ultimoRelatorio()
  } catch {
    // O relatório é extra: sem ele a lista continua (o erro da API aparece na lista).
  } finally {
    carregandoRelatorio.value = false
  }
}

// `comRelatorio = false` logo depois do "Sincronizar": o relatório na tela já é o novo.
let seq = 0
async function carregar(comRelatorio = true) {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const r = await chamar<ProdutoCatalogo[]>('catalogo')
    if (meu !== seq) return
    produtos.value = r
    if (comRelatorio) void carregarRelatorio()
  } catch (e: any) {
    if (meu !== seq) return
    erro.value = erroAppUranyx(e, 'Não deu para carregar o catálogo').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
onMounted(() => carregar())

async function sincronizar() {
  if (sincronizando.value) return
  sincronizando.value = true
  try {
    const r = normalizarRelatorio(await chamar<RelatorioSyncApi>('catalogo/sincronizar', { method: 'POST' }))
    relatorio.value = r
    if (r && r.ok) {
      const pend = pendenciasRelatorio(r)
      toasts.success('Catálogo copiado do site', [
        `${r.produtos_site} produtos no site: ${r.criados} novos, ${r.atualizados} atualizados, ${r.fora_do_site} fora do site.`,
        pend ? `${pend} ${pend === 1 ? 'item para conferir' : 'itens para conferir'} no relatório.` : 'Nada para conferir.',
      ])
    } else {
      toasts.error('A cópia do site falhou', r?.erro || 'Veja o relatório.')
    }
    await carregar(false)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para sincronizar')
    toasts.error('Não deu para sincronizar', [er.texto, ...linhasDoErro(er)])
  } finally {
    sincronizando.value = false
  }
}

function semAcento(s: string) {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

const filtrados = computed(() => {
  const q = semAcento(busca.value.trim())
  return produtos.value.filter((p) => {
    if (categoria.value && p.categoria !== categoria.value) return false
    if (situacao.value === 'ativos' && !p.ativo) return false
    if (situacao.value === 'inativos' && p.ativo) return false
    if (situacao.value === 'fora_do_site' && !p.fora_do_site) return false
    if (situacao.value === 'sem_sku' && p.skus.length) return false
    if (!q) return true
    return semAcento(p.nome).includes(q) || p.skus.some((s) => s.codigo_base.includes(q))
  })
})

const temFiltro = computed(() => !!(busca.value.trim() || categoria.value || situacao.value))
function limparFiltros() {
  busca.value = ''
  categoria.value = ''
  situacao.value = ''
}

const contagem = computed(() => ({
  total: produtos.value.length,
  ativos: produtos.value.filter((p) => p.ativo).length,
  fora: produtos.value.filter((p) => p.fora_do_site).length,
  semSku: produtos.value.filter((p) => !p.skus.length).length,
}))

// ─── gaveta ─────────────────────────────────────────────────────────────────
const gavetaAberta = ref(false)
const selecionadoId = ref<string | null>(null)
const selecionado = computed(() => produtos.value.find((p) => p.id === selecionadoId.value) ?? null)

function abrir(p: ProdutoCatalogo) {
  selecionadoId.value = p.id
  gavetaAberta.value = true
}

function aoSalvar(p: ProdutoCatalogo) {
  const i = produtos.value.findIndex((x) => x.id === p.id)
  if (i >= 0) produtos.value.splice(i, 1, p)
  else produtos.value.push(p)
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_APP_URANYX" />
    <PageHeader
      title="Catálogo do app"
      description="Os produtos que o app mostra a quem comprou. Nome, foto, linha e SKUs vêm do site; aqui ficam os prazos de garantia, a voltagem, a logo, a foto travada e os manuais."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar()">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" :disabled="sincronizando || !!indisponivel" @click="sincronizar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': sincronizando }" />
          {{ sincronizando ? 'Sincronizando…' : 'Sincronizar com o site' }}
        </Button>
      </template>
    </PageHeader>

    <AppUranyxIndisponivel v-if="indisponivel" :mensagem="indisponivel" :carregando="carregando" @tentar="carregar()" />

    <template v-else>
      <AppUranyxRelatorioSync :relatorio="relatorio" :carregando="carregandoRelatorio" />

      <div class="flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input v-model="busca" class="h-9 w-64 max-w-full rounded-md border bg-background pl-8 pr-3 text-sm" placeholder="Nome ou código (dg053)…" />
        </div>
        <select v-model="categoria" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Categoria">
          <option value="">Categoria: todas</option>
          <option v-for="(label, valor) in CATEGORIA_LABEL" :key="valor" :value="valor">{{ label }}</option>
        </select>
        <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Situação">
          <option value="">Situação: todos</option>
          <option value="ativos">Ativos</option>
          <option value="inativos">Inativos</option>
          <option value="fora_do_site">Fora do site</option>
          <option value="sem_sku">Sem código ligado</option>
        </select>
        <button v-if="temFiltro" class="text-sm text-muted-foreground underline" @click="limparFiltros">limpar filtros</button>
        <span class="ml-auto text-sm text-muted-foreground">
          {{ filtrados.length }} de {{ contagem.total }} · {{ contagem.ativos }} ativos
          <template v-if="contagem.fora"> · {{ contagem.fora }} fora do site</template>
          <template v-if="contagem.semSku"> · {{ contagem.semSku }} sem código</template>
        </span>
      </div>

      <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

      <div class="table-card overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr>
              <th class="w-14">Foto</th>
              <th>Produto</th>
              <th>Categoria</th>
              <th>SKUs</th>
              <th class="whitespace-nowrap">Garantia</th>
              <th>No app</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && !produtos.length">
              <td colspan="6" class="py-10 text-center text-muted-foreground">Carregando…</td>
            </tr>
            <tr v-else-if="!filtrados.length">
              <td colspan="6" class="py-10 text-center text-muted-foreground">
                <Package class="mx-auto mb-2 size-6 opacity-50" />
                {{ temFiltro ? 'Nada com esses filtros.' : 'Nenhum produto ainda. Use "Sincronizar com o site" para trazer o catálogo.' }}
              </td>
            </tr>
            <tr v-for="p in filtrados" :key="p.id" class="cursor-pointer" @click="abrir(p)">
              <td>
                <div class="grid size-10 place-items-center overflow-hidden rounded-md border bg-muted/30">
                  <img v-if="p.foto_url" :src="p.foto_url" :alt="p.nome" class="size-full object-contain" loading="lazy" />
                  <ImageOff v-else class="size-4 text-muted-foreground" aria-hidden="true" />
                </div>
              </td>
              <td>
                <div class="font-medium">{{ p.nome }}</div>
                <div class="mt-0.5 flex flex-wrap gap-1">
                  <span v-if="p.fora_do_site" class="pill-warning whitespace-nowrap" title="Saiu do site; continua no app de quem comprou">fora do site</span>
                  <span v-if="p.foto_travada" class="pill-muted whitespace-nowrap" title="A cópia do site não troca a foto">foto travada</span>
                  <span v-if="p.site_produto_id == null" class="pill-muted whitespace-nowrap" title="Não veio do site">cadastro à mão</span>
                </div>
              </td>
              <td class="whitespace-nowrap">{{ CATEGORIA_LABEL[p.categoria] ?? p.categoria }}</td>
              <td>
                <div class="flex max-w-[22rem] flex-wrap gap-1">
                  <span
                    v-for="s in p.skus"
                    :key="s.codigo_base"
                    :class="s.origem === 'manual' ? 'pill-muted' : 'pill-info'"
                    class="font-mono"
                    :title="`${s.origem === 'manual' ? 'Ligado no painel' : 'Veio do site'}${s.cor ? ` · ${s.cor}` : ''}`"
                  >
                    {{ s.codigo_base }}<span v-if="s.origem === 'manual'" class="font-sans">· manual</span>
                  </span>
                  <span v-if="!p.skus.length" class="text-xs text-amber-700 dark:text-amber-400">nenhum código</span>
                </div>
              </td>
              <td class="whitespace-nowrap">{{ garantiaTexto(p) }}</td>
              <td>
                <span v-if="p.ativo" class="pill-success">ativo</span>
                <span v-else class="pill-muted">inativo</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <AppUranyxProdutoGaveta v-model:open="gavetaAberta" :produto="selecionado" @salvo="aoSalvar" />
  </div>
</template>
