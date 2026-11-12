<script lang="ts">
// Aba E-mail › Agente (12/11/2026): o agente do atendimento respondendo e-mail.
// O Eduardo: "a chave usa mesma do atendimento e como se fosse a extensao do
// atendimento pro email so", "o do email pode deixar ali na parte de email mesmo a
// config do agente deixe bem intuitivo" e "so admin mexe no agente ok e ve".
//   1. Sites (o SAC da Uranyx e da Charlots, sem loja): liga aqui — Desligado,
//      Só sugere (a equipe confere e envia) ou Responde sozinho;
//   2. Lojas: a MESMA chave do atendimento (o botão "Agente (Claude)" da loja na
//      aba Lojas) — aqui só a lista de onde ele já responde e-mail;
//   3. Regras do e-mail: entram no agente só quando o canal é e-mail. A do
//      formulário de SAC é do sistema (desliga e edita, não apaga).
// Backend: routers/mail_agente.py + services/atendimento/email_agente.py.
export type SiteDoAgente = {
  site: string
  nome: string
  canal_id: string | null
  ligado: boolean
  modo: 'copiloto' | 'auto' | null
  formulario: string
}
export type LojaDoAgente = { canal_id: string; plataforma: string; loja: string; modo: 'copiloto' | 'auto' }
export type RegraDoAgente = {
  id: string
  chave: string | null
  titulo: string
  instrucao: string
  ativa: boolean
  ordem: number
  fixa: boolean
  atualizada_em: string | null
}
export type AgenteDoEmail = { disponivel: boolean; sites: SiteDoAgente[]; lojas: LojaDoAgente[]; regras: RegraDoAgente[] }
export type EstadoDoSite = 'desligado' | 'copiloto' | 'auto'

export const ESTADOS_DO_SITE: { valor: EstadoDoSite; rotulo: string; dica: string }[] = [
  { valor: 'desligado', rotulo: 'Desligado', dica: 'A equipe responde o e-mail do SAC, como hoje.' },
  { valor: 'copiloto', rotulo: 'Só sugere', dica: 'O agente escreve a resposta na conversa; a equipe confere e envia.' },
  { valor: 'auto', rotulo: 'Responde sozinho', dica: 'Sai sozinho o pedido para abrir pelo formulário de SAC; o resto fica como sugestão para a equipe.' },
]
export function estadoDoSite(s: SiteDoAgente): EstadoDoSite {
  return s.ligado && s.modo ? s.modo : 'desligado'
}
export const NOME_PLATAFORMA: Record<string, string> = {
  shopee: 'Shopee', ml: 'Mercado Livre', tiktok: 'TikTok', amazon: 'Amazon', magalu: 'Magalu', carrefour: 'Carrefour',
}
export function rotuloDoModo(modo: string | null | undefined): string {
  return modo === 'auto' ? 'responde sozinho' : modo === 'copiloto' ? 'só sugere' : 'desligado'
}
</script>

<script setup lang="ts">
import { Bot, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'

const BASE = '/api/atendimento/email/agente'
const { api } = useApi()
const dados = ref<AgenteDoEmail | null>(null)
const carregando = ref(false)
const ocupado = ref('')
const erro = ref('')
const aviso = ref('')
const editando = ref('')
const form = reactive({ titulo: '', instrucao: '' })
const nova = reactive({ aberta: false, titulo: '', instrucao: '' })
let descartado = false

const CODIGOS: Record<string, string> = {
  site_sem_linha: 'Este site ainda não tem a linha dele no atendimento (aba Lojas).',
  regra_fixa: 'Esta regra é usada pelo sistema: desligue em vez de apagar.',
}
function textoErro(e: any, padrao: string) {
  const code = e?.data?.detail?.code
  return (code && CODIGOS[code]) || erroDaApi(e, padrao).texto
}

async function carregar() {
  if (descartado) return
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<AgenteDoEmail>(BASE, { timeout: 15_000, retry: 0 })
    if (!descartado) dados.value = r
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui carregar o agente do e-mail.')
  } finally { if (!descartado) carregando.value = false }
}

async function mudarSite(s: SiteDoAgente, estado: EstadoDoSite) {
  if (ocupado.value || estadoDoSite(s) === estado) return
  if (estado === 'auto' && !window.confirm(`Ligar o agente para responder sozinho o e-mail do SAC da ${s.nome}?`)) return
  ocupado.value = `site:${s.site}`
  erro.value = ''
  aviso.value = ''
  try {
    const r = await api<AgenteDoEmail>(`${BASE}/sites/${encodeURIComponent(s.site)}`, { method: 'PUT', body: { modo: estado === 'desligado' ? null : estado } })
    if (descartado) return
    dados.value = r
    aviso.value = `SAC da ${s.nome}: ${rotuloDoModo(estado === 'desligado' ? null : estado)}.`
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui mudar o agente deste site.')
  } finally { if (!descartado) ocupado.value = '' }
}

function substituir(r: RegraDoAgente) {
  if (dados.value) dados.value.regras = dados.value.regras.map((x) => (x.id === r.id ? r : x))
}
async function alternarRegra(r: RegraDoAgente) {
  if (ocupado.value) return
  ocupado.value = r.id
  erro.value = ''
  try {
    substituir(await api<RegraDoAgente>(`${BASE}/regras/${r.id}`, { method: 'PATCH', body: { ativa: !r.ativa } }))
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui mudar a regra.')
  } finally { if (!descartado) ocupado.value = '' }
}
function editar(r: RegraDoAgente) {
  editando.value = r.id
  form.titulo = r.titulo
  form.instrucao = r.instrucao
}
async function salvarRegra(r: RegraDoAgente) {
  if (ocupado.value) return
  ocupado.value = r.id
  erro.value = ''
  try {
    substituir(await api<RegraDoAgente>(`${BASE}/regras/${r.id}`, { method: 'PATCH', body: { titulo: form.titulo, instrucao: form.instrucao } }))
    editando.value = ''
    aviso.value = 'Regra salva.'
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui salvar a regra.')
  } finally { if (!descartado) ocupado.value = '' }
}
async function apagarRegra(r: RegraDoAgente) {
  if (ocupado.value || r.fixa || !window.confirm(`Apagar a regra "${r.titulo}"?`)) return
  ocupado.value = r.id
  erro.value = ''
  try {
    await api(`${BASE}/regras/${r.id}`, { method: 'DELETE' })
    if (dados.value) dados.value.regras = dados.value.regras.filter((x) => x.id !== r.id)
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui apagar a regra.')
  } finally { if (!descartado) ocupado.value = '' }
}
async function criarRegra() {
  if (ocupado.value) return
  ocupado.value = 'nova'
  erro.value = ''
  try {
    const r = await api<RegraDoAgente>(`${BASE}/regras`, { method: 'POST', body: { titulo: nova.titulo, instrucao: nova.instrucao } })
    if (dados.value) dados.value.regras = [...dados.value.regras, r]
    Object.assign(nova, { aberta: false, titulo: '', instrucao: '' })
    aviso.value = 'Regra criada.'
  } catch (e) {
    if (!descartado) erro.value = textoErro(e, 'Não consegui criar a regra.')
  } finally { if (!descartado) ocupado.value = '' }
}

onMounted(() => { void carregar() })
onBeforeUnmount(() => { descartado = true })
</script>

<template>
  <section class="space-y-5" aria-label="Agente do e-mail" data-mail-agente>
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="flex items-center gap-2 text-base font-semibold"><Bot class="size-4" /> Agente no e-mail</h3>
        <p class="mt-1 max-w-3xl text-xs text-muted-foreground">O mesmo agente do atendimento também responde e-mail, com a assinatura da marca ou da loja. Ele nunca responde e-mail particular, código de acesso, aviso de plataforma nem remetente suspeito.</p>
      </div>
      <button type="button" class="inline-flex items-center gap-1 rounded border px-3 py-2 text-xs disabled:opacity-50" :disabled="carregando || !!ocupado" data-agente-recarregar @click="carregar"><RefreshCw class="size-3.5" /> Atualizar</button>
    </div>
    <p v-if="erro" role="alert" class="rounded border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">{{ erro }}</p>
    <p v-if="aviso" role="status" class="rounded border bg-muted/40 p-3 text-xs">{{ aviso }}</p>
    <p v-if="carregando && !dados" class="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> Carregando…</p>

    <template v-else-if="dados">
      <p v-if="!dados.disponivel" class="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-200" data-agente-indisponivel>O agente (Claude) não está ligado no servidor: nada é respondido até ele voltar.</p>

      <!-- 1. Sites -->
      <div class="space-y-2">
        <h4 class="text-sm font-semibold">SAC dos sites</h4>
        <div class="grid gap-3 md:grid-cols-2">
          <div v-for="s in dados.sites" :key="s.site" class="space-y-2 rounded-lg border p-3" :data-agente-site="s.site">
            <div class="flex items-center justify-between gap-2">
              <span class="font-medium">SAC {{ s.nome }}</span>
              <span class="rounded px-1.5 py-0.5 text-[11px] font-medium" :class="s.ligado ? 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-300' : 'bg-muted text-muted-foreground'">{{ rotuloDoModo(s.modo) }}</span>
            </div>
            <div class="inline-flex rounded-md border p-0.5" role="radiogroup" :aria-label="`Agente no SAC da ${s.nome}`">
              <button
                v-for="e in ESTADOS_DO_SITE"
                :key="e.valor"
                type="button"
                role="radio"
                :aria-checked="estadoDoSite(s) === e.valor"
                :title="e.dica"
                :disabled="!!ocupado || !s.canal_id || (!dados.disponivel && e.valor !== 'desligado')"
                class="rounded px-2.5 py-1 text-xs disabled:opacity-50"
                :class="estadoDoSite(s) === e.valor ? (e.valor === 'desligado' ? 'bg-muted font-medium' : 'bg-primary font-medium text-primary-foreground') : 'text-muted-foreground hover:bg-muted'"
                :data-agente-site-estado="e.valor"
                @click="mudarSite(s, e.valor)"
              >{{ e.rotulo }}</button>
            </div>
            <p class="text-[11px] text-muted-foreground">{{ ESTADOS_DO_SITE.find((e) => e.valor === estadoDoSite(s))?.dica }}</p>
            <p class="text-[11px] text-muted-foreground">Formulário de SAC: <a :href="s.formulario" target="_blank" rel="noopener" class="text-primary hover:underline">{{ s.formulario.replace('https://', '') }}</a></p>
            <p v-if="!s.canal_id" class="text-[11px] text-amber-700 dark:text-amber-300">Este site ainda não tem a linha dele no atendimento.</p>
          </div>
        </div>
      </div>

      <!-- 2. Lojas -->
      <div class="space-y-2">
        <h4 class="text-sm font-semibold">Lojas <span class="font-normal text-muted-foreground">— a mesma chave do atendimento</span></h4>
        <p class="text-xs text-muted-foreground">Quando o agente está ligado no atendimento de uma loja (botão "Agente (Claude)" na aba Lojas), ele responde o e-mail dela também, no mesmo modo e com os mesmos assuntos liberados.</p>
        <div v-if="dados.lojas.length" class="flex flex-wrap gap-1.5" data-agente-lojas>
          <span v-for="l in dados.lojas" :key="l.canal_id" class="rounded border px-2 py-1 text-xs" :data-agente-loja="l.canal_id">
            <span class="text-muted-foreground">{{ NOME_PLATAFORMA[l.plataforma] || l.plataforma }} ·</span> {{ l.loja }}
            <span class="ml-1 text-[10px]" :class="l.modo === 'auto' ? 'text-indigo-700 dark:text-indigo-300' : 'text-muted-foreground'">{{ rotuloDoModo(l.modo) }}</span>
          </span>
        </div>
        <p v-else class="text-xs text-muted-foreground">Nenhuma loja com o agente ligado.</p>
      </div>

      <!-- 3. Regras -->
      <div class="space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="text-sm font-semibold">Regras do e-mail</h4>
          <button type="button" class="inline-flex items-center gap-1 rounded border px-2.5 py-1.5 text-xs disabled:opacity-50" :disabled="!!ocupado || nova.aberta" data-agente-nova-regra @click="nova.aberta = true"><Plus class="size-3.5" /> Nova regra</button>
        </div>
        <p class="text-xs text-muted-foreground">Valem só no e-mail, acima das regras do chat.</p>
        <form v-if="nova.aberta" class="space-y-2 rounded-lg border p-3" data-agente-form-nova @submit.prevent="criarRegra">
          <input v-model="nova.titulo" required minlength="3" maxlength="120" placeholder="Quando… (ex.: cliente pede nota fiscal)" class="w-full rounded border bg-background px-2 py-1.5 text-sm" />
          <textarea v-model="nova.instrucao" required minlength="10" maxlength="4000" rows="3" placeholder="O que o agente faz nesse caso" class="w-full rounded border bg-background px-2 py-1.5 text-sm" />
          <div class="flex gap-2">
            <button type="submit" class="rounded bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-50" :disabled="!!ocupado">Criar</button>
            <button type="button" class="rounded border px-3 py-1.5 text-xs" @click="nova.aberta = false">Cancelar</button>
          </div>
        </form>
        <div class="divide-y rounded-lg border">
          <div v-for="r in dados.regras" :key="r.id" class="space-y-1.5 p-3" :class="r.ativa ? '' : 'opacity-60'" :data-agente-regra="r.id">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="text-sm font-medium">{{ r.titulo }} <span v-if="r.fixa" class="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground" title="Usada pelo sistema: sai sozinha no SAC dos sites em Responde sozinho">do sistema</span></p>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <button type="button" role="switch" :aria-checked="r.ativa" :aria-label="`${r.ativa ? 'Desligar' : 'Ligar'} a regra ${r.titulo}`" class="relative h-5 w-9 rounded-full transition-colors disabled:opacity-50" :class="r.ativa ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'" :disabled="!!ocupado" data-agente-regra-ativa @click="alternarRegra(r)"><span class="absolute top-0.5 size-4 rounded-full bg-white shadow transition-all" :class="r.ativa ? 'left-[18px]' : 'left-0.5'" /></button>
                <button v-if="editando !== r.id" type="button" class="text-xs text-primary hover:underline disabled:opacity-50" :disabled="!!ocupado" data-agente-regra-editar @click="editar(r)">Editar</button>
                <button v-if="!r.fixa" type="button" class="rounded p-1 text-muted-foreground hover:text-destructive disabled:opacity-50" :disabled="!!ocupado" :aria-label="`Apagar a regra ${r.titulo}`" @click="apagarRegra(r)"><Trash2 class="size-3.5" /></button>
              </div>
            </div>
            <form v-if="editando === r.id" class="space-y-2" @submit.prevent="salvarRegra(r)">
              <input v-model="form.titulo" required minlength="3" maxlength="120" class="w-full rounded border bg-background px-2 py-1.5 text-sm" />
              <textarea v-model="form.instrucao" required minlength="10" maxlength="4000" rows="5" class="w-full rounded border bg-background px-2 py-1.5 text-sm" />
              <div class="flex gap-2">
                <button type="submit" class="rounded bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-50" :disabled="!!ocupado">Salvar</button>
                <button type="button" class="rounded border px-3 py-1.5 text-xs" @click="editando = ''">Cancelar</button>
              </div>
            </form>
            <p v-else class="whitespace-pre-line text-xs text-muted-foreground">{{ r.instrucao }}</p>
          </div>
          <p v-if="!dados.regras.length" class="p-4 text-center text-xs text-muted-foreground">Nenhuma regra.</p>
        </div>
      </div>
    </template>
  </section>
</template>
