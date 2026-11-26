<script setup lang="ts">
import {
  Plus, Trash2, RefreshCw, X, AlertCircle, Loader2, Eye,
  Send, Check, Link2, Copy, Minus,
  Smartphone, Briefcase, Zap, DollarSign, Settings2, Upload, Coins,
  ChevronDown, Download, Search, Tags, Camera, FolderPlus,
} from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { isoToday } from '~/lib/date'
import { ordenarCategorias } from '~/lib/categorias'
import { gerarXlsx, TIPO_XLSX, type CelulaPlanilha } from '~/lib/planilhaXlsx'
import {
  MOTIVO_ANUNCIO, cartoesDaGrade, celulaComparavel, divergenciasDaConferencia, margemDaResposta,
  tipoSemAnuncio,
  type CelulaConferida, type Divergencia, type MargemConferencia,
} from '~/lib/cartaoVermelho'
import {
  calcularReposicao, explicarReposicao, prazoDias, reposicaoPorPeca, textoFormulaReposicao,
} from '~/utils/reposicao'

definePageMeta({
  middleware: [
    // A aba Concorrência saiu em 13/11/2026 (Organização do DaVinci, item 4).
    // O link antigo /pricing/concorrencia abre a Tabela de Preços, mantendo o
    // ?dept=, em vez de cair numa aba vazia.
    (to) => {
      if (to.params.tab !== 'concorrencia') return
      return navigateTo({ path: '/pricing/tabela', query: to.query, hash: to.hash }, { replace: true })
    },
    'permission',
    // Faturamento (13/11/2026, item 6) é o botão ao lado de Celular · Eletro ·
    // Mala (ordem da M-23), em /pricing/faturamento. Quem só tem a permissão
    // Faturamento entra direto nele (sem os departamentos); quem não tem Faturamento e
    // abre o link dele cai na Tabela de Preços.
    (to) => {
      const u = useAuthStore().user
      if (!u || u.role === 'admin') return
      const tem = (r: 'tabela_precos' | 'faturamento') => u.permissions?.[r]?.view === true
      const query = to.query
      if (to.params.tab === 'faturamento' && !tem('faturamento')) {
        return navigateTo({ path: '/pricing/tabela', query }, { replace: true })
      }
      if (to.params.tab !== 'faturamento' && !tem('tabela_precos')) {
        return navigateTo({ path: '/pricing/faturamento', query }, { replace: true })
      }
    },
  ],
  // Até 13/11/2026 só tabela_precos. O Faturamento saiu do menu e virou botão
  // daqui, então quem tinha só Faturamento continua chegando nele.
  permissionAny: [
    { resource: 'tabela_precos', action: 'view' },
    { resource: 'faturamento', action: 'view' },
  ],
})

// Concorrência (busca na API pública do ML) saiu em 13/11/2026; a rota da API
// e a permissão tabela_precos_concorrencia continuam, só a aba some.
// 'faturamento' não é sub-aba: é o botão da linha dos departamentos, e as
// sub-abas (TABS) somem enquanto ele está escolhido.
type Tab = 'tabela' | 'contas' | 'produtos' | 'faturamento'

const TABS: { key: Tab; label: string; icon: any }[] = [
  { key: 'tabela', label: 'Tabela de Preços', icon: DollarSign },
  { key: 'contas', label: 'Contas', icon: Settings2 },
  { key: 'produtos', label: 'Produtos', icon: Upload },
]

const route = useRoute()
const router = useRouter()
const { api } = useApi()

const tab = computed<Tab>(() => {
  const t = route.params.tab as string
  if (t === 'faturamento') return 'faturamento'
  return (TABS.find((x) => x.key === t)?.key ?? 'tabela') as Tab
})

function setTab(t: Tab) {
  // Preserve the active department (?dept=) when switching sub-tabs.
  router.push({ path: `/pricing/${t}`, query: { ...route.query } })
}

// Ajuda da tela (M-18, 23/11/2026): o "?" da barra do topo abre este texto num
// painel à direita. Abas Tabela de Preços e Contas (M-59), nas três categorias;
// na aba Produtos o "?" some. O Faturamento registra a dele (FaturamentoPainel).
const AJUDA_TABELA = {
  titulo: 'Tabela de Preços',
  conteudo: [
    {
      titulo: 'Para que serve',
      itens: [
        'Mostra o preço de venda de cada produto (linhas) em cada conta (colunas), calculado a partir do custo do kit, da comissão, da margem e do frete.',
        'Os custos ficam na aba Produtos; a comissão, a margem e o frete ficam na aba Contas.',
      ],
    },
    {
      titulo: 'Como ler uma célula',
      itens: [
        { termo: 'Número', texto: 'é o preço da Tabela.' },
        { termo: '"—"', texto: 'não dá para calcular: falta o custo do produto ou a comissão/margem da conta.' },
        { termo: 'Aviãozinho', texto: 'envia o preço para o anúncio.' },
        { termo: 'Cartão vermelho', texto: 'passe o mouse para ver o motivo; clique para conferir só aquela célula. Motivos: conta sem integração, sem anúncio vinculado, anúncio pausado/encerrado, último envio com erro ou preço do anúncio fora da margem.' },
        { termo: '"(!) anúncio 3863 +2"', texto: 'o preço desse anúncio e de mais 2 anúncios está fora da margem.' },
        { termo: 'Olho', texto: 'aparece ao passar o mouse no nome do produto e mostra o preço real no ar (ML, Shopee, Amazon, TikTok). Não grava nada.' },
        { termo: 'Cor de fundo', texto: 'mostra só a plataforma.' },
      ],
    },
    {
      titulo: 'Barra de botões',
      itens: [
        { termo: 'Buscar', texto: 'procura o produto por SKU ou nome.' },
        { termo: 'Plataforma e Contas', texto: 'escolhem quais colunas aparecem.' },
        { termo: 'Layout', texto: 'mostra ou esconde as colunas de Kit e de Estoque; fica lembrado neste navegador.' },
        { termo: 'Excel', texto: 'baixa todas as colunas e contas (só a busca filtra).' },
        { termo: 'Atualizar', texto: 'planilha, conferir ou atualizar anúncio — Geral ou Por conta.' },
        { termo: 'Reposição (ADM)', texto: 'dias de reposição; muda também a tela Importação.' },
        { termo: 'Margem (ADM)', texto: 'a diferença aceita entre o preço do anúncio e o da Tabela.' },
      ],
    },
    {
      titulo: 'Passo a passo',
      numerada: true,
      itens: [
        'Cadastre o custo na aba Produtos.',
        'Confira a comissão, a margem e o frete na aba Contas.',
        'Escolha a plataforma e as contas.',
        'Clique em Atualizar › Conferir.',
        'Resolva os cartões vermelhos e use Atualizar anúncio.',
      ],
    },
    {
      titulo: 'Observação',
      itens: ['Enviar preço exige a permissão de editar a Tabela de Preços.'],
    },
  ],
}
// Ajuda da aba Contas (M-59, 25/11/2026): o mesmo painel e o mesmo formato da
// Tabela de Preços. "(ADM)": colunas que só o administrador muda (Kit, Comissão,
// marg/frete e Margem mínima ele também é o único que vê; Custo total todos veem).
const AJUDA_CONTAS = {
  titulo: 'Contas',
  conteudo: [
    {
      titulo: 'Para que serve',
      itens: [
        'Guarda, para cada conta de marketplace, a comissão, as margens e os fretes que a Tabela de Preços usa para calcular o preço.',
        'Cada linha é uma conta; a do Mercado Livre vale para Clássico e Premium.',
      ],
    },
    {
      titulo: 'Colunas',
      itens: [
        { termo: 'Plataforma e Conta', texto: 'o marketplace e o nome da conta.' },
        { termo: 'Catálogo ML', texto: 'só nas contas do Mercado Livre: Ativo ou Inativo; clique para ligar ou desligar o catálogo. As margens próprias do catálogo, só o ADM muda.' },
        { termo: 'Equipe', texto: 'número da equipe que vê a conta; vem da loja ou da integração ligada a ela. Sem nenhuma, aparece "Sem equipe".' },
        { termo: 'Kit (ADM)', texto: 'qual dos kits de custo da aba Produtos a conta usa.' },
        { termo: 'Comissão (ADM)', texto: '% da plataforma. No ML aparece Clássico · Premium; clique para mudar.' },
        { termo: 'marg e frete (ADM)', texto: 'margem (%) e frete (R$) por tipo de produto; são eles que formam o preço.' },
        { termo: 'Afiliado e Desconto', texto: '% de afiliado e de desconto da conta; são só anotação.' },
        { termo: 'Ads', texto: 'a caixinha mostra o % Geral; clique nela e marque Geral (um % e um gasto por dia para a conta toda) ou Produto (um % e um gasto por dia para cada produto; produto sem % fica sem Ads). Vale um ou outro; trocar não apaga o que foi digitado. Com Produto, a lista mostra a categoria de venda de cada produto; o botão Categoria de venda mostra só os produtos das categorias marcadas (nenhuma marcada = todos) e a caixinha Ads só os que têm % ou gasto por dia nesta conta. Os dois somam com a busca, não gravam nada e voltam zerados ao abrir a janela.' },
        { termo: 'Custo total (ADM)', texto: 'teto para Afiliado + Desconto + Ads; se passar, o sistema não grava. Todos veem. Com a Margem mínima preenchida, ele é calculado e não se digita (fica cinza).' },
        { termo: 'Margem mínima (ADM)', texto: 'margem mínima (%) da conta; só o administrador vê e muda. É diferente da "Margem Mín" da categoria de venda e da do produto: aquelas seguram o pedido na Margem; esta define o Custo total. Preenchida, o Custo total sai do caso mais apertado da conta: a folga entre a menor marg da conta e a Margem mínima, tirada a comissão (no ML, a maior entre Clássico e Premium) e dividida por 1 mais a menor marg; arredondado para baixo, nunca abaixo de 0. Frete não entra. Ex.: Shopee aguiar, menor marg 35,6%, comissão 14% e Margem mínima 25%: (35,6% − 25%) × (1 − 14%) ÷ 1,356 = 6,7% → Custo total 6%. Sem nenhuma marg, o Custo total fica vazio. Muda sozinho quando mudam a Margem mínima, uma marg ou a comissão. Apagando a Margem mínima, o Custo total fica com o último valor e volta a ser digitado. Se ficar abaixo de Afiliado + Desconto + Ads, grava assim mesmo e a caixinha do Custo total fica com a borda vermelha até alguém corrigir: enquanto isso, dá para baixar Afiliado, Desconto ou Ads um de cada vez, mesmo que a soma ainda não caiba; o que aumenta a soma continua recusado.' },
      ],
    },
    {
      titulo: 'Barra de botões',
      itens: [
        { termo: 'Buscar', texto: 'procura a conta pelo nome, plataforma ou e-mail.' },
        { termo: 'Plataforma e Contas', texto: 'escolhem quais linhas aparecem.' },
        { termo: 'Layout', texto: 'mostra ou esconde Catálogo ML, Equipe, Kit, Comissão e Obs; fica lembrado neste navegador.' },
        { termo: 'Equipes', texto: 'mostra só as contas das equipes marcadas (ou "Sem equipe").' },
        { termo: 'Excel', texto: 'baixa as contas que estão na tela (busca e filtros), só com as colunas à mostra.' },
        { termo: 'Vincular Integrações', texto: 'liga as contas sem integração à conexão do marketplace; não troca vínculo que já existe.' },
        { termo: 'Adicionar Conta', texto: 'cria uma conta nova; no ML ela já nasce com Clássico e Premium, com a comissão usual da categoria.' },
      ],
    },
    {
      titulo: 'Passo a passo',
      numerada: true,
      itens: [
        'Crie a conta em Adicionar Conta.',
        'Preencha kit, comissão, margens e fretes (ADM).',
        'Clique em Vincular Integrações.',
        'Confira se a Equipe apareceu.',
        'Volte à aba Tabela de Preços e use Atualizar › Conferir.',
      ],
    },
    {
      titulo: 'Observação',
      itens: [
        'Afiliado, Desconto, Ads, Custo total e Margem mínima não mudam o preço da Tabela.',
        'Conta sem integração não envia preço e acende o cartão vermelho.',
        'Mudar as contas, Vincular Integrações e Adicionar Conta exigem a permissão de editar Contas.',
      ],
    },
  ],
}
useAjuda(() => (tab.value === 'tabela' ? AJUDA_TABELA : tab.value === 'contas' ? AJUDA_CONTAS : null))

// Quem vê o quê na linha dos botões: os departamentos (e as sub-abas) são da
// Tabela de Preços; o botão Faturamento, da permissão Faturamento.
const podePrecos = useCan('tabela_precos', 'view')
const podeFaturamento = useCan('faturamento', 'view')

const canEditContas = useCan('tabela_precos_contas', 'edit')
const canDeleteContas = useCan('tabela_precos_contas', 'delete')
const canEditProdutos = useCan('tabela_precos_produtos', 'edit')
const canDeleteProdutos = useCan('tabela_precos_produtos', 'delete')
// "Atualizar anúncio" do botão Atualizar ▾ (M-14, 18/11/2026): a mesma regra
// do envio de sempre (o POST /api/pricing/push pede tabela_precos editar).
// Sem ela a opção não aparece; o aviãozinho da célula não mudou.
const podeEnviarPreco = useCan('tabela_precos', 'edit')
// Conferência de preços dos anúncios (02/11/2026): o botão "Margem" é só do
// administrador. Desde o M-3 (17/11/2026) o resultado — cartão vermelho e
// "(!) anúncio X" — aparece para todos que veem a Tabela; desde o M-7
// (18/11/2026) "Conferir anúncios" também é de todos e o campo "Reposição
// [N] dias" da barra é só do administrador. O useCan libera tudo para admin,
// então aqui é o papel mesmo.
const isAdmin = useIsAdmin()

// Departments + their subtypes are loaded from /api/segments at boot.
// Icon mapping is by slug — unknown slugs fall back to a generic Tags icon.
type DeptKey = string

const DEPT_ICONS: Record<string, any> = {
  celular: Smartphone,
  mala: Briefcase,
  eletro: Zap,
}

// Categorias que não aparecem mais na barra. "Catálogo" saiu em 06/10/2026:
// o catálogo do ML virou o grupo "ML Catálogo" dentro de cada categoria
// (ligado por conta na aba Contas). O segmento continua no banco, só some
// daqui — vale para a lista da API e para a lista fixa abaixo.
const DEPTS_FORA_DA_BARRA = new Set(['catalogo'])

// M-23 (23/11/2026): os botões em ordem alfabética — Celular, Eletro, Mala —
// e o Faturamento depois deles (lib/categorias.ts, a lista de todas as telas).
const DEPARTMENTS_FALLBACK = [
  { value: 'celular', label: 'Celular', icon: Smartphone },
  { value: 'eletro', label: 'Eletro', icon: Zap },
  { value: 'mala', label: 'Mala', icon: Briefcase },
]

const DEPARTMENTS = ref<{ value: string; label: string; icon: any }[]>([...DEPARTMENTS_FALLBACK])

// "Queima de estoque" saiu da Mala em 07/10/2026 (segmento desativado; era o
// último tipo, os outros não mudam de posição).
const TYPE_HEADERS_FALLBACK: Record<string, string[]> = {
  celular: ['Acessórios', 'Diversos', 'Regular', 'Robusto', 'Apple'],
  eletro: ['1', '2', '3', '4', '5'],
  mala: ['Acessórios', '12"', '18" e 20"', '24" acima'],
}

const TYPE_HEADERS = ref<Record<string, string[]>>({ ...TYPE_HEADERS_FALLBACK })

type SegmentRow = {
  id: string
  parent_id: string | null
  name: string
  slug: string
  sort_order: number
  active: boolean
}

// Raw segments list — kept alongside TYPE_HEADERS because the slot editor
// popover needs to resolve `account.segment_id` (department root) to its
// ordered children for the per-slot dropdowns.
const allSegments = ref<SegmentRow[]>([])

async function loadSegments() {
  try {
    const rows = await api<SegmentRow[]>('/api/segments')
    allSegments.value = rows
    // M-23 (23/11/2026): a barra segue a ordem alfabética das categorias
    // (Celular, Eletro, Mala), não o sort_order do banco (Celular, Mala,
    // Eletro); um segmento-raiz que não seja categoria vem depois, no
    // sort_order. Os TIPOS de cada categoria (as colunas/slots) continuam no
    // sort_order — isso é dado, não muda.
    const roots = ordenarCategorias(
      rows
        .filter((r) => r.parent_id === null && r.active && !DEPTS_FORA_DA_BARRA.has(r.slug))
        .sort((a, b) => a.sort_order - b.sort_order),
      (r) => r.slug,
    )
    if (roots.length === 0) return

    DEPARTMENTS.value = roots.map((r) => ({
      value: r.slug,
      label: r.name,
      icon: DEPT_ICONS[r.slug] ?? Tags,
    }))

    const next: Record<string, string[]> = {}
    for (const root of roots) {
      const children = rows
        .filter((r) => r.parent_id === root.id && r.active)
        .sort((a, b) => a.sort_order - b.sort_order)
        .map((r) => r.name)
      next[root.slug] = children.length
        ? children
        : (TYPE_HEADERS_FALLBACK[root.slug] ?? [])
    }
    TYPE_HEADERS.value = next
  } catch {
    /* keep fallback */
  }
}

// ============================================================ slot editor
// Compact per-account popover that lets the user pick which segment
// slot{1..5}_segment_id points to. Default mapping mirrors the department
// children's sort_order; this popover only matters when the user wants to
// remap (e.g. swap Acessórios/Diversos for one specific account).
const slotPopoverAccountId = ref<string | null>(null)
const slotPopoverValues = ref<Array<string | null>>([null, null, null, null, null])
// Como a janela abriu (para mandar só o que a pessoa mudou) e, na conta ML com
// par, em que campos o Clássico e o Premium estão diferentes.
const slotPopoverAbertos = ref<Array<string | null>>([null, null, null, null, null])
const slotPopoverDiferentes = ref<boolean[]>([false, false, false, false, false])
const slotPopoverSaving = ref(false)
const slotPopoverError = ref<string | null>(null)

// Conta ML com par (M-53): cada campo abre com o slot do Clássico, ou o do
// Premium se o do Clássico está vazio (atlas/fiore têm o Clássico sem slots e
// o Premium preenchido). Os dois preenchidos e diferentes → aviso no campo.
function openSlotPopover(acc: Account) {
  const par = parDe(acc)
  const valores: Array<string | null> = []
  const diferentes: boolean[] = []
  for (let i = 1; i <= 5; i++) {
    const k = `slot${i}_segment_id`
    const c: string | null = ((par ? par.classico : acc) as any)[k] ?? null
    const p: string | null = par ? ((par.premium as any)[k] ?? null) : null
    valores.push(c ?? p)
    diferentes.push(c != null && p != null && c !== p)
  }
  slotPopoverAccountId.value = acc.id
  slotPopoverValues.value = valores
  slotPopoverAbertos.value = [...valores]
  slotPopoverDiferentes.value = diferentes
  slotPopoverError.value = null
}
function closeSlotPopover() {
  slotPopoverAccountId.value = null
  slotPopoverError.value = null
}
function segmentChildrenFor(acc: Account): SegmentRow[] {
  if (!acc.segment_id) return []
  return allSegments.value
    .filter((s) => s.parent_id === acc.segment_id && s.active)
    .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
}
// Conta ML com par: só vão os slots que a pessoa mudou (o servidor copia para
// o Premium; mandar os 5 apagaria os do Premium que a janela não mostrou) e,
// nada mudado, não grava nada. Conta sem par: os 5, como sempre.
async function saveSlotPopover(accId: string) {
  const acc = accounts.value.find((a) => a.id === accId)
  const par = acc ? parDe(acc) : null
  const body: Record<string, string | null> = {}
  for (let i = 0; i < 5; i++) {
    if (!par || slotPopoverValues.value[i] !== slotPopoverAbertos.value[i]) {
      body[`slot${i + 1}_segment_id`] = slotPopoverValues.value[i]
    }
  }
  if (!Object.keys(body).length) {
    closeSlotPopover()
    return
  }
  slotPopoverSaving.value = true
  slotPopoverError.value = null
  try {
    const updated = await api<Account>(`/api/pricing/accounts/${accId}`, {
      method: 'PATCH',
      body,
    })
    const idx = accounts.value.findIndex((a) => a.id === accId)
    if (idx >= 0) accounts.value[idx] = updated
    // O servidor gravou os mesmos slots no Premium: a cópia local acompanha.
    if (par) {
      const campos = Object.keys(body).flatMap((k) => [k, k.replace('_id', '_name')])
      espelharNaIrma(idx >= 0 ? accounts.value[idx] : updated, campos)
    }
    closeSlotPopover()
  } catch (e: any) {
    slotPopoverError.value = e?.data?.detail?.code ?? 'save_failed'
  } finally {
    slotPopoverSaving.value = false
  }
}

const PLATFORMS = [
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'carrefour', label: 'Carrefour' },
  { value: 'magalu', label: 'Magalu' },
  { value: 'mercadolivre', label: 'ML' },
  { value: 'netshoes', label: 'Netshoes' },
  { value: 'shein', label: 'Shein' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'temu', label: 'Temu' },
  { value: 'tiktok', label: 'TikTok' },
] as const

// Troca de plataforma na célula: conta ML só nasce pelo "+ Adicionar Conta"
// (com Clássico e Premium juntos), então "ML" só aparece para quem já é do ML
// (o servidor recusa a troca para ML com 400 tipo_obrigatorio).
function plataformasDaTroca(acc: Account) {
  return PLATFORMS.filter((p) => p.value !== 'mercadolivre' || acc.platform === 'mercadolivre')
}

// Recusa do servidor na troca de plataforma: a mensagem dele, no campo.
const erroPlataforma = ref<{ id: string; mensagem: string } | null>(null)

function platformLabel(p: string) {
  return PLATFORMS.find((x) => x.value === p)?.label ?? p
}

function comparePlatformLabels(a: string, b: string) {
  return platformLabel(a).localeCompare(platformLabel(b), 'pt-BR', { sensitivity: 'base' })
}

// Department lives in the URL (?dept=mala) so it survives the [tab].vue
// re-mount that happens when the user clicks a different sub-tab. Default
// is 'celular' when the query is absent.
// ?dept= que não está na barra (link antigo ?dept=catalogo, slug digitado
// errado) também abre o Celular, em vez de uma tela vazia sem botão marcado.
const department = computed<DeptKey>({
  get() {
    const q = route.query.dept
    const v = typeof q === 'string' && q ? q : 'celular'
    return DEPARTMENTS.value.some((d) => d.value === v) ? v : 'celular'
  },
  set(v) {
    // Com o Faturamento aberto, escolher um departamento volta para a Tabela
    // de Preços daquele departamento.
    if (tab.value === 'faturamento') {
      router.push({ path: '/pricing/tabela', query: { ...route.query, dept: v } })
      return
    }
    router.replace({ path: route.path, query: { ...route.query, dept: v } })
  },
})

// Quantas colunas de kit cada departamento mostra na aba Produtos
// (celular vai até kit 8; mala e eletro usam só o kit 1).
const KIT_COUNT_BY_DEPT: Record<string, number> = { celular: 8, mala: 1, eletro: 1 }
const kitCount = computed(() => KIT_COUNT_BY_DEPT[department.value] ?? 4)

// Composição de cada kit do Celular (anotação do Eduardo, 2026-08-21).
// Só informativo: aparece embaixo do "Kit N" no cabeçalho da aba Produtos
// e no hover das colunas "kit N" da calculadora. Mala e eletro têm um kit
// só, sem nome — ficam sem legenda.
// Kit 8 (Eduardo, 30/09/2026): "agora é celular + fone + óculos + airtag".
// Os custos já estavam nessa conta (A17 12.128: 560 + 40 + 320 + 22 = 942);
// só o nome tinha ficado em "Celular + Airtag".
const KIT_NOMES_CELULAR: Record<number, string> = {
  1: 'Celular + Fone c/ fio',
  2: 'Celular + Fone',
  3: 'Celular + Relógio',
  4: 'Celular + Fone + Relógio',
  5: 'Celular + Relógio + Airtag',
  6: 'Celular + Fone + Airtag',
  7: 'Celular + Óculos',
  8: 'Celular + Fone + Óculos + Airtag',
}
// Versão curta pro cabeçalho (coluna estreita): "Celular" vira "Cel".
function kitNome(k: number): string {
  const nome = department.value === 'celular' ? KIT_NOMES_CELULAR[k] : undefined
  return nome ? nome.replace('Celular', 'Cel') : ''
}
function kitTitulo(k: number): string {
  const nome = department.value === 'celular' ? KIT_NOMES_CELULAR[k] : undefined
  return nome ? `Kit ${k} = ${nome}` : `Kit ${k}`
}

// Colunas fixas da grade (02/11/2026): Produto · Estoque · 7d · 30d ·
// Reposição · kit 1..8 (celular) ou custo (mala/eletro). A posição (left)
// de cada uma sai da soma das larguras anteriores — antes eram números
// soltos no template. Menu "Layout ▾" da barra (M-2, 17/11/2026; antes era o
// botão "Mostrar kits"): "Kit" (só celular) mostra os 8 custos de kit e
// "Estoque" mostra Estoque, 7d, 30d e Reposição. A aba Produtos e o Excel
// continuam com tudo. A escolha fica lembrada neste navegador.
// Produto: olho 16 + espaço 4 + nome 180 + margens 16.
const LARGURA_PRODUTO = 216
const LARGURA_FIXA = 56
const LARGURA_REPOSICAO = 64
const ocultarKits = ref(false)
const ocultarEstoque = ref(false)
// As caixas do menu Layout são "mostrar" (marcada = aparece).
const mostrarKits = computed({
  get: () => !ocultarKits.value,
  set: (v: boolean) => { ocultarKits.value = !v },
})
const mostrarEstoque = computed({
  get: () => !ocultarEstoque.value,
  set: (v: boolean) => { ocultarEstoque.value = !v },
})
const kitsNaGrade = computed(() =>
  department.value === 'celular' && !ocultarKits.value ? kitCount.value : 0,
)
const colunasFixas = computed(() => {
  const colunas: [string, number][] = [['produto', LARGURA_PRODUTO]]
  // Sem as 4 de estoque, os kits (ou o custo) encostam no Produto: o left
  // de cada um é recalculado aqui, e o template tira os th/td junto.
  if (!ocultarEstoque.value) {
    colunas.push(
      ['estoque', LARGURA_FIXA],
      ['v7', LARGURA_FIXA],
      ['v30', LARGURA_FIXA],
      ['reposicao', LARGURA_REPOSICAO],
    )
  }
  if (department.value === 'celular') {
    for (let k = 1; k <= kitsNaGrade.value; k++) colunas.push([`kit${k}`, LARGURA_FIXA])
  } else {
    colunas.push(['custo', LARGURA_FIXA])
  }
  let left = 0
  return colunas.map(([chave, largura]) => {
    const coluna = { chave, largura, left }
    left += largura
    return coluna
  })
})
const colunaFixaPorChave = computed(() => new Map(colunasFixas.value.map((c) => [c.chave, c])))
function estiloFixa(chave: string): Record<string, string> {
  const c = colunaFixaPorChave.value.get(chave)
  return c ? { left: `${c.left}px`, minWidth: `${c.largura}px` } : {}
}

// =========================================================== accounts state

type Account = {
  id: string
  user_id: string
  name: string
  platform: string
  listing_type: string | null
  department: string
  // Kit, comissão, margens e fretes: null para quem não é ADM (M-10,
  // 18/11/2026) — o /accounts e o /grid não mandam; o preço vem pronto.
  kit_number: number | null
  commission: string | number | null
  margin1: string | number | null
  shipping1: string | number | null
  margin2: string | number | null
  shipping2: string | number | null
  margin3: string | number | null
  shipping3: string | number | null
  margin4: string | number | null
  shipping4: string | number | null
  margin5: string | number | null
  shipping5: string | number | null
  email: string | null
  phone: string | null
  observation: string | null
  discount: string | null
  affiliate: string | null
  ads: string | null
  coupon: string | null
  offer: string | null
  // M-11 (18/11/2026): campos próprios da aba Contas (% de 0 a 100; vem como
  // texto decimal do servidor, "4.00"). Desconto até, Cupom até e Oferta
  // limite saíram da tela na M-55 (25/11/2026); ficam no banco, fora da tela.
  afiliado_pct: string | number | null
  // M-55: Desconto (%), só anotação como o Afiliado. M-56: Custo total (%) =
  // teto de Afiliado + Desconto + Ads, conferido no servidor; todos veem, só o
  // ADM muda. Nenhum dos dois entra no preço.
  desconto_pct?: string | number | null
  custo_total_pct?: string | number | null
  // M-85 (26/11/2026, migration 0519): Margem mínima (%) da conta, só o ADM vê e
  // muda (para os outros o servidor manda null). M-86: preenchida, o Custo total
  // acima é calculado pelo servidor e não se digita.
  margem_minima_pct?: string | number | null
  // M-53 (25/11/2026, migration 0511): o mesmo valor no Clássico e no Premium
  // da mesma conta ML de kit; nas outras linhas, null.
  par_id?: string | null
  has_password: boolean
  integration_id: string | null
  segment_id: string | null
  sort_order: number
  created_at: string
  updated_at: string
  // Slot ↔ segment binding. Each slot{N}_segment_id pins which segment the
  // margin{N}/shipping{N} pair applies to. The router resolves and returns
  // the segment name alongside for read-only display.
  slot1_segment_id: string | null
  slot2_segment_id: string | null
  slot3_segment_id: string | null
  slot4_segment_id: string | null
  slot5_segment_id: string | null
  slot1_segment_name: string | null
  slot2_segment_name: string | null
  slot3_segment_name: string | null
  slot4_segment_name: string | null
  slot5_segment_name: string | null
  // Catálogo ML (06/10/2026): 'kit' = conta normal; 'catalogo' = coluna de
  // catálogo ligada numa conta ML de kit ("filha", conta_base_id = a conta de
  // kit). A filha herda comissão, margens, fretes e anotações da base na hora
  // do cálculo — por isso a tela sempre lê esses números da base.
  canal?: 'kit' | 'catalogo'
  conta_base_id?: string | null
  // Na conta de kit: id da coluna de catálogo dela (catálogo ligado), ou null.
  conta_catalogo_id?: string | null
  // Margem própria do catálogo (07/10/2026), só na conta de catálogo:
  // {"1".."5": margem do tipo, ou null = usa a da conta de kit}. Os
  // margin1..5 dela continuam vindo com os da base.
  margens_catalogo?: Record<string, string | number | null> | null
  // Coluna "Equipe" da aba Contas (03/11/2026): as equipes de vendas que veem
  // a conta ("Equipe 1/2/3", a mesma de Lojas), pela régua da cerca. Vazia =
  // sem equipe. A coluna de catálogo vem com as da base.
  equipes?: EquipeDaConta[]
}

type EquipeDaConta = { numero: number; nome: string }

// Global toast feedback for push / auto-match outcomes. Rendered by
// <AppToastStack /> in app.vue, so we just call success/error/warning here.
const toast = useToasts()

const accounts = ref<Account[]>([])
const accountsLoading = ref(false)
const accountsErr = ref<string | null>(null)

async function loadAccounts() {
  accountsLoading.value = true
  accountsErr.value = null
  try {
    accounts.value = await api<Account[]>('/api/pricing/accounts')
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    accountsLoading.value = false
  }
}

// =========================================================== integrations
type Integration = { id: string; name: string; platform: string }
const integrations = ref<Integration[]>([])

async function loadIntegrations() {
  try {
    integrations.value = await api<Integration[]>('/api/integrations')
  } catch {
    integrations.value = []
  }
}

const PRICING_TO_INTEG_PLATFORM: Record<string, string> = {
  mercadolivre: 'ml',
  shopee: 'shopee',
  amazon: 'amazon',
  tiktok: 'tiktok',
  temu: 'temu',
  magalu: 'magalu',
}

function integrationsForPricingPlatform(p: string): Integration[] {
  const target = PRICING_TO_INTEG_PLATFORM[p]
  if (!target) return []
  return integrations.value
    .filter((i) => i.platform === target)
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
}

function integrationName(id: string | null): string {
  if (!id) return '—'
  return integrations.value.find((i) => i.id === id)?.name ?? '—'
}

async function setAccountIntegration(acc: Account, integration_id: string | null) {
  try {
    const updated = await api<Account>(`/api/pricing/accounts/${acc.id}`, {
      method: 'PATCH',
      body: { integration_id },
    })
    Object.assign(acc, updated)
    flash(acc.id, 'integration_id')
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'save_failed'
  }
}

// Conta de catálogo ("filha") não é conta própria: na aba Contas ela aparece
// como o controle "Catálogo ML" na linha da conta de kit, e não entra nos
// contadores. Na Tabela de Preços ela vira o grupo "ML Catálogo".
function ehCatalogo(a: Account): boolean {
  return a.canal === 'catalogo'
}

// conta de kit (id) → conta de catálogo dela
const catalogoPorBase = computed(() => {
  const m = new Map<string, Account>()
  for (const a of accounts.value) {
    if (ehCatalogo(a) && a.conta_base_id) m.set(a.conta_base_id, a)
  }
  return m
})

function catalogoAtivo(acc: Account): boolean {
  return catalogoPorBase.value.has(acc.id) || !!acc.conta_catalogo_id
}

// O Catálogo ML só existe em conta de kit do Mercado Livre.
function podeTerCatalogo(acc: Account): boolean {
  return acc.platform === 'mercadolivre' && !ehCatalogo(acc)
}

function temMargemPropria(acc: Account): boolean {
  const filha = catalogoPorBase.value.get(acc.id)
  if (!filha) return false
  for (let t = 1; t <= 5; t++) if (margemPropria(filha, t) != null) return true
  return false
}

// =========================================================== conta ML em uma linha (M-53)
// Clássico e Premium da mesma conta do ML são duas linhas em pricing_accounts
// com o mesmo par_id. A aba Contas mostra UMA linha (a do Clássico) e grava
// nela; o servidor repete na do Premium — menos a comissão, que é de cada tipo
// e vai pela janela da Comissão. A grade da Tabela de Preços continua com as
// duas colunas (ela lê o /grid, não esta lista).
const linhasPorPar = computed(() => {
  const m = new Map<string, Account[]>()
  for (const a of accounts.value) {
    if (ehCatalogo(a) || !a.par_id) continue
    const linhas = m.get(a.par_id)
    if (linhas) linhas.push(a)
    else m.set(a.par_id, [a])
  }
  for (const linhas of m.values()) linhas.sort((a, b) => ordemTipo(a) - ordemTipo(b))
  return m
})

type ParMl = { classico: Account; premium: Account }

// As duas linhas da conta (Clássico primeiro). null = conta sem par na lista:
// aparece sozinha, como antes, com o tipo dela ao lado de "ML".
function parDe(acc: Account): ParMl | null {
  if (!acc.par_id || ehCatalogo(acc)) return null
  const linhas = linhasPorPar.value.get(acc.par_id)
  if (!linhas || linhas.length < 2) return null
  return { classico: linhas[0], premium: linhas[1] }
}

// A linha do Premium não aparece na aba Contas (nem nos contadores).
function escondidaNaAba(acc: Account): boolean {
  const par = parDe(acc)
  return !!par && par.classico.id !== acc.id
}

function irmaDe(acc: Account): Account | null {
  const par = parDe(acc)
  if (!par) return null
  return par.classico.id === acc.id ? par.premium : par.classico
}

// Título das janelas da conta (Comissão, Catálogo, Ads): plataforma + conta,
// com o nome como está cadastrado — "ML forpaper", "Amazon kfa".
function tituloConta(acc: Account): string {
  return `${platformLabel(acc.platform)} ${acc.name}`
}

// Conta do ML sem par na lista: a coluna Tipo ML saiu (M-53), então o tipo
// dela aparece ao lado de "ML" na coluna Plataforma.
function tipoDaContaSolta(acc: Account): string {
  if (acc.platform !== 'mercadolivre' || ehCatalogo(acc) || parDe(acc)) return ''
  const t = rotuloTipo(acc)
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : 'sem tipo'
}

// Com que comissão a conta ML nasce (o servidor decide pela categoria e
// ignora a do corpo; a linha de adicionar conta só mostra).
function comissaoPadraoMl(dept: string): string {
  return dept === 'mala' ? '14% · 19%' : '11% · 16%'
}

// O servidor repete na linha do Premium o que foi gravado no Clássico (menos
// comissão e ordem): a cópia local acompanha, para a irmã não ficar velha.
const CAMPOS_SO_DA_LINHA = new Set(['commission', 'sort_order', 'listing_type', 'platform'])
function espelharNaIrma(acc: Account, campos: string[]) {
  const irma = irmaDe(acc)
  if (!irma) return
  for (const k of campos) if (!CAMPOS_SO_DA_LINHA.has(k)) (irma as any)[k] = (acc as any)[k]
}

// ---------- Comissão da conta ML (M-53): "14% · 19%" (Clássico · Premium).
// O ADM clica e abre a janelinha "ML forpaper" com os dois campos; só grava no
// Salvar, pelo PUT /comissao-ml (frações, as duas linhas numa transação).
const TIPOS_COMISSAO_ML = [
  { chave: 'classico', rotulo: 'Clássico' },
  { chave: 'premium', rotulo: 'Premium' },
] as const
const comissaoMlId = ref<string | null>(null) // a linha do Clássico
const comissaoMlValores = reactive({ classico: '', premium: '' })
const comissaoMlSalvando = ref(false)
const comissaoMlErro = ref('')

// 0.14 → "14"; 0.165 → "16,5"; vazio → "".
function textoComissao(v: string | number | null): string {
  const n = v == null || v === '' ? null : Number(v)
  if (n == null || Number.isNaN(n)) return ''
  return (n * 100).toFixed(2).replace(/\.?0+$/, '').replace('.', ',')
}

// A célula do par: "14% · 19%" (Clássico · Premium); com decimal, "15,5%".
function textoComissaoPar(par: ParMl): string {
  const pct = (v: string | number | null) => (textoComissao(v) ? `${textoComissao(v)}%` : '—')
  return `${pct(par.classico.commission)} · ${pct(par.premium.commission)}`
}

function abrirComissaoMl(acc: Account) {
  const par = parDe(acc)
  if (!par || !isAdmin.value) return
  comissaoMlValores.classico = textoComissao(par.classico.commission)
  comissaoMlValores.premium = textoComissao(par.premium.commission)
  comissaoMlErro.value = ''
  comissaoMlId.value = acc.id
}

function fecharComissaoMl() {
  if (comissaoMlSalvando.value) return
  comissaoMlId.value = null
  comissaoMlErro.value = ''
}

function alternarJanelaComissao(acc: Account, aberta: boolean) {
  if (aberta) abrirComissaoMl(acc)
  else fecharComissaoMl()
}

function textoErroComissaoMl(e: any): string {
  const d = e?.data?.detail
  const status = e?.status ?? e?.response?.status
  if (d?.code === 'sem_par') return 'Esta conta não tem Clássico e Premium juntos — recarregue a página.'
  if (status === 403) return 'Só o administrador muda a comissão.'
  if (status === 422) return 'Use um número de 0 a 99,99, ex.: 14 ou 16,5.'
  return d?.mensagem || d?.message || 'Não foi possível salvar; tente de novo.'
}

async function salvarComissaoMl() {
  const acc = accounts.value.find((a) => a.id === comissaoMlId.value)
  const par = acc ? parDe(acc) : null
  if (!par || !isAdmin.value || comissaoMlSalvando.value) return
  const body: { classico: number | null; premium: number | null } = { classico: null, premium: null }
  for (const tipo of ['classico', 'premium'] as const) {
    // 100% (ou o que arredonda para 100%) o servidor recusa: a comissão é
    // menor que o preço.
    const v = lerPct(comissaoMlValores[tipo].replace('%', ''))
    const fracao = v == null ? null : Number((v / 100).toFixed(4))
    if (v === undefined || (fracao != null && fracao >= 1)) {
      comissaoMlErro.value = `${tipo === 'classico' ? 'Clássico' : 'Premium'}: use um número de 0 a 99,99, ex.: 14 ou 16,5.`
      return
    }
    body[tipo] = fracao
  }
  const igual = (v: string | number | null, novo: number | null) =>
    (v == null || v === '' ? null : Number(Number(v).toFixed(4))) === novo
  if (igual(par.classico.commission, body.classico) && igual(par.premium.commission, body.premium)) {
    fecharComissaoMl()
    return
  }
  comissaoMlSalvando.value = true
  comissaoMlErro.value = ''
  try {
    const r = await api<{ classico: Account; premium: Account }>(
      `/api/pricing/accounts/${par.classico.id}/comissao-ml`,
      { method: 'PUT', body },
    )
    Object.assign(par.classico, r.classico)
    Object.assign(par.premium, r.premium)
    flash(par.classico.id, 'commission')
    comissaoMlSalvando.value = false
    fecharComissaoMl()
  } catch (e: any) {
    comissaoMlErro.value = textoErroComissaoMl(e)
  } finally {
    comissaoMlSalvando.value = false
  }
}

const accountsByDept = computed(() => {
  const m: Record<DeptKey, Account[]> = { celular: [], mala: [], eletro: [] }
  for (const a of accounts.value) {
    // A coluna de catálogo e a linha do Premium de uma conta ML com par não
    // são contas próprias na aba Contas (nem nos contadores — M-53).
    if (ehCatalogo(a) || escondidaNaAba(a)) continue
    const k = a.department as DeptKey
    if (!m[k]) m[k] = []
    m[k].push(a)
  }
  for (const k of Object.keys(m) as DeptKey[]) {
    m[k].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
  }
  return m
})

const accountsCurrent = computed(() => accountsByDept.value[department.value] ?? [])

// Pares marg/frete da aba Contas: um por tipo ATIVO do departamento (os
// títulos do cabeçalho). Desenhar sempre 5 desalinhava a tabela quando um
// tipo é desativado (Mala sem "Queima de estoque", 07/10/2026).
const nTiposAba = computed(() => (TYPE_HEADERS.value[department.value] ?? []).length)

// Layout ▾ da aba Contas (M-51, 25/11/2026): caixas para mostrar ou esconder
// colunas, como o Layout da Tabela de Preços (marcada = aparece; lembrado
// neste navegador). Kit e Comissão só entram na lista de quem já vê essas
// colunas (ADM, M-10). Desconto e Custo total (M-55/M-56) ficam sempre à mostra,
// e a Margem mínima também (só para o ADM, M-85).
// O menu mostra só a caixa e o nome (M-61); a explicação fica na Ajuda.
const COLUNAS_LAYOUT_CONTAS = [
  { chave: 'catalogo', rotulo: 'Catálogo ML', soAdmin: false },
  { chave: 'equipe', rotulo: 'Equipe', soAdmin: false },
  { chave: 'kit', rotulo: 'Kit', soAdmin: true },
  { chave: 'comissao', rotulo: 'Comissão', soAdmin: true },
  { chave: 'obs', rotulo: 'Obs', soAdmin: false },
]
const CHAVE_COLUNAS_OCULTAS_CONTAS = 'precos.contas.colunasOcultas'
// Guarda as DESMARCADAS: vazio = tudo à mostra (o começo).
const colunasOcultasContas = ref<string[]>([])
const opcoesLayoutContas = computed(() => COLUNAS_LAYOUT_CONTAS.filter((c) => !c.soAdmin || isAdmin.value))

function mostraColunaConta(chave: string): boolean {
  return !colunasOcultasContas.value.includes(chave)
}

// O que veio do navegador ("kit,obs"): só as chaves que existem.
function lerColunasOcultasContas(texto: string | null): string[] {
  const validas = new Set(COLUNAS_LAYOUT_CONTAS.map((c) => c.chave))
  return (texto || '').split(',').map((s) => s.trim()).filter((s) => validas.has(s))
}

// Colunas da tabela da aba Contas (o colspan das linhas de grupo e de aviso):
// Plataforma, Conta, Catálogo ML, Equipe, [Kit, Comissão e os pares
// marg/frete — só ADM, M-10], as anotações (a Margem mínima só ADM, M-85) e a
// lixeira. (Tipo ML saiu na M-53.) Menos as que o Layout esconde (M-51).
const colunasContas = computed(() =>
  2
  + (mostraColunaConta('catalogo') ? 1 : 0)
  + (mostraColunaConta('equipe') ? 1 : 0)
  + (isAdmin.value
    ? (mostraColunaConta('kit') ? 1 : 0) + (mostraColunaConta('comissao') ? 1 : 0) + nTiposAba.value * 2
    : 0)
  + camposAnotacao.value.length - (mostraColunaConta('obs') ? 0 : 1)
  + 1,
)

// SSH-style contas filters
const contasSearch = ref('')
// Filtros Plataforma ▾ e Equipe ▾ (M-9, 18/11/2026; antes eram select):
// caixas de marcar, com "Marcar todas" e "Limpar". Guardam o que está
// DESMARCADO: vazio = tudo marcado (o começo, e como voltam ao recarregar a
// página); opção que aparece ao trocar de aba já vem marcada.
const contasPlataformasDesmarcadas = ref<string[]>([])
// Equipe: o número da equipe ('101'...) ou 'sem' = contas sem equipe. Uma
// conta vista por duas equipes aparece se qualquer uma delas estiver marcada.
const SEM_EQUIPE = 'sem'
const contasEquipesDesmarcadas = ref<string[]>([])
// Contas ▾ (M-51, 25/11/2026): pelo nome da conta, como o Contas ▾ da Tabela
// de Preços ("atlas" vale para a atlas de todas as plataformas). Guarda o nome
// (minúsculo) DESMARCADO: vazio = tudo marcado = sem filtro.
const contasNomesDesmarcados = ref<string[]>([])
const buscaNomesContas = ref('')

function chaveNomeConta(a: Account): string {
  return (a.name || '').trim().toLocaleLowerCase('pt-BR')
}

// Marca/desmarca uma opção do filtro (devolve a lista nova).
function alternarNaLista(lista: string[], valor: string): string[] {
  return lista.includes(valor) ? lista.filter((v) => v !== valor) : [...lista, valor]
}

function equipesDaConta(a: Account): EquipeDaConta[] {
  return a.equipes ?? []
}

// Só o número (M-9): "Equipe 2" → "2". Nome fora desse formato fica como veio.
function numeroDaEquipe(e: EquipeDaConta): string {
  return e.nome.replace(/^Equipe\s+/i, '')
}

function rotuloEquipes(a: Account): string {
  const eq = equipesDaConta(a)
  return eq.length ? eq.map(numeroDaEquipe).join(' / ') : 'Sem equipe'
}

// As chaves da conta no filtro: o número de cada equipe, ou 'sem'.
function chavesEquipe(a: Account): string[] {
  const eq = equipesDaConta(a)
  return eq.length ? eq.map((e) => String(e.numero)) : [SEM_EQUIPE]
}

function passaFiltroEquipe(a: Account, desmarcadas: string[]): boolean {
  if (!desmarcadas.length) return true
  return chavesEquipe(a).some((k) => !desmarcadas.includes(k))
}

// As opções do filtro: as equipes das contas desta aba (celular/mala/eletro),
// em ordem de número, e "Sem equipe" no fim se houver conta sem nenhuma.
function opcoesEquipe(contas: Account[]): { valor: string; rotulo: string }[] {
  const porNumero = new Map<number, string>()
  let semEquipe = false
  for (const a of contas) {
    const eq = equipesDaConta(a)
    if (!eq.length) semEquipe = true
    for (const e of eq) porNumero.set(e.numero, numeroDaEquipe(e))
  }
  const out = Array.from(porNumero.entries())
    .sort(([a], [b]) => a - b)
    .map(([numero, rotulo]) => ({ valor: String(numero), rotulo }))
  if (semEquipe) out.push({ valor: SEM_EQUIPE, rotulo: 'Sem equipe' })
  return out
}

const equipesPresentesNoDept = computed(() => opcoesEquipe(accountsCurrent.value))

// Só aparece quando há mais de uma opção (como no Faturamento): quem é de uma
// equipe só vê as contas dela. Com uma opção desta aba desmarcada continua na
// tela, para dar para marcar de novo (o filtro sobrevive à troca de aba).
const mostrarFiltroEquipe = computed(() => {
  const opcoes = equipesPresentesNoDept.value
  return opcoes.length > 1 || opcoes.some((o) => contasEquipesDesmarcadas.value.includes(o.valor))
})

// Opções do Contas ▾: os nomes das contas desta aba, sem repetir, em ordem.
const opcoesNomesContas = computed<{ valor: string; rotulo: string }[]>(() => {
  const porChave = new Map<string, string>()
  for (const a of accountsCurrent.value) {
    const chave = chaveNomeConta(a)
    if (!porChave.has(chave)) porChave.set(chave, (a.name || '').trim())
  }
  return Array.from(porChave, ([valor, rotulo]) => ({ valor, rotulo })).sort((x, y) =>
    x.rotulo.localeCompare(y.rotulo, 'pt-BR', { sensitivity: 'base' }),
  )
})

// Lista do Contas ▾ depois da busca do próprio menu.
const opcoesNomesContasBuscadas = computed(() => {
  const q = buscaNomesContas.value.trim().toLocaleLowerCase('pt-BR')
  return q ? opcoesNomesContas.value.filter((o) => o.valor.includes(q)) : opcoesNomesContas.value
})

// "Contas (2)": com filtro parcial, o botão mostra quantas estão marcadas.
const rotuloNomesContas = computed(() => {
  const total = opcoesNomesContas.value.length
  const marcadas = opcoesNomesContas.value.filter((o) => !contasNomesDesmarcados.value.includes(o.valor)).length
  return marcadas < total ? `Contas (${marcadas})` : 'Contas'
})

// "Marcar todas" / "Limpar" valem para a lista à vista (com busca, só as encontradas).
function marcarNomesContas(marcar: boolean) {
  const visiveis = opcoesNomesContasBuscadas.value.map((o) => o.valor)
  contasNomesDesmarcados.value = marcar
    ? contasNomesDesmarcados.value.filter((v) => !visiveis.includes(v))
    : Array.from(new Set([...contasNomesDesmarcados.value, ...visiveis]))
}

const accountsFiltered = computed(() => {
  const q = contasSearch.value.trim().toLowerCase()
  const pd = contasPlataformasDesmarcadas.value
  const ed = contasEquipesDesmarcadas.value
  const nd = contasNomesDesmarcados.value
  return accountsCurrent.value.filter((a) => {
    if (pd.includes(a.platform)) return false
    if (!passaFiltroEquipe(a, ed)) return false
    if (nd.length && nd.includes(chaveNomeConta(a))) return false
    if (!q) return true
    return (
      a.name.toLowerCase().includes(q) ||
      (a.platform || '').toLowerCase().includes(q) ||
      (a.email || '').toLowerCase().includes(q)
    )
  })
})

const accountsGrouped = computed<{ platform: string; label: string; rows: Account[] }[]>(() => {
  const groups = new Map<string, Account[]>()
  for (const a of accountsFiltered.value) {
    const key = a.platform || 'outros'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(a)
  }
  const order = ['amazon', 'magalu', 'mercadolivre', 'shopee', 'shein', 'temu', 'aliexpress', 'tiktok', 'carrefour', 'netshoes']
  return Array.from(groups.entries())
    .sort(([a], [b]) => {
      const ia = order.indexOf(a)
      const ib = order.indexOf(b)
      if (ia === -1 && ib === -1) return a.localeCompare(b)
      if (ia === -1) return 1
      if (ib === -1) return -1
      return ia - ib
    })
    .map(([platform, rows]) => ({
      platform,
      label: platformLabel(platform).toUpperCase(),
      rows: rows.slice().sort((a, b) =>
        (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' }) ||
        ordemTipo(a) - ordemTipo(b),
      ),
    }))
})

const platformsPresentInDept = computed(() => {
  const set = new Set<string>()
  for (const a of accountsCurrent.value) set.add(a.platform)
  return Array.from(set).sort(comparePlatformLabels)
})

// =========================================================== products state

type PricingProduct = {
  id: string
  user_id: string
  product_id: string | null
  sku: string
  name: string
  department: string
  product_type: number
  bling_cost_price: string | number | null
  cost_kit1: string | number
  cost_kit2: string | number | null
  cost_kit3: string | number | null
  cost_kit4: string | number | null
  cost_kit5: string | number | null
  cost_kit6: string | number | null
  cost_kit7: string | number | null
  cost_kit8: string | number | null
  // Preço base do anúncio de catálogo do ML (custo, como os kits). NULL =
  // sem preço de catálogo: as colunas ML Catálogo mostram "—" e não enviam.
  preco_catalogo: string | number | null
  description: string | null
  model: string | null
  ean: string | null
  is_active: boolean
  fotos_url: string | null
  fotos_path: string | null
  fotos_count: number | null
  videos_count: number | null
  // Embalagens (caixa, arte em PDF…) = subpasta "Embalagens" dentro da pasta
  // de fotos no MEGA. count NULL = nunca contado (≠ 0 = pasta vazia).
  embalagens_url: string | null
  embalagens_path: string | null
  embalagens_count: number | null
  midias_contadas_em: string | null
  prioridade_estoque: string | null
  // Colunas Modelo / Nº do certificado / Validade (05/11/2026). Desde a M-69
  // (26/11/2026): o certificado da coluna SKU da Importação › Certificações
  // (sem ela, o gravado na linha, certificacao_id). Modelo só leitura.
  certificacao?: CertificacaoDoProduto | null
  certificacao_id?: string | null
  // Os códigos da linha caem em certificados DIFERENTES na coluna SKU: os
  // nomes deles. O sistema não escolhe sozinho (vale o gravado) e a tela avisa.
  certificacao_conflito?: string[]
  // Prazo da garantia do produto, em meses (0..120; 0 = sem garantia, "se for
  // avulso não tem garantia"; NULL = sem prazo cadastrado). Coluna "Garantia
  // (meses)" da aba Produtos (06/11/2026): o painel de Garantia calcula o fim
  // de cada venda por este número.
  garantia_meses: number | null
  created_at: string
  updated_at: string
}
type CertificacaoDoProduto = {
  id: string
  produto: string | null
  modelo: string | null
  certificado: string | null
  numero: string | null
  fim: string | null
  // Garantia (meses) do certificado (06/11/2026). Com valor, a coluna
  // Garantia (meses) do produto vem dele; null = o produto tem a dele.
  garantia_meses?: number | null
}

// Tags de estoque válidas pra coluna Prioridade (mesmo conjunto dos sufixos
// de SKU: dg053.ci, dg053.sp…). Eduardo (2026-08-27): "a tag que eu colocar
// la, o sku com a tag, ja deve trocar, porque a prioridade e ele".
const TAGS_PRIORIDADE = ['ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd'] as const

// Certificado na aba Produtos (Celular e Eletro; Mala não tem certificado).
// O servidor acha a linha da Certificações pela coluna SKU dela (M-69).
const mostraCertificado = computed(() => department.value !== 'mala')
function certNumero(c: CertificacaoDoProduto | null | undefined): string {
  if (!c) return '—'
  if (c.numero?.trim()) return c.numero
  return c.certificado === 'isento' ? 'Isento' : 'sem número'
}
// Validade em DIAS que faltam (Eduardo, 05/11/2026: "coloque a validade por
// dias"); a data fica no balão. Conta pelo dia de hoje em São Paulo.
function certDias(c: CertificacaoDoProduto | null | undefined): number | null {
  if (!c?.fim) return null
  const dia = (iso: string) => { const [a, m, d] = iso.split('-').map(Number); return Date.UTC(a, m - 1, d) }
  return Math.round((dia(c.fim) - dia(isoToday())) / 86_400_000)
}
function certValidade(c: CertificacaoDoProduto | null | undefined): string {
  const n = certDias(c)
  if (n === null) return '—'
  if (n === 0) return 'vence hoje'
  if (n < 0) return `vencido há ${-n} ${n === -1 ? 'dia' : 'dias'}`
  return `${n.toLocaleString('pt-BR')} ${n === 1 ? 'dia' : 'dias'}`
}
// Mesmas cores da Certificações: vermelho vencido, âmbar a menos de 30 dias.
function certValidadeClasse(c: CertificacaoDoProduto | null | undefined): string {
  const n = certDias(c)
  if (n === null) return ''
  if (n < 0) return 'text-red-700 font-medium'
  return n < 30 ? 'text-amber-700 font-medium' : ''
}
// Coluna Modelo (M-69, 26/11/2026): só leitura. Mostra o certificado do
// produto, que é definido na coluna SKU da Importação › Certificações (o SKU
// sem a tag: dg019 vale para dg019.ra, dg019.sp…). Antes ela era uma lista
// para escolher e, sem escolha, valia o código no fim do nome; as duas
// coisas saíram (a escolha que já estava gravada continua valendo quando a
// coluna SKU não fala do produto).
const MODELO_DEFINIDO_EM = 'definido em Importação › Certificações'
function certTitulo(c: CertificacaoDoProduto | null | undefined): string {
  if (!c) return `Sem certificado. O certificado do produto é ${MODELO_DEFINIDO_EM} (coluna SKU).`
  const tipo = c.certificado ? ` · ${c.certificado === 'isento' ? 'isento' : c.certificado.toUpperCase()}` : ''
  const fim = c.fim ? ` Válido até ${c.fim.split('-').reverse().join('/')}.` : ''
  return `Da Certificações: ${c.produto || 'sem produto'}${tipo}.${fim} Muda lá, muda aqui.`
}
// O texto e o balão da coluna Modelo.
function certModelo(p: PricingProduct): string {
  return p.certificacao ? (p.certificacao.modelo || p.certificacao.produto || '—') : '—'
}
function certModeloTitulo(p: PricingProduct): string {
  const base = `Modelo ${MODELO_DEFINIDO_EM}.`
  return p.certificacao ? `${base} ${certTitulo(p.certificacao)}` : `${base} Este produto não tem certificado.`
}
// Conflito: os códigos da linha ("dg052,dg053") estão em certificados
// diferentes na coluna SKU. O sistema não escolhe sozinho.
function certConflitoTitulo(p: PricingProduct): string {
  const nomes = p.certificacao_conflito || []
  if (nomes.length < 2) return ''
  const vale = p.certificacao ? `Vale o ${p.certificacao.modelo || p.certificacao.produto || 'gravado na linha'}, que já estava gravado.` : 'Nenhum vale até acertar.'
  return `Os códigos desta linha estão em certificados diferentes: ${nomes.join(', ')}. ${vale} Acerte a coluna SKU em Importação › Certificações.`
}
// Garantia e Validade pelo certificado (06/11/2026, Eduardo: "em
// certificações adicione o campo garantia, e aí ele passa a mudar de acordo
// com o certificado"; "deixe a opção de editar na tabela de preços"). O
// servidor decide para onde vai a edição (PATCH do produto): a Garantia de
// produto cujo certificado tem garantia e a Validade gravam NO CERTIFICADO e
// valem para todos os produtos dele.
function garantiaDoCertificado(p: PricingProduct): boolean {
  return p.certificacao?.garantia_meses != null
}
function garantiaTitulo(p: PricingProduct): string {
  const base = p.garantia_meses == null
    ? 'Prazo da garantia não cadastrado'
    : p.garantia_meses === 0
      ? 'Sem garantia (0 = sem garantia)'
      : `Garantia de ${p.garantia_meses} ${p.garantia_meses === 1 ? 'mês' : 'meses'}`
  if (!garantiaDoCertificado(p)) return base
  const modelo = p.certificacao?.modelo?.trim() || p.certificacao?.produto || 'sem modelo'
  return `${base} — vem do certificado ${modelo}: vale para todos os produtos dele.`
}
function validadeTitulo(p: PricingProduct): string {
  if (!p.certificacao) return `Sem certificado: o certificado do produto é ${MODELO_DEFINIDO_EM} (coluna SKU).`
  if (!canEditProdutos.value) return certTitulo(p.certificacao)
  return `${certTitulo(p.certificacao)} Clique para mudar a data (a Validade do certificado): vale para todos os produtos dele.`
}
// O input de data da Validade com data incompleta ou que não existe: o
// navegador mostra o que foi digitado, mas o value vem "" (igual ao campo
// apagado) e só validity.badInput avisa.
const VALIDADE_INCOMPLETA = 'Validade: data incompleta ou que não existe — escolha a data no calendário, ou apague a data inteira para tirar a validade'
function dataIncompleta(el: HTMLInputElement | HTMLSelectElement | null): boolean {
  return !!el && (el as HTMLInputElement).type === 'date' && !!el.validity?.badInput
}
// Os 422 da Garantia e da Validade viram frase (o resto mostra o código).
const GARANTIA_DO_CERTIFICADO = 'Garantia: o prazo deste produto vem do certificado e vale para todos os produtos dele — para tirar, apague na Certificações (ou use 0 = sem garantia)'
function erroDoProduto(code: string | undefined): string {
  if (code === 'garantia_do_certificado') return GARANTIA_DO_CERTIFICADO
  if (code === 'sem_certificado') return `Validade: o produto não tem certificado — ele é ${MODELO_DEFINIDO_EM} (coluna SKU)`
  if (code === 'certificado_pela_certificacoes') return `Modelo: ${MODELO_DEFINIDO_EM}`
  return code ?? 'save_failed'
}
// Validade digitada (input de data, AAAA-MM-DD): vazio apaga a data (null);
// data que não existe ("2026-02-30") = undefined, e a célula não envia.
function lerDataValidade(raw: string): string | null | undefined {
  const t = raw.trim()
  if (!t) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t)
  if (!m) return undefined
  const [a, mes, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const dt = new Date(Date.UTC(a, mes - 1, d))
  if (dt.getUTCFullYear() !== a || dt.getUTCMonth() !== mes - 1 || dt.getUTCDate() !== d) return undefined
  return t
}
// Depois de gravar no certificado: as outras linhas do MESMO certificado
// mostram a validade/garantia nova sem recarregar a tabela. `prazoTambem` =
// a edição foi da Garantia; o prazo só vai para as outras linhas quando o
// certificado da RESPOSTA tem garantia (aí o servidor passou o prazo a todos
// os produtos dele).
function espelharCertificado(updated: PricingProduct, prazoTambem: boolean) {
  const c = updated.certificacao
  if (!c) return
  for (const x of [...products.value, ...(grid.value?.products ?? [])]) {
    if (x.id === updated.id || x.certificacao?.id !== c.id) continue
    x.certificacao = { ...c }
    if (prazoTambem && c.garantia_meses != null) x.garantia_meses = c.garantia_meses
  }
}

const products = ref<PricingProduct[]>([])
const productsLoading = ref(false)
const productsErr = ref<string | null>(null)
const searchProdutos = ref('')

// ----- Pendências (sku-audit) for the Produtos tab -----
type AuditPendingRow = {
  sku: string
  title: string | null
  stock: number | null
  accounts: string[]
  account_count: number
  issues: string[]
  bling_cost: string | null
  pricing_cost: string | null
  divergent_departments: string[] | null
  dismissed: boolean
}

const auditRows = ref<AuditPendingRow[]>([])
const auditLoading = ref(false)
const auditLoaded = ref(false)
const auditError = ref<string | null>(null)
const auditShowDismissed = ref(false)
const auditHidden = ref(false)

async function loadAudit() {
  auditLoading.value = true
  auditError.value = null
  try {
    const includeDismissed = '?include_dismissed=true'
    auditRows.value = await api<AuditPendingRow[]>(`/api/pricing/sku-audit${includeDismissed}`)
    auditLoaded.value = true
  } catch (e: any) {
    auditError.value = e?.data?.detail?.code || 'audit_failed'
    auditLoaded.value = true
  } finally {
    auditLoading.value = false
  }
}

// Botão "Atualizar estoque do Bling" no painel de pendências: dispara o job
// refresh-bling-stock (puxa saldo do Bling página a página e regrava em
// products), aguarda terminar e recarrega a auditoria. Corrige números
// defasados — ex. kit com componente zerado que ficou com estoque em cache.
// O endpoint exige produtos:edit, então gateamos o botão pela mesma permissão.
const canRefreshStock = useCan('produtos', 'edit')
const stockRefreshing = ref(false)

async function refreshBlingStock() {
  if (stockRefreshing.value) return
  stockRefreshing.value = true
  try {
    const { job_id } = await api<{ job_id: string }>('/api/jobs/refresh-bling-stock', {
      method: 'POST',
    })
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      const job = await api<BatchJob>(`/api/jobs/${job_id}`)
      if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
        if (job.status === 'succeeded') {
          toast.success('Estoque atualizado', 'Puxado do Bling')
          await loadAudit()
        } else {
          toast.error('Falha ao atualizar estoque', job.error || undefined)
        }
        return
      }
    }
    toast.warning('Atualização ainda rodando', 'Clique em Atualizar daqui a pouco')
  } catch (e: any) {
    toast.error('Erro ao atualizar estoque', e?.data?.detail?.code || e?.message || undefined)
  } finally {
    stockRefreshing.value = false
  }
}

const auditPending = computed(() => auditRows.value.filter((r) => !r.dismissed))
const auditDismissed = computed(() => auditRows.value.filter((r) => r.dismissed))

function issueBadgeClass(issue: string): string {
  const base = 'inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold'
  if (issue === 'Sem anúncio') return `${base} bg-red-100 text-red-700`
  if (issue === 'Custo divergente') return `${base} bg-orange-100 text-orange-700`
  return `${base} bg-amber-100 text-amber-700`
}

async function dismissAuditSku(sku: string) {
  try {
    await api(`/api/pricing/sku-audit/${encodeURIComponent(sku)}/dismiss`, { method: 'POST' })
    const row = auditRows.value.find((r) => r.sku === sku)
    if (row) row.dismissed = true
  } catch (e: any) {
    auditError.value = e?.data?.detail?.code || 'dismiss_failed'
  }
}

async function undismissAuditSku(sku: string) {
  try {
    await api(`/api/pricing/sku-audit/${encodeURIComponent(sku)}/undismiss`, { method: 'POST' })
    const row = auditRows.value.find((r) => r.sku === sku)
    if (row) row.dismissed = false
  } catch (e: any) {
    auditError.value = e?.data?.detail?.code || 'undismiss_failed'
  }
}

async function loadProducts() {
  productsLoading.value = true
  productsErr.value = null
  try {
    products.value = await api<PricingProduct[]>('/api/pricing/products')
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    productsLoading.value = false
  }
}

const productsByDept = computed(() => {
  const m: Record<DeptKey, PricingProduct[]> = { celular: [], mala: [], eletro: [] }
  for (const p of products.value) {
    const k = p.department as DeptKey
    if (!m[k]) m[k] = []
    m[k].push(p)
  }
  for (const k of Object.keys(m) as DeptKey[]) {
    m[k].sort((a, b) => (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' }))
  }
  return m
})

// Filtro "Mídias:" ao lado da busca — acha rápido quem ainda não tem pasta,
// fotos ou embalagem, sem precisar rolar a tabela inteira.
type FiltroMidias = '' | 'sem_pasta' | 'sem_fotos' | 'sem_embalagem'
const filtroMidias = ref<FiltroMidias>('')

function semPasta(p: PricingProduct): boolean {
  return !p.fotos_path && !p.fotos_url
}

function passaFiltroMidias(p: PricingProduct, f: FiltroMidias): boolean {
  if (f === 'sem_pasta') return semPasta(p)
  if (f === 'sem_fotos') return semPasta(p) || p.fotos_count === 0
  if (f === 'sem_embalagem') return p.embalagens_count == null || p.embalagens_count === 0
  return true
}

const productsBuscados = computed(() => {
  const list = productsByDept.value[department.value] ?? []
  const q = searchProdutos.value.trim().toLowerCase()
  if (!q) return list
  return list.filter(
    (p) =>
      p.sku.toLowerCase().includes(q) ||
      p.name.toLowerCase().includes(q) ||
      (p.ean ?? '').toLowerCase().includes(q),
  )
})

const productsCurrent = computed(() => {
  const f = filtroMidias.value
  if (!f) return productsBuscados.value
  return productsBuscados.value.filter((p) => passaFiltroMidias(p, f))
})

// Quantos caem em cada opção do filtro (já com a busca aplicada) — o número
// aparece no próprio select.
const contagemFiltroMidias = computed(() => {
  const out: Record<Exclude<FiltroMidias, ''>, number> = { sem_pasta: 0, sem_fotos: 0, sem_embalagem: 0 }
  for (const p of productsBuscados.value) {
    if (passaFiltroMidias(p, 'sem_pasta')) out.sem_pasta++
    if (passaFiltroMidias(p, 'sem_fotos')) out.sem_fotos++
    if (passaFiltroMidias(p, 'sem_embalagem')) out.sem_embalagem++
  }
  return out
})

// =========================================================== inline-edit infra

type EditKey = { id: string; field: string }
const editing = ref<EditKey | null>(null)
const editValue = ref<string>('')
const editInputRef = ref<HTMLInputElement | HTMLSelectElement | null>(null)
function setEditInputRef(el: any) {
  if (el) editInputRef.value = el
}
const savedFlash = ref<Set<string>>(new Set())

function flash(id: string, field: string) {
  const k = `${id}::${field}`
  savedFlash.value.add(k)
  setTimeout(() => savedFlash.value.delete(k), 1200)
}

function isEditing(id: string, field: string) {
  return editing.value?.id === id && editing.value?.field === field
}

function isFlashed(id: string, field: string) {
  return savedFlash.value.has(`${id}::${field}`)
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
}

async function focusEditInput() {
  await nextTick()
  const el = editInputRef.value
  if (el) {
    el.focus()
    if ('select' in el) (el as HTMLInputElement).select?.()
    return
  }
  // Fallback: setEditInputRef sometimes hasn't been called yet on the
  // first tick when the v-if just remounted the input — retry once on
  // the next macrotask before giving up.
  setTimeout(() => {
    const el2 = editInputRef.value
    if (!el2) return
    el2.focus()
    if ('select' in el2) (el2 as HTMLInputElement).select?.()
  }, 0)
}

// =========================================================== account inline edit

// Anotações da conta na aba Contas, nesta ordem (M-55, 25/11/2026): Afiliado |
// Desconto | Ads | Custo total | Margem mínima | Obs. `observation` é a "Obs";
// Ads abre o painel do M-12 (gravado à parte, não no texto `ads`). Afiliado,
// Desconto, Custo total e Margem mínima são % próprios, fora do cálculo do preço.
// Desconto até, Cupom até e Oferta limite saíram da tela (ficam no banco), e os
// textos antigos discount/affiliate/coupon/offer também ficam só no banco.
// `soAdmin`: só o ADM muda (Custo total, M-56; Margem mínima, M-85).
// `soAdminVe`: só o ADM vê — a coluna nem aparece para os outros, na tela e no
// Excel (Margem mínima, M-85; o servidor também não manda o valor).
// `cabecalho`: o título em linhas ("Margem" / "mínima"); sem ele, `label`.
type CampoAnotacao = {
  key: string
  label: string
  tipo?: 'pct'
  soAdmin?: boolean
  soAdminVe?: boolean
  titulo?: string
  cabecalho?: string[]
}
const STORE_NOTE_FIELDS: CampoAnotacao[] = [
  { key: 'afiliado_pct', label: 'Afiliado', tipo: 'pct', titulo: 'Quanto pode gastar com afiliado (%)' },
  { key: 'desconto_pct', label: 'Desconto', tipo: 'pct', titulo: 'Desconto da conta (%) — só anotação, fora do cálculo do preço' },
  { key: 'ads', label: 'Ads', titulo: '% Geral que a conta pode gastar em Ads — clique para abrir o Ads (gasto por dia e % por produto)' },
  {
    key: 'custo_total_pct', label: 'Custo total', tipo: 'pct', soAdmin: true,
    titulo: 'Teto de Afiliado + Desconto + Ads (%). Vazio = sem teto. Só o administrador muda',
  },
  {
    key: 'margem_minima_pct', label: 'Margem mínima', tipo: 'pct', soAdmin: true, soAdminVe: true,
    cabecalho: ['Margem', 'mínima'],
    titulo: 'Margem mínima da conta (%). Só o administrador vê e muda.',
  },
  { key: 'observation', label: 'Obs' },
]

// As anotações que esta pessoa vê (M-85): a Margem mínima só para o ADM. É a
// lista da tabela (cabeçalho, linha de adicionar, linhas e colspan) e do Excel.
const camposAnotacao = computed(() => STORE_NOTE_FIELDS.filter((f) => !f.soAdminVe || isAdmin.value))

// Quem muda o campo: Custo total e Margem mínima só o ADM; os outros, quem
// edita Contas.
function podeEditarAnotacao(f: CampoAnotacao): boolean {
  return f.soAdmin ? isAdmin.value : canEditContas.value
}

// M-86 (26/11/2026): com a Margem mínima preenchida, o Custo total é calculado
// pelo servidor e fica travado para todos, inclusive o ADM (fundo transparente,
// texto cinza, sem digitar). Quem não é ADM não recebe a Margem mínima: vê o
// Custo total travado como sempre, sem saber que é calculado.
function calculadoPelaMargemMinima(acc: Account, f: CampoAnotacao): boolean {
  return f.key === 'custo_total_pct' && acc.margem_minima_pct != null && acc.margem_minima_pct !== ''
}

function anotacaoTravada(acc: Account, f: CampoAnotacao): boolean {
  return !podeEditarAnotacao(f) || calculadoPelaMargemMinima(acc, f)
}

// O título da caixinha; com o aviso do Custo total abaixo da soma, ele vai junto.
function tituloAnotacao(acc: Account, f: CampoAnotacao): string | undefined {
  let titulo = f.titulo
  if (calculadoPelaMargemMinima(acc, f)) titulo = 'Calculado pela Margem mínima'
  else if (f.soAdmin && !podeEditarAnotacao(f)) titulo = 'Só o administrador muda o Custo total'
  const aviso = avisoNoCampo(acc, f)
  return aviso ? `${titulo}. ${aviso}` : titulo
}

function textoAnotacao(acc: Account, f: CampoAnotacao): string {
  const v = (acc as any)[f.key]
  if (v == null || v === '') return ''
  return f.tipo === 'pct' ? String(Number(v)).replace('.', ',') : String(v)
}

// M-56: o servidor recusa (422 custo_total_estourado) a gravação que faria
// Afiliado + Desconto + Ads passar do Custo total. O campo fica com a borda
// vermelha, o aviso do servidor aparece junto dele e o valor volta ao anterior.
// Conta que já está acima do teto (M-86): o servidor aceita o que não aumenta a
// soma (baixar um campo por vez), mesmo sem caber ainda.
type ErroCustoTotal = { id: string; campo: string; mensagem: string }
const erroCustoTotal = ref<ErroCustoTotal | null>(null)

function erroCustoTotalDe(e: any): string | null {
  const d = e?.data?.detail
  if (d?.code !== 'custo_total_estourado') return null
  return d.mensagem || 'Passa do Custo total. Não gravado.'
}

function estourouCustoTotal(acc: Account, campo: string): string | null {
  const e = erroCustoTotal.value
  return e && e.id === acc.id && e.campo === campo ? e.mensagem : null
}

// Campos próprios: gravam ao sair do campo (o Enter tira o foco), com o
// `lerPct` da margem (só número de 0 a 100, com decimal). Valor inválido volta
// ao que estava, com aviso; igual ao atual não grava.
function gravarAnotacao(acc: Account, f: CampoAnotacao, ev: Event) {
  if (anotacaoTravada(acc, f)) return
  const el = ev.target as HTMLInputElement
  const valor = lerPct(el.value)
  if (valor === undefined) {
    toast.error(`${f.label}: valor inválido`, 'Use só número de 0 a 100, ex.: 4 ou 4,5')
    el.value = textoAnotacao(acc, f)
    return
  }
  el.value = valor == null ? '' : String(valor).replace('.', ',')
  if (el.value === textoAnotacao(acc, f)) return
  if (estourouCustoTotal(acc, f.key)) erroCustoTotal.value = null
  void _patchAccount(acc.id, f.key, el.value)
}

function desfazerAnotacao(acc: Account, f: CampoAnotacao, ev: Event) {
  const el = ev.target as HTMLInputElement
  el.value = textoAnotacao(acc, f)
  el.blur()
}

// ---------- Ads da aba Contas (M-12, 18/11/2026; migration 0503)
// A célula Ads é a caixinha com o % Geral (M-55, 25/11/2026) e abre o painel
// "Amazon kfa" (M-57): cabeçalho Produto | Pode gastar (%) | ROAS mínimo |
// Gasto por dia (R$), a linha [x] Geral na mesma grade (ROAS mínimo = 100 ÷ %
// calculado) e [x] Por produto (% e gasto por dia de cada produto do
// departamento; % vazio = usa a Geral). O "% gasto real" e o "ROAS real"
// continuam no Marketing, fora da janela. Grava no servidor ao sair do campo,
// com Enter ou ao fechar; quem não edita Contas só vê. Fora do cálculo de
// preço. O texto antigo (`ads`) fica no banco.
// M-78 (26/11/2026): logo abaixo do título, a linha [x] Geral [x] Produto (o
// antigo "Por produto"), UM OU OUTRO (marcar uma desmarca a outra; clicar na
// marcada desmarca = conta sem Ads). Geral: só a linha "Todos os produtos"
// com o % Geral, o ROAS mínimo e o gasto por dia. Produto: só a busca e a
// lista; produto sem % próprio fica sem Ads ("—"), não usa mais o Geral.
// Trocar não apaga nada. Conta sem nada gravado = Geral.
// M-79 + M-80 (26/11/2026): com Produto marcado, a mesma linha ganha, depois
// de um traço fino, o filtro "Categoria de venda ▾" e a caixinha "Ads" (só os
// produtos com Ads nesta conta); a lista ganha a coluna "Categoria de venda"
// depois do nome (janela de 40rem). Os dois filtros são só da tela: somam com
// a busca, não gravam e voltam zerados ao abrir a janela ou trocar de
// departamento.
type AdsValores = { pode_gastar_pct: number | null; gasto_dia: number | null }
type AdsConta = AdsValores & {
  ads_geral_ligada: boolean
  ads_por_produto_ligada: boolean
  produtos: Record<string, AdsValores>
  real: { pct_gasto: number | null; roas: number | null }
}
// M-79: categoria de venda = o segmento do produto (filho do centro de custo da aba).
type AdsCategoria = { id: string; name: string }
type AdsResposta = {
  department: string
  pode_editar: boolean
  produtos: { id: string; sku: string; name: string; categoria: AdsCategoria | null }[]
  // As opções do filtro "Categoria de venda", na ordem das colunas da aba.
  categorias: AdsCategoria[]
  contas: Record<string, AdsConta>
}
// Conta sem nada gravado (ou criada depois da leitura): o padrão do servidor.
const ADS_PADRAO: AdsConta = {
  ads_geral_ligada: true,
  pode_gastar_pct: null,
  gasto_dia: null,
  ads_por_produto_ligada: false,
  produtos: {},
  real: { pct_gasto: null, roas: null },
}
const ads = ref<AdsResposta | null>(null)
const adsErro = ref(false)
const buscaAdsProduto = ref('')
// Os filtros da lista do Produto (M-79 e M-80), como a busca: uma só para
// todas as contas, só da tela (não gravam, não ficam lembrados) e zerados a
// cada abertura da janela e a cada troca de departamento.
// M-79: ids das categorias de venda marcadas (nenhuma = todas).
const filtroCategoriaAds = ref<string[]>([])
const menuCategoriaAdsAberto = ref(false)
// M-80: caixinha "Ads" = só os produtos com Ads nesta conta (% próprio ou
// gasto por dia = têm linha em pricing_ads_produto; a mesma regra que os põe
// no topo). Não usa gasto real nem campanha (o Marketing só tem por conta).
const soComAds = ref(false)
// Produtos limpos com a caixinha "Ads" ligada: continuam na lista (a linha não
// some debaixo do cursor) até a caixinha ser desligada e ligada ou a janela reabrir.
const adsMantidosNoFiltro = ref(new Set<string>())
const podeEditarAds = computed(() => !!ads.value?.pode_editar)
let seqAds = 0
// Por conta: só a resposta da última gravação vale (duas no ar ao mesmo tempo).
const seqAdsConta: Record<string, number> = {}

async function carregarAds() {
  if (import.meta.server) return
  const dep = department.value
  const seq = ++seqAds
  ads.value = null
  adsErro.value = false
  try {
    const r = await api<AdsResposta>(`/api/pricing/ads?department=${encodeURIComponent(dep)}`)
    if (seq === seqAds) ads.value = r
  } catch {
    if (seq === seqAds) adsErro.value = true
  }
}

function adsDa(accId: string): AdsConta {
  return ads.value?.contas[accId] ?? ADS_PADRAO
}

// A Geral que vale: com a caixa ligada e % gravado; senão nenhuma ("—").
function pctGeralAds(c: AdsConta): number | null {
  return c.ads_geral_ligada ? c.pode_gastar_pct : null
}

// Qual das duas vale (M-78: um ou outro; o servidor já manda só uma, a Geral
// ganha se vierem as duas). null = conta sem Ads.
function modoAds(c: AdsConta): 'geral' | 'produto' | null {
  if (c.ads_geral_ligada) return 'geral'
  return c.ads_por_produto_ligada ? 'produto' : null
}

// O % do produto: só o próprio, com "Produto" marcado; sem ele, sem Ads.
function pctDoProdutoAds(c: AdsConta, prodId: string): number | null {
  return modoAds(c) === 'produto' ? (c.produtos[prodId]?.pode_gastar_pct ?? null) : null
}

// ---------- Custo total abaixo de Afiliado + Desconto + Ads (M-86, item 7)
// O Custo total calculado pela Margem mínima pode ficar abaixo do que já está
// gravado: o servidor grava assim mesmo (não barra quem mexeu na Margem mínima,
// na marg ou na comissão) e a caixinha do Custo total fica com a borda vermelha
// e o aviso até alguém corrigir. A soma é a da trava do servidor
// (services/pricing/custo_total.py): Afiliado + Desconto + o MAIOR Ads que vale
// (com "Geral", o % Geral; com "Produto", o maior % próprio dos produtos da aba).

// O maior Ads que vale na conta; sem Ads, 0.
function maiorAdsDaConta(c: AdsConta, produtos: string[]): number {
  const modo = modoAds(c)
  if (modo === 'geral') return Number(c.pode_gastar_pct ?? 0)
  if (modo !== 'produto') return 0
  let maior = 0
  for (const id of produtos) {
    const v = c.produtos[id]?.pode_gastar_pct
    if (v != null && Number(v) > maior) maior = Number(v)
  }
  return maior
}

// "Afiliado + Desconto + Ads (9%) passa do Custo total (6%)", ou null se cabe
// (ou se a conta não tem Custo total). Conta em centésimos: os % têm 2 casas.
function avisoSomaAcimaDoCustoTotal(acc: Account, adsPct: number): string | null {
  const teto = acc.custo_total_pct
  if (teto == null || teto === '') return null
  const centesimos = (v: unknown) => Math.round(Number(v || 0) * 100)
  const soma = centesimos(acc.afiliado_pct) + centesimos(acc.desconto_pct) + centesimos(adsPct)
  const limite = centesimos(teto)
  if (soma <= limite) return null
  const pct = (c: number) => String(c / 100).replace('.', ',')
  return `Afiliado + Desconto + Ads (${pct(soma)}%) passa do Custo total (${pct(limite)}%)`
}

// conta → aviso, das contas da aba (o Ads só depois que a janela de Ads carrega).
const avisosCustoTotal = computed(() => {
  const m = new Map<string, string>()
  const produtos = ads.value?.produtos.map((p) => p.id) ?? []
  for (const acc of accountsCurrent.value) {
    const aviso = avisoSomaAcimaDoCustoTotal(acc, ads.value ? maiorAdsDaConta(adsDa(acc.id), produtos) : 0)
    if (aviso) m.set(acc.id, aviso)
  }
  return m
})

function avisoNoCampo(acc: Account, f: CampoAnotacao): string | null {
  return f.key === 'custo_total_pct' ? (avisosCustoTotal.value.get(acc.id) ?? null) : null
}

// O X fecha a caixinha do aviso (a borda vermelha fica); outro valor reabre.
const avisosFechados = ref<Record<string, string>>({})
function avisoAberto(acc: Account, f: CampoAnotacao): string | null {
  const aviso = avisoNoCampo(acc, f)
  return aviso && avisosFechados.value[acc.id] !== aviso ? aviso : null
}
function fecharAvisoCustoTotal(acc: Account) {
  const aviso = avisosCustoTotal.value.get(acc.id)
  if (aviso) avisosFechados.value = { ...avisosFechados.value, [acc.id]: aviso }
}

// Clique numa das caixas: marcar uma desmarca a outra; clicar na marcada
// desmarca (as duas desmarcadas = conta sem Ads). Nada é apagado.
function trocarModoAds(accId: string, modo: 'geral' | 'produto') {
  const atual = modoAds(adsDa(accId))
  const mudanca: Partial<AdsConta> =
    atual === modo
      ? modo === 'geral'
        ? { ads_geral_ligada: false }
        : { ads_por_produto_ligada: false }
      : modo === 'geral'
        ? { ads_geral_ligada: true, ads_por_produto_ligada: false }
        : { ads_por_produto_ligada: true, ads_geral_ligada: false }
  void gravarAds(accId, mudanca)
}

// Ajuda do botão da caixinha de Ads na tabela de Contas.
function tituloBotaoAds(c: AdsConta): string {
  const modo = modoAds(c)
  if (modo === 'produto') return 'Ads por produto — clique para ver o % e o gasto por dia de cada produto'
  if (modo === 'geral') return 'Ads da conta: % Geral — clique para ver o gasto por dia ou trocar para Produto'
  return 'Conta sem Ads — clique para escolher Geral ou Produto'
}

// ROAS mínimo = 100 ÷ % (2% → 50); sem % ou com 0% não há ("—").
function roasMinimo(pct: number | null): number | null {
  return pct ? 100 / pct : null
}

function fmtRoas(v: number | null): string {
  return v == null ? '—' : v.toLocaleString('pt-BR', { maximumFractionDigits: 1 })
}

function fmtGasto(v: number | null | undefined): string {
  return v == null ? '' : v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// Gasto por dia: "50", "50,90", "1.234,56", "3.000" → reais com centavos;
// vazio → null; outra coisa ou negativo → undefined (não grava). Ponto sem
// vírgula em grupos de 3 é milhar ("3.000" = 3 mil, não 3,00).
function lerGasto(texto: string): number | null | undefined {
  const t = texto.trim()
  if (!t) return null
  if (/^\d{1,3}(\.\d{3})+$/.test(t)) return Number(t.replace(/\./g, ''))
  const n = parseDec(texto)
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) / 100 : undefined
}

// Os filtros só valem com "Produto" marcado (é quando o botão e a caixinha
// aparecem); com Geral ficam guardados até a janela fechar.
function filtroCategoriaAdsDe(c: AdsConta): string[] {
  return modoAds(c) === 'produto' ? filtroCategoriaAds.value : []
}

function filtroAdsLigado(c: AdsConta): boolean {
  return soComAds.value && modoAds(c) === 'produto'
}

// Lista do Produto: os produtos do departamento que passam na busca (SKU ou
// nome), no filtro Categoria de venda (M-79) e na caixinha Ads (M-80) — passa
// quem passa em todos —, primeiro os que têm valor próprio, como na Margem.
// `ate` para antes de um filtro ('busca' = só a busca; 'categoria' = busca +
// categoria): é com isso que a lista vazia diz qual filtro a esvaziou.
function produtosDoAds(c: AdsConta, ate: 'busca' | 'categoria' | 'ads' = 'ads') {
  const q = buscaAdsProduto.value.trim().toLowerCase()
  const cats = ate === 'busca' ? [] : filtroCategoriaAdsDe(c)
  const soAds = ate === 'ads' && filtroAdsLigado(c)
  const achados = (ads.value?.produtos ?? []).filter(
    (p) =>
      (!q || p.sku.toLowerCase().includes(q) || p.name.toLowerCase().includes(q)) &&
      (!cats.length || (!!p.categoria && cats.includes(p.categoria.id))) &&
      (!soAds || !!c.produtos[p.id] || adsMantidosNoFiltro.value.has(p.id)),
  )
  return [...achados.filter((p) => c.produtos[p.id]), ...achados.filter((p) => !c.produtos[p.id])]
}

// Lista vazia: diz o que a esvaziou — a caixinha Ads, o filtro de categoria,
// a busca ou o departamento sem produto.
function textoListaAdsVazia(c: AdsConta): string {
  if (produtosDoAds(c, 'categoria').length) return 'Nenhum produto com Ads nesta conta.'
  if (produtosDoAds(c, 'busca').length) return 'Nenhum produto com esse filtro.'
  return buscaAdsProduto.value.trim() ? 'Nenhum produto encontrado.' : 'Nenhum produto neste departamento.'
}

// M-79: marca ou desmarca uma categoria no filtro (só na tela).
function alternarCategoriaAds(id: string) {
  const atual = filtroCategoriaAds.value
  filtroCategoriaAds.value = atual.includes(id) ? atual.filter((x) => x !== id) : [...atual, id]
}

// M-79: clique na janela fora do botão/menu do filtro fecha o menu.
function fecharMenuCategoriaAdsFora(ev: Event) {
  const el = ev.target as Element | null
  if (menuCategoriaAdsAberto.value && !el?.closest?.('[data-filtro-ads-categoria]')) {
    menuCategoriaAdsAberto.value = false
  }
}

// M-80: liga ou desliga a caixinha "Ads"; os produtos limpos que estavam
// presos na lista saem.
function alternarFiltroAds() {
  soComAds.value = !soComAds.value
  adsMantidosNoFiltro.value = new Set()
}

// Zera os dois filtros e o menu (ao abrir a janela e ao trocar de departamento).
function zerarFiltrosAds() {
  filtroCategoriaAds.value = []
  menuCategoriaAdsAberto.value = false
  soComAds.value = false
  adsMantidosNoFiltro.value = new Set()
}

function textoErroAds(e: any): string {
  const status = e?.status ?? e?.response?.status
  if (status === 422) return 'Use % de 0 a 100 e gasto em reais (não negativo).'
  if (status === 403) return 'Só quem edita Contas altera o Ads.'
  if (status === 404) return 'Conta ou produto não encontrado — recarregue a página.'
  return 'Tente de novo; voltou ao que está salvo.'
}

// Custo total estourado na janela de Ads (M-56): o aviso fica junto do campo
// que foi recusado. `chave` = "geral:<campo>" ou "produto:<id>:<campo>".
const erroAds = ref<{ accId: string; chave: string; mensagem: string } | null>(null)

function chaveAds(mudanca: Partial<AdsConta>, prodId?: string): string {
  return `${prodId ? `produto:${prodId}` : 'geral'}:${Object.keys(mudanca).join(',')}`
}

// Aviso para mostrar num lugar da janela: a chave exata ou um começo dela.
function erroAdsEm(accId: string, chave: string, comeco = false): string | null {
  const e = erroAds.value
  if (!e || e.accId !== accId) return null
  return (comeco ? e.chave.startsWith(chave) : e.chave === chave) ? e.mensagem : null
}

// O aviso da troca Geral / Produto (M-78): fica embaixo da linha das caixas.
function erroAdsDoModo(accId: string): string | null {
  return erroAdsEm(accId, 'geral:ads_', true)
}

// O aviso da linha "Todos os produtos" (% ou gasto por dia da Geral).
function erroAdsDaLinhaGeral(accId: string): string | null {
  return erroAdsDoModo(accId) ? null : erroAdsEm(accId, 'geral:', true)
}

// A linha do Ads (a do produto, a da Geral ou a das caixas) está na janela
// aberta desta conta?
function linhaAdsNaTela(accId: string, prodId?: string, modo = false): boolean {
  if (typeof document === 'undefined') return false
  const painel = document.querySelector(`[data-ads-painel="${accId}"]`)
  if (!painel) return false
  const seletor = prodId ? `[data-ads-produto-linha="${prodId}"]` : modo ? '[data-ads-modo]' : '[data-ads-geral]'
  return !!painel.querySelector(seletor)
}

async function gravarAds(accId: string, mudanca: Partial<AdsConta>, prodId?: string) {
  const r = ads.value
  if (!r?.pode_editar) return
  const antes = adsDa(accId)
  const dep = r.department
  // M-80: com a caixinha "Ads" ligada, o produto mexido fica na lista mesmo
  // que fique sem Ads (até desligar e ligar a caixinha ou reabrir a janela).
  if (prodId && filtroAdsLigado(antes)) adsMantidosNoFiltro.value.add(prodId)
  // Já na tela; volta ao salvo se o servidor recusar.
  r.contas[accId] = prodId
    ? {
        ...antes,
        produtos: {
          ...antes.produtos,
          [prodId]: { pode_gastar_pct: null, gasto_dia: null, ...antes.produtos[prodId], ...mudanca },
        },
      }
    : { ...antes, ...mudanca }
  const seq = (seqAdsConta[accId] ?? 0) + 1
  seqAdsConta[accId] = seq
  const url = prodId
    ? `/api/pricing/ads/${encodeURIComponent(accId)}/produto/${encodeURIComponent(prodId)}`
    : `/api/pricing/ads/${encodeURIComponent(accId)}`
  try {
    const salvo = await api<AdsConta>(url, { method: 'PUT', body: mudanca })
    if (ads.value?.department === dep && seqAdsConta[accId] === seq) ads.value.contas[accId] = salvo
    if (erroAds.value?.accId === accId) erroAds.value = null
    flash(accId, 'ads')
  } catch (e: any) {
    // M-80: recusado com a caixinha "Ads" ligada — mesmo que ela tenha sido
    // ligada com o pedido no ar — a linha fica na lista com o aviso. A volta
    // ao salvo (logo abaixo) pode tirar o Ads do produto, e aí o filtro
    // esconderia a linha junto com o aviso, depois de linhaAdsNaTela já ter
    // visto a linha na tela (e não ter mandado o aviso solto).
    if (prodId && filtroAdsLigado(adsDa(accId))) adsMantidosNoFiltro.value.add(prodId)
    const estouro = erroCustoTotalDe(e)
    if (estouro) {
      erroAds.value = { accId, chave: chaveAds(mudanca, prodId), mensagem: estouro }
      // O aviso junto do campo só basta se a linha recusada está na janela
      // aberta. Janela fechada (gravou ao fechar) ou produto escondido pela
      // busca ou pelos filtros Categoria de venda / Ads: vai também no aviso solto.
      const ehModo = 'ads_geral_ligada' in mudanca || 'ads_por_produto_ligada' in mudanca
      if (!linhaAdsNaTela(accId, prodId, ehModo)) toast.error('Não salvou o Ads', estouro)
    } else {
      toast.error('Não salvou o Ads', textoErroAds(e))
    }
    if (ads.value?.department === dep && seqAdsConta[accId] === seq) ads.value.contas[accId] = antes
  }
}

// Campo de % ou de gasto do painel (Geral ou produto): o input diz de qual
// conta, campo e produto é (data-ads-*). Vazio apaga; inválido volta ao salvo.
function gravarCampoAds(ev: Event) {
  const el = ev.target as HTMLInputElement
  const { adsConta: accId, adsCampo, adsProduto: prodId } = el.dataset
  if (!accId || !podeEditarAds.value) return
  const campo = adsCampo === 'gasto_dia' ? 'gasto_dia' : 'pode_gastar_pct'
  const c = adsDa(accId)
  const salvo = (prodId ? c.produtos[prodId]?.[campo] : c[campo]) ?? null
  const valor = campo === 'gasto_dia' ? lerGasto(el.value) : lerPct(el.value)
  if (valor === undefined) {
    toast.error(
      'Valor inválido',
      campo === 'gasto_dia' ? 'Use o valor em reais, ex.: 50 ou 50,90; vazio apaga.' : 'Use um número de 0 a 100; vazio apaga.',
    )
    el.value = campo === 'gasto_dia' ? fmtGasto(salvo) : salvo == null ? '' : fmtPct(salvo)
    return
  }
  if (valor === salvo) return
  void gravarAds(accId, { [campo]: valor }, prodId)
}

// Esc ou clique fora fecham o painel com o campo ainda focado, sem change
// (igual ao menu da Margem): ao fechar, grava o campo que está com o foco.
// Ao abrir, a busca de produto começa vazia e os filtros Categoria de venda e
// Ads (M-79/M-80) zerados; o menu de categorias fecha junto com a janela.
function abrirOuFecharAds(aberto: boolean) {
  menuCategoriaAdsAberto.value = false
  if (aberto) {
    buscaAdsProduto.value = ''
    zerarFiltrosAds()
    erroAds.value = null
    return
  }
  if (typeof document === 'undefined') return
  const el = document.activeElement as HTMLInputElement | null
  if (el?.closest?.('[data-menu-ads]') && el.dataset.adsConta) gravarCampoAds({ target: el } as unknown as Event)
}

function startEditAccount(acc: Account, field: string) {
  if (!canEditContas.value) return
  // Flush any pending edit BEFORE overwriting editing.value. commitEditAccount
  // is now synchronous — it captures + clears in the same tick and dispatches
  // the PATCH fire-and-forget — so calling it here cannot race with the new
  // edit we're about to start.
  if (
    editing.value &&
    (editing.value.id !== acc.id || editing.value.field !== field)
  ) {
    commitEditAccount()
  }
  editing.value = { id: acc.id, field }
  if (field === 'platform' && erroPlataforma.value?.id === acc.id) erroPlataforma.value = null
  // Na coluna de catálogo a margem editada é a própria (vazio = a da conta).
  const raw = ehCatalogo(acc) && field.startsWith('margin')
    ? acc.margens_catalogo?.[field.slice('margin'.length)] ?? null
    : (acc as any)[field]
  if (field === 'commission' || field.startsWith('margin')) {
    const n = raw == null || raw === '' ? null : Number(raw)
    editValue.value = n == null || Number.isNaN(n) ? '' : (n * 100).toFixed(2).replace(/\.?0+$/, '')
  } else if (field.startsWith('shipping')) {
    editValue.value = raw == null || raw === '' ? '' : String(raw)
  } else if (field === 'kit_number') {
    editValue.value = String(raw ?? 1)
  } else {
    editValue.value = raw == null ? '' : String(raw)
  }
  focusEditInput()
}

// Aceita decimal BRASILEIRO nos campos numéricos digitados: "674,50" →
// 674.5 e "1.234,56" → 1234.56 (com vírgula, pontos são milhar). Number()
// puro devolvia NaN pra vírgula: o cadastro de produto mandava "NaN" pro
// backend (422 → banner create_failed, Eduardo 2026-08-24) e as edições
// inline simplesmente não salvavam.
function parseDec(raw: unknown): number {
  const s = String(raw ?? '').trim()
  if (!s) return NaN
  const norm = s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s
  return Number(norm)
}

// SSH parity: commit is SYNCHRONOUS. Reads editing.value + editValue.value
// the moment blur/enter fires, clears them immediately, then dispatches
// the PATCH fire-and-forget. Keeping this sync (not async) guarantees the
// snapshot can't be reassigned by another scheduled microtask between
// capture and clear — we're already running synchronously inside the
// blur callback.
function commitEditAccount(): void {
  const snap = editing.value
  if (!snap) return
  const { id, field } = snap
  const raw = editValue.value.trim()
  editing.value = null
  editValue.value = ''
  void _patchAccount(id, field, raw)
}

async function _patchAccount(id: string, field: string, raw: string) {
  const acc = accounts.value.find((x) => x.id === id)
  if (!acc) return
  // Coluna de catálogo: só a margem própria dos tipos se edita.
  if (ehCatalogo(acc)) {
    if (field.startsWith('margin')) await _patchMargemCatalogo(acc, field, raw)
    return
  }

  const payload: Record<string, unknown> = {}

  if (field === 'name') {
    if (!raw) return
    payload.name = raw
  } else if (field === 'platform') {
    // Conta ML com Clássico e Premium não troca de plataforma por aqui (o par
    // ficaria meio ML, meio outra plataforma).
    if (!raw || raw === acc.platform || parDe(acc)) return
    // Sair do ML apaga a coluna de catálogo da conta no servidor (e os preços
    // fixados à mão nela): pede confirmação, igual ao Desligar.
    if (
      raw !== 'mercadolivre' &&
      catalogoAtivo(acc) &&
      !confirm(
        `Trocar a plataforma de "${acc.name}"?\n\n` +
          'O Catálogo ML desta conta sai da Tabela de Preços; os preços fixados à mão e as margens próprias do catálogo são apagados.',
      )
    ) return
    payload.platform = raw
  } else if (field === 'kit_number') {
    const n = parseInt(raw)
    if (!Number.isFinite(n) || n < 1 || n > 8) return
    payload.kit_number = n
  } else if (field === 'commission') {
    if (!raw) {
      payload.commission = null
    } else {
      const pct = parseDec(raw)
      if (Number.isNaN(pct)) return
      payload.commission = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('margin')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const pct = parseDec(raw)
      if (Number.isNaN(pct)) return
      payload[field] = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('shipping')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const n = parseDec(raw)
      if (Number.isNaN(n)) return
      payload[field] = n
    }
  } else if (field.startsWith('observation') || field === 'discount' || field === 'affiliate' || field === 'ads' || field === 'coupon' || field === 'offer' || field === 'email' || field === 'phone') {
    payload[field] = raw || null
  } else if (field === 'afiliado_pct' || field === 'desconto_pct' || field === 'custo_total_pct' || field === 'margem_minima_pct') {
    const v = lerPct(raw)
    if (v === undefined) return
    payload[field] = v
  } else {
    return
  }

  // Optimistic update: paint the new value before the round-trip.
  const antes: Record<string, unknown> = {}
  for (const k of Object.keys(payload)) antes[k] = (acc as any)[k]
  Object.assign(acc, payload)

  try {
    const updated = await api<Account>(`/api/pricing/accounts/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(acc, updated)
    // Conta ML com par: o servidor gravou o mesmo no Premium (M-53) — e o
    // Custo total que ele recalculou pela Margem mínima (M-86), nas duas.
    espelharNaIrma(acc, [...Object.keys(payload), 'custo_total_pct'])
    // O servidor apagou a coluna de catálogo (conta saiu do ML): tira ela da
    // lista local, senão o controle continua "Ligado" quando a conta volta
    // para o ML.
    if (!updated.conta_catalogo_id) {
      accounts.value = accounts.value.filter((x) => !(ehCatalogo(x) && x.conta_base_id === id))
    }
    if (erroCustoTotal.value?.id === id) erroCustoTotal.value = null
    if (field === 'platform' && erroPlataforma.value?.id === id) erroPlataforma.value = null
    flash(id, field)
  } catch (e: any) {
    // M-56: passou do Custo total — nada gravado; o campo volta ao anterior,
    // com a borda vermelha e o aviso do servidor junto dele.
    const estouro = erroCustoTotalDe(e)
    if (estouro) {
      Object.assign(acc, antes)
      erroCustoTotal.value = { id, campo: field, mensagem: estouro }
      return
    }
    // Troca de plataforma recusada (ex.: 400 tipo_obrigatorio, para ML): o
    // valor volta e a mensagem do servidor fica na célula.
    const d = e?.data?.detail
    if (field === 'platform' && (e?.status ?? e?.response?.status) === 400 && (d?.message || d?.mensagem)) {
      Object.assign(acc, antes)
      erroPlataforma.value = { id, mensagem: d.message || d.mensagem }
      return
    }
    accountsErr.value = e?.data?.detail?.code ?? 'save_failed'
    // Reload to revert the optimistic update on failure.
    await loadAccounts()
  }
}

// Margem própria do catálogo (07/10/2026): PATCH na conta de catálogo só com
// a margem do tipo. Vazio grava null = volta a usar a margem da conta de kit.
async function _patchMargemCatalogo(filha: Account, field: string, raw: string) {
  const t = field.slice('margin'.length)
  let valor: string | null = null
  if (raw && raw !== '-' && raw !== '—') {
    const pct = parseDec(raw)
    if (Number.isNaN(pct)) return
    valor = (pct / 100).toFixed(4)
  }
  const antes = filha.margens_catalogo ?? {}
  const atual = antes[t] == null || antes[t] === '' ? null : Number(antes[t])
  if (atual === (valor == null ? null : Number(valor))) return
  // Pinta já; a resposta do servidor confirma.
  filha.margens_catalogo = { ...antes, [t]: valor }
  try {
    const updated = await api<Account>(`/api/pricing/accounts/${filha.id}`, {
      method: 'PATCH',
      body: { [field]: valor },
    })
    Object.assign(filha, updated)
    flash(filha.id, field)
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.message ?? e?.data?.detail?.code ?? 'save_failed'
    await loadAccounts()
  }
}

// Conta ML com par (M-53): uma confirmação só; o servidor exclui o Clássico e o
// Premium (e os catálogos deles).
async function deleteAccount(a: Account) {
  const par = parDe(a)
  const linhas = par ? [par.classico, par.premium] : [a]
  const avisoPar = par ? '\n\nSaem o Clássico e o Premium desta conta (as duas colunas da Tabela de Preços).' : ''
  const avisoCatalogo = linhas.some(catalogoAtivo) ? '\n\nO Catálogo ML desta conta também sai.' : ''
  if (!confirm(`Excluir conta "${par ? a.name : nomeComTipo(a)}"?${avisoPar}${avisoCatalogo}`)) return
  try {
    await api(`/api/pricing/accounts/${a.id}`, { method: 'DELETE' })
    const ids = new Set(linhas.map((x) => x.id))
    accounts.value = accounts.value.filter((x) => !ids.has(x.id) && !(x.conta_base_id && ids.has(x.conta_base_id)))
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'delete_failed'
  }
}

async function autoMatchAccounts() {
  try {
    const r = await api<{ matched?: number; skipped?: number; updated?: number }>(
      '/api/pricing/accounts/auto-match',
      { method: 'POST' },
    )
    await loadAccounts()
    // Backend may report either `matched` (new field) or `updated` (legacy).
    const matched = r.matched ?? r.updated ?? 0
    const skipped = r.skipped ?? 0
    if (matched > 0) {
      toast.success(
        `${matched} conta(s) vinculada(s) com sucesso!`,
        skipped > 0 ? `${skipped} sem match` : undefined,
      )
    } else {
      toast.info('Todas as contas já estão vinculadas.', skipped > 0 ? `${skipped} sem match` : undefined)
    }
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'auto_match_failed'
    toast.error('Erro ao vincular integrações', accountsErr.value || undefined)
  }
}

// =========================================================== Catálogo ML da conta (M-54)

// A célula "Catálogo ML" mostra "Ativo" ou "Inativo" e abre a janela da conta
// ("ML forpaper"): [x] Catálogo ativo e, para o ADM, o botão Margens com
// [x] Usar as margens da conta + um % por tipo da categoria. Nada grava antes
// do Salvar. A escolha vale para a CONTA: grava pelo id do Clássico e o
// servidor faz o mesmo no Premium (liga/desliga as duas colunas de catálogo e
// copia as margens para a do Premium). Ativo → Inativo pede confirmação, porque
// desligar APAGA as colunas de catálogo, com os preços fixados à mão e as
// margens próprias. Margem em branco (ou "Usar as margens da conta") grava
// null = o catálogo usa a margem da conta; comissão e frete são sempre os da
// conta.
const catalogoJanelaId = ref<string | null>(null) // a linha (Clássico) da janela aberta
const catalogoJanelaAtivo = ref(false)
const catalogoJanelaMargens = ref(false) // botão Margens aberto
const catalogoJanelaUsarDaConta = ref(true)
const catalogoJanelaValores = ref<string[]>([])
// Como a janela abriu: Salvar só manda as margens dos campos cujo texto mudou
// (ou todas, se a caixa "Usar as margens da conta" mudou). Nada mudou = nada
// de margem vai ao servidor.
let catalogoJanelaUsarAberto = true
let catalogoJanelaValoresAbertos: string[] = []
const catalogoJanelaSalvando = ref(false)
const catalogoJanelaErro = ref('')

// 0.44 → "44"; 0.4425 → "44,25"; 0.445 → "44,5" (até 2 casas, sem zeros à
// direita: abrir e salvar não arredonda a margem gravada); sem margem → "".
function textoMargemCatalogo(m: number | null): string {
  return m == null ? '' : (m * 100).toFixed(2).replace(/\.?0+$/, '').replace('.', ',')
}

// A margem da conta de kit, em cinza nos campos travados ("44,0" ou "—").
function margemDaContaTexto(acc: Account, t: number): string {
  return fmtMargin((acc as any)[`margin${t}`]).replace('%', '').replace('.', ',')
}

function abrirCatalogo(acc: Account) {
  if (!canEditContas.value || !podeTerCatalogo(acc)) return
  const filha = catalogoPorBase.value.get(acc.id)
  catalogoJanelaAtivo.value = catalogoAtivo(acc)
  catalogoJanelaMargens.value = false
  catalogoJanelaUsarDaConta.value = !temMargemPropria(acc)
  catalogoJanelaValores.value = Array.from({ length: nTiposAba.value }, (_, i) =>
    textoMargemCatalogo(filha ? margemPropria(filha, i + 1) : null),
  )
  catalogoJanelaUsarAberto = catalogoJanelaUsarDaConta.value
  catalogoJanelaValoresAbertos = [...catalogoJanelaValores.value]
  catalogoJanelaErro.value = ''
  catalogoJanelaId.value = acc.id
}

// Fechar (Cancelar, Esc, clique fora) descarta o que não foi salvo.
function fecharCatalogo() {
  if (catalogoJanelaSalvando.value) return
  catalogoJanelaId.value = null
  catalogoJanelaErro.value = ''
}

// Balão da célula: o estado e, para o ADM, as margens próprias do catálogo.
function tituloCatalogoDaConta(acc: Account): string {
  if (!catalogoAtivo(acc)) return 'Catálogo ML inativo — clique para ativar'
  const filha = catalogoPorBase.value.get(acc.id)
  if (isAdmin.value && filha && temMargemPropria(acc)) {
    return `Catálogo ML ativo — margens próprias: ${resumoMargemPropria(filha)}`
  }
  return 'Catálogo ML ativo — clique para mudar'
}

function alternarJanelaCatalogo(acc: Account, aberta: boolean) {
  if (aberta) abrirCatalogo(acc)
  else fecharCatalogo()
}

function textoErroCatalogo(e: any): string {
  const d = e?.data?.detail
  if (d?.code === 'tipo_obrigatorio') return 'Esta conta está sem tipo (clássico/premium); o catálogo não pode ser ativado.'
  if (d?.code === 'catalogo_desencontrado') {
    return 'O catálogo do Clássico e o do Premium estão desencontrados; nada foi gravado. Recarregue a página e, se continuar, avise o suporte.'
  }
  if ((e?.status ?? e?.response?.status) === 403) return 'Sem permissão para mudar o catálogo desta conta.'
  return d?.mensagem || d?.message || d?.code || 'Não foi possível salvar; tente de novo.'
}

async function salvarCatalogo() {
  const acc = accounts.value.find((a) => a.id === catalogoJanelaId.value)
  if (!acc || !canEditContas.value || catalogoJanelaSalvando.value) return
  const par = parDe(acc)
  const ativoAntes = catalogoAtivo(acc)
  const ativo = catalogoJanelaAtivo.value
  // Margens: só o ADM e só com o catálogo ativo. "Usar as margens da conta"
  // grava null em todos os tipos; desmarcada, o campo vazio também é null.
  let margens: Record<string, string | null> | null = null
  if (isAdmin.value && ativo) {
    margens = {}
    const nomes = TYPE_HEADERS.value[department.value] ?? []
    const mudouCaixa = catalogoJanelaUsarDaConta.value !== catalogoJanelaUsarAberto
    for (let i = 0; i < catalogoJanelaValores.value.length; i++) {
      // Caixa igual: só os campos que a pessoa mexeu (com a caixa marcada os
      // campos estão travados, então nenhum).
      if (!mudouCaixa && (catalogoJanelaUsarDaConta.value || catalogoJanelaValores.value[i] === catalogoJanelaValoresAbertos[i])) continue
      let valor: string | null = null
      const raw = catalogoJanelaUsarDaConta.value ? '' : (catalogoJanelaValores.value[i] || '').replace('%', '').trim()
      if (raw) {
        const pct = parseDec(raw)
        if (Number.isNaN(pct)) {
          catalogoJanelaErro.value = `Margem inválida em ${nomes[i] ?? `tipo ${i + 1}`}`
          return
        }
        valor = (pct / 100).toFixed(4)
      }
      margens[`margin${i + 1}`] = valor
    }
  }
  if (
    ativoAntes && !ativo &&
    !confirm(
      `Desativar o Catálogo ML de "${par ? acc.name : nomeComTipo(acc)}"?\n\n` +
        (par
          ? 'As colunas de catálogo do Clássico e do Premium desta conta saem da Tabela de Preços; os preços fixados à mão e as margens próprias do catálogo (dos dois tipos) são apagados.'
          : 'As colunas de catálogo desta conta saem da Tabela de Preços; os preços fixados à mão e as margens próprias do catálogo são apagados.'),
    )
  ) return
  catalogoJanelaSalvando.value = true
  catalogoJanelaErro.value = ''
  let mudou = false
  try {
    if (ativo !== ativoAntes) {
      await api(`/api/pricing/accounts/${acc.id}/catalogo`, { method: 'POST', body: { ativo } })
      mudou = true
      await loadAccounts()
      toast.success(
        ativo ? 'Catálogo ML ativado' : 'Catálogo ML desativado',
        `${tituloConta(acc)}${ativo ? ': colunas novas no grupo ML Catálogo da Tabela de Preços' : ''}`,
      )
    }
    // A coluna de catálogo do Clássico (nova, se acabou de ativar): só as
    // margens que mudaram; o servidor copia para a do Premium.
    const filha = margens ? catalogoPorBase.value.get(acc.id) : undefined
    if (margens && filha) {
      const body: Record<string, string | null> = {}
      for (const [campo, valor] of Object.entries(margens)) {
        const atual = margemPropria(filha, Number(campo.slice('margin'.length)))
        if ((atual == null ? null : atual.toFixed(4)) !== valor) body[campo] = valor
      }
      if (Object.keys(body).length) {
        await api(`/api/pricing/accounts/${filha.id}`, { method: 'PATCH', body })
        mudou = true
        await loadAccounts()
      }
    }
    if (mudou) flash(acc.id, 'catalogo')
    catalogoJanelaSalvando.value = false
    fecharCatalogo()
  } catch (e: any) {
    // Se ativou e só a margem falhou, a lista já foi relida (mostra "Ativo") e
    // a janela fica aberta para tentar a margem de novo.
    catalogoJanelaErro.value = textoErroCatalogo(e)
  } finally {
    catalogoJanelaSalvando.value = false
  }
}

// =========================================================== add account row

const showAddAcc = ref(false)
const newAcc = reactive({ name: '', platform: 'mercadolivre', kit_number: 1, commission: '' })
const addingAcc = ref(false)

function openAddAcc() {
  newAcc.name = ''
  newAcc.platform = 'mercadolivre'
  newAcc.kit_number = 1
  newAcc.commission = ''
  showAddAcc.value = true
  nextTick(() => {
    const el = document.getElementById('new-acc-name') as HTMLInputElement | null
    el?.focus()
  })
}

async function submitNewAcc() {
  if (!newAcc.name.trim()) return
  addingAcc.value = true
  try {
    const body: Record<string, unknown> = {
      name: newAcc.name.trim(),
      platform: newAcc.platform,
      department: department.value,
    }
    // ML (M-53): a conta nasce com Clássico e Premium, sem ninguém escolher
    // tipo, e com a comissão usual da categoria em cada um (o servidor decide;
    // o ADM troca depois na janela da Comissão).
    const ehMl = newAcc.platform === 'mercadolivre'
    // Kit e comissão só o ADM manda (M-10): sem eles a conta nasce no Kit 1.
    if (isAdmin.value) {
      body.kit_number = newAcc.kit_number || 1
      if (newAcc.commission && !ehMl) body.commission = (parseDec(newAcc.commission) / 100).toFixed(4)
    }
    const created = await api<Account>('/api/pricing/accounts', { method: 'POST', body })
    // ML: a resposta é o Clássico; o Premium vem relendo a lista.
    if (ehMl) await loadAccounts()
    if (!accounts.value.some((a) => a.id === created.id)) accounts.value.push(created)
    // Conta nova nasce sem loja (= sem equipe): se os filtros a esconderiam,
    // "Sem equipe" e a plataforma dela voltam a ficar marcados para ela aparecer.
    if (!passaFiltroEquipe(created, contasEquipesDesmarcadas.value)) {
      contasEquipesDesmarcadas.value = contasEquipesDesmarcadas.value.filter((v) => !chavesEquipe(created).includes(v))
    }
    if (contasPlataformasDesmarcadas.value.includes(created.platform)) {
      contasPlataformasDesmarcadas.value = alternarNaLista(contasPlataformasDesmarcadas.value, created.platform)
    }
    // Mesmo nome de uma conta desmarcada no Contas ▾ (M-51): volta a marcar.
    if (contasNomesDesmarcados.value.includes(chaveNomeConta(created))) {
      contasNomesDesmarcados.value = alternarNaLista(contasNomesDesmarcados.value, chaveNomeConta(created))
    }
    showAddAcc.value = false
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'create_failed'
  } finally {
    addingAcc.value = false
  }
}

// ---- Fotos via MEGA (sidecar megacmd) --------------------------------
const megaBusy = ref(false)
const showMegaLogin = ref(false)
const megaLoginForm = reactive({ email: '', password: '', code: '' })
const megaLoginBusy = ref(false)
const megaLoginErr = ref('')
// O login pode ser aberto pela pílula de status (só conectar) ou pelo
// "Vincular pastas pelo nome…" (conectar e já mostrar a prévia).
const megaLoginDepois = ref<'previa' | null>(null)
const showMegaPreview = ref(false)
const megaPreview = ref<any | null>(null)
const megaApplyBusy = ref(false)

// Pílula de status no topo da aba Produtos: antes o único jeito de saber se
// o MEGA estava conectado era clicar em "Fotos (MEGA)" e ver o que acontecia.
type MegaStatus = 'verificando' | 'conectado' | 'desconectado' | 'fora'
const megaStatus = ref<MegaStatus | null>(null)
const megaStatusInfo = ref('')

async function carregarMegaStatus() {
  megaStatus.value = 'verificando'
  try {
    const st = await api<any>('/api/pricing/mega/status')
    if (!st.available) {
      megaStatus.value = 'fora'
      megaStatusInfo.value = st.error || 'serviço do MEGA não está no ar'
    } else if (!st.logged_in) {
      megaStatus.value = 'desconectado'
      megaStatusInfo.value = ''
    } else {
      megaStatus.value = 'conectado'
      megaStatusInfo.value = st.email || ''
    }
  } catch (e: any) {
    megaStatus.value = 'fora'
    megaStatusInfo.value = megaErrMsg(e)
  }
}

function abrirMegaLogin(depois: 'previa' | null) {
  megaLoginErr.value = ''
  megaLoginDepois.value = depois
  showMegaLogin.value = true
}

function megaErrMsg(e: any): string {
  const d = e?.data?.detail
  const msg = d?.message || d?.code || e?.message || 'erro'
  if (/not logged/i.test(String(msg)))
    return 'Conta MEGA não conectada — clique em "MEGA desconectado — Conectar" no topo'
  return String(msg)
}

async function megaSyncClick() {
  if (megaBusy.value) return
  megaBusy.value = true
  try {
    const st = await api<any>('/api/pricing/mega/status')
    if (!st.available) {
      megaStatus.value = 'fora'
      megaStatusInfo.value = st.error || 'serviço do MEGA não está no ar'
      toast.error('MEGA fora do ar', [st.error || 'serviço não está no ar'])
      return
    }
    if (!st.logged_in) {
      megaStatus.value = 'desconectado'
      abrirMegaLogin('previa')
      return
    }
    megaStatus.value = 'conectado'
    megaStatusInfo.value = st.email || ''
    await megaDryRun()
  } catch (e: any) {
    toast.error('MEGA', [megaErrMsg(e)])
  } finally {
    megaBusy.value = false
  }
}

async function megaDryRun() {
  const rep = await api<any>('/api/pricing/mega/sync', {
    method: 'POST',
    body: { dry_run: true, only_missing: true },
  })
  megaPreview.value = rep
  showMegaPreview.value = true
}

async function submitMegaLogin() {
  if (megaLoginBusy.value) return
  megaLoginBusy.value = true
  megaLoginErr.value = ''
  try {
    const res = await api<any>('/api/pricing/mega/login', {
      method: 'POST',
      body: {
        email: megaLoginForm.email.trim(),
        password: megaLoginForm.password,
        code: megaLoginForm.code.trim() || null,
      },
    })
    if (!res.ok) {
      megaLoginErr.value = res.message || 'login falhou — confira email e senha'
      return
    }
    showMegaLogin.value = false
    megaLoginForm.password = ''
    megaLoginForm.code = ''
    megaStatus.value = 'conectado'
    megaStatusInfo.value = megaLoginForm.email.trim()
    if (megaLoginDepois.value !== 'previa') {
      toast.success('MEGA conectado')
      return
    }
    toast.success('MEGA conectado', ['Buscando pastas de fotos…'])
    megaBusy.value = true
    try {
      await megaDryRun()
    } finally {
      megaBusy.value = false
    }
  } catch (e: any) {
    megaLoginErr.value = megaErrMsg(e)
  } finally {
    megaLoginBusy.value = false
  }
}

async function megaApply() {
  if (megaApplyBusy.value) return
  megaApplyBusy.value = true
  try {
    const rep = await api<any>('/api/pricing/mega/sync', {
      method: 'POST',
      body: { dry_run: false, only_missing: true },
    })
    showMegaPreview.value = false
    const lines = [`${rep.applied} produto(s) ligados à pasta do MEGA`]
    if (rep.errors?.length) {
      lines.push(`${rep.errors.length} erro(s) ao gerar link`)
      toast.warning('Pastas vinculadas', lines)
    } else {
      toast.success('Pastas vinculadas', lines)
    }
    await loadProducts()
    // Reconta fotos/vídeos/embalagens em segundo plano (não segura o
    // fechamento do modal).
    void megaCountsRefresh(true)
  } catch (e: any) {
    toast.error('Vincular pastas', [megaErrMsg(e)])
  } finally {
    megaApplyBusy.value = false
  }
}

// -- contagem de fotos/vídeos/embalagens por pasta (mega-find via sidecar)
// Botão "Recontar fotos e embalagens" do topo e da prévia; a API grava
// fotos_count/videos_count/embalagens_count e a hora da contagem.
const megaCountsBusy = ref(false)
async function megaCountsRefresh(silent = false) {
  if (megaCountsBusy.value) return
  megaCountsBusy.value = true
  try {
    const rep = await api<any>('/api/pricing/mega/counts/refresh', {
      method: 'POST',
      body: {},
    })
    await loadProducts()
    if (!silent)
      toast.success('Fotos e embalagens recontadas', [
        `${rep.folders_counted} pasta(s) contadas`,
        `${rep.products_updated} produto(s) atualizados`,
      ])
  } catch (e: any) {
    if (!silent) toast.error('Recontar fotos e embalagens', [megaErrMsg(e)])
  } finally {
    megaCountsBusy.value = false
  }
}

// -- criação da estrutura de pastas marca/modelo no MEGA ---------------
// Pasta principal sugerida = departamento ativo (espelha /Celular, /Malas,
// /Eletro no MEGA — mesma organização das abas do DaVinci).
const MEGA_PASTA_POR_DEPT: Record<string, string> = {
  celular: 'Celular',
  mala: 'Malas',
  eletro: 'Eletro',
}
const showMegaScaffold = ref(false)
const megaScaffoldBrand = ref('')
const megaScaffoldText = ref('')
const megaScaffoldBusy = ref(false)
const megaScaffoldCount = computed(
  () => megaScaffoldText.value.split('\n').map(s => s.trim()).filter(Boolean).length,
)

// Sugere uma pasta por MODELO a partir dos produtos sem link: corta
// tamanho/kit de mala ("M2 listrada mala 12+18" → "M2 listrada") e
// memória/cor de eletrônico ("uranyx Fossibot S7 8.128 - …" → "Fossibot S7"),
// então várias linhas da tabela apontam pra mesma pasta de fotos.
function suggestMegaFolders(): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const p of products.value) {
    if (p.fotos_url) continue
    let n = (p.name || '').trim()
    if (!n) continue
    if (/^uranyx\s/i.test(n)) {
      n = n.split(/\s+-\s+/)[0].replace(/^uranyx\s+/i, '')
      n = n.replace(/\s+\d+\.\d+.*$/, '').replace(/\s*\(.*$/, '')
    } else if (/\b(mala|maleta)\b/i.test(n)) {
      n = n.replace(/\s+(mala|maleta)\b.*$/i, '')
    } else {
      n = n.split(/\s+-\s+/)[0].replace(/\s+\d+\.\d+.*$/, '')
    }
    n = n.replace(/\s*\+\s*acess\w*.*$/i, '').trim()
    if (!n) continue
    const key = n.toLowerCase()
    if (!seen.has(key)) {
      seen.add(key)
      out.push(n)
    }
  }
  const norm = (s: string) =>
    s.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, ' ').trim()
  // descarta nome que é prefixo de outro ("M2" × "M2 listrada") pra não
  // criar pastas ambíguas no matching
  return out.filter(a => !out.some(b => b !== a && norm(b).startsWith(norm(a) + ' ')))
}

function openMegaScaffold() {
  if (!megaScaffoldBrand.value.trim())
    megaScaffoldBrand.value = MEGA_PASTA_POR_DEPT[department.value] ?? ''
  if (!megaScaffoldText.value.trim())
    megaScaffoldText.value = suggestMegaFolders().join('\n')
  showMegaScaffold.value = true
}

async function megaScaffoldCreate() {
  const brand = megaScaffoldBrand.value.trim()
  const names = megaScaffoldText.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (!brand || !names.length || megaScaffoldBusy.value) return
  megaScaffoldBusy.value = true
  try {
    const rep = await api<any>('/api/pricing/mega/scaffold', {
      method: 'POST',
      body: { brand, names },
    })
    const lines = [
      `${rep.folders_created} pasta(s) em ${rep.brand_path}`,
      `${rep.applied} produto(s) receberam o link`,
    ]
    if (rep.unmatched_products?.length)
      lines.push(`${rep.unmatched_products.length} produto(s) continuam sem pasta`)
    if (rep.errors?.length) {
      toast.warning('Estrutura criada com avisos', [...lines, ...rep.errors.slice(0, 3)])
    } else {
      toast.success('Estrutura criada no MEGA', lines)
    }
    showMegaScaffold.value = false
    await loadProducts()
    await megaDryRun()
  } catch (e: any) {
    toast.error('Criar pastas no MEGA', [megaErrMsg(e)])
  } finally {
    megaScaffoldBusy.value = false
  }
}

// -- painel de mídias do produto (MidiasProdutoDrawer) ------------------
// Substitui a célula antiga (câmera + lápis + upload de 10px + campo que
// gravava o link ao sair dele): a célula virou uma etiqueta com texto e o
// clique abre o painel com fotos, vídeos e embalagens do produto.
type AbaMidias = 'fotos' | 'videos' | 'embalagens'
type CamposMidias = Partial<Pick<PricingProduct,
  | 'fotos_url' | 'fotos_path' | 'embalagens_url' | 'embalagens_path'
  | 'fotos_count' | 'videos_count' | 'embalagens_count' | 'midias_contadas_em'>>

// Guarda só o id: se a lista for recarregada com o painel aberto, o painel
// continua olhando a linha nova (e não um objeto que saiu da tabela).
const midiasProdutoId = ref<string | null>(null)
const midiasAba = ref<AbaMidias>('fotos')
const midiasEnviandoId = ref<string | null>(null)
const midiasProduto = computed(() =>
  midiasProdutoId.value ? products.value.find((x) => x.id === midiasProdutoId.value) ?? null : null,
)

function abrirMidias(p: PricingProduct, aba: AbaMidias) {
  midiasAba.value = aba
  midiasProdutoId.value = p.id
}

function fecharMidias() {
  midiasProdutoId.value = null
  midiasEnviandoId.value = null
}

// Linhas que apontam pra MESMA pasta do MEGA (ex.: M2 listrada 12" e 18"):
// o que se envia numa aparece em todas, e o painel avisa isso.
function irmaosDe(p: PricingProduct): { id: string; sku: string; name: string }[] {
  if (!p.fotos_path) return []
  return products.value
    .filter((x) => x.id !== p.id && x.fotos_path === p.fotos_path)
    .map((x) => ({ id: x.id, sku: x.sku, name: x.name }))
}

// Aplica o que a API devolveu (envio, recontagem, troca de pasta) sem
// recarregar a tabela inteira. A linha aberta recebe tudo; as irmãs — as que
// JÁ apontavam pra pasta resultante — recebem link, contagens e embalagens,
// porque são a mesma pasta no MEGA. Trocar ou desligar a pasta mexe só na
// linha aberta (a API faz o mesmo), então quem ficou na pasta antiga não muda.
//
// A linha é achada pelo `id` que o painel guardou no início da operação, e
// não pelo painel aberto: fechar o painel no meio de um envio zera
// midiasProdutoId, e o resultado que chegava depois (a pasta criada no 1º
// lote) se perdia — a linha ficava "+ Adicionar" até o F5. `irmas`: o que
// copiar para as irmãs (omitido = o mesmo da linha; false = não mexer —
// ex.: troca de pasta cuja recontagem falhou, ver MidiasProdutoDrawer).
function aplicarMidias(id: string, campos: CamposMidias, irmas?: CamposMidias | false) {
  const aberto = products.value.find((x) => x.id === id)
  if (!aberto) return
  const semIndefinidos = (c: CamposMidias) => Object.fromEntries(
    Object.entries(c).filter(([, v]) => v !== undefined),
  ) as CamposMidias
  const presentes = semIndefinidos(campos)
  const pathNovo = 'fotos_path' in presentes ? presentes.fotos_path ?? null : aberto.fotos_path
  if (pathNovo && irmas !== false) {
    const { fotos_path: _ignorado, ...paraIrmas } = semIndefinidos(irmas ?? presentes)
    for (const x of products.value) {
      if (x.id !== aberto.id && x.fotos_path === pathNovo) Object.assign(x, paraIrmas)
    }
  }
  Object.assign(aberto, presentes)
}

function onMidiasEnviando(ativo: boolean) {
  midiasEnviandoId.value = ativo ? midiasProdutoId.value : null
}

// Etiqueta da célula Fotos: texto visível, sem nada escondido no hover.
function rotuloFotos(p: PricingProduct): { linha1: string; linha2: string | null; tom: 'vazio' | 'ver' | 'ambar' | 'ok' } {
  if (semPasta(p)) return { linha1: '+ Adicionar', linha2: null, tom: 'vazio' }
  if (p.fotos_count == null) return { linha1: 'Ver pasta', linha2: null, tom: 'ver' }
  const f = p.fotos_count
  const v = p.videos_count ?? 0
  if (f === 0 && v === 0) return { linha1: 'Pasta vazia', linha2: null, tom: 'ambar' }
  return {
    linha1: `${f} ${f === 1 ? 'foto' : 'fotos'}`,
    linha2: v > 0 ? `${v} ${v === 1 ? 'vídeo' : 'vídeos'}` : null,
    tom: 'ok',
  }
}

function rotuloEmbalagens(p: PricingProduct): { texto: string; tom: 'vazio' | 'ver' | 'ambar' | 'ok' } {
  if (!p.embalagens_path) return { texto: '+ Adicionar', tom: 'vazio' }
  const n = p.embalagens_count
  if (n == null) return { texto: 'Ver pasta', tom: 'ver' }
  if (n === 0) return { texto: 'Pasta vazia', tom: 'ambar' }
  return { texto: `${n} ${n === 1 ? 'arquivo' : 'arquivos'}`, tom: 'ok' }
}

// Mesmo peso visual do badge da coluna Tabela, com borda pra parecer botão.
// Uma linha só (M-9): "3 fotos" e "2 vídeos" lado a lado, para a linha da
// tabela não crescer.
const ETIQUETA_MIDIA_BASE = 'inline-flex items-center justify-center gap-1 whitespace-nowrap min-w-[5.5rem] px-2 py-0.5 rounded border text-[11px] font-medium leading-tight transition-colors'
function classeEtiquetaMidia(tom: 'vazio' | 'ver' | 'ambar' | 'ok' | 'enviando'): string {
  switch (tom) {
    case 'vazio': return `${ETIQUETA_MIDIA_BASE} border-dashed border-muted-foreground/40 text-muted-foreground hover:border-blue-400 hover:text-blue-700 dark:hover:text-blue-300`
    case 'ver': return `${ETIQUETA_MIDIA_BASE} border-slate-200 bg-slate-100 text-slate-700 hover:bg-slate-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200`
    case 'ambar': return `${ETIQUETA_MIDIA_BASE} border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200`
    case 'enviando': return `${ETIQUETA_MIDIA_BASE} border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-200`
    default: return `${ETIQUETA_MIDIA_BASE} border-sky-200 bg-sky-50 text-sky-800 hover:bg-sky-100 dark:border-sky-800 dark:bg-sky-900/30 dark:text-sky-200`
  }
}

// =========================================================== product inline edit

// Coluna "Garantia (meses)" (06/11/2026): o texto digitado vira o número que
// vai no PATCH. Vazio/"-"/"—" = null (apaga o prazo); inteiro de 0 a 120 = o
// número (0 = sem garantia, "se for avulso não tem garantia"); qualquer outra
// coisa (121, "12,5", "abc") = undefined, e a célula não envia nada e avisa.
const GARANTIA_MESES_MAX = 120
function lerGarantiaMeses(raw: string): number | null | undefined {
  const t = raw.trim()
  if (!t || t === '-' || t === '—') return null
  if (!/^\d+$/.test(t)) return undefined
  const n = parseInt(t, 10)
  if (!Number.isFinite(n) || n < 0 || n > GARANTIA_MESES_MAX) return undefined
  return n
}

function startEditProduct(p: PricingProduct, field: string) {
  if (!canEditProdutos.value) return
  // Flush any pending edit BEFORE overwriting editing.value. commitEditProduct
  // is now synchronous (PATCH dispatched fire-and-forget inside) so this
  // call cannot race with the new edit we're about to start below.
  if (
    editing.value &&
    (editing.value.id !== p.id || editing.value.field !== field)
  ) {
    commitEditProduct()
  }
  // Validade: só produto com certificado (a data é o Fim do certificado).
  if (field === 'certificado_fim' && !p.certificacao) return
  editing.value = { id: p.id, field }
  const raw = field === 'certificado_fim' ? p.certificacao?.fim : (p as any)[field]
  editValue.value = raw == null ? '' : String(raw)
  focusEditInput()
}

// SSH parity: commit is SYNCHRONOUS. Captures editing.value + editValue.value
// the moment blur/enter fires and dispatches the PATCH fire-and-forget. See
// `commitEditAccount` for the rationale (no async = no window for other
// microtasks to rewrite the refs between read and clear).
function commitEditProduct(): void {
  const snap = editing.value
  if (!snap) return
  const { id, field } = snap
  const raw = editValue.value.trim()
  editing.value = null
  editValue.value = ''
  // Validade: o <input type="date"> entrega "" tanto para o campo apagado
  // quanto para a data incompleta ou que não existe (30/02) — só
  // validity.badInput separa os dois. Data ruim avisa e não envia; senão
  // apagaria o Fim do certificado de todos os produtos dele.
  if (field === 'certificado_fim' && dataIncompleta(editInputRef.value)) {
    productsErr.value = VALIDADE_INCOMPLETA
    return
  }
  void _patchProduct(id, field, raw)
}

async function _patchProduct(id: string, field: string, raw: string) {
  const p =
    products.value.find((x) => x.id === id) ??
    grid.value?.products.find((x) => x.id === id)
  if (!p) return

  const payload: Record<string, unknown> = {}

  if (field === 'sku' || field === 'name') {
    if (!raw) return
    payload[field] = raw
  } else if (field === 'department') {
    payload.department = raw
  } else if (field === 'product_type') {
    const n = parseInt(raw)
    if (!Number.isFinite(n)) return
    payload.product_type = n
  } else if (field.startsWith('cost_kit')) {
    if (!raw || raw === '-' || raw === '—') {
      if (field === 'cost_kit1') payload[field] = '0'
      else payload[field] = null
    } else {
      const n = parseDec(raw)
      if (Number.isNaN(n)) return
      payload[field] = n.toFixed(2)
    }
  } else if (field === 'preco_catalogo') {
    // Coluna Catálogo: vazio = sem preço de catálogo (NULL), nunca 0.
    if (!raw || raw === '-' || raw === '—') {
      payload.preco_catalogo = null
    } else {
      const n = parseDec(raw)
      if (Number.isNaN(n) || n < 0) return
      payload.preco_catalogo = n > 0 ? n.toFixed(2) : null
    }
  } else if (field === 'garantia_meses') {
    const n = lerGarantiaMeses(raw)
    if (n === undefined) {
      productsErr.value = `Garantia: prazo inválido ("${raw}") — use um número inteiro de meses, de 0 (sem garantia) a ${GARANTIA_MESES_MAX}, ou deixe vazio para tirar o prazo`
      return
    }
    // Sem mudança não manda PATCH: o servidor grava a vigência a cada troca.
    if (productsErr.value?.startsWith('Garantia:')) productsErr.value = null
    if ((p.garantia_meses ?? null) === n) return
    // Apagar o prazo que vem do certificado tiraria o de todos os produtos
    // dele: não manda (o servidor também recusa, 422 garantia_do_certificado).
    if (n === null && garantiaDoCertificado(p)) {
      productsErr.value = GARANTIA_DO_CERTIFICADO
      return
    }
    payload.garantia_meses = n
  } else if (field === 'certificado_fim') {
    // Validade (06/11/2026): grava o Fim do certificado do produto.
    if (!p.certificacao) return
    const fim = lerDataValidade(raw)
    if (fim === undefined) {
      productsErr.value = `Validade: data inválida ("${raw}") — escolha a data no calendário, ou apague para tirar a validade`
      return
    }
    if (productsErr.value?.startsWith('Validade:')) productsErr.value = null
    if ((p.certificacao.fim ?? null) === fim) return
    payload.certificado_fim = fim
  } else if (field === 'description' || field === 'model' || field === 'ean') {
    payload[field] = raw || null
  } else {
    return
  }

  // Optimistic update: paint the new value immediately, both on the
  // products list and the grid mirror, so the user sees the change without
  // waiting on the network. Failures revert via loadGrid below.
  // A Validade não é campo da linha: pinta o Fim do certificado dela.
  if (field === 'certificado_fim') {
    if (p.certificacao) p.certificacao = { ...p.certificacao, fim: payload.certificado_fim as string | null }
  } else {
    Object.assign(p, payload)
    const gp0 = grid.value?.products.find((x) => x.id === id)
    if (gp0 && gp0 !== p) Object.assign(gp0, payload)
  }
  const mudaPreco = field.startsWith('cost_kit') || field === 'preco_catalogo'
  if (mudaPreco) recomputeCellsForProduct(id)

  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(p, updated)
    const gp = grid.value?.products.find((x) => x.id === id)
    if (gp && gp !== p) Object.assign(gp, updated)
    // As outras linhas do mesmo certificado, pelo que o SERVIDOR devolveu (o
    // certificado pode ter ganhado ou perdido a garantia depois que a tela
    // abriu): a Garantia só vai para todas quando a resposta diz que o
    // certificado tem garantia — aí o servidor passou o prazo a todos.
    if (field === 'certificado_fim' || field === 'garantia_meses') {
      espelharCertificado(updated, field === 'garantia_meses')
    }
    flash(id, field)
    // SSH parity: do NOT reload the whole grid after a cost edit. The
    // server response already carries the canonical row, and recomputing
    // cells in-memory from the new cost is enough to repaint dependent
    // prices. A full loadGrid() here used to occasionally overwrite the
    // freshly-saved value with stale data, producing the "I typed 20 and
    // it snapped back to 4000" bug.
    if (mudaPreco) {
      if (isAdmin.value) recomputeCellsForProduct(id)
      else if (grid.value) void loadGrid()
    }
  } catch (e: any) {
    productsErr.value = erroDoProduto(e?.data?.detail?.code)
    // Revert the optimistic update on failure by re-fetching.
    if (grid.value) await loadGrid()
    else await loadProducts()
  }
}

// Coluna Prioridade: define a tag de estoque prioritária do produto. O robô
// troca o SKU do pedido "Em aberto" pra essa tag ANTES da margem/NF, desde
// que o SKU alvo exista no Bling com saldo suficiente. Vazio = nada muda.
async function setPrioridade(p: PricingProduct, v: string) {
  const antes = p.prioridade_estoque
  p.prioridade_estoque = v || null // otimista
  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${p.id}`, {
      method: 'PATCH',
      body: { prioridade_estoque: v || null },
    })
    Object.assign(p, updated)
    flash(p.id, 'prioridade_estoque')
  } catch (e: any) {
    p.prioridade_estoque = antes
    productsErr.value = e?.data?.detail?.code ?? 'save_failed'
  }
}

async function toggleActive(p: PricingProduct) {
  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${p.id}`, {
      method: 'PATCH',
      body: { is_active: !p.is_active },
    })
    Object.assign(p, updated)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'toggle_failed'
  }
}

// Excluir o produto leva a pasta dele no MEGA para a LIXEIRA do MEGA
// (Eduardo 29/09: "quando eu remover um produto, ele tem que remover do MEGA
// também") — menos quando outra linha usa a mesma pasta. A confirmação diz
// antes o que vai acontecer; a API decide de novo (outras linhas podem não
// estar carregadas nesta tela) e o aviso final conta o que aconteceu.
async function deleteProduct(p: PricingProduct) {
  const pasta = (p.fotos_path || '').trim()
  const irmas = pasta ? products.value.filter((x) => x.id !== p.id && x.fotos_path === p.fotos_path) : []
  let aviso = ''
  if (pasta && irmas.length) {
    aviso = `\n\nA pasta no MEGA (${pasta}) NÃO será apagada: ela também é usada por ${irmas.map((x) => x.sku).join(', ')}.`
  } else if (pasta) {
    const itens = [
      p.fotos_count ? `${p.fotos_count} foto(s)` : '',
      p.videos_count ? `${p.videos_count} vídeo(s)` : '',
      p.embalagens_count ? `${p.embalagens_count} embalagem(ns)` : '',
    ].filter(Boolean).join(', ')
    aviso = `\n\nA pasta dele no MEGA (${pasta}${itens ? ` — ${itens}` : ''}) vai junto para a LIXEIRA do MEGA. Se foi engano, dá para recuperar pelo site do MEGA.`
  }
  if (!confirm(`Excluir produto "${p.sku}"?${aviso}`)) return
  try {
    const res = await api<any>(`/api/pricing/products/${p.id}`, { method: 'DELETE' })
    products.value = products.value.filter((x) => x.id !== p.id)
    const mega = res?.mega
    if (mega === 'lixeira') toast.success('Produto excluído', `A pasta ${pasta} foi para a lixeira do MEGA`)
    else if (mega === 'mantida') toast.info('Produto excluído', `A pasta ${pasta} continua no MEGA — usada por ${(res?.usada_por || []).join(', ')}`)
    else if (mega === 'erro') toast.warning('Produto excluído, mas a pasta continua no MEGA', `O MEGA não respondeu (${res?.erro || 'fora do ar'}). Apague ${pasta} pelo site do MEGA.`)
    else if (mega === 'fora_das_raizes') toast.info('Produto excluído', `A pasta ${pasta} fica no MEGA (fora de Celular/Malas/uranyx)`)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'delete_failed'
  }
}

// =========================================================== add product row

const showAddProd = ref(false)
const newProd = reactive({
  sku: '',
  name: '',
  product_type: 2,
  cost_kit1: '0',
  cost_kit2: '',
  cost_kit3: '',
  cost_kit4: '',
  cost_kit5: '',
  cost_kit6: '',
  cost_kit7: '',
  cost_kit8: '',
  preco_catalogo: '',
  ean: '',
  description: '',
  model: '',
})
const addingProd = ref(false)

function openAddProd() {
  newProd.sku = ''
  newProd.name = ''
  newProd.product_type = 2
  newProd.cost_kit1 = '0'
  newProd.cost_kit2 = ''
  newProd.cost_kit3 = ''
  newProd.cost_kit4 = ''
  newProd.cost_kit5 = ''
  newProd.cost_kit6 = ''
  newProd.cost_kit7 = ''
  newProd.cost_kit8 = ''
  newProd.preco_catalogo = ''
  newProd.ean = ''
  newProd.description = ''
  newProd.model = ''
  showAddProd.value = true
  nextTick(() => {
    const el = document.getElementById('new-prod-sku') as HTMLInputElement | null
    el?.focus()
  })
}

async function submitNewProd() {
  if (!newProd.sku.trim() || !newProd.name.trim()) return
  addingProd.value = true
  try {
    // parseDec: aceita vírgula ("674,50"). Valor ilegível aborta com
    // mensagem clara em vez de mandar "NaN" pro backend (422).
    const k1 = parseDec(newProd.cost_kit1 || 0)
    if (Number.isNaN(k1)) {
      productsErr.value = `Kit 1: preço inválido ("${newProd.cost_kit1}") — use só números, ex.: 674,50`
      return
    }
    const body: Record<string, unknown> = {
      sku: newProd.sku.trim(),
      name: newProd.name.trim(),
      department: department.value,
      product_type: newProd.product_type || 2,
      cost_kit1: k1.toFixed(2),
    }
    for (let k = 2; k <= 8; k++) {
      const v = (newProd as any)[`cost_kit${k}`]
      if (!v) continue
      const n = parseDec(v)
      if (Number.isNaN(n)) {
        productsErr.value = `Kit ${k}: preço inválido ("${v}") — use só números, ex.: 674,50`
        return
      }
      body[`cost_kit${k}`] = n.toFixed(2)
    }
    if (newProd.preco_catalogo.trim()) {
      const n = parseDec(newProd.preco_catalogo)
      if (Number.isNaN(n) || n < 0) {
        productsErr.value = `Catálogo: preço inválido ("${newProd.preco_catalogo}") — use só números, ex.: 674,50`
        return
      }
      if (n > 0) body.preco_catalogo = n.toFixed(2)
    }
    if (newProd.ean) body.ean = newProd.ean
    if (newProd.description) body.description = newProd.description
    if (newProd.model) body.model = newProd.model
    const created = await api<PricingProduct>('/api/pricing/products', { method: 'POST', body })
    products.value.push(created)
    showAddProd.value = false
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'create_failed'
  } finally {
    addingProd.value = false
  }
}

// =========================================================== overrides / grid

type CellColor = 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple' | 'pink' | 'gray'
// Célula de conta de catálogo: o /grid diz quais anúncios de catálogo a
// célula atinge e se o envio está bloqueado (o servidor decide; a tela só
// mostra e não manda célula bloqueada).
type CatalogoAnuncio = {
  external_id: string
  status: string | null
  listing_type: string | null
  // MLBs dos anúncios comuns presos a este anúncio de catálogo (item_relations)
  sincronizado_com: string[]
  // Por que ESTE anúncio não recebe: 'mesmo_anuncio' (outra linha da tabela
  // também casa com ele) | 'sincronizado' | 'pausado' | 'em_revisao' |
  // 'encerrado' | 'so_catalogo' (célula de Kit) (null = recebe o preço)
  bloqueio?: string | null
}
// Também vem na célula de KIT de conta com o catálogo ligado quando o único
// anúncio que casa é de catálogo (bloqueio 'so_catalogo'): não envia (o
// aviãozinho fica apagado; o motivo está no balão da célula).
type CatalogoCelula = {
  anuncios: CatalogoAnuncio[]
  // null = pode enviar; 'sem_anuncio' | 'mesmo_anuncio' | 'sincronizado' |
  // 'pausado' | 'em_revisao' | 'encerrado' | 'sem_tipo' | 'sem_preco_catalogo' |
  // 'so_catalogo'
  bloqueio: string | null
  texto: string | null
}
type GridCell = {
  pricing_account_id: string
  pricing_product_id: string
  price: string | number | null
  source: string
  cell_status: 'auto' | 'manual' | 'locked' | 'disabled' | 'NA' | 'SV' | 'error' | 'no_link'
  has_override: boolean
  cell_color: CellColor | null
  catalogo?: CatalogoCelula | null
}
type GridResponse = {
  accounts: Account[]
  products: PricingProduct[]
  cells: GridCell[]
}
type PushResult = {
  pricing_account_id: string
  pricing_product_id: string
  ok: boolean
  code: string
  detail: string | null
  price: string | null
  item_id: string | null
  variation_id: string | null
  cached: boolean
  // Um item por anúncio que recebeu (ou pulou) o preço.
  payload?: { links?: { success?: boolean; skipped?: boolean }[] } | null
}

const grid = ref<GridResponse | null>(null)
const gridLoading = ref(false)
const gridErr = ref<string | null>(null)
// O preço manual (disquete/Enter na célula) saiu no M-3 (17/11/2026): a
// Tabela usa só o preço calculado e o servidor recusa preço manual novo.
const pushing = ref(false)
const lastPushResults = ref<PushResult[]>([])
// SSH-style per-cell feedback while pushItemsBatch walks the queue.
// `pushStates` is keyed by `${productId}:${accountId}` and ticks through
// pushing → success/error/no_link. `bulkPushProgress` shows "3/173" while
// the loop runs. Success states fade out 5s after the run finishes; errors
// and no_link stick around so the user can review them.
type PushCellState = 'pushing' | 'success' | 'error' | 'no_link'
const pushStates = ref<Map<string, PushCellState>>(new Map())
const bulkPushProgress = ref<string>('')

function pushCellKey(productId: string, accountId: string): string {
  return `${productId}:${accountId}`
}

// ---------- Feature 7: grid search
const gridSearch = ref('')

// ---------- Feature 7b: filtros Plataforma ▾ e Contas ▾ (M-8, 18/11/2026)
// Caixas de marcar (antes era um select só de plataforma). Guardamos as
// DESMARCADAS: vazio = tudo marcado, e plataforma/conta nova já aparece.
// Não ficam lembrados: recarregar a página ou trocar de departamento volta
// com tudo marcado — veja o watch(department) lá embaixo.
const plataformasDesmarcadas = ref<Set<string>>(new Set())
const contasDesmarcadas = ref<Set<string>>(new Set())
const buscaContas = ref('')
// Filtro "Equipes ▾" (M-26, 23/11/2026): a mesma regra dos dois de cima. Guarda
// o número da equipe desmarcada ('101' = Equipe 1...); vazio = sem filtro.
const equipesDesmarcadas = ref<Set<string>>(new Set())

// ---------- Feature 8: botão Atualizar ▾ (M-14, 18/11/2026; era o Enviar ▾)
// O painel junta Atualizar planilha (o antigo Recarregar), Conferir (o antigo
// Conferir anúncios) e Atualizar anúncio (o antigo Enviar). Contas: Geral =
// as colunas visíveis; Por conta = as marcadas na lista. As duas não andam
// juntas. Tudo volta ao padrão cada vez que o painel abre (abrirPainelAtualizar).
const painelAtualizarAberto = ref(false)
const atualizarPlanilha = ref(true)
const atualizarConferir = ref(false)
const atualizarAnuncio = ref(false)
const atualizarModoContas = ref<'geral' | 'conta' | null>('geral')
const atualizarContasMarcadas = ref<Set<string>>(new Set())
const buscaAtualizarContas = ref('')
// Enquanto executa: o botão mostra a etapa e o andamento.
const atualizando = ref(false)
const atualizandoEtapa = ref<'' | 'anuncio' | 'conferir' | 'planilha'>('')

// ---------- Feature 11: keyboard nav
// Só o anel azul e as setas: a paleta "Cor:" (M-2) e o Enter que abria o
// preço manual (M-3) saíram em 17/11/2026. As cores já gravadas no banco
// (pricing_overrides.cell_color) só deixaram de aparecer.
const selectedCell = ref<{ row: number; col: number } | null>(null)

// ---------- Feature 12: compare mode
const compareProductIds = ref<Set<string>>(new Set())
const actualPriceMap = ref<Record<string, Record<string, number | null>>>({})
const competitorLoadingId = ref<string | null>(null)
const compareToast = ref<string | null>(null)

// A grade é do departamento da última carga pedida: resposta atrasada de
// outro departamento (troca rápida de aba) é descartada. `gridDepartamento`
// diz de qual departamento são as células na tela (a conferência só compara
// com a grade certa).
let seqGrid = 0
const gridDepartamento = ref<string | null>(null)

async function loadGrid() {
  const dep = department.value
  const seq = ++seqGrid
  gridLoading.value = true
  gridErr.value = null
  try {
    const qs = dep ? `?department=${dep}` : ''
    const r = await api<GridResponse>(`/api/pricing/grid${qs}`)
    if (seq !== seqGrid) return
    grid.value = r
    gridDepartamento.value = dep
  } catch (e: any) {
    if (seq !== seqGrid) return
    gridErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    if (seq === seqGrid) gridLoading.value = false
  }
  if (seq !== seqGrid) return
  // Stock + sales maps load in the background; failures don't break the grid.
  loadStockMap()
  loadSalesMaps()
}

// No mesmo departamento a grade velha vale enquanto recarrega (depois de cada
// edição): os (!) e os cartões vermelhos não piscam.
const gradePronta = computed(() => !!grid.value && gridDepartamento.value === department.value)

// Maps: pricing_product_id -> integer (stock or units sold)
const stockMap = ref<Record<string, number>>({})
const salesMap7d = ref<Record<string, number>>({})
const salesMap30d = ref<Record<string, number>>({})
// Parcelas do Estoque (02/11/2026): no celular a coluna soma os lotes
// (ci, pi, ra, sa, sp, cd); o balão da célula mostra de onde veio o número.
// `peca` = a peça da linha (base no celular, SKU na mala/eletro), para a
// Reposição item a item. `conjunto` = linha só de composição do Bling
// (conjunto de mala, kit com acessório): ali a Reposição não se aplica.
type ParteEstoque = { sku: string; estoque: number; peca?: string }
type EstoqueDetalhe = { total: number; partes: ParteEstoque[]; conjunto?: boolean }
const stockPartes = ref<Record<string, ParteEstoque[]>>({})
const stockConjunto = ref<Record<string, boolean>>({})
const salesPecas30d = ref<Record<string, Record<string, number>>>({})
// A Reposição só é calculada com estoque e vendas carregados: mapa que falhou
// ou ainda não chegou daria um vermelho falso. O contador descarta a resposta
// atrasada de outro departamento. O loadGrid relê os mapas depois de cada
// edição: no mesmo departamento os números ficam na tela enquanto atualiza
// (sem piscar "…"), e uma releitura que falha mantém os anteriores.
type EstadoMapa = 'carregando' | 'ok' | 'erro'
const estoqueEstado = ref<EstadoMapa>('carregando')
const vendas7Estado = ref<EstadoMapa>('carregando')
const vendasEstado = ref<EstadoMapa>('carregando')
let seqEstoque = 0
let seqVendas = 0
let estoqueDoDepartamento: string | null = null
let vendas7DoDepartamento: string | null = null
let vendasDoDepartamento: string | null = null

async function loadStockMap() {
  const dep = department.value
  const seq = ++seqEstoque
  const temDados = estoqueDoDepartamento === dep && estoqueEstado.value === 'ok'
  if (!temDados) estoqueEstado.value = 'carregando'
  try {
    const qs = dep ? `&department=${dep}` : ''
    const r = await api<Record<string, number | EstoqueDetalhe>>(`/api/pricing/stock-map?detalhe=1${qs}`)
    if (seq !== seqEstoque) return
    const totais: Record<string, number> = {}
    const partes: Record<string, ParteEstoque[]> = {}
    const conjunto: Record<string, boolean> = {}
    for (const [id, v] of Object.entries(r)) {
      if (typeof v === 'number') {
        totais[id] = v
      } else {
        totais[id] = Number(v.total) || 0
        if (v.partes?.length) partes[id] = v.partes
        if (v.conjunto) conjunto[id] = true
      }
    }
    stockMap.value = totais
    stockPartes.value = partes
    stockConjunto.value = conjunto
    estoqueDoDepartamento = dep
    estoqueEstado.value = 'ok'
  } catch {
    if (seq !== seqEstoque || temDados) return
    stockMap.value = {}
    stockPartes.value = {}
    stockConjunto.value = {}
    estoqueDoDepartamento = null
    estoqueEstado.value = 'erro'
  }
}

async function loadSalesMaps() {
  const dep = department.value
  const seq = ++seqVendas
  const tem7 = vendas7DoDepartamento === dep && vendas7Estado.value === 'ok'
  const tem30 = vendasDoDepartamento === dep && vendasEstado.value === 'ok'
  if (!tem7) vendas7Estado.value = 'carregando'
  if (!tem30) vendasEstado.value = 'carregando'
  const qs = dep ? `&department=${dep}` : ''
  type Vendas30 = Record<string, number | { total: number; pecas?: Record<string, number> }>
  const [m7, m30] = await Promise.all([
    api<Record<string, number>>(`/api/pricing/sales-map?days=7${qs}`).catch(() => null),
    api<Vendas30>(`/api/pricing/sales-map?days=30&detalhe=1${qs}`).catch(() => null),
  ])
  if (seq !== seqVendas) return
  if (m7) {
    salesMap7d.value = m7
    vendas7DoDepartamento = dep
    vendas7Estado.value = 'ok'
  } else if (!tem7) {
    salesMap7d.value = {}
    vendas7DoDepartamento = null
    vendas7Estado.value = 'erro'
  }
  if (m30) {
    const totais: Record<string, number> = {}
    const pecas: Record<string, Record<string, number>> = {}
    for (const [id, v] of Object.entries(m30)) {
      if (typeof v === 'number') {
        totais[id] = v
      } else {
        totais[id] = Number(v.total) || 0
        if (v.pecas) pecas[id] = v.pecas
      }
    }
    salesMap30d.value = totais
    salesPecas30d.value = pecas
    vendasDoDepartamento = dep
    vendasEstado.value = 'ok'
  } else if (!tem30) {
    salesMap30d.value = {}
    salesPecas30d.value = {}
    vendasDoDepartamento = null
    vendasEstado.value = 'erro'
  }
}

function textoVendas(estado: EstadoMapa, valor: number | undefined): string {
  if (estado === 'carregando') return '…'
  if (estado === 'erro') return '—'
  return (valor ?? 0) === 0 ? '—' : String(valor)
}

function tituloVendas(estado: EstadoMapa, dias: number): string {
  if (estado === 'erro') return 'As vendas não carregaram — recarregue a tabela.'
  if (estado === 'carregando') return 'carregando…'
  return `Unidades vendidas nos últimos ${dias} dias (sem cancelados nem transferências internas; peça de kit conta)`
}

function tituloEstoque(prodId: string): string {
  if (estoqueEstado.value === 'carregando') return 'carregando…'
  if (estoqueEstado.value === 'erro') return 'O estoque não carregou — recarregue a tabela.'
  const partes = stockPartes.value[prodId]
  const total = stockMap.value[prodId] ?? 0
  if (!partes?.length) return `Estoque no Bling: ${total}`
  if (partes.length === 1) return `Estoque no Bling: ${partes[0].sku} ${partes[0].estoque}`
  const oQueSoma = department.value === 'celular' ? 'soma dos lotes' : 'soma dos SKUs da linha'
  return `Estoque no Bling, ${oQueSoma}: ${partes.map((p) => `${p.sku} ${p.estoque}`).join(' + ')} = ${total}`
}

// ---------- Reposição (02/11/2026, tarefas do Eduardo)
// Mesma conta da Importação (utils/reposicao.ts) e com o MESMO prazo dela,
// por departamento (import_config): mudar aqui muda lá. Só quem edita a
// Importação muda o prazo (o PATCH é o da Importação).
// M-2 (17/11/2026), igual à Importação no M-4: um campo só, "Reposição [N]
// dias" na barra, e a conta usa só ele —
//   Reposição = Estoque − vendas 30d × Reposição ÷ 30
// O tempo de estoque saiu da tela e da conta (vai 0); continua no banco e
// no GET /reposicao-config, que a tela ignora.
type PrazosReposicao = {
  department: string
  tempo_reposicao: number | null
  pode_editar: boolean
}
type CampoPrazo = 'tempo_reposicao'
const prazos = ref<PrazosReposicao | null>(null)
const prazosErro = ref(false)
const salvandoPrazos = ref(false)
let seqPrazos = 0

async function carregarPrazos() {
  if (import.meta.server) return
  const dep = department.value
  const seq = ++seqPrazos
  try {
    const r = await api<PrazosReposicao>(`/api/pricing/reposicao-config?department=${encodeURIComponent(dep)}`)
    if (seq !== seqPrazos) return
    prazos.value = r
    prazosErro.value = false
  } catch {
    if (seq !== seqPrazos) return
    prazos.value = null
    prazosErro.value = true
  }
}

// Manda SÓ o prazo da Reposição (a Importação mexe na mesma linha: mandar o
// tempo de estoque junto regravaria um valor velho) e mostra o que voltou
// do servidor.
async function salvarPrazos(campo: CampoPrazo) {
  const p = prazos.value
  if (!p?.pode_editar) return
  const valor = p[campo] as unknown
  if (!Number.isInteger(valor) || (valor as number) < 0 || (valor as number) > 999) {
    toast.error('Prazo inválido', 'Use um número inteiro de dias, de 0 a 999.')
    await carregarPrazos()
    return
  }
  salvandoPrazos.value = true
  const seq = ++seqPrazos
  try {
    const r = await api<{ tempo_reposicao: number }>(
      `/api/importacao/config?categoria=${encodeURIComponent(p.department)}`,
      { method: 'PATCH', body: { [campo]: valor } },
    )
    if (seq === seqPrazos && prazos.value?.department === p.department) {
      prazos.value = { ...prazos.value, tempo_reposicao: r.tempo_reposicao }
    }
  } catch {
    toast.error('Não salvou o prazo', 'Tente de novo; o valor voltou ao que está salvo.')
    await carregarPrazos()
  } finally {
    salvandoPrazos.value = false
  }
}

const formulaReposicao = computed(() => textoFormulaReposicao(prazos.value?.tempo_reposicao))

// Balão do campo "Reposição [N] dias" da barra: a conta, o que cada parte
// quer dizer (era a explicação da caixa amarela, que saiu no M-2) e quem
// pode mudar. Carregando/erro/sem prazo também aparecem aqui.
const tituloPrazos = computed(() => {
  const quem = prazos.value?.pode_editar
    ? 'O mesmo prazo da tela Importação deste departamento: mudar aqui muda lá.'
    : 'O prazo é o da tela Importação; só quem edita a Importação pode mudar.'
  const estoque = department.value === 'celular'
    ? 'soma dos lotes (ci, pi, ra, sa, sp, cd) no Bling'
    : 'soma dos SKUs da linha no Bling'
  const estado = prazosErro.value
    ? 'O prazo da Reposição não carregou — recarregue a página.'
    : !prazos.value
      ? 'carregando o prazo…'
      : prazos.value.tempo_reposicao == null
        ? 'Este departamento não tem prazo de reposição na Importação.'
        : ''
  return [
    estado,
    `${formulaReposicao.value}.`,
    'Negativo, em vermelho = falta estoque para o prazo: é quanto comprar (linha com várias cores ou voltagens: soma só as que faltam).',
    `Estoque = ${estoque}; 30d = vendas dos últimos 30 dias (sem cancelados; kit e conjunto contam em cada peça).`,
    'Não desconta o que já está em lote aberto na Importação.',
    quem,
  ].filter(Boolean).join('\n')
})

// Uma célula de Reposição. Na linha com várias peças (cores, voltagens) a
// conta é peça a peça: a sobra de uma não cobre a falta de outra — a célula
// mostra a soma só das faltas (quanto comprar) e o balão lista as peças.
type ReposicaoCelula =
  | { tipo: 'carregando' }
  | { tipo: 'erro' }
  | { tipo: 'sem_prazo' }
  | { tipo: 'conjunto' }
  | { tipo: 'ok'; valor: number; titulo: string }

const PECAS_NO_BALAO = 10

function estoquePorPeca(prodId: string): Record<string, number> | null {
  const partes = stockPartes.value[prodId]
  if (!partes?.length) return {}
  if (partes.some((p) => !p.peca)) return null
  const m: Record<string, number> = {}
  for (const p of partes) m[p.peca!] = (m[p.peca!] ?? 0) + (Number(p.estoque) || 0)
  return m
}

function calcularCelulaReposicao(prodId: string): ReposicaoCelula {
  if (
    estoqueEstado.value === 'carregando'
    || vendasEstado.value === 'carregando'
    || !prazos.value
    || prazos.value.department !== department.value
  ) {
    return { tipo: prazosErro.value ? 'erro' : 'carregando' }
  }
  if (estoqueEstado.value === 'erro' || vendasEstado.value === 'erro') return { tipo: 'erro' }
  // Um prazo só (M-2): o tempo de estoque vai 0, como na Importação.
  const tr = prazos.value.tempo_reposicao
  if (prazoDias(tr) == null) return { tipo: 'sem_prazo' }
  if (stockConjunto.value[prodId]) return { tipo: 'conjunto' }
  const formula = textoFormulaReposicao(tr)
  const porEstoque = estoquePorPeca(prodId)
  const porVendas = salesPecas30d.value[prodId]
  if (porEstoque && porVendas) {
    const linha = reposicaoPorPeca(porEstoque, porVendas, tr, 0)
    if (linha && linha.pecas.length > 1) {
      const ordem = [...linha.pecas].sort((a, b) => a.r.valor - b.r.valor)
      const linhas = [
        linha.faltando.length
          ? `Faltam ${-linha.valor} no total, somando só os itens da linha que faltam (a sobra de um não cobre a falta de outro):`
          : `Nenhum item da linha falta; sobram ${linha.valor} no total:`,
        ...ordem.slice(0, PECAS_NO_BALAO).map((p) => {
          const res = p.r.valor < 0 ? `faltam ${-p.r.valor}` : p.r.valor > 0 ? `sobram ${p.r.valor}` : 'exato'
          return `${p.peca}: estoque ${p.estoque}, ${p.vendas30d} vendidos em 30 dias → ${res}`
        }),
      ]
      if (ordem.length > PECAS_NO_BALAO) linhas.push(`… e mais ${ordem.length - PECAS_NO_BALAO} itens`)
      linhas.push(formula)
      return { tipo: 'ok', valor: linha.valor, titulo: linhas.join('\n') }
    }
  }
  const estoque = stockMap.value[prodId] ?? 0
  const vendas = salesMap30d.value[prodId] ?? 0
  const r = calcularReposicao(estoque, vendas, tr, 0)
  if (!r) return { tipo: 'sem_prazo' }
  return { tipo: 'ok', valor: r.valor, titulo: `${explicarReposicao(r, estoque, vendas)}\n${formula}` }
}

// Calculado uma vez por mudança (o template só consulta o mapa).
const reposicoes = computed(() => {
  const m = new Map<string, ReposicaoCelula>()
  for (const p of grid.value?.products ?? []) m.set(p.id, calcularCelulaReposicao(p.id))
  return m
})

function reposicaoDe(prodId: string): number | null {
  const c = reposicoes.value.get(prodId)
  return c?.tipo === 'ok' ? c.valor : null
}

function textoReposicao(prodId: string): string {
  const c = reposicoes.value.get(prodId)
  if (!c || c.tipo === 'carregando') return '…'
  if (c.tipo !== 'ok') return '—'
  return c.valor < 0 ? `−${-c.valor}` : String(c.valor)
}

function tituloReposicao(prodId: string): string {
  const c = reposicoes.value.get(prodId)
  if (!c || c.tipo === 'carregando') return 'carregando…'
  if (c.tipo === 'erro') return 'Estoque, vendas ou o prazo da Reposição não carregaram — recarregue a tabela.'
  if (c.tipo === 'sem_prazo') return 'Sem prazo de reposição para este departamento (o prazo vem da Importação).'
  if (c.tipo === 'conjunto') {
    return 'Linha de conjunto/kit (composição no Bling): a compra é feita pelos itens avulsos, que têm a Reposição na linha deles. Somar aqui contaria a mesma demanda duas vezes.'
  }
  return c.titulo
}

// ---------- Conferência dos anúncios (02/11/2026)
// O servidor lê o preço VIVO de cada anúncio (job no worker, só na produção)
// e guarda; aqui a célula ganha (!) quando algum anúncio dela foge da
// margem de erro em relação ao preço da Tabela. Mudar a margem não relê
// nada: só recalcula na tela.
// M-2/M-3 (17/11/2026): a leitura vale para todos que veem a Tabela (o
// servidor só manda as células das contas que a pessoa vê na grade); o
// botão "Margem" continua só do administrador. A margem saiu do navegador e
// foi para o servidor (Geral + por produto). M-7 (18/11/2026): "Conferir
// anúncios" é de todos; quem não é administrador vê no balão a data e o
// andamento, sem as contas que não leram (o servidor não manda o resumo).
// Os tipos da conferência (AnuncioConferido, CelulaConferida, MargemConferencia,
// Divergencia) e a regra do cartão vermelho moram em lib/cartaoVermelho.ts
// desde a M-22 (24/11/2026): o Faturamento conta as Pendências com ela.
// POST /api/pricing/conferencia/celula (M-15): a célula como ficou gravada
// (null = não manda mais preço e saiu) ou o erro da leitura (nada gravado).
type ConferirCelulaResposta = {
  celula: CelulaConferida | null
  erro: string | null
  // 19/11/2026: a marca "sem anúncio vinculado" não vale nesta célula (o
  // servidor já tirou). Vem também com `erro` (a leitura falhou, a marca saiu).
  tem_vinculo?: boolean
}
type JobConferencia = {
  id: string
  status: BatchJob['status']
  total: number
  processed: number
  created_at: string
  finished_at: string | null
  error: string | null
  result: {
    celulas?: number
    anuncios?: number
    lidos?: number
    sem_leitura?: number
    contas_com_erro?: { conta: string; motivo: string }[]
  } | null
  // Revisão M-14: conferência só de algumas contas → quantas; null = inteira.
  parcial?: number | null
}
// `ultimo_concluido`: o último job do departamento que terminou bem.
// `margem` e `pode_conferir` vêm desde o M-2 (pode_conferir = todos desde o M-7).
type ConferenciaResposta = {
  job: JobConferencia | null
  ultimo_concluido: string | null
  celulas: CelulaConferida[]
  margem?: Partial<MargemConferencia> | null
  pode_conferir?: boolean
  // 19/11/2026: células com "sem anúncio vinculado" gravado agora (só com as
  // células); a grade aberta tira a marca das que não estão mais aqui.
  sem_vinculo?: { pricing_account_id: string; pricing_product_id: string }[] | null
}

const CHAVE_OCULTAR_KITS = 'precos.ocultarKits'
const CHAVE_OCULTAR_ESTOQUE = 'precos.ocultarEstoque'
const conferencia = ref<ConferenciaResposta | null>(null)
// 'carregando' até a 1ª resposta do departamento; 'erro' se ela falhou (aí o
// balão do "Conferir anúncios" não diz "ainda não conferido", que levaria a
// conferir de novo à toa).
const conferenciaEstado = ref<EstadoMapa>('carregando')
const conferenciaIniciando = ref(false)
// A releitura falhou com dados na tela (ex.: a do fim da conferência): eles
// podem ser de antes do fim — o balão do botão avisa e a tela tenta de novo
// algumas vezes ('tentando') antes de pedir para recarregar ('desistiu').
const releitura = ref<'ok' | 'tentando' | 'desistiu'>('ok')
const TENTATIVAS_RELEITURA = 5
let tentativasReleitura = 0
let seqConferencia = 0
let timerConferencia: ReturnType<typeof setTimeout> | null = null
let desmontado = false
// A margem do departamento (null até a conferência carregar). `seqMargem`
// conta as gravações: uma leitura da conferência que saiu antes de uma
// gravação não desfaz a margem que acabou de ser gravada.
const margem = ref<MargemConferencia | null>(null)
const salvandoMargem = ref(false)
let seqMargem = 0
// Conferir uma célula só (M-15, 18/11/2026): as células sendo lidas
// (produto::conta), o erro da última leitura de cada uma (o balão do cartão
// mostra) e os resultados que chegaram, com a ordem em que chegaram — uma
// leitura inteira da conferência que saiu antes não volta a célula para o
// dado mais antigo.
const celulasConferindo = ref<Set<string>>(new Set())
const errosDaCelula = ref<Record<string, string>>({})
let seqCelula = 0
const celulasRecentes = new Map<string, { seq: number; celula: CelulaConferida | null }>()

const conferenciaRodando = computed(() => {
  const s = conferencia.value?.job?.status
  return s === 'pending' || s === 'running'
})

// A margem como a tela usa (margemDaResposta, lib/cartaoVermelho.ts).

async function carregarConferencia() {
  if (import.meta.server || desmontado) return
  const dep = department.value
  const seq = ++seqConferencia
  const margemAntes = seqMargem
  const celulasAntes = seqCelula
  try {
    const r = await api<ConferenciaResposta>(`/api/pricing/conferencia?department=${encodeURIComponent(dep)}`)
    if (seq !== seqConferencia || desmontado) return
    r.celulas = comCelulasRecentes(r.celulas ?? [], celulasAntes)
    conferencia.value = r
    aplicarSemVinculo(r.sem_vinculo, dep)
    conferenciaEstado.value = 'ok'
    releitura.value = 'ok'
    tentativasReleitura = 0
    if (margemAntes === seqMargem || margem.value?.department !== dep) margem.value = margemDaResposta(r.margem, dep)
  } catch {
    if (seq !== seqConferencia || desmontado) return
    // Falha passageira com dados do mesmo departamento: fica com os últimos,
    // avisa e relê daqui a pouco (algumas vezes).
    if (!conferencia.value) {
      conferenciaEstado.value = 'erro'
    } else if (tentativasReleitura < TENTATIVAS_RELEITURA) {
      tentativasReleitura++
      releitura.value = 'tentando'
      if (timerConferencia) clearTimeout(timerConferencia)
      timerConferencia = setTimeout(() => void carregarConferencia(), 10000)
      return
    } else {
      releitura.value = 'desistiu'
    }
  }
  agendarConferencia()
}

// Administrador, com o job rodando: a cada 4 s só o andamento (leve,
// /api/jobs/{id}/status); as células (resposta grande, MBs na mala) a cada 5
// voltas e no fim — as contas vão chegando aos poucos.
// M-7 (18/11/2026): no resto do tempo, e para quem não é administrador (o
// /api/jobs não mostra a conferência a ele), a consulta de 30 s. Só com a
// aba Tabela aberta: fora dela (ou fora da página) o timer para.
const VOLTAS_ATE_RELER_CELULAS = 5
const INTERVALO_CONSULTA = 30000
let voltasConferencia = 0
function agendarConferencia() {
  if (timerConferencia) clearTimeout(timerConferencia)
  timerConferencia = null
  if (desmontado || !import.meta.client || tab.value !== 'tabela') return
  timerConferencia = conferenciaRodando.value && isAdmin.value
    ? setTimeout(() => void acompanharConferencia(), 4000)
    : setTimeout(() => void consultarConferencia(), INTERVALO_CONSULTA)
}

// A consulta de 30 s (M-7): leve, só o último job (`celulas=false`). Vale
// para a conferência de qualquer pessoa. Rodando: atualiza o andamento do
// balão. Quando a que rodava termina, ou aparece outra já terminada, relê a
// conferência inteira: os (!) e os cartões se atualizam sozinhos, sem
// recarregar a página nem a grade.
async function consultarConferencia() {
  const antes = conferencia.value
  // Sem dados ou com a releitura falhando: relê tudo.
  if (!antes || releitura.value !== 'ok') return carregarConferencia()
  const seq = seqConferencia
  try {
    const r = await api<ConferenciaResposta>(
      `/api/pricing/conferencia?department=${encodeURIComponent(department.value)}&celulas=false`,
    )
    // Uma leitura inteira (troca de departamento, clique) passou na frente.
    if (desmontado || seq !== seqConferencia || conferencia.value !== antes) return
    const job = r.job
    const rodando = job?.status === 'pending' || job?.status === 'running'
    const terminou = !rodando && (job?.id !== antes.job?.id || job?.status !== antes.job?.status)
    if (terminou || r.ultimo_concluido !== antes.ultimo_concluido) {
      await carregarConferencia()
      return
    }
    antes.job = job
  } catch {
    /* rede piscou: tenta de novo na próxima volta */
  }
  agendarConferencia()
}

async function acompanharConferencia() {
  const job = conferencia.value?.job
  if (!job || desmontado) return
  const dep = department.value
  try {
    const atual = await api<{ status: BatchJob['status']; total: number; processed: number; error?: string | null }>(
      `/api/jobs/${job.id}/status`,
    )
    if (desmontado || dep !== department.value || conferencia.value?.job?.id !== job.id) return
    conferencia.value.job = {
      ...conferencia.value.job,
      status: atual.status,
      total: atual.total,
      processed: atual.processed,
      error: atual.error ?? null,
    }
    voltasConferencia++
    const terminou = atual.status !== 'pending' && atual.status !== 'running'
    if (terminou || voltasConferencia % VOLTAS_ATE_RELER_CELULAS === 0) {
      await carregarConferencia()
      return
    }
  } catch {
    /* rede piscou: tenta de novo na próxima volta */
  }
  agendarConferencia()
}
onBeforeUnmount(() => {
  desmontado = true
  if (timerConferencia) clearTimeout(timerConferencia)
})

// M-14 (18/11/2026): `contas` = só estas colunas (as das outras ficam com a
// leitura anterior); sem `contas`, todas as colunas que a pessoa vê (o
// departamento inteiro para o administrador). `dep`: o departamento de
// quando o Atualizar foi clicado (revisão M-14).
async function conferirAnuncios(contas?: string[], dep = department.value) {
  if (conferenciaIniciando.value || conferenciaRodando.value) return
  conferenciaIniciando.value = true
  try {
    const body = contas ? { department: dep, contas } : { department: dep }
    await api('/api/pricing/conferencia', { method: 'POST', body })
    toast.success('Conferência começou', 'Os anúncios estão sendo lidos nos marketplaces.')
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'so_no_servidor') {
      toast.warning('A conferência só roda no servidor', 'Ela lê os anúncios ao vivo nos marketplaces.')
    } else if (code === 'conferencia_em_andamento') {
      toast.warning('Já há uma conferência rodando', 'O andamento aparece no botão "Atualizar", na linha Conferir.')
    } else {
      toast.error('A conferência não começou', textoErroConferencia(code) || 'erro desconhecido')
    }
  } finally {
    conferenciaIniciando.value = false
  }
  await carregarConferencia()
}

// Códigos de job.error da conferência, em português de gente.
const ERROS_CONFERENCIA: Record<string, string> = {
  so_no_servidor: 'só roda no servidor',
  tempo_esgotado: 'passou de 1 hora e parou — confira de novo',
  interrompida: 'o servidor reiniciou no meio (ex.: atualização) — confira de novo',
  worker_nao_executou: 'o servidor não pegou a tarefa — confira de novo',
  parou_sem_sinal: 'parou sem dar sinal — confira de novo',
  orphan_no_heartbeat: 'parou sem dar sinal — confira de novo',
  fila_indisponivel: 'a fila do servidor não respondeu — confira de novo',
  conferencia_em_andamento: 'já havia outra conferência rodando',
  invalid_department: 'departamento inválido',
  contas_invalidas: 'alguma conta escolhida não está mais neste departamento — atualize a planilha',
}
// Código desconhecido (ex.: o texto de uma exceção do servidor) não aparece
// cru: o balão mostra o texto genérico e o código entre parênteses.
function textoErroConferencia(codigo: string | null | undefined): string {
  return codigo ? ERROS_CONFERENCIA[codigo] ?? 'erro inesperado no servidor — confira de novo' : ''
}

// ---------- Conferir só uma célula (M-15, 18/11/2026)
// Clique no cartão vermelho: o servidor lê na hora só os anúncios vinculados
// daquela célula (aquele produto naquela conta) e grava só ela, sem mexer no
// job nem na data da última conferência do departamento. A célula atualiza
// aqui sem reler a conferência inteira. Mesma permissão do "Conferir
// anúncios" (`pode_conferir`); conta sem integração não tem o que ler.
const ERROS_CELULA: Record<string, string> = {
  so_no_servidor: 'a conferência só roda no servidor',
  celula_em_conferencia: 'esta célula já está sendo conferida',
  account_not_found: 'esta conta não aparece mais na sua Tabela',
  product_not_found: 'este produto não existe mais',
  produto_sem_departamento: 'este produto não está em nenhuma aba',
}
function textoErroCelula(codigo: string | null | undefined): string {
  return (codigo && ERROS_CELULA[codigo]) || 'não deu para ler agora — tente de novo'
}

// A lista da conferência com a célula `chave` (produto::conta) trocada pela
// nova, ou sem ela (null: a célula não manda mais preço).
function trocarCelula(celulas: CelulaConferida[], chave: string, nova: CelulaConferida | null): CelulaConferida[] {
  const outras = celulas.filter((c) => `${c.pricing_product_id}::${c.pricing_account_id}` !== chave)
  return nova ? [...outras, nova] : outras
}

// A célula conferida sozinha DEPOIS que uma leitura inteira saiu (`desde`) é
// mais nova que a resposta dela: fica a da célula. As de antes já vieram do
// servidor e saem da lista.
function comCelulasRecentes(celulas: CelulaConferida[], desde: number): CelulaConferida[] {
  let lista = celulas
  for (const [chave, r] of celulasRecentes) {
    if (r.seq > desde) lista = trocarCelula(lista, chave, r.celula)
    else celulasRecentes.delete(chave)
  }
  return lista
}

// O balão do cartão: os motivos, o erro da última leitura da célula e, quando
// dá para clicar, o convite.
function tituloDoCartao(motivos: string, clicavel: boolean, erro: string | null): string {
  const linhas = [motivos]
  if (erro) linhas.push(`A conferência desta célula falhou: ${erro}`)
  if (clicavel) linhas.push('Clique para conferir só esta célula')
  return linhas.join('\n')
}

// ---------- "Sem anúncio vinculado" vencido (19/11/2026)
// O envio que não acha vínculo grava `no_link` na célula, e só um envio com
// sucesso tirava: vínculo criado depois deixava o cartão vermelho preso. Hoje
// o servidor tira na conferência (inteira ou do clique); aqui a grade aberta
// acompanha, sem relê-la inteira (o loadGrid de fora de hora desfaria um custo
// recém-digitado pelo administrador). Tira também o cinza do envio que ficou.
function tirarSemVinculoDaGrade(prodId: string, accId: string) {
  const c = cellOf(prodId, accId)
  if (c?.cell_status === 'no_link') c.cell_status = 'auto'
  const ck = pushCellKey(prodId, accId)
  if (pushStates.value.get(ck) === 'no_link') {
    const m = new Map(pushStates.value)
    m.delete(ck)
    pushStates.value = m
  }
}

// `lista`: as células com no_link gravado agora (GET da conferência). A grade
// do mesmo departamento tira a marca das que não estão mais nela.
function aplicarSemVinculo(
  lista: ConferenciaResposta['sem_vinculo'],
  dep: string,
) {
  if (!lista || !grid.value || gridDepartamento.value !== dep) return
  const ainda = new Set(lista.map((c) => `${c.pricing_product_id}::${c.pricing_account_id}`))
  for (const c of grid.value.cells) {
    if (c.cell_status === 'no_link' && !ainda.has(`${c.pricing_product_id}::${c.pricing_account_id}`)) {
      tirarSemVinculoDaGrade(c.pricing_product_id, c.pricing_account_id)
    }
  }
}

function conferindoCelula(prodId: string, accId: string): boolean {
  return celulasConferindo.value.has(`${prodId}::${accId}`)
}

function erroDaCelula(prodId: string, accId: string): string | null {
  return errosDaCelula.value[`${prodId}::${accId}`] ?? null
}

// Dá para conferir a célula: quem pode conferir, e a conta tem integração
// (na coluna de catálogo, a conta de kit dela).
function celulaConferivel(prodId: string, acc: Account): boolean {
  if (!conferencia.value?.pode_conferir) return false
  if (contaParametros(acc).integration_id == null) return false
  const cat = cellOf(prodId, acc.id)?.catalogo
  return !(cat?.bloqueio === 'sem_anuncio' && tipoSemAnuncio(cat.texto) === 'sem_integracao')
}

function trocarErroDaCelula(chave: string, erro: string | null) {
  const erros = { ...errosDaCelula.value }
  if (erro) erros[chave] = erro
  else delete erros[chave]
  errosDaCelula.value = erros
}

async function conferirCelula(prodId: string, acc: Account) {
  const chave = `${prodId}::${acc.id}`
  // Dois cliques seguidos: uma leitura só.
  if (celulasConferindo.value.has(chave) || !celulaConferivel(prodId, acc)) return
  const dep = department.value
  celulasConferindo.value = new Set([...celulasConferindo.value, chave])
  trocarErroDaCelula(chave, null)
  try {
    const r = await api<ConferirCelulaResposta>('/api/pricing/conferencia/celula', {
      method: 'POST',
      body: { pricing_account_id: acc.id, pricing_product_id: prodId },
    })
    if (desmontado || dep !== department.value) return
    // A marca "sem anúncio vinculado" não vale (o servidor tirou antes de ler):
    // sai da grade aberta mesmo se a leitura falhou.
    if (r.tem_vinculo) tirarSemVinculoDaGrade(prodId, acc.id)
    if (r.erro) {
      // A leitura não foi gravada: o cartão continua e o balão mostra o erro.
      trocarErroDaCelula(chave, r.erro)
      return
    }
    const celula = r.celula ?? null
    celulasRecentes.set(chave, { seq: ++seqCelula, celula })
    if (conferencia.value) conferencia.value.celulas = trocarCelula(conferencia.value.celulas, chave, celula)
  } catch (e: any) {
    if (desmontado || dep !== department.value) return
    trocarErroDaCelula(chave, textoErroCelula(e?.data?.detail?.code))
  } finally {
    const s = new Set(celulasConferindo.value)
    s.delete(chave)
    celulasConferindo.value = s
  }
}

// Balão da conferência (desde o M-14 na linha Conferir do Atualizar ▾; antes
// no botão "Conferir anúncios"): o que antes ficava escrito na faixa roxa
// (última conferência, andamento, não terminou, contas que não leram, não
// carregou). A faixa e o resumo saíram no M-2.
const tituloConferir = computed(() => {
  const linhas = ['Lê agora o preço de cada anúncio deste departamento nos marketplaces e compara com a Tabela.']
  const conf = conferencia.value
  const job = conf?.job
  if (!conf) {
    linhas.push(conferenciaEstado.value === 'erro'
      ? 'A última conferência não carregou — recarregue a página antes de conferir de novo.'
      : 'Carregando a última conferência…')
  } else if (!job) {
    linhas.push('Ainda não conferido.')
  } else if (conferenciaRodando.value) {
    linhas.push(`Lendo os anúncios: ${job.processed} de ${job.total || '…'} contas.`)
  } else {
    // Revisão M-14: a parcial não é o departamento inteiro — as outras contas
    // ficaram com a leitura anterior (e o aviso delas continua abaixo).
    const parcial = job.parcial != null ? ` — parcial (${job.parcial === 1 ? '1 conta' : `${job.parcial} contas`})` : ''
    linhas.push(`Última conferência: ${fmtQuando(job.finished_at || job.created_at)}${parcial}.`)
    if (job.status === 'failed' || job.status === 'cancelled') {
      const cru = job.error && !ERROS_CONFERENCIA[job.error] ? ` (${job.error})` : ''
      linhas.push(`Não terminou${job.error ? `: ${textoErroConferencia(job.error)}${cru}` : ''}.`)
    }
    const contas = job.result?.contas_com_erro ?? []
    if (contas.length) {
      linhas.push(`${contas.length} conta(s) não leram:`, ...contas.map((c) => `• ${c.conta}: ${c.motivo}`))
    }
  }
  if (releitura.value !== 'ok') {
    linhas.push(releitura.value === 'tentando'
      ? 'O resultado mais recente não carregou — tentando de novo…'
      : 'O resultado mais recente não carregou — recarregue a página.')
  }
  return linhas.join('\n')
})

// O balão tem um aviso (não carregou, não terminou, conta que não leu):
// o botão ganha um alerta para a pessoa saber que vale passar o mouse.
const conferirComAviso = computed(() => {
  const job = conferencia.value?.job
  if (!conferencia.value) return conferenciaEstado.value === 'erro'
  if (releitura.value !== 'ok') return true
  if (!job || conferenciaRodando.value) return false
  return job.status === 'failed' || job.status === 'cancelled' || !!job.result?.contas_com_erro?.length
})

// Linha "Conferir" do Atualizar ▾ (M-14): aparece para quem pode conferir (o
// servidor diz; todos que veem a Tabela desde o M-7). Enquanto a resposta
// não chega, aparece, como o botão de antes.
const podeConferir = computed(() => conferencia.value?.pode_conferir !== false)

// À direita da linha "Conferir": "última 18/11 12:06" ou o andamento.
const textoUltimaConferencia = computed(() => {
  const conf = conferencia.value
  const job = conf?.job
  if (!conf) return conferenciaEstado.value === 'erro' ? 'não carregou' : '…'
  if (!job) return 'nunca'
  if (conferenciaRodando.value) return `lendo ${job.processed}/${job.total || '…'}`
  return `última ${fmtQuando(job.finished_at || job.created_at).replace(',', '')}`
})

const conferenciaPorCelula = computed(() => {
  const m = new Map<string, CelulaConferida>()
  for (const c of conferencia.value?.celulas ?? []) {
    m.set(`${c.pricing_product_id}::${c.pricing_account_id}`, c)
  }
  return m
})

// Margem da linha, (!) e cartão vermelho: lib/cartaoVermelho.ts (M-22).
// A célula que a grade mostra com preço que vai para o marketplace.
function celulaComparavelDaGrade(prodId: string, accId: string): GridCell | null {
  return celulaComparavel(cellOf(prodId, accId))
}

// Compara com o preço ATUAL da célula (o da Tabela agora), não com o da hora
// da conferência, e com a margem da linha. Só com a grade e a margem do
// departamento certo carregadas.
// Calculado uma vez para a grade inteira (a célula só consulta o mapa).
const divergencias = computed(() => {
  const mg = margem.value
  if (!mg || mg.department !== department.value || !gradePronta.value) return new Map<string, Divergencia>()
  return divergenciasDaConferencia(conferencia.value?.celulas ?? [], mg, celulaComparavelDaGrade)
})

function divergenciaDe(prodId: string, accId: string): Divergencia | null {
  return divergencias.value.get(`${prodId}::${accId}`) ?? null
}

// Os motivos de cada célula da grade (produto::conta), já juntos para o
// balão. Calculado uma vez para a grade inteira, como `divergencias`; vale
// também para célula sem preço (conta sem integração: cartão em todas). A
// regra (motivosDoCartao) é a de lib/cartaoVermelho.ts, a mesma das
// Pendências do Faturamento (M-22).
const cartoes = computed(() => {
  if (!gradePronta.value || !grid.value) return new Map<string, string>()
  return cartoesDaGrade({
    contas: grid.value.accounts,
    produtos: grid.value.products,
    contaParametros: (acc) => contaParametros(acc as Account),
    celulaDe: (chave) => cellMap.value.get(chave),
    conferidaDe: (chave) => conferenciaPorCelula.value.get(chave),
    divergenciaDe: (chave) => divergencias.value.get(chave),
  })
})

function cartaoDe(prodId: string, accId: string): string | null {
  return cartoes.value.get(`${prodId}::${accId}`) ?? null
}

const fmtReal = (v: number) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
const fmtPct = (v: number) => v.toLocaleString('pt-BR', { maximumFractionDigits: 2 })
// Um formatador só: a data entra no balão de milhares de células a cada
// recálculo (toLocaleString com opções é ~20× mais lento).
const FMT_QUANDO = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
const fmtQuando = (iso: string | null | undefined) => (iso ? FMT_QUANDO.format(new Date(iso)) : '—')

function tituloDivergencia(d: Divergencia): string {
  const linhas = d.fora.map((a) => {
    const dif = ((a.preco! - d.tabela) / d.tabela) * 100
    const sinal = dif > 0 ? '+' : ''
    const promo = a.preco_promocional != null ? ` · em promoção por ${fmtReal(a.preco_promocional)}` : ''
    const pausado = a.ativo === false ? ' · anúncio pausado' : ''
    const obs = a.motivo && a.motivo !== 'em promoção' ? ` · ${a.motivo}` : ''
    const variacao = a.variacao ? ` (variação ${a.variacao})` : ''
    return `Anúncio ${a.anuncio}${variacao}: ${fmtReal(a.preco!)}, ${sinal}${dif.toFixed(1).replace('.', ',')}% da Tabela${promo}${pausado}${obs}`
  })
  return [
    `Fora da margem de ${fmtPct(d.margem)}%: Tabela ${fmtReal(d.tabela)}`,
    ...linhas,
    ...(d.naoLidos ? [`${d.naoLidos} de ${d.anuncios} anúncios desta célula sem preço lido`] : []),
    `Conferido em ${fmtQuando(d.conferidoEm)}`,
  ].join('\n')
}

// ---------- Botão "Margem N% ▾" (M-2, 17/11/2026; só administrador)
// Menu igual ao do Layout: [x] Geral [n] % e [x] Por produto (busca + um %
// por produto). Cada mudança grava na hora no servidor; recusa → aviso e
// volta ao que está salvo. Campo de % por produto vazio = sem % próprio
// (usa a Geral).
const rotuloMargem = computed(() => {
  const m = margem.value
  if (!m) return 'Margem …'
  return m.geral_ligada ? `Margem ${fmtPct(m.margem_pct)}%` : 'Margem —'
})

const tituloMargem = computed(() => {
  const m = margem.value
  const base = 'Margem de erro da conferência: a diferença aceita entre o preço do anúncio e o da Tabela, '
    + 'para o (!) e o cartão vermelho.'
  if (!m) return `${base}\n${conferenciaEstado.value === 'erro' ? 'Não carregou — recarregue a página.' : 'carregando…'}`
  const proprios = Object.keys(m.produtos).length
  return [
    base,
    m.geral_ligada
      ? `Geral: ${fmtPct(m.margem_pct)}% (produtos sem % próprio).`
      : 'Geral desligada: produto sem % próprio não é conferido.',
    m.por_produto_ligada
      ? `Por produto: ${proprios} produto(s) com % próprio.`
      : `Por produto desligado: todos usam a Geral (${proprios} % próprio(s) guardado(s)).`,
  ].join('\n')
})

// "7,5" ou "7.5" → 7.5; vazio → null; outra coisa ou fora de 0..100 → undefined.
function lerPct(texto: string): number | null | undefined {
  const t = texto.trim().replace(',', '.')
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) && n >= 0 && n <= 100 ? n : undefined
}

function textoErroMargem(e: any): string {
  const status = e?.status ?? e?.response?.status
  if (status === 422) return 'Use um número de 0 a 100.'
  if (status === 403) return 'Só o administrador muda a margem.'
  if (status === 404) return 'Produto não encontrado — recarregue a página.'
  return 'Tente de novo; voltou ao que está salvo.'
}

async function salvarMargem(mudanca: { margem_pct?: number; geral_ligada?: boolean; por_produto_ligada?: boolean }) {
  const antes = margem.value
  if (!antes?.pode_editar || salvandoMargem.value) return
  const dep = antes.department
  // Já na tela (as caixas respondem na hora); volta ao salvo se o servidor recusar.
  margem.value = { ...antes, ...mudanca }
  salvandoMargem.value = true
  seqMargem++
  try {
    const r = await api<MargemConferencia>('/api/pricing/conferencia/margem', {
      method: 'PUT',
      body: { department: dep, ...mudanca },
    })
    if (margem.value?.department === dep) margem.value = margemDaResposta(r, dep)
  } catch (e: any) {
    toast.error('Não salvou a margem', textoErroMargem(e))
    if (margem.value?.department === dep) margem.value = antes
  } finally {
    salvandoMargem.value = false
  }
}

function salvarMargemGeral(ev: Event) {
  const campo = ev.target as HTMLInputElement
  const atual = margem.value
  if (!atual) return
  const valor = lerPct(campo.value)
  if (valor == null) {
    toast.error('Margem inválida', 'Use um número de 0 a 100.')
    campo.value = fmtPct(atual.margem_pct)
    return
  }
  if (valor === atual.margem_pct) return
  void salvarMargem({ margem_pct: valor })
}

async function salvarMargemProduto(prodId: string, ev: Event) {
  const campo = ev.target as HTMLInputElement
  const antes = margem.value
  if (!antes?.pode_editar || salvandoMargem.value) return
  const salvo = antes.produtos[prodId] ?? null
  const voltar = () => {
    campo.value = salvo == null ? '' : fmtPct(salvo)
  }
  const valor = lerPct(campo.value)
  if (valor === undefined) {
    toast.error('Margem inválida', 'Use um número de 0 a 100, ou deixe vazio para usar a Geral.')
    voltar()
    return
  }
  if (valor === salvo) return
  const dep = antes.department
  salvandoMargem.value = true
  seqMargem++
  try {
    const r = await api<MargemConferencia>(
      `/api/pricing/conferencia/margem/produto/${encodeURIComponent(prodId)}`,
      { method: 'PUT', body: { margem_pct: valor } },
    )
    if (margem.value?.department === dep) margem.value = margemDaResposta(r, dep)
  } catch (e: any) {
    toast.error('Não salvou a margem do produto', textoErroMargem(e))
    voltar()
  } finally {
    salvandoMargem.value = false
  }
}

// Lista do "Por produto" (M-2, 17/11/2026: um campo % para cada produto do
// departamento; a busca só filtra). Sem busca vêm TODOS os produtos da grade:
// primeiro os que já têm % próprio, por nome; depois os outros, por nome. Com
// texto, a busca (SKU ou nome) filtra as duas partes; a rolagem do menu dá
// conta da Mala, que tem muitas linhas.
const buscaMargemProduto = ref('')
const produtosDaMargem = computed(() => {
  const prods = gradePronta.value ? grid.value?.products ?? [] : []
  const proprios = margem.value?.produtos ?? {}
  const porNome = (a: PricingProduct, b: PricingProduct) =>
    (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' })
  const q = buscaMargemProduto.value.trim().toLowerCase()
  const achados = q
    ? prods.filter((p) => (p.sku || '').toLowerCase().includes(q) || (p.name || '').toLowerCase().includes(q))
    : prods
  return {
    comPropria: achados.filter((p) => proprios[p.id] != null).sort(porNome),
    semPropria: achados.filter((p) => proprios[p.id] == null).sort(porNome),
  }
})

// O menu "Margem ▾" grava os campos no change, que só dispara ao sair do
// campo ou com Enter. Esc ou clique fora fecham o Popover e tiram o campo do
// DOM ainda com o foco — e tirar um campo focado não dispara blur/change
// (Firefox e Safari seguem a especificação). Por isso, ao fechar, grava o
// campo que está com o foco (M-2, 17/11/2026). Se o change também disparar,
// o "valor igual ao salvo" / "já salvando" das funções evita gravar duas vezes.
function gravarCampoMargemComFoco() {
  if (typeof document === 'undefined') return
  const el = document.activeElement as HTMLInputElement | null
  if (!el?.closest?.('[data-menu-margem]')) return
  if (el.dataset.campo === 'margem-geral') salvarMargemGeral({ target: el } as unknown as Event)
  else if (el.dataset.margemProduto) void salvarMargemProduto(el.dataset.margemProduto, { target: el } as unknown as Event)
}

const cellMap = computed(() => {
  const m = new Map<string, GridCell>()
  for (const c of grid.value?.cells ?? []) {
    m.set(`${c.pricing_product_id}::${c.pricing_account_id}`, c)
  }
  return m
})

function cellOf(prodId: string, accId: string): GridCell | undefined {
  return cellMap.value.get(`${prodId}::${accId}`)
}

function cellLabel(c: GridCell | undefined): string {
  // SSH semantics: NA/SV are explicit user flags. Missing/uncomputable cells
  // show "—" so the user can tell apart "sem anúncio" from "sem preço".
  if (!c) return '—'
  if (c.cell_status === 'NA') return 'NA'
  if (c.cell_status === 'SV') return 'SV'
  if (c.source === 'disabled') return '∅'
  if (c.price == null) return '—'
  return Number(c.price).toFixed(0)
}

// SSH: "Ao editar, TODOS os preços da linha recalculam automaticamente no
// frontend (pois dependem do custo)". While the user is mid-edit on a
// cost_kit cell, every non-override cell in that product's row paints with
// the formula evaluated against the typed value.
function liveCellLabel(prod: any, acc: Account): string {
  const c = cellOf(prod.id, acc.id)
  // Explicit user states + override (price fixed) always win.
  if (c) {
    if (c.cell_status === 'NA') return 'NA'
    if (c.cell_status === 'SV') return 'SV'
    if (c.source === 'disabled') return '∅'
    if (c.source === 'override') return c.price != null ? Number(c.price).toFixed(0) : '—'
  }
  if (
    editing.value
    && editing.value.id === prod.id
    && (editing.value.field.startsWith('cost_kit') || editing.value.field === 'preco_catalogo')
  ) {
    const typed = parseDec(editValue.value)
    if (Number.isFinite(typed)) {
      const fakeProd = { ...prod, [editing.value.field]: typed }
      const p = computePrice(acc, fakeProd as any)
      if (p != null) return String(Math.round(p))
    }
  }
  return cellLabel(c)
}

// Cores da célula: saíram no M-3 (17/11/2026). O preço fica sempre em preto
// (sem fundo por status, por cor escolhida, por preço manual, por 'locked'
// nem por margem negativa — esta continua no aviso acima da grade); o que
// precisa de atenção vira o cartão vermelho (cartaoDe). Fica só o fundo da
// coluna (colunaBg). O disquete (preço manual), o ⊘ e os botões NA/SV também
// saíram: os estados já gravados continuam valendo no servidor.

async function pushCell(c: GridCell) {
  const bloq = catalogoBloqueado(c.pricing_product_id, c.pricing_account_id)
  if (bloq) {
    toast.warning('Envio bloqueado', textoBloqueio(bloq))
    return
  }
  pushing.value = true
  lastPushResults.value = []
  try {
    const r = await api<{ results: PushResult[] }>('/api/pricing/push', {
      method: 'POST',
      headers: { 'Idempotency-Key': `cell:${c.pricing_product_id}:${c.pricing_account_id}:${Date.now()}` },
      body: {
        items: [{ pricing_account_id: c.pricing_account_id, pricing_product_id: c.pricing_product_id }],
      },
    })
    lastPushResults.value = r.results
    const okItems = r.results.filter((x) => x.ok)
    const failItems = r.results.filter((x) => !x.ok)
    // all_skipped = nenhum anúncio recebeu (encerrado, moderação, anúncio de
    // outro canal no ML): aviso, não erro — igual ao "Enviar todas".
    const bloqItems = failItems.filter((x) => ehCodigoBloqueio(x.code) || x.code === 'all_skipped')
    if (failItems.length === 0) {
      const priceTxt = okItems[0]?.price ? ` — R$ ${Number(okItems[0].price).toFixed(0)}` : ''
      const anuncios = okItems.reduce(
        (n, x) => n + (x.payload?.links?.filter((l) => l.success).length || 1),
        0,
      )
      // 'partial' = parte dos anúncios recebeu, parte não: aviso, não sucesso.
      const parcial = okItems.filter((x) => x.code === 'partial')
      if (parcial.length) {
        toast.warning(`Preço enviado em parte${priceTxt}`, parcial.map((x) => x.detail || x.code).slice(0, 5))
      } else {
        toast.success(`Preço enviado${priceTxt}`, `${anuncios} anúncio(s) ok`)
      }
    } else if (okItems.length === 0 && bloqItems.length === failItems.length) {
      toast.warning('Envio bloqueado', bloqItems.map((f) => f.detail || f.code).slice(0, 5))
    } else if (okItems.length === 0) {
      toast.error(
        'Erro no push',
        failItems.map((f) => f.detail || f.code).slice(0, 5),
      )
    } else {
      toast.warning(
        `Push parcial: ${okItems.length} ok, ${failItems.length} erro(s)`,
        failItems.map((f) => f.detail || f.code).slice(0, 5),
      )
    }
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
    toast.error('Erro no push', gridErr.value || undefined)
  } finally {
    pushing.value = false
  }
}

type BatchJob = {
  id: string
  type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  result?: { summary?: { total: number; ok: number; failed: number; cached: number } }
  error?: string | null
}

// SSH-style cell-by-cell push: walk the items sequentially from the browser,
// one POST /api/pricing/push per cell, and update per-cell visual state in
// `pushStates` so the user sees each spinner → ✓/✗ as it happens. Callers
// must build `items` in the visual order they want (e.g. produto×conta in
// gridAccounts order) — pushItemsBatch groups by product only to compute
// the "N/total" label.
// `bloqueadas` = células de catálogo bloqueadas que o chamador já tirou da
// fila; entram na contagem final de puladas.
async function pushItemsBatch(
  items: { pricing_account_id: string; pricing_product_id: string }[],
  keyHint: string,
  bloqueadas = 0,
) {
  if (!items.length) {
    if (bloqueadas > 0) {
      toast.warning(
        'Nada para enviar',
        `${bloqueadas} célula(s) de catálogo bloqueada(s) — passe o mouse na célula para ver o motivo`,
      )
    } else {
      gridErr.value = 'no_eligible_cells'
    }
    return
  }
  pushing.value = true
  lastPushResults.value = []
  pushStates.value = new Map()
  bulkPushProgress.value = ''
  const baseKey = `${keyHint}:${Date.now()}`

  const productIds = [...new Set(items.map((it) => it.pricing_product_id))]
  const results: PushResult[] = []
  let sent = 0
  let errors = 0
  let noLinks = 0
  let skipped = 0
  let recusadas = 0
  const errorDetails: string[] = []
  // Motivo de cada célula em que NENHUM anúncio recebeu (all_skipped).
  const puladosDetalhe: string[] = []

  function setCell(ck: string, st: PushCellState) {
    pushStates.value = new Map(pushStates.value).set(ck, st)
  }

  try {
    for (let pIdx = 0; pIdx < productIds.length; pIdx++) {
      const prodId = productIds[pIdx]
      bulkPushProgress.value = `${pIdx + 1}/${productIds.length}`
      const prodItems = items.filter((it) => it.pricing_product_id === prodId)

      for (const item of prodItems) {
        const ck = pushCellKey(item.pricing_product_id, item.pricing_account_id)
        setCell(ck, 'pushing')

        try {
          const r = await api<{ results: PushResult[] }>('/api/pricing/push', {
            method: 'POST',
            headers: { 'Idempotency-Key': `${baseKey}:${ck}` },
            body: { items: [item] },
          })
          const result = r.results[0]
          if (!result) {
            setCell(ck, 'error')
            errors++
            continue
          }
          results.push(result)
          lastPushResults.value = results.slice()

          if (result.ok) {
            setCell(ck, 'success')
            sent++
          } else if (result.code === 'no_link' || result.code === 'account_not_linked') {
            setCell(ck, 'no_link')
            noLinks++
          } else if (result.code === 'all_skipped') {
            // Nenhum anúncio recebeu (o ML vivo disse pausado, sincronizado,
            // outro canal/tipo, encerrado…): cinza, não ✓ — o preço não foi.
            setCell(ck, 'no_link')
            skipped++
            puladosDetalhe.push(result.detail || 'anúncio pulado')
          } else if (ehCodigoBloqueio(result.code)) {
            // servidor recusou: célula de catálogo bloqueada / anúncio de outro canal
            setCell(ck, 'no_link')
            recusadas++
          } else {
            setCell(ck, 'error')
            errors++
            errorDetails.push(`${result.code}: ${result.detail || ''}`)
          }
        } catch (e: any) {
          setCell(ck, 'error')
          errors++
          errorDetails.push(e?.message || 'request_failed')
        }
      }
    }

    const totalBloq = bloqueadas + recusadas
    const txtBloq = totalBloq > 0 ? `${totalBloq} bloqueada(s) de catálogo pulada(s)` : ''
    const txtPulados = skipped > 0 ? `${skipped} célula(s) não enviada(s) — anúncio pulado:` : ''
    if (errors > 0) {
      toast.error(
        `Envio: ${sent} ok, ${errors} erro(s)${noLinks > 0 ? `, ${noLinks} sem vínculo` : ''}${totalBloq > 0 ? `, ${totalBloq} bloqueada(s)` : ''}${skipped > 0 ? `, ${skipped} pulada(s)` : ''}`,
        [...errorDetails.slice(0, 5), ...puladosDetalhe.slice(0, 5)],
      )
    } else if (sent === 0 && (noLinks > 0 || totalBloq > 0 || skipped > 0)) {
      toast.warning(
        'Nenhum preço enviado',
        [
          noLinks > 0 ? `${noLinks} célula(s) sem vínculo` : '',
          txtBloq,
          txtPulados,
          ...puladosDetalhe.slice(0, 5),
        ].filter(Boolean),
      )
    } else if (skipped > 0) {
      toast.warning(
        `Envio: ${sent} preço(s) enviado(s), ${skipped} não enviado(s)`,
        [txtBloq, txtPulados, ...puladosDetalhe.slice(0, 5)].filter(Boolean),
      )
    } else {
      toast.success(
        'Push concluído!',
        [`${sent} preço(s) enviado(s) com sucesso`, txtBloq].filter(Boolean),
      )
    }

    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
    toast.error('Erro no push', gridErr.value || undefined)
  } finally {
    pushing.value = false
    bulkPushProgress.value = ''
    // Let success indicators fade after 5s; keep error/no_link so the user
    // can scan the grid for cells that still need attention.
    setTimeout(() => {
      pushStates.value = new Map(
        [...pushStates.value.entries()].filter(([, v]) => v !== 'success'),
      )
    }, 5000)
  }
}

// "Atualizar anúncio" do Atualizar ▾ (M-14, 18/11/2026; antes "Enviar todas
// as contas" e "Enviar" de uma conta): manda o preço de cada linha da tela
// (com a busca) às colunas escolhidas. Pergunta antes, como o Enviar de
// sempre. false = a pessoa desistiu (aí o resto do Atualizar não roda).
async function enviarPrecoDasContas(contaIds: string[]): Promise<boolean> {
  if (!grid.value) return false
  const escolhidas = new Set(contaIds)
  const items: { pricing_account_id: string; pricing_product_id: string }[] = []
  // gridAccounts.value carries the visual left-to-right order; iterating
  // products × gridAccounts means the queue walks the table the same way
  // the user reads it, so the per-cell spinners march in a predictable path.
  for (const p of filteredGridProducts.value) {
    for (const a of gridAccounts.value) {
      if (!escolhidas.has(a.id)) continue
      const c = cellOf(p.id, a.id)
      if (c && c.price != null && c.source !== 'locked' && c.source !== 'disabled') {
        items.push({ pricing_account_id: a.id, pricing_product_id: p.id })
      }
    }
  }
  const fila = separarBloqueadas(items)
  const aviso = fila.bloqueadas > 0 ? ` (${fila.bloqueadas} bloqueada(s) de catálogo serão puladas)` : ''
  const contas = escolhidas.size === 1 ? '1 conta' : `${escolhidas.size} contas`
  if (!confirm(`Enviar o preço de ${fila.items.length} célula(s) para ${contas}?${aviso}`)) return false
  await pushItemsBatch(fila.items, 'atualizar', fila.bloqueadas)
  return true
}

// =========================================================== grid: conta de catálogo

// Conta de kit de onde a coluna de catálogo tira comissão, margens, fretes e
// anotações. Procura no /grid (inclui contas fora do filtro de marketplace) e
// depois na lista da aba Contas.
const contasPorId = computed(() => {
  const m = new Map<string, Account>()
  for (const a of accounts.value) m.set(a.id, a)
  for (const a of grid.value?.accounts ?? []) m.set(a.id, a)
  return m
})

function contaBase(acc: Account): Account | null {
  if (!ehCatalogo(acc) || !acc.conta_base_id) return null
  return contasPorId.value.get(acc.conta_base_id) ?? null
}

// Conta cujos números valem para a coluna: a base, na coluna de catálogo.
function contaParametros(acc: Account): Account {
  return contaBase(acc) ?? acc
}

// Margem própria do catálogo (07/10/2026): na coluna de catálogo, a margem do
// tipo é a dela quando preenchida (aba Contas, botão “% margens” do Catálogo ML); senão a da
// conta de kit. Comissão e frete são sempre os da conta de kit.
function margemPropria(acc: Account, t: number): number | null {
  if (!ehCatalogo(acc)) return null
  const v = acc.margens_catalogo?.[String(t)]
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// "Acessórios 40.0% · Regular 25.0%" — os tipos com margem própria.
function resumoMargemPropria(acc: Account): string {
  const nomes = TYPE_HEADERS.value[acc.department as DeptKey] ?? []
  const partes: string[] = []
  for (let t = 1; t <= 5; t++) {
    const m = margemPropria(acc, t)
    if (m != null) partes.push(`${nomes[t - 1] ?? `tipo ${t}`} ${fmtMargin(m)}`)
  }
  return partes.join(' · ')
}

// Cabeçalho da coluna de catálogo = nome da conta de kit (+ selo "catálogo").
function nomeColuna(acc: Account): string {
  return ehCatalogo(acc) ? contaParametros(acc).name : acc.name
}

function tipoDe(acc: Account): string {
  return acc.listing_type || contaParametros(acc).listing_type || ''
}

// "clássico" / "premium" (ou vazio) — para cabeçalhos que não têm a linha do tipo.
function rotuloTipo(acc: Account): string {
  const t = tipoDe(acc).toLowerCase()
  if (t.includes('classico') || t.includes('clássico')) return 'clássico'
  if (t.includes('premium')) return 'premium'
  return ''
}

// "aguiar clássico" — onde só o nome deixaria o clássico igual ao premium.
function nomeComTipo(acc: Account): string {
  return [nomeColuna(acc), rotuloTipo(acc)].filter(Boolean).join(' ')
}

// Custo base da coluna: Kit N na conta de kit; na coluna de catálogo, o
// preco_catalogo do produto (vazio/0 = sem preço — nunca cai para o Kit 1).
function custoDaConta(prod: PricingProduct, acc: Account): number | null {
  if (ehCatalogo(acc)) {
    const v = prod.preco_catalogo
    if (v == null || v === '') return null
    const n = Number(v)
    return Number.isFinite(n) && n > 0 ? n : null
  }
  return getKitCost(prod, acc.kit_number)
}

// Frase de reserva caso o /grid não mande o `texto` do bloqueio.
const MOTIVOS_BLOQUEIO: Record<string, string> = {
  sem_anuncio: 'Sem anúncio de catálogo vinculado nesta conta',
  mesmo_anuncio: 'Outra linha da tabela manda preço para o mesmo anúncio de catálogo: deixe o preço de catálogo só na linha certa',
  sincronizado: 'Anúncio de catálogo preso ao anúncio comum: o preço dele vem da coluna de kit',
  pausado: 'Anúncio de catálogo pausado',
  em_revisao: 'Anúncio de catálogo em revisão no Mercado Livre',
  encerrado: 'Anúncio de catálogo encerrado',
  sem_tipo: 'Conta sem tipo (clássico/premium)',
  sem_preco_catalogo: 'Produto sem preço de catálogo (coluna Catálogo em Produtos)',
  so_catalogo: 'Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo',
}

// MOTIVO_ANUNCIO (motivo curto por anúncio): lib/cartaoVermelho.ts.

function catalogoBloqueado(prodId: string, accId: string): CatalogoCelula | null {
  const cat = cellOf(prodId, accId)?.catalogo
  return cat && cat.bloqueio ? cat : null
}

function textoBloqueio(cat: CatalogoCelula): string {
  return cat.texto || MOTIVOS_BLOQUEIO[cat.bloqueio ?? ''] || 'Envio bloqueado'
}

// Hover da célula de catálogo (e da de Kit 'só catálogo'): motivo do
// bloqueio + anúncios que ela atinge. Célula de Kit comum não tem `catalogo`.
function tituloCelulaCatalogo(prodId: string, acc: Account): string | undefined {
  const cat = cellOf(prodId, acc.id)?.catalogo
  if (!cat) return undefined
  const linhas: string[] = []
  if (cat.bloqueio) linhas.push(`Envio bloqueado: ${textoBloqueio(cat)}`)
  else if (cat.texto) linhas.push(cat.texto)
  for (const an of cat.anuncios ?? []) {
    const extras = [an.status, an.listing_type].filter(Boolean).join(', ')
    const preso = an.sincronizado_com?.length ? ` · preso a ${an.sincronizado_com.join(', ')}` : ''
    const fora = an.bloqueio ? ` — não envia (${MOTIVO_ANUNCIO[an.bloqueio] ?? an.bloqueio})` : ''
    linhas.push(`Catálogo ${an.external_id}${extras ? ` (${extras})` : ''}${preso}${fora}`)
  }
  return linhas.length ? linhas.join('\n') : undefined
}

// Respostas do /push que querem dizer "célula bloqueada / anúncio de outro
// canal", e não falha: contam como puladas, não como erro.
const CODIGOS_BLOQUEIO = new Set([
  'sem_anuncio', 'mesmo_anuncio', 'sincronizado', 'pausado', 'em_revisao', 'encerrado',
  'sem_tipo', 'sem_preco_catalogo', 'canal_errado', 'tipo_errado', 'bloqueado', 'so_catalogo',
])
function ehCodigoBloqueio(code: string): boolean {
  return CODIGOS_BLOQUEIO.has(code) || code.startsWith('catalogo_') || code.startsWith('bloqueado_')
}

// Separa as células que podem ir das bloqueadas (catálogo), mantendo a ordem.
function separarBloqueadas(
  items: { pricing_account_id: string; pricing_product_id: string }[],
): { items: { pricing_account_id: string; pricing_product_id: string }[]; bloqueadas: number } {
  const livres = items.filter((it) => !catalogoBloqueado(it.pricing_product_id, it.pricing_account_id))
  return { items: livres, bloqueadas: items.length - livres.length }
}

// =========================================================== grid extras (features)

// Feature 1: account groups by platform + kit (SSH order)
// Platform order chosen to match the SSH UI (Shein sits next to Shopee):
//   amazon → magalu → mercadolivre → shopee → shein → temu → aliexpress → tiktok
// Carrefour e Netshoes ficam no fim, preservando a posição das contas existentes.
const PLATFORM_ORDER: Record<string, number> = {
  amazon: 0, magalu: 1, mercadolivre: 2, shopee: 3,
  shein: 4, temu: 5, aliexpress: 6, tiktok: 7,
  carrefour: 8, netshoes: 9,
}

// Same SSH order is reused by the body iteration so each data column lines
// up with its grouped header — see `gridAccounts` below.
//
// Filtros Plataforma ▾ e Contas ▾ (M-8): drop the unchecked accounts.
// The downstream consumers (accountGroups,
// pushItemsBatch's "send all visible" iteration, the cellOf lookups
// in keyboard nav) all read from this computed so they inherit the
// filter automatically.
// Desde 07/10/2026 o clássico e o premium do ML têm o MESMO nome ("aguiar") e
// o tipo fica só na coluna Tipo ML: com o nome igual, clássico vem primeiro.
function ordemTipo(acc: Account): number {
  const t = (acc.listing_type || '').toLowerCase()
  if (t.includes('classico') || t.includes('clássico')) return 0
  if (t.includes('premium')) return 1
  return 2
}

function compararContasKit(a: Account, b: Account): number {
  if (a.kit_number !== b.kit_number) return (a.kit_number || 0) - (b.kit_number || 0)
  const n = (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' })
  return n !== 0 ? n : ordemTipo(a) - ordemTipo(b)
}

// Ordem visual sem filtro (também a do Excel). As colunas de catálogo vêm
// DEPOIS de todos os grupos "ML kit N" da plataforma, num grupo só ("ML
// Catálogo"), na mesma ordem das contas de kit de onde saíram.
const gridAccountsOrdenadas = computed<Account[]>(() => {
  return (grid.value?.accounts ?? []).slice().sort((a, b) => {
    const pa = PLATFORM_ORDER[a.platform] ?? 99
    const pb = PLATFORM_ORDER[b.platform] ?? 99
    if (pa !== pb) return pa - pb
    const ca = ehCatalogo(a) ? 1 : 0
    const cb = ehCatalogo(b) ? 1 : 0
    if (ca !== cb) return ca - cb
    if (ca) return compararContasKit(contaParametros(a), contaParametros(b))
    return compararContasKit(a, b)
  })
})

// Filtro "Plataforma ▾" (M-8, 18/11/2026; antes um select): uma caixa por
// coluna de plataforma — o ML vem separado em "ML Kit" e "ML Catálogo".
function chavePlataforma(a: Account): string {
  if (ehCatalogo(a)) return a.platform === 'mercadolivre' ? 'ml_catalogo' : `${a.platform}_catalogo`
  if (a.platform === 'mercadolivre') return 'ml_kit'
  return a.platform
}

function rotuloPlataforma(a: Account): string {
  if (a.platform === 'mercadolivre') return ehCatalogo(a) ? 'ML Catálogo' : 'ML Kit'
  return ehCatalogo(a) ? `${platformLabel(a.platform)} Catálogo` : platformLabel(a.platform)
}

// Filtro "Contas ▾" (M-8): por nome de loja ("atlas" marca todas as colunas
// da atlas, em qualquer plataforma). Maiúscula/minúscula não separa.
function chaveConta(a: Account): string {
  return (nomeColuna(a) || '').trim().toLocaleLowerCase('pt-BR')
}

// Filtro "Equipes ▾" (M-26, 23/11/2026): a equipe da coluna. O /grid não traz
// as equipes (é lido a toda hora, 03/11/2026): vêm da lista da aba Contas
// (/accounts, lida ao abrir a página), pela id. A coluna de catálogo já vem
// lá com as equipes da conta de kit.
const equipesPorConta = computed(() => {
  const m = new Map<string, EquipeDaConta[]>()
  for (const a of accounts.value) m.set(a.id, a.equipes ?? [])
  return m
})

// Os números das equipes da coluna ('101'...); vazio = conta sem equipe.
function chavesEquipeDaColuna(a: Account): string[] {
  const eq = a.equipes?.length ? a.equipes : (equipesPorConta.value.get(a.id) ?? [])
  return eq.map((e) => String(e.numero))
}

// Opções do Equipes ▾: sempre 1, 2 e 3, nessa ordem, mesmo sem conta da
// equipe no departamento (101/102/103 = Equipe 1/2/3, como na aba Contas).
// Não é permissão: quem é de equipe só recebe as contas dela, então a lista
// mostra só as equipes que aparecem nas contas que a pessoa vê.
const EQUIPES_DO_FILTRO = [
  { value: '101', label: '1' },
  { value: '102', label: '2' },
  { value: '103', label: '3' },
]
const gridEquipeOptions = computed<{ value: string; label: string }[]>(() => {
  if (isAdmin.value) return EQUIPES_DO_FILTRO
  const vistas = new Set<string>()
  for (const a of accounts.value) for (const e of a.equipes ?? []) vistas.add(String(e.numero))
  const minhas = EQUIPES_DO_FILTRO.filter((o) => vistas.has(o.value))
  return minhas.length ? minhas : EQUIPES_DO_FILTRO
})

// "Equipes (1)": com filtro parcial, o botão mostra quantas estão marcadas.
const rotuloFiltroEquipes = computed(() => {
  const total = gridEquipeOptions.value.length
  const marcadas = gridEquipeOptions.value.filter((o) => !equipesDesmarcadas.value.has(o.value)).length
  return marcadas < total ? `Equipes (${marcadas})` : 'Equipes'
})

function marcarEquipes(marcar: boolean) {
  equipesDesmarcadas.value = marcar ? new Set() : new Set(gridEquipeOptions.value.map((o) => o.value))
}

// A coluna só aparece com a plataforma, a conta E a equipe marcadas. Equipes
// com tudo marcado não filtra (aparece até conta sem equipe); com alguma
// desmarcada, a coluna precisa ser de uma equipe marcada (conta de duas
// equipes aparece se qualquer uma estiver marcada; sem equipe some).
const gridAccounts = computed<Account[]>(() => {
  const plats = plataformasDesmarcadas.value
  const contas = contasDesmarcadas.value
  const equipes = equipesDesmarcadas.value
  if (!plats.size && !contas.size && !equipes.size) return gridAccountsOrdenadas.value
  const equipesMarcadas = new Set(
    gridEquipeOptions.value.filter((o) => !equipes.has(o.value)).map((o) => o.value),
  )
  return gridAccountsOrdenadas.value.filter(
    (a) => !plats.has(chavePlataforma(a)) && !contas.has(chaveConta(a))
      && (!equipes.size || chavesEquipeDaColuna(a).some((k) => equipesMarcadas.has(k))),
  )
})

// Opções do Plataforma ▾: só as colunas que existem no departamento, na
// ordem da tabela (Amazon, Magalu, ML Kit, ML Catálogo, Shopee...). Com ML e
// sem nenhum catálogo ligado, "ML Catálogo" aparece apagado, com o caminho.
const gridPlatformOptions = computed<{ value: string; label: string; disabled?: boolean }[]>(() => {
  const out: { value: string; label: string; disabled?: boolean }[] = []
  const vistas = new Set<string>()
  const contas = gridAccountsOrdenadas.value
  const temCatalogoMl = contas.some((a) => chavePlataforma(a) === 'ml_catalogo')
  for (const a of contas) {
    const chave = chavePlataforma(a)
    if (vistas.has(chave)) continue
    vistas.add(chave)
    out.push({ value: chave, label: rotuloPlataforma(a) })
    if (chave === 'ml_kit' && !temCatalogoMl) {
      out.push({ value: 'ml_catalogo', label: 'ML Catálogo (ligue na aba Contas)', disabled: true })
    }
  }
  return out
})

// Opções do Contas ▾: nomes de loja sem repetir, em ordem alfabética.
const gridContaOptions = computed<{ value: string; label: string }[]>(() => {
  const porChave = new Map<string, string>()
  for (const a of gridAccountsOrdenadas.value) {
    const chave = chaveConta(a)
    if (!porChave.has(chave)) porChave.set(chave, (nomeColuna(a) || '').trim())
  }
  return Array.from(porChave, ([value, label]) => ({ value, label })).sort((x, y) =>
    x.label.localeCompare(y.label, 'pt-BR', { sensitivity: 'base' }),
  )
})

// Lista do Contas ▾ depois da busca do próprio menu.
const gridContaOptionsBuscadas = computed(() => {
  const q = buscaContas.value.trim().toLocaleLowerCase('pt-BR')
  return q ? gridContaOptions.value.filter((o) => o.value.includes(q)) : gridContaOptions.value
})

// "Contas (2)": com filtro parcial, o botão mostra quantas estão marcadas.
const rotuloContas = computed(() => {
  const total = gridContaOptions.value.length
  const marcadas = gridContaOptions.value.filter((o) => !contasDesmarcadas.value.has(o.value)).length
  return marcadas < total ? `Contas (${marcadas})` : 'Contas'
})

// Uma caixa marcada/desmarcada (no template a ref chega desembrulhada, por
// isso o filtro vai pelo nome).
function alternarFiltro(qual: 'plataforma' | 'conta' | 'equipe', chave: string, marcar: boolean) {
  const alvo = qual === 'plataforma' ? plataformasDesmarcadas : qual === 'equipe' ? equipesDesmarcadas : contasDesmarcadas
  const s = new Set(alvo.value)
  if (marcar) s.delete(chave)
  else s.add(chave)
  alvo.value = s
}

// "Marcar todas" / "Limpar" do Contas ▾ valem para a lista que está à vista
// (com busca, só as encontradas).
function marcarContas(marcar: boolean) {
  const s = new Set(contasDesmarcadas.value)
  for (const o of gridContaOptionsBuscadas.value) {
    if (marcar) s.delete(o.value)
    else s.add(o.value)
  }
  contasDesmarcadas.value = s
}

function marcarPlataformas(marcar: boolean) {
  plataformasDesmarcadas.value = marcar
    ? new Set()
    : new Set(gridPlatformOptions.value.filter((o) => !o.disabled).map((o) => o.value))
}

const accountGroups = computed<
  { label: string; platform: string; catalogo: boolean; accounts: Account[] }[]
>(() => {
  const accs = gridAccounts.value
  const groups: { label: string; platform: string; catalogo: boolean; accounts: Account[] }[] = []
  let currentKey = ''
  for (const acc of accs) {
    const catalogo = ehCatalogo(acc)
    // Quem não é ADM não recebe o kit (M-10): um grupo por plataforma, com o
    // nome do filtro Plataforma ▾ ("ML Kit", "Shopee"...).
    const semKit = acc.kit_number == null
    const key = catalogo
      ? `${acc.platform}-catalogo`
      : semKit ? chavePlataforma(acc) : `${acc.platform}-kit${acc.kit_number}`
    const label = catalogo
      ? `${platformLabel(acc.platform)} Catálogo`
      : semKit ? rotuloPlataforma(acc) : `${platformLabel(acc.platform)} kit ${acc.kit_number}`
    if (key !== currentKey) {
      groups.push({ label, platform: acc.platform, catalogo, accounts: [acc] })
      currentKey = key
    } else {
      groups[groups.length - 1].accounts.push(acc)
    }
  }
  return groups
})

const firstAccountIdInGroup = computed<Set<string>>(() => {
  const s = new Set<string>()
  for (const g of accountGroups.value) {
    if (g.accounts[0]) s.add(g.accounts[0].id)
  }
  return s
})

// Feature 7: grid search
const produtosDaBusca = computed(() => {
  const prods = grid.value?.products ?? []
  const q = gridSearch.value.trim().toLowerCase()
  return q ? prods.filter((p) => p.sku.toLowerCase().includes(q) || p.name.toLowerCase().includes(q)) : prods
})

// O filtro "só linhas com (!)" saiu com a faixa da conferência (M-2).
const filteredGridProducts = computed(() =>
  produtosDaBusca.value.slice().sort((a, b) =>
    (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' }),
  ),
)

// A seleção (anel azul, setas) é por posição: se a lista visível muda (busca,
// recarga, filtros Plataforma/Contas), a posição apontaria para outro
// produto ou outra coluna — então a seleção sai.
watch(
  () => `${filteredGridProducts.value.map((p) => p.id).join(',')}|${gridAccounts.value.map((a) => a.id).join(',')}`,
  () => {
    selectedCell.value = null
  },
)

// Feature 9: negative margin helpers + counter
function getKitCost(prod: PricingProduct, kitNumber: number | null): number {
  const key = `cost_kit${Math.max(1, Math.min(8, kitNumber || 1))}` as keyof PricingProduct
  const v = prod[key] ?? prod.cost_kit1
  return Number(v ?? 0)
}

function getMarginShipping(acc: Account, productType: number): { margin: number; shipping: number } | null {
  const t = Math.max(1, Math.min(5, productType || 1))
  const rawMargin = (acc as any)[`margin${t}`]
  if (rawMargin == null || rawMargin === '') return null
  const margin = Number(rawMargin)
  const shipping = Number((acc as any)[`shipping${t}`] ?? 0)
  if (!Number.isFinite(margin) || !Number.isFinite(shipping)) return null
  return { margin, shipping }
}

// Margem e frete da coluna no tipo do produto: na de catálogo, a margem
// própria do tipo (se houver) com o frete da conta de kit.
function margemFreteDaColuna(acc: Account, productType: number): { margin: number; shipping: number } | null {
  const params = contaParametros(acc)
  const t = Math.max(1, Math.min(5, productType || 1))
  const propria = margemPropria(acc, t)
  if (propria == null) return getMarginShipping(params, productType)
  const shipping = Number((params as any)[`shipping${t}`] ?? 0)
  return Number.isFinite(shipping) ? { margin: propria, shipping } : null
}

// Mirrors backend calc.py: (cost * (1 + margin) + shipping) / (1 - commission)
// Returns null if any required input is missing (margin/commission/cost).
// Coluna de catálogo: custo = preco_catalogo; comissão/frete da base; margem
// própria do tipo quando preenchida, senão a da base.
function computePrice(acc: Account, prod: PricingProduct): number | null {
  if (ehCatalogo(acc) && !contaBase(acc)) return null
  const cost = custoDaConta(prod, acc)
  if (cost == null || !Number.isFinite(cost) || cost <= 0) return null
  const pa = contaParametros(acc)
  if (pa.commission == null || pa.commission === '') return null
  const ms = margemFreteDaColuna(acc, (prod as any).product_type ?? 2)
  if (!ms) return null
  const commission = Number(pa.commission)
  if (!Number.isFinite(commission)) return null
  const denom = 1 - commission
  if (denom <= 0) return null
  // SSH rounds the computed price to integer reais (Math.round). Cents
  // only show up on manual overrides.
  const price = (cost * (1 + ms.margin) + ms.shipping) / denom
  return Math.round(price)
}

// Re-compute every non-overridden cell for `prodId` against the current
// account roster. Skips cells the user explicitly set (manual/locked/NA/SV/
// disabled status, or a real price_override producing source='override').
// Used after inline kit edits to reflect the new cost without a full grid
// reload. has_override alone is NOT a skip signal — overrides with
// cell_status='auto' and no price_override are just leftover rows from the
// SSH import and should still recompute.
function recomputeCellsForProduct(prodId: string) {
  // Sem comissão, margens, fretes e kit (quem não é ADM, M-10) não dá para
  // recalcular aqui: o preço novo vem do servidor (loadGrid depois de gravar).
  if (!grid.value || !isAdmin.value) return
  const prod = grid.value.products.find((x) => x.id === prodId)
  if (!prod) return
  for (const acc of grid.value.accounts) {
    const c = cellOf(prodId, acc.id)
    if (!c) continue
    if (c.cell_status === 'manual' || c.cell_status === 'locked'
        || c.cell_status === 'NA' || c.cell_status === 'SV'
        || c.source === 'disabled' || c.source === 'override') continue
    const newPrice = computePrice(acc, prod)
    c.price = newPrice == null ? null : (newPrice.toFixed(2) as any)
    c.source = newPrice == null ? 'missing_inputs' : 'computed'
  }
}

const negativeMarginCount = computed(() => {
  if (!grid.value) return 0
  let count = 0
  for (const prod of grid.value.products) {
    for (const acc of grid.value.accounts) {
      const c = cellOf(prod.id, acc.id)
      if (!c || c.price == null || c.source === 'disabled' || c.source === 'locked') continue
      if (c.cell_status === 'NA' || c.cell_status === 'SV') continue
      const price = Number(c.price)
      const cost = custoDaConta(prod, acc)
      const ms = getMarginShipping(contaParametros(acc), prod.product_type)
      const shipping = ms ? Number(ms.shipping) : 0
      if (cost != null && price < cost + shipping) count++
    }
  }
  return count
})

// Feature 13: platform color coding
function platformBg(platform: string): string {
  switch (platform) {
    case 'shopee': return 'bg-orange-50/50'
    case 'shein': return 'bg-teal-50/50'
    case 'amazon': return 'bg-yellow-50/50'
    case 'temu': return 'bg-purple-50/50'
    case 'aliexpress': return 'bg-red-50/50'
    case 'tiktok': return 'bg-pink-50/50'
    case 'mercadolivre': return 'bg-blue-50/50'
    default: return ''
  }
}
function platformHeaderBg(platform: string): string {
  switch (platform) {
    case 'amazon':       return 'bg-yellow-50 dark:bg-yellow-900/30'
    case 'magalu':       return 'bg-blue-50 dark:bg-blue-900/30'
    case 'mercadolivre': return 'bg-muted'
    case 'shopee':       return 'bg-orange-50 dark:bg-orange-900/30'
    case 'shein':        return 'bg-teal-50 dark:bg-teal-900/30'
    case 'temu':         return 'bg-purple-50 dark:bg-purple-900/30'
    case 'aliexpress':   return 'bg-red-50 dark:bg-red-900/30'
    case 'tiktok':       return 'bg-pink-50 dark:bg-pink-900/30'
    default:             return 'bg-muted/50'
  }
}

// Colunas de catálogo ganham um tom próprio para não se misturar com o kit.
function colunaBg(acc: Account): string {
  return ehCatalogo(acc) ? 'bg-indigo-50/50 dark:bg-indigo-900/10' : platformBg(acc.platform)
}
function colunaHeaderBg(acc: Account): string {
  return ehCatalogo(acc) ? 'bg-indigo-50 dark:bg-indigo-900/30' : platformHeaderBg(acc.platform)
}
function grupoHeaderBg(group: { platform: string; catalogo: boolean }): string {
  return group.catalogo ? 'bg-indigo-100 text-indigo-900 dark:bg-indigo-900/40 dark:text-indigo-100' : platformHeaderBg(group.platform)
}

// As 6 anotações da conta (desconto, afiliado, ads, cupom, oferta, obs) saíram
// do cabeçalho da grade no M-3 (17/11/2026): ficam só na aba Contas. O
// Desfazer/Refazer (M-2) só servia ao preço manual e saiu junto com ele.

// Feature 5: Excel/CSV export
function handleExportExcel() {
  if (!grid.value) return
  // Ordem da tela (catálogo depois dos kits), sem o filtro de marketplace.
  const accs = gridAccountsOrdenadas.value
  const prods = filteredGridProducts.value
  // Excel leva sempre os 8 kits e as colunas de estoque, mesmo com o Kit ou o
  // Estoque desmarcados no Layout.
  const headers = ['SKU', 'Produto', 'Estoque', '7d', '30d', 'Reposição', department.value === 'celular' ? 'Kit1' : 'Custo']
  if (department.value === 'celular') {
    for (let k = 2; k <= 8; k++) headers.push(`Kit${k}`)
  }
  // Coluna Catálogo depois do último kit (Kit8 no celular, Custo nos outros).
  headers.push('Catálogo')
  for (const acc of accs) {
    const nome = [nomeColuna(acc), rotuloTipo(acc)].filter(Boolean).join(' ')
    headers.push(ehCatalogo(acc) ? `${nome} catálogo` : nome)
  }
  const rows = prods.map(p => {
    const row: string[] = [
      p.sku,
      p.name,
      // Mapa que não carregou sai em branco (não como zero).
      estoqueEstado.value === 'ok' ? String(stockMap.value[p.id] ?? 0) : '',
      vendas7Estado.value === 'ok' ? String(salesMap7d.value[p.id] ?? 0) : '',
      vendasEstado.value === 'ok' ? String(salesMap30d.value[p.id] ?? 0) : '',
      reposicaoDe(p.id) != null ? String(reposicaoDe(p.id)) : '',
      String(Number(p.cost_kit1 || 0).toFixed(0)),
    ]
    if (department.value === 'celular') {
      for (let k = 2; k <= 8; k++) {
        row.push(String(Number((p as any)[`cost_kit${k}`] || 0).toFixed(0)))
      }
    }
    row.push(p.preco_catalogo != null && p.preco_catalogo !== '' ? Number(p.preco_catalogo).toFixed(0) : '')
    for (const acc of accs) {
      const c = cellOf(p.id, acc.id)
      if (c?.cell_status === 'NA') row.push('NA')
      else if (c?.cell_status === 'SV') row.push('SV')
      else row.push(c?.price != null ? String(Number(c.price).toFixed(0)) : '')
    }
    return row
  })
  const csv = [headers, ...rows].map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `tabela-precos-${department.value}-${isoToday()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// Excel da aba Contas (M-51, 25/11/2026): as contas que estão na tela (busca e
// filtros, na ordem da tabela) com as colunas à vista (Layout), na ordem da
// tabela: Plataforma | Conta | Catálogo ML | Equipe | Kit | Comissão |
// marg/frete | Afiliado | Desconto | Ads | Custo total | Margem mínima | Obs. Quem
// não é ADM não recebe Kit, Comissão, margens, fretes nem a Margem mínima (M-85;
// nem vêm do /accounts). A conta do ML
// com Clássico e Premium é uma linha só, como na tela (M-53), com a Comissão
// "14% · 19%"; Catálogo ML "Ativo"/"Inativo" (M-54). Afiliado, Desconto, Ads (o
// % Geral), Custo total e Margem mínima vão como número; vazio fica em branco,
// não "—".
function linhasExcelContas(): CelulaPlanilha[][] {
  const semTraco = (s: string) => (s === '—' ? '' : s)
  const numero = (v: unknown): number | '' => {
    if (v == null || v === '') return ''
    const n = Number(v)
    return Number.isFinite(n) ? n : ''
  }
  const tipos = TYPE_HEADERS.value[department.value] ?? []
  const notas = camposAnotacao.value.filter((f) => f.key !== 'observation' || mostraColunaConta('obs'))
  const cab: CelulaPlanilha[] = ['Plataforma', 'Conta']
  if (mostraColunaConta('catalogo')) cab.push('Catálogo ML')
  if (mostraColunaConta('equipe')) cab.push('Equipe')
  if (isAdmin.value) {
    if (mostraColunaConta('kit')) cab.push('Kit')
    if (mostraColunaConta('comissao')) cab.push('Comissão')
    for (const t of tipos) cab.push(`${t} marg`, `${t} frete`)
  }
  for (const f of notas) cab.push(f.tipo === 'pct' || f.key === 'ads' ? `${f.label} (%)` : f.label)
  const linhas: CelulaPlanilha[][] = [cab]
  for (const acc of accountsGrouped.value.flatMap((g) => g.rows)) {
    const plataforma = [platformLabel(acc.platform), tipoDaContaSolta(acc)].filter(Boolean).join(' ')
    const l: CelulaPlanilha[] = [plataforma, acc.name]
    if (mostraColunaConta('catalogo')) l.push(podeTerCatalogo(acc) ? (catalogoAtivo(acc) ? 'Ativo' : 'Inativo') : '')
    if (mostraColunaConta('equipe')) l.push(rotuloEquipes(acc))
    if (isAdmin.value) {
      if (mostraColunaConta('kit')) l.push(acc.kit_number ?? '')
      if (mostraColunaConta('comissao')) {
        const par = parDe(acc)
        l.push(par ? textoComissaoPar(par) : semTraco(fmtCommission(acc.commission)))
      }
      for (let t = 1; t <= tipos.length; t++) {
        l.push(
          semTraco(fmtMargin((acc as any)[`margin${t}`])),
          semTraco(fmtShipping((acc as any)[`shipping${t}`])),
        )
      }
    }
    for (const f of notas) {
      if (f.key === 'ads') l.push(ads.value ? numero(pctGeralAds(adsDa(acc.id))) : '')
      else if (f.tipo === 'pct') l.push(numero((acc as any)[f.key]))
      else l.push(String((acc as any)[f.key] ?? ''))
    }
    linhas.push(l)
  }
  return linhas
}

function exportarExcelContas() {
  if (!accountsFiltered.value.length) return
  const bytes = gerarXlsx(linhasExcelContas(), 'Contas')
  const blob = new Blob([bytes as BlobPart], { type: TIPO_XLSX })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `contas-${department.value}-${isoToday()}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

// Feature 8: o painel do Atualizar ▾ (M-14, 18/11/2026).
// Toda vez que abre: só "Atualizar planilha" marcado e Contas em Geral.
function abrirPainelAtualizar(aberto: boolean) {
  painelAtualizarAberto.value = aberto
  if (!aberto) return
  atualizarPlanilha.value = true
  atualizarConferir.value = false
  atualizarAnuncio.value = false
  atualizarModoContas.value = 'geral'
  atualizarContasMarcadas.value = new Set()
  buscaAtualizarContas.value = ''
}

// Revisão M-14: uma conferência começou com o painel aberto (de outra pessoa,
// pela consulta de 30 s): a caixa Conferir fica apagada, então sai marcada —
// senão travava o Atualizar também para a planilha e o anúncio.
watch(conferenciaRodando, (rodando) => {
  if (rodando) atualizarConferir.value = false
})

// Geral e Por conta não andam juntas: marcar uma desmarca a outra.
function escolherModoContas(modo: 'geral' | 'conta', marcar: boolean) {
  if (marcar) atualizarModoContas.value = modo
  else if (atualizarModoContas.value === modo) atualizarModoContas.value = null
}

// Marcar uma conta na lista já passa para "Por conta".
function alternarContaAtualizar(accId: string, marcar: boolean) {
  const s = new Set(atualizarContasMarcadas.value)
  if (marcar) s.add(accId)
  else s.delete(accId)
  atualizarContasMarcadas.value = s
  if (marcar) atualizarModoContas.value = 'conta'
}

// A lista "Por conta": as colunas visíveis, agrupadas como no cabeçalho
// (Shopee kit 1, Shopee kit 2...), com a busca pelo nome ou pelo grupo.
const gruposAtualizarContas = computed(() => {
  const q = buscaAtualizarContas.value.trim().toLocaleLowerCase('pt-BR')
  if (!q) return accountGroups.value
  return accountGroups.value
    .map((g) => ({
      ...g,
      accounts: g.accounts.filter((a) =>
        `${nomeColuna(a)} ${g.label}`.toLocaleLowerCase('pt-BR').includes(q)),
    }))
    .filter((g) => g.accounts.length > 0)
})

// As marcadas que continuam na tela (na ordem da tabela).
const contasMarcadasAtualizar = computed(() =>
  gridAccounts.value.filter((a) => atualizarContasMarcadas.value.has(a.id)).map((a) => a.id),
)

// As colunas que Conferir e Atualizar anúncio usam.
const contasDoAtualizar = computed<string[]>(() => {
  if (atualizarModoContas.value === 'geral') return gridAccounts.value.map((a) => a.id)
  if (atualizarModoContas.value === 'conta') return contasMarcadasAtualizar.value
  return []
})

// Por que o "Atualizar" do rodapé está apagado ('' = pode).
const motivoAtualizarBloqueado = computed(() => {
  const anuncio = atualizarAnuncio.value && podeEnviarPreco.value
  const conferir = atualizarConferir.value && podeConferir.value
  if (!atualizarPlanilha.value && !anuncio && !conferir) return 'Marque o que atualizar.'
  if ((anuncio || conferir) && !contasDoAtualizar.value.length) return 'Escolha as contas.'
  if (anuncio && !grid.value?.cells.length) return 'A planilha ainda não carregou: atualize só a planilha antes.'
  if (conferir && (conferenciaIniciando.value || conferenciaRodando.value)) return 'Já há uma conferência rodando.'
  return ''
})

// O texto do botão: "Atualizar" parado; a etapa e o andamento enquanto roda.
const rotuloAtualizar = computed(() => {
  if (atualizandoEtapa.value === 'anuncio' || (pushing.value && !atualizando.value)) {
    return bulkPushProgress.value ? `Enviando ${bulkPushProgress.value}…` : 'Enviando…'
  }
  if (atualizandoEtapa.value === 'conferir') return 'Iniciando conferência…'
  if (atualizandoEtapa.value === 'planilha') return 'Atualizando planilha…'
  const job = conferencia.value?.job
  if (conferenciaRodando.value && job) return `Conferindo ${job.processed}/${job.total || '…'}…`
  return 'Atualizar'
})

// Executa o que está marcado, nesta ordem: Atualizar anúncio, Conferir (lê o
// preço novo) e Atualizar planilha. Planilha recarrega a tabela inteira; as
// contas valem para Conferir e Atualizar anúncio. Geral sem filtro nenhum vai
// sem `contas`: o servidor confere todas as colunas que a pessoa vê (o
// departamento inteiro para o administrador, como o botão de antes).
// Revisão M-14: trocou de departamento durante o envio (que leva minutos) →
// a conferência e a planilha, que eram do outro, não rodam.
async function executarAtualizar() {
  if (atualizando.value || pushing.value || motivoAtualizarBloqueado.value) return
  const dep = department.value
  const anuncio = atualizarAnuncio.value && podeEnviarPreco.value
  const conferir = atualizarConferir.value && podeConferir.value
  const planilha = atualizarPlanilha.value
  const contas = contasDoAtualizar.value.slice()
  const semFiltro = !plataformasDesmarcadas.value.size && !contasDesmarcadas.value.size && !equipesDesmarcadas.value.size
  const contasDaConferencia = atualizarModoContas.value === 'geral' && semFiltro ? undefined : contas
  painelAtualizarAberto.value = false
  atualizando.value = true
  try {
    if (anuncio) {
      atualizandoEtapa.value = 'anuncio'
      if (!(await enviarPrecoDasContas(contas))) return
    }
    if (department.value !== dep) {
      if (conferir) toast.warning('A conferência não foi feita', 'O departamento mudou durante o envio — confira de novo.')
      return
    }
    if (conferir) {
      atualizandoEtapa.value = 'conferir'
      await conferirAnuncios(contasDaConferencia, dep)
    }
    if (planilha) {
      atualizandoEtapa.value = 'planilha'
      await loadGrid()
    }
  } finally {
    atualizando.value = false
    atualizandoEtapa.value = ''
  }
}

// Feature 11: keyboard navigation in grid
// Setas movem a seleção e Esc limpa. Ctrl/Cmd+Z/Y (desfazer o preço manual)
// e o Enter que abria o preço manual saíram no M-2/M-3 (17/11/2026). Tecla
// digitada num campo da grade (custo do kit) é do campo: não mexe a seleção.
function handleGridKeyDown(e: KeyboardEvent) {
  if (!selectedCell.value) return
  const alvo = e.target as HTMLElement | null
  if (alvo && (alvo.tagName === 'INPUT' || alvo.tagName === 'TEXTAREA' || alvo.tagName === 'SELECT')) return
  const { row, col } = selectedCell.value
  const maxRow = filteredGridProducts.value.length - 1
  const maxCol = gridAccounts.value.length - 1
  switch (e.key) {
    case 'ArrowUp': e.preventDefault(); if (row > 0) selectedCell.value = { row: row - 1, col }; break
    case 'ArrowDown': e.preventDefault(); if (row < maxRow) selectedCell.value = { row: row + 1, col }; break
    case 'ArrowLeft': e.preventDefault(); if (col > 0) selectedCell.value = { row, col: col - 1 }; break
    case 'ArrowRight': e.preventDefault(); if (col < maxCol) selectedCell.value = { row, col: col + 1 }; break
    case 'Escape': e.preventDefault(); selectedCell.value = null; break
  }
}

// Feature 12: compare mode (placeholder until /api/pricing/actual-prices exists)
async function toggleCompare(prod: PricingProduct) {
  if (compareProductIds.value.has(prod.id)) {
    compareProductIds.value.delete(prod.id)
    compareProductIds.value = new Set(compareProductIds.value)
    return
  }
  competitorLoadingId.value = prod.id
  compareProductIds.value.add(prod.id)
  compareProductIds.value = new Set(compareProductIds.value)
  try {
    const results = await api<Record<string, number | null>>(
      `/api/pricing/actual-prices/${prod.id}?department=${department.value}`,
    )
    actualPriceMap.value[prod.id] = results
  } catch {
    compareToast.value = 'Comparação real ainda não disponível neste backend.'
    setTimeout(() => { compareToast.value = null }, 2500)
  } finally {
    competitorLoadingId.value = null
  }
}

// =========================================================== display helpers

function fmtCommission(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(0)}%`
}

function fmtMargin(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtShipping(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toFixed(0)
}

function fmtMoney(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toFixed(2)
}

function fmtBRL(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `R$ ${n.toFixed(0)}`
}

function tabelaName(p: PricingProduct): string {
  const headers = TYPE_HEADERS.value
  const list = headers[(p.department as DeptKey)] ?? headers.celular
  const t = Math.max(1, Math.min(5, p.product_type || 1))
  return list[t - 1] ?? '—'
}

function tabelaBadgeClass(p: PricingProduct): string {
  const t = Math.max(1, Math.min(5, p.product_type || 1))
  switch (t) {
    case 1: return 'bg-slate-100 text-slate-700'
    case 2: return 'bg-blue-100 text-blue-700'
    case 3: return 'bg-emerald-100 text-emerald-700'
    case 4: return 'bg-orange-100 text-orange-700'
    case 5: return 'bg-purple-100 text-purple-700'
    default: return 'bg-gray-100 text-gray-700'
  }
}

// =========================================================== boot

// Quem só tem Faturamento não carrega nada da Tabela de Preços (a API
// recusaria). No Faturamento, só os departamentos e as contas, que os botões
// "Celular (N contas)" mostram; produtos e integrações são das sub-abas.
if (podePrecos.value) {
  await loadSegments()
  await loadAccounts()
  if (tab.value !== 'faturamento') {
    await loadProducts()
    await loadIntegrations()
  }
}

// Status do MEGA pra pílula do topo: uma vez por visita à aba Produtos e só
// depois de montar — no servidor a chamada ao sidecar só atrasaria a página,
// e mudar o estado antes da hidratação faria a pílula piscar.
let paginaMontada = false
function talvezCarregarMegaStatus() {
  if (!paginaMontada || tab.value !== 'produtos' || !canEditProdutos.value) return
  if (megaStatus.value != null) return
  void carregarMegaStatus()
}
// Preferências deste navegador (02/11/2026): as caixas Kit e Estoque do menu
// Layout (M-2, 17/11/2026; a margem da conferência saiu daqui e foi para o
// servidor). Lidas só depois de montar (no servidor não há localStorage).
function lerPreferencia(chave: string): string | null {
  try {
    return localStorage.getItem(chave)
  } catch {
    return null
  }
}
function gravarPreferencia(chave: string, valor: string) {
  try {
    localStorage.setItem(chave, valor)
  } catch {
    /* navegador sem armazenamento: vale só nesta visita */
  }
}

onMounted(() => {
  paginaMontada = true
  talvezCarregarMegaStatus()
  ocultarKits.value = lerPreferencia(CHAVE_OCULTAR_KITS) === '1'
  ocultarEstoque.value = lerPreferencia(CHAVE_OCULTAR_ESTOQUE) === '1'
  watch(ocultarKits, (v) => gravarPreferencia(CHAVE_OCULTAR_KITS, v ? '1' : '0'))
  watch(ocultarEstoque, (v) => gravarPreferencia(CHAVE_OCULTAR_ESTOQUE, v ? '1' : '0'))
  // Layout ▾ da aba Contas (M-51): as colunas escondidas, "kit,obs".
  colunasOcultasContas.value = lerColunasOcultasContas(lerPreferencia(CHAVE_COLUNAS_OCULTAS_CONTAS))
  watch(colunasOcultasContas, (v) => gravarPreferencia(CHAVE_COLUNAS_OCULTAS_CONTAS, v.join(',')))
})

// Prazo e conferência (com a margem): uma vez por visita à aba e por troca de
// departamento (o loadGrid roda depois de cada edição e não precisa reler
// isto). A conferência é de todos desde o M-3: o cartão vermelho e o (!).
function carregarExtrasDaTabela() {
  void carregarPrazos()
  conferencia.value = null
  conferenciaEstado.value = 'carregando'
  releitura.value = 'ok'
  tentativasReleitura = 0
  // M-15: os erros e os resultados das células conferidas sozinhas eram do
  // departamento de antes.
  errosDaCelula.value = {}
  celulasRecentes.clear()
  void carregarConferencia()
}

watch(
  tab,
  async (t) => {
    if (t === 'tabela') {
      carregarExtrasDaTabela()
      await loadGrid()
    } else if (timerConferencia) {
      // M-7: fora da aba Tabela a consulta de 30 s para (volta ao voltar).
      clearTimeout(timerConferencia)
      timerConferencia = null
    }
    if (t === 'produtos') {
      loadAudit()
      talvezCarregarMegaStatus()
    }
    // Ads da aba Contas (M-12): uma leitura por visita à aba e por departamento.
    if (t === 'contas') void carregarAds()
  },
  { immediate: true },
)

watch(department, async () => {
  // Switching department invalidates the Plataforma/Contas filters — the
  // new dept may not have the same columns, and stale filters silently
  // hide columns until the operator notices. Volta tudo marcado.
  plataformasDesmarcadas.value = new Set()
  contasDesmarcadas.value = new Set()
  equipesDesmarcadas.value = new Set()
  buscaContas.value = ''
  buscaMargemProduto.value = ''
  // Até os mapas, o prazo, a conferência e a margem do departamento novo
  // chegarem, nada do anterior é usado (a Reposição mostra "…", não números
  // trocados; nenhum (!) com a margem de outro departamento).
  estoqueEstado.value = 'carregando'
  vendas7Estado.value = 'carregando'
  vendasEstado.value = 'carregando'
  prazos.value = null
  margem.value = null
  buscaAdsProduto.value = ''
  zerarFiltrosAds()
  // Resposta de mapa do departamento anterior ainda no ar é descartada.
  seqEstoque++
  seqVendas++
  if (tab.value === 'contas') void carregarAds()
  if (tab.value === 'tabela') {
    carregarExtrasDaTabela()
    await loadGrid()
  }
})
</script>

<template>
  <div class="space-y-4">
    <!-- M-25 (23/11/2026): sem a linha de descrição; fica só o título e os
         botões de categoria sobem, com a mesma distância de antes. -->
    <PageHeader title="Tabela de Preços" />

    <!-- ============================================ Department pills -->
    <div class="flex flex-wrap gap-2">
      <template v-if="podePrecos">
        <button
          v-for="d in DEPARTMENTS"
          :key="d.value"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors"
          :class="tab !== 'faturamento' && department === d.value
            ? 'bg-primary text-primary-foreground border-primary'
            : 'border-border hover:bg-muted'"
          @click="department = d.value"
        >
          <component :is="d.icon" class="h-4 w-4" />
          {{ d.label }} ({{ accountsByDept[d.value]?.length ?? 0 }} contas)
        </button>
      </template>
      <!-- Faturamento (13/11/2026): saiu do menu e virou este botão, depois das
           categorias (desde a M-23, 23/11/2026: Celular, Eletro, Mala). Sem
           número, como era o item do menu. -->
      <button
        v-if="podeFaturamento"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors"
        :class="tab === 'faturamento'
          ? 'bg-primary text-primary-foreground border-primary'
          : 'border-border hover:bg-muted'"
        data-botao-faturamento
        @click="tab !== 'faturamento' && setTab('faturamento')"
      >
        <Coins class="h-4 w-4" />
        Faturamento
      </button>
    </div>

    <!-- ============================================ Sub-tabs -->
    <!-- São dos departamentos: somem enquanto o Faturamento está escolhido. -->
    <div v-if="tab !== 'faturamento'" class="flex flex-wrap gap-1 rounded-md bg-muted/40 p-1 w-fit">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors"
        :class="tab === t.key
          ? 'bg-background shadow-sm'
          : 'text-muted-foreground hover:text-foreground'"
        @click="setTab(t.key)"
      >
        <component :is="t.icon" class="h-4 w-4" />
        {{ t.label }}
        <span v-if="t.key === 'contas'" class="text-xs text-muted-foreground">
          ({{ accountsCurrent.length }})
        </span>
        <span v-else-if="t.key === 'produtos'" class="text-xs text-muted-foreground">
          ({{ productsByDept[department]?.length ?? 0 }})
        </span>
      </button>
    </div>

    <!-- ============================================ CONTAS -->
    <section v-if="tab === 'contas'" class="space-y-3">
      <!-- Barra (M-51, 25/11/2026; saíram o título da categoria e a frase de
           baixo): no estilo da barra da Tabela de Preços, tudo com
           28px (h-7). Buscar | Plataforma ▾ | Contas ▾ | Layout ▾ | Equipes ▾ |
           Excel … e à direita N conta(s), Vincular Integrações e + Adicionar Conta. -->
      <div class="flex flex-wrap items-center gap-2" data-barra-contas>
        <div class="relative flex-1 min-w-[200px] max-w-sm">
          <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            v-model="contasSearch"
            placeholder="Buscar conta..."
            class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"
          />
        </div>
        <!-- Plataforma ▾ e Equipes ▾ (M-9): caixas de marcar, no visual dos
             menus da Tabela de Preços ("Marcar todas · Limpar" no topo, lista
             com rolagem). Recarregar a página marca tudo de novo. -->
        <PopoverRoot>
          <PopoverTrigger as-child>
            <button
              type="button"
              class="btn btn-sm h-7 py-0"
              :class="{ 'border-blue-400 font-medium': contasPlataformasDesmarcadas.length }"
              title="Filtrar contas pela plataforma"
              data-filtro-plataforma
            >
              Plataforma
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-plataforma
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div class="flex items-center gap-2 px-1 pb-1 text-xs">
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="contasPlataformasDesmarcadas = []">Marcar todas</button>
                <span class="text-muted-foreground">·</span>
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="contasPlataformasDesmarcadas = [...platformsPresentInDept]">Limpar</button>
              </div>
              <div class="max-h-72 overflow-y-auto border-t pt-1">
                <label
                  v-for="p in platformsPresentInDept"
                  :key="p"
                  class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="!contasPlataformasDesmarcadas.includes(p)"
                    @change="contasPlataformasDesmarcadas = alternarNaLista(contasPlataformasDesmarcadas, p)"
                  />
                  {{ platformLabel(p) }}
                </label>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <!-- Contas ▾ (M-51): pelo nome da conta, igual ao Contas ▾ da Tabela de
             Preços. Tudo marcado = sem filtro; filtro parcial: "Contas (2)". -->
        <PopoverRoot>
          <PopoverTrigger as-child>
            <button
              type="button"
              class="btn btn-sm h-7 py-0"
              :class="{ 'border-blue-400 font-medium': contasNomesDesmarcados.length }"
              title="Filtrar pelo nome da conta"
              data-filtro-contas
            >
              {{ rotuloNomesContas }}
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-contas
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div class="relative px-1 pb-1">
                <Search class="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <input
                  v-model="buscaNomesContas"
                  placeholder="Buscar conta…"
                  class="h-7 w-full border rounded pl-7 pr-2 text-sm bg-background"
                  data-campo="contas-nomes-busca"
                />
              </div>
              <div class="flex items-center gap-2 px-1 pb-1 text-xs">
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarNomesContas(true)">Marcar todas</button>
                <span class="text-muted-foreground">·</span>
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="marcarNomesContas(false)">Limpar</button>
              </div>
              <div class="max-h-72 overflow-y-auto border-t pt-1">
                <label
                  v-for="o in opcoesNomesContasBuscadas"
                  :key="o.valor"
                  class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="!contasNomesDesmarcados.includes(o.valor)"
                    @change="contasNomesDesmarcados = alternarNaLista(contasNomesDesmarcados, o.valor)"
                  />
                  <span class="truncate">{{ o.rotulo }}</span>
                </label>
                <p v-if="!opcoesNomesContasBuscadas.length" class="px-1 py-1 text-[11px] text-muted-foreground">
                  {{ buscaNomesContas.trim() ? 'Nenhuma conta encontrada.' : 'Nenhuma conta neste departamento.' }}
                </p>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <!-- Layout ▾ (M-51): mostra/esconde colunas da tabela de contas, como o
             Layout da Tabela de Preços. Lembrado neste navegador. -->
        <PopoverRoot>
          <PopoverTrigger as-child>
            <button type="button" class="btn btn-sm h-7 py-0" data-acao="layout-contas" title="Escolher as colunas que a tabela de contas mostra">
              Layout
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-layout-contas
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <!-- Só a caixa e o nome (M-61): a explicação fica na Ajuda ("?"). -->
              <label
                v-for="c in opcoesLayoutContas"
                :key="c.chave"
                class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
              >
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="mostraColunaConta(c.chave)"
                  :data-campo="`layout-contas-${c.chave}`"
                  @change="colunasOcultasContas = alternarNaLista(colunasOcultasContas, c.chave)"
                />
                <span>{{ c.rotulo }}</span>
              </label>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <PopoverRoot v-if="mostrarFiltroEquipe">
          <PopoverTrigger as-child>
            <button
              type="button"
              class="btn btn-sm h-7 py-0"
              :class="{ 'border-blue-400 font-medium': equipesPresentesNoDept.some((o) => contasEquipesDesmarcadas.includes(o.valor)) }"
              title="Filtrar contas pela equipe"
              data-filtro-equipe
            >
              Equipes
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-equipe
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div class="flex items-center gap-2 px-1 pb-1 text-xs">
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="contasEquipesDesmarcadas = []">Marcar todas</button>
                <span class="text-muted-foreground">·</span>
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" @click="contasEquipesDesmarcadas = equipesPresentesNoDept.map((o) => o.valor)">Limpar</button>
              </div>
              <div class="max-h-72 overflow-y-auto border-t pt-1">
                <label
                  v-for="o in equipesPresentesNoDept"
                  :key="o.valor"
                  class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="!contasEquipesDesmarcadas.includes(o.valor)"
                    @change="contasEquipesDesmarcadas = alternarNaLista(contasEquipesDesmarcadas, o.valor)"
                  />
                  {{ o.rotulo }}
                </label>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <button class="btn btn-sm h-7 py-0" :disabled="!accountsFiltered.length" data-acao="excel-contas" @click="exportarExcelContas">
          <Download class="h-4 w-4 mr-1" /> Excel
        </button>
        <span class="ml-auto text-xs text-muted-foreground">
          {{ accountsFiltered.length }} conta(s)
        </span>
        <button v-if="canEditContas" class="btn btn-sm h-7 py-0" @click="autoMatchAccounts">
          <Link2 class="h-4 w-4 mr-1" /> Vincular Integrações
        </button>
        <button v-if="canEditContas" class="btn btn-sm h-7 py-0 btn-primary" :disabled="showAddAcc" @click="openAddAcc">
          <Plus class="h-4 w-4 mr-1" /> Adicionar Conta
        </button>
      </div>

      <div v-if="accountsErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ accountsErr }}
      </div>

      <div class="border rounded-lg overflow-auto max-h-[calc(100vh-320px)]">
        <table class="w-full text-sm border-collapse">
          <thead class="sticky top-0 bg-muted z-10">
            <!-- Cabeçalho no formato da Tabela de Preços (M-9): 11px, peso 600,
                 linha de 14px, centralizado; Plataforma, Conta e Obs à esquerda,
                 como o conteúdo. M-52 (25/11/2026): Plataforma e depois Conta,
                 na ordem da tela de Lojas. A coluna Tipo ML saiu na M-53: a
                 conta do ML é uma linha só (Clássico e Premium). -->
            <tr>
              <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[100px]">Plataforma</th>
              <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[120px]">Conta</th>
              <!-- Catálogo ML, Equipe, Kit, Comissão e Obs: escondidos pelo
                   Layout ▾ (M-51). Desconto e Custo total ficam sempre. -->
              <th
                v-if="mostraColunaConta('catalogo')"
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-24"
                title="Catálogo ML da conta (Clássico e Premium juntos): Ativo = colunas próprias no grupo ML Catálogo da Tabela de Preços, calculadas pela coluna Catálogo dos produtos com a comissão e os fretes desta conta, que mandam preço só para o anúncio de catálogo. Clique para mudar."
              >Catálogo ML</th>
              <th
                v-if="mostraColunaConta('equipe')"
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[70px]"
                title="Número da equipe que vê esta conta (1, 2 ou 3, a mesma da tela de Lojas), pela loja ligada a ela — a que separa o que cada vendedor enxerga."
              >Equipe</th>
              <!-- Kit, Comissão e os pares marg/frete: só ADM (M-10, 18/11/2026). -->
              <template v-if="isAdmin">
              <th v-if="mostraColunaConta('kit')" class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-12" data-coluna-so-admin="kit">Kit</th>
              <th v-if="mostraColunaConta('comissao')" class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-20" title="Comissão da conta. No ML: Clássico · Premium — clique para mudar." data-coluna-so-admin="comissao">Comissão</th>
              <th
                v-for="(label, i) in TYPE_HEADERS[department] ?? []"
                :key="i"
                colSpan="2"
                class="text-center px-1 py-2 text-[11px] font-semibold leading-[14px] border-b border-border"
                data-coluna-so-admin="margem-frete"
              >
                <div>{{ label }}</div>
                <div class="text-muted-foreground flex justify-center gap-3">
                  <span>marg</span><span>frete</span>
                </div>
              </th>
              </template>
              <!-- Afiliado / Desconto / Ads / Custo total / Margem mínima (M-55/M-56/
                   M-85): colunas de ~62px, centralizadas, com a caixinha de 34×24px; só
                   Obs à esquerda. Margem mínima só para o ADM (camposAnotacao), com o
                   título em duas linhas ("Margem" / "mínima"). -->
              <th
                v-for="f in camposAnotacao"
                v-show="f.key !== 'observation' || mostraColunaConta('obs')"
                :key="`h-${f.key}`"
                class="px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border"
                :class="f.key === 'observation' ? 'text-left min-w-[120px]' : 'text-center w-[62px] min-w-[62px]'"
                :title="f.titulo"
                :data-coluna-anotacao="f.key"
              ><template v-if="f.cabecalho"><span v-for="parte in f.cabecalho" :key="parte" class="block">{{ parte }}</span></template><template v-else>{{ f.label }}</template></th>
              <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-16"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="accountsLoading && !accounts.length">
              <td :colspan="colunasContas" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!accountsFiltered.length && !showAddAcc">
              <td :colspan="colunasContas" class="text-center py-6 text-muted-foreground">
                {{ accountsCurrent.length ? 'Nenhuma conta corresponde aos filtros.' : 'Nenhuma conta neste departamento.' }}
              </td>
            </tr>

            <!-- add row: Plataforma e depois Conta (M-52). ML nasce com Clássico
                 e Premium, sem escolher tipo (M-53). -->
            <tr v-if="showAddAcc" class="bg-blue-50/40 dark:bg-blue-900/10">
              <td class="border border-border px-1 py-1">
                <select v-model="newAcc.platform" class="w-full text-xs border rounded px-1 py-1 bg-background">
                  <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
                </select>
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  id="new-acc-name"
                  v-model="newAcc.name"
                  type="text"
                  placeholder="Conta"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                  @keydown.enter="submitNewAcc"
                  @keydown.escape="showAddAcc = false"
                />
              </td>
              <!-- Catálogo ML e Equipe: definidos depois, na linha criada. -->
              <td v-if="mostraColunaConta('catalogo')" class="border border-border text-center text-xs text-muted-foreground">—</td>
              <td v-if="mostraColunaConta('equipe')" class="border border-border text-center text-xs text-muted-foreground">—</td>
              <template v-if="isAdmin">
              <td v-if="mostraColunaConta('kit')" class="border border-border px-1 py-1">
                <input
                  v-model.number="newAcc.kit_number"
                  type="text" inputmode="numeric"
                  class="w-full text-xs border rounded px-1 py-1 bg-background text-center"
                  data-campo="novo-kit"
                />
              </td>
              <!-- ML: a comissão de cada tipo é a usual da categoria (Clássico · Premium). -->
              <template v-if="mostraColunaConta('comissao')">
              <td
                v-if="newAcc.platform === 'mercadolivre'"
                class="border border-border px-1 py-1 text-center text-xs text-muted-foreground whitespace-nowrap"
                title="Comissão com que a conta nasce (Clássico · Premium), a usual da categoria. Depois, clique na Comissão da conta para mudar."
                data-campo="nova-comissao-ml"
              >{{ comissaoPadraoMl(department) }}</td>
              <td v-else class="border border-border px-1 py-1">
                <input
                  v-model="newAcc.commission"
                  type="text" inputmode="decimal"
                  placeholder="11"
                  class="w-full text-xs border rounded px-1 py-1 bg-background text-center"
                  data-campo="nova-comissao"
                  @keydown.enter="submitNewAcc"
                />
              </td>
              </template>
              <td v-for="i in nTiposAba * 2" :key="i" class="border border-border text-center text-xs text-muted-foreground">—</td>
              </template>
              <td v-for="i in camposAnotacao.length - (mostraColunaConta('obs') ? 0 : 1)" :key="`obs-${i}`" class="border border-border text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1 text-center">
                <div class="flex gap-0.5 justify-center">
                  <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" :disabled="addingAcc" @click="submitNewAcc">
                    <Loader2 v-if="addingAcc" class="h-3 w-3 animate-spin" />
                    <Check v-else class="h-3 w-3" />
                  </button>
                  <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="showAddAcc = false">
                    <X class="h-3 w-3" />
                  </button>
                </div>
              </td>
            </tr>

            <!-- data rows grouped by platform (SSH-style) -->
            <template v-for="group in accountsGrouped" :key="group.platform">
              <tr class="bg-muted/40">
                <td :colspan="colunasContas" class="px-3 py-1.5 text-[11px] font-bold tracking-wider text-muted-foreground uppercase" data-grupo-contas>
                  {{ group.label }} · {{ group.rows.length }} CONTA(S)
                </td>
              </tr>
              <template v-for="acc in group.rows" :key="acc.id">
              <tr class="hover:bg-accent/30">
              <!-- plataforma (M-52: primeira coluna, à esquerda). Conta ML com
                   Clássico e Premium não troca de plataforma por aqui; conta ML
                   sem par (só um tipo) mostra o tipo ao lado de "ML". -->
              <td
                class="border border-border px-2 py-1.5 text-xs text-left whitespace-nowrap"
                :class="{
                  'cursor-pointer': !parDe(acc),
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'platform'),
                  'ring-1 ring-destructive ring-inset': erroPlataforma?.id === acc.id && !isEditing(acc.id, 'platform'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'platform'),
                }"
                :title="parDe(acc) ? 'Conta do ML com Clássico e Premium (as duas colunas na Tabela de Preços)' : undefined"
                data-coluna-plataforma
                @click="!isEditing(acc.id, 'platform') && !parDe(acc) && startEditAccount(acc, 'platform')"
              >
                <select
                  v-if="isEditing(acc.id, 'platform')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEditAccount"
                  @change="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                >
                  <option v-for="p in plataformasDaTroca(acc)" :key="p.value" :value="p.value">{{ p.label }}</option>
                </select>
                <template v-else>
                  <span>{{ platformLabel(acc.platform) }}</span>
                  <span v-if="tipoDaContaSolta(acc)" class="ml-1 text-muted-foreground" data-tipo-conta-solta>{{ tipoDaContaSolta(acc) }}</span>
                </template>
                <p
                  v-if="erroPlataforma?.id === acc.id"
                  class="mt-0.5 whitespace-normal text-[11px] leading-snug text-destructive"
                  role="alert"
                  data-erro-plataforma
                >{{ erroPlataforma?.mensagem }}</p>
              </td>
              <!-- conta (o nome, como está cadastrado; M-52: segunda coluna) -->
              <td
                class="border border-border px-2 py-1.5 text-xs text-left relative"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'name'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'name'),
                }"
              >
                <input
                  v-if="isEditing(acc.id, 'name')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEditAccount"
                  @keydown.enter.prevent="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                />
                <div v-else class="flex items-center gap-1 group/acc">
                  <span
                    class="font-medium cursor-pointer flex-1 truncate"
                    @click="startEditAccount(acc, 'name')"
                  >{{ acc.name }}<Check v-if="isFlashed(acc.id, 'name')" class="inline h-3 w-3 ml-1 text-emerald-600" /></span>
                  <button
                    v-if="canEditContas"
                    class="opacity-0 group-hover/acc:opacity-100 text-muted-foreground hover:text-foreground p-0.5 rounded"
                    title="Vincular segmento aos slots de margem/frete"
                    @click.stop="openSlotPopover(acc)"
                  >
                    <Tags class="h-3 w-3" />
                  </button>
                </div>
                <!-- slot↔segment editor popover (per-account) -->
                <div
                  v-if="slotPopoverAccountId === acc.id"
                  class="absolute left-0 top-full mt-1 z-30 w-72 rounded-md border bg-background shadow-lg p-3 text-left"
                  @click.stop
                >
                  <div class="text-xs font-semibold mb-2">
                    Slots desta conta · {{ DEPARTMENTS.find(d => d.value === acc.department)?.label }}
                  </div>
                  <div class="space-y-1.5">
                    <div v-for="i in 5" :key="i">
                      <div class="flex items-center gap-2">
                        <span class="text-[10px] text-muted-foreground w-12 shrink-0">
                          slot{{ i }}<br/>
                          <span class="opacity-70">m{{ i }}/f{{ i }}</span>
                        </span>
                        <select
                          v-model="slotPopoverValues[i - 1]"
                          class="flex-1 text-xs border rounded px-1.5 py-1 bg-background"
                        >
                          <option :value="null">— sem vínculo —</option>
                          <option
                            v-for="seg in segmentChildrenFor(acc)"
                            :key="seg.id"
                            :value="seg.id"
                          >{{ seg.name }}</option>
                        </select>
                      </div>
                      <!-- Conta ML com par: Clássico e Premium com slots diferentes aqui. -->
                      <p
                        v-if="slotPopoverDiferentes[i - 1]"
                        class="pl-14 pt-0.5 text-[10px] leading-snug text-amber-700 dark:text-amber-400"
                        data-aviso-slot-diferente
                      >Clássico e Premium estão diferentes neste campo; se você mudar, o novo valor vale para os dois.</p>
                    </div>
                  </div>
                  <p v-if="slotPopoverError" class="text-[10px] text-red-600 mt-1.5">{{ slotPopoverError }}</p>
                  <div class="flex justify-end gap-1 pt-2 mt-2 border-t">
                    <button class="text-xs px-2 py-1 rounded hover:bg-muted" @click="closeSlotPopover">Cancelar</button>
                    <button
                      class="text-xs px-2 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                      :disabled="slotPopoverSaving"
                      @click="saveSlotPopover(acc.id)"
                    >
                      {{ slotPopoverSaving ? 'salvando…' : 'salvar' }}
                    </button>
                  </div>
                </div>
              </td>
              <!-- Catálogo ML (M-54): só "Ativo" ou "Inativo". Quem edita Contas
                   clica e abre a janela da conta ("ML forpaper"): [x] Catálogo ativo
                   e, para o ADM, o botão Margens. Nada grava antes do Salvar. -->
              <td
                v-if="mostraColunaConta('catalogo')"
                class="border border-border px-1 py-1 text-center text-xs"
                :class="{ 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'catalogo') }"
                data-coluna-catalogo
              >
                <template v-if="podeTerCatalogo(acc)">
                  <PopoverRoot
                    v-if="canEditContas"
                    :open="catalogoJanelaId === acc.id"
                    @update:open="alternarJanelaCatalogo(acc, $event)"
                  >
                    <PopoverTrigger as-child>
                      <button
                        type="button"
                        class="rounded px-1.5 py-0.5 text-xs hover:bg-muted"
                        data-acao="catalogo"
                        :title="tituloCatalogoDaConta(acc)"
                      >{{ catalogoAtivo(acc) ? 'Ativo' : 'Inativo' }}</button>
                    </PopoverTrigger>
                    <PopoverPortal>
                      <PopoverContent
                        side="bottom"
                        align="start"
                        :side-offset="4"
                        :collision-padding="8"
                        data-janela-catalogo
                        class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 text-xs shadow-lg"
                      >
                        <div class="px-1 pb-2 text-xs font-bold">{{ tituloConta(acc) }}</div>
                        <label class="flex cursor-pointer items-center gap-2 px-1 py-1">
                          <input
                            v-model="catalogoJanelaAtivo"
                            type="checkbox"
                            class="size-3.5"
                            :disabled="catalogoJanelaSalvando"
                            data-campo="catalogo-ativo"
                          />
                          Catálogo ativo
                        </label>
                        <!-- Margens: só o ADM (quem via o link das margens antes), com o
                             catálogo marcado como ativo. -->
                        <template v-if="isAdmin">
                          <div class="px-1 pt-1">
                            <button
                              type="button"
                              class="btn btn-sm h-6 py-0 text-xs"
                              :class="{ 'border-blue-400 font-medium': catalogoJanelaMargens && catalogoJanelaAtivo }"
                              :disabled="!catalogoJanelaAtivo || catalogoJanelaSalvando"
                              data-acao="margens-catalogo"
                              @click="catalogoJanelaMargens = !catalogoJanelaMargens"
                            >
                              Margens
                              <ChevronDown class="h-3 w-3 ml-1" />
                            </button>
                          </div>
                          <div
                            v-if="catalogoJanelaMargens && catalogoJanelaAtivo"
                            class="mt-1 space-y-1 border-t px-1 pt-1.5"
                            data-margens-catalogo
                          >
                            <label class="flex cursor-pointer items-center gap-2">
                              <input
                                v-model="catalogoJanelaUsarDaConta"
                                type="checkbox"
                                class="size-3.5"
                                :disabled="catalogoJanelaSalvando"
                                data-campo="catalogo-usar-margens-da-conta"
                              />
                              Usar as margens da conta
                            </label>
                            <div
                              v-for="(nome, i) in TYPE_HEADERS[department] ?? []"
                              :key="i"
                              class="flex items-center justify-between gap-2"
                            >
                              <label :for="`margem-cat-${acc.id}-${i}`">{{ nome }}</label>
                              <span class="inline-flex items-center gap-1">
                                <!-- Marcada: travado, com a margem da conta em cinza. -->
                                <input
                                  v-if="catalogoJanelaUsarDaConta"
                                  :id="`margem-cat-${acc.id}-${i}`"
                                  :value="margemDaContaTexto(acc, i + 1)"
                                  type="text"
                                  disabled
                                  class="h-6 w-14 rounded border bg-muted px-1 text-right text-xs text-muted-foreground"
                                />
                                <input
                                  v-else
                                  :id="`margem-cat-${acc.id}-${i}`"
                                  v-model="catalogoJanelaValores[i]"
                                  type="text"
                                  inputmode="decimal"
                                  class="h-6 w-14 rounded border bg-background px-1 text-right text-xs"
                                  :placeholder="margemDaContaTexto(acc, i + 1)"
                                  :disabled="catalogoJanelaSalvando"
                                  @keydown.enter.prevent="salvarCatalogo()"
                                />
                                <span class="text-muted-foreground">%</span>
                              </span>
                            </div>
                          </div>
                        </template>
                        <p v-if="catalogoJanelaErro" class="px-1 pt-1 text-[11px] leading-snug text-destructive" role="alert">
                          {{ catalogoJanelaErro }}
                        </p>
                        <div class="mt-2 flex justify-end gap-2 border-t px-1 pt-2">
                          <button
                            type="button"
                            class="btn btn-sm h-7 py-0"
                            :disabled="catalogoJanelaSalvando"
                            @click="fecharCatalogo()"
                          >Cancelar</button>
                          <button
                            type="button"
                            class="btn btn-sm btn-primary h-7 py-0"
                            :disabled="catalogoJanelaSalvando"
                            data-acao="salvar-catalogo"
                            @click="salvarCatalogo()"
                          >{{ catalogoJanelaSalvando ? 'Salvando…' : 'Salvar' }}</button>
                        </div>
                      </PopoverContent>
                    </PopoverPortal>
                  </PopoverRoot>
                  <span v-else>{{ catalogoAtivo(acc) ? 'Ativo' : 'Inativo' }}</span>
                </template>
                <span v-else class="text-xs text-muted-foreground">—</span>
              </td>
              <!-- equipe: só leitura (vem da equipe de vendas da loja) -->
              <td
                v-if="mostraColunaConta('equipe')"
                class="border border-border px-2 py-1.5 text-xs text-center whitespace-nowrap"
                :class="{ 'text-muted-foreground': !equipesDaConta(acc).length }"
                data-coluna-equipe
              >{{ rotuloEquipes(acc) }}</td>
              <!-- kit, comissão e pares marg/frete: só ADM (M-10) -->
              <template v-if="isAdmin">
              <!-- kit -->
              <td
                v-if="mostraColunaConta('kit')"
                class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'kit_number') }"
                @click="!isEditing(acc.id, 'kit_number') && startEditAccount(acc, 'kit_number')"
              >
                <input
                  v-if="isEditing(acc.id, 'kit_number')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="text" inputmode="numeric"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditAccount"
                  @keydown.enter.prevent="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ acc.kit_number }}</span>
              </td>
              <!-- comissão. Conta ML com par (M-53): "14% · 19%" (Clássico · Premium);
                   clicar abre a janelinha "ML forpaper" e só grava no Salvar. -->
              <td
                v-if="mostraColunaConta('comissao') && parDe(acc)"
                class="border border-border px-1 py-1 text-xs text-center whitespace-nowrap"
                :class="{ 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'commission') }"
                data-comissao-ml
              >
                <PopoverRoot :open="comissaoMlId === acc.id" @update:open="alternarJanelaComissao(acc, $event)">
                  <PopoverTrigger as-child>
                    <button
                      type="button"
                      class="rounded px-1 py-0.5 text-xs hover:bg-muted"
                      data-acao="comissao-ml"
                      title="Comissão do Clássico · do Premium — clique para mudar"
                    >{{ textoComissaoPar(parDe(acc)!) }}</button>
                  </PopoverTrigger>
                  <PopoverPortal>
                    <PopoverContent
                      side="bottom"
                      align="start"
                      :side-offset="4"
                      :collision-padding="8"
                      data-janela-comissao-ml
                      class="z-[70] w-52 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 text-xs shadow-lg"
                    >
                      <div class="px-1 pb-2 text-xs font-bold">{{ tituloConta(acc) }}</div>
                      <div
                        v-for="t in TIPOS_COMISSAO_ML"
                        :key="t.chave"
                        class="flex items-center justify-between gap-2 px-1 py-0.5"
                      >
                        <label :for="`comissao-ml-${t.chave}`">{{ t.rotulo }}</label>
                        <span class="inline-flex items-center gap-1">
                          <input
                            :id="`comissao-ml-${t.chave}`"
                            v-model="comissaoMlValores[t.chave]"
                            type="text"
                            inputmode="decimal"
                            class="h-6 w-14 rounded border bg-background px-1 text-right text-xs"
                            :disabled="comissaoMlSalvando"
                            :data-campo="`comissao-ml-${t.chave}`"
                            @keydown.enter.prevent="salvarComissaoMl()"
                          />
                          <span class="text-muted-foreground">%</span>
                        </span>
                      </div>
                      <p v-if="comissaoMlErro" class="px-1 pt-1 text-[11px] leading-snug text-destructive" role="alert">
                        {{ comissaoMlErro }}
                      </p>
                      <div class="mt-2 flex justify-end gap-2 border-t px-1 pt-2">
                        <button
                          type="button"
                          class="btn btn-sm h-7 py-0"
                          :disabled="comissaoMlSalvando"
                          @click="fecharComissaoMl()"
                        >Cancelar</button>
                        <button
                          type="button"
                          class="btn btn-sm btn-primary h-7 py-0"
                          :disabled="comissaoMlSalvando"
                          data-acao="salvar-comissao-ml"
                          @click="salvarComissaoMl()"
                        >{{ comissaoMlSalvando ? 'Salvando…' : 'Salvar' }}</button>
                      </div>
                    </PopoverContent>
                  </PopoverPortal>
                </PopoverRoot>
              </td>
              <!-- as outras (e conta ML sem par): clique e digite, como antes -->
              <td
                v-else-if="mostraColunaConta('comissao')"
                class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'commission'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'commission'),
                }"
                @click="!isEditing(acc.id, 'commission') && startEditAccount(acc, 'commission')"
              >
                <input
                  v-if="isEditing(acc.id, 'commission')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="text" inputmode="decimal"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditAccount"
                  @keydown.enter.prevent="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ fmtCommission(acc.commission) }}</span>
              </td>
              <!-- pares marg/frete: um por tipo ativo da aba -->
              <template v-for="t in nTiposAba" :key="t">
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, `margin${t}`),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, `margin${t}`),
                  }"
                  @click="!isEditing(acc.id, `margin${t}`) && startEditAccount(acc, `margin${t}`)"
                >
                  <input
                    v-if="isEditing(acc.id, `margin${t}`)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="text" inputmode="decimal"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ fmtMargin((acc as any)[`margin${t}`]) }}</span>
                </td>
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, `shipping${t}`),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, `shipping${t}`),
                  }"
                  @click="!isEditing(acc.id, `shipping${t}`) && startEditAccount(acc, `shipping${t}`)"
                >
                  <input
                    v-if="isEditing(acc.id, `shipping${t}`)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="text" inputmode="numeric"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ fmtShipping((acc as any)[`shipping${t}`]) }}</span>
                </td>
              </template>
              </template>
              <!-- afiliado / desconto / ads / custo total / margem mínima / obs (M-55/M-56/M-85) -->
              <template v-for="f in camposAnotacao" :key="`c-${f.key}`">
                <!-- Campos próprios (%): caixinha de 34×24px, 12px, número no meio e
                     "%" ao lado, sempre à mostra; gravam ao sair ou com Enter. Custo
                     total e Margem mínima: só o ADM muda (o Custo total os outros veem
                     travado; a Margem mínima eles nem veem). Com a Margem mínima
                     preenchida (M-86) o Custo total é calculado e fica travado para
                     todos, em cinza. Recusa do Custo total (M-56): borda vermelha e o
                     aviso do servidor junto do campo. Custo total calculado abaixo de
                     Afiliado + Desconto + Ads (M-86): borda vermelha e o aviso até
                     alguém corrigir. -->
                <td
                  v-if="f.tipo"
                  class="relative border border-border px-1 py-1 text-center w-[62px] min-w-[62px]"
                  :class="{ 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, f.key) }"
                >
                  <div class="flex items-center justify-center gap-0.5">
                    <input
                      :value="textoAnotacao(acc, f)"
                      type="text"
                      inputmode="decimal"
                      maxlength="6"
                      :readonly="anotacaoTravada(acc, f)"
                      class="h-6 w-[34px] min-w-0 rounded border px-0.5 text-center text-xs"
                      :class="[
                        anotacaoTravada(acc, f) ? 'bg-transparent cursor-default' : 'bg-background',
                        {
                          'text-muted-foreground': calculadoPelaMargemMinima(acc, f),
                          'border-destructive ring-1 ring-destructive': estourouCustoTotal(acc, f.key) || avisoNoCampo(acc, f),
                        },
                      ]"
                      :title="tituloAnotacao(acc, f)"
                      :data-campo="`conta-${f.key}`"
                      @blur="gravarAnotacao(acc, f, $event)"
                      @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
                      @keydown.escape.prevent="desfazerAnotacao(acc, f, $event)"
                    />
                    <span class="text-xs text-muted-foreground">%</span>
                  </div>
                  <div
                    v-if="estourouCustoTotal(acc, f.key)"
                    class="absolute left-0 top-full z-20 mt-0.5 flex w-60 items-start gap-1 rounded border border-destructive/50 bg-background px-2 py-1 text-left text-[11px] leading-snug text-destructive shadow-md"
                    role="alert"
                    data-aviso-custo-total
                  >
                    <span class="flex-1">{{ estourouCustoTotal(acc, f.key) }}</span>
                    <button
                      type="button"
                      class="shrink-0 text-muted-foreground hover:text-foreground"
                      title="Fechar o aviso"
                      @click="erroCustoTotal = null"
                    ><X class="h-3 w-3" /></button>
                  </div>
                  <div
                    v-else-if="avisoAberto(acc, f)"
                    class="absolute left-0 top-full z-20 mt-0.5 flex w-60 items-start gap-1 rounded border border-destructive/50 bg-background px-2 py-1 text-left text-[11px] leading-snug text-destructive shadow-md"
                    role="alert"
                    data-aviso-custo-total-abaixo
                  >
                    <span class="flex-1">{{ avisoAberto(acc, f) }}</span>
                    <button
                      type="button"
                      class="shrink-0 text-muted-foreground hover:text-foreground"
                      title="Fechar o aviso (a borda vermelha fica até corrigir)"
                      @click="fecharAvisoCustoTotal(acc)"
                    ><X class="h-3 w-3" /></button>
                  </div>
                </td>
                <!-- Ads (M-12/M-55): a caixinha com o % Geral (vazia sem %) abre o
                     painel da conta; não se digita na tabela. Quem não edita Contas
                     abre e só vê. O botão é "flex" com a largura da célula (M-60),
                     igual ao invólucro dos outros campos: assim a caixinha vazia não
                     sobe nem deixa a linha mais alta. -->
                <td
                  v-else-if="f.key === 'ads'"
                  class="border border-border px-1 py-1 text-center w-[62px] min-w-[62px]"
                  :class="{ 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'ads') }"
                >
                  <PopoverRoot @update:open="abrirOuFecharAds">
                    <PopoverTrigger as-child>
                      <button
                        type="button"
                        class="flex w-full items-center justify-center gap-0.5"
                        data-acao="ads"
                        :data-ads-botao="acc.id"
                        :title="adsErro ? 'O Ads não carregou — recarregue a página.' : tituloBotaoAds(adsDa(acc.id))"
                      >
                        <span
                          class="inline-flex h-6 w-[34px] items-center justify-center rounded border bg-background text-xs hover:bg-muted"
                          data-ads-caixa
                        >{{ ads && pctGeralAds(adsDa(acc.id)) != null ? fmtPct(pctGeralAds(adsDa(acc.id))!) : '' }}</span>
                        <span class="text-xs text-muted-foreground">%</span>
                      </button>
                    </PopoverTrigger>
                    <PopoverPortal>
                      <PopoverContent
                        side="bottom"
                        align="start"
                        :side-offset="4"
                        :collision-padding="8"
                        data-menu-ads
                        :data-ads-painel="acc.id"
                        class="z-[70] w-[40rem] max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 text-xs shadow-lg"
                        @pointerdown="fecharMenuCategoriaAdsFora"
                      >
                        <!-- M-57: título = plataforma + conta (12px, negrito). M-78:
                             logo abaixo, a linha das caixas Geral | Produto (um ou
                             outro) com um traço fino embaixo; com Geral, o cabeçalho e
                             a linha "Todos os produtos"; com Produto, a busca, o
                             cabeçalho e a lista. Cabeçalho, Geral e lista reservam a
                             mesma faixa da barra de rolagem (scrollbar-gutter), para as
                             colunas baterem. M-79: janela de 40rem e a coluna
                             "Categoria de venda" depois do nome, na mesma grade. -->
                        <div class="px-1 pb-1 text-xs font-bold" data-ads-titulo>{{ tituloConta(acc) }}</div>
                        <div v-if="!ads" class="px-1 py-1 text-muted-foreground">
                          {{ adsErro ? 'O Ads não carregou — recarregue a página.' : 'carregando o Ads…' }}
                        </div>
                        <!-- `c` = o Ads desta conta (alias para o template; nada até carregar). -->
                        <template v-for="c in ads ? [adsDa(acc.id)] : []" :key="acc.id">
                          <!-- M-78: Geral e Produto lado a lado. M-79/M-80: com Produto
                               marcado, nesta mesma linha, depois de um traço fino, o
                               filtro "Categoria de venda ▾" e a caixinha "Ads". -->
                          <div
                            class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-1 pb-1.5"
                            data-ads-modo
                          >
                            <label class="flex cursor-pointer items-center gap-1.5">
                              <input
                                type="checkbox"
                                class="size-3.5"
                                :class="{ 'ring-1 ring-destructive': erroAdsEm(acc.id, 'geral:ads_geral_ligada', true) }"
                                data-campo="ads-geral-ligada"
                                :checked="modoAds(c) === 'geral'"
                                :disabled="!podeEditarAds"
                                @change="trocarModoAds(acc.id, 'geral')"
                              />
                              Geral
                            </label>
                            <label class="flex cursor-pointer items-center gap-1.5">
                              <input
                                type="checkbox"
                                class="size-3.5"
                                :class="{ 'ring-1 ring-destructive': erroAdsEm(acc.id, 'geral:ads_por_produto_ligada', true) }"
                                data-campo="ads-por-produto-ligada"
                                :checked="modoAds(c) === 'produto'"
                                :disabled="!podeEditarAds"
                                @change="trocarModoAds(acc.id, 'produto')"
                              />
                              Produto
                            </label>
                            <!-- M-79 + M-80: filtros só da tela (não gravam; quem só vê
                                 também usa). O menu de categorias é só caixinha + nome e
                                 abre dentro da própria janela, para não fechar a janela
                                 de Ads. -->
                            <template v-if="modoAds(c) === 'produto'">
                              <span class="h-4 w-px bg-border" aria-hidden="true"></span>
                              <div class="relative" data-filtro-ads-categoria>
                                <button
                                  type="button"
                                  class="inline-flex h-6 items-center gap-1 rounded border bg-background px-2 text-xs hover:bg-muted"
                                  :class="{ 'border-blue-400 font-medium': filtroCategoriaAds.length }"
                                  data-campo="ads-filtro-categoria"
                                  title="Mostrar só os produtos das categorias de venda marcadas"
                                  :aria-expanded="menuCategoriaAdsAberto"
                                  @click="menuCategoriaAdsAberto = !menuCategoriaAdsAberto"
                                >
                                  {{ filtroCategoriaAds.length ? `Categoria de venda · ${filtroCategoriaAds.length}` : 'Categoria de venda' }}
                                  <ChevronDown class="h-3 w-3" />
                                </button>
                                <div
                                  v-if="menuCategoriaAdsAberto"
                                  class="absolute left-0 top-full z-10 mt-1 max-h-64 w-48 overflow-y-auto rounded-md border bg-background p-1 shadow-lg"
                                  data-menu-ads-categoria
                                >
                                  <label
                                    v-for="cat in ads?.categorias ?? []"
                                    :key="cat.id"
                                    class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-muted/50"
                                  >
                                    <input
                                      type="checkbox"
                                      class="size-3.5"
                                      :checked="filtroCategoriaAds.includes(cat.id)"
                                      :data-campo="`ads-filtro-categoria-${cat.id}`"
                                      @change="alternarCategoriaAds(cat.id)"
                                    />
                                    <span class="truncate">{{ cat.name }}</span>
                                  </label>
                                </div>
                              </div>
                              <label
                                class="flex cursor-pointer items-center gap-1.5"
                                title="Mostrar só os produtos com Ads nesta conta (% próprio ou gasto por dia)"
                              >
                                <input
                                  type="checkbox"
                                  class="size-3.5"
                                  data-campo="ads-filtro-so-com-ads"
                                  :checked="soComAds"
                                  @change="alternarFiltroAds"
                                />
                                Ads
                              </label>
                            </template>
                          </div>
                          <p
                            v-if="erroAdsDoModo(acc.id)"
                            class="px-1 pt-1 text-[11px] leading-snug text-destructive"
                            role="alert"
                            data-aviso-custo-total
                          >{{ erroAdsDoModo(acc.id) }}</p>
                          <div
                            v-if="modoAds(c) === 'geral'"
                            class="overflow-hidden px-1 pt-1 [scrollbar-gutter:stable]"
                          >
                            <!-- M-79: a mesma grade do Produto; a Geral não tem
                                 categoria de venda (coluna vazia). -->
                            <div
                              class="grid grid-cols-[minmax(0,1fr)_6rem_5rem_4rem_6rem] items-end gap-x-2 pb-0.5 text-[11px] font-semibold leading-[14px] text-muted-foreground"
                              data-ads-cabecalho
                            >
                              <span></span>
                              <span></span>
                              <span class="text-right">Pode gastar (%)</span>
                              <span class="text-right">ROAS mínimo</span>
                              <span class="text-right">Gasto por dia (R$)</span>
                            </div>
                            <div
                              class="grid grid-cols-[minmax(0,1fr)_6rem_5rem_4rem_6rem] items-center gap-x-2 py-0.5"
                              data-ads-geral
                            >
                              <span class="truncate">Todos os produtos</span>
                              <span></span>
                              <span class="inline-flex items-center justify-end gap-0.5">
                                <input
                                  type="text"
                                  inputmode="decimal"
                                  class="h-6 w-12 border rounded px-1 text-right text-xs bg-background disabled:opacity-60"
                                  :class="{ 'border-destructive ring-1 ring-destructive': erroAdsEm(acc.id, 'geral:pode_gastar_pct') }"
                                  data-campo="ads-pct"
                                  :data-ads-conta="acc.id"
                                  data-ads-campo="pode_gastar_pct"
                                  :value="c.pode_gastar_pct == null ? '' : fmtPct(c.pode_gastar_pct)"
                                  :disabled="!podeEditarAds"
                                  @change="gravarCampoAds"
                                />
                                %
                              </span>
                              <span class="text-right text-muted-foreground" data-ads-roas-minimo>
                                {{ fmtRoas(roasMinimo(c.pode_gastar_pct)) }}
                              </span>
                              <input
                                type="text"
                                inputmode="decimal"
                                class="h-6 w-full border rounded px-1 text-right text-xs bg-background disabled:opacity-60"
                                :class="{ 'border-destructive ring-1 ring-destructive': erroAdsEm(acc.id, 'geral:gasto_dia') }"
                                data-campo="ads-gasto-dia"
                                :data-ads-conta="acc.id"
                                data-ads-campo="gasto_dia"
                                :value="fmtGasto(c.gasto_dia)"
                                :disabled="!podeEditarAds"
                                @change="gravarCampoAds"
                              />
                            </div>
                            <p
                              v-if="erroAdsDaLinhaGeral(acc.id)"
                              class="pb-1 text-[11px] leading-snug text-destructive"
                              role="alert"
                              data-aviso-custo-total
                            >{{ erroAdsDaLinhaGeral(acc.id) }}</p>
                          </div>
                          <template v-else-if="modoAds(c) === 'produto'">
                          <div class="px-1 pb-1 pt-1">
                            <div class="relative">
                              <Search class="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
                              <input
                                v-model="buscaAdsProduto"
                                placeholder="Buscar produto por SKU ou nome…"
                                class="h-6 w-full border rounded pl-6 pr-2 text-xs bg-background"
                                data-campo="ads-busca-produto"
                              />
                            </div>
                          </div>
                          <div class="overflow-hidden px-1 [scrollbar-gutter:stable]">
                            <!-- M-79: "Categoria de venda" logo depois do Produto. -->
                            <div
                              class="grid grid-cols-[minmax(0,1fr)_6rem_5rem_4rem_6rem] items-end gap-x-2 pb-0.5 text-[11px] font-semibold leading-[14px] text-muted-foreground"
                              data-ads-cabecalho-produto
                            >
                              <span>Produto</span>
                              <span>Categoria de venda</span>
                              <span class="text-right">Pode gastar (%)</span>
                              <span class="text-right">ROAS mínimo</span>
                              <span class="text-right">Gasto por dia (R$)</span>
                            </div>
                          </div>
                          <div
                            class="max-h-64 overflow-y-auto px-1 pb-1 [scrollbar-gutter:stable]"
                            data-lista-ads-produto
                          >
                            <template v-for="p in produtosDoAds(c)" :key="p.id">
                              <div
                                class="grid grid-cols-[minmax(0,1fr)_6rem_5rem_4rem_6rem] items-center gap-x-2 py-0.5"
                                :data-ads-produto-linha="p.id"
                              >
                                <span class="truncate" :title="`${p.sku} — ${p.name}`">{{ p.name }}</span>
                                <span
                                  class="truncate text-muted-foreground"
                                  :title="p.categoria?.name ?? ''"
                                  data-ads-categoria
                                >{{ p.categoria?.name ?? '' }}</span>
                                <span class="inline-flex items-center justify-end gap-0.5">
                                  <input
                                    type="text"
                                    inputmode="decimal"
                                    class="h-6 w-12 border rounded px-1 text-right text-xs bg-background disabled:opacity-60"
                                    :class="{ 'border-destructive ring-1 ring-destructive': erroAdsEm(acc.id, `produto:${p.id}:pode_gastar_pct`) }"
                                    placeholder="—"
                                    :value="c.produtos[p.id]?.pode_gastar_pct == null ? '' : fmtPct(c.produtos[p.id].pode_gastar_pct!)"
                                    :data-ads-conta="acc.id"
                                    data-ads-campo="pode_gastar_pct"
                                    :data-ads-produto="p.id"
                                    :disabled="!podeEditarAds"
                                    @change="gravarCampoAds"
                                  />
                                  %
                                </span>
                                <span class="text-right text-muted-foreground" data-ads-roas-produto>
                                  {{ fmtRoas(roasMinimo(pctDoProdutoAds(c, p.id))) }}
                                </span>
                                <input
                                  type="text"
                                  inputmode="decimal"
                                  class="h-6 w-full border rounded px-1 text-right text-xs bg-background disabled:opacity-60"
                                  :class="{ 'border-destructive ring-1 ring-destructive': erroAdsEm(acc.id, `produto:${p.id}:gasto_dia`) }"
                                  :value="fmtGasto(c.produtos[p.id]?.gasto_dia)"
                                  :data-ads-conta="acc.id"
                                  data-ads-campo="gasto_dia"
                                  :data-ads-produto="p.id"
                                  :disabled="!podeEditarAds"
                                  @change="gravarCampoAds"
                                />
                              </div>
                              <p
                                v-if="erroAdsEm(acc.id, `produto:${p.id}:`, true)"
                                class="pb-1 text-[11px] leading-snug text-destructive"
                                role="alert"
                                data-aviso-custo-total
                              >{{ erroAdsEm(acc.id, `produto:${p.id}:`, true) }}</p>
                            </template>
                            <p v-if="!produtosDoAds(c).length" class="py-1 text-muted-foreground">
                              {{ textoListaAdsVazia(c) }}
                            </p>
                          </div>
                          </template>
                          <!-- M-62: sem a frase do rodapé (nem a linha dela); a lista vai até o fim. -->
                        </template>
                      </PopoverContent>
                    </PopoverPortal>
                  </PopoverRoot>
                </td>
                <td
                  v-else
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-left max-w-[260px]"
                  v-show="f.key !== 'observation' || mostraColunaConta('obs')"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, f.key),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, f.key),
                  }"
                  @click="!isEditing(acc.id, f.key) && startEditAccount(acc, f.key)"
                >
                  <input
                    v-if="isEditing(acc.id, f.key)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="text"
                    class="w-full text-xs bg-transparent outline-none"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else :class="{ 'text-muted-foreground': !((acc as any)[f.key]) }">
                    {{ (acc as any)[f.key] || '—' }}
                  </span>
                </td>
              </template>
              <!-- actions -->
              <td class="border border-border px-1 py-1 text-center">
                <button
                  v-if="canDeleteContas"
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  :title="`Excluir ${acc.name}`"
                  @click="deleteAccount(acc)"
                >
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
              </tr>
              </template>
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ PRODUTOS -->
    <section v-else-if="tab === 'produtos'" class="space-y-3">
      <!-- Pendências box (Custo divergente / Sem anúncio / Fora da tabela) -->
      <div
        v-if="!auditHidden && (auditLoading || auditLoaded || auditError)"
        class="rounded border border-amber-300 bg-amber-50 p-3"
      >
        <div class="flex items-center justify-between">
          <div class="text-sm font-semibold text-amber-800 flex items-center gap-2">
            <AlertCircle class="h-4 w-4" />
            <span v-if="auditLoading">Verificando pendências…</span>
            <span v-else-if="auditPending.length === 0 && auditDismissed.length === 0">
              Nenhuma pendência encontrada.
            </span>
            <span v-else>
              {{ auditPending.length }} produto(s) do Bling com pendências
              <span v-if="auditDismissed.length" class="font-normal text-amber-700">({{ auditDismissed.length }} dispensado(s))</span>
            </span>
          </div>
          <div class="flex gap-2">
            <button
              v-if="canRefreshStock"
              class="btn btn-sm"
              :disabled="stockRefreshing"
              title="Puxa o estoque atual do Bling e regrava — corrige números defasados (ex. kit com componente zerado)"
              @click="refreshBlingStock"
            >
              <RefreshCw class="h-3.5 w-3.5 mr-1" :class="{ 'animate-spin': stockRefreshing }" /> Atualizar estoque do Bling
            </button>
            <button class="btn btn-sm" :disabled="auditLoading" @click="loadAudit">
              <RefreshCw class="h-3.5 w-3.5 mr-1" :class="{ 'animate-spin': auditLoading }" /> Atualizar
            </button>
            <button class="btn btn-sm" @click="auditHidden = true">Ocultar</button>
          </div>
        </div>

        <div v-if="auditError" class="mt-2 text-xs text-red-700">{{ auditError }}</div>

        <div v-if="auditPending.length" class="mt-3 overflow-auto rounded border border-amber-200 bg-background max-h-[420px]">
          <table class="w-full text-xs">
            <thead class="bg-amber-100/60 sticky top-0">
              <tr class="text-left">
                <th class="px-2 py-1.5 font-semibold">SKU</th>
                <th class="px-2 py-1.5 font-semibold">Produto</th>
                <th class="px-2 py-1.5 font-semibold text-right w-20">Estoque</th>
                <th class="px-2 py-1.5 font-semibold w-44">Contas</th>
                <th class="px-2 py-1.5 font-semibold">Pendências</th>
                <th class="px-2 py-1.5 font-semibold w-24"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in auditPending" :key="row.sku" class="border-t border-amber-100 align-top">
                <td class="px-2 py-1.5 font-mono">{{ row.sku }}</td>
                <td class="px-2 py-1.5">{{ row.title || '—' }}</td>
                <td class="px-2 py-1.5 text-right tabular-nums">{{ row.stock ?? 0 }}</td>
                <td class="px-2 py-1.5">
                  <span v-if="row.account_count === 0" class="text-red-600 font-medium">Nenhuma</span>
                  <span v-else>
                    {{ row.account_count }} ({{ row.accounts.slice(0, 3).join(', ') }}{{ row.accounts.length > 3 ? ', ...' : '' }})
                  </span>
                </td>
                <td class="px-2 py-1.5">
                  <div class="flex flex-col gap-1">
                    <div class="flex flex-wrap gap-1">
                      <span v-for="iss in row.issues" :key="iss" :class="issueBadgeClass(iss)">{{ iss }}</span>
                    </div>
                    <div v-if="row.issues.includes('Custo divergente')" class="text-[10px] text-orange-700">
                      Bling: R$ {{ row.bling_cost ?? '—' }} · Tabela: R$ {{ row.pricing_cost ?? '—' }}
                      <span v-if="row.divergent_departments?.length" class="text-orange-500">
                        ({{ row.divergent_departments.join(', ') }})
                      </span>
                    </div>
                  </div>
                </td>
                <td class="px-2 py-1.5 text-right">
                  <button class="btn btn-xs" @click="dismissAuditSku(row.sku)">Dispensar</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="auditDismissed.length" class="mt-3">
          <button class="text-xs text-amber-800 underline" @click="auditShowDismissed = !auditShowDismissed">
            {{ auditShowDismissed ? 'Ocultar' : 'Dispensados' }} ({{ auditDismissed.length }})
          </button>
          <div v-if="auditShowDismissed" class="mt-2 overflow-auto rounded border border-amber-200 bg-background max-h-[280px]">
            <table class="w-full text-xs">
              <thead class="bg-amber-100/40 sticky top-0">
                <tr class="text-left">
                  <th class="px-2 py-1.5 font-semibold">SKU</th>
                  <th class="px-2 py-1.5 font-semibold">Produto</th>
                  <th class="px-2 py-1.5 font-semibold">Pendências</th>
                  <th class="px-2 py-1.5 font-semibold w-24"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in auditDismissed" :key="row.sku" class="border-t border-amber-100">
                  <td class="px-2 py-1.5 font-mono">{{ row.sku }}</td>
                  <td class="px-2 py-1.5">{{ row.title || '—' }}</td>
                  <td class="px-2 py-1.5">
                    <span v-for="iss in row.issues" :key="iss" :class="issueBadgeClass(iss)" class="mr-1">{{ iss }}</span>
                  </td>
                  <td class="px-2 py-1.5 text-right">
                    <button class="btn btn-xs" @click="undismissAuditSku(row.sku)">Restaurar</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 class="text-base font-semibold">
            Produtos — {{ DEPARTMENTS.find(d => d.value === department)?.label }}
          </h3>
          <p class="text-xs text-muted-foreground">
            {{ productsCurrent.length }} produto(s) — clique para editar
          </p>
        </div>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <!-- MEGA: três controles visíveis no lugar do antigo "Fotos (MEGA)",
               que escondia login, vínculo e contagem atrás de um clique só. -->
          <template v-if="canEditProdutos">
            <span
              v-if="megaStatus === 'verificando' || megaStatus == null"
              class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs text-muted-foreground"
            >
              <Loader2 class="h-3 w-3 animate-spin" /> Verificando MEGA…
            </span>
            <span
              v-else-if="megaStatus === 'conectado'"
              class="inline-flex items-center gap-1.5 rounded-full border border-emerald-300 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200"
              :title="megaStatusInfo ? `Conta: ${megaStatusInfo}` : undefined"
            >
              <span class="h-1.5 w-1.5 rounded-full bg-emerald-500"></span> MEGA conectado
            </span>
            <button
              v-else-if="megaStatus === 'desconectado'"
              class="inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
              title="Entrar na conta MEGA (feito uma vez; a sessão fica salva no servidor)"
              @click="abrirMegaLogin(null)"
            >
              <span class="h-1.5 w-1.5 rounded-full bg-amber-500"></span> MEGA desconectado — Conectar
            </button>
            <button
              v-else
              class="inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              :title="`${megaStatusInfo || 'serviço do MEGA não respondeu'} — clique para verificar de novo`"
              @click="carregarMegaStatus"
            >
              <span class="h-1.5 w-1.5 rounded-full bg-slate-400"></span> MEGA fora do ar
            </button>
            <button
              class="btn btn-sm"
              :disabled="megaBusy"
              title="Procura no MEGA uma pasta com o nome de cada produto sem pasta e mostra a prévia antes de gravar"
              @click="megaSyncClick"
            >
              <Loader2 v-if="megaBusy" class="h-4 w-4 mr-1 animate-spin" />
              <Link2 v-else class="h-4 w-4 mr-1" /> Vincular pastas pelo nome…
            </button>
            <button
              class="btn btn-sm"
              :disabled="megaCountsBusy"
              title="Conta de novo as fotos, vídeos e embalagens de todas as pastas do MEGA"
              @click="megaCountsRefresh()"
            >
              <Loader2 v-if="megaCountsBusy" class="h-4 w-4 mr-1 animate-spin" />
              <RefreshCw v-else class="h-4 w-4 mr-1" />
              {{ megaCountsBusy ? 'Recontando…' : 'Recontar fotos e embalagens' }}
            </button>
          </template>
          <button v-if="canEditProdutos" class="btn btn-sm btn-primary" :disabled="showAddProd" @click="openAddProd">
            <Plus class="h-4 w-4 mr-1" /> Adicionar Produto
          </button>
        </div>
      </div>

      <!-- barra de filtros: busca + filtro de mídias (rotulado, sempre à vista) -->
      <div class="flex flex-wrap items-center gap-3">
        <input
          v-model="searchProdutos"
          placeholder="buscar SKU, nome, EAN…"
          class="border rounded px-2 py-1 text-sm bg-background w-56"
        />
        <label class="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          Mídias:
          <select
            v-model="filtroMidias"
            class="border rounded px-2 py-1 text-sm bg-background text-foreground"
            :class="filtroMidias ? 'border-blue-400 font-medium' : ''"
          >
            <option value="">Todas</option>
            <option value="sem_pasta">Sem pasta ({{ contagemFiltroMidias.sem_pasta }})</option>
            <option value="sem_fotos">Sem fotos ({{ contagemFiltroMidias.sem_fotos }})</option>
            <option value="sem_embalagem">Sem embalagem ({{ contagemFiltroMidias.sem_embalagem }})</option>
          </select>
        </label>
        <button v-if="filtroMidias" class="text-xs text-muted-foreground underline" @click="filtroMidias = ''">
          limpar filtro
        </button>
      </div>

      <div v-if="productsErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ productsErr }}
      </div>

      <div class="border rounded-lg overflow-auto max-h-[calc(100vh-320px)]">
        <table class="w-full text-sm border-collapse">
          <thead class="sticky top-0 bg-muted z-10">
            <!-- Cabeçalho no formato da Tabela de Preços (M-9): 11px, peso 600,
                 linha de 14px. SKU, Produto e EAN à esquerda; o resto centralizado. -->
            <tr>
              <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[120px]">SKU</th>
              <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[200px]">Produto</th>
              <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[100px]">EAN</th>
              <template v-if="department === 'mala'">
                <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border">Descrição</th>
                <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border">Modelo</th>
              </template>
              <template v-if="mostraCertificado">
                <th
                  class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[90px]"
                  title="Modelo do certificado: definido em Importação › Certificações (coluna SKU, o SKU sem a tag). O Nº e a Validade vêm junto."
                  data-col-cert-modelo
                >Modelo</th>
                <th
                  class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[120px]"
                  title="Número do certificado (Anatel/Inmetro/Anvisa), copiado da Certificações."
                >Nº certificado</th>
                <th
                  class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-24"
                  title="Quantos dias faltam para o certificado vencer (coluna Validade da Certificações; a data aparece ao passar o mouse). Vermelho = vencido; âmbar = vence em menos de 30 dias. Clique para mudar a data: grava na Validade do certificado e vale para todos os produtos dele."
                  data-col-cert-validade
                >Validade</th>
              </template>
              <!-- Kit (M-9): coluna de ~98px; descrição em 11px, até 2 linhas. -->
              <th v-for="k in kitCount" :key="`kith-${k}`" class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-[98px] min-w-[98px]" :title="kitTitulo(k)">
                Kit {{ k }}
                <span v-if="kitNome(k)" class="block text-[11px] font-normal text-muted-foreground leading-[14px] whitespace-normal line-clamp-2">{{ kitNome(k) }}</span>
              </th>
              <th
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-24"
                title="Preço base do anúncio de catálogo do Mercado Livre (R$), um por linha. As colunas do grupo ML Catálogo da Tabela de Preços somam margem, frete e comissão da conta de kit sobre este valor. Vazio = sem preço de catálogo (não envia)."
              >Catálogo</th>
              <th
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-24"
                title="Prazo da garantia do produto, em meses (0 = sem garantia). Aumentar vale para todas as vendas; diminuir só para as vendas a partir da mudança; corrigir no mesmo dia desfaz o valor anterior. Produto cujo certificado tem garantia: o prazo vem do certificado, e mudar aqui muda para todos os produtos dele."
                data-col-garantia-meses
              >Garantia (meses)</th>
              <th
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-28"
                title="Fotos e vídeos do produto no MEGA. Clique na etiqueta para ver as fotos, enviar mais, baixar ou trocar a pasta."
              >Fotos</th>
              <th
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-28"
                title="Fotos da caixa, PDF da arte e outros arquivos de embalagem. Ficam na subpasta Embalagens, dentro da pasta de fotos do produto no MEGA, e não aparecem para as agências."
              >Embalagens</th>
              <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-24">Tabela</th>
              <th
                class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-20"
                title="Tag de estoque prioritária: venda que sair em outra tag é trocada pra esta ANTES da nota fiscal (só se o SKU com a tag existir no Bling e tiver saldo). Vazio = nada muda."
              >Prioridade</th>
              <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-12"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="productsLoading && !products.length">
              <td colSpan="999" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!productsCurrent.length && !showAddProd">
              <td colSpan="999" class="text-center py-6 text-muted-foreground">
                {{ filtroMidias || searchProdutos.trim() ? 'Nenhum produto com esse filtro.' : 'Nenhum produto neste departamento.' }}
              </td>
            </tr>

            <!-- add row -->
            <tr v-if="showAddProd" class="bg-blue-50/40 dark:bg-blue-900/10">
              <td class="border border-border px-1 py-1">
                <input
                  id="new-prod-sku"
                  v-model="newProd.sku"
                  type="text" placeholder="SKU"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                  @keydown.enter="submitNewProd"
                  @keydown.escape="showAddProd = false"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model="newProd.name"
                  type="text" placeholder="Nome"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                  @keydown.enter="submitNewProd"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model="newProd.ean"
                  type="text" placeholder="EAN"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                />
              </td>
              <template v-if="department === 'mala'">
                <td class="border border-border px-1 py-1">
                  <input v-model="newProd.description" type="text" placeholder="Descrição" class="w-full text-xs border rounded px-1.5 py-1 bg-background" />
                </td>
                <td class="border border-border px-1 py-1">
                  <input v-model="newProd.model" type="text" placeholder="Modelo" class="w-full text-xs border rounded px-1.5 py-1 bg-background" />
                </td>
              </template>
              <!-- Certificado: vem sozinho pelo código no nome, depois de salvar. -->
              <template v-if="mostraCertificado">
                <td v-for="i in 3" :key="`newcert-${i}`" class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              </template>
              <td v-for="k in kitCount" :key="`newkit-${k}`" class="border border-border px-1 py-1">
                <input v-model="(newProd as any)[`cost_kit${k}`]" type="text" inputmode="decimal" :placeholder="k === 1 ? '0.00' : ''" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-center" />
              </td>
              <td class="border border-border px-1 py-1">
                <input v-model="newProd.preco_catalogo" type="text" inputmode="decimal" placeholder="—" title="Preço de catálogo (opcional)" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-center" />
              </td>
              <!-- Garantia (meses): define depois, na linha criada. -->
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <!-- Fotos e Embalagens: enviadas depois, pelo painel da linha criada. -->
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1">
                <select v-model.number="newProd.product_type" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-center">
                  <option
                    v-for="(label, i) in (TYPE_HEADERS[department] ?? [])"
                    :key="i"
                    :value="i + 1"
                  >
                    {{ label }}
                  </option>
                </select>
              </td>
              <!-- Prioridade: define depois, na linha criada. -->
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1 text-center">
                <div class="flex gap-0.5 justify-center">
                  <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" :disabled="addingProd" @click="submitNewProd">
                    <Loader2 v-if="addingProd" class="h-3 w-3 animate-spin" />
                    <Check v-else class="h-3 w-3" />
                  </button>
                  <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="showAddProd = false">
                    <X class="h-3 w-3" />
                  </button>
                </div>
              </td>
            </tr>

            <!-- data rows -->
            <!-- Todas as linhas com 33px (M-9): nada quebra linha nas
                 células; texto longo é cortado com reticências. -->
            <tr v-for="p in productsCurrent" :key="p.id" class="h-[33px] whitespace-nowrap hover:bg-accent/30" :class="{ 'opacity-50': !p.is_active }">
              <td
                class="border border-border px-2 py-1.5 text-xs font-mono cursor-pointer max-w-[220px]"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'sku'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'sku'),
                }"
                :title="p.sku"
                @click="!isEditing(p.id, 'sku') && startEditProduct(p, 'sku')"
              >
                <input
                  v-if="isEditing(p.id, 'sku')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none font-mono"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else class="block truncate">{{ p.sku.length > 20 ? p.sku.slice(0, 20) + '…' : p.sku }}</span>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'name'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'name') }"
                @click="!isEditing(p.id, 'name') && startEditProduct(p, 'name')"
              >
                <input
                  v-if="isEditing(p.id, 'name')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else class="block max-w-[320px] truncate" :title="p.name">{{ p.name }}</span>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs font-mono cursor-pointer max-w-[220px]"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'ean'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'ean') }"
                :title="p.ean || undefined"
                @click="!isEditing(p.id, 'ean') && startEditProduct(p, 'ean')"
              >
                <input
                  v-if="isEditing(p.id, 'ean')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none font-mono"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <!-- EAN aceita até 1000 chars (vários códigos); truncado na célula,
                     completo no hover e no clique-para-editar. -->
                <span v-else class="block truncate">{{ p.ean || '—' }}</span>
              </td>
              <template v-if="department === 'mala'">
                <td
                  class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                  :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'description'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'description') }"
                  @click="!isEditing(p.id, 'description') && startEditProduct(p, 'description')"
                >
                  <input
                    v-if="isEditing(p.id, 'description')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else class="mx-auto block max-w-[260px] truncate" :title="p.description || undefined">{{ p.description || '—' }}</span>
                </td>
                <td
                  class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                  :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'model'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'model') }"
                  @click="!isEditing(p.id, 'model') && startEditProduct(p, 'model')"
                >
                  <input
                    v-if="isEditing(p.id, 'model')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else class="mx-auto block max-w-[200px] truncate" :title="p.model || undefined">{{ p.model || '—' }}</span>
                </td>
              </template>
              <template v-if="mostraCertificado">
                <!-- Modelo (M-69): só leitura; o certificado é definido em
                     Importação › Certificações (coluna SKU). Conflito = os
                     códigos da linha em certificados diferentes: selo laranja
                     com a explicação no balão. -->
                <td
                  class="border border-border px-2 py-1.5 text-xs text-center whitespace-nowrap"
                  :class="{ 'text-muted-foreground': !p.certificacao }"
                  :title="certModeloTitulo(p)"
                  data-cert-modelo
                >
                  <span class="inline-flex items-center gap-1">
                    <span>{{ certModelo(p) }}</span>
                    <span
                      v-if="certConflitoTitulo(p)"
                      class="rounded px-1 text-[10px] leading-4 bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                      :title="certConflitoTitulo(p)"
                      data-cert-conflito
                    >conflito</span>
                  </span>
                </td>
                <td
                  class="border border-border px-2 py-1.5 text-xs font-mono whitespace-nowrap text-center"
                  :class="{ 'text-muted-foreground': !p.certificacao?.numero }"
                  :title="certTitulo(p.certificacao)"
                  data-cert-numero
                >{{ certNumero(p.certificacao) }}</td>
                <!-- Validade (06/11/2026): editável por quem edita produtos,
                     quando o produto tem certificado. Mostra os dias; ao
                     clicar edita a data Fim DO CERTIFICADO (vale para todos
                     os produtos dele). Sem certificado: só leitura. -->
                <td
                  class="border border-border px-2 py-1.5 text-xs whitespace-nowrap text-center tabular-nums"
                  :class="[
                    p.certificacao?.fim ? certValidadeClasse(p.certificacao) : 'text-muted-foreground',
                    {
                      'cursor-pointer': canEditProdutos && !!p.certificacao,
                      'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'certificado_fim'),
                      'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'certificado_fim'),
                    },
                  ]"
                  :title="validadeTitulo(p)"
                  data-cert-validade
                  @click="canEditProdutos && p.certificacao && !isEditing(p.id, 'certificado_fim') && startEditProduct(p, 'certificado_fim')"
                >
                  <input
                    v-if="isEditing(p.id, 'certificado_fim')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="date"
                    class="w-full min-w-[118px] text-xs bg-transparent outline-none text-center"
                    data-cert-validade-data
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <template v-else>{{ certValidade(p.certificacao) }}</template>
                </td>
              </template>
              <td
                v-for="k in kitCount" :key="`kit-${k}`"
                class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, `cost_kit${k}`),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, `cost_kit${k}`),
                }"
                @click="!isEditing(p.id, `cost_kit${k}`) && startEditProduct(p, `cost_kit${k}`)"
              >
                <input
                  v-if="isEditing(p.id, `cost_kit${k}`)"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text" inputmode="decimal"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ fmtBRL((p as any)[`cost_kit${k}`]) }}</span>
              </td>
              <!-- Catálogo: preço base do anúncio de catálogo do ML; edita igual
                   aos kits. Vazio = "—" (sem preço de catálogo). -->
              <td
                class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'preco_catalogo'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'preco_catalogo'),
                }"
                @click="!isEditing(p.id, 'preco_catalogo') && startEditProduct(p, 'preco_catalogo')"
              >
                <input
                  v-if="isEditing(p.id, 'preco_catalogo')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text" inputmode="decimal"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else :class="{ 'text-muted-foreground': p.preco_catalogo == null || p.preco_catalogo === '' }">{{ fmtBRL(p.preco_catalogo) }}</span>
              </td>
              <!-- Garantia (meses), 06/11/2026: prazo único do produto, em
                   todos os segmentos. Edita quem edita produtos (mesmo PATCH
                   das outras células); vazio = "—" (sem prazo cadastrado);
                   0 = "sem garantia" (o avulso). Produto cujo certificado
                   tem garantia: o servidor grava no certificado e o prazo
                   vale para todos os produtos dele (title avisa). -->
              <td
                class="border border-border px-2 py-1.5 text-xs text-center"
                :class="{
                  'cursor-pointer': canEditProdutos,
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'garantia_meses'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'garantia_meses'),
                }"
                :title="garantiaTitulo(p)"
                data-col-garantia-meses
                @click="canEditProdutos && !isEditing(p.id, 'garantia_meses') && startEditProduct(p, 'garantia_meses')"
              >
                <input
                  v-if="isEditing(p.id, 'garantia_meses')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text" inputmode="numeric"
                  placeholder="meses"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else :class="{ 'text-muted-foreground': p.garantia_meses == null || p.garantia_meses === 0 }">{{ p.garantia_meses == null ? '—' : p.garantia_meses === 0 ? 'sem garantia' : p.garantia_meses }}</span>
              </td>
              <!-- Fotos: etiqueta com texto (quantas fotos/vídeos, pasta vazia,
                   sem pasta). O clique abre o painel de mídias na aba Fotos —
                   ver, enviar, baixar e trocar a pasta moram lá. -->
              <td class="border border-border px-1 py-1 text-center">
                <span
                  v-if="midiasEnviandoId === p.id"
                  :class="classeEtiquetaMidia('enviando')"
                >
                  <span class="inline-flex items-center gap-1">
                    <Loader2 class="h-3 w-3 animate-spin" /> Enviando…
                  </span>
                </span>
                <span
                  v-else-if="semPasta(p) && !canEditProdutos"
                  class="text-xs text-muted-foreground"
                >—</span>
                <button
                  v-else
                  type="button"
                  :class="classeEtiquetaMidia(rotuloFotos(p).tom)"
                  :title="p.fotos_path ? `Pasta no MEGA: ${p.fotos_path}` : semPasta(p) ? 'Enviar as primeiras fotos ou escolher uma pasta do MEGA' : 'Abrir as fotos deste produto'"
                  @click="abrirMidias(p, 'fotos')"
                >
                  <span>{{ rotuloFotos(p).linha1 }}</span>
                  <span v-if="rotuloFotos(p).linha2" class="text-[10px] font-normal">{{ rotuloFotos(p).linha2 }}</span>
                </button>
              </td>
              <!-- Embalagens: subpasta "Embalagens" dentro da pasta de fotos.
                   Mesma etiqueta; o clique abre o painel na aba Embalagens. -->
              <td class="border border-border px-1 py-1 text-center">
                <span
                  v-if="!p.embalagens_path && !canEditProdutos"
                  class="text-xs text-muted-foreground"
                >—</span>
                <button
                  v-else
                  type="button"
                  :class="classeEtiquetaMidia(rotuloEmbalagens(p).tom)"
                  :title="p.embalagens_path ? `Pasta no MEGA: ${p.embalagens_path}` : 'Enviar fotos da caixa, PDF da arte etc.'"
                  @click="abrirMidias(p, 'embalagens')"
                >
                  {{ rotuloEmbalagens(p).texto }}
                </button>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'product_type'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'product_type') }"
                @click="!isEditing(p.id, 'product_type') && startEditProduct(p, 'product_type')"
              >
                <select
                  v-if="isEditing(p.id, 'product_type')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  class="w-full text-xs bg-background outline-none text-center"
                  @blur="commitEditProduct" @change="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                >
                  <option
                    v-for="(label, i) in (TYPE_HEADERS[p.department as DeptKey] ?? [])"
                    :key="i"
                    :value="String(i + 1)"
                  >
                    {{ label }}
                  </option>
                </select>
                <span v-else class="inline-block px-2 py-0.5 rounded text-[10px] font-medium" :class="tabelaBadgeClass(p)">
                  {{ tabelaName(p) }}
                </span>
              </td>
              <!-- Prioridade de estoque: select direto (sem modo edição) —
                   troca o SKU do pedido pra esta tag antes da NF. -->
              <td
                class="border border-border px-1 py-1 text-center"
                :class="{ 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'prioridade_estoque') }"
              >
                <select
                  v-if="canEditProdutos"
                  class="text-xs border rounded px-1 py-0.5 bg-background text-center cursor-pointer"
                  :class="p.prioridade_estoque ? 'font-semibold text-amber-700 dark:text-amber-400 border-amber-400' : 'text-muted-foreground'"
                  :value="p.prioridade_estoque || ''"
                  :title="p.prioridade_estoque
                    ? `Prioridade ${p.prioridade_estoque.toUpperCase()}: venda em outra tag é trocada pra .${p.prioridade_estoque} antes da NF (se existir no Bling com saldo)`
                    : 'Sem prioridade — escolha uma tag pra trocar as vendas deste produto pra ela'"
                  @change="setPrioridade(p, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="" :selected="!p.prioridade_estoque">—</option>
                  <option
                    v-for="t in TAGS_PRIORIDADE"
                    :key="t"
                    :value="t"
                    :selected="t === p.prioridade_estoque"
                  >{{ t.toUpperCase() }}</option>
                </select>
                <span v-else class="text-xs" :class="p.prioridade_estoque ? 'font-semibold text-amber-700 dark:text-amber-400' : 'text-muted-foreground'">
                  {{ p.prioridade_estoque ? p.prioridade_estoque.toUpperCase() : '—' }}
                </span>
              </td>
              <td class="border border-border px-1 py-1 text-center">
                <button
                  v-if="canDeleteProdutos"
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  :title="`Excluir ${p.sku}`"
                  @click="deleteProduct(p)"
                >
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- MEGA: painel de mídias do produto (fotos, vídeos, embalagens) -->
      <MidiasProdutoDrawer
        v-if="midiasProduto"
        :produto="midiasProduto"
        :aba-inicial="midiasAba"
        :pode-editar="canEditProdutos"
        :irmaos="irmaosDe(midiasProduto)"
        :aplicar="aplicarMidias"
        @enviando="onMidiasEnviando"
        @mega-desconectado="carregarMegaStatus"
        @fechar="fecharMidias"
      />

      <!-- MEGA: modal de login -->
      <div
        v-if="showMegaLogin"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showMegaLogin = false"
      >
        <div class="bg-background border rounded-lg shadow-xl w-full max-w-sm p-4 space-y-3">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <Camera class="h-4 w-4" /> Conectar conta MEGA
          </h3>
          <p class="text-xs text-muted-foreground">
            Login feito uma única vez — a sessão fica salva no servidor.
            A senha não é armazenada.
          </p>
          <input
            v-model="megaLoginForm.email"
            type="email" placeholder="email do MEGA"
            class="w-full border rounded px-2 py-1.5 text-sm bg-background"
          />
          <input
            v-model="megaLoginForm.password"
            type="password" placeholder="senha do MEGA"
            class="w-full border rounded px-2 py-1.5 text-sm bg-background"
            @keydown.enter="submitMegaLogin"
          />
          <input
            v-model="megaLoginForm.code"
            type="text" placeholder="código 2FA (só se a conta tiver)"
            class="w-full border rounded px-2 py-1.5 text-sm bg-background"
            @keydown.enter="submitMegaLogin"
          />
          <p v-if="megaLoginErr" class="text-xs text-destructive">{{ megaLoginErr }}</p>
          <div class="flex justify-end gap-2">
            <button class="btn btn-sm" @click="showMegaLogin = false">Cancelar</button>
            <button
              class="btn btn-sm btn-primary"
              :disabled="megaLoginBusy || !megaLoginForm.email || !megaLoginForm.password"
              @click="submitMegaLogin"
            >
              <Loader2 v-if="megaLoginBusy" class="h-4 w-4 mr-1 animate-spin" /> Conectar
            </button>
          </div>
        </div>
      </div>

      <!-- MEGA: modal de prévia da sincronização -->
      <div
        v-if="showMegaPreview && megaPreview"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showMegaPreview = false"
      >
        <div class="bg-background border rounded-lg shadow-xl w-full max-w-2xl p-4 space-y-3 max-h-[85vh] overflow-auto">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <Link2 class="h-4 w-4" /> Vincular pastas pelo nome — prévia
          </h3>
          <p class="text-xs text-muted-foreground">
            {{ megaPreview.folders_total }} pasta(s) no MEGA ·
            {{ megaPreview.matched_total }} produto(s) casaram pelo nome ·
            <b class="text-foreground">{{ megaPreview.to_apply }}</b> vão ser ligados à pasta agora
            (quem já tem pasta não muda). Nada é gravado antes de você clicar em "Ligar".
          </p>
          <p v-if="!megaPreview.folders_total && canEditProdutos" class="text-xs text-amber-700">
            A conta MEGA ainda está sem pastas — use "Criar pastas no MEGA" pra montar a
            estrutura (marca → uma pasta por modelo) a partir dos seus produtos.
          </p>
          <div v-if="megaPreview.to_apply" class="border rounded max-h-56 overflow-auto">
            <table class="w-full text-xs">
              <tbody>
                <tr
                  v-for="m in megaPreview.matched.filter((x: any) => !x.has_url)"
                  :key="m.sku"
                  class="border-b border-border/50"
                >
                  <td class="px-2 py-1 font-mono whitespace-nowrap">{{ m.sku }}</td>
                  <td class="px-2 py-1">{{ m.name }}</td>
                  <td class="px-2 py-1 text-muted-foreground">📁 {{ m.folder }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <details v-if="megaPreview.ambiguous?.length" class="text-xs">
            <summary class="cursor-pointer text-amber-700">
              {{ megaPreview.ambiguous.length }} produto(s) com mais de uma pasta possível
              (escolha no painel do produto: clique na coluna Fotos › "Escolher pasta existente…")
            </summary>
            <ul class="mt-1 pl-4 list-disc space-y-0.5">
              <li v-for="a in megaPreview.ambiguous" :key="a.sku">
                {{ a.name }} → {{ a.candidates.join(' | ') }}
              </li>
            </ul>
          </details>
          <details v-if="megaPreview.unmatched_products?.length" class="text-xs">
            <summary class="cursor-pointer text-muted-foreground">
              {{ megaPreview.unmatched_products.length }} produto(s) sem pasta no MEGA
            </summary>
            <ul class="mt-1 pl-4 list-disc space-y-0.5">
              <li v-for="u in megaPreview.unmatched_products" :key="u.sku">{{ u.name }}</li>
            </ul>
          </details>
          <details v-if="megaPreview.unmatched_folders?.length" class="text-xs">
            <summary class="cursor-pointer text-muted-foreground">
              {{ megaPreview.unmatched_folders.length }} pasta(s) do MEGA sem produto
            </summary>
            <ul class="mt-1 pl-4 list-disc space-y-0.5">
              <li v-for="(f, i) in megaPreview.unmatched_folders" :key="i">{{ f }}</li>
            </ul>
          </details>
          <div class="flex items-center justify-between gap-2 pt-1">
            <div v-if="canEditProdutos" class="flex gap-2">
              <button class="btn btn-sm" @click="openMegaScaffold">
                <FolderPlus class="h-4 w-4 mr-1" /> Criar pastas no MEGA…
              </button>
              <button
                class="btn btn-sm"
                :disabled="megaCountsBusy"
                title="Conta de novo as fotos, vídeos e embalagens de cada pasta do MEGA e mostra os números nas colunas Fotos e Embalagens"
                @click="megaCountsRefresh()"
              >
                <Loader2 v-if="megaCountsBusy" class="h-4 w-4 mr-1 animate-spin" />
                <RefreshCw v-else class="h-4 w-4 mr-1" />
                Recontar fotos e embalagens
              </button>
            </div>
            <span v-else></span>
            <div class="flex gap-2">
              <button class="btn btn-sm" @click="showMegaPreview = false">Fechar</button>
              <button
                class="btn btn-sm btn-primary"
                :disabled="megaApplyBusy || !megaPreview.to_apply"
                @click="megaApply"
              >
                <Loader2 v-if="megaApplyBusy" class="h-4 w-4 mr-1 animate-spin" />
                Ligar {{ megaPreview.to_apply }} produto(s)
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- MEGA: modal de criação da estrutura de pastas (marca/modelo) -->
      <div
        v-if="showMegaScaffold"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showMegaScaffold = false"
      >
        <div class="bg-background border rounded-lg shadow-xl w-full max-w-lg p-4 space-y-3 max-h-[85vh] overflow-auto">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <FolderPlus class="h-4 w-4" /> Criar pastas no MEGA
          </h3>
          <p class="text-xs text-muted-foreground">
            Cria a pasta do departamento com uma subpasta por modelo
            (ex.: /Malas/M2 listrada, /Celular/Fossibot S7) e já preenche o
            link de fotos de todos os produtos daquele modelo. As fotos e
            vídeos podem ser adicionados depois — o link continua o mesmo.
          </p>
          <label class="block text-xs">
            <span class="text-muted-foreground">Pasta principal (departamento)</span>
            <input
              v-model="megaScaffoldBrand"
              placeholder="Celular"
              class="mt-1 w-full border rounded px-2 py-1.5 text-sm bg-background"
            />
          </label>
          <label class="block text-xs">
            <span class="text-muted-foreground">
              Modelos — uma pasta por linha ({{ megaScaffoldCount }}); edite à vontade
            </span>
            <textarea
              v-model="megaScaffoldText"
              rows="12"
              class="mt-1 w-full border rounded px-2 py-1.5 text-xs font-mono bg-background"
            ></textarea>
          </label>
          <div class="flex justify-end gap-2 pt-1">
            <button class="btn btn-sm" @click="showMegaScaffold = false">Cancelar</button>
            <button
              class="btn btn-sm btn-primary"
              :disabled="megaScaffoldBusy || !megaScaffoldCount || !megaScaffoldBrand.trim()"
              @click="megaScaffoldCreate"
            >
              <Loader2 v-if="megaScaffoldBusy" class="h-4 w-4 mr-1 animate-spin" />
              Criar {{ megaScaffoldCount }} pasta(s)
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================================ TABELA DE PREÇOS (grid) -->
    <section v-else-if="tab === 'tabela'" class="space-y-3">
      <!-- Barra (M-2, 17/11/2026; M-8, 18/11/2026: Plataforma ▾ e Contas ▾
           com caixas; M-14, 18/11/2026): Buscar | Plataforma ▾ | Contas ▾ |
           Layout ▾ | Equipes ▾ (M-26) | Excel | Atualizar ▾ | Reposição [N] dias | Margem N% ▾.
           Todos com 28px (h-7) e o mesmo espaço. Recarregar, Enviar e
           Conferir anúncios viraram as opções do Atualizar ▾. Saíram antes
           Desfazer, Refazer, Custo Bling, Enviar relatório e a paleta Cor. -->
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative flex-1 min-w-[200px] max-w-sm">
          <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            v-model="gridSearch"
            placeholder="Buscar por SKU ou nome..."
            class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"
          />
        </div>
        <!-- Plataforma ▾ (M-8, 18/11/2026; antes um select): uma caixa por
             coluna de plataforma do departamento. Desmarcada = as colunas
             dela somem. Recarregar a página volta tudo marcado. -->
        <PopoverRoot>
          <PopoverTrigger as-child>
            <button type="button" class="btn btn-sm h-7 py-0" data-filtro="plataforma" title="Escolher as plataformas que a grade mostra">
              Plataforma
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-plataforma
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div class="flex items-center gap-2 px-1 pb-1 text-xs">
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="plataforma-marcar-todas" @click="marcarPlataformas(true)">Marcar todas</button>
                <span class="text-muted-foreground">·</span>
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="plataforma-limpar" @click="marcarPlataformas(false)">Limpar</button>
              </div>
              <div class="max-h-72 overflow-y-auto border-t pt-1">
                <label
                  v-for="o in gridPlatformOptions"
                  :key="o.value"
                  class="flex items-center gap-2 rounded px-1 py-1 text-sm"
                  :class="o.disabled ? 'cursor-not-allowed text-muted-foreground' : 'cursor-pointer hover:bg-muted/50'"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="!o.disabled && !plataformasDesmarcadas.has(o.value)"
                    :disabled="o.disabled"
                    :data-plataforma="o.value"
                    @change="alternarFiltro('plataforma', o.value, ($event.target as HTMLInputElement).checked)"
                  />
                  {{ o.label }}
                </label>
                <p v-if="!gridPlatformOptions.length" class="px-1 py-1 text-[11px] text-muted-foreground">
                  Nenhuma conta neste departamento.
                </p>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <!-- Contas ▾ (M-8): por nome de loja, sem repetir ("atlas" mostra
             todas as colunas da atlas). Combina com a Plataforma: a coluna só
             aparece com as duas marcadas. Filtro parcial: "Contas (2)". -->
        <PopoverRoot>
          <PopoverTrigger as-child>
            <button type="button" class="btn btn-sm h-7 py-0" data-filtro="contas" title="Escolher as contas que a grade mostra">
              {{ rotuloContas }}
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-contas
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div class="relative px-1 pb-1">
                <Search class="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <input
                  v-model="buscaContas"
                  placeholder="Buscar conta…"
                  class="h-7 w-full border rounded pl-7 pr-2 text-sm bg-background"
                  data-campo="contas-busca"
                />
              </div>
              <div class="flex items-center gap-2 px-1 pb-1 text-xs">
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="contas-marcar-todas" @click="marcarContas(true)">Marcar todas</button>
                <span class="text-muted-foreground">·</span>
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="contas-limpar" @click="marcarContas(false)">Limpar</button>
              </div>
              <div class="max-h-72 overflow-y-auto border-t pt-1">
                <label
                  v-for="o in gridContaOptionsBuscadas"
                  :key="o.value"
                  class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="!contasDesmarcadas.has(o.value)"
                    :data-conta="o.value"
                    @change="alternarFiltro('conta', o.value, ($event.target as HTMLInputElement).checked)"
                  />
                  <span class="truncate">{{ o.label }}</span>
                </label>
                <p v-if="!gridContaOptionsBuscadas.length" class="px-1 py-1 text-[11px] text-muted-foreground">
                  {{ buscaContas.trim() ? 'Nenhuma conta encontrada.' : 'Nenhuma conta neste departamento.' }}
                </p>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <!-- Layout ▾ (era "Mostrar kits"): para todos que veem a Tabela. Kit só
             no Celular (Mala e Eletro não têm kits). Lembrado neste navegador. -->
        <PopoverRoot>
          <PopoverTrigger as-child>
            <button type="button" class="btn btn-sm h-7 py-0" data-acao="layout" title="Escolher as colunas que a grade mostra">
              Layout
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-layout
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <!-- Só a caixa e o nome (M-61): a explicação fica na Ajuda ("?"). -->
              <label
                v-if="department === 'celular'"
                class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
              >
                <input v-model="mostrarKits" type="checkbox" class="size-4" data-campo="layout-kit" />
                <span>Kit</span>
              </label>
              <label class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
                <input v-model="mostrarEstoque" type="checkbox" class="size-4" data-campo="layout-estoque" />
                <span>Estoque</span>
              </label>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <!-- Equipes ▾ (M-26, 23/11/2026): 1, 2 e 3, no visual do Plataforma e
             do Contas. Soma com os dois: a coluna só aparece passando nos três.
             Filtro parcial: "Equipes (1)". Sem a lista da aba Contas (quem não
             tem permissão de ver Contas, ex. churchill) não há a equipe de cada
             coluna: o botão não aparece, senão desmarcar esvaziaria a grade. -->
        <PopoverRoot v-if="!accountsErr">
          <PopoverTrigger as-child>
            <button type="button" class="btn btn-sm h-7 py-0" data-filtro="equipes" title="Escolher as equipes cujas contas a grade mostra">
              {{ rotuloFiltroEquipes }}
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-equipes
              class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div class="flex items-center gap-2 px-1 pb-1 text-xs">
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="equipes-marcar-todas" @click="marcarEquipes(true)">Marcar todas</button>
                <span class="text-muted-foreground">·</span>
                <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="equipes-limpar" @click="marcarEquipes(false)">Limpar</button>
              </div>
              <div class="max-h-72 overflow-y-auto border-t pt-1">
                <label
                  v-for="o in gridEquipeOptions"
                  :key="o.value"
                  class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="!equipesDesmarcadas.has(o.value)"
                    :data-equipe="o.value"
                    @change="alternarFiltro('equipe', o.value, ($event.target as HTMLInputElement).checked)"
                  />
                  {{ o.label }}
                </label>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <button class="btn btn-sm h-7 py-0" :disabled="!grid?.products.length" @click="handleExportExcel">
          <Download class="h-4 w-4 mr-1" /> Excel
        </button>
        <!-- Atualizar ▾ (M-14, 18/11/2026; era o Enviar ▾, e ganhou o que
             faziam Recarregar e Conferir anúncios). Toda vez que abre:
             só "Atualizar planilha" marcado e Contas em Geral — ninguém envia
             preço sem querer. Opção sem permissão não aparece. -->
        <PopoverRoot :open="painelAtualizarAberto" @update:open="abrirPainelAtualizar">
          <PopoverTrigger as-child>
            <button
              type="button"
              class="btn btn-sm h-7 py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white"
              data-acao="atualizar"
              :disabled="atualizando || pushing"
              :title="rotuloAtualizar === 'Atualizar' ? 'Atualizar a planilha, conferir os anúncios ou enviar o preço aos anúncios' : rotuloAtualizar"
            >
              <Loader2 v-if="atualizando || pushing || conferenciaRodando" class="h-4 w-4 mr-1 animate-spin" />
              <RefreshCw v-else class="h-4 w-4 mr-1" />
              {{ rotuloAtualizar }}
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-atualizar
              class="z-[70] w-[300px] max-w-[calc(100vw-16px)] rounded-md border bg-background py-1 shadow-lg"
            >
              <div class="flex h-[26px] items-center px-3 text-[11px] font-semibold text-muted-foreground">
                O que atualizar
              </div>
              <label
                class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50"
                title="Recarrega a tabela inteira"
              >
                <input v-model="atualizarPlanilha" type="checkbox" class="size-4 shrink-0" data-campo="atualizar-planilha" />
                Atualizar planilha
              </label>
              <label
                v-if="podeConferir"
                class="flex h-[26px] items-center gap-2 px-3 text-sm"
                :class="conferenciaIniciando || conferenciaRodando ? 'cursor-not-allowed text-muted-foreground' : 'cursor-pointer hover:bg-muted/50'"
              >
                <input
                  v-model="atualizarConferir"
                  type="checkbox"
                  class="size-4 shrink-0"
                  data-campo="atualizar-conferir"
                  :disabled="conferenciaIniciando || conferenciaRodando"
                />
                Conferir
                <span
                  class="ml-auto inline-flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground"
                  data-ultima-conferencia
                  :title="tituloConferir"
                >
                  {{ textoUltimaConferencia }}
                  <AlertCircle v-if="conferirComAviso" class="h-3.5 w-3.5 text-amber-600" data-aviso-conferencia />
                </span>
              </label>
              <label
                v-if="podeEnviarPreco"
                class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50"
                title="Envia o preço da Tabela aos anúncios das contas escolhidas"
              >
                <input v-model="atualizarAnuncio" type="checkbox" class="size-4 shrink-0" data-campo="atualizar-anuncio" />
                Atualizar anúncio
              </label>
              <div class="mt-1 flex h-[26px] items-center border-t px-3 text-[11px] font-semibold text-muted-foreground">
                Contas
              </div>
              <label
                class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50"
                title="As colunas que estão aparecendo pelos filtros Plataforma, Contas e Equipes"
              >
                <input
                  type="checkbox"
                  class="size-4 shrink-0"
                  data-campo="atualizar-geral"
                  :checked="atualizarModoContas === 'geral'"
                  @change="escolherModoContas('geral', ($event.target as HTMLInputElement).checked)"
                />
                Geral
                <span class="ml-auto shrink-0 text-[11px] text-muted-foreground">todas as visíveis</span>
              </label>
              <label class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50">
                <input
                  type="checkbox"
                  class="size-4 shrink-0"
                  data-campo="atualizar-por-conta"
                  :checked="atualizarModoContas === 'conta'"
                  @change="escolherModoContas('conta', ($event.target as HTMLInputElement).checked)"
                />
                Por conta
                <span class="ml-auto shrink-0 text-[11px] text-muted-foreground">{{ contasMarcadasAtualizar.length }} marcadas</span>
              </label>
              <template v-if="atualizarModoContas === 'conta'">
                <div class="relative px-3 py-1">
                  <Search class="absolute left-5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                  <input
                    v-model="buscaAtualizarContas"
                    placeholder="Buscar conta…"
                    class="h-7 w-full border rounded pl-7 pr-2 text-sm bg-background"
                    data-campo="atualizar-contas-busca"
                  />
                </div>
                <div class="max-h-60 overflow-y-auto" data-lista-atualizar-contas>
                  <template v-for="group in gruposAtualizarContas" :key="group.label">
                    <div class="flex h-[26px] items-center bg-muted/30 px-3 text-xs font-semibold text-muted-foreground">
                      {{ group.label }}
                    </div>
                    <label
                      v-for="acc in group.accounts"
                      :key="acc.id"
                      class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50"
                    >
                      <input
                        type="checkbox"
                        class="size-4 shrink-0"
                        :checked="atualizarContasMarcadas.has(acc.id)"
                        :data-conta-atualizar="acc.id"
                        @change="alternarContaAtualizar(acc.id, ($event.target as HTMLInputElement).checked)"
                      />
                      <span class="truncate">{{ nomeColuna(acc) }}</span>
                      <span class="ml-auto shrink-0 text-[10px] text-muted-foreground">
                        {{ ehCatalogo(acc) ? `${tipoDe(acc) || 'sem tipo'} · catálogo` : (acc.listing_type || acc.platform) }}
                      </span>
                    </label>
                  </template>
                  <p v-if="!gruposAtualizarContas.length" class="px-3 py-1 text-[11px] text-muted-foreground">
                    {{ buscaAtualizarContas.trim() ? 'Nenhuma conta encontrada.' : 'Nenhuma conta na tela.' }}
                  </p>
                </div>
              </template>
              <div class="mt-1 border-t px-3 pb-1 pt-2">
                <!-- Revisão M-14: o motivo à vista (o botão apagado não mostra o balão). -->
                <p
                  v-if="motivoAtualizarBloqueado && !atualizando && !pushing"
                  class="pb-1 text-[11px] text-muted-foreground"
                  data-motivo-atualizar
                >
                  {{ motivoAtualizarBloqueado }}
                </p>
                <button
                  type="button"
                  class="btn btn-sm h-7 w-full py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white"
                  data-acao="atualizar-executar"
                  :disabled="atualizando || pushing || !!motivoAtualizarBloqueado"
                  :title="motivoAtualizarBloqueado || 'Faz o que está marcado: primeiro o anúncio, depois a conferência, por último a planilha'"
                  @click="executarAtualizar"
                >
                  <RefreshCw class="h-4 w-4 mr-1" /> Atualizar
                </button>
              </div>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
        <!-- Reposição única (M-2): o MESMO prazo da Importação, por
             departamento — mudar aqui muda lá. Reposição = Estoque − vendas
             30d × Reposição ÷ 30. A explicação da conta está no balão.
             M-7 (18/11/2026): o campo é só do administrador; a coluna
             Reposição da grade continua para todos (Layout → Estoque). -->
        <label
          v-if="isAdmin"
          class="inline-flex items-center gap-1 text-sm"
          data-prazo-reposicao
          :title="tituloPrazos"
        >
          <span class="text-muted-foreground">Reposição</span>
          <input
            v-if="prazos"
            v-model.number="prazos.tempo_reposicao"
            type="number" min="0" max="999" step="1"
            class="h-7 w-16 border rounded px-2 text-right text-sm bg-background disabled:cursor-not-allowed disabled:opacity-60"
            data-campo="tempo-reposicao"
            :disabled="!prazos.pode_editar || salvandoPrazos"
            @change="salvarPrazos('tempo_reposicao')"
          />
          <input
            v-else
            type="text"
            :value="prazosErro ? '—' : '…'"
            class="h-7 w-16 border rounded px-2 text-right text-sm bg-background disabled:cursor-not-allowed disabled:opacity-60"
            data-campo="tempo-reposicao"
            disabled
          />
          <span class="text-muted-foreground">dias</span>
        </label>
        <!-- Margem N% ▾ (M-2): só administrador, a mesma trava de hoje. A
             margem fica no servidor (vale para todos); a do produto ganha da
             Geral. O (!) e o cartão vermelho usam a margem da linha. -->
        <PopoverRoot v-if="isAdmin" @update:open="(aberto: boolean) => { if (!aberto) gravarCampoMargemComFoco() }">
          <PopoverTrigger as-child>
            <button type="button" class="btn btn-sm h-7 py-0" data-acao="margem" :title="tituloMargem">
              {{ rotuloMargem }}
              <ChevronDown class="h-3 w-3 ml-1" />
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              side="bottom"
              align="start"
              :side-offset="4"
              :collision-padding="8"
              data-menu-margem
              class="z-[70] w-80 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
            >
              <div v-if="!margem" class="px-1 py-1 text-sm text-muted-foreground">
                {{ conferenciaEstado === 'erro' ? 'A margem não carregou — recarregue a página.' : 'carregando a margem…' }}
              </div>
              <template v-else>
                <div class="flex items-center gap-2 rounded px-1 py-1 text-sm">
                  <label class="flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      class="size-4"
                      data-campo="margem-geral-ligada"
                      :checked="margem.geral_ligada"
                      :disabled="!margem.pode_editar || salvandoMargem"
                      @change="salvarMargem({ geral_ligada: !margem.geral_ligada })"
                    />
                    Geral
                  </label>
                  <input
                    type="text"
                    inputmode="decimal"
                    class="h-7 w-16 border rounded px-2 text-right text-sm bg-background disabled:opacity-60"
                    data-campo="margem-geral"
                    :value="fmtPct(margem.margem_pct)"
                    :disabled="!margem.pode_editar || salvandoMargem"
                    @change="salvarMargemGeral"
                  />
                  %
                </div>
                <p class="px-1 pb-1 text-[11px] text-muted-foreground">
                  Vale para todos os produtos sem % próprio. Desmarcada: esses produtos não são conferidos.
                </p>
                <label class="flex cursor-pointer items-center gap-2 rounded border-t px-1 pb-1 pt-2 text-sm">
                  <input
                    type="checkbox"
                    class="size-4"
                    data-campo="margem-por-produto-ligada"
                    :checked="margem.por_produto_ligada"
                    :disabled="!margem.pode_editar || salvandoMargem"
                    @change="salvarMargem({ por_produto_ligada: !margem.por_produto_ligada })"
                  />
                  Por produto
                </label>
                <p class="px-1 pb-1 text-[11px] text-muted-foreground">
                  O produto com % próprio (ex.: com desconto) usa o dele; os outros usam a Geral. Vazio = usa a Geral.
                  Desmarcada: todos usam a Geral.
                </p>
                <div class="relative px-1 pb-1">
                  <Search class="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                  <input
                    v-model="buscaMargemProduto"
                    placeholder="Buscar produto por SKU ou nome…"
                    class="h-7 w-full border rounded pl-7 pr-2 text-sm bg-background"
                    data-campo="margem-busca-produto"
                  />
                </div>
                <div
                  class="max-h-64 overflow-y-auto px-1"
                  :class="{ 'opacity-60': !margem.por_produto_ligada }"
                  data-lista-margem-produto
                >
                  <div
                    v-for="p in [...produtosDaMargem.comPropria, ...produtosDaMargem.semPropria]"
                    :key="p.id"
                    class="flex items-center justify-between gap-2 py-0.5 text-xs"
                  >
                    <span class="truncate" :title="`${p.sku} — ${p.name}`">{{ p.name }}</span>
                    <span class="inline-flex shrink-0 items-center gap-1">
                      <input
                        type="text"
                        inputmode="decimal"
                        class="h-6 w-14 border rounded px-1 text-right text-xs bg-background disabled:opacity-60"
                        :placeholder="margem.geral_ligada ? fmtPct(margem.margem_pct) : '—'"
                        :value="margem.produtos[p.id] != null ? fmtPct(margem.produtos[p.id]) : ''"
                        :disabled="!margem.pode_editar || salvandoMargem"
                        :data-margem-produto="p.id"
                        @change="salvarMargemProduto(p.id, $event)"
                      />
                      %
                    </span>
                  </div>
                  <p
                    v-if="!produtosDaMargem.comPropria.length && !produtosDaMargem.semPropria.length"
                    class="py-1 text-[11px] text-muted-foreground"
                  >
                    {{ buscaMargemProduto.trim() ? 'Nenhum produto encontrado.' : 'Nenhum produto neste departamento.' }}
                  </p>
                </div>
                <div class="mt-1 border-t px-1 pt-1 text-[11px] text-muted-foreground">
                  Grava ao sair do campo, com Enter ou ao fechar; vale para todos.
                </div>
              </template>
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
      </div>

      <div
        v-if="isAdmin && negativeMarginCount > 0"
        data-aviso-margem-negativa
        class="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 flex items-center gap-2"
      >
        <AlertCircle class="h-4 w-4" />
        <strong>{{ negativeMarginCount }}</strong>
        combinação(ões) com margem negativa (preço &lt; custo + frete)
      </div>

      <div v-if="compareToast" class="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        {{ compareToast }}
      </div>

      <div v-if="gridErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ gridErr }}
      </div>

      <div v-if="lastPushResults.length" class="rounded border bg-muted/30 px-3 py-2 text-sm">
        <div class="font-medium mb-1">Resultado do push:</div>
        <ul class="space-y-0.5 max-h-40 overflow-y-auto">
          <li
            v-for="(r, idx) in lastPushResults"
            :key="idx"
            class="flex items-center gap-2"
            :class="r.ok ? 'text-emerald-700' : 'text-red-700'"
          >
            <span class="font-mono text-xs">{{ r.code }}</span>
            <span v-if="r.cached" class="text-xs px-1 rounded bg-amber-100 text-amber-900">cached</span>
            <span v-if="r.price">{{ Number(r.price).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) }}</span>
            <span v-if="r.detail" class="text-xs text-muted-foreground">{{ r.detail }}</span>
          </li>
        </ul>
      </div>

      <div
        class="overflow-auto rounded border max-h-[70vh] focus:outline-none"
        tabindex="0"
        @keydown="handleGridKeyDown"
      >
        <table class="text-xs border-collapse">
          <thead class="bg-background sticky top-0 z-20">
            <!-- Cabeçalho (M-8, 18/11/2026): todos os textos com 11px, peso 600,
                 altura de linha 14px, centralizados, e 24px (h-6) por linha.
                 Só "Produto" fica à esquerda. O colspan de cada grupo é o número
                 de colunas visíveis dele (filtros Plataforma e Contas). -->
            <!-- Row 1: Platform groups -->
            <tr>
              <th
                class="sticky left-0 bg-background h-6 px-2 py-0 z-30"
                :colspan="colunasFixas.length"
              />
              <th
                v-for="group in accountGroups"
                :key="`g-${group.label}`"
                :colspan="group.accounts.length"
                class="h-6 px-2 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] whitespace-nowrap border-l-[3px] border-gray-500"
                :class="grupoHeaderBg(group)"
                :title="group.catalogo ? 'Preço = coluna Catálogo do produto + frete e comissão da conta de kit; margem da conta de kit, ou a própria do catálogo quando preenchida (aba Contas, botão “% margens” do Catálogo ML). Envia só para o anúncio de catálogo; célula bloqueada não envia (aviãozinho apagado, motivo no balão da célula).' : undefined"
              >
                {{ group.label }}
              </th>
            </tr>
            <!-- Row 2: só o nome da conta, centralizado (M-3, 17/11/2026: saíram
                 as 6 anotações, o selo "catálogo" e o "margem própria" — os
                 dados continuam na aba Contas; M-8: saiu o aviãozinho — o envio
                 por conta fica na lista do botão Enviar). O espaço da esquerda
                 fica vazio: "Produto" está no cabeçalho da linha 3. -->
            <tr>
              <th
                class="sticky left-0 bg-background h-6 px-2 py-0 z-30"
                :colspan="colunasFixas.length"
              />
              <th
                v-for="acc in gridAccounts"
                :key="`n-${acc.id}`"
                class="h-6 min-w-[110px] px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] border-l"
                :class="colunaHeaderBg(acc)"
                :title="nomeColuna(acc)"
                data-cab-conta
              >
                <div class="truncate">{{ nomeColuna(acc) }}</div>
              </th>
            </tr>
            <!-- Row 3: Listing type + cost column headers -->
            <tr>
              <th class="sticky left-0 bg-background h-6 px-2 py-0 align-middle text-left text-[11px] font-semibold leading-[14px] z-30" :style="estiloFixa('produto')">Produto</th>
              <!-- Estoque, 7d, 30d e Reposição: somem com "Estoque" desmarcado no
                   Layout (o left das colunas presas vem de colunasFixas). -->
              <template v-if="!ocultarEstoque">
                <th
                  class="sticky bg-background h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] text-green-700 z-30 dark:text-green-400"
                  :style="estiloFixa('estoque')"
                  title="Estoque no Bling. Celular: soma de todos os lotes do produto (ci, pi, ra, sa, sp e cd), sem kits e sem usados. Mala/eletro: o SKU de cada cor. Passe o mouse no número para ver as parcelas."
                >Estoque</th>
                <th class="sticky bg-blue-50 h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] text-blue-700 z-30 dark:bg-blue-950 dark:text-blue-300" :style="estiloFixa('v7')" :title="tituloVendas('ok', 7)">7d</th>
                <th class="sticky bg-blue-50 h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] text-blue-700 z-30 dark:bg-blue-950 dark:text-blue-300" :style="estiloFixa('v30')" :title="tituloVendas('ok', 30)">30d</th>
                <th
                  class="sticky bg-amber-50 h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] text-amber-800 z-30 dark:bg-amber-950 dark:text-amber-200"
                  :style="estiloFixa('reposicao')"
                  :title="`${formulaReposicao}. Negativo (vermelho) = falta: é quanto comprar. Positivo = sobra. Linha com várias cores ou voltagens: conta item a item e soma só as faltas. Linha de conjunto/kit: — (a compra é pelos avulsos). Não desconta o que já está em lote aberto na Importação.`"
                  data-coluna="reposicao"
                >Reposição</th>
              </template>
              <!-- Celular mostra os 8 kits (pedido do Eduardo 2026-08-24;
                   antes parava no kit 4); "Kit" desmarcado no Layout esconde todos. -->
              <template v-if="department === 'celular'">
                <th
                  v-for="k in kitsNaGrade" :key="`gridkith-${k}`"
                  class="sticky bg-background h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] whitespace-nowrap text-muted-foreground z-30"
                  :style="estiloFixa(`kit${k}`)"
                  :title="kitTitulo(k)"
                >
                  kit {{ k }}
                </th>
              </template>
              <template v-else>
                <th class="sticky bg-background h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] text-blue-700 z-30" :style="estiloFixa('custo')">custo</th>
              </template>
              <th
                v-for="acc in gridAccounts"
                :key="`lt-${acc.id}`"
                class="h-6 px-1 py-0 align-middle text-center text-[11px] font-semibold leading-[14px] whitespace-nowrap text-muted-foreground border-l"
                :class="colunaHeaderBg(acc)"
              >
                {{ tipoDe(acc) || platformLabel(acc.platform) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="gridLoading && !grid">
              <td colSpan="999" class="p-6 text-center text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!filteredGridProducts.length">
              <td colSpan="999" class="p-6 text-center text-muted-foreground">
                {{ !grid?.products.length
                  ? 'Nenhum produto. Crie/importe produtos na aba Produtos.'
                  : 'Nenhum produto corresponde à busca.' }}
              </td>
            </tr>
            <!-- Linhas (M-8, 18/11/2026): todas com 34px; o "(!) anúncio X"
                 fica no pé da célula, sem aumentar a linha. -->
            <tr
              v-for="(prod, rowIdx) in filteredGridProducts"
              :key="prod.id"
              class="h-[34px] border-t hover:bg-muted/20 group"
              data-linha-grade
            >
              <!-- Sticky: SKU + nome + compare toggle -->
              <td class="sticky left-0 bg-background px-2 py-1 text-xs whitespace-nowrap z-10" :style="estiloFixa('produto')">
                <div class="flex items-center gap-1">
                  <button
                    class="p-0.5 rounded shrink-0 transition-opacity"
                    :class="compareProductIds.has(prod.id) ? 'text-purple-600 bg-purple-100 opacity-100' : 'text-gray-400 hover:text-purple-600 opacity-0 group-hover:opacity-100'"
                    title="Comparar com preço real"
                    @click.stop="toggleCompare(prod)"
                  >
                    <Loader2 v-if="competitorLoadingId === prod.id" class="h-3 w-3 animate-spin" />
                    <Eye v-else class="h-3 w-3" />
                  </button>
                  <div class="min-w-0">
                    <div class="text-xs truncate max-w-[180px]" :title="`${prod.sku} — ${prod.name}`">{{ prod.name }}</div>
                  </div>
                </div>
              </td>
              <!-- Estoque, 7d, 30d e Reposição: somem com "Estoque" desmarcado no Layout -->
              <template v-if="!ocultarEstoque">
                <!-- Sticky: Estoque (soma dos lotes no celular; balão com as parcelas) -->
                <td
                  class="sticky bg-green-50 px-1 py-1 text-center text-xs font-bold z-10 dark:bg-green-950"
                  :class="estoqueEstado !== 'ok'
                    ? 'text-muted-foreground/60'
                    : (stockMap[prod.id] ?? 0) <= 0 ? 'text-red-600 dark:text-red-400' : 'text-green-700 dark:text-green-400'"
                  :style="estiloFixa('estoque')"
                  :title="tituloEstoque(prod.id)"
                >
                  {{ estoqueEstado === 'carregando' ? '…' : estoqueEstado === 'erro' ? '—' : stockMap[prod.id] ?? 0 }}
                </td>
                <!-- Sticky: 7d sales -->
                <td
                  class="sticky bg-blue-50 px-1 py-1 text-center text-xs font-bold z-10 dark:bg-blue-950"
                  :class="vendas7Estado !== 'ok' || (salesMap7d[prod.id] ?? 0) === 0 ? 'text-muted-foreground/60' : 'text-blue-700 dark:text-blue-300'"
                  :style="estiloFixa('v7')"
                  :title="tituloVendas(vendas7Estado, 7)"
                >
                  {{ textoVendas(vendas7Estado, salesMap7d[prod.id]) }}
                </td>
                <!-- Sticky: 30d sales -->
                <td
                  class="sticky bg-blue-50 px-1 py-1 text-center text-xs font-bold z-10 dark:bg-blue-950"
                  :class="vendasEstado !== 'ok' || (salesMap30d[prod.id] ?? 0) === 0 ? 'text-muted-foreground/60' : 'text-blue-700 dark:text-blue-300'"
                  :style="estiloFixa('v30')"
                  :title="tituloVendas(vendasEstado, 30)"
                >
                  {{ textoVendas(vendasEstado, salesMap30d[prod.id]) }}
                </td>
                <!-- Sticky: Reposição = estoque − vendas 30d × prazo ÷ 30 (negativo em vermelho = comprar) -->
                <td
                  class="sticky bg-amber-50 px-1 py-1 text-center text-xs font-bold z-10 dark:bg-amber-950"
                  :class="(reposicaoDe(prod.id) ?? 0) < 0 ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'"
                  :style="estiloFixa('reposicao')"
                  :title="tituloReposicao(prod.id)"
                  data-coluna="reposicao"
                >
                  {{ textoReposicao(prod.id) }}
                </td>
              </template>
              <!-- Sticky cost: kits (celular, somem com "Kit" desmarcado no Layout) ou custo -->
              <template v-if="department === 'celular'">
                <td
                  v-for="k in kitsNaGrade" :key="`gridkit-${k}`"
                  class="sticky bg-background px-1 py-1 text-center text-xs z-10 cursor-pointer"
                  :class="[
                    'text-muted-foreground',
                    isEditing(prod.id, `cost_kit${k}`) ? 'ring-2 ring-blue-500 ring-inset' : '',
                    isFlashed(prod.id, `cost_kit${k}`) ? 'bg-emerald-50 dark:bg-emerald-900/20' : '',
                  ]"
                  :style="estiloFixa(`kit${k}`)"
                  @click="canEditProdutos && !isEditing(prod.id, `cost_kit${k}`) && startEditProduct(prod as any, `cost_kit${k}`)"
                >
                  <input
                    v-if="isEditing(prod.id, `cost_kit${k}`)"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text" inputmode="decimal"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ (prod as any)[`cost_kit${k}`] != null ? Number((prod as any)[`cost_kit${k}`]).toFixed(0) : '—' }}</span>
                </td>
              </template>
              <template v-else>
                <td
                  class="sticky bg-background text-blue-700 font-bold px-1 py-1 text-center text-xs z-10 cursor-pointer"
                  :class="[
                    isEditing(prod.id, 'cost_kit1') ? 'ring-2 ring-blue-500 ring-inset' : '',
                    isFlashed(prod.id, 'cost_kit1') ? 'bg-emerald-50 dark:bg-emerald-900/20' : '',
                  ]"
                  :style="estiloFixa('custo')"
                  @click="canEditProdutos && !isEditing(prod.id, 'cost_kit1') && startEditProduct(prod as any, 'cost_kit1')"
                >
                  <input
                    v-if="isEditing(prod.id, 'cost_kit1')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text" inputmode="decimal"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ Number(prod.cost_kit1 || 0).toFixed(0) }}</span>
                </td>
              </template>
              <!-- Account cells (M-3, 17/11/2026): preço sempre em preto + cartão
                   vermelho + aviãozinho. Saíram o "?", o disquete (preço manual),
                   o ⊘, o NA, o SV, o cadeado e as cores; fica a linha "(!) anúncio X"
                   embaixo do preço quando o anúncio está fora da margem. -->
              <td
                v-for="(acc, accIdx) in gridAccounts"
                :key="acc.id"
                class="px-1 py-1 relative text-foreground"
                :class="[
                  colunaBg(acc),
                  'border-l',
                  selectedCell?.row === rowIdx && selectedCell?.col === accIdx ? 'ring-2 ring-blue-500 ring-inset' : '',
                ]"
                :title="tituloCelulaCatalogo(prod.id, acc)"
                @click="selectedCell = { row: rowIdx, col: accIdx }"
              >
                <!-- Per-cell push status (SSH-style overlay) -->
                <div
                  v-if="pushStates.get(pushCellKey(prod.id, acc.id))"
                  class="absolute inset-0 flex items-center justify-center z-10 pointer-events-none"
                  :class="{
                    'bg-blue-100/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'pushing',
                    'bg-emerald-100/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'success',
                    'bg-red-100/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'error',
                    'bg-gray-200/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'no_link',
                  }"
                >
                  <Loader2 v-if="pushStates.get(pushCellKey(prod.id, acc.id)) === 'pushing'" class="h-3 w-3 animate-spin text-blue-600" />
                  <Check v-else-if="pushStates.get(pushCellKey(prod.id, acc.id)) === 'success'" class="h-3 w-3 text-emerald-600" />
                  <X v-else-if="pushStates.get(pushCellKey(prod.id, acc.id)) === 'error'" class="h-3 w-3 text-red-600" />
                  <Minus v-else class="h-3 w-3 text-gray-500" />
                </div>
                <div class="flex flex-col gap-0.5">
                  <div class="flex items-center justify-between gap-1">
                    <span class="tabular-nums">{{ liveCellLabel(prod, acc) }}</span>
                    <!-- Cartão e aviãozinho em espaços fixos (sempre reservados):
                         ficam alinhados em todas as linhas e colunas. -->
                    <div class="flex shrink-0 items-center gap-0.5">
                      <!-- M-15: clicar no cartão confere só esta célula; enquanto
                           lê, o indicador girando fica no lugar do cartão (o
                           espaço é fixo: o preço e o aviãozinho não se mexem). -->
                      <span class="inline-flex h-4 w-2.5 items-center justify-center">
                        <Loader2
                          v-if="conferindoCelula(prod.id, acc.id)"
                          class="block h-[10px] w-[10px] animate-spin text-red-600"
                          aria-label="Conferindo esta célula…"
                          data-cartao-conferindo
                        />
                        <button
                          v-else-if="cartaoDe(prod.id, acc.id) && celulaConferivel(prod.id, acc)"
                          type="button"
                          class="block h-[10px] w-[7px] rotate-12 rounded-[1px] bg-red-600 shadow-sm cursor-pointer hover:bg-red-700"
                          :title="tituloDoCartao(cartaoDe(prod.id, acc.id)!, true, erroDaCelula(prod.id, acc.id))"
                          :aria-label="tituloDoCartao(cartaoDe(prod.id, acc.id)!, true, erroDaCelula(prod.id, acc.id))"
                          data-cartao
                          data-cartao-clicavel
                          @click.stop="conferirCelula(prod.id, acc)"
                        />
                        <span
                          v-else-if="cartaoDe(prod.id, acc.id)"
                          class="block h-[10px] w-[7px] rotate-12 rounded-[1px] bg-red-600 shadow-sm cursor-help"
                          role="img"
                          :title="tituloDoCartao(cartaoDe(prod.id, acc.id)!, false, erroDaCelula(prod.id, acc.id))"
                          :aria-label="tituloDoCartao(cartaoDe(prod.id, acc.id)!, false, erroDaCelula(prod.id, acc.id))"
                          data-cartao
                        />
                        <span v-else class="invisible block h-[10px] w-[7px]" aria-hidden="true" />
                      </span>
                      <button
                        v-if="cellOf(prod.id, acc.id)?.price != null"
                        class="p-0.5 rounded"
                        :class="catalogoBloqueado(prod.id, acc.id) ? 'text-muted-foreground opacity-40 cursor-not-allowed' : 'opacity-60 hover:opacity-100 hover:bg-emerald-100 text-emerald-700'"
                        :title="catalogoBloqueado(prod.id, acc.id)
                          ? `Envio bloqueado: ${textoBloqueio(catalogoBloqueado(prod.id, acc.id)!)}`
                          : 'Push esta célula'"
                        :disabled="pushing || !!catalogoBloqueado(prod.id, acc.id)"
                        @click.stop="pushCell(cellOf(prod.id, acc.id)!)"
                      >
                        <Send class="h-3 w-3" />
                      </button>
                      <span v-else class="invisible block h-4 w-4" aria-hidden="true" />
                    </div>
                  </div>
                  <div
                    v-if="compareProductIds.has(prod.id) && actualPriceMap[prod.id]?.[acc.id] != null"
                    class="text-[9px]"
                    :class="actualPriceMap[prod.id][acc.id]! > Number(cellOf(prod.id, acc.id)?.price || 0) ? 'text-cyan-600' : 'text-purple-600'"
                  >
                    real: {{ actualPriceMap[prod.id][acc.id] }}
                  </div>
                  <!-- Conferência (para todos desde o M-3): anúncio fora da margem
                       da linha (a do produto ou a Geral) → (!) e o preço dele.
                       M-8: preso no pé da célula (absolute), abaixo do preço sem
                       encostar nele e sem aumentar a linha de 34px. -->
                  <div
                    v-if="divergenciaDe(prod.id, acc.id)"
                    class="absolute inset-x-1 bottom-px truncate cursor-help text-[9px] font-bold leading-[9px] text-red-600 dark:text-red-400"
                    :title="tituloDivergencia(divergenciaDe(prod.id, acc.id)!)"
                    data-divergencia="1"
                  >
                    (!) anúncio {{ Math.round(divergenciaDe(prod.id, acc.id)!.fora[0].preco!) }}<template v-if="divergenciaDe(prod.id, acc.id)!.fora.length > 1"> +{{ divergenciaDe(prod.id, acc.id)!.fora.length - 1 }}</template>
                  </div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ FATURAMENTO -->
    <!-- A tela de Faturamento de antes (/faturamento), com os mesmos filtros
         e a mesma tabela (13/11/2026). -->
    <FaturamentoPainel v-else-if="tab === 'faturamento'" embutido />
  </div>
</template>
