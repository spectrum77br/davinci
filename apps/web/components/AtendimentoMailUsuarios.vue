<script lang="ts">
// Administração da aba E-mail (12/11/2026): a lista de usuários, como as subcontas
// de um ERP. O Eduardo: a administração antiga "está complexa e sem uso".
// Estado = abre as Caixas (permissão atendimento_email_leitura); Editar = cada
// conta de e-mail com as pastas dela em checkbox (o Eduardo: "qnd digo escolher
// tambem me refiro as pastas das lojas ex informativo sac de sites"). Conta
// inteira = todas as pastas, também as novas. Só admin que mexe.
export type PastaDaCaixa = { id: string; nome: string; tipo: string; total: number }
export type CaixaDoUsuario = { id: string; label: string; pastas: PastaDaCaixa[] }
export type UsuarioDoEmail = {
  id: string
  login: string
  nome: string
  admin: boolean
  ativo: boolean
  ligado: boolean
  caixas: string[]
  pastas: Record<string, string[]>
  todas: boolean
  dono_de: string[]
  criado_em: string | null
  ultimo_acesso: string | null
}
export type ListaDeUsuarios = { caixas: CaixaDoUsuario[]; usuarios: UsuarioDoEmail[] }

export type Escolha = Record<string, 'inteira' | string[]>
export function escolhaDoUsuario(u: UsuarioDoEmail): Escolha {
  const e: Escolha = {}
  for (const id of u.caixas) if (!u.dono_de.includes(id)) e[id] = u.pastas?.[id] ? [...u.pastas[id]] : 'inteira'
  return e
}
export function corpoDaEscolha(e: Escolha) {
  return Object.entries(e).filter(([, v]) => v === 'inteira' || v.length).map(([id, v]) => (v === 'inteira' ? { id } : { id, pastas: [...v].sort() }))
}
export function permissaoDeCaixas(u: UsuarioDoEmail): string {
  if (u.admin || u.todas) return 'Todas as caixas'
  return u.caixas.length ? 'Caixas específicas' : 'Nenhuma caixa'
}
export function dataHora(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).replace(',', '')
}
</script>

<script setup lang="ts">
import { Loader2, RefreshCw, ShieldCheck, X } from 'lucide-vue-next'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'

const emit = defineEmits<{ (e: 'mudou'): void }>()
const { api } = useApi()
const dados = ref<ListaDeUsuarios | null>(null)
const carregando = ref(false)
const ocupado = ref('')
const erro = ref('')
const aviso = ref('')
const editando = ref<UsuarioDoEmail | null>(null)
const form = reactive({ ligado: false, todas: false, escolha: {} as Escolha })
let descartado = false

const CODIGOS: Record<string, string> = {
  admin_ve_todas: 'Administrador já vê todas as caixas.',
  usuario_inativo: 'Este usuário está inativo no DaVinci: só dá para desligar.',
  caixa_nao_encontrada: 'Uma das caixas não existe mais. Atualize a lista.',
  usuario_nao_encontrado: 'Usuário não encontrado. Atualize a lista.',
  leitores_demais: 'Esta caixa já tem leitores demais.',
  pastas_invalidas: 'Marque pelo menos uma pasta ou a conta inteira.',
  atendimento_permission_required: 'Só administradores que cuidam do Atendimento mexem nesta tela.',
}
function textoErro(e: any, padrao: string) {
  const code = e?.data?.detail?.code
  return (code && CODIGOS[code]) || erroDaApi(e, padrao).texto
}
const nomeCaixa = (id: string) => dados.value?.caixas.find((c) => c.id === id)?.label || 'Caixa'

async function carregar() {
  if (descartado || ocupado.value) return
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<ListaDeUsuarios>('/api/atendimento/email/admin/usuarios', { timeout: 15_000, retry: 0 })
    if (!descartado) dados.value = r
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui carregar os usuários do e-mail.')
  } finally { if (!descartado) carregando.value = false }
}

async function gravar(u: UsuarioDoEmail, ligado: boolean, escolha: Escolha): Promise<boolean> {
  ocupado.value = u.id
  erro.value = ''
  aviso.value = ''
  try {
    const r = await api<UsuarioDoEmail>(`/api/atendimento/email/admin/usuarios/${encodeURIComponent(u.id)}`, { method: 'PUT', body: { ligado, caixas: corpoDaEscolha(escolha) } })
    if (descartado) return false
    if (dados.value) dados.value.usuarios = dados.value.usuarios.map((x) => (x.id === r.id ? r : x))
    aviso.value = `${r.nome}: ${r.ligado ? 'ligado' : 'desligado'} · ${permissaoDeCaixas(r).toLowerCase()}.`
    emit('mudou')
    return true
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui salvar este usuário.')
    return false
  } finally { if (!descartado) ocupado.value = '' }
}

function alternarEstado(u: UsuarioDoEmail) {
  if (u.admin || ocupado.value || (!u.ativo && !u.ligado)) return
  void gravar(u, !u.ligado, escolhaDoUsuario(u))
}
function resumoDoUsuario(u: UsuarioDoEmail): string {
  if (u.admin || u.todas) return ''
  return u.caixas.map((id) => `${nomeCaixa(id)}: ${u.pastas?.[id] ? `${u.pastas[id].length} pasta(s)` : 'conta inteira'}`).join(' · ')
}

function abrir(u: UsuarioDoEmail) {
  if (u.admin || ocupado.value) return
  editando.value = u
  form.ligado = u.ligado
  form.escolha = escolhaDoUsuario(u)
  form.todas = !!dados.value?.caixas.length && u.todas
  erro.value = ''
}
function fechar() { if (!ocupado.value) editando.value = null }
const daPessoa = (id: string) => !!editando.value?.dono_de.includes(id)
const caixasEditaveis = computed(() => (dados.value?.caixas || []).filter((c) => !daPessoa(c.id)))
function recalcularTodas() {
  form.todas = !!caixasEditaveis.value.length && caixasEditaveis.value.every((c) => form.escolha[c.id] === 'inteira')
}
function contaInteira(id: string, sim: boolean) {
  const e = { ...form.escolha }
  if (sim) e[id] = 'inteira'
  else delete e[id]
  form.escolha = e
  recalcularTodas()
}
const pastaMarcada = (caixa: string, pasta: string) => form.escolha[caixa] === 'inteira' || (Array.isArray(form.escolha[caixa]) && (form.escolha[caixa] as string[]).includes(pasta))
function marcarPasta(caixa: CaixaDoUsuario, pasta: string, sim: boolean) {
  const atual = form.escolha[caixa.id]
  let lista = atual === 'inteira' ? caixa.pastas.map((p) => p.id) : [...(atual || [])]
  lista = sim ? [...new Set([...lista, pasta])] : lista.filter((p) => p !== pasta)
  const e = { ...form.escolha }
  if (lista.length) e[caixa.id] = lista
  else delete e[caixa.id]
  form.escolha = e
  recalcularTodas()
}
function escolherTodas(sim: boolean) {
  form.todas = sim
  if (sim) form.escolha = Object.fromEntries(caixasEditaveis.value.map((c) => [c.id, 'inteira' as const]))
}
async function salvar() {
  const u = editando.value
  if (!u) return
  const escolha = form.todas ? Object.fromEntries(caixasEditaveis.value.map((c) => [c.id, 'inteira' as const])) : form.escolha
  if (await gravar(u, form.ligado, escolha)) editando.value = null
}

onMounted(() => { void carregar() })
onBeforeUnmount(() => { descartado = true })
</script>

<template>
  <section class="space-y-4" aria-label="Usuários do e-mail" data-mail-usuarios>
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="flex items-center gap-2 text-base font-semibold"><ShieldCheck class="size-4" /> Usuários do e-mail</h3>
        <p class="mt-1 max-w-3xl text-xs text-muted-foreground">Ligue o acesso e marque, em Editar, quais caixas de e-mail cada usuário vê. Administrador vê todas. O e-mail particular do dono da caixa e os códigos de acesso nunca aparecem para os outros usuários.</p>
      </div>
      <button type="button" class="inline-flex items-center gap-1 rounded border px-3 py-2 text-xs disabled:opacity-50" :disabled="carregando || !!ocupado" data-usuarios-recarregar @click="carregar"><RefreshCw class="size-3.5" /> Atualizar</button>
    </div>
    <p v-if="erro && !editando" role="alert" class="rounded border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">{{ erro }}</p>
    <p v-if="aviso" role="status" class="rounded border bg-muted/40 p-3 text-xs">{{ aviso }}</p>
    <p v-if="carregando && !dados" class="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> Carregando os usuários…</p>
    <div v-else-if="dados" class="overflow-x-auto rounded-lg border">
      <table class="w-full min-w-[860px] text-left text-sm">
        <thead class="bg-muted/40 text-xs text-muted-foreground">
          <tr><th class="px-3 py-3 font-medium">E-mail</th><th class="px-3 py-3 font-medium">Nome</th><th class="px-3 py-3 font-medium">Permissão de caixas</th><th class="px-3 py-3 font-medium">Estado</th><th class="px-3 py-3 font-medium">Data de criação</th><th class="px-3 py-3 font-medium">Último acesso</th><th class="px-3 py-3 font-medium">Ações</th></tr>
        </thead>
        <tbody class="divide-y">
          <tr v-for="u in dados.usuarios" :key="u.id" :class="u.ativo ? '' : 'text-muted-foreground'" :data-usuario="u.id">
            <td class="px-3 py-3 break-all">{{ u.login }}</td>
            <td class="px-3 py-3">{{ u.nome }}<span v-if="u.admin" class="ml-1.5 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">admin</span><span v-else-if="!u.ativo" class="ml-1.5 rounded bg-muted px-1.5 py-0.5 text-[10px]">inativo</span></td>
            <td class="px-3 py-3" :title="resumoDoUsuario(u)" data-usuario-permissao>{{ permissaoDeCaixas(u) }}<span v-if="!u.admin && !u.todas && u.caixas.length" class="ml-1 text-xs text-muted-foreground">({{ u.caixas.length }})</span><span v-if="!u.admin && !u.todas && u.caixas.length" class="block text-[11px] text-muted-foreground">{{ resumoDoUsuario(u) }}</span></td>
            <td class="px-3 py-3">
              <button type="button" role="switch" :aria-checked="u.ligado" :aria-label="`${u.ligado ? 'Desligar' : 'Ligar'} o e-mail de ${u.nome}`" :title="u.admin ? 'Administrador: sempre ligado' : u.ligado ? 'Ligado: abre as caixas marcadas na aba E-mail' : 'Desligado: não abre as caixas'" class="relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:cursor-default" :class="[u.ligado ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600', ocupado === u.id ? 'animate-pulse' : '', u.admin || (!u.ativo && !u.ligado) ? 'opacity-60' : 'cursor-pointer']" :disabled="u.admin || !!ocupado || (!u.ativo && !u.ligado)" data-usuario-estado @click="alternarEstado(u)"><span class="absolute top-0.5 size-4 rounded-full bg-white shadow transition-all" :class="u.ligado ? 'left-[18px]' : 'left-0.5'" /></button>
            </td>
            <td class="px-3 py-3 tabular-nums">{{ dataHora(u.criado_em) }}</td>
            <td class="px-3 py-3 tabular-nums">{{ dataHora(u.ultimo_acesso) }}</td>
            <td class="px-3 py-3"><button type="button" class="text-primary hover:underline disabled:cursor-default disabled:text-muted-foreground disabled:no-underline" :disabled="u.admin || !!ocupado" :title="u.admin ? 'Administrador vê todas as caixas' : ''" data-usuario-editar @click="abrir(u)">Editar</button></td>
          </tr>
          <tr v-if="!dados.usuarios.length"><td colspan="7" class="p-6 text-center text-muted-foreground">Nenhum usuário.</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="editando" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" :aria-label="`Editar ${editando.nome}`" data-usuario-dialogo @click.self="fechar">
      <form class="flex max-h-[90vh] w-full max-w-2xl flex-col gap-4 rounded-lg border bg-background p-5 shadow-lg" @submit.prevent="salvar">
        <div class="flex items-start justify-between gap-3">
          <div><h4 class="font-semibold">{{ editando.nome }}</h4><p class="break-all text-xs text-muted-foreground">{{ editando.login }}</p></div>
          <button type="button" class="rounded p-1 hover:bg-muted" aria-label="Fechar" @click="fechar"><X class="size-4" /></button>
        </div>
        <label class="flex items-center justify-between gap-3 rounded border p-3 text-sm"><span>Estado<span class="block text-xs text-muted-foreground">{{ form.ligado ? 'Ligado: abre as caixas marcadas na aba E-mail' : 'Desligado: não abre as caixas' }}</span></span><input v-model="form.ligado" type="checkbox" class="size-4" :disabled="!editando.ativo && !form.ligado" data-dialogo-ligado /></label>
        <fieldset class="min-h-0 space-y-2 overflow-y-auto pr-1">
          <legend class="text-sm font-medium">Permissão de caixas</legend>
          <div class="flex flex-wrap gap-4">
            <label class="flex items-center gap-2 text-sm"><input type="radio" name="todas" :checked="form.todas" data-dialogo-todas @change="escolherTodas(true)" /> Todas as caixas</label>
            <label class="flex items-center gap-2 text-sm"><input type="radio" name="todas" :checked="!form.todas" data-dialogo-especificas @change="escolherTodas(false)" /> Caixas e pastas específicas</label>
          </div>
          <div v-for="c in dados?.caixas || []" :key="c.id" class="rounded border" :data-dialogo-conta="c.id">
            <label class="flex items-center gap-2 border-b bg-muted/40 px-3 py-2 text-sm font-medium">
              <input type="checkbox" :checked="daPessoa(c.id) || form.todas || form.escolha[c.id] === 'inteira'" :disabled="daPessoa(c.id) || form.todas" :data-dialogo-caixa="c.id" @change="contaInteira(c.id, ($event.target as HTMLInputElement).checked)" />
              {{ c.label }}
              <span class="text-xs font-normal text-muted-foreground">{{ daPessoa(c.id) ? '(dono: vê tudo)' : '— conta inteira, inclui pastas novas' }}</span>
            </label>
            <div class="grid gap-x-4 gap-y-1.5 p-3 sm:grid-cols-2 lg:grid-cols-3">
              <label v-for="p in c.pastas" :key="p.id" class="flex min-w-0 items-center gap-2 text-sm">
                <input type="checkbox" :checked="daPessoa(c.id) || form.todas || pastaMarcada(c.id, p.id)" :disabled="daPessoa(c.id) || form.todas" :data-dialogo-pasta="`${c.id}:${p.id}`" @change="marcarPasta(c, p.id, ($event.target as HTMLInputElement).checked)" />
                <span class="truncate" :title="p.nome">{{ p.nome }}</span><span v-if="p.total" class="text-[11px] tabular-nums text-muted-foreground">{{ p.total }}</span>
              </label>
              <p v-if="!c.pastas.length" class="text-xs text-muted-foreground">Nenhuma pasta ainda.</p>
            </div>
          </div>
          <p v-if="!dados?.caixas.length" class="text-xs text-muted-foreground">Nenhuma caixa cadastrada.</p>
          <p class="text-[11px] text-muted-foreground">Marcar a conta inteira inclui as pastas que forem criadas depois; marcar pastas soltas libera só elas. Uma caixa nova cadastrada depois não entra sozinha. As lojas do usuário no Atendimento e as Filas das lojas não mudam por esta tela.</p>
        </fieldset>
        <p v-if="erro" role="alert" class="text-xs text-destructive">{{ erro }}</p>
        <div class="flex justify-end gap-2 border-t pt-3">
          <button type="button" class="rounded border px-3 py-2 text-xs" :disabled="!!ocupado" @click="fechar">Cancelar</button>
          <button type="submit" class="inline-flex items-center gap-1 rounded bg-primary px-3 py-2 text-xs text-primary-foreground disabled:opacity-50" :disabled="!!ocupado" data-dialogo-salvar><Loader2 v-if="ocupado" class="size-3.5 animate-spin" /> Salvar</button>
        </div>
      </form>
    </div>
  </section>
</template>
