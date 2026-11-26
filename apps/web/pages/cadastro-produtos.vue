<script setup lang="ts">
// Cadastros › Produtos (M-29, Harry 24/11/2026): a lista oficial de categorias e
// tags de envio — tabela Tipo | Nome. Os filtros Tags e Categoria do Produtos 2
// (M-30) leem esta lista (GET /api/produto-cadastros/listas).
// Quem vê: permissão `cadastro_produtos` (view; a migration 0508 deu a quem já via
// alguma aba de Cadastros). Criar, editar e excluir: só
// administrador (o servidor confere; aqui os botões nem aparecem para os outros).
// Regras do servidor (routers/produto_cadastros.py): nome repetido no tipo é
// recusado; Categoria que ainda tem produto (base do ERP) não sai nem muda de
// nome; Tag de envio em uso pede confirmação com a contagem.
// M-75 (Harry 25/11/2026): tipo "Categoria de venda" = os grupos de margem/frete da aba Contas.
// NÃO é cadastro novo: são os segmentos filhos de Cadastros › Segmentos (o mesmo dado), com a
// raiz como centro de custo. Vão pelas rotas /categorias-venda; a nova entra no fim do
// centro de custo (até 5), só a última sai e "Apple"/"Queima de estoque" não trocam de nome
// (o servidor confere e manda a frase). O menu "Centro de custo" do Novo cadastro vem de um
// lugar só: `centros_custo` da lista (services/categorias_venda.centros_do_menu no servidor).
// M-76: esse menu é a lista de Cadastros › Centro de custo. Centro novo aparece, mas ainda não
// recebe categoria de venda: ao escolher, a janela avisa e o Salvar fica travado (o servidor
// recusa com a mesma frase). Desde a M-89 o menu "Centro de custo" virou o menu "Tabela" (só os
// centros com tabela na aba Contas), e o centro novo nem aparece.
// M-84 (Harry 26/11/2026): a coluna "Centro de custo" saiu da tabela (fica Tipo | Nome | Ações);
// o centro continua nos dados, no Novo cadastro, no lápis e nos avisos.
// M-89 (Harry 26/11/2026): coluna "Tabela" antes de Tipo (Tabela | Tipo | Nome | Ações), vinda
// do servidor: Categoria e Tag de envio → "Produtos 2"; Categoria de venda → "Contas Celular",
// "Contas Eletro" ou "Contas Mala" (as do Catálogo não aparecem mais aqui); tipo novo
// "Tipo de conta" (tabela "Lojas": Celular, Eletro, Mala) só para ver — sem lápis nem lixeira.
// No Novo cadastro a pessoa escolhe primeiro a Tabela; o Tipo depende dela e, nas tabelas de
// Contas, a tabela é o centro de custo da Categoria de venda. No lápis a Tabela só aparece.
// M-90 (Harry 26/11/2026): a lixeira da Categoria de venda confere o uso só na tabela dela
// (Tabela de Preços e contas). Em uso, a janela não bloqueia: pede "Transferir para" (as outras
// categorias de venda ativas do mesmo centro, escolha única) e o botão "Transferir e excluir"
// manda o destino (DELETE ?destino_id=); o servidor transfere e exclui de uma vez. Sem uso,
// confirmação simples com os avisos do servidor (nome reservado, produtos da tela Produtos que
// ficam sem segmento). Categoria e Tag de envio seguem como antes.
// M-91 (Harry 26/11/2026): a lixeira da Categoria de venda aceita a do meio (não só a última).
// O GET /uso manda `sobem` (as que andam uma posição) e `contas_centro`; a janela lista o que
// acontece (transferência, colunas que andam levando margem/frete, margem/frete apagados, "Não dá
// para desfazer.") e o DELETE é o mesmo da M-90 — o servidor faz tudo num commit só.
import { TABS_CADASTROS } from '~/lib/navGroups'
import { AlertCircle, Check, Loader2, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'cadastro_produtos', action: 'view' },
})

type Tipo = { id: string; nome: string; chave: string | null; tabela?: string | null }
type Cadastro = {
  id: string
  tipo_id: string
  tipo_nome: string
  tipo_chave: string | null
  nome: string
  centro_custo?: string | null
  ativa?: boolean
  tabela?: string | null
}
type Centro = { id: string; nome: string; aceita_categoria?: boolean; tabela?: string | null }
type Uso = {
  regra: string | null
  produtos: number | null
  bloqueia: boolean
  mensagem: string | null
  // M-90: só na Categoria de venda (rota /categorias-venda/{id}/uso).
  contas?: number
  produtos_tela_produtos?: number
  precisa_destino?: boolean
  destinos?: { id: string; nome: string }[]
  centro_custo?: string | null
  avisos?: string[]
  // M-91: as categorias que andam uma posição na aba Contas (vazia se for a última) e em
  // quantas contas do centro a margem/frete da excluída são apagados.
  sobem?: string[]
  contas_centro?: number
}
// Uma opção do menu "Tabela" do Novo cadastro; `centro` = o centro de custo (tabelas de Contas).
type Tabela = { id: string; nome: string; centro: string | null }

const CHAVE_VENDA = 'categoria_venda'
const CHAVE_TIPO_CONTA = 'tipo_conta'
const TABELA_PRODUTOS2 = 'Produtos 2'
const ehVenda = (c: { tipo_chave?: string | null; chave?: string | null } | null | undefined) =>
  !!c && (c.tipo_chave === CHAVE_VENDA || c.chave === CHAVE_VENDA)
// M-89: Tipo de conta (Celular, Eletro, Mala da tabela Lojas) é só para ver.
const ehTipoConta = (c: { tipo_chave?: string | null; chave?: string | null } | null | undefined) =>
  !!c && (c.tipo_chave === CHAVE_TIPO_CONTA || c.chave === CHAVE_TIPO_CONTA)

const { api } = useApi()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

const tipos = ref<Tipo[]>([])
const cadastros = ref<Cadastro[]>([])
const centros = ref<Centro[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// A mensagem que o servidor manda (detail.mensagem) é frase de gente; o resto vira o código.
function msgErro(e: any): string {
  const d = e?.data?.detail
  if (d && typeof d === 'object' && d.mensagem) return d.mensagem
  if (typeof d === 'string') return d
  return d?.code || e?.message || 'erro'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const r = await api<{ tipos: Tipo[]; cadastros: Cadastro[]; centros_custo?: Centro[] }>('/api/produto-cadastros')
    tipos.value = r.tipos
    cadastros.value = r.cadastros
    centros.value = r.centros_custo ?? []
  } catch (e: any) {
    error.value = msgErro(e)
  } finally {
    loading.value = false
  }
}

await load()

// ---------------------------------------------------------------- + Novo tipo / renomear tipo
const tipoAberto = ref(false)
const tipoNome = ref('')
const tipoEditando = ref<string | null>(null)
const tipoEditNome = ref('')
const tipoErro = ref<string | null>(null)
const tipoSalvando = ref(false)

function abrirTipo() {
  tipoAberto.value = true
  tipoNome.value = ''
  tipoEditando.value = null
  tipoErro.value = null
}

async function criarTipo() {
  if (!tipoNome.value.trim()) return
  tipoSalvando.value = true
  tipoErro.value = null
  try {
    await api('/api/produto-cadastros/tipos', { method: 'POST', body: { nome: tipoNome.value } })
    tipoNome.value = ''
    await load()
  } catch (e: any) {
    tipoErro.value = msgErro(e)
  } finally {
    tipoSalvando.value = false
  }
}

function editarTipo(t: Tipo) {
  tipoEditando.value = t.id
  tipoEditNome.value = t.nome
  tipoErro.value = null
}

async function salvarTipo(t: Tipo) {
  if (!tipoEditNome.value.trim() || tipoEditNome.value === t.nome) {
    tipoEditando.value = null
    return
  }
  tipoSalvando.value = true
  tipoErro.value = null
  try {
    await api(`/api/produto-cadastros/tipos/${t.id}`, { method: 'PATCH', body: { nome: tipoEditNome.value } })
    tipoEditando.value = null
    await load()
  } catch (e: any) {
    tipoErro.value = msgErro(e)
  } finally {
    tipoSalvando.value = false
  }
}

// ---------------------------------------------------------------- + Novo cadastro / lápis
const cadAberto = ref(false)
const cadEditando = ref<Cadastro | null>(null)
const cadTabela = ref(TABELA_PRODUTOS2) // M-89: o id da opção do menu "Tabela"
const cadTipo = ref('')
const cadNome = ref('')
const cadErro = ref<string | null>(null)
const cadAviso = ref<string | null>(null) // tag em uso: confirmar a troca
const cadSalvando = ref(false)

// M-89: o menu "Tabela" do Novo cadastro — Produtos 2 e as tabelas de Contas (um centro de custo
// com raiz cada: Contas Celular, Contas Eletro, Contas Mala). Centro novo, sem raiz, não tem
// tabela na aba Contas e não entra.
const tabelasDoMenu = computed<Tabela[]>(() => [
  { id: TABELA_PRODUTOS2, nome: TABELA_PRODUTOS2, centro: null },
  ...centros.value
    .filter((c) => c.aceita_categoria !== false)
    .map((c) => ({ id: c.id, nome: c.tabela || `Contas ${c.nome}`, centro: c.id })),
])
// Categoria de venda nova: o centro de custo é o da tabela escolhida.
const cadCentro = computed(() => tabelasDoMenu.value.find((t) => t.id === cadTabela.value)?.centro ?? '')
// Os tipos de cada tabela: Produtos 2 → os da lista (Categoria, Tag de envio…); Contas → só a
// Categoria de venda. Tipo de conta (Lojas) é só para ver: não entra em menu nenhum.
function tiposDaTabela(contas: boolean): Tipo[] {
  return tipos.value.filter((t) => !ehTipoConta(t) && (contas ? ehVenda(t) : !ehVenda(t)))
}
// O tipo escolhido na janela é Categoria de venda?
const cadEhVenda = computed(() => ehVenda(tipos.value.find((t) => t.id === cadTipo.value)))
// Os tipos do menu: Categoria de venda não troca com os outros (é outro dado) — no lápis de
// uma categoria de venda o menu fica travado; no lápis das outras ela não aparece.
const tiposDoMenu = computed(() =>
  cadEditando.value ? tiposDaTabela(ehVenda(cadEditando.value)) : tiposDaTabela(!!cadCentro.value),
)

// Trocou a Tabela no Novo cadastro: o Tipo volta para o primeiro daquela tabela.
function trocarTabela() {
  cadTipo.value = tiposDaTabela(!!cadCentro.value)[0]?.id ?? ''
}

function abrirCadastro(c: Cadastro | null = null) {
  if (c && ehTipoConta(c)) return // só para ver
  cadAberto.value = true
  cadEditando.value = c
  // No lápis a Tabela não muda (só aparece); no Novo cadastro começa no Produtos 2.
  cadTabela.value = TABELA_PRODUTOS2
  cadTipo.value = c?.tipo_id ?? tiposDaTabela(false)[0]?.id ?? ''
  cadNome.value = c?.nome ?? ''
  cadErro.value = null
  cadAviso.value = null
}

function fecharCadastro() {
  cadAberto.value = false
  cadEditando.value = null
}

async function salvarCadastro(confirmar = false) {
  if (!cadTipo.value || !cadNome.value.trim()) {
    cadErro.value = 'Escolha o tipo e digite o nome.'
    return
  }
  if (cadEhVenda.value && !cadEditando.value && !cadCentro.value) {
    cadErro.value = 'Escolha a tabela.'
    return
  }
  cadSalvando.value = true
  cadErro.value = null
  try {
    if (cadEhVenda.value) {
      // M-75: o segmento filho (mesmo dado da aba Segmentos); o lápis só troca o nome.
      if (cadEditando.value) {
        await api(`/api/produto-cadastros/categorias-venda/${cadEditando.value.id}`, {
          method: 'PATCH',
          body: { nome: cadNome.value },
        })
      } else {
        await api('/api/produto-cadastros/categorias-venda', {
          method: 'POST',
          body: { centro_custo_id: cadCentro.value, nome: cadNome.value },
        })
      }
    } else if (cadEditando.value) {
      await api(`/api/produto-cadastros/${cadEditando.value.id}`, {
        method: 'PATCH',
        body: { tipo_id: cadTipo.value, nome: cadNome.value, confirmar },
      })
    } else {
      await api('/api/produto-cadastros', { method: 'POST', body: { tipo_id: cadTipo.value, nome: cadNome.value } })
    }
    fecharCadastro()
    await load()
  } catch (e: any) {
    const d = e?.data?.detail
    if (d?.code === 'tag_em_uso' && !confirmar) {
      cadAviso.value = `${d.mensagem || 'Há produtos com esta tag.'} Trocar mesmo assim?`
    } else {
      cadErro.value = msgErro(e)
    }
  } finally {
    cadSalvando.value = false
  }
}

// ---------------------------------------------------------------- lixeira (sempre com confirmação)
const excluindo = ref<Cadastro | null>(null)
const excluirUso = ref<Uso | null>(null)
const excluirErro = ref<string | null>(null)
const excluirCarregando = ref(false)
// M-90: o destino escolhido em "Transferir para" (Categoria de venda em uso).
const excluirDestino = ref<string | null>(null)

// Escolha única: marcar uma desmarca a outra; clicar na marcada desmarca.
function escolherDestino(id: string) {
  excluirDestino.value = excluirDestino.value === id ? null : id
}

const precisaDestino = computed(() => !!excluirUso.value?.precisa_destino && !excluirUso.value?.bloqueia)
const destinoNome = computed(
  () => excluirUso.value?.destinos?.find((d) => d.id === excluirDestino.value)?.nome ?? null,
)

function numero(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.')
}

// «Os N produtos e as N contas passam para “Y” (Centro) e a categoria “X” é excluída. …»
const textoTransferencia = computed(() => {
  const u = excluirUso.value
  const nome = excluindo.value?.nome
  if (!precisaDestino.value || !u || !destinoNome.value || !nome) return null
  const p = u.produtos ?? 0
  const c = u.contas ?? 0
  const partes: string[] = []
  if (p) partes.push(p === 1 ? 'o produto' : `os ${numero(p)} produtos`)
  if (c) partes.push(c === 1 ? 'a conta' : `as ${numero(c)} contas`)
  if (!partes.length) return null
  const quem = partes.join(' e ')
  const centro = u.centro_custo ? ` (${u.centro_custo})` : ''
  return (
    `${quem.charAt(0).toUpperCase()}${quem.slice(1)} ${p + c === 1 ? 'passa' : 'passam'} para ` +
    `“${destinoNome.value}”${centro} e a categoria “${nome}” é excluída. Não dá para desfazer.`
  )
})
// Com produtos da Tabela de Preços: o preço deles muda de margem/frete.
const textoPrecos = computed(() => {
  const p = excluirUso.value?.produtos ?? 0
  if (!precisaDestino.value || !destinoNome.value || !p) return null
  return p === 1
    ? `O preço desse produto passa a usar a margem e o frete de “${destinoNome.value}”.`
    : `Os preços desses produtos passam a usar a margem e o frete de “${destinoNome.value}”.`
})
// Produtos da tela Produtos (não travam): vão junto para o destino.
const textoTelaProdutos = computed(() => {
  const n = excluirUso.value?.produtos_tela_produtos ?? 0
  if (!precisaDestino.value || !destinoNome.value || !n) return null
  return n === 1
    ? `1 produto da tela Produtos vai junto para “${destinoNome.value}”.`
    : `${numero(n)} produtos da tela Produtos vão junto para “${destinoNome.value}”.`
})

// M-91: Categoria de venda "do meio" — sai, e as seguintes andam uma posição na aba Contas
// levando a margem e o frete de cada conta. A janela troca os textos da M-90 por esta lista.
const doMeio = computed(() => !excluirUso.value?.bloqueia && (excluirUso.value?.sobem?.length ?? 0) > 0)

// ["A", "B", "C"] → “A”, “B” e “C”
function entreAspas(nomes: string[]): string {
  const itens = nomes.map((n) => `“${n}”`)
  return itens.length === 1 ? itens[0] : `${itens.slice(0, -1).join(', ')} e ${itens[itens.length - 1]}`
}

const textosDoMeio = computed<string[]>(() => {
  const u = excluirUso.value
  const nome = excluindo.value?.nome
  if (!doMeio.value || !u || !nome) return []
  const linhas: string[] = []
  const p = u.produtos ?? 0
  const c = u.contas ?? 0
  if (precisaDestino.value && destinoNome.value && (p || c)) {
    const destino = `“${destinoNome.value}”`
    const partes: string[] = []
    if (p) partes.push(p === 1 ? 'o produto' : `os ${numero(p)} produtos`)
    if (c) partes.push(c === 1 ? 'a conta' : `as ${numero(c)} contas`)
    const quem = partes.join(' e ')
    let t = `${quem.charAt(0).toUpperCase()}${quem.slice(1)} ${p + c === 1 ? 'passa' : 'passam'} para ${destino}`
    if (p === 1) t += ` e o preço ${c ? 'do produto' : 'dele'} passa a usar a margem e o frete de ${destino}`
    else if (p) t += ` e os preços ${c ? 'dos produtos' : 'deles'} passam a usar a margem e o frete de ${destino}`
    linhas.push(`${t}.`)
  }
  if (precisaDestino.value && textoTelaProdutos.value) linhas.push(textoTelaProdutos.value)
  const sobem = u.sobem ?? []
  linhas.push(
    sobem.length === 1
      ? `Na aba Contas, a coluna ${entreAspas(sobem)} anda uma posição, levando a margem e o frete de cada conta.`
      : `Na aba Contas, as colunas ${entreAspas(sobem)} andam uma posição, levando a margem e o frete de cada conta.`,
  )
  const n = u.contas_centro ?? 0
  if (n) {
    linhas.push(
      n === 1
        ? `A margem e o frete de “${nome}” na conta são apagados.`
        : `A margem e o frete de “${nome}” nas ${numero(n)} contas são apagados.`,
    )
  }
  linhas.push('Não dá para desfazer.')
  return linhas
})

// M-89: Tipo de conta não tem lápis nem lixeira (na coluna Ações aparece "—").
const podeMexer = (c: Cadastro) => !ehTipoConta(c)

// Categoria de venda (M-75) tem rota própria: a linha é o segmento filho.
function rotaDe(c: Cadastro): string {
  return ehVenda(c) ? `/api/produto-cadastros/categorias-venda/${c.id}` : `/api/produto-cadastros/${c.id}`
}

async function pedirExclusao(c: Cadastro) {
  if (!podeMexer(c)) return
  excluindo.value = c
  excluirUso.value = null
  excluirErro.value = null
  excluirDestino.value = null
  excluirCarregando.value = true
  try {
    excluirUso.value = await api<Uso>(`${rotaDe(c)}/uso`)
  } catch (e: any) {
    excluirErro.value = msgErro(e)
  } finally {
    excluirCarregando.value = false
  }
}

function fecharExclusao() {
  excluindo.value = null
  excluirUso.value = null
  excluirDestino.value = null
}

async function confirmarExclusao() {
  if (!excluindo.value) return
  // M-90: Categoria de venda em uso só vai com o destino escolhido.
  if (ehVenda(excluindo.value) && precisaDestino.value && !excluirDestino.value) return
  excluirCarregando.value = true
  excluirErro.value = null
  try {
    // A pessoa já viu a contagem (tag em uso) nesta janela: vai com confirmar.
    // Categoria de venda não tem confirmar: em uso vai com o destino (M-90), e o servidor
    // transfere e exclui de uma vez.
    let url = `${rotaDe(excluindo.value)}?confirmar=true`
    if (ehVenda(excluindo.value)) {
      url = precisaDestino.value
        ? `${rotaDe(excluindo.value)}?destino_id=${encodeURIComponent(excluirDestino.value ?? '')}`
        : rotaDe(excluindo.value)
    }
    await api(url, { method: 'DELETE' })
    fecharExclusao()
    await load()
  } catch (e: any) {
    excluirErro.value = msgErro(e)
  } finally {
    excluirCarregando.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader title="Produtos">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="isAdmin" size="sm" variant="outline" data-acao="novo-tipo" @click="abrirTipo">
          <Plus class="size-4 mr-1" /> Novo tipo
        </Button>
        <Button v-if="isAdmin" size="sm" data-acao="novo-cadastro" @click="abrirCadastro()">
          <Plus class="size-4 mr-1" /> Novo cadastro
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <!-- Letras da Tabela de Preços › Contas: cabeçalho 11px semibold, células 12px,
         bordas nas células e linhas da mesma altura (h-8). -->
    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)] max-w-3xl">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-36">Tabela</th>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-48">Tipo</th>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border">Nome</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-20">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !cadastros.length">
            <td colspan="4" class="text-center py-6 text-xs text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!cadastros.length">
            <td colspan="4" class="text-center py-6 text-xs text-muted-foreground">Nenhum cadastro.</td>
          </tr>
          <tr v-for="c in cadastros" :key="c.id" class="h-8 hover:bg-accent/30" :data-cadastro="c.nome">
            <td class="border border-border px-2 py-1 text-xs">{{ c.tabela || '—' }}</td>
            <td class="border border-border px-2 py-1 text-xs">{{ c.tipo_nome }}</td>
            <td
              class="border border-border px-2 py-1 text-xs font-medium"
              :class="{ 'text-muted-foreground': c.ativa === false }"
            >
              {{ c.nome }}
              <span
                v-if="c.ativa === false"
                class="ml-1 rounded border border-border px-1 text-[10px] font-normal text-muted-foreground"
                data-marca="inativa"
              >inativa</span>
            </td>
            <td class="border border-border px-1 py-1 text-center">
              <!-- M-89: Tipo de conta é só para ver. -->
              <span v-if="!podeMexer(c)" class="text-xs text-muted-foreground" data-marca="so-ver">—</span>
              <div v-else-if="isAdmin" class="flex gap-0.5 justify-center">
                <button
                  class="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded"
                  title="Editar"
                  data-acao="editar"
                  @click="abrirCadastro(c)"
                >
                  <Pencil class="h-3.5 w-3.5" />
                </button>
                <button
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  title="Excluir"
                  data-acao="excluir"
                  @click="pedirExclusao(c)"
                >
                  <Trash2 class="h-3.5 w-3.5" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- + Novo tipo: cadastra o tipo e renomeia os que existem (lápis). -->
    <div
      v-if="tipoAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="tipos"
      @click.self="tipoAberto = false"
    >
      <div class="w-full max-w-sm max-h-[90vh] overflow-y-auto rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">Novo tipo</h3>
          <button class="p-1 hover:bg-muted rounded" title="Fechar" @click="tipoAberto = false"><X class="h-4 w-4" /></button>
        </div>
        <div v-if="tipoErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ tipoErro }}
        </div>
        <div class="flex gap-2">
          <input
            v-model="tipoNome"
            type="text"
            maxlength="60"
            placeholder="Nome do tipo (ex.: Cor)"
            class="flex-1 text-sm border rounded px-2 py-1 bg-background"
            data-campo="tipo-nome"
            @keydown.enter="criarTipo"
          />
          <Button size="sm" :disabled="tipoSalvando || !tipoNome.trim()" @click="criarTipo">
            <Loader2 v-if="tipoSalvando" class="size-4 mr-1 animate-spin" /> Cadastrar
          </Button>
        </div>
        <div class="text-xs font-medium">Tipos cadastrados</div>
        <ul class="divide-y divide-border rounded border border-border">
          <li v-for="t in tipos" :key="t.id" class="flex items-center gap-2 px-2 h-8 text-xs" :data-tipo="t.nome">
            <template v-if="tipoEditando === t.id">
              <input
                v-model="tipoEditNome"
                type="text"
                maxlength="60"
                class="flex-1 text-xs border rounded px-1.5 py-0.5 bg-background"
                data-campo="tipo-renomear"
                @keydown.enter="salvarTipo(t)"
                @keydown.escape="tipoEditando = null"
              />
              <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" title="Salvar" @click="salvarTipo(t)"><Check class="h-3.5 w-3.5" /></button>
              <button class="p-1 text-destructive hover:bg-destructive/10 rounded" title="Cancelar" @click="tipoEditando = null"><X class="h-3.5 w-3.5" /></button>
            </template>
            <template v-else>
              <span class="flex-1">{{ t.nome }}</span>
              <!-- Categoria de venda (M-75) e Tipo de conta (M-89) não são tipos desta tabela:
                   não renomeiam aqui. -->
              <button v-if="!ehVenda(t) && !ehTipoConta(t)" class="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded" title="Renomear" data-acao="renomear-tipo" @click="editarTipo(t)">
                <Pencil class="h-3.5 w-3.5" />
              </button>
            </template>
          </li>
        </ul>
      </div>
    </div>

    <!-- + Novo cadastro e lápis: Tabela (M-89), Tipo (os tipos da tabela) e Nome. -->
    <div
      v-if="cadAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="cadastro"
      @click.self="fecharCadastro"
    >
      <div class="w-full max-w-sm rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">{{ cadEditando ? 'Editar cadastro' : 'Novo cadastro' }}</h3>
          <button class="p-1 hover:bg-muted rounded" title="Fechar" @click="fecharCadastro"><X class="h-4 w-4" /></button>
        </div>
        <div v-if="cadErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ cadErro }}
        </div>
        <!-- M-89: primeiro a Tabela. No lápis ela só aparece (travada); no Novo cadastro a
             escolha define os tipos e, nas tabelas de Contas, o centro de custo. -->
        <label v-if="cadEditando" class="block text-xs text-muted-foreground">
          Tabela
          <input
            :value="cadEditando.tabela || '—'"
            type="text"
            disabled
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground disabled:opacity-60"
            data-campo="cadastro-tabela-fixa"
          />
        </label>
        <label v-else class="block text-xs text-muted-foreground">
          Tabela
          <select
            v-model="cadTabela"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
            data-campo="cadastro-tabela"
            @change="trocarTabela"
          >
            <option v-for="r in tabelasDoMenu" :key="r.id" :value="r.id">{{ r.nome }}</option>
          </select>
        </label>
        <label class="block text-xs text-muted-foreground">
          Tipo
          <select
            v-model="cadTipo"
            :disabled="ehVenda(cadEditando)"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground disabled:opacity-60"
            data-campo="cadastro-tipo"
          >
            <option v-for="t in tiposDoMenu" :key="t.id" :value="t.id">{{ t.nome }}</option>
          </select>
        </label>
        <label class="block text-xs text-muted-foreground">
          Nome
          <input
            v-model="cadNome"
            type="text"
            maxlength="60"
            :placeholder="cadEhVenda ? 'ex.: Acessórios' : 'ex.: .sp ou Celular'"
            class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
            data-campo="cadastro-nome"
            @keydown.enter="salvarCadastro(false)"
          />
        </label>
        <div v-if="cadAviso" class="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/15 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 space-y-2">
          <p>{{ cadAviso }}</p>
          <div class="flex justify-end gap-2">
            <Button size="sm" variant="ghost" @click="cadAviso = null">Não</Button>
            <Button size="sm" :disabled="cadSalvando" data-acao="confirmar-troca" @click="salvarCadastro(true)">Sim, trocar</Button>
          </div>
        </div>
        <div v-else class="flex justify-end gap-2">
          <Button size="sm" variant="ghost" @click="fecharCadastro">Cancelar</Button>
          <Button size="sm" :disabled="cadSalvando" data-acao="salvar-cadastro" @click="salvarCadastro(false)">
            <Loader2 v-if="cadSalvando" class="size-4 mr-1 animate-spin" /> Salvar
          </Button>
        </div>
      </div>
    </div>

    <!-- Lixeira: sempre pergunta antes. Categoria com produto: só avisa (não exclui).
         M-90: Categoria de venda em uso pede "Transferir para" e vira "Transferir e excluir". -->
    <div
      v-if="excluindo"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      data-janela="excluir"
      @click.self="fecharExclusao"
    >
      <div class="w-full max-w-sm rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <h3 class="font-semibold text-sm">Excluir “{{ excluindo.nome }}” ({{ excluindo.tipo_nome }})?</h3>
        <p v-if="excluirCarregando && !excluirUso" class="text-xs text-muted-foreground">
          <Loader2 class="inline h-3.5 w-3.5 animate-spin" /> conferindo quantos produtos usam…
        </p>
        <div v-if="excluirErro" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ excluirErro }}
        </div>
        <div
          v-if="excluirUso?.bloqueia"
          class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          data-aviso="bloqueada"
        >
          {{ excluirUso.mensagem }}
        </div>
        <div
          v-else-if="precisaDestino"
          class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          data-aviso="em-uso"
        >
          {{ excluirUso?.mensagem }}
        </div>
        <p v-else-if="excluirUso?.mensagem" class="text-xs text-amber-800 dark:text-amber-300" data-aviso="contagem">
          {{ excluirUso.mensagem }} Excluir mesmo assim?
        </p>
        <!-- M-90: nome reservado; produtos da tela Produtos que ficam sem segmento. -->
        <p
          v-for="a in excluirUso?.avisos ?? []"
          :key="a"
          class="text-xs text-amber-800 dark:text-amber-300"
          data-aviso="aviso"
        >
          {{ a }}
        </p>
        <!-- M-90: o destino (escolha única) entre as outras categorias de venda ativas do centro. -->
        <div v-if="precisaDestino" class="space-y-1" data-campo="transferir-para">
          <div class="text-xs text-muted-foreground">Transferir para</div>
          <div class="max-h-48 overflow-y-auto rounded border border-border divide-y divide-border">
            <label
              v-for="d in excluirUso?.destinos ?? []"
              :key="d.id"
              class="flex items-center gap-2 px-2 h-8 text-xs cursor-pointer hover:bg-accent/30"
              :data-destino="d.nome"
            >
              <input type="checkbox" :checked="excluirDestino === d.id" @change="escolherDestino(d.id)" />
              {{ d.nome }}
            </label>
          </div>
          <div v-if="textoTransferencia && !doMeio" class="space-y-1 pt-1 text-xs text-destructive" data-aviso="transferencia">
            <p>{{ textoTransferencia }}</p>
            <p v-if="textoPrecos">{{ textoPrecos }}</p>
            <p v-if="textoTelaProdutos">{{ textoTelaProdutos }}</p>
          </div>
        </div>
        <!-- M-91: categoria do meio — o que acontece, abaixo de "Transferir para". -->
        <ul
          v-if="textosDoMeio.length"
          class="list-disc space-y-1 pl-4 text-xs text-destructive"
          data-aviso="do-meio"
        >
          <li v-for="t in textosDoMeio" :key="t">{{ t }}</li>
        </ul>
        <div class="flex justify-end gap-2">
          <Button size="sm" variant="ghost" @click="fecharExclusao">{{ excluirUso?.bloqueia ? 'Fechar' : 'Cancelar' }}</Button>
          <Button
            v-if="excluirUso && !excluirUso.bloqueia"
            size="sm"
            variant="destructive"
            :disabled="excluirCarregando || (precisaDestino && !excluirDestino)"
            data-acao="confirmar-exclusao"
            @click="confirmarExclusao"
          >
            {{ precisaDestino ? 'Transferir e excluir' : 'Excluir' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
