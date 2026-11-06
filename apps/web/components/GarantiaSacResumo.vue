<script setup lang="ts">
// O "Resumo do SAC" (06/11/2026, "Painel de Garantia: alterações" v3, §3 da
// aba Atendimento): bloco recolhível no topo da conversa com o que o cliente
// preencheu no formulário do site — as seções Atendimento, Comprador, Compra,
// Produto e Defeito (lidas no servidor: GET /api/garantias/{id}/sac; o CPF já
// vem mascarado para quem não tem "Ver o CPF completo") e a Documentação: as
// fotos, o vídeo e o comprovante do e-mail, que abrem AQUI. Os arquivos vêm de
// rota autenticada (GET /api/garantias/sac/{id}/anexos/{anexo}): a tela busca
// cada um como blob e mostra pelo endereço local (`URL.createObjectURL`),
// devolvido ao navegador quando o bloco sai da tela ou o caso muda.
import { onKeyStroke } from '@vueuse/core'
import { ChevronDown, ChevronRight, ClipboardList, Download, FileText, Loader2, Video } from 'lucide-vue-next'
import { dataHoraBR, errosDaApi, tamanhoLegivel, type SacAnexo, type SacDetalhe } from '~/lib/garantias'

const props = defineProps<{ sac: SacDetalhe }>()

const { api } = useApi()
const toasts = useToasts()

const aberto = ref(true)
const tipoDo = (a: SacAnexo) => (a.tipo || '').toLowerCase()
const ehFoto = (a: SacAnexo) => tipoDo(a).startsWith('image/') || /\.(png|jpe?g|webp|gif|heic)$/i.test(a.nome || '')
const ehVideo = (a: SacAnexo) => tipoDo(a).startsWith('video/') || /\.(mp4|mov|m4v|webm|3gp)$/i.test(a.nome || '')
const ehPdf = (a: SacAnexo) => tipoDo(a) === 'application/pdf' || /\.pdf$/i.test(a.nome || '')
const fotos = computed(() => props.sac.anexos.filter(ehFoto))
const outros = computed(() => props.sac.anexos.filter((a) => !ehFoto(a)))

// Os endereços locais dos arquivos já baixados (por id do anexo).
const locais = ref<Record<string, string>>({})
const baixando = ref<Set<string>>(new Set())
const falhou = ref<Set<string>>(new Set())
// Saiu da tela (trocou de caso, fechou o painel) com um arquivo chegando: ele
// não vira endereço local — ninguém mais o devolveria ao navegador (revisão
// 06/11/2026).
let desmontado = false
function liberar() {
  for (const u of Object.values(locais.value)) {
    try { URL.revokeObjectURL(u) } catch { /* já liberado */ }
  }
  locais.value = {}
  falhou.value = new Set()
}
async function baixar(a: SacAnexo): Promise<string | null> {
  if (locais.value[a.id]) return locais.value[a.id]
  if (baixando.value.has(a.id)) return null
  baixando.value = new Set([...baixando.value, a.id])
  const caso = props.sac.id
  try {
    const blob = await api<Blob>(a.url, { responseType: 'blob' })
    // O caso mudou ou o bloco saiu da tela enquanto baixava: não guarda.
    if (desmontado || props.sac.id !== caso) return null
    const u = URL.createObjectURL(blob)
    locais.value = { ...locais.value, [a.id]: u }
    return u
  } catch (e) {
    if (desmontado) return null
    falhou.value = new Set([...falhou.value, a.id])
    toasts.error(`Não consegui abrir ${a.nome || 'o arquivo'}`, errosDaApi(e).geral)
    return null
  } finally {
    const s = new Set(baixando.value)
    s.delete(a.id)
    baixando.value = s
  }
}
// As fotos entram em miniatura quando o bloco está aberto (o vídeo e o PDF só
// no clique: podem ser grandes).
watch([() => props.sac.id, aberto], ([, ab], antes) => {
  if (antes && antes[0] !== props.sac.id) liberar()
  if (ab) for (const a of fotos.value) void baixar(a)
}, { immediate: true })
onBeforeUnmount(() => {
  desmontado = true
  liberar()
})

const fotoAberta = ref<{ url: string; nome: string } | null>(null)
onKeyStroke('Escape', () => { fotoAberta.value = null })
async function abrirFoto(a: SacAnexo) {
  const u = await baixar(a)
  if (u) fotoAberta.value = { url: u, nome: a.nome || 'foto' }
}
const videoAberto = ref<string | null>(null)
async function abrirVideo(a: SacAnexo) {
  const u = await baixar(a)
  if (u) videoAberto.value = a.id
}
// PDF: numa aba nova (aberta já no clique, para o navegador não bloquear;
// o endereço entra quando o arquivo chega). O resto: salvar o arquivo.
async function abrirArquivo(a: SacAnexo) {
  if (ehPdf(a)) {
    const janela = typeof window !== 'undefined' ? window.open('', '_blank') : null
    const u = await baixar(a)
    if (u && janela) janela.location.href = u
    else if (janela) janela.close()
    return
  }
  const u = await baixar(a)
  if (!u) return
  const link = document.createElement('a')
  link.href = u
  link.download = a.nome || 'arquivo'
  link.click()
}
function rotulo(a: SacAnexo): string {
  return a.papel || a.nome || 'arquivo'
}
</script>

<template>
  <section class="rounded-lg border border-amber-300/70 bg-amber-50/60 text-sm dark:border-amber-800/60 dark:bg-amber-900/20" data-sac-resumo>
    <button
      type="button"
      class="flex w-full items-center gap-1.5 px-3 py-2 text-left"
      :aria-expanded="aberto"
      data-sac-resumo-alternar
      @click="aberto = !aberto"
    >
      <ChevronDown v-if="aberto" class="size-4 shrink-0" aria-hidden="true" />
      <ChevronRight v-else class="size-4 shrink-0" aria-hidden="true" />
      <ClipboardList class="size-4 shrink-0 text-amber-800 dark:text-amber-200" aria-hidden="true" />
      <span class="font-semibold text-amber-900 dark:text-amber-200">Resumo do SAC</span>
      <span class="text-xs text-muted-foreground">protocolo {{ sac.protocolo }} · {{ dataHoraBR(sac.recebido_em) }}</span>
    </button>
    <div v-if="aberto" class="space-y-3 border-t border-amber-300/50 px-3 py-2.5 dark:border-amber-800/40" data-sac-resumo-corpo>
      <div class="grid gap-3 lg:grid-cols-2">
        <div v-for="sec in sac.secoes" :key="sec.titulo" class="space-y-1" :data-sac-secao="sec.titulo">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{{ sec.titulo }}</h3>
          <dl class="grid grid-cols-[minmax(7rem,auto),1fr] gap-x-3 gap-y-0.5 text-[13px]">
            <template v-for="c in sec.campos" :key="c.rotulo">
              <dt class="text-muted-foreground">{{ c.rotulo }}</dt>
              <dd class="whitespace-pre-wrap break-words">{{ c.valor || '—' }}</dd>
            </template>
          </dl>
        </div>
      </div>
      <div class="space-y-1.5" data-sac-documentacao>
        <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Documentação</h3>
        <p v-if="!sac.anexos.length" class="text-xs text-muted-foreground">Nenhum arquivo chegou com o e-mail do formulário.</p>
        <div v-if="fotos.length" class="flex flex-wrap gap-2">
          <figure v-for="a in fotos" :key="a.id" class="w-28 space-y-0.5" :data-sac-anexo="a.id">
            <button type="button" class="flex size-28 items-center justify-center overflow-hidden rounded-md border bg-background" :title="`abrir ${rotulo(a)}`" @click="abrirFoto(a)">
              <img v-if="locais[a.id]" :src="locais[a.id]" :alt="rotulo(a)" class="size-full object-cover" />
              <Loader2 v-else-if="baixando.has(a.id)" class="size-4 animate-spin text-muted-foreground" />
              <span v-else class="px-1 text-center text-[10px] text-muted-foreground">{{ falhou.has(a.id) ? 'não abriu — tocar para tentar' : 'foto' }}</span>
            </button>
            <figcaption class="break-words text-[10px] leading-tight text-muted-foreground">{{ rotulo(a) }}</figcaption>
          </figure>
        </div>
        <ul v-if="outros.length" class="space-y-1.5">
          <li v-for="a in outros" :key="a.id" class="space-y-1" :data-sac-anexo="a.id">
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <button
                v-if="ehVideo(a)"
                type="button"
                class="inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted"
                :disabled="baixando.has(a.id)"
                @click="abrirVideo(a)"
              >
                <Loader2 v-if="baixando.has(a.id)" class="size-3.5 animate-spin" /><Video v-else class="size-3.5" /> {{ rotulo(a) }}
              </button>
              <button
                v-else
                type="button"
                class="inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted"
                :disabled="baixando.has(a.id)"
                :title="ehPdf(a) ? 'abrir numa aba nova' : 'salvar o arquivo'"
                @click="abrirArquivo(a)"
              >
                <Loader2 v-if="baixando.has(a.id)" class="size-3.5 animate-spin" /><FileText v-else-if="ehPdf(a)" class="size-3.5" /><Download v-else class="size-3.5" /> {{ rotulo(a) }}
              </button>
              <span v-if="a.papel && a.nome" class="break-all text-muted-foreground">{{ a.nome }}</span>
              <span v-if="a.tamanho" class="text-muted-foreground">{{ tamanhoLegivel(a.tamanho) }}</span>
            </div>
            <video v-if="ehVideo(a) && videoAberto === a.id && locais[a.id]" :src="locais[a.id]" controls playsinline preload="metadata" class="max-h-72 max-w-full rounded-md border bg-black" data-sac-video />
          </li>
        </ul>
      </div>
    </div>

    <!-- foto grande -->
    <div v-if="fotoAberta" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" role="dialog" aria-label="foto" @click.self="fotoAberta = null">
      <img :src="fotoAberta.url" :alt="fotoAberta.nome" class="max-h-full max-w-full rounded-md object-contain" />
    </div>
  </section>
</template>
