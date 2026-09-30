<script setup lang="ts">
// NF Faturador › Emissão de Serviço: NFS-e de intermediação das empresas do
// grupo. Eduardo: tomador do grupo ou de fora, "todo mês, as mesmas" — por isso
// as notas fixas e o "Emitir do mês".
//
// 29/09/2026: o motor é a NFE.io (app que o grupo já usa, com as empresas
// cadastradas). A NFE.io assina, numera e fala com a prefeitura: sai a senha do
// certificado. Teste × produção é de cada empresa (o ambiente dela na NFE.io);
// a nota enviada fica "Na prefeitura" até a prefeitura autorizar.
//
// 28/09/2026 ("to achando simples e bagunçado"): a página virou a CASCA. Ela
// carrega os dados, guarda o estado na URL (?aba, ?mes, ?nota, ?empresa) e
// entrega tudo às abas por provide(NFSE_TELA) — as abas e as janelas não
// recebem props. As janelas (confirmação, emissão, nota avulsa,
// detalhe da nota, cancelamento, formulários) ficam montadas UMA vez aqui, e
// qualquer aba abre por cima sem trocar de lugar.
import { computed, onBeforeUnmount, onMounted, provide, ref, watch, type Ref } from 'vue'
import { onBeforeRouteLeave, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import { TooltipProvider } from 'reka-ui'
import {
  BookUser, Building2, CheckCircle2, FilePlus2, FileText, FlaskConical, Loader2, RotateCcw, Send, ShieldAlert,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { TABS_NF } from '~/lib/navGroups'
import {
  erroApi, fmtMes, itemReenvio, mesAtual, mesValido, NFSE_TELA, plural, prestadorPorId, STATUS_PARA_RESOLVER,
  type AbaId, type AbrirModeloOpts, type AbrirTomadorOpts, type AvulsaApi, type CancelarApi, type ConfirmApi,
  type ConfirmarOpts, type Emissao, type EmpresaApi, type ItemLote, type LoteApi, type Modelo, type ModeloApi,
  type NfseTela, type NotaApi, type Prestador, type ResultadoLote, type SecaoEmpresa, type StatusNfse, type Tomador,
  type TomadorApi,
} from '~/lib/nfse'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'emissao_servico', action: 'view' },
})

const route = useRoute()
const router = useRouter()
const { api } = useApi()
const toasts = useToasts()

const canEdit = useCan('emissao_servico', 'edit')
const canDelete = useCan('emissao_servico', 'delete')
const isAdmin = useIsAdmin()
const podeAbrirCadastroEmpresa = useCan('empresa', 'view')
const podeEditarCadastroEmpresa = useCan('empresa', 'edit')

// --- URL ---------------------------------------------------------------------

const ABAS: readonly AbaId[] = ['emitir', 'notas', 'cadastros', 'empresas']

function textoDaQuery(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}
function abaDaQuery(v: unknown): AbaId {
  const s = textoDaQuery(v)
  return s && (ABAS as readonly string[]).includes(s) ? (s as AbaId) : 'emitir'
}
// Competência futura é recusada pela prefeitura: o Emitir não passa do mês atual.
function mesDaQuery(v: unknown): string {
  return mesValido(v) && v <= mesAtual() ? v : mesAtual()
}

// router.replace sem sujar o histórico. Mudanças seguidas (ex.: aba + grupo)
// se somam em `pendente` até a navegação terminar.
let pendente: LocationQueryRaw | null = null
let navegando = 0

function mudarQuery(extra: Record<string, string | undefined>) {
  const q: LocationQueryRaw = { ...(pendente ?? (route.query as LocationQuery)) }
  for (const [k, v] of Object.entries(extra)) {
    if (v === undefined || v === '') delete q[k]
    else q[k] = v
  }
  pendente = q
  navegando++
  router.replace({ query: q }).finally(() => {
    navegando--
    if (!navegando) pendente = null
  })
}

// --- Estado ------------------------------------------------------------------

const status = ref<StatusNfse | null>(null)
const erroStatus = ref<string | null>(null)
const prestadores = ref<Prestador[]>([])
const tomadores = ref<Tomador[]>([])
const modelos = ref<Modelo[]>([])
const carregando = ref(false)
const carregado = ref(false)
const erroCarga = ref<string | null>(null)
const versao = ref(0)
const contadorNotas = ref(0)
const contadorEmitir = ref<number | null>(null)
const mes = ref(mesDaQuery(route.query.mes))
const aba = ref<AbaId>(abaDaQuery(route.query.aba))

// Janelas montadas uma vez (cada uma faz defineExpose com a sua API).
const confirmRef = ref<ConfirmApi | null>(null)
const loteRef = ref<LoteApi | null>(null)
const avulsaRef = ref<AvulsaApi | null>(null)
const notaRef = ref<NotaApi | null>(null)
const cancelarRef = ref<CancelarApi | null>(null)
const modeloRef = ref<ModeloApi | null>(null)
const tomadorRef = ref<TomadorApi | null>(null)
const empresaRef = ref<EmpresaApi | null>(null)

// A janela só vale se já expôs o método (enquanto um componente não existe,
// o Vue renderiza uma tag desconhecida no lugar e o ref aponta para ela).
// Nunca falha calado: sem a janela, a pessoa vê o aviso e o console registra.
function janela<T extends object>(r: Ref<T | null>, metodo: keyof T): T | null {
  const v = r.value as Record<PropertyKey, unknown> | null
  if (v && typeof v[metodo as PropertyKey] === 'function') return v as T
  console.warn(`[emissao-servico] janela sem o método "${String(metodo)}"`, v)
  toasts.error('Não deu para abrir esta janela', 'Atualize a página e tente de novo.')
  return null
}

// --- Carga -------------------------------------------------------------------

// Notas de teste e de produção juntas: cada uma diz de que ambiente é.
async function contarParaResolver(): Promise<number> {
  try {
    const listas = await Promise.all(
      STATUS_PARA_RESOLVER.map((s) => api<Emissao[]>(`/api/nfse/emissoes?status=${s}`)),
    )
    return listas.reduce((total, l) => total + l.length, 0)
  } catch {
    return contadorNotas.value
  }
}

// Lê a ligação do servidor com a NFE.io (chave e trava de produção). Falhou:
// ninguém emite até saber (erroStatus).
async function lerStatus(): Promise<StatusNfse | null> {
  try {
    const st = await api<StatusNfse>('/api/nfse/status')
    status.value = st
    erroStatus.value = null
    return st
  } catch (e) {
    status.value = null
    erroStatus.value = erroApi(e)
    return null
  }
}

// O ambiente é de cada empresa na NFE.io e pode mudar com a página aberta
// (alguém liga a produção lá). Antes de mandar notas, o assistente relê o
// status e as empresas; a tela acompanha sem recarregar tudo.
async function conferirEmpresas(): Promise<Prestador[] | null> {
  try {
    const [, ps] = await Promise.all([lerStatus(), api<Prestador[]>('/api/nfse/prestadores')])
    if (JSON.stringify(ps) !== JSON.stringify(prestadores.value)) {
      prestadores.value = ps
      versao.value++
    }
    return erroStatus.value ? null : ps
  } catch {
    return null
  }
}

async function carregarTudo(): Promise<void> {
  carregando.value = true
  try {
    const [ps, ts, ms, n] = await Promise.all([
      api<Prestador[]>('/api/nfse/prestadores'),
      api<Tomador[]>('/api/nfse/tomadores'),
      api<Modelo[]>('/api/nfse/modelos'),
      contarParaResolver(),
      lerStatus(),
    ])
    prestadores.value = ps
    tomadores.value = ts
    modelos.value = ms
    contadorNotas.value = n
    erroCarga.value = null
    versao.value++
    carregado.value = true
  } catch (e) {
    erroCarga.value = erroApi(e)
    toasts.error('Não carregou a Emissão de Serviço', erroApi(e))
  } finally {
    carregando.value = false
  }
}

// Várias chamadas seguidas viram no máximo uma carga em andamento + uma logo
// depois (que enxerga tudo o que mudou nesse meio-tempo).
let atual: Promise<void> | null = null
let proxima: Promise<void> | null = null

function recarregar(): Promise<void> {
  if (!atual) {
    atual = carregarTudo().finally(() => {
      atual = null
    })
    return atual
  }
  if (!proxima) {
    proxima = atual.then(() => {
      proxima = null
      return recarregar()
    })
  }
  return proxima
}

// Voltou para esta aba do navegador. Os links "Cadastros › Empresas" abrem a
// ficha da empresa em OUTRA aba (ex.: para cadastrar a % da empresa) e a tela lia as empresas uma vez só: ao voltar, a gaveta da
// nota fixa continuava "sem % padrão" e o Emitir mandava a % antiga. Relê as
// empresas e, se algo mudou, recarrega a tela (a prévia do Emitir refaz com a
// % nova; as gavetas abertas acompanham, sem fechar). Com notas saindo no
// assistente, não mexe em nada.
let relendoEmpresas = false

async function aoVoltarParaAba() {
  if (document.visibilityState !== 'visible' || !carregado.value || carregando.value || relendoEmpresas) return
  const lote = loteRef.value as Partial<LoteApi> | null
  if (lote && typeof lote.ocupado === 'function' && lote.ocupado()) return
  relendoEmpresas = true
  try {
    const ps = await api<Prestador[]>('/api/nfse/prestadores')
    if (JSON.stringify(ps) !== JSON.stringify(prestadores.value)) await recarregar()
  } catch {
    // Sem conexão agora: fica o que já está na tela ("atualizar" relê tudo).
  } finally {
    relendoEmpresas = false
  }
}

// --- Ações da tela -------------------------------------------------------------

function irPara(destino: AbaId, extra?: Record<string, string | undefined>) {
  aba.value = destino
  mudarQuery({ ...(extra ?? {}), aba: destino === 'emitir' ? undefined : destino })
}

async function confirmar(o: ConfirmarOpts): Promise<boolean> {
  const j = janela(confirmRef, 'perguntar')
  return j ? j.perguntar(o) : false
}

let empresaAberta: string | null = null
async function abrirEmpresa(companyId: string, foco?: SecaoEmpresa): Promise<boolean> {
  const j = janela(empresaRef, 'abrir')
  if (!j) return false
  empresaAberta = companyId
  mudarQuery({ empresa: companyId })
  try {
    return await j.abrir(companyId, foco)
  } finally {
    if (empresaAberta === companyId) {
      empresaAberta = null
      mudarQuery({ empresa: undefined })
    }
  }
}

let notaAberta: string | null = null
async function abrirNota(e: Emissao | string): Promise<void> {
  const j = janela(notaRef, 'abrir')
  if (!j) return
  const id = typeof e === 'string' ? e : e.id
  notaAberta = id
  mudarQuery({ nota: id })
  try {
    await j.abrir(e)
  } finally {
    if (notaAberta === id) {
      notaAberta = null
      mudarQuery({ nota: undefined })
    }
  }
}

async function abrirModelo(o?: AbrirModeloOpts): Promise<Modelo | null> {
  const j = janela(modeloRef, 'abrir')
  return j ? j.abrir(o) : null
}

async function abrirTomador(o?: AbrirTomadorOpts): Promise<Tomador | null> {
  const j = janela(tomadorRef, 'abrir')
  return j ? j.abrir(o) : null
}

async function abrirAvulsa(o?: { competencia?: string }): Promise<void> {
  const j = janela(avulsaRef, 'abrir')
  if (j) await j.abrir(o)
}

async function emitirLote(o: { competencia: string; itens: ItemLote[] }): Promise<ResultadoLote[]> {
  const j = janela(loteRef, 'emitir')
  if (!j || !o.itens.length) return []
  const res = await j.emitir(o)
  if (res.length) await recarregar()
  return res
}

async function reenviar(e: Emissao): Promise<Emissao | null> {
  const res = await emitirLote({ competencia: e.competencia.slice(0, 7), itens: [itemReenvio(e)] })
  return res[0]?.emissao ?? null
}

async function cancelar(e: Emissao): Promise<Emissao | null> {
  const j = janela(cancelarRef, 'cancelar')
  if (!j) return null
  const nova = await j.cancelar(e)
  if (nova) await recarregar()
  return nova
}

function avisarConferencia(antes: Emissao['status'], depois: Emissao) {
  const s = depois.status
  const motivo = depois.erros?.[0]?.o_que_fazer || depois.erros?.[0]?.descricao || depois.flow_message || undefined
  if (s === 'emitida' && antes === 'cancelando') {
    toasts.warning('O cancelamento não foi aceito', motivo ? [motivo, 'A nota continua emitida.'] : 'A nota continua emitida.')
  } else if (s === 'emitida') {
    toasts.success(depois.n_nfse ? `Nota nº ${depois.n_nfse} confirmada: está emitida` : 'Nota confirmada: está emitida')
  } else if (s === 'cancelada') {
    toasts.success('Cancelamento confirmado')
  } else if (s === 'processando' || s === 'enviando') {
    toasts.info(
      'Ainda na prefeitura',
      'A NFE.io está esperando a prefeitura autorizar. O DaVinci confere sozinho a cada 2 minutos; não reenvie.',
    )
  } else if (s === 'rejeitada' && antes !== 'rejeitada') {
    toasts.warning('A nota foi recusada', motivo ?? 'Veja o motivo, corrija e reenvie.')
  } else if (s === 'rejeitada') {
    toasts.warning('Continua recusada', motivo ?? 'Corrija o que foi apontado e reenvie.')
  } else if (s === 'cancelando') {
    toasts.info('O cancelamento ainda não voltou', 'A prefeitura não confirmou. Tente de novo em alguns minutos.')
  } else {
    toasts.warning('A NFE.io ainda não achou esta nota', 'Não reenvie. Tente atualizar de novo em alguns minutos.')
  }
}

// "Atualizar da NFE.io": relê a nota lá (não precisa de senha).
async function conferir(e: Emissao): Promise<Emissao | null> {
  try {
    const nova = await api<Emissao>(`/api/nfse/emissoes/${e.id}/conferir`, { method: 'POST' })
    avisarConferencia(e.status, nova)
    await recarregar()
    return nova
  } catch (err) {
    toasts.error('Não deu para atualizar da NFE.io', erroApi(err))
    return null
  }
}

// Várias de uma vez, sem aviso (o assistente de emissão acompanha as notas
// "Na prefeitura" a cada ~4 s). A API aceita até 200 ids por chamada.
async function atualizarEmissoes(ids: string[]): Promise<Emissao[] | null> {
  if (!ids.length) return []
  try {
    const partes: string[][] = []
    for (let i = 0; i < ids.length; i += 200) partes.push(ids.slice(i, i + 200))
    const rs = await Promise.all(
      partes.map((p) =>
        api<{ emissoes: Emissao[] }>('/api/nfse/emissoes/atualizar', { method: 'POST', body: { ids: p } }),
      ),
    )
    return rs.flatMap((r) => r.emissoes ?? [])
  } catch {
    return null
  }
}

// "Reenviar por e-mail": a NFE.io manda o PDF e o XML de novo para o e-mail do tomador.
async function enviarEmail(e: Emissao): Promise<boolean> {
  const ok = await confirmar({
    titulo: 'Reenviar a nota por e-mail?',
    texto: `A NFE.io manda o PDF e o XML${e.n_nfse ? ` da nota nº ${e.n_nfse}` : ''} de novo para o e-mail do tomador${e.tomador_nome ? ` (${e.tomador_nome})` : ''}.`,
    botao: 'Reenviar e-mail',
  })
  if (!ok) return false
  try {
    await api(`/api/nfse/emissoes/${e.id}/enviar-email`, { method: 'POST' })
    toasts.success('E-mail pedido à NFE.io', 'O tomador recebe a nota em alguns minutos.')
    return true
  } catch (err) {
    toasts.error('Não deu para reenviar o e-mail', erroApi(err))
    return false
  }
}

const tela: NfseTela = {
  status,
  erroStatus,
  prestadores,
  tomadores,
  modelos,
  carregado,
  carregando,
  versao,
  mes,
  contadorEmitir,
  canEdit,
  canDelete,
  isAdmin,
  podeAbrirCadastroEmpresa,
  podeEditarCadastroEmpresa,
  recarregar,
  conferirEmpresas,
  irPara,
  confirmar,
  abrirEmpresa,
  abrirModelo,
  abrirTomador,
  abrirNota,
  abrirAvulsa,
  emitirLote,
  conferir,
  atualizarEmissoes,
  reenviar,
  cancelar,
  enviarEmail,
}
provide(NFSE_TELA, tela)

// --- Abas ----------------------------------------------------------------------

// Empresas usadas em notas fixas ativas que ainda não podem emitir.
const contadorEmpresas = computed(() => {
  const ids = new Set(modelos.value.filter((m) => m.ativo).map((m) => m.company_id))
  let n = 0
  for (const id of ids) {
    const p = prestadorPorId(prestadores.value, id)
    if (p && !p.pronto) n++
  }
  return n
})

const opcoesAbas = computed(() => [
  {
    id: 'emitir',
    rotulo: 'Emitir do mês',
    icone: Send,
    contador: contadorEmitir.value,
    tomContador: 'neutro' as const,
    dica:
      contadorEmitir.value != null
        ? `${plural(contadorEmitir.value, 'nota pronta', 'notas prontas')} para emitir em ${fmtMes(mes.value)}`
        : undefined,
  },
  {
    id: 'notas',
    rotulo: 'Notas enviadas',
    icone: FileText,
    contador: contadorNotas.value,
    tomContador: 'atencao' as const,
    dica: `${plural(contadorNotas.value, 'nota', 'notas')} para resolver (recusadas, sem resposta ou na prefeitura)`,
  },
  { id: 'cadastros', rotulo: 'Notas fixas e tomadores', icone: BookUser },
  {
    id: 'empresas',
    rotulo: 'Empresas',
    icone: Building2,
    contador: contadorEmpresas.value,
    tomContador: 'atencao' as const,
    dica: `${plural(contadorEmpresas.value, 'empresa', 'empresas')} que ainda não podem emitir`,
  },
])

const abaModel = computed({
  get: () => aba.value as string,
  set: (v: string) => {
    aba.value = abaDaQuery(v)
  },
})

const textoErroStatus = computed(() => {
  const t = (erroStatus.value ?? '').trim()
  return t ? `${t}${/[.!?]$/.test(t) ? '' : '.'} Nada pode ser emitido até isso ser resolvido.` : ''
})

// Selo do cabeçalho: a ligação deste servidor com a NFE.io.
const seloServidor = computed(() => {
  if (erroStatus.value) {
    return { classe: 'pill-danger', icone: ShieldAlert, texto: 'Emissão travada', dica: erroStatus.value, girar: false }
  }
  const st = status.value
  if (!st) return { classe: 'pill-muted', icone: Loader2, texto: 'NFE.io…', dica: 'Conferindo a ligação com a NFE.io…', girar: true }
  if (!st.chave_configurada) {
    return {
      classe: 'pill-danger',
      icone: ShieldAlert,
      texto: 'NFE.io sem chave',
      dica: 'A chave de acesso da NFE.io não está configurada neste servidor: nada pode ser emitido.',
      girar: false,
    }
  }
  if (!st.producao_liberada) {
    return {
      classe: 'pill-warning',
      icone: FlaskConical,
      texto: 'Só teste',
      dica: 'Neste servidor a produção está travada: só empresas em Teste na NFE.io emitem (nota simulada, sem valor fiscal).',
      girar: false,
    }
  }
  return {
    classe: 'pill-success',
    icone: CheckCircle2,
    texto: 'NFE.io ligada',
    dica: 'Cada empresa emite no ambiente dela na NFE.io: Teste (nota simulada) ou Produção (nota de verdade).',
    girar: false,
  }
})

// --- URL ⇄ estado --------------------------------------------------------------

watch(aba, (a) => {
  mudarQuery({ aba: a === 'emitir' ? undefined : a, ...(a !== 'notas' ? { grupo: undefined } : {}) })
})

watch(mes, (m) => {
  mudarQuery({ mes: m === mesAtual() ? undefined : m })
})

function abrirDaQuery(q: LocationQuery) {
  const n = textoDaQuery(q.nota)
  if (n && n !== notaAberta) abrirNota(n)
  const emp = textoDaQuery(q.empresa)
  if (emp && emp !== empresaAberta) abrirEmpresa(emp)
}

// Link colado, voltar/avançar ou NuxtLink para ?aba=…: a tela acompanha. As
// mudanças feitas pela própria página (navegando > 0) não voltam para cá.
watch(
  () => route.query,
  (q) => {
    if (navegando > 0) return
    const a = abaDaQuery(q.aba)
    if (a !== aba.value) aba.value = a
    const m = mesDaQuery(q.mes)
    if (m !== mes.value) mes.value = m
    if (carregado.value) abrirDaQuery(q)
  },
)

onMounted(async () => {
  document.addEventListener('visibilitychange', aoVoltarParaAba)
  await recarregar()
  abrirDaQuery(route.query)
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', aoVoltarParaAba)
})

// Voltar do navegador (ou um link do menu) no meio da emissão desmontaria o
// assistente com as notas ainda saindo, sem mostrar o resultado. Segura aqui;
// fechar/recarregar a aba o próprio assistente segura (beforeunload).
onBeforeRouteLeave(() => {
  const lote = loteRef.value as Partial<LoteApi> | null
  if (lote && typeof lote.ocupado === 'function' && lote.ocupado()) {
    toasts.warning(
      'Espere as notas terminarem de sair',
      'Se precisar sair, clique em "parar depois desta nota" e espere o resultado.',
    )
    return false
  }
})
</script>

<template>
  <TooltipProvider :delay-duration="300">
    <div class="space-y-5">
      <RouteTabs :tabs="TABS_NF" />

      <PageHeader
        title="Emissão de Serviço"
        description="Notas fiscais de serviço (NFS-e) das empresas do grupo, emitidas pela NFE.io."
      >
        <template #actions>
          <NfseDica :texto="seloServidor.dica" lado="bottom">
            <span
              tabindex="0"
              class="h-7 cursor-default px-2.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              :class="seloServidor.classe"
              :aria-label="`${seloServidor.texto}. ${seloServidor.dica}`"
            >
              <component
                :is="seloServidor.icone"
                class="size-3.5 shrink-0"
                :class="seloServidor.girar && 'animate-spin motion-reduce:animate-none'"
                aria-hidden="true"
              />
              {{ seloServidor.texto }}
            </span>
          </NfseDica>
          <Button size="sm" variant="outline" :disabled="carregando" @click="recarregar()">
            <RotateCcw class="mr-1.5 size-4" :class="carregando && 'animate-spin'" aria-hidden="true" />
            atualizar
          </Button>
          <Button v-if="canEdit" size="sm" variant="outline" @click="abrirAvulsa({ competencia: mes })">
            <FilePlus2 class="mr-1.5 size-4" aria-hidden="true" />
            nota avulsa
          </Button>
        </template>
      </PageHeader>

      <NfseAviso
        v-if="erroStatus"
        tom="perigo"
        :icone="ShieldAlert"
        titulo="Não deu para conferir a ligação com a NFE.io"
      >
        {{ textoErroStatus }}
        <template #acoes>
          <Button size="sm" variant="outline" :disabled="carregando" @click="recarregar()">tentar de novo</Button>
        </template>
      </NfseAviso>

      <NfseAviso
        v-else-if="status && !status.chave_configurada"
        tom="perigo"
        :icone="ShieldAlert"
        titulo="A chave de acesso da NFE.io não está configurada"
      >
        Sem ela, nenhuma nota pode ser emitida nem atualizada. Fale com o administrador do DaVinci.
      </NfseAviso>

      <NfseAviso
        v-else-if="erroCarga && !carregado"
        tom="perigo"
        titulo="Não carregou a Emissão de Serviço"
      >
        {{ erroCarga }}
        <template #acoes>
          <Button size="sm" variant="outline" :disabled="carregando" @click="recarregar()">tentar de novo</Button>
        </template>
      </NfseAviso>

      <NfseSegmentado v-model="abaModel" :opcoes="opcoesAbas" />

      <KeepAlive :max="4">
        <NfseEmitir v-if="aba === 'emitir'" />
        <NfseHistorico v-else-if="aba === 'notas'" />
        <NfseCadastros v-else-if="aba === 'cadastros'" />
        <NfsePrestadores v-else />
      </KeepAlive>

      <!-- Janelas: montadas uma vez, abertas por qualquer aba via useNfseTela(). -->
      <NfseConfirmDialog ref="confirmRef" />
      <NfseEmitirLote ref="loteRef" />
      <NfseEmitirAvulsa ref="avulsaRef" />
      <NfseHistoricoNota ref="notaRef" />
      <NfseHistoricoCancelar ref="cancelarRef" />
      <NfseModelosSheet ref="modeloRef" />
      <NfseTomadoresSheet ref="tomadorRef" />
      <NfsePrestadoresSheet ref="empresaRef" />
    </div>
  </TooltipProvider>
</template>

<style>
/* Tabelas da Emissão de Serviço (.table-card + .tabela-nfse). O .table-card
   global tira a linha do td:last-child; aqui a linha vai de ponta a ponta
   (inclusive na linha aberta, que é um td só) e some só no fim da tabela. */
.tabela-nfse tbody td {
  border-bottom-width: 1px !important;
}
.tabela-nfse tbody:last-child tr:last-child td {
  border-bottom-width: 0 !important;
}

/* Coluna de ações presa à direita quando a tabela rola para o lado (janela
   estreita). O fundo tem de ser opaco, senão o texto passa por baixo; o tom
   da linha (cabeçalho, grupo, marcada, hover) vai por cima como imagem. */
.tabela-nfse .col-acoes {
  position: sticky;
  right: 0;
  z-index: 1;
  background-color: hsl(var(--card)) !important;
}
.tabela-nfse thead .col-acoes {
  background-image: linear-gradient(hsl(var(--muted) / 0.4), hsl(var(--muted) / 0.4));
}
.tabela-nfse tbody tr:hover .col-acoes,
.tabela-nfse tbody tr.linha-grupo .col-acoes {
  background-image: linear-gradient(hsl(var(--muted) / 0.3), hsl(var(--muted) / 0.3));
}
.tabela-nfse tbody tr.linha-marcada .col-acoes {
  background-image: linear-gradient(hsl(var(--primary) / 0.05), hsl(var(--primary) / 0.05));
}
.tabela-nfse tbody tr.linha-marcada:hover .col-acoes {
  background-image: linear-gradient(hsl(var(--muted) / 0.3), hsl(var(--muted) / 0.3)),
    linear-gradient(hsl(var(--primary) / 0.05), hsl(var(--primary) / 0.05));
}
/* Sombra na borda da coluna presa só quando ainda há tabela escondida por
   baixo dela (navegador sem suporte: fica sem sombra, nada quebra). */
.tabela-nfse,
.tabela-nfse > .overflow-x-auto {
  container-type: scroll-state;
}
@container scroll-state(scrollable: right) {
  /* box-shadow não pinta em célula de tabela com bordas colapsadas */
  .tabela-nfse .col-acoes::before {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: -12px;
    width: 12px;
    pointer-events: none;
    background: linear-gradient(to left, rgb(0 0 0 / 0.08), transparent);
  }
}
</style>
