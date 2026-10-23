<script setup lang="ts">
// Cadastros › Contabilidade › Compra (23/10/2026). Eduardo: "do lado de emissao de
// servico vai ter a nova aba compra": puxar as empresas, escolher de qual das
// contas Bling de NF comprar, puxar o faturamento do mês, escolher a quantidade
// (valor por embalagem), cadastrar o produto (Embalagem, NCM 3923.21.10) e
// outros. "designer pode usar a mesma da nossa emissao de servico".
//
// Mora dentro da página da Emissão de Serviço (mesma senha extra, mesma
// permissão) e usa o contexto dela (useNfseTela/useNfseApi). Três abas:
// Fazer compra (formulário), Compras feitas (lista, planilha do Bling, NF) e
// Produtos (o catálogo). Editar uma compra volta o formulário com ela.
import { computed, onMounted, ref, watch } from 'vue'
import { ClipboardList, Package, ShoppingCart } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { erroApi, useNfseApi, useNfseTela } from '~/lib/nfse'
import type { Compra, Produto, Vendedora } from '~/lib/compra'

type Aba = 'fazer' | 'compras' | 'produtos'

const { api } = useNfseApi()
const tela = useNfseTela()

const aba = ref<Aba>('fazer')
const vendedoras = ref<Vendedora[]>([])
const produtosAtivos = ref<Produto[]>([])
const carregandoOpcoes = ref(false)
const erroOpcoes = ref<string | null>(null)
const editando = ref<Compra | null>(null)
const versaoLista = ref(0) // muda = a lista recarrega
const ultimaSalva = ref<string | null>(null) // destaca na lista a compra que acabou de ser registrada

async function carregarOpcoes() {
  carregandoOpcoes.value = true
  erroOpcoes.value = null
  try {
    const r = await api<{ vendedoras: Vendedora[]; produtos: Produto[] }>('/api/nfse/compras/opcoes')
    vendedoras.value = r.vendedoras
    produtosAtivos.value = r.produtos
  } catch (e) {
    erroOpcoes.value = erroApi(e)
  } finally {
    carregandoOpcoes.value = false
  }
}

onMounted(carregarOpcoes)
// o "atualizar" do topo (recarregar da página) também relê as vendedoras e os produtos
watch(() => tela.versao.value, () => carregarOpcoes())

// as empresas do grupo vêm da carga da página (a mesma da Emissão de Serviço)
const semEmpresas = computed(() => tela.carregado.value === false && !tela.carregando.value && !tela.prestadores.value.length)

const opcoesAbas = computed(() => [
  { id: 'fazer', rotulo: editando.value ? `Editando a compra nº ${editando.value.numero}` : 'Fazer compra', icone: ShoppingCart },
  { id: 'compras', rotulo: 'Compras feitas', icone: ClipboardList },
  { id: 'produtos', rotulo: 'Produtos', icone: Package },
])
const abaModel = computed({
  get: () => aba.value as string,
  set: (v: string) => {
    aba.value = (['fazer', 'compras', 'produtos'].includes(v) ? v : 'fazer') as Aba
  },
})

function aoSalvar(c: Compra) {
  ultimaSalva.value = c.id
  editando.value = null
  versaoLista.value++
  aba.value = 'compras'
}

function editar(c: Compra) {
  editando.value = c
  aba.value = 'fazer'
}

function pararDeEditar() {
  editando.value = null
}

function aoMudarProdutos() {
  carregarOpcoes()
}
</script>

<template>
  <div class="space-y-5">
    <NfseAviso v-if="erroOpcoes" tom="perigo" titulo="Não carregou a Compra">
      {{ erroOpcoes }}
      <template #acoes>
        <Button size="sm" variant="outline" :disabled="carregandoOpcoes" @click="carregarOpcoes()">tentar de novo</Button>
      </template>
    </NfseAviso>

    <NfseAviso v-if="semEmpresas" tom="perigo" titulo="Não carregou a lista de empresas do grupo">
      Sem ela não dá para escolher quem compra. Use o botão "atualizar" lá em cima.
    </NfseAviso>

    <NfseSegmentado v-model="abaModel" :opcoes="opcoesAbas" tamanho="sm" />

    <CompraForm
      v-show="aba === 'fazer'"
      :vendedoras="vendedoras"
      :produtos="produtosAtivos"
      :carregando="carregandoOpcoes"
      :editando="editando"
      @salva="aoSalvar"
      @parar-de-editar="pararDeEditar"
      @ir-produtos="aba = 'produtos'"
    />
    <CompraLista
      v-if="aba === 'compras'"
      :versao="versaoLista"
      :destacar="ultimaSalva"
      @editar="editar"
    />
    <CompraProdutos v-if="aba === 'produtos'" @mudou="aoMudarProdutos" />
  </div>
</template>
