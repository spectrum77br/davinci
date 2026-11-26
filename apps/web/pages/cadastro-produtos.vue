<script setup lang="ts">
// Cadastros › Produtos (M-29, Harry 24/11/2026): a lista oficial de categorias e
// tags de envio — tabela Tipo | Nome. Os filtros Tags e Categoria do Produtos 2
// (M-30) leem esta lista (GET /api/produto-cadastros/listas).
// Quem vê: permissão `cadastro_produtos` (view; a migration 0508 deu a quem já via
// alguma aba de Cadastros). Criar, editar e excluir: só
// administrador (o servidor confere; aqui os botões nem aparecem para os outros).
// Regras do servidor (routers/produto_cadastros.py): nome repetido no tipo é
// recusado; Categoria que ainda tem produto (base do ERP) não sai nem muda de
// nome; Tag de envio em uso pede confirmação com a contagem.
// M-75 (Harry 25/11/2026): tipo "Categoria de venda" = os grupos de margem/frete da aba Contas.
// NÃO é cadastro novo: são os segmentos filhos de Cadastros › Segmentos (o mesmo dado), com a
// raiz como centro de custo. Vão pelas rotas /categorias-venda; a nova entra no fim do
// centro de custo (até 5), só a última sai e "Apple"/"Queima de estoque" não trocam de nome
// (o servidor confere e manda a frase). O menu "Centro de custo" do Novo cadastro vem de um
// lugar só: `centros_custo` da lista (services/categorias_venda.centros_do_menu no servidor).
// M-76: esse menu é a lista de Cadastros › Centro de custo. Centro novo aparece, mas ainda não
// recebe categoria de venda: ao escolher, a janela avisa e o Salvar fica travado (o servidor
// recusa com a mesma frase).
// M-84 (Harry 26/11/2026): a coluna "Centro de custo" saiu da tabela (fica Tipo | Nome | Ações);
// o centro continua nos dados, no Novo cadastro, no lápis e nos avisos.
import { TABS_CADASTROS } from '~/lib/navGroups'
import { AlertCircle, Check, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'cadastro_produtos', action: 'view' },
})

type Tipo = { id: string; nome: string; chave: string | null }
type Cadastro = {
  id: string
  tipo_id: string
  tipo_nome: string
  tipo_chave: string | null
  nome: string
  centro_custo?: string | null
  ativa?: boolean
}
type Centro = { id: string; nome: string; aceita_categoria?: boolean }
type Uso = { regra: string | null; produtos: number | null; bloqueia: boolean; mensagem: string | null }

const CHAVE_VENDA = 'categoria_venda'
// A mesma frase do servidor (services/categorias_venda.MSG_SO_FIXOS).
const MSG_SO_FIXOS = 'Categoria de venda só em Celular, Eletro e Mala por enquanto.'
const ehVenda = (c: { tipo_chave?: string | null; chave?: string | null } | null | undefined) =>
  !!c && (c.tipo_chave === CHAVE_VENDA || c.chave === CHAVE_VENDA)

const { api } = useApi()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

const tipos = ref<Tipo[]>([])
const cadastros = ref<Cadastro[]>([])
const centros = ref<Centro[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// A mensagem que o servidor manda (detail.mensagem) é frase de gente; o resto vira o código.
function msgErro(e: any): string {
  const d = e?.data?.detail
  if (d && typeof d === 'object' && d.mensagem) return d.mensagem
  if (typeof d === 'string') return d
  return d?.code || e?.message || 'erro'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const r = await api<{ tipos: Tipo[]; cadastros: Cadastro[]; centros_custo?: Centro[] }>('/api/produto-cadastros')
    tipos.value = r.tipos
    cadastros.value = r.cadastros
    centros.value = r.centros_custo ?? []
  } catch (e: any) {
    error.value = msgErro(e)
  } finally {
    loading.value = false
  }
}

await load()

// ---------------------------------------------------------------- + Novo tipo / renomear tipo
const tipoAberto = ref(false)
const tipoNome = ref('')
const tipoEditando = ref<string | null>(null)
const tipoEditNome = ref('')
const tipoErro = ref<string | null>(null)
const tipoSalvando = ref(false)

function abrirTipo() {
  tipoAberto.value = true
  tipoNome.value = ''
  tipoEditando.value = null
  tipoErro.value = null
}

async function criarTipo() {
  if (!tipoNome.value.trim()) return
  tipoSalvando.value = true
  tipoErro.value = null
  try {
    await api('/api/produto-cadastros/tipos', { method: 'POST', body: { nome: tipoNome.value } })
    tipoNome.value = ''
    await load()
  } catch (e: any) {
    tipoErro.value = msgErro(e)
  } finally {
    tipoSalvando.value = false
  }
}

function editarTipo(t: Tipo) {
  tipoEditando.value = t.id
  tipoEditNome.value = t.nome
  tipoErro.value = null
}

async function salvarTipo(t: Tipo) {
  if (!tipoEditNome.value.trim() || tipoEditNome.value === t.nome) {
    tipoEditando.value = null
    return
  }
  tipoSalvando.value = true
  tipoErro.value = null
  try {
    await api(`/api/produto-cadastros/tipos/${t.id}`, { method: 'PATCH', body: { nome: tipoEditNome.value } })
    tipoEditando.value = null
    await load()
  } catch (e: any) {
    tipoErro.value = msgErro(e)
  } finally {
    tipoSalvando.value = false
  }
}

// ---------------------------------------------------------------- + Novo cadastro / lápis
const cadAberto = ref(false)
const cadEditando = ref<Cadastro | null>(null)
const cadTipo = ref('')
const cadNome = ref('')
const cadCentro = ref('') // Categoria de venda nova: o id do centro de custo escolhido no menu
const cadErro = ref<string | null>(null)
const cadAviso = ref<string | null>(null) // tag em uso: confirmar a troca
const cadSalvando = ref(false)

// O tipo escolhido na janela é Categoria de venda?
const cadEhVenda = computed(() => ehVenda(tipos.value.find((t) => t.id === cadTipo.value)))
// M-76: o centro escolhido no Novo cadastro ainda não recebe categoria de venda (centro novo).
const cadCentroSemCategoria = computed(
  () =>
    cadEhVenda.value &&
    !cadEditando.value &&
    centros.value.find((c) => c.id === cadCentro.value)?.aceita_categoria === false,
)
// Os tipos do menu: Categoria de venda não troca com os outros (é outro dado) — no lápis de
// uma categoria de venda o menu fica travado; no lápis das outras ela não aparece.
const tiposDoMenu = computed(() =>
  cadEditando.value && !ehVenda(cadEditando.value) ? tipos.value.filter((t) => !ehVenda(t)) : tipos.value,
)

function abrirCadastro(c: Cadastro | null = null) {
  cadAberto.value = true
  cadEditando.value = c
  cadTipo.value = c?.tipo_id ?? tipos.value[0]?.id ?? ''
  cadNome.value = c?.nome ?? ''
  // No lápis o centro de custo não muda (só aparece); no Novo cadastro a pessoa escolhe.
  cadCentro.value = ''
  cadErro.value = null
  cadAviso.value = null
}

function fecharCadastro() {
  cadAberto.value = false
  cadEditando.value = null
}

async function salvarCadastro(confirmar = false) {
  if (!cadTipo.value || !cadNome.value.trim()) {
    cadErro.value = 'Escolha o tipo e digite o nome.'
    return
  }
  if (cadEhVenda.value && !cadEditando.value && !cadCentro.value) {
    cadErro.value = 'Escolha o centro de custo.'
    return
  }
  if (cadCentroSemCategoria.value) {
    cadErro.value = MSG_SO_FIXOS
    return
  }
  cadSalvando.value = true
  cadErro.value = null
  try {
    if (cadEhVenda.value) {
      // M-75: o segmento filho (mesmo dado da aba Segmentos); o lápis só troca o nome.
      if (cadEditando.value) {
        await api(`/api/produto-cadastros/categorias-venda/${cadEditando.value.id}`, {
          method: 'PATCH',
          body: { nome: cadNome.value },
        })
      } else {
        await api('/api/produto-cadastros/categorias-venda', {
          method: 'POST',
          body: { centro_custo_id: cadCentro.value, nome: cadNome.value },
        })
      }
    } else if (cadEditando.value) {
      await api(`/api/produto-cadastros/${cadEditando.value.id}`, {
        method: 'PATCH',
        body: { tipo_id: cadTipo.value, nome: cadNome.value, confirmar },
      })
    } else {
      await api('/api/produto-cadastros', { method: 'POST', body: { tipo_id: cadTipo.value, nome: cadNome.value } })
    }
    fecharCadastro()
    await load()
  } catch (e: any) {
    const d = e?.data?.detail
    if (d?.code === 'tag_em_uso' && !confirmar) {
      cadAviso.value = `${d.mensagem || 'Há produtos com esta tag.'} Trocar mesmo assim?`
    } else {
      cadErro.value = msgErro(e)
    }
  } finally {
    cadSalvando.value = false
  }
}

// ---------------------------------------------------------------- lixeira (sempre com confirmação)
const excluindo = ref<Cadastro | null>(null)
const excluirUso = ref<Uso | null>(null)
const excluirErro = ref<string | null>(null)
const excluirCarregando = ref(false)

// Categoria de venda (M-75) tem rota própria: a linha é o segmento filho.
function rotaDe(c: Cadastro): string {
  return ehVenda(c) ? `/api/produto-cadastros/categorias-venda/${c.id}` : `/api/produto-cadastros/${c.id}`
}

async function pedirExclusao(c: Cadastro) {
  excluindo.value = c
  excluirUso.value = null
  excluirErro.value = null
  excluirCarregando.value = true
  try {
    excluirUso.value = await api<Uso>(`${rotaDe(c)}/uso`)
  } catch (e: any) {
    excluirErro.value = msgErro(e)
  } finally {
    excluirCarregando.value = false
  }
}

function fecharExclusao() {
  excluindo.value = null
  excluirUso.value = null
}

async function confirmarExclusao() {
  if (!excluindo.value) return
  excluirCarregando.value = true
  excluirErro.value = null
  try {
    // A pessoa já viu a contagem (tag em uso) nesta janela: vai com confirmar.
    // Categoria de venda não tem confirmação: em uso, o servidor recusa.
    const url = ehVenda(excluindo.value) ? rotaDe(excluindo.value) : `${rotaDe(excluindo.value)}?confirmar=true`
    await api(url, { method: 'DELETE' })
    fecharExclusao()
    await load()
  } catch (e: any) {
    excluirErro.value = msgErro(e)
  } finally {
    excluirCarregando.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader title="Produtos">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="isAdmin" size="sm" variant="outline" data-acao="novo-tipo" @click="abrirTipo">
          <Plus class="size-4 mr-1" /> Novo tipo
        </Button>
        <Button v-if="isAdmin" size="sm" data-acao="novo-cadastro" @click="abrirCadastro()">
          <Plus class="size-4 mr-1" /> Novo cadastro
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <!-- Letras da Tabela de Preços › Contas: cabeçalho 11px semibold, células 12px,
         bordas nas células e linhas da mesma altura (h-8). -->
    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)] max-w-3xl">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-48">Tipo</th>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border">Nome</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-20">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !cadastros.length">
            <td colspan="3" class="text-center py-6 text-xs text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!cadastros.length">
            <td colspan="3" class="text-center py-6 text-xs text-muted-foreground">Nenhum cadastro.</td>
          </tr>
          <tr v-for="c in cadastros" :key="c.id" class="h-8 hover:bg-accent/30" :data-cadastro="c.nome">
            <td class="border border-border px-2 py-1 text-xs">{{ c.tipo_nome }}</td>
            <td
              class="border border-border px-2 py-1 text-xs font-medium"
              :class="{ 'text-muted-foreground': c.ativa === false }"
            >
              {{ c.nome }}
              <span
                v-if="c.ativa === false"
                class="ml-1 rounded border border-border px-1 text-[10px] font-normal text-muted-foreground"
                data-marca="inativa"
              >inativa</span>
            </td>
            <td class="border border-border px-1 py-1 text-center">
              <div v-if="isAdmin" class="flex gap-0.5 justify-center">
                <button
                  class="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded"
                  title="Editar"
                  data-acao="editar"
                  @click="abrirCadastro(c)"
                >
                  <Pencil class="h-3.5 w-3.5" />
                </button>
                <button
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  title="Excluir"
                  data-acao="excluir"
                  @click="pedirExclusao(c)"
                >
                  <Trash2 class="h-3.5 w-3.5" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- + Novo tipo: cadastra o tipo e renomeia os que existem (lápis). -->
    <div
      v-if="tipoAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="tipos"
      @click.self="tipoAberto = false"
    >
      <div class="w-full max-w-sm max-h-[90vh] overflow-y-auto rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">Novo tipo</h3>
          <button class="p-1 hover:bg-muted rounded" title="Fechar" @click="tipoAberto = false"><X class="h-4 w-4" /></button>
        </div>
        <div v-if="tipoErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ tipoErro }}
        </div>
        <div class="flex gap-2">
          <input
            v-model="tipoNome"
            type="text"
            maxlength="60"
            placeholder="Nome do tipo (ex.: Cor)"
            class="flex-1 text-sm border rounded px-2 py-1 bg-background"
            data-campo="tipo-nome"
            @keydown.enter="criarTipo"
          />
          <Button size="sm" :disabled="tipoSalvando || !tipoNome.trim()" @click="criarTipo">
            <Loader2 v-if="tipoSalvando" class="size-4 mr-1 animate-spin" /> Cadastrar
          </Button>
        </div>
        <div class="text-xs font-medium">Tipos cadastrados</div>
        <ul class="divide-y divide-border rounded border border-border">
          <li v-for="t in tipos" :key="t.id" class="flex items-center gap-2 px-2 h-8 text-xs" :data-tipo="t.nome">
            <template v-if="tipoEditando === t.id">
              <input
                v-model="tipoEditNome"
                type="text"
                maxlength="60"
                class="flex-1 text-xs border rounded px-1.5 py-0.5 bg-background"
                data-campo="tipo-renomear"
                @keydown.enter="salvarTipo(t)"
                @keydown.escape="tipoEditando = null"
              />
              <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" title="Salvar" @click="salvarTipo(t)"><Check class="h-3.5 w-3.5" /></button>
              <button class="p-1 text-destructive hover:bg-destructive/10 rounded" title="Cancelar" @click="tipoEditando = null"><X class="h-3.5 w-3.5" /></button>
            </template>
            <template v-else>
              <span class="flex-1">{{ t.nome }}</span>
              <!-- Categoria de venda (M-75) não é tipo desta tabela: não renomeia aqui. -->
              <button v-if="!ehVenda(t)" class="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded" title="Renomear" data-acao="renomear-tipo" @click="editarTipo(t)">
                <Pencil class="h-3.5 w-3.5" />
              </button>
            </template>
          </li>
        </ul>
      </div>
    </div>

    <!-- + Novo cadastro e lápis: Tipo (lista dos tipos) e Nome. -->
    <div
      v-if="cadAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="cadastro"
      @click.self="fecharCadastro"
    >
      <div class="w-full max-w-sm rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">{{ cadEditando ? 'Editar cadastro' : 'Novo cadastro' }}</h3>
          <button class="p-1 hover:bg-muted rounded" title="Fechar" @click="fecharCadastro"><X class="h-4 w-4" /></button>
        </div>
        <div v-if="cadErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ cadErro }}
        </div>
        <label class="block text-xs text-muted-foreground">
          Tipo
          <select
            v-model="cadTipo"
            :disabled="ehVenda(cadEditando)"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground disabled:opacity-60"
            data-campo="cadastro-tipo"
          >
            <option v-for="t in tiposDoMenu" :key="t.id" :value="t.id">{{ t.nome }}</option>
          </select>
        </label>
        <!-- Categoria de venda: o centro de custo. No lápis só o nome muda: o centro aparece
             travado (pode ser um que não está no menu, como o Catálogo). -->
        <label v-if="cadEhVenda && cadEditando" class="block text-xs text-muted-foreground">
          Centro de custo
          <input
            :value="cadEditando.centro_custo || '—'"
            type="text"
            disabled
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground disabled:opacity-60"
            data-campo="cadastro-centro-fixo"
          />
        </label>
        <label v-else-if="cadEhVenda" class="block text-xs text-muted-foreground">
          Centro de custo
          <select
            v-model="cadCentro"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
            data-campo="cadastro-centro"
          >
            <option value="" disabled>Escolha…</option>
            <option v-for="r in centros" :key="r.id" :value="r.id">{{ r.nome }}</option>
          </select>
        </label>
        <p
          v-if="cadCentroSemCategoria"
          class="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/15 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"
          data-aviso="centro-sem-categoria"
        >
          {{ MSG_SO_FIXOS }}
        </p>
        <label class="block text-xs text-muted-foreground">
          Nome
          <input
            v-model="cadNome"
            type="text"
            maxlength="60"
            :placeholder="cadEhVenda ? 'ex.: Acessórios' : 'ex.: .sp ou Celular'"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
            data-campo="cadastro-nome"
            @keydown.enter="salvarCadastro(false)"
          />
        </label>
        <div v-if="cadAviso" class="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/15 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 space-y-2">
          <p>{{ cadAviso }}</p>
          <div class="flex justify-end gap-2">
            <Button size="sm" variant="ghost" @click="cadAviso = null">Não</Button>
            <Button size="sm" :disabled="cadSalvando" data-acao="confirmar-troca" @click="salvarCadastro(true)">Sim, trocar</Button>
          </div>
        </div>
        <div v-else class="flex justify-end gap-2">
          <Button size="sm" variant="ghost" @click="fecharCadastro">Cancelar</Button>
          <Button size="sm" :disabled="cadSalvando || cadCentroSemCategoria" data-acao="salvar-cadastro" @click="salvarCadastro(false)">
            <Loader2 v-if="cadSalvando" class="size-4 mr-1 animate-spin" /> Salvar
          </Button>
        </div>
      </div>
    </div>

    <!-- Lixeira: sempre pergunta antes. Categoria com produto: só avisa (não exclui). -->
    <div
      v-if="excluindo"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="excluir"
      @click.self="fecharExclusao"
    >
      <div class="w-full max-w-sm rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <h3 class="font-semibold text-sm">Excluir “{{ excluindo.nome }}” ({{ excluindo.tipo_nome }})?</h3>
        <p v-if="excluirCarregando && !excluirUso" class="text-xs text-muted-foreground">
          <Loader2 class="inline h-3.5 w-3.5 animate-spin" /> conferindo quantos produtos usam…
        </p>
        <div v-if="excluirErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ excluirErro }}
        </div>
        <div
          v-if="excluirUso?.bloqueia"
          class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          data-aviso="bloqueada"
        >
          {{ excluirUso.mensagem }}
        </div>
        <p v-else-if="excluirUso?.mensagem" class="text-xs text-amber-800 dark:text-amber-300" data-aviso="contagem">
          {{ excluirUso.mensagem }} Excluir mesmo assim?
        </p>
        <div class="flex justify-end gap-2">
          <Button size="sm" variant="ghost" @click="fecharExclusao">{{ excluirUso?.bloqueia ? 'Fechar' : 'Cancelar' }}</Button>
          <Button
            v-if="excluirUso && !excluirUso.bloqueia"
            size="sm"
            variant="destructive"
            :disabled="excluirCarregando"
            data-acao="confirmar-exclusao"
            @click="confirmarExclusao"
          >
            Excluir
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
