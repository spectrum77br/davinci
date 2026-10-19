<script lang="ts">
import { TIPOS_EMAIL } from '~/components/AtendimentoEmailCartao.vue'

export type PessoaEmailAdmin = { id: string; nome: string; ativo: boolean }
export type CaixaEmailAdmin = { id: string; label: string; address: string }
export type LojaEmailAdmin = { chave: string; nome: string; plataforma: string }
export type PoliticaEmail = {
  mailbox_id: string
  user_id: string
  modo: 'padrao' | 'selecionadas' | 'bloqueado'
  lojas_permitidas: string[]
  lojas_bloqueadas: string[]
  tipos_permitidos: string[]
  pedidos_permitidos: { loja: string; numero: string }[]
  updated_at: string | null
  updated_by_nome?: string | null
}
export type AdministracaoEmail = {
  pessoas: PessoaEmailAdmin[]
  caixas: CaixaEmailAdmin[]
  lojas: LojaEmailAdmin[]
  politicas: PoliticaEmail[]
  lixeira_total: number
}
export type EmailNaLixeira = {
  message_id: string; mailbox_id: string; caixa: string; assunto: string | null
  loja: string | null; pedido: string | null; tipo_email: string | null
  excluido_em: string; excluido_por: string | null; motivo: string | null
}
export type FormPoliticaEmail = Pick<PoliticaEmail, 'modo' | 'lojas_permitidas' | 'lojas_bloqueadas' | 'tipos_permitidos' | 'pedidos_permitidos'>
export function formPoliticaEmail(p?: PoliticaEmail | null): FormPoliticaEmail {
  return {
    modo: p?.modo || 'padrao',
    lojas_permitidas: [...(p?.lojas_permitidas || [])],
    lojas_bloqueadas: [...(p?.lojas_bloqueadas || [])],
    tipos_permitidos: [...(p?.tipos_permitidos ?? TIPOS_EMAIL.map((t) => t.value))],
    pedidos_permitidos: (p?.pedidos_permitidos || []).map((r) => ({ ...r })),
  }
}
export function corpoPoliticaEmail(f: FormPoliticaEmail, atualizado: string | null) {
  const lista = (itens: string[]) => [...new Set(itens.map((i) => i.trim()).filter(Boolean))].sort()
  const pedidos = [...new Map(f.pedidos_permitidos.map((p) => {
    const item = { loja: p.loja.trim(), numero: p.numero.trim() }
    return [JSON.stringify(item), item] as const
  })).values()].sort((a, b) => a.loja.localeCompare(b.loja) || a.numero.localeCompare(b.numero))
  return { modo: f.modo, lojas_permitidas: f.modo === 'selecionadas' ? lista(f.lojas_permitidas) : [], lojas_bloqueadas: lista(f.lojas_bloqueadas), tipos_permitidos: lista(f.tipos_permitidos), pedidos_permitidos: pedidos, expected_updated_at: atualizado }
}
export function resumoPoliticaEmail(f: FormPoliticaEmail): string {
  if (f.modo === 'bloqueado') return 'Nenhum e-mail desta caixa fica disponível para esta pessoa.'
  if (!f.tipos_permitidos.length) return 'Nenhuma categoria selecionada: esta pessoa não verá e-mails desta caixa.'
  const permitidas = f.lojas_permitidas.filter((l) => !f.lojas_bloqueadas.includes(l))
  if (f.modo === 'selecionadas' && !permitidas.length) return 'Nenhuma loja permitida: esta pessoa não verá e-mails desta caixa, mesmo com pedidos adicionados.'
  const partes = [f.modo === 'padrao' ? 'Mantém o acesso que esta pessoa já tem.' : 'Libera somente a seleção abaixo; o acesso novo à caixa é só leitura.']
  if (f.modo === 'selecionadas') partes.push(`${permitidas.length} loja(s) permitida(s).`)
  if (f.pedidos_permitidos.length) partes.push(`Restringe a ${f.pedidos_permitidos.length} venda(s) específica(s).`)
  partes.push(`${f.tipos_permitidos.length} de 3 categorias; ${f.lojas_bloqueadas.length} loja(s) bloqueada(s).`)
  return partes.join(' ')
}
</script>

<script setup lang="ts">
import { Loader2, RefreshCw, Save, ShieldCheck, Undo2, X } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'
import { rotuloTipoEmail } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{ mailboxId?: string }>()
const emit = defineEmits<{ (e: 'mudou'): void; (e: 'configurar', id: string): void }>()
const { api } = useApi()
const dados = ref<AdministracaoEmail | null>(null)
const secao = ref<'acessos' | 'exibicao' | 'lixeira'>('acessos')
const caixaId = ref('')
const pessoaId = ref('')
const form = reactive<FormPoliticaEmail>(formPoliticaEmail())
const carregando = ref(false)
const ocupado = ref('')
const erro = ref('')
const aviso = ref('')
const conflito = ref(false)
const buscaLoja = ref('')
const pedidoLoja = ref('')
const pedidoNumeros = ref('')
const lixeiraCaixa = ref('')
const lixeiraBusca = ref('')
const lixeiraItens = ref<EmailNaLixeira[]>([])
const lixeiraProximo = ref<string | null>(null)
const lendoLixeira = ref(false)
const erroLixeira = ref('')
const READ_OPTIONS = { timeout: 15_000, retry: 0 }
let geracao = 0
let geracaoLixeira = 0
let descartado = false
const politica = computed(() => dados.value?.politicas.find((p) => p.mailbox_id === caixaId.value && p.user_id === pessoaId.value) || null)
const mudou = computed(() => JSON.stringify(corpoPoliticaEmail(form, null)) !== JSON.stringify(corpoPoliticaEmail(formPoliticaEmail(politica.value), null)))
const pessoa = computed(() => dados.value?.pessoas.find((p) => p.id === pessoaId.value))
const caixa = computed(() => dados.value?.caixas.find((c) => c.id === caixaId.value))
const podeSalvar = computed(() => !!dados.value && !!pessoa.value && !!caixa.value && mudou.value && !ocupado.value && !conflito.value)
const lojas = computed(() => {
  const lista = [...(dados.value?.lojas || [])]
  for (const chave of [...form.lojas_permitidas, ...form.lojas_bloqueadas, ...form.pedidos_permitidos.map((p) => p.loja)]) {
    if (!lista.some((l) => l.chave === chave)) lista.push({ chave, nome: 'Loja indisponível no cadastro', plataforma: '' })
  }
  const busca = buscaLoja.value.trim().toLocaleLowerCase('pt-BR')
  return lista.filter((l) => !busca || `${l.nome} ${l.plataforma}`.toLocaleLowerCase('pt-BR').includes(busca))
})
const nomeLoja = (chave: string) => dados.value?.lojas.find((l) => l.chave === chave)?.nome || 'Loja indisponível no cadastro'
function textoErro(e: any, padrao: string) {
  const code = e?.data?.detail?.code
  const conhecidos: Record<string, string> = {
    so_admin: 'Somente administradores podem gerenciar os e-mails.',
    atendimento_so_quem_mexe: 'É preciso administrar o Atendimento para mudar estas regras.',
    usuario_inativo: 'Esta pessoa está inativa. Revise a seleção.',
    email_tem_envio_pendente: 'Há uma resposta pendente. Confira o envio antes de excluir este e-mail.',
    email_envio_pendente: 'Há uma resposta pendente ou em conferência para este e-mail. Confira o envio antes de excluir.',
  }
  return (code && conhecidos[code]) || erroDaApi(e, padrao).texto
}
function aplicarPolitica() {
  Object.assign(form, formPoliticaEmail(politica.value))
  conflito.value = false
  pedidoLoja.value = ''
  pedidoNumeros.value = ''
  buscaLoja.value = ''
}
async function carregar() {
  if (ocupado.value || descartado) return
  if (mudou.value && !window.confirm('Recarregar a regra salva e descartar as alterações ainda não salvas neste formulário?')) return
  const g = ++geracao
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<AdministracaoEmail>('/api/atendimento/email/admin', READ_OPTIONS)
    if (descartado || g !== geracao) return
    dados.value = r
    if (!r.caixas.some((c) => c.id === caixaId.value)) caixaId.value = r.caixas.find((c) => c.id === props.mailboxId)?.id || r.caixas[0]?.id || ''
    if (!r.pessoas.some((p) => p.id === pessoaId.value)) pessoaId.value = r.pessoas.find((p) => p.ativo)?.id || r.pessoas[0]?.id || ''
    aplicarPolitica()
  } catch (e) {
    if (!descartado && g === geracao) erro.value = textoErro(e, 'Não consegui carregar a administração dos e-mails.')
  } finally { if (!descartado && g === geracao) carregando.value = false }
}
function selecionar(campo: 'caixa' | 'pessoa', valor: string) {
  if (ocupado.value) return
  if (mudou.value && !window.confirm('Trocar a seleção e descartar as alterações ainda não salvas desta regra?')) return
  if (campo === 'caixa') caixaId.value = valor
  else pessoaId.value = valor
  aplicarPolitica()
  erro.value = ''
  aviso.value = ''
}
function trocarSelecao(campo: 'caixa' | 'pessoa', evento: Event) {
  const seletor = evento.target as HTMLSelectElement
  selecionar(campo, seletor.value)
  // Cancelar não pode deixar o seletor nativo indicando outra pessoa/caixa.
  seletor.value = campo === 'caixa' ? caixaId.value : pessoaId.value
}
function restaurarPadrao() {
  if (ocupado.value || conflito.value) return
  Object.assign(form, formPoliticaEmail())
  pedidoLoja.value = ''; pedidoNumeros.value = ''
  aviso.value = 'O formulário voltou ao acesso padrão. Clique em Salvar regra de acesso para aplicar.'
}
function alternar(campo: 'lojas_permitidas' | 'lojas_bloqueadas' | 'tipos_permitidos', chave: string, marcar: boolean) {
  if (ocupado.value) return
  form[campo] = marcar ? [...new Set([...form[campo], chave])] : form[campo].filter((c) => c !== chave)
}
function adicionarPedidos() {
  if (ocupado.value || !pedidoLoja.value) return
  const numeros = [...new Set(pedidoNumeros.value.split(/[\n,;]/).map((n) => n.trim()).filter(Boolean))]
  if (!numeros.length) return
  if (numeros.some((n) => !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(n))) {
    erro.value = 'Revise os números dos pedidos: use até 64 letras, números, hífen ou sublinhado.'
    return
  }
  for (const numero of numeros) if (!form.pedidos_permitidos.some((p) => p.loja === pedidoLoja.value && p.numero === numero)) form.pedidos_permitidos.push({ loja: pedidoLoja.value, numero })
  if (form.modo === 'selecionadas' && !form.lojas_permitidas.includes(pedidoLoja.value)) form.lojas_permitidas.push(pedidoLoja.value)
  pedidoNumeros.value = ''
  erro.value = ''
}
async function salvar() {
  if (!podeSalvar.value) return
  const mb = caixaId.value, user = pessoaId.value, g = geracao
  const body = corpoPoliticaEmail(form, politica.value?.updated_at || null)
  ocupado.value = 'salvar'
  erro.value = ''
  aviso.value = ''
  try {
    const r = await api<PoliticaEmail>(`/api/atendimento/email/admin/acessos/${encodeURIComponent(mb)}/${encodeURIComponent(user)}`, { method: 'PUT', body })
    if (descartado || g !== geracao || mb !== caixaId.value || user !== pessoaId.value) return
    if (r.mailbox_id !== mb || r.user_id !== user) throw new Error('politica_divergente')
    dados.value!.politicas = [...dados.value!.politicas.filter((p) => p.mailbox_id !== mb || p.user_id !== user), r]
    aplicarPolitica()
    aviso.value = 'Regra de acesso salva. Ela vale também para e-mails antigos, anexos e e-mails abertos pela venda.'
    emit('mudou')
  } catch (e: any) {
    if (descartado || g !== geracao || mb !== caixaId.value || user !== pessoaId.value) return
    if (Number(e?.status || e?.statusCode || e?.response?.status) === 409) {
      conflito.value = true
      erro.value = 'Outra pessoa alterou esta regra. Sua edição foi mantida; recarregue a regra salva antes de editar novamente. Nada foi sobrescrito.'
    } else erro.value = textoErro(e, 'Não consegui salvar esta regra de acesso.')
  } finally { if (!descartado && g === geracao) ocupado.value = '' }
}
function mudarSecao(valor: 'acessos' | 'exibicao' | 'lixeira') {
  secao.value = valor
  if (valor === 'lixeira') void carregarLixeira()
}
async function carregarLixeira(mais = false) {
  if (descartado || secao.value !== 'lixeira' || (mais && (!lixeiraProximo.value || lendoLixeira.value))) return
  const g = ++geracaoLixeira
  lendoLixeira.value = true
  erroLixeira.value = ''
  if (!mais) { lixeiraItens.value = []; lixeiraProximo.value = null }
  const q = new URLSearchParams({ limite: '50' })
  if (lixeiraCaixa.value) q.set('mailbox_id', lixeiraCaixa.value)
  if (lixeiraBusca.value.trim()) q.set('q', lixeiraBusca.value.trim())
  if (mais && lixeiraProximo.value) q.set('antes', lixeiraProximo.value)
  try {
    const r = await api<{ itens: EmailNaLixeira[]; proximo: string | null }>(`/api/atendimento/email/admin/lixeira?${q}`, READ_OPTIONS)
    if (descartado || g !== geracaoLixeira || secao.value !== 'lixeira') return
    lixeiraItens.value = mais ? [...new Map([...lixeiraItens.value, ...r.itens].map((e) => [e.message_id, e])).values()] : r.itens
    lixeiraProximo.value = r.proximo
  } catch (e) {
    if (!descartado && g === geracaoLixeira) erroLixeira.value = textoErro(e, 'Não consegui carregar a lixeira do DaVinci.')
  } finally { if (!descartado && g === geracaoLixeira) lendoLixeira.value = false }
}
async function restaurar(item: EmailNaLixeira) {
  if (ocupado.value || descartado) return
  ocupado.value = `restaurar:${item.message_id}`
  erroLixeira.value = ''
  try {
    await api(`/api/atendimento/email/admin/emails/${encodeURIComponent(item.message_id)}/restaurar`, { method: 'POST', body: {} })
    if (descartado) return
    dados.value!.lixeira_total = Math.max(0, dados.value!.lixeira_total - 1)
    aviso.value = 'E-mail restaurado no DaVinci. As regras de acesso continuam valendo.'
    emit('mudou')
    await carregarLixeira()
  } catch (e) { if (!descartado) erroLixeira.value = textoErro(e, 'Não consegui restaurar este e-mail.') }
  finally { if (!descartado) ocupado.value = '' }
}
onMounted(() => { void carregar() })
onBeforeUnmount(() => { descartado = true; ++geracao; ++geracaoLixeira })
</script>

<template>
  <section class="space-y-4" aria-label="Administração dos e-mails" data-mail-administracao>
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="flex items-center gap-2 text-base font-semibold"><ShieldCheck class="size-4" /> Administração dos e-mails</h3>
        <p class="mt-1 max-w-3xl text-xs text-muted-foreground">Defina quem vê cada loja ou venda, organize as pastas e recupere os e-mails excluídos do DaVinci.</p>
      </div>
      <button type="button" class="inline-flex items-center gap-1 rounded border px-3 py-2 text-xs disabled:opacity-50" :disabled="carregando || !!ocupado" data-admin-recarregar @click="carregar"><RefreshCw class="size-3.5" /> {{ conflito ? 'Recarregar regra salva' : 'Atualizar administração' }}</button>
    </div>
    <p v-if="erro" role="alert" class="rounded border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">{{ erro }}</p>
    <p v-if="aviso" role="status" class="rounded border bg-muted/40 p-3 text-xs">{{ aviso }}</p>
    <div class="flex flex-wrap gap-1 border-b pb-2" role="group" aria-label="Opções de administração">
      <button v-for="opcao in [{ value: 'acessos', label: 'Acesso das pessoas' }, { value: 'exibicao', label: 'Pastas e exibição' }, { value: 'lixeira', label: 'Lixeira do DaVinci' }]" :key="opcao.value" type="button" class="rounded-md px-3 py-2 text-xs" :class="secao === opcao.value ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:bg-muted'" :aria-pressed="secao === opcao.value" :data-email-admin-secao="opcao.value" @click="mudarSecao(opcao.value as 'acessos' | 'exibicao' | 'lixeira')">{{ opcao.label }}<span v-if="opcao.value === 'lixeira' && dados" class="ml-1.5 rounded-full bg-muted px-1.5 tabular-nums">{{ dados.lixeira_total }}</span></button>
    </div>
    <p v-if="carregando && !dados" class="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> Carregando a administração…</p>
    <template v-else-if="dados">
      <section v-if="secao === 'acessos'" class="space-y-4" aria-label="Acesso das pessoas aos e-mails">
        <div class="grid gap-3 rounded-lg border p-4 md:grid-cols-2">
          <label class="space-y-1 text-xs font-medium">Caixa de e-mail<select :value="caixaId" class="block w-full rounded border bg-background p-2 font-normal" :disabled="!!ocupado" aria-label="Caixa da regra de acesso" @change="trocarSelecao('caixa', $event)"><option v-for="c in dados.caixas" :key="c.id" :value="c.id">{{ c.label }} · {{ c.address }}</option></select></label>
          <label class="space-y-1 text-xs font-medium">Pessoa<select :value="pessoaId" class="block w-full rounded border bg-background p-2 font-normal" :disabled="!!ocupado" aria-label="Pessoa da regra de acesso" @change="trocarSelecao('pessoa', $event)"><option v-for="p in dados.pessoas" :key="p.id" :value="p.id">{{ p.nome }}{{ p.ativo ? '' : ' (inativa)' }}</option></select></label>
        </div>
        <p v-if="!dados.caixas.length || !dados.pessoas.length" class="text-sm text-muted-foreground">Cadastre uma caixa e uma pessoa para definir os acessos.</p>
        <form v-else class="space-y-4 rounded-lg border p-4" @submit.prevent="salvar">
          <fieldset class="space-y-4" :disabled="!!ocupado || conflito">
            <legend class="sr-only">Regra de acesso de {{ pessoa?.nome }} na caixa {{ caixa?.label }}</legend>
            <label class="block space-y-1 text-xs font-medium">Acesso desta pessoa<select v-model="form.modo" class="block w-full max-w-xl rounded border bg-background p-2 font-normal" aria-label="Modo de acesso aos e-mails"><option value="padrao">Manter o acesso atual e aplicar as restrições abaixo</option><option value="selecionadas">Permitir somente lojas e vendas selecionadas (só leitura)</option><option value="bloqueado">Bloquear todos os e-mails desta caixa</option></select></label>
            <p class="rounded bg-muted/50 p-3 text-xs leading-relaxed" data-admin-acesso-resumo>{{ resumoPoliticaEmail(form) }}</p>
            <template v-if="form.modo !== 'bloqueado'">
              <fieldset class="space-y-2"><legend class="text-xs font-medium">Categorias permitidas</legend><div class="flex flex-wrap gap-4"><label v-for="t in TIPOS_EMAIL" :key="t.value" class="flex items-center gap-2 text-xs"><input type="checkbox" :checked="form.tipos_permitidos.includes(t.value)" :data-admin-tipo="t.value" @change="alternar('tipos_permitidos', t.value, ($event.target as HTMLInputElement).checked)" />{{ t.label }}</label></div></fieldset>
              <fieldset class="space-y-2"><legend class="text-xs font-medium">Lojas</legend>
                <label class="block text-xs text-muted-foreground">Buscar loja<input v-model="buscaLoja" type="search" class="mt-1 block w-full max-w-sm rounded border bg-background p-2 text-foreground" /></label>
                <div class="max-h-72 overflow-auto rounded border">
                  <table class="w-full text-left text-xs"><thead class="sticky top-0 bg-background"><tr><th class="p-2 font-medium">Loja e plataforma</th><th v-if="form.modo === 'selecionadas'" class="p-2 font-medium">Permitir</th><th class="p-2 font-medium">Bloquear</th></tr></thead><tbody class="divide-y"><tr v-for="l in lojas" :key="l.chave"><td class="p-2">{{ l.nome }}<span v-if="l.plataforma" class="ml-1 text-muted-foreground">· {{ plataformaInfo(l.plataforma).curto }}</span></td><td v-if="form.modo === 'selecionadas'" class="p-2"><input type="checkbox" :checked="form.lojas_permitidas.includes(l.chave)" :aria-label="`Permitir ${l.nome} ${l.plataforma}`" :data-admin-permitir="l.chave" @change="alternar('lojas_permitidas', l.chave, ($event.target as HTMLInputElement).checked)" /></td><td class="p-2"><input type="checkbox" :checked="form.lojas_bloqueadas.includes(l.chave)" :aria-label="`Bloquear ${l.nome} ${l.plataforma}`" :data-admin-bloquear="l.chave" @change="alternar('lojas_bloqueadas', l.chave, ($event.target as HTMLInputElement).checked)" /></td></tr><tr v-if="!lojas.length"><td colspan="3" class="p-4 text-muted-foreground">Nenhuma loja encontrada.</td></tr></tbody></table>
                </div><p class="text-[11px] text-muted-foreground">Bloquear prevalece sobre permitir. As permissões da equipe no Atendimento continuam valendo.</p>
              </fieldset>
              <fieldset class="space-y-2"><legend class="text-xs font-medium">Vendas específicas (opcional)</legend><p class="text-[11px] text-muted-foreground">Ao adicionar pedidos, o acesso fica restrito a essas vendas, respeitando as lojas e categorias escolhidas.</p>
                <div class="grid items-end gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"><label class="text-xs">Loja<select v-model="pedidoLoja" class="mt-1 block w-full rounded border bg-background p-2" aria-label="Loja dos pedidos permitidos"><option value="">Escolha a loja</option><option v-for="l in dados.lojas" :key="l.chave" :value="l.chave">{{ l.nome }} · {{ plataformaInfo(l.plataforma).curto }}</option></select></label><label class="text-xs">Número do pedido<textarea v-model="pedidoNumeros" rows="2" maxlength="6000" class="mt-1 block w-full rounded border bg-background p-2" placeholder="Um por linha" aria-label="Números dos pedidos permitidos" /></label><button type="button" class="rounded border px-3 py-2 text-xs disabled:opacity-40" :disabled="!pedidoLoja || !pedidoNumeros.trim()" data-admin-adicionar-pedidos @click="adicionarPedidos">Adicionar pedidos</button></div>
                <ul v-if="form.pedidos_permitidos.length" class="max-h-48 space-y-1 overflow-auto"><li v-for="(p, i) in form.pedidos_permitidos" :key="`${p.loja}:${p.numero}`" class="flex items-center gap-2 rounded border px-2 py-1.5 text-xs"><span class="min-w-0 flex-1 break-words">{{ nomeLoja(p.loja) }} · Pedido {{ p.numero }}</span><button type="button" class="rounded p-1 hover:bg-muted" :aria-label="`Remover pedido ${p.numero}`" @click="form.pedidos_permitidos.splice(i, 1)"><X class="size-3.5" /></button></li></ul>
              </fieldset>
            </template>
          </fieldset>
          <p class="text-[11px] text-muted-foreground">Se um chamado misturar e-mails permitidos e bloqueados, a conversa inteira fica oculta para essa pessoa. Os e-mails permitidos continuam acessíveis individualmente.</p>
          <div class="flex flex-wrap items-center justify-between gap-3 border-t pt-3"><p class="text-[11px] text-muted-foreground"><template v-if="politica?.updated_at">Última alteração: {{ politica.updated_by_nome || 'administrador' }} · {{ fmtDataHora(politica.updated_at) }}</template><template v-else>Sem regra específica: valem os acessos atuais.</template></p><button type="button" class="rounded border px-3 py-2 text-xs disabled:opacity-40" :disabled="!!ocupado || conflito" data-admin-restaurar-padrao @click="restaurarPadrao">Restaurar acesso padrão no formulário</button><button type="submit" class="inline-flex items-center gap-1 rounded bg-primary px-3 py-2 text-xs text-primary-foreground disabled:opacity-40" :disabled="!podeSalvar" data-admin-salvar-acesso><Loader2 v-if="ocupado === 'salvar'" class="size-3.5 animate-spin" /><Save v-else class="size-3.5" /> Salvar regra de acesso</button></div>
        </form>
      </section>
      <section v-else-if="secao === 'exibicao'" class="space-y-4" aria-label="Pastas e exibição dos e-mails"><div class="flex flex-wrap items-center gap-2 rounded-lg border p-3"><label class="text-xs">Caixa<select :value="caixaId" class="ml-2 max-w-full rounded border bg-background p-2" @change="trocarSelecao('caixa', $event)"><option v-for="c in dados.caixas" :key="c.id" :value="c.id">{{ c.label }}</option></select></label><button type="button" class="rounded border px-3 py-2 text-xs hover:bg-muted" :disabled="!caixaId" data-admin-configurar-caixa @click="emit('configurar', caixaId)">Configurar endereços, ponte e leitores da caixa</button></div><AtendimentoMailPastas :is-admin="true" /></section>
      <section v-else class="space-y-3" aria-label="Lixeira do DaVinci"><p class="text-xs text-muted-foreground">A exclusão retira o e-mail das telas do DaVinci e pode ser desfeita aqui. O original e as pastas do Tuta permanecem como estão.</p><form class="flex flex-wrap items-end gap-2" role="search" @submit.prevent="carregarLixeira()"><label class="text-xs">Caixa<select v-model="lixeiraCaixa" class="mt-1 block rounded border bg-background p-2"><option value="">Todas as caixas</option><option v-for="c in dados.caixas" :key="c.id" :value="c.id">{{ c.label }}</option></select></label><label class="min-w-0 flex-1 text-xs">Buscar e-mail excluído<input v-model="lixeiraBusca" type="search" maxlength="120" class="mt-1 block w-full rounded border bg-background p-2" placeholder="Assunto, loja ou pedido" /></label><button type="submit" class="rounded border px-3 py-2 text-xs disabled:opacity-40" :disabled="lendoLixeira">Buscar</button></form><p v-if="erroLixeira" role="alert" class="text-xs text-destructive">{{ erroLixeira }}</p><p v-if="lendoLixeira && !lixeiraItens.length" class="flex items-center gap-1 py-8 text-xs text-muted-foreground"><Loader2 class="size-4 animate-spin" /> Carregando lixeira…</p><p v-else-if="!lixeiraItens.length && !erroLixeira" class="rounded border border-dashed p-6 text-center text-xs text-muted-foreground">Nenhum e-mail excluído encontrado.</p><ul v-else class="space-y-2"><li v-for="item in lixeiraItens" :key="item.message_id" class="space-y-1 rounded-lg border p-3 text-xs" :data-admin-excluido="item.message_id"><div class="flex flex-wrap items-start justify-between gap-2"><p class="min-w-0 flex-1 break-words font-medium">{{ item.assunto || 'E-mail recebido' }}</p><button type="button" class="inline-flex items-center gap-1 rounded border px-2 py-1.5 disabled:opacity-40" :disabled="!!ocupado" :data-admin-restaurar="item.message_id" @click="restaurar(item)"><Loader2 v-if="ocupado === `restaurar:${item.message_id}`" class="size-3.5 animate-spin" /><Undo2 v-else class="size-3.5" /> Restaurar</button></div><p class="text-muted-foreground">{{ item.caixa }}<template v-if="item.loja"> · {{ item.loja }}</template><template v-if="item.pedido"> · Pedido {{ item.pedido }}</template><template v-if="item.tipo_email"> · {{ rotuloTipoEmail(item.tipo_email) }}</template></p><p class="text-muted-foreground">Excluído em {{ fmtDataHora(item.excluido_em) }}<template v-if="item.excluido_por"> por {{ item.excluido_por }}</template></p><p v-if="item.motivo" class="break-words text-muted-foreground">Motivo: {{ item.motivo }}</p></li></ul><button v-if="lixeiraProximo" type="button" class="rounded border px-3 py-2 text-xs disabled:opacity-40" :disabled="lendoLixeira" data-admin-lixeira-mais @click="carregarLixeira(true)">Carregar mais</button></section>
    </template>
  </section>
</template>
