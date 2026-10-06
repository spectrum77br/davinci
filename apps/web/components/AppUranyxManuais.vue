<script setup lang="ts">
// App Uranyx › Produto › Manuais: PDFs do produto que o app mostra no bloco
// "Manual" (só os ativos, pela ordem). Subir = o PDF vai para
// /arquivos e depois vira manual do produto; apagar o manual apaga o arquivo
// (a API do app cuida disso, se ninguém mais usa).
import { ArrowDown, ArrowUp, ExternalLink, FileText, Loader2, Pencil, Trash2, Upload } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  erroAppUranyx, formatarTamanho, linhasDoErro, problemaPdf, tituloDoArquivo, type Manual,
} from '~/lib/appUranyx'

const props = defineProps<{ catalogoId: string; produtoNome?: string }>()

const { chamar, subirArquivo } = useAppUranyx()
const toasts = useToasts()

const manuais = ref<Manual[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const subindo = ref(false)
const ocupado = ref<string | null>(null)
const entrada = ref<HTMLInputElement | null>(null)

// Renomear: um de cada vez, na própria linha.
const renomeando = ref<string | null>(null)
const novoTitulo = ref('')

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const r = await chamar<Manual[]>(`catalogo/${props.catalogoId}/manuais`)
    if (meu !== seq) return
    manuais.value = [...r].sort((a, b) => a.ordem - b.ordem)
  } catch (e: any) {
    if (meu !== seq) return
    erro.value = erroAppUranyx(e, 'Não deu para carregar os manuais').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
watch(() => props.catalogoId, () => { renomeando.value = null; void carregar() }, { immediate: true })

function avisarErro(titulo: string, e: any) {
  const er = erroAppUranyx(e, titulo)
  toasts.error(titulo, [er.texto, ...linhasDoErro(er)])
}

function escolher() {
  if (!subindo.value) entrada.value?.click()
}

async function aoEscolher(ev: Event) {
  const alvo = ev.target as HTMLInputElement
  const arquivo = alvo.files?.[0]
  alvo.value = ''
  if (!arquivo) return
  const problema = problemaPdf(arquivo)
  if (problema) { toasts.error('O manual não subiu', problema); return }
  subindo.value = true
  const titulo = tituloDoArquivo(arquivo.name)
  let arquivoId: string | null = null
  try {
    const enviado = await subirArquivo(arquivo, titulo)
    arquivoId = enviado.id
    const ordem = manuais.value.length ? Math.max(...manuais.value.map((m) => m.ordem)) + 1 : 0
    await chamar(`catalogo/${props.catalogoId}/manuais`, {
      method: 'POST',
      body: { titulo, arquivo_id: enviado.id, ordem },
    })
    toasts.success('Manual adicionado', `"${titulo}" já aparece no app${props.produtoNome ? ` em ${props.produtoNome}` : ''}.`)
    await carregar()
  } catch (e: any) {
    avisarErro('O manual não subiu', e)
    // O PDF subiu mas o manual não foi criado: tira o arquivo solto (melhor esforço).
    if (arquivoId) chamar(`arquivos/${arquivoId}`, { method: 'DELETE' }).catch(() => {})
  } finally {
    subindo.value = false
  }
}

function comecarRenomear(m: Manual) {
  renomeando.value = m.id
  novoTitulo.value = m.titulo
}

async function salvarTitulo(m: Manual) {
  const titulo = novoTitulo.value.trim()
  if (!titulo || titulo.length > 80) { toasts.error('Título inválido', 'Use de 1 a 80 caracteres.'); return }
  if (titulo === m.titulo) { renomeando.value = null; return }
  ocupado.value = m.id
  try {
    await chamar(`manuais/${m.id}`, { method: 'PATCH', body: { titulo } })
    m.titulo = titulo
    renomeando.value = null
  } catch (e: any) {
    avisarErro('Não deu para renomear', e)
  } finally {
    ocupado.value = null
  }
}

async function mudarAtivo(m: Manual, ev: Event) {
  const caixa = ev.target as HTMLInputElement
  const ativo = caixa.checked
  ocupado.value = m.id
  try {
    await chamar(`manuais/${m.id}`, { method: 'PATCH', body: { ativo } })
    m.ativo = ativo
  } catch (e: any) {
    caixa.checked = m.ativo // a caixa volta a mostrar o que está valendo
    avisarErro(ativo ? 'Não deu para ativar' : 'Não deu para desativar', e)
  } finally {
    ocupado.value = null
  }
}

// Sobe/desce um manual e grava a ordem nova só de quem mudou.
async function mover(indice: number, delta: -1 | 1) {
  const alvo = indice + delta
  if (alvo < 0 || alvo >= manuais.value.length || ocupado.value) return
  const nova = [...manuais.value]
  const [m] = nova.splice(indice, 1)
  nova.splice(alvo, 0, m)
  const mudancas = nova.map((x, i) => ({ x, ordem: i })).filter(({ x, ordem }) => x.ordem !== ordem)
  const antes = manuais.value
  manuais.value = nova
  ocupado.value = m.id
  try {
    for (const { x, ordem } of mudancas) {
      await chamar(`manuais/${x.id}`, { method: 'PATCH', body: { ordem } })
      x.ordem = ordem
    }
  } catch (e: any) {
    manuais.value = antes
    avisarErro('Não deu para mudar a ordem', e)
    await carregar()
  } finally {
    ocupado.value = null
  }
}

async function apagar(m: Manual) {
  if (!window.confirm(`Apagar o manual "${m.titulo}"? Ele some do app na hora.`)) return
  ocupado.value = m.id
  try {
    await chamar(`manuais/${m.id}`, { method: 'DELETE' })
    manuais.value = manuais.value.filter((x) => x.id !== m.id)
    toasts.success('Manual apagado')
  } catch (e: any) {
    avisarErro('Não deu para apagar', e)
  } finally {
    ocupado.value = null
  }
}
</script>

<template>
  <section class="space-y-2">
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="text-sm font-semibold">Manuais (PDF)</h3>
      <span class="text-xs text-muted-foreground">o app mostra só os ativos, nesta ordem</span>
      <input ref="entrada" type="file" accept="application/pdf,.pdf" class="hidden" aria-hidden="true" tabindex="-1" @change="aoEscolher" />
      <Button type="button" size="sm" variant="outline" class="ml-auto" :disabled="subindo" @click="escolher">
        <Loader2 v-if="subindo" class="mr-1 size-4 animate-spin" />
        <Upload v-else class="mr-1 size-4" />
        {{ subindo ? 'Subindo…' : 'Subir PDF' }}
      </Button>
    </div>

    <p v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</p>
    <p v-else-if="carregando && !manuais.length" class="text-sm text-muted-foreground">Carregando…</p>
    <p v-else-if="!manuais.length" class="rounded-md border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
      Nenhum manual. Suba o PDF (até 25 MB) e ele aparece no app, na página do produto.
    </p>

    <ul v-if="manuais.length" class="divide-y rounded-md border">
      <li v-for="(m, i) in manuais" :key="m.id" class="flex flex-wrap items-center gap-2 px-3 py-2 text-sm" :class="{ 'opacity-60': !m.ativo }">
        <div class="flex flex-col">
          <button type="button" class="rounded p-0.5 hover:bg-muted disabled:opacity-30" :disabled="i === 0 || !!ocupado" aria-label="Subir na ordem" @click="mover(i, -1)">
            <ArrowUp class="size-3.5" />
          </button>
          <button type="button" class="rounded p-0.5 hover:bg-muted disabled:opacity-30" :disabled="i === manuais.length - 1 || !!ocupado" aria-label="Descer na ordem" @click="mover(i, 1)">
            <ArrowDown class="size-3.5" />
          </button>
        </div>
        <FileText class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <form v-if="renomeando === m.id" class="flex gap-1.5" @submit.prevent="salvarTitulo(m)">
            <input v-model="novoTitulo" maxlength="80" class="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm" aria-label="Título do manual" />
            <Button type="submit" size="sm" :disabled="ocupado === m.id">Salvar</Button>
            <Button type="button" size="sm" variant="ghost" @click="renomeando = null">Cancelar</Button>
          </form>
          <template v-else>
            <div class="truncate font-medium" :title="m.titulo">{{ m.titulo }}</div>
            <div v-if="m.tamanho" class="text-xs text-muted-foreground">{{ formatarTamanho(m.tamanho) }}</div>
          </template>
        </div>
        <template v-if="renomeando !== m.id">
          <label class="inline-flex items-center gap-1 text-xs">
            <input type="checkbox" class="size-4" :checked="m.ativo" :disabled="ocupado === m.id" @change="mudarAtivo(m, $event)" />
            ativo
          </label>
          <a
            v-if="m.url"
            :href="m.url"
            target="_blank"
            rel="noopener noreferrer"
            class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Abrir o PDF"
            aria-label="Abrir o PDF"
          >
            <ExternalLink class="size-4" />
          </a>
          <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="Renomear" aria-label="Renomear" :disabled="!!ocupado" @click="comecarRenomear(m)">
            <Pencil class="size-4" />
          </button>
          <button type="button" class="rounded p-1 text-muted-foreground hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40" title="Apagar" aria-label="Apagar manual" :disabled="!!ocupado" @click="apagar(m)">
            <Trash2 class="size-4" />
          </button>
        </template>
      </li>
    </ul>
  </section>
</template>
