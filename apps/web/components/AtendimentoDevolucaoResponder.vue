<script lang="ts">
// A resposta da loja à devolução do TikTok (18/11/2026, o dono: "se der p eles
// responder a devolução por ali tbm" → tudo, inclusive o pacote). A janela do
// botão do bloco (AtendimentoDevolucaoTiktok): diz o que acontece no TikTok
// ANTES do clique final, pede o "entendi" e, na recusa, o motivo (a lista que o
// TikTok dá para o caso naquele estado), o comentário (até 500 bytes, o limite
// do TikTok) e as fotos (obrigatória ao recusar o pacote). O servidor relê o
// caso antes de mandar: se ele mudou, nada sai e a janela diz por quê.
//
// ACESSIBILIDADE (18/11/2026, o dono: "acessibilidade"): a janela recebe o foco ao
// abrir, prende o Tab dentro dela, fecha no Esc e devolve o foco a quem a abriu;
// título e efeito ligados por `aria-labelledby`/`aria-describedby`; cada campo com
// rótulo; o contador ligado ao comentário; o que falta para mandar aparece em
// TEXTO (não só na dica do mouse), num `aria-live`, ligado ao botão; o erro do
// TikTok em `role="alert"`.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-devolucao-tiktok.cjs — por isso não importa nada.

export const COMENTARIO_MAX_BYTES = 500
export const COMENTARIO_MIN = 5
export const MAX_FOTOS = 6
export const FOTO_MAX_BYTES = 5 * 1024 * 1024
export const FOTO_TIPOS = ['image/jpeg', 'image/png', 'image/webp']

// O Tab preso na janela (acessibilidade, 18/11/2026, o dono: "acessibilidade"):
// do último volta ao primeiro, e Shift+Tab do primeiro vai ao último. -1 = o
// foco está fora da lista (na própria janela): Tab vai ao primeiro, Shift+Tab ao último.
export function proximoFoco(atual: number, total: number, voltar: boolean): number {
  if (total <= 0) return -1
  if (atual < 0) return voltar ? total - 1 : 0
  return voltar ? (atual - 1 + total) % total : (atual + 1) % total
}
export const SELETOR_FOCAVEL = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function bytesUtf8(t: string): number {
  return new TextEncoder().encode(t).length
}

// O que falta para mandar (vazio = pode mandar). A mesma conferência do
// servidor (`devolucao_detalhe.conferir_pedido`), para a pessoa saber antes.
export function faltaParaMandar(
  acao: { recusa: boolean; exige_foto: boolean },
  f: { entendi: boolean; motivo: string; comentario: string; fotos: { size: number; type: string }[] },
): string[] {
  const falta: string[] = []
  if (acao.recusa) {
    if (!f.motivo) falta.push('Escolha o motivo da recusa.')
    const comentario = f.comentario.trim().replace(/\s+/g, ' ')
    if (comentario.length < COMENTARIO_MIN) falta.push('Explique a recusa no comentário (é o que o TikTok lê para decidir).')
    if (bytesUtf8(comentario) > COMENTARIO_MAX_BYTES) falta.push(`O comentário passa do limite do TikTok (${COMENTARIO_MAX_BYTES} bytes). Resuma.`)
    if (acao.exige_foto && !f.fotos.length) falta.push('Mande ao menos uma foto do produto que voltou.')
    if (f.fotos.length > MAX_FOTOS) falta.push(`No máximo ${MAX_FOTOS} fotos.`)
    if (f.fotos.some((x) => !FOTO_TIPOS.includes(x.type))) falta.push('Só foto JPG, PNG ou WEBP.')
    if (f.fotos.some((x) => !x.size || x.size > FOTO_MAX_BYTES)) falta.push('Foto vazia ou acima de 5 MB.')
  }
  if (!f.entendi) falta.push('Marque que entendeu o que vai acontecer no TikTok.')
  return falta
}
</script>

<script setup lang="ts">
import { ImagePlus, Loader2, X } from 'lucide-vue-next'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'
import type { DevolucaoAcao, DevolucaoDetalhe } from '~/components/AtendimentoDevolucaoTiktok.vue'

const props = defineProps<{
  acao: DevolucaoAcao
  // A base do caso: /api/atendimento/conversas/{id}/reclamacoes/{rid}
  url: string
  numero: string
}>()
const emit = defineEmits<{
  (e: 'fechar'): void
  (e: 'respondido', d: DevolucaoDetalhe): void
}>()
const { api } = useApi()
const uid = `dev-resp-${useId()}`
const ids = {
  titulo: `${uid}-titulo`, efeito: `${uid}-efeito`, motivo: `${uid}-motivo`, comentario: `${uid}-comentario`,
  contador: `${uid}-contador`, fotos: `${uid}-fotos`, falta: `${uid}-falta`,
}

// O foco: entra na janela ao abrir e volta para quem a abriu ao fechar.
const caixa = ref<HTMLElement | null>(null)
let quemAbriu: HTMLElement | null = null
onMounted(() => {
  quemAbriu = (globalThis.document?.activeElement as HTMLElement | null) ?? null
  void nextTick(() => caixa.value?.focus())
})
onBeforeUnmount(() => { quemAbriu?.focus?.() })
function prenderFoco(ev: KeyboardEvent) {
  const raiz = caixa.value
  if (!raiz) return
  const lista = Array.from(raiz.querySelectorAll<HTMLElement>(SELETOR_FOCAVEL))
  const alvo = lista[proximoFoco(lista.indexOf(globalThis.document?.activeElement as HTMLElement), lista.length, ev.shiftKey)]
  if (!alvo) return
  ev.preventDefault()
  alvo.focus()
}

const entendi = ref(false)
const motivo = ref('')
const comentario = ref('')
const fotos = ref<File[]>([])
const previas = ref<string[]>([])
const enviando = ref(false)
const erro = ref('')
const motivos = ref<{ codigo: string; texto: string }[]>([])
const lendoMotivos = ref(false)
const erroMotivos = ref('')

async function lerMotivos() {
  lendoMotivos.value = true
  erroMotivos.value = ''
  try {
    const r = await api<{ itens: { codigo: string; texto: string }[] }>(`${props.url}/motivos-recusa`)
    motivos.value = r.itens || []
    if (!motivos.value.length) erroMotivos.value = 'O TikTok não deu nenhum motivo de recusa para este caso agora.'
  } catch (e) {
    erroMotivos.value = erroDaApi(e, 'Não deu para ler os motivos no TikTok agora.').texto
  } finally {
    lendoMotivos.value = false
  }
}
onMounted(() => { if (props.acao.recusa) void lerMotivos() })

const falta = computed(() => faltaParaMandar(props.acao, {
  entendi: entendi.value, motivo: motivo.value, comentario: comentario.value, fotos: fotos.value,
}))
const bytes = computed(() => bytesUtf8(comentario.value.trim().replace(/\s+/g, ' ')))

function escolherFotos(ev: Event) {
  const input = ev.target as HTMLInputElement
  const novas = Array.from(input.files || [])
  input.value = ''
  for (const f of novas) {
    if (fotos.value.length >= MAX_FOTOS) break
    fotos.value.push(f)
    previas.value.push(URL.createObjectURL(f))
  }
}
function tirarFoto(i: number) {
  URL.revokeObjectURL(previas.value[i])
  fotos.value.splice(i, 1)
  previas.value.splice(i, 1)
}
onBeforeUnmount(() => { for (const p of previas.value) URL.revokeObjectURL(p) })

async function mandar() {
  if (falta.value.length || enviando.value) return
  enviando.value = true
  erro.value = ''
  const fd = new FormData()
  fd.append('acao', props.acao.codigo)
  if (props.acao.recusa) {
    fd.append('motivo', motivo.value)
    fd.append('comentario', comentario.value.trim().replace(/\s+/g, ' '))
    for (const f of fotos.value) fd.append('fotos', f, f.name)
  }
  try {
    const d = await api<DevolucaoDetalhe>(`${props.url}/responder`, { method: 'POST', body: fd })
    emit('respondido', d)
  } catch (e) {
    erro.value = erroDaApi(e, 'O TikTok não recebeu a resposta.').texto
  } finally {
    enviando.value = false
  }
}
function fechar() {
  // No meio do envio não fecha: a resposta é o que diz se o TikTok recebeu.
  if (!enviando.value) emit('fechar')
}
</script>

<template>
  <div
    class="fixed inset-0 z-[80] flex items-stretch justify-center bg-black/50 sm:items-center sm:p-4"
    data-dialogo-devolucao
    @click.self="fechar"
  >
    <div
      ref="caixa"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="ids.titulo"
      :aria-describedby="ids.efeito"
      tabindex="-1"
      class="flex max-h-full w-full flex-col bg-background text-xs shadow-2xl outline-none focus-visible:ring-2 focus-visible:ring-primary sm:max-h-[88vh] sm:max-w-md sm:rounded-lg sm:border"
      @keydown.esc.stop.prevent="fechar"
      @keydown.tab="prenderFoco"
    >
      <header class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
        <div class="min-w-0 flex-1">
          <h2 :id="ids.titulo" class="text-sm font-semibold">{{ acao.rotulo }}</h2>
          <p class="break-words text-[11px] text-muted-foreground">Devolução nº {{ numero }} · sai no TikTok, em nome da loja</p>
        </div>
        <button type="button" class="rounded p-1 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50" aria-label="fechar (Esc)" :disabled="enviando" @click="fechar"><X class="size-4" aria-hidden="true" /></button>
      </header>

      <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <p :id="ids.efeito" class="rounded-md border px-2.5 py-2 leading-relaxed" :class="acao.recusa ? 'border-red-500/40 bg-red-500/5' : 'border-amber-500/40 bg-amber-500/10'" data-efeito>{{ acao.efeito }}</p>

        <template v-if="acao.recusa">
          <div class="space-y-1">
            <label :for="ids.motivo" class="block font-medium">Motivo (lista do TikTok para este caso)</label>
            <select :id="ids.motivo" v-model="motivo" class="h-8 w-full rounded-md border bg-background px-2" :disabled="lendoMotivos || !motivos.length" :aria-busy="lendoMotivos" aria-required="true" data-motivo>
              <option value="" disabled>{{ lendoMotivos ? 'lendo os motivos no TikTok…' : 'escolha o motivo' }}</option>
              <option v-for="m in motivos" :key="m.codigo" :value="m.codigo">{{ m.texto }}</option>
            </select>
            <p v-if="erroMotivos" class="text-[11px] text-red-700 dark:text-red-300" role="alert">{{ erroMotivos }} <button type="button" class="underline" @click="lerMotivos">tentar de novo</button></p>
          </div>
          <div class="space-y-1">
            <label :for="ids.comentario" class="block font-medium">Comentário para o TikTok</label>
            <textarea :id="ids.comentario" v-model="comentario" rows="4" class="w-full rounded-md border bg-background p-2" placeholder="O que a loja viu: o estado do produto, o que falta, o que não confere com o pedido…" aria-required="true" :aria-describedby="ids.contador" :aria-invalid="bytes > COMENTARIO_MAX_BYTES" data-comentario />
            <p :id="ids.contador" class="text-right text-[11px] tabular-nums" :class="bytes > COMENTARIO_MAX_BYTES ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'">{{ bytes }} de {{ COMENTARIO_MAX_BYTES }} bytes</p>
          </div>
          <fieldset class="space-y-1">
            <legend :id="ids.fotos" class="font-medium">Fotos {{ acao.exige_foto ? '(obrigatória: o produto que voltou)' : '(opcional)' }}</legend>
            <ul class="flex flex-wrap gap-1.5" :aria-labelledby="ids.fotos">
              <li v-for="(p, i) in previas" :key="p" class="relative size-14 overflow-hidden rounded border">
                <img :src="p" :alt="`foto ${i + 1} para o TikTok`" class="size-full object-cover" />
                <button type="button" class="absolute right-0 top-0 rounded-bl bg-black/60 p-0.5 text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" :aria-label="`tirar a foto ${i + 1}`" @click="tirarFoto(i)"><X class="size-3" aria-hidden="true" /></button>
              </li>
              <li v-if="fotos.length < MAX_FOTOS">
                <label class="flex size-14 cursor-pointer items-center justify-center rounded border border-dashed text-muted-foreground focus-within:ring-2 focus-within:ring-primary hover:bg-muted">
                  <ImagePlus class="size-5" aria-hidden="true" />
                  <span class="sr-only">Adicionar fotos (até {{ MAX_FOTOS }}, JPG, PNG ou WEBP)</span>
                  <input type="file" accept="image/jpeg,image/png,image/webp" multiple class="sr-only" data-fotos @change="escolherFotos" />
                </label>
              </li>
            </ul>
          </fieldset>
        </template>

        <label class="flex items-start gap-2">
          <input v-model="entendi" type="checkbox" class="mt-0.5" data-entendi />
          <span>Entendi: isso vai para o TikTok agora e não dá para desfazer pelo DaVinci.</span>
        </label>
        <!-- O que falta para mandar: em texto, não só na dica do mouse. -->
        <div :id="ids.falta" aria-live="polite" data-falta>
          <ul v-if="falta.length" class="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
            <li v-for="f in falta" :key="f">{{ f }}</li>
          </ul>
        </div>
        <p v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-2 text-red-800 dark:text-red-200" role="alert" data-erro>{{ erro }}</p>
      </div>

      <footer class="flex shrink-0 items-center justify-end gap-2 border-t px-4 py-3">
        <button type="button" class="rounded-md border px-3 py-1.5 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50" :disabled="enviando" @click="fechar">Cancelar</button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          :class="[acao.recusa ? 'bg-red-700 hover:bg-red-800' : 'bg-emerald-700 hover:bg-emerald-800', falta.length || enviando ? 'cursor-not-allowed opacity-50' : '']"
          :aria-disabled="!!falta.length || enviando"
          :aria-describedby="falta.length ? ids.falta : undefined"
          :aria-busy="enviando"
          data-mandar
          @click="mandar"
        >
          <Loader2 v-if="enviando" class="size-3.5 animate-spin" aria-hidden="true" />
          {{ enviando ? 'mandando ao TikTok…' : `${acao.rotulo} no TikTok` }}
        </button>
      </footer>
    </div>
  </div>
</template>
