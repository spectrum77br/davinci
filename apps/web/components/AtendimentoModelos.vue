<script setup lang="ts">
// Aba "Respostas prontas" do Atendimento (25/09/2026): os textos que a equipe
// insere com um clique na caixa de resposta (menu "Respostas prontas" da
// conversa). Cada uma pode valer para todas as lojas ou só para uma
// plataforma/caixa — o menu da conversa mostra só as que servem ali.
//
// Lacunas: {numero_pedido}, {rastreio}, {transportadora}, {previsao_entrega},
// {protocolo} (o chamado dos sites) e
// {comprador} são trocadas pelo dado do pedido na hora de inserir. O que não
// tiver dado fica entre chaves e a tela segura o envio até a pessoa trocar —
// comprador nenhum recebe "{rastreio}".
//
// O contador usa o limite da caixa (ML pós-venda: 350 caracteres; a API do ML
// recusa a mensagem inteira acima disso). Resposta que vale para várias
// caixas é medida pelo menor limite entre elas.
// Temu e AliExpress não aparecem aqui: lá não há caixa de envio (a resposta é
// no Seller Center), e a resposta pronta só serve para ela.
//
// Por equipe (03/11/2026, o Eduardo: "as q ja tem la deixe pra todos mas as
// novas que eles vao criar vai ter essa ideia de especifico por equipe"): a
// lista vem do servidor no escopo da pessoa — as "para todos" + as da equipe
// dela; o admin, todas, com o filtro de equipe. Quem é de equipe cria a da
// equipe dele e mexe só nas da equipe; a "para todos" só o admin muda.
//
// 05/11/2026, o Eduardo: "os 3 grupos devem poder desativar as respostas padroes
// pra deixar quais eles querem ... se um desativar e outro nao nao afeta o
// outro". Na "para todos", o "desativar" de quem é de equipe vale só para ela
// (PUT .../ativa-na-equipe); o do admin continua valendo para todos.
import { Check, Loader2, MessageSquareText, Pencil, Plus, RotateCcw, Search, Trash2, X } from 'lucide-vue-next'
import {
  LACUNAS,
  PLATAFORMAS_COM_ENVIO,
  canaisDa,
  canalLabel,
  comoLista,
  comoObjeto,
  erroDaApi,
  filtrarProntas,
  limiteDe,
  plataformaInfo,
  prontaAtiva,
  tamanhoDoEnvio,
  type Modelo,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ canEdit: boolean; canDelete: boolean }>()
const emit = defineEmits<{ (e: 'mudou', lista: Modelo[]): void }>()
const { api } = useApi()
const toasts = useToasts()

const modelos = ref<Modelo[]>([])
// As equipes do formulário e do filtro (GET /modelos/equipes): `todas` = o admin
// (escolhe a equipe ou "para todos"); `minhas` = a equipe de quem é de equipe.
type Equipes = { equipes: { numero: number; nome: string }[]; minhas: number[]; todas: boolean }
const equipes = ref<Equipes | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const salvando = ref(false)

// A conversa usa a mesma lista (menu da caixa de resposta): toda mudança aqui
// vai para a página, sem esperar recarregar.
watch(modelos, (l) => emit('mudou', l), { deep: true })

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const [lista, eq] = await Promise.all([
      api<unknown>('/api/atendimento/modelos'),
      equipes.value ? Promise.resolve(equipes.value) : api<Equipes>('/api/atendimento/modelos/equipes').catch(() => null),
    ])
    modelos.value = comoLista<Modelo>(lista, 'modelos')
    if (eq && Array.isArray(eq.equipes)) equipes.value = eq
  } catch (e: any) {
    erro.value = erroDaApi(e, 'Não consegui carregar as respostas prontas').texto
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)

const busca = ref('')
const filtroPlataforma = ref('')
// '' = todas; 'todos' = só as "para todos"; '101' = só as da equipe 101.
const filtroEquipe = ref('')
const visiveis = computed(() =>
  filtrarProntas(
    modelos.value
      .filter((m) => !filtroPlataforma.value || !m.plataforma || m.plataforma === filtroPlataforma.value)
      .filter((m) => !filtroEquipe.value || (filtroEquipe.value === 'todos' ? m.equipe == null : String(m.equipe ?? '') === filtroEquipe.value)),
    busca.value,
  ),
)

// O filtro: o admin escolhe entre todas as equipes; quem é de equipe, entre a
// dele e as "para todos" (as outras nem chegam).
const equipesDoFiltro = computed(() => {
  const e = equipes.value
  if (!e) return []
  return e.todas ? e.equipes : e.equipes.filter((x) => e.minhas.includes(x.numero))
})
// Quem cria: o admin (canEdit) ou quem é de equipe.
const podeCriar = computed(() => props.canEdit || !!equipes.value?.minhas.length)
// Por linha, o que o servidor disse (a própria equipe ou o admin).
const podeMexer = (m: Modelo) => m.pode_editar ?? props.canEdit
// O admin apaga com o `delete`; quem é de equipe apaga as da equipe dele.
const podeApagar = (m: Modelo) => podeMexer(m) && (props.canDelete || !props.canEdit)
function rotuloEquipe(m: Pick<Modelo, 'equipe' | 'equipe_nome'>) {
  return m.equipe == null ? 'para todos' : m.equipe_nome || `Equipe ${m.equipe}`
}

function alcance(m: Pick<Modelo, 'plataforma' | 'canal'>) {
  if (!m.plataforma) return 'todas as lojas'
  const p = plataformaInfo(m.plataforma).nome
  return m.canal ? `${p} · ${canalLabel(m.canal)}` : p
}

// ─── formulário (um só: nova ou edição) ─────────────────────────────────────
// `equipe`: '' = para todos (só o admin), senão o número da equipe.
type Form = { id: string | null; titulo: string; texto: string; plataforma: string; canal: string; ativo: boolean; ordem: number; equipe: string }
const vazio = (): Form => ({ id: null, titulo: '', texto: '', plataforma: '', canal: '', ativo: true, ordem: 0, equipe: '' })
const form = reactive<Form>(vazio())
const formAberto = ref(false)
const caixaTexto = ref<HTMLTextAreaElement | null>(null)

const limite = computed(() => limiteDe(form.plataforma || null, form.canal || null))
// Mesma contagem da caixa de resposta (a do backend: texto normalizado).
const tamanho = computed(() => tamanhoDoEnvio(form.texto, form.plataforma || null))

// A equipe que a nova nasce: a de quem é de equipe; o admin, a do filtro (ou "para todos").
function equipeDaNova(): string {
  const e = equipes.value
  if (e && !e.todas && e.minhas.length) return String(e.minhas[0])
  return /^\d+$/.test(filtroEquipe.value) ? filtroEquipe.value : ''
}
function abrirNova() {
  Object.assign(form, vazio(), { ordem: (modelos.value.reduce((mx, m) => Math.max(mx, m.ordem ?? 0), 0) || 0) + 10, equipe: equipeDaNova() })
  formAberto.value = true
}
function abrirEdicao(m: Modelo) {
  Object.assign(form, { id: m.id, titulo: m.titulo, texto: m.texto, plataforma: m.plataforma || '', canal: m.canal || '', ativo: m.ativo, ordem: m.ordem ?? 0, equipe: m.equipe == null ? '' : String(m.equipe) })
  formAberto.value = true
}
// As opções do campo "Equipe": o admin escolhe; quem é de equipe fica com a dele.
const opcoesEquipe = computed(() => {
  const e = equipes.value
  if (!e) return []
  return e.todas ? e.equipes : e.equipes.filter((x) => e.minhas.includes(x.numero))
})
const escolheEquipe = computed(() => !!equipes.value?.todas)
function fechar() {
  formAberto.value = false
  Object.assign(form, vazio())
}
function aoMudarPlataforma() {
  if (!canaisDa(form.plataforma).some((c) => c.value === form.canal)) form.canal = ''
}
function inserirLacuna(chave: string) {
  const el = caixaTexto.value
  const t = `{${chave}}`
  const ini = el?.selectionStart ?? form.texto.length
  const fim = el?.selectionEnd ?? ini
  form.texto = form.texto.slice(0, ini) + t + form.texto.slice(fim)
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(ini + t.length, ini + t.length)
  })
}

function falhou(e: any, padrao: string) {
  const er = erroDaApi(e, padrao)
  toasts.error(er.texto, er.motivos)
}

async function salvar() {
  if (!form.titulo.trim() || !form.texto.trim()) return
  const body = {
    titulo: form.titulo.trim(),
    texto: form.texto.trim(),
    plataforma: form.plataforma || null,
    canal: (form.plataforma && form.canal) || null,
    ativo: form.ativo,
    ordem: Number(form.ordem) || 0,
    equipe: form.equipe ? Number(form.equipe) : null,
  }
  salvando.value = true
  try {
    if (form.id) {
      const id = form.id
      const r = comoObjeto<Modelo>(await api<unknown>(`/api/atendimento/modelos/${encodeURIComponent(id)}`, { method: 'PATCH', body }), 'modelo')
      const i = modelos.value.findIndex((m) => m.id === id)
      if (i >= 0) modelos.value[i] = r ?? { ...modelos.value[i], ...body }
    } else {
      const r = comoObjeto<Modelo>(await api<unknown>('/api/atendimento/modelos', { method: 'POST', body }), 'modelo')
      if (r) modelos.value = [...modelos.value, r]
      else await carregar()
    }
    toasts.success('Resposta pronta salva')
    fechar()
  } catch (e: any) {
    falhou(e, 'Não consegui salvar a resposta pronta')
  } finally {
    salvando.value = false
  }
}

async function alternar(m: Modelo) {
  salvando.value = true
  try {
    const r = comoObjeto<Modelo>(await api<unknown>(`/api/atendimento/modelos/${encodeURIComponent(m.id)}`, { method: 'PATCH', body: { ativo: !m.ativo } }), 'modelo')
    const i = modelos.value.findIndex((x) => x.id === m.id)
    if (i >= 0) modelos.value[i] = r ?? { ...m, ativo: !m.ativo }
  } catch (e: any) {
    falhou(e, 'Não consegui mudar a resposta pronta')
  } finally {
    salvando.value = false
  }
}

// A resposta padrão só para a equipe de quem clicou: as outras não mudam.
async function alternarNaEquipe(m: Modelo) {
  salvando.value = true
  try {
    const ativo = !!m.desativada_na_equipe
    const r = comoObjeto<Modelo>(await api<unknown>(`/api/atendimento/modelos/${encodeURIComponent(m.id)}/ativa-na-equipe`, { method: 'PUT', body: { ativo } }), 'modelo')
    const i = modelos.value.findIndex((x) => x.id === m.id)
    if (i >= 0) modelos.value[i] = r ?? { ...m, desativada_na_equipe: !ativo }
  } catch (e: any) {
    falhou(e, 'Não consegui mudar a resposta pronta')
  } finally {
    salvando.value = false
  }
}

async function apagar(m: Modelo) {
  if (!confirm(`Apagar a resposta pronta "${m.titulo}"?\n\nSe for só por um tempo, use "desativar".`)) return
  salvando.value = true
  try {
    await api(`/api/atendimento/modelos/${encodeURIComponent(m.id)}`, { method: 'DELETE' })
    modelos.value = modelos.value.filter((x) => x.id !== m.id)
  } catch (e: any) {
    falhou(e, 'Não consegui apagar a resposta pronta')
  } finally {
    salvando.value = false
  }
}

// "{rastreio}" — escrito no script porque "}}" dentro de {{ }} fecha a
// interpolação antes da hora no template.
const comChaves = (chave: string) => `{${chave}}`
const LACUNAS_DA_TELA = LACUNAS.filter((l) => ['numero_pedido', 'rastreio', 'transportadora', 'previsao_entrega', 'comprador', 'protocolo'].includes(l.chave))
</script>

<template>
  <section class="rounded-lg border bg-card">
    <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2">
      <MessageSquareText class="size-4 text-muted-foreground" />
      <h2 class="text-sm font-semibold">Respostas prontas</h2>
      <span class="text-xs text-muted-foreground">{{ modelos.filter(prontaAtiva).length }} ativa(s)</span>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input v-model="busca" class="h-8 w-48 rounded-md border bg-background pl-7 pr-2 text-xs" placeholder="buscar…" aria-label="buscar resposta pronta" />
        </div>
        <select v-if="equipesDoFiltro.length" v-model="filtroEquipe" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar por equipe">
          <option value="">todas as equipes</option>
          <option value="todos">só as para todos</option>
          <option v-for="e in equipesDoFiltro" :key="e.numero" :value="String(e.numero)">só {{ e.nome }}</option>
        </select>
        <select v-model="filtroPlataforma" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar por plataforma">
          <option value="">todas</option>
          <option v-for="p in PLATAFORMAS_COM_ENVIO" :key="p.value" :value="p.value">servem para {{ p.nome }}</option>
        </select>
        <Button size="sm" variant="outline" class="h-8" :disabled="carregando" @click="carregar">
          <RotateCcw class="size-4" :class="{ 'animate-spin': carregando }" />
        </Button>
        <Button v-if="podeCriar && !formAberto" size="sm" class="h-8" @click="abrirNova">
          <Plus class="mr-1.5 size-4" /> nova resposta
        </Button>
      </div>
    </div>

    <div v-if="erro" class="m-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar">tentar de novo</button>
    </div>

    <!-- formulário -->
    <div v-if="formAberto" class="space-y-2 border-b bg-muted/30 px-3 py-3">
      <div class="grid gap-2 md:grid-cols-[1fr_150px_180px_160px_90px]">
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Título (só para achar no menu)</span>
          <input v-model="form.titulo" maxlength="200" class="h-9 w-full rounded-md border bg-background px-2 text-sm" placeholder="ex.: Rastreio enviado" />
        </label>
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground" :title="escolheEquipe ? 'para todos ou só uma equipe' : 'a resposta nova fica só para a sua equipe'">Equipe</span>
          <select v-model="form.equipe" class="h-9 w-full rounded-md border bg-background px-2 text-sm" :disabled="!escolheEquipe && opcoesEquipe.length < 2" aria-label="equipe da resposta pronta">
            <option v-if="escolheEquipe" value="">para todos</option>
            <option v-for="e in opcoesEquipe" :key="e.numero" :value="String(e.numero)">{{ e.nome }}</option>
          </select>
        </label>
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Vale para</span>
          <select v-model="form.plataforma" class="h-9 w-full rounded-md border bg-background px-2 text-sm" @change="aoMudarPlataforma">
            <option value="">todas as lojas</option>
            <option v-for="p in PLATAFORMAS_COM_ENVIO" :key="p.value" :value="p.value">{{ p.nome }}</option>
          </select>
        </label>
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Caixa</span>
          <select v-model="form.canal" class="h-9 w-full rounded-md border bg-background px-2 text-sm" :disabled="canaisDa(form.plataforma).length < 2">
            <option value="">todas</option>
            <option v-for="c in canaisDa(form.plataforma)" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
        </label>
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground" title="menor aparece primeiro no menu">Ordem</span>
          <input v-model.number="form.ordem" type="number" class="h-9 w-full rounded-md border bg-background px-2 text-sm" />
        </label>
      </div>
      <label class="block space-y-1">
        <span class="text-[11px] font-medium text-muted-foreground">Texto</span>
        <textarea ref="caixaTexto" v-model="form.texto" rows="5" maxlength="4000" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="Olá, {comprador}! Seu pedido {numero_pedido} já foi enviado — o rastreio é {rastreio}." />
      </label>
      <div class="flex flex-wrap items-center gap-1.5 text-[11px]">
        <span class="text-muted-foreground">Inserir dado do pedido:</span>
        <button v-for="l in LACUNAS_DA_TELA" :key="l.chave" type="button" class="rounded border bg-background px-1.5 py-0.5 font-mono hover:bg-muted" :title="l.label" @click="inserirLacuna(l.chave)">{{ comChaves(l.chave) }}</button>
        <span
          v-if="limite"
          class="ml-auto tabular-nums"
          :class="tamanho > limite ? 'font-semibold text-red-600 dark:text-red-400' : 'text-muted-foreground'"
          :title="form.plataforma ? `limite da caixa escolhida` : 'vale para várias caixas: conta o menor limite (ML pós-venda, 350)'"
        >{{ tamanho }}/{{ limite }}</span>
      </div>
      <p v-if="limite && tamanho > limite" class="text-[11px] text-red-600 dark:text-red-400">
        Maior que o limite da caixa — nessa loja a mensagem seria recusada. Encurte ou restrinja a outra plataforma.
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <label class="inline-flex items-center gap-1.5 text-xs">
          <input v-model="form.ativo" type="checkbox" /> ativa (aparece no menu)
        </label>
        <div class="ml-auto flex gap-1.5">
          <Button size="sm" variant="ghost" @click="fechar"><X class="mr-1 size-4" /> cancelar</Button>
          <Button size="sm" :disabled="salvando || !form.titulo.trim() || !form.texto.trim()" @click="salvar">
            <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin" /><Check v-else class="mr-1.5 size-4" /> salvar
          </Button>
        </div>
      </div>
    </div>

    <div v-if="carregando && !modelos.length" class="px-3 py-6 text-center text-sm text-muted-foreground">
      <Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando…
    </div>
    <div v-else-if="!visiveis.length && !formAberto && !erro" class="px-3 py-8 text-center text-sm text-muted-foreground">
      {{ modelos.length ? 'Nada com esse filtro.' : 'Nenhuma resposta pronta ainda. As mais úteis costumam ser: rastreio enviado, nota fiscal, prazo de envio e agradecimento.' }}
    </div>

    <ul class="divide-y">
      <li v-for="m in visiveis" :key="m.id" class="flex items-start gap-3 px-3 py-2.5" :class="prontaAtiva(m) ? '' : 'opacity-60'">
        <span class="mt-0.5 w-8 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground" title="ordem no menu">{{ m.ordem ?? 0 }}</span>
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="text-sm font-medium">{{ m.titulo }}</span>
            <span class="rounded-full bg-muted px-2 py-0.5 text-[11px]">{{ alcance(m) }}</span>
            <span
              class="rounded-full px-2 py-0.5 text-[11px]"
              :class="m.equipe == null ? 'bg-muted text-muted-foreground' : 'bg-sky-500/15 text-sky-800 dark:text-sky-300'"
              data-equipe-pronta
            >{{ rotuloEquipe(m) }}</span>
            <span v-if="!m.ativo" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground">{{ m.equipe == null && !escolheEquipe ? 'desativada pelo admin' : 'desativada' }}</span>
            <span v-else-if="m.desativada_na_equipe" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground" data-desativada-na-equipe>desativada na sua equipe</span>
            <span v-if="escolheEquipe && m.desativada_equipes?.length" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground" title="desativada só para essas equipes; as outras continuam vendo" data-desativada-por>desativada por {{ m.desativada_equipes.join(', ') }}</span>
            <span
              v-if="limiteDe(m.plataforma, m.canal) && m.texto.length > (limiteDe(m.plataforma, m.canal) || 0)"
              class="rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] text-red-700 dark:text-red-300"
            >acima de {{ limiteDe(m.plataforma, m.canal) }} caracteres</span>
          </div>
          <div class="mt-0.5 line-clamp-3 whitespace-pre-line text-xs text-muted-foreground" :title="m.texto">{{ m.texto }}</div>
        </div>
        <div v-if="podeMexer(m)" class="flex shrink-0 items-center gap-0.5">
          <Button size="sm" variant="ghost" class="h-7 px-2 text-xs" :disabled="salvando" @click="alternar(m)">{{ m.ativo ? 'desativar' : 'ativar' }}</Button>
          <Button size="sm" variant="ghost" class="h-7 px-2" title="editar" @click="abrirEdicao(m)"><Pencil class="size-3.5" /></Button>
          <Button v-if="podeApagar(m)" size="sm" variant="ghost" class="h-7 px-2 text-red-600" title="apagar" :disabled="salvando" @click="apagar(m)"><Trash2 class="size-3.5" /></Button>
        </div>
        <div v-else-if="m.pode_desativar" class="flex shrink-0 items-center gap-0.5">
          <Button size="sm" variant="ghost" class="h-7 px-2 text-xs" title="só para a sua equipe: as outras continuam vendo" :disabled="salvando" data-alternar-na-equipe @click="alternarNaEquipe(m)">{{ m.desativada_na_equipe ? 'ativar' : 'desativar' }}</Button>
        </div>
      </li>
    </ul>
  </section>
</template>
