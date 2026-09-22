<script setup lang="ts">
// Aba Roteiros — o briefing, fora da linha de produção.
//
// Eduardo, 21/09/2026: "criaremos em marketing uma aba roteiro e lá poderemos
// escrever o roteiro pro item e também poderemos criar personagens que vão
// aparecer também no nosso domínio (...) em roteiro talvez a gente já mande
// pra alguém específico (...) uma regra também de que se não preenchido vai
// para os 2".
//
// UMA aba, duas seções — não duas abas. O pedido é "uma aba roteiro" onde
// também se criam os personagens.
//
// ## A regra invertida do destino
//
// Destino VAZIO = as DUAS agências veem. É o oposto da coluna Equipe da aba
// Criativos, onde vazio = ninguém de fora vê. A tela diz isso com todas as
// letras no seletor, porque a diferença não é adivinhável.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  Plus, Trash2, Loader2, Search, Upload, X, Link2, ExternalLink,
  Users, NotebookPen, Eye, EyeOff, CornerDownLeft, Image as ImageIcon,
  File as FileIcon,
} from 'lucide-vue-next'

const props = defineProps<{ foco?: string | null }>()

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('marketing_criativos', 'edit')

type Anexo = {
  id: string
  file_name: string | null
  file_mime: string | null
  file_size: number | null
}
type Ref_ = {
  id: string
  tipo: 'imagem' | 'link'
  titulo: string | null
  url: string | null
  file_name: string | null
  file_mime: string | null
  file_size: number | null
}
type Personagem = {
  id: string
  nome: string
  descricao: string | null
  // Atalho pra quem usa a MESMA ferramenta que gerou o rosto: `<<<uuid>>>` e
  // `hf_2026…` são ids DENTRO dela e somem se o asset for apagado, se trocar
  // de conta ou se a agência usar outro gerador. Quem DURA é o arquivo.
  referencia: string | null
  // Como a persona se move e fala (um Shorts, normalmente).
  video_url: string | null
  ativo: boolean
  imagens: Anexo[]
  vozes: Anexo[]
}
type Roteiro = {
  id: string
  titulo: string
  texto: string | null
  marca: string | null
  // Resolvidos pelo servidor a partir do texto de marca/SKU — a tela só lê.
  marca_id: string | null
  product_id: string | null
  sku: string | null
  equipe_destino: string | null
  ativo: boolean
  referencias: Ref_[]
  personagens: Personagem[]
  created_at: string | null
}

const ERR_PT: Record<string, string> = {
  titulo_obrigatorio: 'O roteiro precisa de um título.',
  nome_obrigatorio: 'O personagem precisa de um nome.',
  personagem_repetido: 'Já existe um personagem com esse nome.',
  roteiro_nao_encontrado: 'Esse roteiro não existe mais.',
  personagem_nao_encontrado: 'Esse personagem não existe mais.',
  referencia_nao_encontrada: 'Essa referência não existe mais.',
  imagem_nao_encontrada: 'Essa imagem não existe mais.',
  fora_da_sua_equipe: 'Esse roteiro é de outra equipe de marketing.',
  muitas_referencias: 'Limite de 30 referências por roteiro.',
  muitas_imagens: 'Limite de 30 imagens por personagem.',
  extensao_nao_aceita:
    'Só JPG, PNG, WEBP, GIF (e PDF na referência). SVG e HTML ficam de fora — rodam script no navegador de quem abrir.',
  referencia_grande_demais: 'Arquivo acima de 25 MB.',
  imagem_grande_demais: 'Imagem acima de 25 MB.',
  link_invalido: 'O link precisa começar com http:// ou https://.',
  link_vazio: 'Escreva o link.',
  link_longo_demais: 'Link longo demais.',
  forbidden: 'Você não tem permissão pra isso.',
}

function errMsg(e: any): string {
  const code = e?.data?.detail?.code
  if (code && ERR_PT[code]) return ERR_PT[code]
  return e?.data?.detail?.message ?? e?.message ?? 'Erro inesperado'
}

const secao = ref<'roteiros' | 'personagens'>('roteiros')

// ---- roteiros -------------------------------------------------------------
const roteiros = ref<Roteiro[]>([])
const personagens = ref<Personagem[]>([])
const destinos = ref<string[]>([])
const marcas = ref<{ nome: string; slug: string }[]>([])
const carregando = ref(false)
const selId = ref<string | null>(null)
const q = ref('')

const sel = computed(() => roteiros.value.find((r) => r.id === selId.value) ?? null)

// O texto do roteiro tem modelo LOCAL, e não `:value="sel.texto"`.
// Amarrado no estado do servidor, QUALQUER resposta que chegasse enquanto
// alguém digita (anexar imagem, colar link, ligar personagem, um PATCH de
// outro campo) reescrevia o textarea e jogava fora o que estava sendo
// escrito — e roteiro é texto longo, ninguém redigita.
const textoLocal = ref('')
watch(sel, (r) => { textoLocal.value = r?.texto ?? '' }, { immediate: true })

const listaFiltrada = computed(() => {
  const t = q.value.trim().toLowerCase()
  if (!t) return roteiros.value
  return roteiros.value.filter((r) =>
    [r.titulo, r.marca, r.sku, r.texto, r.equipe_destino]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(t),
  )
})

async function carregar() {
  carregando.value = true
  try {
    const [rs, ps, ds] = await Promise.all([
      api<Roteiro[]>('/api/marketing/roteiros'),
      api<Personagem[]>('/api/marketing/personagens'),
      api<string[]>('/api/marketing/roteiros/destinos').catch(() => [] as string[]),
    ])
    roteiros.value = rs
    personagens.value = ps
    destinos.value = ds
    if (!selId.value && rs.length) selId.value = rs[0].id
  } catch (e: any) {
    toasts.error('Erro ao carregar', errMsg(e))
  } finally {
    carregando.value = false
  }
}

async function carregarMarcas() {
  try {
    marcas.value = await api<{ nome: string; slug: string }[]>('/api/marcas?ativo=true')
  } catch {
    marcas.value = []
  }
}

onMounted(async () => {
  await Promise.all([carregar(), carregarMarcas()])
  // Vindo da aba Criativos ("escrever roteiro"): abre logo o que foi criado.
  if (props.foco) abrir(props.foco)
})

function abrir(id: string) {
  secao.value = 'roteiros'
  selId.value = id
}
defineExpose({ abrir, carregar })

const criando = ref(false)
async function criarRoteiro() {
  criando.value = true
  try {
    const novo = await api<Roteiro>('/api/marketing/roteiros', {
      method: 'POST',
      body: { titulo: 'Roteiro sem título' },
    })
    roteiros.value = [novo, ...roteiros.value]
    selId.value = novo.id
  } catch (e: any) {
    toasts.error('Erro ao criar', errMsg(e))
  } finally {
    criando.value = false
  }
}

// Salvamento por campo, no molde da planilha de Criativos: sai do campo,
// salva. Sem botão de salvar pra ninguém perder texto ao trocar de roteiro.
const salvando = ref(false)

// Cada PATCH leva um número; só o último manda. Dois salvamentos em voo (sai
// do título, sai do SKU) voltavam fora de ordem e a tela ficava com o estado
// mais VELHO por cima do mais novo.
let salvarSeq = 0

async function salvar(campo: string, valor: unknown) {
  const r = sel.value
  if (!r) return
  const seq = ++salvarSeq
  ;(r as any)[campo] = valor // otimista: a tela não pisca enquanto salva
  salvando.value = true
  try {
    const resp = await api<Roteiro>(`/api/marketing/roteiros/${r.id}`, {
      method: 'PATCH',
      body: { [campo]: valor },
    })
    if (seq !== salvarSeq || sel.value !== r) return
    aplicaResposta(r, resp)
  } catch (e: any) {
    toasts.error('Erro ao salvar', errMsg(e))
    await carregar()
  } finally {
    if (seq === salvarSeq) salvando.value = false
  }
}

// Copia do servidor só o que ELE resolve — vínculos e listas. Os campos de
// texto ficam como estão na tela: reescrevê-los é exatamente o que apagava o
// que a pessoa estava digitando.
function aplicaResposta(r: Roteiro, resp: Roteiro) {
  r.marca_id = resp.marca_id
  r.product_id = resp.product_id
  r.referencias = resp.referencias
  r.personagens = resp.personagens
  r.ativo = resp.ativo
}

async function apagarRoteiro(r: Roteiro) {
  if (!window.confirm(`Apagar o roteiro "${r.titulo}"? Os criativos ligados a ele continuam.`)) return
  try {
    await api(`/api/marketing/roteiros/${r.id}`, { method: 'DELETE' })
    roteiros.value = roteiros.value.filter((x) => x.id !== r.id)
    if (selId.value === r.id) selId.value = roteiros.value[0]?.id ?? null
  } catch (e: any) {
    toasts.error('Erro ao apagar', errMsg(e))
  }
}

// ---- referências ----------------------------------------------------------
const refInput = ref<HTMLInputElement | null>(null)
const enviando = ref(false)
const linkNovo = ref('')

function refUrl(r: Roteiro, x: Ref_) {
  return `/api/marketing/roteiros/${r.id}/referencia/${x.id}`
}

async function onRefEscolhida(ev: Event) {
  const input = ev.target as HTMLInputElement
  const arquivos = Array.from(input.files ?? [])
  const r = sel.value
  input.value = ''
  if (!arquivos.length || !r) return
  enviando.value = true
  try {
    const fd = new FormData()
    for (const f of arquivos) fd.append('files', f)
    aplicaResposta(r, await api<Roteiro>(`/api/marketing/roteiros/${r.id}/referencia`, {
      method: 'POST',
      body: fd,
    }))
  } catch (e: any) {
    toasts.error('Erro ao anexar', errMsg(e))
  } finally {
    enviando.value = false
  }
}

async function addLink() {
  const r = sel.value
  const url = linkNovo.value.trim()
  if (!r || !url) return
  enviando.value = true
  try {
    aplicaResposta(r, await api<Roteiro>(`/api/marketing/roteiros/${r.id}/referencia/link`, {
      method: 'POST',
      body: { url },
    }))
    linkNovo.value = ''
  } catch (e: any) {
    toasts.error('Erro ao salvar o link', errMsg(e))
  } finally {
    enviando.value = false
  }
}

async function tirarRef(r: Roteiro, x: Ref_) {
  if (!window.confirm('Tirar essa referência?')) return
  try {
    aplicaResposta(r, await api<Roteiro>(`/api/marketing/roteiros/${r.id}/referencia/${x.id}`, {
      method: 'DELETE',
    }))
  } catch (e: any) {
    toasts.error('Erro ao tirar', errMsg(e))
  }
}

// ---- personagens do roteiro ----------------------------------------------
const textoEl = ref<HTMLTextAreaElement | null>(null)
const personagemNovo = ref('')

const disponiveis = computed(() => {
  const jaTem = new Set((sel.value?.personagens ?? []).map((p) => p.id))
  return personagens.value.filter((p) => p.ativo && !jaTem.has(p.id))
})

async function ligarPersonagem() {
  const r = sel.value
  const id = personagemNovo.value
  if (!r || !id) return
  try {
    aplicaResposta(r, await api<Roteiro>(`/api/marketing/roteiros/${r.id}/personagem`, {
      method: 'POST',
      body: { personagem_id: id },
    }))
    personagemNovo.value = ''
  } catch (e: any) {
    toasts.error('Erro ao ligar o personagem', errMsg(e))
  }
}

async function desligarPersonagem(r: Roteiro, p: Personagem) {
  try {
    aplicaResposta(r, await api<Roteiro>(`/api/marketing/roteiros/${r.id}/personagem/${p.id}`, {
      method: 'DELETE',
    }))
  } catch (e: any) {
    toasts.error('Erro ao desligar', errMsg(e))
  }
}

// O ponto do vínculo: a etiqueta do gerador entra no texto por um clique, no
// lugar do cursor. Os dois roteiros que existiam em produção foram escritos
// colando `<<<48dbb6ed-…>>>` à mão — um UUID digitado errado não dá erro,
// só gera outro rosto.
async function inserirNoTexto(p: Personagem) {
  const r = sel.value
  const etiqueta = (p.referencia || p.nome).trim()
  if (!r || !etiqueta) return
  const el = textoEl.value
  // O valor VIVO do campo, não o que o servidor devolveu da última vez:
  // quem digitou sem sair do textarea perdia tudo ao clicar em inserir.
  const atual = el ? el.value : textoLocal.value
  const fim = atual.length
  // selectionStart/End podem vir invertidos (seleção feita da direita pra
  // esquerda) — sem ordenar, o slice come um pedaço do texto.
  const a0 = el ? Math.min(el.selectionStart ?? fim, el.selectionEnd ?? fim) : fim
  const a1 = el ? Math.max(el.selectionStart ?? fim, el.selectionEnd ?? fim) : fim
  const novo = atual.slice(0, a0) + etiqueta + atual.slice(a1)
  textoLocal.value = novo
  await nextTick()
  if (el) {
    el.focus()
    el.setSelectionRange(a0 + etiqueta.length, a0 + etiqueta.length)
  }
  await salvar('texto', novo)
}

// ---- cadastro de personagens ---------------------------------------------
const pSel = ref<Personagem | null>(null)
const pForm = ref({ nome: '', descricao: '', referencia: '', video_url: '' })
const pSalvando = ref(false)
const pImgInput = ref<HTMLInputElement | null>(null)
const pVozInput = ref<HTMLInputElement | null>(null)
// Qual botão abriu o seletor: a mesma rota recebe foto e voz, e o `tipo`
// decide a lista branca de extensão do lado do servidor.
const tipoDoUpload = ref<'imagem' | 'voz'>('imagem')

function novoPersonagem() {
  pSel.value = null
  pForm.value = { nome: '', descricao: '', referencia: '', video_url: '' }
}

function editarPersonagem(p: Personagem) {
  pSel.value = p
  pForm.value = {
    nome: p.nome,
    descricao: p.descricao ?? '',
    referencia: p.referencia ?? '',
    video_url: p.video_url ?? '',
  }
}

async function salvarPersonagem(extra: Record<string, unknown> = {}) {
  const corpo = {
    nome: pForm.value.nome,
    descricao: pForm.value.descricao,
    referencia: pForm.value.referencia,
    video_url: pForm.value.video_url,
    ...extra,
  }
  if (!corpo.nome.trim()) {
    toasts.warning('Dê um nome ao personagem')
    return
  }
  pSalvando.value = true
  try {
    if (pSel.value) {
      const at = await api<Personagem>(`/api/marketing/personagens/${pSel.value.id}`, {
        method: 'PATCH',
        body: corpo,
      })
      Object.assign(pSel.value, at)
      // O card na grade é outro objeto quando a lista foi recarregada: sem
      // isto o olho muda na ficha e a grade continua mostrando o estado velho.
      const naLista = personagens.value.find((x) => x.id === at.id)
      if (naLista && naLista !== pSel.value) Object.assign(naLista, at)
    } else {
      const novo = await api<Personagem>('/api/marketing/personagens', {
        method: 'POST',
        body: corpo,
      })
      personagens.value = [...personagens.value, novo]
      pSel.value = novo
    }
  } catch (e: any) {
    toasts.error('Erro ao salvar', errMsg(e))
  } finally {
    pSalvando.value = false
  }
}

// O botão do olho mandava `salvarPersonagem()` e virava `pSel.ativo` na mão,
// LOCALMENTE — e o `Object.assign` da resposta trazia o `ativo` antigo de
// volta por cima. Ou seja: personagem não desligava. E é `ativo` que tira a
// foto e a etiqueta da mão da agência, então o interruptor precisa chegar
// mesmo ao servidor.
async function alternarAtivo() {
  const p = pSel.value
  if (!p) return
  await salvarPersonagem({ ativo: !p.ativo })
}

function pedirArquivo(tipo: 'imagem' | 'voz') {
  tipoDoUpload.value = tipo
  ;(tipo === 'voz' ? pVozInput : pImgInput).value?.click()
}

async function onArquivoEscolhido(ev: Event) {
  const input = ev.target as HTMLInputElement
  const arquivos = Array.from(input.files ?? [])
  const p = pSel.value
  input.value = ''
  if (!arquivos.length || !p) return
  pSalvando.value = true
  try {
    const fd = new FormData()
    for (const f of arquivos) fd.append('files', f)
    const at = await api<Personagem>(
      `/api/marketing/personagens/${p.id}/arquivo?tipo=${tipoDoUpload.value}`,
      { method: 'POST', body: fd },
    )
    Object.assign(p, at)
    const naLista = personagens.value.find((x) => x.id === at.id)
    if (naLista && naLista !== p) Object.assign(naLista, at)
  } catch (e: any) {
    toasts.error('Erro ao subir o arquivo', errMsg(e))
  } finally {
    pSalvando.value = false
  }
}

function arqUrl(p: Personagem, a: Anexo, baixar = false) {
  return `/api/marketing/personagens/${p.id}/arquivo/${a.id}${baixar ? '?download=1' : ''}`
}

async function tirarArquivo(p: Personagem, a: Anexo) {
  if (!window.confirm(`Apagar "${a.file_name}"?`)) return
  try {
    Object.assign(p, await api<Personagem>(
      `/api/marketing/personagens/${p.id}/arquivo/${a.id}`, { method: 'DELETE' },
    ))
  } catch (e: any) {
    toasts.error('Erro ao apagar', errMsg(e))
  }
}

async function apagarPersonagem(p: Personagem) {
  if (!window.confirm(`Apagar o personagem "${p.nome}"? Ele sai dos roteiros que o usam.`)) return
  try {
    await api(`/api/marketing/personagens/${p.id}`, { method: 'DELETE' })
    personagens.value = personagens.value.filter((x) => x.id !== p.id)
    if (pSel.value?.id === p.id) novoPersonagem()
    await carregar()
  } catch (e: any) {
    toasts.error('Erro ao apagar', errMsg(e))
  }
}

function destinoLabel(d: string | null): string {
  return d || 'as duas agências'
}
</script>

<template>
  <div class="space-y-3">
    <input ref="refInput" type="file" accept=".jpg,.jpeg,.png,.webp,.gif,.pdf" multiple class="hidden" @change="onRefEscolhida">
    <input ref="pImgInput" type="file" accept=".jpg,.jpeg,.png,.webp,.gif" multiple class="hidden" @change="onArquivoEscolhido">
    <input ref="pVozInput" type="file" accept=".mp3,.m4a,.wav,.aac,.ogg" class="hidden" @change="onArquivoEscolhido">

    <!-- seções -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="inline-flex rounded-lg bg-muted p-0.5">
        <button
          class="rounded-md px-3 py-1 text-xs transition-colors"
          :class="secao === 'roteiros' ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:bg-background/60'"
          @click="secao = 'roteiros'"
        >
          <NotebookPen class="mr-1 inline size-3.5" /> Roteiros
        </button>
        <button
          class="rounded-md px-3 py-1 text-xs transition-colors"
          :class="secao === 'personagens' ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:bg-background/60'"
          @click="secao = 'personagens'"
        >
          <Users class="mr-1 inline size-3.5" /> Personagens
        </button>
      </div>
      <Loader2 v-if="carregando || salvando" class="size-3.5 animate-spin text-muted-foreground" />
      <span v-if="salvando" class="text-[11px] text-muted-foreground">salvando…</span>
    </div>

    <!-- ══════════════ roteiros ══════════════ -->
    <div v-if="secao === 'roteiros'" class="grid gap-3 lg:grid-cols-[280px_1fr]">
      <!-- lista -->
      <div class="space-y-2">
        <div class="flex items-center gap-1.5">
          <div class="relative flex-1">
            <Search class="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              v-model="q"
              type="text"
              placeholder="filtrar…"
              class="h-8 w-full rounded-md border bg-background pl-7 pr-2 text-xs outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <button
            v-if="canEdit"
            class="btn btn-sm btn-primary shrink-0 gap-1"
            :disabled="criando"
            title="Novo roteiro"
            @click="criarRoteiro"
          >
            <Loader2 v-if="criando" class="size-3.5 animate-spin" />
            <Plus v-else class="size-3.5" />
          </button>
        </div>

        <div class="max-h-[calc(100vh-280px)] space-y-1 overflow-auto rounded-lg border p-1">
          <p v-if="!listaFiltrada.length" class="px-2 py-6 text-center text-xs text-muted-foreground">
            {{ roteiros.length ? 'Nada bate com o filtro.' : 'Nenhum roteiro ainda.' }}
          </p>
          <button
            v-for="r in listaFiltrada"
            :key="r.id"
            class="w-full rounded-md px-2 py-1.5 text-left transition-colors"
            :class="selId === r.id ? 'bg-primary/10 ring-1 ring-primary/30' : 'hover:bg-muted'"
            @click="selId = r.id"
          >
            <div class="flex items-center gap-1">
              <span class="truncate text-xs font-medium">{{ r.titulo }}</span>
              <EyeOff v-if="!r.ativo" class="ml-auto size-3 shrink-0 text-muted-foreground" title="desligado — a agência não vê" />
            </div>
            <div class="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
              <span v-if="r.marca">{{ r.marca }}</span>
              <span v-if="r.sku" class="font-mono">{{ r.sku }}</span>
              <span class="pill-muted">{{ destinoLabel(r.equipe_destino) }}</span>
            </div>
          </button>
        </div>
      </div>

      <!-- editor -->
      <div v-if="sel" class="space-y-3 rounded-lg border p-3">
        <div class="flex flex-wrap items-start gap-2">
          <input
            :value="sel.titulo"
            type="text"
            :readonly="!canEdit"
            class="min-w-0 flex-1 rounded-md border bg-background px-2 py-1.5 text-sm font-medium outline-none focus:ring-2 focus:ring-ring"
            placeholder="Título do roteiro"
            @change="salvar('titulo', ($event.target as HTMLInputElement).value)"
          />
          <button
            v-if="canEdit"
            class="btn btn-sm gap-1"
            :title="sel.ativo ? 'Desligar: some da tela da agência e para de servir as imagens' : 'Ligar: volta a aparecer pro destinatário'"
            @click="salvar('ativo', !sel.ativo)"
          >
            <Eye v-if="sel.ativo" class="size-3.5" />
            <EyeOff v-else class="size-3.5" />
            {{ sel.ativo ? 'visível' : 'desligado' }}
          </button>
          <button
            v-if="canEdit"
            class="btn btn-sm px-1.5 text-muted-foreground hover:text-destructive"
            title="Apagar roteiro"
            @click="apagarRoteiro(sel)"
          >
            <Trash2 class="size-3.5" />
          </button>
        </div>

        <div class="grid gap-2 sm:grid-cols-3">
          <div>
            <label class="mb-1 block text-[11px] font-medium text-muted-foreground">Marca</label>
            <select
              :value="sel.marca ?? ''"
              :disabled="!canEdit"
              class="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
              @change="salvar('marca', ($event.target as HTMLSelectElement).value)"
            >
              <option value="">—</option>
              <option v-if="sel.marca && !marcas.some((m) => m.slug === sel!.marca)" :value="sel.marca">{{ sel.marca }}</option>
              <option v-for="m in marcas" :key="m.slug" :value="m.slug">{{ m.nome || m.slug }}</option>
            </select>
          </div>
          <div>
            <label class="mb-1 block text-[11px] font-medium text-muted-foreground">SKU</label>
            <input
              :value="sel.sku ?? ''"
              type="text"
              :readonly="!canEdit"
              class="h-8 w-full rounded-md border bg-background px-2 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
              @change="salvar('sku', ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div>
            <label class="mb-1 block text-[11px] font-medium text-muted-foreground">
              Vai para
            </label>
            <select
              :value="sel.equipe_destino ?? ''"
              :disabled="!canEdit"
              class="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
              @change="salvar('equipe_destino', ($event.target as HTMLSelectElement).value)"
            >
              <!-- Vazio é a REGRA, não a ausência dela: "se não preenchido vai
                   para os 2". Por isso o rótulo diz o que acontece. -->
              <option value="">as duas agências</option>
              <option v-if="sel.equipe_destino && !destinos.includes(sel.equipe_destino)" :value="sel.equipe_destino">
                {{ sel.equipe_destino }}
              </option>
              <option v-for="d in destinos" :key="d" :value="d">{{ d }}</option>
            </select>
          </div>
        </div>

        <!-- o texto -->
        <div>
          <label class="mb-1 block text-[11px] font-medium text-muted-foreground">Roteiro</label>
          <textarea
            ref="textoEl"
            v-model="textoLocal"
            rows="14"
            :readonly="!canEdit"
            class="w-full resize-y rounded-md border bg-background px-2.5 py-2 text-xs leading-relaxed outline-none focus:ring-2 focus:ring-ring"
            placeholder="Cena, fala, texto na tela. Use os personagens abaixo pra inserir a etiqueta do gerador no lugar certo."
            @change="salvar('texto', textoLocal)"
          />
          <p class="mt-1 text-[11px] text-muted-foreground">
            A agência vê este texto assim que ele deixa de estar vazio. Roteiro em
            branco não aparece pra ninguém.
          </p>
        </div>

        <!-- personagens do roteiro -->
        <div>
          <div class="mb-1.5 flex flex-wrap items-center gap-2">
            <label class="text-[11px] font-medium text-muted-foreground">Personagens deste vídeo</label>
            <select
              v-if="canEdit && disponiveis.length"
              v-model="personagemNovo"
              class="ml-auto h-7 rounded-md border bg-background px-2 text-[11px] outline-none focus:ring-2 focus:ring-ring"
              @change="ligarPersonagem"
            >
              <option value="">adicionar…</option>
              <option v-for="p in disponiveis" :key="p.id" :value="p.id">{{ p.nome }}</option>
            </select>
          </div>
          <div v-if="sel.personagens.length" class="flex flex-wrap gap-1.5">
            <div
              v-for="p in sel.personagens"
              :key="p.id"
              class="flex items-center gap-1.5 rounded-full border bg-muted/50 py-0.5 pl-0.5 pr-1.5"
            >
              <img
                v-if="p.imagens.length"
                :src="arqUrl(p, p.imagens[0])"
                :alt="p.nome"
                class="size-5 rounded-full object-cover"
              />
              <Users v-else class="size-3.5 shrink-0 text-muted-foreground" />
              <span class="text-[11px]">{{ p.nome }}</span>
              <button
                v-if="canEdit && p.referencia"
                class="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-primary"
                :title="`Inserir ${p.referencia} no texto, na posição do cursor`"
                @click="inserirNoTexto(p)"
              >
                <CornerDownLeft class="size-3" />
              </button>
              <button
                v-if="canEdit"
                class="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-destructive"
                title="Tirar do roteiro"
                @click="desligarPersonagem(sel, p)"
              >
                <X class="size-3" />
              </button>
            </div>
          </div>
          <p v-else class="text-[11px] text-muted-foreground">
            Nenhum. Escolha acima pra a agência ver a foto e a etiqueta junto do briefing.
          </p>
        </div>

        <!-- referências -->
        <div>
          <div class="mb-1.5 flex flex-wrap items-center gap-2">
            <label class="text-[11px] font-medium text-muted-foreground">Referências</label>
            <span class="text-[11px] text-muted-foreground">imagens e links de produto</span>
            <button
              v-if="canEdit"
              class="btn btn-xs ml-auto gap-1"
              :disabled="enviando"
              @click="refInput?.click()"
            >
              <Loader2 v-if="enviando" class="size-3 animate-spin" />
              <Upload v-else class="size-3" />
              anexar
            </button>
          </div>

          <div
            v-if="sel.referencias.some((x) => x.tipo === 'imagem')"
            class="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2"
          >
            <div
              v-for="x in sel.referencias.filter((y) => y.tipo === 'imagem')"
              :key="x.id"
              class="group relative overflow-hidden rounded-md border bg-muted"
            >
              <a :href="refUrl(sel, x)" target="_blank" rel="noopener" class="flex h-20 items-center justify-center">
                <img
                  v-if="x.file_mime?.startsWith('image/')"
                  :src="refUrl(sel, x)"
                  :alt="x.file_name ?? ''"
                  class="h-full w-full object-cover"
                  loading="lazy"
                />
                <FileIcon v-else class="size-5 text-muted-foreground" />
              </a>
              <button
                v-if="canEdit"
                class="absolute right-1 top-1 rounded bg-background/90 p-0.5 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"
                title="Tirar"
                @click="tirarRef(sel, x)"
              >
                <X class="size-3" />
              </button>
              <div class="truncate px-1 py-0.5 text-[10px] text-muted-foreground">{{ x.file_name }}</div>
            </div>
          </div>

          <div v-if="sel.referencias.some((x) => x.tipo === 'link')" class="mt-1.5 space-y-1">
            <div
              v-for="x in sel.referencias.filter((y) => y.tipo === 'link')"
              :key="x.id"
              class="flex items-center gap-1.5 text-xs"
            >
              <Link2 class="size-3.5 shrink-0 text-muted-foreground" />
              <a
                :href="x.url ?? '#'"
                target="_blank"
                rel="noopener noreferrer"
                class="truncate text-primary hover:underline"
              >{{ x.titulo || x.url }}</a>
              <ExternalLink class="size-3 shrink-0 text-muted-foreground" />
              <button
                v-if="canEdit"
                class="ml-auto shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive"
                @click="tirarRef(sel, x)"
              >
                <X class="size-3" />
              </button>
            </div>
          </div>

          <div v-if="canEdit" class="mt-1.5 flex items-center gap-1.5">
            <input
              v-model="linkNovo"
              type="url"
              placeholder="https://… link do produto"
              class="h-7 min-w-0 flex-1 rounded-md border bg-background px-2 text-[11px] outline-none focus:ring-2 focus:ring-ring"
              @keydown.enter.prevent="addLink"
            />
            <button class="btn btn-xs gap-1" :disabled="enviando || !linkNovo.trim()" @click="addLink">
              <Plus class="size-3" /> link
            </button>
          </div>
        </div>
      </div>

      <div v-else class="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        Escolha um roteiro à esquerda — ou crie o primeiro.
      </div>
    </div>

    <!-- ══════════════ personagens ══════════════ -->
    <div v-else class="grid gap-3 lg:grid-cols-[1fr_320px]">
      <div>
        <p class="mb-2 text-xs text-muted-foreground">
          Ideias de personagem que as agências podem usar nos vídeos. Além do nome e
          da descrição, guarde a <b class="text-foreground">etiqueta do gerador</b> —
          é ela que faz o mesmo rosto sair em todo vídeo.
        </p>
        <div class="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2">
          <button
            v-for="p in personagens"
            :key="p.id"
            class="flex flex-col overflow-hidden rounded-lg border text-left transition-colors hover:border-primary/50"
            :class="pSel?.id === p.id ? 'ring-2 ring-primary/40' : ''"
            @click="editarPersonagem(p)"
          >
            <div class="flex h-28 items-center justify-center bg-muted">
              <img v-if="p.imagens.length" :src="arqUrl(p, p.imagens[0])" :alt="p.nome" class="h-full w-full object-cover" loading="lazy" />
              <ImageIcon v-else class="size-6 text-muted-foreground" />
            </div>
            <div class="space-y-0.5 p-2">
              <div class="flex items-center gap-1">
                <span class="truncate text-xs font-medium">{{ p.nome }}</span>
                <EyeOff v-if="!p.ativo" class="ml-auto size-3 shrink-0 text-muted-foreground" />
              </div>
              <p v-if="p.descricao" class="line-clamp-2 text-[11px] text-muted-foreground">{{ p.descricao }}</p>
              <div class="flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                <span v-if="p.imagens.length">{{ p.imagens.length }} foto{{ p.imagens.length > 1 ? 's' : '' }}</span>
                <span v-if="p.vozes.length" class="pill-muted">voz</span>
                <span v-if="p.video_url" class="pill-muted">vídeo</span>
              </div>
            </div>
          </button>
          <button
            v-if="canEdit"
            class="flex h-full min-h-[140px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground"
            @click="novoPersonagem"
          >
            <Plus class="size-5" /> novo personagem
          </button>
        </div>
      </div>

      <!-- ficha -->
      <div v-if="canEdit" class="space-y-2 rounded-lg border p-3">
        <h3 class="text-sm font-medium">{{ pSel ? pSel.nome : 'Novo personagem' }}</h3>
        <div>
          <label class="mb-1 block text-[11px] font-medium text-muted-foreground">Nome</label>
          <input v-model="pForm.nome" type="text" class="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring" placeholder="ex.: Lívia" />
        </div>
        <div>
          <label class="mb-1 block text-[11px] font-medium text-muted-foreground">Descrição</label>
          <textarea v-model="pForm.descricao" rows="3" class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-xs outline-none focus:ring-2 focus:ring-ring" placeholder="ex.: estudante brasileira de 22 anos, cabelo loiro…" />
        </div>
        <div>
          <label class="mb-1 block text-[11px] font-medium text-muted-foreground">Referência em vídeo</label>
          <input v-model="pForm.video_url" type="url" class="h-8 w-full rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring" placeholder="ex.: https://youtube.com/shorts/…" />
          <p class="mt-1 text-[11px] text-muted-foreground">Como a persona se move e fala.</p>
        </div>
        <div>
          <label class="mb-1 block text-[11px] font-medium text-muted-foreground">
            Etiqueta do gerador <span class="font-normal">(opcional)</span>
          </label>
          <input v-model="pForm.referencia" type="text" class="h-8 w-full rounded-md border bg-background px-2 font-mono text-[11px] outline-none focus:ring-2 focus:ring-ring" placeholder="ex.: <<<48dbb6ed-…>>> ou @Lívia" />
          <p class="mt-1 text-[11px] text-muted-foreground">
            Atalho pra quem usa a MESMA ferramenta que gerou o rosto — ela some se o
            asset for apagado lá ou se a agência usar outro gerador. Quem garante o
            rosto de verdade é a foto abaixo, que eles baixam.
          </p>
        </div>
        <div class="flex items-center gap-1.5">
          <button class="btn btn-sm btn-primary gap-1" :disabled="pSalvando" @click="salvarPersonagem()">
            <Loader2 v-if="pSalvando" class="size-3.5 animate-spin" /> salvar
          </button>
          <button
            v-if="pSel"
            class="btn btn-sm gap-1"
            :disabled="pSalvando"
            :title="pSel.ativo
              ? 'Desligar: some do catálogo da agência, sai dos roteiros e para de servir as fotos'
              : 'Ligar: volta a aparecer pras agências'"
            @click="alternarAtivo"
          >
            <Eye v-if="pSel.ativo" class="size-3.5" />
            <EyeOff v-else class="size-3.5" />
            {{ pSel.ativo ? 'visível' : 'desligado' }}
          </button>
          <button v-if="pSel" class="btn btn-sm ml-auto px-1.5 text-muted-foreground hover:text-destructive" title="Apagar" @click="apagarPersonagem(pSel)">
            <Trash2 class="size-3.5" />
          </button>
        </div>

        <div v-if="pSel">
          <div class="mb-1 flex items-center gap-2">
            <label class="text-[11px] font-medium text-muted-foreground">Fotos de referência</label>
            <button class="btn btn-xs ml-auto gap-1" :disabled="pSalvando" @click="pedirArquivo('imagem')">
              <Upload class="size-3" /> subir
            </button>
          </div>
          <div v-if="pSel.imagens.length" class="grid grid-cols-3 gap-1.5">
            <div v-for="img in pSel.imagens" :key="img.id" class="group relative overflow-hidden rounded border bg-muted">
              <a :href="arqUrl(pSel, img)" target="_blank" rel="noopener">
                <img :src="arqUrl(pSel, img)" :alt="img.file_name ?? ''" class="h-16 w-full object-cover" loading="lazy" />
              </a>
              <button
                class="absolute right-0.5 top-0.5 rounded bg-background/90 p-0.5 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"
                title="Apagar"
                @click="tirarArquivo(pSel, img)"
              >
                <X class="size-3" />
              </button>
            </div>
          </div>
          <p v-else class="text-[11px] text-muted-foreground">
            Nenhuma. É o que a agência baixa — rosto, roupa, acessórios. Sem isso
            sobra só a descrição.
          </p>

          <div class="mb-1 mt-3 flex items-center gap-2">
            <label class="text-[11px] font-medium text-muted-foreground">Voz</label>
            <button class="btn btn-xs ml-auto gap-1" :disabled="pSalvando" @click="pedirArquivo('voz')">
              <Upload class="size-3" /> subir MP3
            </button>
          </div>
          <div v-for="v in pSel.vozes" :key="v.id" class="mb-1 flex items-center gap-1.5">
            <audio :src="arqUrl(pSel, v)" controls preload="none" class="h-7 min-w-0 flex-1" />
            <button
              class="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive"
              :title="`Apagar ${v.file_name}`"
              @click="tirarArquivo(pSel, v)"
            >
              <X class="size-3" />
            </button>
          </div>
          <p v-if="!pSel.vozes.length" class="text-[11px] text-muted-foreground">
            Nenhuma. As personas que vocês já usam têm todas um MP3 de voz.
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
