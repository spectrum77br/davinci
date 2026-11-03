<script setup lang="ts">
/**
 * Marketing › Lives (Eduardo, 03/11/2026).
 *
 * "gravar as lives que pessoas reais vão fazer, para medir o padrão de uma boa
 * live" — e, mais tarde, virar persona de IA. Aqui só se CADASTRA: quem vai
 * apresentar cola o link de compartilhar da live (Shopee ou TikTok). O robô
 * do Mac mini (~/Desktop/lives-gravador) pega as que estão aguardando, grava
 * o stream inteiro com ffmpeg e devolve o caminho do vídeo e os números
 * (espectadores, curtidas...) — o vídeo fica no Mac, não no servidor.
 *
 * Só o robô muda o status. A tela recarrega sozinha a cada 30 s enquanto
 * houver live aguardando ou gravando, e avisa quando o robô some.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  AlertTriangle, Copy, ExternalLink, Loader2, Pencil, Plus, Radio, RefreshCw, RotateCcw, Trash2, X,
} from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'
import {
  ERROS_LIVES, duracaoTxt, minutosSemSinal, numTxt, plataformaDoLink, sparkline, tamanhoTxt,
  type Live, type LojaLive,
} from '~/utils/lives'

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('marketing_criativos', 'edit')

const lives = ref<Live[]>([])
const lojas = ref<LojaLive[]>([])
const carregando = ref(false)
const carregou = ref(false)
const erro = ref<string | null>(null)
const agora = ref(Date.now())

const errMsg = (e: any) => apiErrMsg(e, ERROS_LIVES)

async function carregar() {
  carregando.value = true
  try {
    lives.value = await api<Live[]>('/api/marketing/lives')
    erro.value = null
  } catch (e: any) {
    erro.value = errMsg(e)
  } finally {
    carregando.value = false
    carregou.value = true
    agora.value = Date.now()
  }
}

async function carregarLojas() {
  try {
    lojas.value = await api<LojaLive[]>('/api/marketing/lives/lojas')
  } catch {
    lojas.value = []
  }
}

// Recarrega sozinho só enquanto tem algo andando.
let timer: ReturnType<typeof setInterval> | null = null
onMounted(async () => {
  await Promise.all([carregar(), carregarLojas()])
  timer = setInterval(() => {
    if (lives.value.some((l) => l.status === 'aguardando' || l.status === 'gravando')) void carregar()
    else agora.value = Date.now()
  }, 30000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

// ---- formulário (novo e editar) -------------------------------------------
type Form = { link: string; apresentador: string; integration_id: string; produtos: string; observacao: string }
const vazio = (): Form => ({ link: '', apresentador: '', integration_id: '', produtos: '', observacao: '' })
const form = reactive<Form>(vazio())
const editando = ref<Live | null>(null)
const modalAberto = ref(false)
const salvando = ref(false)

const plataformaForm = computed(() => plataformaDoLink(form.link))
const lojasDoForm = computed(() =>
  plataformaForm.value ? lojas.value.filter((l) => l.plataforma === plataformaForm.value) : lojas.value,
)
const linkTravado = computed(() => !!editando.value && editando.value.status !== 'aguardando')

function abrirNova() {
  Object.assign(form, vazio())
  editando.value = null
  modalAberto.value = true
}

function abrirEditar(l: Live) {
  Object.assign(form, {
    link: l.link,
    apresentador: l.apresentador || '',
    integration_id: l.integration_id || '',
    produtos: l.produtos || '',
    observacao: l.observacao || '',
  })
  editando.value = l
  modalAberto.value = true
}

function fechar() {
  if (!salvando.value) modalAberto.value = false
}

async function salvar() {
  if (!plataformaForm.value) {
    toasts.error('Link inválido', ERROS_LIVES.link_invalido)
    return
  }
  // Loja de outra plataforma (trocou o link depois de escolher): limpa.
  if (form.integration_id && !lojasDoForm.value.some((l) => l.id === form.integration_id)) {
    form.integration_id = ''
  }
  const corpo = {
    link: form.link.trim(),
    apresentador: form.apresentador,
    integration_id: form.integration_id || null,
    produtos: form.produtos,
    observacao: form.observacao,
  }
  salvando.value = true
  try {
    if (editando.value) {
      const r = await api<Live>(`/api/marketing/lives/${editando.value.id}`, { method: 'PATCH', body: corpo })
      lives.value = lives.value.map((x) => (x.id === r.id ? { ...r, created_by_nome: x.created_by_nome } : x))
      toasts.success('Live atualizada')
    } else {
      const r = await api<Live>('/api/marketing/lives', { method: 'POST', body: corpo })
      lives.value = [r, ...lives.value]
      toasts.success('Live cadastrada', 'O robô do Mac vai gravar quando ela estiver no ar.')
    }
    modalAberto.value = false
  } catch (e: any) {
    toasts.error('Erro ao salvar', errMsg(e))
  } finally {
    salvando.value = false
  }
}

async function regravar(l: Live) {
  try {
    const r = await api<Live>(`/api/marketing/lives/${l.id}/regravar`, { method: 'POST' })
    lives.value = lives.value.map((x) => (x.id === r.id ? { ...r, created_by_nome: x.created_by_nome } : x))
  } catch (e: any) {
    toasts.error('Erro', errMsg(e))
  }
}

async function apagar(l: Live) {
  if (!window.confirm('Apagar esta live da lista?')) return
  try {
    await api(`/api/marketing/lives/${l.id}`, { method: 'DELETE' })
    lives.value = lives.value.filter((x) => x.id !== l.id)
  } catch (e: any) {
    toasts.error('Erro ao apagar', errMsg(e))
  }
}

async function copiar(texto: string) {
  try {
    await navigator.clipboard.writeText(texto)
    toasts.success('Caminho copiado')
  } catch {
    toasts.error('Não deu pra copiar', texto)
  }
}

// ---- apresentação -----------------------------------------------------------
const STATUS: Record<Live['status'], { rotulo: string; cls: string }> = {
  aguardando: { rotulo: 'Aguardando', cls: 'bg-muted text-muted-foreground' },
  gravando: { rotulo: 'Gravando', cls: 'bg-red-600 text-white' },
  gravada: { rotulo: 'Gravada', cls: 'bg-emerald-600 text-white' },
  erro: { rotulo: 'Erro', cls: 'bg-destructive/15 text-destructive' },
}
const ROTULO_PLATAFORMA = { shopee: 'Shopee', tiktok: 'TikTok' } as const

const dataHora = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'

/** Gravando e o robô sem dar sinal há mais de 3 min: algo parou no Mac. */
function roboSumiu(l: Live): number | null {
  if (l.status !== 'gravando') return null
  const m = minutosSemSinal(l.robo_visto_em, agora.value)
  return m != null && m >= 3 ? m : null
}

/** Minutos gravados até agora (gravando) ou duração final (gravada). */
function duracao(l: Live): string {
  if (l.duracao_s != null) return duracaoTxt(l.duracao_s)
  if (l.status === 'gravando' && l.inicio_em) {
    return duracaoTxt(Math.max(0, Math.round((agora.value - new Date(l.inicio_em).getTime()) / 1000)))
  }
  return '—'
}

const contagem = computed(() => {
  const c = { aguardando: 0, gravando: 0, gravada: 0, erro: 0 }
  for (const l of lives.value) c[l.status]++
  return c
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold flex items-center gap-2"><Radio class="size-5" /> Lives</h2>
        <p class="text-sm text-muted-foreground max-w-2xl">
          Cole o link de compartilhar da live (Shopee ou TikTok). O robô do Mac mini grava a live inteira
          quando ela estiver no ar e guarda os números (espectadores, curtidas) para medir o padrão de uma boa live.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': carregando }" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" @click="abrirNova">
          <Plus class="size-4 mr-1" /> Nova live
        </Button>
      </div>
    </div>

    <p v-if="erro" role="alert" class="text-sm text-destructive flex items-center gap-2">
      <AlertTriangle class="size-4 shrink-0" /> {{ erro }}
    </p>

    <p v-if="lives.length" class="text-xs text-muted-foreground">
      {{ contagem.gravando }} gravando · {{ contagem.aguardando }} aguardando ·
      {{ contagem.gravada }} gravada(s) · {{ contagem.erro }} com erro
    </p>

    <div v-if="carregando && !carregou" class="rounded-lg border p-8 text-center text-sm text-muted-foreground">
      Carregando lives…
    </div>
    <EmptyState
      v-else-if="carregou && !lives.length && !erro"
      :icon="Radio"
      title="Nenhuma live cadastrada"
      description="Quando alguém for fazer uma live, cadastre aqui o link. Pode cadastrar antes de começar: o robô espera até 12 h a live entrar no ar."
    />

    <ul v-else class="space-y-3">
      <li v-for="l in lives" :key="l.id" class="rounded-lg border p-4 space-y-3" :data-live="l.id">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded px-2 py-0.5 text-xs font-medium inline-flex items-center gap-1" :class="STATUS[l.status].cls">
                <span v-if="l.status === 'gravando'" class="size-1.5 rounded-full bg-white animate-pulse" />
                {{ STATUS[l.status].rotulo }}
              </span>
              <span class="rounded border px-2 py-0.5 text-xs">{{ ROTULO_PLATAFORMA[l.plataforma] }}</span>
              <span class="font-medium truncate">{{ l.titulo || l.canal || 'Live sem título ainda' }}</span>
              <span v-if="l.canal && l.titulo" class="text-sm text-muted-foreground">· {{ l.canal }}</span>
            </div>
            <p class="text-sm text-muted-foreground">
              <span>Apresenta: <b class="text-foreground font-medium">{{ l.apresentador || '—' }}</b></span>
              <span> · Loja: {{ l.loja || '—' }}</span>
              <span> · cadastrada {{ dataHora(l.created_at) }}<template v-if="l.created_by_nome"> por {{ l.created_by_nome }}</template></span>
            </p>
            <a :href="l.link" target="_blank" rel="noopener noreferrer"
               class="text-xs text-primary inline-flex items-center gap-1 break-all hover:underline">
              {{ l.link }} <ExternalLink class="size-3 shrink-0" />
            </a>
          </div>
          <div class="flex items-center gap-1">
            <Button v-if="canEdit" size="sm" variant="ghost" aria-label="Editar" @click="abrirEditar(l)">
              <Pencil class="size-4" />
            </Button>
            <Button v-if="canEdit && l.status === 'erro'" size="sm" variant="outline" @click="regravar(l)">
              <RotateCcw class="size-4 mr-1" /> tentar de novo
            </Button>
            <Button v-if="canEdit && (l.status === 'aguardando' || l.status === 'erro')"
                    size="sm" variant="ghost" aria-label="Apagar" @click="apagar(l)">
              <Trash2 class="size-4" />
            </Button>
          </div>
        </div>

        <p v-if="l.produtos" class="text-sm"><span class="text-muted-foreground">Produtos:</span> {{ l.produtos }}</p>
        <p v-if="l.observacao" class="text-sm text-muted-foreground">{{ l.observacao }}</p>
        <p v-if="l.erro" class="text-sm text-destructive flex items-start gap-2">
          <AlertTriangle class="size-4 shrink-0 mt-0.5" /> {{ l.erro }}
        </p>
        <p v-if="roboSumiu(l) != null" class="text-sm text-amber-600 flex items-start gap-2">
          <AlertTriangle class="size-4 shrink-0 mt-0.5" />
          O robô do Mac não dá sinal há {{ roboSumiu(l) }} min. Confira se o gravador está ligado no Mac mini.
        </p>

        <div v-if="l.inicio_em" class="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p class="text-xs text-muted-foreground">Início → fim</p>
            <p>{{ dataHora(l.inicio_em) }} → {{ l.fim_em ? dataHora(l.fim_em) : (l.status === 'gravando' ? 'no ar' : '—') }}</p>
            <p class="text-xs text-muted-foreground">{{ duracao(l) }} · {{ tamanhoTxt(l.tamanho_bytes) }}</p>
          </div>
          <div>
            <p class="text-xs text-muted-foreground">Espectadores (pico · média)</p>
            <p>{{ numTxt(l.metricas?.espectadores_pico) }} · {{ numTxt(l.metricas?.espectadores_media) }}</p>
            <p class="text-xs text-muted-foreground">total que passou: {{ numTxt(l.metricas?.total_espectadores) }}</p>
          </div>
          <div>
            <p class="text-xs text-muted-foreground">Curtidas · comentários</p>
            <p>{{ numTxt(l.metricas?.curtidas) }} · {{ numTxt(l.metricas?.comentarios) }}</p>
            <p class="text-xs text-muted-foreground">seguidores ganhos: {{ numTxt(l.metricas?.seguidores_ganhos) }}</p>
          </div>
          <div>
            <p class="text-xs text-muted-foreground">Audiência ao longo da live</p>
            <svg v-if="sparkline(l.metricas?.serie)" viewBox="0 0 160 32" class="mt-1 block h-8 w-40 text-primary" aria-hidden="true">
              <polyline :points="sparkline(l.metricas?.serie)" fill="none" stroke="currentColor" stroke-width="1.5" />
            </svg>
            <p v-else class="text-xs text-muted-foreground">—</p>
          </div>
        </div>

        <div v-if="l.arquivo" class="flex items-center gap-2 text-xs">
          <span class="text-muted-foreground shrink-0">Arquivo no Mac mini:</span>
          <code class="truncate rounded bg-muted px-1.5 py-0.5">{{ l.arquivo }}</code>
          <Button size="sm" variant="ghost" class="h-6 px-1" aria-label="Copiar caminho" @click="copiar(l.arquivo)">
            <Copy class="size-3.5" />
          </Button>
        </div>
      </li>
    </ul>

    <!-- ============================================== modal novo / editar -->
    <div
      v-if="modalAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="fechar"
      @keydown.esc="fechar"
    >
      <section role="dialog" aria-modal="true" aria-labelledby="live-titulo"
               class="w-full max-w-lg max-h-[90vh] overflow-auto rounded-lg border bg-background p-5 space-y-4">
        <div class="flex items-start justify-between gap-3">
          <h2 id="live-titulo" class="text-lg font-semibold">{{ editando ? 'Editar live' : 'Nova live' }}</h2>
          <Button variant="ghost" size="sm" :disabled="salvando" aria-label="Fechar" @click="fechar">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <label for="live-link" class="text-sm font-medium">Link de compartilhar da live</label>
            <Input id="live-link" v-model="form.link" class="mt-1" :disabled="linkTravado || salvando"
                   placeholder="https://live.shopee.com.br/share?from=live&session=… ou https://www.tiktok.com/@loja/live" />
            <p v-if="form.link && plataformaForm" class="mt-1 text-xs text-muted-foreground">
              Plataforma: {{ ROTULO_PLATAFORMA[plataformaForm] }}
            </p>
            <p v-else-if="form.link" class="mt-1 text-xs text-destructive">{{ ERROS_LIVES.link_invalido }}</p>
            <p v-if="linkTravado" class="mt-1 text-xs text-muted-foreground">A gravação já começou: o link não muda mais.</p>
          </div>
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label for="live-apresentador" class="text-sm font-medium">Quem apresenta</label>
              <Input id="live-apresentador" v-model="form.apresentador" class="mt-1" :disabled="salvando" />
            </div>
            <div>
              <label for="live-loja" class="text-sm font-medium">Loja</label>
              <select id="live-loja" v-model="form.integration_id" :disabled="salvando"
                      class="mt-1 h-9 w-full rounded-md border bg-background px-2 text-sm">
                <option value="">—</option>
                <option v-for="o in lojasDoForm" :key="o.id" :value="o.id">{{ o.nome }}</option>
              </select>
            </div>
          </div>
          <div>
            <label for="live-produtos" class="text-sm font-medium">Produtos da live</label>
            <textarea id="live-produtos" v-model="form.produtos" rows="3" :disabled="salvando"
                      class="mt-1 w-full rounded-md border bg-background p-2 text-sm"
                      placeholder="Ex.: Mala ABS 28, kit 3 malas, nécessaire" />
          </div>
          <div>
            <label for="live-obs" class="text-sm font-medium">Observação</label>
            <textarea id="live-obs" v-model="form.observacao" rows="2" :disabled="salvando"
                      class="mt-1 w-full rounded-md border bg-background p-2 text-sm"
                      placeholder="Ex.: começa às 20h, cupom LIVE10" />
          </div>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="salvando" @click="fechar">Cancelar</Button>
          <Button :disabled="salvando || !plataformaForm" @click="salvar">
            <Loader2 v-if="salvando" class="size-4 mr-1 animate-spin" />
            {{ editando ? 'Salvar' : 'Cadastrar' }}
          </Button>
        </div>
      </section>
    </div>
  </div>
</template>
