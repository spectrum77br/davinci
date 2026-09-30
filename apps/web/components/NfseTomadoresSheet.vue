<script setup lang="ts">
// Formulário do tomador (gaveta à direita), montado UMA vez pela página.
// Qualquer lugar abre com tela.abrirTomador({ tomador?, preset? }) e recebe de
// volta o tomador salvo (ou null se a pessoa desistir). Serve também para o
// "+ cadastrar novo tomador" do NfseTomadorSelect, que abre por cima da nota
// fixa ou da nota avulsa sem sair delas.
//
// Tomador do grupo: CNPJ e razão social vêm do cadastro da empresa. De fora:
// CNPJ/CPF (com dígito conferido) e nome. Trocar o tipo mantém o que foi
// digitado dos dois lados, mas só o lado escolhido vai para a API.
import { computed, nextTick, ref, watch } from 'vue'
import { AlertTriangle, Building2, CheckCircle2, ChevronRight, Contact, IdCard, Loader2, UserRound } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  campoDoErro, codigoErro, docValido, enderecoTomador, erroApi, fmtDoc, prestadorPorId, soDigitos,
  tomadorParaForm, TOM_TEXTO, useNfseTela, type AbrirTomadorOpts, type Tomador, type TomadorApi, type TomadorForm,
  useNfseApi,
} from '~/lib/nfse'

type Campo =
  | 'company_id' | 'documento' | 'nome' | 'email' | 'fone'
  | 'cep' | 'cmun_ibge' | 'numero' | 'logradouro' | 'complemento' | 'bairro'

// Ordem em que os erros são mostrados (rola até o primeiro).
const ORDEM: Campo[] = [
  'company_id', 'documento', 'nome', 'email', 'fone', 'cep', 'cmun_ibge', 'logradouro', 'numero', 'complemento', 'bairro',
]
const CAMPOS_ENDERECO: Campo[] = ['cep', 'cmun_ibge', 'logradouro', 'numero', 'complemento', 'bairro']
// Códigos da API que o campoDoErro não liga a um campo.
const CAMPO_POR_CODIGO: Record<string, Campo> = { empresa_nao_encontrada: 'company_id' }
// "falta o bairro" / "faltam a rua e o número" (nomes vindos de enderecoTomador).
const ARTIGO: Record<string, string> = {
  'CEP': 'o CEP',
  'código do município': 'o código do município',
  'rua': 'a rua',
  'número': 'o número',
  'bairro': 'o bairro',
}

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

function vazio(): TomadorForm {
  return {
    tipo: 'grupo',
    company_id: null,
    documento: null,
    nome: null,
    email: null,
    fone: null,
    cep: null,
    cmun_ibge: null,
    logradouro: null,
    numero: null,
    complemento: null,
    bairro: null,
    ativo: true,
  }
}

const aberto = ref(false)
const form = ref<TomadorForm>(vazio())
const original = ref<Tomador | null>(null)
const abrirEndereco = ref(false)
const erros = ref<Partial<Record<Campo, string>>>({})
const erroGeral = ref<string | null>(null)
const tentouSalvar = ref(false)
const salvando = ref(false)
const inicial = ref('')
const sheet = ref<{ rolarPara(id: string): void } | null>(null)
let resolver: ((t: Tomador | null) => void) | null = null

const somenteLeitura = computed(() => !tela.canEdit.value)
const editando = computed(() => !!form.value.id)
const externo = computed(() => form.value.tipo === 'externo')

const titulo = computed(() => {
  if (somenteLeitura.value) return 'Tomador'
  return editando.value ? 'Editar tomador' : 'Novo tomador'
})

const subtitulo = computed(() => {
  const t = original.value
  if (t && (t.nome_nota || t.nome)) {
    const doc = t.documento_nota || t.documento
    return doc ? `${t.nome_nota || t.nome} · ${fmtDoc(doc)}` : (t.nome_nota || t.nome || '')
  }
  return 'Quem recebe a nota: uma empresa do grupo ou um cliente de fora.'
})

// --- Tipo -------------------------------------------------------------------------

const OPCOES_TIPO = [
  {
    valor: 'grupo',
    titulo: 'Empresa do grupo',
    descricao: 'CNPJ e razão social vêm do cadastro da empresa.',
    icone: Building2,
  },
  {
    valor: 'externo',
    titulo: 'Cliente de fora',
    descricao: 'Outra empresa ou uma pessoa, com CNPJ ou CPF.',
    icone: UserRound,
  },
]

const tipoModel = computed({
  get: () => form.value.tipo as string,
  set: (v: string | number | null) => {
    if (v === 'grupo' || v === 'externo') form.value.tipo = v
  },
})

// --- Identificação ----------------------------------------------------------------

const empresa = computed(() => prestadorPorId(tela.prestadores.value, form.value.company_id))

// Mesmo tomador já cadastrado (não impede: só avisa, para não virar duplicado).
const repetido = computed<Tomador | null>(() => {
  const f = form.value
  const outros = tela.tomadores.value.filter((t) => t.id !== f.id)
  if (f.tipo === 'grupo') {
    if (!f.company_id) return null
    return outros.find((t) => t.tipo === 'grupo' && t.company_id === f.company_id) ?? null
  }
  const d = soDigitos(f.documento)
  if (d.length !== 11 && d.length !== 14) return null
  return outros.find((t) => t.tipo === 'externo' && soDigitos(t.documento_nota || t.documento) === d) ?? null
})

const textoRepetido = computed(() => {
  const t = repetido.value
  if (!t) return ''
  const nome = t.nome_nota || t.nome || 'sem nome'
  const quem = form.value.tipo === 'grupo' ? 'para esta empresa' : 'com este CNPJ/CPF'
  return t.ativo
    ? `Já existe o tomador “${nome}” ${quem}. Confira se não é o mesmo antes de salvar.`
    : `Já existe o tomador “${nome}” ${quem}, desativado. Dá para reativar na lista de tomadores em vez de criar outro.`
})

// O NfseDocInput já explica número incompleto ou com dígito errado; o rótulo
// só repete o erro quando o campo está vazio ou quando a API discordou.
const erroDocumento = computed(() => {
  const e = erros.value.documento
  if (!e) return null
  const d = soDigitos(form.value.documento)
  return !d || docValido(d) ? e : null
})

// --- Contato e endereço (guardados só com os dígitos; a API também limpa) ------------

// (11) 91234-5678 / (11) 1234-5678, mostrando o que já foi digitado.
function mascaraFone(d: string): string {
  if (!d) return ''
  if (d.length <= 2) return `(${d}`
  const ddd = d.slice(0, 2)
  const resto = d.slice(2)
  const corte = resto.length > 8 ? 5 : 4
  return resto.length > corte ? `(${ddd}) ${resto.slice(0, corte)}-${resto.slice(corte)}` : `(${ddd}) ${resto}`
}

function mascaraCep(d: string): string {
  return d.length > 5 ? `${d.slice(0, 5)}-${d.slice(5, 8)}` : d
}

type CampoDigitos = 'fone' | 'cep' | 'cmun_ibge'
const LIMITE: Record<CampoDigitos, number> = { fone: 11, cep: 8, cmun_ibge: 7 }
const MASCARA: Record<CampoDigitos, (d: string) => string> = {
  fone: mascaraFone,
  cep: mascaraCep,
  cmun_ibge: (d) => d,
}

function mostrar(campo: CampoDigitos): string {
  return MASCARA[campo](soDigitos(form.value[campo]).slice(0, LIMITE[campo]))
}

function digitar(campo: CampoDigitos, ev: Event) {
  const el = ev.target as HTMLInputElement
  const d = soDigitos(el.value).slice(0, LIMITE[campo])
  el.value = MASCARA[campo](d)
  form.value[campo] = d || null
}

const endereco = computed(() => enderecoTomador(form.value))

const textoFaltando = computed(() => {
  const l = endereco.value.faltando.map((x) => ARTIGO[x] ?? x)
  const frase = l.length > 1 ? `${l.slice(0, -1).join(', ')} e ${l[l.length - 1]}` : (l[0] ?? '')
  return `${l.length > 1 ? 'faltam' : 'falta'} ${frase}`
})

function aoAlternarEndereco(ev: Event) {
  abrirEndereco.value = (ev.target as HTMLDetailsElement).open
}

// --- Abrir / fechar ---------------------------------------------------------------

// Mudar o tipo e voltar não conta como mudança.
function retrato(): string {
  return JSON.stringify(form.value)
}

const sujo = computed(() => aberto.value && !somenteLeitura.value && retrato() !== inicial.value)

function terminar(t: Tomador | null) {
  const r = resolver
  resolver = null
  r?.(t)
}

function abrir(o?: AbrirTomadorOpts): Promise<Tomador | null> {
  terminar(null)
  const base = o?.tomador ? tomadorParaForm(o.tomador) : vazio()
  form.value = { ...base, ...(o?.preset ?? {}) }
  original.value = o?.tomador ?? null
  abrirEndereco.value = enderecoTomador(form.value).preenchidos > 0
  erros.value = {}
  erroGeral.value = null
  tentouSalvar.value = false
  salvando.value = false
  inicial.value = retrato()
  aberto.value = true
  return new Promise<Tomador | null>((res) => {
    resolver = res
  })
}

function fechar(t: Tomador | null) {
  aberto.value = false
  terminar(t)
}

// A gaveta já perguntou "Sair sem salvar?" quando precisava.
function aoMudar(v: boolean) {
  if (!v) fechar(null)
}

async function cancelar() {
  if (sujo.value) {
    const ok = await tela.confirmar({
      titulo: 'Sair sem salvar?',
      texto: 'O que você mudou vai se perder.',
      tom: 'perigo',
      botao: 'Sair sem salvar',
      voltar: 'Continuar editando',
    })
    if (!ok) return
  }
  fechar(null)
}

defineExpose<TomadorApi>({ abrir })

// --- Validação e envio ------------------------------------------------------------

function validar(): Partial<Record<Campo, string>> {
  const f = form.value
  const e: Partial<Record<Campo, string>> = {}
  if (f.tipo === 'grupo') {
    if (!f.company_id) e.company_id = 'Escolha a empresa do grupo.'
  } else {
    const d = soDigitos(f.documento)
    if (!d) e.documento = 'Digite o CNPJ ou o CPF.'
    else if (!docValido(d)) e.documento = 'CNPJ/CPF inválido: confira os números.'
    if (!(f.nome ?? '').trim()) e.nome = 'Preencha o nome ou a razão social.'
  }
  const email = (f.email ?? '').trim()
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = 'Confira o e-mail: falta o @ ou o domínio.'
  const fone = soDigitos(f.fone)
  if (fone && fone.length < 10) e.fone = 'Telefone com DDD: 10 ou 11 números.'
  const cep = soDigitos(f.cep)
  if (cep && cep.length !== 8) e.cep = 'O CEP tem 8 dígitos.'
  const ibge = soDigitos(f.cmun_ibge)
  if (ibge && ibge.length !== 7) e.cmun_ibge = 'O código tem 7 dígitos.'
  return e
}

// Depois da 1ª tentativa, os erros acompanham o que a pessoa corrige.
watch(
  form,
  () => {
    if (tentouSalvar.value) erros.value = validar()
  },
  { deep: true },
)

function irParaCampo(campo: Campo) {
  if (CAMPOS_ENDERECO.includes(campo)) abrirEndereco.value = true
  nextTick(() => {
    const id = `nfse-tomador-campo-${campo}`
    sheet.value?.rolarPara(id)
    document
      .getElementById(id)
      ?.querySelector<HTMLElement>('input, textarea, button[role="combobox"]')
      ?.focus({ preventScroll: true })
  })
}

function limpo(v: string | null | undefined): string | null {
  const s = (v ?? '').trim()
  return s || null
}

async function salvar() {
  if (somenteLeitura.value || salvando.value) return
  tentouSalvar.value = true
  erroGeral.value = null
  const e = validar()
  erros.value = e
  const primeiro = ORDEM.find((c) => e[c])
  if (primeiro) {
    irParaCampo(primeiro)
    return
  }

  const f = form.value
  const grupo = f.tipo === 'grupo'
  const corpo = {
    tipo: f.tipo,
    company_id: grupo ? f.company_id : null,
    documento: grupo ? null : limpo(soDigitos(f.documento)),
    nome: grupo ? null : limpo(f.nome),
    email: limpo(f.email),
    fone: limpo(soDigitos(f.fone)),
    cep: limpo(soDigitos(f.cep)),
    cmun_ibge: limpo(soDigitos(f.cmun_ibge)),
    logradouro: limpo(f.logradouro),
    numero: limpo(f.numero),
    complemento: limpo(f.complemento),
    bairro: limpo(f.bairro),
    ativo: f.ativo,
  }

  salvando.value = true
  try {
    const salvo = f.id
      ? await api<Tomador>(`/api/nfse/tomadores/${f.id}`, { method: 'PATCH', body: corpo })
      : await api<Tomador>('/api/nfse/tomadores', { method: 'POST', body: corpo })
    toasts.success('Tomador salvo')
    await tela.recarregar()
    fechar(tela.tomadores.value.find((t) => t.id === salvo.id) ?? salvo)
  } catch (err) {
    const msg = erroApi(err)
    const campo = (campoDoErro(err) ?? CAMPO_POR_CODIGO[codigoErro(err) ?? '']) as Campo | undefined
    if (campo && ORDEM.includes(campo)) {
      erros.value = { ...erros.value, [campo]: msg }
      irParaCampo(campo)
    } else {
      erroGeral.value = msg
      nextTick(() => sheet.value?.rolarPara('nfse-tomador-erro'))
    }
  } finally {
    salvando.value = false
  }
}

const classeCampo =
  'h-9 w-full rounded-md border bg-background px-3 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'
const classeErro = 'border-red-500 dark:border-red-400'
</script>

<template>
  <NfseSheet
    ref="sheet"
    :open="aberto"
    :titulo="titulo"
    :subtitulo="subtitulo"
    largura="md"
    :sujo="sujo"
    @update:open="aoMudar"
  >
    <template #cabecalho-extra>
      <span v-if="editando && original && !original.ativo" class="pill-muted">desativado</span>
    </template>

    <form id="nfse-tomador-form" class="space-y-5" novalidate @submit.prevent="salvar">
      <NfseAviso v-if="erroGeral" id="nfse-tomador-erro" tom="perigo" titulo="Não deu para salvar o tomador">
        {{ erroGeral }}
      </NfseAviso>
      <NfseAviso v-if="somenteLeitura" tom="neutro" compacto>
        Você pode ver este tomador, mas não tem permissão para mudar.
      </NfseAviso>
      <NfseAviso v-else-if="editando && original && !original.ativo" tom="neutro" compacto>
        Este tomador está desativado: não aparece nas listas de escolha. Ligue “Ativo”, lá embaixo, para usar de novo.
      </NfseAviso>

      <fieldset :disabled="somenteLeitura || salvando" class="min-w-0 space-y-5">
        <!-- 1. Tipo -->
        <NfseSecao titulo="Quem recebe a nota" :icone="UserRound">
          <NfseOpcoes
            v-model="tipoModel"
            :opcoes="OPCOES_TIPO"
            :colunas="2"
            :disabled="somenteLeitura || salvando"
          />
        </NfseSecao>

        <!-- 2. Identificação -->
        <NfseSecao
          titulo="Identificação"
          :descricao="externo ? 'É o que vai escrito na nota.' : 'Vem do cadastro da empresa e vai escrito na nota.'"
          :icone="IdCard"
        >
          <template v-if="!externo">
            <NfseCampo
              id="nfse-tomador-campo-company_id"
              rotulo="Empresa"
              obrigatorio
              :erro="erros.company_id"
              para="nfse-tomador-empresa"
            >
              <NfseEmpresaSelect
                id="nfse-tomador-empresa"
                v-model="form.company_id"
                filtro="com_cnpj"
                :disabled="somenteLeitura || salvando"
                :invalido="!!erros.company_id"
              />
            </NfseCampo>
            <p v-if="empresa" class="rounded-md bg-muted/40 p-3 text-sm">
              <span class="text-muted-foreground">Na nota vai:{{ ' ' }}</span>
              <span class="font-medium">{{ empresa.razao_social || empresa.apelido }}</span>
              <span class="whitespace-nowrap text-muted-foreground tabular-nums">{{ ' · ' + fmtDoc(empresa.cnpj) }}</span>
            </p>
          </template>

          <div v-else class="grid gap-3 sm:grid-cols-2">
            <NfseCampo
              id="nfse-tomador-campo-documento"
              rotulo="CNPJ ou CPF"
              obrigatorio
              :erro="erroDocumento"
              para="nfse-tomador-documento"
            >
              <NfseDocInput
                id="nfse-tomador-documento"
                v-model="form.documento"
                :disabled="somenteLeitura || salvando"
                :invalido="!!erros.documento"
              />
            </NfseCampo>
            <NfseCampo
              id="nfse-tomador-campo-nome"
              rotulo="Nome ou razão social"
              obrigatorio
              :erro="erros.nome"
              para="nfse-tomador-nome"
            >
              <input
                id="nfse-tomador-nome"
                v-model="form.nome"
                type="text"
                maxlength="300"
                autocomplete="off"
                placeholder="como deve sair na nota"
                :class="[classeCampo, erros.nome && classeErro]"
                :aria-invalid="erros.nome ? 'true' : undefined"
              />
            </NfseCampo>
          </div>

          <NfseAviso v-if="repetido && !somenteLeitura" tom="atencao" compacto>
            {{ textoRepetido }}
          </NfseAviso>
        </NfseSecao>

        <!-- 3. Contato -->
        <NfseSecao titulo="Contato" descricao="opcional" :icone="Contact">
          <div class="grid gap-3 sm:grid-cols-2">
            <NfseCampo id="nfse-tomador-campo-email" rotulo="E-mail" :erro="erros.email" para="nfse-tomador-email">
              <input
                id="nfse-tomador-email"
                v-model="form.email"
                type="email"
                inputmode="email"
                maxlength="200"
                autocomplete="off"
                placeholder="financeiro@empresa.com.br"
                :class="[classeCampo, erros.email && classeErro]"
                :aria-invalid="erros.email ? 'true' : undefined"
              />
            </NfseCampo>
            <NfseCampo id="nfse-tomador-campo-fone" rotulo="Telefone" :erro="erros.fone" para="nfse-tomador-fone">
              <input
                id="nfse-tomador-fone"
                :value="mostrar('fone')"
                type="tel"
                inputmode="tel"
                autocomplete="off"
                placeholder="(11) 91234-5678"
                :class="[classeCampo, 'tabular-nums', erros.fone && classeErro]"
                :aria-invalid="erros.fone ? 'true' : undefined"
                @input="digitar('fone', $event)"
              />
            </NfseCampo>
          </div>
        </NfseSecao>

        <!-- 4. Endereço -->
        <details
          id="nfse-tomador-endereco"
          class="group rounded-lg border"
          :open="abrirEndereco"
          @toggle="aoAlternarEndereco"
        >
          <summary
            class="flex cursor-pointer list-none flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium hover:bg-muted/40 [&::-webkit-details-marker]:hidden"
          >
            <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
            Endereço
            <span class="font-normal text-muted-foreground">(opcional)</span>
            <span
              class="ml-auto tabular-nums"
              :class="
                endereco.situacao === 'completo'
                  ? 'pill-success'
                  : endereco.situacao === 'incompleto'
                    ? 'pill-warning'
                    : 'pill-muted'
              "
            >
              {{ endereco.preenchidos }} de 5 preenchidos
            </span>
          </summary>
          <div class="space-y-3 border-t px-3 py-3">
            <p class="text-xs text-muted-foreground">
              Só vai na nota se estiver completo: CEP, município, rua, número e bairro.
            </p>
            <div class="grid grid-cols-6 gap-3">
              <NfseCampo
                id="nfse-tomador-campo-cep"
                class="col-span-6 sm:col-span-2"
                rotulo="CEP"
                :erro="erros.cep"
                para="nfse-tomador-cep"
              >
                <input
                  id="nfse-tomador-cep"
                  :value="mostrar('cep')"
                  type="text"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="00000-000"
                  :class="[classeCampo, 'tabular-nums', erros.cep && classeErro]"
                  :aria-invalid="erros.cep ? 'true' : undefined"
                  @input="digitar('cep', $event)"
                />
              </NfseCampo>
              <NfseCampo
                id="nfse-tomador-campo-cmun_ibge"
                class="col-span-6 sm:col-span-4"
                rotulo="Código IBGE do município"
                dica="7 dígitos. Ex.: 3550308 = São Paulo."
                :erro="erros.cmun_ibge"
                para="nfse-tomador-cmun_ibge"
              >
                <input
                  id="nfse-tomador-cmun_ibge"
                  :value="mostrar('cmun_ibge')"
                  type="text"
                  inputmode="numeric"
                  autocomplete="off"
                  placeholder="3550308"
                  :class="[classeCampo, 'font-mono tabular-nums', erros.cmun_ibge && classeErro]"
                  :aria-invalid="erros.cmun_ibge ? 'true' : undefined"
                  @input="digitar('cmun_ibge', $event)"
                />
              </NfseCampo>
              <NfseCampo
                id="nfse-tomador-campo-logradouro"
                class="col-span-6 sm:col-span-4"
                rotulo="Rua"
                :erro="erros.logradouro"
                para="nfse-tomador-logradouro"
              >
                <input
                  id="nfse-tomador-logradouro"
                  v-model="form.logradouro"
                  type="text"
                  maxlength="255"
                  autocomplete="off"
                  placeholder="Av. Paulista"
                  :class="[classeCampo, erros.logradouro && classeErro]"
                />
              </NfseCampo>
              <NfseCampo
                id="nfse-tomador-campo-numero"
                class="col-span-6 sm:col-span-2"
                rotulo="Número"
                :erro="erros.numero"
                para="nfse-tomador-numero"
              >
                <input
                  id="nfse-tomador-numero"
                  v-model="form.numero"
                  type="text"
                  maxlength="60"
                  autocomplete="off"
                  placeholder="1000"
                  :class="[classeCampo, erros.numero && classeErro]"
                />
              </NfseCampo>
              <NfseCampo
                id="nfse-tomador-campo-complemento"
                class="col-span-6 sm:col-span-3"
                rotulo="Complemento"
                opcional
                :erro="erros.complemento"
                para="nfse-tomador-complemento"
              >
                <input
                  id="nfse-tomador-complemento"
                  v-model="form.complemento"
                  type="text"
                  maxlength="156"
                  autocomplete="off"
                  placeholder="sala 12"
                  :class="[classeCampo, erros.complemento && classeErro]"
                />
              </NfseCampo>
              <NfseCampo
                id="nfse-tomador-campo-bairro"
                class="col-span-6 sm:col-span-3"
                rotulo="Bairro"
                :erro="erros.bairro"
                para="nfse-tomador-bairro"
              >
                <input
                  id="nfse-tomador-bairro"
                  v-model="form.bairro"
                  type="text"
                  maxlength="60"
                  autocomplete="off"
                  placeholder="Bela Vista"
                  :class="[classeCampo, erros.bairro && classeErro]"
                />
              </NfseCampo>
            </div>

            <p
              v-if="endereco.situacao === 'completo'"
              class="flex items-center gap-1.5 text-xs"
              :class="TOM_TEXTO.sucesso"
            >
              <CheckCircle2 class="size-3.5 shrink-0" aria-hidden="true" />
              Endereço completo: vai na nota.
            </p>
            <p
              v-else-if="endereco.situacao === 'incompleto'"
              class="flex items-start gap-1.5 text-xs"
              :class="TOM_TEXTO.atencao"
            >
              <AlertTriangle class="mt-px size-3.5 shrink-0" aria-hidden="true" />
              <span>Endereço incompleto: {{ textoFaltando }}. Assim ele não vai na nota.</span>
            </p>
          </div>
        </details>
      </fieldset>
    </form>

    <template #rodape>
      <NfseSwitch
        v-if="editando && !somenteLeitura"
        v-model="form.ativo"
        rotulo="Ativo"
        :dica="form.ativo ? 'Aparece nas listas de escolha.' : 'Desativado: some das listas de escolha.'"
        :disabled="salvando"
      />
      <span v-else />
      <div class="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" :disabled="salvando" @click="cancelar">
          {{ somenteLeitura ? 'Fechar' : 'Cancelar' }}
        </Button>
        <Button v-if="!somenteLeitura" type="submit" form="nfse-tomador-form" size="sm" :disabled="salvando">
          <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
          Salvar tomador
        </Button>
      </div>
    </template>
  </NfseSheet>
</template>
