<script setup lang="ts">
// App Uranyx › Receitas: criar e editar uma receita. A foto sobe por upload
// (reduzida aqui para JPEG de 1600 px) e vai como foto_arquivo_id; o vídeo é
// o link do YouTube. Ingredientes e passos são "um por linha". O ajuste do
// aparelho (temperatura, tempo, função) é opcional; vazio vai como null.
import { Trash2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  DIFICULDADE_LABEL, erroAppUranyx, inteiroOuNull, linhasDoErro, linhasParaLista, listaParaLinhas,
  tagsDoTexto, youtubeValido,
  type Ajuste, type ArquivoEnviado, type Dificuldade, type ProdutoCatalogo, type Receita,
} from '~/lib/appUranyx'

const props = defineProps<{ open: boolean; receita: Receita | null; eletros: ProdutoCatalogo[] }>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'salva', r: Receita): void
  (e: 'apagada', id: string): void
}>()

const { chamar } = useAppUranyx()
const toasts = useToasts()

const form = reactive({
  titulo: '',
  youtube_url: '',
  tempo_min: '' as string | number,
  rendimento: '',
  dificuldade: 'facil' as Dificuldade,
  ingredientes: '',
  passos: '',
  temperatura_c: '' as string | number,
  ajuste_tempo_min: '' as string | number,
  funcao: '',
  tags: '',
  publicada: false,
  eletrodomesticos: [] as string[],
})
const fotoNova = ref<ArquivoEnviado | null>(null)
const salvando = ref(false)
const apagando = ref(false)
const erros = ref<string[]>([])
// O quadro de erros fica no topo da gaveta: quem salvou lá embaixo precisa vê-lo.
const caixaErros = ref<HTMLElement | null>(null)
watch(erros, (v) => {
  if (v.length) void nextTick(() => caixaErros.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
})

const editando = computed(() => !!props.receita)

function preencher() {
  const r = props.receita
  fotoNova.value = null
  erros.value = []
  form.titulo = r?.titulo ?? ''
  form.youtube_url = r?.youtube_url ?? ''
  form.tempo_min = r?.tempo_min ?? ''
  form.rendimento = r?.rendimento ?? ''
  form.dificuldade = r?.dificuldade ?? 'facil'
  form.ingredientes = listaParaLinhas(r?.ingredientes)
  form.passos = listaParaLinhas(r?.passos)
  form.temperatura_c = r?.ajuste?.temperatura_c ?? ''
  form.ajuste_tempo_min = r?.ajuste?.tempo_min ?? ''
  form.funcao = r?.ajuste?.funcao ?? ''
  form.tags = (r?.tags ?? []).join(', ')
  form.publicada = r?.publicada ?? false
  form.eletrodomesticos = (r?.eletrodomesticos ?? []).map((e) => e.id)
}
watch(() => [props.open, props.receita?.id], () => { if (props.open) preencher() }, { immediate: true })

// `eletros` é o GET /catalogo?categoria=eletrodomestico (com os inativos). O
// eletrodoméstico que a receita já tem e não está ali deixou de ser da
// categoria (o site ou o painel trocou a linha): continua na lista, marcado.
// A API do app recusa (422) a lista de eletrodomésticos que tiver um deles.
type OpcaoEletro = { id: string; nome: string; ativo: boolean; foraDaCategoria: boolean }
const opcoesEletros = computed(() => {
  const base: OpcaoEletro[] = props.eletros.map((p) => ({ id: p.id, nome: p.nome, ativo: p.ativo, foraDaCategoria: false }))
  for (const e of props.receita?.eletrodomesticos ?? []) {
    if (!base.some((b) => b.id === e.id)) base.push({ id: e.id, nome: e.nome, ativo: true, foraDaCategoria: true })
  }
  return base.sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'))
})
const foraDaCategoria = computed(() => new Set(opcoesEletros.value.filter((o) => o.foraDaCategoria).map((o) => o.id)))

function mesmosIds(a: string[], b: string[]): boolean {
  const x = [...new Set(a)].sort()
  const y = [...new Set(b)].sort()
  return x.length === y.length && x.every((v, i) => v === y[i])
}

// No PATCH os eletrodomésticos só vão quando a seleção mudou (a API troca a
// lista inteira), e já sem os que saíram da categoria: assim editar o resto
// da receita não trava no 422 por causa de um produto que mudou de linha.
function eletrosParaEnviar(): { enviar: boolean; ids: string[] } {
  const ids = form.eletrodomesticos.filter((id) => !foraDaCategoria.value.has(id))
  const antes = (props.receita?.eletrodomesticos ?? []).map((e) => e.id)
  return { enviar: !editando.value || !mesmosIds(form.eletrodomesticos, antes), ids }
}

function montarAjuste(): { ajuste: Ajuste | null; problemas: string[] } {
  const problemas: string[] = []
  const ajuste: Ajuste = {}
  if (form.temperatura_c !== '') {
    const t = inteiroOuNull(form.temperatura_c, 30, 300)
    if (t === null) problemas.push('Ajuste: a temperatura vai de 30 a 300 °C.')
    else ajuste.temperatura_c = t
  }
  if (form.ajuste_tempo_min !== '') {
    const t = inteiroOuNull(form.ajuste_tempo_min, 1, 600)
    if (t === null) problemas.push('Ajuste: o tempo vai de 1 a 600 minutos.')
    else ajuste.tempo_min = t
  }
  const f = form.funcao.trim()
  if (f) ajuste.funcao = f.slice(0, 60)
  return { ajuste: Object.keys(ajuste).length ? ajuste : null, problemas }
}

async function salvar() {
  if (salvando.value) return
  const problemas: string[] = []
  const titulo = form.titulo.trim()
  if (titulo.length < 2) problemas.push('Título: use pelo menos 2 letras.')
  const youtube = form.youtube_url.trim()
  if (!youtube) problemas.push('Link do YouTube: obrigatório.')
  else if (!youtubeValido(youtube)) problemas.push('Link do YouTube: use um link https:// do YouTube (youtube.com ou youtu.be).')
  const tempo = inteiroOuNull(form.tempo_min, 1, 1440)
  if (tempo === null) problemas.push('Tempo: de 1 a 1440 minutos.')
  const rendimento = form.rendimento.trim()
  if (!rendimento) problemas.push('Rendimento: obrigatório (ex.: 4 porções).')
  const ingredientes = linhasParaLista(form.ingredientes)
  if (!ingredientes.length) problemas.push('Ingredientes: pelo menos um (um por linha).')
  const passos = linhasParaLista(form.passos)
  if (!passos.length) problemas.push('Modo de preparo: pelo menos um passo (um por linha).')
  const selecao = eletrosParaEnviar()
  if (selecao.enviar && !selecao.ids.length) {
    problemas.push(form.eletrodomesticos.length
      ? 'Eletrodomésticos: marque pelo menos um que ainda seja eletrodoméstico.'
      : 'Eletrodomésticos: marque pelo menos um.')
  }
  if (!editando.value && !fotoNova.value) problemas.push('Foto: escolha a foto da receita.')
  const { ajuste, problemas: pAjuste } = montarAjuste()
  problemas.push(...pAjuste)
  erros.value = problemas
  if (problemas.length) return

  const corpo: Record<string, unknown> = {
    titulo,
    youtube_url: youtube,
    tempo_min: tempo,
    rendimento,
    dificuldade: form.dificuldade,
    ingredientes,
    passos,
    ajuste,
    tags: tagsDoTexto(form.tags),
    publicada: form.publicada,
  }
  if (selecao.enviar) corpo.eletrodomesticos = selecao.ids
  if (fotoNova.value) corpo.foto_arquivo_id = fotoNova.value.id

  salvando.value = true
  try {
    const r = props.receita
      ? await chamar<Receita>(`receitas/${props.receita.id}`, { method: 'PATCH', body: corpo })
      : await chamar<Receita>('receitas', { method: 'POST', body: corpo })
    emit('salva', r)
    toasts.success(props.receita ? 'Receita salva' : 'Receita criada', r.publicada ? 'Já aparece no app.' : 'Rascunho: só aparece no app depois de publicar.')
    emit('update:open', false)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para salvar a receita')
    erros.value = [er.texto, ...linhasDoErro(er)]
  } finally {
    salvando.value = false
  }
}

async function apagar() {
  const r = props.receita
  if (!r || apagando.value) return
  if (!window.confirm(`Apagar a receita "${r.titulo}"? Ela some do app, inclusive dos favoritos.`)) return
  apagando.value = true
  try {
    await chamar(`receitas/${r.id}`, { method: 'DELETE' })
    emit('apagada', r.id)
    toasts.success('Receita apagada', r.titulo)
    emit('update:open', false)
  } catch (e: any) {
    toasts.error('Não deu para apagar', erroAppUranyx(e, 'Não deu para apagar').texto)
  } finally {
    apagando.value = false
  }
}

const campo = 'h-9 w-full rounded-md border bg-background px-2 text-sm'
const area = 'w-full rounded-md border bg-background px-2 py-1.5 text-sm'
</script>

<template>
  <AppUranyxGaveta
    :open="open"
    :titulo="receita ? receita.titulo : 'Nova receita'"
    :subtitulo="receita ? (receita.publicada ? 'Publicada: aparece no app' : 'Rascunho: não aparece no app') : 'Aparece no app de quem tem o eletrodoméstico, depois de publicar'"
    @update:open="(v) => emit('update:open', v)"
  >
    <div v-if="erros.length" ref="caixaErros" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
      <p v-for="(l, i) in erros" :key="i">{{ l }}</p>
    </div>

    <section class="space-y-3">
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Título</span>
        <input v-model="form.titulo" maxlength="120" :class="campo" placeholder="ex.: Batata frita na Air Fryer" />
      </label>
      <div class="space-y-1 text-sm">
        <span class="text-muted-foreground">Foto</span>
        <AppUranyxFoto :url="receita?.foto_url" comprimir rotulo="Foto" @enviado="(a) => (fotoNova = a)" />
      </div>
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Vídeo (link do YouTube)</span>
        <input v-model="form.youtube_url" type="url" maxlength="500" :class="campo" placeholder="https://www.youtube.com/watch?v=…" />
      </label>
      <div class="grid gap-3 sm:grid-cols-3">
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Tempo (min)</span>
          <input v-model="form.tempo_min" type="number" min="1" max="1440" step="1" :class="campo" />
        </label>
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Rendimento</span>
          <input v-model="form.rendimento" maxlength="60" :class="campo" placeholder="ex.: 4 porções" />
        </label>
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Dificuldade</span>
          <select v-model="form.dificuldade" :class="campo">
            <option v-for="(label, valor) in DIFICULDADE_LABEL" :key="valor" :value="valor">{{ label }}</option>
          </select>
        </label>
      </div>
    </section>

    <section class="space-y-3">
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Ingredientes (um por linha)</span>
        <textarea v-model="form.ingredientes" rows="6" :class="area" placeholder="500 g de batata&#10;1 colher de azeite&#10;Sal a gosto" />
      </label>
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Modo de preparo (um passo por linha)</span>
        <textarea v-model="form.passos" rows="6" :class="area" placeholder="Corte as batatas em palitos.&#10;Tempere com azeite e sal.&#10;Asse por 20 minutos, mexendo na metade." />
      </label>
    </section>

    <section class="space-y-2">
      <h3 class="text-sm font-semibold">Ajuste do aparelho <span class="font-normal text-muted-foreground">(opcional)</span></h3>
      <div class="grid gap-3 sm:grid-cols-3">
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Temperatura (°C)</span>
          <input v-model="form.temperatura_c" type="number" min="30" max="300" step="1" :class="campo" />
        </label>
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Tempo (min)</span>
          <input v-model="form.ajuste_tempo_min" type="number" min="1" max="600" step="1" :class="campo" />
        </label>
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Função</span>
          <input v-model="form.funcao" maxlength="60" :class="campo" placeholder="ex.: Fritar" />
        </label>
      </div>
    </section>

    <section class="space-y-2">
      <h3 class="text-sm font-semibold">Eletrodomésticos</h3>
      <p v-if="!opcoesEletros.length" class="text-sm text-amber-700 dark:text-amber-400">
        Nenhum eletrodoméstico no catálogo. A receita precisa de pelo menos um (sincronize o catálogo com o site).
      </p>
      <div v-else class="grid gap-1.5 sm:grid-cols-2">
        <label v-for="e in opcoesEletros" :key="e.id" class="flex items-center gap-2 text-sm">
          <input v-model="form.eletrodomesticos" type="checkbox" :value="e.id" class="size-4" />
          <span :class="e.foraDaCategoria ? 'text-amber-700 dark:text-amber-400' : { 'text-muted-foreground': !e.ativo }">
            {{ e.nome }}{{ e.foraDaCategoria ? ' (não é mais eletrodoméstico)' : e.ativo ? '' : ' (inativo)' }}
          </span>
        </label>
      </div>
      <p v-if="foraDaCategoria.size" class="text-xs text-muted-foreground">
        O que não é mais eletrodoméstico fica na receita enquanto a seleção não mudar. Ao mudar a seleção, ele sai (a API
        do app só aceita eletrodomésticos).
      </p>
    </section>

    <section class="space-y-3">
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Tags (separadas por vírgula)</span>
        <input v-model="form.tags" :class="campo" placeholder="ex.: lanche, rápido, sem glúten" />
      </label>
      <label class="flex items-center gap-2 text-sm">
        <input v-model="form.publicada" type="checkbox" class="size-4" />
        Publicada (aparece no app)
      </label>
    </section>

    <template #rodape>
      <Button v-if="receita" variant="ghost" class="text-red-600 hover:text-red-700" :disabled="apagando" @click="apagar">
        <Trash2 class="mr-1 size-4" /> Apagar
      </Button>
      <Button variant="ghost" class="ml-auto" @click="emit('update:open', false)">Cancelar</Button>
      <Button :disabled="salvando" @click="salvar">{{ salvando ? 'Salvando…' : receita ? 'Salvar' : 'Criar receita' }}</Button>
    </template>
  </AppUranyxGaveta>
</template>
