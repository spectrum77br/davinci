<script setup lang="ts">
// Contabilidade › Compra › "Compras feitas" (23/10/2026): as compras registradas,
// por mês e situação. Daqui sai a planilha do Bling (uma compra ou várias da
// MESMA vendedora — cada planilha é importada no Bling de uma empresa), anota-se
// o nº da NF que saiu lá, edita-se (sem NF), cancela, reabre e exclui.
//
// 04/11/2026 — emissão AUTOMÁTICA: com a vendedora automática (hoje só a
// BEZERRA, Bling 44), "emitir a NF (robô)" põe a compra na fila; o robô do Mac
// pega (cron a cada 15 min, 8h–19h30), importa, gera a NF, acerta NCM/IE, manda
// UMA vez à SEFAZ e devolve o XML conferido. Enquanto houver compra na fila ou
// com o robô, a lista se relê sozinha a cada 30 s.
//
// 04/11/2026 (Eduardo: "igual emissão de serviço, tem que dar para imprimir a nota
// etc."): a NF emitida com a chave imprime (a DANFE, desenhada no servidor do XML que
// está no DaVinci, abre numa aba) e baixa o PDF pelo menu; marcando várias, a barra de
// baixo imprime todas num PDF só ou baixa os PDFs/XMLs num .zip — como em Notas
// emitidas da Emissão de Serviço.
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import {
  Ban, ClipboardList, Copy, Download, FileCode2, FileDown, FilePen, FileX2, Hash, ListX, Loader2, OctagonX, Printer,
  RotateCcw, Send, Trash2, Undo2,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useToasts } from '~/composables/useToasts'
import {
  codigoErro, erroApi, fmtBrl, fmtDoc, fmtMes, mesParaData, plural, problemasApi, useNfseApi, useNfseTela,
  type MenuItem,
} from '~/lib/nfse'
import {
  LOTE_COMPRA, ROTULO_ETAPA, ROTULO_STATUS, acoesDaCompra, chaveCurta, chaveDoErro, fmtNcm, fmtQtd, grupoDaMarcacao,
  nomeDaPlanilha, nomeDanfe, numeroQueimado, paraLote, passouDaSefaz, vendedoraAutomatica,
  type AcaoCompra, type Compra, type GrupoMarcacao, type StatusCompra, type Vendedora,
} from '~/lib/compra'

const props = defineProps<{ versao: number; destacar: string | null; vendedoras: Vendedora[] }>()
const emit = defineEmits<{ (e: 'editar', c: Compra): void }>()

const tela = useNfseTela()
const { api, baixarArquivo, abrirArquivo, imprimirLote, baixarLote } = useNfseApi()
const toasts = useToasts()

const mes = ref('') // '' = todos os meses
const status = ref<'todas' | StatusCompra>('todas')
const compras = ref<Compra[]>([])
const carregando = ref(false)
const carregou = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null) // id da compra com ação rodando ('lote' = planilha das marcadas)
const marcadas = ref<Set<string>>(new Set())
// imprimir / baixar as NFs marcadas em andamento (a barra gira o botão dela)
const acaoLote = ref<'imprimir' | 'pdf' | 'xml' | null>(null)

let seq = 0
// silencioso = a releitura dos 30 s: sem esqueleto e, se falhar, fica a lista que já está na tela
async function carregar(silencioso = false) {
  const s = ++seq
  if (!silencioso) {
    carregando.value = true
    erro.value = null
  }
  try {
    const query: Record<string, string> = {}
    if (mes.value) query.competencia = mesParaData(mes.value)
    if (status.value !== 'todas') query.status = status.value
    const r = await api<Compra[]>('/api/nfse/compras', { query })
    if (s !== seq) return
    compras.value = r
    carregou.value = true
    erro.value = null
    limparMarcadas()
  } catch (e) {
    if (s === seq && !silencioso) erro.value = erroApi(e)
  } finally {
    if (s === seq) carregando.value = false
  }
}

watch([mes, status, () => props.versao], () => carregar(), { immediate: true })

const OPCOES_STATUS = [
  { id: 'todas', rotulo: 'Todas' },
  { id: 'registrada', rotulo: 'Sem NF' },
  { id: 'erro', rotulo: 'Com erro' },
  { id: 'nf_emitida', rotulo: 'NF emitida' },
  { id: 'cancelada', rotulo: 'Canceladas' },
]
const statusModel = computed({
  get: () => status.value as string,
  set: (v: string) => {
    status.value = (['registrada', 'erro', 'nf_emitida', 'cancelada'].includes(v) ? v : 'todas') as typeof status.value
  },
})

// --- releitura sozinha enquanto o robô trabalha -------------------------------------------
// A Compra mora num <KeepAlive>: fora da aba (ou com a página trancada) o laço
// para. Não entra no algoOcupado() da página: esperar o robô não segura o cadeado.

const PASSO_MS = 30_000
const comRobo = computed(() => compras.value.some((c) => c.status === 'na_fila' || c.status === 'emitindo'))
let ativa = true
let timer: ReturnType<typeof setTimeout> | null = null

function pararReleitura() {
  if (timer) clearTimeout(timer)
  timer = null
}

function agendarReleitura() {
  pararReleitura()
  if (!ativa || !comRobo.value) return
  timer = setTimeout(async () => {
    timer = null
    if (!ativa) return
    // ação de uma linha no meio (ou janela aberta): espera a próxima volta
    if (!ocupado.value && !nfAberta.value) await carregar(true)
    agendarReleitura()
  }, PASSO_MS)
}

watch(comRobo, agendarReleitura, { immediate: true })
onActivated(() => {
  if (ativa) return
  ativa = true
  // voltou para a aba: o robô pode ter andado enquanto ela estava escondida
  if (comRobo.value) carregar(true)
  agendarReleitura()
})
onDeactivated(() => {
  ativa = false
  pararReleitura()
})
onBeforeUnmount(() => {
  ativa = false
  pararReleitura()
})

const totalVisivel = computed(() =>
  compras.value.filter((c) => c.status !== 'cancelada').reduce((s, c) => s + Number(c.total), 0),
)

// --- marcar várias: planilha das sem NF | imprimir/baixar as NFs ---------------------------
// Dois grupos que não se misturam (lib/compra.ts grupoDaMarcacao): compra SEM NF → uma
// planilha só (a com NF já está no Bling: número repetido = o Bling recusa o arquivo
// todo); NF EMITIDA com a chave → imprimir/baixar (04/11). Marcou de um, as caixinhas do
// outro ficam apagadas até limpar. Na ordem da tela: é a ordem das folhas ao imprimir.

const listaMarcadas = computed(() => compras.value.filter((c) => marcadas.value.has(c.id)))
// o grupo da seleção: o da 1ª marcada (guardado, para a compra que muda de grupo no meio
// — ex.: anotaram a NF de uma marcada para a planilha — sair da seleção em vez de virá-la)
const grupoSel = ref<GrupoMarcacao | null>(null)
const grupoMarcado = computed<GrupoMarcacao | null>(() => (marcadas.value.size ? grupoSel.value : null))
// O cabeçalho marca um grupo só: o que já está marcado; sem nada marcado, as NFs no
// filtro "NF emitida" e, nos outros, as sem NF (como antes) — ou as NFs, se não houver.
const grupoDoCabecalho = computed<GrupoMarcacao | null>(() => {
  if (grupoMarcado.value) return grupoMarcado.value
  const tem = (g: GrupoMarcacao) => compras.value.some((c) => grupoDaMarcacao(c) === g)
  if (status.value === 'nf_emitida') return tem('nf') ? 'nf' : null
  return tem('planilha') ? 'planilha' : tem('nf') ? 'nf' : null
})
const marcaveis = computed(() =>
  grupoDoCabecalho.value ? compras.value.filter((c) => grupoDaMarcacao(c) === grupoDoCabecalho.value) : [],
)
const vendedorasMarcadas = computed(() => new Set(listaMarcadas.value.map((c) => c.vendedor_cnpj)))
const marcacao = computed(() => {
  const n = listaMarcadas.value.length
  if (!n) return 'nenhuma'
  return n === marcaveis.value.length ? 'todas' : 'algumas'
})
const marcadasNf = computed(() => (grupoMarcado.value === 'nf' ? listaMarcadas.value : []))

function podeMarcar(c: Compra): boolean {
  const g = grupoDaMarcacao(c)
  return !!g && (!grupoMarcado.value || g === grupoMarcado.value)
}

// a dica da caixinha apagada: por que não marca
function motivoNaoMarca(c: Compra): string | undefined {
  if (podeMarcar(c)) return undefined
  const g = grupoDaMarcacao(c)
  if (g === 'nf') return 'Há compras sem NF marcadas (planilha): limpe a seleção para marcar NFs.'
  if (g === 'planilha') return 'Há NFs marcadas (imprimir/baixar): limpe a seleção para marcar compras sem NF.'
  if (c.status === 'nf_emitida') return 'NF anotada sem a chave: o XML não está no DaVinci (imprima pelo Bling).'
  if (c.status === 'na_fila' || c.status === 'emitindo') return 'Está com o robô: a NF sai por ele.'
  return undefined
}

// Sai da seleção o que sumiu da lista ou mudou de grupo (foi para o robô, ganhou ou
// perdeu a NF...).
function limparMarcadas() {
  if (!marcadas.value.size) return
  const porId = new Map(compras.value.map((c) => [c.id, c]))
  const s = new Set([...marcadas.value].filter((id) => {
    const c = porId.get(id)
    return !!c && grupoDaMarcacao(c) === grupoSel.value
  }))
  if (s.size !== marcadas.value.size) marcadas.value = s
}

function alternar(c: Compra) {
  if (acaoLote.value) return
  const s = new Set(marcadas.value)
  if (s.has(c.id)) s.delete(c.id)
  else if (podeMarcar(c)) {
    if (!s.size) grupoSel.value = grupoDaMarcacao(c)
    s.add(c.id)
  } else return
  marcadas.value = s
}
function marcarTodas() {
  if (acaoLote.value) return
  if (marcacao.value === 'todas') {
    marcadas.value = new Set()
    return
  }
  grupoSel.value = grupoDoCabecalho.value
  marcadas.value = new Set(marcaveis.value.map((c) => c.id))
}
function limparSelecao() {
  marcadas.value = new Set()
}

// Imprimir (as DANFEs num PDF só, numa aba) ou baixar os PDFs/XMLs (.zip, uma pasta por
// compradora) das NFs marcadas: o mesmo caminho da Emissão de Serviço (a página, com a
// chave), só que nas rotas da Compra (LOTE_COMPRA). Chamado direto do clique: imprimir
// abre a aba antes de esperar o servidor.
async function acaoArquivos(acao: 'imprimir' | 'pdf' | 'xml') {
  const lista = marcadasNf.value
  if (acaoLote.value || ocupado.value || !lista.length) return
  acaoLote.value = acao
  try {
    const notas = lista.map(paraLote)
    if (acao === 'imprimir') await imprimirLote(notas, LOTE_COMPRA)
    else await baixarLote(notas, acao, LOTE_COMPRA)
  } finally {
    acaoLote.value = null
  }
}

async function planilhaDasMarcadas() {
  const lista = listaMarcadas.value
  if (!lista.length || ocupado.value || grupoMarcado.value !== 'planilha') return
  if (vendedorasMarcadas.value.size > 1) {
    toasts.warning('Marque compras de uma empresa que vende só', [
      'Cada planilha é importada no Bling de uma empresa: baixe uma por vendedora.',
    ])
    return
  }
  ocupado.value = 'lote'
  try {
    const ok = await baixarArquivo('/api/nfse/compras/planilha', nomeDaPlanilha(lista), {
      method: 'POST',
      body: { ids: lista.map((c) => c.id) },
    })
    if (ok) toasts.success(`Planilha com ${plural(lista.length, 'compra', 'compras')} baixada`)
  } finally {
    ocupado.value = null
  }
}

// --- ações de uma compra ------------------------------------------------------------------

// Quais ações aparecem sai de acoesDaCompra (lib/compra.ts, testada em
// tests/compra-emissao.cjs); aqui só o rótulo, o ícone e o traço antes.
const MENU: Record<AcaoCompra, Omit<MenuItem, 'id'>> = {
  emitir: { rotulo: 'emitir a NF (robô)', icone: Send },
  planilha: { rotulo: 'baixar planilha do Bling', icone: Download },
  nf: { rotulo: 'anotar a NF que saiu', icone: Hash },
  trocar_nf: { rotulo: 'trocar o nº da NF', icone: Hash },
  editar: { rotulo: 'editar', icone: FilePen },
  cancelar: { rotulo: 'cancelar a compra', icone: Ban, perigo: true, separar: true },
  cancelar_conferido: { rotulo: 'cancelar (conferi no Bling 44)', icone: Ban, perigo: true, separar: true },
  tirar_fila: { rotulo: 'tirar da fila', icone: ListX },
  parar_robo: { rotulo: 'tirar do robô', icone: OctagonX, perigo: true },
  imprimir: { rotulo: 'imprimir a NF (DANFE)', icone: Printer },
  pdf: { rotulo: 'baixar PDF (DANFE)', icone: FileDown },
  xml: { rotulo: 'baixar XML', icone: FileCode2 },
  chave: { rotulo: 'copiar chave', icone: Copy },
  desfazer: { rotulo: 'desfazer a NF', icone: Undo2, separar: true },
  desfazer_conferido: { rotulo: 'desfazer a NF (conferi no Bling 44)', icone: Undo2, perigo: true, separar: true },
  reabrir: { rotulo: 'reabrir', icone: RotateCcw },
  excluir: { rotulo: 'excluir', icone: Trash2, perigo: true, separar: true },
}

const automaticas = computed(() => new Set(props.vendedoras.filter(vendedoraAutomatica).map((v) => v.cnpj)))

function itensDoMenu(c: Compra): MenuItem[] {
  const acoes = acoesDaCompra(c, {
    canEdit: tela.canEdit.value,
    canDelete: tela.canDelete.value,
    automatica: automaticas.value.has(c.vendedor_cnpj),
  })
  // traço só ENTRE grupos: no 1º item ficaria um risco solto no topo do menu
  return acoes.map((id, i) => ({ id, ...MENU[id], separar: !!MENU[id].separar && i > 0 }))
}
const menus = computed(() => new Map(compras.value.map((c) => [c.id, itensDoMenu(c)])))

function trocar(c: Compra) {
  const i = compras.value.findIndex((x) => x.id === c.id)
  if (i >= 0) compras.value.splice(i, 1, c)
  limparMarcadas()
  if (status.value !== 'todas' && c.status !== status.value) carregar()
}

async function rodar(c: Compra, fazer: () => Promise<void>, erroTitulo: string) {
  if (ocupado.value) return
  ocupado.value = c.id
  try {
    await fazer()
  } catch (e) {
    toasts.error(erroTitulo, erroApi(e))
  } finally {
    ocupado.value = null
  }
}

async function escolher(c: Compra, acao: string) {
  const nome = `a compra nº ${c.numero}`
  if (acao === 'emitir') {
    await emitir(c)
  } else if (acao === 'tirar_fila') {
    await rodar(c, async () => {
      try {
        const r = await api<Compra>(`/api/nfse/compras/${c.id}/tirar-da-fila`, { method: 'POST' })
        trocar(r)
        toasts.success(`Compra nº ${r.numero} fora da fila`, ['Voltou para "sem NF": dá para editar, baixar a planilha ou emitir de novo.'])
      } catch (e) {
        // 409 = o robô pegou entre a última leitura e o clique: a lista mostra onde ele está
        void carregar(true)
        throw e
      }
    }, 'Não deu para tirar da fila')
  } else if (acao === 'parar_robo') {
    const ok = await tela.confirmar({
      titulo: `Tirar ${nome} do robô?`,
      texto:
        'O robô para no próximo passo e a compra vai para "com erro". Se ele já importou o pedido ou mandou a NF à SEFAZ, '
        + 'confira no Bling 44 antes de qualquer coisa: depois, anote a NF que saiu ou cancele a compra. O número da compra não volta a ser usado.',
      linhas: [`Onde o robô está: ${rotuloEtapa(c)}`],
      tom: 'perigo',
      botao: 'tirar do robô',
      digitar: 'PARAR',
    })
    if (ok) {
      await rodar(c, async () => {
        const r = await api<Compra>(`/api/nfse/compras/${c.id}/parar-robo`, { method: 'POST' })
        trocar(r)
        // a etapa que o SERVIDOR devolveu (a da confirmação é da última leitura, até 30 s atrás)
        toasts.warning(`Compra nº ${r.numero} tirada do robô`, [
          `Parou em: ${rotuloEtapa(r)}.`,
          passouDaSefaz(r.emissao_etapa)
            ? 'A NF pode já ter ido à SEFAZ: NÃO reenvie — no Bling 44, só "Consultar situação". Depois anote a NF que saiu ou cancele.'
            : 'Confira no Bling 44 o pedido e a NF antes de anotar ou cancelar.',
        ])
      }, 'Não deu para tirar do robô')
    }
  } else if (acao === 'xml') {
    await rodar(c, async () => {
      await baixarArquivo(`/api/nfse/compras/${c.id}/xml`, `nfe_${c.nf_numero || c.numero}.xml`)
    }, 'Não deu para baixar o XML')
  } else if (acao === 'imprimir') {
    // A DANFE numa aba para imprimir. Nada de espera antes do abrirArquivo: ele abre a
    // aba no clique (depois de um await o navegador bloquearia).
    await rodar(c, async () => {
      await abrirArquivo(`/api/nfse/compras/${c.id}/pdf`, {
        titulo: `DANFE da NF ${c.nf_numero ?? ''}`.trim(),
        carregando: 'Montando a DANFE da NF…',
        nome: nomeDanfe(c),
        erro: 'Não deu para abrir a DANFE',
      })
    }, 'Não deu para abrir a DANFE')
  } else if (acao === 'pdf') {
    await rodar(c, async () => {
      await baixarArquivo(`/api/nfse/compras/${c.id}/pdf`, nomeDanfe(c))
    }, 'Não deu para baixar o PDF')
  } else if (acao === 'chave') {
    await copiarChave(c)
  } else if (acao === 'planilha') {
    await rodar(c, async () => {
      await baixarArquivo(`/api/nfse/compras/${c.id}/planilha`, nomeDaPlanilha([c]))
    }, 'Não deu para baixar a planilha')
  } else if (acao === 'nf' || acao === 'trocar_nf') {
    abrirNf(c)
  } else if (acao === 'editar') {
    emit('editar', c)
  } else if (acao === 'cancelar') {
    const ok = await tela.confirmar({
      titulo: `Cancelar ${nome}?`,
      texto: 'Ela continua na lista como cancelada e não gera mais planilha. Dá para reabrir depois.',
      tom: 'perigo',
      botao: 'Cancelar a compra',
    })
    if (ok) await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/cancelar`, { method: 'POST' })), 'Não deu para cancelar')
  } else if (acao === 'cancelar_conferido') {
    const ok = await tela.confirmar({
      titulo: `Cancelar ${nome}?`,
      texto:
        `O robô já mexeu no Bling de ${c.vendedor_nome}: o pedido ${c.numero} pode estar lá, e talvez a NF. `
        + 'Cancele só depois de conferir no Bling 44 que não saiu NF (ou de cancelá-la lá). Não dá para reabrir: '
        + 'se ainda precisar da compra, registre uma nova.',
      linhas: [`Onde o robô parou: ${rotuloEtapa(c)}`, ...(c.emissao_erro ? [c.emissao_erro] : [])],
      tom: 'perigo',
      botao: 'conferi, cancelar',
    })
    if (ok) {
      await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/cancelar`, {
        method: 'POST',
        query: { conferido: 1 },
      })), 'Não deu para cancelar')
    }
  } else if (acao === 'reabrir') {
    await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/reabrir`, { method: 'POST' })), 'Não deu para reabrir')
  } else if (acao === 'desfazer') {
    const ok = await tela.confirmar({
      titulo: `Desfazer a NF ${c.nf_numero} da compra nº ${c.numero}?`,
      texto: 'A compra volta para "sem NF" (aqui no DaVinci). Se a NF foi emitida de verdade, cancele-a no Bling de quem vendeu.',
      botao: 'Desfazer',
    })
    if (ok) await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/desfazer-nf`, { method: 'POST' })), 'Não deu para desfazer a NF')
  } else if (acao === 'desfazer_conferido') {
    // NF do robô: existe na SEFAZ. A compra vira cancelada (guarda nº e chave) e nunca volta para "sem NF".
    const ok = await tela.confirmar({
      titulo: `Desfazer a NF ${c.nf_numero} da compra nº ${c.numero}?`,
      texto:
        'Essa compra passou pelo robô: a NF existe na SEFAZ. Cancele a NF no Bling 44 antes. Aqui a compra vai para '
        + '"cancelada" com o número e a chave guardados — não volta para "sem NF". Se ainda precisar da compra, registre uma nova.',
      linhas: c.nf_chave ? [`Chave ${c.nf_chave}`] : undefined,
      tom: 'perigo',
      botao: 'conferi, desfazer',
    })
    if (ok) {
      await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/desfazer-nf`, {
        method: 'POST',
        query: { conferido: 1 },
      })), 'Não deu para desfazer a NF')
    }
  } else if (acao === 'excluir') {
    const ok = await tela.confirmar({
      titulo: `Excluir ${nome}?`,
      texto: 'Ela some da lista. Não dá para desfazer.',
      tom: 'perigo',
      botao: 'Excluir',
    })
    if (ok) {
      await rodar(c, async () => {
        await api(`/api/nfse/compras/${c.id}`, { method: 'DELETE' })
        compras.value = compras.value.filter((x) => x.id !== c.id)
        toasts.success(`Compra nº ${c.numero} excluída`)
      }, 'Não deu para excluir')
    }
  }
}

// --- emitir pelo robô -------------------------------------------------------------------------

function resumoDaEmissao(c: Compra): string {
  return `${c.comprador.apelido || c.comprador.razao_social} compra ${resumoItens(c)} de ${c.vendedor_nome} · ${fmtBrl(c.total)} · CFOP ${c.cfop}`
}

// A NF é de verdade: confirma digitando EMITIR. O servidor ainda pode pedir uma
// 2ª confirmação (409): a planilha já foi baixada (importada à mão = NF em dobro)
// ou já tem outra compra da empresa com a vendedora no mesmo mês. Cada uma é
// perguntada uma vez e o pedido vai de novo com a flag.
async function emitir(c: Compra) {
  const ok = await tela.confirmar({
    titulo: 'Emitir NF de verdade',
    texto: `A compra nº ${c.numero} entra na fila do robô do Mac, que emite a NF no Bling de ${c.vendedor_nome} e manda à SEFAZ.`,
    linhas: [
      resumoDaEmissao(c),
      'Vai à SEFAZ uma vez só; depois disso, só se desfaz cancelando a NF no Bling.',
      'O robô pega a fila a cada 15 min, das 8h às 19h30.',
    ],
    tom: 'perigo',
    botao: 'emitir',
    digitar: 'EMITIR',
  })
  if (!ok) return
  const flags = { confirmo_planilha_nao_importada: false, confirmo_outra_no_mes: false }
  await rodar(c, async () => {
    for (;;) {
      try {
        const r = await api<Compra>(`/api/nfse/compras/${c.id}/emitir`, { method: 'POST', body: { ...flags } })
        trocar(r)
        toasts.success(`Compra nº ${r.numero} na fila do robô`, ['A lista se atualiza sozinha enquanto ele trabalha.'])
        return
      } catch (e) {
        const codigo = codigoErro(e)
        if (codigo === 'planilha_ja_baixada' && !flags.confirmo_planilha_nao_importada) {
          const sim = await tela.confirmar({
            titulo: 'A planilha dessa compra já foi baixada',
            texto:
              `Se ela foi importada no Bling de ${c.vendedor_nome}, o robô importaria o mesmo pedido de novo e sairia NF em dobro. `
              + 'Confira em Importações do Bling antes de seguir.',
            linhas: [erroApi(e)],
            tom: 'perigo',
            botao: 'não importei, emitir',
          })
          if (!sim) return
          flags.confirmo_planilha_nao_importada = true
          continue
        }
        if (codigo === 'outra_no_mes' && !flags.confirmo_outra_no_mes) {
          const sim = await tela.confirmar({
            titulo: 'Já tem outra compra nesse mês',
            texto: `${c.comprador.apelido || c.comprador.razao_social} já tem compra de ${c.vendedor_nome} com o faturamento de ${fmtMes(c.competencia.slice(0, 7))} na fila ou com NF.`,
            linhas: [erroApi(e)],
            tom: 'perigo',
            botao: 'emitir mesmo assim',
          })
          if (!sim) return
          flags.confirmo_outra_no_mes = true
          continue
        }
        if (codigo === 'nao_emite_automatico') {
          const lista = problemasApi(e)
          const erros = (e as any)?.data?.detail?.erros
          toasts.error('Essa compra não sai pelo robô', lista.length ? lista : Array.isArray(erros) && erros.length ? erros : erroApi(e))
          return
        }
        throw e
      }
    }
  }, 'Não deu para pôr na fila do robô')
}

async function copiarChave(c: Compra) {
  if (!c.nf_chave) return
  try {
    await navigator.clipboard.writeText(c.nf_chave)
    toasts.success('Chave copiada')
  } catch {
    toasts.error('Não deu para copiar', 'Selecione a chave e copie com Ctrl+C.')
  }
}

// --- célula Situação ----------------------------------------------------------------------

const p2 = (n: number) => String(n).padStart(2, '0')
// hoje: "às 14:30"; outro dia: "em 03/11 às 14:30"
function quando(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const hora = `às ${p2(d.getHours())}:${p2(d.getMinutes())}`
  return d.toDateString() === new Date().toDateString() ? hora : `em ${p2(d.getDate())}/${p2(d.getMonth() + 1)} ${hora}`
}

function rotuloEtapa(c: Compra): string {
  return c.emissao_etapa ? (ROTULO_ETAPA[c.emissao_etapa] ?? c.emissao_etapa) : 'na fila, o robô ainda não pegou'
}

// a dica do selo: o que acontece agora e o que a pessoa pode fazer
function dicaDaSituacao(c: Compra): string | null {
  if (c.status === 'na_fila') return 'O robô do Mac pega a fila a cada 15 min, das 8h às 19h30. Até ele pegar, dá para tirar da fila.'
  if (c.status === 'emitindo') return 'O robô está com ela. Para interromper, use "tirar do robô" no menu (precisa conferir o Bling 44 depois).'
  if (c.status === 'erro' && numeroQueimado(c)) {
    return 'O robô já tentou importar: o pedido (e talvez a NF) pode estar no Bling 44. Confira lá e anote a NF que saiu ou cancele.'
  }
  if (c.status === 'erro') return 'Nada foi feito no Bling: corrija e emita de novo, ou baixe a planilha e emita à mão.'
  return null
}

const nfAberta = ref<Compra | null>(null)
const nfNumero = ref('')
const nfChave = ref('')
const nfSalvando = ref(false)
const nfErro = ref<string | null>(null)
// compra que passou pelo robô (ou já com chave): a janela pede também a chave, que já
// vem preenchida com a do XML/erro do robô — sem ela não há "copiar chave" nem XML
const nfComChave = computed(() => !!nfAberta.value && (numeroQueimado(nfAberta.value) || !!nfAberta.value.nf_chave))

function abrirNf(c: Compra) {
  nfAberta.value = c
  nfNumero.value = c.nf_numero ?? ''
  nfChave.value = c.nf_chave ?? chaveDoErro(c.emissao_erro)
  nfErro.value = null
}

async function salvarNf() {
  const c = nfAberta.value
  if (!c) return
  const n = nfNumero.value.replace(/\D/g, '')
  if (!n) {
    nfErro.value = 'Digite o número da NF (só números).'
    return
  }
  const chave = nfComChave.value ? nfChave.value.replace(/\D/g, '') : ''
  if (chave && chave.length !== 44) {
    nfErro.value = 'A chave de acesso da NF-e tem 44 números (ou deixe em branco).'
    return
  }
  nfSalvando.value = true
  try {
    const r = await api<Compra>(`/api/nfse/compras/${c.id}/nf`, {
      method: 'POST',
      body: chave ? { nf_numero: n, nf_chave: chave } : { nf_numero: n },
    })
    trocar(r)
    toasts.success(`Compra nº ${r.numero}: NF ${r.nf_numero} anotada`)
    nfAberta.value = null
  } catch (e) {
    nfErro.value = erroApi(e)
  } finally {
    nfSalvando.value = false
  }
}

function resumoItens(c: Compra): string {
  return c.itens.map((i) => `${fmtQtd(i.quantidade)} × ${i.nome}`).join(' + ')
}
function fmtData(iso: string): string {
  const [y, m, d] = iso.split('-')
  return d && m && y ? `${d}/${m}/${y}` : iso
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-end gap-3">
      <NfseMesPicker v-model="mes" nome="Mês do faturamento" permitir-todos />
      <NfseSegmentado v-model="statusModel" :opcoes="OPCOES_STATUS" tamanho="sm" />
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <span v-if="grupoMarcado === 'planilha'" class="text-sm text-muted-foreground">
          {{ plural(listaMarcadas.length, 'marcada', 'marcadas') }}
        </span>
        <!-- NF marcada: imprimir/baixar na barra de baixo (a planilha é só das sem NF) -->
        <Button
          v-if="tela.canEdit.value && grupoMarcado !== 'nf'"
          size="sm"
          variant="outline"
          :disabled="!listaMarcadas.length || !!ocupado"
          :title="vendedorasMarcadas.size > 1 ? 'marque compras de uma vendedora só' : 'uma planilha com as compras marcadas'"
          @click="planilhaDasMarcadas"
        >
          <Loader2 v-if="ocupado === 'lote'" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <Download v-else class="mr-1.5 size-4" aria-hidden="true" />
          planilha das marcadas
        </Button>
      </div>
    </div>

    <NfseAviso v-if="erro" tom="perigo" titulo="Não carregou as compras">
      {{ erro }}
      <template #acoes>
        <Button size="sm" variant="outline" :disabled="carregando" @click="carregar()">tentar de novo</Button>
      </template>
    </NfseAviso>

    <NfseSkeletonTabela v-if="!carregou && carregando" :linhas="4" :colunas="6" />

    <EmptyState
      v-else-if="carregou && !compras.length"
      :icon="ClipboardList"
      title="Nenhuma compra aqui"
      :description="mes ? `Nada registrado com o faturamento de ${fmtMes(mes)}.` : 'As compras registradas em Fazer compra aparecem aqui.'"
    />

    <div v-else-if="carregou" class="table-card tabela-nfse">
      <div class="relative overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th class="w-10">
                <input
                  type="checkbox"
                  class="size-4 rounded align-middle accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
                  :checked="marcacao === 'todas'"
                  :indeterminate="marcacao === 'algumas'"
                  :disabled="!marcaveis.length || !!acaoLote"
                  :aria-label="grupoDoCabecalho === 'nf' ? 'marcar todas as NFs emitidas da lista' : 'marcar todas as compras sem NF da lista'"
                  :title="grupoDoCabecalho === 'nf' ? 'marcar todas as NFs emitidas (imprimir ou baixar)' : 'marcar todas as compras sem NF (planilha)'"
                  @change="marcarTodas"
                />
              </th>
              <th class="whitespace-nowrap">Nº</th>
              <th>Quem compra → de quem</th>
              <th class="hidden lg:table-cell">O que</th>
              <th class="hidden whitespace-nowrap xl:table-cell">Faturamento</th>
              <th class="!text-right">Total</th>
              <th>Situação</th>
              <th class="col-acoes w-px"><span class="sr-only">Ações</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in compras"
              :key="c.id"
              :class="[
                marcadas.has(c.id) && 'linha-marcada bg-primary/5',
                destacar === c.id && 'bg-emerald-50/60 dark:bg-emerald-950/20',
              ]"
            >
              <td class="w-10">
                <input
                  type="checkbox"
                  class="size-4 rounded align-middle accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
                  :checked="marcadas.has(c.id)"
                  :disabled="(!podeMarcar(c) && !marcadas.has(c.id)) || !!acaoLote"
                  :title="motivoNaoMarca(c)"
                  :aria-label="`marcar a compra nº ${c.numero}`"
                  @change="alternar(c)"
                />
              </td>
              <td class="whitespace-nowrap">
                <div class="font-medium tabular-nums">{{ c.numero }}</div>
                <div class="text-xs text-muted-foreground tabular-nums">{{ fmtData(c.data_pedido) }}</div>
              </td>
              <td class="min-w-[220px]">
                <div class="font-medium" :class="c.status === 'cancelada' && 'text-muted-foreground line-through'">
                  {{ c.comprador.apelido || c.comprador.razao_social }}
                  <span class="font-normal text-muted-foreground">→ {{ c.vendedor_nome }}</span>
                </div>
                <div class="text-xs text-muted-foreground">
                  {{ fmtDoc(c.comprador.cnpj) }} · CFOP {{ c.cfop }}
                  <template v-if="c.criado_por"> · por {{ c.criado_por }}</template>
                </div>
              </td>
              <td class="hidden min-w-[180px] lg:table-cell">
                <div class="text-sm">{{ resumoItens(c) }}</div>
                <div class="text-xs text-muted-foreground">
                  NCM {{ [...new Set(c.itens.map((i) => fmtNcm(i.ncm)))].join(', ') }}
                </div>
              </td>
              <td class="hidden whitespace-nowrap xl:table-cell">
                <div class="text-sm tabular-nums">{{ c.faturamento != null ? fmtBrl(c.faturamento) : '—' }}</div>
                <div class="text-xs text-muted-foreground">
                  {{ fmtMes(c.competencia.slice(0, 7)) }}<template v-if="c.modo === 'percentual' && c.percentual">
                    · {{ String(Number(c.percentual)).replace('.', ',') }}%</template>
                </div>
              </td>
              <td class="whitespace-nowrap !text-right font-medium tabular-nums">{{ fmtBrl(c.total) }}</td>
              <td class="whitespace-nowrap">
                <NfseDica :texto="dicaDaSituacao(c)" lado="left">
                  <span
                    :class="ROTULO_STATUS[c.status]?.classe ?? 'pill-muted'"
                    :tabindex="dicaDaSituacao(c) ? 0 : undefined"
                  >
                    <Loader2 v-if="c.status === 'emitindo'" class="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    {{ ROTULO_STATUS[c.status]?.texto ?? c.status }}
                  </span>
                </NfseDica>
                <template v-if="c.status === 'na_fila'">
                  <div class="mt-0.5 text-xs text-muted-foreground tabular-nums">
                    pedida {{ quando(c.emissao_pedida_em) }}
                  </div>
                  <!-- o robô devolveu para a fila (nada foi feito no Bling): o porquê e quantas já foram -->
                  <NfseDica v-if="c.emissao_erro" :texto="c.emissao_erro" lado="left">
                    <div class="max-w-[220px] truncate text-xs text-amber-600 dark:text-amber-400" tabindex="0">
                      {{ c.emissao_erro }}
                    </div>
                  </NfseDica>
                  <div v-if="c.emissao_tentativas" class="text-xs text-muted-foreground tabular-nums">
                    tentativa {{ c.emissao_tentativas }} de 3
                  </div>
                </template>
                <template v-else-if="c.status === 'emitindo'">
                  <div class="mt-0.5 text-xs">{{ rotuloEtapa(c) }}</div>
                  <div class="text-xs text-muted-foreground tabular-nums">
                    desde {{ quando(c.emissao_iniciada_em) || '—' }} · tentativa {{ c.emissao_tentativas || 1 }}
                  </div>
                  <!-- pausada: o robô volta sozinho na próxima rodada -->
                  <NfseDica v-if="c.emissao_erro" :texto="c.emissao_erro" lado="left">
                    <div class="max-w-[220px] truncate text-xs text-amber-600 dark:text-amber-400" tabindex="0">
                      {{ c.emissao_erro }}
                    </div>
                  </NfseDica>
                </template>
                <template v-else-if="c.status === 'erro'">
                  <NfseDica v-if="c.emissao_erro" :texto="c.emissao_erro" lado="left">
                    <div class="mt-0.5 max-w-[220px] truncate text-xs text-red-600 dark:text-red-400" tabindex="0">
                      {{ c.emissao_erro }}
                    </div>
                  </NfseDica>
                  <div v-if="numeroQueimado(c)" class="text-xs text-muted-foreground">parou em: {{ rotuloEtapa(c) }}</div>
                  <div v-if="c.nf_numero" class="text-xs text-muted-foreground tabular-nums">NF {{ c.nf_numero }}</div>
                </template>
                <template v-else-if="c.nf_numero">
                  <div class="mt-0.5 text-xs text-muted-foreground tabular-nums">NF {{ c.nf_numero }}</div>
                  <NfseDica v-if="c.nf_chave" :texto="`Chave ${c.nf_chave}`" lado="left">
                    <div class="w-fit text-xs text-muted-foreground tabular-nums" tabindex="0" :aria-label="`chave ${c.nf_chave}`">
                      chave {{ chaveCurta(c.nf_chave) }}
                    </div>
                  </NfseDica>
                </template>
              </td>
              <td class="col-acoes">
                <Loader2 v-if="ocupado === c.id" class="mx-2 size-4 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="trabalhando" />
                <NfseMenu
                  v-else-if="menus.get(c.id)?.length"
                  :itens="menus.get(c.id) ?? []"
                  :disabled="!!ocupado || !!acaoLote"
                  :rotulo="`ações da compra nº ${c.numero}`"
                  @escolher="(a: string) => escolher(c, a)"
                />
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td colspan="8" class="text-right text-sm text-muted-foreground">
                {{ plural(compras.filter((c) => c.status !== 'cancelada').length, 'compra', 'compras') }} (sem as canceladas):
                <span class="ml-1 text-base font-semibold text-foreground tabular-nums">{{ fmtBrl(totalVisivel) }}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>

    <!-- Barra fixa: imprimir / baixar as NFs marcadas (como em Notas emitidas) -->
    <CompraNfBarra
      :marcadas="marcadasNf"
      :acao="acaoLote"
      @imprimir="acaoArquivos('imprimir')"
      @baixar="acaoArquivos"
      @limpar="limparSelecao"
    />

    <NfseDialog
      :open="!!nfAberta"
      :titulo="nfAberta ? `NF da compra nº ${nfAberta.numero}` : 'NF da compra'"
      descricao="O número da NF que saiu no Bling de quem vendeu."
      tamanho="sm"
      :icone="nfAberta?.nf_numero ? FileX2 : Hash"
      :fechavel="!nfSalvando"
      foco-inicial="#compra-nf-numero"
      @update:open="(v: boolean) => { if (!v) nfAberta = null }"
    >
      <form id="compra-nf" class="space-y-2" @submit.prevent="salvarNf">
        <label for="compra-nf-numero" class="text-sm font-medium">Número da NF</label>
        <input
          id="compra-nf-numero"
          v-model="nfNumero"
          type="text"
          inputmode="numeric"
          autocomplete="off"
          maxlength="20"
          class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :class="nfErro && 'border-red-500 dark:border-red-400'"
        />
        <template v-if="nfComChave">
          <label for="compra-nf-chave" class="block pt-1 text-sm font-medium">
            Chave de acesso <span class="font-normal text-muted-foreground">(44 números)</span>
          </label>
          <input
            id="compra-nf-chave"
            v-model="nfChave"
            type="text"
            inputmode="numeric"
            autocomplete="off"
            maxlength="60"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <p class="text-xs text-muted-foreground">
            A do Bling 44 (o robô já preenche quando trouxe). Com ela a compra guarda a NF certa: copiar chave e XML.
          </p>
        </template>
        <p v-if="nfErro" class="text-xs text-red-600 dark:text-red-400">{{ nfErro }}</p>
      </form>
      <template #rodape>
        <Button type="button" variant="outline" size="sm" :disabled="nfSalvando" @click="nfAberta = null">Voltar</Button>
        <Button type="submit" form="compra-nf" size="sm" :disabled="nfSalvando">
          <Loader2 v-if="nfSalvando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          salvar
        </Button>
      </template>
    </NfseDialog>
  </div>
</template>
