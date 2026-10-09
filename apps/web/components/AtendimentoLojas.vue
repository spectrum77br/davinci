<script lang="ts">
// O CORTE pela plataforma do topo (09/10/2026, Eduardo: "quando selecionar a
// plataforma lá em cima, corta as outras ali da listagem: deixa só as lojas da
// plataforma mesmo naquele navegável na esquerda"). Funções puras aqui em
// cima (testadas em tests/atendimento-filtro-plataforma.cjs e
// tests/atendimento-caixa-humano.cjs):
// - quem corta é ESCOLHER A PLATAFORMA (`filtros.plataforma_topo`): no menu
//   do topo (AtendimentoFiltroPlataforma) ou no NOME do grupo aqui na barra.
//   Clicar numa LOJA na barra filtra a lista, mas não corta — senão, em
//   "Todas", o primeiro clique numa loja Shopee sumia com as outras
//   plataformas e a pessoa tinha de voltar ao menu para ir a uma loja TikTok;
// - o botão do topo mostra o CORTE (`chipDoTopo`): com uma loja clicada na
//   barra inteira, ele continua "Todas" — botão e barra nunca dizem coisas
//   diferentes;
// - só corta enquanto o filtro da lista está DENTRO da plataforma do topo
//   (`corteDaBarra`: a plataforma inteira ou uma loja dela). Se o filtro saiu
//   dela por outro caminho (um link), a barra volta inteira;
// - "Redes" corta para Instagram + Facebook (as duas, com os cabeçalhos);
// - cortada, o número de "Todas · Shopee" é o da plataforma no /resumo (o
//   mesmo do botão do topo e da lista: conta também a conversa sem loja
//   identificada, que não tem linha na barra).
import { GRUPOS_CAIXA, aguardandoDe, corteDaBarra, plataformasDoValor, topoDoValor } from '~/components/AtendimentoFiltroPlataforma.vue'
import type { FiltrosLista } from '~/components/AtendimentoLista.vue'
import { nomeDoGrupo } from '~/components/AtendimentoPlataforma.vue'

// As plataformas que a barra mostra ([] = todas) — a régua mora no menu do topo.
export { corteDaBarra }
export function gruposVisiveis<G extends { plataforma: string }>(grupos: G[], corte: string[]): G[] {
  return corte.length ? grupos.filter((g) => corte.includes(g.plataforma)) : grupos
}
// "Shopee", "Redes" — o nome do menu do topo para o corte.
export function nomeDoCorte(corte: string[]): string {
  const g = GRUPOS_CAIXA.find((x) => x.plataformas.join(',') === corte.join(','))
  return g ? g.nome : corte.map((p) => nomeDoGrupo(p)).join(' + ')
}
// A linha "Todas · Shopee" está acesa: a plataforma do corte inteira, sem
// loja, site ou conta escolhidos.
export function ativaTodasDoCorte(f: FiltrosLista, corte: string[]): boolean {
  return corte.length > 0 && plataformasDoValor(f.plataforma).join(',') === corte.join(',')
    && !f.integration_id && !f.externo_ref && !f.rede_social_id
}
// O número de "Todas · Shopee": o da plataforma no /resumo (o "falta
// responder", ou o da Caixa Humano).
// (Os tipos pelo `aguardandoDe`: o Resumo e o NumeroDaCaixa já são importados no setup.)
export function numeroDoCorte(resumo: Parameters<typeof aguardandoDe>[0], corte: string[], numero?: string | null): number {
  return aguardandoDe(resumo, corte, numero === 'humano' ? 'humano' : 'aguardando')
}
// O clique no NOME do grupo: a plataforma inteira E o corte (é escolher a
// plataforma, como no menu do topo). Instagram ou Facebook cortam para Redes
// (o grupo do menu). A caixa (Pergunta/Pós-venda do ML) fica na mesma plataforma.
export function filtrosDoGrupo(f: FiltrosLista, plataforma: string): FiltrosLista {
  return {
    ...f,
    plataforma,
    integration_id: '',
    externo_ref: '',
    rede_social_id: '',
    canal: f.plataforma === plataforma ? f.canal : '',
    plataforma_topo: topoDoValor(plataforma),
  }
}
// O clique em "Todas · Shopee": a plataforma do corte inteira; a caixa
// (Pergunta/Pós-venda do ML) e o tipo do chamado do Site ficam se a
// plataforma não mudou.
export function filtrosTodasDoCorte(f: FiltrosLista, corte: string[]): FiltrosLista {
  const valor = corte.join(',')
  const mesma = plataformasDoValor(f.plataforma).join(',') === valor
  return {
    ...f,
    plataforma: valor,
    integration_id: '',
    externo_ref: '',
    rede_social_id: '',
    canal: mesma ? f.canal : '',
    tipo_chamado: mesma ? f.tipo_chamado : '',
  }
}
</script>

<script setup lang="ts">
// Barra de lojas da Caixa (Atendimento, 28/09/2026) — a coluna estreita da
// esquerda do Duoke: "Todas" no topo e as lojas agrupadas por plataforma, cada
// uma com o ícone da plataforma, o nome e a bolinha VERMELHA com as conversas
// esperando resposta (01/10/2026: antes era o "não lida" da plataforma, que a
// Shopee zera quando o robô do Duoke responde sozinho — a loja ficava sem
// bolinha com comprador esperando, ou com bolinha e a lista vazia). O número
// da plataforma continua no title. Clicar filtra a lista.
// Loja que o DaVinci não consegue ler (TikTok sem escopo, canal com erro)
// aparece apagada, com cadeado/alerta e o motivo no title — ninguém acha que
// "não tem mensagem" quando na verdade não dá para ler. Loja com UM canal
// lendo e outro com problema (ML com Perguntas ok e Pós-venda sem permissão)
// continua acesa, com o número e um alerta pequeno: apagar a loja inteira
// esconderia as não lidas das perguntas que o DaVinci está lendo.
// Recolhida (como no print do Duoke) fica só o ícone e o número.
// Temu e AliExpress (30/09/2026) vêm do robô do Mac mini: sem sinal dele há
// alguns minutos, o backend marca a loja como "leitura parada" e ela apaga
// aqui com o ícone de tomada solta — o Seller Center continua recebendo, só
// não chega ao DaVinci.
// Sites e redes (02/10/2026): o grupo "Sites" (Charlots e Uranyx — o
// carrinho abandonado), e o Instagram/Facebook com UMA linha por conta (antes
// era uma linha "Direct" juntando 7buyers, Charlots e Uranyx): a linha da
// conta soma o Direct e os comentários dela. Nenhuma tem integração: a linha
// filtra a lista pela origem (`externo_ref`) ou pela conta
// (`rede_social_id`). A conta com Direct esperando fica ACESA mesmo com a
// caixa de comentários sem permissão (token sem o escopo novo): o Direct
// continua sendo lido — apagar a linha esconderia a bolinha dele.
// O NOME da plataforma (cabeçalho do grupo) filtra a plataforma inteira —
// todas as lojas dela (08/10/2026: "ver só Mercado Livre, só Shopee"). Com a
// barra recolhida o cabeçalho vira uma etiqueta curta, clicável do mesmo
// jeito (antes era só um traço). Os chips do topo da lista fazem o mesmo.
// Plataforma escolhida no MENU DO TOPO ou pelo NOME do grupo (09/10/2026): a
// barra mostra só as lojas dela, com "Todas · Shopee" no lugar de "Todas"
// (clicar mostra a plataforma inteira; o número é o dela). Com uma
// plataforma só, o cabeçalho do grupo some (repetiria a linha "Todas ·
// Shopee"). Para ver todas as plataformas de novo: "Todas" no menu do topo.
// Na CAIXA HUMANO (`numero="humano"`, 09/10/2026) as bolinhas contam as
// conversas que esperam uma pessoa (a IA não pode responder), não o "falta
// responder".
import { ChevronsLeft, ChevronsRight, Inbox, Lock, TriangleAlert, Unplug } from 'lucide-vue-next'
import type { NumeroDaCaixa } from '~/components/AtendimentoFiltroPlataforma.vue'
import {
  leituraParada,
  plataformaInfo,
  sellerCenterDe,
  semLeitura,
  statusCanalCodigo,
  statusCanalInfo,
  viaRobo,
  type Resumo,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ resumo: Resumo | null; numero?: NumeroDaCaixa }>()
const filtros = defineModel<FiltrosLista>('filtros', { required: true })
const recolhida = defineModel<boolean>('recolhida', { default: false })

// Ordem do Duoke: Shopee, TikTok, Mercado Livre; depois as que o Duoke não
// tem — Amazon, Magalu (por API, como as de cima), as lojas do robô (Temu,
// AliExpress) —, os sites e as redes (Instagram com o Direct, Facebook).
const ORDEM = ['shopee', 'tiktok', 'ml', 'amazon', 'magalu', 'temu', 'aliexpress', 'site', 'instagram', 'facebook']

type LojaBarra = {
  // Chave da linha: o integration_id; a loja do robô que vier sem integração
  // (o robô conhece a loja pelo perfil do AdsPower) usa plataforma + nome; o
  // site, a origem externa; a conta de rede, a conta do cadastro.
  chave: string
  // '' = loja sem integração: a lista filtra pela origem/conta abaixo, ou
  // (loja do robô) só pela plataforma.
  integration_id: string
  // Site: "site:charlots" (todas as caixas do site).
  externo_ref: string
  // Conta de rede social: o Direct e os comentários dela.
  rede_social_id: string
  // Quantas das esperando são do Direct (só no title).
  direct: number
  // Quantas conversas de Direct a conta tem (qualquer situação): com alguma,
  // a linha fica acesa mesmo com a caixa de comentários sem leitura.
  direct_total: number
  // A conta tem caixa de comentários (canal no /resumo) — sem ela, a linha é
  // só o Direct (7buyers).
  comentarios: boolean
  plataforma: string
  conta: string
  // A bolinha: conversas esperando resposta (= `aguardando`) — na Caixa
  // Humano, as que esperam uma pessoa (= `humano`).
  nao_lidas: number
  // O "não lida" que a própria plataforma informa (só no title).
  nao_lidas_plataforma: number | null
  aguardando: number
  // Na Caixa Humano (falta responder e a IA não pode).
  humano: number
  vencidas: number
  status: string
  motivo: string | null
  // Nenhum canal da loja está sendo lido (apagada) × algum canal com
  // problema e outro lendo (parcial: acesa, com alerta).
  apagada: boolean
  parcial: boolean
}

// As lojas vêm do /resumo (a API nova manda todas, com zero, e o pior estado
// entre os canais da loja). A antiga só mandava as que tinham conversa: as
// que faltam saem dos canais do mesmo resumo, e o status também.
const lojas = computed<LojaBarra[]>(() => {
  const r = props.resumo
  if (!r) return []
  const mapa = new Map<string, LojaBarra>()
  const statusDosCanais = new Map<string, string[]>()
  // As contas de rede que têm caixa de comentários (o canal externo dela).
  const contasComCaixa = new Set<string>()
  for (const c of r.canais || []) {
    if (!c.integration_id && c.rede_social_id) contasComCaixa.add(c.rede_social_id)
    if (!c.integration_id) continue
    statusDosCanais.set(c.integration_id, [...(statusDosCanais.get(c.integration_id) || []), c.status])
  }
  for (const l of r.lojas || []) {
    // Sem integração só entram a loja do robô (tem nome e sinal próprio), o
    // site e a conta de rede (têm por onde filtrar) e o Direct de conta que
    // saiu do cadastro ("Direct (conta fora do cadastro)": clicar filtra o
    // Instagram inteiro — sem a linha, a soma das contas não batia com o
    // número da plataforma). A conversa de loja que saiu do DaVinci ou da
    // Amazon sem conta não vira linha — não há o que filtrar por ela.
    const semId = !l.integration_id
    const rede = semId ? l.rede_social_id || '' : ''
    const externo = semId && !rede ? l.externo_ref || '' : ''
    const directSemConta = semId && !rede && !externo && l.plataforma === 'instagram' && (Number(l.direct_total) > 0 || Number(l.direct_aguardando) > 0)
    if (semId && !rede && !externo && !directSemConta && !(viaRobo(l.plataforma) && l.conta)) continue
    const chave = l.integration_id
      || (rede ? `rede:${rede}` : externo ? `ext:${externo}` : `semid:${l.plataforma}:${l.conta}`)
    mapa.set(chave, {
      chave,
      integration_id: l.integration_id || '',
      externo_ref: externo,
      rede_social_id: rede,
      direct: Number(l.direct_aguardando) || 0,
      direct_total: Number(l.direct_total) || 0,
      comentarios: !!rede && contasComCaixa.has(rede),
      plataforma: l.plataforma,
      conta: l.conta || 'sem nome',
      nao_lidas: (props.numero === 'humano' ? Number(l.humano) : l.aguardando) || 0,
      nao_lidas_plataforma: typeof l.nao_lidas === 'number' ? l.nao_lidas : null,
      aguardando: l.aguardando || 0,
      humano: Number(l.humano) || 0,
      vencidas: l.vencidas || 0,
      status: l.status_canal || '',
      motivo: l.status_motivo || null,
      apagada: false,
      parcial: false,
    })
  }
  for (const c of r.canais || []) {
    if (!c.integration_id || mapa.has(c.integration_id)) continue
    mapa.set(c.integration_id, {
      chave: c.integration_id,
      integration_id: c.integration_id,
      externo_ref: '',
      rede_social_id: '',
      direct: 0,
      direct_total: 0,
      comentarios: false,
      plataforma: c.plataforma,
      conta: c.conta || 'sem nome',
      nao_lidas: 0,
      nao_lidas_plataforma: null,
      aguardando: 0,
      humano: 0,
      vencidas: 0,
      status: '',
      motivo: null,
      apagada: false,
      parcial: false,
    })
  }
  for (const l of mapa.values()) {
    const sts = (l.integration_id ? statusDosCanais.get(l.integration_id) : null) || []
    if (!l.status) l.status = sts.find((s) => semLeitura(s)) || sts[0] || 'ok'
    l.status = statusCanalCodigo(l.status)
    // O `status_canal` da API é o PIOR entre os canais; se os canais vieram
    // no mesmo resumo, dá para saber se algum ainda está lendo. A leitura
    // parada do robô vale para a loja inteira (é o Mac mini que parou), mesmo
    // que o canal diga outra coisa.
    l.apagada = leituraParada(l.status) || (sts.length ? sts.every((s) => semLeitura(s)) : semLeitura(l.status))
    l.parcial = !l.apagada && (semLeitura(l.status) || sts.some((s) => semLeitura(s)))
    // Conta do Instagram: o status é o da caixa de COMENTÁRIOS; o Direct é
    // outra leitura (o adaptador só lê o que o robô de DM grava) e continua.
    // Com Direct (esperando ou não), a linha fica acesa com o alerta, e a
    // bolinha conta. `direct_total` é do /resumo novo; o antigo só tinha o
    // `direct_aguardando`.
    if (l.apagada && l.rede_social_id && (l.direct > 0 || l.direct_total > 0)) {
      l.apagada = false
      l.parcial = true
    }
  }
  return [...mapa.values()]
})

type Grupo = { plataforma: string; nome: string; lojas: LojaBarra[]; nao_lidas: number }
const grupos = computed<Grupo[]>(() => {
  const porPlat = new Map<string, LojaBarra[]>()
  for (const l of lojas.value) porPlat.set(l.plataforma, [...(porPlat.get(l.plataforma) || []), l])
  const ordem = [...ORDEM, ...[...porPlat.keys()].filter((p) => !ORDEM.includes(p)).sort()]
  return ordem
    .filter((p) => porPlat.has(p))
    .map((p) => {
      const ls = (porPlat.get(p) || []).slice().sort((a, b) => a.conta.localeCompare(b.conta, 'pt-BR'))
      return { plataforma: p, nome: nomeDoGrupo(p), lojas: ls, nao_lidas: ls.reduce((s, l) => s + (l.apagada ? 0 : l.nao_lidas), 0) }
    })
})
// O corte pela plataforma do topo ([] = a barra inteira) e os grupos à mostra.
const corte = computed(() => corteDaBarra(filtros.value))
const visiveis = computed(() => gruposVisiveis(grupos.value, corte.value))
// Uma plataforma só no corte: o cabeçalho do grupo repetiria "Todas · Shopee".
const semCabecalho = computed(() => corte.value.length === 1)
const nomeCorte = computed(() => (corte.value.length ? nomeDoCorte(corte.value) : ''))
// Cortada: o número da plataforma (o mesmo do botão do topo e da lista);
// inteira: a soma das lojas, como sempre.
const total = computed(() => (corte.value.length
  ? numeroDoCorte(props.resumo, corte.value, props.numero)
  : visiveis.value.reduce((s, g) => s + g.nao_lidas, 0)))
// API ANTIGA (sem uma linha por conta): o Direct do Instagram não tem loja
// no resumo e aparece como uma linha só, com o "aguardando" da plataforma.
// Só quando o /resumo traz a plataforma — o backend só a manda para quem vê
// todas as equipes e havendo DM; sem essa condição, quem tem escopo restrito
// via "Direct" e, ao clicar, uma lista sempre vazia. Com o filtro já no
// Instagram (lembrado no navegador) a linha fica, para a pessoa ver o que
// está escolhido e sair. A API nova manda as contas em `lojas` (grupo
// Instagram, acima) e esta linha some.
const plataformaInstagram = computed(() => props.resumo?.plataformas?.find((p) => p.plataforma === 'instagram') ?? null)
const temContasInstagram = computed(() => grupos.value.some((g) => g.plataforma === 'instagram'))
const instagram = computed(() => (props.numero === 'humano' ? Number(plataformaInstagram.value?.humano) || 0 : plataformaInstagram.value?.aguardando ?? 0))
const mostrarInstagram = computed(() => !!props.resumo && !temContasInstagram.value && (!!plataformaInstagram.value || filtros.value.plataforma === 'instagram')
  && (!corte.value.length || corte.value.includes('instagram')))

function contador(n: number) {
  return n > 99 ? '99+' : String(n)
}
function motivo(l: LojaBarra): string {
  const partes = [`${plataformaInfo(l.plataforma).nome} · ${l.conta}`]
  if (l.apagada) {
    const st = statusCanalInfo(l.status)
    partes.push(st ? `${st.label}: ${st.hint}` : l.status)
    if (l.motivo) partes.push(l.motivo)
  } else {
    partes.push(`${l.aguardando} conversa(s) esperando resposta`)
    if (l.humano || props.numero === 'humano') partes.push(`${l.humano} na Caixa Humano (a IA não pode responder)`)
    if (typeof l.nao_lidas_plataforma === 'number') partes.push(`${l.nao_lidas_plataforma} não lida(s) segundo a plataforma`)
    if (l.vencidas) partes.push(`${l.vencidas} vencida(s)`)
    if (l.parcial) partes.push(l.motivo ? `Atenção — ${l.motivo}` : 'Atenção: um dos canais desta loja não está sendo lido')
  }
  const sc = sellerCenterDe(l.plataforma)
  if (sc) partes.push(`Lida pelo robô do Mac mini (AdsPower) — a resposta é no ${sc.nome}`)
  if (l.plataforma === 'instagram' && !l.integration_id && !l.rede_social_id) {
    partes.push(`Direct de conta que não está em Cadastros › Redes Sociais: ${l.direct} esperando (só leitura)`)
  }
  if (l.plataforma === 'instagram' && l.rede_social_id) {
    partes.push(
      l.comentarios
        ? `Direct: ${l.direct} esperando (só leitura) · comentários: ${Math.max(0, l.aguardando - l.direct)} esperando`
        : `Direct: ${l.direct} esperando (só leitura) — a conta não tem leitura de comentários`,
    )
  }
  if (l.plataforma === 'site') partes.push('Carrinho abandonado do lojista no site (lido do site, servidor a servidor)')
  // Sem integração nem origem, a lista não separa esta loja das outras da plataforma.
  if (!l.integration_id && !l.externo_ref && !l.rede_social_id && (grupos.value.find((g) => g.plataforma === l.plataforma)?.lojas.length ?? 0) > 1) {
    partes.push(`Clicar mostra as conversas de todas as lojas ${plataformaInfo(l.plataforma).nome}`)
  }
  return partes.join('\n')
}

function escolherTodas() {
  // Barra cortada: "Todas · Shopee" = a Shopee inteira (sair do corte é no menu do topo).
  if (corte.value.length) {
    filtros.value = filtrosTodasDoCorte(filtros.value, corte.value)
    return
  }
  filtros.value = { ...filtros.value, plataforma: '', integration_id: '', canal: '', externo_ref: '', rede_social_id: '' }
}
function escolherPlataforma(p: string) {
  filtros.value = { ...filtros.value, plataforma: p, integration_id: '', externo_ref: '', rede_social_id: '', canal: filtros.value.plataforma === p ? filtros.value.canal : '' }
}
// O NOME do grupo: escolher a plataforma — filtra e corta a barra (como o
// menu do topo). A loja do robô sem integração e a linha antiga do Direct
// usam `escolherPlataforma` (são lojas: não cortam).
function escolherGrupo(p: string) {
  filtros.value = filtrosDoGrupo(filtros.value, p)
}
function escolherLoja(l: LojaBarra) {
  // Site e conta de rede: a lista filtra pela origem (todas as caixas do
  // site) ou pela conta (o Direct e os comentários dela).
  if (!l.integration_id && (l.rede_social_id || l.externo_ref)) {
    filtros.value = {
      ...filtros.value,
      plataforma: l.plataforma,
      integration_id: '',
      canal: '',
      externo_ref: l.rede_social_id ? '' : l.externo_ref,
      rede_social_id: l.rede_social_id,
    }
    return
  }
  // Loja do robô sem integração: o GET /conversas só filtra por integração,
  // então o que dá é a plataforma inteira.
  if (!l.integration_id) {
    escolherPlataforma(l.plataforma)
    return
  }
  filtros.value = {
    ...filtros.value,
    plataforma: l.plataforma,
    integration_id: l.integration_id,
    externo_ref: '',
    rede_social_id: '',
    // Mesma plataforma: a caixa escolhida (Pergunta/Pós-venda do ML) continua.
    canal: filtros.value.plataforma === l.plataforma ? filtros.value.canal : '',
  }
}
const ativaTodas = computed(() => (corte.value.length ? ativaTodasDoCorte(filtros.value, corte.value) : !filtros.value.plataforma))
const tituloTodas = computed(() => {
  const n = props.numero === 'humano' ? `${total.value} esperando uma pessoa (a IA não pode responder)` : `${total.value} não lida(s)`
  return corte.value.length
    ? `Todas as lojas ${nomeCorte.value} — ${n}\nPara ver as outras plataformas: "Todas" no menu Plataforma, acima`
    : `Todas as lojas — ${n}`
})
// A plataforma inteira escolhida — sozinha ou num grupo dos chips ("Redes" =
// "instagram,facebook" acende Instagram e Facebook).
function ativaPlataforma(p: string) {
  return (filtros.value.plataforma || '').split(',').includes(p) && !filtros.value.integration_id && !filtros.value.externo_ref && !filtros.value.rede_social_id
}
function ativaLoja(l: LojaBarra) {
  if (l.rede_social_id) return filtros.value.rede_social_id === l.rede_social_id
  if (!l.integration_id && l.externo_ref) return filtros.value.externo_ref === l.externo_ref
  if (!l.integration_id) return ativaPlataforma(l.plataforma)
  return filtros.value.integration_id === l.integration_id
}
</script>

<template>
  <nav class="flex min-h-0 flex-1 flex-col" aria-label="lojas">
    <div class="flex shrink-0 items-center border-b px-2 py-1.5" :class="recolhida ? 'justify-center' : 'justify-between'">
      <span v-if="!recolhida" class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Lojas</span>
      <button
        type="button"
        class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        :title="recolhida ? 'mostrar o nome das lojas' : 'recolher a barra de lojas'"
        :aria-label="recolhida ? 'mostrar o nome das lojas' : 'recolher a barra de lojas'"
        @click="recolhida = !recolhida"
      >
        <ChevronsRight v-if="recolhida" class="size-4" />
        <ChevronsLeft v-else class="size-4" />
      </button>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto py-1">
      <!-- Todas -->
      <button
        type="button"
        class="relative mx-1 flex w-[calc(100%-0.5rem)] items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]"
        :class="[ativaTodas ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-muted', recolhida ? 'justify-center gap-1 px-1' : '']"
        :aria-pressed="ativaTodas"
        :title="tituloTodas"
        data-lojas-todas
        @click="escolherTodas"
      >
        <AtendimentoIconePlataforma v-if="corte.length === 1" :plataforma="corte[0]" :tamanho="recolhida ? 18 : 16" decorativo />
        <Inbox v-else class="size-4 shrink-0" />
        <span v-if="!recolhida" class="min-w-0 flex-1 truncate">{{ corte.length ? `Todas · ${nomeCorte}` : 'Todas' }}</span>
        <span
          v-if="total"
          class="flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-none text-white tabular-nums"
          :class="recolhida ? 'h-4 min-w-4 px-0.5 text-[9px]' : ''"
        >{{ contador(total) }}</span>
      </button>

      <div v-if="!resumo" class="space-y-1.5 px-2 py-2" aria-busy="true">
        <div v-for="i in 8" :key="i" class="h-6 animate-pulse rounded bg-muted" />
      </div>

      <template v-for="g in visiveis" :key="g.plataforma">
        <!-- cabeçalho do grupo: filtra a plataforma inteira (cortada numa
             plataforma só, some: a linha "Todas · Shopee" já faz isso) -->
        <template v-if="!semCabecalho">
          <button
            v-if="!recolhida"
            type="button"
            class="mx-1 mt-2 flex w-[calc(100%-0.5rem)] items-center gap-1.5 rounded px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-wider"
            :class="ativaPlataforma(g.plataforma) ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'"
            :aria-pressed="ativaPlataforma(g.plataforma)"
            :title="`Só ${g.nome}: todas as lojas ${g.nome}`"
            :data-grupo="g.plataforma"
            @click="escolherGrupo(g.plataforma)"
          >
            <AtendimentoIconePlataforma :plataforma="g.plataforma" :tamanho="14" decorativo />
            <span class="min-w-0 flex-1 truncate underline-offset-2 hover:underline">{{ g.nome }}</span>
            <span v-if="g.nao_lidas" class="font-semibold normal-case tracking-normal text-red-600 tabular-nums dark:text-red-400">{{ contador(g.nao_lidas) }}</span>
          </button>
          <!-- recolhida: a etiqueta curta da plataforma, clicável do mesmo jeito -->
          <button
            v-else
            type="button"
            class="mx-1 mt-1.5 block w-[calc(100%-0.5rem)] truncate rounded border-t px-0.5 pb-0.5 pt-1 text-center text-[9px] font-semibold uppercase leading-none tracking-tight"
            :class="ativaPlataforma(g.plataforma) ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'"
            :aria-pressed="ativaPlataforma(g.plataforma)"
            :aria-label="`Só ${g.nome}: todas as lojas ${g.nome}`"
            :title="`Só ${g.nome}: todas as lojas ${g.nome}${g.nao_lidas ? ` — ${g.nao_lidas} ${numero === 'humano' ? 'esperando uma pessoa' : 'esperando resposta'}` : ''}`"
            :data-grupo="g.plataforma"
            @click="escolherGrupo(g.plataforma)"
          >{{ plataformaInfo(g.plataforma).curto }}</button>
        </template>

        <button
          v-for="l in g.lojas"
          :key="l.chave"
          type="button"
          class="relative mx-1 flex w-[calc(100%-0.5rem)] items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]"
          :class="[
            ativaLoja(l) ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-muted',
            l.apagada ? 'opacity-50' : '',
            recolhida ? 'justify-center gap-1 px-1' : '',
          ]"
          :aria-pressed="ativaLoja(l)"
          :title="motivo(l)"
          @click="escolherLoja(l)"
        >
          <AtendimentoIconePlataforma :plataforma="l.plataforma" :tamanho="recolhida ? 18 : 15" decorativo />
          <span v-if="!recolhida" class="min-w-0 flex-1 truncate">{{ l.conta }}</span>
          <template v-if="l.apagada">
            <Lock v-if="l.status === 'sem_escopo' || l.status === 'desligado'" class="size-3.5 shrink-0 text-muted-foreground" />
            <Unplug v-else-if="leituraParada(l.status)" class="size-3.5 shrink-0 text-red-600 dark:text-red-400" aria-label="leitura parada" />
            <TriangleAlert v-else class="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
          </template>
          <TriangleAlert v-if="l.parcial && !recolhida" class="size-3 shrink-0 text-amber-600 dark:text-amber-400" />
          <span
            v-if="!l.apagada && l.nao_lidas"
            class="flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-none text-white tabular-nums"
            :class="recolhida ? 'h-4 min-w-4 px-0.5 text-[9px]' : ''"
          >{{ contador(l.nao_lidas) }}</span>
        </button>
      </template>

      <!-- Instagram (Direct, só leitura) — só com a API antiga, sem as contas -->
      <template v-if="mostrarInstagram">
        <div v-if="recolhida" class="mx-2 mt-1.5 border-t pt-1.5" />
        <div v-else class="mx-1 mt-2 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Instagram</div>
        <button
          type="button"
          class="relative mx-1 flex w-[calc(100%-0.5rem)] items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]"
          :class="[ativaPlataforma('instagram') ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-muted', recolhida ? 'justify-center gap-1 px-1' : '']"
          :aria-pressed="ativaPlataforma('instagram')"
          title="Direct do Instagram — só leitura no Atendimento"
          @click="escolherPlataforma('instagram')"
        >
          <AtendimentoIconePlataforma plataforma="instagram" :tamanho="recolhida ? 18 : 15" decorativo />
          <span v-if="!recolhida" class="min-w-0 flex-1 truncate">Direct</span>
          <span
            v-if="instagram"
            class="flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-none text-white tabular-nums"
            :class="recolhida ? 'h-4 min-w-4 px-0.5 text-[9px]' : ''"
          >{{ contador(instagram) }}</span>
        </button>
      </template>
    </div>
  </nav>
</template>
