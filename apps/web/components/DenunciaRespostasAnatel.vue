<script setup lang="ts">
// ── Ouvidoria › Denúncia › Anúncios e denúncias › sub-aba "Respostas da Anatel" (07/10/2026) ─────────────
// Vinicius: "pode fazer a lista das respostas da Anatel no DaVinci"; "vamos conseguir ver no painel qual teve
// resposta da Anatel e qual o texto?". Duas respostas de 29/09 ("URL inexistente na Shopee") ficaram 8 dias sem
// ninguém ver. Uma linha por protocolo (Anatel Consumidor) ou processo (SEI): o texto da Anatel, a data, os
// anúncios e se seguem no ar, e o que fizemos depois (o robô anota na denúncia quando reabre ou junta documento).
// Padrão: só o que pede ação (respondida ou exigência); "todas" mostra também as já tratadas.
import { onMounted, ref } from 'vue'
import { CircleAlert, CircleCheck, TriangleAlert } from 'lucide-vue-next'
import { dataBr, numero, pillSituacaoAnuncio } from '~/lib/denuncia'

type AnuncioResp = {
  id: string
  titulo: string | null
  loja: string | null
  marketplace: string | null
  situacao: string | null
  verificado_em: string | null
}
type Resposta = {
  canal: string
  protocolo: string
  situacao: string
  pede_acao: boolean
  area: string
  respondida_em: string | null
  lido_em: string | null
  prazo_oficial: string | null
  texto: string
  acoes: string[]
  anuncios: AnuncioResp[]
  processo_sei: string[]
  dias: number | null
  denuncia_id: number
}

const emit = defineEmits<{ (e: 'abrir', anuncio: string, denuncia: number): void }>()

const { api } = useApi()
const itens = ref<Resposta[]>([])
const pedeAcao = ref(0)
const todas = ref(false)
const carregando = ref(true)
const erro = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const r = await api<{ total: number; pede_acao: number; itens: Resposta[] }>(
      `/api/denuncia/anatel/respostas${todas.value ? '?todas=1' : ''}`,
    )
    itens.value = r.itens
    pedeAcao.value = r.pede_acao
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}

function verTodas(v: boolean) {
  if (todas.value === v) return
  todas.value = v
  void carregar()
}

function pillSituacao(s: string): string {
  if (s === 'Exigência') return 'pill-danger'
  if (s === 'Respondida — analisar') return 'pill-warning'
  return 'pill-muted'
}

// a Anatel diz que o anúncio não existe / foi excluído, mas o robô viu no ar depois da resposta
const RE_SUMIU = /inexistente|exclu[ií]d|n[aã]o (?:foi )?localizad|n[aã]o encontrad|removid/i
function contradiz(it: Resposta, a: AnuncioResp): boolean {
  return RE_SUMIU.test(it.texto) && a.situacao === 'ativo' && !!a.verificado_em && (!it.respondida_em || a.verificado_em > it.respondida_em)
}

function ha(d: number | null): string {
  if (d === null) return ''
  if (d === 0) return 'hoje'
  return d === 1 ? 'há 1 dia' : `há ${d} dias`
}

onMounted(carregar)
defineExpose({ carregar })
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-2">
      <div class="inline-flex rounded-md border p-0.5 text-sm">
        <button
          type="button"
          class="rounded px-3 py-1"
          :class="!todas ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
          @click="verTodas(false)"
        >
          Pedem ação ({{ numero(pedeAcao) }})
        </button>
        <button
          type="button"
          class="rounded px-3 py-1"
          :class="todas ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
          @click="verTodas(true)"
        >
          Todas com resposta
        </button>
      </div>
      <p class="text-xs text-muted-foreground">
        O robô lê os protocolos do Anatel Consumidor e os processos do SEI no passo 1. Aqui aparece o que a Anatel respondeu e o texto dela.
      </p>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
    <div v-else-if="carregando && !itens.length" class="text-sm text-muted-foreground">carregando…</div>
    <div v-else-if="!itens.length" class="rounded-md border px-3 py-6 text-center text-sm text-muted-foreground">
      {{ todas ? 'A Anatel ainda não respondeu nenhuma denúncia.' : 'Nenhuma resposta da Anatel esperando ação.' }}
    </div>

    <article
      v-for="it in itens"
      :key="it.canal + it.protocolo"
      class="rounded-lg border p-3 space-y-3"
      :class="it.pede_acao ? 'border-amber-300 dark:border-amber-700' : ''"
    >
      <header class="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span :class="pillSituacao(it.situacao)">{{ it.situacao || '—' }}</span>
        <span class="text-sm font-medium">{{ it.canal }}</span>
        <span class="font-mono text-sm">{{ it.protocolo }}</span>
        <span class="text-sm">
          respondeu em <b class="tabular-nums">{{ dataBr(it.respondida_em, false) }}</b>
          <span class="text-muted-foreground" :class="it.pede_acao && (it.dias ?? 0) > 3 ? 'text-red-600 dark:text-red-400 font-medium' : ''">
            ({{ ha(it.dias) }})
          </span>
        </span>
        <span v-if="it.area" class="text-xs text-muted-foreground">{{ it.area }}</span>
        <span v-if="it.lido_em" class="ml-auto text-xs text-muted-foreground">lido pelo robô em {{ dataBr(it.lido_em) }}</span>
      </header>

      <blockquote class="rounded-md border-l-4 border-primary/60 bg-muted/40 px-3 py-2 text-sm whitespace-pre-line">
        {{ it.texto || 'A Anatel mudou a situação, mas o texto da resposta não veio na leitura do robô.' }}
      </blockquote>

      <div class="space-y-1">
        <div class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {{ it.anuncios.length === 1 ? 'Anúncio' : `${it.anuncios.length} anúncios` }}
        </div>
        <ul class="space-y-1">
          <li v-for="a in it.anuncios" :key="a.id" class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
            <button
              type="button"
              class="font-mono text-xs text-primary hover:underline"
              title="abrir a ficha do anúncio"
              @click="emit('abrir', a.id, it.denuncia_id)"
            >
              {{ a.id }}
            </button>
            <span class="min-w-0 truncate max-w-[420px]" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
            <span class="text-xs text-muted-foreground">{{ a.loja || '' }} · {{ a.marketplace || '' }}</span>
            <span :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao === 'ativo' ? 'no ar' : a.situacao || '—' }}</span>
            <span v-if="contradiz(it, a)" class="inline-flex items-center gap-1 text-xs font-medium text-red-700 dark:text-red-400">
              <TriangleAlert class="size-3.5" /> a Anatel diz que não existe, mas o robô viu no ar em {{ dataBr(a.verificado_em) }}
            </span>
          </li>
        </ul>
        <p v-if="it.processo_sei.length" class="text-xs text-muted-foreground">
          Os mesmos anúncios estão no processo do SEI <span class="font-mono">{{ it.processo_sei.join(', ') }}</span>.
        </p>
      </div>

      <div class="text-sm">
        <div v-if="it.acoes.length" class="flex items-start gap-1.5 text-emerald-700 dark:text-emerald-400">
          <CircleCheck class="size-4 mt-0.5 shrink-0" />
          <div><span class="font-medium">O que fizemos:</span> {{ it.acoes.join(' · ') }}</div>
        </div>
        <div v-else-if="it.pede_acao" class="flex items-center gap-1.5 text-amber-700 dark:text-amber-400">
          <CircleAlert class="size-4 shrink-0" /> Ninguém respondeu ainda.
        </div>
      </div>
    </article>
  </div>
</template>
