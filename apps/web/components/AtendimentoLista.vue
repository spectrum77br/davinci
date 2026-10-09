<script lang="ts">
import type { Resumo as ResumoDaCaixa } from '~/components/AtendimentoPlataforma.vue'

// Filtros da lista — a página guarda e manda para a API (`GET /conversas`).
export type FiltrosLista = {
  plataforma: string
  integration_id: string
  canal: string
  filtro: string
  q: string
  // Linhas da barra de lojas SEM integração (02/10/2026): o site
  // ("site:charlots" — todas as caixas dele) e a conta de rede social (o
  // Direct e os comentários da mesma conta). Opcionais: '' = sem filtro.
  externo_ref?: string
  rede_social_id?: string
  // O tipo do chamado dos sites (RF6, 09/10/2026): os chips SAC / Atacado /
  // Dúvidas e sugestões do grupo Site (`?tipo_chamado=`). '' = todos.
  tipo_chamado?: string
  // A plataforma ESCOLHIDA — no menu do topo ou pelo nome do grupo na barra
  // (09/10/2026, Eduardo: "quando selecionar a plataforma lá em cima, corta
  // as outras ali da listagem"): a barra de lojas mostra só as lojas dela, e
  // o botão do topo mostra ela. Não vai para a API (a lista filtra por
  // `plataforma`); clicar numa LOJA na barra não mexe aqui — senão a barra
  // encolhia a cada clique numa loja.
  plataforma_topo?: string
}
type OpcaoFiltro = { value: string; label: string; hint: string }
// Como o Duoke (01/10/2026): duas abas em cima — "Todas" (o All, onde a lista
// abre) e "Falta responder" (a Fila) — e o resto no menu "Filtrar" ao lado.
export const ABAS_LISTA: OpcaoFiltro[] = [
  { value: 'todas', label: 'Todas', hint: 'todas as conversas abertas, da mais recente para a mais antiga' },
  { value: 'aguardando', label: 'Falta responder', hint: 'o comprador falou por último e ninguém respondeu de verdade (resposta automática não conta)' },
]
export const FILTROS_MENU: OpcaoFiltro[] = [
  { value: 'vencendo', label: 'Vencendo', hint: 'o prazo de resposta acaba em menos de 2 h' },
  { value: 'vencidas', label: 'Vencidas', hint: 'passou do prazo sem resposta' },
  { value: 'automatica', label: 'Só resposta automática', hint: 'o robô do Duoke (ou uma campanha) respondeu e nenhuma pessoa ainda' },
  { value: 'com_rascunho', label: 'Com sugestão da IA', hint: 'a IA deixou uma resposta pronta para conferir' },
  // Resposta nossa que a plataforma não confirmou: pode ter chegado ou não.
  // Alguém precisa olhar na plataforma e marcar — o DaVinci não reenvia.
  { value: 'a_conferir', label: 'A conferir', hint: 'resposta enviada pelo DaVinci que a plataforma não confirmou — confira se chegou ao comprador' },
  { value: 'minhas', label: 'Minhas', hint: 'conversas atribuídas a você' },
  { value: 'fechadas', label: 'Fechadas', hint: 'fechadas por alguém da equipe' },
  // O e-mail das lojas (Tuta) que ainda não achou o pedido (RF5, 09/10/2026):
  // a fila "Sem vínculo" de E-mail › Filas, aqui pela conversa. Não é etiqueta.
  { value: 'email_sem_vinculo', label: 'E-mail sem vínculo', hint: 'e-mail das lojas (Tuta) ainda sem pedido ligado — quem cuida do Atendimento vincula em E-mail › Filas' },
  // Pela ETIQUETA (status atual, 01/10/2026), da mais urgente para a menos —
  // os mesmos códigos da API (`filtro=reclamacao`…). Pré-venda e Pós-venda
  // passaram a ser a etiqueta: a conversa com reclamação aberta está em
  // Reclamação, não em Pós-venda.
  { value: 'reclamacao', label: 'Reclamação', hint: 'reclamação ou mediação aberta na plataforma' },
  { value: 'ag_cancelamento', label: 'Ag. cancelamento', hint: 'pedido em Aguardando Cancelamento no Bling (fora a trava do robô da Margem)' },
  { value: 'devolucao', label: 'Devolução', hint: 'devolução aberta na plataforma ou pedido em Aguardando Devolução no Bling' },
  // Avaliação de venda sem resposta da loja (RF8, 02/10/2026): Shopee sem
  // resposta passada a carência (1 h na nota 1–3, 24 h na 4–5); Mercado Livre
  // com nota 1–3 ainda não tratada. O selo da linha leva as estrelas.
  { value: 'avaliacao', label: 'Avaliação', hint: 'avaliação de venda sem resposta da loja (Shopee) ou com nota 1–3 sem tratar (Mercado Livre)' },
  // Carrinho abandonado dos sites (RF9, 02/10/2026).
  { value: 'carrinho', label: 'Carrinho', hint: 'carrinho abandonado do lojista no site (Charlots, Uranyx), ainda sem finalizar' },
  { value: 'pre_venda', label: 'Pré-venda', hint: 'perguntas e conversas sem pedido ligado' },
  { value: 'pos_venda', label: 'Pós-venda', hint: 'conversa de um pedido sem nada aberto (sem reclamação, devolução nem Ag. cancelamento)' },
  // Mídia das redes sociais (RF7, 02/10/2026): comentário, menção e Direct.
  { value: 'midia', label: 'Mídia', hint: 'comentários, menções e Direct das redes sociais das marcas' },
]
// Os filtros do menu que são ETIQUETA (contam pelo /resumo `etiquetas`).
export const FILTROS_ETIQUETA = new Set(['reclamacao', 'ag_cancelamento', 'devolucao', 'avaliacao', 'carrinho', 'midia', 'pre_venda', 'pos_venda'])
export const FILTROS_RAPIDOS: OpcaoFiltro[] = [...ABAS_LISTA, ...FILTROS_MENU]
// Na Caixa Humano (09/10/2026) o menu Filtrar não mostra o que nunca entra
// nela: a fechada, o carrinho do site e as redes (Mídia) — o servidor só põe
// lá o que falta responder num canal que tem resposta.
export const FILTROS_FORA_DA_HUMANO = new Set(['fechadas', 'carrinho', 'midia'])
export function opcoesDoFiltrar(caixa: string | null | undefined): OpcaoFiltro[] {
  return caixa === 'humano' ? FILTROS_MENU.filter((f) => !FILTROS_FORA_DA_HUMANO.has(f.value)) : FILTROS_MENU
}
// O filtro em que a lista abre (e para onde volta ao tirar o do menu): na
// Caixa, "Todas"; na Caixa Humano, "Falta responder" (pelo prazo).
export function filtroPadrao(caixa: string | null | undefined): string {
  return caixa === 'humano' ? 'aguardando' : 'todas'
}

// Os TIPOS do chamado dos sites (RF6, 09/10/2026): os chips do grupo Site,
// com os códigos da API (`ROTULO_TIPO_CAIXA` de mail_atendimento/constantes.py,
// sobre `dados.mail.caixa`). Filtro da lista, não etiqueta: o motor de
// etiqueta não muda (o chamado continua Pré-venda/Pós-venda no menu Filtrar).
export const TIPOS_CHAMADO: OpcaoFiltro[] = [
  { value: 'sac', label: 'SAC', hint: 'chamados do SAC dos sites (protocolo S: US-26-0001…)' },
  { value: 'atacado', label: 'Atacado', hint: 'chamados de Atacado dos sites (protocolo A: UA-26-0001…)' },
  { value: 'duvidas', label: 'Dúvidas e sugestões', hint: 'chamados de Dúvidas e sugestões dos sites (protocolo DS: UDS-26-0001…)' },
]
// Os chips aparecem no grupo Site inteiro — com um site escolhido na barra
// (a caixa do carrinho), o chamado do e-mail não é dele.
export function temChipsDoChamado(f: Pick<FiltrosLista, 'plataforma' | 'integration_id' | 'externo_ref'>): boolean {
  return f.plataforma === 'site' && !f.integration_id && !f.externo_ref
}
// O tipo que filtra de verdade: o lembrado fora do grupo Site não vale.
export function tipoChamadoAtivo(f: FiltrosLista): string {
  const t = (f.tipo_chamado || '').trim().toLowerCase()
  return temChipsDoChamado(f) && TIPOS_CHAMADO.some((x) => x.value === t) ? t : ''
}
// Os números dos chips: os chamados abertos por tipo (o `chamados` da
// plataforma site no /resumo). API antiga (sem a chave): sem número.
export function contagemDosChamados(resumo: ResumoDaCaixa | null | undefined): Record<string, number> | null {
  const site = (resumo?.plataformas || []).find((p) => p.plataforma === 'site')
  if (!site?.chamados) return null
  return Object.fromEntries(TIPOS_CHAMADO.map((t) => [t.value, Number(site.chamados?.[t.value]) || 0]))
}
</script>

<script setup lang="ts">
// Coluna da lista na Caixa (Atendimento, 25/09/2026; cara do Duoke em
// 28/09/2026): a fila de conversas de todas as lojas. Cada linha como no
// Duoke — avatar redondo (foto ou iniciais) com o mini-ícone da plataforma e a
// bolinha vermelha de não lidas; "comprador | loja" e a hora (HH:MM hoje,
// DD/MM antes); a prévia numa linha ("[Pedido]", "[Imagem]" quando não é
// texto); a conversa aberta em azul. O que é nosso e o Duoke não tem fica
// discreto: selo de prazo, "IA sugeriu", "a conferir", atribuída.
// Os contadores dos filtros rápidos vêm do /resumo (a lista é paginada,
// contar os itens carregados mentiria). Setas ↑/↓ andam pela lista sem mouse.
// A PLATAFORMA se escolhe nos chips logo acima da Caixa (08/10/2026,
// AtendimentoFiltroPlataforma na página — "só Mercado Livre, só Shopee"), em
// qualquer largura; "Redes" é um grupo (`instagram,facebook`). A loja se
// escolhe na barra de lojas (AtendimentoLojas); em tela estreita, onde a
// barra some, volta o seletor de loja aqui.
// No cabeçalho, o botão "Ag. cancel." abre a lista dos pedidos em Aguardando
// Cancelamento no Bling, com ou sem conversa (AtendimentoAgCancelamentoLista,
// item 4, fase 4b); "Abrir conversa" de lá seleciona a conversa aqui.
// CAIXA HUMANO (09/10/2026, `caixa="humano"`): a mesma lista, filtrada pelo
// servidor — só o que falta responder e a IA não pode responder. No lugar
// das abas Todas/Falta responder, o título com o número (da loja, da
// plataforma ou o total); o menu Filtrar continua (sem os números, que são
// da Caixa). Toda linha que está na Caixa Humano — também na Caixa — leva o
// chip âmbar com o motivo (o primeiro; todos no title).
import { onClickOutside } from '@vueuse/core'
import { Bot, Check, Inbox, ListFilter, Loader2, Lock, PackageX, PauseCircle, RotateCcw, Search, Sparkles, TriangleAlert, UserRound, X } from 'lucide-vue-next'
import { ETIQUETAS_INFO, faixaDaEtiqueta, secundariasDe } from '~/components/AtendimentoEtiqueta.vue'
import {
  canaisDa,
  canalLabel,
  chipHumano,
  horaLista,
  plataformaInfo,
  prazoDe,
  variasCaixas,
  type ConversaResumo,
  type Resumo,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  itens: ConversaResumo[]
  carregando: boolean
  carregandoMais: boolean
  erro: string | null
  temMais: boolean
  selecionada: string | null
  resumo: Resumo | null
  agora: number
  meuId: string | null
  // '' = a Caixa; 'humano' = a Caixa Humano (09/10/2026).
  caixa?: string
}>()
const filtros = defineModel<FiltrosLista>('filtros', { required: true })
const emHumano = computed(() => props.caixa === 'humano')
const padrao = computed(() => filtroPadrao(props.caixa))
const emit = defineEmits<{
  (e: 'selecionar', id: string): void
  (e: 'carregarMais'): void
  (e: 'recarregar'): void
}>()

// A lista Ag. cancelamento (item 4, fase 4b): pelo pedido, não pela conversa.
const agListaAberta = ref(false)
function abrirDaListaAg(id: string) {
  agListaAberta.value = false
  emit('selecionar', id)
}

function mudar<K extends keyof FiltrosLista>(k: K, v: FiltrosLista[K]) {
  const novo = { ...filtros.value, [k]: v }
  // Trocou a plataforma: a loja e o canal de antes não valem mais.
  if (k === 'plataforma') {
    novo.integration_id = ''
    novo.canal = ''
    novo.externo_ref = ''
    novo.rede_social_id = ''
    novo.tipo_chamado = ''
  }
  // Escolheu a loja no seletor (só as com integração): sai a linha sem
  // integração (site, conta de rede) que estivesse escolhida na barra.
  if (k === 'integration_id') {
    novo.externo_ref = ''
    novo.rede_social_id = ''
  }
  filtros.value = novo
}

// Busca com espera de 300 ms (mesmo ritmo de Chamados) — digitar "12345" não
// dispara cinco consultas.
const busca = ref(filtros.value.q)
let buscaTimer: ReturnType<typeof setTimeout> | null = null
watch(busca, (v) => {
  if (buscaTimer) clearTimeout(buscaTimer)
  buscaTimer = setTimeout(() => mudar('q', v.trim()), 300)
})
watch(() => filtros.value.q, (q) => { if (q !== busca.value.trim()) busca.value = q })
onBeforeUnmount(() => { if (buscaTimer) clearTimeout(buscaTimer) })

// ─── contadores (do /resumo) ────────────────────────────────────────────────
// Loja escolhida → números da loja (o resumo por loja não separa "vencendo");
// plataforma → da plataforma; nada → soma de tudo.
// Etiquetas (Reclamação, Devolução…): conversas abertas com aquela etiqueta,
// do `etiquetas` do /resumo no mesmo nível. A API antiga não manda: sem número.
function porEtiqueta(contagens: (Record<string, number> | undefined)[]): Record<string, number | null> {
  const out: Record<string, number | null> = {}
  const tem = contagens.length > 0 && contagens.every((c) => !!c && typeof c === 'object')
  for (const e of FILTROS_ETIQUETA) out[e] = tem ? contagens.reduce((s, c) => s + (Number(c?.[e]) || 0), 0) : null
  return out
}
// "E-mail sem vínculo" (RF5): o número da linha, da loja ou das plataformas;
// a API antiga não manda — sem número.
function semVinculo(linhas: ({ email_sem_vinculo?: number } | null | undefined)[]): number | null {
  if (!linhas.length || !linhas.every((x) => typeof x?.email_sem_vinculo === 'number')) return null
  return linhas.reduce((s, x) => s + (Number(x?.email_sem_vinculo) || 0), 0)
}
// A Caixa Humano (09/10/2026): o título dela na lista. Do mesmo jeito; a API
// antiga não manda — sem número.
function naHumano(linhas: ({ humano?: number } | null | undefined)[]): number | null {
  if (!linhas.length || !linhas.every((x) => typeof x?.humano === 'number')) return null
  return linhas.reduce((s, x) => s + (Number(x?.humano) || 0), 0)
}
const contagem = computed((): Record<string, number | null> => {
  const r = props.resumo
  if (!r) return {}
  const f = filtros.value
  // "A conferir": o total vem no topo do resumo; por loja/plataforma só se o
  // backend separar — sem o número certo, melhor não mostrar número nenhum.
  // Linha sem integração escolhida na barra (site, conta de rede): os
  // números dela, como os da loja.
  const semIntegracao = f.externo_ref
    ? r.lojas.find((x) => !x.integration_id && x.externo_ref === f.externo_ref)
    : f.rede_social_id
      ? r.lojas.find((x) => !x.integration_id && x.rede_social_id === f.rede_social_id)
      : null
  if (semIntegracao) {
    return {
      aguardando: semIntegracao.aguardando ?? 0,
      vencidas: semIntegracao.vencidas ?? 0,
      vencendo: null,
      a_conferir: semIntegracao.a_conferir ?? null,
      email_sem_vinculo: semVinculo([semIntegracao]),
      humano: naHumano([semIntegracao]),
      ...porEtiqueta([semIntegracao.etiquetas]),
    }
  }
  if (f.integration_id) {
    const l = r.lojas.find((x) => x.integration_id === f.integration_id)
    return {
      aguardando: l?.aguardando ?? 0,
      vencidas: l?.vencidas ?? 0,
      vencendo: null,
      a_conferir: l?.a_conferir ?? null,
      email_sem_vinculo: l ? semVinculo([l]) : 0,
      humano: l ? naHumano([l]) : 0,
      ...porEtiqueta(l ? [l.etiquetas] : []),
    }
  }
  // Uma plataforma ou um grupo dos chips ("Redes" = "instagram,facebook").
  const escolhidas = (f.plataforma || '').split(',').filter(Boolean)
  const ps = escolhidas.length ? r.plataformas.filter((p) => escolhidas.includes(p.plataforma)) : r.plataformas
  const porPlataforma = ps.length > 0 && ps.every((p) => typeof p.a_conferir === 'number')
  return {
    aguardando: ps.reduce((s, p) => s + (p.aguardando || 0), 0),
    vencendo: ps.reduce((s, p) => s + (p.vencendo || 0), 0),
    vencidas: ps.reduce((s, p) => s + (p.vencidas || 0), 0),
    a_conferir: porPlataforma ? ps.reduce((s, p) => s + (p.a_conferir || 0), 0) : (f.plataforma ? null : (r.a_conferir ?? null)),
    email_sem_vinculo: f.plataforma ? (ps.length ? semVinculo(ps) : 0) : semVinculo([r]),
    humano: f.plataforma ? (ps.length ? naHumano(ps) : 0) : naHumano([r]),
    // Sem plataforma escolhida, o total do topo; com ela, o da plataforma
    // (a que não tem conversa não manda `etiquetas`: conta zero).
    ...(f.plataforma
      ? porEtiqueta(ps.length ? ps.map((p) => p.etiquetas || {}) : [])
      : porEtiqueta([r.etiquetas])),
  }
})
function contadorCls(value: string, n: number | null | undefined) {
  if (!n) return 'bg-muted text-muted-foreground'
  if (value === 'vencidas') return 'bg-red-500 text-white'
  if (value === 'vencendo' || value === 'a_conferir' || value === 'email_sem_vinculo') return 'bg-amber-500 text-white'
  // Etiqueta: a cor dela (Reclamação vermelho, Devolução roxo…).
  if (FILTROS_ETIQUETA.has(value)) return ETIQUETAS_INFO[value]?.cls || 'bg-primary/15 text-primary'
  return 'bg-primary/15 text-primary'
}

// ─── menu "Filtrar" ─────────────────────────────────────────────────────────
const menuAberto = ref(false)
const menuRef = ref<HTMLElement | null>(null)
onClickOutside(menuRef, () => { menuAberto.value = false })
const filtroDoMenu = computed(() => FILTROS_MENU.find((f) => f.value === filtros.value.filtro) ?? null)
// As opções do menu (na Caixa Humano, sem o que nunca entra nela).
const opcoesDoMenu = computed(() => opcoesDoFiltrar(props.caixa))
function escolherDoMenu(value: string) {
  menuAberto.value = false
  // Clicar de novo no filtro escolhido tira o filtro (volta para Todas — na
  // Caixa Humano, para "Falta responder").
  mudar('filtro', filtros.value.filtro === value ? padrao.value : value)
}

// ─── chips do tipo do chamado (grupo Site, RF6) ─────────────────────────────
// SAC / Atacado / Dúvidas e sugestões com os chamados abertos de cada tipo;
// clicar no aceso volta para todos. Junto do resto (aba, Filtrar, busca).
const chipsDoChamado = computed(() => temChipsDoChamado(filtros.value))
const tipoAtivo = computed(() => tipoChamadoAtivo(filtros.value))
const numerosDoChamado = computed(() => contagemDosChamados(props.resumo))
function escolherTipo(tipo: string) {
  mudar('tipo_chamado', tipoAtivo.value === tipo ? '' : tipo)
}

// A escolha da plataforma (antes um <select> só em tela estreita) virou os
// chips acima da Caixa (AtendimentoFiltroPlataforma), que só mostram as
// plataformas que existem para a pessoa.

// O seletor de loja (tela estreita, onde a barra some): as lojas com
// integração e, como na barra (02/10/2026), as linhas sem integração que
// filtram — o site (`externo_ref`) e a conta de rede (`rede_social_id`: o
// Direct e os comentários dela). A chave diz qual filtro a opção liga.
// A plataforma pode ser um GRUPO dos chips ("Redes" = "instagram,facebook").
const escolhidas = computed(() => (filtros.value.plataforma || '').split(',').filter(Boolean))
// UMA plataforma escolhida (os nomes de caixa e a loja dizem respeito a ela).
const umaPlataforma = computed(() => (escolhidas.value.length === 1 ? escolhidas.value[0] : ''))
type ResumoLojaLista = NonNullable<Resumo['lojas']>[number]
function chaveDaLoja(l: Pick<ResumoLojaLista, 'integration_id' | 'externo_ref' | 'rede_social_id'>): string {
  if (l.integration_id) return `i:${l.integration_id}`
  if (l.rede_social_id) return `r:${l.rede_social_id}`
  if (l.externo_ref) return `e:${l.externo_ref}`
  return ''
}
const lojas = computed(() => {
  const ls = props.resumo?.lojas || []
  const f = escolhidas.value
  // Conversa de loja que saiu do DaVinci fica sem integration_id: não dá
  // para filtrar por ela (aparece em "todas lojas"). A loja do robô sem
  // integração também não (só a plataforma inteira).
  return ls
    .map((l) => ({ ...l, chave: chaveDaLoja(l) }))
    .filter((l) => !!l.chave)
    .filter((l) => !f.length || f.includes(l.plataforma))
    .sort((a, b) => a.plataforma.localeCompare(b.plataforma) || (a.conta || '').localeCompare(b.conta || '', 'pt-BR'))
})
const lojaEscolhida = computed(() => chaveDaLoja({
  integration_id: filtros.value.integration_id || null,
  externo_ref: filtros.value.externo_ref || null,
  rede_social_id: filtros.value.rede_social_id || null,
}))
function escolherLoja(chave: string) {
  const l = lojas.value.find((x) => x.chave === chave)
  // Loja com integração (ou "todas lojas"): o caminho de sempre.
  if (!l || l.integration_id) {
    mudar('integration_id', l?.integration_id || '')
    return
  }
  // Site e conta de rede: como o clique na barra (AtendimentoLojas).
  filtros.value = {
    ...filtros.value,
    plataforma: l.plataforma,
    integration_id: '',
    canal: '',
    externo_ref: l.rede_social_id ? '' : l.externo_ref || '',
    rede_social_id: l.rede_social_id || '',
  }
}
// O Instagram tem duas caixas na mesma conta quando os comentários estão
// sendo lidos (02/10/2026): o Direct (só leitura) e os comentários. Aí a
// lista diz de qual caixa é a conversa, e o seletor de caixa aparece.
const CAIXAS_INSTAGRAM = [
  { value: 'dm', label: 'Direct' },
  { value: 'comentario', label: 'Comentários' },
]
const instagramComComentarios = computed(() => !!props.resumo?.canais?.some((c) => c.plataforma === 'instagram' && c.canal === 'comentario'))
const canais = computed(() =>
  umaPlataforma.value === 'instagram' && instagramComComentarios.value ? CAIXAS_INSTAGRAM : canaisDa(umaPlataforma.value),
)

// ─── itens ──────────────────────────────────────────────────────────────────
function titulo(c: ConversaResumo) {
  return c.comprador_nome || (c.pedido_marketplace ? `Pedido ${c.pedido_marketplace}` : 'Comprador')
}
function loja(c: ConversaResumo) {
  const nome = c.conta || plataformaInfo(c.plataforma).nome
  // Conta do Instagram com Direct e comentários: "@charlots_br · Direct".
  if (c.plataforma === 'instagram' && instagramComComentarios.value) return `${nome} · ${canalLabel(c.canal)}`
  // Só onde a loja tem mais de uma caixa o canal ajuda (ML: Pergunta ×
  // Pós-venda; Magalu: Pergunta × Chat × SAC); nos outros é sempre o mesmo.
  return variasCaixas(c.plataforma) ? `${nome} · ${canalLabel(c.canal)}` : nome
}
// Prévia como no Duoke: o que não é texto vira "[Pedido]", "[Produto]",
// "[Imagem]" — o cartão do pedido não tem texto, e a linha ficava vazia.
const PREVIA_TIPO: Record<string, string> = {
  imagem: '[Imagem]',
  produto: '[Produto]',
  pedido: '[Pedido]',
  video: '[Vídeo]',
  arquivo: '[Arquivo]',
  outro: '[Mensagem]',
}
function previa(c: ConversaResumo) {
  // Sem os ** do markdown do assistente do ML (mediação) na prévia.
  let t = (c.ultima_mensagem_resumo || '').replace(/\*\*/g, '').trim()
  const tipo = (c.ultima_mensagem_tipo || '').toLowerCase()
  // Mensagem sem texto: o backend guarda o tipo cru no resumo ("[pedido]",
  // gravar.recalcular) — vira o rótulo do Duoke ("[Pedido]").
  const cru = /^\[([a-z_]+)\]$/.exec(t)
  if (cru) t = PREVIA_TIPO[cru[1]] || PREVIA_TIPO[tipo] || PREVIA_TIPO.outro
  const marca = tipo && tipo !== 'texto' && !t.startsWith('[') ? PREVIA_TIPO[tipo] || '' : ''
  const corpo = [marca, t].filter(Boolean).join(' ')
  if (!corpo) return c.anuncio_titulo || ''
  return c.ultima_autor === 'loja' ? `Loja: ${corpo}` : corpo
}
// A etiqueta na linha: selo só das que pedem atenção (Pós-venda sem
// destaque), mais o indicador das secundárias e a mão da troca manual.
function temEtiqueta(c: ConversaResumo) {
  return !!(faixaDaEtiqueta(c.etiqueta) || secundariasDe(c.etiqueta, c.etiquetas_secundarias).length)
}
function temSelos(c: ConversaResumo) {
  return !!(c.humano || temEtiqueta(c) || c.eh_pergunta || c.envio_a_conferir || c.tem_rascunho || c.atribuido_a || c.ia_pausada || c.somente_leitura || c.sem_resposta_necessaria || c.situacao === 'bloqueada' || c.situacao === 'fechada')
}
// Na linha escolhida (fundo azul) os selos coloridos viram translúcidos —
// âmbar/violeta em cima do azul não se lê.
function selo(sel: boolean, cls: string) {
  return sel ? 'bg-white/20 text-current' : cls
}

// A Caixa Humano vazia: sem filtro nenhum, a boa notícia; com filtro, o de
// sempre (com a busca, "nada encontrado").
const textoVazioHumano = computed(() => {
  if (filtros.value.q) return 'Nada encontrado para essa busca na Caixa Humano.'
  if (filtros.value.filtro === padrao.value) return 'Nada esperando uma pessoa agora — o que a IA não puder responder aparece aqui.'
  return 'Nenhuma conversa na Caixa Humano com esses filtros.'
})

const VAZIO: Record<string, string> = {
  aguardando: 'Nada esperando resposta agora.',
  automatica: 'Nenhuma conversa só com a resposta automática.',
  pre_venda: 'Nenhuma conversa de pré-venda com esses filtros.',
  pos_venda: 'Nenhuma conversa de pós-venda com esses filtros.',
  reclamacao: 'Nenhuma reclamação aberta com esses filtros.',
  devolucao: 'Nenhuma devolução aberta com esses filtros.',
  ag_cancelamento: 'Nenhum pedido em Aguardando Cancelamento com esses filtros.',
  avaliacao: 'Nenhuma avaliação sem resposta com esses filtros.',
  vencendo: 'Nenhuma conversa perto de vencer.',
  vencidas: 'Nenhuma conversa vencida.',
  com_rascunho: 'Nenhuma sugestão da IA esperando conferência.',
  a_conferir: 'Nenhuma resposta esperando conferência — tudo que saiu pelo DaVinci foi confirmado.',
  minhas: 'Nenhuma conversa atribuída a você.',
  fechadas: 'Nenhuma conversa fechada com esses filtros.',
  email_sem_vinculo: 'Nenhum e-mail de loja sem vínculo — todos acharam o pedido.',
  todas: 'Nenhuma conversa com esses filtros.',
}
const textoVazio = computed(() => {
  if (filtros.value.q) return 'Nada encontrado para essa busca.'
  const tipo = TIPOS_CHAMADO.find((t) => t.value === tipoAtivo.value)
  if (tipo) return `Nenhum chamado de ${tipo.label} com esses filtros.`
  return VAZIO[filtros.value.filtro] || VAZIO.todas
})

// ↑/↓ com o foco na lista: anda pelas conversas (a equipe responde em
// sequência — tirar a mão do teclado a cada conversa cansa).
const listaRef = ref<HTMLElement | null>(null)
// Acessibilidade: o foco fica no listbox (o <ul>, uma parada de Tab só) e o
// leitor de tela anuncia a conversa escolhida pelo `aria-activedescendant`.
// Por isso os itens não entram no Tab (tabindex -1) e o <li> vira
// `presentation` — um listitem entre o listbox e a option quebra a relação.
// Erro, vazio e "carregar mais" ficam fora do listbox (não são opções).
function opcaoId(id: string) {
  return `atd-conversa-${id}`
}
const ativa = computed(() => (props.selecionada && props.itens.some((c) => c.id === props.selecionada) ? opcaoId(props.selecionada) : undefined))
function mover(delta: number) {
  if (!props.itens.length) return
  const i = props.itens.findIndex((c) => c.id === props.selecionada)
  const j = i < 0 ? 0 : Math.min(props.itens.length - 1, Math.max(0, i + delta))
  const alvo = props.itens[j]
  if (!alvo) return
  emit('selecionar', alvo.id)
  nextTick(() => listaRef.value?.querySelector<HTMLElement>(`[data-conversa="${CSS.escape(alvo.id)}"]`)?.scrollIntoView({ block: 'nearest' }))
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <!-- filtros -->
    <div class="shrink-0 space-y-2 border-b p-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          v-model="busca"
          class="h-8 w-full rounded-md border bg-background pl-8 pr-7 text-sm"
          placeholder="buscar comprador, pedido, nº do Bling, SKU…"
          aria-label="buscar conversas"
        />
        <button v-if="busca" type="button" class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted" title="limpar busca" @click="busca = ''">
          <X class="size-3.5" />
        </button>
      </div>
      <!-- A loja: na barra de lojas em tela larga; aqui só quando ela some (tela
           estreita). A caixa do ML (Pergunta/Pós-venda) fica sempre. -->
      <div class="gap-1.5" :class="canais.length > 1 ? 'flex' : 'flex lg:hidden'">
        <select
          :value="lojaEscolhida"
          class="h-8 min-w-0 flex-1 rounded-md border bg-background px-1.5 text-xs lg:hidden"
          aria-label="loja"
          :disabled="!!filtros.plataforma && !lojas.length"
          @change="escolherLoja(($event.target as HTMLSelectElement).value)"
        >
          <option value="">todas lojas</option>
          <option v-for="l in lojas" :key="l.chave" :value="l.chave">
            {{ umaPlataforma ? '' : `${plataformaInfo(l.plataforma).curto} · ` }}{{ l.conta || 'sem nome' }}<template v-if="emHumano ? l.humano : l.aguardando"> ({{ emHumano ? l.humano : l.aguardando }})</template>
          </option>
        </select>
        <select
          v-if="canais.length > 1"
          :value="filtros.canal"
          class="h-8 min-w-[92px] shrink-0 rounded-md border bg-background px-1.5 text-xs lg:flex-1"
          aria-label="canal"
          @change="mudar('canal', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">{{ plataformaInfo(umaPlataforma).curto }}: todas as caixas</option>
          <option v-for="c in canais" :key="c.value" :value="c.value">{{ c.label }}</option>
        </select>
      </div>
      <!-- abas (Todas / Falta responder) + menu Filtrar, como o Duoke; na Caixa
           Humano, o título dela com o número no lugar das abas -->
      <div class="flex items-end gap-2 border-b">
        <div
          v-if="emHumano"
          class="flex min-w-0 flex-1 items-center gap-1 pb-1.5 text-xs font-medium text-primary"
          title="Só o que falta responder e a IA não pode responder (reclamação, devolução, pediu atendente, assunto de dinheiro ou direito…) — respondeu, sai daqui"
          data-titulo-humano
        >
          <UserRound class="size-3.5 shrink-0" aria-hidden="true" />
          <span class="truncate">Caixa Humano</span>
          <span
            v-if="contagem.humano"
            class="min-w-[18px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold tabular-nums text-white"
          >{{ contagem.humano > 99 ? '99+' : contagem.humano }}</span>
        </div>
        <div v-else class="flex min-w-0 flex-1 items-end gap-3" role="tablist" aria-label="conversas">
          <button
            v-for="a in ABAS_LISTA"
            :key="a.value"
            type="button"
            role="tab"
            class="-mb-px inline-flex items-center gap-1 border-b-2 px-0.5 pb-1.5 text-xs font-medium transition-colors"
            :class="filtros.filtro === a.value ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
            :title="a.hint"
            :aria-selected="filtros.filtro === a.value"
            @click="mudar('filtro', a.value)"
          >
            {{ a.label }}
            <span
              v-if="a.value === 'aguardando' && contagem.aguardando"
              class="min-w-[18px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold tabular-nums text-white"
            >{{ contagem.aguardando > 99 ? '99+' : contagem.aguardando }}</span>
          </button>
        </div>
        <button
          type="button"
          class="mb-1 inline-flex h-7 shrink-0 items-center gap-1 rounded-md border px-2 text-[11px] transition-colors hover:bg-muted"
          title="Pedidos em Aguardando Cancelamento no Bling, com ou sem conversa: o motivo e as sugestões de troca"
          data-abrir-ag-cancelamento
          @click="agListaAberta = true"
        >
          <PackageX class="size-3.5" /> Ag. cancel.
        </button>
        <div ref="menuRef" class="relative mb-1 shrink-0" @keydown.esc="menuAberto = false">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1 rounded-md border px-2 text-[11px] transition-colors"
            :class="filtroDoMenu ? 'border-primary bg-primary/10 text-primary' : 'hover:bg-muted'"
            aria-haspopup="menu"
            :aria-expanded="menuAberto"
            @click="menuAberto = !menuAberto"
          >
            <ListFilter class="size-3.5" /> Filtrar
          </button>
          <div
            v-if="menuAberto"
            role="menu"
            aria-label="filtrar conversas"
            class="absolute right-0 z-30 mt-1 w-60 rounded-md border bg-background p-1 shadow-lg"
          >
            <template v-for="(f, i) in opcoesDoMenu" :key="f.value">
              <div
                v-if="FILTROS_ETIQUETA.has(f.value) && (i === 0 || !FILTROS_ETIQUETA.has(opcoesDoMenu[i - 1].value))"
                role="separator"
                class="mx-2 mt-1 border-t pt-1 text-[10px] uppercase tracking-wide text-muted-foreground"
              >Etiqueta</div>
              <button
                type="button"
                role="menuitemradio"
                :aria-checked="filtros.filtro === f.value"
                class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
                :class="filtros.filtro === f.value ? 'font-medium text-primary' : ''"
                :title="f.hint"
                @click="escolherDoMenu(f.value)"
              >
                <span class="min-w-0 flex-1 truncate">{{ f.label }}</span>
                <span
                  v-if="!emHumano && contagem[f.value] !== undefined && contagem[f.value] !== null"
                  class="min-w-[18px] rounded-full px-1 text-center text-[10px] font-semibold tabular-nums"
                  :class="contadorCls(f.value, contagem[f.value])"
                >{{ contagem[f.value] }}</span>
                <Check v-if="filtros.filtro === f.value" class="size-3.5 shrink-0" />
              </button>
            </template>
          </div>
        </div>
      </div>
      <!-- O tipo do chamado (grupo Site, RF6): SAC · Atacado · Dúvidas e sugestões -->
      <div v-if="chipsDoChamado" class="flex flex-wrap items-center gap-1 text-[11px]" role="group" aria-label="tipo do chamado" data-chips-chamado>
        <button
          type="button"
          class="rounded-full border px-2 py-0.5 transition-colors"
          :class="!tipoAtivo ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted'"
          :aria-pressed="!tipoAtivo"
          title="todos os chamados e conversas dos sites"
          data-tipo-chamado=""
          @click="escolherTipo('')"
        >Todos</button>
        <button
          v-for="t in TIPOS_CHAMADO"
          :key="t.value"
          type="button"
          class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 transition-colors"
          :class="tipoAtivo === t.value ? 'border-teal-600 bg-teal-500/15 text-teal-800 dark:text-teal-300' : 'text-muted-foreground hover:bg-muted'"
          :aria-pressed="tipoAtivo === t.value"
          :title="numerosDoChamado ? `${t.hint} — ${numerosDoChamado[t.value]} aberto(s)` : t.hint"
          :data-tipo-chamado="t.value"
          @click="escolherTipo(t.value)"
        >
          {{ t.label }}
          <span
            v-if="numerosDoChamado"
            class="min-w-[16px] rounded-full px-1 text-center text-[10px] font-semibold tabular-nums"
            :class="numerosDoChamado[t.value] ? 'bg-teal-600 text-white' : 'bg-muted text-muted-foreground'"
          >{{ numerosDoChamado[t.value] > 99 ? '99+' : numerosDoChamado[t.value] }}</span>
        </button>
      </div>
      <!-- Caixa Humano: o que é, numa linha; e o aviso quando a triagem está desligada no servidor -->
      <p v-if="emHumano" class="text-[11px] leading-snug text-muted-foreground" data-dica-humano>
        Só o que a IA não pode responder e falta responder — respondeu, sai daqui.
        <span v-if="resumo?.flags?.humano_ativa === false" class="block text-amber-700 dark:text-amber-300" data-humano-desligada>
          A triagem está desligada no servidor: aqui só aparecem as conversas com a IA pausada.
        </span>
      </p>
      <div v-if="filtroDoMenu" class="flex items-center gap-1 text-[11px]">
        <span class="text-muted-foreground">Filtro:</span>
        <span class="inline-flex items-center gap-1 rounded-full border border-primary bg-primary/10 px-2 py-0.5 text-primary" :title="filtroDoMenu.hint">
          {{ filtroDoMenu.label }}
          <button type="button" class="rounded-full hover:bg-primary/20" aria-label="tirar o filtro" @click="mudar('filtro', padrao)">
            <X class="size-3" />
          </button>
        </span>
      </div>
    </div>

    <!-- itens -->
    <div ref="listaRef" class="min-h-0 flex-1 overflow-y-auto">
      <div v-if="erro" class="m-2 space-y-1 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
        <div>{{ erro }}</div>
        <button type="button" class="inline-flex items-center gap-1 underline" @click="emit('recarregar')"><RotateCcw class="size-3" /> tentar de novo</button>
      </div>

      <div v-if="carregando && !itens.length" class="space-y-2 p-3" aria-busy="true">
        <div v-for="i in 6" :key="i" class="space-y-1.5 rounded-md border p-2">
          <div class="h-3 w-1/3 animate-pulse rounded bg-muted" />
          <div class="h-3 w-2/3 animate-pulse rounded bg-muted" />
          <div class="h-3 w-full animate-pulse rounded bg-muted" />
        </div>
      </div>

      <div v-else-if="!itens.length && !erro" class="px-4 py-10 text-center text-sm text-muted-foreground">
        <component :is="emHumano ? UserRound : Inbox" class="mx-auto mb-2 size-6 opacity-60" />
        {{ emHumano ? textoVazioHumano : textoVazio }}
        <div v-if="filtros.filtro !== padrao" class="mt-2">
          <button type="button" class="text-xs underline" @click="mudar('filtro', padrao)">{{ emHumano ? 'tirar o filtro' : 'ver todas' }}</button>
        </div>
      </div>

      <ul
        v-else
        class="space-y-0.5 px-1.5 py-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary"
        tabindex="0"
        role="listbox"
        aria-label="conversas"
        :aria-activedescendant="ativa"
        :aria-busy="carregando || undefined"
        @keydown.down.prevent="mover(1)"
        @keydown.up.prevent="mover(-1)"
      >
        <li v-for="c in itens" :key="c.id" role="presentation">
          <button
            :id="opcaoId(c.id)"
            type="button"
            role="option"
            tabindex="-1"
            :aria-selected="c.id === selecionada"
            :data-conversa="c.id"
            class="relative flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left transition-colors"
            :class="[
              c.id === selecionada ? 'bg-primary text-primary-foreground shadow-sm' : 'hover:bg-muted/70',
              c.situacao === 'fechada' && c.id !== selecionada ? 'opacity-60' : '',
            ]"
            :data-etiqueta="c.etiqueta || undefined"
            @click="emit('selecionar', c.id)"
          >
            <!-- Faixa da etiqueta (Reclamação vermelho, Ag. cancelamento laranja,
                 Devolução roxo, Pré-venda azul; Pós-venda sem destaque). -->
            <span
              v-if="faixaDaEtiqueta(c.etiqueta)"
              class="absolute inset-y-1.5 left-0 w-1 rounded-full"
              :class="faixaDaEtiqueta(c.etiqueta)"
              aria-hidden="true"
              data-faixa
            />
            <AtendimentoAvatar
              class="mt-0.5"
              :nome="c.comprador_nome || c.pedido_marketplace"
              :foto="c.comprador_avatar"
              :plataforma="c.plataforma"
              :nao-lidas="c.pendentes ?? c.nao_lidas"
              :tamanho="40"
            />
            <span class="min-w-0 flex-1">
              <span class="flex items-center gap-1 text-[13px] leading-5">
                <span class="min-w-0 truncate" :class="c.aguardando_resposta ? 'font-semibold' : 'font-medium'" :title="titulo(c)">{{ titulo(c) }}</span>
                <span class="shrink-0 opacity-40" aria-hidden="true">|</span>
                <span class="min-w-0 max-w-[48%] shrink-[2] truncate" :class="c.id === selecionada ? '' : 'text-foreground/80'" :title="loja(c)">{{ loja(c) }}</span>
                <span
                  class="ml-auto shrink-0 pl-1 text-[11px] tabular-nums"
                  :class="c.id === selecionada ? 'text-primary-foreground/80' : 'text-muted-foreground'"
                  :title="c.ultima_mensagem_em ? new Date(c.ultima_mensagem_em).toLocaleString('pt-BR') : ''"
                >{{ horaLista(c.ultima_mensagem_em, agora) }}</span>
              </span>
              <span class="mt-0.5 flex items-center gap-1.5">
                <span
                  class="min-w-0 flex-1 truncate text-xs"
                  :class="c.id === selecionada ? 'text-primary-foreground/85' : 'text-muted-foreground'"
                  :title="previa(c)"
                >{{ previa(c) || '—' }}</span>
                <span
                  v-if="prazoDe(c, agora) && prazoDe(c, agora)!.nivel !== 'ok'"
                  class="shrink-0 rounded px-1 py-px text-[10px] font-medium"
                  :class="selo(c.id === selecionada, prazoDe(c, agora)!.cls)"
                  :title="prazoDe(c, agora)!.titulo"
                >{{ prazoDe(c, agora)!.texto }}</span>
                <span
                  v-else-if="prazoDe(c, agora)"
                  class="shrink-0 text-[10px] tabular-nums"
                  :class="c.id === selecionada ? 'text-primary-foreground/70' : 'text-muted-foreground'"
                  :title="prazoDe(c, agora)!.titulo"
                >{{ prazoDe(c, agora)!.texto }}</span>
              </span>
              <span v-if="temSelos(c)" class="mt-1 flex flex-wrap items-center gap-1 text-[10px]">
                <!-- Caixa Humano (09/10/2026): por que a IA não responde esta —
                     o primeiro motivo, "+N" os outros, todos no title. -->
                <span
                  v-if="c.humano"
                  class="inline-flex min-w-0 max-w-full items-center gap-0.5 rounded px-1.5 py-px font-medium"
                  :class="selo(c.id === selecionada, 'bg-amber-500/20 text-amber-900 ring-1 ring-inset ring-amber-500/40 dark:text-amber-200')"
                  :title="chipHumano(c.humano)?.titulo"
                  data-selo-humano
                >
                  <UserRound class="size-3 shrink-0" aria-hidden="true" />
                  <span class="truncate">{{ chipHumano(c.humano)?.texto }}</span>
                  <span v-if="(chipHumano(c.humano)?.mais ?? 0) > 0" class="shrink-0 opacity-75">+{{ chipHumano(c.humano)?.mais }}</span>
                </span>
                <AtendimentoEtiqueta
                  v-if="temEtiqueta(c)"
                  :etiqueta="c.etiqueta"
                  :secundarias="c.etiquetas_secundarias"
                  :manual="c.etiqueta_manual"
                  :desde="c.etiqueta_desde"
                  :estrelas="c.avaliacao_estrelas"
                  :selecionada="c.id === selecionada"
                  esconder-pos-venda
                />
                <!-- RF7: comentário com pergunta ainda sem resposta da marca — vem
                     primeiro no filtro Mídia e em "Falta responder". -->
                <span
                  v-if="c.eh_pergunta"
                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-px font-medium"
                  :class="selo(c.id === selecionada, 'bg-pink-500/15 text-pink-700 dark:text-pink-300')"
                  title="pergunta sem resposta da marca: vem primeiro no filtro Mídia e em Falta responder"
                  data-selo-pergunta
                >
                  <span class="font-bold" aria-hidden="true">?</span> pergunta
                </span>
                <span
                  v-if="c.envio_a_conferir"
                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-px font-medium"
                  :class="selo(c.id === selecionada, 'bg-amber-500/20 text-amber-800 dark:text-amber-300')"
                  title="a plataforma não confirmou uma resposta nossa — abra e marque se ela chegou ao comprador"
                >
                  <TriangleAlert class="size-3" /> a conferir
                </span>
                <span v-if="c.tem_rascunho" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px font-medium" :class="selo(c.id === selecionada, 'bg-violet-500/15 text-violet-700 dark:text-violet-300')">
                  <Sparkles class="size-3" /> IA sugeriu
                </span>
                <span v-if="c.atribuido_a" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-sky-500/15 text-sky-700 dark:text-sky-300')" :title="`atribuída a ${c.atribuido_a_nome || 'alguém'}`">
                  <UserRound class="size-3" /> {{ c.atribuido_a === meuId ? 'você' : (c.atribuido_a_nome || 'atribuída') }}
                </span>
                <span v-if="c.ia_pausada" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')" title="a IA não sugere nesta conversa">
                  <PauseCircle class="size-3" /><Bot class="size-3" /> pausada
                </span>
                <span v-if="c.sem_resposta_necessaria" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')">não precisa de resposta</span>
                <span v-if="c.situacao === 'bloqueada' && c.canal === 'avaliacao'" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')" title="a plataforma não deixa responder esta avaliação pela API — trate e use “Marcar como tratada” no cartão">sem resposta pela API</span>
                <span v-else-if="c.situacao === 'bloqueada'" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-red-500/15 text-red-700 dark:text-red-300')">bloqueada</span>
                <span v-if="c.situacao === 'fechada'" class="rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')">fechada</span>
                <span v-if="c.somente_leitura" class="inline-flex items-center gap-0.5 rounded px-1.5 py-px" :class="selo(c.id === selecionada, 'bg-muted text-muted-foreground')" title="só leitura no Atendimento">
                  <Lock class="size-3" /> só leitura
                </span>
              </span>
            </span>
          </button>
        </li>
      </ul>

      <div v-if="temMais && itens.length" class="p-2">
        <button
          type="button"
          class="flex w-full items-center justify-center gap-1.5 rounded-md border py-1.5 text-xs hover:bg-muted disabled:opacity-60"
          :disabled="carregandoMais"
          @click="emit('carregarMais')"
        >
          <Loader2 v-if="carregandoMais" class="size-3.5 animate-spin" />
          carregar mais
        </button>
      </div>
    </div>
    <AtendimentoAgCancelamentoLista v-if="agListaAberta" v-model:aberto="agListaAberta" @abrir-conversa="abrirDaListaAg" />
  </div>
</template>
