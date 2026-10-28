<script setup lang="ts">
// Garantia automática (28/10/2026) — o histórico da garantia no estilo do
// Atendimento: as mensagens das conversas ligadas (o robô liga pelo nº do
// pedido), uma embaixo da outra, com as fotos e os VÍDEOS (tocam aqui), o
// separador de dia e os marcos (compra, entrega, outro pedido do mesmo
// comprador, atendimento registrado). Tudo lido NA HORA do Atendimento
// (GET /api/garantias/{id}/historico): nada é copiado. A foto e o vídeo vêm do
// CDN da plataforma, como no Atendimento — o link pode expirar; o que vale
// como prova é o "Registrar atendimento", que baixa e congela.
// "Registrar atendimento" abre o mesmo modal do Atendimento (GarantiaVincular)
// já com esta garantia e a conversa escolhida.
import { onKeyStroke } from '@vueuse/core'
import { AlertCircle, ExternalLink, FileText, Loader2, MessageSquare, RefreshCw, ShieldCheck, Sparkles, Video } from 'lucide-vue-next'
import type { ConversaDetalhe, Mensagem } from '~/components/AtendimentoPlataforma.vue'
import {
  CANAL_ROTULO,
  dataCurta,
  errosDaApi,
  formularioSacLegivel,
  ladoDaMensagem,
  linhasDoHistorico,
  nomePlataforma,
  pecasDoHistorico,
  textoNf,
  type AtendimentoGarantia,
  type GarantiaLinha,
  type Historico,
  type HistoricoConversa,
  type HistoricoMensagem,
  type SituacaoConversa,
} from '~/lib/garantias'

// `automatico`: o robô está ligado (GET /regras) — só então a tela promete
// que a conversa "aparece aqui sozinha".
const props = defineProps<{ garantia: GarantiaLinha; versao?: number; automatico?: boolean }>()
const emit = defineEmits<{ (e: 'detalhes', id: number): void; (e: 'mudou'): void }>()

const { api } = useApi()
const acesso = useGarantiaAcesso()

const historico = ref<Historico | null>(null)
const carregando = ref(false)
const erro = ref('')
let geracao = 0
const rolagem = ref<HTMLElement | null>(null)

async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = ''
  try {
    const h = await api<Historico>(`/api/garantias/${props.garantia.id}/historico`)
    if (g !== geracao) return
    historico.value = h
    await nextTick()
    if (rolagem.value) rolagem.value.scrollTop = rolagem.value.scrollHeight
  } catch (e) {
    if (g !== geracao) return
    erro.value = errosDaApi(e).geral || 'Não consegui carregar o histórico.'
  } finally {
    if (g === geracao) carregando.value = false
  }
}
// Várias fontes: o Vue compara valor a valor (a lista relida traz OUTRO
// objeto da mesma garantia, e isso não pode reler o histórico nem gravar
// "consultou" de novo).
watch([() => props.garantia.id, () => props.versao], () => { historico.value = null; void carregar() }, { immediate: true })

const linhas = computed(() => (historico.value ? linhasDoHistorico(historico.value) : []))
const conversas = computed(() => historico.value?.conversas || [])
// SAC do site: a conversa do protocolo. O e-mail do formulário vira um cartão
// com o texto inteiro (ele chega como mensagem de sistema do e-mail).
const conversaPorId = computed(() => new Map(conversas.value.map((c) => [c.id, c])))
function protocoloDa(m: HistoricoMensagem): string | null {
  return conversaPorId.value.get(m.conversa_id)?.protocolo || null
}
function linkDa(m: HistoricoMensagem): string {
  return conversaPorId.value.get(m.conversa_id)?.link || '/atendimento'
}
const sacAberto = ref<Set<string>>(new Set())
function alternarSac(id: string) {
  const s = new Set(sacAberto.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  sacAberto.value = s
}

function hora(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}
// A resposta da loja que NÃO saiu (ou ficou a conferir) — o selo do Atendimento.
const STATUS_MSG: Record<string, { label: string; cls: string; hint: string }> = {
  enviando: { label: 'enviando…', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300', hint: 'saindo para a plataforma' },
  revisar: { label: 'a conferir', cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300', hint: 'a plataforma não confirmou — pode não ter chegado ao comprador' },
  falhou: { label: 'não saiu', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'a plataforma recusou — a mensagem NÃO chegou ao comprador' },
}
// Quantas mensagens (fora as de sistema) cada conversa tem no histórico: só
// dá para registrar atendimento de conversa com mensagem depois da compra.
const mensagensPorConversa = computed(() => {
  const n = new Map<string, number>()
  for (const m of historico.value?.mensagens || []) {
    if (m.autor !== 'sistema') n.set(m.conversa_id, (n.get(m.conversa_id) || 0) + 1)
  }
  return n
})
function rotuloConversa(c: HistoricoConversa | null): string {
  if (!c) return 'outra conversa'
  if (c.protocolo) return `SAC do site · protocolo ${c.protocolo}`
  const canal = CANAL_ROTULO[c.canal] || c.canal
  return `${nomePlataforma(c.plataforma) || c.plataforma} · ${canal}${c.comprador_nome ? ` · ${c.comprador_nome}` : ''}`
}
function rotuloAutor(m: HistoricoMensagem): string {
  const lado = ladoDaMensagem(m)
  if (lado === 'cliente') return 'Cliente'
  if (lado === 'mediador') return 'Mediador da plataforma'
  if (m.origem === 'davinci_ia') return 'Loja · IA do DaVinci'
  if (m.origem === 'davinci_humano') return 'Loja · equipe pelo DaVinci'
  if (m.origem === 'davinci_auto') return 'Loja · automática'
  return 'Loja'
}
function balaoCls(m: HistoricoMensagem): string {
  const lado = ladoDaMensagem(m)
  if (lado === 'cliente') return 'rounded-tl-sm border border-border/70 bg-background'
  if (lado === 'mediador') return 'rounded-tl-sm border border-violet-300/70 bg-violet-50 dark:border-violet-800/60 dark:bg-violet-900/25'
  return 'rounded-tr-sm bg-sky-100 dark:bg-sky-900/45'
}
const MARCO_CLS: Record<string, string> = {
  compra: 'border-slate-300 bg-background text-muted-foreground',
  entrega: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200',
  outro_pedido: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-200',
  atendimento: 'border-primary/40 bg-primary/5 text-foreground',
}

// Links no texto viram clicáveis (sem v-html: o texto vem da plataforma).
const RE_URL = /(https?:\/\/[^\s|]+)/g
function partes(t: string | null): { url: boolean; v: string }[] {
  const s = t || ''
  const out: { url: boolean; v: string }[] = []
  let ultimo = 0
  for (const m of s.matchAll(RE_URL)) {
    const i = m.index ?? 0
    if (i > ultimo) out.push({ url: false, v: s.slice(ultimo, i) })
    out.push({ url: true, v: m[0] })
    ultimo = i + m[0].length
  }
  if (ultimo < s.length) out.push({ url: false, v: s.slice(ultimo) })
  return out
}

// Foto aberta grande (Esc ou clique fora fecha); vídeo que não tocou vira link.
const imagemAberta = ref<{ url: string; nome: string } | null>(null)
onKeyStroke('Escape', () => { imagemAberta.value = null })
const videoFalhou = ref<Set<string>>(new Set())
const fotoFalhou = ref<Set<string>>(new Set())
function falhouVideo(chave: string) { videoFalhou.value = new Set([...videoFalhou.value, chave]) }
function falhouFoto(chave: string) { fotoFalhou.value = new Set([...fotoFalhou.value, chave]) }

// ─── registrar atendimento (o modal do Atendimento, já com esta garantia) ───
const registrando = ref<HistoricoConversa | null>(null)
const mensagensDaConversa = computed<Mensagem[]>(() =>
  (historico.value?.mensagens || []).filter((m) => m.conversa_id === registrando.value?.id) as unknown as Mensagem[],
)
// "Nenhuma marcada" = as 100 mais recentes DO QUE A TELA MOSTRA (desde a compra).
const idsPadrao = computed(() =>
  mensagensDaConversa.value.filter((m) => m.autor !== 'sistema').slice(-100).map((m) => m.id),
)
const conversaDoModal = computed(() => {
  const c = registrando.value
  if (!c) return null
  const ms = mensagensDaConversa.value
  return {
    id: c.id,
    plataforma: c.plataforma,
    canal: c.canal,
    conta: c.conta,
    comprador_nome: c.comprador_nome,
    pedido_marketplace: c.pedido_marketplace,
    ultima_mensagem_em: ms.length ? ms[ms.length - 1].enviada_em : null,
  } as unknown as ConversaDetalhe
})
const situacaoDoModal = computed<SituacaoConversa | null>(() => {
  const c = registrando.value
  if (!c) return null
  return {
    conversa_id: c.id,
    pedido_encontrado: true,
    pedido_bling: props.garantia.pedido_bling,
    pedido_marketplace: props.garantia.pedido_marketplace,
    produto_uranyx: true,
    cpf_conhecido: true,
    alerta_cpf_sem_garantia: false,
    garantias: [props.garantia],
    busca_sugerida: props.garantia.pedido_bling,
    vinculos: [],
  }
})
function aoRegistrar(_a: AtendimentoGarantia) {
  registrando.value = null
  emit('mudou')
  void carregar()
}
</script>

<template>
  <section class="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border bg-card" data-garantia-historico>
    <!-- cabeçalho: quem, pedido, NF, prazos e de onde vieram as conversas -->
    <header class="space-y-2 border-b px-4 py-3">
      <div class="flex flex-wrap items-start gap-2">
        <div class="min-w-0 flex-1">
          <h2 class="truncate text-base font-semibold">{{ garantia.cliente_nome }}</h2>
          <p class="text-xs text-muted-foreground">
            pedido {{ garantia.pedido_bling }}<template v-if="garantia.pedido_marketplace"> ({{ garantia.pedido_marketplace }})</template>
            <template v-if="garantia.plataforma"> · {{ nomePlataforma(garantia.plataforma) }}</template>
            <template v-if="garantia.conta"> · {{ garantia.conta }}</template>
            · NF {{ textoNf(garantia.nf_numero, garantia.nf_serie) }}
          </p>
        </div>
        <GarantiaStatus :status="garantia.status" :entregue-sem-data="garantia.entregue_sem_data" />
        <Button size="sm" variant="ghost" class="h-8" :disabled="carregando" title="ler de novo" @click="carregar">
          <RefreshCw class="size-4" :class="{ 'animate-spin': carregando }" />
        </Button>
        <Button size="sm" variant="outline" class="h-8" data-abrir-detalhe @click="emit('detalhes', garantia.id)">
          <FileText class="mr-1 size-4" /> Detalhes
        </Button>
      </div>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span><span class="text-muted-foreground">Entrega</span> <span class="tabular-nums">{{ dataCurta(garantia.data_inicio) }}</span></span>
        <span><span class="text-muted-foreground">Fim HW</span> <span class="tabular-nums">{{ dataCurta(garantia.fim_hardware) }}</span></span>
        <span><span class="text-muted-foreground">Fim SW</span> <span class="tabular-nums">{{ dataCurta(garantia.fim_software) }}</span></span>
        <span v-if="garantia.origem === 'robo'" class="inline-flex items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-violet-700 dark:text-violet-300" title="criada sozinha pelo DaVinci com a nota fiscal de produto do pedido" data-selo-robo>
          <Sparkles class="size-3" /> criada sozinha pela NF
        </span>
      </div>
      <!-- as conversas ligadas: abrir no Atendimento ou registrar o atendimento -->
      <ul v-if="conversas.length" class="flex flex-wrap gap-1.5" data-conversas-ligadas>
        <li v-for="c in conversas" :key="c.id" class="inline-flex items-center gap-1 rounded-full border bg-background py-0.5 pl-1.5 pr-1 text-xs">
          <AtendimentoIconePlataforma :plataforma="c.plataforma" :tamanho="14" decorativo />
          <span class="max-w-[220px] truncate" :title="rotuloConversa(c)">{{ rotuloConversa(c) }}</span>
          <a :href="c.link" target="_blank" rel="noopener" class="rounded p-0.5 text-muted-foreground hover:text-foreground" title="abrir esta conversa no Atendimento"><ExternalLink class="size-3.5" /></a>
          <button v-if="acesso.registra && mensagensPorConversa.get(c.id)" type="button" class="rounded bg-primary/10 px-1.5 py-px font-medium text-primary hover:bg-primary/20" title="registrar o atendimento desta conversa na garantia (tipo, solução — copia as mensagens e baixa as fotos)" @click="registrando = c">
            registrar atendimento
          </button>
        </li>
      </ul>
    </header>

    <!-- mensagens -->
    <div ref="rolagem" class="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain bg-muted/40 px-3 py-3 dark:bg-muted/20">
      <div v-if="carregando && !historico" class="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> lendo as conversas…</div>
      <p v-else-if="erro" role="alert" class="flex items-center justify-center gap-2 py-10 text-sm text-destructive"><AlertCircle class="size-4" /> {{ erro }}</p>
      <template v-else-if="historico">
        <div v-if="historico.cortadas" class="flex justify-center">
          <span class="rounded-full bg-background px-2.5 py-0.5 text-[11px] text-muted-foreground shadow-sm">mostrando as mensagens mais recentes — as mais antigas estão no Atendimento</span>
        </div>
        <div v-if="historico.restrito" class="mx-auto max-w-md rounded-lg border bg-background px-4 py-3 text-center text-sm text-muted-foreground">
          As conversas desta garantia ficam no Atendimento, e você não tem acesso a elas.
        </div>
        <div v-else-if="!historico.mensagens.length" class="mx-auto max-w-md space-y-1 py-8 text-center text-sm text-muted-foreground" data-sem-conversa>
          <MessageSquare class="mx-auto size-6 opacity-60" />
          <p>Nenhuma conversa do cliente sobre este pedido ainda.</p>
          <p v-if="automatico" class="text-xs">Quando o cliente escrever (Shopee, TikTok, Mercado Livre, Amazon), a conversa aparece aqui sozinha.</p>
        </div>
        <template v-for="l in linhas" :key="l.chave">
          <div v-if="l.tipo === 'dia'" class="flex justify-center py-1">
            <span class="rounded-full bg-background px-2.5 py-0.5 text-[10px] font-medium text-muted-foreground shadow-sm">{{ l.texto }}</span>
          </div>
          <div v-else-if="l.tipo === 'conversa'" class="flex items-center gap-2 py-0.5 text-[11px] text-muted-foreground">
            <span class="h-px flex-1 bg-border" aria-hidden="true" />
            <span class="max-w-[75%] truncate">{{ rotuloConversa(l.conversa) }}</span>
            <span class="h-px flex-1 bg-border" aria-hidden="true" />
          </div>
          <div v-else-if="l.tipo === 'marco'" class="flex justify-center" :data-marco="l.marco.tipo">
            <div class="flex max-w-[90%] items-start gap-1.5 rounded-md border px-2.5 py-1 text-[11px]" :class="MARCO_CLS[l.marco.tipo]">
              <ShieldCheck v-if="l.marco.tipo === 'atendimento' || l.marco.tipo === 'entrega'" class="mt-px size-3.5 shrink-0" />
              <span class="break-words">{{ l.marco.texto }}<span v-if="!l.marco.sem_hora" class="opacity-70"> · {{ hora(l.marco.em) }}</span></span>
            </div>
          </div>
          <div v-else-if="ladoDaMensagem(l.m) === 'nota'" class="flex justify-center">
            <div class="max-w-[85%] whitespace-pre-wrap break-words rounded-md border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-xs text-amber-900 dark:border-amber-800/60 dark:bg-amber-900/25 dark:text-amber-200">
              <span class="font-medium">Nota interna:</span> {{ l.m.texto }}
            </div>
          </div>
          <!-- SAC do site: o e-mail do formulário inteiro, num cartão -->
          <div v-else-if="protocoloDa(l.m) && ladoDaMensagem(l.m) === 'sistema'" class="flex justify-center" data-sac-formulario>
            <div class="w-full max-w-[90%] rounded-lg border border-amber-300/70 bg-amber-50/60 px-3 py-2 text-sm dark:border-amber-800/60 dark:bg-amber-900/20 md:max-w-[80%]">
              <div class="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span class="font-semibold text-amber-900 dark:text-amber-200">📋 Formulário do SAC do site · protocolo {{ protocoloDa(l.m) }}</span>
                <span class="text-muted-foreground">{{ hora(l.m.enviada_em) }}</span>
                <a :href="linkDa(l.m)" target="_blank" rel="noopener" class="ml-auto inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-xs hover:bg-muted" title="abre o e-mail no Atendimento, com os links das fotos e do vídeo (válidos por 30 dias)">
                  <ExternalLink class="size-3" /> ver o e-mail completo (fotos e vídeo)
                </a>
              </div>
              <div class="whitespace-pre-wrap break-words text-[13px] leading-relaxed" :class="sacAberto.has(l.m.id) ? '' : 'max-h-64 overflow-hidden'">{{ formularioSacLegivel(l.m.texto) }}</div>
              <button type="button" class="mt-1 text-xs text-primary underline-offset-2 hover:underline" @click="alternarSac(l.m.id)">{{ sacAberto.has(l.m.id) ? 'mostrar menos' : 'ler o formulário inteiro' }}</button>
            </div>
          </div>
          <div v-else-if="ladoDaMensagem(l.m) === 'sistema'" class="flex justify-center">
            <div class="max-w-[85%] whitespace-pre-wrap break-words px-3 py-0.5 text-center text-[11px] text-muted-foreground">{{ l.m.texto || (pecasDoHistorico(l.m)[0]?.nome ?? '') }}</div>
          </div>
          <div v-else class="flex flex-col" :class="ladoDaMensagem(l.m) === 'loja' ? 'items-end' : 'items-start'" :data-msg-id="l.m.id">
            <div class="mb-0.5 px-1 text-[11px] text-muted-foreground">{{ rotuloAutor(l.m) }}</div>
            <div class="max-w-[85%] rounded-lg px-3 py-2 md:max-w-[75%]" :class="balaoCls(l.m)">
              <div v-if="l.m.status && STATUS_MSG[l.m.status]" class="mb-1">
                <span class="inline-flex items-center rounded px-1.5 py-px text-[11px]" :class="STATUS_MSG[l.m.status].cls" :title="STATUS_MSG[l.m.status].hint" data-status-msg>{{ STATUS_MSG[l.m.status].label }}</span>
              </div>
              <div v-if="l.m.texto" class="whitespace-pre-wrap break-words text-sm leading-relaxed"><template v-for="(p, i) in partes(l.m.texto)" :key="i"><a v-if="p.url" :href="p.v" target="_blank" rel="noopener noreferrer" class="break-all underline">{{ p.v }}</a><template v-else>{{ p.v }}</template></template></div>
              <div v-if="pecasDoHistorico(l.m).length" class="flex flex-wrap gap-2" :class="l.m.texto ? 'mt-2' : ''">
                <template v-for="pc in pecasDoHistorico(l.m)" :key="pc.chave">
                  <template v-if="pc.tipo === 'imagem'">
                    <button v-if="!fotoFalhou.has(pc.chave)" type="button" class="overflow-hidden rounded-md border bg-background" :title="`abrir ${pc.nome}`" @click="imagemAberta = { url: pc.url, nome: pc.nome }">
                      <img :src="pc.url" :alt="pc.nome" loading="lazy" referrerpolicy="no-referrer" class="size-32 object-cover" @error="falhouFoto(pc.chave)" />
                    </button>
                    <a v-else :href="pc.url" target="_blank" rel="noopener noreferrer" class="rounded border border-dashed bg-background px-2 py-0.5 text-xs text-muted-foreground underline" title="a plataforma não mostrou a foto (o link pode ter expirado)">foto (abrir link)</a>
                  </template>
                  <template v-else-if="pc.tipo === 'video'">
                    <video v-if="!videoFalhou.has(pc.chave)" :src="pc.url" controls preload="metadata" playsinline referrerpolicy="no-referrer" class="max-h-72 max-w-full rounded-md border bg-black" data-video @error="falhouVideo(pc.chave)" />
                    <a v-else :href="pc.url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 rounded border border-dashed bg-background px-2 py-0.5 text-xs text-muted-foreground underline" title="o vídeo não tocou aqui (o link pode ter expirado)"><Video class="size-3.5" /> vídeo (abrir link)</a>
                  </template>
                  <span v-else-if="pc.tipo === 'cartao'" class="rounded border bg-background px-2 py-0.5 text-xs text-muted-foreground">{{ pc.nome }}</span>
                  <a v-else-if="pc.tipo === 'link'" :href="pc.url" target="_blank" rel="noopener noreferrer" class="max-w-[220px] truncate rounded border bg-background px-2 py-0.5 text-xs underline">{{ pc.nome }}</a>
                  <span v-else class="rounded border border-dashed bg-background px-2 py-0.5 text-xs text-muted-foreground" title="a plataforma não manda o link do arquivo (Mercado Livre): veja na própria conversa">{{ pc.nome }} (só na plataforma)</span>
                </template>
              </div>
              <div v-if="!l.m.texto && !pecasDoHistorico(l.m).length" class="text-sm italic text-muted-foreground">[{{ l.m.tipo }}]</div>
            </div>
            <div class="mt-0.5 px-1 text-[10px] text-muted-foreground">{{ hora(l.m.enviada_em) }}</div>
          </div>
        </template>
      </template>
    </div>

    <!-- foto grande -->
    <div v-if="imagemAberta" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" role="dialog" aria-label="foto" @click.self="imagemAberta = null">
      <img :src="imagemAberta.url" :alt="imagemAberta.nome" referrerpolicy="no-referrer" class="max-h-full max-w-full rounded-md object-contain" />
    </div>

    <GarantiaVincular
      v-if="registrando && conversaDoModal"
      :conversa="conversaDoModal"
      :mensagens="mensagensDaConversa"
      :situacao="situacaoDoModal"
      :ids-padrao="idsPadrao"
      @fechar="registrando = null"
      @vinculado="aoRegistrar"
    />
  </section>
</template>
