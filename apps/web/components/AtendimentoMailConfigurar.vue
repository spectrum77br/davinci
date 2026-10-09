<script lang="ts">
// "CONFIGURAR" uma caixa da Central de e-mail (08/10/2026), dentro da aba
// E-mail › Caixas (AtendimentoMail, a tela do outro dev). Só admin que MEXE
// no /atendimento (o botão só aparece para ele; as rotas conferem de novo).
//
//   Endereços  — os aliases da conta (um por linha). Vai pelo PATCH da
//                Central (/api/mail/mailboxes/{id} {aliases}): troca a lista
//                inteira e recifra; o endereço principal fica sempre.
//   Atendimento — a configuração NOSSA (/api/mail/mailboxes/{id}/settings):
//     visibilidade  privada (só o dono e os admins) | empresa (a equipe vê os
//                   e-mails das lojas pelo /atendimento; a caixa inteira, só
//                   dono/admin);
//     ponte         levar ao /atendimento o que chegar DAQUI EM DIANTE;
//     só aliases de loja — a caixa privada com ponte leva SÓ o e-mail dos
//                   endereços de loja (obrigatório: o resto dela é do dono);
//     remetente estrito — a resposta sai SÓ pelo endereço que recebeu (nunca
//                   cai no principal);
//     modo de envio teste | real e os destinatários de teste (caixa da
//                   empresa: no teste, só eles recebem).
// Tudo nasce desligado; cada mudança arriscada pede confirmação. Na caixa
// PRIVADA de outra pessoa, só o DONO abre mais dela para a equipe (passar
// para empresa, desligar "só endereços de loja", puxar o corte para trás) —
// o admin pode desligar a ponte (a rota recusa com `so_o_dono_da_caixa`).
// Funções puras aqui em cima (tests/atendimento-mail-atendimento.cjs).

export type ConfigCaixa = {
  mailbox_id: string
  configurada: boolean
  visibilidade: 'privada' | 'empresa'
  ponte_ligada: boolean
  ponte_desde: string | null
  ponte_so_aliases_de_loja: boolean
  remetente_estrito: boolean
  envio_modo: 'teste' | 'real'
  destinatarios_teste: string[]
  teto_hora: number
  teto_dia: number
  teto_conta_hora: number
  envio_pausado_ate: string | null
  envio_pausa_motivo: string | null
  agente_tipo: string | null
  updated_at: string | null
}
export type FormConfig = {
  visibilidade: 'privada' | 'empresa'
  ponte_ligada: boolean
  ponte_so_aliases_de_loja: boolean
  remetente_estrito: boolean
  envio_modo: 'teste' | 'real'
  destinatarios: string
}

// "um por linha" (ou vírgula/ponto e vírgula), minúsculo, sem repetir.
export function listaDeEnderecos(texto: string | null | undefined): string[] {
  return [...new Set((texto || '').split(/[\n,;]/).map((x) => x.trim().toLowerCase()).filter(Boolean))]
}

// Os aliases que vão no PATCH: o principal sempre (e primeiro), sem repetir.
export function aliasesDoTexto(texto: string | null | undefined, principal: string): string[] {
  const p = (principal || '').trim().toLowerCase()
  return [...new Set([p, ...listaDeEnderecos(texto)].filter(Boolean))]
}

export function formDaConfig(c: ConfigCaixa): FormConfig {
  return {
    visibilidade: c.visibilidade,
    ponte_ligada: c.ponte_ligada,
    ponte_so_aliases_de_loja: c.ponte_so_aliases_de_loja,
    remetente_estrito: c.remetente_estrito,
    envio_modo: c.envio_modo,
    destinatarios: (c.destinatarios_teste || []).join('\n'),
  }
}

// A caixa PRIVADA com a ponte ligada só leva os endereços de loja (a rota
// recusa o contrário com `ponte_privada_so_aliases_de_loja`).
export function soAliasesObrigatorio(f: Pick<FormConfig, 'visibilidade' | 'ponte_ligada'>): boolean {
  return f.visibilidade === 'privada' && f.ponte_ligada
}

// Só o que MUDOU vai no PATCH (a rota grava só os campos mandados).
export function mudancasDaConfig(atual: ConfigCaixa, f: FormConfig): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const so = soAliasesObrigatorio(f) ? true : f.ponte_so_aliases_de_loja
  if (f.visibilidade !== atual.visibilidade) out.visibilidade = f.visibilidade
  if (f.ponte_ligada !== atual.ponte_ligada) out.ponte_ligada = f.ponte_ligada
  if (so !== atual.ponte_so_aliases_de_loja) out.ponte_so_aliases_de_loja = so
  if (f.remetente_estrito !== atual.remetente_estrito) out.remetente_estrito = f.remetente_estrito
  if (f.envio_modo !== atual.envio_modo) out.envio_modo = f.envio_modo
  const dest = listaDeEnderecos(f.destinatarios)
  const antes = (atual.destinatarios_teste || []).map((x) => x.toLowerCase())
  if (dest.length !== antes.length || dest.some((d, i) => d !== antes[i])) out.destinatarios_teste = dest
  return out
}

// As perguntas antes de gravar o que muda o que a equipe vê ou o que sai.
export function confirmacoesDaConfig(m: Record<string, unknown>, nomeDaCaixa: string): string[] {
  const out: string[] = []
  if (m.ponte_ligada === true) out.push(`Ligar a ponte da caixa "${nomeDaCaixa}"? Os e-mails das lojas que chegarem DAQUI EM DIANTE entram no Atendimento (os antigos não).`)
  if (m.visibilidade === 'empresa') out.push(`Passar "${nomeDaCaixa}" para caixa da EMPRESA? A equipe passa a ver os e-mails das lojas pelo Atendimento (a caixa inteira continua só com o dono e os admins). Responder, ligar o envio, mexer nos endereços e trocar a chave do Mac por ela passam a exigir quem cuida do Atendimento — se o dono não cuida, perde isso — e o envio começa em MODO TESTE (só os endereços de teste recebem).`)
  if (m.envio_modo === 'real') out.push(`Envio REAL em "${nomeDaCaixa}"? As respostas passam a ir para o cliente (sempre por clique de uma pessoa).`)
  if (m.ponte_so_aliases_de_loja === false) out.push('Levar ao Atendimento TODOS os e-mails da caixa (não só os dos endereços de loja)?')
  return out
}

// Os endereços que saem da lista (a resposta já na fila por eles não sai mais).
export function aliasesRemovidos(antes: string[], depois: string[]): string[] {
  const d = new Set(depois.map((x) => x.toLowerCase()))
  return antes.filter((a) => !d.has(a.toLowerCase()))
}

export const ERROS_CONFIG: Record<string, string> = {
  atendimento_permission_required: 'Só quem cuida do Atendimento pode mudar isso nesta caixa.',
  so_o_dono_da_caixa: 'Caixa privada de outra pessoa: só o DONO dela passa para empresa, tira "só endereços de loja" ou puxa o corte para trás. Desligar a ponte, você pode.',
  ponte_privada_so_aliases_de_loja: 'Caixa privada com a ponte ligada só leva os endereços de loja: deixe "só endereços de loja" marcado.',
  campo_nao_editavel: 'Este campo não se muda por aqui.',
  mailbox_not_found: 'Caixa não encontrada. Atualize.',
}
</script>

<script setup lang="ts">
import { Loader2, Save, X } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  mailbox: { id: string; label: string; address: string; aliases: string[] }
}>()
const emit = defineEmits<{ (e: 'mudou'): void; (e: 'fechar'): void }>()

const { api } = useApi()
const toasts = useToasts()

const config = ref<ConfigCaixa | null>(null)
const form = reactive<FormConfig>({ visibilidade: 'privada', ponte_ligada: false, ponte_so_aliases_de_loja: true, remetente_estrito: false, envio_modo: 'teste', destinatarios: '' })
const aliasesTexto = ref('')
const carregando = ref(false)
const salvando = ref<'' | 'config' | 'aliases'>('')
const erro = ref<string | null>(null)

function textoDoErro(e: any, padrao: string): string {
  const code = e?.data?.detail?.code
  if (code && ERROS_CONFIG[code]) return ERROS_CONFIG[code]
  const er = erroDaApi(e, padrao)
  return [er.texto, ...er.motivos].join(' — ')
}

function aplicar(c: ConfigCaixa) {
  config.value = c
  Object.assign(form, formDaConfig(c))
}
async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    aplicar(await api<ConfigCaixa>(`/api/mail/mailboxes/${encodeURIComponent(props.mailbox.id)}/settings`))
  } catch (e: any) {
    erro.value = textoDoErro(e, 'Não consegui ler a configuração da caixa')
  } finally {
    carregando.value = false
  }
}
onMounted(() => {
  aliasesTexto.value = (props.mailbox.aliases || []).filter((a) => a.toLowerCase() !== props.mailbox.address.toLowerCase()).join('\n')
  void carregar()
})

const obrigatorio = computed(() => soAliasesObrigatorio(form))
// Na caixa privada com a ponte, fica marcado (e travado).
const soAliases = computed({
  get: () => obrigatorio.value || form.ponte_so_aliases_de_loja,
  set: (v: boolean) => { form.ponte_so_aliases_de_loja = v },
})
const mudancas = computed(() => (config.value ? mudancasDaConfig(config.value, form) : {}))
const temMudanca = computed(() => Object.keys(mudancas.value).length > 0)
const aliasesNovos = computed(() => aliasesDoTexto(aliasesTexto.value, props.mailbox.address))
const aliasesMudaram = computed(() => {
  const antes = (props.mailbox.aliases || []).map((a) => a.toLowerCase())
  const depois = aliasesNovos.value
  return antes.length !== depois.length || antes.some((a) => !depois.includes(a))
})

async function salvarConfig() {
  const c = config.value
  if (!c || salvando.value || !temMudanca.value) return
  const m = mudancas.value
  for (const pergunta of confirmacoesDaConfig(m, props.mailbox.label)) if (!confirm(pergunta)) return
  salvando.value = 'config'
  erro.value = null
  try {
    aplicar(await api<ConfigCaixa>(`/api/mail/mailboxes/${encodeURIComponent(props.mailbox.id)}/settings`, { method: 'PATCH', body: m }))
    toasts.success('Configuração da caixa salva')
    emit('mudou')
  } catch (e: any) {
    erro.value = textoDoErro(e, 'Não consegui salvar a configuração')
  } finally {
    salvando.value = ''
  }
}

async function salvarAliases() {
  if (salvando.value || !aliasesMudaram.value) return
  const novos = aliasesNovos.value
  const saem = aliasesRemovidos(props.mailbox.aliases || [], novos)
  if (saem.length && !confirm(`Tirar da caixa: ${saem.join(', ')}?\n\nResposta já na fila por estes endereços não sai mais.`)) return
  salvando.value = 'aliases'
  erro.value = null
  try {
    await api(`/api/mail/mailboxes/${encodeURIComponent(props.mailbox.id)}`, { method: 'PATCH', body: { aliases: novos } })
    toasts.success(`Endereços da caixa salvos (${novos.length})`)
    emit('mudou')
  } catch (e: any) {
    erro.value = textoDoErro(e, 'Não consegui salvar os endereços')
  } finally {
    salvando.value = ''
  }
}
</script>

<template>
  <div class="space-y-3 rounded-lg border p-4 text-sm" data-mail-configurar>
    <div class="flex items-center gap-2">
      <span class="font-medium">Configurar {{ mailbox.label }}</span>
      <Loader2 v-if="carregando" class="h-4 w-4 animate-spin" />
      <button type="button" class="ml-auto rounded p-1 hover:bg-muted" title="fechar" aria-label="fechar" @click="emit('fechar')"><X class="h-4 w-4" /></button>
    </div>
    <p v-if="erro" role="alert" class="rounded border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">{{ erro }}</p>

    <!-- endereços (o PATCH da Central) -->
    <div class="space-y-1">
      <label class="block text-xs font-medium" for="mail-config-aliases">Endereços da conta (aliases), um por linha</label>
      <textarea id="mail-config-aliases" v-model="aliasesTexto" rows="5" class="w-full rounded border bg-background p-2 font-mono text-xs" placeholder="16tr@tuta.com" />
      <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>O principal ({{ mailbox.address }}) fica sempre. Total: {{ aliasesNovos.length }} de 200.</span>
        <button type="button" class="ml-auto inline-flex items-center gap-1 rounded border px-2 py-1 text-foreground hover:bg-muted disabled:opacity-50" :disabled="!aliasesMudaram || !!salvando || aliasesNovos.length > 200" data-salvar-aliases @click="salvarAliases">
          <Loader2 v-if="salvando === 'aliases'" class="h-3.5 w-3.5 animate-spin" /><Save v-else class="h-3.5 w-3.5" /> salvar endereços
        </button>
      </div>
    </div>

    <!-- o Atendimento (a configuração nossa) -->
    <fieldset v-if="config" class="space-y-2 border-t pt-3" :disabled="!!salvando">
      <legend class="sr-only">Atendimento</legend>
      <div class="text-xs font-medium">Atendimento</div>
      <label class="flex flex-wrap items-center gap-2 text-xs">
        Quem vê
        <select v-model="form.visibilidade" class="rounded border bg-background p-1.5" data-config-visibilidade>
          <option value="privada">privada — só o dono e os admins</option>
          <option value="empresa">da empresa — a equipe vê os e-mails das lojas pelo Atendimento</option>
        </select>
      </label>
      <label class="flex items-start gap-2 text-xs">
        <input v-model="form.ponte_ligada" type="checkbox" class="mt-0.5" data-config-ponte />
        <span>Levar ao Atendimento os e-mails das lojas (a ponte)<template v-if="config.ponte_ligada && config.ponte_desde"> — ligada desde {{ fmtDataHora(config.ponte_desde) }}</template><template v-else> — vale para o que chegar daqui em diante</template></span>
      </label>
      <label class="flex items-start gap-2 text-xs" :class="obrigatorio ? 'opacity-70' : ''">
        <input v-model="soAliases" type="checkbox" class="mt-0.5" :disabled="obrigatorio" data-config-so-aliases />
        <span>Só os endereços de loja (o principal e os outros ficam fora)<template v-if="obrigatorio"> — obrigatório na caixa privada</template></span>
      </label>
      <label class="flex items-start gap-2 text-xs">
        <input v-model="form.remetente_estrito" type="checkbox" class="mt-0.5" data-config-estrito />
        <span>Remetente estrito: a resposta sai só pelo endereço que recebeu (nunca cai no principal)</span>
      </label>
      <label class="flex flex-wrap items-center gap-2 text-xs">
        Envio pelo Atendimento
        <select v-model="form.envio_modo" class="rounded border bg-background p-1.5" data-config-modo>
          <option value="teste">teste — só os endereços de teste recebem</option>
          <option value="real">real — a resposta vai para o cliente</option>
        </select>
      </label>
      <p v-if="form.visibilidade !== 'empresa'" class="text-[11px] text-muted-foreground">Na caixa privada, o modo de envio e os tetos não valem: o envio segue a regra da Central (o admin permite as respostas, o Mac confirma, uma pessoa clica).</p>
      <label class="block text-xs">
        Destinatários de teste (um por linha)
        <textarea v-model="form.destinatarios" rows="2" class="mt-1 w-full rounded border bg-background p-2 font-mono text-xs" data-config-destinatarios />
      </label>
      <p class="text-[11px] text-muted-foreground">Tetos da caixa: {{ config.teto_hora }} por hora, {{ config.teto_dia }} por dia, {{ config.teto_conta_hora }} por hora na conta do Tuta.<template v-if="config.envio_pausado_ate"> Envio pausado até {{ fmtDataHora(config.envio_pausado_ate) }}<template v-if="config.envio_pausa_motivo"> ({{ config.envio_pausa_motivo }})</template>.</template></p>
      <div class="flex justify-end">
        <button type="button" class="inline-flex items-center gap-1 rounded bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-40" :disabled="!temMudanca || !!salvando" data-salvar-config @click="salvarConfig">
          <Loader2 v-if="salvando === 'config'" class="h-3.5 w-3.5 animate-spin" /><Save v-else class="h-3.5 w-3.5" /> salvar o Atendimento
        </button>
      </div>
    </fieldset>
  </div>
</template>
