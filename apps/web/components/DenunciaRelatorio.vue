<script setup lang="ts">
// ── Ouvidoria › Denúncia › Robô › Ocorrências: relatório do dia (05/10/2026) ──
// Vinicius: "um relatório no final do dia, mostrando quantos anúncios ele achou, quantos
// denunciou na loja, quantos abriu reclamação na Anatel etc." — dia do calendário (0h–24h),
// dentro das Ocorrências, e "tem que sair em Excel". Hoje abre ao vivo (parcial); dia que
// passou vem congelado (contas em services/denuncia_relatorio.py).
import { watch, ref, computed } from 'vue'
import { Check, FileSpreadsheet, Flag, Landmark, Loader2, MailCheck, Search, Trash2, Camera } from 'lucide-vue-next'
import { numero } from '~/lib/denuncia'

type PorGrupo = { Nosso: number; Diversos: number; Outros: number }
type PorResposta = { removidos: number; recusados: number; sem_resposta: number; outras: number }
type Processo = { processo: string; hora: string; loja: string; site: string; grupo: string; anuncios: number }
type Linha = { anuncio_id: string; site: string; loja: string; titulo: string; grupo: string; hora: string }
type Relatorio = {
  dia: string
  parcial: boolean
  fechado_em: string | null
  lido_em: string | null
  lido_por: string | null
  anotado: boolean
  numeros: {
    achou: { total: number; lojas_proprias: number; descartados: number; por_site: Record<string, PorGrupo> }
    denunciou: { total: number; de_novo: number; replicas?: number; por_site: Record<string, PorGrupo> }
    anatel: {
      lojas: number; anuncios: number; consumidor: number; processos: Processo[]
      // 05/10: andamento na Anatel (relatórios fechados antes não têm)
      situacao?: Record<string, number>
      movimentos?: { processo: string; situacao: string; area: string; loja: string; site: string }[]
    }
    respostas: { total: number; por_site: Record<string, PorResposta> }
    sairam: { total: number; lista: Linha[] }
    prints: { capturas: number; anuncios: number; registros: number }
    conferidos: { anuncios: number; fora_do_ar: number }
  }
  passos: { nome: string; inicio: string | null; fim: string | null; minutos: number | null; situacao: string; erro: string }[]
  ocorrencias: { titulo: string; tipo: string; vezes: number; primeira: string | null; ultima: string | null; detalhe: string }[]
  sem_noticia: { de: string; ate: string }[]
}

const props = defineProps<{ open: boolean; dia: string | null; podeMarcar?: boolean }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void; (e: 'lido', dia: string): void }>()

const { api } = useApi()
const rel = ref<Relatorio | null>(null)
const erro = ref<string | null>(null)
const carregando = ref(false)
const baixando = ref(false)
const marcando = ref(false)

async function carregar() {
  if (!props.dia) return
  carregando.value = true
  erro.value = null
  try {
    rel.value = await api<Relatorio>(`/api/denuncia/relatorios/${props.dia}`)
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}
watch(() => [props.open, props.dia], ([aberto]) => {
  if (aberto) {
    rel.value = null
    void carregar()
  }
}, { immediate: true })

const diaBr = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}`
function hm(v: string | null | undefined): string {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return String(v).slice(11, 16)
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })
}
function diaHm(v: string | null | undefined): string {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return String(v)
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })
}

const titulo = computed(() => (props.dia ? `Relatório do dia ${diaBr(props.dia)}` : 'Relatório do dia'))
const subtitulo = computed(() => {
  const r = rel.value
  if (!r) return 'carregando…'
  if (r.parcial) return 'Dia em andamento — números até agora (0h às 24h, horário de Brasília)'
  const lido = r.lido_em ? ` · lido por ${r.lido_por || '—'} em ${diaHm(r.lido_em)}` : ''
  return `Fechado em ${diaHm(r.fechado_em)}${lido}`
})

// status_anatel dos processos do SEI → nome na tela (a ordem é a do caminho)
const SITUACOES: [string, string][] = [
  ['Enviada', 'sem leitura'], ['Recebida', 'recebidos'], ['Em tratamento', 'na fiscalização'],
  ['Respondida — analisar', 'respondidos'], ['Exigência', 'pedem complemento'],
]
const NOME_SITUACAO: Record<string, string> = {
  Recebida: 'recebido', 'Em tratamento': 'na fiscalização', 'Respondida — analisar': 'Anatel respondeu', Exigência: 'pede complemento',
}
const SITES = ['Mercado Livre', 'Shopee', 'TikTok Shop', 'Amazon', 'Anatel']
const NOME_SITE: Record<string, string> = { 'TikTok Shop': 'TikTok' }
const porSite = computed(() => {
  const n = rel.value?.numeros
  if (!n) return []
  const nomes = new Set([...Object.keys(n.achou.por_site), ...Object.keys(n.denunciou.por_site), ...Object.keys(n.respostas.por_site)])
  return SITES.filter((s) => nomes.has(s)).concat([...nomes].filter((s) => !SITES.includes(s))).map((s) => {
    const a = n.achou.por_site[s]
    const d = n.denunciou.por_site[s]
    const r = n.respostas.por_site[s]
    return {
      site: NOME_SITE[s] || s,
      achouNosso: a?.Nosso || 0,
      achouDiversos: (a?.Diversos || 0) + (a?.Outros || 0),
      denNosso: d?.Nosso || 0,
      denDiversos: (d?.Diversos || 0) + (d?.Outros || 0),
      removidos: r?.removidos || 0,
      recusados: r?.recusados || 0,
      semResposta: r?.sem_resposta || 0,
    }
  })
})
const total = computed(() => porSite.value.reduce((t, l) => ({
  achouNosso: t.achouNosso + l.achouNosso, achouDiversos: t.achouDiversos + l.achouDiversos,
  denNosso: t.denNosso + l.denNosso, denDiversos: t.denDiversos + l.denDiversos,
  removidos: t.removidos + l.removidos, recusados: t.recusados + l.recusados, semResposta: t.semResposta + l.semResposta,
}), { achouNosso: 0, achouDiversos: 0, denNosso: 0, denDiversos: 0, removidos: 0, recusados: 0, semResposta: 0 }))

const SITUACAO: Record<string, string> = {
  feito: 'pill-success', erro: 'pill-danger', rodando: 'pill-info', 'na fila': 'pill-muted', 'parou pra outro passo': 'pill-warning',
}

async function baixar() {
  if (!props.dia) return
  baixando.value = true
  try {
    const blob = await api<Blob>(`/api/denuncia/relatorios/${props.dia}/excel`, { responseType: 'blob' as any })
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `robo-denuncia-${props.dia}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    useToasts().push({ kind: 'error', title: 'Não deu para baixar o Excel', lines: e?.data?.detail?.code || e?.message || 'erro' })
  } finally {
    baixando.value = false
  }
}

async function marcarLido() {
  if (!props.dia) return
  marcando.value = true
  try {
    await api(`/api/denuncia/relatorios/${props.dia}/lido`, { method: 'POST' })
    emit('lido', props.dia)
    await carregar()
  } catch (e: any) {
    useToasts().push({ kind: 'error', title: 'Não deu para marcar como lido', lines: e?.data?.detail?.code || e?.message || 'erro' })
  } finally {
    marcando.value = false
  }
}
</script>

<template>
  <DenunciaGaveta :open="open" :titulo="titulo" :subtitulo="subtitulo" @update:open="(v) => emit('update:open', v)">
    <template #cabecalho-extra>
      <span v-if="rel?.parcial" class="pill-info">até agora</span>
    </template>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
    <div v-else-if="carregando && !rel" class="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 class="size-4 animate-spin" /> carregando…
    </div>

    <template v-if="rel">
      <div class="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" :disabled="baixando" @click="baixar">
          <Loader2 v-if="baixando" class="mr-1 size-3.5 animate-spin" />
          <FileSpreadsheet v-else class="mr-1 size-3.5" /> Baixar Excel
        </Button>
        <Button v-if="podeMarcar && !rel.parcial && !rel.lido_em" size="sm" variant="outline" :disabled="marcando" @click="marcarLido">
          <Loader2 v-if="marcando" class="mr-1 size-3.5 animate-spin" />
          <Check v-else class="mr-1 size-3.5" /> Lido
        </Button>
      </div>

      <!-- números do dia -->
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <StatCard compact label="Anúncios novos" :value="numero(rel.numeros.achou.total)" :icon="Search"
                  :hint="`Nosso ${total.achouNosso} · Diversos ${total.achouDiversos}`" />
        <StatCard compact label="Denúncias nas lojas" :value="numero(rel.numeros.denunciou.total)" :icon="Flag"
                  :hint="rel.numeros.denunciou.replicas ? `${rel.numeros.denunciou.replicas} réplica(s) · Nosso ${total.denNosso} · Diversos ${total.denDiversos}` : rel.numeros.denunciou.de_novo ? `${rel.numeros.denunciou.de_novo} de novo` : `Nosso ${total.denNosso} · Diversos ${total.denDiversos}`" />
        <StatCard compact label="Anatel (lojas)" :value="numero(rel.numeros.anatel.lojas)" :icon="Landmark"
                  :hint="`${rel.numeros.anatel.anuncios} anúncio(s) nas petições`" />
        <StatCard compact label="Removidos" :value="numero(total.removidos)" tone="success" :icon="MailCheck"
                  :hint="`recusados ${total.recusados} · sem resposta ${total.semResposta}`" />
        <StatCard compact label="Saíram do ar" :value="numero(rel.numeros.sairam.total)" :icon="Trash2"
                  :hint="`${rel.numeros.conferidos.anuncios} conferido(s) no dia`" />
        <StatCard compact label="Prints" :value="numero(rel.numeros.prints.capturas)" :icon="Camera"
                  :hint="`${rel.numeros.prints.anuncios} anúncio(s)`" />
      </div>

      <!-- por site -->
      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Por site</h3>
        <div class="table-card overflow-x-auto">
          <table class="w-full min-w-[600px] text-xs">
            <thead>
              <tr>
                <th rowspan="2">Site</th>
                <th colspan="2" class="text-center">Achou</th>
                <th colspan="2" class="text-center">Denunciou</th>
                <th colspan="3" class="text-center">Respostas das plataformas</th>
              </tr>
              <tr>
                <th class="text-right">Nosso</th><th class="text-right">Diversos</th>
                <th class="text-right">Nosso</th><th class="text-right">Diversos</th>
                <th class="text-right">Removidos</th><th class="text-right">Recusados</th><th class="text-right">Sem resposta</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!porSite.length"><td colspan="8" class="py-4 text-center text-muted-foreground">Nada no dia.</td></tr>
              <tr v-for="l in porSite" :key="l.site">
                <td class="font-medium">{{ l.site }}</td>
                <td class="text-right tabular-nums">{{ l.achouNosso || '—' }}</td>
                <td class="text-right tabular-nums">{{ l.achouDiversos || '—' }}</td>
                <td class="text-right tabular-nums">{{ l.denNosso || '—' }}</td>
                <td class="text-right tabular-nums">{{ l.denDiversos || '—' }}</td>
                <td class="text-right tabular-nums text-emerald-700 dark:text-emerald-400">{{ l.removidos || '—' }}</td>
                <td class="text-right tabular-nums">{{ l.recusados || '—' }}</td>
                <td class="text-right tabular-nums">{{ l.semResposta || '—' }}</td>
              </tr>
              <tr v-if="porSite.length > 1" class="bg-muted/40 font-semibold">
                <td>Total</td>
                <td class="text-right tabular-nums">{{ total.achouNosso }}</td>
                <td class="text-right tabular-nums">{{ total.achouDiversos }}</td>
                <td class="text-right tabular-nums">{{ total.denNosso }}</td>
                <td class="text-right tabular-nums">{{ total.denDiversos }}</td>
                <td class="text-right tabular-nums">{{ total.removidos }}</td>
                <td class="text-right tabular-nums">{{ total.recusados }}</td>
                <td class="text-right tabular-nums">{{ total.semResposta }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="text-[11px] text-muted-foreground">
          Achou = anúncios de concorrentes vistos pela 1ª vez no dia (fora {{ rel.numeros.achou.lojas_proprias }} das nossas lojas e
          {{ rel.numeros.achou.descartados }} capas/peças). Respostas = o que as plataformas responderam no dia, de denúncias de qualquer data.
        </p>
      </section>

      <!-- Anatel -->
      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Anatel — {{ rel.numeros.anatel.lojas }} loja(s) peticionada(s)</h3>
        <div v-if="rel.numeros.anatel.processos.length" class="table-card overflow-x-auto">
          <table class="w-full min-w-[560px] text-xs">
            <thead><tr><th>Hora</th><th>Loja</th><th>Site</th><th>Certificado</th><th class="text-right">Anúncios</th><th>Processo SEI</th></tr></thead>
            <tbody>
              <tr v-for="p in rel.numeros.anatel.processos" :key="p.processo + p.loja">
                <td class="tabular-nums">{{ p.hora }}</td>
                <td class="font-medium">{{ p.loja }}</td>
                <td>{{ NOME_SITE[p.site] || p.site }}</td>
                <td>{{ p.grupo }}</td>
                <td class="text-right tabular-nums">{{ p.anuncios }}</td>
                <td class="font-mono text-[11px]">{{ p.processo }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="text-xs text-muted-foreground">Nenhuma petição no dia.</p>
        <!-- 05/10: o andamento na Anatel (lido no SEI pelo passo 1) -->
        <div v-if="rel.numeros.anatel.situacao" class="rounded-md border bg-muted/30 px-3 py-2 text-xs">
          <div class="font-medium">Todos os processos no SEI</div>
          <div class="mt-0.5 text-muted-foreground">
            <template v-for="(k, i) in SITUACOES" :key="k[0]">
              <span v-if="i"> · </span>{{ k[1] }} <span class="font-semibold text-foreground">{{ rel.numeros.anatel.situacao[k[0]] || 0 }}</span>
            </template>
          </div>
          <div v-if="rel.numeros.anatel.movimentos?.length" class="mt-2 space-y-0.5">
            <div class="font-medium">A Anatel mexeu em {{ rel.numeros.anatel.movimentos.length }} processo(s) no dia</div>
            <div v-for="m in rel.numeros.anatel.movimentos" :key="m.processo" class="flex gap-2">
              <span class="font-mono text-[11px]">{{ m.processo }}</span>
              <span>{{ NOME_SITUACAO[m.situacao] || m.situacao }}<template v-if="m.area"> ({{ m.area }})</template></span>
              <span class="truncate text-muted-foreground">{{ m.loja }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- saíram do ar -->
      <section v-if="rel.numeros.sairam.lista.length" class="space-y-2">
        <h3 class="text-sm font-semibold">Saíram do ar — {{ rel.numeros.sairam.total }}</h3>
        <ul class="space-y-1 text-xs">
          <li v-for="a in rel.numeros.sairam.lista" :key="a.anuncio_id" class="flex gap-2">
            <span class="w-24 shrink-0 text-muted-foreground">{{ NOME_SITE[a.site] || a.site }}</span>
            <span class="w-36 shrink-0 truncate font-medium" :title="a.loja">{{ a.loja }}</span>
            <span class="min-w-0 truncate" :title="a.titulo">{{ a.titulo }}</span>
          </li>
        </ul>
      </section>

      <!-- como o robô rodou -->
      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Como o robô rodou</h3>
        <p v-if="!rel.anotado" class="text-xs text-muted-foreground">
          Sem anotação do robô neste dia (o DaVinci anota desde 05/10/2026; ou o Mac mini não deu notícia).
        </p>
        <div v-else class="table-card overflow-x-auto">
          <table class="w-full min-w-[560px] text-xs">
            <thead><tr><th>Horário</th><th>Passo</th><th>Como terminou</th></tr></thead>
            <tbody>
              <tr v-if="!rel.passos.length"><td colspan="3" class="py-4 text-center text-muted-foreground">Nenhum passo rodou.</td></tr>
              <tr v-for="(p, i) in rel.passos" :key="i">
                <td class="whitespace-nowrap tabular-nums">
                  {{ hm(p.inicio) || '—' }}<template v-if="p.fim"> – {{ hm(p.fim) }}</template>
                  <span v-if="p.minutos !== null" class="text-muted-foreground"> ({{ p.minutos }} min)</span>
                </td>
                <td class="font-medium">{{ p.nome }}</td>
                <td>
                  <span :class="SITUACAO[p.situacao] || 'pill-muted'">{{ p.situacao }}</span>
                  <div v-if="p.erro" class="mt-0.5 line-clamp-2 text-red-700 dark:text-red-400" :title="p.erro">{{ p.erro }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- o que travou -->
      <section v-if="rel.anotado" class="space-y-2">
        <h3 class="text-sm font-semibold">O que apareceu nas ocorrências</h3>
        <ul v-if="rel.ocorrencias.length || rel.sem_noticia.length" class="space-y-1.5 text-xs">
          <li v-for="b in rel.sem_noticia" :key="b.de" class="flex gap-2">
            <span class="pill-danger shrink-0">sem notícia</span>
            <span>Mac mini sem notícia de {{ diaHm(b.de) }} a {{ diaHm(b.ate) }}</span>
          </li>
          <li v-for="o in rel.ocorrencias" :key="o.titulo + o.tipo" class="flex gap-2">
            <span class="shrink-0" :class="o.tipo === 'pessoa' ? 'pill-danger' : 'pill-warning'">{{ o.tipo === 'pessoa' ? 'pessoa' : 'aviso' }}</span>
            <div class="min-w-0">
              <div class="font-medium">
                {{ o.titulo }}<span v-if="o.vezes > 1" class="text-muted-foreground"> · {{ o.vezes }} vezes</span>
                <span class="font-normal text-muted-foreground"> · {{ hm(o.primeira) }}<template v-if="hm(o.ultima) !== hm(o.primeira)"> a {{ hm(o.ultima) }}</template></span>
              </div>
              <div v-if="o.detalhe" class="line-clamp-2 text-muted-foreground" :title="o.detalhe">{{ o.detalhe }}</div>
            </div>
          </li>
        </ul>
        <p v-else class="text-xs text-muted-foreground">Nada travou.</p>
      </section>
    </template>
  </DenunciaGaveta>
</template>
