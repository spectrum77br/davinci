<script setup lang="ts">
// Cadastros › Envios Threema › Contatos externos (tarefa 16, C7): quem recebe
// aviso sem login no DaVinci (ex.: "Roma"). É a mesma lista do seletor da
// Ouvidoria › Robôs e do Informar. O ID aparece só com as 2 primeiras letras;
// ID que já é de alguém de Usuários é recusado ("já é de Fulano": escolha a
// pessoa). Contato em uso (num envio ou grupo, ou numa lista da configuração
// antiga, onde recebe de verdade hoje) não se apaga. Só admin.
import { computed, onMounted, reactive, ref } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { textoDesativarContato, threemaIdValido, type ContatoOut, type Pessoas } from '~/lib/enviosThreema'

const emit = defineEmits<{ (e: 'mudou'): void }>()
const { api } = useApi()

const lista = ref<ContatoOut[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const msg = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  try {
    lista.value = (await api<Pessoas>('/api/threema/pessoas')).contatos
  } catch {
    erro.value = 'Não deu para carregar os contatos.'
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)

const novo = reactive({ nome: '', threema_id: '', observacao: '' })
const idOk = computed(() => threemaIdValido(novo.threema_id))
const salvando = ref(false)

async function criar() {
  if (!novo.nome.trim() || !idOk.value || salvando.value) return
  salvando.value = true
  erro.value = null
  msg.value = null
  try {
    await api('/api/threema/contatos', {
      method: 'POST',
      body: { nome: novo.nome, threema_id: novo.threema_id, observacao: novo.observacao.trim() || null },
    })
    Object.assign(novo, { nome: '', threema_id: '', observacao: '' })
    msg.value = 'Contato cadastrado.'
    await carregar()
    emit('mudou')
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para cadastrar o contato.'
  } finally {
    salvando.value = false
  }
}

async function mudar(c: ContatoOut, body: Record<string, unknown>, ok: string) {
  erro.value = null
  msg.value = null
  try {
    await api(`/api/threema/contatos/${c.id}`, { method: 'PATCH', body })
    msg.value = ok
    await carregar()
    emit('mudou')
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para salvar o contato.'
  }
}
function renomear(c: ContatoOut) {
  const nome = prompt('Novo nome do contato:', c.nome)
  if (nome && nome.trim() && nome.trim() !== c.nome) mudar(c, { nome }, 'Nome trocado.')
}
function alternar(c: ContatoOut) {
  if (c.ativo && c.usado_em.length && !confirm(textoDesativarContato(c))) return
  mudar(c, { ativo: !c.ativo }, c.ativo ? 'Contato desativado.' : 'Contato reativado.')
}
async function apagar(c: ContatoOut) {
  if (!confirm(`Apagar o contato ${c.nome}?`)) return
  erro.value = null
  msg.value = null
  try {
    await api(`/api/threema/contatos/${c.id}`, { method: 'DELETE' })
    msg.value = 'Contato apagado.'
    await carregar()
    emit('mudou')
  } catch (e: any) {
    erro.value = e?.data?.detail?.message || 'Não deu para apagar o contato.'
  }
}
const ORIGEM: Record<string, string> = { tela: 'cadastrado aqui', migracao: 'veio da Ouvidoria', semeadura: 'veio da semente' }
</script>

<template>
  <div class="space-y-3">
    <p class="text-sm text-muted-foreground">
      Quem recebe aviso e não tem login no DaVinci. Quem tem login vem de Configurações › Usuários.
    </p>
    <p v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700" data-erro>{{ erro }}</p>
    <p v-if="msg" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{{ msg }}</p>

    <div class="flex flex-wrap items-end gap-2 rounded-md border p-3" data-form-contato>
      <label class="text-sm">
        Nome
        <input v-model="novo.nome" maxlength="80" class="mt-1 block h-9 w-48 rounded-md border bg-background px-2 text-sm" />
      </label>
      <label class="text-sm">
        ID do Threema
        <input v-model="novo.threema_id" maxlength="8" class="mt-1 block h-9 w-32 rounded-md border bg-background px-2 text-sm uppercase" placeholder="ABCD1234" />
      </label>
      <label class="text-sm">
        Observação
        <input v-model="novo.observacao" maxlength="500" class="mt-1 block h-9 w-56 rounded-md border bg-background px-2 text-sm" />
      </label>
      <button type="button" class="inline-flex h-9 items-center gap-1 rounded-md bg-primary px-3 text-sm text-primary-foreground disabled:opacity-50" :disabled="!novo.nome.trim() || !idOk || salvando" @click="criar">
        <Plus class="size-4" /> Cadastrar
      </button>
      <span v-if="novo.threema_id && !idOk" class="text-xs text-amber-700" data-id-invalido>
        O ID do Threema tem 8 letras/números.
      </span>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr><th class="text-left">Contato</th><th class="text-left">ID</th><th class="text-left">Uso</th><th class="text-left">Situação</th><th /></tr>
        </thead>
        <tbody>
          <tr v-if="!carregando && !lista.length">
            <td colspan="5" class="py-6 text-center text-muted-foreground">Nenhum contato externo.</td>
          </tr>
          <tr v-for="c in lista" :key="c.id" :data-contato="c.id" class="align-top" :class="{ 'text-muted-foreground': !c.ativo }">
            <td>
              <div class="font-medium">{{ c.nome }}</div>
              <div v-if="c.observacao" class="text-xs text-muted-foreground">{{ c.observacao }}</div>
              <div class="text-xs text-muted-foreground">{{ ORIGEM[c.origem] || c.origem }}</div>
              <div v-if="c.e_de_usuario" class="text-xs text-amber-700">o ID é de {{ c.e_de_usuario }} em Usuários</div>
            </td>
            <td class="text-xs">{{ c.threema_id }}</td>
            <td class="text-xs text-muted-foreground">
              {{ c.usado_em.length ? c.usado_em.map((u) => u.nome).join(', ') : '—' }}
            </td>
            <td class="text-xs">{{ c.ativo ? 'ativo' : 'inativo' }}</td>
            <td class="whitespace-nowrap text-right text-xs">
              <button type="button" class="rounded border px-2 py-1 hover:bg-muted" @click="renomear(c)">renomear</button>
              <button type="button" class="ml-1 rounded border px-2 py-1 hover:bg-muted" @click="alternar(c)">{{ c.ativo ? 'desativar' : 'reativar' }}</button>
              <button type="button" class="ml-1 rounded p-1 hover:bg-muted" :aria-label="`apagar ${c.nome}`" @click="apagar(c)"><Trash2 class="size-4" /></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
