<script lang="ts">
// "QUEM MAIS VÊ (só leitura)" de uma caixa da Central de e-mail (09/10/2026),
// dentro do Configurar (AtendimentoMailConfigurar, só admin que MEXE).
// Eduardo: "deixe o usuário israel ver a aba de e-mail agora".
//
//   GET /api/mail/mailboxes/{id}/leitores — a lista, os candidatos (usuários
//       ativos, sem o dono e sem admins: esses já veem) e quem/quando mudou;
//   PUT /api/mail/mailboxes/{id}/leitores {leitores: [ids]} — troca a lista.
//
// O leitor vê a caixa (a lista e o e-mail aberto, os anexos); nunca responde,
// resolve envio, configura nem troca a chave (a API confere).
// Funções puras aqui em cima (tests/atendimento-mail-leitores.cjs).

export type Pessoa = { id: string; nome: string }
export type VisaoLeitores = {
  mailbox_id: string
  leitores: (Pessoa & { ativo: boolean })[]
  candidatos: Pessoa[]
  atualizado_por: Pessoa | null
  atualizado_em: string | null
}

// As opções do seletor: quem já está na lista (mesmo inativo, para poder
// tirar; o `ativo` dela vale) + os candidatos, por nome, sem repetir.
export function opcoesDeLeitores(v: VisaoLeitores): (Pessoa & { ativo: boolean })[] {
  const por = new Map<string, Pessoa & { ativo: boolean }>()
  for (const l of v.leitores || []) por.set(l.id, l)
  for (const c of v.candidatos || []) if (!por.has(c.id)) por.set(c.id, { ...c, ativo: true })
  return [...por.values()].sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'))
}

export function mesmaLista(a: string[], b: string[]): boolean {
  const sa = new Set(a)
  return sa.size === new Set(b).size && b.every((x) => sa.has(x))
}

export const ERROS_LEITORES: Record<string, string> = {
  atendimento_permission_required: 'Só quem cuida do Atendimento muda quem vê a caixa.',
  leitor_inativo: 'Só usuários ativos podem ver a caixa. Tire quem está inativo e salve de novo.',
  leitores_demais: 'Lista grande demais (máximo 50).',
  mailbox_not_found: 'Caixa não encontrada. Atualize.',
}
</script>

<script setup lang="ts">
import { Eye, Loader2, Save } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ mailbox: { id: string; label: string } }>()
const emit = defineEmits<{ (e: 'mudou'): void }>()
const { api } = useApi()
const toasts = useToasts()

const visao = ref<VisaoLeitores | null>(null)
const escolhidos = ref<string[]>([])
const carregando = ref(false)
const salvando = ref(false)
const erro = ref<string | null>(null)

const rota = computed(() => `/api/mail/mailboxes/${encodeURIComponent(props.mailbox.id)}/leitores`)
const opcoes = computed(() => (visao.value ? opcoesDeLeitores(visao.value) : []))
const mudou = computed(() => !!visao.value && !mesmaLista(escolhidos.value, visao.value.leitores.map((l) => l.id)))

function textoDoErro(e: any, padrao: string): string {
  const code = e?.data?.detail?.code
  if (code && ERROS_LEITORES[code]) return ERROS_LEITORES[code]
  const er = erroDaApi(e, padrao)
  return [er.texto, ...er.motivos].join(' — ')
}
function aplicar(v: VisaoLeitores) {
  visao.value = v
  escolhidos.value = v.leitores.map((l) => l.id)
}
async function carregar() {
  carregando.value = true
  erro.value = null
  try { aplicar(await api<VisaoLeitores>(rota.value)) }
  catch (e: any) { erro.value = textoDoErro(e, 'Não consegui ler quem vê a caixa') }
  finally { carregando.value = false }
}
function alternar(id: string, marcado: boolean) {
  escolhidos.value = marcado ? [...new Set([...escolhidos.value, id])] : escolhidos.value.filter((x) => x !== id)
}
async function salvar() {
  if (!visao.value || salvando.value || !mudou.value) return
  const novos = escolhidos.value.filter((id) => !visao.value!.leitores.some((l) => l.id === id))
  if (novos.length) {
    const nomes = opcoes.value.filter((o) => novos.includes(o.id)).map((o) => o.nome).join(', ')
    if (!confirm(`Deixar ${nomes} ver a caixa "${props.mailbox.label}" inteira (só leitura: lista, e-mails e anexos)?`)) return
  }
  salvando.value = true
  erro.value = null
  try {
    aplicar(await api<VisaoLeitores>(rota.value, { method: 'PUT', body: { leitores: escolhidos.value } }))
    toasts.success('Quem vê a caixa: salvo')
    emit('mudou')
  } catch (e: any) {
    erro.value = textoDoErro(e, 'Não consegui salvar quem vê a caixa')
  } finally { salvando.value = false }
}
onMounted(() => { void carregar() })
</script>

<template>
  <fieldset class="space-y-2 border-t pt-3" :disabled="salvando" data-mail-leitores>
    <legend class="sr-only">Quem mais vê (só leitura)</legend>
    <div class="flex items-center gap-2 text-xs font-medium"><Eye class="h-3.5 w-3.5" /> Quem mais vê (só leitura) <Loader2 v-if="carregando" class="h-3.5 w-3.5 animate-spin" /></div>
    <p class="text-[11px] text-muted-foreground">Além do dono e dos admins. Quem estiver marcado vê a caixa inteira na aba E-mail (lista, e-mails e anexos), mas não responde, não configura e não troca a chave.</p>
    <p v-if="erro" role="alert" class="rounded border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">{{ erro }}</p>
    <div v-if="visao" class="max-h-48 space-y-1 overflow-y-auto rounded border p-2">
      <p v-if="!opcoes.length" class="text-xs text-muted-foreground">Nenhum usuário ativo além do dono e dos admins.</p>
      <label v-for="o in opcoes" :key="o.id" class="flex items-center gap-2 text-xs" :data-leitor="o.id">
        <input type="checkbox" :checked="escolhidos.includes(o.id)" @change="alternar(o.id, ($event.target as HTMLInputElement).checked)" />
        <span>{{ o.nome }}</span>
        <span v-if="!o.ativo" class="text-[11px] text-red-700">inativo — tire da lista</span>
      </label>
    </div>
    <div v-if="visao" class="flex flex-wrap items-center gap-2">
      <span class="text-[11px] text-muted-foreground"><template v-if="visao.atualizado_em">Mudou por último: {{ visao.atualizado_por?.nome || '—' }} em {{ fmtDataHora(visao.atualizado_em) }}</template><template v-else>Ninguém além do dono e dos admins.</template></span>
      <button type="button" class="ml-auto inline-flex items-center gap-1 rounded bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-40" :disabled="!mudou || salvando" data-salvar-leitores @click="salvar">
        <Loader2 v-if="salvando" class="h-3.5 w-3.5 animate-spin" /><Save v-else class="h-3.5 w-3.5" /> salvar quem vê
      </button>
    </div>
  </fieldset>
</template>
