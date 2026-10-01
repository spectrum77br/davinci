<script setup lang="ts">
// "Integrar na NFE.io" / "Completar integração" (diálogo central), montado UMA
// vez pela página e aberto por tela.integrarEmpresa(p) — na aba Empresas e na
// gaveta da empresa.
//
// 01/10/2026 (Eduardo: "algumas empresas nossas não estão integradas no nfe.io,
// precisa integrar"): ao abrir, o servidor só LÊ (Receita, NFE.io e o
// certificado guardado em Cadastros › Empresas) e mostra o que vai acontecer.
// A pessoa confere os dados (regime, endereço, inscrição municipal) e confirma:
// o servidor cria a empresa na NFE.io (ou acha pelo CNPJ, sem criar outra),
// manda o certificado guardado, cadastra a inscrição municipal em TESTE e liga.
// A senha do certificado nunca vem para a tela. Neste servidor sem a trava
// liberada (NFSE_INTEGRAR_LIBERADO) dá para conferir, mas o botão fica travado.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Loader2, PlugZap, RotateCw } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  codigoErro, EMAIL_VALIDO, erroApi, fmtData, fmtDoc, NATUREZAS_INTEGRAR, REGIMES_INTEGRAR, soDigitos, useNfseApi,
  useNfseTela,
  type ChecklistItem, type IntegrarApi, type IntegrarForm, type IntegrarPrevia, type IntegrarResultado, type Msg,
  type PassoIntegracao, type Prestador,
} from '~/lib/nfse'

// Igual ao servidor (services/nfse/ambiente.py MSG_INTEGRAR_TRAVADO).
const MSG_TRAVADO =
  'Neste servidor a integração com a NFE.io está travada (só o servidor oficial do DaVinci cadastra empresas lá). Dá para conferir os dados, mas não para confirmar.'
const SAO_PAULO = '3550308'

type Estado = 'carregando' | 'erro' | 'form' | 'enviando' | 'resultado'

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

const CLASSE_INPUT =
  'h-9 w-full rounded-md border bg-background px-3 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:[color-scheme:dark]'
const CLASSE_ERRO = 'border-red-500 dark:border-red-400'

const aberto = ref(false)
const estado = ref<Estado>('carregando')
const prestador = ref<Prestador | null>(null)
const previa = ref<IntegrarPrevia | null>(null)
const form = ref<IntegrarForm>(vazio())
const tentou = ref(false)
const erroCarga = ref<string | null>(null)
const erroCargaStatus = ref<number | null>(null)
const erroGeral = ref<string | null>(null)
const errosServidor = ref<Record<string, string>>({})
const problemas = ref<Msg[]>([])
const passosErro = ref<PassoIntegracao[] | null>(null)
// A NFE.io não respondeu se criou: o botão vira "Tentar de novo" e refaz a prévia.
const incerta = ref(false)
// 01/10/2026 (revisão): o servidor pediu um dado que esta tela não mostra (o
// plano mudou desde a prévia): o botão vira "Conferir de novo" e refaz a prévia.
const reconferir = ref(false)
// O município no modo completar aparece quando o servidor pede e fica até
// reabrir (antes sumia no 1º dígito, porque o erro do servidor se apaga ao digitar).
const mostrarMunicipioCompletar = ref(false)
const resultado = ref<IntegrarResultado | null>(null)
const municipio = ref<string | null>(null) // "São Paulo/SP"
const municipioErro = ref<string | null>(null)

let resolver: ((v: boolean) => void) | null = null
let mudou = false
let geracao = 0

function vazio(): IntegrarForm {
  return {
    razao_social: null,
    nome_fantasia: null,
    regime: null,
    natureza_juridica: null,
    endereco: { logradouro: null, numero: null, complemento: null, bairro: null, cep: null, cmun_ibge: null },
    inscricao_municipal: null,
    email: null,
  }
}

// --- Abrir / fechar ----------------------------------------------------------------

function abrir(p: Prestador): Promise<boolean> {
  // Chamado de novo com uma aberta: a anterior vale como desistência.
  const anterior = resolver
  resolver = null
  anterior?.(false)
  prestador.value = p
  mudou = false
  aberto.value = true
  void carregar()
  return new Promise<boolean>((res) => {
    resolver = res
  })
}

function terminar(v: boolean) {
  aberto.value = false
  geracao++
  const r = resolver
  resolver = null
  r?.(v)
}

function aoMudar(v: boolean) {
  if (!v && estado.value !== 'enviando') terminar(mudou)
}

const exposto: IntegrarApi = {
  abrir,
  // Integrando: a senha da página que vencer espera acabar.
  ocupado: () => aberto.value && estado.value === 'enviando',
}
defineExpose(exposto)

// Desmontada (a página trancou ou saiu): quem abriu não fica esperando.
onBeforeUnmount(() => terminar(false))

function statusDe(e: any): number | null {
  const s = e?.status ?? e?.statusCode ?? e?.response?.status
  return typeof s === 'number' && s > 0 ? s : null
}

async function carregar() {
  const p = prestador.value
  if (!p) return
  const g = ++geracao
  estado.value = 'carregando'
  previa.value = null
  resultado.value = null
  tentou.value = false
  erroCarga.value = null
  erroCargaStatus.value = null
  erroGeral.value = null
  errosServidor.value = {}
  problemas.value = []
  passosErro.value = null
  incerta.value = false
  reconferir.value = false
  mostrarMunicipioCompletar.value = false
  municipioErro.value = null
  try {
    const r = await api<IntegrarPrevia>(`/api/nfse/prestadores/${p.company_id}/nfeio/integrar`)
    if (g !== geracao) return
    previa.value = r
    const e = r.dados.endereco
    form.value = {
      razao_social: r.dados.razao_social,
      nome_fantasia: r.dados.nome_fantasia,
      regime: r.dados.regime,
      natureza_juridica: r.dados.natureza_juridica,
      endereco: {
        logradouro: e.logradouro,
        numero: e.numero,
        complemento: e.complemento,
        bairro: e.bairro,
        cep: soDigitos(e.cep) || null,
        cmun_ibge: soDigitos(e.cmun_ibge) || null,
      },
      inscricao_municipal: r.dados.inscricao_municipal,
      email: r.dados.email,
    }
    municipio.value = e.municipio_nome ? `${e.municipio_nome}${e.uf ? `/${e.uf}` : ''}` : null
    estado.value = 'form'
  } catch (err) {
    if (g !== geracao) return
    erroCarga.value = erroApi(err)
    erroCargaStatus.value = statusDe(err)
    estado.value = 'erro'
    // Já integrada (outra pessoa, outra aba): a lista se atualiza.
    if (codigoErro(err) === 'ja_integrada') void tela.recarregar()
  }
}

// --- O que vai acontecer -------------------------------------------------------------

function passo(id: PassoIntegracao['id']): PassoIntegracao | undefined {
  return previa.value?.passos.find((x) => x.id === id)
}
const criar = computed(() => passo('criar_empresa')?.situacao === 'fazer')
const fazerInscricao = computed(() => passo('inscricao')?.situacao === 'fazer')
const fazerCertificado = computed(() => passo('certificado')?.situacao === 'fazer')
const completar = computed(() => previa.value?.modo === 'completar')

function okDe(s: PassoIntegracao['situacao']): boolean | null {
  if (s === 'feito' || s === 'ja_estava' || s === 'pular') return true
  if (s === 'falhou') return false
  return null
}
function itens(passos: PassoIntegracao[]): ChecklistItem[] {
  return passos.map((x) => ({
    chave: x.id,
    titulo: x.titulo,
    ok: okDe(x.situacao),
    detalhe: x.detalhe ?? (x.situacao === 'nao_feito' ? 'não chegou a rodar' : undefined),
  }))
}
// O detalhe de cada passo aparece inteiro embaixo do título (NfseChecklist quebra linha).
const passosMostrados = computed(() => itens(passosErro.value ?? previa.value?.passos ?? []))
const errosDosPassos = computed<Msg[]>(() =>
  (resultado.value?.passos ?? passosErro.value ?? []).flatMap((x) => x.erros ?? []),
)

const titulo = computed(() => {
  const nome = previa.value?.apelido ?? prestador.value?.apelido ?? 'a empresa'
  const modo = previa.value?.modo ?? (prestador.value?.integracao === 'incompleta' ? 'completar' : 'criar')
  return modo === 'completar' ? `Completar a integração da ${nome}` : `Integrar ${nome} na NFE.io`
})

function nomeAmbiente(a: string | null | undefined): string {
  return a === 'Production' ? 'Produção' : 'Teste'
}

const regimeDica = computed(
  () =>
    `${previa.value?.regime_motivo ?? 'Confira com a contabilidade'}. O regime decide a retenção de IR (Lucro Presumido retém 1,5%): confirme com a contabilidade.`,
)

const podeConfirmar = computed(() => !!previa.value && previa.value.liberado && !previa.value.bloqueios.length)

// 01/10/2026 (revisão): o rodapé diz só o que vai ser feito de verdade (no
// "completar" não cria empresa e só manda o certificado se esse passo for feito).
const textoRodape = computed(() => {
  const partes: string[] = []
  if (criar.value) partes.push('cadastra a empresa na NFE.io')
  if (fazerCertificado.value) partes.push('envia o certificado guardado')
  if (fazerInscricao.value) partes.push('cadastra a inscrição municipal em TESTE')
  const lista = partes.length > 1 ? `${partes.slice(0, -1).join(', ')} e ${partes[partes.length - 1]}` : partes[0]
  if (!lista) return 'Ao confirmar, o DaVinci liga a empresa e lê os dados da NFE.io. Nenhuma nota é emitida.'
  if (completar.value) {
    return `Ao confirmar, o DaVinci completa o que falta na NFE.io (${lista}), sem criar outra empresa. Nenhuma nota é emitida.`
  }
  return `Ao confirmar, o DaVinci ${lista}. Nenhuma nota é emitida.`
})

// --- Validação (as mesmas regras do servidor) -----------------------------------------

function normIm(v: string | null | undefined): string {
  return (v ?? '').replace(/[.\-/\s]/g, '').toUpperCase()
}

const errosLocais = computed<Record<string, string>>(() => {
  const e: Record<string, string> = {}
  const f = form.value
  const end = f.endereco
  if (criar.value) {
    const rs = (f.razao_social ?? '').trim()
    if (rs.length < 2 || rs.length > 60) {
      e.razao_social =
        'A razão social precisa ter de 2 a 60 letras (limite da NFE.io). Abrevie se for maior (ex.: LTDA, COM., SERV.).'
    }
    // O nome fantasia da Receita pode vir com mais de 60 letras: o maxlength do
    // campo não corta o que já veio preenchido.
    if ((f.nome_fantasia ?? '').trim().length > 60) {
      e.nome_fantasia = 'O nome fantasia pode ter até 60 letras (limite da NFE.io). Abrevie ou deixe vazio.'
    }
    if (!f.regime) e.regime = 'Escolha o regime tributário (confira com a contabilidade).'
    if ((end.logradouro ?? '').trim().length < 5) e['endereco.logradouro'] = 'Preencha a rua com o tipo (ex.: Rua Exemplo).'
    if (!(end.numero ?? '').trim()) e['endereco.numero'] = 'Preencha o número (ou S/N).'
    if ((end.bairro ?? '').trim().length < 2) e['endereco.bairro'] = 'Preencha o bairro.'
    if (soDigitos(end.cep).length !== 8) e['endereco.cep'] = 'O CEP tem 8 dígitos.'
    if (soDigitos(end.cmun_ibge).length !== 7 || municipioErro.value) {
      e['endereco.cmun_ibge'] = 'Código IBGE do município não encontrado (7 dígitos).'
    }
  }
  if (fazerInscricao.value) {
    const im = normIm(f.inscricao_municipal)
    if (!/^[0-9A-Z]{1,20}$/.test(im)) {
      e.inscricao_municipal = 'Preencha a inscrição municipal (em São Paulo, o CCM de 8 dígitos).'
    } else if (criar.value && soDigitos(end.cmun_ibge) === SAO_PAULO && !/^\d{8}$/.test(im)) {
      e.inscricao_municipal = 'Em São Paulo o CCM tem 8 dígitos.'
    }
    if (!f.natureza_juridica) e.natureza_juridica = 'Escolha a natureza jurídica.'
    const email = (f.email ?? '').trim()
    if (email && !EMAIL_VALIDO.test(email)) e.email = 'E-mail inválido.'
  }
  return e
})

function erro(campo: string): string | null {
  return errosServidor.value[campo] ?? (tentou.value ? errosLocais.value[campo] ?? null : null)
}

// Mexeu no formulário: o erro que o servidor deu some (a regra local continua).
watch(form, () => {
  if (Object.keys(errosServidor.value).length) errosServidor.value = {}
}, { deep: true })

// Município da Receita no modo completar: a NFE.io já tem a cidade; só se o
// servidor pedir é que o campo aparece na parte da inscrição (e fica).
const pedeMunicipioNaInscricao = computed(() => !criar.value && mostrarMunicipioCompletar.value)

const CAMPOS_CRIAR = new Set([
  'razao_social', 'nome_fantasia', 'regime',
  'endereco.logradouro', 'endereco.numero', 'endereco.complemento', 'endereco.bairro', 'endereco.cep',
  'endereco.cmun_ibge',
])
const CAMPOS_INSCRICAO = new Set(['inscricao_municipal', 'natureza_juridica', 'email', 'endereco.cmun_ibge'])
// O campo que o servidor marcou existe nesta tela?
function campoNaTela(k: string): boolean {
  return (criar.value && CAMPOS_CRIAR.has(k)) || (fazerInscricao.value && CAMPOS_INSCRICAO.has(k))
}

// --- Campos com máscara --------------------------------------------------------------

const cepMostrado = computed(() => {
  const d = soDigitos(form.value.endereco.cep).slice(0, 8)
  return d.length > 5 ? `${d.slice(0, 5)}-${d.slice(5)}` : d
})
function digitarCep(ev: Event) {
  const el = ev.target as HTMLInputElement
  const d = soDigitos(el.value).slice(0, 8)
  el.value = d.length > 5 ? `${d.slice(0, 5)}-${d.slice(5)}` : d
  form.value.endereco.cep = d || null
}

function digitarMunicipio(ev: Event) {
  const el = ev.target as HTMLInputElement
  const d = soDigitos(el.value).slice(0, 7)
  el.value = d
  form.value.endereco.cmun_ibge = d || null
  municipio.value = null
  municipioErro.value = null
}

async function conferirMunicipio() {
  const d = soDigitos(form.value.endereco.cmun_ibge)
  if (d.length !== 7) return
  try {
    const achados = await api<{ cmun_ibge: string; nome: string; uf: string }[]>(
      `/api/nfse/municipios?q=${encodeURIComponent(d)}`,
    )
    if (soDigitos(form.value.endereco.cmun_ibge) !== d) return
    const m = achados.find((x) => x.cmun_ibge === d)
    municipio.value = m ? `${m.nome}/${m.uf}` : null
    municipioErro.value = m ? null : 'código não encontrado'
  } catch {
    // Sem resposta: o servidor confere ao confirmar.
  }
}

// --- Confirmar ------------------------------------------------------------------------

function focarPrimeiroErro() {
  const primeiro = Object.keys(errosLocais.value)[0] ?? Object.keys(errosServidor.value)[0]
  if (!primeiro) return
  document.getElementById(`nfse-integrar-${primeiro.replace('.', '-')}`)?.focus()
}

async function confirmar() {
  const p = prestador.value
  const pv = previa.value
  if (!p || !pv || estado.value !== 'form') return
  if (incerta.value || reconferir.value) {
    // A NFE.io não disse se criou, ou o plano mudou: refaz a prévia (que procura
    // pelo CNPJ antes).
    await carregar()
    return
  }
  if (!podeConfirmar.value) return
  tentou.value = true
  if (Object.keys(errosLocais.value).length) {
    focarPrimeiroErro()
    return
  }
  const f = form.value
  // 01/10/2026 (revisão): empresa, regime e endereço só vão quando a empresa vai
  // ser CRIADA. No "completar" esses campos nem aparecem na tela (nem são
  // usados no servidor): mandar o nome da Receita com mais de 60 letras
  // travava sem ter onde corrigir. Do endereço, no completar, só o município
  // (quando a NFE.io não tem a cidade e o servidor pede).
  const cmun = soDigitos(f.endereco.cmun_ibge) || null
  const corpo: IntegrarForm = {
    razao_social: criar.value ? (f.razao_social ?? '').trim() || null : null,
    nome_fantasia: criar.value ? (f.nome_fantasia ?? '').trim() || null : null,
    regime: criar.value ? f.regime : null,
    natureza_juridica: f.natureza_juridica,
    inscricao_municipal: normIm(f.inscricao_municipal) || null,
    email: (f.email ?? '').trim() || null,
    endereco: criar.value
      ? { ...f.endereco, cep: soDigitos(f.endereco.cep) || null, cmun_ibge: cmun }
      : { logradouro: null, numero: null, complemento: null, bairro: null, cep: null, cmun_ibge: cmun },
    certificado_id: pv.certificado?.id ?? null,
  }
  estado.value = 'enviando'
  erroGeral.value = null
  errosServidor.value = {}
  problemas.value = []
  passosErro.value = null
  try {
    const r = await api<IntegrarResultado>(`/api/nfse/prestadores/${p.company_id}/nfeio/integrar`, {
      method: 'POST',
      body: corpo,
    })
    resultado.value = r
    mudou = true
    estado.value = 'resultado'
    if (r.ok) toasts.success(r.mensagem)
    else toasts.warning('Integração incompleta', r.mensagem)
    await tela.recarregar()
  } catch (err) {
    estado.value = 'form'
    erroGeral.value = erroApi(err)
    const d = (err as { data?: { detail?: any } })?.data?.detail
    if (d && typeof d === 'object' && !Array.isArray(d)) {
      if (d.campos && typeof d.campos === 'object') errosServidor.value = { ...d.campos }
      if (typeof d.campo === 'string' && d.campo) {
        errosServidor.value = { ...errosServidor.value, [d.campo]: erroGeral.value ?? '' }
      }
      if (Array.isArray(d.erros)) problemas.value = d.erros
      if (Array.isArray(d.passos)) passosErro.value = d.passos
      if (!criar.value && errosServidor.value['endereco.cmun_ibge']) mostrarMunicipioCompletar.value = true
      // Campo que esta tela não mostra (ex.: certificado_id, ou endereço quando a
      // prévia era "completar"): o texto vai no aviso e a prévia é refeita.
      const fora = Object.entries(errosServidor.value).filter(([k]) => !campoNaTela(k))
      if (fora.length) {
        const textos = fora.map(([, v]) => v).filter((v) => v && v !== erroGeral.value)
        erroGeral.value = [erroGeral.value, ...textos].filter(Boolean).join(' ')
        reconferir.value = true
      }
    }
    const code = codigoErro(err)
    if (code === 'nfeio_incerta') {
      incerta.value = true
      mudou = true
      void tela.recarregar()
    } else if (statusDe(err) === 409) {
      void tela.recarregar()
    }
    if (Object.keys(errosServidor.value).length) focarPrimeiroErro()
  }
}

async function abrirEmpresa() {
  const id = prestador.value?.company_id
  terminar(mudou)
  if (id) await tela.abrirEmpresa(id, 'nfeio')
}
</script>

<template>
  <NfseDialog
    :open="aberto"
    :titulo="titulo"
    descricao="Confira os dados. Nada vai para a NFE.io antes de você clicar no botão."
    tamanho="lg"
    camada="topo"
    :icone="PlugZap"
    :fechavel="estado !== 'enviando'"
    @update:open="aoMudar"
  >
    <!-- Lendo a Receita e a NFE.io -->
    <div v-if="estado === 'carregando'" class="flex items-center gap-2 py-6 text-sm text-muted-foreground" role="status">
      <Loader2 class="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      Consultando a Receita e a NFE.io…
    </div>

    <!-- Não deu para abrir -->
    <NfseAviso v-else-if="estado === 'erro'" tom="perigo" titulo="Não deu para abrir a integração">
      {{ erroCarga }}
    </NfseAviso>

    <!-- Integrando -->
    <div v-else-if="estado === 'enviando'" class="flex items-center gap-2 py-6 text-sm" role="status">
      <Loader2 class="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      Integrando na NFE.io… pode levar até 2 minutos. Não feche esta janela.
    </div>

    <!-- Resultado -->
    <div v-else-if="estado === 'resultado' && resultado" class="space-y-4">
      <NfseAviso :tom="resultado.ok ? 'sucesso' : 'atencao'">{{ resultado.mensagem }}</NfseAviso>
      <NfseChecklist :itens="itens(resultado.passos)" disabled />
      <NfseProblemas v-if="errosDosPassos.length" :erros="errosDosPassos" :acoes="false" />
    </div>

    <!-- Conferir e confirmar. 01/10/2026 (revisão): <div>, não <form> — Enter num
         campo não pode disparar a integração; só o clique no botão. -->
    <div v-else-if="previa" class="space-y-5">
      <NfseAviso v-if="previa.na_nfeio" tom="info">
        Essa empresa já existe na NFE.io como {{ previa.na_nfeio.nome }} ({{ nomeAmbiente(previa.na_nfeio.ambiente) }}).
        O DaVinci não cria outra: só completa o que falta e liga.
      </NfseAviso>

      <NfseAviso v-if="previa.bloqueios.length" tom="perigo" titulo="Não dá para integrar ainda">
        <ul class="list-disc space-y-0.5 pl-4">
          <li v-for="b in previa.bloqueios" :key="b">{{ b }}</li>
        </ul>
      </NfseAviso>
      <NfseAviso v-if="previa.avisos.length" tom="atencao">
        <ul class="list-disc space-y-0.5 pl-4">
          <li v-for="a in previa.avisos" :key="a">{{ a }}</li>
        </ul>
      </NfseAviso>
      <NfseAviso v-if="!previa.liberado" tom="atencao">{{ MSG_TRAVADO }}</NfseAviso>

      <!-- O que vai acontecer -->
      <section v-if="passosMostrados.length" class="space-y-1.5">
        <h3 class="text-sm font-semibold">O que vai acontecer</h3>
        <NfseChecklist :itens="passosMostrados" disabled />
      </section>

      <fieldset :disabled="!podeConfirmar" class="m-0 min-w-0 space-y-5 border-0 p-0">
        <!-- Empresa -->
        <section v-if="criar" class="space-y-3">
          <h3 class="text-sm font-semibold">Empresa</h3>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <NfseCampo rotulo="CNPJ">
              <p class="flex h-9 items-center text-sm tabular-nums">{{ fmtDoc(previa.cnpj) }}</p>
            </NfseCampo>
            <NfseCampo
              rotulo="Regime tributário"
              para="nfse-integrar-regime"
              obrigatorio
              :erro="erro('regime')"
              :dica="regimeDica"
            >
              <select
                id="nfse-integrar-regime"
                v-model="form.regime"
                :class="[CLASSE_INPUT, erro('regime') && CLASSE_ERRO]"
                :aria-invalid="erro('regime') ? 'true' : undefined"
              >
                <option :value="null" disabled>escolha…</option>
                <option v-for="(rotulo, valor) in REGIMES_INTEGRAR" :key="valor" :value="valor">{{ rotulo }}</option>
              </select>
            </NfseCampo>
            <NfseCampo
              class="sm:col-span-2"
              rotulo="Razão social"
              para="nfse-integrar-razao_social"
              obrigatorio
              :erro="erro('razao_social')"
              :dica="`${(form.razao_social ?? '').length}/60`"
            >
              <input
                id="nfse-integrar-razao_social"
                v-model="form.razao_social"
                type="text"
                autocomplete="off"
                :class="[CLASSE_INPUT, erro('razao_social') && CLASSE_ERRO]"
                :aria-invalid="erro('razao_social') ? 'true' : undefined"
              />
            </NfseCampo>
            <NfseCampo
              class="sm:col-span-2"
              rotulo="Nome fantasia"
              para="nfse-integrar-nome_fantasia"
              opcional
              :erro="erro('nome_fantasia')"
              :dica="`${(form.nome_fantasia ?? '').length}/60`"
            >
              <input
                id="nfse-integrar-nome_fantasia"
                v-model="form.nome_fantasia"
                type="text"
                autocomplete="off"
                :class="[CLASSE_INPUT, erro('nome_fantasia') && CLASSE_ERRO]"
                :aria-invalid="erro('nome_fantasia') ? 'true' : undefined"
              />
            </NfseCampo>
          </div>
        </section>

        <!-- Endereço -->
        <section v-if="criar" class="space-y-3">
          <h3 class="text-sm font-semibold">Endereço <span class="font-normal text-muted-foreground">(da Receita)</span></h3>
          <div class="grid grid-cols-6 gap-3">
            <NfseCampo
              class="col-span-6 sm:col-span-4"
              rotulo="Rua"
              para="nfse-integrar-endereco-logradouro"
              obrigatorio
              :erro="erro('endereco.logradouro')"
            >
              <input
                id="nfse-integrar-endereco-logradouro"
                v-model="form.endereco.logradouro"
                type="text"
                maxlength="120"
                autocomplete="off"
                placeholder="Rua Exemplo"
                :class="[CLASSE_INPUT, erro('endereco.logradouro') && CLASSE_ERRO]"
              />
            </NfseCampo>
            <NfseCampo
              class="col-span-3 sm:col-span-2"
              rotulo="Número"
              para="nfse-integrar-endereco-numero"
              obrigatorio
              :erro="erro('endereco.numero')"
            >
              <input
                id="nfse-integrar-endereco-numero"
                v-model="form.endereco.numero"
                type="text"
                maxlength="20"
                autocomplete="off"
                placeholder="S/N"
                :class="[CLASSE_INPUT, erro('endereco.numero') && CLASSE_ERRO]"
              />
            </NfseCampo>
            <NfseCampo
              class="col-span-3 sm:col-span-3"
              rotulo="Complemento"
              para="nfse-integrar-endereco-complemento"
              opcional
              :erro="erro('endereco.complemento')"
            >
              <input
                id="nfse-integrar-endereco-complemento"
                v-model="form.endereco.complemento"
                type="text"
                maxlength="60"
                autocomplete="off"
                :class="[CLASSE_INPUT, erro('endereco.complemento') && CLASSE_ERRO]"
              />
            </NfseCampo>
            <NfseCampo
              class="col-span-6 sm:col-span-3"
              rotulo="Bairro"
              para="nfse-integrar-endereco-bairro"
              obrigatorio
              :erro="erro('endereco.bairro')"
            >
              <input
                id="nfse-integrar-endereco-bairro"
                v-model="form.endereco.bairro"
                type="text"
                maxlength="60"
                autocomplete="off"
                :class="[CLASSE_INPUT, erro('endereco.bairro') && CLASSE_ERRO]"
              />
            </NfseCampo>
            <NfseCampo
              class="col-span-6 sm:col-span-2"
              rotulo="CEP"
              para="nfse-integrar-endereco-cep"
              obrigatorio
              :erro="erro('endereco.cep')"
            >
              <input
                id="nfse-integrar-endereco-cep"
                :value="cepMostrado"
                type="text"
                inputmode="numeric"
                autocomplete="off"
                placeholder="00000-000"
                :class="[CLASSE_INPUT, 'tabular-nums', erro('endereco.cep') && CLASSE_ERRO]"
                @input="digitarCep"
              />
            </NfseCampo>
            <NfseCampo
              class="col-span-6 sm:col-span-4"
              rotulo="Município (código IBGE)"
              para="nfse-integrar-endereco-cmun_ibge"
              obrigatorio
              :erro="erro('endereco.cmun_ibge')"
              dica="7 dígitos. Ex.: 3550308 = São Paulo."
            >
              <div class="flex items-center gap-2">
                <input
                  id="nfse-integrar-endereco-cmun_ibge"
                  :value="form.endereco.cmun_ibge ?? ''"
                  type="text"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="3550308"
                  :class="[CLASSE_INPUT, 'w-32 font-mono tabular-nums', erro('endereco.cmun_ibge') && CLASSE_ERRO]"
                  @input="digitarMunicipio"
                  @blur="conferirMunicipio"
                />
                <span v-if="municipio" class="text-sm">{{ municipio }}</span>
                <span v-else-if="municipioErro" class="text-xs text-red-600 dark:text-red-400">{{ municipioErro }}</span>
              </div>
            </NfseCampo>
          </div>
        </section>

        <!-- Inscrição municipal -->
        <section v-if="fazerInscricao" class="space-y-3">
          <h3 class="text-sm font-semibold">Inscrição municipal</h3>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <NfseCampo
              rotulo="Inscrição municipal"
              para="nfse-integrar-inscricao_municipal"
              obrigatorio
              :erro="erro('inscricao_municipal')"
              dica="Em São Paulo é o CCM, 8 dígitos. Peça à contabilidade se não souber."
            >
              <input
                id="nfse-integrar-inscricao_municipal"
                v-model="form.inscricao_municipal"
                type="text"
                maxlength="30"
                autocomplete="off"
                spellcheck="false"
                :class="[CLASSE_INPUT, 'tabular-nums', erro('inscricao_municipal') && CLASSE_ERRO]"
                :aria-invalid="erro('inscricao_municipal') ? 'true' : undefined"
              />
            </NfseCampo>
            <NfseCampo
              rotulo="Natureza jurídica"
              para="nfse-integrar-natureza_juridica"
              :obrigatorio="!previa.natureza_texto"
              :erro="erro('natureza_juridica')"
            >
              <p v-if="previa.natureza_texto && form.natureza_juridica" class="flex min-h-9 items-center text-sm">
                {{ previa.natureza_texto }} <span class="ml-1 text-muted-foreground">(da Receita)</span>
              </p>
              <select
                v-else
                id="nfse-integrar-natureza_juridica"
                v-model="form.natureza_juridica"
                :class="[CLASSE_INPUT, erro('natureza_juridica') && CLASSE_ERRO]"
              >
                <option :value="null" disabled>escolha…</option>
                <option v-for="(rotulo, valor) in NATUREZAS_INTEGRAR" :key="valor" :value="valor">{{ rotulo }}</option>
              </select>
            </NfseCampo>
            <NfseCampo
              class="sm:col-span-2"
              rotulo="E-mail"
              para="nfse-integrar-email"
              opcional
              :erro="erro('email')"
              dica="Vai no cadastro da NFE.io."
            >
              <input
                id="nfse-integrar-email"
                v-model="form.email"
                type="text"
                inputmode="email"
                autocomplete="off"
                spellcheck="false"
                placeholder="financeiro@empresa.com.br"
                :class="[CLASSE_INPUT, erro('email') && CLASSE_ERRO]"
              />
            </NfseCampo>
            <NfseCampo
              v-if="pedeMunicipioNaInscricao"
              class="sm:col-span-2"
              rotulo="Município (código IBGE)"
              para="nfse-integrar-endereco-cmun_ibge"
              obrigatorio
              :erro="erro('endereco.cmun_ibge')"
            >
              <input
                id="nfse-integrar-endereco-cmun_ibge"
                :value="form.endereco.cmun_ibge ?? ''"
                type="text"
                inputmode="numeric"
                autocomplete="off"
                placeholder="3550308"
                :class="[CLASSE_INPUT, 'w-32 font-mono tabular-nums', CLASSE_ERRO]"
                @input="digitarMunicipio"
                @blur="conferirMunicipio"
              />
            </NfseCampo>
          </div>
          <p class="text-xs text-muted-foreground">Entra em TESTE (notas simuladas).</p>
        </section>

        <!-- Certificado -->
        <section v-if="fazerCertificado && previa.certificado" class="space-y-1.5">
          <h3 class="text-sm font-semibold">Certificado digital</h3>
          <p class="flex flex-wrap items-center gap-2 text-sm">
            <span>
              {{ previa.certificado.filename }}
              <template v-if="previa.certificado.validade"> · vence {{ fmtData(previa.certificado.validade) }}</template>
            </span>
            <span :class="previa.certificado.validade_conferida ? 'pill-success' : 'pill-muted'">
              {{ previa.certificado.validade_conferida ? 'conferido no arquivo' : 'validade digitada no cadastro' }}
            </span>
          </p>
          <p class="text-xs text-muted-foreground">
            A senha fica no servidor: não aparece aqui nem vai para o histórico.
          </p>
        </section>
      </fieldset>

      <NfseProblemas v-if="problemas.length" :erros="problemas" :acoes="false" />
      <NfseAviso v-if="erroGeral" tom="perigo" titulo="Não deu para integrar">{{ erroGeral }}</NfseAviso>
    </div>

    <template #rodape>
      <template v-if="estado === 'erro'">
        <Button
          v-if="erroCargaStatus !== 409"
          type="button"
          variant="outline"
          size="sm"
          @click="carregar"
        >
          <RotateCw class="mr-1.5 size-4" aria-hidden="true" /> Tentar de novo
        </Button>
        <Button type="button" size="sm" @click="terminar(mudou)">Fechar</Button>
      </template>
      <template v-else-if="estado === 'resultado'">
        <Button type="button" variant="outline" size="sm" @click="abrirEmpresa">Abrir empresa</Button>
        <Button type="button" size="sm" @click="terminar(true)">Fechar</Button>
      </template>
      <template v-else-if="estado === 'form' || estado === 'enviando'">
        <p class="mr-auto hidden max-w-xs text-xs text-muted-foreground sm:block">{{ textoRodape }}</p>
        <Button type="button" variant="outline" size="sm" :disabled="estado === 'enviando'" @click="terminar(mudou)">
          Cancelar
        </Button>
        <Button
          type="button"
          size="sm"
          :disabled="estado === 'enviando' || (!incerta && !reconferir && !podeConfirmar)"
          @click="confirmar"
        >
          <Loader2 v-if="estado === 'enviando'" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <RotateCw v-else-if="incerta || reconferir" class="mr-1.5 size-4" aria-hidden="true" />
          <PlugZap v-else class="mr-1.5 size-4" aria-hidden="true" />
          {{ incerta ? 'Tentar de novo' : reconferir ? 'Conferir de novo' : completar ? 'Completar integração' : 'Integrar na NFE.io' }}
        </Button>
      </template>
    </template>
  </NfseDialog>
</template>
