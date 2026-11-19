<script setup lang="ts">
// Cadastros › Envios Threema › Grupos (tarefa 16, C7). Um grupo é uma lista
// de pessoas e contatos externos que vários envios usam ("Estoque e NF",
// "Robô da Margem"...). Mudar o grupo muda quem recebe em todos os envios que
// o usam — por isso cada um mostra "usado em N envios". Grupo em uso não se
// apaga (a API responde 409 com a lista). Só admin.
import { computed, onMounted, reactive, ref } from 'vue'
import { Pencil, Plus, Trash2 } from 'lucide-vue-next'
import type { GrupoOut, Pessoas, RefSel } from '~/lib/enviosThreema'

const emit = defineEmits<{ (e: 'mudou'): void }>()
const { api } = useApi()

const pessoas = ref<Pessoas | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const msg = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  try {
    pessoas.value = await api<Pessoas>('/api/threema/pessoas')
  } catch {
    erro.value = 'Não deu para carregar os grupos.'
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)

const grupos = computed(() => pessoas.value?.grupos ?? [])

// formulário: novo (id null) ou editando um grupo
const aberto = ref(false)
const form = reactive({ id: null as string | null, nome: '', descricao: '', membros: [] as RefSel[] })
const salvando = ref(false)

function novo() {
  Object.assign(form, { id: null, nome: '', descricao: '', membros: [] })
  aberto.value = true
  erro.value = null
}
function editar(g: GrupoOut) {
  Object.assign(form, {
    id: g.id,
    nome: g.nome,
    descricao: g.descricao || '',
    membros: g.membros.map((m) => ({ tipo: m.tipo, id: m.id, nome: m.nome })),
  })
  aberto.value = true
  erro.value = null
}

async function salvar() {
  if (!form.nome.trim() || salvando.value) return
  salvando.value = true
  erro.value = null
  msg.value = null
  const body = {
    nome: form.nome,
    descricao: form.descricao.trim() || null,
    membros: form.membros.map(({ tipo, id }) => ({ tipo, id })),
  }
  try {
    if (form.id) await api(`/api/threema/grupos/${form.id}`, { method: 'PATCH', body })
    else await api('/api/threema/grupos', { method: 'POST', body })
    aberto.value = false
    msg.value = form.id ? 'Grupo salvo.' : 'Grupo criado.'
    await carregar()
    emit('mudou')
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para salvar o grupo.'
  } finally {
    salvando.value = false
  }
}

async function apagar(g: GrupoOut) {
  if (!confirm(`Apagar o grupo "${g.nome}"?`)) return
  erro.value = null
  msg.value = null
  try {
    await api(`/api/threema/grupos/${g.id}`, { method: 'DELETE' })
    msg.value = 'Grupo apagado.'
    await carregar()
    emit('mudou')
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para apagar o grupo.'
  }
}

function usadoEm(g: GrupoOut): string {
  const n = new Set(g.usado_em.map((u) => u.chave)).size
  if (!n) return 'não está em nenhum envio'
  return `usado em ${n} ${n === 1 ? 'envio' : 'envios'}`
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-2">
      <p class="flex-1 text-sm text-muted-foreground">
        Mudar um grupo muda quem recebe em todos os envios que o usam.
      </p>
      <button type="button" class="inline-flex h-9 items-center gap-1 rounded-md border px-3 text-sm hover:bg-muted" data-acao="novo-grupo" @click="novo">
        <Plus class="size-4" /> Novo grupo
      </button>
    </div>
    <p v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700" data-erro>{{ erro }}</p>
    <p v-if="msg" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{{ msg }}</p>

    <div v-if="aberto" class="space-y-2 rounded-md border p-3" data-form-grupo>
      <label class="block text-sm">
        Nome
        <input v-model="form.nome" maxlength="80" class="mt-1 h-9 w-full rounded-md border bg-background px-2 text-sm" />
      </label>
      <label class="block text-sm">
        Descrição
        <input v-model="form.descricao" maxlength="500" class="mt-1 h-9 w-full rounded-md border bg-background px-2 text-sm" />
      </label>
      <div class="text-sm">Quem está no grupo</div>
      <EnviosThreemaSeletor v-model="form.membros" :pessoas="pessoas" :tipos="['usuario', 'contato']" />
      <div class="flex gap-2">
        <button type="button" class="h-9 rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-50" :disabled="!form.nome.trim() || salvando" @click="salvar">
          {{ form.id ? 'Salvar' : 'Criar grupo' }}
        </button>
        <button type="button" class="h-9 rounded-md border px-3 text-sm" @click="aberto = false">cancelar</button>
      </div>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr><th class="text-left">Grupo</th><th class="text-left">Quem está</th><th class="text-left">Uso</th><th /></tr>
        </thead>
        <tbody>
          <tr v-if="!carregando && !grupos.length">
            <td colspan="4" class="py-6 text-center text-muted-foreground">Nenhum grupo ainda.</td>
          </tr>
          <tr v-for="g in grupos" :key="g.id" :data-grupo="g.id" class="align-top">
            <td>
              <div class="font-medium">{{ g.nome }}</div>
              <div v-if="g.descricao" class="text-xs text-muted-foreground">{{ g.descricao }}</div>
            </td>
            <td class="text-xs">
              <span v-if="!g.membros.length" class="text-red-700">ninguém</span>
              <span v-for="(m, i) in g.membros" :key="i" :class="m.valido ? '' : 'text-amber-700'">
                {{ m.nome }}<span v-if="!m.valido"> ({{ m.aviso }})</span><span v-if="i < g.membros.length - 1">, </span>
              </span>
            </td>
            <td class="text-xs text-muted-foreground" :title="g.usado_em.map((u) => u.nome).join(', ')">{{ usadoEm(g) }}</td>
            <td class="whitespace-nowrap text-right">
              <button type="button" class="rounded p-1 hover:bg-muted" :aria-label="`editar ${g.nome}`" @click="editar(g)"><Pencil class="size-4" /></button>
              <button type="button" class="rounded p-1 hover:bg-muted" :aria-label="`apagar ${g.nome}`" @click="apagar(g)"><Trash2 class="size-4" /></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
