<script setup lang="ts">
// Uma empresa do grupo (quem emite a NFS-e), numa gaveta à direita. Montada
// UMA vez pela página; qualquer aba abre com `tela.abrirEmpresa(companyId,
// foco?)` — o "corrigir" da prévia cai direto na seção certa (foco). Devolve
// true se salvou (ou ligou/atualizou a NFE.io) pelo menos uma vez.
//
// 29/09/2026 (motor NFE.io): a empresa emite pela NFE.io. Saem "Onde a empresa
// está", "Regime tributário", "Total aproximado de tributos", "Certificado A1",
// o avançado (série) e os testes no gov.br: tudo isso está no cadastro da
// empresa NA NFE.io e aparece aqui só para ler, no cartão "NFE.io" (ligada,
// Teste × Produção, situação na prefeitura, regime, se retém IR, certificado,
// inscrição municipal, município, link "Abrir na NFE.io"). Os botões do cartão
// só LEEM a NFE.io: procurar pelo CNPJ, colar o link e atualizar.
//
// Ficam aqui o "Serviço prestado" (código do serviço na prefeitura, item da LC
// 116, NBS e a retenção de IR: automática, sempre ou nunca) e a % padrão das
// notas de percentual (só leitura; muda em Cadastros › Empresas).
//
// Pendências (impedem a emissão) e avisos (não impedem) vêm do servidor, já em
// texto para ler, no topo da gaveta.
//
// 01/10/2026 (Eduardo: "precisa integrar"): empresa sem NFE.io ganha o botão
// "Integrar na NFE.io" (cria lá com os dados da Receita e o certificado
// guardado; diálogo NfseIntegrarDialog). Os botões de procurar/colar viram o
// caminho secundário ("Já existe na NFE.io?"). Ligada mas sem certificado ou
// sem inscrição municipal lá: aviso + "Completar integração".
import { computed, nextTick, ref } from 'vue'
import {
  AlertTriangle, Briefcase, Check, CheckCircle2, ClipboardPaste, ExternalLink, Eye, Loader2, Percent, Plug, PlugZap,
  RefreshCw, Save, Search, XCircle,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  ambienteTexto, campoDoErro, erroApi, fmtDataHora, fmtDoc, fmtHora, fmtPct, OPCOES_RETENCAO, pctPositivo,
  pendenciaTexto, plural, prestadorPorId, regimeTexto, situacaoCertificado, situacaoFiscalTexto, soDigitos, TOM_TEXTO,
  useNfseTela,
  type Fiscal, type Prestador, type RetencaoIr, type SecaoEmpresa,
  useNfseApi,
} from '~/lib/nfse'

const tela = useNfseTela()
const { prestadores, canEdit, podeAbrirCadastroEmpresa, podeEditarCadastroEmpresa } = tela
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

// O padrão do grupo (medido nas notas que já saíram pela NFE.io): 6303 na
// prefeitura, item 10.05 da LC 116 (intermediação).
const VAZIO: Fiscal = {
  city_service_code: '6303',
  federal_service_code: '10.05',
  c_nbs: null,
  retencao_ir: 'auto',
  email: null,
  fone: null,
}

type CampoForm = 'city_service_code' | 'federal_service_code' | 'c_nbs'

// Campos que mostram o próprio erro embaixo. Erro da API em outro campo vira
// aviso geral (para nunca sumir calado).
const CAMPOS_COM_ERRO = new Set(['city_service_code', 'federal_service_code', 'c_nbs', 'retencao_ir'])

const CLASSE_INPUT =
  'h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:[color-scheme:dark]'
const CLASSE_ERRO = 'border-red-500 dark:border-red-400'

// Três campos lado a lado: cada NfseCampo vira subgrade (rótulo, campo, dica)
// para os campos ficarem na mesma altura mesmo quando um rótulo quebra linha.
const CAMPO_EM_GRADE = 'sm:row-span-3 sm:grid sm:grid-rows-subgrid sm:gap-y-1.5 sm:space-y-0'

// --- Estado ----------------------------------------------------------------------

const aberto = ref(false)
const companyId = ref<string | null>(null)
// Sempre a versão mais nova da lista da tela (depois de recarregar, as
// pendências e o cartão da NFE.io atualizam sozinhos).
const p = computed<Prestador | null>(() => prestadorPorId(prestadores.value, companyId.value))

const form = ref<Fiscal>({ ...VAZIO })
const salvo = ref<Fiscal>({ ...VAZIO })
const salvando = ref(false)
const salvoEm = ref<Date | null>(null)
const erros = ref<Record<string, string>>({})
const sheetRef = ref<{ rolarPara(id: string): void } | null>(null)

// Cartão da NFE.io: procurar pelo CNPJ, colar o link, atualizar.
const nfeioAcao = ref<null | 'cnpj' | 'link' | 'atualizar'>(null)
const nfeioErro = ref<string | null>(null)
const colando = ref(false)
const refColado = ref('')
const campoColar = ref<HTMLInputElement | null>(null)

let resolverAbrir: ((v: boolean) => void) | null = null
let promessaAberta: Promise<boolean> | null = null
let salvouAlgo = false

// --- Formulário ⇄ API ------------------------------------------------------------

function paraForm(f: Partial<Fiscal> | null | undefined): Fiscal {
  const out: Fiscal = { ...VAZIO, ...(f ?? {}) }
  if (out.retencao_ir !== 'sempre' && out.retencao_ir !== 'nunca') out.retencao_ir = 'auto'
  return out
}

function corpo(f: Fiscal): Record<string, unknown> {
  const limpo = (v: string | null | undefined) => {
    const t = (v ?? '').trim()
    return t === '' ? null : t
  }
  return {
    city_service_code: limpo(f.city_service_code),
    federal_service_code: limpo(f.federal_service_code),
    c_nbs: limpo(f.c_nbs),
    retencao_ir: f.retencao_ir,
    email: limpo(f.email),
    fone: limpo(f.fone),
  }
}

const sujo = computed(() => JSON.stringify(corpo(form.value)) !== JSON.stringify(corpo(salvo.value)))
// Empresa sem serviço prestado salvo: dá para salvar o padrão sem mexer em nada.
const podeSalvar = computed(() => canEdit.value && !salvando.value && (sujo.value || !p.value?.fiscal))

// --- Abrir / fechar ----------------------------------------------------------------

function iniciar(alvo: Prestador) {
  const f = paraForm(alvo.fiscal)
  form.value = f
  salvo.value = { ...f }
  erros.value = {}
  salvoEm.value = null
  salvouAlgo = false
  nfeioAcao.value = null
  nfeioErro.value = null
  colando.value = false
  refColado.value = ''
}

function rolarPara(id: string) {
  sheetRef.value?.rolarPara(id)
}

function rolarDepois(foco?: SecaoEmpresa) {
  if (!foco) return
  // Espera a gaveta terminar de entrar antes de rolar.
  void nextTick(() => setTimeout(() => rolarPara(foco), 250))
}

async function abrir(id: string, foco?: SecaoEmpresa): Promise<boolean> {
  // A mesma empresa já está aberta (ex.: outro "corrigir"): não perde o que
  // foi digitado, só leva até a seção pedida.
  if (aberto.value && companyId.value === id && promessaAberta) {
    rolarDepois(foco)
    return promessaAberta
  }
  // Pedido novo com a gaveta aberta: o anterior termina aqui.
  if (resolverAbrir) {
    const r = resolverAbrir
    resolverAbrir = null
    promessaAberta = null
    r(salvouAlgo)
  }
  companyId.value = id
  if (!p.value && !tela.carregado.value) await tela.recarregar()
  const alvo = p.value
  if (!alvo) {
    companyId.value = null
    toasts.error('Empresa não encontrada', 'Atualize a página e tente de novo.')
    return false
  }
  iniciar(alvo)
  const promessa = new Promise<boolean>((res) => {
    resolverAbrir = res
  })
  promessaAberta = promessa
  aberto.value = true
  rolarDepois(foco)
  return promessa
}

function fechar() {
  aberto.value = false
  const r = resolverAbrir
  resolverAbrir = null
  promessaAberta = null
  r?.(salvouAlgo)
}

// X, Esc e clique fora: o NfseSheet já perguntou "Sair sem salvar?".
function aoMudarAberto(v: boolean) {
  if (!v) fechar()
}

const textoSujo = computed(() => `As mudanças no serviço prestado da ${p.value?.apelido ?? 'empresa'} vão se perder.`)

// Botão "Fechar" do rodapé: mesma pergunta do X.
async function pedirFechar() {
  if (sujo.value && canEdit.value) {
    const ok = await tela.confirmar({
      titulo: 'Sair sem salvar?',
      texto: textoSujo.value,
      tom: 'perigo',
      botao: 'Sair sem salvar',
      voltar: 'Continuar editando',
    })
    if (!ok) return
  }
  fechar()
}

// --- Pendências e avisos (do servidor) -----------------------------------------------

const pendencias = computed(() => (p.value?.pendencias ?? []).map((t) => ({ bruto: t, ...pendenciaTexto(t) })))
const avisos = computed(() => p.value?.avisos ?? [])

function resolverPendencia(x: { alvo: 'empresa' | 'cadastro'; foco?: SecaoEmpresa }) {
  if (x.alvo === 'cadastro') {
    if (p.value && podeAbrirCadastroEmpresa.value) window.open(`/companies/${p.value.company_id}`, '_blank', 'noopener')
    return
  }
  rolarPara(x.foco ?? 'nfeio')
}

// --- Cartão NFE.io -------------------------------------------------------------------

const n = computed(() => p.value?.nfeio ?? null)
const cert = computed(() => (p.value ? situacaoCertificado(p.value) : null))
const situacaoFiscal = computed(() => situacaoFiscalTexto(n.value?.status_fiscal))

function textoRetencao(): string {
  const x = n.value
  if (!x) return '—'
  const regra = form.value.retencao_ir === 'auto' ? salvo.value.retencao_ir : form.value.retencao_ir
  if (regra === 'sempre') return 'Sim, em toda nota (definido aqui)'
  if (regra === 'nunca') return 'Não (definido aqui)'
  return x.retem_ir ? 'Sim: 1,5% quando o IR passa de R$ 10,00' : 'Não (pelo regime da empresa)'
}

// Resposta do servidor ao ligar/atualizar: a empresa inteira (PrestadorOut).
// Recarrega a tela para a lista e as outras abas acompanharem.
async function aposNfeio(msg: string) {
  salvouAlgo = true
  toasts.success(msg)
  await tela.recarregar()
}

async function ligar(ref: string, acao: 'cnpj' | 'link') {
  const alvo = p.value
  if (!alvo || !canEdit.value || nfeioAcao.value) return
  nfeioAcao.value = acao
  nfeioErro.value = null
  try {
    await api<Prestador>(`/api/nfse/prestadores/${alvo.company_id}/nfeio/ligar`, {
      method: 'POST',
      body: { ref },
    })
    colando.value = false
    refColado.value = ''
    await aposNfeio(`${alvo.apelido} ligada à NFE.io`)
  } catch (e) {
    nfeioErro.value = erroApi(e)
  } finally {
    nfeioAcao.value = null
  }
}

// "Integrar na NFE.io" / "Completar integração": o diálogo avisa e recarrega.
async function integrar() {
  const alvo = p.value
  if (!alvo || !canEdit.value || nfeioAcao.value) return
  const mudou = await tela.integrarEmpresa(alvo)
  if (mudou) salvouAlgo = true
}

function procurarPeloCnpj() {
  void ligar('', 'cnpj')
}

function abrirColar() {
  colando.value = true
  nfeioErro.value = null
  void nextTick(() => campoColar.value?.focus())
}

// Aceita o link (https://app.nfe.io/companies/<id>), o id sozinho ou um CNPJ.
const refValido = computed(() => {
  const t = refColado.value.trim()
  if (!t) return false
  if (/app\.nfe\.io\/companies\/[0-9a-f]{24,32}/i.test(t)) return true
  if (/^[0-9a-f]{24}$|^[0-9a-f]{32}$/i.test(t)) return true
  return soDigitos(t).length === 14
})

function ligarPeloColado() {
  if (!refValido.value) {
    nfeioErro.value = 'Cole o link da empresa na NFE.io (app.nfe.io/companies/…), o código dela ou o CNPJ.'
    return
  }
  void ligar(refColado.value.trim(), 'link')
}

async function atualizarNfeio() {
  const alvo = p.value
  if (!alvo || !canEdit.value || nfeioAcao.value) return
  nfeioAcao.value = 'atualizar'
  nfeioErro.value = null
  try {
    await api<Prestador>(`/api/nfse/prestadores/${alvo.company_id}/nfeio/atualizar`, { method: 'POST' })
    await aposNfeio(`${alvo.apelido} atualizada da NFE.io`)
  } catch (e) {
    nfeioErro.value = erroApi(e)
  } finally {
    nfeioAcao.value = null
  }
}

// '2026-09-28T02:10:00Z' → data e hora locais.
function quando(iso: string | null | undefined): string {
  const t = fmtDataHora(iso)
  return t === '—' ? t : t.replace(' ', ' às ')
}

// --- Serviço prestado ------------------------------------------------------------------

function limparErro(campo: string) {
  if (!erros.value[campo]) return
  const e = { ...erros.value }
  delete e[campo]
  erros.value = e
}

const LIMPAR: Record<CampoForm, (v: string) => string> = {
  // O código da prefeitura varia de cidade para cidade: números, letras, ponto, traço e barra.
  city_service_code: (v) => v.replace(/[^\w.\-/]/g, '').slice(0, 20),
  federal_service_code: (v) => v.replace(/[^\d.]/g, '').slice(0, 10),
  c_nbs: (v) => soDigitos(v).slice(0, 9),
}

function digitar(campo: CampoForm, ev: Event) {
  const el = ev.target as HTMLInputElement
  const v = LIMPAR[campo](el.value)
  if (el.value !== v) el.value = v
  form.value[campo] = v
  limparErro(campo)
}

const retencaoModel = computed({
  get: () => form.value.retencao_ir as string,
  set: (v: string | number | null) => {
    form.value.retencao_ir = (v === 'sempre' || v === 'nunca' ? v : 'auto') as RetencaoIr
  },
})

function validar(): Record<string, string> {
  const f = form.value
  const e: Record<string, string> = {}
  if (!(f.city_service_code ?? '').trim()) e.city_service_code = 'Sem o código do serviço a nota não sai.'
  const fed = (f.federal_service_code ?? '').trim()
  if (fed && !/^\d{1,2}\.?\d{2}$/.test(fed)) e.federal_service_code = 'Use o item da LC 116. Ex.: 10.05'
  if ((f.c_nbs ?? '').trim() && soDigitos(f.c_nbs).length !== 9) e.c_nbs = 'Tem 9 números.'
  return e
}

async function salvar(): Promise<boolean> {
  const alvo = p.value
  if (!alvo || !canEdit.value || salvando.value) return false
  const e = validar()
  erros.value = e
  if (Object.keys(e).length) {
    rolarPara('servico')
    return false
  }
  salvando.value = true
  try {
    // O contrato devolve a empresa (PrestadorOut); a versão antiga, { ok, fiscal }.
    const r = await api<{ fiscal?: Fiscal | null }>(`/api/nfse/prestadores/${alvo.company_id}/fiscal`, {
      method: 'PUT',
      body: corpo(form.value),
    })
    const novo = r?.fiscal ? paraForm(r.fiscal) : { ...form.value }
    form.value = novo
    salvo.value = { ...novo }
    salvoEm.value = new Date()
    salvouAlgo = true
    toasts.success(`Serviço prestado da ${alvo.apelido} salvo`)
    // Não fecha. Se o Emitir estiver aberto por trás, a prévia refaz pela `versao`.
    await tela.recarregar()
    return true
  } catch (err) {
    const campo = campoDoErro(err)
    if (campo && CAMPOS_COM_ERRO.has(campo)) {
      erros.value = { ...erros.value, [campo]: erroApi(err) }
      rolarPara('servico')
    } else {
      toasts.error('Não salvou', erroApi(err))
    }
    return false
  } finally {
    salvando.value = false
  }
}

// Enter num campo: salva só se mudou algo (não repete o PUT à toa).
function enviar() {
  if (podeSalvar.value) void salvar()
}

defineExpose({ abrir })
</script>

<template>
  <NfseSheet
    ref="sheetRef"
    :open="aberto"
    largura="lg"
    :titulo="p ? `Empresa — ${p.apelido}` : 'Empresa'"
    :subtitulo="p ? `${p.razao_social} · ${p.cnpj ? fmtDoc(p.cnpj) : 'sem CNPJ'}` : undefined"
    :sujo="sujo && canEdit"
    :texto-sujo="textoSujo"
    @update:open="aoMudarAberto"
  >
    <template #cabecalho-extra>
      <template v-if="p">
        <NfseAmbienteBadge tamanho="sm" :ambiente="p.nfeio?.ambiente" :ligada="!!p.nfeio" />
        <span v-if="p.pronto" class="pill-success">
          <Check class="size-3" aria-hidden="true" />
          Pronta
        </span>
        <span v-else class="pill-warning">{{ plural(p.pendencias.length, 'pendência', 'pendências') }}</span>
      </template>
    </template>

    <div v-if="p" class="space-y-5">
      <NfseAviso v-if="!canEdit" tom="neutro" :icone="Eye" compacto>
        Você pode ver, mas não pode mudar nem ligar a empresa à NFE.io.
      </NfseAviso>

      <!-- 0. O que impede e o que só avisa (do servidor) -->
      <NfseAviso v-if="pendencias.length" tom="perigo" :titulo="`A ${p.apelido} ainda não pode emitir`">
        <ul class="space-y-1">
          <li v-for="x in pendencias" :key="x.bruto" class="flex flex-wrap items-start gap-x-2">
            <span class="min-w-0 flex-1">{{ x.texto }}</span>
            <Button
              v-if="x.alvo === 'empresa' || podeAbrirCadastroEmpresa"
              type="button"
              variant="link"
              class="h-auto p-0 text-xs"
              @click="resolverPendencia(x)"
            >
              {{ x.alvo === 'cadastro' ? 'abrir Cadastros › Empresas' : 'ver onde resolver' }}
            </Button>
          </li>
        </ul>
      </NfseAviso>
      <NfseAviso v-else tom="sucesso" compacto>
        A {{ p.apelido }} pode emitir.
      </NfseAviso>
      <NfseAviso v-if="avisos.length" tom="atencao" titulo="Avisos (não impedem a emissão)">
        <ul class="list-disc space-y-0.5 pl-4">
          <li v-for="a in avisos" :key="a">{{ a }}</li>
        </ul>
      </NfseAviso>

      <!-- 1. NFE.io (só leitura: o cadastro fica na NFE.io) -->
      <NfseSecao
        id="nfeio"
        titulo="NFE.io"
        descricao="A NFE.io assina e manda a nota para a prefeitura. Certificado, regime e inscrição municipal ficam no cadastro da empresa lá."
        :icone="Plug"
      >
        <template v-if="n && canEdit" #acoes>
          <Button type="button" size="sm" variant="outline" :disabled="!!nfeioAcao" @click="atualizarNfeio">
            <Loader2
              v-if="nfeioAcao === 'atualizar'"
              class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
            <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
            Atualizar
          </Button>
        </template>

        <!-- Não ligada -->
        <div v-if="!n" class="space-y-3 rounded-md border border-dashed p-3">
          <p class="text-sm">
            <span class="font-medium">Esta empresa ainda não está integrada na NFE.io.</span>
            <span class="text-muted-foreground"> Sem isso ela não emite.</span>
          </p>
          <div v-if="canEdit">
            <Button type="button" size="sm" :disabled="!!nfeioAcao" @click="integrar">
              <PlugZap class="mr-1.5 size-4" aria-hidden="true" />
              Integrar na NFE.io
            </Button>
          </div>
          <div v-if="canEdit" class="flex flex-wrap items-center gap-2 border-t pt-3">
            <span class="text-xs text-muted-foreground">Já existe na NFE.io?</span>
            <NfseDica :texto="p.cnpj ? 'Procura na NFE.io a empresa com o mesmo CNPJ.' : 'A empresa está sem CNPJ: cadastre em Cadastros › Empresas.'">
              <span class="inline-flex" :tabindex="p.cnpj ? undefined : 0">
                <Button type="button" size="sm" variant="outline" :disabled="!p.cnpj || !!nfeioAcao" @click="procurarPeloCnpj">
                  <Loader2
                    v-if="nfeioAcao === 'cnpj'"
                    class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
                    aria-hidden="true"
                  />
                  <Search v-else class="mr-1.5 size-4" aria-hidden="true" />
                  Procurar pelo CNPJ
                </Button>
              </span>
            </NfseDica>
            <Button type="button" size="sm" variant="outline" :disabled="!!nfeioAcao" @click="abrirColar">
              <ClipboardPaste class="mr-1.5 size-4" aria-hidden="true" />
              Colar link da NFE.io
            </Button>
          </div>
        </div>

        <!-- Ligada -->
        <div v-else class="space-y-3 rounded-md border p-3">
          <NfseAviso v-if="p.integracao === 'incompleta' && canEdit" tom="atencao">
            A integração ficou incompleta (falta certificado ou inscrição municipal na NFE.io, ou a
            NFE.io ainda não respondeu se a inscrição está lá).
            <template #acoes>
              <Button type="button" size="sm" :disabled="!!nfeioAcao" @click="integrar">
                <PlugZap class="mr-1.5 size-4" aria-hidden="true" />
                Completar integração
              </Button>
            </template>
          </NfseAviso>
          <dl class="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2 text-sm sm:grid-cols-[170px_1fr]">
            <dt class="text-muted-foreground">Ambiente</dt>
            <dd class="flex flex-wrap items-center gap-1.5">
              <NfseAmbienteBadge tamanho="sm" :ambiente="n.ambiente" />
              <span class="text-xs text-muted-foreground">{{ ambienteTexto(n.ambiente) }}</span>
            </dd>

            <dt class="text-muted-foreground">Situação na prefeitura</dt>
            <dd :class="TOM_TEXTO[situacaoFiscal.tom]">{{ situacaoFiscal.texto }}</dd>

            <dt class="text-muted-foreground">Regime</dt>
            <dd :class="!n.regime && 'text-muted-foreground'">{{ regimeTexto(n.regime) }}</dd>

            <dt class="text-muted-foreground">Retém IR?</dt>
            <dd>{{ textoRetencao() }}</dd>

            <dt class="text-muted-foreground">Certificado</dt>
            <dd class="flex flex-wrap items-center gap-1.5">
              <NfseNfeioChip :prestador="p" />
              <span v-if="cert?.vencido && !n.teste" class="text-xs" :class="TOM_TEXTO.perigo">
                renove na NFE.io: vencido, a nota não sai
              </span>
              <span v-else-if="cert?.tom === 'atencao'" class="text-xs" :class="TOM_TEXTO.atencao">
                renove na NFE.io antes de vencer
              </span>
            </dd>

            <dt class="text-muted-foreground">Inscrição municipal</dt>
            <dd class="tabular-nums" :class="!n.inscricao_municipal && 'text-muted-foreground'">
              {{ n.inscricao_municipal || 'não informada' }}
            </dd>

            <dt class="text-muted-foreground">Município</dt>
            <dd :class="!n.municipio && 'text-muted-foreground'">
              {{ n.municipio ? `${n.municipio}${n.uf ? `/${n.uf}` : ''}` : 'não informado' }}
            </dd>

            <template v-if="n.sincronizado_em">
              <dt class="text-muted-foreground">Lido da NFE.io</dt>
              <dd class="text-xs text-muted-foreground">{{ quando(n.sincronizado_em) }}</dd>
            </template>
          </dl>
          <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-2.5 text-xs">
            <a
              :href="n.link"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-1 font-medium text-primary hover:underline"
            >Abrir na NFE.io<ExternalLink class="size-3" aria-hidden="true" /></a>
            <span class="text-muted-foreground">Para mudar certificado, regime ou ambiente, mude lá e clique em Atualizar.</span>
            <template v-if="canEdit">
              <Button type="button" variant="link" class="ml-auto h-auto p-0 text-xs" :disabled="!!nfeioAcao" @click="procurarPeloCnpj">
                procurar de novo pelo CNPJ
              </Button>
              <Button type="button" variant="link" class="h-auto p-0 text-xs" :disabled="!!nfeioAcao" @click="abrirColar">
                colar outro link
              </Button>
            </template>
          </div>
        </div>

        <!-- Colar o link (ligar ou trocar a ligação) -->
        <div v-if="colando && canEdit" class="space-y-1.5">
          <label for="nfse-nfeio-link" class="text-sm font-medium">Link da empresa na NFE.io</label>
          <div class="flex flex-wrap gap-2">
            <input
              id="nfse-nfeio-link"
              ref="campoColar"
              v-model="refColado"
              type="text"
              autocomplete="off"
              spellcheck="false"
              placeholder="https://app.nfe.io/companies/…"
              :class="[CLASSE_INPUT, 'min-w-0 flex-1 font-mono text-xs']"
              @keydown.enter.prevent="ligarPeloColado"
            />
            <Button type="button" size="sm" :disabled="!!nfeioAcao || !refColado.trim()" @click="ligarPeloColado">
              <Loader2
                v-if="nfeioAcao === 'link'"
                class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
                aria-hidden="true"
              />
              <Plug v-else class="mr-1.5 size-4" aria-hidden="true" />
              Ligar
            </Button>
            <Button type="button" size="sm" variant="ghost" :disabled="!!nfeioAcao" @click="colando = false">Cancelar</Button>
          </div>
          <p class="text-xs text-muted-foreground">
            Abra a empresa no site da NFE.io e copie o endereço da barra do navegador. O código dela ou o CNPJ também servem.
          </p>
        </div>

        <div v-if="nfeioErro" class="flex items-start gap-2 text-sm" role="alert">
          <XCircle class="mt-0.5 size-4 shrink-0" :class="TOM_TEXTO.perigo" aria-hidden="true" />
          <span class="min-w-0">{{ nfeioErro }}</span>
        </div>
      </NfseSecao>

      <form id="nfse-fiscal-form" novalidate @submit.prevent="enviar">
        <fieldset :disabled="!canEdit" class="m-0 min-w-0 space-y-5 border-0 p-0">
          <!-- 2. Serviço prestado -->
          <NfseSecao
            id="servico"
            titulo="Serviço prestado"
            descricao="Códigos que vão em todas as notas desta empresa (a nota fixa pode ter os seus). Na dúvida, pergunte à contabilidade."
            :icone="Briefcase"
          >
            <div class="grid gap-3 sm:grid-cols-3">
              <NfseCampo
                :class="CAMPO_EM_GRADE"
                rotulo="Código do serviço na prefeitura"
                dica="6303 = intermediação (o padrão do grupo)"
                para="nfse-f-city"
                :erro="erros.city_service_code"
              >
                <input
                  id="nfse-f-city"
                  :value="form.city_service_code ?? ''"
                  maxlength="20"
                  autocomplete="off"
                  placeholder="6303"
                  :class="[CLASSE_INPUT, 'font-mono tabular-nums', erros.city_service_code && CLASSE_ERRO]"
                  :aria-invalid="erros.city_service_code ? 'true' : undefined"
                  @input="digitar('city_service_code', $event)"
                />
              </NfseCampo>
              <NfseCampo
                :class="CAMPO_EM_GRADE"
                rotulo="Item da lista de serviços (LC 116)"
                dica="10.05 = intermediação de negócios"
                para="nfse-f-federal"
                :erro="erros.federal_service_code"
              >
                <input
                  id="nfse-f-federal"
                  :value="form.federal_service_code ?? ''"
                  inputmode="decimal"
                  maxlength="10"
                  autocomplete="off"
                  placeholder="10.05"
                  :class="[CLASSE_INPUT, 'font-mono tabular-nums', erros.federal_service_code && CLASSE_ERRO]"
                  :aria-invalid="erros.federal_service_code ? 'true' : undefined"
                  @input="digitar('federal_service_code', $event)"
                />
              </NfseCampo>
              <NfseCampo
                :class="CAMPO_EM_GRADE"
                rotulo="Código NBS"
                dica="Só se a prefeitura pedir. 9 números."
                para="nfse-f-nbs"
                opcional
                :erro="erros.c_nbs"
              >
                <input
                  id="nfse-f-nbs"
                  :value="form.c_nbs ?? ''"
                  inputmode="numeric"
                  maxlength="9"
                  autocomplete="off"
                  :class="[CLASSE_INPUT, 'font-mono tabular-nums', erros.c_nbs && CLASSE_ERRO]"
                  :aria-invalid="erros.c_nbs ? 'true' : undefined"
                  @input="digitar('c_nbs', $event)"
                />
              </NfseCampo>
            </div>

            <NfseCampo rotulo="Retenção de IR" :erro="erros.retencao_ir">
              <NfseOpcoes
                v-model="retencaoModel"
                :opcoes="OPCOES_RETENCAO"
                :colunas="3"
                :disabled="!canEdit"
                aria-label="retenção de IR"
              />
            </NfseCampo>

            <!-- % padrão das notas de percentual: só leitura, muda em Cadastros › Empresas -->
            <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 rounded-md border bg-muted/30 px-3 py-2.5">
              <div class="min-w-0 space-y-0.5">
                <p class="flex flex-wrap items-center gap-x-1.5 text-sm">
                  <Percent class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span class="text-muted-foreground">Porcentagem das notas de percentual:</span>
                  <span v-if="pctPositivo(p.percentual_servico)" class="font-medium tabular-nums">
                    {{ fmtPct(p.percentual_servico) }}
                  </span>
                  <span v-else class="text-muted-foreground">não definida</span>
                </p>
                <p class="text-xs text-muted-foreground">
                  {{
                    pctPositivo(p.percentual_servico)
                      ? 'Vale nas notas fixas de percentual desta empresa que não têm % própria.'
                      : 'Sem ela, cada nota fixa de percentual precisa da sua própria %.'
                  }}
                </p>
              </div>
              <!-- Quem só vê Cadastros › Empresas abre a ficha, mas não muda a %: o link é "ver". -->
              <div v-if="podeAbrirCadastroEmpresa" class="space-y-0.5 text-xs sm:text-right">
                <a
                  :href="`/companies/${p.company_id}`"
                  target="_blank"
                  rel="noopener"
                  class="inline-flex items-center gap-0.5 whitespace-nowrap text-primary hover:underline"
                >{{ podeEditarCadastroEmpresa ? 'editar' : 'ver' }} em Cadastros › Empresas<ExternalLink class="size-3" aria-hidden="true" /></a>
                <p v-if="!podeEditarCadastroEmpresa" class="text-muted-foreground">Para mudar, peça para quem tem acesso.</p>
              </div>
              <p v-else class="text-xs text-muted-foreground">Muda em Cadastros › Empresas: peça para quem tem acesso.</p>
            </div>
          </NfseSecao>
        </fieldset>
      </form>
    </div>

    <template #rodape>
      <span class="flex items-center gap-1.5 text-xs text-muted-foreground" aria-live="polite">
        <template v-if="!canEdit">
          <Eye class="size-3.5" aria-hidden="true" />
          Só leitura
        </template>
        <template v-else-if="sujo">
          <span class="size-2 rounded-full bg-amber-500" aria-hidden="true" />
          Alterações não salvas
        </template>
        <template v-else-if="p && !p.fiscal">
          <AlertTriangle class="size-3.5 text-amber-600 dark:text-amber-400" aria-hidden="true" />
          Serviço prestado ainda não salvo
        </template>
        <template v-else-if="salvoEm">
          <CheckCircle2 class="size-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
          Salvo às {{ fmtHora(salvoEm) }}
        </template>
      </span>
      <div class="flex items-center gap-2">
        <Button type="button" size="sm" variant="outline" @click="pedirFechar">Fechar</Button>
        <Button v-if="canEdit" type="submit" form="nfse-fiscal-form" size="sm" :disabled="!podeSalvar">
          <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <Save v-else class="mr-1.5 size-4" aria-hidden="true" />
          Salvar
        </Button>
      </div>
    </template>
  </NfseSheet>
</template>
