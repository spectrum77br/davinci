<script setup lang="ts">
// Contabilidade › Compra › "Fazer compra" (23/10/2026). Eduardo: "puxar todas as
// empresas; deixar opção de fazer pedido/compra; selecionar qual empresa vamos
// fazer a compra (as 4 que temos de NF); puxar valor do faturamento (opção para
// selecionar o mês) e deixar opção para selecionar a quantidade a ser comprada
// (valor por embalagem) [...] e com base no faturamento tbm".
//
// Quem compra = empresa do grupo; quem vende = conta Bling de NF ("as empresas
// são as mesmas que já utilizamos do bling"). A quantidade sai digitada ou por um
// % do faturamento do mês (÷ preço da embalagem, para baixo). Registrar grava a
// compra; a planilha do Bling (pedidos de venda) sai na hora ou depois, na lista.
// Modelo: NF-e 13917 (COMERCIAL LEMES → VITA, 450 × Embalagem a R$ 9,30, CFOP 5102).
import { computed, reactive, ref, watch } from 'vue'
import {
  Building2, Download, FileText, Info, Loader2, Package, Plus, Save, Trash2, TrendingUp, X,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useToasts } from '~/composables/useToasts'
import {
  erroApi, fmtBrl, fmtDoc, fmtMes, mesAtual, mesParaData, plural, somarMes, useNfseApi, useNfseTela,
} from '~/lib/nfse'
import {
  CAMPOS_ENDERECO, ORIGEM_ENDERECO, cfopSugerido, faltandoNoEndereco, fmtNcm, fmtQtd, lerQuantidade,
  nomeDaPlanilha, quantidadePeloPercentual, totalDaLinha,
  type Compra, type Comprador, type Endereco, type FaturamentoCompra, type Produto, type Vendedora,
} from '~/lib/compra'

const props = defineProps<{
  vendedoras: Vendedora[]
  produtos: Produto[]
  carregando: boolean
  editando: Compra | null
}>()
const emit = defineEmits<{
  (e: 'salva', c: Compra): void
  (e: 'parar-de-editar'): void
  (e: 'ir-produtos'): void
}>()

const tela = useNfseTela()
const { api, baixarArquivo } = useNfseApi()
const toasts = useToasts()

type Linha = {
  chave: number
  produto_id: string | null
  nome: string
  sku: string
  ncm: string
  unidade: string
  quantidade: string
  valor_unitario: string // decimal "9.30"
}

const ENDERECO_VAZIO: Endereco = {
  logradouro: '', numero: '', complemento: '', bairro: '', cep: '', municipio: '', uf: '',
}

function hojeIso(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' })
}

let proximaChave = 1
function linhaDoProduto(p: Produto | undefined): Linha {
  return {
    chave: proximaChave++,
    produto_id: p?.id ?? null,
    nome: p?.nome ?? '',
    sku: p?.sku ?? '',
    ncm: p?.ncm ?? '',
    unidade: p?.unidade ?? 'UN',
    quantidade: '',
    valor_unitario: p?.preco_padrao ?? '',
  }
}

// Mês do faturamento: o mês anterior (fechado) por padrão.
const form = reactive({
  company_id: null as string | null,
  vendedor_cnpj: '',
  mes: somarMes(mesAtual(), -1),
  data_pedido: hojeIso(),
  modo: 'quantidade' as 'quantidade' | 'percentual',
  percentual: '',
  cfop: '',
  ie: '',
  endereco: { ...ENDERECO_VAZIO } as Endereco,
  observacoes: '',
  linhas: [] as Linha[],
})

const comprador = ref<Comprador | null>(null)
const carregandoComprador = ref(false)
const erroComprador = ref<string | null>(null)
const enderecoOriginal = ref<string>('')
const fat = ref<FaturamentoCompra | null>(null)
const carregandoFat = ref(false)
const salvando = ref(false)
const tentou = ref(false)
let pularRecargaDoComprador = false // edição: o endereço/IE vêm da foto da compra

const hoje = mesAtual()

// --- quem vende -----------------------------------------------------------------------

const opcoesVendedoras = computed<Vendedora[]>(() => {
  const lista = [...props.vendedoras]
  const e = props.editando
  if (e && !lista.some((v) => v.cnpj === e.vendedor_cnpj)) {
    lista.push({ cnpj: e.vendedor_cnpj, nome: e.vendedor_nome, uf: e.vendedor_uf, municipio: null, notas_90d: 0, ultima: null })
  }
  return lista
})
const vendedora = computed(() => opcoesVendedoras.value.find((v) => v.cnpj === form.vendedor_cnpj) ?? null)

// --- quem compra ------------------------------------------------------------------------

function paraForm(e: Partial<Endereco> | null | undefined): Endereco {
  const out = { ...ENDERECO_VAZIO }
  for (const k of Object.keys(out) as (keyof Endereco)[]) out[k] = (e?.[k] ?? '') as string
  return out
}

let seqComprador = 0
async function carregarComprador(id: string) {
  // a flag vale só para ESTA carga (edição): presa, engoliria a próxima troca de empresa
  const pular = pularRecargaDoComprador
  pularRecargaDoComprador = false
  const seq = ++seqComprador
  carregandoComprador.value = true
  erroComprador.value = null
  try {
    const r = await api<Comprador>(`/api/nfse/compras/comprador/${id}`)
    if (seq !== seqComprador) return
    comprador.value = r
    if (pular) return
    form.ie = r.inscricao_estadual ?? ''
    form.endereco = paraForm(r.endereco)
    enderecoOriginal.value = JSON.stringify(form.endereco)
  } catch (e) {
    if (seq !== seqComprador) return
    comprador.value = null
    erroComprador.value = erroApi(e)
  } finally {
    if (seq === seqComprador) carregandoComprador.value = false
  }
}

watch(
  () => form.company_id,
  (id) => {
    comprador.value = null
    erroComprador.value = null
    // troca de empresa: IE e endereço da anterior não podem ir na compra desta
    if (!id || !pularRecargaDoComprador) {
      form.endereco = { ...ENDERECO_VAZIO }
      form.ie = ''
      enderecoOriginal.value = JSON.stringify(form.endereco)
    }
    if (!id) return
    carregarComprador(id)
  },
)

const enderecoMudou = computed(() => JSON.stringify(form.endereco) !== enderecoOriginal.value)
const faltaEndereco = computed(() => faltandoNoEndereco(form.endereco))

// --- faturamento do mês ---------------------------------------------------------------------

let seqFat = 0
watch(
  () => [form.company_id, form.mes] as const,
  async ([id, mes]) => {
    const seq = ++seqFat
    fat.value = null
    if (!id || !mes) return
    carregandoFat.value = true
    try {
      const r = await api<FaturamentoCompra>('/api/nfse/compras/faturamento', {
        query: { company_id: id, competencia: mesParaData(mes) },
      })
      if (seq === seqFat) fat.value = r
    } catch {
      if (seq === seqFat) fat.value = null
    } finally {
      if (seq === seqFat) carregandoFat.value = false
    }
  },
)

const temFaturamento = computed(() => !!fat.value && fat.value.tem_loja && Number(fat.value.valor) > 0)

// --- itens ---------------------------------------------------------------------------------

// Sempre uma linha; começa com a Embalagem (ou o 1º produto) assim que o
// catálogo chega — a linha em branco de antes do catálogo é trocada.
function garantirLinha() {
  const emb = props.produtos.find((p) => p.nome.toLowerCase() === 'embalagem') ?? props.produtos[0]
  if (!form.linhas.length) {
    form.linhas.push(linhaDoProduto(emb))
    return
  }
  const l = form.linhas[0]!
  if (form.linhas.length === 1 && emb && !l.produto_id && !l.nome && !l.quantidade) {
    Object.assign(l, { ...linhaDoProduto(emb), chave: l.chave })
  }
}
watch(() => props.produtos, () => { if (!props.editando) garantirLinha() }, { immediate: true })

function escolherProduto(l: Linha, id: string) {
  const p = props.produtos.find((x) => x.id === id)
  if (!p) return
  Object.assign(l, { produto_id: p.id, nome: p.nome, sku: p.sku, ncm: p.ncm, unidade: p.unidade })
  if (p.preco_padrao) l.valor_unitario = p.preco_padrao
}

function adicionarLinha() {
  form.linhas.push(linhaDoProduto(undefined))
}

function tirarLinha(i: number) {
  form.linhas.splice(i, 1)
  garantirLinha()
}

const totalGeral = computed(() =>
  form.linhas.reduce((s, l) => s + totalDaLinha(lerQuantidade(l.quantidade) ?? 0, l.valor_unitario || 0), 0),
)

// % do faturamento: a quantidade da 1ª linha sai da conta (e ainda dá para mexer).
const qtdPeloPct = computed(() => {
  const l = form.linhas[0]
  if (form.modo !== 'percentual' || !l || !temFaturamento.value) return null
  const pct = Number(String(form.percentual).replace(',', '.'))
  if (!Number.isFinite(pct) || pct <= 0) return null
  return quantidadePeloPercentual(fat.value!.valor, pct, l.valor_unitario || 0)
})
let pularPct = false // edição: a quantidade gravada não é trocada quando o faturamento chega
watch(qtdPeloPct, (q) => {
  if (pularPct) {
    if (q != null) pularPct = false
    return
  }
  if (q != null && form.linhas[0]) form.linhas[0].quantidade = q > 0 ? String(q) : ''
})
const alvoPct = computed(() => {
  if (!temFaturamento.value) return null
  const pct = Number(String(form.percentual).replace(',', '.'))
  if (!Number.isFinite(pct) || pct <= 0) return null
  return (Number(fat.value!.valor) * pct) / 100
})

const OPCOES_MODO = [
  { id: 'quantidade', rotulo: 'Digitar a quantidade' },
  { id: 'percentual', rotulo: '% do faturamento' },
]
const modoModel = computed({
  get: () => form.modo as string,
  set: (v: string) => {
    form.modo = v === 'percentual' ? 'percentual' : 'quantidade'
  },
})

// --- CFOP ------------------------------------------------------------------------------------

const cfopDoProduto = computed(() => {
  const id = form.linhas[0]?.produto_id
  return props.produtos.find((p) => p.id === id)?.cfop ?? null
})
const cfopAuto = computed(() => cfopDoProduto.value ?? cfopSugerido(vendedora.value?.uf, form.endereco.uf))
const cfopFinal = computed(() => (form.cfop || '').replace(/\D/g, '') || cfopAuto.value)
const dicaCfop = computed(() => {
  if (cfopDoProduto.value) return `${cfopDoProduto.value} vem do cadastro do produto.`
  const uv = vendedora.value?.uf
  const uc = form.endereco.uf
  if (!uv || !uc) return '5102 dentro do estado, 6102 para outro estado.'
  return uv.toUpperCase() === uc.toUpperCase()
    ? `${uv} → ${uc}: mesmo estado, 5102.`
    : `${uv} → ${uc}: outro estado, 6102.`
})

// --- conferência antes de salvar -----------------------------------------------------------

function erroDaLinha(l: Linha): string | null {
  if (!l.nome.trim() || !l.sku.trim()) return 'escolha o produto'
  if (l.ncm.replace(/\D/g, '').length !== 8) return 'NCM com 8 números'
  if (!lerQuantidade(l.quantidade)) return 'quantidade inteira maior que zero'
  if (!(Number(l.valor_unitario) > 0)) return 'preço por unidade'
  return null
}

const erros = computed(() => {
  const e: Record<string, string> = {}
  if (!form.company_id) e.company = 'Escolha a empresa que compra.'
  if (!form.vendedor_cnpj) e.vendedora = 'Escolha de qual empresa vai comprar.'
  if (!form.mes) e.mes = 'Escolha o mês do faturamento.'
  if (form.modo === 'percentual') {
    if (!temFaturamento.value) e.pct = 'Sem faturamento nesse mês: use "Digitar a quantidade".'
    else if (!(Number(String(form.percentual).replace(',', '.')) > 0)) e.pct = 'Digite o % (ex.: 1,5).'
  }
  const cf = (form.cfop || '').replace(/\D/g, '')
  if (cf && !/^[1-7]\d{3}$/.test(cf)) e.cfop = 'O CFOP tem 4 números (ex.: 5102).'
  form.linhas.forEach((l, i) => {
    const x = erroDaLinha(l)
    if (x) e[`linha${i}`] = x
  })
  if (form.company_id && (carregandoComprador.value || (!comprador.value && !erroComprador.value))) {
    e.comprador = 'Espere carregar os dados da empresa que compra.'
  } else if (form.company_id && erroComprador.value) {
    e.comprador = 'Não carregaram os dados da empresa que compra: escolha de novo.'
  }
  if (form.company_id && comprador.value && faltaEndereco.value.length) {
    e.endereco = `Falta no endereço: ${faltaEndereco.value.join(', ').toLowerCase()}.`
  }
  if (!form.data_pedido) e.data = 'Escolha a data do pedido.'
  return e
})
const temErro = computed(() => Object.keys(erros.value).length > 0)
const mostrar = (k: string) => (tentou.value ? erros.value[k] : undefined)

// --- salvar --------------------------------------------------------------------------------

function corpo() {
  return {
    company_id: form.company_id,
    vendedor_cnpj: form.vendedor_cnpj,
    competencia: mesParaData(form.mes),
    data_pedido: form.data_pedido || null,
    modo: form.modo,
    percentual: form.modo === 'percentual' ? String(form.percentual).trim() : null,
    cfop: cfopFinal.value,
    inscricao_estadual: form.ie.trim() || null,
    endereco: enderecoMudou.value ? form.endereco : null,
    observacoes: form.observacoes.trim() || null,
    itens: form.linhas.map((l) => ({
      produto_id: l.produto_id,
      nome: l.nome.trim(),
      sku: l.sku.trim(),
      ncm: l.ncm,
      unidade: l.unidade || 'UN',
      quantidade: lerQuantidade(l.quantidade),
      valor_unitario: l.valor_unitario,
    })),
  }
}

function limpar() {
  pularPct = false
  pularRecargaDoComprador = false
  form.modo = 'quantidade'
  form.percentual = ''
  form.cfop = ''
  form.observacoes = ''
  form.data_pedido = hojeIso()
  form.linhas = []
  garantirLinha()
  tentou.value = false
}

async function salvar(baixar: boolean) {
  tentou.value = true
  if (temErro.value || salvando.value) {
    if (temErro.value) toasts.warning('Falta preencher', Object.values(erros.value))
    return
  }
  salvando.value = true
  const e = props.editando
  try {
    const c = await api<Compra>(e ? `/api/nfse/compras/${e.id}` : '/api/nfse/compras', {
      method: e ? 'PUT' : 'POST',
      body: corpo(),
    })
    toasts.success(
      e ? `Compra nº ${c.numero} alterada` : `Compra nº ${c.numero} registrada`,
      [`${c.comprador.apelido || c.comprador.razao_social} compra de ${c.vendedor_nome}: ${fmtBrl(c.total)} (CFOP ${c.cfop}).`],
    )
    if (baixar) await baixarArquivo(`/api/nfse/compras/${c.id}/planilha`, nomeDaPlanilha([c]))
    limpar()
    emit('salva', c)
  } catch (err) {
    toasts.error(e ? 'Não deu para salvar a compra' : 'Não deu para registrar a compra', erroApi(err))
  } finally {
    salvando.value = false
  }
}

function cancelarEdicao() {
  limpar()
  emit('parar-de-editar')
}

// --- editar: o formulário volta com a compra --------------------------------------------------

watch(
  () => props.editando,
  (c) => {
    if (!c) return
    pularRecargaDoComprador = form.company_id !== c.company_id
    pularPct = c.modo === 'percentual'
    form.company_id = c.company_id
    form.vendedor_cnpj = c.vendedor_cnpj
    form.mes = c.competencia.slice(0, 7)
    form.data_pedido = c.data_pedido
    form.modo = c.modo
    form.percentual = c.percentual ? String(Number(c.percentual)).replace('.', ',') : ''
    // CFOP que era o automático volta a seguir vendedora/UF/produto; só o digitado diferente fica fixo
    const cfopProduto = props.produtos.find((p) => p.id === c.itens[0]?.produto_id)?.cfop ?? null
    const autoGravado = cfopProduto ?? cfopSugerido(c.vendedor_uf, c.comprador.endereco?.uf)
    form.cfop = c.cfop === autoGravado ? '' : c.cfop
    form.ie = c.comprador.inscricao_estadual ?? ''
    form.endereco = paraForm(c.comprador.endereco)
    // a foto da compra conta como "o que veio": só manda o endereço se mexer de novo
    enderecoOriginal.value = JSON.stringify(form.endereco)
    form.observacoes = c.observacoes ?? ''
    form.linhas = c.itens.map((i) => ({
      chave: proximaChave++,
      produto_id: i.produto_id,
      nome: i.nome,
      sku: i.sku,
      ncm: i.ncm,
      unidade: i.unidade,
      quantidade: String(i.quantidade),
      valor_unitario: String(i.valor_unitario),
    }))
    tentou.value = false
  },
  { immediate: true },
)

const podeEditar = computed(() => tela.canEdit.value)
</script>

<template>
  <div class="space-y-5">
    <NfseAviso v-if="!podeEditar" tom="info" compacto>
      Você pode ver, mas não registrar compras. Peça acesso de edição em Contabilidade.
    </NfseAviso>

    <NfseAviso v-if="editando" tom="atencao" :titulo="`Editando a compra nº ${editando.numero}`">
      Ao salvar, a compra fica com o que está aqui. Se a planilha já foi importada no Bling, importe a nova
      ou ajuste o pedido lá também.
      <template #acoes>
        <Button size="sm" variant="outline" class="h-8 px-2.5 text-foreground" @click="cancelarEdicao">
          <X class="mr-1.5 size-4" aria-hidden="true" />
          cancelar edição
        </Button>
      </template>
    </NfseAviso>

    <!-- 1. Quem compra e de quem -->
    <NfseSecao titulo="Quem compra e de quem" :icone="Building2">
      <div class="grid gap-3 sm:grid-cols-2">
        <NfseCampo rotulo="Empresa que compra" obrigatorio para="compra-empresa" :erro="mostrar('company')">
          <NfseEmpresaSelect
            id="compra-empresa"
            v-model="form.company_id"
            filtro="com_cnpj"
            :disabled="!podeEditar"
            :invalido="!!mostrar('company')"
            placeholder="escolha a empresa do grupo…"
          />
        </NfseCampo>
        <NfseCampo
          rotulo="Comprar de (empresa de NF do Bling)"
          obrigatorio
          para="compra-vendedora"
          :erro="mostrar('vendedora')"
          dica="As contas Bling que emitem NF (as que tiveram nota nos últimos 90 dias). A planilha é importada no Bling dela."
        >
          <select
            id="compra-vendedora"
            v-model="form.vendedor_cnpj"
            :disabled="!podeEditar || carregando"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            :class="mostrar('vendedora') && 'border-red-500 dark:border-red-400'"
            :aria-invalid="mostrar('vendedora') ? 'true' : undefined"
          >
            <option value="" disabled>{{ carregando ? 'carregando…' : 'escolha de quem comprar…' }}</option>
            <option v-for="v in opcoesVendedoras" :key="v.cnpj" :value="v.cnpj">
              {{ v.nome }} · {{ fmtDoc(v.cnpj) }}{{ v.uf ? ` · ${v.uf}` : '' }}
            </option>
          </select>
          <p v-if="!carregando && !opcoesVendedoras.length" class="text-xs text-amber-600 dark:text-amber-400">
            Nenhuma conta Bling de NF teve nota nos últimos 90 dias.
          </p>
        </NfseCampo>
      </div>
    </NfseSecao>

    <!-- 2. Faturamento do mês -->
    <NfseSecao titulo="Faturamento do mês" :icone="TrendingUp">
      <div class="grid gap-3 sm:grid-cols-[auto_1fr] sm:items-start">
        <NfseCampo rotulo="Mês" :erro="mostrar('mes')">
          <NfseMesPicker v-model="form.mes" nome="Mês do faturamento" :max="hoje" :disabled="!podeEditar" />
        </NfseCampo>
        <div class="rounded-lg border bg-muted/20 px-4 py-3 text-sm" aria-live="polite">
          <p v-if="!form.company_id" class="text-muted-foreground">Escolha a empresa que compra para ver o faturamento.</p>
          <p v-else-if="carregandoFat" class="flex items-center gap-2 text-muted-foreground">
            <Loader2 class="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            somando as vendas de {{ fmtMes(form.mes) }}…
          </p>
          <template v-else-if="fat && fat.tem_loja">
            <p class="text-xs text-muted-foreground">Faturamento de {{ fmtMes(form.mes) }}</p>
            <p class="text-xl font-semibold tabular-nums">{{ fmtBrl(fat.valor) }}</p>
            <p class="text-xs text-muted-foreground">
              {{ plural(fat.pedidos, 'pedido', 'pedidos') }} em {{ plural(fat.lojas.length, 'loja', 'lojas') }}
              (as lojas com o CNPJ da empresa, mesma conta da aba Faturamento)
            </p>
          </template>
          <p v-else-if="fat" class="text-amber-600 dark:text-amber-400">
            Essa empresa não tem loja com o CNPJ dela em Cadastros › Lojas: o faturamento não aparece. Use a quantidade direto.
          </p>
          <p v-else class="text-muted-foreground">Não deu para ler o faturamento agora.</p>
        </div>
      </div>
    </NfseSecao>

    <!-- 3. O que comprar -->
    <NfseSecao titulo="O que comprar" :icone="Package">
      <template #acoes>
        <Button size="sm" variant="ghost" class="h-8 px-2.5" @click="emit('ir-produtos')">produtos cadastrados</Button>
      </template>
      <div class="grid gap-3 sm:grid-cols-[auto_1fr] sm:items-end">
        <NfseCampo rotulo="Como sai a quantidade">
          <NfseSegmentado v-model="modoModel" :opcoes="OPCOES_MODO" tamanho="sm" />
        </NfseCampo>
        <NfseCampo
          v-if="form.modo === 'percentual'"
          rotulo="% do faturamento"
          obrigatorio
          para="compra-pct"
          :erro="mostrar('pct')"
          dica="Quantas unidades da 1ª linha cabem nesse % do faturamento, ao preço por unidade (arredonda para baixo)."
        >
          <div class="flex flex-wrap items-center gap-3">
            <div class="relative inline-flex w-fit">
              <input
                id="compra-pct"
                v-model="form.percentual"
                type="text"
                inputmode="decimal"
                autocomplete="off"
                maxlength="10"
                placeholder="1,5"
                :disabled="!podeEditar"
                class="h-9 w-28 rounded-md border bg-background pl-3 pr-8 text-right text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :class="mostrar('pct') && 'border-red-500 dark:border-red-400'"
              />
              <span class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">%</span>
            </div>
            <p v-if="alvoPct != null && qtdPeloPct != null" class="text-xs text-muted-foreground">
              {{ fmtBrl(alvoPct) }} ÷ {{ fmtBrl(form.linhas[0]?.valor_unitario || 0) }} =
              <span class="font-medium text-foreground">{{ fmtQtd(qtdPeloPct) }} un.</span>
            </p>
          </div>
        </NfseCampo>
      </div>

      <div class="table-card tabela-nfse">
        <div class="relative overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr>
                <th class="min-w-[200px]">Produto</th>
                <th class="hidden xl:table-cell">SKU · NCM</th>
                <th class="!text-right">Quantidade</th>
                <th class="!text-right">Preço por unidade</th>
                <th class="!text-right">Total</th>
                <th class="col-acoes w-px"><span class="sr-only">Ações</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(l, i) in form.linhas" :key="l.chave">
                <td>
                  <select
                    :value="l.produto_id ?? ''"
                    :disabled="!podeEditar"
                    :aria-label="`produto da linha ${i + 1}`"
                    class="h-9 w-full rounded-md border bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    :class="mostrar(`linha${i}`) === 'escolha o produto' && 'border-red-500 dark:border-red-400'"
                    @change="escolherProduto(l, ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="" disabled>{{ l.produto_id ? l.nome : (l.nome || 'escolha o produto…') }}</option>
                    <option v-for="p in produtos" :key="p.id" :value="p.id">{{ p.nome }}</option>
                  </select>
                  <p class="mt-1 text-xs text-muted-foreground xl:hidden">{{ l.sku || '—' }} · NCM {{ fmtNcm(l.ncm) || '—' }}</p>
                  <p v-if="mostrar(`linha${i}`)" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ mostrar(`linha${i}`) }}</p>
                </td>
                <td class="hidden whitespace-nowrap text-xs text-muted-foreground xl:table-cell">
                  {{ l.sku || '—' }} · {{ fmtNcm(l.ncm) || '—' }}
                </td>
                <td class="!text-right">
                  <input
                    v-model="l.quantidade"
                    type="text"
                    inputmode="numeric"
                    autocomplete="off"
                    maxlength="10"
                    placeholder="450"
                    :disabled="!podeEditar"
                    :aria-label="`quantidade da linha ${i + 1}`"
                    class="h-9 w-28 rounded-md border bg-background px-3 text-right text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                </td>
                <td class="!text-right">
                  <div class="ml-auto w-36">
                    <NfseValorInput v-model="l.valor_unitario" compacto :disabled="!podeEditar" rotulo="preço" />
                  </div>
                </td>
                <td class="whitespace-nowrap !text-right font-medium tabular-nums">
                  {{ fmtBrl(totalDaLinha(lerQuantidade(l.quantidade) ?? 0, l.valor_unitario || 0)) }}
                </td>
                <td class="col-acoes">
                  <Button
                    v-if="podeEditar && form.linhas.length > 1"
                    size="icon"
                    variant="ghost"
                    class="size-8"
                    :aria-label="`tirar a linha ${i + 1}`"
                    @click="tirarLinha(i)"
                  >
                    <Trash2 class="size-4" aria-hidden="true" />
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <Button v-if="podeEditar" size="sm" variant="outline" @click="adicionarLinha">
          <Plus class="mr-1.5 size-4" aria-hidden="true" />
          outro produto
        </Button>
        <p class="ml-auto text-sm">
          Total da compra: <span class="text-lg font-semibold tabular-nums">{{ fmtBrl(totalGeral) }}</span>
        </p>
      </div>
    </NfseSecao>

    <!-- 4. Dados da nota -->
    <NfseSecao titulo="Dados da nota" :icone="FileText">
      <div class="grid gap-3 sm:grid-cols-3">
        <NfseCampo rotulo="CFOP" para="compra-cfop" :erro="mostrar('cfop')" :dica="dicaCfop">
          <input
            id="compra-cfop"
            v-model="form.cfop"
            type="text"
            inputmode="numeric"
            maxlength="5"
            :placeholder="cfopAuto"
            :disabled="!podeEditar"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </NfseCampo>
        <NfseCampo rotulo="Data do pedido" obrigatorio para="compra-data" :erro="mostrar('data')">
          <input
            id="compra-data"
            v-model="form.data_pedido"
            type="date"
            :disabled="!podeEditar"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:[color-scheme:dark]"
          />
        </NfseCampo>
        <NfseCampo
          rotulo="Inscrição estadual de quem compra"
          para="compra-ie"
          dica="PJ contribuinte sem IE é recusada na SEFAZ (rejeição 232). ISENTO também vale."
        >
          <input
            id="compra-ie"
            v-model="form.ie"
            type="text"
            autocomplete="off"
            maxlength="20"
            :disabled="!podeEditar"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </NfseCampo>
      </div>

      <div v-if="form.company_id" class="space-y-2">
        <div class="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span class="font-medium">Endereço de quem compra</span>
          <span v-if="carregandoComprador" class="text-xs text-muted-foreground">carregando…</span>
          <span v-else-if="enderecoMudou" class="text-xs text-amber-600 dark:text-amber-400">alterado aqui (vale só para esta compra)</span>
          <span v-else-if="comprador?.endereco_origem" class="text-xs text-muted-foreground">
            {{ ORIGEM_ENDERECO[comprador.endereco_origem] }}
          </span>
          <span v-else-if="comprador" class="text-xs text-amber-600 dark:text-amber-400">
            não achei o endereço da empresa: digite
          </span>
        </div>
        <p v-if="erroComprador" class="text-xs text-red-600 dark:text-red-400">{{ erroComprador }}</p>
        <div class="grid gap-3 sm:grid-cols-6">
          <NfseCampo
            v-for="c in CAMPOS_ENDERECO"
            :key="c.campo"
            :rotulo="c.rotulo"
            :obrigatorio="c.obrigatorio"
            :para="`compra-end-${c.campo}`"
            :class="{
              'sm:col-span-3': c.campo === 'logradouro',
              'sm:col-span-1': c.campo === 'numero' || c.campo === 'uf',
              'sm:col-span-2': ['complemento', 'bairro', 'cep', 'municipio'].includes(c.campo),
            }"
          >
            <input
              :id="`compra-end-${c.campo}`"
              v-model="form.endereco[c.campo]"
              type="text"
              autocomplete="off"
              :maxlength="c.campo === 'uf' ? 2 : c.campo === 'cep' ? 9 : 120"
              :disabled="!podeEditar || carregandoComprador"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              :class="[
                c.campo === 'uf' && 'uppercase',
                tentou && c.obrigatorio && !(form.endereco[c.campo] ?? '').toString().trim() && 'border-red-500 dark:border-red-400',
              ]"
            />
          </NfseCampo>
        </div>
        <p v-if="mostrar('endereco')" class="text-xs text-red-600 dark:text-red-400">{{ mostrar('endereco') }}</p>
      </div>

      <NfseCampo
        rotulo="Observações da nota"
        opcional
        para="compra-obs"
        dica="Vai nas Informações Complementares da NF (coluna Observações da planilha)."
      >
        <textarea
          id="compra-obs"
          v-model="form.observacoes"
          rows="2"
          maxlength="500"
          :disabled="!podeEditar"
          class="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </NfseCampo>

      <NfseAviso tom="info" :icone="Info" compacto>
        A planilha do Bling não leva IE, NCM nem CFOP. Ao emitir no Bling de
        <span class="font-medium">{{ vendedora?.nome || 'quem vende' }}</span>: conferir a natureza de operação (CFOP
        {{ cfopFinal }}), o NCM do produto (pelo SKU) e a IE do contato.
      </NfseAviso>
    </NfseSecao>

    <div v-if="podeEditar" class="flex flex-wrap items-center justify-end gap-2">
      <p v-if="tentou && temErro" class="mr-auto text-sm text-red-600 dark:text-red-400">
        Falta: {{ Object.values(erros).join(' ') }}
      </p>
      <Button v-if="editando" variant="ghost" :disabled="salvando" @click="cancelarEdicao">cancelar edição</Button>
      <Button variant="outline" :disabled="salvando" @click="salvar(false)">
        <Save class="mr-1.5 size-4" aria-hidden="true" />
        {{ editando ? 'salvar alterações' : 'registrar compra' }}
      </Button>
      <Button :disabled="salvando" @click="salvar(true)">
        <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <Download v-else class="mr-1.5 size-4" aria-hidden="true" />
        {{ editando ? 'salvar e baixar planilha' : 'registrar e baixar planilha' }}
      </Button>
    </div>
  </div>
</template>
