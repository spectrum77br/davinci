<script setup lang="ts">
// ── Denúncia › Denúncias (30/09/2026) ──────────────────────────────────────
// Cada denúncia feita (marketplace, Anatel, Anatel SEI…). Mesma regra do
// sistema do mini: o mesmo canal + protocolo cobrindo vários anúncios é UMA
// linha. Prazo vencido sem resposta fica em vermelho. Cópia só leitura.
import { computed, onMounted, ref } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import {
  type Prova, dataBr, numero, pillResultado, pillSituacaoAnuncio, pillSituacaoDenuncia, prazoVencido,
} from '~/lib/denuncia'

definePageMeta({ middleware: ['permission'], permission: { resource: 'denuncia', action: 'view' } })

type Grupo = {
  id: number
  ids: number[]
  canal: string | null
  protocolo: string | null
  data: string | null
  hora: string | null
  situacao: string | null
  resultado: string | null
  resultado_confirmado: number | null
  prazo: string | null
  tipo: string | null
  tentativa: number | null
  motivo: string | null
  refazer: number | null
  anuncios: { id: string | null; loja: string | null; titulo: string | null; situacao: string | null }[]
}
type Resposta = {
  total: number
  itens: Grupo[]
  resumo: Record<string, Record<string, number>>
  opcoes: { canais: string[]; situacoes: string[] }
}
type Detalhe = {
  denuncia: Record<string, any>
  linhas: Record<string, any>[]
  anuncios: { id: string; loja: string | null; titulo: string | null; situacao: string | null; url: string | null }[]
  provas: Prova[]
}

const { api } = useApi()
const itens = ref<Grupo[]>([])
const total = ref(0)
const resumo = ref<Resposta['resumo']>({})
const opcoes = ref<Resposta['opcoes']>({ canais: [], situacoes: [] })
const carregando = ref(true)
const erro = ref<string | null>(null)
const copia = ref<{ carregar: () => Promise<void> } | null>(null)

const canal = ref('')
const situacao = ref('')
const tipo = ref('')
const q = ref('')

const aberta = ref<number | null>(null)
const detalhe = ref<Detalhe | null>(null)
const anuncioAberto = ref<string | null>(null)

const canaisOrdenados = computed(() =>
  Object.entries(resumo.value).sort((a, b) => (b[1].total || 0) - (a[1].total || 0)),
)

function dicaCanal(r: Record<string, number>): string {
  return Object.entries(r)
    .filter(([k]) => k !== 'total')
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${v} ${k.toLowerCase()}`)
    .join(' · ')
}

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const qs = new URLSearchParams()
    if (canal.value) qs.set('canal', canal.value)
    if (situacao.value) qs.set('situacao', situacao.value)
    if (tipo.value) qs.set('tipo', tipo.value)
    if (q.value.trim()) qs.set('q', q.value.trim())
    const r = await api<Resposta>(`/api/denuncia/denuncias?${qs}`)
    itens.value = r.itens
    total.value = r.total
    resumo.value = r.resumo
    opcoes.value = r.opcoes
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}

function recarregar() {
  void copia.value?.carregar()
  void carregar()
}

function porCanal(c: string) {
  canal.value = canal.value === c ? '' : c
  void carregar()
}

async function abrir(g: Grupo) {
  aberta.value = g.id
  detalhe.value = null
  try {
    detalhe.value = await api<Detalhe>(`/api/denuncia/denuncias/${g.id}`)
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const gavetaAberta = computed({
  get: () => aberta.value !== null,
  set: (v: boolean) => {
    if (!v) aberta.value = null
  },
})
const d = computed(() => detalhe.value?.denuncia || {})

onMounted(carregar)
</script>

<template>
  <div class="space-y-5">
    <PageHeader
      title="Denúncias"
      description="Denúncias feitas pelo robô da fiscalização nos marketplaces e na Anatel — uma linha por protocolo."
    >
      <template #actions>
        <DenunciaCopia ref="copia" />
        <Button size="sm" variant="outline" @click="recarregar">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div v-if="canaisOrdenados.length" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
      <button
        v-for="[c, r] in canaisOrdenados"
        :key="c"
        type="button"
        class="text-left rounded-lg"
        :class="canal === c ? 'ring-2 ring-primary' : ''"
        :title="`filtrar ${c}`"
        @click="porCanal(c)"
      >
        <StatCard compact :label="c" :value="numero(r.total)" :hint="dicaCanal(r)" />
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="flex flex-wrap gap-2 items-center">
      <Input v-model="q" placeholder="protocolo, anúncio ou loja…" class="w-60" @keyup.enter="carregar" />
      <select v-model="canal" class="h-9 rounded-md border bg-background px-2 text-sm" @change="carregar">
        <option value="">todos canais</option>
        <option v-for="c in opcoes.canais" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="situacao" class="h-9 rounded-md border bg-background px-2 text-sm" @change="carregar">
        <option value="">todas situações</option>
        <option v-for="s in opcoes.situacoes" :key="s" :value="s">{{ s }}</option>
      </select>
      <select v-model="tipo" class="h-9 rounded-md border bg-background px-2 text-sm" @change="carregar">
        <option value="">normal e reiteração</option>
        <option value="normal">normal</option>
        <option value="reiteração">reiteração</option>
      </select>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th>Data</th>
            <th>Canal</th>
            <th>Protocolo</th>
            <th>Anúncios</th>
            <th>Situação</th>
            <th>Resultado</th>
            <th>Prazo</th>
            <th class="text-right">Tent.</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && itens.length === 0">
            <td colspan="8" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="itens.length === 0">
            <td colspan="8" class="text-center text-muted-foreground py-6">nenhuma denúncia neste filtro</td>
          </tr>
          <tr v-for="g in itens" :key="g.id" class="cursor-pointer" @click="abrir(g)">
            <td class="text-xs tabular-nums whitespace-nowrap">
              {{ dataBr(g.data, false) }}
              <div v-if="g.hora" class="text-[11px] text-muted-foreground">{{ g.hora }}</div>
            </td>
            <td class="text-xs whitespace-nowrap">
              {{ g.canal }}
              <div v-if="g.tipo && g.tipo !== 'normal'" class="text-[11px] text-muted-foreground">{{ g.tipo }}</div>
            </td>
            <td class="font-mono text-xs">{{ g.protocolo || '—' }}</td>
            <td class="text-xs max-w-[260px]">
              <div class="truncate" :title="g.anuncios.map((x) => `${x.id} ${x.loja || ''}`).join('\n')">
                <template v-if="g.anuncios.length === 1">{{ g.anuncios[0].loja || g.anuncios[0].id }}</template>
                <template v-else>{{ g.anuncios.length }} anúncios · {{ [...new Set(g.anuncios.map((x) => x.loja).filter(Boolean))].slice(0, 2).join(', ') }}</template>
              </div>
            </td>
            <td class="whitespace-nowrap">
              <span :class="pillSituacaoDenuncia(g.situacao)">{{ g.situacao || '—' }}</span>
              <span v-if="g.refazer" class="ml-1 text-[11px] text-amber-600">refazer</span>
            </td>
            <td><span v-if="g.resultado" :class="pillResultado(g.resultado)">{{ g.resultado }}</span></td>
            <td class="text-xs tabular-nums whitespace-nowrap" :class="prazoVencido(g.prazo, g.situacao) ? 'text-red-600 font-medium' : ''">
              {{ dataBr(g.prazo, false) }}
            </td>
            <td class="text-right text-xs tabular-nums">{{ g.tentativa || 1 }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="text-sm text-muted-foreground">{{ numero(total) }} denúncias<span v-if="total > itens.length"> (mostrando as {{ itens.length }} mais recentes)</span></div>

    <DenunciaGaveta
      v-model:open="gavetaAberta"
      :titulo="d.protocolo ? `${d.canal} · ${d.protocolo}` : `${d.canal || 'Denúncia'} #${aberta}`"
      :subtitulo="[dataBr(d.data, false), d.hora, d.tipo && d.tipo !== 'normal' ? d.tipo : null].filter(Boolean).join(' · ')"
    >
      <template #cabecalho-extra>
        <span v-if="d.situacao" :class="pillSituacaoDenuncia(d.situacao)">{{ d.situacao }}</span>
        <span v-if="d.resultado" :class="pillResultado(d.resultado)">{{ d.resultado }}</span>
      </template>
      <div v-if="!detalhe" class="text-sm text-muted-foreground">carregando…</div>
      <template v-else>
        <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <div><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Prazo</dt><dd :class="prazoVencido(d.prazo, d.situacao) ? 'text-red-600 font-medium' : ''">{{ dataBr(d.prazo, false) }}</dd></div>
          <div><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Tentativa</dt><dd>{{ d.tentativa || 1 }}</dd></div>
          <div v-if="d.motivo" class="col-span-2"><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Motivo</dt><dd>{{ d.motivo }}</dd></div>
          <div v-if="d.protocolo_citado"><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Protocolo citado</dt><dd class="font-mono text-xs">{{ d.protocolo_citado }}</dd></div>
          <div v-if="d.sei_processo"><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Processo SEI</dt><dd class="font-mono text-xs">{{ d.sei_processo }}</dd></div>
          <div v-if="d.status_anatel" class="col-span-2"><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Status na Anatel</dt><dd>{{ d.status_anatel }} <span class="text-xs text-muted-foreground">{{ dataBr(d.status_anatel_em) }}</span></dd></div>
          <div v-if="d.resultado_nota" class="col-span-2"><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Nota do resultado</dt><dd class="whitespace-pre-wrap">{{ d.resultado_nota }}</dd></div>
        </dl>

        <section v-if="d.resposta || d.resposta_anatel">
          <h3 class="text-sm font-semibold mb-2">Resposta</h3>
          <p class="text-sm whitespace-pre-wrap rounded-lg border bg-muted/30 px-3 py-2">{{ d.resposta || d.resposta_anatel }}</p>
        </section>

        <section>
          <h3 class="text-sm font-semibold mb-2">Anúncios denunciados ({{ detalhe.anuncios.length }})</h3>
          <ul class="space-y-1">
            <li v-for="x in detalhe.anuncios" :key="x.id">
              <button type="button" class="w-full text-left rounded-md border px-3 py-2 text-sm hover:border-primary/50 flex items-center gap-2" @click="aberta = null; anuncioAberto = x.id">
                <span class="font-mono text-xs shrink-0">{{ x.id }}</span>
                <span class="truncate flex-1">{{ x.loja }} — {{ x.titulo }}</span>
                <span :class="pillSituacaoAnuncio(x.situacao)">{{ x.situacao }}</span>
              </button>
            </li>
          </ul>
        </section>

        <section v-if="d.texto">
          <h3 class="text-sm font-semibold mb-2">Texto enviado</h3>
          <p class="text-xs whitespace-pre-wrap rounded-lg border bg-muted/30 px-3 py-2 max-h-80 overflow-y-auto">{{ d.texto }}</p>
        </section>

        <section>
          <h3 class="text-sm font-semibold mb-2">Provas ({{ detalhe.provas.length }})</h3>
          <DenunciaProvas :provas="detalhe.provas" />
        </section>

        <p v-if="d.obs" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ d.obs }}</p>
      </template>
    </DenunciaGaveta>

    <DenunciaAnuncioGaveta :anuncio-id="anuncioAberto" @fechar="anuncioAberto = null" />
  </div>
</template>
