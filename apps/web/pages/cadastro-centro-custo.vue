<script setup lang="ts">
// Cadastros › Centro de custo (M-76, Harry 25/11/2026): a aba logo depois de Produtos, com a
// mesma tela de Cadastros › Produtos sem a coluna Tipo e sem "Novo tipo" — Nome | Ações.
// Celular, Eletro e Mala NÃO são uma lista paralela: são as raízes de Cadastros › Segmentos
// (o Catálogo não é centro de custo). Esta lista é a fonte do menu "Centro de custo" das
// Categorias de venda (Cadastros › Produtos) e, depois, do cadastro do produto no Produtos 2.
// Quem vê: quem vê Cadastros › Produtos (`cadastro_produtos`). Incluir, renomear e excluir: só
// administrador (o servidor confere; aqui os botões nem aparecem para os outros).
// Regras do servidor (routers/centros_custo.py): nome até 60 letras, sem repetir; Celular,
// Eletro e Mala não mudam de nome e não saem; os outros só saem sem uso (a lixeira pergunta
// antes e mostra quantos usam). Centro novo não vira botão nas outras telas.
import { TABS_CADASTROS } from '~/lib/navGroups'
import { AlertCircle, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'cadastro_produtos', action: 'view' },
})

type Centro = { id: string; nome: string; fixo: boolean; segment_id: string | null }
type Uso = {
  categorias: number
  contas: number
  lojas: number
  produtos: number
  bloqueia: boolean
  mensagem: string | null
}

// A mesma frase do servidor (services/centros_custo.MSG_FIXO_NOME).
const MSG_FIXO = 'Celular, Eletro e Mala não mudam de nome: o sistema procura esses nomes em outras telas.'

const { api } = useApi()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

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
    centros.value = await api<Centro[]>('/api/centros-custo')
  } catch (e: any) {
    error.value = msgErro(e)
  } finally {
    loading.value = false
  }
}

await load()

// ---------------------------------------------------------------- + Novo cadastro / lápis
const cadAberto = ref(false)
const cadEditando = ref<Centro | null>(null)
const cadNome = ref('')
const cadErro = ref<string | null>(null)
const cadSalvando = ref(false)

function abrirCadastro(c: Centro | null = null) {
  cadAberto.value = true
  cadEditando.value = c
  cadNome.value = c?.nome ?? ''
  cadErro.value = null
}

function fecharCadastro() {
  cadAberto.value = false
  cadEditando.value = null
}

async function salvarCadastro() {
  if (!cadNome.value.trim()) {
    cadErro.value = 'Digite o nome.'
    return
  }
  cadSalvando.value = true
  cadErro.value = null
  try {
    if (cadEditando.value) {
      await api(`/api/centros-custo/${cadEditando.value.id}`, { method: 'PATCH', body: { nome: cadNome.value } })
    } else {
      await api('/api/centros-custo', { method: 'POST', body: { nome: cadNome.value } })
    }
    fecharCadastro()
    await load()
  } catch (e: any) {
    cadErro.value = msgErro(e)
  } finally {
    cadSalvando.value = false
  }
}

// ---------------------------------------------------------------- lixeira (sempre com confirmação)
const excluindo = ref<Centro | null>(null)
const excluirUso = ref<Uso | null>(null)
const excluirErro = ref<string | null>(null)
const excluirCarregando = ref(false)

async function pedirExclusao(c: Centro) {
  excluindo.value = c
  excluirUso.value = null
  excluirErro.value = null
  excluirCarregando.value = true
  try {
    excluirUso.value = await api<Uso>(`/api/centros-custo/${c.id}/uso`)
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
    await api(`/api/centros-custo/${excluindo.value.id}`, { method: 'DELETE' })
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
    <PageHeader title="Centro de custo">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="isAdmin" size="sm" data-acao="novo-cadastro" @click="abrirCadastro()">
          <Plus class="size-4 mr-1" /> Novo cadastro
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <!-- As letras de Cadastros › Produtos (as da Tabela de Preços › Contas): cabeçalho 11px
         semibold, células 12px, bordas nas células e linhas da mesma altura (h-8). -->
    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)] max-w-3xl">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border">Nome</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-20">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !centros.length">
            <td colspan="2" class="text-center py-6 text-xs text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!centros.length">
            <td colspan="2" class="text-center py-6 text-xs text-muted-foreground">Nenhum centro de custo.</td>
          </tr>
          <tr v-for="c in centros" :key="c.id" class="h-8 hover:bg-accent/30" :data-centro="c.nome">
            <td class="border border-border px-2 py-1 text-xs font-medium">{{ c.nome }}</td>
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

    <!-- + Novo cadastro e lápis: só o nome. Celular, Eletro e Mala: o lápis explica por que
         não mudam de nome (o campo fica travado). -->
    <div
      v-if="cadAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="cadastro"
      @click.self="fecharCadastro"
    >
      <div class="w-full max-w-sm rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">{{ cadEditando ? 'Editar centro de custo' : 'Novo centro de custo' }}</h3>
          <button class="p-1 hover:bg-muted rounded" title="Fechar" @click="fecharCadastro"><X class="h-4 w-4" /></button>
        </div>
        <div v-if="cadErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ cadErro }}
        </div>
        <p
          v-if="cadEditando?.fixo"
          class="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/15 px-3 py-2 text-xs text-amber-800 dark:text-amber-300"
          data-aviso="fixo"
        >
          {{ MSG_FIXO }}
        </p>
        <label class="block text-xs text-muted-foreground">
          Nome
          <input
            v-model="cadNome"
            type="text"
            maxlength="60"
            placeholder="ex.: Brinquedos"
            :disabled="cadEditando?.fixo"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground disabled:opacity-60"
            data-campo="cadastro-nome"
            @keydown.enter="salvarCadastro"
          />
        </label>
        <div class="flex justify-end gap-2">
          <Button size="sm" variant="ghost" @click="fecharCadastro">{{ cadEditando?.fixo ? 'Fechar' : 'Cancelar' }}</Button>
          <Button v-if="!cadEditando?.fixo" size="sm" :disabled="cadSalvando" data-acao="salvar-cadastro" @click="salvarCadastro">
            <Loader2 v-if="cadSalvando" class="size-4 mr-1 animate-spin" /> Salvar
          </Button>
        </div>
      </div>
    </div>

    <!-- Lixeira: sempre pergunta antes. Em uso (ou Celular, Eletro e Mala): mostra quantos usam
         e não tem o botão Excluir. -->
    <div
      v-if="excluindo"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="excluir"
      @click.self="fecharExclusao"
    >
      <div class="w-full max-w-sm rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <h3 class="font-semibold text-sm">Excluir “{{ excluindo.nome }}”?</h3>
        <p v-if="excluirCarregando && !excluirUso" class="text-xs text-muted-foreground">
          <Loader2 class="inline h-3.5 w-3.5 animate-spin" /> conferindo quem usa…
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
        <p v-else-if="excluirUso" class="text-xs text-muted-foreground" data-aviso="livre">
          Ninguém usa este centro de custo.
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
