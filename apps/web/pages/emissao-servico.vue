<script setup lang="ts">
// Cadastros › Emissão de Serviço (ao lado de Empresas desde 30/09): NFS-e de intermediação das empresas do
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
//
// 30/09/2026 (Eduardo: "o mesmo esquema de senha do empresas"): a página pede a
// senha extra (a mesma de Empresas e do Valuation) e só carrega depois dela.
// Quem confere é o servidor: sem a chave, /api/nfse recusa tudo. As abas e as
// janelas chamam a API por useNfseApi() (provide daqui), que leva a chave.
//
// 30/09/2026 (Eduardo: "aumente o tempo de acesso para 30 min"): a chave desta
// página vale 30 minutos (Empresas e Valuation continuam 15). Na mesma leva:
// imprimir e baixar notas em lote (Notas emitidas) e o e-mail da nota passou a
// ser só MANUAL, mandado pelo DaVinci (a NFE.io não recebe mais o e-mail do tomador).
import { computed, onBeforeUnmount, onMounted, provide, ref, unref, watch, type Ref } from 'vue'
import { onBeforeRouteLeave, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import { TooltipProvider } from 'reka-ui'
import {
  BookUser, Building2, CheckCircle2, FilePlus2, FileText, FlaskConical, Loader2, Lock, Plus, RotateCcw, Send, ShieldAlert,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { SENHA_EXTRA_TRAVA } from '~/composables/useSenhaExtra'
import { TABS_CADASTROS } from '~/lib/navGroups'
import {
  erroApi, fmtMes, itemReenvio, MAX_LOTE_ARQUIVOS, mesAtual, mesValido, NFSE_API, NFSE_TELA, plural, prestadorPorId,
  STATUS_PARA_RESOLVER,
  type AbaId, type AbrirModeloOpts, type AbrirTomadorOpts, type AvulsaApi, type CancelarApi, type ConfirmApi,
  type ConfirmarOpts, type EmailApi, type Emissao, type EmpresaApi, type IntegrarApi, type ItemLote, type LoteApi, type Modelo, type ModeloApi,
  type NfseApi, type NfseTela, type NotaApi, type NotaDoLote, type PedidoEmitir, type Prestador, type ResultadoArquivos,
  type ResultadoLote,
  type SecaoEmpresa, type StatusNfse, type Tomador, type TomadorApi,
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
const emailRef = ref<EmailApi | null>(null)
const modeloRef = ref<ModeloApi | null>(null)
const tomadorRef = ref<TomadorApi | null>(null)
const empresaRef = ref<EmpresaApi | null>(null)
// 01/10/2026: "Integrar na NFE.io" (NfseIntegrarDialog).
const integrarRef = ref<IntegrarApi | null>(null)

// --- Senha extra -------------------------------------------------------------
// A mesma senha de Empresas e do Valuation, com desbloqueio próprio (a chave de
// Empresas não abre esta página). A chave fica só na memória da página: tranca
// ao sair de /emissao-servico, ao recarregar e aos 30 minutos (o prazo vem do
// servidor, `expires_in`). Só espera o que termina sozinho: notas saindo no
// assistente, um cancelamento a caminho da prefeitura, um e-mail saindo ou
// notas descendo em lote (trancar no meio desmontaria a janela e o resultado
// se perderia). No último minuto, um aviso deixa digitar a senha de novo sem trancar.

function loteOcupado(): boolean {
  const lote = loteRef.value as Partial<LoteApi> | null
  return !!(lote && typeof lote.ocupado === 'function' && lote.ocupado())
}
function cancelamentoOcupado(): boolean {
  const c = cancelarRef.value as Partial<CancelarApi> | null
  return !!(c && typeof c.ocupado === 'function' && c.ocupado())
}
function emailOcupado(): boolean {
  const m = emailRef.value as Partial<EmailApi> | null
  return !!(m && typeof m.ocupado === 'function' && m.ocupado())
}
// Integrando empresa na NFE.io (pode levar até 2 minutos): a senha que vence espera.
function integrarOcupado(): boolean {
  const m = integrarRef.value as Partial<IntegrarApi> | null
  return !!(m && typeof m.ocupado === 'function' && m.ocupado())
}
// Imprimir/baixar em lote em andamento (100 PDFs levam uns 20 s na NFE.io).
const arquivosOcupado = ref(false)
function algoOcupado(): boolean {
  return loteOcupado() || cancelamentoOcupado() || emailOcupado() || integrarOcupado() || arquivosOcupado.value
}
// A janela de envio aberta (conferindo, esperando a prefeitura ou no fim)
// fecha do jeito normal antes do cadeado: mostra o resumo e devolve o resultado.
function fecharLote() {
  const lote = loteRef.value as Partial<LoteApi> | null
  if (lote && typeof lote.fecharParaTrancar === 'function') lote.fecharParaTrancar()
}

const trava = useSenhaExtra('nfse', '/api/nfse/unlock', 'X-Nfse-Token', /^\/emissao-servico(\/|$)/, {
  podeTrancar: () => !algoOcupado(),
  antesDeTrancar: fecharLote,
})
// As janelas mostram o aviso do último minuto por dentro (NfseSheet/NfseDialog).
provide(SENHA_EXTRA_TRAVA, trava)

// Toda chamada a /api/nfse passa por aqui (e pelas abas, via useNfseApi()).
async function apiN<T = any>(path: string, opts: any = {}): Promise<T> {
  try {
    return await api<T>(path, { ...opts, headers: { ...(opts.headers || {}), ...trava.headers() } })
  } catch (e) {
    // A chave venceu no meio do uso: volta para o cadeado.
    if (trava.eTravamento(e)) trava.trancarQuandoPuder()
    throw e
  }
}

// PDF e XML: link direto não leva o cabeçalho com a chave. A página pede, com a
// chave, um link de 60 s só daquele arquivo, e o navegador abre esse link: o
// PDF abre na aba com o nome da nota (NFSe_<nº>.pdf) e o XML baixa com o nome certo.
function salvarArquivo(url: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = '' // o nome vem do servidor
  document.body.appendChild(a)
  a.click()
  a.remove()
}

async function linkDoArquivo(id: string, tipo: 'pdf' | 'xml'): Promise<string> {
  const r = await apiN<{ url: string }>(`/api/nfse/emissoes/${id}/link?tipo=${tipo}`, { method: 'POST' })
  return r.url
}

async function abrirPdf(id: string): Promise<void> {
  // A aba abre JÁ no clique: depois de esperar o link, o navegador a bloquearia.
  const aba = window.open('', '_blank')
  if (aba) {
    aba.opener = null
    try {
      aba.document.title = 'PDF da nota'
      aba.document.body.textContent = 'Carregando o PDF da nota…'
    } catch {
      // aba de outro jeito (extensão, política do navegador): segue sem o aviso
    }
  }
  try {
    const url = await linkDoArquivo(id, 'pdf')
    // Bloqueador de janelas (sem aba): o PDF é baixado. A pessoa fechou a aba
    // antes de o link chegar: desistiu, não baixa nada.
    if (!aba) salvarArquivo(url)
    else if (!aba.closed) aba.location.href = url
  } catch (e) {
    aba?.close()
    toasts.error('Não deu para abrir o PDF', erroApi(e))
  }
}

async function baixarXml(id: string): Promise<void> {
  try {
    salvarArquivo(await linkDoArquivo(id, 'xml'))
  } catch (e) {
    toasts.error('Não deu para baixar o XML', erroApi(e))
  }
}

// --- Notas em lote (30/09) -----------------------------------------------------
// Imprimir e baixar várias notas de uma vez (aba Notas emitidas e fim do
// assistente de emissão). A NFE.io não tem download em lote: o servidor busca
// nota por nota e devolve UM arquivo — o PDF juntado (imprimir) ou o .zip com
// uma pasta por empresa (o nº da nota é por empresa: ATV nº 4 e Rocha nº 4 não
// se sobrescrevem). O que não veio da NFE.io fica de fora e é avisado aqui pelo
// nº e pela empresa (no .zip, com o motivo, em _FALTARAM.txt).

// Com responseType 'blob' o erro também chega como arquivo: lê o JSON de
// dentro para a mensagem aparecer e para a senha vencida voltar ao cadeado
// (trava.eTravamento só olha e.data.detail). Devolve um erro NOVO: o `data` do
// erro do $fetch é só-leitura (mesma lição da tela Empresas).
async function lerErroDeArquivo(e: any): Promise<any> {
  if (typeof Blob === 'undefined' || !(e?.data instanceof Blob)) return e
  let data: any = null
  try {
    data = JSON.parse(await e.data.text())
  } catch {
    // corpo que não é JSON (proxy fora do ar, por exemplo): fica a mensagem padrão
  }
  const lido = { data, message: e?.message, status: e?.status, statusCode: e?.statusCode }
  if (trava.eTravamento(lido)) trava.trancarQuandoPuder()
  return lido
}

function lerCabecalhos(h: Headers, pedidas: number): ResultadoArquivos {
  const numero = (nome: string, padrao: number) => {
    const bruto = (h.get(nome) ?? '').trim()
    const v = Number(bruto)
    return bruto && Number.isFinite(v) ? v : padrao
  }
  const faltaram = (h.get('X-Nfse-Faltaram') ?? '').split(',').map((s) => s.trim()).filter(Boolean)
  const total = numero('X-Nfse-Total', pedidas)
  return { total, ok: numero('X-Nfse-Ok', total - faltaram.length), faltaram }
}

// "20260930-1415" (hora daqui) para o nome do arquivo.
function carimbo(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`
}

function salvarBlob(arquivo: Blob, nome: string) {
  const href = URL.createObjectURL(arquivo)
  const a = document.createElement('a')
  a.href = href
  a.download = nome
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revogar na hora corta o download em alguns navegadores.
  setTimeout(() => URL.revokeObjectURL(href), 60_000)
}

// "nº 5 (ATV), nº 7 (Rocha)" das que faltaram; acima de 8, "e mais N".
function quaisFaltaram(notas: NotaDoLote[], ids: string[]): string {
  const porId = new Map(notas.map((n) => [n.id, n]))
  const nomes = ids.map((id) => {
    const n = porId.get(id)
    if (!n) return 'uma nota'
    const numero = n.n_nfse ? `nº ${n.n_nfse}` : 'nota sem número'
    return n.prestador_nome ? `${numero} (${n.prestador_nome})` : numero
  })
  const primeiras = nomes.slice(0, 8).join(', ')
  return nomes.length > 8 ? `${primeiras} e mais ${nomes.length - 8}` : primeiras
}

function idsDoLote(notas: NotaDoLote[]): string[] {
  return [...new Set(notas.map((n) => n.id))]
}

// Um lote por vez (a aba Notas emitidas e o fim do assistente usam o mesmo).
function avisarOcupado() {
  toasts.info('Espere terminar o que está descendo', 'Um lote de notas por vez.')
}

// No máximo 100 por vez (o servidor recusa mais). Confere ANTES de abrir a aba
// de impressão: sem isto, a aba abria em branco, fechava e o aviso vinha em inglês.
function demaisNoLote(ids: string[]): boolean {
  if (ids.length <= MAX_LOTE_ARQUIVOS) return false
  toasts.warning(
    `No máximo ${MAX_LOTE_ARQUIVOS} notas por vez`,
    `São ${ids.length}. Vá por partes: filtre por mês ou empresa na aba Notas emitidas.`,
  )
  return true
}

// Junta os PDFs no servidor e abre numa aba para imprimir (Ctrl+P no leitor de
// PDF do navegador). A aba abre JÁ no clique: depois de esperar o servidor, o
// navegador a bloquearia sem aviso (lição do controle-estoque: "5 funcionava e
// 8 não"). Bloqueador de janelas (sem aba): baixa o PDF. A pessoa fechou a aba
// antes de o PDF chegar: desistiu, não faz nada.
async function imprimirLote(notas: NotaDoLote[]): Promise<ResultadoArquivos | null> {
  const ids = idsDoLote(notas)
  if (!ids.length || demaisNoLote(ids)) return null
  if (arquivosOcupado.value) {
    avisarOcupado()
    return null
  }
  const aba = window.open('', '_blank')
  if (aba) {
    aba.opener = null
    try {
      aba.document.title = 'Notas para imprimir'
      aba.document.body.textContent = `Juntando ${plural(ids.length, 'nota', 'notas')} para imprimir…`
    } catch {
      // aba de outro jeito (extensão, política do navegador): segue sem o aviso
    }
  }
  arquivosOcupado.value = true
  try {
    let cab: ResultadoArquivos | null = null
    const pdf = await apiN<Blob>('/api/nfse/emissoes/lote/imprimir', {
      method: 'POST',
      body: { ids },
      responseType: 'blob',
      onResponse({ response }: { response: Response }) {
        if (response.ok) cab = lerCabecalhos(response.headers, ids.length)
      },
    })
    const r: ResultadoArquivos = cab ?? { total: ids.length, ok: ids.length, faltaram: [] }
    if (!aba) {
      salvarBlob(pdf, `notas-de-servico_${carimbo()}.pdf`)
    } else if (!aba.closed) {
      const url = URL.createObjectURL(pdf)
      aba.location.href = url
      // O leitor de PDF já carregou; depois de 1 minuto o link some da memória.
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    }
    if (r.faltaram.length) {
      toasts.warning('Ficaram fora da impressão', [
        `${quaisFaltaram(notas, r.faltaram)}.`,
        'A NFE.io não devolveu o PDF dessas notas. Tente de novo em alguns minutos.',
      ])
    }
    return r
  } catch (e) {
    aba?.close()
    const lido = await lerErroDeArquivo(e)
    // Senha vencida: o cadeado já explica.
    if (!trava.eTravamento(lido)) toasts.error('Não deu para juntar as notas para imprimir', erroApi(lido))
    return null
  } finally {
    arquivosOcupado.value = false
  }
}

// Baixa o .zip com os PDFs ou os XMLs das notas (uma pasta por empresa). O nome
// é montado aqui: o $fetch com blob não lê o nome que o servidor manda.
async function baixarLote(notas: NotaDoLote[], tipo: 'pdf' | 'xml'): Promise<ResultadoArquivos | null> {
  const ids = idsDoLote(notas)
  if (!ids.length || demaisNoLote(ids)) return null
  if (arquivosOcupado.value) {
    avisarOcupado()
    return null
  }
  arquivosOcupado.value = true
  const rotulo = tipo === 'pdf' ? 'PDFs' : 'XMLs'
  try {
    let cab: ResultadoArquivos | null = null
    const zip = await apiN<Blob>('/api/nfse/emissoes/lote/arquivos', {
      method: 'POST',
      body: { ids, tipo },
      responseType: 'blob',
      onResponse({ response }: { response: Response }) {
        if (response.ok) cab = lerCabecalhos(response.headers, ids.length)
      },
    })
    const r: ResultadoArquivos = cab ?? { total: ids.length, ok: ids.length, faltaram: [] }
    salvarBlob(zip, `notas-de-servico_${tipo}_${carimbo()}.zip`)
    if (r.faltaram.length) {
      toasts.warning(`Baixadas ${r.ok} de ${plural(r.total, 'nota', 'notas')} (${rotulo})`, [
        `Faltaram: ${quaisFaltaram(notas, r.faltaram)}.`,
        'A NFE.io não devolveu o arquivo dessas notas; o motivo está no _FALTARAM.txt, dentro do .zip.',
      ])
    } else {
      toasts.success(r.ok === 1 ? `1 nota baixada (${rotulo})` : `${r.ok} notas baixadas (${rotulo})`)
    }
    return r
  } catch (e) {
    const lido = await lerErroDeArquivo(e)
    if (!trava.eTravamento(lido)) toasts.error(`Não deu para baixar os ${rotulo}`, erroApi(lido))
    return null
  } finally {
    arquivosOcupado.value = false
  }
}

const nfseApi: NfseApi = { api: apiN, abrirPdf, baixarXml, imprimirLote, baixarLote }
provide(NFSE_API, nfseApi)

function bloquear() {
  if (algoOcupado()) {
    toasts.warning('Espere terminar o que está em andamento', 'Dá para bloquear assim que acabar.')
    return
  }
  fecharLote()
  trava.trancar()
}

// Trancou: nada da página fica na memória esperando a próxima senha. `geracao`
// descarta a resposta de uma carga que ainda estava no caminho.
let geracao = 0
function esquecerTudo() {
  geracao++
  status.value = null
  erroStatus.value = null
  prestadores.value = []
  tomadores.value = []
  modelos.value = []
  contadorNotas.value = 0
  contadorEmitir.value = null
  erroCarga.value = null
  carregado.value = false
  // A janela da nota/empresa que estava aberta sumiu com o cadeado. Com a
  // ?nota= / ?empresa= ainda na URL, ela reabre depois da senha, como um link.
  notaAberta = null
  empresaAberta = null
}

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
      STATUS_PARA_RESOLVER.map((s) => apiN<Emissao[]>(`/api/nfse/emissoes?status=${s}`)),
    )
    return listas.reduce((total, l) => total + l.length, 0)
  } catch {
    return contadorNotas.value
  }
}

// Lê a ligação do servidor com a NFE.io (chave e trava de produção). Falhou:
// ninguém emite até saber (erroStatus).
async function lerStatus(): Promise<StatusNfse | null> {
  const minha = geracao
  try {
    const st = await apiN<StatusNfse>('/api/nfse/status')
    if (minha !== geracao) return null
    status.value = st
    erroStatus.value = null
    return st
  } catch (e) {
    if (minha !== geracao) return null
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
    const minha = geracao
    const [, ps] = await Promise.all([lerStatus(), apiN<Prestador[]>('/api/nfse/prestadores')])
    if (minha !== geracao) return null
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
  // Trancada: não há o que carregar (a senha dispara a carga de novo).
  if (!trava.token.value) return
  const minha = geracao
  carregando.value = true
  try {
    const [ps, ts, ms, n] = await Promise.all([
      apiN<Prestador[]>('/api/nfse/prestadores'),
      apiN<Tomador[]>('/api/nfse/tomadores'),
      apiN<Modelo[]>('/api/nfse/modelos'),
      contarParaResolver(),
      lerStatus(),
    ])
    // Trancou enquanto carregava: descarta.
    if (minha !== geracao) return
    prestadores.value = ps
    tomadores.value = ts
    modelos.value = ms
    contadorNotas.value = n
    erroCarga.value = null
    versao.value++
    carregado.value = true
  } catch (e) {
    // Trancou no meio (ou a chave venceu): o cadeado já explica, sem aviso de erro.
    if (minha !== geracao || trava.eTravamento(e)) return
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

// Botão "atualizar" do topo (01/10/2026, Eduardo: "quando clique no botão de
// atualizar, ele atualize tudo também"): antes de reler o DaVinci, relê da
// NFE.io todas as empresas ligadas (certificado trocado lá só aparecia no
// "Atualizar" de dentro da empresa). Só GET lá; quem não edita só recarrega.
// Se a NFE.io não responder, avisa e mostra o que já estava guardado.
const relendoNfeio = ref(false)

async function atualizarTudo(): Promise<void> {
  if (unref(canEdit) && !relendoNfeio.value) {
    relendoNfeio.value = true
    try {
      await apiN('/api/nfse/nfeio/atualizar-ligadas', { method: 'POST' })
    } catch (e) {
      if (!trava.eTravamento(e)) toasts.warning('Não deu para reler a NFE.io agora', erroApi(e))
    } finally {
      relendoNfeio.value = false
    }
  }
  await recarregar()
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
    const ps = await apiN<Prestador[]>('/api/nfse/prestadores')
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

// 01/10/2026 (Eduardo: "não aparece para qual mês eu quero gerar ela, ela já cai
// em outubro… quero a opção de escolher na hora"): a gaveta da nota fixa nova
// chama isto ao salvar. Troca o mês do Emitir e deixa o pedido (base do % e as
// recém-criadas para marcar) para o NfseEmitir ler. Mês futuro não vale.
const pedidoEmitir = ref<PedidoEmitir | null>(null)
// A "Base do %" do Emitir mora aqui (o NfseEmitir usa este ref) para a gaveta
// da nota fixa nova começar com ela.
const baseEmitir = ref<{ mes: string; valor: string }>({ mes: '', valor: '' })
function irParaEmitir(o: PedidoEmitir) {
  const m = mesDaQuery(o.mes)
  mes.value = m
  pedidoEmitir.value = { ...o, mes: m }
  irPara('emitir')
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
    const nova = await apiN<Emissao>(`/api/nfse/emissoes/${e.id}/conferir`, { method: 'POST' })
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
        apiN<{ emissoes: Emissao[] }>('/api/nfse/emissoes/atualizar', { method: 'POST', body: { ids: p } }),
      ),
    )
    return rs.flatMap((r) => r.emissoes ?? [])
  } catch {
    return null
  }
}

// Excluir nota fixa (Eduardo, 30/09: "precisa ter um botão para apagar quando
// necessário"). Do "Emitir do mês", da lista de notas fixas e do formulário.
async function excluirModelo(m: Modelo): Promise<boolean> {
  const ok = await confirmar({
    tom: 'perigo',
    titulo: `Excluir a nota fixa “${m.nome}”?`,
    texto: 'Se ela já tiver nota emitida, fica só desativada: o que já saiu não muda.',
    botao: 'Excluir nota fixa',
  })
  if (!ok) return false
  try {
    await apiN(`/api/nfse/modelos/${m.id}`, { method: 'DELETE' })
    await recarregar()
    if (modelos.value.some((x) => x.id === m.id)) {
      toasts.info('A nota fixa já tinha nota: ficou desativada', 'O que já saiu continua em Notas emitidas.')
    } else {
      toasts.success('Nota fixa excluída')
    }
    return true
  } catch (e) {
    toasts.error('Não deu para excluir a nota fixa', erroApi(e))
    return false
  }
}

// "Enviar por e-mail" (30/09/2026, Eduardo: nada automático). O e-mail do
// tomador não vai mais à NFE.io (com ele, ela avisava o tomador sozinha na
// emissão e no cancelamento). Quem manda o PDF e o XML é o DaVinci, só quando a
// pessoa pede, para o endereço que ela confirma na janela (NfseEmailDialog).
async function enviarEmail(e: Emissao): Promise<boolean> {
  const j = janela(emailRef, 'enviar')
  return j ? j.enviar(e) : false
}

// 01/10/2026 (Eduardo: "precisa integrar"): cria a empresa na NFE.io (ou
// completa o que falta) pela janela NfseIntegrarDialog. true = mudou algo.
async function integrarEmpresa(p: Prestador): Promise<boolean> {
  const j = janela(integrarRef, 'abrir')
  return j ? j.abrir(p) : false
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
  excluirModelo,
  abrirTomador,
  abrirNota,
  abrirAvulsa,
  emitirLote,
  conferir,
  atualizarEmissoes,
  reenviar,
  cancelar,
  enviarEmail,
  integrarEmpresa,
  irParaEmitir,
  pedidoEmitir,
  baseEmitir,
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
    rotulo: 'Notas emitidas',
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

// Carrega quando a chave aparece (a pessoa acabou de digitar a senha). Não dá
// para esperar um aviso do cartão: ao desbloquear ele sai da tela e o Vue
// descarta o aviso (lição da tela Empresas, 25/09/2026).
async function carregarAposSenha() {
  await recarregar()
  // Link com ?nota= / ?empresa=: abre depois da senha e da carga (mesmo se a
  // carga falhou, como antes da senha existir: a janela lê a nota sozinha).
  if (trava.token.value) abrirDaQuery(route.query)
}

watch(
  () => trava.token.value,
  (agora, antes) => {
    if (agora && !antes) void carregarAposSenha()
    if (!agora && antes) esquecerTudo()
  },
)

onMounted(() => {
  document.addEventListener('visibilitychange', aoVoltarParaAba)
  if (trava.iniciar()) void carregarAposSenha()
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
      <RouteTabs :tabs="TABS_CADASTROS" />

      <SenhaExtraTrava v-if="!trava.token.value" titulo="Emissão de Serviço" :trava="trava" :minutos="30" />
      <template v-else>
      <SenhaExtraRenovar :trava="trava" />
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
          <Button
            size="sm"
            variant="outline"
            :disabled="carregando || relendoNfeio"
            :title="canEdit ? 'relê da NFE.io todas as empresas ligadas e recarrega a tela' : 'recarrega a tela'"
            @click="atualizarTudo()"
          >
            <RotateCcw class="mr-1.5 size-4" :class="(carregando || relendoNfeio) && 'animate-spin'" aria-hidden="true" />
            {{ relendoNfeio ? 'relendo a NFE.io…' : 'atualizar' }}
          </Button>
          <!-- 01/10/2026 (Eduardo: "ele precisa ir lá pra cima do lado de nota avulsa, e
               precisa aparecer em emitir do mês"): saiu do cartão "Notas fixas". -->
          <Button v-if="canEdit" size="sm" variant="outline" @click="abrirModelo()">
            <Plus class="mr-1.5 size-4" aria-hidden="true" />
            nova nota fixa
          </Button>
          <Button v-if="canEdit" size="sm" variant="outline" @click="abrirAvulsa({ competencia: mes })">
            <FilePlus2 class="mr-1.5 size-4" aria-hidden="true" />
            nota avulsa
          </Button>
          <Button size="sm" variant="ghost" title="tranca a página de novo nesta aba" @click="bloquear">
            <Lock class="mr-1.5 size-4" aria-hidden="true" />
            bloquear
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
      <NfseEmailDialog ref="emailRef" />
      <NfseModelosSheet ref="modeloRef" />
      <NfseTomadoresSheet ref="tomadorRef" />
      <NfsePrestadoresSheet ref="empresaRef" />
      <NfseIntegrarDialog ref="integrarRef" />
      </template>
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
