<script setup lang="ts">
// Cartão "Tomadores" (aba Notas fixas e tomadores): quem recebe a nota. Do
// grupo (CNPJ e razão social vêm do cadastro da empresa) ou de fora (CNPJ/CPF,
// nome e, se quiser, endereço). Busca, tipo, contato, se o endereço vai na
// nota, em quais notas fixas é usado, desativar e reativar. O formulário é a
// gaveta NfseTomadoresSheet, aberta pela página via tela.abrirTomador.
import { computed, ref } from 'vue'
import { Ban, FilePlus2, Plus, RotateCcw, Search, SearchX, Users, X } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  enderecoTomador, erroApi, fmtDoc, plural, soDigitos, tomadorParaForm, useNfseTela, type MenuItem, type Modelo,
  type Tomador,
} from '~/lib/nfse'

const tela = useNfseTela()
const { canEdit, canDelete, carregado } = tela
const { api } = useApi()
const toasts = useToasts()

const busca = ref('')
const mostrarDesativados = ref(false)

function nome(t: Tomador): string {
  return t.nome_nota || t.nome || '(sem nome)'
}

// (11) 91234-5678 / (11) 1234-5678
function fmtFone(v: string | null | undefined): string {
  const d = soDigitos(v)
  if (d.length === 11) return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`
  if (d.length === 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`
  return v || ''
}

function contato(t: Tomador): string {
  return [t.email, fmtFone(t.fone)].filter(Boolean).join(' · ')
}

// Notas fixas ATIVAS que apontam para cada tomador.
const usoPorTomador = computed(() => {
  const mapa = new Map<string, Modelo[]>()
  for (const m of tela.modelos.value) {
    if (!m.ativo) continue
    const l = mapa.get(m.tomador_id) ?? []
    l.push(m)
    mapa.set(m.tomador_id, l)
  }
  return mapa
})

function usadoEm(t: Tomador): Modelo[] {
  return usoPorTomador.value.get(t.id) ?? []
}

function dicaUso(t: Tomador): string {
  const l = usadoEm(t)
  return `Notas fixas ativas com este tomador:\n${l.map((m) => `• ${m.nome}`).join('\n')}`
}

const ativos = computed(() => tela.tomadores.value.filter((t) => t.ativo))
const qtdDesativados = computed(() => tela.tomadores.value.length - ativos.value.length)

const termo = computed(() => busca.value.trim().toLowerCase())

function bate(t: Tomador): boolean {
  const q = termo.value
  if (!q) return true
  if ([nome(t), t.nome, t.email].join(' ').toLowerCase().includes(q)) return true
  const d = soDigitos(q)
  return d.length >= 3 && soDigitos(t.documento_nota || t.documento).includes(d)
}

const lista = computed(() =>
  tela.tomadores.value
    .filter((t) => (mostrarDesativados.value || t.ativo) && bate(t))
    .sort((a, b) => Number(b.ativo) - Number(a.ativo) || nome(a).localeCompare(nome(b), 'pt-BR')),
)

function endereco(t: Tomador) {
  const e = enderecoTomador(t)
  if (e.situacao === 'completo') return { classe: 'pill-success', texto: 'completo', dica: 'Endereço completo: vai na nota.' }
  if (e.situacao === 'incompleto') {
    return {
      classe: 'pill-warning',
      texto: 'incompleto',
      dica: `Endereço incompleto não vai na nota. Falta: ${e.faltando.join(', ')}.`,
    }
  }
  return { classe: 'pill-muted', texto: 'sem endereço', dica: 'Sem endereço. A nota sai só com CNPJ/CPF e nome.' }
}

// --- Ações ------------------------------------------------------------------

function novo() {
  tela.abrirTomador()
}

function abrir(t: Tomador) {
  tela.abrirTomador({ tomador: t })
}

async function desativar(t: Tomador) {
  const uso = usadoEm(t)
  const ok = await tela.confirmar({
    tom: 'perigo',
    titulo: `Desativar o tomador ${nome(t)}?`,
    texto: 'Ele some das listas de escolha. As notas que já saíram continuam em Notas enviadas.',
    linhas: uso.length
      ? [
          `Está em ${plural(uso.length, 'nota fixa ativa', 'notas fixas ativas')}: ${uso.map((m) => m.nome).join(', ')}. Troque o tomador nelas ou desative-as também.`,
        ]
      : undefined,
    botao: 'Desativar tomador',
  })
  if (!ok) return
  try {
    await api(`/api/nfse/tomadores/${t.id}`, { method: 'DELETE' })
    toasts.success('Tomador desativado')
    await tela.recarregar()
  } catch (e) {
    toasts.error('Não deu para desativar o tomador', erroApi(e))
  }
}

const reativando = ref<string | null>(null)

async function reativar(t: Tomador) {
  if (reativando.value) return
  reativando.value = t.id
  try {
    const { id: _id, ...corpo } = tomadorParaForm(t)
    await api(`/api/nfse/tomadores/${t.id}`, { method: 'PATCH', body: { ...corpo, ativo: true } })
    toasts.success('Tomador reativado')
    await tela.recarregar()
  } catch (e) {
    toasts.error('Não deu para reativar o tomador', erroApi(e))
  } finally {
    reativando.value = null
  }
}

function itensMenu(t: Tomador): MenuItem[] {
  const itens: MenuItem[] = []
  if (canEdit.value && t.ativo) itens.push({ id: 'nova_nota', rotulo: 'Nova nota fixa para este tomador', icone: FilePlus2 })
  if (t.ativo) {
    if (canDelete.value) itens.push({ id: 'desativar', rotulo: 'Desativar…', icone: Ban, perigo: true, separar: itens.length > 0 })
  } else if (canEdit.value) {
    itens.push({ id: 'reativar', rotulo: 'Reativar', icone: RotateCcw, disabled: reativando.value === t.id })
  }
  return itens
}

function escolher(t: Tomador, id: string) {
  if (id === 'nova_nota') tela.abrirModelo({ preset: { tomador_id: t.id } })
  else if (id === 'desativar') desativar(t)
  else if (id === 'reativar') reativar(t)
}

function limparBusca() {
  busca.value = ''
}
</script>

<template>
  <div class="overflow-hidden rounded-xl border bg-card">
    <!-- Cabeçalho -->
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-3">
      <div class="flex min-w-0 items-center gap-2">
        <Users class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h2 class="text-sm font-semibold">Tomadores</h2>
        <span v-if="carregado" class="rounded bg-muted px-1.5 text-[11px] tabular-nums text-muted-foreground">
          {{ plural(ativos.length, 'ativo', 'ativos') }}
        </span>
      </div>
      <p class="w-full text-xs text-muted-foreground sm:w-auto">
        quem recebe a nota: empresa do grupo ou cliente de fora
      </p>
      <div class="flex w-full flex-wrap items-center gap-x-3 gap-y-2 sm:ml-auto sm:w-auto">
        <div class="relative w-full sm:w-64">
          <Search class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            v-model="busca"
            type="search"
            autocomplete="off"
            placeholder="buscar nome ou CNPJ/CPF…"
            aria-label="buscar tomador"
            class="h-9 w-full rounded-md border bg-background pl-8 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-search-cancel-button]:hidden"
            :class="busca ? 'pr-8' : 'pr-3'"
          />
          <button
            v-if="busca"
            type="button"
            class="absolute right-1.5 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="limpar busca"
            @click="limparBusca"
          >
            <X class="size-3.5" aria-hidden="true" />
          </button>
        </div>
        <NfseSwitch
          v-if="qtdDesativados > 0"
          v-model="mostrarDesativados"
          :rotulo="`mostrar desativados (${qtdDesativados})`"
        />
        <Button v-if="canEdit" size="sm" @click="novo">
          <Plus class="mr-1.5 size-4" aria-hidden="true" />
          novo tomador
        </Button>
      </div>
    </div>

    <!-- 1ª carga -->
    <NfseSkeletonTabela v-if="!carregado" class="rounded-none border-0" :linhas="3" :colunas="5" />

    <!-- Nenhum tomador -->
    <div v-else-if="!tela.tomadores.value.length" class="p-4">
      <EmptyState
        :icon="Users"
        title="Nenhum tomador ainda"
        description="Cadastre quem recebe as notas: uma empresa do grupo ou um cliente de fora."
      >
        <Button v-if="canEdit" size="sm" @click="novo">
          <Plus class="mr-1.5 size-4" aria-hidden="true" />
          novo tomador
        </Button>
      </EmptyState>
    </div>

    <!-- Nada com a busca / tudo desativado -->
    <div v-else-if="!lista.length" class="p-4">
      <EmptyState
        v-if="termo"
        :icon="SearchX"
        :title="`Nada encontrado para “${busca.trim()}”`"
        description="Procure pelo nome, e-mail ou pelos números do CNPJ/CPF."
      >
        <Button size="sm" variant="outline" @click="limparBusca">
          <X class="mr-1.5 size-4" aria-hidden="true" /> limpar busca
        </Button>
      </EmptyState>
      <EmptyState
        v-else
        :icon="Users"
        title="Todos os tomadores estão desativados"
        description="Desativados, eles não aparecem nas listas de escolha."
      >
        <Button size="sm" variant="outline" @click="mostrarDesativados = true">mostrar desativados</Button>
      </EmptyState>
    </div>

    <!-- Lista -->
    <div v-else class="table-card tabela-nfse relative overflow-x-auto rounded-none border-0">
      <table class="w-full">
        <thead>
          <tr>
            <th>Tomador</th>
            <th>Tipo</th>
            <th class="hidden lg:table-cell">Contato</th>
            <th class="hidden whitespace-nowrap xl:table-cell">Endereço na nota</th>
            <th class="whitespace-nowrap">Usado em</th>
            <th class="w-px"><span class="sr-only">ações</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="t in lista"
            :key="t.id"
            class="cursor-pointer"
            :class="!t.ativo && 'opacity-60'"
            @click="abrir(t)"
          >
            <td class="min-w-[200px]">
              <div class="flex min-w-0 flex-wrap items-center gap-1.5">
                <span class="font-medium">{{ nome(t) }}</span>
                <span v-if="!t.ativo" class="pill-muted">desativado</span>
              </div>
              <div class="text-xs text-muted-foreground tabular-nums">{{ fmtDoc(t.documento_nota || t.documento) }}</div>
            </td>
            <td>
              <span :class="t.tipo === 'grupo' ? 'pill-info' : 'pill-muted'" class="whitespace-nowrap">
                {{ t.tipo === 'grupo' ? 'Do grupo' : 'De fora' }}
              </span>
            </td>
            <td class="hidden lg:table-cell">
              <span v-if="contato(t)" class="text-xs text-muted-foreground">{{ contato(t) }}</span>
              <span v-else class="text-xs text-muted-foreground">—</span>
            </td>
            <td class="hidden xl:table-cell">
              <NfseDica :texto="endereco(t).dica">
                <span :class="endereco(t).classe" class="cursor-help whitespace-nowrap">{{ endereco(t).texto }}</span>
              </NfseDica>
            </td>
            <td class="whitespace-nowrap">
              <NfseDica v-if="usadoEm(t).length" :texto="dicaUso(t)">
                <span class="cursor-help text-xs underline decoration-dotted underline-offset-4">
                  {{ plural(usadoEm(t).length, 'nota fixa', 'notas fixas') }}
                </span>
              </NfseDica>
              <span v-else class="text-xs text-muted-foreground">nenhuma</span>
            </td>
            <td class="w-px whitespace-nowrap text-right" @click.stop>
              <div class="inline-flex items-center gap-0.5">
                <Button size="sm" variant="ghost" class="h-8 px-2.5" @click="abrir(t)">
                  {{ canEdit ? 'editar' : 'ver' }}
                </Button>
                <NfseMenu
                  v-if="itensMenu(t).length"
                  :itens="itensMenu(t)"
                  :rotulo="`mais ações de ${nome(t)}`"
                  @escolher="(id: string) => escolher(t, id)"
                />
                <span v-else class="inline-block size-8" aria-hidden="true" />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
