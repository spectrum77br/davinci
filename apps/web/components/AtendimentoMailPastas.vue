<script lang="ts">
// PASTAS E REGRAS do e-mail das lojas (RF5, 09/10/2026), em E-mail › Filas
// (só quem MEXE no /atendimento: as rotas recusam os outros com
// `atendimento_so_quem_mexe`; MUDAR é de admin — `so_admin`).
//
//   Pastas  — as pastas de cada caixa que a ponte enxerga, com a plataforma,
//             a finalidade e o que a ponte faz com ela: lê o corpo (entra no
//             Atendimento), só conta, ou não lê. Admin escolhe à mão a
//             plataforma/finalidade ou "ignorar" (vale mais que a regra) e
//             confirma a pasta nova ("está certa").
//   Regras  — a tabela de PALAVRAS (nova plataforma = nova linha, sem mexer
//             no código): plataforma (a última palavra do nome: "vendas ml"),
//             finalidade (a primeira: "vendas" = só histórico) e marca (as
//             caixas dos sites). Mudar uma palavra reclassifica as pastas.
//
// Rotas que já existiam (routers/atendimento_email.py): GET/PATCH
// …/email/pastas, GET/PUT …/email/regras. Nada de e-mail aparece aqui — só o
// nome das pastas e das caixas. Funções puras aqui em cima
// (tests/atendimento-mail-atendimento.cjs).

export type PastaEmail = {
  id: string
  mailbox_id: string
  chave: string
  nome: string
  caminho: string | null
  tipo: string | null
  plataforma: string | null
  finalidade: string | null
  marca: string | null
  ler: string
  plataforma_manual: string | null
  finalidade_manual: string | null
  ignorar: boolean
  revisada: boolean
  vista_em: string | null
  sumiu_em: string | null
}
export type CaixaDasPastas = { id: string; nome: string }
export type RespostaPastas = {
  itens: PastaEmail[]
  caixas?: CaixaDasPastas[]
  plataformas: string[]
  finalidades: string[]
}
export type RegraPasta = { id: string | null; tipo: string; palavra: string; valor: string; ativa: boolean }
export type EdicaoPasta = { plataforma: string; finalidade: string; ignorar: boolean }

// `mail_folders.ler` (regras.LER_*): o que a ponte faz com a pasta.
export const LER: Record<string, { rotulo: string; dica: string; cls: string }> = {
  corpo: { rotulo: 'lê o corpo', dica: 'o e-mail entra no Atendimento, na conversa da loja', cls: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300' },
  so_contar: { rotulo: 'só conta', dica: 'o e-mail só é contado: o texto não entra no Atendimento', cls: 'bg-amber-500/15 text-amber-800 dark:text-amber-300' },
  nao: { rotulo: 'não lê', dica: 'a pasta fica de fora', cls: 'bg-muted text-muted-foreground' },
}
// Os tipos de palavra (regras.TIPOS) e o que cada um olha no nome da pasta.
export const TIPOS_DE_REGRA: { value: string; label: string; dica: string }[] = [
  { value: 'plataforma', label: 'Plataforma', dica: 'a ÚLTIMA palavra do nome da pasta ("vendas ml" → Mercado Livre); inteira ("ali" não casa dentro de "magalu")' },
  { value: 'finalidade', label: 'Finalidade', dica: 'a PRIMEIRA palavra ("vendas" = só histórico; "mensagens", "problema", "reclamação" = falta responder)' },
  { value: 'marca', label: 'Marca', dica: 'qualquer palavra do nome, nas caixas dos sites ("*uranyx sac" → a marca uranyx)' },
]
export const ROTULO_FINALIDADE: Record<string, string> = {
  vendas: 'vendas (só histórico)',
  mensagens: 'mensagens',
  problema: 'problema',
  reclamacao: 'reclamação',
  entrada: 'Entrada',
  enviados: 'Enviados',
}

// As pastas de cada caixa (a ordem da rota: caixa e nome), com o nome da
// caixa; a que sumiu do Tuta vai para o fim da caixa.
export function pastasPorCaixa(r: RespostaPastas | null | undefined): { id: string; nome: string; pastas: PastaEmail[] }[] {
  const nomes = new Map((r?.caixas || []).map((c) => [c.id, c.nome]))
  const grupos = new Map<string, PastaEmail[]>()
  for (const p of r?.itens || []) {
    if (!grupos.has(p.mailbox_id)) grupos.set(p.mailbox_id, [])
    grupos.get(p.mailbox_id)!.push(p)
  }
  return [...grupos.entries()].map(([id, pastas]) => ({
    id,
    nome: nomes.get(id) || 'Caixa',
    pastas: [...pastas.filter((p) => !p.sumiu_em), ...pastas.filter((p) => !!p.sumiu_em)],
  }))
}

// O que a tela de edição começa mostrando: a escolha À MÃO ('' = pela regra).
export function edicaoDe(p: Pick<PastaEmail, 'plataforma_manual' | 'finalidade_manual' | 'ignorar'>): EdicaoPasta {
  return { plataforma: p.plataforma_manual || '', finalidade: p.finalidade_manual || '', ignorar: !!p.ignorar }
}

// O corpo do PATCH: só o que mudou ('' = volta para a regra). Nada mudou → null.
export function corpoDaPasta(p: Pick<PastaEmail, 'plataforma_manual' | 'finalidade_manual' | 'ignorar'>, e: EdicaoPasta): Record<string, string | boolean> | null {
  const antes = edicaoDe(p)
  const corpo: Record<string, string | boolean> = {}
  if (e.plataforma !== antes.plataforma) corpo.plataforma = e.plataforma
  if (e.finalidade !== antes.finalidade) corpo.finalidade = e.finalidade
  if (e.ignorar !== antes.ignorar) corpo.ignorar = e.ignorar
  return Object.keys(corpo).length ? corpo : null
}

// As palavras por tipo, na ordem da tela (plataforma, finalidade, marca).
export function regrasPorTipo(itens: RegraPasta[] | null | undefined): Record<string, RegraPasta[]> {
  const out: Record<string, RegraPasta[]> = Object.fromEntries(TIPOS_DE_REGRA.map((t) => [t.value, []]))
  for (const r of itens || []) (out[r.tipo] ||= []).push(r)
  for (const lista of Object.values(out)) lista.sort((a, b) => a.palavra.localeCompare(b.palavra, 'pt-BR'))
  return out
}

// A palavra nova (ou mudada) antes do PUT: '' = pode; senão, o porquê.
export function erroDaRegra(r: Pick<RegraPasta, 'tipo' | 'palavra' | 'valor'>, opcoes: Pick<RespostaPastas, 'plataformas' | 'finalidades'> | null | undefined): string {
  if (!TIPOS_DE_REGRA.some((t) => t.value === r.tipo)) return 'Escolha o tipo da palavra.'
  const palavra = (r.palavra || '').trim()
  if (!palavra) return 'Escreva a palavra.'
  if (palavra.length > 64 || /\s/.test(palavra)) return 'A palavra é uma só (sem espaço, até 64 letras).'
  const valor = (r.valor || '').trim().toLowerCase()
  if (!valor) return 'Escolha o que a palavra quer dizer.'
  if (r.tipo === 'plataforma' && !(opcoes?.plataformas || []).includes(valor)) return 'Plataforma desconhecida.'
  if (r.tipo === 'finalidade' && !(opcoes?.finalidades || []).includes(valor)) return 'Finalidade desconhecida.'
  return ''
}

// O corpo do PUT …/regras (a rota acha a palavra pelo tipo + palavra).
export function corpoDaRegra(r: Pick<RegraPasta, 'tipo' | 'palavra' | 'valor' | 'ativa'>): Omit<RegraPasta, 'id'> {
  return { tipo: r.tipo, palavra: r.palavra.trim(), valor: r.valor.trim().toLowerCase(), ativa: r.ativa }
}

export const ERROS_PASTAS: Record<string, string> = {
  so_admin: 'Só admin muda as pastas e as palavras.',
  atendimento_so_quem_mexe: 'Só quem cuida do Atendimento vê as pastas do e-mail.',
  plataforma_invalida: 'Plataforma desconhecida.',
  finalidade_invalida: 'Finalidade desconhecida.',
  tipo_invalido: 'Tipo de palavra desconhecido.',
  palavra_invalida: 'A palavra não serve (só letras e números).',
  pasta_nao_encontrada: 'A pasta não existe mais (o Tuta mudou): atualize.',
}
</script>

<script setup lang="ts">
import { Loader2, RefreshCw } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ isAdmin: boolean }>()

const { api } = useApi()
const toasts = useToasts()

const dados = ref<RespostaPastas | null>(null)
const regras = ref<RegraPasta[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null)
const edicao = reactive<Record<string, EdicaoPasta>>({})
const nova = reactive<{ tipo: string; palavra: string; valor: string }>({ tipo: 'plataforma', palavra: '', valor: '' })

const caixas = computed(() => pastasPorCaixa(dados.value))
const porTipo = computed(() => regrasPorTipo(regras.value))
const erroNova = computed(() => erroDaRegra(nova, dados.value))
const nomeDaPlataforma = (p: string | null | undefined) => (p ? plataformaInfo(p).nome : '—')

function falhou(e: any, padrao: string) {
  const code = e?.data?.detail?.code
  const er = erroDaApi(e, padrao)
  toasts.error((code && ERROS_PASTAS[code]) || er.texto, er.motivos)
}

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const [p, r] = await Promise.all([
      api<RespostaPastas>('/api/atendimento/email/pastas'),
      api<{ itens: RegraPasta[] }>('/api/atendimento/email/regras'),
    ])
    dados.value = p
    regras.value = r?.itens || []
    for (const pasta of p?.itens || []) edicao[pasta.id] = edicaoDe(pasta)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    erro.value = (code && ERROS_PASTAS[code]) || erroDaApi(e, 'Não consegui carregar as pastas do e-mail').texto
  } finally {
    carregando.value = false
  }
}
onMounted(() => { void carregar() })

// Uma ação por vez; depois, tudo relido (a regra nova reclassifica as pastas).
async function acao(chave: string, fn: () => Promise<unknown>, ok: string, padrao: string) {
  if (ocupado.value || !props.isAdmin) return
  ocupado.value = chave
  try {
    await fn()
    toasts.success(ok)
    await carregar()
  } catch (e: any) {
    falhou(e, padrao)
  } finally {
    ocupado.value = null
  }
}

function salvarPasta(p: PastaEmail) {
  const corpo = corpoDaPasta(p, edicao[p.id] || edicaoDe(p))
  if (!corpo) return
  return acao(`pasta:${p.id}`, () => api(`/api/atendimento/email/pastas/${encodeURIComponent(p.id)}`, { method: 'PATCH', body: corpo }), `Pasta "${p.nome}" salva`, 'Não consegui salvar a pasta')
}
// Pasta nova que já está certa pela regra: só marca como revisada.
function confirmarPasta(p: PastaEmail) {
  return acao(`pasta:${p.id}`, () => api(`/api/atendimento/email/pastas/${encodeURIComponent(p.id)}`, { method: 'PATCH', body: {} }), `Pasta "${p.nome}" conferida`, 'Não consegui marcar a pasta')
}
function salvarRegra(r: RegraPasta, mudanca: Partial<Pick<RegraPasta, 'valor' | 'ativa'>>) {
  const regra = { ...r, ...mudanca }
  const motivo = erroDaRegra(regra, dados.value)
  if (motivo) {
    toasts.error(motivo)
    return
  }
  return acao(`regra:${r.tipo}:${r.palavra}`, () => api('/api/atendimento/email/regras', { method: 'PUT', body: corpoDaRegra(regra) }), `Palavra "${r.palavra}" salva`, 'Não consegui salvar a palavra')
}
function adicionarRegra() {
  if (erroNova.value) return
  const regra = corpoDaRegra({ ...nova, ativa: true })
  return acao('regra:nova', async () => {
    await api('/api/atendimento/email/regras', { method: 'PUT', body: regra })
    nova.palavra = ''
    nova.valor = ''
  }, `Palavra "${regra.palavra}" adicionada`, 'Não consegui adicionar a palavra')
}
// Trocou o tipo da palavra nova: o valor de antes não serve.
watch(() => nova.tipo, () => { nova.valor = '' })
</script>

<template>
  <div class="space-y-4 text-xs" data-mail-pastas>
    <div class="flex flex-wrap items-start gap-2 text-muted-foreground">
      <p class="min-w-0 flex-1">
        Só entram no Atendimento as pastas com o nome de uma plataforma (a última palavra: "vendas <b>ml</b>"). A escolha à mão de uma pasta vale mais que as palavras.
        <template v-if="!isAdmin"> Só admin muda — aqui você só vê.</template>
      </p>
      <button type="button" class="inline-flex items-center gap-1 hover:text-foreground" :disabled="carregando" @click="carregar">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregando }" /> atualizar
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300">{{ erro }}</div>
    <div v-else-if="carregando && !dados" class="flex justify-center py-8"><Loader2 class="size-5 animate-spin" /></div>

    <template v-if="dados">
      <!-- as pastas, por caixa -->
      <section class="space-y-2" aria-label="Pastas">
        <h3 class="text-[13px] font-semibold text-foreground">Pastas</h3>
        <div v-if="!caixas.length" class="text-muted-foreground">Nenhuma pasta ainda (o Mac manda a lista quando lê a caixa).</div>
        <div v-for="c in caixas" :key="c.id" class="space-y-1 rounded-lg border p-2.5" :data-pastas-caixa="c.id">
          <div class="font-medium text-foreground">{{ c.nome }} <span class="font-normal text-muted-foreground">· {{ c.pastas.length }} pasta(s)</span></div>
          <ul class="divide-y">
            <li v-for="p in c.pastas" :key="p.id" class="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5" :class="p.sumiu_em ? 'opacity-60' : ''" :data-pasta="p.id">
              <span class="min-w-[9rem] font-medium" :title="p.caminho || p.nome">{{ p.nome }}</span>
              <span v-if="!p.revisada && !p.sumiu_em" class="rounded bg-sky-500/15 px-1.5 py-px text-sky-800 dark:text-sky-300" title="pasta nova: confira a plataforma (ou ignore)">nova</span>
              <span v-if="p.sumiu_em" class="text-muted-foreground" :title="`sumiu do Tuta em ${fmtDataHora(p.sumiu_em)}`">sumiu do Tuta</span>
              <span class="text-muted-foreground">{{ nomeDaPlataforma(p.plataforma) }}<template v-if="p.plataforma_manual"> (à mão)</template></span>
              <span class="text-muted-foreground">· {{ p.finalidade ? ROTULO_FINALIDADE[p.finalidade] || p.finalidade : 'sem finalidade' }}<template v-if="p.finalidade_manual"> (à mão)</template></span>
              <span v-if="p.marca" class="text-muted-foreground">· marca {{ p.marca }}</span>
              <span class="rounded px-1.5 py-px" :class="(LER[p.ler] || LER.nao).cls" :title="(LER[p.ler] || LER.nao).dica" data-pasta-ler>{{ (LER[p.ler] || { rotulo: p.ler }).rotulo }}</span>
              <span v-if="isAdmin && edicao[p.id]" class="ml-auto flex flex-wrap items-center gap-1.5">
                <select v-model="edicao[p.id].plataforma" class="h-7 rounded border bg-background px-1" aria-label="plataforma da pasta">
                  <option value="">plataforma pela palavra</option>
                  <option v-for="pl in dados.plataformas" :key="pl" :value="pl">{{ nomeDaPlataforma(pl) }}</option>
                </select>
                <select v-model="edicao[p.id].finalidade" class="h-7 rounded border bg-background px-1" aria-label="finalidade da pasta">
                  <option value="">finalidade pela palavra</option>
                  <option v-for="fi in dados.finalidades" :key="fi" :value="fi">{{ ROTULO_FINALIDADE[fi] || fi }}</option>
                </select>
                <label class="inline-flex items-center gap-1"><input v-model="edicao[p.id].ignorar" type="checkbox" class="size-3.5" /> ignorar</label>
                <button
                  type="button"
                  class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50"
                  :disabled="!!ocupado || !corpoDaPasta(p, edicao[p.id])"
                  data-salvar-pasta
                  @click="salvarPasta(p)"
                ><Loader2 v-if="ocupado === `pasta:${p.id}`" class="mr-1 inline size-3 animate-spin" />salvar</button>
                <button
                  v-if="!p.revisada && !p.sumiu_em"
                  type="button"
                  class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50"
                  :disabled="!!ocupado"
                  title="a pasta nova já está certa pela palavra: só marca como conferida"
                  data-confirmar-pasta
                  @click="confirmarPasta(p)"
                >está certa</button>
              </span>
            </li>
          </ul>
        </div>
      </section>

      <!-- a tabela de palavras -->
      <section class="space-y-2" aria-label="Palavras das pastas">
        <h3 class="text-[13px] font-semibold text-foreground">Palavras (nova plataforma = nova palavra)</h3>
        <div v-for="t in TIPOS_DE_REGRA" :key="t.value" class="space-y-1 rounded-lg border p-2.5" :data-regras-tipo="t.value">
          <div><span class="font-medium text-foreground">{{ t.label }}</span> <span class="text-muted-foreground">— {{ t.dica }}</span></div>
          <div v-if="!porTipo[t.value]?.length" class="text-muted-foreground">Nenhuma palavra.</div>
          <ul v-else class="flex flex-wrap gap-1.5">
            <li
              v-for="r in porTipo[t.value]"
              :key="`${r.tipo}:${r.palavra}`"
              class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5"
              :class="r.ativa ? '' : 'opacity-50'"
              :data-regra="`${r.tipo}:${r.palavra}`"
            >
              <span class="font-mono">{{ r.palavra }}</span>
              <span class="text-muted-foreground">→ {{ t.value === 'plataforma' ? nomeDaPlataforma(r.valor) : t.value === 'finalidade' ? ROTULO_FINALIDADE[r.valor] || r.valor : r.valor }}</span>
              <span v-if="!r.ativa" class="text-muted-foreground">(desligada)</span>
              <button
                v-if="isAdmin"
                type="button"
                class="ml-0.5 rounded px-1 text-muted-foreground underline hover:text-foreground disabled:opacity-50"
                :disabled="!!ocupado"
                :title="r.ativa ? 'desligar a palavra (as pastas são reclassificadas)' : 'ligar a palavra de novo'"
                data-alternar-regra
                @click="salvarRegra(r, { ativa: !r.ativa })"
              >{{ r.ativa ? 'desligar' : 'ligar' }}</button>
            </li>
          </ul>
        </div>
        <form v-if="isAdmin" class="flex flex-wrap items-center gap-1.5 rounded-lg border border-dashed p-2.5" data-regra-nova @submit.prevent="adicionarRegra">
          <span class="font-medium text-foreground">Nova palavra:</span>
          <select v-model="nova.tipo" class="h-7 rounded border bg-background px-1" aria-label="tipo da palavra">
            <option v-for="t in TIPOS_DE_REGRA" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
          <input v-model="nova.palavra" class="h-7 w-32 rounded border bg-background px-1.5" placeholder="palavra (ex.: kwai)" aria-label="palavra" maxlength="64" />
          <span class="text-muted-foreground">→</span>
          <select v-if="nova.tipo === 'plataforma'" v-model="nova.valor" class="h-7 rounded border bg-background px-1" aria-label="plataforma">
            <option value="">plataforma…</option>
            <option v-for="pl in dados.plataformas" :key="pl" :value="pl">{{ nomeDaPlataforma(pl) }}</option>
          </select>
          <select v-else-if="nova.tipo === 'finalidade'" v-model="nova.valor" class="h-7 rounded border bg-background px-1" aria-label="finalidade">
            <option value="">finalidade…</option>
            <option v-for="fi in dados.finalidades" :key="fi" :value="fi">{{ ROTULO_FINALIDADE[fi] || fi }}</option>
          </select>
          <input v-else v-model="nova.valor" class="h-7 w-32 rounded border bg-background px-1.5" placeholder="marca (ex.: uranyx)" aria-label="marca" maxlength="64" />
          <button type="submit" class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="!!ocupado || !!erroNova" :title="erroNova || 'adiciona a palavra (a que já existe troca de valor); as pastas são reclassificadas'">
            <Loader2 v-if="ocupado === 'regra:nova'" class="mr-1 inline size-3 animate-spin" />adicionar
          </button>
          <span v-if="erroNova && (nova.palavra || nova.valor)" class="text-amber-800 dark:text-amber-300">{{ erroNova }}</span>
        </form>
      </section>
    </template>
  </div>
</template>
