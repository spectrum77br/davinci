<script setup lang="ts">
// Detalhe de uma nota (gaveta à direita), montado UMA vez pela página e aberto
// por tela.abrirNota(e | id). De cima para baixo: o que aconteceu (com a ação
// certa para a situação), resumo, arquivos, avisos da NFE.io, linha do tempo
// (com os pedidos de cancelamento) e os detalhes técnicos fechados. Atualizar
// da NFE.io, reenviar, cancelar e enviar por e-mail passam pela tela
// (confirmação e lote são as janelas de sempre) e a gaveta passa a mostrar a
// nota que voltou.
//
// 29/09/2026 (motor NFE.io): saem série/nº e identificador da DPS, perfil de
// assinatura e o XML enviado; entram o nº da nota, o código de verificação, o
// IR retido e o envio por e-mail. 30/09/2026: "Enviar por e-mail" é manual, pelo
// DaVinci (janela com o endereço); a NFE.io não manda nada ao tomador sozinha.
import { computed, ref } from 'vue'
import {
  Activity, AlertTriangle, Ban, Building2, ChevronRight, Copy, FileCode2, FileDown, FlaskConical, History, Loader2, Mail,
  Paperclip, ReceiptText, RefreshCw, Repeat, RotateCw, UserRound,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  ambienteTexto, erroApi, fmtBrl, fmtDataHora, fmtDoc, fmtMes, fmtPctOrigem, mesBaseDaEmissao, mesParaData,
  MOTIVOS_CANCELAMENTO, origemDaEmissao, situacao, TEXTO_TESTE, textoMesBase, useNfseTela,
  type Emissao, type EventoNota, type Msg, type NotaApi, type Tom,
  useNfseApi,
} from '~/lib/nfse'

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api, abrirPdf, baixarXml } = useNfseApi()
const toasts = useToasts()

const aberto = ref(false)
const nota = ref<Emissao | null>(null)
const eventos = ref<EventoNota[]>([])
const eventosCarregados = ref(false)
const carregandoEventos = ref(false)
const erroEventos = ref<string | null>(null)
const acao = ref<null | 'conferir' | 'reenviar' | 'cancelar' | 'email'>(null)

let resolver: (() => void) | null = null
let seqAbrir = 0
let seqEventos = 0

// --- Abrir / fechar --------------------------------------------------------------

// Com o id (link ?nota=…): procura na lista (vêm as 500 mais recentes, de
// teste e de produção). Não achou lá: pergunta à NFE.io por ela.
async function buscar(id: string): Promise<Emissao | null> {
  try {
    const lista = await api<Emissao[]>('/api/nfse/emissoes')
    const achou = lista.find((x) => x.id === id)
    if (achou || lista.length < 500) return achou ?? null
  } catch {
    // tenta pelo atualizar
  }
  const r = await tela.atualizarEmissoes([id])
  return r?.find((x) => x.id === id) ?? null
}

function terminar() {
  const r = resolver
  resolver = null
  r?.()
}

async function abrir(e: Emissao | string): Promise<void> {
  // Outra nota já aberta: ela "fecha" (a promise dela resolve) e abre esta.
  terminar()
  const meu = ++seqAbrir
  const alvo = typeof e === 'string' ? await buscar(e) : e
  if (meu !== seqAbrir) return
  if (!alvo) {
    toasts.error('Nota não encontrada', 'Ela pode ter sido apagada, ou o link está incompleto.')
    return
  }
  nota.value = alvo
  eventos.value = []
  eventosCarregados.value = false
  erroEventos.value = null
  acao.value = null
  aberto.value = true
  carregarEventos()
  return new Promise<void>((res) => {
    resolver = res
  })
}

function fechar() {
  aberto.value = false
  terminar()
}

function mudarAberto(v: boolean) {
  if (!v) fechar()
}

const exposto: NotaApi = { abrir }
defineExpose(exposto)

// --- Dados -----------------------------------------------------------------------

async function carregarEventos() {
  const n = nota.value
  if (!n) return
  const meu = ++seqEventos
  carregandoEventos.value = true
  erroEventos.value = null
  try {
    const r = await api<EventoNota[]>(`/api/nfse/emissoes/${n.id}/eventos`)
    if (meu !== seqEventos) return
    eventos.value = r
    eventosCarregados.value = true
  } catch (e) {
    if (meu === seqEventos) erroEventos.value = erroApi(e)
  } finally {
    if (meu === seqEventos) carregandoEventos.value = false
  }
}

// Relê esta nota do servidor (quando a ação não devolveu a nota nova, por
// exemplo um reenvio que não chegou a sair).
async function releNota() {
  const n = nota.value
  if (!n) return
  try {
    const q = new URLSearchParams({
      competencia: mesParaData(n.competencia),
      company_id: n.company_id,
    })
    const lista = await api<Emissao[]>(`/api/nfse/emissoes?${q}`)
    const nova = lista.find((x) => x.id === n.id)
    if (nova && nota.value?.id === n.id) nota.value = nova
  } catch {
    // fica a que já está na tela
  }
}

async function email() {
  const n = nota.value
  if (!n || acao.value) return
  acao.value = 'email'
  try {
    await tela.enviarEmail(n)
  } finally {
    acao.value = null
  }
}

async function rodar(tipo: 'conferir' | 'reenviar' | 'cancelar') {
  const n = nota.value
  if (!n || acao.value) return
  acao.value = tipo
  try {
    const fn = tipo === 'conferir' ? tela.conferir : tipo === 'reenviar' ? tela.reenviar : tela.cancelar
    const nova = await fn(n)
    if (nota.value?.id !== n.id) return
    if (nova) nota.value = nova
    else if (tipo === 'reenviar') await releNota()
    if (nova || tipo === 'reenviar') await carregarEventos()
  } finally {
    acao.value = null
  }
}

// --- Apresentação ------------------------------------------------------------------

const n = computed(() => nota.value)
const info = computed(() => situacao(nota.value?.status ?? 'emitida'))
const snap = computed<Record<string, any>>(() => nota.value?.snapshot ?? {})

const titulo = computed(() => {
  const x = nota.value
  if (!x) return 'Nota'
  return x.n_nfse ? `Nota nº ${x.n_nfse}` : 'Nota ainda sem número'
})

function capitalizar(s: string): string {
  return s ? s[0]!.toUpperCase() + s.slice(1) : s
}

const subtitulo = computed(() => {
  const x = nota.value
  if (!x) return ''
  return `${x.prestador_nome || 'Empresa'} → ${x.tomador_nome || 'tomador'} · ${fmtMes(x.competencia)}`
})

// '03/10/2026 14:31' → '03/10/2026 às 14:31'
function quando(iso: string | null | undefined): string {
  const t = fmtDataHora(iso)
  return t === '—' ? t : t.replace(' ', ' às ')
}
function diaLocal(iso: string | null | undefined): string {
  return fmtDataHora(iso).slice(0, 10)
}
function pct(v: string | null | undefined): string {
  const x = Number(v)
  return Number.isFinite(x) ? `${x.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%` : ''
}

// A nota autorizada (e o PDF e o XML dela, gerados pela NFE.io) só existe
// nestas situações.
const autorizada = computed(() => ['emitida', 'cancelando', 'cancelada'].includes(nota.value?.status ?? ''))

// PDF e XML descem com a chave da senha extra (link direto seria recusado).
function verPdf() {
  if (nota.value) void abrirPdf(nota.value.id)
}
function verXml() {
  if (nota.value) void baixarXml(nota.value.id)
}
// PDF e XML vêm da NFE.io depois que a prefeitura autoriza. "PDF pendente na
// NFE.io" (alerta) = a nota saiu, mas o PDF ainda não: o link pode dar erro.
const temPdf = autorizada
const temXmlNota = autorizada

const textoOcorrido = computed(() => {
  const x = nota.value
  if (!x) return ''
  if (x.status === 'emitida') return `Emitida e autorizada pela prefeitura em ${quando(x.dh_proc || x.dh_emi)}.`
  if (x.status === 'cancelada') {
    return `Nota cancelada${x.cancelada_em ? ` em ${diaLocal(x.cancelada_em)}` : ''}. Ela não vale mais.`
  }
  return info.value.explicacao
})

// Ações da situação (conferir e reenviar só com permissão de edição). PDF e
// XML ficam só em "Arquivos" (não repetem na caixa "O que aconteceu"). Na
// recusada, os botões vêm DEPOIS do motivo e de "Onde corrigir": primeiro ler
// e corrigir, depois reenviar.
const acoes = computed(() => {
  const x = nota.value
  const s = x?.status
  const edit = tela.canEdit.value
  const conferir =
    !edit || !s
      ? null
      : s === 'incerta' || s === 'enviando' || s === 'processando' || s === 'rejeitada'
        ? 'Atualizar da NFE.io'
        : s === 'cancelando'
          ? 'Atualizar cancelamento'
          : null
  return {
    reenviar: edit && s === 'rejeitada',
    conferir,
  }
})
const recusada = computed(() => nota.value?.status === 'rejeitada')

// Falha de conexão guardada na nota sem resposta (ex.: "tempo esgotado").
const detalheConexao = computed(() => {
  const x = nota.value
  if (!x || (x.status !== 'incerta' && x.status !== 'enviando')) return ''
  return (x.erros ?? []).map((m) => m.descricao || m.o_que_fazer || '').filter(Boolean).join(' · ')
})

// Nota de percentual (29/09): o valor saiu de "0,5% de R$ 200.000,00".
const deOnde = computed(() => {
  const x = nota.value
  if (!x) return ''
  const p = x.percentual ?? x.snapshot?.servico?.percentual ?? null
  const base = x.base_calculo ?? x.snapshot?.servico?.base_calculo ?? null
  return p != null && base != null ? `${fmtPctOrigem(p, origemDaEmissao(x))} de ${fmtBrl(base)}` : ''
})

// 01/10/2026: a base veio do faturamento de outro mês (ex.: setembro numa nota de
// outubro) → "faturamento de setembro/2026". Mesmo mês da nota: nada.
const mesBase = computed(() =>
  textoMesBase(mesBaseDaEmissao(nota.value), nota.value?.snapshot?.servico?.base_origem),
)

const tomador = computed(() => tela.tomadores.value.find((t) => t.id === nota.value?.tomador_id) ?? null)
const modelo = computed(() => tela.modelos.value.find((m) => m.id === nota.value?.modelo_id) ?? null)

const contexto = computed(() => ({
  companyId: nota.value?.company_id ?? null,
  empresa: nota.value?.prestador_nome ?? null,
  tomadorId: nota.value?.tomador_id ?? null,
  modeloId: nota.value?.modelo_id ?? null,
  modeloTemCodigos: !!(modelo.value?.city_service_code || modelo.value?.federal_service_code || modelo.value?.c_nbs),
}))

// Linha do tempo -------------------------------------------------------------------

type Passo = {
  chave: string
  titulo: string
  quando: string | null
  tom: Tom
  detalhe?: string
  resultado?: { texto: string; tom: Tom }
  erros?: Msg[] | null
}

const PONTO: Record<Tom, string> = {
  sucesso: 'bg-emerald-500 dark:bg-emerald-400',
  atencao: 'bg-amber-500 dark:bg-amber-400',
  perigo: 'bg-red-500 dark:bg-red-400',
  info: 'bg-primary',
  neutro: 'bg-muted-foreground/50',
}
const TEXTO_TOM: Record<Tom, string> = {
  sucesso: 'text-emerald-700 dark:text-emerald-400',
  atencao: 'text-amber-700 dark:text-amber-400',
  perigo: 'text-red-700 dark:text-red-400',
  info: 'text-primary',
  neutro: 'text-muted-foreground',
}

function resultadoEvento(s: string): { texto: string; tom: Tom } {
  if (s === 'registrado' || s === 'aceito') return { texto: 'Cancelamento aceito pela prefeitura', tom: 'sucesso' }
  if (s === 'rejeitado') return { texto: 'Cancelamento recusado', tom: 'perigo' }
  if (s === 'incerto' || s === 'pendente' || s === 'processando') {
    return { texto: 'A prefeitura ainda não confirmou: clique em Atualizar cancelamento', tom: 'atencao' }
  }
  return { texto: 'Enviando o pedido…', tom: 'info' }
}

const passos = computed<Passo[]>(() => {
  const x = nota.value
  if (!x) return []
  const out: Passo[] = [{ chave: 'criada', titulo: 'Criada', quando: x.created_at, tom: 'neutro' }]
  if (x.enviado_em) {
    out.push({
      chave: 'enviada',
      titulo: `Enviada à NFE.io${x.tentativas > 1 ? ` (${x.tentativas} tentativas)` : ''}`,
      quando: x.enviado_em,
      tom: 'info',
    })
  }
  if (autorizada.value) {
    out.push({
      chave: 'autorizada',
      titulo: x.n_nfse ? `Autorizada pela prefeitura: nº ${x.n_nfse}` : 'Autorizada pela prefeitura',
      quando: x.dh_proc || x.dh_emi,
      tom: 'sucesso',
    })
  } else if (x.status === 'rejeitada') {
    const m = x.erros?.[0]
    out.push({
      chave: 'recusada',
      titulo: 'Recusada',
      quando: x.dh_proc || x.enviado_em,
      tom: 'perigo',
      detalhe: m?.o_que_fazer || m?.descricao || x.flow_message || undefined,
    })
  } else if (x.status === 'processando') {
    out.push({ chave: 'processando', titulo: 'Na prefeitura: esperando a autorização', quando: null, tom: 'info' })
  } else if (x.status === 'incerta') {
    out.push({ chave: 'incerta', titulo: 'Sem resposta da NFE.io', quando: null, tom: 'atencao' })
  } else if (x.status === 'enviando') {
    out.push({ chave: 'enviando', titulo: 'Esperando a resposta da NFE.io', quando: null, tom: 'info' })
  }
  for (const ev of eventos.value) {
    const motivo = MOTIVOS_CANCELAMENTO[ev.c_motivo as 1 | 2 | 9]
    const cancelamento = ev.tipo_evento === '101101' || /cancel/i.test(ev.tipo_evento)
    out.push({
      chave: `ev-${ev.id}`,
      titulo: cancelamento
        ? `Cancelamento pedido — ${motivo?.titulo ?? `motivo ${ev.c_motivo ?? '?'}`}`
        : `Evento ${ev.tipo_evento}`,
      quando: ev.created_at,
      tom: 'neutro',
      detalhe: ev.x_motivo ? `“${ev.x_motivo}”` : undefined,
      resultado: resultadoEvento(ev.status),
      erros: ev.status === 'rejeitado' ? ev.erros : null,
    })
  }
  if (x.cancelada_em) out.push({ chave: 'cancelada', titulo: 'Cancelada', quando: x.cancelada_em, tom: 'neutro' })
  return out
})

// Detalhes técnicos -------------------------------------------------------------------

async function copiarChave() {
  const c = nota.value?.chave_acesso
  if (!c) return
  try {
    await navigator.clipboard.writeText(c)
    toasts.success('Chave copiada')
  } catch {
    toasts.error('Não deu para copiar', 'Selecione a chave e copie com Ctrl+C.')
  }
}

// Códigos que foram na nota (retrato gravado na emissão).
const codigos = computed(() => {
  const s = snap.value?.servico ?? {}
  return [
    s.city_service_code && `serviço ${s.city_service_code}`,
    s.federal_service_code && `LC 116 ${s.federal_service_code}`,
    s.c_nbs && `NBS ${s.c_nbs}`,
  ]
    .filter(Boolean)
    .join(' · ')
})

const recibo = computed(() => {
  const x = nota.value
  if (!x || (x.rps_numero == null && !x.rps_serie)) return ''
  return [x.rps_serie && `série ${x.rps_serie}`, x.rps_numero != null && `nº ${x.rps_numero}`].filter(Boolean).join(' · ')
})
</script>

<template>
  <NfseSheet :open="aberto" largura="md" :titulo="titulo" :subtitulo="subtitulo" @update:open="mudarAberto">
    <template #cabecalho-extra>
      <NfseSituacao v-if="n" :estado="n.status" :teste="n.teste" />
    </template>

    <div v-if="n" class="space-y-6">
      <!-- 1. O que aconteceu -->
      <NfseSecao titulo="O que aconteceu" :icone="Activity">
        <NfseAviso v-if="n.teste" tom="atencao" :icone="FlaskConical" compacto>
          <span class="font-medium">{{ TEXTO_TESTE }}</span>
        </NfseAviso>
        <NfseAviso :tom="info.tom" :icone="info.icone">
          <p>{{ textoOcorrido }}</p>
          <p v-if="detalheConexao" class="text-xs opacity-80">Detalhe da conexão: {{ detalheConexao }}</p>
          <!-- na prefeitura, sem resposta, enviando ou cancelando: atualizar é a ação -->
          <template v-if="acoes.conferir && !recusada" #acoes>
            <Button size="sm" :disabled="!!acao" @click="rodar('conferir')">
              <Loader2 v-if="acao === 'conferir'" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
              <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
              {{ acoes.conferir }}
            </Button>
          </template>
        </NfseAviso>

        <!-- recusada: 1º o motivo (com o botão que leva ao conserto), 2º onde
             corrigir, 3º reenviar ou atualizar da NFE.io -->
        <template v-if="recusada">
          <NfseProblemas :erros="n.erros" :contexto="contexto" />

          <div
            v-if="tela.canEdit.value"
            class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"
          >
            <span>Onde corrigir:</span>
            <Button variant="link" class="h-auto p-0 text-xs" @click="tela.abrirEmpresa(n.company_id)">
              <Building2 class="mr-1 size-3.5" aria-hidden="true" /> empresa {{ n.prestador_nome || '' }}
            </Button>
            <Button v-if="tomador" variant="link" class="h-auto p-0 text-xs" @click="tela.abrirTomador({ tomador })">
              <UserRound class="mr-1 size-3.5" aria-hidden="true" /> tomador
            </Button>
            <Button v-if="modelo" variant="link" class="h-auto p-0 text-xs" @click="tela.abrirModelo({ modelo })">
              <Repeat class="mr-1 size-3.5" aria-hidden="true" /> nota fixa
            </Button>
          </div>

          <div v-if="acoes.reenviar || acoes.conferir" class="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button v-if="acoes.reenviar" size="sm" :disabled="!!acao" @click="rodar('reenviar')">
              <Loader2 v-if="acao === 'reenviar'" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
              <RotateCw v-else class="mr-1.5 size-4" aria-hidden="true" />
              Reenviar
            </Button>
            <Button
              v-if="acoes.conferir"
              size="sm"
              variant="outline"
              :disabled="!!acao"
              @click="rodar('conferir')"
            >
              <Loader2 v-if="acao === 'conferir'" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
              <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
              {{ acoes.conferir }}
            </Button>
          </div>
        </template>
      </NfseSecao>

      <!-- 2. Resumo -->
      <NfseSecao titulo="Resumo da nota" :icone="ReceiptText">
        <dl class="grid grid-cols-[130px_1fr] gap-x-4 gap-y-2.5 text-sm sm:grid-cols-[150px_1fr]">
          <template v-if="n.n_nfse">
            <dt class="text-muted-foreground">Nº da nota</dt>
            <dd class="font-medium tabular-nums">{{ n.n_nfse }}</dd>
          </template>

          <template v-if="n.check_code">
            <dt class="text-muted-foreground">Código de verificação</dt>
            <dd class="break-all font-mono text-xs">{{ n.check_code }}</dd>
          </template>

          <dt class="text-muted-foreground">Empresa que emite</dt>
          <dd class="min-w-0">
            <div class="font-medium">{{ n.prestador_nome || snap.prestador?.nome || '—' }}</div>
            <div v-if="snap.prestador?.cnpj" class="text-xs tabular-nums text-muted-foreground">
              <template v-if="snap.prestador?.nome && snap.prestador.nome !== n.prestador_nome">
                {{ snap.prestador.nome }} ·
              </template>
              {{ fmtDoc(snap.prestador.cnpj) }}
            </div>
          </dd>

          <dt class="text-muted-foreground">Tomador</dt>
          <dd class="min-w-0">
            <div class="font-medium">{{ n.tomador_nome || snap.tomador?.nome || '—' }}</div>
            <div v-if="snap.tomador?.documento" class="text-xs tabular-nums text-muted-foreground">
              {{ fmtDoc(snap.tomador.documento) }}
            </div>
          </dd>

          <dt class="text-muted-foreground">Mês de competência</dt>
          <dd>{{ capitalizar(fmtMes(n.competencia)) }}</dd>

          <dt class="text-muted-foreground">Valor do serviço</dt>
          <dd class="min-w-0">
            <div
              class="text-base font-semibold tabular-nums"
              :class="n.status === 'cancelada' && 'text-muted-foreground line-through decoration-muted-foreground/60'"
            >
              {{ fmtBrl(n.valor_servico) }}
            </div>
            <div v-if="deOnde" class="text-xs tabular-nums text-muted-foreground" title="percentual sobre a base de cálculo">
              {{ deOnde }}
            </div>
            <div v-if="deOnde && mesBase" class="text-xs text-muted-foreground">{{ mesBase }}</div>
          </dd>

          <template v-if="n.v_issqn">
            <dt class="text-muted-foreground">ISS</dt>
            <dd class="tabular-nums">
              {{ fmtBrl(n.v_issqn) }}
              <span v-if="pct(n.p_aliq_aplic)" class="text-muted-foreground">({{ pct(n.p_aliq_aplic) }})</span>
            </dd>
          </template>

          <template v-if="n.ir_retido != null && Number(n.ir_retido) > 0">
            <dt class="text-muted-foreground">IR retido</dt>
            <dd class="tabular-nums">{{ fmtBrl(n.ir_retido) }} <span class="text-muted-foreground">(1,5%)</span></dd>
          </template>

          <template v-if="n.v_liq">
            <dt class="text-muted-foreground">Valor líquido</dt>
            <dd class="tabular-nums">{{ fmtBrl(n.v_liq) }}</dd>
          </template>

          <dt class="text-muted-foreground">Emitida em</dt>
          <dd class="tabular-nums">{{ autorizada && n.dh_emi ? quando(n.dh_emi) : '—' }}</dd>

          <dt class="text-muted-foreground">Origem</dt>
          <dd>
            <template v-if="n.modelo_id">
              Nota fixa<template v-if="modelo">: <span class="font-medium">{{ modelo.nome }}</span></template>
            </template>
            <template v-else>Nota avulsa</template>
          </dd>

          <dt class="text-muted-foreground">Descrição</dt>
          <dd class="min-w-0 whitespace-pre-line break-words">{{ n.descricao || '—' }}</dd>
        </dl>
      </NfseSecao>

      <!-- 3. Arquivos -->
      <NfseSecao
        titulo="Arquivos"
        :icone="Paperclip"
        descricao="PDF e XML da nota autorizada, gerados pela NFE.io."
      >
        <div v-if="temPdf || temXmlNota" class="flex flex-wrap gap-2">
          <Button v-if="temPdf" size="sm" variant="outline" @click="verPdf">
            <FileDown class="mr-1.5 size-4" aria-hidden="true" /> Baixar PDF
          </Button>
          <Button v-if="temXmlNota" size="sm" variant="outline" @click="verXml">
            <FileCode2 class="mr-1.5 size-4" aria-hidden="true" /> XML da nota
          </Button>
          <Button
            v-if="n.status === 'emitida' && tela.canEdit.value"
            size="sm"
            variant="outline"
            :disabled="!!acao"
            @click="email"
          >
            <Loader2 v-if="acao === 'email'" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
            <Mail v-else class="mr-1.5 size-4" aria-hidden="true" />
            Enviar por e-mail
          </Button>
        </div>
        <p v-else class="text-sm text-muted-foreground">O PDF e o XML aparecem aqui quando a prefeitura autorizar a nota.</p>
      </NfseSecao>

      <!-- 4. Avisos (não impediram a nota) -->
      <NfseSecao
        v-if="n.alertas?.length"
        titulo="Avisos da NFE.io"
        :icone="AlertTriangle"
        descricao="Não impediram a nota, mas vale conferir."
      >
        <!-- um por vez: no modo compacto o bloco não repete o título da seção -->
        <div class="space-y-1.5">
          <NfseProblemas v-for="(a, i) in n.alertas" :key="i" compacto :alertas="[a]" :acoes="false" />
        </div>
      </NfseSecao>

      <!-- 5. Linha do tempo -->
      <NfseSecao titulo="Linha do tempo" :icone="History">
        <div v-if="carregandoEventos && !eventosCarregados" class="space-y-3 pl-4" aria-busy="true">
          <div v-for="i in 3" :key="i" class="space-y-1.5">
            <div class="h-3.5 w-2/3 animate-pulse rounded bg-muted motion-reduce:animate-none" />
            <div class="h-3 w-1/3 animate-pulse rounded bg-muted motion-reduce:animate-none" />
          </div>
        </div>
        <template v-else>
          <ol class="relative ml-1 space-y-4 border-l pl-5 text-sm">
            <li v-for="p in passos" :key="p.chave" class="relative">
              <span
                class="absolute -left-[26px] top-1.5 size-2.5 rounded-full ring-4 ring-background"
                :class="PONTO[p.tom]"
                aria-hidden="true"
              />
              <div class="font-medium leading-snug">{{ p.titulo }}</div>
              <div v-if="p.detalhe" class="mt-0.5 break-words text-muted-foreground">{{ p.detalhe }}</div>
              <div v-if="p.resultado" class="mt-0.5 text-xs font-medium" :class="TEXTO_TOM[p.resultado.tom]">
                {{ p.resultado.texto }}
              </div>
              <NfseProblemas v-if="p.erros?.length" class="mt-1.5" compacto :erros="p.erros" :acoes="false" />
              <div v-if="p.quando" class="mt-0.5 text-xs tabular-nums text-muted-foreground">{{ quando(p.quando) }}</div>
            </li>
          </ol>
          <p v-if="erroEventos" class="text-xs text-muted-foreground">
            Não carregou os pedidos de cancelamento ({{ erroEventos }})
            <Button variant="link" class="ml-1 h-auto p-0 text-xs" @click="carregarEventos">tentar de novo</Button>
          </p>
        </template>
      </NfseSecao>

      <!-- 6. Detalhes técnicos (fechado) -->
      <details class="group rounded-lg border">
        <summary
          class="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium hover:bg-muted/40 [&::-webkit-details-marker]:hidden"
        >
          <ChevronRight
            class="size-4 text-muted-foreground transition-transform group-open:rotate-90 motion-reduce:transition-none"
            aria-hidden="true"
          />
          Detalhes técnicos
          <span class="text-xs font-normal text-muted-foreground">para a contabilidade ou o suporte</span>
        </summary>
        <dl class="grid grid-cols-[130px_1fr] gap-x-4 gap-y-1.5 px-3 pb-3 pt-1 text-xs sm:grid-cols-[150px_1fr]">
          <template v-if="n.chave_acesso">
            <dt class="text-muted-foreground">Chave de acesso</dt>
            <dd class="flex min-w-0 items-start gap-1">
              <span class="break-all font-mono">{{ n.chave_acesso }}</span>
              <Button
                variant="ghost"
                size="icon"
                class="-my-1 size-7 shrink-0"
                aria-label="copiar a chave de acesso"
                @click="copiarChave"
              >
                <Copy class="size-3.5" />
              </Button>
            </dd>
          </template>

          <dt class="text-muted-foreground">Ambiente na NFE.io</dt>
          <dd>{{ n.nfeio_ambiente ? ambienteTexto(n.nfeio_ambiente) : n.teste ? 'Teste — nota simulada' : 'Produção' }}</dd>

          <template v-if="recibo">
            <dt class="text-muted-foreground">Recibo (RPS)</dt>
            <dd class="tabular-nums">{{ recibo }}</dd>
          </template>

          <dt class="text-muted-foreground">Tentativas de envio</dt>
          <dd class="tabular-nums">{{ n.tentativas }}</dd>

          <template v-if="n.flow_message">
            <dt class="text-muted-foreground">Última resposta</dt>
            <dd class="break-words">{{ n.flow_message }}</dd>
          </template>

          <dt class="text-muted-foreground">Processada em</dt>
          <dd class="tabular-nums">{{ quando(n.dh_proc) }}</dd>

          <template v-if="codigos">
            <dt class="text-muted-foreground">Códigos do serviço</dt>
            <dd class="font-mono">{{ codigos }}</dd>
          </template>
        </dl>
      </details>
    </div>

    <template #rodape>
      <div>
        <Button
          v-if="n?.status === 'emitida' && tela.canDelete.value"
          size="sm"
          variant="outline"
          class="border-red-500/30 text-red-600 hover:bg-red-500/10 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300"
          :disabled="!!acao"
          @click="rodar('cancelar')"
        >
          <Loader2 v-if="acao === 'cancelar'" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
          <Ban v-else class="mr-1.5 size-4" aria-hidden="true" />
          Cancelar nota…
        </Button>
      </div>
      <Button size="sm" @click="fechar">Fechar</Button>
    </template>
  </NfseSheet>
</template>
