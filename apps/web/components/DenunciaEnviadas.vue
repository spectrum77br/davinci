<script setup lang="ts">
// ── Ouvidoria › Denúncia › Anúncios e denúncias › sub-aba "Denúncias enviadas" ─────────────
// O histórico de cada denúncia feita (marketplace, Anatel, Anatel SEI…), como era a visão
// "Por denúncia" da antiga aba Denúncias (30/09). Mesma regra do sistema do mini: o mesmo
// canal + protocolo cobrindo vários anúncios é UMA linha. Prazo vencido sem resposta fica em
// vermelho. 01/10 (Vinicius: "hoje apareceria tudo junto, certo?"): clicar abre a ficha do
// anúncio — a mesma da aba — já na parte desta denúncia.
import { computed, onMounted, ref } from 'vue'
import {
  type DenunciaEnviada, dataBr, numero, pillResultado, pillSituacaoDenuncia, prazoVencido,
} from '~/lib/denuncia'

type Resposta = {
  total: number
  itens: DenunciaEnviada[]
  resumo: Record<string, Record<string, number>>
  opcoes: { canais: string[]; situacoes: string[] }
}

const emit = defineEmits<{ (e: 'abrir', d: DenunciaEnviada): void }>()

const { api } = useApi()
const itens = ref<DenunciaEnviada[]>([])
const total = ref(0)
const resumo = ref<Resposta['resumo']>({})
const opcoes = ref<Resposta['opcoes']>({ canais: [], situacoes: [] })
const carregando = ref(true)
const erro = ref<string | null>(null)

const canal = ref('')
const situacao = ref('')
const tipo = ref('')
const q = ref('')

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

function porCanal(c: string) {
  canal.value = canal.value === c ? '' : c
  void carregar()
}

onMounted(carregar)
defineExpose({ carregar })
</script>

<template>
  <div class="space-y-4">
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
            <th class="w-24">Data</th>
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
          <tr v-for="g in itens" :key="g.id" class="cursor-pointer" @click="emit('abrir', g)">
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
    <div class="text-sm text-muted-foreground">
      {{ numero(total) }} denúncias<span v-if="total > itens.length"> (mostrando as {{ itens.length }} mais recentes)</span>
    </div>
  </div>
</template>
