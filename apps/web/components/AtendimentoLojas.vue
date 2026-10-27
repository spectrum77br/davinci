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
import { GRUPOS_CAIXA, aguardandoDe, corteDaBarra, numeroDe, plataformasDoValor, topoDoValor } from '~/components/AtendimentoFiltroPlataforma.vue'
import type { FiltrosLista } from '~/components/AtendimentoLista.vue'
import { lojaNosModos, nomeDoGrupo } from '~/components/AtendimentoPlataforma.vue'

// Na CAIXA HUMANO a barra só lista as lojas nos modos dela (14/10/2026, o
// dono: "mostre também só as que estão ativadas … por cada lugar, separado
// Shopee, ML"): a loja fora do Automático some da barra, cada plataforma no
// seu grupo. Fica a que ainda tem conversa contada lá (nenhum número some da
// tela). `modos` null = toda loja (a Caixa Humano de todas, ou a API antiga).
export function lojasDaCaixaHumano<L extends { integration_id?: string | null; externo_ref?: string | null; rede_social_id?: string | null; humano?: number }>(
  lojas: L[],
  canais: Parameters<typeof lojaNosModos>[0],
  modos: string[] | null,
): L[] {
  if (!modos) return lojas
  return lojas.filter((l) => (Number(l.humano) || 0) > 0 || lojaNosModos(canais, l, modos) === true)
}
// As plataformas que a barra mostra ([] = todas) — a régua mora no menu do topo
// (e o número de uma linha do /resumo também).
export { corteDaBarra, numeroDe }
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
// O campo do /resumo que a barra mostra: o da Caixa Humano, o do Duoke (a
// Caixa, desde 14/10/2026) ou o "falta responder" (sem `numero`).
// (Os tipos pelo `aguardandoDe`: o Resumo e o NumeroDaCaixa já são importados no setup.)
export function campoDaBarra(numero?: string | null): NonNullable<Parameters<typeof aguardandoDe>[2]> {
  return numero === 'humano' || numero === 'nao_lidas_duoke' ? numero : 'aguardando'
}
// O número de "Todas · Shopee": o da plataforma no /resumo (o do Duoke, o
// "falta responder", ou o da Caixa Humano).
export function numeroDoCorte(resumo: Parameters<typeof aguardandoDe>[0], corte: string[], numero?: string | null): number {
  return aguardandoDe(resumo, corte, campoDaBarra(numero))
}
// A bolinha da loja: o mesmo campo, da linha da loja no /resumo.
export function numeroDaLoja(loja: Parameters<typeof numeroDe>[0], numero?: string | null): number {
  return numeroDe(loja, campoDaBarra(numero))
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
// (Pergunta/Pós-venda do ML) fica se a plataforma não mudou.
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
  }
}

// ─── Os E-MAILS de cada loja (27/10/2026) ───────────────────────────────────
// O Eduardo, 27/10: "vamos mudar um pouco a disposição da caixa de
// atendimento: botar um AMPLIÁVEL que vai abrir os e-mails embaixo de cada
// loja … então poderá ampliar e visualizar os e-mails ali pelo atendimento
// mesmo de cada loja". No mesmo dia: embaixo de cada loja, as PASTAS do Tuta
// que ele unificou — Problemas, Envios, Devolução e Informativo —, cada uma
// com "opção de encolher ou abrir", só com os e-mails daquela loja; TODA loja
// tem as caixas, mesmo vazias, e a vazia diz POR QUÊ (em tese toda loja recebe
// e-mail: zero é um vínculo que falta). A ESCOLHA NÃO MORA AQUI: é a constante
// GRUPOS_EMAIL_NA_BARRA_DE_LOJAS da API (apps/api/app/services/mail_atendimento/
// constantes.py), que chega em `grupos_na_barra` (GET /api/atendimento/email/
// contagens?por_loja=true) com o status de cada pasta na ponte, junto dos
// números de cada loja. A aba E-mail é só de admin — quem não é admin vê
// e-mail SÓ por aqui. A seta da loja abre as caixas; cada caixa abre (e
// lembra, por pessoa) a lista curta dos e-mails dela, carregada SÓ quando ela
// abre (AtendimentoLojaGrupoEmails); "ver todos" e o clique num e-mail abrem a
// lista da loja na própria Caixa (a lista do meio — AtendimentoLojaEmails — e
// a conversa). A loja sem integração (a ficha: sem chat no DaVinci) é uma
// linha como as outras no grupo da plataforma, com as mesmas caixas: o
// clique nela abre as caixas. O chat da loja e a bolinha dela NÃO mudam: o
// e-mail continua fora da lista e dos números da Caixa (`separa_email`).
export type StatusDaPasta = 'liga' | 'sem_categoria' | 'outra_categoria' | 'so_contar' | 'sem_pasta'
export type GrupoDaBarra = { chave: string; rotulo: string; pastas: string[]; status: StatusDaPasta | string }
// Um grupo da loja: os e-mails da caixa nos últimos dias (o número; a lista
// mostra os mesmos), as conversas abertas, as esperando resposta (a bolinha) e
// todas as conversas (as fechadas também).
export type ContaDoGrupo = { emails: number; abertas: number; aguardando: number; total: number }
// O endereço da loja para a ponte (o porquê da caixa vazia).
export type AliasDaLoja = 'na_ponte' | 'fora_da_caixa' | 'sem_cadastro'
export type EmailsDaLoja = {
  integration_id: string | null
  store_info_id: string | null
  plataforma: string | null
  // O nome da loja (o da integração ou o da ficha).
  loja: string | null
  alias?: AliasDaLoja | string | null
  // Os e-mails da loja nos últimos dias, em qualquer pasta (o diagnóstico).
  emails_30d?: number
  grupos: Partial<Record<string, ContaDoGrupo>>
}
// O que o clique numa caixa manda para a página (a loja da linha + o grupo).
export type EscolhaEmail = {
  chave: string
  integration_id: string
  store_info_id: string
  plataforma: string
  conta: string
  grupo: string
}
// Um e-mail da caixa (GET /api/atendimento/email/lojas/emails): sem o texto.
export type EmailDaCaixa = {
  id: string
  estado: string
  recebido_em: string | null
  pasta: string | null
  assunto: string | null
  de: string | null
  de_nome?: string | null
  seguranca?: boolean
  anexos?: number
  conversa_id: string | null
  aguardando_resposta?: boolean
  situacao?: string | null
  comprador_nome?: string | null
}
type LinhaDaBarra = { chave: string; integration_id?: string | null; externo_ref?: string | null; rede_social_id?: string | null; plataforma: string; conta: string }

// Os grupos que a barra mostra, na ordem da API (sem a lista — a API antiga —: nenhum).
export function gruposDaBarra(grupos: readonly GrupoDaBarra[] | null | undefined): GrupoDaBarra[] {
  const vistos = new Set<string>()
  return (Array.isArray(grupos) ? grupos : []).filter((g) => {
    if (!g || typeof g.chave !== 'string' || !g.chave || vistos.has(g.chave)) return false
    vistos.add(g.chave)
    return true
  })
}
function nomeComparavel(v: string | null | undefined): string {
  return (v || '').normalize('NFD').replace(/[̀-ͯ]/g, '').trim().toLowerCase().replace(/\s+/g, ' ')
}
// O número da caixa: os e-mails dela nos últimos dias (a lista mostra os mesmos).
export function numeroDoGrupo(e: EmailsDaLoja | null | undefined, g: string): number {
  return Number(e?.grupos?.[g]?.emails) || 0
}
// A bolinha vermelha da caixa: as conversas de e-mail esperando resposta.
export function esperandoDoGrupo(e: EmailsDaLoja | null | undefined, g: string): number {
  return Number(e?.grupos?.[g]?.aguardando) || 0
}
// A loja do robô (sem integração, sem site nem conta de rede) é a ficha de
// MESMA plataforma e mesmo nome — uma só (duas fichas com o mesmo nome: a
// linha do robô fica com as caixas vazias e as fichas viram linhas próprias,
// nunca a errada).
function fichaDoRobo(lista: EmailsDaLoja[], l: LinhaDaBarra): EmailsDaLoja | null {
  if (l.integration_id || l.externo_ref || l.rede_social_id) return null
  const nome = nomeComparavel(l.conta)
  const iguais = nome ? lista.filter((e) => !e.integration_id && !!e.store_info_id && e.plataforma === l.plataforma && nomeComparavel(e.loja) === nome) : []
  return iguais.length === 1 ? iguais[0] : null
}
function daLinha(lista: EmailsDaLoja[], l: LinhaDaBarra): EmailsDaLoja | null {
  if (l.integration_id) return lista.find((e) => e.integration_id === l.integration_id) || null
  return fichaDoRobo(lista, l)
}
// A linha da barra é uma LOJA (tem caixas de e-mail)? A integração ou a loja do
// robô; o site e as contas de rede não (o chamado do site fica na aba E-mail).
export function linhaTemCaixas(l: LinhaDaBarra): boolean {
  return !l.externo_ref && !l.rede_social_id
}
// Os e-mails da linha da barra: pela integração ou (a loja do robô) pela
// ficha. A loja que a API não mandou (a lista dela é de outra pessoa, a API
// antiga) ganha as caixas vazias, sem o porquê da loja (só o da pasta).
export function emailsDaLoja(porLoja: EmailsDaLoja[] | null | undefined, l: LinhaDaBarra): EmailsDaLoja | null {
  if (!porLoja || !linhaTemCaixas(l)) return null
  return daLinha(porLoja, l) || { integration_id: l.integration_id || null, store_info_id: null, plataforma: l.plataforma, loja: l.conta, alias: null, grupos: {} }
}
// As lojas que NÃO têm linha de chat na barra (a ficha sem integração — a
// Shopee "atlas", a Amazon "VR" —, a ficha ambígua, a integração sem canal):
// viram linhas como as outras (`chaveSoEmail`), para quem não é admin chegar a
// elas (a aba E-mail é só de admin).
export function lojasSemLinha(porLoja: EmailsDaLoja[] | null | undefined, linhas: LinhaDaBarra[]): EmailsDaLoja[] {
  const lista = porLoja || []
  const usadas = new Set<EmailsDaLoja>()
  for (const l of linhas) {
    const e = daLinha(lista, l)
    if (e) usadas.add(e)
  }
  return lista.filter((e) => !usadas.has(e) && !!e.plataforma)
}
export function chaveSoEmail(e: Pick<EmailsDaLoja, 'integration_id' | 'store_info_id'>): string {
  return e.integration_id ? `email:i:${e.integration_id}` : `email:f:${e.store_info_id || ''}`
}
// A caixa vazia: o PORQUÊ (o dono, 27/10: distinguir a pasta que ainda não liga
// da loja sem endereço na ponte e da loja que só não tem e-mail nesta pasta).
// Primeiro o da pasta (vale para todas as lojas), depois o da loja.
export function motivosDaCaixaVazia(g: GrupoDaBarra | null | undefined, e: EmailsDaLoja | null | undefined, dias = 30): string[] {
  const motivos: string[] = []
  const pasta = `“${g?.pastas?.[0] || g?.rotulo || 'pasta'}”`
  if (g?.status === 'sem_pasta') motivos.push(`A pasta ${pasta} ainda não chegou na ponte: o Tuta ainda não mandou essa pasta.`)
  else if (g?.status === 'so_contar') motivos.push(`A pasta ${pasta} ainda não está configurada: a ponte só conta os e-mails dela (E-mail › Pastas).`)
  else if (g?.status === 'sem_categoria') motivos.push(`A pasta ${pasta} está sem categoria: a ponte guarda os e-mails dela sem ligar à loja (E-mail › Pastas).`)
  else if (g?.status === 'outra_categoria') motivos.push(`A pasta ${pasta} é de Chamado: os e-mails dela vão para Pós-venda › Chamados.`)
  if (e?.alias === 'sem_cadastro') motivos.push('Nenhum e-mail ligado a esta loja — o endereço/alias da loja não está na ponte (Cadastros › Lojas, campo e-mail).')
  else if (e?.alias === 'fora_da_caixa') motivos.push('Nenhum e-mail ligado a esta loja — o e-mail do cadastro não está em nenhuma caixa do Tuta lida pela ponte.')
  else if (e && e.alias && !(Number(e.emails_30d) > 0)) motivos.push(`Nenhum e-mail ligado a esta loja nos últimos ${dias} dias.`)
  else if (e && Number(e.emails_30d) > 0 && !motivos.length) motivos.push(`Nenhum e-mail nesta pasta nos últimos ${dias} dias (a loja recebeu ${e.emails_30d} em outras pastas).`)
  if (!motivos.length) motivos.push(`Nenhum e-mail nesta pasta nos últimos ${dias} dias.`)
  return motivos
}
export function tituloDoGrupo(e: EmailsDaLoja | null | undefined, g: GrupoDaBarra, conta: string, dias = 30): string {
  const n = numeroDoGrupo(e, g.chave)
  const partes = [`${g.rotulo} · ${conta}: ${n} e-mail(s) nos últimos ${dias} dias`]
  const esperando = esperandoDoGrupo(e, g.chave)
  if (esperando) partes.push(`${esperando} esperando resposta`)
  return `${partes.join(' · ')}\n${n ? 'Abre a lista da loja aqui na Caixa' : motivosDaCaixaVazia(g, e, dias).join('\n')}`
}
export function tituloDoAmpliavel(e: EmailsDaLoja | null, conta: string, aberto: boolean, grupos: readonly GrupoDaBarra[] | null | undefined): string {
  const resumo = gruposDaBarra(grupos).map((g) => `${g.rotulo} ${numeroDoGrupo(e, g.chave)}`).join(' · ')
  return `${aberto ? 'Esconder' : 'Mostrar'} os e-mails da loja ${conta}${resumo ? ` (${resumo})` : ''}`
}
export function escolhaDoEmail(l: LinhaDaBarra, e: EmailsDaLoja, grupo: string): EscolhaEmail {
  return {
    chave: l.chave,
    integration_id: e.integration_id || '',
    store_info_id: e.integration_id ? '' : e.store_info_id || '',
    plataforma: e.plataforma || l.plataforma,
    conta: l.conta,
    grupo,
  }
}
// As lojas e as caixas abertas: a página lembra POR PESSOA neste navegador
// (`lojasEmailsAbertos` e `gruposEmailsAbertos`, no localStorage, sempre com try).
export function alternarAberta(lista: string[], chave: string): string[] {
  return lista.includes(chave) ? lista.filter((c) => c !== chave) : [...lista, chave]
}
export function chaveDoGrupoAberto(chaveDaLoja: string, grupo: string): string {
  return `${chaveDaLoja}|${grupo}`
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
// responder". Desde 14/10/2026 ela é só das lojas no Automático (o servidor
// manda zero para as outras: sem bolinha); o title da loja fora diz por quê.
// NA CAIXA, O NÚMERO DO DUOKE (`numero="nao_lidas_duoke"`, 14/10/2026, o
// dono: "o nosso ATV mostra 9, o deles 5 … tá pegando uns antigos de
// avaliação"): a bolinha é a soma das MENSAGENS do comprador nas conversas de
// chat esperando resposta. Avaliação, reclamação/devolução, carrinho e
// comentários não entram (têm os filtros próprios); o Direct, 1 por conversa
// esperando na janela de 24 h. O "falta responder" (conversas) fica no title.
// Desde 15/10/2026 (o dono: os números têm que bater com o Duoke) a régua é a
// do NÃO LIDO DO PRÓPRIO DUOKE: nas conversas de chat esperando resposta com
// o comprador nas últimas 24 h, as mensagens dele desde a última resposta de
// pessoa da loja ou desde que alguém abriu a conversa no DaVinci — o robô não
// zera, abrir zera (services/atendimento/pendente.py). O title da loja
// explica (`TEXTO_NUMERO_DA_LOJA`).
import { ChevronRight, ChevronsLeft, ChevronsRight, Inbox, Lock, TriangleAlert, Unplug } from 'lucide-vue-next'
import type { NumeroDaCaixa } from '~/components/AtendimentoFiltroPlataforma.vue'
import {
  leituraParada,
  modosDaCaixaHumano,
  plataformaInfo,
  sellerCenterDe,
  semLeitura,
  statusCanalCodigo,
  statusCanalInfo,
  textoModosHumano,
  viaRobo,
  type Resumo,
} from '~/components/AtendimentoPlataforma.vue'

// `emails` (27/10/2026): os e-mails de cada loja por grupo (a página lê; sem
// ele — a Caixa Humano, a API antiga — a barra fica como era, sem seta).
// `gruposEmail`: as pastas que a barra mostra (`grupos_na_barra` da API — a
// escolha do dono mora lá — com o status de cada uma na ponte); `diasEmail`:
// a janela dos números. `emailAberto`: a loja e o grupo cujos e-mails a Caixa
// mostra agora. `emailsAbertos` (v-model): as lojas com a seta aberta;
// `gruposAbertos` (v-model): as caixas abertas (`loja|grupo`) — a página
// lembra as duas. `tique`: o polling da página (relê as caixas abertas).
// `modo="emails"` (o celular e o tablet, sem a barra de lojas): só as lojas,
// abertas pelo clique na linha, sem o chat.
const props = defineProps<{ resumo: Resumo | null; numero?: NumeroDaCaixa; emails?: EmailsDaLoja[] | null; gruposEmail?: GrupoDaBarra[] | null; diasEmail?: number; emailAberto?: { chave: string; grupo: string } | null; modo?: '' | 'emails'; tique?: number }>()
const filtros = defineModel<FiltrosLista>('filtros', { required: true })
const recolhida = defineModel<boolean>('recolhida', { default: false })
const emailsAbertos = defineModel<string[]>('emailsAbertos', { default: () => [] })
const gruposAbertos = defineModel<string[]>('gruposAbertos', { default: () => [] })
const emit = defineEmits<{
  (e: 'abrirEmails', escolha: EscolhaEmail): void
  (e: 'abrirEmail', escolha: EscolhaEmail, email: EmailDaCaixa): void
}>()

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
  // A bolinha: na Caixa, o número do Duoke (= `nao_lidas_duoke`: mensagens
  // não lidas esperando resposta); na Caixa Humano, as que esperam uma
  // pessoa (= `humano`); sem `numero`, o "falta responder" (= `aguardando`).
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
  // A loja sem linha de chat (27/10/2026: a ficha sem integração): uma linha
  // como as outras, com as caixas de e-mail; o clique abre as caixas.
  so_email?: boolean
  emails?: EmailsDaLoja | null
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
      nao_lidas: numeroDaLoja(l, props.numero),
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

// As lojas sem linha de chat na barra (27/10/2026: a ficha sem integração, a
// integração sem canal): uma linha como as outras no grupo da plataforma, com
// as caixas de e-mail (sem número de chat). Só onde há a seta.
const gruposDaLoja = computed(() => gruposDaBarra(props.gruposEmail))
const dias = computed(() => Number(props.diasEmail) || 30)
// Só a loja que TEM e-mail numa das caixas (27/10/2026, o Eduardo: "criou lojas a
// mais ali no atendimento"): a ficha sem chat e sem e-mail nas pastas não vira linha.
const lojasSoEmail = computed<LojaBarra[]>(() => {
  if (!props.emails || props.numero === 'humano') return []
  const comEmail = (e: EmailsDaLoja) => gruposDaLoja.value.some((g) => numeroDoGrupo(e, g.chave) > 0)
  return lojasSemLinha(props.emails, lojas.value).filter(comEmail).map((e) => ({
    chave: chaveSoEmail(e),
    integration_id: '',
    externo_ref: '',
    rede_social_id: '',
    direct: 0,
    direct_total: 0,
    comentarios: false,
    plataforma: e.plataforma || '',
    conta: e.loja || 'loja sem nome',
    nao_lidas: 0,
    nao_lidas_plataforma: null,
    aguardando: 0,
    humano: 0,
    vencidas: 0,
    status: 'ok',
    motivo: null,
    apagada: false,
    parcial: false,
    so_email: true,
    emails: e,
  }))
})

type Grupo = { plataforma: string; nome: string; lojas: LojaBarra[]; nao_lidas: number }
const grupos = computed<Grupo[]>(() => {
  const porPlat = new Map<string, LojaBarra[]>()
  const chat = props.numero === 'humano' ? lojasDaCaixaHumano(lojas.value, props.resumo?.canais, modosHumano.value) : lojas.value
  // No modo "emails" (o celular), só as lojas (com as caixas de e-mail).
  const base = props.modo === 'emails'
    ? [...chat, ...lojasSoEmail.value].filter((l) => !!emailsDe(l))
    : [...chat, ...lojasSoEmail.value]
  for (const l of base) porPlat.set(l.plataforma, [...(porPlat.get(l.plataforma) || []), l])
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
const instagram = computed(() => numeroDe(plataformaInstagram.value, campoDaBarra(props.numero)))
const mostrarInstagram = computed(() => !!props.resumo && !temContasInstagram.value && (!!plataformaInstagram.value || filtros.value.plataforma === 'instagram')
  && (!corte.value.length || corte.value.includes('instagram'))
  // Na Caixa Humano, só com conversa contada lá (rede não fica no Automático).
  && (props.numero !== 'humano' || !modosHumano.value || instagram.value > 0))

// A Caixa Humano só das lojas nos modos dela (14/10/2026): a loja fora (a do
// robô sem integração: não dá para saber — fica como sempre).
const modosHumano = computed(() => modosDaCaixaHumano(props.resumo?.flags))
function foraDaCaixaHumano(l: LojaBarra): boolean {
  const modos = modosHumano.value
  return !!modos && !l.humano && lojaNosModos(props.resumo?.canais, l, modos) === false
}
function contador(n: number) {
  return n > 99 ? '99+' : String(n)
}
// A régua do número da loja na Caixa (15/10/2026), no title.
const TEXTO_NUMERO_DA_LOJA = 'mensagem(ns) não lida(s) do cliente'
function motivo(l: LojaBarra): string {
  const partes = [`${plataformaInfo(l.plataforma).nome} · ${l.conta}`]
  if (l.so_email) {
    partes.push('Sem chat no DaVinci (a loja não tem integração ligada): o clique mostra as pastas de e-mail dela.')
    return partes.join('\n')
  }
  if (props.modo === 'emails') {
    partes.push('O clique mostra os e-mails desta loja.')
    return partes.join('\n')
  }
  if (l.apagada) {
    const st = statusCanalInfo(l.status)
    partes.push(st ? `${st.label}: ${st.hint}` : l.status)
    if (l.motivo) partes.push(l.motivo)
  } else {
    // Na Caixa, o número do Duoke e, ao lado, o "Falta responder" (conversas,
    // só o chat). Na Caixa Humano (e sem `numero`), o texto de sempre.
    if (props.numero === 'nao_lidas_duoke') {
      partes.push(`${l.nao_lidas} ${TEXTO_NUMERO_DA_LOJA}`)
      partes.push(`${l.aguardando} conversa(s) no Falta responder (só o chat)`)
    } else partes.push(`${l.aguardando} conversa(s) esperando resposta`)
    if (props.numero === 'humano' && foraDaCaixaHumano(l)) partes.push(`Fora da Caixa Humano: a loja não está ${textoModosHumano(modosHumano.value || [])} (mude em Lojas e modo)`)
    else if (l.humano || props.numero === 'humano') partes.push(`${l.humano} na Caixa Humano (a IA não pode responder)`)
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
    // Na Caixa, o Direct que passou da janela de 24 h da Meta fica fora do
    // número do Duoke (continua no "Falta responder" e em "Vencidas").
    const foraDaJanela = props.numero === 'nao_lidas_duoke' ? Math.max(0, l.direct - l.nao_lidas) : 0
    const direct = `Direct: ${l.direct} esperando (só leitura)${foraDaJanela ? ` — ${foraDaJanela} passou(aram) da janela de 24 h: fora do número, no Falta responder` : ''}`
    partes.push(
      l.comentarios
        // Os comentários não entram nos números desde 14/10/2026 (só o chat): o filtro Mídia.
        ? `${direct} · comentários: no filtro Mídia`
        : `${direct} — a conta não tem leitura de comentários`,
    )
  }
  if (l.plataforma === 'site') partes.push('Carrinho abandonado do lojista no site (lido do site, servidor a servidor)')
  // Sem integração nem origem, a lista não separa esta loja das outras da plataforma.
  if (!l.integration_id && !l.externo_ref && !l.rede_social_id && (grupos.value.find((g) => g.plataforma === l.plataforma)?.lojas.filter((x) => !x.so_email).length ?? 0) > 1) {
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
  // A loja só de e-mail e o modo "emails" (o celular): o clique abre os e-mails.
  if (l.so_email || props.modo === 'emails') {
    alternarEmails(l)
    return
  }
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
// Com os e-mails de uma loja na Caixa (27/10/2026) nenhuma linha do CHAT fica
// acesa: aceso fica o tipo escolhido, embaixo da loja.
const ativaTodas = computed(() => !props.emailAberto && (corte.value.length ? ativaTodasDoCorte(filtros.value, corte.value) : !filtros.value.plataforma))
const tituloTodas = computed(() => {
  const n = props.numero === 'humano'
    ? `${total.value} esperando uma pessoa (a IA não pode responder)`
    : props.numero === 'nao_lidas_duoke'
      ? `${total.value} mensagem(ns) não lida(s)`
      : `${total.value} não lida(s)`
  return corte.value.length
    ? `Todas as lojas ${nomeCorte.value} — ${n}\nPara ver as outras plataformas: "Todas" no menu Plataforma, acima`
    : `Todas as lojas — ${n}`
})
// A plataforma inteira escolhida — sozinha ou num grupo dos chips ("Redes" =
// "instagram,facebook" acende Instagram e Facebook).
function ativaPlataforma(p: string) {
  if (props.emailAberto) return false
  return (filtros.value.plataforma || '').split(',').includes(p) && !filtros.value.integration_id && !filtros.value.externo_ref && !filtros.value.rede_social_id
}
function ativaLoja(l: LojaBarra) {
  if (props.emailAberto || l.so_email || props.modo === 'emails') return false
  if (l.rede_social_id) return filtros.value.rede_social_id === l.rede_social_id
  if (!l.integration_id && l.externo_ref) return filtros.value.externo_ref === l.externo_ref
  if (!l.integration_id) return ativaPlataforma(l.plataforma)
  return filtros.value.integration_id === l.integration_id
}

// ─── os e-mails de cada loja (27/10/2026) ───
// A seta só na barra aberta (recolhida, não cabe) e em TODA loja (o site e as
// contas de rede não são loja de e-mail) — mesmo sem e-mail: a caixa vazia diz
// por quê.
function emailsDe(l: LojaBarra): EmailsDaLoja | null {
  if (!props.emails || !gruposDaLoja.value.length || (recolhida.value && props.modo !== 'emails')) return null
  if (l.so_email) return l.emails || null
  return emailsDaLoja(props.emails, l)
}
// A loja cujos e-mails a Caixa mostra agora (a barra recolhida acende o ícone dela).
function emailAbertoNaLinha(l: LojaBarra): boolean {
  return !!props.emailAberto && props.emailAberto.chave === l.chave
}
// A linha some? Só a "só e-mail" com a barra recolhida (sem seta, não cabe), a
// não ser a que está aberta na Caixa.
function linhaVisivel(l: LojaBarra): boolean {
  return !l.so_email || !recolhida.value || props.modo === 'emails' || emailAbertoNaLinha(l)
}
function emailsAbertosDe(l: LojaBarra): boolean {
  return (emailsAbertos.value || []).includes(l.chave)
}
function alternarEmails(l: LojaBarra) {
  emailsAbertos.value = alternarAberta(emailsAbertos.value || [], l.chave)
}
function ativaEmail(l: LojaBarra, g: string): boolean {
  return props.emailAberto?.chave === l.chave && props.emailAberto?.grupo === g
}
// A caixa de uma pasta: abre e fecha sozinha (lembrada); a lista curta dela só
// é carregada aberta (AtendimentoLojaGrupoEmails).
function grupoAberto(l: LojaBarra, g: string): boolean {
  return (gruposAbertos.value || []).includes(chaveDoGrupoAberto(l.chave, g))
}
function alternarGrupo(l: LojaBarra, g: string) {
  gruposAbertos.value = alternarAberta(gruposAbertos.value || [], chaveDoGrupoAberto(l.chave, g))
}
// O clique na caixa (27/10/2026, o Eduardo: "ao clicar em problemas já deve abrir a
// listagem"): abre a caixa (a lista curta) E a lista da loja no meio; na caixa aberta e
// já na tela, o clique fecha a caixa (a lista do meio fica).
function clicarGrupo(l: LojaBarra, g: string) {
  const aberto = grupoAberto(l, g)
  if (aberto && ativaEmail(l, g)) {
    alternarGrupo(l, g)
    return
  }
  if (!aberto) alternarGrupo(l, g)
  abrirEmails(l, g)
}
function escolhaDe(l: LojaBarra, g: string): EscolhaEmail | null {
  const e = emailsDe(l)
  return e ? escolhaDoEmail(l, e, g) : null
}
// "ver todos": a lista da loja na Caixa (a lista do meio).
function abrirEmails(l: LojaBarra, g: string) {
  const escolha = escolhaDe(l, g)
  if (escolha) emit('abrirEmails', escolha)
}
// Um e-mail da lista curta: a lista da loja na Caixa e o e-mail (ou a conversa dele).
function abrirEmail(l: LojaBarra, g: string, email: EmailDaCaixa) {
  const escolha = escolhaDe(l, g)
  if (escolha) emit('abrirEmail', escolha, email)
}
// O número cinza do tipo vai até 999 (a bolinha vermelha segue no 99+): numa
// loja com 341 vendas, "99+" em todos os tipos não diria nada.
function contadorDoTipo(n: number) {
  return n > 999 ? '999+' : String(n)
}
// O id do grupo (aria-controls): a chave tem ":" e espaços na loja do robô.
function idDosEmails(l: LojaBarra): string {
  return `emails-loja-${l.chave.replace(/[^a-zA-Z0-9_-]/g, '_')}`
}
function idDaCaixa(l: LojaBarra, g: string): string {
  return `${idDosEmails(l)}-${g.replace(/[^a-zA-Z0-9_-]/g, '_')}`
}
</script>

<template>
  <nav class="flex min-h-0 flex-1 flex-col" aria-label="lojas">
    <div class="flex shrink-0 items-center border-b px-2 py-1.5" :class="recolhida ? 'justify-center' : 'justify-between'">
      <span v-if="!recolhida" class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{{ modo === 'emails' ? 'E-mails das lojas' : 'Lojas' }}</span>
      <button
        v-if="modo !== 'emails'"
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
        v-if="modo !== 'emails'"
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
        <!-- o modo "emails" (o celular): o nome da plataforma, sem filtrar o chat -->
        <div
          v-if="modo === 'emails'"
          class="mx-1 mt-2 flex items-center gap-1.5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
          :data-grupo="g.plataforma"
        >
          <AtendimentoIconePlataforma :plataforma="g.plataforma" :tamanho="14" decorativo />
          <span class="min-w-0 flex-1 truncate">{{ g.nome }}</span>
        </div>
        <template v-else-if="!semCabecalho">
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
            :title="`Só ${g.nome}: todas as lojas ${g.nome}${g.nao_lidas ? ` — ${g.nao_lidas} ${numero === 'humano' ? 'esperando uma pessoa' : numero === 'nao_lidas_duoke' ? 'mensagem(ns) não lida(s)' : 'esperando resposta'}` : ''}`"
            :data-grupo="g.plataforma"
            @click="escolherGrupo(g.plataforma)"
          >{{ plataformaInfo(g.plataforma).curto }}</button>
        </template>

        <template v-for="l in g.lojas" :key="l.chave">
          <div v-if="linhaVisivel(l)" class="relative">
            <!-- a linha do CHAT (o clique filtra a lista); a loja sem chat (a ficha
                 sem integração) e o modo "emails" (o celular) abrem as pastas de
                 e-mail da loja pelo próprio clique -->
            <button
              type="button"
              class="relative mx-1 flex w-[calc(100%-0.5rem)] items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]"
              :class="[
                ativaLoja(l) ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-muted',
                recolhida && emailAbertoNaLinha(l) ? 'bg-primary/10 text-primary ring-1 ring-primary/40' : '',
                l.apagada ? 'opacity-50' : '',
                recolhida ? 'justify-center gap-1 px-1' : '',
                emailsDe(l) && !l.so_email && modo !== 'emails' ? 'pr-7' : '',
              ]"
              :aria-pressed="l.so_email || modo === 'emails' ? undefined : ativaLoja(l)"
              :aria-expanded="(l.so_email || modo === 'emails') && emailsDe(l) ? emailsAbertosDe(l) : undefined"
              :aria-controls="(l.so_email || modo === 'emails') && emailsDe(l) && emailsAbertosDe(l) ? idDosEmails(l) : undefined"
              :aria-current="recolhida && emailAbertoNaLinha(l) ? 'true' : undefined"
              :title="motivo(l)"
              :data-so-email="l.so_email ? l.chave : undefined"
              @click="escolherLoja(l)"
            >
              <AtendimentoIconePlataforma :plataforma="l.plataforma" :tamanho="recolhida ? 18 : 15" decorativo />
              <span v-if="!recolhida" class="min-w-0 flex-1 truncate">{{ l.conta }}</span>
              <template v-if="l.so_email || modo === 'emails'">
                <ChevronRight v-if="!recolhida && emailsDe(l)" class="size-3.5 shrink-0 transition-transform" :class="emailsAbertosDe(l) ? 'rotate-90' : ''" aria-hidden="true" />
              </template>
              <template v-else>
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
              </template>
            </button>
            <!-- o AMPLIÁVEL dos e-mails da loja (27/10/2026): em toda loja -->
            <button
              v-if="emailsDe(l) && !l.so_email && modo !== 'emails'"
              type="button"
              class="absolute right-1.5 top-1/2 flex size-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              :aria-expanded="emailsAbertosDe(l)"
              :aria-controls="emailsAbertosDe(l) ? idDosEmails(l) : undefined"
              :aria-label="tituloDoAmpliavel(emailsDe(l), l.conta, emailsAbertosDe(l), gruposDaLoja)"
              :title="tituloDoAmpliavel(emailsDe(l), l.conta, emailsAbertosDe(l), gruposDaLoja)"
              :data-ampliar-emails="l.chave"
              @click="alternarEmails(l)"
            >
              <ChevronRight class="size-3.5 transition-transform" :class="emailsAbertosDe(l) ? 'rotate-90' : ''" />
            </button>
          </div>
          <div
            v-if="emailsDe(l) && emailsAbertosDe(l)"
            :id="idDosEmails(l)"
            role="group"
            :aria-label="`E-mails da loja ${l.conta}`"
            class="mb-1 ml-5 mr-1 space-y-px border-l border-border/70 pl-1"
            :data-emails-loja="l.chave"
          >
            <!-- uma CAIXA por pasta (Problemas · Envios · Devolução · Informativo):
                 abre e fecha sozinha; aberta, a lista curta dos e-mails dela -->
            <template v-for="g in gruposDaLoja" :key="g.chave">
              <button
                type="button"
                class="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[12px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :class="ativaEmail(l, g.chave) ? 'bg-primary/10 font-semibold text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'"
                :aria-expanded="grupoAberto(l, g.chave)"
                :aria-controls="grupoAberto(l, g.chave) ? idDaCaixa(l, g.chave) : undefined"
                :title="tituloDoGrupo(emailsDe(l), g, l.conta, dias)"
                :data-email-grupo="g.chave"
                @click="clicarGrupo(l, g.chave)"
              >
                <ChevronRight class="size-3 shrink-0 transition-transform" :class="grupoAberto(l, g.chave) ? 'rotate-90' : ''" aria-hidden="true" />
                <span class="min-w-0 flex-1 truncate">{{ g.rotulo }}</span>
                <span
                  v-if="esperandoDoGrupo(emailsDe(l), g.chave)"
                  class="flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-semibold leading-none text-white tabular-nums"
                  data-email-esperando
                >{{ contador(esperandoDoGrupo(emailsDe(l), g.chave)) }}</span>
                <span class="shrink-0 text-[11px] tabular-nums" :class="numeroDoGrupo(emailsDe(l), g.chave) ? '' : 'opacity-50'" data-email-numero>{{ contadorDoTipo(numeroDoGrupo(emailsDe(l), g.chave)) }}</span>
              </button>
              <AtendimentoLojaGrupoEmails
                v-if="grupoAberto(l, g.chave)"
                :id="idDaCaixa(l, g.chave)"
                :escolha="escolhaDe(l, g.chave)!"
                :total="numeroDoGrupo(emailsDe(l), g.chave)"
                :motivos="motivosDaCaixaVazia(g, emailsDe(l), dias)"
                :tique="tique"
                @abrir="(email: EmailDaCaixa) => abrirEmail(l, g.chave, email)"
                @ver-todos="abrirEmails(l, g.chave)"
              />
            </template>
          </div>
        </template>
      </template>

      <div v-if="modo === 'emails' && resumo && !visiveis.length" class="px-3 py-6 text-center text-xs text-muted-foreground" data-sem-emails-de-loja>
        {{ emails ? 'Nenhuma loja para mostrar agora.' : 'Carregando os e-mails das lojas…' }}
      </div>

      <!-- Instagram (Direct, só leitura) — só com a API antiga, sem as contas -->
      <template v-if="mostrarInstagram && modo !== 'emails'">
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
